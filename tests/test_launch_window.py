#!/usr/bin/env python3
"""`scenario.window: launch`: a window taken from the launch, with no anchor (#446).

The rule is the self-check's to pin on the fixture's launch, with and without
a `reportFullyDrawn()` call. Here: each end the rule can give, which launch is
taken when a trace holds several, and that the startup block is the launch
the window was built from. How a config asks for it is in
`test_scenario_window.py`, what the report says in `test_launch_report.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, selftest  # noqa: E402
from echolot import launch as launch_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402
from echolot.main import _startup_info, analyze_trace  # noqa: E402

APP = "com.example.app"
PROJECT = {"package": APP, "process": APP}
LAUNCH = {"project": PROJECT, "scenario": {"name": "startup", "window": "launch"}}
MS = 1_000_000


def _ms(report: dict) -> tuple[float, float]:
    """The window's bounds in the fixture's own milliseconds."""
    w = report["window"]
    return ((w["ts_start"] - fixture.BASE_NS) / MS, (w["ts_end"] - fixture.BASE_NS) / MS)


def _fixture(tmp_path: Path, config: dict = LAUNCH, **build) -> dict:
    trace = tmp_path / "launch.perfetto-trace"
    trace.write_bytes(fixture.build(**build))
    return analyze_trace(trace, Config(config))


# --- the rule, on the fixture's launch ------------------------------------------

def test_a_launch_without_the_call_closes_at_its_end(tmp_path: Path) -> None:
    rep = _fixture(tmp_path)
    w = rep["window"]
    check("from the intent at 40 to the launch's end at 1105", _ms(rep) == (40.0, 1105.0), _ms(rep))
    check("says so", (w["defined_by"], w["report_fully_drawn"]) == ("launch", None), w)
    check("has no anchors to prove", w["start_anchor"] is None and w["end_anchor"] is None, w)
    check("and its startup is the window", (w["startup"]["from_window_start_ms"],
          w["startup"]["from_window_end_ms"], w["startup"]["in_window_ms"])
          == (0.0, 0.0, 1065.0), w["startup"])
    check("no detector failed on it", [d["id"] for d in rep["detectors"] if d["error"]] == [])


def test_a_launch_with_the_call_runs_on_to_its_end(tmp_path: Path) -> None:
    rep = _fixture(tmp_path, fully_drawn="app")
    w = rep["window"]
    check("to the end of the app's first `reportFullyDrawn` slice, the outer of the two",
          _ms(rep) == (40.0, 1160.4), _ms(rep))
    check("says so", (w["defined_by"], w["report_fully_drawn"])
          == ("launch+reportFullyDrawn", "app"), w)
    check("and the startup ended 55.4 ms before the window closed",
          w["startup"]["from_window_end_ms"] == -55.4 and w["startup"]["dur_ms"] == 1065.0,
          w["startup"])


def test_system_servers_record_stands_in_for_a_missing_slice(tmp_path: Path) -> None:
    rep = _fixture(tmp_path, fully_drawn="system_server")
    w = rep["window"]
    check("to the end of `ActivityManager:ReportingFullyDrawn <package>`",
          _ms(rep) == (40.0, 1162.8), _ms(rep))
    check("the same definition, read from system_server",
          (w["defined_by"], w["report_fully_drawn"])
          == ("launch+reportFullyDrawn", "system_server"), w)


def test_a_call_before_the_first_frame_closes_nothing(tmp_path: Path) -> None:
    rep = _fixture(tmp_path, fully_drawn="early")
    w = rep["window"]
    check("the launch's end is the later of the two", _ms(rep) == (40.0, 1105.0), _ms(rep))
    check("and the report says the call was there",
          (w["defined_by"], w["report_fully_drawn"]) == ("launch", "app"), w)


def test_an_anchored_report_carries_none_of_it(tmp_path: Path) -> None:
    rep = _fixture(tmp_path, selftest.FIXTURE_CONFIG, fully_drawn="app")
    w = rep["window"]
    check("no `defined_by`, no `report_fully_drawn`",
          "defined_by" not in w and "report_fully_drawn" not in w, sorted(w))
    check("no share before the process in its budget",
          "before_process_ms" not in w["main_thread"], w["main_thread"])
    check("and its text says nothing of a launch window",
          "Window: the launch" not in report_mod.to_markdown(rep))


# --- which launch, in a trace of several -----------------------------------------

PID, SYSTEM = 7300, 1000


def _two_launches(path: Path, *, call_at: float | None = None, second_pid: int | None = None,
                  failed_only: bool = False) -> Path:
    """A process started at 100 (cold) and brought to the front again at 500.

    `second_pid` makes the second launch another process's of the same
    package, as two cold starts in one recording are. `failed_only` leaves a
    launch that never completed, and no process activity inside it.
    """
    builder = fixture.TraceProtoBuilder()
    procs = [(PID, APP, {PID: APP}), (SYSTEM, "system_server", {SYSTEM: "system_server"})]
    if second_pid:
        procs.append((second_pid, APP, {second_pid: APP}))
    fixture.process_tree(builder, procs)
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {0: [], 1: []}

    def on_cpu(cpu: int, pid: int, start: float, end: float) -> None:
        by_cpu[cpu] += [
            (fixture.ms(start), next(seq), "sched", f"swapper/{cpu}", 0, fixture.R, APP, pid),
            (fixture.ms(end), next(seq), "sched", APP, pid, fixture.S, f"swapper/{cpu}", 0)]

    def slices(cpu: int, pid: int, tree: list) -> None:
        for ts, order, kind, name in fixture._flatten(tree, [], seq):
            buf = f"B|{pid}|{name}\n" if kind == "B" else f"E|{pid}\n"
            by_cpu[cpu].append((ts, order, "print", pid, buf))

    def launch(ident: int, start: float, end: float, completed: bool = True) -> None:
        name = f"launchingActivity#{ident}"
        events = [(start, f"S|{SYSTEM}|{name}|{ident}"), (end, f"F|{SYSTEM}|{name}|{ident}")]
        if completed:
            events += [(end, f"B|{SYSTEM}|{name}:completed:{APP}"), (end, f"E|{SYSTEM}")]
        for at, buf in events:
            by_cpu[0].append((fixture.ms(at), next(seq), "print", SYSTEM, buf + "\n"))

    if failed_only:
        on_cpu(0, PID, 10, 20)
        slices(0, PID, [("Idle", 10, 5, [])])
        launch(1, 100, 300, completed=False)
    else:
        on_cpu(0, PID, 130, 900)
        first = [("bindApplication", 140, 60, []), ("activityStart", 210, 5, []),
                 ("activityResume", 220, 5, [])]
        second = [("activityStart", 520, 5, []), ("activityResume", 530, 5, [])]
        call = ([("reportFullyDrawn() for ComponentActivity", call_at, 0.5, [])]
                if call_at is not None else [])
        launch(1, 100, 300)
        launch(2, 500, 600)
        if second_pid:
            on_cpu(1, second_pid, 505, 900)
            slices(0, PID, sorted(first + call, key=lambda s: s[1]))
            slices(1, second_pid, [("bindApplication", 510, 5, []), *second,
                                   ("Choreographer#doFrame", 560, 20, [])])
        else:
            slices(0, PID, sorted(first + second + call, key=lambda s: s[1]))
    fixture.ftrace(builder, by_cpu)
    path.write_bytes(builder.serialize())
    return path


def test_the_first_launch_of_the_process_is_the_window(tmp_path: Path) -> None:
    rep = analyze_trace(_two_launches(tmp_path / "two.perfetto-trace"), Config(LAUNCH))
    w = rep["window"]
    check("the cold one at 100, and not the one brought to the front at 500",
          _ms(rep) == (100.0, 300.0) and w["defined_by"] == "launch", (_ms(rep), w))
    check("its startup block is that launch, of the two in the trace",
          (w["startup"]["type"], w["startup"]["dur_ms"], w["startup"]["startups"])
          == ("cold", 200.0, 2), w["startup"])
    check("the stretch before the process started is the budget's, not a hole",
          w["main_thread"]["before_process_ms"] == 30.0, w["main_thread"])


def test_a_call_after_the_next_launch_is_not_this_launchs(tmp_path: Path) -> None:
    late = analyze_trace(_two_launches(tmp_path / "late.perfetto-trace", call_at=700),
                         Config(LAUNCH))
    check("called at 700, after the launch at 500 began: the first closes at its end",
          _ms(late) == (100.0, 300.0) and late["window"]["report_fully_drawn"] is None,
          (_ms(late), late["window"]))
    between = analyze_trace(_two_launches(tmp_path / "between.perfetto-trace", call_at=400),
                            Config(LAUNCH))
    check("called at 400, before it: the first runs on to the call",
          _ms(between) == (100.0, 400.5)
          and between["window"]["defined_by"] == "launch+reportFullyDrawn",
          (_ms(between), between["window"]))


def test_the_launch_is_the_measured_processs_own(tmp_path: Path) -> None:
    """Two processes of one package, each with its launch: `analyze` measures
    the one with the most slices, and its launch is the window, not the
    package's first."""
    rep = analyze_trace(_two_launches(tmp_path / "pids.perfetto-trace", second_pid=7400,
                                      call_at=None), Config(LAUNCH))
    check("the process with four slices, started by the second launch",
          rep["window"]["pid"] == 7400 and _ms(rep) == (500.0, 600.0),
          (rep["window"]["pid"], _ms(rep)))


