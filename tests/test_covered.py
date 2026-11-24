#!/usr/bin/env python3
"""A thread markers mostly cover still has its unnamed CPU measured (#844).

`uninstrumented_cpu` shows a thread only where its slices cover under
`max_covered_pct` of its CPU. Past that it used to be no row at all, so a
round that added markers to a pool read as every millisecond of it saved.
The row is measured whatever the bar now, and `compare` sets the round
before against what is left.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402


def _det(rep: dict) -> dict:
    return next(d for d in rep["detectors"] if d["id"] == "uninstrumented_cpu")


def test_a_mostly_covered_thread_is_compared_with_what_it_has_left(tmp_path: Path) -> None:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    # HeapTaskDaemon: 100 ms on a CPU, 80 of it inside slices.
    opened = Config({**selftest.FIXTURE_CONFIG, "detectors": {"uninstrumented_cpu": {
        "max_covered_pct": 100, "min_running_ms": 10}}})
    before = report_mod.aggregate([analyze_trace(trace, opened)])
    after = report_mod.aggregate([analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))])
    shown = {r["location"]: r for r in _det(before)["rows"]}
    check("shown where the bar lets it through", shown.get("HeapTaskDaemon", {}).get("total_ms") == 20.0,
          shown)
    under = {r["location"]: r for r in _det(after)["below"]["rows"]}
    check("and measured where it does not", under.get("HeapTaskDaemon", {}).get("total_ms") == 20.0,
          _det(after)["below"])
    cmp = compare_mod.build(before, after, before_path="b.json", after_path="a.json")
    heap = [r for r in cmp["rows"] if r["location"] == "HeapTaskDaemon"]
    check("compared with what it measured, not gone", len(heap) == 1
          and heap[0]["change"] != compare_mod.VANISHED and heap[0]["delta_ms"] == 0.0, heap)
