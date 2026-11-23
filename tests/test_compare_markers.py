"""The report's markers table in `compare` (#618).

An async section the app writes has no thread to answer for, so no detector
reads it: the markers table is the only place it is measured. A load that ran
twice per run instead of once, or an async section 300 ms longer, reached no
row of the comparison at all. Here the table's rows are paired by name, judged
by self time like any row, and by their calls per run on their own.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report, row  # noqa: E402

LOAD = "loadCollection"


def _marker(name: str, self_ms: list[float], count: list[int]) -> dict:
    """A merged markers row, as `report._merge_markers` writes one."""
    n = len(self_ms)
    out = row(name, sorted(self_ms)[n // 2], values=self_ms,
              count=sorted(count)[n // 2])
    out["runs"] = f"{n}/{n}"
    out["total_ms"] = out["self_ms"]
    out["spread"]["count"] = {"min": min(count), "max": max(count), "values": count}
    return out


def _side(*markers: dict, runs: int = 5) -> dict:
    rep = report([det("d", [row("A", 100.0)])], runs=runs)
    rep["markers"] = {"prefix": "AGENTTMP_", "globs": ["AGENTTMP_*", LOAD],
                      "rows": list(markers), "absent": []}
    return rep


def _load(cmp: dict) -> dict:
    return next(r for r in cmp["markers"] if r["location"] == LOAD)


def test_a_load_that_ran_twice_per_run_is_listed_with_its_calls() -> None:
    once = _side(_marker(LOAD, [2070.0, 2080.0, 2079.4, 2090.0, 2060.0], [1, 1, 1, 1, 1]))
    twice = _side(_marker(LOAD, [4140.0, 4160.0, 4158.8, 4180.0, 4120.0], [2, 2, 2, 2, 2]))
    cmp = compare(once, twice)
    r = _load(cmp)
    check("a row of its own, judged by self time", r["detector"] == "markers"
          and r["change"] == "grew" and r["holds"] is True, r)
    check("and by its calls: 1 → 2, and that holds",
          r["calls"]["before"] == 1 and r["calls"]["after"] == 2
          and r["calls"]["moved"] and r["calls"]["holds"] is True, r["calls"])
    check("counted in the summary", cmp["summary"]["markers_moved"] == 1, cmp["summary"])
    text = compare_mod.to_markdown(cmp)
    check("its own section in comparison.md, with the calls",
          "## Markers" in text and f"| {LOAD} |" in text and "1 → 2, yes" in text, text)
    back = compare(twice, once)
    check("the fix the other way round: 2 → 1, shrank",
          _load(back)["change"] == "shrank" and _load(back)["calls"]["after"] == 1)


def test_an_async_section_300_ms_longer_is_a_row_that_holds() -> None:
    before = _side(_marker(LOAD, [1950.0, 1960.0, 1959.1, 1970.0, 1940.0], [1] * 5))
    after = _side(_marker(LOAD, [2250.0, 2260.0, 2259.1, 2270.0, 2240.0], [1] * 5))
    r = _load(compare(before, after))
    check("grew, holds, and the calls did not move",
          r["change"] == "grew" and r["holds"] is True and r["delta_ms"] == 300.0
          and not r["calls"]["moved"], r)


def test_a_steady_marker_is_not_listed() -> None:
    same = _side(_marker(LOAD, [1950.0, 1960.0, 1959.1, 1970.0, 1940.0], [1] * 5))
    cmp = compare(same, copy.deepcopy(same))
    check("steady, out of the section", _load(cmp)["change"] == "steady"
          and cmp["summary"]["markers_moved"] == 0
          and "## Markers" not in compare_mod.to_markdown(cmp))


def test_a_report_without_the_table_is_unknown_rather_than_empty() -> None:
    old = report([det("d", [row("A", 100.0)])], runs=5)
    new = _side(_marker(LOAD, [100.0] * 5, [1] * 5))
    check("nothing paired against a side that never had the table",
          compare(old, new)["markers"] == [])


def test_the_merge_keeps_each_runs_calls() -> None:
    def one(count: int) -> dict:
        return {"window": {"duration_ms": 1000.0}, "detectors": [], "trace": "t",
                "summary": {}, "markers": {"prefix": "AGENTTMP_", "globs": [LOAD],
                                           "absent": [], "rows": [{
                                               "location": LOAD, "count": count,
                                               "self_ms": 100.0 * count,
                                               "total_ms": 100.0 * count,
                                               "max_ms": 100.0, "detail": "async"}]}}
    merged = report_mod._merge_markers([one(1), one(2), one(1)], 3)
    spread = merged["rows"][0]["spread"]
    check("the calls per run, in report order", spread["count"]["values"] == [1, 2, 1]
          and spread["self_ms"]["values"] == [100.0, 200.0, 100.0], spread)


def test_a_temporary_marker_is_named_once() -> None:
    """Found by two detectors and by the markers table, it is one marker (#584)."""
    before = _side()
    after = _side(_marker("AGENTTMP_fill", [90.0] * 5, [1] * 5))
    for d_id in ("main_thread_block", "main_thread_outlier"):
        after["detectors"].append(det(d_id, [row("AGENTTMP_fill", 90.0)]))
        before["detectors"].append(det(d_id, []))
    cmp = compare(before, after, temp_prefix="AGENTTMP_")
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "instrumentation")
    check("one marker, named once", text.startswith("1 marker(s)")
          and text.count("AGENTTMP_fill") == 1, text)
