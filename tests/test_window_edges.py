"""What crosses the window's edges, and what never closed.

trace_processor gives a slice or a thread state still open when the recording
stopped `dur = -1`. `_slice_win` reads such a slice as running to the end of
the window; these checks hold the rest of the pipeline to the same reading:
the window's own end, the children a self time subtracts, a thread's last
state, and a cooling level raised before the window opened. Each trace is the
fixture with one thing changed, the way `test_app_init` builds its own.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402

NO_ANCHORS = {"project": selftest.FIXTURE_CONFIG["project"],
              "scenario": {"name": "fixture"}}
# The fixture's own slices, before any test here patches them.
SLICES = fixture.SLICES


def _analysed(tmp_path: Path, config: dict | None = None) -> dict:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    return analyze_trace(trace, Config(config or selftest.FIXTURE_CONFIG))


def _at(ns: int) -> float:
    """A timestamp in the fixture's milliseconds."""
    return (ns - fixture.BASE_NS) / 1e6


def _rows(report: dict, detector: str) -> dict[str, dict]:
    det = next(d for d in report["detectors"] if d["id"] == detector)
    check(f"{detector} ran without an error", det["error"] is None, det["error"])
    return {r["location"]: r for r in det["rows"]}


def _ending_on(name: str, dur: float | None) -> tuple[dict, dict]:
    """The fixture's slices with `name` on a worker at 500 ms, and a config
    that ends the window on it."""
    slices = copy.deepcopy(SLICES)
    slices[fixture.TID_WORKER] = [*slices.get(fixture.TID_WORKER, []),
                                  (name, 500, dur, [])]
    config = copy.deepcopy(selftest.FIXTURE_CONFIG)
    config["scenario"]["end"] = {"name": name}
    return slices, config


def test_an_end_anchor_that_never_closed_runs_to_the_end_of_the_trace(
        tmp_path: Path, monkeypatch) -> None:
    """The scenario got stuck: the stall belongs in the window, and is said."""
    slices, config = _ending_on("AGENTTMP_screen_ready", 50)
    monkeypatch.setattr(fixture, "SLICES", slices)
    w = _analysed(tmp_path, config)["window"]
    check("closed, the window ends where the anchor does",
          (_at(w["ts_start"]), _at(w["ts_end"])) == (100.0, 550.0), w)
    check("and the anchor is not called unfinished",
          "unfinished" not in w["end_anchor"], w["end_anchor"])

    slices, config = _ending_on("AGENTTMP_screen_ready", None)
    monkeypatch.setattr(fixture, "SLICES", slices)
    report = _analysed(tmp_path, config)
    w = report["window"]
    check("open, the window runs to the end of the trace, not to where it began",
          _at(w["ts_end"]) == 1400.0, w)
    check("the anchor still matched once, and is marked as never closed",
          w["end_anchor"] == {"glob": "AGENTTMP_screen_ready", "matches": 1,
                              "unfinished": True}, w["end_anchor"])
    check("the marker is in the markers table",
          any(r["location"] == "AGENTTMP_screen_ready"
              for r in report["markers"]["rows"]), report["markers"])

    from echolot import report as report_mod
    md = report_mod.to_markdown(report)
    check("report.md says the scenario did not reach its end",
          "`AGENTTMP_screen_ready` never closed" in md, md[:2000])


def test_without_anchors_an_open_slice_runs_the_window_to_the_end(
        tmp_path: Path, monkeypatch) -> None:
    """A main thread stuck in a lock wait as the rest of the process went quiet.

    The window used to close where the wait began, which took the wait out of
    every row and left `monitor_contention` with nothing to say.
    """
    monkeypatch.setattr(fixture, "SLICES", {fixture.TID_MAIN: [
        ("Choreographer#doFrame 1", 10, None, [
            ("monitor contention with owner worker (4101)", 50, None, []),
        ]),
    ]})
    report = _analysed(tmp_path, NO_ANCHORS)
    w = report["window"]
    check("the window runs from the first slice to the end of the trace",
          (_at(w["ts_start"]), _at(w["ts_end"])) == (10.0, 1400.0), w)
    rows = _rows(report, "main_thread_block")
    check("the wait is the main thread's longest stretch of its own",
          rows["monitor contention with owner worker (4101)"]["self_ms"] == 1350.0
          and rows["Choreographer#doFrame 1"]["self_ms"] == 40.0, rows)
    check("and monitor_contention reports it", _rows(report, "monitor_contention"),
          report["detectors"])


