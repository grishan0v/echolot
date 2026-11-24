"""What a detector reads, and whether the trace holds it (#441).

A detector with no rows says one thing: it ran, and nothing cleared its bar.
Some are silent for another reason: the trace does not hold what they read.
`io_wait` reads the kernel's reason for a sleep, and a recording made without
the platform's state has none. `composables` reads names that are written
only when somebody switched that on, and `frame_jank` a frame timeline that
phones older than Android 12 do not write. Left alone, the two silences read
alike, to an agent and to a person, and a list that marks every silent check
fine would tick the ones that never looked.

So a detector says what it reads, in its header, straight after `@why`:

    -- @needs: platform_state

and `analyze` asks each trace once for each source a planned detector names.
A detector that reads only what every recording `collect` makes holds — the
slices of the app, the threads' states, who woke whom — declares nothing.
What a source is, and how its presence is told, is in `SOURCES` below.

The answer is two keys on the detector's entry in `report.json`:

    "checked": true | false      did it look
    "reason": "…"                only when false: one sentence a person can read

For a set of repeats a detector counts as checked only when every repeat held
what it reads (`merged`). A report written before the keys existed has
neither, which means unknown, and never checked.

Presence is positive evidence only. Where the trace itself cannot say — a
recording with no composable names might be one nobody asked for, or an app
that has none — the recording's own account of itself is used: the config the
tracing service writes into the trace, or what `analyze` already read into the
report's `environment`. Where neither says, the source is not held, and that
is the safe side: a check that is marked "not checked" for nothing costs a
re-recording, and one marked fine that never looked costs the answer.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Callable

# What a detector that failed to run says: it did not look either.
FAILED = ("The check failed on this recording, so it says nothing about it; "
          "the error is in the report.")


class Probe:
    """What a source's test looks at: one trace and what its report already says.

    `environment` is the report's own block for this trace, so that a source
    the report has already measured is not measured twice.
    """

    def __init__(self, tp: Any, environment: dict[str, Any] | None = None):
        self.tp = tp
        self.environment = environment or {}
        self._config: str | None = None
        self._read = False

    def any(self, sql: str) -> bool:
        """Whether the query returns a row. A query the trace cannot answer is no."""
        try:
            return bool(self.tp.query(sql))
        except Exception as e:  # a table this trace_processor does not have
            print(f"[!] needs: {e}", file=sys.stderr)
            return False

    def config(self) -> str | None:
        """The recording's own config as trace_processor prints it back.

        None for a trace that carries none: one made by a tool that does not
        write it, or put together by hand.
        """
        if not self._read:
            self._read = True
            try:
                rows = self.tp.query("SELECT str_value AS text FROM metadata "
                                     "WHERE name = 'trace_config_pbtxt'")
                self._config = (rows[0]["text"] if rows else None) or None
            except Exception as e:  # a trace_processor without the key
                print(f"[!] needs: {e}", file=sys.stderr)
        return self._config

    def categories(self) -> set[str] | None:
        """The atrace categories the recording asked for, None when it does not say."""
        text = self.config()
        if text is None:
            return None
        return set(re.findall(r'atrace_categories:\s*"([^"]+)"', text))


@dataclass(frozen=True)
class Source:
    """Something a trace may or may not hold, and what to say when it does not."""
    id: str
    holds: Callable[[Probe], bool]
    missing: Callable[[Probe], str]
    # What it is and what puts it in a recording, for `echolot explain`
    # (#494): an agent asking about one detector, before any trace is open.
    about: str = ""


def _platform_state(p: Probe) -> bool:
    # `sched/sched_blocked_reason` writes `io_wait` on the state a thread is
    # woken out of uninterruptible sleep, and nothing else does, so one row
    # with a value anywhere in the trace says the event was recorded. The
    # whole trace and not the window: a recording either carried the event or
    # did not, and a window of half a second can hold no sleep of the app's
    # while the phone as a whole held thousands.
    return p.any("SELECT 1 FROM thread_state WHERE io_wait IS NOT NULL LIMIT 1")


def _no_platform_state(p: Probe) -> str:
    return ("The recording does not carry the phone's own state, such as why a "
            "thread went to sleep, so there was nothing for this check to read.")


def _frame_timeline(p: Probe) -> bool:
    # SurfaceFlinger's frames, the app's and its own display frames alike:
    # any row says the data source ran. The app's own frames are not asked
    # for. An app that drew nothing while the recording ran is a quiet
    # detector that looked, and that is a different answer.
    return p.any("SELECT 1 FROM actual_frame_timeline_slice LIMIT 1")


def _no_frame_timeline(p: Probe) -> str:
    return ("The recording carries no frame timeline, the screen's own record "
            "of when each frame was due: phones older than Android 12 do not "
            "write one, and a recording has to ask for it.")


def _composable_names(p: Probe) -> bool:
    # What `analyze` read into `environment.compose_names`: the composable
    # calls on the main thread inside the window, in runtime-tracing's shape.
    # A trace with none cannot tell an app that does not write them from a
    # recording that did not ask, and the detector cannot see either.
    return bool((p.environment.get("compose_names") or {}).get("calls"))


def _no_composable_names(p: Probe) -> str:
    if (p.environment.get("compose_names") or {}).get("asked"):
        return ("The recording asked for the names of composables and none "
                "arrived: the app is not set up to write them, or it has no "
                "composables.")
    return ("The recording does not carry the names of composables, which have "
            "to be switched on when recording.")


# The categories a detector reads, in the words a person has for them.
CATEGORIES = {
    "am": "the system's activity records, where it notes freezes and app starts",
    "dalvik": "the Android runtime's records, where it notes garbage collection "
              "and lock waits",
    "binder_driver": "the records of calls between processes",
}


def _category(name: str) -> Source:
    def holds(p: Probe) -> bool:
        return name in (p.categories() or ())

    def missing(p: Probe) -> str:
        what = CATEGORIES[name]
        if p.categories() is None:
            return (f"The recording does not say which records it asked for, so "
                    f"it cannot be told whether it holds {what}, which this "
                    f"check reads.")
        return f"The recording did not ask for {what}, which this check reads."

    return Source(f"atrace:{name}", holds, missing,
                  f"{CATEGORIES[name]} — the `{name}` atrace category, in "
                  f"`runner.atrace_categories` by default")


SOURCES: dict[str, Source] = {s.id: s for s in (
    Source("platform_state", _platform_state, _no_platform_state,
           "why a thread went to sleep in the kernel, the "
           "`sched/sched_blocked_reason` event: `collect` records it unless "
           "`runner.environment: false`"),
    Source("frame_timeline", _frame_timeline, _no_frame_timeline,
           "SurfaceFlinger's record of every frame, its deadline and what it "
           "took: Android 12 or newer, and `collect` asks for it"),
    Source("composable_names", _composable_names, _no_composable_names,
           "the names `runtime-tracing` writes for every composable call: "
           "`runner.compose_names: true`, for an app that carries it"),
    *(_category(name) for name in CATEGORIES),
)}


def parse(value: str | None) -> tuple[str, ...]:
    """What a header's `-- @needs:` says: source names, comma-separated.

    A name that is not a source is refused when the detector is loaded. Left
    to run, a typo would ask the trace for something nobody defined, and the
    detector would read as checked.
    """
    names = [n.strip() for n in (value or "").split(",") if n.strip()]
    for name in names:
        if name not in SOURCES:
            raise ValueError(f"@needs: '{name}' is not a source. The sources "
                             f"are: {', '.join(SOURCES)}")
    return tuple(dict.fromkeys(names))


def survey(tp: Any, environment: dict[str, Any] | None,
           wanted: set[str]) -> dict[str, str | None]:
    """Each wanted source: None when the trace holds it, else why it does not.

    Only what a detector in this run needs is asked of the trace.
    """
    probe = Probe(tp, environment)
    return {name: None if SOURCES[name].holds(probe) else SOURCES[name].missing(probe)
            for name in sorted(wanted)}


def verdict(needs: tuple[str, ...], held: dict[str, str | None],
            error: str | None = None) -> dict[str, Any]:
    """`checked`, and `reason` when it is false, for one detector on one trace.

    A detector that failed did not look either, so it is not checked, and the
    reason says to read its error. What the trace lacks comes first when both
    are true: that is the cause a person can do something about.
    """
    lacking = [held[n] for n in needs if held.get(n)]
    if lacking:
        return {"checked": False, "reason": " ".join(lacking)}
    if error:
        return {"checked": False, "reason": FAILED}
    return {"checked": True}


def merged(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """`checked` and `reason` for a detector merged from its repeats.

    Checked only when every repeat was. One repeat that lacked what the
    detector reads is a repeat whose silence says nothing, and its rows are in
    the medians all the same, so the set says so, with how many. A repeat from
    a report that carries no `checked` is unknown, which is never enough to
    call the set checked: the result then has neither key.
    """
    known = [r for r in runs if "checked" in r]
    if not known:
        return {}
    lacking = [r for r in known if r["checked"] is False]
    if not lacking:
        return {"checked": True} if len(known) == len(runs) else {}
    reasons = list(dict.fromkeys(r.get("reason") or FAILED for r in lacking))
    reason = " ".join(reasons)
    if len(lacking) < len(runs):
        reason = f"{reason.rstrip('.')} ({len(lacking)} of the {len(runs)} recordings)."
    return {"checked": False, "reason": reason}


def could_not_run(detector: dict[str, Any] | None) -> bool:
    """A detector the trace did not give what it reads.

    Not one that failed: its `error` has its own path through the report and
    through `compare`, and a merged detector that failed in some repeats and
    ran in the rest is compared on those rows, as it always was.
    """
    return bool(detector and detector.get("checked") is False
                and not detector.get("error"))


def said(detectors: list[dict[str, Any]]) -> tuple[int, str] | None:
    """How many detectors could not run, and why, in one run of sentences.

    Grouped by the reason, which is a sentence of its own and names what is
    missing, so that detectors lacking the same thing are said together:
    "`io_wait`, `main_thread_kernel_waits` — The recording does not carry …".
    None when every one looked.
    """
    groups: dict[str, list[str]] = {}
    for d in detectors:
        if could_not_run(d):
            groups.setdefault(d.get("reason") or FAILED, []).append(d["id"])
    if not groups:
        return None
    return (sum(len(ids) for ids in groups.values()),
            " ".join(f"{', '.join(f'`{i}`' for i in ids)} — {reason}"
                     for reason, ids in groups.items()))


def line(detectors: list[dict[str, Any]]) -> str | None:
    """The line report.md says it with, next to the one about detectors turned off."""
    found = said(detectors)
    if found is None:
        return None
    count, why = found
    return (f"> ⚠️ {count} detector(s) could not run, because the trace does not "
            f"hold what they read. Their silence says nothing about the app. "
            f"{why} A recording that holds what they read lets them look; until "
            f"then those checks are open.")
