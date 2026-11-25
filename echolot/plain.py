"""A report in plain words, for someone who has never opened a trace.

A report row is written for an agent that knows the tool:

    uninstrumented_cpu | arch_disk_io_0 | 486.78 | 3/3 | 99.0% of CPU outside slices

Three things come out of it here, and a model writes none of them: the same
report gives the same words every time.

  - three fixed texts per detector, kept in its .sql header beside `@title`
    and `@why`: what was checked, how it touches the app, and what a clean
    result says (`@plain_title`, `@plain_why`, `@plain_clean`);
  - one sentence about what was found, from a template per detector over its
    rows (`FOUND`), with a generic one for a detector that has none;
  - how much attention a detector needs, and a verdict for the whole.

The attention levels, one sentence each:

  needs attention   one thing the main thread spent or lost holds at least 15%
                    of the launch and at least 0.15 s, in every launch (time
                    it sat idle is not time it lost: see `_held`);
  worth a look      the check found something else: smaller, on another
                    thread, or not in every launch;
  a marker to move  the check found only a marker put one level off;
  fine              the check ran and found nothing above its limit;
  not checked       the check could not run on these recordings, or the report
                    is too old to say whether it could.

A check with rows reads its rows, whatever `checked` says: some of a set's
recordings may have lacked what it reads, and the rest have rows. Only an empty
check can be "not checked", and an empty one is "fine" only when its entry says
`checked: true`. An entry with no `checked` at all is from a report written
before echolot recorded it (#441): `analyze` always writes the key now.

A freeze the system itself recorded (`anr`) is always "needs attention".

Why the numbers are what they are is in the comments by `SHARE` and
`FLOOR_MS`, and why only the main thread's own time can be red is by
`_on_main`. The verdict is the launch against `scenario.budget_ms`.

`annotate` writes the result into report.json: per detector `plain` and
`attention`, at the top `verdict`. `render` is the same thing as a list for a
terminal, `echolot report --plain`.
"""

from __future__ import annotations

import copy
import functools
import math
import re
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable

from . import launch as launch_mod
from .merge import metric_of
from .tp import load_detectors

NEEDS_ATTENTION = "needs_attention"
WORTH_A_LOOK = "worth_a_look"
FINE = "fine"
NOT_CHECKED = "not_checked"
# A check whose only rows are near misses of `repeated_work`: advice on where
# a marker sits, not a finding, and not a reason to send it to an agent (#556).
MOVE_MARKER = "move_marker"
LEVELS = (NEEDS_ATTENTION, WORTH_A_LOOK, MOVE_MARKER, FINE, NOT_CHECKED)
# The verdict of a report without a time limit: nothing to hold the window
# against. Its own word, since "not checked" is what a check that could not
# run says (#556).
NO_LIMIT = "no_limit"

# What a row has to hold to need attention: this share of the launch, and at
# least this many milliseconds.
#
# The share is the prototype's 15%. It separated the two causes of a slow first
# launch from the rest, and every red row on the problem launches measured at
# least 280 ms. On its own it is not enough: every launch has a biggest piece,
# and in a short one the biggest piece is a large share. On 15 recordings of a
# healthy cold start (0.44 to 0.65 s) 15% alone marked a launch red 46% of the
# time on three recordings and 60% of the time on one; with the floor, 0% and
# 7%. A healthy launch's largest rows were 55 to 79 ms. The floor sits in the
# gap between those and the 280 ms of the smallest red row, on a log scale.
# Both numbers come from one app; a second app is where to check them next.
SHARE = 0.15
FLOOR_MS = 150.0

# The verdict's own words. Its levels are the checks' levels, for their
# colours, but it judges one thing, the window against the time limit, and
# the checks judge their rows: a red "Needs attention" above a list with no
# check that needs attention read as a contradiction (#824). So the headline
# says which limit it is about.
VERDICT_WORD = {NEEDS_ATTENTION: "Over the limit", WORTH_A_LOOK: "A little over",
                FINE: "Within the limit", NO_LIMIT: "No time limit",
                NOT_CHECKED: "Not checked"}


def verdict_word(level: Any) -> str:
    """What the verdict's level says, in words of the time limit."""
    return VERDICT_WORD.get(level) or str(level or "unknown").replace("_", " ").capitalize()


# The verdict: a launch up to a quarter over its budget is "worth a look", more
# is "needs attention". The median of three recordings of one healthy build
# moved from -4% to +14% (5th to 95th percentile), so a quarter is about twice
# the noise a median of three has.
OVER_BUDGET = 1.25

DETECTOR_DIR = Path(__file__).parent / "sql" / "detectors"

# The generic clean text, for a detector this version does not know.
_CLEAN = "It found nothing above its limit."

# Why an empty check did not look, when the entry has no reason of its own.
_OLD_REPORT = ("This report was written before echolot recorded whether a check could "
               "run. Analyse the recordings again to find out.")
_FAILED = "It failed to run on these recordings."
_COULD_NOT = "It could not run on these recordings."


# ---------------------------------------------------------------------------
# the fixed texts


@functools.lru_cache(maxsize=1)
def _shipped() -> dict[str, tuple[str, str, str]]:
    return {d.id: (d.plain_title, d.plain_why, d.plain_clean)
            for d in load_detectors(DETECTOR_DIR)}


def fixed_texts(det_id: str, title: str = "") -> tuple[str, str, str]:
    """What was checked, how it touches the app, what a clean result says.

    From the .sql header of a shipped detector. A detector this version does
    not ship (a report from a newer one) gets its own title, no second text
    and a generic third: nothing here guesses what it means.
    """
    texts = _shipped().get(det_id)
    if texts and all(texts):
        return texts
    return (title or det_id, "", _CLEAN)


# ---------------------------------------------------------------------------
# numbers as a person says them


