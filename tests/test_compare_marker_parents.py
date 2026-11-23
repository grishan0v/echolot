"""A slice's self time going into a temporary marker placed under it (#584).

A marker directly under a slice is its child, and self time is a slice less
its direct children, so the round that adds the marker reads as the slice
getting cheaper by exactly the marker's time. `analyze` records for each
temporary marker the slices it sat in; `compare` judges those slices on their
total and says where the self time went; a set whose repeats carry the markers
in some runs only is said to be two rounds.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report  # noqa: E402

PREFIX = "AGENTTMP_"
SLICE = "Compose:recompose"
MARK = "AGENTTMP_recompose_items"


def _row(location: str, self_ms: list[float], total_ms: list[float]) -> dict:
    n = len(self_ms)
    return {"location": location, "runs": f"{n}/{n}", "count": 1,
            "self_ms": sorted(self_ms)[n // 2], "total_ms": sorted(total_ms)[n // 2],
            "spread": {m: {"min": min(v), "max": max(v), "values": v}
                       for m, v in (("self_ms", self_ms), ("total_ms", total_ms))}}


def _plain() -> dict:
    """A 100 ms main-thread slice, no markers."""
    rep = report([det("main_thread_block", [
        _row(SLICE, [100.0, 101.0, 99.0, 100.5, 99.5], [100.0, 101.0, 99.0, 100.5, 99.5])])],
        runs=5)
    rep["markers"] = {"prefix": PREFIX, "globs": [PREFIX + "*"], "rows": [], "absent": []}
    return rep


def _marked() -> dict:
    """The same slice with a temporary marker directly under it taking 90 ms."""
    marker = _row(MARK, [90.0, 90.5, 89.5, 90.2, 89.8], [90.0, 90.5, 89.5, 90.2, 89.8])
    rep = report([det("main_thread_block", [
        _row(SLICE, [10.0, 10.5, 9.5, 10.2, 9.8], [100.0, 101.0, 99.0, 100.5, 99.5]),
        dict(marker)])], runs=5)
    rep["markers"] = {"prefix": PREFIX, "globs": [PREFIX + "*"], "absent": [],
                      "rows": [{**marker, "parents": {SLICE: 90.0}}]}
    return rep


def _slice(cmp: dict) -> dict:
    return next(r for r in cmp["rows"] if r["location"] == SLICE)


def test_analyze_names_the_slice_a_temporary_marker_sits_in(marker_report) -> None:
    rows = {r["location"]: r for r in marker_report["markers"]["rows"]}
    parents = rows["AGENTTMP_insert_preset"].get("parents") or {}
    check("each slice it sat directly in, with its time there",
          parents.get("AGENTTMP_seed_first") == 28.0
          and parents.get("AGENTTMP_seed_again") == 24.0, parents)
    check("and a marker at the top of its thread names none",
          "parents" not in rows["AGENTTMP_seed_first"]
          or "AGENTTMP_seed_first" not in rows["AGENTTMP_seed_first"]["parents"], rows)


def test_a_marker_added_under_a_slice_does_not_make_it_cheaper() -> None:
    cmp = compare(_plain(), _marked(), temp_prefix=PREFIX)
    r = _slice(cmp)
    check("judged on its total, which did not move",
          r["metric"] == "total_ms" and r["change"] == "steady" and not r["holds"], r)
    check("and says where its self time went",
          r["into_markers"] == {"ms": 90.0, "markers": [MARK], "side": "after"}, r)
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "instrumentation")
    check("the instrumentation warning names it next to the marker",
          f"`{MARK}`" in text and f"`{SLICE}` (90 ms) went into them" in text, text)


def test_markers_taken_out_are_not_work_that_went_away() -> None:
    cmp = compare(_marked(), _plain(), temp_prefix=PREFIX)
    r = _slice(cmp)
    check("the slice is not a regression", r["metric"] == "total_ms"
          and r["change"] == "steady" and r["into_markers"]["side"] == "before", r)
    text = next((w["text"] for w in cmp["warnings"]
                 if w["id"] == "instrumentation-removed"), "")
    check("the gone marker gets a warning, and the slice is named",
          f"`{MARK}`" in text and f"`{SLICE}` (90 ms) came back" in text, cmp["warnings"])


def test_without_markers_the_slice_is_judged_as_before() -> None:
    plain = _plain()
    cheaper = _plain()
    cheaper["detectors"][0]["rows"] = [
        _row(SLICE, [10.0, 10.5, 9.5, 10.2, 9.8], [10.0, 10.5, 9.5, 10.2, 9.8])]
    r = _slice(compare(plain, cheaper, temp_prefix=PREFIX))
    check("by self time, shrank, and it holds",
          r["metric"] == "self_ms" and r["change"] == "shrank" and r["holds"]
          and "into_markers" not in r, r)


def test_the_table_says_which_rows_are_read_by_total() -> None:
    plain = _plain()
    cheaper = _marked()
    # A real change under the marker too: the total grew by 50 ms.
    cheaper["detectors"][0]["rows"][0] = _row(
        SLICE, [10.0, 10.5, 9.5, 10.2, 9.8], [150.0, 151.0, 149.0, 150.5, 149.5])
    text = compare_mod.to_markdown(compare(plain, cheaper, temp_prefix=PREFIX))
    check("the row is marked as read by its total",
          f"{SLICE} *(by total)*" in text and "+50.0" in text, text)


def test_a_set_with_the_marker_in_some_repeats_is_two_rounds() -> None:
    def one(with_marker: bool) -> dict:
        rows = [{"location": MARK, "count": 1, "self_ms": 90.0, "total_ms": 90.0,
                 "max_ms": 90.0, "detail": "main", "parents": {SLICE: 90.0}}] \
            if with_marker else []
        return {"markers": {"prefix": PREFIX, "globs": [PREFIX + "*"], "absent": [],
                            "rows": rows}}
    merged = report_mod._merge_markers([one(True), one(False)], 2)
    check("the merged table says so, with the slice it sits in",
          merged.get("mixed") == [{"location": MARK, "runs": "1/2", "parents": [SLICE]}]
          and merged["rows"][0]["parents"] == {SLICE: 90.0}, merged)
    lines = "\n".join(report_mod._markers_lines(merged))
    check("and report.md warns, naming the marker and the slice",
          "in some repeats and not others" in lines and f"`{MARK}` (1/2)" in lines
          and f"`{SLICE}`" in lines and "two rounds" in lines, lines)
    whole = report_mod._merge_markers([one(True), one(True)], 2)
    check("a marker in every repeat is no warning", "mixed" not in whole, whole)
