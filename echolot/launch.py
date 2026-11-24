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

The other window with no anchor is the whole trace (#519): `window: whole`,
or `analyze --whole-trace` for a recording made outside any scenario. Its
`defined_by` is `whole`, and it is the one window a report may call the
whole trace. One cut by anchors that matched nothing runs over the process's
own first and last slices instead, and says so (#614).
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
# The whole trace, from its first moment to its last.
WHOLE = "whole"

# `window.report_fully_drawn`: where the call was read. None when the trace
# holds no call after the launch began.
APP = "app"
SYSTEM_SERVER = "system_server"

# A slice still open when the recording stopped ends with the trace, as an
# anchor's does (context.sql).
_END = "CASE WHEN dur < 0 THEN (SELECT end_ts FROM trace_bounds) ELSE ts + dur END"

# The packages the system's permission dialog belongs to: the permission
# controller from Android 10 on, under Google's name or the platform's, and
# the package installer before it. An app that asks for a permission before
# its first frame has the dialog started over it, and the platform's launch
# ends on the dialog: the launch the trace records, and the activity
# `am start -W` names, are the permission controller's (#810).
PERMISSION_DIALOGS = ("com.google.android.permissioncontroller",
                      "com.android.permissioncontroller",
                      "com.android.packageinstaller")

# The permission an app asks for at its start on Android 13 and later most
# often: the one that took the launches of #810.
NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"


def permission_dialog(name: str | None) -> bool:
    """Whether a package, or a component `pkg/Class`, is the system's
    permission dialog's."""
    return bool(name) and name.strip().split("/", 1)[0] in PERMISSION_DIALOGS


def grant_said(package: str, ungranted: list[str] | None = None) -> str:
    """What to do about a permission dialog that takes the app's launch: grant
    the permission once, before recording, and the dialog does not come again.

    `ungranted` is the runtime permissions the app held without a grant when
    `collect` saw the dialog, as `dumpsys package` listed them; the dialog
    asks for one of them, and the dump does not say which. Without them the
    command names the usual one and how to list the rest.
    """
    if ungranted:
        first = NOTIFICATIONS if NOTIFICATIONS in ungranted else ungranted[0]
        others = [p for p in ungranted if p != first]
        shown = ", ".join(f"`{p}`" for p in others[:4])
        more = f" and {len(others) - 4} more" if len(others) > 4 else ""
        rest = (f" (it held {shown}{more} ungranted too; the dialog asks for one of "
                f"them)" if others else "")
        return (f"grant it once before recording, `adb shell pm grant {package} "
                f"{first}`{rest}, or tap Allow on the phone")
    return (f"grant it once before recording, `adb shell pm grant {package} "
            f"<permission>`, or tap Allow on the phone. On Android 13 and later the "
            f"one an app asks for at its start is most often `{NOTIFICATIONS}`; "
            f"`adb shell dumpsys package {package}` lists the ones it lacks as "
            f"`granted=false` under `runtime permissions`")


def _no_launch(process: str, trace: str | None, launches: list[dict]) -> str:
    """Why the trace holds no launch of `process`, from what it holds instead
    and from what `collect` kept beside it, and what to do.

    A permission dialog started over the app before its first frame takes the
    launch: the trace's launch is the permission controller's, and the
    sentence used to send a person recorded with `runner.mode: launch` to
    set `runner.mode: launch` (#810). That advice is kept for a trace whose
    recording is not known to have started the app.
    """
    from . import recording

    package = process.split(":", 1)[0]
    where = f" {Path(trace).name}" if trace else ""
    head = f"no launch of {process} in trace{where}"
    record = recording.kept(trace, "launch") if trace else None
    record = record if isinstance(record, dict) else {}
    others = list(dict.fromkeys(str(s["package"]) for s in launches
                                if s.get("package") and s["package"] != package))
    dialog = next((p for p in others if permission_dialog(p)), None)
    if dialog is None and permission_dialog(record.get("activity")):
        dialog = str(record["activity"]).split("/", 1)[0]
    if dialog:
        ungranted = record.get("ungranted")
        return (f"{head}: its launch is {dialog}'s. A permission dialog came up over "
                f"the app before its first frame, and the platform's launch ended on "
                f"the dialog, so `window: launch` has nothing of the app's to take the "
                f"window from, and every recording will hold the same until the "
                f"permission is granted: "
                f"{grant_said(package, ungranted if isinstance(ungranted, list) else None)}. "
                f"Then record again.")
    held = (f"; the launches it holds are of {', '.join(others)}" if others else "")
    started = recording.kept(trace, "runner") == "launch" if trace else False
    how = ("`collect` started the app inside this recording (`runner.mode: launch`), "
           "and the platform recorded no launch of it as the app's: `echolot probe` "
           "on the trace lists what it holds" if started else
           "The platform records a launch when the app is started inside the "
           "recording: `runner.mode: launch` does that, and so does a startup "
           "macrobenchmark")
    return (f"{head}{held}, so `window: launch` has nothing to take the window "
            f"from. {how}. For a scenario that is not a launch, name `start` and "
            f"`end` instead.")


class NoLaunch(ConfigError):
    """A trace with no launch of the process, under `window: launch`: the
    window cannot be taken, and the message says why and what to do."""


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
        raise NoLaunch(_no_launch(process, trace, launches))
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


def whole(tp) -> dict[str, Any]:
    """The bounds of the whole trace, as a window with nothing to cut it.

    `trace_bounds`, the recording's own first and last moments, and not the
    process's first and last slices: those are what a window whose anchors
    matched nothing runs over, and that window is not the whole trace.
    """
    rows = tp.query("SELECT start_ts AS ts_start, end_ts AS ts_end FROM trace_bounds")
    return {**rows[0], "defined_by": WHOLE}


def is_launch(window: dict[str, Any]) -> bool:
    """Whether the window is a launch: closed by the trace's launch, in one
    way or, over repeats that disagreed, in two."""
    by = window.get("defined_by")
    return bool(by) and by != WHOLE


def is_whole(window: dict[str, Any]) -> bool:
    """Whether the window is the whole trace (`window: whole`, `--whole-trace`)."""
    return window.get("defined_by") == WHOLE


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

    The whole trace says only what it is. Its start is no intent, and a main
    thread with no state there may have been asleep since before the
    recording began: that stretch is not named, and the budget's warning
    about a window partly accounted for says the process started inside it.
    """
    if found.get("defined_by") == WHOLE:
        return {"defined_by": WHOLE}
    budget = window.get("main_thread")
    if budget:
        first = tp.query(
            "SELECT MIN(t.ts) AS ts FROM _tstate_win t CROSS JOIN _proc p "
            "WHERE t.tid = p.pid AND t.dur > 0")
        if first and first[0]["ts"] is not None:
            budget["before_process_ms"] = round(
                max(0, first[0]["ts"] - window["ts_start"]) / 1e6, 2)
    out = {"defined_by": found["defined_by"],
           "report_fully_drawn": found["report_fully_drawn"]}
    drawn = ended_on(tp, window)
    if drawn:
        out["activity"] = drawn
    return out


def ended_on(tp, window: dict[str, Any]) -> str | None:
    """The activity the launch ended on, as the trace has it: the class of the
    main thread's last `performCreate:` inside the window (#909).

    A launch the app took on to another activity by itself creates both, the
    one started first: on Wikipedia's, `performCreate:…MainActivity` and then
    `…PageActivity`, the page it opened on its own. Android 12 and on name the
    class in that slice; an older trace has none, and says nothing here.
    """
    start, end = window.get("ts_start"), window.get("ts_end")
    if start is None or end is None:
        return None
    rows = tp.query(f"""
        SELECT name FROM _slice
        WHERE is_main_thread = 1 AND name GLOB 'performCreate:*'
          AND ts >= {start} AND ts < {end}
        ORDER BY ts DESC LIMIT 1
    """)
    name = rows[0]["name"].split(":", 1)[1].strip() if rows else ""
    return name or None


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
    if kinds == [WHOLE]:
        return out
    sources = [w.get("report_fully_drawn") for w in windows if w.get("defined_by")]
    out["report_fully_drawn"] = min(sources, key=(None, SYSTEM_SERVER, APP).index)
    return out


def lines(window: dict[str, Any]) -> list[str]:
    """What bounds a window that is the launch or the whole trace, where an
    anchored one names its anchors."""
    by = window.get("defined_by")
    if not by:
        return []
    if by == WHOLE:
        return ["The window is the whole trace, from its first moment to its "
                "last: no scenario cut it, so every number below is about "
                "everything the app did while the recording ran."]
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
        line = head + ("`reportFullyDrawn` as system_server records it. The "
                       "app's own slice of that call is not in the trace.")
    elif by == FULLY_DRAWN:
        line = head + ("the end of the app's `reportFullyDrawn` call, which "
                       "came after the first frame.")
    elif source:
        line = head + ("the launch's end, its first frame. `reportFullyDrawn` "
                       "was called before that frame, so it closes nothing.")
    else:
        line = head + ("the launch's end, its first frame. The trace holds no "
                       "`reportFullyDrawn` call after the launch began.")
    # Where the launch ended, when the trace says (#909): a launch the app
    # took on to another screen by itself ran on through that screen.
    if window.get("activity") and not window.get("activities"):
        line += f" The launch ended on `{window['activity']}`."
    return [line]


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
    WHOLE: "as the whole trace (`--whole-trace` or `window: whole`)",
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

    The whole trace against any other window is the same answer for a
    larger reason: one side's rows are about the scenario, the other's about
    everything the app did while its recording ran, as long as somebody let
    it run (#519).

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
