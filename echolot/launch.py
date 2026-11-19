"""The window of a launch scenario, taken from the trace and not from anchors.

`scenario.window: launch` is the one scenario whose window nobody has to
name. The hardest field before a first report is where the window ends: a
person on their own cannot say which slice means "ready" before they have
seen a trace, and for how the app opens they do not have to. The platform
records every launch, from the intent to the first frame, and an app that
knows when its content is there says so with `reportFullyDrawn()`.

The rule, decided on 158 real traces of launches (#446):

- the window starts where the launch starts, as the platform records it: the
  intent, before the app's process exists;
- it ends at the later of two moments: the launch's end, and the end of the
  app's first main-thread `reportFullyDrawn` slice at or after the launch's
  start. Where the app's slice is missing, system_server's record of the
  same call stands in for it. Where neither exists, the launch's end closes
  the window.

Nobody chooses between the two ends, and the report says which one the trace
gave: `window.defined_by` is `launch` or `launch+reportFullyDrawn`. The two
cover different stretches, so `compare` refuses one against the other.

The call is read from its slice and not from Perfetto's time to full
display. That table needs frame-timeline data, which most recordings here
lacked, and it adds one frame to the same moment.

The bounds are computed here, in Python, between the two SQL passes:
`context.sql` is kept to the old, stable tables on purpose, and the launch
comes from a standard-library module. Everything after is unchanged. No
detector reads an anchor; they read the window's two numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ConfigError
from .tp import sql_value

# `window.defined_by`: what closed a launch window. A window cut by the
# config's anchors has no such key.
LAUNCH = "launch"
FULLY_DRAWN = "launch+reportFullyDrawn"

# `window.report_fully_drawn`: where the call was read. None when the trace
# holds no call after the launch began.
APP = "app"
SYSTEM_SERVER = "system_server"

# A slice still open when the recording stopped ends with the trace, as an
# anchor's does (context.sql).
_END = "CASE WHEN dur < 0 THEN (SELECT end_ts FROM trace_bounds) ELSE ts + dur END"


def find(tp, upid: int, trace: str | None = None) -> dict[str, Any]:
    """The launch window of the process `upid`: its bounds and what closed it.

    Runs after `context.sql`, whose `_proc` and `_slice` it reads. Raises
    ConfigError for a trace that holds no launch of the process: there is no
    window to fall back to, and the whole trace under a scenario called a
    launch would be a report about something else.

    The launch is the process's own: the one the standard library ties to
    its main thread, and the first of them when the process was brought to
    the front more than once. A launch the library could tie to no process
    is taken by the package's name instead.
    """
    process = tp.query("SELECT name FROM _proc")[0]["name"] or ""
    package = process.split(":", 1)[0]
    try:
        tp.exec_script("INCLUDE PERFETTO MODULE android.startup.startups;")
        launches = tp.query(f"""
            SELECT s.startup_id AS startup_id, s.ts AS ts, s.ts_end AS ts_end,
                   s.package AS package,
                   EXISTS (SELECT 1 FROM android_startup_processes p
                            WHERE p.startup_id = s.startup_id
                              AND p.upid = {int(upid)}) AS own
            FROM android_startups s
            WHERE s.dur > 0
            ORDER BY s.ts
        """)
    except Exception as e:  # a trace_processor without the module
        raise ConfigError(
            f"`window: launch` reads the launch from trace_processor's "
            f"`android.startup.startups` module, and this one could not give "
            f"it: {e}") from e
    of_package = [s for s in launches if s["package"] == package]
    mine = [s for s in launches if s["own"]] or of_package
    if not mine:
        where = f" {Path(trace).name}" if trace else ""
        raise ConfigError(
            f"no launch of {process} in trace{where}, so `window: launch` has "
            f"nothing to take the window from. The platform records a launch "
            f"when the app is started inside the recording: `runner.mode: "
            f"launch` does that, and so does a startup macrobenchmark. For a "
            f"scenario that is not a launch, name `start` and `end` instead.")
    chosen = mine[0]
    start, launch_end = chosen["ts"], chosen["ts_end"]
    # The call belongs to this launch only up to the next one of the same
    # app: an app brought to the front twice calls it once, if at all.
    later = [s["ts"] for s in of_package + mine if s["ts"] > start]
    before = f" AND ts < {min(later)}" if later else ""

    source = None
    call = tp.query(f"""
        SELECT {_END} AS ts_end FROM _slice
        WHERE is_main_thread = 1 AND name GLOB 'reportFullyDrawn*'
          AND ts >= {start}{before}
        ORDER BY ts, depth LIMIT 1
    """)
    if call:
        source = APP
    else:
        # What ActivityManager writes as it takes the same call, on one of
        # its binder threads. An Activity that is not androidx's leaves no
        # slice of its own on some versions, and this one is always there.
        named = chosen["package"] or package
        call = tp.query(f"""
            SELECT {_END} AS ts_end FROM slice
            WHERE name = 'ActivityManager:ReportingFullyDrawn {sql_value(named)}'
              AND ts >= {start}{before}
            ORDER BY ts LIMIT 1
        """)
        if call:
            source = SYSTEM_SERVER
    # The later of the two. A call made before the first frame is the app's
    # claim, and the launch is not over: its end still closes the window.
    drawn = call[0]["ts_end"] if call else None
    closed_on_call = drawn is not None and drawn > launch_end
    return {
        "ts_start": start,
        "ts_end": drawn if closed_on_call else launch_end,
        "defined_by": FULLY_DRAWN if closed_on_call else LAUNCH,
        "report_fully_drawn": source,
        "startup_id": chosen["startup_id"],
    }


def facts(tp, window: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """What a launch window says about itself in `report.json`.

    `defined_by` and `report_fully_drawn`, and one number put into the main
    thread's budget: `before_process_ms`. A window that opens at the intent
    starts before the app's process exists, a median 62 ms before on the
    launches this was decided on. The main thread has no state for that
    stretch, so the budget accounts for 85 to 94% of such a window, and the
    warning about a window only partly accounted for fired on 26 launch
    reports of 38. That share is the launch itself and no hole in the
    recording, so it is measured and named: from the window's start to the
    main thread's first state in it. Nothing for a process that was already
    there, as on a warm or hot start.
    """
    budget = window.get("main_thread")
    if budget:
        first = tp.query(
            "SELECT MIN(t.ts) AS ts FROM _tstate_win t CROSS JOIN _proc p "
            "WHERE t.tid = p.pid AND t.dur > 0")
        if first and first[0]["ts"] is not None:
            budget["before_process_ms"] = round(
                max(0, first[0]["ts"] - window["ts_start"]) / 1e6, 2)
    return {"defined_by": found["defined_by"],
            "report_fully_drawn": found["report_fully_drawn"]}


# --- what the report says about it ------------------------------------------------

def merged(windows: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """What closed the window, for every repeat at once.

    A check like the anchors', so the worst case speaks. Repeats that did
    not close the same way are two measurements in one set: `defined_by`
    names both, joined, and `defined_by_runs` counts each, which is what the
    report warns about and `compare` refuses. Where the call was read from
    is the least any repeat had: none, then system_server's record, then the
    app's own slice.
    """
    defined = [w["defined_by"] for w in windows if w.get("defined_by")]
    if not defined:
        return {}
    kinds = sorted(set(defined))
    out: dict[str, Any] = {"defined_by": " and ".join(kinds)}
    if len(kinds) > 1:
        out["defined_by_runs"] = {k: f"{defined.count(k)}/{total}" for k in kinds}
    sources = [w.get("report_fully_drawn") for w in windows if w.get("defined_by")]
    out["report_fully_drawn"] = min(sources, key=(None, SYSTEM_SERVER, APP).index)
    return out


def lines(window: dict[str, Any]) -> list[str]:
    """What bounds a window that is the launch, where an anchored one names its anchors."""
    by = window.get("defined_by")
    if not by:
        return []
    mixed = window.get("defined_by_runs")
    if mixed:
        closed = {FULLY_DRAWN: "on `reportFullyDrawn`", LAUNCH: "at the launch's end"}
        counted = " and ".join(
            f"{closed.get(kind, f'`{kind}`')} in {runs.split('/')[0]}"
            for kind, runs in sorted(mixed.items(), reverse=True))
        total = next(iter(mixed.values())).split("/")[1]
        return [
            f"> ⚠️ The launch window did not close the same way in every repeat: "
            f"{counted} of {total}. Those are two windows, the medians below "
            f"mix them, and `compare` refuses this report. Look for the repeat "
            f"where the app did not call `reportFullyDrawn`, or called it "
            f"before its first frame."
        ]
    head = "Window: the launch as the platform records it, from the intent to "
    source = window.get("report_fully_drawn")
    if by == FULLY_DRAWN and source == SYSTEM_SERVER:
        return [head + "`reportFullyDrawn` as system_server records it. The "
                       "app's own slice of that call is not in the trace."]
    if by == FULLY_DRAWN:
        return [head + "the end of the app's `reportFullyDrawn` call, which "
                       "came after the first frame."]
    if source:
        return [head + "the launch's end, its first frame. `reportFullyDrawn` "
                       "was called before that frame, so it closes nothing."]
    return [head + "the launch's end, its first frame. The trace holds no "
                   "`reportFullyDrawn` call after the launch began."]


# --- whether two reports measured one window ---------------------------------------

# A window cut by the config's anchors carries no `defined_by`: a report from
# such a scenario is what it always was, and one written before the key
# existed reads the same way.
ANCHORS = "anchors"

# How each definition is said in a sentence.
_IN_WORDS = {
    ANCHORS: "by the config's start and end anchors",
    LAUNCH: "as the launch, closed at its end",
    FULLY_DRAWN: "as the launch, closed on `reportFullyDrawn`",
}


def definition(report: dict[str, Any]) -> str:
    """How a report's window was defined: `anchors`, or its `window.defined_by`."""
    return (report.get("window") or {}).get("defined_by") or ANCHORS


