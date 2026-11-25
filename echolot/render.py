"""The report written out: Markdown for a person, JSON for the agent.

`to_markdown` writes the whole report; `overview`, `detector_view`,
`markers_view` and `window_view` write the parts `echolot report` shows one
at a time; `to_json` writes the file itself. Nothing here changes a report:
it is built in report.py, and its rows are merged in merge.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import launch as launch_mod
from . import needs as needs_mod
from . import recording as recording_mod
from . import table
from . import when as when_mod
from .merge import THREADS, _FLOORED, _merge_debuggable, metric_of, short_runs

COLUMNS = ["location", "threads", "runs", "count", "self_ms", "total_ms", "max_ms",
           "covered_ms", "code", "detail"]
# Keys a row may carry that are not columns. `_table` renders anything it does
# not know as an extra column, which is right for a detector that invents one
# and wrong for bookkeeping the report writes itself. `places` is the json
# side of the `code` column — see place.py — and `stacks` the json side of
# what the evidence says the samples named — see stacks.py. `unfinished` is a
# marker that never closed, which its numbers say with a `≥`. A column a
# detector's predicate is held against, beside its numbers, is the threshold's
# business and no finding (`GATES`, #832).
GATES = {"verdict_frames", "stretch_ms"}
HIDDEN = {"spread", "places", "stacks", "unfinished", "parents", "variants", "fired",
          "floors", "unfinished_runs", "open_ms", *GATES}
HEADERS = {
    "location": "Where",
    "threads": "Threads",
    "runs": "Runs",
    "count": "N",
    "self_ms": "Self, ms",
    "total_ms": "Total, ms",
    "max_ms": "Max, ms",
    "covered_ms": "Instrumented, ms",
    "code": "In the code",
    "detail": "Evidence",
}



def _throttled_line(thermal: dict[str, Any], cooldown: dict[str, Any] | None) -> str:
    """The warning for a set the kernel throttled, with what to do about it.

    What to do depends on how the set was cooled, which `collect` keeps beside
    it (#522). Cooled before every repeat and throttled all the same, the
    scenario heats the device itself, and recording again after a longer
    wait throttles again. Recorded without cooling, the setting that does it
    is named.
    """
    where = thermal.get("throttle_device")
    runs = thermal.get("throttled_runs")
    head = ("> ⚠️ The kernel throttled the device during this window"
            + (f" (`{where}`)" if where else "")
            + (f", in {runs} repeats" if runs else ""))
    if cooldown and cooldown.get("on"):
        limit = cooldown.get("max_c")
        sensor = cooldown.get("sensor")
        # Merged over the set, the warmest start; one repeat's own otherwise.
        before = cooldown.get("before_c_max", cooldown.get("before_c"))
        cooled = ("`runner.cooldown` was on"
                  + (f": {sensor} at or below {limit:g} °C before each repeat"
                     if isinstance(limit, (int, float)) else "")
                  + (f", {before:.0f} °C at most" if isinstance(before, (int, float)) else ""))
        return (f"{head}, though {cooled}. The scenario itself heats this device, "
                f"and cooling longer before each repeat would not change it: the "
                f"numbers are the app on a slowed machine. Compare them only with "
                f"sets recorded the same way, on the same device.")
    tail = ". Capacity was taken away while these numbers were measured, so they are the app on a slowed machine. "
    if cooldown is not None:
        return (f"{head}{tail}The set was recorded without `runner.cooldown`: set it "
                f"(`max_c` under it, the temperature each repeat waits for) and record "
                f"again before comparing anything to them.")
    return (f"{head}{tail}Let it cool and record again before comparing anything to "
            f"them; `runner.cooldown` waits for that before each repeat.")



def elsewhere_said(left: list[dict[str, Any]], read: int) -> str:
    """The sentence for the launches `analyze` left out as having ended on
    another activity than the rest of the set (#909)."""
    k = len(left)
    names = [Path(e["trace"]).name for e in left]
    shown = [Path(e["trace"]).name if names.count(Path(e["trace"]).name) == 1
             else "/".join(Path(e["trace"]).parts[-2:]) for e in left]
    named = ", ".join(
        f"`{name}` ("
        + (f"repeat {e['repeat']}, " if e.get("repeat") else "")
        + f"ended on `{e['activity']}`)" for name, e in zip(shown, left, strict=True))
    others = left[0].get("others")
    where = f"; the other {read} ended on `{others}`" if others else ""
    return (f"Left out: {named}{where}. Those are two journeys: an app that opens "
            f"another screen by itself (a page read last, a login, an onboarding) "
            f"ends its launch there, and the window runs on through it. The numbers "
            f"are over the other {read}. To read {'it' if k == 1 else 'them'}, "
            f"analyze {'it' if k == 1 else 'them'} by name, alone.")


def _activities_lines(w: dict[str, Any]) -> list[str]:
    """Launches that ended on different activities, as many on each, so that
    none could be left out (#909)."""
    counts = w.get("activities")
    if not counts:
        return []
    total = sum(counts.values())
    each = ", ".join(f"`{a}` in {n}" for a, n in counts.items())
    return [f"> ⚠️ The launches ended on different activities: {each} of {total}. "
            f"The app went on to another screen by itself in some of them, so these "
            f"are two journeys, and the medians below mix them. Analyze each by name."]


def _boot_lines(w: dict[str, Any]) -> list[str]:
    """What the report says of the app's `BOOT_COMPLETED` receivers inside its
    window (#910): their time is the reset's, not the start's."""
    rows = w.get("boot_receivers")
    if not rows:
        return []
    said = []
    for row in rows:
        spread = (f" ({row['ms_min']}–{row['ms_max']})"
                  if row.get("ms_min") is not None and row["ms_min"] != row["ms_max"] else "")
        runs = row.get("runs")
        where = f", in {runs.split('/')[0]} of {runs.split('/')[1]} repeats" if runs else ""
        said.append(f"`{row['name']}` {row['ms']} ms{spread}{where}")
    return [f"> ⚠️ The main thread ran the app's `BOOT_COMPLETED` receiver inside the "
            f"window: {'; '.join(said)}. On Android 15 `am force-stop` leaves an app "
            f"stopped, and its next start is sent `BOOT_COMPLETED`; `collect`'s "
            f"`reset_policy: force-stop` and a startup macrobenchmark stop the app "
            f"before every repeat. A user's cold start, after the system reclaimed "
            f"the process, runs none of it: it is the reset's work, not the start's, "
            f"and nothing to look for in the start's code."]


def undrawn(report: dict[str, Any]) -> tuple[int, int] | None:
    """(repeats in which the app drew no frame, repeats that could tell), when any."""
    d = (report.get("window") or {}).get("drawn")
    if not isinstance(d, dict) or not isinstance(d.get("count"), int):
        return None
    none, runs = d.get("none", 1 if d["count"] == 0 else 0), d.get("runs", 1)
    return (none, runs) if none else None


def _undrawn_lines(report: dict[str, Any]) -> list[str]:
    """The first thing to say of a set whose scenario never reached its screen."""
    got = undrawn(report)
    if not got:
        return []
    none, runs = got
    where = "in its recording" if runs == 1 else (
        f"in any of the {runs} repeats" if none == runs else f"in {none} of {runs} repeats")
    them = "it" if none == 1 else "those repeats"
    return [f"> ⚠️ The app drew no frame {where}: the scenario did not reach its "
            f"screen there. Look at what the phone showed (an error screen, no "
            f"network, a dialog in the way) and record again; the numbers below "
            f"from {them} are not about the scenario."]


def _unjudged_lines(w: dict[str, Any]) -> list[str]:
    """What `frame_jank` did not judge: the frames the app presented on a
    surface the frame timeline does not hold, a `SurfaceView` a renderer
    draws into from a thread of its own (#1011). Silent or not, its rows
    are about the app's windows only."""
    frames = w.get("frames")
    surfaces = frames.get("unjudged") if isinstance(frames, dict) else None
    if not surfaces:
        return []
    each = []
    for s in surfaces:
        k, _, n = str(s.get("runs") or "").partition("/")
        where = f", in {k} of {n} repeats" if n and k != n else ""
        each.append(f"{s.get('presents')} frames on `{s.get('surface')}` with "
                    f"`{s.get('call')}` on `{s.get('thread')}`{where}")
    judged = sum(s.get("judged") or 0 for s in surfaces)
    held = "none" if not judged else f"only {judged:g}"
    them = "that surface" if len(surfaces) == 1 else "those surfaces"
    return [f"> ⚠️ `frame_jank` judged the {frames.get('count')} frames the frame "
            f"timeline holds of the app. The app also presented {'; '.join(each)}, and "
            f"the timeline holds {held} of those: whether {them} moved smoothly is not in "
            f"`frame_jank`'s rows, silent or not. The times of those presents are in the "
            f"trace; the app sets their pace itself, so a long gap between two may be a "
            f"pause rather than a late frame."]



def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def same_recording_said(groups: list[list[str]]) -> str:
    """The sentence for traces that were one recording given more than once."""
    named = "; ".join(" = ".join(f"`{p}`" for p in g) for g in groups)
    extra = sum(len(g) - 1 for g in groups)
    return (f"The same recording was given more than once: {named}. Each is "
            f"counted once, so the runs are the distinct recordings, "
            f"{extra} fewer than the paths given.")


def unfinished_said(left: list[dict[str, Any]], read: int) -> str:
    """The sentence for the traces `analyze` left out because their launch
    had not ended when the recording stopped (#921)."""
    k = len(left)
    named = ", ".join(f"`{Path(e['trace']).name}`" for e in left)
    return (f"Left out: {named}. {'Its' if k == 1 else 'Each one'}'s launch had not "
            f"drawn its first frame when the recording stopped, so it has no end to "
            f"close the window at. The numbers are over the other {read}. Record "
            f"again with a longer `runner.duration_ms`, so that the recording "
            f"outlasts the launch.")


def not_cold_said(left: list[dict[str, Any]], read: int) -> str:
    """The sentence for the traces `analyze` left out as not cold (#523)."""
    k = len(left)
    named = ", ".join(f"`{Path(e['trace']).name}`" + (f" ({e['state']})" if e.get("state") else "")
                      for e in left)
    return (f"Left out: {named}. `collect` stopped the app before "
            f"{'it' if k == 1 else 'each'} and found its process running again by "
            f"the start, so {'it holds' if k == 1 else 'they hold'} neither the "
            f"process start nor bindApplication. The numbers are over the other "
            f"{read}. To read {'it' if k == 1 else 'them'}, analyze "
            f"{'it' if k == 1 else 'them'} by name, alone.")


def _traces_line(traces: list[str], limit: int = 8) -> str:
    """The files behind `Runs: N`, so a stranger among them is visible.

    The count alone hid the failure this exists for. A probe capture left in
    `.echolot/traces/` was picked up by the documented
    `analyze .echolot/traces/*.perfetto-trace`, and `Runs: 4` after a
    `collect` that recorded three read as ordinary. The medians were taken
    across two sittings, one row came from the stray trace alone, and the
    header said nothing a reader could act on. Named, it is obvious at a
    glance which file does not belong.

    A pid would have been the precise signal and is not available: every cold
    start launches the process again, so the repeats of one healthy round
    already carry three different pids.
    """
    names = [Path(t).name.removesuffix(".perfetto-trace") for t in traces]
    shown = names if len(names) <= limit else names[:limit - 1]
    line = ", ".join(f"`{n}`" for n in shown)
    if len(shown) < len(names):
        line += f" and {len(names) - len(shown)} more"
    return "Traces: " + line


# What each bucket is called on the page. Plain words rather than the state
# letters: `D` means nothing to a reader who has not spent an afternoon in
# thread_state, and the whole point of this line is that it is read at a
# glance.
BUDGET_LABELS = (
    ("on_cpu", "on a CPU"),
    ("waiting_for_cpu", "waiting for a CPU"),
    ("in_kernel", "blocked in the kernel"),
    ("sleeping", "sleeping"),
    ("other", "other"),
)


def _budget_lines(budget: dict[str, Any] | None) -> list[str]:
    """One line saying where the window went, above the findings that explain part of it.

    "Detectors fired: 5 of 12" counts detectors. This counts the window, which
    is the number a reader actually needs to decide whether a quiet report
    means a clean run. It also makes a compound stall legible: 40% waiting for
    a CPU next to 35% blocked in the kernel is one sentence here and two
    unrelated table rows anywhere else.

    Buckets under a percent are dropped from the line and kept in the JSON.
    A tail of "0% this, 0% that" is how a summary stops being read.
    """
    if not budget:
        return []
    window = budget.get("window_ms") or 0
    if not window:
        return []

    parts = []
    for key, label in BUDGET_LABELS:
        ms = budget.get(key) or 0
        share = ms / window * 100
        if share >= 1:
            parts.append(f"{share:.0f}% {label}")
    # A window that is the launch opens at the intent, before the app's
    # process exists, and the main thread has no state until it does. That
    # stretch is the launch's own and is named as a share of its own; it is
    # no hole in the recording, which the warning below is about.
    late = budget.get("before_process_ms") or 0
    late_share = late / window * 100
    if late_share >= 1:
        parts.append(f"{late_share:.0f}% before the process started")
    if not parts:
        return []

    out = ["Main thread: " + " · ".join(parts)]
    out.extend(_in_rows_line(budget))
    accounted = budget.get("accounted_pct")
    # Anything much short of the whole window means the thread had no state
    # for part of it; a state still open when the recording stopped is counted
    # to the window's end. Silence would read as "the rest was nothing".
    if accounted is None or accounted + late_share >= 95:
        return out
    if late:
        out.append(
            f"> ⚠️ Only {accounted + late_share:.0f}% of the window is accounted "
            f"for on the main thread, the {late:.0f} ms before its process "
            f"started included. It had no state for the rest of it — the "
            f"recording has a hole there — so the shares above are of what "
            f"was seen, not of the scenario."
        )
    else:
        out.append(
            f"> ⚠️ Only {accounted:.0f}% of the window is accounted for on the "
            f"main thread. It did not exist for the rest of it — the process "
            f"started inside the window, or the recording has a hole there — "
            f"so the shares above are of what was seen, not of the scenario."
        )
    return out


# The share of the CPUs' time in the window other processes may take before
# the report says the device was busy (#596). 13–17% on quiet cold starts of
# an SM-A515F, and 10% on the workshop app's; 80% with eight busy loops of
# the shell user running. Those counted the processes that draw the app as
# well; without them (#914, `analyze.PLATFORM_PROCESSES`), 9–17% on quiet cold
# starts of Wikipedia on an SM-F711B, whose 287 ms window they had filled to
# 53–70%.
OUTSIDE_PCT = 40


def outside_load(c: dict[str, Any] | None) -> dict[str, Any] | None:
    """`crowding.outside` when other processes took enough of the CPUs to say so."""
    outside = (c or {}).get("outside") or {}
    pct = outside.get("pct")
    return outside if pct is not None and pct >= OUTSIDE_PCT else None


def outside_who(outside: dict[str, Any]) -> str:
    """The largest load by name: `` `sh` (8 processes, uid 2000, 67.9%)``."""
    top = (outside.get("top") or [None])[0]
    if not top:
        return ""
    bits = []
    if (top.get("processes") or 1) > 1:
        bits.append(f"{top['processes']} processes")
    if top.get("uid") is not None:
        bits.append(f"uid {top['uid']}")
    bits.append(f"{top.get('pct', 0):.0f}%")
    return f"`{top.get('name')}` ({', '.join(bits)})"


def _crowding_lines(c: dict[str, Any] | None) -> list[str]:
    """The app's threads against the cores, under the budget, when they outnumbered them.

    Nothing while there were never more threads ready than cores: then the
    crowd explains no wait, and a line saying so would be read past. With
    other processes taking a large part of the CPUs, the wait is behind them
    as well, and the line says so instead of putting it on the app (#596).

    Nothing under a millisecond either: rounded, it read "more than 8 for 0
    ms of the window", and so short a crowd explains no wait that
    `runnable_starvation` would report (#711).
    """
    if not c or (c.get("above_cores_ms") or 0) < 1:
        return []
    outside = outside_load(c)
    behind = (f"behind the app's own work and other processes', which took "
              f"{outside['pct']:.0f}% of the CPUs' time" if outside
              else "behind the app's own work")
    return [f"App threads: up to {c['peak']} running or ready at once on "
            f"{c['cores']} cores, more than {c['cores']} for "
            f"{c['above_cores_ms']:.0f} ms of the window. A thread of the app "
            f"waiting for a CPU then waits {behind}, and "
            f"`runnable_starvation` says who held the main thread's core."]


def _outside_lines(c: dict[str, Any] | None) -> list[str]:
    """A warning when other processes took a large part of the CPUs in the window.

    The share of the main thread's core others held is no measure of it: 35%
    at rest, 42% with eight busy loops taking 68% of all eight cores. This is
    the window's capacity, cores times its length, and the share of it that
    went to processes other than the app.
    """
    outside = outside_load(c)
    if not outside:
        return []
    who = outside_who(outside)
    runs = short_runs(outside.get("runs"))
    return [f"> ⚠️ Other processes took {outside['pct']:.0f}% of the CPUs' time "
            f"in this window" + (f", most of it {who}" if who else "")
            + (f", in {runs[0]} of {runs[1]} repeats" if runs else "")
            + ". The app ran on a busy device, and its waits for a CPU below "
              "are partly theirs. Record again with nothing else running."]


def _in_rows_line(budget: dict[str, Any]) -> list[str]:
    """How much of that window the findings below stand for, each moment once.

    Under the budget, because it is the other half of the same account: the
    line above says what the main thread was doing, this one how much of it
    the rows below describe. It is also the number a reader used to work out
    by adding up the milliseconds down the page, which counts a disk wait
    inside a slice twice and can pass 100% without a single row being wrong.
    The markers table is not in it: those are the project's own names,
    measured whether or not anything is wrong with them.
    """
    pct = budget.get("in_rows_pct")
    if pct is None:
        return []
    line = (f"Covered by the findings below: **{pct:.0f}%** of the main "
            f"thread's window, each moment counted once")
    uncounted = budget.get("in_rows_uncounted") or []
    if uncounted:
        # A detector without `@intervals`, or one whose second query failed
        # (stderr says so). Either way the number is short by an unknown
        # amount, and a reader has to know which rows it leaves out.
        line += (" — not counting " + ", ".join(f"`{d}`" for d in uncounted)
                 + ", whose rows could not be placed on the main thread's "
                   "timeline")
    return [line]



# The thread states among the startup's reasons, in the budget's words. The
# other reasons are the names the standard library gives them, kept as they
# are: a reader can look them up there.
STARTUP_STATES = {
    "Running": "on a CPU",
    "R": "waiting for a CPU",
    "R+": "waiting for a CPU",
    "D": "blocked in the kernel",
    "DK": "blocked in the kernel",
    "S": "sleeping",
}


def startup_by_label(reasons: dict[str, float] | None) -> list[tuple[str, float]]:
    """The startup's reasons in the budget's words, longest first, each label
    once: `R` and `R+` (runnable, and runnable after a preemption) are both
    "waiting for a CPU", and were two items of that name in `report --window`
    (#822). Ties by label."""
    out: dict[str, float] = {}
    for reason, ms in (reasons or {}).items():
        label = STARTUP_STATES.get(reason, f"`{reason}`")
        out[label] = out.get(label, 0.0) + (ms or 0.0)
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


def _startup_lines(startup: dict[str, Any] | None, built: bool = False) -> list[str]:
    """The app's startup by Perfetto's own account, under the window's.

    The budget says where the window went by thread state. This says where
    the startup went by reason — `bind_application`, `binder`, `io`, the
    thread states — as the standard library divides it. It is the platform's
    measure, from the launch to the first frame, and a second line says how it
    sits against the window whenever the two do not coincide: a window from
    `bindApplication` misses the process start, and one that stops before the
    first frame misses the end of the startup.

    Reasons under a percent are dropped from the line and kept in the JSON,
    as in the budget. `built` is for a window that is the launch: the
    startup described is then the one the window was built from.
    """
    if not startup or not startup.get("dur_ms"):
        return []
    dur = startup["dur_ms"]
    parts = [f"{ms / dur * 100:.0f}% {label}"
             for label, ms in startup_by_label(startup.get("reasons")) if ms / dur * 100 >= 1]
    kind = startup.get("type") or "of a kind Perfetto could not tell"
    line = f"Startup: {kind}, **{dur:.0f} ms** from the launch to the first frame"
    if startup.get("startups") and built:
        line += (f" (one of {startup['startups']} in the trace: the launch the "
                 f"window is built from)")
    elif startup.get("startups"):
        line += (f" (the one of {startup['startups']} in the trace that shares "
                 f"the most with the window)")
    if parts:
        line += ": " + " · ".join(parts)
    return [line, *_startup_against_window(startup)]


def _startup_against_window(startup: dict[str, Any]) -> list[str]:
    """Where the startup began and ended against the scenario's window, when not with it."""
    began = startup.get("from_window_start_ms") or 0.0
    ended = startup.get("from_window_end_ms") or 0.0
    if not startup.get("in_window_ms"):
        return [
            "> ⚠️ The startup does not overlap the scenario's window: its "
            "shares are of the launch, and the findings below are of the "
            "window."
        ]
    if abs(began) < 1 and abs(ended) < 1:
        return []
    start = ("began with the window" if abs(began) < 1 else
             f"began {abs(began):.0f} ms {'before' if began < 0 else 'after'} the window opened")
    end = ("ended with it" if abs(ended) < 1 else
           f"ended {abs(ended):.0f} ms {'before' if ended < 0 else 'after'} it closed")
    return [f"That is the platform's measure rather than the window: the startup "
            f"{start}, and {end}."]

def _environment_lines(env: dict[str, Any], unbuilt: bool = False) -> list[str]:
    """One line for the machine the numbers below were measured on.

    Placed above the findings rather than under them: it is the condition
    every duration in the report is stated under, and a reader who learns
    after the table that the device was throttled has already drawn the
    conclusion.

    Kept to one line while there is nothing wrong, because on a settled device
    there is nothing to act on and a paragraph of context would push the
    findings down the page for no reason. Throttling gets its own warning,
    since it means the numbers below understate the app.
    """
    if not env:
        return []
    out: list[str] = []
    cpu, thermal = env.get("cpu"), env.get("thermal")

    bits: list[str] = []
    if cpu and cpu.get("mean_mhz"):
        bit = f"clock **{cpu['mean_mhz']:.0f} MHz**"
        low, high = cpu.get("mean_mhz_min"), cpu.get("mean_mhz_max")
        if low is not None and high is not None and high > low:
            bit += f" (from {low:.0f} to {high:.0f} across repeats)"
        # Below the whole, the mean is an average over the part we could see,
        # and saying so is cheaper than having it believed of all of it.
        covered, total = cpu.get("measured_ms"), cpu.get("on_cpu_ms")
        if covered and total and covered < total * 0.99:
            bit += f", measured over {covered / total * 100:.0f}% of on-CPU time"
        # The same for repeats: a clock read in one of five is that one's.
        short = short_runs(cpu.get("runs"))
        if short:
            bit += f", read in {short[0]} of {short[1]} repeats"
        bits.append(bit)
    if thermal and thermal.get("max_celsius") is not None:
        bit = f"peak {thermal['max_celsius']:.0f} °C"
        short = short_runs(thermal.get("runs"))
        if short:
            bit += f" in {short[0]} of {short[1]} repeats"
        bits.append(bit)
    memory = env.get("memory")
    if memory and memory.get("available_mb_min") is not None:
        bits.append(f"{memory['available_mb_min']:.0f} MB free at the low point")
    if bits:
        out.append("Device: " + ", ".join(bits))
    out.extend(_compile_lines(env.get("compile"), env.get("debuggable")))

    if thermal and thermal.get("throttled"):
        out.append(_throttled_line(thermal, env.get("cooldown")))

    # Absence is a third answer and has to look like one. Without this the
    # header simply says nothing about the device, which reads as a device
    # with nothing to say. Worded by what is absent: a trace that has its
    # clock and lacks only a temperature still lets `compare` judge the
    # machine, and saying otherwise sent a reader to re-record for nothing.
    missing = env.get("missing") or []
    if set(missing) >= {"cpu", "thermal", "memory"}:
        out.append(
            f"Device state not recorded: {', '.join(missing)} — this trace "
            f"carries no platform-state sources, so `compare` cannot tell a "
            f"slower machine from a slower app."
        )
    elif missing:
        line = f"Device state not recorded: {', '.join(missing)}"
        if "cpu" in missing:
            line += (" — without the clock, `compare` cannot tell a slower "
                     "machine from a slower app")
        elif "thermal" in missing:
            line += " — whether the kernel throttled the device is not known"
        out.append(line + ".")
    short = short_runs((cpu or {}).get("runs")) or short_runs((thermal or {}).get("runs"))
    if short:
        out.append(
            f"> ⚠️ The device state comes from {short[0]} of {short[1]} "
            f"repeats; the others carry none. What it says about the machine "
            f"is about those repeats alone, and `compare` says so too."
        )
    out.extend(_sampling_lines(env.get("sampling")))
    out.extend(_names_lines((env.get("sampling") or {}).get("names"), unbuilt))
    out.extend(_compose_names_lines(env.get("compose_names")))
    return out


# The filters under which ART compiled none of the app's code ahead of time:
# the interpreter and the JIT carry the launch. `run-from-apk` is a build with
# no compiled code to open at all, as a debuggable build nobody compiled is.
UNCOMPILED_FILTERS = frozenset({
    "assume-verified", "extract", "verify", "quicken",
    "run-from-apk", "run-from-apk-fallback", "run-from-vdex-fallback",
})


def _compile_lines(c: dict[str, Any] | None, debuggable: Any = None) -> list[str]:
    """How the app was compiled, under the clock: the other half of the machine it ran on.

    One line while the app ran compiled. A warning when it ran compiled from
    nothing, since that is not how a user who installed it gets it, and one
    when the repeats disagree, since the set is then two measurements.

    `debuggable` is the merged `environment.debuggable`: a debuggable build
    is warned of first, and its compile filter is not blamed, since nothing
    compiles it ahead of time whatever its profile (#925).
    """
    # One recording's is its own `true`/`false`; a set's is merged.
    debug = (debuggable if isinstance(debuggable, dict)
             else _merge_debuggable([debuggable]) or {})
    out: list[str] = []
    if debug.get("repeats"):
        k, n = debug["repeats"], debug.get("of") or debug["repeats"]
        out.append(
            "> ⚠️ This is a debuggable build"
            + (f" in {k} of {n} repeats" if k < n else "")
            + ": ART runs it interpreted and with the JIT alone, whatever its "
              "profile, so these numbers are not what users get, and a baseline "
              "profile cannot change them. Measure a release-like build: one that "
              "is not debuggable, and is profileable, as a `benchmark` build type is.")
    if not c:
        return out
    filters = (c.get("filter") or "").split(" and ") if c.get("filter") else []
    if filters:
        bit = f"Compiled: **{c['filter']}**"
        if c.get("reason"):
            bit += f" ({c['reason']})"
        short = short_runs(c.get("runs"))
        if short:
            bit += f", read in {short[0]} of {short[1]} repeats"
    else:
        bit = "Compiled: not in this trace, the process was already running when it began"
    bits = [bit]
    jit = c.get("jit") or {}
    if jit.get("methods"):
        bits.append(f"JIT {jit['methods']} methods, {jit.get('ms') or 0:.0f} ms")
    verified = c.get("verified") or {}
    if verified.get("classes"):
        part = (f"{verified['classes']} classes verified at run time, "
                f"{verified.get('ms') or 0:.0f} ms")
        if verified.get("main_ms"):
            part += f", {verified['main_ms']:.0f} ms of it on the main thread"
        bits.append(part)
    out.append(" · ".join(bits))
    if len(filters) > 1 or " and " in (c.get("reason") or ""):
        out.append(
            f"> ⚠️ The repeats ran on different compile states ({c['filter']}"
            + (f"; {c['reason']}" if c.get("reason") else "")
            + "). Each is a different speed for the same code, so the medians "
              "below mix two measurements. Record the set again on one build, "
              "installed one way.")
    bare = [f for f in filters if f in UNCOMPILED_FILTERS]
    # A debuggable build in every repeat is not compiled whatever its profile:
    # the advice to record one compiled from it cannot be followed (#925).
    if bare and not (debug.get("repeats") and debug["repeats"] == debug.get("of")):
        some = len(bare) < len(filters)
        out.append(
            f"> ⚠️ The app ran with `filter={'`, `filter='.join(bare)}`"
            + (" in some repeats" if some else "")
            + ": none of its code was compiled ahead of time"
            + (" in those" if some else "")
            + ", so the interpreter and the JIT carried it. An install from "
              "Google Play usually comes compiled from a profile, so these "
              "numbers are slower than what most users see. Record again on a "
              "build compiled from its baseline profile; `adb shell dumpsys "
              "package dexopt` says how the installed one is compiled.")
    return out


def _compose_names_lines(c: dict[str, Any] | None) -> list[str]:
    """Whether composables were named, and what that costs a comparison.

    Nothing for a trace that neither asked for names nor had any: that is
    every trace of an app without `runtime-tracing`.
    """
    if not c:
        return []
    if not c.get("calls"):
        return [
            "> ⚠️ The recording asked for composable names and none arrived: the "
            "app does not carry `androidx.compose.runtime:runtime-tracing`, or its "
            "Perfetto SDK was not switched on, which `runner.compose_names` does for "
            "a cold start."
        ]
    ran, _, total = (c.get("runs") or "").partition("/")
    part = f", in {ran} of {total} repeats" if total and ran != total else ""
    return [
        f"Composable names: {c['calls']} calls{part}. They come with the app's "
        f"Perfetto SDK loaded at its start, which costs the start time, so compare "
        f"these numbers only with another round recorded with them."
    ]


# The share of the app's sampled methods that has to read as minified before
# the header warns. Some code arrives minified from elsewhere — SDKs ship that
# way — and no mapping of this build names it: on a phone, a build that kept
# its names had 38 of its 3,309 sampled methods so, a little over 1%. A build
# R8 minified reads that way in most of its own code and of the libraries it
# ships, and so does one given another build's mapping.
MINIFIED_SHARE = 0.10


def _names_lines(names: dict[str, Any] | None, unbuilt: bool = False) -> list[str]:
    """Whether the app's sampled methods came back by name.

    Nothing for a build that kept its names and was given no mapping. A
    warning for a minified build without one: the frames of the app read as
    `a.b.c`, and `project.mapping` is the fix — unless the config names one
    the build has not written yet (`unbuilt`), which its own line says (see
    `_mapping_lines`). With a mapping, one line saying
    how many methods it named back — or a warning when many still read as
    minified, which is what a mapping from another build leaves: it renames
    the frames it happens to match, wrongly, and misses the rest, and the rows
    then mix real names with minified ones as if they were one list.
    """
    if not names:
        return []
    total, left = names.get("methods") or 0, names.get("minified") or 0
    many = bool(total) and left / total >= MINIFIED_SHARE
    if "renamed" not in names:
        if not many:
            return []
        fix = ("" if unbuilt else
               " Set `project.mapping` to the build's `mapping.txt` and the "
               "frames come back by name.")
        return [
            f"> ⚠️ {left} of the app's {total} sampled methods read as minified, "
            f"`a.b.c`: a reader cannot find them, and neither can `code`.{fix}"
        ]
    if many:
        return [
            f"> ⚠️ {left} of the app's {total} sampled methods still read as "
            f"minified after `project.mapping` renamed {names['renamed']}. A "
            f"mapping from another build renames the frames it happens to "
            f"match, wrongly, and misses the rest: check that it is this "
            f"build's."
        ]
    if total and names["renamed"] / total < MINIFIED_SHARE:
        return [
            f"> ⚠️ `project.mapping` named back {names['renamed']} of the app's "
            f"{total} sampled methods, and they did not read as minified: the "
            f"build recorded keeps its names, or the mapping is another "
            f"build's. Check that its variant is the one that ran; a build "
            f"that is not minified needs no mapping."
        ]
    line = (f"`project.mapping` named {names['renamed']} of the app's {total} "
            f"sampled methods back")
    if not left:
        return [f"{line}, and none reads as minified."]
    return [f"{line}. {left} still read as minified, too few to be this build's "
            f"own: code that arrived minified, as some SDKs ship, which no "
            f"mapping of this build names."]


def _unbuilt(report: dict[str, Any]) -> bool:
    """Whether the config named a mapping that was not there for this report."""
    named = (report.get("config") or {}).get("mapping")
    return bool(named) and not named.get("found")


def _mapping_lines(report: dict[str, Any]) -> list[str]:
    """A mapping the config names and the build had not written yet.

    `scan` names a minified build's mapping before anything is built, and
    AGP writes the file with the variant. Until it is there the report is
    made without it, and says so here rather than stopping.
    """
    if not _unbuilt(report):
        return []
    path = report["config"]["mapping"]["path"]
    return [
        f"> ⚠️ `project.mapping` names `{path}`, and no file was there: AGP "
        f"writes it when the variant is built. Until it is, the frames R8 "
        f"renamed keep its names, in the samples and in the lock slices; build "
        f"the variant and analyze again."
    ]


def _unbuilt_markers_lines(report: dict[str, Any]) -> list[str]:
    """Temporary markers in the code that no trace holds (#390).

    In launch and command modes the app on the phone was built before them,
    and only a build and an install put them in the next round; the line
    names the gradle task where `analyze` could read it. In gradle mode
    `collect` builds the app, so the traces are older than the markers.
    Either way, markers that a fresh round still lacks wrap code the
    scenario does not run.
    """
    u = (report.get("markers") or {}).get("unbuilt") or {}
    names = u.get("names") or []
    if not names:
        return []
    if len(names) == 1:
        head = f"The temporary marker `{names[0]}` is in the code and not in these traces"
        it, they, are, wrap = "it", "it", "is", "wraps"
    else:
        head = f"{len(names)} temporary markers are in the code and none is in these traces"
        it, they, are, wrap = "them", "they", "are", "wrap"
    if u.get("in_build"):
        # `collect` read the build before it recorded and found them there.
        return [f"> ⚠️ {head}, though `collect` found {it} in the build on the phone "
                f"before it recorded: the scenario did not run the code {they} {wrap}. "
                f"Look at what the phone showed during the recording; there is nothing "
                f"to rebuild."]
    got = undrawn(report)
    if got and got[0] == got[1]:
        return [f"> ⚠️ {head}. The app drew no frame in these recordings, so the "
                f"scenario most likely did not run the code {they} {wrap}: see the "
                f"warning above before rebuilding anything."]
    still = (f"If {they} {are} still missing after that, the scenario does not run "
             f"the code {they} {wrap}.")
    if u.get("mode") == "gradle":
        return [f"> ⚠️ {head}: the traces were recorded before {they} went in. In "
                f"gradle mode `echolot collect` builds the app with {it}, so record "
                f"again. {still}"]
    tasks = u.get("install") or []
    how = f" with {install_command(tasks)}" if tasks else ""
    return [f"> ⚠️ {head}: the app on the phone was built before {it}. Build and "
            f"install the app{how}, then record again. {still}"]


def _removed_markers_lines(report: dict[str, Any], shown: int = 5) -> list[str]:
    """Prefixed names the traces hold and no string in the code makes any more (#393).

    `mark --remove` took them out, or a hand did, after the app that
    recorded these traces was built: it has none of the changes made to the
    code since, a fix among them, and a round compared as if it had one
    says the fix did nothing. In gradle mode `collect` builds the app from
    the code, so it is the traces that are older.
    """
    r = (report.get("markers") or {}).get("removed") or {}
    names = r.get("names") or []
    if not names:
        return []
    listed = ", ".join(f"`{n}`" for n in names[:shown])
    if len(names) > shown:
        listed += f" and {len(names) - shown} more"
    if len(names) == 1:
        head = f"The temporary marker {listed} is in these traces and nowhere in the code"
        they = "it was"
    else:
        head = (f"{len(names)} temporary markers are in these traces and nowhere in the "
                f"code ({listed})")
        they = "they were"
    if r.get("mode") == "gradle":
        return [f"> ⚠️ {head}: the traces were recorded before {they} taken out. In "
                f"gradle mode `echolot collect` builds the app from the code as it is "
                f"now, so record again."]
    tasks = r.get("install") or []
    how = f" with {install_command(tasks)}" if tasks else ""
    return [f"> ⚠️ {head}: the app on the phone was built before {they} taken out, "
            f"and has none of the changes made to the code since. Build and install "
            f"the app{how}, then record again."]


def _code_markers_lines(report: dict[str, Any]) -> list[str]:
    """Temporary markers the traces and the code disagree on: a build older than the code."""
    return [*_unbuilt_markers_lines(report), *_removed_markers_lines(report)]


def install_command(tasks: list[str]) -> str:
    """`./gradlew :app:installBenchmark`, and every other task installing as the same package.

    `mark --apply` and the report say it the same way.
    """
    line = f"`./gradlew {tasks[0]}`"
    if len(tasks) > 1:
        line += f" (or {', '.join(f'`{t}`' for t in tasks[1:])}, whichever is on the phone)"
    return line


def _retrace_lines(retrace: dict[str, Any] | None) -> list[str]:
    """What `project.mapping` made of the frames R8 wrote into lock slices.

    The rows' `code` names the real methods, and their evidence still says
    `a.a.run()(SourceFile:6)`; one line says why the two differ. A frame
    the mapping has no place for — its line in no range of its method, or a
    method its class lacks — is what a mapping from another build leaves
    many of, and the right one a few. From two such frames and a tenth of
    them up, the line is a warning, unless the frames carry the mapping's own
    id (`mapping.another_build`).
    """
    from .mapping import another_build, no_place, short_id
    if retrace and retrace.get("map_id"):
        ids = retrace["map_id"]
        return [
            f"> ⚠️ The lock slices name their files after mapping "
            f"`{short_id(ids['frames'])}`, and `project.mapping` is "
            f"`{short_id(ids['mapping'])}`: another build's. Its names would be "
            f"wrong, so the frames stay as recorded; point `project.mapping` at "
            f"the mapping of the build that ran."
        ]
    if not retrace or not retrace.get("frames"):
        return []
    total, outside = retrace["frames"], retrace.get("outside") or 0
    matched = retrace.get("matched_map_id")
    if another_build(total, outside, matched):
        return [
            f"> ⚠️ {outside} of the {total} frames R8 wrote into the lock slices "
            f"have no place in `project.mapping`: no range of their method holds "
            f"their line. A mapping from another build names the frames it "
            f"happens to match, wrongly, and has no place for the rest: check "
            f"that it is this build's."
        ]
    line = (f"`project.mapping` named back the {total} frames R8 wrote into the "
            f"lock slices; the evidence keeps them as recorded.")
    if outside:
        line += " " + no_place(outside, matched)
    return [line]


def _sampling_lines(s: dict[str, Any] | None) -> list[str]:
    """What sampled the app while it was measured, and what came of it.

    Nothing when nothing did: that is how every trace was recorded before
    `runner.sampling`, and the report said nothing about it then either.

    Otherwise one line, and a warning in place of it for the three ways it
    comes back empty: no stacks, no samples of this process, no sampler. Each
    points somewhere else — the app's manifest, how the recording was
    filtered, the device. No stacks has a second cause that looks the same
    from here: a profileable app whose start the sampler lost, which on a
    device happened in one cold start of six. Only another round tells them
    apart, so the warning names both.
    """
    if not s:
        return []
    rate = f" at {s['hz']} Hz" if s.get("hz") else ""
    if not s.get("started"):
        return [
            f"> ⚠️ The recording asked for callstack samples{rate} and none "
            f"arrived: the device's sampler, `traced_perf`, did not run. The "
            f"app was not slowed by it, and there are no stacks to read."
        ]
    runs = s.get("runs") or ""
    ran, _, total = runs.partition("/")
    part = f" in {ran} of {total} repeats" if total and ran != total else ""
    cost = ("The sampler takes time from the app, so compare these numbers "
            "only with another round sampled at the same rate.")
    if part:
        cost = ("The repeats were not recorded alike, and the sampler takes "
                "time from the app, so its cost is part of the spread below.")
    samples, stacked = s.get("samples") or 0, s.get("with_stack") or 0
    head = f"Sampled callstacks{rate}{part}"
    if not samples:
        return [
            f"> ⚠️ {head}, and none of the samples in the window is this "
            f"process's. A sampler filtered by process name misses a process "
            f"it first meets under zygote's name, which is how a cold start "
            f"begins; `runner.sampling` records without that filter. {cost}"
        ]
    # A single trace has no `runs_with_stack`: it is one repeat, with a stack
    # or without.
    got, _, tried = (s.get("runs_with_stack") or f"{int(bool(stacked))}/1").partition("/")
    if got == "0":
        return [
            f"> ⚠️ {head}: {samples} samples of this process in the window, "
            f"none with a stack. Either the app is not profileable or "
            f"debuggable, which a `user` build of Android needs before the "
            f"sampler unwinds it — `<profileable android:shell=\"true\" />` "
            f"in the manifest — or the sampler lost the process as it "
            f"started, which a cold start does now and then. If another round "
            f"comes back with stacks, it was the second. {cost}"
        ]
    if got != tried:
        return [
            f"{head}: {samples} samples of this process in the window. They "
            f"came with a stack in {got} of {tried} repeats and with none in "
            f"the rest: the sampler lost the process as it started, which a "
            f"cold start does now and then, and those repeats have no stacks "
            f"to read. {cost}"
        ]
    # Rounded, a share can read 100% with samples missing their stack, or 0%
    # with some carrying one. Neither end is said unless it is exact.
    share = ("all" if stacked == samples
             else f"{min(max(round(stacked / samples * 100), 1), 99)}%")
    return [
        f"{head}: {samples} samples of this process in the window, {share} "
        f"with a stack. {cost}"
    ]


def to_markdown(report: dict[str, Any]) -> str:
    w = report["window"]
    out: list[str] = []
    out.append("# Marker Report")
    out.append("")
    traces = report.get("traces")
    if traces:
        out.append(f"Runs: **{len(traces)}**, numbers are medians across them")
        out.append(_traces_line(traces))
    else:
        out.append(f"Trace: `{report['trace']}`")
    if report.get("same_recording"):
        out.append(f"> ⚠️ {same_recording_said(report['same_recording'])}")
    if report.get("not_cold"):
        out.append(f"> ⚠️ {not_cold_said(report['not_cold'], len(traces or [None]))}")
    if report.get("ended_elsewhere"):
        out.append(f"> ⚠️ {elsewhere_said(report['ended_elsewhere'], len(traces or [None]))}")
    if report.get("unfinished"):
        out.append(f"> ⚠️ {unfinished_said(report['unfinished'], len(traces or [None]))}")
    if report.get("scenario"):
        out.append(f"Scenario: `{report['scenario']}`")
    if w.get("process"):
        # Every cold start is a new process, so a set has as many pids as
        # recordings: the one shown is the first's, and says so (#581).
        first = (f" in `{Path(traces[0]).name.removesuffix('.perfetto-trace')}`, "
                 f"the first of {len(traces)}") if traces and len(traces) > 1 else ""
        out.append(f"Process: `{w['process']}` (pid {w.get('pid')}{first})")
    out.extend(_undrawn_lines(report))
    alts = w.get("process_alternatives")
    if alts:
        names = ", ".join(f"`{a['name']}`" for a in alts)
        total = w.get("process_alternatives_total") or len(alts)
        if total > len(alts):
            names += f" … and {total - len(alts)} more"
        out.append(
            f"> ⚠️ The mask matched more than one process. The largest by slice "
            f"count was taken; skipped: {names}. If this is the wrong one, "
            f"narrow `project.process`."
        )
    if w.get("duration_ms") is not None:
        # The whole trace is no scenario's window, and is not called one.
        line = (f"{'Window' if launch_mod.is_whole(w) else 'Scenario window'}: "
                f"**{w['duration_ms']} ms**")
        low, high = w.get("duration_ms_min"), w.get("duration_ms_max")
        if low is not None:
            # The shortest and the longest repeat: right after a duration,
            # a bare "from … to …" read as the window's start and end (#585).
            line += f" (from {low} to {high} across repeats)"
        out.append(line)
        out.extend(_diverged_lines(w))
    out.extend(launch_mod.lines(w))

    out.extend(_budget_lines(w.get("main_thread")))
    out.extend(_crowding_lines(w.get("crowding")))
    out.extend(_outside_lines(w.get("crowding")))
    out.extend(_startup_lines(w.get("startup"), launch_mod.is_launch(w)))
    out.extend(_window_warning_lines(w))
    out.extend(_drift_lines(report))
    out.extend(_unjudged_lines(w))

    out.extend(_environment_lines(report.get("environment") or {}, _unbuilt(report)))
    out.extend(recording_mod.header_lines(report.get("recording")))
    out.extend(_no_sources_lines(report.get("no_sources")))
    out.extend(_mapping_lines(report))
    out.extend(_retrace_lines(report.get("retrace")))
    out.extend(_code_markers_lines(report))

    s = report["summary"]
    out.append(
        f"Detectors fired: **{s['detectors_fired']} of {s['detectors_run']}**"
    )
    # "3 of 6" reads as though six were all there is, so a detector that did
    # not run is named. It used to be named as an oversight — the config's
    # `detectors:` section was an allowlist, and every detector it happened
    # not to mention was off. This warning was written for that, and on a real
    # project it printed at the top of three reports in a row while four of ten
    # detectors sat out; being told is not the same as having chosen. Now the
    # only way to be here is to have written `false`, so it says so.
    absent = s.get("absent_ids") or []
    if absent:
        out.append(
            f"> ⚠️ {len(absent)} detector(s) are turned off in this config and "
            f"did not run: {', '.join(f'`{a}`' for a in absent)}. Remove the "
            f"`false` to bring one back, or run `--defaults` to ignore the "
            f"config's thresholds entirely."
        )
    # Ran, and could not see: not silent either (#441).
    unseen = needs_mod.line(report["detectors"])
    if unseen:
        out.append(unseen)
    line = _config_line(report)
    if line:
        out.append(line)
    out.append("")

    out.extend(_markers_lines(report.get("markers") or {}))

    # A detector that failed did not look, and that is no answer: it is not
    # silence, and the ground it covers was not checked. Listed apart, above
    # the findings, with its error. It used to land under Silent beside the
    # detectors that looked and found nothing, and a report where every one
    # had failed called the run clean.
    failed = [d for d in report["detectors"] if d.get("error") and not d["rows"]]
    for d in failed:
        out.append(f"> ⚠️ Failed: `{d['id']}` — {d['error']}")
    if failed:
        out.append("")

    # Nothing fired still ends the way every report does, with the Silent
    # line and the toolchain footer. It used to return here, before both —
    # and the footer is where a trace_processor other than the pinned one is
    # named, so a clean report from a binary that bypassed the pin did not
    # say so.
    if s["detectors_fired"] == 0 and not failed:
        out.append("_No detector fired._")
        out.append("")
        out.append(
            "Either the run is clean or the config is wrong — check that the "
            + ("process name matches" if w.get("defined_by") else
               "process name and the scenario anchors match")
            + " the trace (`echolot probe`)."
        )
        out.append("")

    for d in report["detectors"]:
        if not d["rows"]:
            continue
        out.append(f"## {d['title']}")
        if d.get("why"):
            out.append(f"_{d['why']}_")
        out.append("")
        out.append(_table(d["rows"]))
        out.append("")
        pooled = _threads_note(d["rows"])
        if pooled:
            out.append(pooled)
            out.append("")
        # Failed in some repeats and ran in the rest: the rows are from the
        # rest, and `runs` counts those.
        part = short_runs(d.get("failed_runs"))
        if part:
            out.append(f"> ⚠️ Failed in {part[0]} of {part[1]} repeats: "
                       f"{d.get('error')}. The rows are from the others.")
            out.append("")
        out.append(f"<sub>detector `{d['id']}`, params: {d['params']}"
                   f"{_source_note(d)}</sub>")
        out.append("")

    quiet = [d["id"] for d in report["detectors"]
             if not d["rows"] and not d.get("error")
             and d.get("checked") is not False]
    if quiet:
        out.append(f"**Silent:** {', '.join(quiet)}")

    tc = report.get("toolchain") or {}
    # Any source but the pin is somebody's own binary, and the footer says
    # whose. Only `--tp-binary` used to be marked, so a binary named by
    # `toolchain.tp_binary` in a gitignored local.yml left the report looking
    # pinned. Said even when its version could not be read: which binary ran
    # matters more than what it calls itself.
    custom = tc.get("source") not in (None, "pinned")
    if tc.get("trace_processor") or custom:
        note = f"trace_processor {tc.get('trace_processor') or 'unknown'}"
        if custom:
            note += f" (custom binary from {tc['source']}, pin bypassed)"
        out.append("")
        out.append(f"<sub>{note}</sub>")
    return "\n".join(out)


def _diverged_lines(w: dict[str, Any]) -> list[str]:
    """Repeats of one scenario cannot differ several-fold. If they do, they
    are not repeats, and a median across them means nothing."""
    low, high = w.get("duration_ms_min"), w.get("duration_ms_max")
    if not (low and high and high > 2 * low):
        return []
    return [f"> ⚠️ Repeat windows diverged {high / low:.1f}x. These are not "
            f"repeats of one scenario: "
            + ("the launches did different things"
               if launch_mod.is_launch(w) else
               "the recordings ran for different lengths"
               if launch_mod.is_whole(w) else
               "either the anchors did not match or the runs did different things")
            + ". A median over such numbers is meaningless."]


def _drift_lines(report: dict[str, Any]) -> list[str]:
    """The set's runs drift from the first to the last (#508). Said where the
    window's warnings are, in the whole report and the overview alike: it
    decides how far the medians below describe one state of the device."""
    from . import drift
    return drift.lines(report.get("drift"))


def _near_said(anchor: dict[str, Any]) -> str:
    """The names the trace holds that read like an anchor it does not (#500)."""
    near = [n for n in anchor.get("near") or [] if isinstance(n, str)]
    if not near:
        return ""
    return ("The trace holds " + ", ".join(f"`{n}`" for n in near)
            + ", which " + ("reads" if len(near) == 1 else "read") + " like it. ")


def _window_warning_lines(w: dict[str, Any]) -> list[str]:
    """What makes the numbers below not about the scenario: an anchor that
    missed or never closed, a scenario with none, repeats of different
    processes, a window that opened inside a wait.

    The whole report says them above its findings, and so does the overview:
    outside a terminal `analyze` prints the overview alone (#492), and these
    are the lines that decide whether its table is worth reading at all.
    """
    out: list[str] = []
    # An anchor that never matched silently collapses the window onto the whole
    # trace. That has to be shouted, not hidden: otherwise the report looks
    # plausible and leads somewhere else entirely.
    for key, label in (("start_anchor", "Start"), ("end_anchor", "End")):
        anchor = w.get(key)
        missed = short_runs(anchor.get("missed")) if anchor else None
        if anchor and anchor.get("matches") == 0 and anchor.get("before_start"):
            out.append(
                f"> ⚠️ {label} anchor `{anchor['glob']}` occurs only before the "
                f"start anchor ({anchor['before_start']} time(s)), so nothing "
                f"closed the window and it ran to where the process's last "
                f"slice ends. Are the "
                f"two anchors swapped? The numbers below are not about your "
                f"scenario."
            )
        elif missed:
            out.append(
                f"> ⚠️ {label} anchor `{anchor['glob']}` was not found in "
                f"{missed[0]} of {missed[1]} repeats — in those "
                f"{FALLBACK[key]}, and their rows are in the "
                f"medians below. Check against `probe`."
            )
        elif anchor and anchor.get("matches") == 0:
            out.append(
                f"> ⚠️ {label} anchor `{anchor['glob']}` was not found in the "
                f"trace — {FALLBACK[key]}. {_near_said(anchor)}Check against "
                f"`probe`; the numbers below are not about your scenario."
            )
        elif anchor and anchor.get("unfinished"):
            out.append(
                f"> ⚠️ {label} anchor `{anchor['glob']}` never closed: the "
                f"scenario had not reached its end when the recording stopped, "
                f"so the window runs to the end of the trace."
            )
    out.extend(_no_anchor_lines(w))
    processes = w.get("processes")
    if processes:
        out.append(
            f"> ⚠️ The repeats measured different processes: "
            f"{', '.join(f'`{p}`' for p in processes)}. The process mask "
            f"matched more than one, and the medians below mix them. Narrow "
            f"`project.process` to the one you mean."
        )

    # A partial account looks exactly like a complete one, which is the only
    # reason this is worth a line. Slices keep their real length across the
    # boundary; thread states are clipped, so the beginning of a stall that
    # started before the anchor is simply not in the report.
    inside = w.get("opened_inside")
    if inside and inside.get("material") and launch_mod.is_launch(w):
        # A launch window has no anchor to move: the app was already running
        # when it was brought to the front.
        out.append(
            f"> ⚠️ The window opened with the main thread already blocked: "
            f"state `{inside['state']}` for {inside['total_ms']} ms, of which "
            f"{inside['before_ms']} ms happened before the launch began. "
            f"Whatever put it there is outside this window, so the waiting "
            f"below is the tail of it rather than the whole."
        )
    elif inside and inside.get("material"):
        out.append(
            f"> ⚠️ The window opened with the main thread already blocked: "
            f"state `{inside['state']}` for {inside['total_ms']} ms, of which "
            f"{inside['before_ms']} ms happened before the anchor matched. "
            f"Whatever put it there is outside this window, so the waiting "
            f"below is the tail of it rather than the whole. Move "
            f"`scenario.start` earlier to see the cause."
        )
    out.extend(_activities_lines(w))
    out.extend(_boot_lines(w))
    return out


# --- views: what `echolot report` prints ---------------------------------------
#
# report.json is the agent's, and on a real hunt the agent cut it up sixteen
# times with jq and python one-liners: the keys, the window, which detectors
# fired with which thresholds, the top rows of one detector with the
# evidence shortened. Each of those is a view the report can offer directly,
# and each one-liner was a window's worth of json read to get at a line.

# What the evidence column means where it is not obvious. `main_thread_block`
# groups by thread as well as by name, and the thread is always the main
# one — so the column shows its comm, which the kernel cuts to fifteen
# characters: `m.example.myapp` for `com.example.myapp`, while
# `com.example.app` is fifteen exactly and arrives whole. Kept rather than
# renamed because it is part of the row's identity, and a rename would make
# every earlier report's rows vanish in `compare`.
EVIDENCE_LEGEND = {
    "main_thread_block": "the thread's comm — always the main thread here; "
                         "the kernel cuts the name to 15 characters",
    "uninstrumented_cpu": "the share of the thread's CPU time outside every "
                          "top-level slice; when the recording sampled "
                          "callstacks, how many stacks fell there, what ran on "
                          "them — the first named Kotlin or Java method from "
                          "the top — and after `ours:` the nearest frame of the "
                          "project's own code: `none` where a whole stack had "
                          "nothing of it, `cut` where the stack ended in the "
                          "framework before its thread's start",
    # "Max 1572 ms" beside "longest 89.1 ms" read as an error (#515): the two
    # measure different things.
    "frame_jank": "the kind of jank as the platform tags it, how many of the "
                  "window's frames had it, and the longest of them from start "
                  "to end. Total and Max are the time past each frame's "
                  "deadline, which a frame queued behind others can run far "
                  "beyond its own length",
}


def clip(text: Any, width: int) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= width else text[:width - 1] + "…"


# What the other thread of a wait was doing, at the end of the evidence:
# `monitor_contention`'s owner and `main_thread_waits`' waking thread
# (#417, #418).
_THEIR_STATE = re.compile(r" · (?:owner|its threads?): [^·]+$")


def clip_evidence(text: Any, width: int) -> str:
    """Evidence cut to `width`, the other thread's state at its end kept.

    A lock's evidence is ART's slice name, two frames long, and the owner's
    state after it is what tells the three fixes apart: cut from the end, it
    was the one part a view of the rows never showed. So the slice's name is
    what gives way.
    """
    text = "" if text is None else str(text)
    tail = _THEIR_STATE.search(text)
    if len(text) <= width or not tail:
        return clip(text, width)
    return clip(text[:tail.start()], max(width - len(tail.group(0)), 24)) + tail.group(0)


def _minor(version: str | None) -> tuple[int, int] | None:
    m = re.match(r"\s*(\d+)\.(\d+)", str(version or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def written_by(version: str | None, detectors: int | None = None,
               short: bool = False) -> str | None:
    """A report from another echolot, said; None for one from this minor version.

    `version` is the report's `toolchain.echolot`, None for a report from
    before it was written down (#507). Detectors are added and reworked from
    one minor version to the next, so a report read back from another one
    says the trace in another tool's words, and `status`, `report` and the
    door used to show it as if it were today's. A patch release changes no
    detector's reading, and is not said.
    """
    from . import __version__

    if version is not None and _minor(version) == _minor(__version__):
        return None
    if short:
        return (f"written by echolot {version}, this is {__version__} — analyze again"
                if version else "written by an older echolot — analyze again")
    ours = len(list(_DETECTOR_DIR.glob("*.sql")))
    theirs = f" ({detectors} detectors)" if detectors is not None else ""
    who = (f"echolot {version}" if version else
           "an echolot from before reports named their version")
    return (f"Written by {who}{theirs}; this is {__version__} ({ours}). Analyze "
            f"its traces again to read them with this one.")


_DETECTOR_DIR = Path(__file__).parent / "sql" / "detectors"


# Where a window with no anchor in the config is cut. context.sql falls back
# to the process's own slices, and says so in its comment; the report has to
# say it too (#614).
SPAN = "the process's first slice to where its last one ends"
FALLBACK = {"start_anchor": "the window opened at the process's first slice",
            "end_anchor": "the window ran to where the process's last slice ends"}


def _no_anchor_lines(w: dict[str, Any]) -> list[str]:
    """One line when the scenario names no start or no end anchor.

    `null` is what setup writes when no anchor was found, and it is honest
    in the config. The window then runs over everything the process did:
    on three `command` scenarios of one app it was 3 to 6 times the path
    from the tap to the settled screen, and the first row of two of them was
    a background batch that only happened to fall inside. A window cut by a
    launch (`defined_by`) has its own line, and a report older than the
    `start_anchor` key says nothing.
    """
    if w.get("defined_by"):
        return []
    start = "start_anchor" in w and w["start_anchor"] is None
    end = "end_anchor" in w and w["end_anchor"] is None
    if start and end:
        where = f"the window runs from {SPAN}"
        what = "No anchors in the scenario"
    elif start:
        where = "the window opens at the process's first slice"
        what = "No start anchor in the scenario"
    elif end:
        where = "the window runs to where the process's last slice ends"
        what = "No end anchor in the scenario"
    else:
        return []
    return [f"> {what}: {where}, so its length is everything the process did "
            f"there, and the scenario may be a small part of it. Name the "
            f"anchors in the config to measure the scenario alone."]


def _no_sources_lines(gap: dict[str, Any] | None) -> list[str]:
    """What `analyze` could not do in the code, when the checkout it was
    given holds no sources (#499): rows left without a file, and
    composables none of which read as the project's own."""
    if not isinstance(gap, dict):
        return []
    what = []
    if gap.get("rows"):
        what.append(f"{gap['rows']} row{'s' if gap['rows'] != 1 else ''} name code and none was placed")
    if gap.get("composable_calls"):
        what.append(f"{gap['composable_calls']} composable calls were recorded and none was told apart "
                    f"as the project's own, so `composables` found nothing to rank")
    if not what:
        return []
    return [f"> ⚠️ No Kotlin or Java sources under `{gap.get('root')}`: {'; '.join(what)}. Name the "
            f"checkout with `analyze --root` or `project.root` in echolot.yml."]


def _header_lines(report: dict[str, Any]) -> list[str]:
    w = report.get("window") or {}
    out = []
    traces = report.get("traces")
    what = f"{len(traces)} runs, medians" if traces else f"`{report.get('trace')}`"
    line = f"**{what}**"
    if report.get("scenario"):
        line += f" · scenario `{report['scenario']}`"
    line += f" · process `{w.get('process')}`"
    if w.get("duration_ms") is not None:
        line += f" · window **{w['duration_ms']} ms**"
        if w.get("duration_ms_min") is not None:
            line += f" (repeats {w['duration_ms_min']}–{w['duration_ms_max']})"
    if report.get("generated_at"):
        line += f" · written {when_mod.local(report['generated_at'])}"
    out.append(line)
    out.extend(_undrawn_lines(report))
    other = written_by((report.get("toolchain") or {}).get("echolot"),
                       (report.get("summary") or {}).get("detectors_run"))
    if other:
        out.append(f"⚠️ {other}")
    if report.get("same_recording"):
        out.append(f"> ⚠️ {same_recording_said(report['same_recording'])}")
    if report.get("not_cold"):
        out.append(f"> ⚠️ {not_cold_said(report['not_cold'], len(traces or [None]))}")
    if report.get("ended_elsewhere"):
        out.append(f"> ⚠️ {elsewhere_said(report['ended_elsewhere'], len(traces or [None]))}")
    if report.get("unfinished"):
        out.append(f"> ⚠️ {unfinished_said(report['unfinished'], len(traces or [None]))}")
    out.extend(_diverged_lines(w))
    anchors = []
    for key, label in (("start_anchor", "start"), ("end_anchor", "end")):
        a = w.get(key)
        if a:
            hit = a.get("matches")
            anchors.append(f"{label} `{a.get('glob')}` "
                           + ("⚠️ 0 matches" if hit == 0 else f"{hit} match(es)")
                           + (", ⚠️ never closed" if a.get("unfinished") else ""))
    if anchors:
        out.append("Anchors: " + " · ".join(anchors))
    out.extend(_window_warning_lines(w))
    out.extend(_drift_lines(report))
    out.extend(launch_mod.lines(w))
    alts = w.get("process_alternatives")
    if alts:
        out.append(f"⚠️ {w.get('process_alternatives_total') or len(alts)} other "
                   f"process(es) matched the mask; the largest was taken")
    out.extend(_budget_lines(w.get("main_thread")))
    out.extend(_crowding_lines(w.get("crowding")))
    out.extend(_outside_lines(w.get("crowding")))
    out.extend(_startup_lines(w.get("startup"), launch_mod.is_launch(w)))
    out.extend(_unjudged_lines(w))
    out.extend(_environment_lines(report.get("environment") or {}, _unbuilt(report)))
    out.extend(recording_mod.header_lines(report.get("recording")))
    out.extend(_no_sources_lines(report.get("no_sources")))
    out.extend(_mapping_lines(report))
    out.extend(_retrace_lines(report.get("retrace")))
    out.extend(_code_markers_lines(report))
    return out


def overview(report: dict[str, Any], cmd: str = "echolot report") -> str:
    """One screen: the window, and every detector with what it found.

    What `echolot report` prints with no view named, and what `analyze`
    prints when its output is not a terminal (#492). `cmd` is the command
    its pointers name: `echolot report` for the latest report, with the
    path in it for one elsewhere, which `report` with no path would not
    read.
    """
    out = ["# Report", ""]
    out.extend(_header_lines(report))
    m = report.get("markers") or {}
    rows, absent = m.get("rows") or [], m.get("absent") or []
    if rows or absent:
        out.append(f"Markers: {len(rows)} measured"
                   + (f", {len(absent)} listed in `domains` and not in the window" if absent else "")
                   + f" — `{cmd} --markers`")
    out.append("")
    table_rows = []
    for d in report.get("detectors") or []:
        found = d.get("rows") or []
        top = found[0] if found else None
        table_rows.append({
            "detector": d["id"],
            "rows": len(found) if found else "—",
            "thresholds": d.get("params_source") or "default",
            "top row": clip(top.get("location"), 48) + _of_threads(top) if top else (
                "failed" if d.get("error")
                else "not checked" if d.get("checked") is False else "silent"),
            "metric": (f"{metric_of(top).replace('_ms', '')} {top.get(metric_of(top))} ms"
                       if top and top.get(metric_of(top)) is not None else ""),
        })
    out.append(table.render(table_rows))
    absent_ids = (report.get("summary") or {}).get("absent_ids") or []
    if absent_ids:
        out.append("")
        out.append("Turned off in this config: " + ", ".join(f"`{a}`" for a in absent_ids))
    unseen = needs_mod.line(report.get("detectors") or [])
    if unseen:
        out.append("")
        out.append(unseen)
    out.append("")
    out.append(f"One detector's rows: `{cmd} --detector <id> --top 5`; "
               "the window and the device: `--window`; the markers: `--markers`; "
               "any of them as json: `--json`.")
    return "\n".join(out)


def detector_view(report: dict[str, Any], det_id: str, top: int = 5,
                  wide: bool = False) -> str:
    """One detector's rows, the longest first, with the evidence kept short."""
    d = next((x for x in report.get("detectors") or [] if x["id"] == det_id), None)
    if d is None:
        known = ", ".join(x["id"] for x in report.get("detectors") or [])
        return f"no detector `{det_id}` in this report — it has: {known}"
    out = [f"## {d.get('title') or det_id} (`{det_id}`)"]
    if d.get("why"):
        out.append(f"_{d['why']}_")
    out.append(f"params: {d.get('params')}{_source_note(d)}")
    if d.get("error"):
        out.append(f"⚠️ the detector failed: {d['error']}")
    unseen = needs_mod.could_not_run(d)
    if unseen:
        out.append(f"⚠️ not checked: {d['reason']}")
    if det_id == "frame_jank":
        out.extend(_unjudged_lines(report.get("window") or {}))
    out.append("")
    rows = d.get("rows") or []
    if not rows:
        out.append("_no row, and that is not silence — the detector could not look_"
                   if unseen else "_silent — no row cleared the thresholds_")
        return "\n".join(out)
    shown = []
    for r in rows[:top]:
        r = dict(r)
        if not wide:
            r["location"] = clip(r.get("location"), 60)
            # Evidence that ends in what the samples named is kept whole: that
            # end is what a reader of the row came for, and it is short by
            # construction — two names a list, each cut to stacks.LONGEST.
            if r.get("detail") is not None and not r.get("stacks"):
                r["detail"] = clip_evidence(r["detail"], 100)
        shown.append(r)
    out.append(_table(shown))
    if len(rows) > top:
        out.append("")
        out.append(f"_Showing {top} of {len(rows)};{_left_once(rows[top:])} "
                   f"`--top {len(rows)}` for all._")
    pooled = _threads_note(rows[:top])
    if pooled:
        out.append("")
        out.append(pooled)
    legend = EVIDENCE_LEGEND.get(det_id)
    if legend:
        out.append("")
        out.append(f"Evidence: {legend}.")
    if any(r.get("places") for r in rows[:top]):
        out.append("")
        out.append("`--json` carries `places`: file and line for every symbol the rows name.")
    if any(r.get("stacks") for r in rows[:top]):
        out.append("")
        out.append("`--json` carries `stacks`: the ten methods that ran most often, and "
                   "the ten nearest frames of the project's own, with their shares.")
    return "\n".join(out)


def _left_once(rows: list[dict[str, Any]]) -> str:
    """How many of the rows a view left out one repeat alone found.

    What the reader of a cut needs to judge it: on a large app's cold start
    one detector had 38 rows over five repeats, and 15 of them were found in
    one (#495). Rows ranked by `standing` put those last, so most of what a
    cut leaves out is often them, and saying how many tells the reader whether
    `--top` is worth a look. Empty for one trace, and when every row left out
    was found more than once.
    """
    once = [r for r in rows if (short_runs(r.get("runs")) or (0, 0))[0] == 1]
    if not once:
        return ""
    n = short_runs(once[0]["runs"])[1]
    if len(rows) == 1:
        return f" the one left was found in one run of {n}."
    if len(once) == len(rows):
        return f" all {len(rows)} left were found in one run of {n}."
    return f" of the {len(rows)} left, {len(once)} were found in one run of {n}."


def markers_view(report: dict[str, Any], top: int = 15) -> str:
    m = report.get("markers") or {}
    rows = m.get("rows") or []
    # Where a hunt looked for its markers and found none (#390), or found
    # ones the code no longer has (#393): the why goes above the table, or
    # above the line that says there is none.
    out = _code_markers_lines(report)
    if out:
        out.append("")
    if not rows and not m.get("absent"):
        return "\n".join([*out, "_no markers: nothing carries the prefix and `domains` "
                                "lists nothing_"])
    out += _markers_lines({**m, "rows": rows[:top]})
    if len(rows) > top:
        out.append(f"_Showing {top} of {len(rows)};{_left_once(rows[top:])} "
                   f"`--top {len(rows)}` for all._")
    return "\n".join(out)


def window_view(report: dict[str, Any]) -> str:
    """`report --window`: the window's own numbers, in milliseconds.

    It used to be the default view's header again, eleven lines of twelve
    word for word (#515). What it gives instead is what the header rounds
    to a percentage or leaves out: the process id and the trace's clock, the
    anchors or the launch that bound it, the main thread's time in ms, the
    frames of each repeat, how many threads were ready at once, and every
    reason of the startup. The device, the mapping and the code markers
    stay in the overview: they are not the window.
    """
    w = report.get("window") or {}
    out = ["## Window", ""]
    traces = report.get("traces")
    what = f"{len(traces)} runs, medians" if traces else f"`{report.get('trace')}`"
    if report.get("scenario"):
        what += f" · scenario `{report['scenario']}`"
    out.append(f"**{what}**")
    if w.get("process"):
        line = f"Process `{w['process']}`"
        if w.get("pid") is not None:
            line += f" (pid {w['pid']}" + (" in the first repeat)" if traces else ")")
        if isinstance(w.get("ts_start"), (int, float)) and isinstance(w.get("ts_end"), (int, float)):
            line += (f", on the trace's clock from {w['ts_start'] / 1e9:.6f} s "
                     f"to {w['ts_end'] / 1e9:.6f} s")
        out.append(line)
    if w.get("duration_ms") is not None:
        line = f"Length: **{w['duration_ms']} ms**"
        if w.get("duration_ms_min") is not None:
            line += f", repeats from {w['duration_ms_min']} to {w['duration_ms_max']} ms"
        out.append(line)
    anchors = []
    for key, label in (("start_anchor", "Start"), ("end_anchor", "End")):
        a = w.get(key)
        if isinstance(a, dict) and a:
            hit = a.get("matches")
            anchors.append(f"{label}: `{a.get('glob')}`, "
                           + ("⚠️ 0 matches" if hit == 0 else f"{hit} match(es)")
                           + (", ⚠️ never closed" if a.get("unfinished") else ""))
    out.extend(anchors)
    out.extend(launch_mod.lines(w))
    alts = w.get("process_alternatives")
    if alts:
        names = ", ".join(f"`{a.get('name')}`" for a in alts if isinstance(a, dict))
        out.append(f"Other processes the mask matched: {names}")
    mt = w.get("main_thread")
    if isinstance(mt, dict) and mt.get("window_ms"):
        parts = [f"{label} {mt[key]} ms" for key, label in (
            ("on_cpu", "on a CPU"), ("waiting_for_cpu", "waiting for a CPU"),
            ("in_kernel", "blocked in the kernel"), ("sleeping", "sleeping"), ("other", "other"))
            if mt.get(key)]
        out.append("Main thread: " + " · ".join(parts)
                   + f", {mt.get('accounted_ms')} of {mt['window_ms']} ms accounted for")
        if mt.get("in_rows_ms") is not None:
            out.append(f"In the findings: {mt['in_rows_ms']} ms ({mt.get('in_rows_pct')}%)")
    frames = w.get("frames")
    if isinstance(frames, dict) and frames.get("count") is not None:
        line = f"Frames: {frames['count']}"
        if frames.get("values"):
            line += " (repeats " + ", ".join(str(v) for v in frames["values"]) + ")"
        out.append(line)
        out.extend(_unjudged_lines(w))
    crowd = w.get("crowding")
    if isinstance(crowd, dict) and crowd.get("cores"):
        out.append(f"Threads ready at once: at most {crowd.get('peak')} on {crowd['cores']} cores, "
                   f"{crowd.get('above_cores_ms') or 0} ms with more ready than cores")
    st = w.get("startup")
    if isinstance(st, dict) and st.get("dur_ms") is not None:
        reasons = st.get("reasons") if isinstance(st.get("reasons"), dict) else {}
        parts = [f"{label} {round(ms, 2)} ms" for label, ms in startup_by_label(reasons)]
        out.append(f"Startup: {st.get('type') or ''} {st['dur_ms']} ms".replace("  ", " ")
                   + (": " + " · ".join(parts) if parts else ""))
    inside = w.get("opened_inside")
    if inside and inside.get("material"):
        out.append(f"⚠️ opened with the main thread already blocked: `{inside['state']}` "
                   f"for {inside['total_ms']} ms, {inside['before_ms']} ms of it before the "
                   + ("launch" if launch_mod.is_launch(w) else "anchor"))
    return "\n".join(out)



def _markers_lines(markers: dict[str, Any]) -> list[str]:
    """The project's own names, measured — above the findings, whatever fired.

    A detector shows a marker only where a threshold says so, and an agent
    that planted one wants the number every time. Absent names are listed
    rather than dropped: a `domains` entry the window never held is a map
    pointing at something this scenario does not run.
    """
    rows = markers.get("rows") or []
    absent = markers.get("absent") or []
    if not rows and not absent:
        return []
    out = ["## Markers"]
    prefix = markers.get("prefix")
    out.append(f"_the names `domains` lists and the `{prefix}` ones; medians per run, "
               f"self time with children subtracted_" if prefix else
               "_the names `domains` lists; medians per run_")
    out.append("")
    if rows:
        # A marker that never closed is a floor, and the table says so: in the
        # columns that depend on the repeats it was open in, or in all three
        # for a report of one recording, which has no `floors`.
        shown = [{**r, **{c: f"≥ {r[c]}" for c in r.get("floors") or _FLOORED
                          if r.get(c) is not None}} if r.get("unfinished") else r
                 for r in rows]
        out.append(table.render(shown, order=COLUMNS,
                                headers={**HEADERS, "location": "Marker", "detail": "Threads"},
                                skip=HIDDEN))
        out.append("")
        if any(r.get("unfinished") for r in rows):
            out.append("≥ — the marker never closed: its end did not run, or the "
                       "recording stopped first. The number runs to the end of "
                       "the window and is a floor.")
            out.append("")
        for r in rows:
            k_n = short_runs(r.get("unfinished_runs"))
            if k_n is None:
                continue
            there = ", ".join(f"≥ {ms} ms" for ms in r.get("open_ms") or [] if ms is not None)
            out.append(f"`{r['location']}` did not close in {k_n[0]} of {k_n[1]} repeats"
                       + (f" (there: {there})" if there else "")
                       + (". Only the numbers marked ≥ depend on those repeats."
                          if r.get("unfinished") else
                          ". Its numbers above do not depend on those repeats."))
            out.append("")
    if absent:
        out.append("Not in the window: " + ", ".join(f"`{g}`" for g in absent)
                   + " — listed in `domains`, never seen in this scenario.")
        out.append("")
    mixed = markers.get("mixed") or []
    if mixed:
        named = ", ".join(f"`{m['location']}` ({m['runs']})" for m in mixed)
        parents = list(dict.fromkeys(p for m in mixed for p in m.get("parents") or []))
        out.append(
            f"> ⚠️ Temporary markers are in some repeats and not others: {named}. "
            f"The set is two rounds, one with the markers and one without, not "
            f"repeats of one."
            + (" The self time of " + ", ".join(f"`{p}`" for p in parents)
               + ", which the markers sit in, was measured two ways — less the "
                 "markers in some runs, whole in the others — so its median is "
                 "neither; read its total." if parents else "")
            + " Analyze the two rounds apart.")
        out.append("")
    return out


def _config_line(report: dict[str, Any]) -> str | None:
    """Which config, and where the thresholds came from — one line.

    A report against calibrated numbers and one against the shipped defaults
    look identical otherwise, and the difference decides whether "silent"
    means clean or means the bar was set above the problem.
    """
    cfg = report.get("config")
    sources = {d.get("params_source", "default") for d in report["detectors"]}
    if not cfg and sources == {"default"}:
        return None
    parts = []
    if cfg and cfg.get("path"):
        parts.append(f"`{cfg['path']}`" + (f" (sha {cfg['sha']})" if cfg.get("sha") else ""))
    if cfg and cfg.get("local"):
        parts.append(f"local `{cfg['local']}`")
    if cfg and cfg.get("defaults"):
        parts.append("thresholds: **built-in defaults** (`--defaults`, the config's "
                     "detectors section ignored)")
    else:
        def counted(part: str) -> int:
            return sum(1 for d in report["detectors"]
                       if part in d.get("params_source", "").split("+"))
        n_cfg, n_own, n_cli = counted("config"), counted("scenario"), counted("cli")
        n_all = len(report["detectors"])
        if n_cfg == 0 and n_own == 0 and n_cli == 0:
            parts.append("thresholds: built-in defaults")
        else:
            bits = []
            if n_cfg:
                bits.append(f"from the config for {n_cfg}")
            if n_own:
                # A scenario's own `detectors:`, laid over the config's for it.
                who = f"scenario `{report['scenario']}`" if report.get("scenario") else "the scenario"
                bits.append(f"{who}'s own for {n_own}")
            if bits:
                bits[0] += f" of {n_all} detectors"
            if n_cli:
                bits.append(f"`--set` on {n_cli}")
            parts.append("thresholds: " + ", ".join(bits))
    return "Config: " + " · ".join(parts)


def _source_note(d: dict[str, Any]) -> str:
    src = d.get("params_source", "default")
    if src == "default":
        return ""
    note = f" — from the {src.replace('+', ' + ')}"
    if d.get("defaults"):
        note += f", defaults would be {d['defaults']}"
    return note


def _of_threads(row: dict[str, Any]) -> str:
    """` (8 threads)` after the name of a row that adds up a pool, or nothing."""
    n = row.get(THREADS)
    return f" ({n} threads)" if isinstance(n, int) and n > 1 else ""


def _threads_note(rows: list[dict[str, Any]]) -> str | None:
    """What a row of several threads is, under a table that has one (#511).

    The reference said not to add such a row up as time of the window, and the
    report did not: an agent read `DefaultDispatch`'s 3.8 s of waiting in a
    5.7 s cold start as the start's, and opened an investigation that was
    abandoned four days later. Contention was 2% of that start.
    """
    pooled = [r for r in rows if _of_threads(r)]
    if not pooled:
        return None
    named = ", ".join(f"`{r['location']}` {r[THREADS]}" for r in pooled[:3])
    more = f" and {len(pooled) - 3} more" if len(pooled) > 3 else ""
    return (f"_Threads: a row adds up every thread of its name — {named}{more}. "
            "Their time together is not time of the window, and can be longer "
            "than it; how the window was spent is in `--window`._")


def _table(rows: list[dict[str, Any]]) -> str:
    return table.render([_with_fired(r) for r in rows], order=COLUMNS,
                        headers=_headers(rows), skip=HIDDEN)


def _headers(rows: list[dict[str, Any]]) -> dict[str, str]:
    """`Max of any run` over rows merged from repeats.

    There Self and Total are medians and Max the longest any repeat saw
    (#512): one launch's longest call beside the median launch's total, so a
    row with one call a launch read "Total 65.86, Max 99.59" (#712). The
    header says the two come from different repeats.
    """
    def repeats(row: dict[str, Any]) -> int:
        try:
            return int(str(row["runs"]).split("/")[1])
        except (KeyError, IndexError, ValueError):
            return 1
    merged = any(repeats(r) > 1 for r in rows)
    return {**HEADERS, "max_ms": "Max of any run, ms"} if merged else HEADERS


def _with_fired(row: dict[str, Any]) -> dict[str, Any]:
    """`5/5, over the bar in 1`: found in every run, past the threshold once.

    The two counts are different news (#505). The first says how often the
    thing happened, the second how often it was large enough to report.
    """
    fired = short_runs(row.get("fired"))
    if fired is None:
        return row
    return {**row, "runs": f"{row['runs']}, over the bar in {fired[0]}"}