def hum(ms: float) -> str:
    """Milliseconds the way a person says them: 0.3 ms, 280 ms, 0.7 s, 12.4 s."""
    if ms >= 100:
        return f"{ms / 1000:.1f} s"
    if ms >= 1:
        return f"{ms:.0f} ms"
    return f"{ms:.1f} ms"


def hum_apart(a: float, b: float) -> tuple[str, str]:
    """Two times said as `hum` says them, unless that makes two different
    times read alike: "takes 0.5 s, you allowed 0.5 s, 4% over" asks a reader
    which number to believe. Then both get the digits that tell them apart:
    whole milliseconds under a second ("479 ms" and "460 ms"), hundredths or
    thousandths of a second above ("1.22 s" and "1.24 s")."""
    said = hum(a), hum(b)
    if said[0] != said[1] or a == b:
        return said
    forms = (["{:.0f} ms", "{:.1f} ms"] if max(a, b) < 1000 else
             ["{s:.2f} s", "{s:.3f} s"]) + ["{:.2f} ms"]
    for form in forms:
        said = form.format(a, s=a / 1000), form.format(b, s=b / 1000)
        if said[0] != said[1]:
            break
    return said


def hum_limit(ms: float) -> str:
    """A time limit the way a person set it: in seconds from 100 ms on, with
    the digits it was given up to whole milliseconds ("0.25 s", "2.25 s",
    "0.5 s", "2.0 s"), not rounded to the tenth `hum` keeps. A limit typed
    as 0.25 s was said as "0.2 s" (#868)."""
    if ms >= 100:
        said = f"{ms / 1000:.3f}".rstrip("0")
        return f"{said}0 s" if said.endswith(".") else f"{said} s"
    return f"{ms:g} ms"


def _ms_of(said: str) -> float:
    number, unit = said.split(" ")
    return float(number) * (1000 if unit == "s" else 1)


def _alike_apart(a: float, b: float) -> tuple[str, str]:
    """`hum_apart`'s forms for a time that reads as its limit, each form taken
    only where it says the limit exactly: "501 ms" and "500 ms", "2.251 s"
    and "2.250 s" (#709)."""
    forms = (["{:.0f} ms", "{:.1f} ms"] if max(a, b) < 1000 else
             ["{s:.2f} s", "{s:.3f} s"]) + ["{:.2f} ms"]
    for form in forms:
        said = form.format(a, s=a / 1000), form.format(b, s=b / 1000)
        if said[0] != said[1] and _ms_of(said[1]) == b:
            return said
    return said


def hum_against(took_ms: float, limit_ms: float, ratio: float) -> tuple[str, str]:
    """A time and its limit as the verdict says them: the limit as it was set
    (`hum_limit`), and the time with at least as many digits, and more where
    the two said would read alike, or where "… times as long" would not be
    their quotient: "takes 0.7 s, you allowed 0.25 s, 2.7 times as long" is
    2.8 by its own numbers (#868). `ratio` is `verdict.ratio`. A share over
    the limit ("25% over") is the key's, as before: under twice the limit the
    times keep the digits that tell them apart and no more."""
    limit = hum_limit(limit_ms)
    shown = limit.split(" ")[0]
    digits = len(shown.split(".")[1]) if limit.endswith(" s") and "." in shown else 1
    tries = ([f"{took_ms / 1000:.{d}f} s" for d in range(digits, 4)] if took_ms >= 100 else []) \
        + [f"{took_ms:.0f} ms", f"{took_ms:.1f} ms", f"{took_ms:.2f} ms"]
    for took in tries:
        a, b = _ms_of(took), _ms_of(limit)
        if a == b and took_ms != limit_ms:
            return _alike_apart(took_ms, limit_ms)
        if took_ms <= limit_ms:
            if a <= b:
                return took, limit
            continue
        if a <= b or b <= 0:
            continue
        if ratio >= 2 and f"{a / b:.1f}" != f"{ratio:.1f}":
            continue
        return took, limit
    return tries[-1], limit


def kernel_name(report: dict[str, Any]) -> str:
    """The name the kernel gives the report's main thread: it keeps 15
    characters of a thread's name, and the main thread of an app carries the
    last 15 of its process's. Empty when the report names no process."""
    return str((report.get("window") or {}).get("process") or "")[-15:]


class _Ctx:
    """What a sentence needs to know about the report it is read from."""

    def __init__(self, report: dict[str, Any]):
        window = report.get("window") or {}
        self.window_ms = float(window.get("duration_ms") or 0)
        self.main = kernel_name(report)
        # A window that is the launch, or that opens on the app's startup,
        # is a launch; any other stretch is a run. The trace holds a startup
        # whenever collect started the app, and a page load or a scroll
        # recorded after it was called a launch (#927).
        self.kind = "launch" if is_launch_window(window) else "run"
        # What a share is a share of. A window whose anchor missed is the
        # recording, whatever the scenario is (#557): "56% of the launch" of
        # such a window is a share of something that is not the launch. The
        # whole trace is the recording by name (#519).
        self.noun = ("recording" if _missed(window) or launch_mod.is_whole(window)
                     else self.kind)
        # The frames drawn in the window, the median over the repeats; None
        # without a frame timeline, or in a report written before it was kept.
        drawn = (window.get("frames") or {}).get("count")
        self.frames = round(float(drawn)) if drawn else None

    @property
    def plural(self) -> str:
        return self.noun + ("es" if self.noun == "launch" else "s")


def is_launch_window(window: dict[str, Any]) -> bool:
    """Is the window the app's launch: built from the launch (`window:
    launch`), or opened at or after the start of the app's startup and
    sharing time with it, as a window from `bindApplication` does? Not a
    window that opens after the startup ended, nor one that opens before the
    app was started, such as one with no start anchor (#927)."""
    if launch_mod.is_launch(window):
        return True
    startup = window.get("startup")
    if not isinstance(startup, dict):
        return False
    began, shared = startup.get("from_window_start_ms"), startup.get("in_window_ms")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (began, shared)):
        # A startup that does not say where it sits against the window, as
        # in a report written before it did: a launch, as it was read then.
        return True
    return shared > 0 and began <= 0