def differ(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """Why two reports' windows cannot be held against each other, or None.

    Two definitions cover two stretches of the launch. Against today's
    anchors, a launch window starts a median 137 ms earlier, at the intent
    and not at `bindApplication`; a launch that ran on to `reportFullyDrawn`
    is a median 56 ms longer than one closed at its end, with a row of its
    own in 15 traces of 35. Every row moves for that reason alone, and
    nothing else in two such reports says so: the scenario's name is the
    same, and a changed config only warns.

    A set whose repeats did not agree among themselves is no single
    definition, and is refused against anything.
    """
    sides = [(side, definition(r), (r.get("window") or {}).get("defined_by_runs"))
             for side, r in (("before", before), ("after", after))]
    mixed = [side for side, _, runs in sides if runs]
    if mixed:
        return (f"the {' and the '.join(mixed)} report's repeats did not close "
                f"their launch windows the same way ("
                + "; ".join(", ".join(f"`{k}` in {v}" for k, v in runs.items())
                            for _, _, runs in sides if runs)
                + "), so its medians mix two windows")
    (_, b, _), (_, a, _) = sides
    if b == a:
        return None
    return (f"the windows were defined differently: "
            f"{_IN_WORDS.get(b, f'`{b}`')} before, "
            f"{_IN_WORDS.get(a, f'`{a}`')} after")
