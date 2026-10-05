"""What `analyze` writes about the window and the markers, at the edges.

A detector that failed, a marker that never closed, an end anchor that occurs
only before the start, and a window that opened inside a block that was still
open, or that ran on past the window's end. Each trace is the fixture with one
thing changed, the way `test_app_init` builds its own.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from echolot.tp import Detector  # noqa: E402

# The fixture's own tables, before any test here patches them.
SLICES = fixture.SLICES
SCHED = fixture.SCHED
D, S = fixture.D, fixture.S


def _analysed(tmp_path: Path, config: dict | None = None, **overrides) -> dict:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    return analyze_trace(trace, Config(config or selftest.FIXTURE_CONFIG),
                         cli_overrides=overrides or None)


def test_a_failed_detector_reports_the_params_it_was_asked_to_run_with(
        tmp_path: Path, monkeypatch) -> None:
    render = Detector.render

    def broken(self, overrides=None):
        if self.id == "main_thread_block":
            raise RuntimeError("no such column: foo")
        return render(self, overrides)

    monkeypatch.setattr(Detector, "render", broken)
    rep = _analysed(tmp_path, main_thread_block={"min_slice_ms": 83.3})
    det = next(d for d in rep["detectors"] if d["id"] == "main_thread_block")
    check("it failed", det["error"] == "no such column: foo", det)
    check("with the threshold it was given, not the shipped one",
          det["params"]["min_slice_ms"] == 83.3, det["params"])


def test_a_marker_that_never_closed_is_a_floor(tmp_path: Path, monkeypatch) -> None:
    slices = copy.deepcopy(SLICES)
    slices[fixture.TID_WORKER] = [("AGENTTMP_fetch", 150, None, [])]
    monkeypatch.setattr(fixture, "SLICES", slices)
    rep = _analysed(tmp_path)
    row = next(r for r in rep["markers"]["rows"] if r["location"] == "AGENTTMP_fetch")
    check("the row says it never closed", row.get("unfinished") is True, row)
    md = report_mod.to_markdown(rep)
    check("and report.md prints its numbers as a floor",
          f"≥ {row['total_ms']}" in md and "the marker never closed" in md, md)
    merged = report_mod.aggregate([rep, rep])
    row = next(r for r in merged["markers"]["rows"] if r["location"] == "AGENTTMP_fetch")
    check("a merge keeps it a floor", row.get("unfinished") is True, row)


def test_an_end_anchor_only_before_the_start_does_not_count(tmp_path: Path) -> None:
    config = copy.deepcopy(selftest.FIXTURE_CONFIG)
    config["scenario"]["start"] = {"name": "Screen.firstFrame"}
    config["scenario"]["end"] = {"name": "AppStart"}
    rep = _analysed(tmp_path, config)
    end = rep["window"]["end_anchor"]
    check("no match closes the window, and the earlier one is counted apart",
          end["matches"] == 0 and end["before_start"] == 1, end)
    md = report_mod.to_markdown(rep)
    check("report.md says the anchors look swapped",
          "occurs only before the start anchor" in md, md[:2000])


def _scene(monkeypatch, main_sched: list, main_slices: list,
           start: float, end: float) -> dict:
    """The main thread on its own schedule, and anchors on a worker."""
    rest = [e for e in SCHED if e[1] not in (fixture.TID_MAIN, fixture.TID_WORKER)]
    monkeypatch.setattr(fixture, "SCHED", [
        *rest, *main_sched, (1, fixture.TID_WORKER, start - 50, end + 50, S)])
    monkeypatch.setattr(fixture, "SLICES", {
        fixture.TID_MAIN: main_slices,
        fixture.TID_WORKER: [("scenario_start", start, 1, []),
                             ("scenario_end", end - 10, 10, [])],
    })
    return {"project": selftest.FIXTURE_CONFIG["project"],
            "scenario": {"name": "x", "start": {"name": "scenario_start"},
                         "end": {"name": "scenario_end"}}}


def test_a_window_opened_inside_a_block_that_never_let_go(
        tmp_path: Path, monkeypatch) -> None:
    """Asleep from 200 ms inside a lock wait that never closed; the window is
    900 to 1000 ms."""
    config = _scene(monkeypatch, [(0, fixture.TID_MAIN, 100, 200, S)],
                    [("Looper.dispatch", 150, None, [
                        ("monitor contention with owner worker (4201)", 190, None, [])])],
                    900, 1000)
    inside = _analysed(tmp_path, config)["window"]["opened_inside"]
    check("the block is seen, and is most of what the window shows",
          inside is not None and inside["state"] == "S"
          and inside["before_ms"] == 700.0 and inside["inside_ms"] == 100.0
          and inside["material"], inside)


def test_the_part_inside_stops_at_the_window_end(tmp_path: Path, monkeypatch) -> None:
    """In D from 200 to 700 ms; the window is 300 to 330 ms."""
    config = _scene(monkeypatch, [(0, fixture.TID_MAIN, 100, 200, D),
                                  (0, fixture.TID_MAIN, 700, 800, S)],
                    [], 300, 330)
    rep = _analysed(tmp_path, config)
    inside = rep["window"]["opened_inside"]
    check("30 ms inside a 30 ms window, against 100 before: material",
          rep["window"]["duration_ms"] == 30.0 and inside is not None
          and inside["inside_ms"] == 30.0 and inside["material"], (rep["window"], inside))
