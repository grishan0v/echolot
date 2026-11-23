#!/usr/bin/env python3
"""`compare` and a detector that could not run on one side or both (#441).

A detector the trace did not give what it reads says nothing on that side, so
the other side's rows are not gone and its own are not new, the way
`compare` already treats a detector that failed. The comparison says which
detectors it left out and why, from each side that could not look. A report
written before `checked` existed is unknown and is compared as it always was.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_compare import changes, compare, det, report, row, warned  # noqa: E402

from echolot import compare as compare_mod  # noqa: E402

NO_STATE = "The recording does not carry the phone's own state, so there was nothing to read."


def looked(det_id: str, rows: list[dict]) -> dict:
    return {**det(det_id, rows), "checked": True}


def blind(det_id: str, why: str = NO_STATE, rows: list[dict] | None = None) -> dict:
    return {**det(det_id, rows or []), "checked": False, "reason": why}


def disk(value: float = 60.0) -> list[dict]:
    return [row("arch_disk_io_0", value, metric="total_ms")]


def test_a_row_the_other_side_could_not_look_for_is_not_gone() -> None:
    before = report([looked("io_wait", disk()), looked("gc_pressure", [])])
    after = report([blind("io_wait"), looked("gc_pressure", [])])
    cmp = compare(before, after)
    check("no row is called gone", cmp["rows"] == [] and cmp["summary"]["vanished"] == 0,
          cmp["rows"])
    check("and the comparison says so", "not-checked-after" in warned(cmp), cmp["warnings"])
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "not-checked-after")
    check("naming the detector and the reason",
          "`io_wait`" in text and NO_STATE in text and "after side" in text, text)
    check("the structured list says which side could not look, and why",
          cmp["summary"]["not_compared"] == [
              {"id": "io_wait", "before": None, "after": NO_STATE}],
          cmp["summary"]["not_compared"])


def test_nor_is_a_row_new_when_the_before_side_could_not_look() -> None:
    before = report([blind("io_wait")])
    after = report([looked("io_wait", disk())])
    cmp = compare(before, after)
    check("no row is called new", cmp["rows"] == [] and cmp["summary"]["appeared"] == 0,
          cmp["rows"])
    check("the before side is named", "not-checked-before" in warned(cmp)
          and "not-checked-after" not in warned(cmp), warned(cmp))
    check("and is the one with a reason",
          cmp["summary"]["not_compared"][0]["before"] == NO_STATE
          and cmp["summary"]["not_compared"][0]["after"] is None,
          cmp["summary"]["not_compared"])


def test_a_detector_neither_side_could_run_is_not_compared_and_not_silent() -> None:
    before, after = report([blind("io_wait")]), report([blind("io_wait")])
    cmp = compare(before, after)
    check("it is out of the table", cmp["rows"] == [], cmp["rows"])
    check("both sides gave a reason",
          cmp["summary"]["not_compared"] == [
              {"id": "io_wait", "before": NO_STATE, "after": NO_STATE}],
          cmp["summary"]["not_compared"])
    check("and silence in both is not what it was", cmp["summary"]["silent_both"] == [],
          cmp["summary"])
    check("one warning that names both sides, not one for each side (#585)",
          "not-checked-both" in warned(cmp)
          and not {"not-checked-before", "not-checked-after"} & warned(cmp), warned(cmp))
    md = compare_mod.to_markdown(cmp)
    check("said once", md.count("could not run on either side") == 1
          and "could not run on the before side" not in md, md)


def test_different_reasons_on_the_two_sides_are_two_warnings() -> None:
    cmp = compare(report([blind("io_wait")]), report([blind("io_wait"), blind("gc_pressure")]))
    check("one for each side", {"not-checked-before", "not-checked-after"} <= warned(cmp), warned(cmp))


def test_the_state_table_says_not_checked() -> None:
    cmp = compare(report([looked("io_wait", disk())]), report([blind("io_wait")]))
    check("a detector that went from rows to not looking",
          cmp["summary"]["state_changed"] == [
              {"id": "io_wait", "before": "1 row(s)", "after": "not checked"}],
          cmp["summary"]["state_changed"])
    md = compare_mod.to_markdown(cmp)
    check("it is printed with the warning above the table",
          "could not run on the after side" in md and "not checked" in md, md)


def test_a_detector_both_sides_looked_at_is_compared_as_ever() -> None:
    before = report([looked("io_wait", disk(60.0)), looked("gc_pressure", [])])
    after = report([looked("io_wait", disk(200.0)), looked("gc_pressure", [])])
    cmp = compare(before, after)
    check("its row moved", changes(cmp) == {"arch_disk_io_0": "grew"}, cmp["rows"])
    check("nothing is left out", cmp["summary"]["not_compared"] == []
          and not any(w["id"].startswith("not-checked") for w in cmp["warnings"]),
          cmp["warnings"])
    check("and a quiet pair is silent in both", cmp["summary"]["silent_both"] == ["gc_pressure"],
          cmp["summary"])


def test_a_report_from_before_the_keys_is_compared_as_it_was() -> None:
    before = report([det("io_wait", disk())])
    after = report([det("io_wait", [])])
    cmp = compare(before, after)
    check("its row is gone, as it always was", changes(cmp) == {"arch_disk_io_0": "vanished"},
          cmp["rows"])
    check("and nothing is said about detectors that could not run",
          cmp["summary"]["not_compared"] == [], cmp["summary"])
    mixed = compare(report([looked("io_wait", disk())]), report([det("io_wait", [])]))
    check("one side unknown is not one side that could not look",
          changes(mixed) == {"arch_disk_io_0": "vanished"}, mixed["rows"])


def test_a_detector_that_failed_keeps_the_failed_path() -> None:
    failed = {**det("io_wait", []), "error": "boom", "checked": False,
              "reason": "The check failed on this recording, so it says nothing about it; "
                        "the error is in the report."}
    cmp = compare(report([looked("io_wait", disk())]), report([failed]))
    check("its own warning", "detector-failed" in warned(cmp), warned(cmp))
    check("and not the other one", not any(w["id"].startswith("not-checked")
                                           for w in cmp["warnings"])
          and cmp["summary"]["not_compared"] == [], cmp["warnings"])
    check("its row is not called gone either", cmp["rows"] == [], cmp["rows"])


def test_a_set_that_failed_in_some_repeats_is_compared_on_the_rest() -> None:
    """Rows from the repeats that ran, as before: failing is not the trace lacking something."""
    partly = {**det("io_wait", disk(200.0)), "error": "boom", "failed_runs": "1/3",
              "checked": False,
              "reason": "The check failed on this recording, so it says nothing about it; "
                        "the error is in the report. (1 of the 3 recordings)."}
    cmp = compare(report([looked("io_wait", disk(60.0))]), report([partly]))
    check("its rows are compared", changes(cmp) == {"arch_disk_io_0": "grew"}, cmp["rows"])
    check("and it is not left out", cmp["summary"]["not_compared"] == [], cmp["summary"])


def test_a_set_where_some_repeats_lacked_it_is_left_out() -> None:
    some = blind("io_wait", NO_STATE.rstrip(".") + " (2 of the 5 recordings).", rows=disk(120.0))
    cmp = compare(report([looked("io_wait", disk(60.0))]), report([some]))
    check("neither side's rows are compared", cmp["rows"] == [], cmp["rows"])
    check("the partial reason is the one said",
          "(2 of the 5 recordings)" in cmp["summary"]["not_compared"][0]["after"],
          cmp["summary"]["not_compared"])


def test_a_detector_only_one_report_has_is_not_a_detector_that_could_not_run() -> None:
    cmp = compare(report([looked("io_wait", disk())]), report([]))
    check("it is as it was: a detector that ran once",
          "detectors" in warned(cmp) and cmp["summary"]["not_compared"] == [], cmp["warnings"])
