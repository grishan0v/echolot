#!/usr/bin/env python3
"""`uninstrumented_cpu` on a main thread marked up but for one stretch (#607).

The doctor check pins the row on the scene `fixture.blind_main` draws: a cold
start whose main thread names 79% of its CPU, and a busy loop with no section
around it. Here are the readers of that row. `compare` judges the main
thread's time outside slices, so a loop that grew is a row that grew; on a
real project, judged on all of the thread's CPU, a 200 ms loop moved by less
than the floor. And the plain reading weighs it as the main thread's time, by
the part nobody named rather than by everything the thread did.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, plain  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.test_compare import compare  # noqa: E402

CONFIG = {
    "project": {"package": fixture.APP_NAME, "process": fixture.APP_NAME},
    "scenario": {"name": "blind", "start": {"name": "bindApplication"},
                 "end": {"name": "Screen.firstFrame"}},
}
MAIN = fixture.BLIND_THREADS[fixture.TID_MAIN]


def _set(tmp_path: Path, loops: tuple[float, ...]) -> dict:
    """Three starts, each with its loop, merged the way `analyze` merges them."""
    reports = []
    for loop in loops:
        path = tmp_path / f"blind-{loop}.perfetto-trace"
        path.write_bytes(fixture.blind_main(loop))
        reports.append(analyze_trace(path, Config(CONFIG)))
    return report_mod.aggregate(reports)


def _detector(report: dict) -> dict:
    return next(d for d in report["detectors"] if d["id"] == "uninstrumented_cpu")


def test_compare_lists_the_main_threads_time_outside_slices_as_grown(tmp_path: Path) -> None:
    before = _set(tmp_path, (95, 100, 105))
    after = _set(tmp_path, (290, 300, 310))
    rows = [r for r in compare(before, after)["rows"]
            if r["detector"] == "uninstrumented_cpu"]
    main = next(r for r in rows if r["location"] == MAIN)
    check("the main thread's 125 ms outside slices grew to 325",
          (main["before"]["total_ms"], main["after"]["total_ms"]) == (125.0, 325.0),
          main)
    check("and the row says it grew, by the loop's 200 ms",
          (main["change"], main["delta_ms"], main["ratio"]) == ("grew", 200.0, 2.6), main)
    check("the instrumented part is carried beside it, and did not move",
          main["before"]["covered_ms"] == main["after"]["covered_ms"] == 1195.0, main)
    check("the pool with no slices stayed where it was",
          [r["change"] for r in rows if r["location"] == "DefaultDispatch"] == ["steady"],
          rows)


def test_the_plain_reading_weighs_the_main_thread_by_the_part_nobody_named(
        tmp_path: Path) -> None:
    planted = _set(tmp_path, (290, 300, 310))
    det = _detector(planted)
    check("the main thread's row is there in all three starts",
          [(r["location"], r["runs"]) for r in det["rows"]]
          == [("DefaultDispatch", "3/3"), (MAIN, "3/3")], det["rows"])
    # 325 ms of a 1520 ms start, past the floor and the share: the main
    # thread's own time, which is the window's. All 1520 ms of its CPU would
    # have made every marked-up main thread a red line.
    check("it needs attention, as the main thread's time",
          plain.attention(det, planted) == plain.NEEDS_ATTENTION, det["rows"])
    text = plain.render(planted, width=200)
    check("the sentence gives the time outside slices, not the CPU",
          "2 threads or pools of threads spent 0.7 s of processor time between them "
          "on work that nothing in the recording names." in text, text)

    healthy = _set(tmp_path, (0, 0, 0))
    check("without the loop the main thread has no row, and the pool alone is worth a look",
          [r["location"] for r in _detector(healthy)["rows"]] == ["DefaultDispatch"]
          and plain.attention(_detector(healthy), healthy) == plain.WORTH_A_LOOK,
          _detector(healthy)["rows"])