def _anchors(window: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in (window.get("start_anchor"), window.get("end_anchor")) if isinstance(a, dict)]


def _missed(window: dict[str, Any]) -> bool:
    """Did an anchor of the window find nothing, in any repeat, or leave the
    window open to the end of the recording? Then the window, in those
    repeats, is the recording and not the scenario."""
    return any(a.get("missed") or not a.get("matches") or a.get("unfinished")
               for a in _anchors(window))


def _closed(window: dict[str, Any]) -> bool:
    """Does the window end where the scenario does: on a launch the platform
    closed, or on an end anchor that matched in every repeat?

    Anything else ends where the recording does: a window without anchors,
    whose length the script fixes (a scroll), one whose anchor missed, and
    the whole trace.
    """
    if _missed(window):
        return False
    end = window.get("end_anchor")
    return launch_mod.is_launch(window) or isinstance(end, dict)


def _of(missed: Any) -> tuple[int, int] | None:
    try:
        k, n = (int(x) for x in str(missed).split("/"))
    except ValueError:
        return None
    return k, n


def window_warning(report: dict[str, Any]) -> str | None:
    """What a person has to read before any number of the report, when the
    window is not the scenario's: an anchor that found nothing, in some
    repeats or in all, or an end that never closed. None for a window that is
    the scenario's. `report.md` says the same in its header, for an agent."""
    window = report.get("window") or {}
    said = []
    for key, where in (("start_anchor", "starts"), ("end_anchor", "ends")):
        anchor = window.get(key)
        if not isinstance(anchor, dict):
            continue
        glob = _name(anchor.get("glob") or "?")
        seen = _of(anchor.get("missed"))
        if not anchor.get("matches") and anchor.get("before_start"):
            said.append(f"The marker where this scenario ends ({glob}) comes only before the one where "
                        f"it starts, so nothing closed the window: the numbers below run to the end of "
                        f"the recording, not to the end of the scenario. Are the two swapped?")
        elif seen and seen[0] < seen[1]:
            said.append(f"echolot did not find where this scenario {where} ({glob}) in {seen[0]} of "
                        f"{seen[1]} recordings. In those the numbers are about everything the app did "
                        f"in the recording, and they are part of the numbers below.")
        elif seen or not anchor.get("matches"):
            scope = (f" in {seen[0]} of {seen[1]} recordings" if seen and seen[1] > 1 else
                     " in the recording")
            said.append(f"echolot did not find where this scenario {where} ({glob}){scope}, so the "
                        f"numbers below are about everything the app did in the recording, not "
                        f"about the scenario.")
        elif anchor.get("unfinished"):
            said.append(f"This scenario had not reached its end ({glob}) when the recording stopped, "
                        f"so the numbers below run to the end of the recording.")
    return " ".join(said) or None


