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
                    of the launch and at least 0.15 s, in every launch;
  worth a look      the check found something else: smaller, on another
                    thread, or not in every launch;
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

from .report import metric_of
from .tp import load_detectors

NEEDS_ATTENTION = "needs_attention"
WORTH_A_LOOK = "worth_a_look"
FINE = "fine"
NOT_CHECKED = "not_checked"
LEVELS = (NEEDS_ATTENTION, WORTH_A_LOOK, FINE, NOT_CHECKED)

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


class _Ctx:
    """What a sentence needs to know about the report it is read from."""

    def __init__(self, report: dict[str, Any]):
        window = report.get("window") or {}
        self.window_ms = float(window.get("duration_ms") or 0)
        # The kernel keeps 15 characters of a thread's name, and the main
        # thread of an app carries the last 15 of its process's.
        self.main = str(window.get("process") or "")[-15:]
        # A startup is a launch; any other stretch is a run.
        self.noun = "launch" if window.get("startup") else "run"

    @property
    def plural(self) -> str:
        return self.noun + ("es" if self.noun == "launch" else "s")


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
    if det_id == "binder_txn":
        return detail.startswith("main")
    if det_id == "io_wait":
        return str(row.get("thread") or detail).startswith("main")
    if not ctx.main:
        return False
    if det_id in ("uninstrumented_cpu", "runnable_starvation", "monitor_contention"):
        return row.get("location") == ctx.main
    if det_id == "gc_pressure":
        return detail == ctx.main
    if det_id == "repeated_work":
        return detail.rstrip().endswith(f"— on {ctx.main}")
    return False


def _counts(det_id: str, row: dict[str, Any]) -> bool:
    """A near miss of `repeated_work` is the report saying a marker was put one
    level off. It is not a finding, whatever it measures."""
    return not (det_id == "repeated_work"
                and str(row.get("detail") or "").startswith("near miss"))


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
    ctx = ctx or _Ctx(report)
    if ctx.window_ms > 0:
        for row in rows:
            ms = _value(row)
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
_ENTERED = re.compile(r"entered from (\d+)")
_FRAMES = re.compile(r"(\d+) of (\d+) frames")
_NO_SECTION = "(no section)"


def _who(det_id: str, row: dict[str, Any], ctx: _Ctx) -> str:
    return "The main thread" if _on_main(det_id, row, ctx) else _name(row.get("location"))


def _also(rows: list[dict[str, Any]], one: str, many: str, tail: str) -> str:
    n = len(rows) - 1
    return f" {_count(n, one, many)} {tail}" if n > 0 else ""


def _f_waits(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"The main thread waited {hum(v)} for {_name(top['location'])}",
                    _runs(top, ctx)) + _share(v, ctx)
    inside = _BLOCKED_IN.search(str(top.get("detail") or ""))
    # "67% idle · 33% blocked in X": inside X for a third of it is not "at the time".
    if inside and int(inside.group(1) or 100) >= 50:
        out += f" It was inside {_name(inside.group(2))} at the time."
    n = len(rows) - 1
    if n > 0:
        out += f" It also waited for {_count(n, 'other thread', 'other threads')}."
    return out


def _f_cpu(rows, ctx):
    top = rows[0]
    unnamed = "mostly doing work that nothing in the recording names"
    if len(rows) == 1:
        return _sentence(f"{_name(top['location'])} was busy for {hum(_value(top))} "
                         f"of processor time, {unnamed}", _runs(top, ctx))
    total = sum(_value(r) for r in rows)
    return (f"{len(rows)} threads or pools of threads were busy for {hum(total)} of "
            f"processor time between them, {unnamed}. "
            + _sentence(f"The busiest is {_name(top['location'])}: {hum(_value(top))}",
                        _runs(top, ctx)))


def _f_outlier(rows, ctx):
    top = rows[0]
    count = int(top.get("count") or 1)
    longest = float(top.get("max_ms") or _value(top))
    usual = _OUTLIER.search(str(top.get("detail") or ""))
    out = (_sentence(f"One {_name(top['location'])} took {hum(longest)}", _runs(top, ctx))
           if count <= 1 else
           _clause(f"{_name(top['location'])} took far longer than usual {count} times",
                   _runs(top, ctx))
           + f": {hum(_value(top))} together, {hum(longest)} at worst.")
    if usual:
        out += f" It usually takes {hum(float(usual.group(1)))}."
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
    longest = f" The longest took {hum(float(top['max_ms']))}." if top.get("max_ms") else ""
    if _on_main("binder_txn", top, ctx):
        out = _sentence(f"The main thread waited {hum(v)} for {calls}", _runs(top, ctx))
        out += longest + _share(v, ctx)
    else:
        out = _sentence(f"{_name(top['location'])}, a background thread, spent {hum(v)} "
                        f"on {calls}", _runs(top, ctx)) + longest
    return out + _also(rows, "more thread made", "more threads made", "long calls, too.")