def test_a_trace_without_a_launch_of_the_process_is_refused(tmp_path: Path) -> None:
    trace = _two_launches(tmp_path / "none.perfetto-trace", failed_only=True)
    with pytest.raises(ConfigError) as e:
        analyze_trace(trace, Config(LAUNCH))
    check("in a sentence, with the trace and what to do", "no launch of com.example.app in "
          "trace none.perfetto-trace" in str(e.value) and "`start` and `end`" in str(e.value),
          e.value)


class _NoModule:
    def exec_script(self, sql: str) -> None:
        raise RuntimeError("Module not found: android.startup.startups")

    def query(self, sql: str) -> list[dict]:
        return [{"name": APP}]


def test_a_trace_processor_without_the_module_is_said() -> None:
    with pytest.raises(ConfigError) as e:
        launch_mod.find(_NoModule(), 1)
    check("the window cannot be built, and the reason is the module",
          "android.startup.startups" in str(e.value), e.value)


# --- the startup block is the launch that built the window ------------------------

class _Startups:
    """What `_startup_info` asks of a trace_processor, answered from a list."""

    def __init__(self, startups: list[dict]):
        self.startups, self.asked = startups, []

    def exec_script(self, sql: str) -> None:
        pass

    def query(self, sql: str) -> list[dict]:
        self.asked.append(sql)
        return self.startups if "FROM android_startups" in sql else []