def test_an_open_child_counts_once(tmp_path: Path, monkeypatch) -> None:
    """With B/E events the parent of an open slice is open too.

    A 300 ms window, 100 to 400 ms: `AppStart` closed after 200, then a frame
    opened at 300 with a lock wait opened inside it at 350, neither closed.
    The frame's own time is 50 ms. Read as zero long, the wait landed in the
    frame's self time as well, and the self times came to 350 ms in 300.
    """
    monkeypatch.setattr(fixture, "SLICES", {
        fixture.TID_MAIN: [
            ("AppStart", 100, 200, []),
            ("AGENTTMP_frame", 300, None, [("AGENTTMP_lock", 350, None, [])]),
        ],
        # The end anchor, on a thread of its own and closed at 400 ms. A
        # closed child of the open frame after the window would be cut too.
        fixture.TID_WORKER: [("Screen.firstFrame", 390, 10, [])],
    })
    report = _analysed(tmp_path)
    w = report["window"]
    check("the window is 100 to 400 ms",
          (_at(w["ts_start"]), _at(w["ts_end"])) == (100.0, 400.0), w)
    rows = _rows(report, "main_thread_block")
    selfs = {name: r["self_ms"] for name, r in rows.items()}
    check("each slice's self time is its own",
          selfs == {"AppStart": 200.0, "AGENTTMP_frame": 50.0,
                    "AGENTTMP_lock": 50.0}, selfs)
    check("and they add up to no more than the window",
          sum(selfs.values()) <= w["duration_ms"], selfs)
    marker = {r["location"]: r for r in report["markers"]["rows"]}
    check("the markers table subtracts the open child the same way",
          marker["AGENTTMP_frame"]["self_ms"] == 50.0
          and marker["AGENTTMP_frame"]["total_ms"] == 100.0, marker)


def test_a_closed_child_after_the_window_is_not_subtracted(
        tmp_path: Path, monkeypatch) -> None:
    """An open slice stops at the window's end, and so does what it holds."""
    monkeypatch.setattr(fixture, "SLICES", {
        fixture.TID_MAIN: [
            ("AppStart", 100, 200, []),
            ("AGENTTMP_frame", 300, None, [("AGENTTMP_after", 450, 50, [])]),
        ],
        fixture.TID_WORKER: [("Screen.firstFrame", 390, 10, [])],
    })
    rows = _rows(_analysed(tmp_path), "main_thread_block")
    check("the frame's 100 ms in the window are all its own",
          rows["AGENTTMP_frame"]["self_ms"] == 100.0, rows)


def _throttled(tmp_path: Path, monkeypatch, cooling: list) -> bool:
    monkeypatch.setattr(fixture, "COOLING", cooling)
    return _analysed(tmp_path)["environment"]["thermal"]["throttled"]


def test_a_cooling_level_holds_until_the_next_sample(
        tmp_path: Path, monkeypatch) -> None:
    """The window is 100 to 1105 ms; the level that decides its start is the
    one set before it, as with the clock."""
    device = "thermal-cpufreq-0"
    check("a level raised before the window opened throttles it",
          _throttled(tmp_path, monkeypatch, [(0, device, 2)]))
    check("and still does when it falls to zero inside the window",
          _throttled(tmp_path, monkeypatch, [(0, device, 2), (700, device, 0)]))
    check("a level raised inside the window throttles it",
          _throttled(tmp_path, monkeypatch, [(150, device, 2)]))
    check("a level raised after the window does not",
          not _throttled(tmp_path, monkeypatch, [(0, device, 0), (1200, device, 3)]))


def test_a_thread_state_still_open_at_the_end_is_counted(tmp_path: Path) -> None:
    """The main thread sleeps from 1300 ms to the end of the recording.

    With no anchors the window is 0 to 1400 ms. The thread's first state
    begins at 50 ms; everything after that is the thread's, its last sleep
    included, which used to be dropped and blamed on a thread that was not
    there.
    """
    report = _analysed(tmp_path, NO_ANCHORS)
    budget = report["window"]["main_thread"]
    check("the window is 0 to 1400 ms",
          report["window"]["duration_ms"] == 1400.0, report["window"])
    check("the budget counts the last sleep: 1350 ms of 1400",
          budget["accounted_ms"] == 1350.0 and budget["accounted_pct"] == 96.4,
          budget)