def _value(row: dict[str, Any]) -> float:
    try:
        return float(row.get(metric_of(row)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _seen(row: dict[str, Any]) -> tuple[int, int] | None:
    """`(k, n)` from a row's `runs` — found in k of n recordings. None for a row
    of a report from one recording, which has none."""
    try:
        k, n = (int(x) for x in str(row["runs"]).split("/"))
    except (KeyError, ValueError):
        return None
    return k, n


def _every_run(row: dict[str, Any]) -> bool:
    seen = _seen(row)
    return seen is None or seen[0] == seen[1]


def _runs(row: dict[str, Any], ctx: _Ctx) -> str:
    seen = _seen(row)
    if seen is None or seen[1] <= 1:
        return ""
    k, n = seen
    return f"in all {n} {ctx.plural}" if k == n else f"in {k} of {n} {ctx.plural}"


def _apart(row: dict[str, Any], ctx: _Ctx) -> str:
    """` in one launch` where a merged row's longest occurrence is one
    repeat's, and nothing otherwise.

    Merged over repeats, `max_ms` is the longest any repeat saw (#512) and
    the totals beside it are medians: the slowest launch's longest call next
    to the median launch's total, so "0.2 s on calls … the longest took
    0.3 s" (#712). Said where the repeats' longest differ; where they are
    all the same, the longest was no larger than any repeat's total.
    """
    values = ((row.get("spread") or {}).get("max_ms") or {}).get("values") or []
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return f" in one {ctx.noun}" if len(nums) > 1 and max(nums) != min(nums) else ""


def _reason(det: dict[str, Any]) -> str:
    """Why an empty check did not look: its own reason, or that it failed, or that
    the report is too old to say."""
    if det.get("checked") is False:
        return det.get("reason") or (_FAILED if det.get("error") else _COULD_NOT)
    return _FAILED if det.get("error") else _OLD_REPORT


def _clause(text: str, runs: str) -> str:
    return f"{text}, {runs}" if runs else text


def _sentence(text: str, runs: str) -> str:
    return _clause(text, runs) + "."


def _share(ms: float, ctx: _Ctx, lead: str = "That is") -> str:
    """` That is 36% of the launch.`, or nothing without a window to say it of."""
    if ctx.window_ms <= 0:
        return ""
    if ms >= ctx.window_ms:
        return f" {lead} as long as the whole {ctx.noun}."
    # Rounded down, so that a sentence saying 15% is one that holds 15%: a
    # piece at 14.6% is "worth a look", and reading "15%" there would not fit.
    pct = int(ms * 100 // ctx.window_ms)
    return f" {lead} {'less than 1' if pct < 1 else pct}% of the {ctx.noun}."


def _name(text: Any) -> str:
    return "`" + str(text).replace("`", "'") + "`"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# whose time a row is


# The location of the main thread's row on the detectors that group by thread
# name and tell it apart by its tid (#511): the kernel's name for it is the
# last fifteen characters of the process's, which nobody reads as the main
# thread, and a worker that never named itself carries it too.
MAIN = "main"
NAMED_MAIN = frozenset({"uninstrumented_cpu", "runnable_starvation", "monitor_contention"})


# Detectors whose rows are, by what they measure, time of the main thread or
# time the user can see: its own work, its waits, a late frame, a freeze.
_MAIN_TIME = frozenset({
    "main_thread_waits", "main_thread_kernel_waits", "main_thread_block",
    "main_thread_outlier", "app_init", "composables", "anr_risk", "frame_jank",
    "anr",
})


def _on_main(det_id: str, row: dict[str, Any], ctx: _Ctx) -> bool:
    """Is this row time the main thread spent or lost, rather than the time of
    some other thread?

    The share of the launch is only a share for the first kind. What another
    thread did is a yardstick against the launch's length, not a part of it:
    the five `uninstrumented_cpu` rows of one first launch add up to 64% of its
    window, more than one thread has, and what those threads were doing ended
    up in `main_thread_waits` where the main thread waited for them. So a row on
    another thread can be worth a look, and cannot by its size alone need
    attention.

    Most detectors say which thread a row is of, in different places: the
    detector itself, a `main` or `background` field, the thread name. An id
    this version does not know is taken for another thread, which asks less.
    """
    if det_id in _MAIN_TIME:
        return True
    detail = str(row.get("detail") or "")
    # `binder_txn` and `gc_pressure` keep the thread in `thread` since #514,
    # and a report written before in `detail`, as `io_wait` once did.
    if det_id in ("binder_txn", "io_wait"):
        return str(row.get("thread") or detail).startswith("main")
    if det_id in NAMED_MAIN:
        # `main` since #511. A report written before then names the main
        # thread as the kernel does, and it is the row without `threads`.
        return row.get("location") == MAIN or (
            "threads" not in row and bool(ctx.main) and row.get("location") == ctx.main)
    if not ctx.main:
        return False
    if det_id == "gc_pressure":
        return str(row.get("thread") or detail) == ctx.main
    if det_id == "repeated_work":
        return detail.rstrip().endswith(f"— on {ctx.main}")
    return False


def _counts(det_id: str, row: dict[str, Any]) -> bool:
    """A near miss of `repeated_work` is the report saying a marker was put one
    level off. It is not a finding, whatever it measures."""
    return not (det_id == "repeated_work"
                and str(row.get("detail") or "").startswith("near miss"))


def _idle_part(row: dict[str, Any]) -> float:
    """The part of a `main_thread_waits` row the main thread spent idle, with
    no slice open, read off its evidence: `idle` is all of it, `67% idle ·
    33% blocked in X` two thirds, `blocked in X` none. The blocked share is
    the one read, so evidence that names a slice is never all idle. Since
    #926 a row holds no idle sleeps and says `blocked in X`; the shares are
    those of a report written before."""
    detail = str(row.get("detail") or "")
    blocked = _BLOCKED_IN.search(detail)
    if blocked:
        return 1 - int(blocked.group(1) or 100) / 100
    return 1.0 if detail.startswith("idle") else 0.0


def _blocked(row: dict[str, Any]) -> float:
    """The waits of a `main_thread_waits` row: all of it since #926, the part
    blocked inside a slice of a row written before, which added the idle
    looper's sleeps to them."""
    return _value(row) * (1 - _idle_part(row))


def _held(det_id: str, row: dict[str, Any], ctx: _Ctx) -> float:
    """How much of a row counts as time the main thread lost.

    Only the waits of a `main_thread_waits` row count, in any window. An
    idle main thread had nothing to do, and the thread that woke it posted
    its next message; it could draw a frame or answer a tap the moment one
    came (#926). Before #926 the row held its idle sleeps too, and time idle
    counted in a window that ends where the scenario does: a WebView's
    thread waking the idle looper read as seconds of waiting in a journey
    the script ended.

    Frames that only waited in a queue (`_stuffed`) count for nothing: they
    were shown without a stutter, and lost no time anyone could see.
    """
    if _stuffed(det_id, row):
        return 0.0
    if det_id == "main_thread_waits":
        return _blocked(row)
    return _value(row)


# The platform's name for frames that waited in a queue behind the frame
# before, and the location of `frame_jank`'s row of them. They reach the
# screen smoothly, a frame or more late: latency, which no level is about
# (#537). `frame_jank` counts nothing past a deadline for them; a report
# written before it did has their delays in the row's total, 16 s of a 40 s
# scroll on one app, and the location is what both have in common.
_STUFFED = "Buffer Stuffing"


def _stuffed(det_id: str, row: dict[str, Any]) -> bool:
    return det_id == "frame_jank" and row.get("location") == _STUFFED


# ---------------------------------------------------------------------------
# the level


def attention(det: dict[str, Any], report: dict[str, Any],
              ctx: _Ctx | None = None) -> str:
    """How much attention a detector's entry in a report needs. See the top."""
    rows = det.get("rows") or []
    if not rows:
        # Nothing found says "fine" only when the check says it looked: not when
        # it says it did not, nor when it failed, nor when the report is older
        # than the key and cannot say.
        looked = det.get("checked") is True and not det.get("error")
        return FINE if looked else NOT_CHECKED
    did = det.get("id", "")
    if did == "anr":
        return NEEDS_ATTENTION
    if not any(_counts(did, row) for row in rows):
        return MOVE_MARKER
    ctx = ctx or _Ctx(report)
    if ctx.window_ms > 0:
        for row in rows:
            ms = _held(did, row, ctx)
            if (_every_run(row) and _counts(did, row) and _on_main(did, row, ctx)
                    and ms >= FLOOR_MS and ms >= SHARE * ctx.window_ms):
                return NEEDS_ATTENTION
    return WORTH_A_LOOK


# ---------------------------------------------------------------------------
# what was found: a template per detector over its rows
#
# Each takes the rows biggest first and says what they say, no more. A number
# of a row that is not one of the sentence's own is left out, and so is a
# cause the row does not name.

_BLOCKED_IN = re.compile(r"(?:(\d+)% )?blocked in (\S+)")
_OUTLIER = re.compile(r"median ([\d.]+) ms of (\d+)")
# Where most of the worst occurrence went, the deepest slice last (#912).
_MOSTLY = re.compile(r"mostly in (?:.* › )?(.+) \(([\d.]+) ms\)$")
_ENTERED = re.compile(r"entered from (\d+)")
_FRAMES = re.compile(r"(\d+) of (\d+) frames")
_NO_SECTION = "(no section)"


def _who(det_id: str, row: dict[str, Any], ctx: _Ctx) -> str:
    return "The main thread" if _on_main(det_id, row, ctx) else _pool(row)


def _pool(row: dict[str, Any]) -> str:
    """A row's thread by name, and how many threads it adds up when that is
    more than one: `DefaultDispatch` alone read as one thread that waited
    3.8 s in a 5.7 s launch (#511)."""
    n = _threads(row)
    return _name(row.get("location")) + (f" ({n} threads together)" if n > 1 else "")


def _threads(row: dict[str, Any]) -> int:
    n = row.get("threads")
    return n if isinstance(n, int) else 1


def _also(rows: list[dict[str, Any]], one: str, many: str, tail: str) -> str:
    n = len(rows) - 1
    return f" {_count(n, one, many)} {tail}" if n > 0 else ""


def _f_waits(rows, ctx):
    # A row is the main thread's waits since #926. A report written before
    # adds the idle looper's sleeps to them and says in `detail` how much, so
    # its rows are read by their waits, and what is left of one is idle.
    top = max(rows, key=_blocked)
    v = _blocked(top)
    idle = _value(top) - v
    if v <= 0:
        # Every row idle: no slice was open, the main thread had nothing to
        # do, and the row's thread posted its next message.
        n = len(rows) - 1
        out = (_clause(f"The main thread had nothing to do for {hum(idle)}", _runs(top, ctx))
               + f", each time until {_name(top['location'])} posted its next message.")
        out += " That is not waiting: while idle, it could still draw and answer taps."
        if n > 0:
            out += f" {_count(n, 'other thread', 'other threads')} woke it, too."
        return out
    n = sum(1 for r in rows if r is not top and _blocked(r) > 0)
    out = _sentence(f"The main thread waited {hum(v)} for {_name(top['location'])}",
                    _runs(top, ctx)) + _share(v, ctx)
    inside = _BLOCKED_IN.search(str(top.get("detail") or ""))
    if inside:
        out += f" It was inside {_name(inside.group(2))} at the time."
    if idle > 0:
        out += (f" The {hum(idle)} it sat idle until that thread posted its next message"
                f" is not counted.")
    if n > 0:
        out += f" It also waited for {_count(n, 'other thread', 'other threads')}."
    return out


def _f_cpu(rows, ctx):
    # A row's time is the thread's CPU outside every slice, not all of its
    # CPU: a main thread that names four fifths of what it does is a row for
    # the fifth, and was not "busy for" that alone.
    top = rows[0]
    unnamed = "on work that nothing in the recording names"
    if len(rows) == 1:
        return _sentence(f"{_who('uninstrumented_cpu', top, ctx)} spent {hum(_value(top))} "
                         f"of processor time {unnamed}", _runs(top, ctx))
    total = sum(_value(r) for r in rows)
    where = ("the main thread" if _on_main("uninstrumented_cpu", top, ctx)
             else _pool(top))
    return (f"{len(rows)} threads or pools of threads spent {hum(total)} of "
            f"processor time between them {unnamed}. "
            + _sentence(f"The largest part is on {where}: "
                        f"{hum(_value(top))}", _runs(top, ctx)))


def _f_outlier(rows, ctx):
    top = rows[0]
    count = int(top.get("count") or 1)
    longest = float(top.get("max_ms") or _value(top))
    apart = _apart(top, ctx)
    usual = _OUTLIER.search(str(top.get("detail") or ""))
    if count <= 1:
        # One occurrence a repeat: its median, and the longest apart.
        once = _value(top) if apart else longest
        out = _sentence(f"One {_name(top['location'])} took {hum(once)}", _runs(top, ctx))
        if apart and longest > once:
            out += f" In one {ctx.noun} it took {hum(longest)}."
    else:
        out = (_clause(f"{_name(top['location'])} took far longer than usual {count} times",
                       _runs(top, ctx))
               + f": {hum(_value(top))} together, {hum(longest)} at worst{apart}.")
    if usual:
        out += f" It usually takes {hum(float(usual.group(1)))}."
    mostly = _MOSTLY.search(str(top.get("detail") or ""))
    if mostly:
        out += (f" Most of the longest was {_name(mostly.group(1))}, "
                f"{hum(float(mostly.group(2)))}.")
    out += _share(_value(top), ctx, "That is" if count <= 1 else "Together that is")
    out += _also(rows, "more step did", "more steps did", "the same.")
    return out


def _f_block(rows, ctx):
    top = rows[0]
    own = " of its own work" if top.get("self_ms") is not None else ""
    out = _sentence(f"The heaviest piece is {_name(top['location'])}: "
                    f"{hum(_value(top))}{own}", _runs(top, ctx)) + _share(_value(top), ctx)
    return out + _also(rows, "more piece took", "more pieces took", "long, too.")


def _f_binder(rows, ctx):
    top = rows[0]
    v = _value(top)
    calls = "calls to the system or to other apps"
    longest = (f" The longest took {hum(float(top['max_ms']))}{_apart(top, ctx)}."
               if top.get("max_ms") else "")
    if _on_main("binder_txn", top, ctx):
        out = _sentence(f"The main thread waited {hum(v)} for {calls}", _runs(top, ctx))
        out += longest + _share(v, ctx)
    else:
        n = _threads(top)
        who = f"{n} background threads together" if n > 1 else "a background thread"
        out = _sentence(f"{_name(top['location'])}, {who}, spent {hum(v)} "
                        f"on {calls}", _runs(top, ctx)) + longest
    return out + _also(rows, "more thread made", "more threads made", "long calls, too.")


def _f_monitor(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('monitor_contention', top, ctx)} waited {hum(v)} for a lock "
                    f"that another thread held", _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}{_apart(top, ctx)}."
    if _on_main("monitor_contention", top, ctx):
        out += _share(v, ctx)
    return out + _also(rows, "more thread waited", "more threads waited", "for locks, too.")


def _piece(row: dict[str, Any]) -> str:
    if row.get("location") == _NO_SECTION:
        return "setup code that nothing in the recording names"
    return _name(row.get("location"))


def _f_repeated(rows, ctx):
    top = rows[0]
    placed = _ENTERED.search(str(top.get("detail") or ""))
    places = f"{placed.group(1)} places" if placed else "more than one place"
    if str(top.get("detail") or "").startswith("near miss"):
        return (f"A marker, {_name(top['location'])}, is reached from {places}, but its "
                f"time differs too much between them to be the same work done twice.")
    v = _value(top)
    out = _sentence(f"{_name(top['location'])} is reached from {places} and costs "
                    f"{hum(v)} together", _runs(top, ctx))
    out += " Each place costs about the same, so the same work may be done more than once."
    if _on_main("repeated_work", top, ctx):
        out += _share(v, ctx)
    return out + _also(rows, "more piece of work is", "more pieces of work are", "like it.")


def _f_init(rows, ctx):
    top = rows[0]
    v = _value(top)
    # Every row is a piece of one stretch, and the stretch took long in the
    # launches where any piece was reported: as many as the most-seen piece.
    seen = max(rows, key=lambda r: (_seen(r) or (1, 1))[0])
    out = _sentence("The setup before the first screen was created took long",
                    _runs(seen, ctx))
    which = "Its only piece" if len(rows) == 1 else f"The largest of its {len(rows)} pieces"
    return out + f" {which} is {_piece(top)}: {hum(v)}." + _share(v, ctx)


def _f_gc(rows, ctx):
    top = rows[0]
    v = _value(top)
    thread = str(top.get("thread") or top.get("detail") or "")
    main = _on_main("gc_pressure", top, ctx)
    where = " on the main thread" if main else (f" on {_name(thread)}" if thread else "")
    out = _clause(f"Memory clean-up ran {_count(int(top.get('count') or 1), 'time', 'times')}"
                  f"{where}", _runs(top, ctx)) + f": {hum(v)} together"
    if top.get("max_ms"):
        out += f", the longest {hum(float(top['max_ms']))}{_apart(top, ctx)}"
    out += "." + (_share(v, ctx) if main else "")
    return out + _also(rows, "more kind", "more kinds", "of clean-up ran a lot, too.")


def _f_runnable(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('runnable_starvation', top, ctx)} was ready to work but had no "
                    f"free processor core for {hum(v)}", _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}{_apart(top, ctx)}."
    if _on_main("runnable_starvation", top, ctx):
        out += _share(v, ctx)
    return out + _also(rows, "more thread waited", "more threads waited", "too.")


def _f_io(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('io_wait', top, ctx)} slept {hum(v)} waiting for the disk",
                    _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}{_apart(top, ctx)}."
    if _on_main("io_wait", top, ctx):
        out += _share(v, ctx)
    return out + _also(rows, "more thread waited", "more threads waited", "for the disk, too.")