def _f_monitor(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('monitor_contention', top, ctx)} waited {hum(v)} for a lock "
                    f"that another thread held", _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}."
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
    thread = str(top.get("detail") or "")
    main = _on_main("gc_pressure", top, ctx)
    where = " on the main thread" if main else (f" on {_name(thread)}" if thread else "")
    out = _clause(f"Memory clean-up ran {_count(int(top.get('count') or 1), 'time', 'times')}"
                  f"{where}", _runs(top, ctx)) + f": {hum(v)} together"
    if top.get("max_ms"):
        out += f", the longest {hum(float(top['max_ms']))}"
    out += "." + (_share(v, ctx) if main else "")
    return out + _also(rows, "more kind", "more kinds", "of clean-up ran a lot, too.")


def _f_runnable(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('runnable_starvation', top, ctx)} was ready to work but had no "
                    f"free processor core for {hum(v)}", _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}."
    if _on_main("runnable_starvation", top, ctx):
        out += _share(v, ctx)
    return out + _also(rows, "more thread waited", "more threads waited", "too.")


def _f_io(rows, ctx):
    top = rows[0]
    v = _value(top)
    out = _sentence(f"{_who('io_wait', top, ctx)} slept {hum(v)} waiting for the disk",
                    _runs(top, ctx))
    if top.get("max_ms"):
        out += f" The longest wait was {hum(float(top['max_ms']))}."
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


def _f_jank(rows, ctx):
    top = rows[0]
    v = _value(top)
    frames = _FRAMES.search(str(top.get("detail") or ""))
    late = (f"{frames.group(1)} of {frames.group(2)} frames came late" if frames else
            f"{_count(int(top.get('count') or 1), 'frame', 'frames')} came late")
    out = _clause(late, _runs(top, ctx)) + f": {hum(v)} past their deadlines together"
    if top.get("max_ms"):
        out += f", {hum(float(top['max_ms']))} at worst"
    out += "." + _share(v, ctx, "Together that is")
    return out + _also(rows, "more kind", "more kinds", "of late frame, too.")


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
    what = ("and nothing in the recording names what it was doing"
            if top.get("location") == "uninstrumented work on the main thread"
            else f"while {_name(top['location'])} was open")
    start = (f"The main thread went {hum(longest)} without answering" if count <= 1 else
             f"The main thread stopped answering {count} times, for up to {hum(longest)} at a time")
    return (_sentence(f"{start}, {what}", _runs(top, ctx))
            + _share(_value(top), ctx, "That is" if count <= 1 else "Together that is"))


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
    fine".
    """
    ctx = ctx or _Ctx(report)
    budget = _budget(report)
    if ctx.window_ms <= 0:
        return {"level": NOT_CHECKED, "budget_ms": budget, "ratio": None,
                "text": f"This recording has no measured {ctx.noun} to judge."}
    lead = f"The {ctx.noun} takes {hum(ctx.window_ms)}."
    if budget is None:
        return {"level": NOT_CHECKED, "budget_ms": None, "ratio": None,
                "text": f"{lead} No time limit is set, so there is nothing to compare it with."}
    # The key, and what is said from it: the sentence's ratio is `verdict.ratio`
    # rounded to one decimal, never the rounded window over the rounded budget
    # ("1.9 s" over "0.5 s" is 3.8 where 1919 ms over 520 ms is 3.7).
    ratio = round(ctx.window_ms / budget, 3)
    allowed = f"You allowed {hum(budget)}, so"
    if ratio <= 1:
        level, rest = FINE, "it is within the limit."
    else:
        level = WORTH_A_LOOK if ratio <= OVER_BUDGET else NEEDS_ATTENTION
        over = round((ratio - 1) * 100)
        rest = (f"it takes {ratio:.1f} times as long." if ratio >= 2 else
                f"it runs {over}% over." if over >= 1 else "it is just over the limit.")
    return {"level": level, "budget_ms": budget, "ratio": ratio,
            "text": f"{lead} {allowed} {rest}"}


# ---------------------------------------------------------------------------
# into the report


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

_LABEL = {NEEDS_ATTENTION: "NEEDS ATTENTION", WORTH_A_LOOK: "WORTH A LOOK",
          FINE: "FINE", NOT_CHECKED: "NOT CHECKED"}
_MARK = {NEEDS_ATTENTION: "!", WORTH_A_LOOK: "~"}


def _weight(det: dict[str, Any]) -> float:
    return max((_value(r) for r in det.get("rows") or []), default=0.0)


def render(report: dict[str, Any], width: int | None = None) -> str:
    """The report as a list a person reads: the verdict, what needs a look
    first, and the rest as a count. A report that already carries the reading
    is read as it says; an older one gets it worked out here."""
    dets = report.get("detectors") or []
    if "verdict" not in report or not all("plain" in d and "attention" in d for d in dets):
        report = annotate(copy.deepcopy(report))
        dets = report["detectors"]
    width = width or max(60, min(100, shutil.get_terminal_size((100, 24)).columns))

    def wrap(text: str, first: str, rest: str) -> list[str]:
        return textwrap.wrap(text, width=width, initial_indent=first,
                             subsequent_indent=rest) or [first.rstrip()]

    v = report["verdict"]
    label = _LABEL.get(v["level"]) or str(v["level"]).upper().replace("_", " ")
    lines = wrap(v["text"], f"{label:<18}", " " * 18)
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
