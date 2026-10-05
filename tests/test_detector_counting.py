"""Rows that counted the same time twice, or one thing as several.

Each case is the fixture with one thing changed, the way `test_app_init`
builds its own: a marker nested in one of its own name, three pool threads
under one cut name, a main thread with one long message full of sections,
and an `io_wait` row whose kernel functions change between repeats. The late
frame is in the fixture itself, where the self-check holds it.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import compare, fixture, report, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import DETECTOR_DIR, analyze_trace  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402

# The fixture's own tables, before any test here patches them.
SLICES = fixture.SLICES
THREADS = fixture.THREADS
SCHED = fixture.SCHED


def _analysed(tmp_path: Path, overrides: dict | None = None) -> dict:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    return analyze_trace(trace, Config(selftest.FIXTURE_CONFIG),
                         cli_overrides=overrides)


def _rows(rep: dict, detector: str) -> list[dict]:
    det = next(d for d in rep["detectors"] if d["id"] == detector)
    check(f"{detector} ran without an error", det["error"] is None, det["error"])
    return det["rows"]


def _nested_parse(inner_ms: float) -> dict:
    """`load_screen` holding `AGENTTMP_parse`, which holds another one."""
    slices = copy.deepcopy(SLICES)
    slices[fixture.TID_OKHTTP] = [
        ("load_screen", 200, 100, [
            ("AGENTTMP_parse", 210, 80, [("AGENTTMP_parse", 215, inner_ms, [])]),
        ]),
    ]
    return slices


def test_a_marker_inside_one_of_its_name_is_not_a_second_caller(
        tmp_path: Path, monkeypatch) -> None:
    """A marker in a recursive function: one entry, from `load_screen`."""
    for inner in (70, 20):
        monkeypatch.setattr(fixture, "SLICES", _nested_parse(inner))
        rep = _analysed(tmp_path)
        rows = [r for r in _rows(rep, "repeated_work")
                if r["location"] == "AGENTTMP_parse"]
        check(f"no row, work done twice or a near miss, with a {inner} ms inner one",
              rows == [], rows)
        marker = {r["location"]: r for r in rep["markers"]["rows"]}["AGENTTMP_parse"]
        check(f"the markers table counts the outer {inner} ms once in the total",
              marker["total_ms"] == 80.0 and marker["self_ms"] == 80.0, marker)


def test_pool_threads_under_one_cut_name_are_one_row(
        tmp_path: Path, monkeypatch) -> None:
    """Three `DefaultDispatch` threads: 300, 120 and 60 ms on a CPU."""
    threads = dict(THREADS)
    threads[fixture.TID_WORKER] = "DefaultDispatch"
    threads[4290] = "DefaultDispatch"
    threads[4291] = "DefaultDispatch"
    monkeypatch.setattr(fixture, "THREADS", threads)
    monkeypatch.setattr(fixture, "SCHED", [
        *SCHED,
        (8, 4290, 300, 420, fixture.S),
        (8, 4291, 500, 560, fixture.S),
    ])
    rows = [r for r in _rows(_analysed(tmp_path), "uninstrumented_cpu")
            if r["location"] == "DefaultDispatch"]
    check("one row for the pool", len(rows) == 1, rows)
    check("with the time of all three", rows[0]["total_ms"] == 480.0, rows)


def test_anr_risk_counts_slices_and_calls_them_that(
        tmp_path: Path, monkeypatch) -> None:
    """One long message with thirty sections in it is not thirty-one messages."""
    slices = copy.deepcopy(SLICES)
    slices[fixture.TID_MAIN] = [
        ("AppStart", 100, 1005, [
            ("long_message", 260, 300, [
                (f"section_{i}", 261 + i * 9, 8, []) for i in range(30)
            ]),
        ]),
        ("Screen.firstFrame", 1100, 5, []),
    ]
    monkeypatch.setattr(fixture, "SLICES", slices)
    rows = _rows(_analysed(tmp_path, {"anr_risk": {"min_stall_ms": 100}}),
                 "anr_risk")
    details = [r["detail"] for r in rows]
    check("the number is said to be slices", details and all(
        d.endswith(" slices") and "messages" not in d for d in details), details)


def test_io_wait_names_a_row_without_its_kernel_functions() -> None:
    """The functions a thread stopped in change between repeats; the row does not."""
    rows = [
        [{"location": "main", "thread": "main", "detail": detail,
          "count": 1, "total_ms": 30.0, "max_ms": 30.0}]
        for detail in ("main · f", "main · f,g", "main · g,f")
    ]
    det = next(d for d in load_detectors(DETECTOR_DIR) if d.id == "io_wait")
    identity = report.identity_of({"identity": list(det.identity)})
    check("io_wait is named by its thread and the thread's kind",
          identity == ("location", "thread"), identity)
    merged = report.merge_rows(rows, identity, total=3)
    check("three repeats of one thread merge into one row",
          len(merged) == 1 and merged[0]["runs"] == "3/3", merged)
    pairs = compare._match(rows[0], rows[1], identity)
    check("and compare pairs the row across them",
          len(pairs) == 1 and pairs[0][0] and pairs[0][1], pairs)