def _f_kernel(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"The main thread was held up inside the system for {hum(v)}, until "
                    f"{_name(top['location'])} let it go", _runs(top, ctx)) + _share(v, ctx)
    n = len(rows) - 1
    if n > 0:
        out += f" {_count(n, 'other thread', 'other threads')} let it go after a hold-up, too."
    return out


# Whose deadline the platform says a late frame missed, by the tag its
# evidence leads with.
_WHOSE = {"Self Jank": " because of the app", "Other Jank": " because of the system"}


def _frames_of(row: dict[str, Any], ctx: _Ctx) -> str:
    """`28 of 1446 frames`: the row's count out of the frames drawn in the
    window, both medians in a merged report. The evidence's two numbers are
    its worst repeat's, and gave "1263 of 1446" beside a row that counted
    1254 (#537); they stand in only for what the report does not keep."""
    count = round(float(row.get("count") or 1))
    said = _FRAMES.search(str(row.get("detail") or ""))
    drawn = ctx.frames or (int(said.group(2)) if said else None)
    return f"{count} of {drawn} frames" if drawn else _count(count, "frame", "frames")


def _f_jank(rows, ctx):
    # The frames that missed a deadline first, and those that only waited in
    # a queue after them, whatever either measures: a report written before
    # #537 summed the wait as time past the deadline, and the queued frames
    # were the largest row there.
    late = [r for r in rows if not _stuffed("frame_jank", r)]
    queued = [r for r in rows if _stuffed("frame_jank", r)]
    said = []
    if late:
        top = late[0]
        v = _value(top)
        whose = _WHOSE.get(str(top.get("detail") or "").split(" · ")[0], "")
        out = (_clause(f"{_frames_of(top, ctx)} came late{whose}", _runs(top, ctx))
               + f": {hum(v)} past their deadlines together")
        if top.get("max_ms"):
            out += f", {hum(float(top['max_ms']))} at worst{_apart(top, ctx)}"
        out += "." + _share(v, ctx, "Together that is")
        if _STUFFED in str(top.get("location") or ""):
            out += " That counts the time they waited in a queue behind the frame before, too."
        said.append(out + _also(late, "more kind", "more kinds", "of late frame, too."))
    if queued:
        said.append(_sentence(f"{_frames_of(queued[0], ctx)} waited in a queue behind the frame "
                              f"before", _runs(queued[0], ctx))
                    + " They reached the screen without a stutter, only later than they "
                      "could: the screen follows a touch a little late.")
    return " ".join(said)