def _startup(sid: int, start_ms: float, end_ms: float) -> dict:
    return {"startup_id": sid, "ts": int(start_ms * MS), "ts_end": int(end_ms * MS),
            "dur": int((end_ms - start_ms) * MS), "type": "cold"}


def test_the_startup_block_is_not_picked_by_overlap_for_a_launch_window() -> None:
    # The window runs 100..1100, on to a late `reportFullyDrawn`. The second
    # launch lies wholly inside it and shares more of it than the first.
    window = {"ts_start": 100 * MS, "ts_end": 1100 * MS}
    startups = [_startup(1, 100, 400), _startup(2, 450, 1000)]
    by_overlap = _startup_info(_Startups(startups), window, APP)
    check("by overlap it would be the second", by_overlap["dur_ms"] == 550.0, by_overlap)
    tp = _Startups(startups)
    built = _startup_info(tp, window, APP, {"startup_id": 1})
    check("for a launch window it is the launch the window was built from",
          (built["dur_ms"], built["from_window_start_ms"], built["from_window_end_ms"])
          == (300.0, 0.0, -700.0), built)
    check("asked for by its id as well, whatever its package is written as",
          "startup_id = 1" in tp.asked[0], tp.asked[0])


# --- the stretch before the process is in the budget ---------------------------------

def test_the_fixtures_launch_window_accounts_for_itself(tmp_path: Path) -> None:
    budget = _fixture(tmp_path)["window"]["main_thread"]
    check("the main thread's first state is at 50, 10 ms into the window",
          (budget["before_process_ms"], budget["accounted_ms"], budget["window_ms"])
          == (10.0, 1055.0, 1065.0), budget)
