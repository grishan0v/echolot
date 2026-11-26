#!/usr/bin/env python3
"""The main thread's idle at its message queue, and what woke it (#1030).

The budget's `sleeping` holds two things alike in their state: the looper
with nothing to do, and a call inside a message that blocked. `window.idle`
tells them apart by the slices, and for the longest stretches of idle says
what ended them: a timer, a thread of the app, another process, or nothing
before the window closed. The doctor checks pin `fixture.idle_looper`; here
every rule gets a moment of its own on one main thread, under an anchor that
spans the window and is no message.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import _woken_by, analyze_trace, idle_stretches  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.render import _budget_lines, _idle_lines, window_view  # noqa: E402

PID = 7300
APP = "com.example.app"
THREADS = {PID: APP, 7301: f"binder:{PID}_2", 7302: "Loader"}
SYSTEM = 1000
SYSTEM_THREADS = {SYSTEM: "system_server", 1001: "InputDispatcher"}
CPU = {7301: 1, 7302: 2, 1001: 3}
# No anchors: the window is the process's slices, `AppStart` from 0 to 300,
# which spans it, holds the idle moments too, and so is no message.
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "idle"}}

SLICES = [("AppStart", 0, 300, [
    ("inflate", 10, 30, []),
    ("deliverInputEvent", 70, 3, []),
    ("bind", 100, 50, []),
    ("draw", 200, 10, []),
    ("small", 215, 1, []),
])]

TIMER = 0      # woken from CPU 0's idle task: no thread
NEVER = -1     # still asleep when the recording stopped
# The main thread's sleeps: (from, to, who woke it).
SLEEPS = [
    (40, 70, 1001),      # idle: system_server's input thread, a short message after
    (73, 74, 7301),      # idle 1 ms: no moment, but inside the stretch
    (76, 100, TIMER),    # idle: its own timeout; 6 ms after the last, so one stretch
    (120, 135, 7302),    # inside bind: a wait, not idle
    (150, 200, 1001),    # idle: another process posted
    (210, 215, 7301),    # idle 5 ms: a moment too short to keep alone
    (216, 330, NEVER),   # idle to the end of the window and beyond it
]


def _trace(path: Path) -> Path:
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [(PID, APP, THREADS), (SYSTEM, "system_server", SYSTEM_THREADS)])
    names = {**THREADS, **SYSTEM_THREADS}
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {0: []}
    on = 0
    for start, end, waker in SLEEPS:
        by_cpu[0].append((fixture.ms(on), next(seq), "sched", "swapper/0", 0, fixture.R,
                          APP, PID))
        by_cpu[0].append((fixture.ms(start), next(seq), "sched", APP, PID, fixture.S,
                          "swapper/0", 0))
        if waker == TIMER:
            by_cpu[0].append((fixture.ms(end) - 1, next(seq), "waking", 0, PID, APP, 0))
        elif waker != NEVER:
            cpu = CPU[waker]
            by_cpu.setdefault(cpu, []).extend([
                (fixture.ms(end - 1), next(seq), "sched", f"swapper/{cpu}", 0, fixture.R,
                 names[waker], waker),
                (fixture.ms(end) - 1, next(seq), "waking", waker, PID, APP, 0),
                (fixture.ms(end), next(seq), "sched", names[waker], waker, fixture.S,
                 f"swapper/{cpu}", 0),
            ])
        on = end
    for ts, order, kind, name in fixture._flatten(SLICES, [], seq):
        buf = f"B|{PID}|{name}\n" if kind == "B" else f"E|{PID}\n"
        by_cpu[0].append((ts, order, "print", PID, buf))
    # Something on another CPU after the main thread's last sleep, so the
    # recording runs past the window.
    by_cpu.setdefault(3, []).extend([
        (fixture.ms(320), next(seq), "sched", "swapper/3", 0, fixture.R, "InputDispatcher", 1001),
        (fixture.ms(330), next(seq), "sched", "InputDispatcher", 1001, fixture.S, "swapper/3", 0),
    ])
    fixture.ftrace(builder, by_cpu)
    path.write_bytes(builder.serialize())
    return path


def _analyzed(tmp_path: Path) -> dict:
    return analyze_trace(_trace(tmp_path / "idle.perfetto-trace"), Config(CONFIG))


def test_idle_is_the_sleeps_with_no_message_open_and_the_anchor_is_none(tmp_path: Path) -> None:
    window = _analyzed(tmp_path)["window"]
    check("the budget's sleeping is every sleep in the window",
          window["main_thread"]["sleeping"] == 209.0, window["main_thread"])
    check("idle is all of it but the wait inside bind, the 1 ms and 5 ms sleeps too",
          window["idle"]["ms"] == 194.0, window["idle"])


def test_stretches_join_across_short_work_and_say_what_ended_them(tmp_path: Path) -> None:
    got = _analyzed(tmp_path)["window"]["idle"]["stretches"]
    check("three stretches, in time order", len(got) == 3, got)
    check("30 ms, 1 ms and 24 ms of idle are one stretch across 3 and 2 ms of work, "
          "after inflate, until bind, ended by a timer",
          got[0] == {"at_ms": 40.0, "ms": 55.0, "span_ms": 60.0, "after": "inflate",
                     "until": "bind", "woken_by": "timer"}, got[0])
    check("the next stretch begins after bind's 50 ms, and system_server ended it",
          got[1] == {"at_ms": 150.0, "ms": 50.0, "span_ms": 50.0, "after": "bind",
                     "until": "draw", "woken_by": "other",
                     "waker": {"thread": "InputDispatcher", "process": "system_server"}}, got[1])
    check("5 ms and the sleep that outlived the window are one, ended by nothing in it",
          got[2] == {"at_ms": 210.0, "ms": 89.0, "span_ms": 90.0, "after": "draw",
                     "until": None, "woken_by": "end"}, got[2])


def test_the_same_trace_gives_the_same_bytes(tmp_path: Path) -> None:
    first = _analyzed(tmp_path)["window"]["idle"]
    second = _analyzed(tmp_path)["window"]["idle"]
    check("idle is deterministic", json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
          (first, second))


def test_the_report_splits_sleeping_and_lists_the_longest_three(tmp_path: Path) -> None:
    report = _analyzed(tmp_path)
    lines = report_mod.to_markdown(report).splitlines()
    budget = next(x for x in lines if x.startswith("Main thread:"))
    check("sleeping in two: 5% asleep inside a message, 65% idle at the message queue",
          "5% asleep inside a message · 65% idle at the message queue" in budget, budget)
    idle = [x for x in lines if x.startswith("Idle at the message queue")]
    check("a line per stretch, in time order", len(idle) == 3, idle)
    check("each says what ended it",
          [x.split(" — ")[1] for x in idle]
          == ["woken by a timer: no thread woke it, its own delayed message or timeout came due",
              "woken by `InputDispatcher` of `system_server`",
              "still idle when the window closed"], idle)
    view = window_view(report)
    check("the window view gives the idle in ms and every stretch, as bullets of its own",
          "Of the sleeping, idle at the message queue: 194.0 ms" in view
          and "- **55.0 ms** over 60.0 ms, from 40.0 ms into the window" in view
          and "Idle at the message queue:" not in view, view)


def test_a_report_without_idle_keeps_its_line() -> None:
    budget = {"on_cpu": 50.0, "sleeping": 50.0, "window_ms": 100.0, "accounted_pct": 100.0}
    check("sleeping as it was", _budget_lines(budget)[0] == "Main thread: 50% on a CPU · 50% sleeping",
          _budget_lines(budget))
    check("and no idle lines", _idle_lines(None) == [] and _idle_lines({"ms": 3.0}) == [])


def test_moments_join_only_across_short_work() -> None:
    def sleep(lo: float, hi: float) -> dict:
        return {"lo": lo * 1e6, "hi": hi * 1e6, "ns": (hi - lo) * 1e6}
    sleeps = [sleep(0, 10), sleep(11, 11.5), sleep(17, 30), sleep(39, 60), sleep(61, 61.5)]
    got = [(s["lo"] / 1e6, s["hi"] / 1e6, s["ns"] / 1e6)
           for s in idle_stretches(sleeps, 2e6, 8e6)]
    check("7 ms of work joins, 9 does not; a short sleep counts where it falls and "
          "carries nothing on",
          got == [(0, 30, 23.5), (39, 60, 21.0)], got)


def test_an_interrupt_is_a_timer_whoever_was_on_the_cpu() -> None:
    check("an interrupt's wakeup names no thread, even with one recorded as the waker",
          _woken_by(None, {"irq": 1, "waker_utid": 42, "past_end": 0}) == {"woken_by": "timer"})
    check("the idle task's is a timer too",
          _woken_by(None, {"irq": None, "waker_utid": 0, "past_end": 0}) == {"woken_by": "timer"})
    check("no wakeup recorded is unknown",
          _woken_by(None, {"irq": None, "waker_utid": None, "past_end": 0}) == {"woken_by": "unknown"})
    check("asleep past the window's end is nothing in it",
          _woken_by(None, {"irq": 1, "waker_utid": 0, "past_end": 1}) == {"woken_by": "end"})


def test_a_set_keeps_the_stretches_its_repeats_agree_on(tmp_path: Path) -> None:
    first = _analyzed(tmp_path)
    second = copy.deepcopy(first)
    second["window"]["idle"]["ms"] = 150.0
    second["window"]["idle"]["stretches"][1]["waker"]["thread"] = "InputReader"
    second["window"]["idle"]["stretches"][1]["ms"] = 40.0
    merged = report_mod.aggregate([first, second])["window"]["idle"]
    check("the idle is the median", merged["ms"] == 172.0 and merged["runs"] == "2/2", merged)
    other = merged["stretches"][1]
    check("one process ends both, so one stretch, the median of its two, the thread a tie "
          "broken by name",
          (other["ms"], other["runs"], other["waker"])
          == (45.0, "2/2", {"thread": "InputDispatcher", "process": "system_server"}), other)


def test_a_stretch_few_repeats_had_is_not_under_the_budget() -> None:
    stretch = {"at_ms": 10.0, "ms": 20.0, "span_ms": 20.0, "after": "inflate", "until": "bind",
               "woken_by": "timer"}
    idle = {"ms": 20.0, "stretches": [{**stretch, "runs": "7/15"}, {**stretch, "at_ms": 50.0,
                                                                     "runs": "8/15"}]}
    shown = _idle_lines(idle)
    check("8 of 15 is shown, 7 of 15 is not", len(shown) == 1 and "(8/15 runs)" in shown[0], shown)
    check("all of them when asked for all", len(_idle_lines(idle, shown=None)) == 2)
    check("a report of one trace has no runs to count", len(_idle_lines({"stretches": [stretch]})) == 1)