def _f_anr(rows, ctx):
    top = rows[0]
    detail = str(top.get("detail") or "")
    when = (f"during the {ctx.noun}" if detail.startswith("inside") else
            f"before the {ctx.noun} began" if detail.startswith("before") else
            f"after the {ctx.noun} ended" if detail.startswith("after") else
            "in the recording")
    seen = _runs(top, ctx)
    tail = f", {seen}" if seen else ""
    if len(rows) == 1 and int(top.get("count") or 1) <= 1:
        return f"The system recorded a freeze {when}: {_name(top['location'])}{tail}."
    return (f"The system recorded {_count(len(rows), 'freeze', 'freezes')}. One of them "
            f"{when}: {_name(top['location'])}{tail}.")


def _f_risk(rows, ctx):
    top = rows[0]
    count = int(top.get("count") or 1)
    longest = float(top.get("max_ms") or _value(top))
    apart = _apart(top, ctx)
    what = ("and nothing in the recording names what it was doing"
            if top.get("location") == "uninstrumented work on the main thread"
            else f"while {_name(top['location'])} was open")
    if count <= 1:
        start = f"The main thread went {hum(_value(top) if apart else longest)} without answering"
    elif apart:
        start = f"The main thread stopped answering {count} times"
    else:
        start = (f"The main thread stopped answering {count} times, "
                 f"for up to {hum(longest)} at a time")
    out = _sentence(f"{start}, {what}", _runs(top, ctx))
    if apart and (count > 1 or longest > _value(top)):
        out += f" The longest{apart}, {hum(longest)}."
    return out + _share(_value(top), ctx, "That is" if count <= 1 else "Together that is")


