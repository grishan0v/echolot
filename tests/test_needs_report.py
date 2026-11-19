#!/usr/bin/env python3
"""What a set of repeats and a report say about a detector that could not look (#441).

Built by hand, like test_report.py: `aggregate` and `to_markdown` are
arithmetic and text over the report structure and need no trace_processor. A
set is checked only when every repeat was; report.md names what could not run
beside the detectors a config turned off, and stops calling it silent; and a
report written before the keys existed is unknown, which is never "checked".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import needs as needs_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402


# --- a set of repeats ------------------------------------------------------------------

def _entry(checked: bool | None, reason: str | None = None, **more) -> dict:
    out: dict = {"id": "io_wait", "title": "io_wait", "why": "", "params": {},
                 "params_source": "default", "error": None, "rows": [], **more}
    if checked is not None:
        out["checked"] = checked
    if reason:
        out["reason"] = reason
    return out


def _report(*detectors: dict) -> dict:
    return {"schema": 1, "generated_at": "2026-11-19T10:00:00+00:00",
            "trace": "t.perfetto-trace", "toolchain": {},
            "window": {"process": "com.example.app", "duration_ms": 1000.0},
            "environment": {},
            "summary": {"detectors_run": len(detectors), "detectors_fired": 0,
                        "fired_ids": [], "absent_ids": []},
            "detectors": list(detectors)}


def test_a_set_is_checked_only_when_every_repeat_was() -> None:
    got = needs_mod.merged([_entry(True)] * 3)
    check("all three looked", got == {"checked": True}, got)

    got = needs_mod.merged([_entry(True), _entry(False, "No state."), _entry(True)])
    check("one that did not look is enough", got["checked"] is False, got)
    check("and the set says how many",
          got["reason"] == "No state (1 of the 3 recordings).", got)


def test_a_set_where_none_looked_does_not_count_them() -> None:
    got = needs_mod.merged([_entry(False, "No state.")] * 3)
    check("the reason is the repeats' own", got == {"checked": False, "reason": "No state."}, got)


def test_different_reasons_across_repeats_are_all_said() -> None:
    got = needs_mod.merged([_entry(False, "No state."), _entry(False, "No frames.")])
    check("both", got["reason"] == "No state. No frames.", got)


def test_a_report_from_before_the_keys_is_unknown_and_stays_so() -> None:
    check("no repeat carries it", needs_mod.merged([_entry(None)] * 3) == {})
    check("one that does not is never enough to call the set checked",
          needs_mod.merged([_entry(True), _entry(None)]) == {})
    got = needs_mod.merged([_entry(False, "No state."), _entry(None)])
    check("but a repeat that did not look says so", got["checked"] is False, got)


def test_aggregate_carries_the_merge() -> None:
    merged = report_mod.aggregate([_report(_entry(True)),
                                   _report(_entry(False, "No state.")),
                                   _report(_entry(True))])
    d = merged["detectors"][0]
    check("the merged entry is not the first repeat's",
          d["checked"] is False and "1 of the 3" in d["reason"], d)

    clean = report_mod.aggregate([_report(_entry(True)), _report(_entry(True))])
    check("a set that all looked", clean["detectors"][0] == {**clean["detectors"][0], "checked": True}
          and "reason" not in clean["detectors"][0], clean["detectors"][0])

    old = report_mod.aggregate([_report(_entry(None)), _report(_entry(None))])
    check("a set of old reports gains nothing",
          "checked" not in old["detectors"][0] and "reason" not in old["detectors"][0],
          old["detectors"][0])


def test_a_single_report_passes_through_as_it_is() -> None:
    one = _report(_entry(False, "No state."))
    d = report_mod.aggregate([one])["detectors"][0]
    check("unchanged", d["checked"] is False and d["reason"] == "No state.", d)


def test_a_detector_that_failed_in_some_repeats_is_not_checked_either() -> None:
    failed = _entry(False, needs_mod.FAILED, error="boom")
    merged = report_mod.aggregate([_report(_entry(True)), _report(failed)])
    d = merged["detectors"][0]
    check("the set did not all look", d["checked"] is False and d["error"] == "boom", d)
    check("and its failure is still the failure's", d["failed_runs"] == "1/2", d)


# --- what the report says -----------------------------------------------------------------

def _two() -> dict:
    return _report(
        _entry(False, "The recording does not carry the phone's own state.",
               id="io_wait", title="io_wait"),
        _entry(False, "The recording does not carry the phone's own state.",
               id="main_thread_kernel_waits", title="kernel waits"),
        _entry(False, "The recording carries no frame timeline.",
               id="frame_jank", title="frames"),
        _entry(True, id="gc_pressure", title="gc"))


def test_the_line_groups_detectors_by_what_they_lack() -> None:
    line = needs_mod.line(_two()["detectors"])
    check("one line", line is not None and "\n" not in line, line)
    check("it counts them", "3 detector(s) could not run" in line, line)
    check("it names the two that lack the same thing together",
          "`io_wait`, `main_thread_kernel_waits` — The recording does not carry the "
          "phone's own state." in line, line)
    check("and the third on its own", "`frame_jank` — The recording carries no frame timeline."
          in line, line)
    check("a detector that looked is not named", "gc_pressure" not in line, line)
    check("nothing to say when every one looked",
          needs_mod.line([_entry(True)]) is None)


def test_a_detector_that_failed_is_left_to_the_failed_line() -> None:
    failed = _entry(False, needs_mod.FAILED, error="boom")
    check("it is the error's to say", needs_mod.line([failed]) is None)
    check("and it did not run", not needs_mod.could_not_run(failed))


def test_report_md_says_it_next_to_the_detectors_turned_off() -> None:
    rep = _two()
    rep["summary"]["absent_ids"] = ["repeated_work"]
    md = report_mod.to_markdown(rep)
    lines = md.splitlines()
    off = next(i for i, line in enumerate(lines) if "turned off in this config" in line)
    check("the new line follows the old one", "could not run" in lines[off + 1], lines[off:off + 3])
    check("and only what looked is silent", "**Silent:** gc_pressure" in md.splitlines(), md)


def test_report_md_does_not_call_an_unchecked_detector_silent() -> None:
    rep = _report(_entry(False, "No state.", id="io_wait"),
                  _entry(True, id="anr_risk"),
                  _entry(None, id="repeated_work"))
    md = report_mod.to_markdown(rep)
    silent = next(line for line in md.splitlines() if line.startswith("**Silent:**"))
    check("the one that looked, and the one from an older report, are",
          silent == "**Silent:** anr_risk, repeated_work", silent)


def test_a_report_with_nothing_unchecked_has_no_such_line() -> None:
    md = report_mod.to_markdown(_report(_entry(True), _entry(None, id="b")))
    check("no line", "could not run" not in md, md)


def test_the_views_say_not_checked_where_they_said_silent() -> None:
    rep = _two()
    overview = report_mod.overview(rep)
    check("the table row", "not checked" in overview and "silent" not in overview.split("\n\n")[1],
          overview)
    check("and the line under it", "could not run" in overview, overview)
    view = report_mod.detector_view(rep, "io_wait")
    check("the detector's own view gives the reason",
          "not checked: The recording does not carry the phone's own state." in view, view)
    check("and does not call it silent", "_silent" not in view, view)
    quiet = report_mod.detector_view(rep, "gc_pressure")
    check("a detector that looked and found nothing is still silent", "_silent" in quiet, quiet)
    summary = report_mod.select(rep, [], None, False, False)["detectors"]
    check("the json summary carries the flag",
          [d["checked"] for d in summary] == [False, False, False, True], summary)