def _f_composables(rows, ctx):
    top = rows[0]
    own = top.get("self_ms") if top.get("self_ms") is not None else _value(top)
    out = _sentence(f"{_name(top['location'])} spent {hum(float(own))} of its own time "
                    f"building the screen", _runs(top, ctx)) + _share(float(own), ctx)
    return out + _also(rows, "more part", "more parts", "of the screen took long, too.")


def _f_generic(rows, ctx):
    top = rows[0]
    return (f"{_count(len(rows), 'result', 'results')}. "
            + _sentence(f"The largest is {_name(top.get('location'))}: {hum(_value(top))}",
                        _runs(top, ctx)))


# One for every shipped detector, which a test holds to.
FOUND: dict[str, Callable[[list[dict[str, Any]], _Ctx], str]] = {
    "main_thread_waits": _f_waits,
    "uninstrumented_cpu": _f_cpu,
    "main_thread_outlier": _f_outlier,
    "main_thread_block": _f_block,
    "binder_txn": _f_binder,
    "monitor_contention": _f_monitor,
    "repeated_work": _f_repeated,
    "app_init": _f_init,
    "gc_pressure": _f_gc,
    "runnable_starvation": _f_runnable,
    "io_wait": _f_io,
    "main_thread_kernel_waits": _f_kernel,
    "frame_jank": _f_jank,
    "anr": _f_anr,
    "anr_risk": _f_risk,
    "composables": _f_composables,
}


def found(det: dict[str, Any], report: dict[str, Any],
          ctx: _Ctx | None = None) -> str | None:
    """One sentence on what a detector found, from its rows alone. None when it
    found nothing."""
    rows = sorted(det.get("rows") or [], key=lambda r: -_value(r))
    if not rows:
        return None
    return FOUND.get(det.get("id"), _f_generic)(rows, ctx or _Ctx(report))


# ---------------------------------------------------------------------------
# the verdict


def _budget(report: dict[str, Any]) -> float | None:
    value = report.get("budget_ms")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        return None
    return float(value)


def verdict(report: dict[str, Any], ctx: _Ctx | None = None) -> dict[str, Any]:
    """The launch against the budget: how long it takes and what that means.

    With no budget there is nothing to compare with, and the verdict says so
    instead of calling the launch fine: green always means "looked at, and
    fine". A window that is not the scenario's (`window_warning`) is not
    judged at all, and `warning` carries what to read first.
    """
    ctx = ctx or _Ctx(report)
    budget = _budget(report)
    warning = window_warning(report)
    if warning:
        # The window is the recording's, and its length says nothing about the
        # scenario: neither "the launch takes 18.8 s" nor a red "9.4 times as
        # long" against a limit. The platform's own start, when the trace
        # holds one, is the platform's number, and is said as that.
        rest = "Its window is not the scenario's"
        rest += f", so it is not held against the time limit of {hum_limit(budget)}." if budget else "."
        startup = (report.get("window") or {}).get("startup") or {}
        start_ms = startup.get("dur_ms") if isinstance(startup, dict) else None
        if isinstance(start_ms, (int, float)) and not isinstance(start_ms, bool) and start_ms > 0:
            rest += f" The platform measured the start at {hum(float(start_ms))}."
        return {"level": NOT_CHECKED, "budget_ms": budget, "ratio": None, "warning": warning,
                "text": f"There is no measured {ctx.kind} to judge. {rest}"}
    if ctx.window_ms <= 0:
        return {"level": NOT_CHECKED, "budget_ms": budget, "ratio": None, "warning": None,
                "text": f"This recording has no measured {ctx.noun} to judge."}
    if budget is None:
        lead = f"The {ctx.noun} takes {hum(ctx.window_ms)}."
        return {"level": NO_LIMIT, "budget_ms": None, "ratio": None, "warning": None,
                "text": f"{lead} No time limit is set, so there is nothing to compare it with."}
    # The key, and what is said from it: the sentence's ratio is `verdict.ratio`
    # rounded to one decimal, never the rounded window over the rounded budget
    # ("1.9 s" over "0.5 s" is 3.8 where 1919 ms over 520 ms is 3.7).
    ratio = round(ctx.window_ms / budget, 3)
    took, limit = hum_against(ctx.window_ms, budget, ratio)
    lead, allowed = f"The {ctx.noun} takes {took}.", f"You allowed {limit}, so"
    # Judged by the exact times: a window a tenth of a millisecond over the
    # limit has a ratio that rounds to 1.0 and is still not within it.
    if ctx.window_ms <= budget:
        level, rest = FINE, "it is within the limit."
    else:
        level = WORTH_A_LOOK if ratio <= OVER_BUDGET else NEEDS_ATTENTION
        over = round((ratio - 1) * 100)
        rest = (f"it takes {ratio:.1f} times as long." if ratio >= 2 else
                f"it runs {over}% over." if over >= 1 else "it is just over the limit.")
    return {"level": level, "budget_ms": budget, "ratio": ratio, "warning": None,
            "text": f"{lead} {allowed} {rest}"}


# ---------------------------------------------------------------------------
# into the report


def is_read(report: dict[str, Any]) -> bool:
    """Does a report carry the plain reading of this version? One whose
    verdict has no `warning` was read before a missed anchor was (#557), and
    its verdict may judge a window that is not the scenario's."""
    dets = report.get("detectors") or []
    v = report.get("verdict")
    return (isinstance(v, dict) and "warning" in v
            and all("plain" in d and "attention" in d for d in dets))


def annotate(report: dict[str, Any]) -> dict[str, Any]:
    """Writes the plain reading into a report, in place: per detector `plain`
    (title, why, clean, found, reason) and `attention`, and at the top
    `verdict`. Reads only what a report already holds, so it can run again,
    and again after something else has changed an entry (`checked`)."""
    ctx = _Ctx(report)
    for det in report.get("detectors") or []:
        level = attention(det, report, ctx)
        title, why, clean = fixed_texts(det.get("id", ""), det.get("title", ""))
        reason = _reason(det) if level == NOT_CHECKED else None
        try:
            text = found(det, report, ctx)
        except Exception as e:  # a sentence is never worth an analysis that failed
            print(f"[!] plain reading of {det.get('id')}: {e}", file=sys.stderr)
            rows = sorted(det.get("rows") or [], key=lambda r: -_value(r))
            text = _f_generic(rows, ctx) if rows else None
        det["attention"] = level
        det["plain"] = {"title": title, "why": why, "clean": clean,
                        "found": text, "reason": reason}
    report["verdict"] = verdict(report, ctx)
    return report


# ---------------------------------------------------------------------------
# for a terminal

_MARK = {NEEDS_ATTENTION: "!", WORTH_A_LOOK: "~"}


def _weight(det: dict[str, Any]) -> float:
    return max((_value(r) for r in det.get("rows") or []), default=0.0)


def render(report: dict[str, Any], width: int | None = None) -> str:
    """The report as a list a person reads: the verdict, what needs a look
    first, and the rest as a count. A report that already carries the reading
    is read as it says; an older one gets it worked out here."""
    dets = report.get("detectors") or []
    if not is_read(report):
        report = annotate(copy.deepcopy(report))
        dets = report["detectors"]
    width = width or max(60, min(100, shutil.get_terminal_size((100, 24)).columns))

    def wrap(text: str, first: str, rest: str) -> list[str]:
        return textwrap.wrap(text, width=width, initial_indent=first,
                             subsequent_indent=rest) or [first.rstrip()]

    v = report["verdict"]
    label = verdict_word(v["level"]).upper()
    # A window that is not the scenario's is said before anything else.
    lines = (wrap(v["warning"], f"{'WINDOW':<18}", " " * 18) + [""]) if v.get("warning") else []
    lines += wrap(v["text"], f"{label:<18}", " " * 18)
    listed = sorted((d for d in dets if d["attention"] in _MARK),
                    key=lambda d: (LEVELS.index(d["attention"]), -_weight(d)))
    if listed:
        lines.append("")
    for d in listed:
        lines.append(f" {_MARK[d['attention']]}  {d['plain']['title']}")
        lines += wrap(d["plain"]["found"] or "", "    ", "    ")
    fine = sum(1 for d in dets if d["attention"] == FINE)
    blind = [d for d in dets if d["attention"] == NOT_CHECKED]
    parts = []
    if fine:
        parts.append(f"ok {fine} {'check is' if fine == 1 else 'checks are'} fine")
    if blind:
        parts.append(f"-  {len(blind)} could not be checked")
    if parts:
        lines += ["", " " + "     ".join(parts)]
    # A reason shared by several is said once, with their titles: a report from
    # before the `checked` key has one for every silent check.
    reasons: dict[str, list[str]] = {}
    for d in blind:
        reasons.setdefault(d["plain"]["reason"] or _COULD_NOT, []).append(d["plain"]["title"])
    for reason, titles in reasons.items():
        lines += wrap(f"{', '.join(titles)}: {reason}", "    ", "      ")
    off = (report.get("summary") or {}).get("absent_ids") or []
    if off:
        lines += ["", f" {_count(len(off), 'more check is', 'more checks are')} "
                      f"switched off in the config."]
    return "\n".join(lines)
