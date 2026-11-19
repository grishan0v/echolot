"""A hunt closed with a structured verdict: `hunt --done --verdict <file>`.

What an agent returns at the end of a hunt has a shape — causes, places, the
measurements that show them, a fix — and as one string it can be read and
nothing else. The verdict is that shape as JSON, and the check that makes it
more than a string is that every row it cites is a row of a report this
investigation filed. These cases pin both halves: what is accepted and
recorded as it came, and what is refused with the entry and the reason named
and nothing written; and that the check gives the same answer every time,
because the same verdict against the same reports must.

    python -m pytest tests/test_hunt_verdict.py
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from echolot import hunt as hunt_mod
from echolot import verdict as verdict_mod
from echolot.main import main as cli
from tests.support import check

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: coldStart
runner:
  mode: launch
"""


def run(project: Path, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    """`echolot …` from inside the project: exit code, stdout, stderr."""
    out, err = io.StringIO(), io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if stdin is None:
                code = cli(list(argv))
            else:
                import sys
                was, sys.stdin = sys.stdin, io.StringIO(stdin)
                try:
                    code = cli(list(argv))
                finally:
                    sys.stdin = was
    finally:
        os.chdir(here)
    return code, out.getvalue(), err.getvalue()


def report(**by_detector: list[str]) -> dict[str, Any]:
    """A report shaped like the ones `analyze` writes, with the rows it is given.

    Keyword `markers_` is the markers table; every other keyword is a detector
    and its rows' locations.
    """
    markers = by_detector.pop("markers_", [])
    return {
        "schema": 1, "runs": 3, "window": {"duration_ms": 2100.0},
        "markers": {"prefix": "AGENTTMP_", "rows": [{"location": m} for m in markers],
                    "absent": [], "in_trace": markers},
        "detectors": [{"id": d, "rows": [{"location": loc, "total_ms": 10.0} for loc in locs],
                       "error": None} for d, locs in by_detector.items()],
    }


def hunt_with(tmp_path: Path, *reports: dict[str, Any]) -> Path:
    """A project with an investigation open and `reports` filed under it, 1, 2, ..."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, _, err = run(tmp_path, "hunt", "cold start 2.1 s", "--since", "unknown")
    assert code == 0, err
    out = tmp_path / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    for rep in reports:
        (out / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        assert hunt_mod.record_report(tmp_path, out) is not None
    return tmp_path


ROUND_1 = report(uninstrumented_cpu=["arch_disk_io_0", "pool-1-thread-1"],
                 main_thread_block=["Store.fill"])
ROUND_2 = report(uninstrumented_cpu=["arch_disk_io_0"],
                 main_thread_block=["Store.fill"],
                 markers_=["AGENTTMP_fill", "AGENTTMP_insert_cards"])

VERDICT: dict[str, Any] = {
    "summary": "Nearly all of the extra time is one call, Store.fill(): 1.5 s of 2.1 s.",
    "inside": {"report": 2, "location": "AGENTTMP_fill"},
    "causes": [{
        "title": "The store is filled in about 150 small transactions",
        "where": ["store/StoreFiller.kt:70-88"],
        "mechanism": "About 3,000 rows go in through about 150 transactions.",
        "evidence": [
            {"report": 1, "detector": "uninstrumented_cpu", "location": "arch_disk_io_0"},
            {"report": 2, "location": "AGENTTMP_insert_cards"},
        ],
        "fix": "One transaction for the whole fill.",
        "expected_ms": [300, 400],
        "confidence": "high",
        "confidence_why": "three runs in each of two rounds, within 10% of each other",
    }],
    "cleanup": "removed",
}


def write(project: Path, verdict: Any, name: str = "verdict.json") -> str:
    (project / name).write_text(verdict if isinstance(verdict, str)
                                else json.dumps(verdict), encoding="utf-8")
    return name


def problems(verdict: Any, *reports: dict[str, Any]) -> list[str]:
    return verdict_mod.validate(verdict, {i: r for i, r in enumerate(reports, 1)})


# --- accepted, and kept as it came -------------------------------------------

def test_a_sound_verdict_closes_the_hunt_and_is_kept_as_it_came(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, out, err = run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    check("accepted", code == 0, err)
    check("says what it recorded", "concluded:" in out and "1 cause(s)" in out
          and "3 row(s)" in out, out)
    h = hunt_mod.load(p)
    check("closed", h["status"] == "concluded" and h["closed_at"], h)
    check("the verdict is the agent's, whole and unchanged", h["verdict"] == VERDICT, h)
    check("the conclusion line, which every other reader reads, is its summary",
          h["conclusion"] == VERDICT["summary"], h["conclusion"])
    found = h["verdict_check"]
    check("what echolot looked at is beside it, not in it",
          found["reports"] == [1, 2] and found["cited"] == 3
          and "verdict_check" not in h["verdict"], found)


def test_one_cause_and_no_estimate_is_a_verdict(tmp_path: Path) -> None:
    """The issue's one open question: what is required. Only the place, the
    evidence and the title of each cause, and the summary."""
    p = hunt_with(tmp_path, ROUND_1)
    bare = {"summary": "It is the store.",
            "causes": [{"title": "The store", "where": ["Store.kt"],
                        "evidence": [{"report": 1, "location": "Store.fill"}]}]}
    code, _, err = run(p, "hunt", "--done", "--verdict", write(p, bare))
    check("accepted", code == 0, err)
    check("kept as it came", hunt_mod.load(p)["verdict"] == bare)


def test_a_hunt_that_found_nothing_says_so_with_no_causes(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    code, _, err = run(p, "hunt", "--done", "--verdict",
                       write(p, {"summary": "Every detector is silent.", "causes": []}))
    check("accepted", code == 0, err)
    code, out, _ = run(p, "hunt", "--show", "1")
    check("shown as nothing found", "causes: none" in out, out)


def test_the_verdict_can_come_on_stdin(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, _, err = run(p, "hunt", "--done", "--verdict", "-", stdin=json.dumps(VERDICT))
    check("accepted", code == 0, err)
    check("and recorded", hunt_mod.load(p)["verdict"] == VERDICT)


def test_text_beside_a_verdict_stays_the_conclusion_line(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, _, err = run(p, "hunt", "--done", "Store.fill, confidence high",
                       "--verdict", write(p, VERDICT))
    check("accepted", code == 0, err)
    h = hunt_mod.load(p)
    check("the words are the line --list shows", h["conclusion"] == "Store.fill, confidence high")
    check("the verdict is there too", h["verdict"] == VERDICT)


# --- refused, with the entry and the reason, and nothing written -------------

def refused(project: Path, verdict: Any) -> str:
    """Hand `verdict` in; it has to be refused whole, and the hunt left open."""
    before = hunt_mod.path(project).read_text(encoding="utf-8")
    code, out, err = run(project, "hunt", "--done", "--verdict", write(project, verdict))
    check("exit is non-zero", code == 2, (code, err))
    check("nothing was concluded", "concluded" not in out, out)
    check("nothing was recorded", hunt_mod.path(project).read_text(encoding="utf-8") == before)
    h = hunt_mod.load(project)
    check("still open, no verdict", h["status"] == "open" and "verdict" not in h, h)
    check("said so", "Nothing was recorded" in err and "still open" in err, err)
    return err


def test_a_citation_of_a_report_the_hunt_does_not_hold_is_refused(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["causes"][0]["evidence"][1]["report"] = 7
    err = refused(p, bad)
    check("the entry is named", "causes[0].evidence[1]" in err, err)
    check("and the reason, with the reports there are",
          "report 7 is not one of this investigation's reports (it has 1, 2)" in err, err)


def test_a_citation_of_a_row_no_report_holds_is_refused(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["causes"][0]["evidence"][0]["location"] = "arch_disk_io_9"
    err = refused(p, bad)
    check("the entry and the location", "causes[0].evidence[0]" in err
          and 'arch_disk_io_9' in err, err)
    check("with the rows that detector does have",
          '"arch_disk_io_0"' in err and '"pool-1-thread-1"' in err, err)


def test_a_row_of_another_report_is_not_the_row_cited(tmp_path: Path) -> None:
    """`pool-1-thread-1` is in report 1 only: by number, report 2 has no such row."""
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["causes"][0]["evidence"][0] = {"report": 2, "location": "pool-1-thread-1"}
    err = refused(p, bad)
    check("named, with the report", "report 2 has no row at \"pool-1-thread-1\"" in err, err)


def test_a_detector_that_is_given_has_to_hold_the_row(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    wrong_detector = copy.deepcopy(VERDICT)
    wrong_detector["causes"][0]["evidence"][0]["detector"] = "main_thread_block"
    err = refused(p, wrong_detector)
    check("the row is there, under another detector — the reason says which",
          "that location is a row of uninstrumented_cpu" in err, err)

    no_detector = copy.deepcopy(VERDICT)
    no_detector["causes"][0]["evidence"][0]["detector"] = "gc_pressure"
    err = refused(p, no_detector)
    check("a detector the report does not have", "report 1 has no detector gc_pressure "
          "(it has main_thread_block, uninstrumented_cpu)" in err, err)


def test_markers_are_rows_for_a_citation_with_no_detector(tmp_path: Path) -> None:
    check("a marker the round recorded is a row",
          problems({"summary": "s", "inside": {"report": 2, "location": "AGENTTMP_fill"},
                    "causes": []}, ROUND_1, ROUND_2) == [])
    check("a marker is not a row of a detector",
          problems({"summary": "s", "causes": [{
              "title": "t", "where": ["w"],
              "evidence": [{"report": 2, "detector": "main_thread_block",
                            "location": "AGENTTMP_fill"}]}]}, ROUND_1, ROUND_2) != [])


def test_a_location_cut_by_the_text_view_says_so(tmp_path: Path) -> None:
    """`echolot report` clips a location at 60 characters, and an agent that
    copies from the table cites the clip."""
    long_name = "androidx.compose.ui.platform.AndroidComposeView.dispatchDraw.inner"
    rep = report(main_thread_block=[long_name])
    why = problems({"summary": "s", "causes": [{
        "title": "t", "where": ["w"],
        "evidence": [{"report": 1, "location": long_name[:59] + "…"}]}]}, rep)
    check("refused", len(why) == 1, why)
    check("with where to get it whole", "--wide" in why[0] and "--json" in why[0], why[0])


def test_the_place_a_verdict_is_inside_is_held_to_the_same_check(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["inside"] = {"report": 1, "location": "AGENTTMP_fill"}   # a round before it existed
    err = refused(p, bad)
    check("named", "inside: report 1 has no row at \"AGENTTMP_fill\"" in err, err)


def test_a_hunt_with_no_reports_has_nothing_to_cite(tmp_path: Path) -> None:
    p = hunt_with(tmp_path)
    err = refused(p, VERDICT)
    check("says there are none, and where they come from",
          "no archived reports" in err and "analyze" in err, err)


def test_a_report_that_does_not_read_is_named_as_that(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    (hunt_mod.home(p, hunt_mod.load(p)) / "reports" / "002.json").write_text("{", encoding="utf-8")
    err = refused(p, VERDICT)
    check("not 'missing'", "report 2 is filed but does not read as a report" in err, err)


def test_every_problem_comes_back_at_once(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    err = refused(p, {"causes": [{"title": "", "mechanism": 4, "expected_ms": "soon",
                                  "confidence": "certain", "evidence": []},
                                 {"where": ["x"], "title": "t", "colour": "red",
                                  "evidence": [{"report": 9, "location": "z"}]}],
                      "cleanup": "mostly"})
    for want in ("summary:", "causes[0].title", "causes[0].where", "causes[0].evidence",
                 "causes[0].mechanism", "causes[0].expected_ms", "causes[0].confidence",
                 "causes[1]: no such field colour", "causes[1].evidence[0]: report 9",
                 "cleanup:"):
        check(f"names {want}", want in err, err)


def test_a_verdict_that_is_not_json_or_not_an_object_is_refused(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    check("prose", "not JSON" in refused(p, "Store.fill, confidence high"))
    check("empty", "is empty" in refused(p, ""))
    check("a list", "has to be a JSON object" in refused(p, "[1, 2]"))


def test_a_verdict_that_cannot_be_read_is_refused_and_the_hunt_stays_open(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    code, _, err = run(p, "hunt", "--done", "--verdict", "no-such-file.json")
    check("refused", code == 2 and "cannot read the verdict" in err, err)
    check("open", hunt_mod.load(p)["status"] == "open")


# --- the check is a function of the verdict and the reports ------------------

SHAPES = [
    {"summary": "s", "causes": [{"title": "t", "where": ["w"], "evidence": []}]},
    {"summary": "", "causes": "none"},
    {"summary": "s", "inside": {"report": 3, "location": "x"},
     "causes": [{"title": "t", "where": [], "evidence": [{"report": 1, "location": "q"}]}]},
    copy.deepcopy(VERDICT),
]


@pytest.mark.parametrize("verdict", SHAPES)
def test_the_same_verdict_against_the_same_reports_is_judged_the_same(verdict: Any) -> None:
    first = problems(verdict, ROUND_1, ROUND_2)
    again = problems(copy.deepcopy(verdict), copy.deepcopy(ROUND_1), copy.deepcopy(ROUND_2))
    check("the same problems, in the same order", first == again, (first, again))
    reordered = json.loads(json.dumps(verdict, sort_keys=True))
    check("whatever order its keys came in",
          sorted(problems(reordered, ROUND_1, ROUND_2)) == sorted(first), reordered)


def test_the_example_verdict_is_sound_against_its_reports() -> None:
    check("no problems", problems(VERDICT, ROUND_1, ROUND_2) == [],
          problems(VERDICT, ROUND_1, ROUND_2))


def test_the_check_is_not_thrown_by_a_report_of_an_odd_shape() -> None:
    odd = {"detectors": [None, {"id": 3}, {"id": "a", "rows": "none"},
                         {"id": "b", "rows": [None, {"location": 5}, {"location": "q"}]}],
           "markers": [1]}
    check("a row that is there is found", problems(
        {"summary": "s", "causes": [{"title": "t", "where": ["w"], "evidence": [
            {"report": 1, "detector": "b", "location": "q"}]}]}, odd) == [])


# --- the markers echolot counts itself ---------------------------------------

def leave_markers(project: Path) -> None:
    src = project / "app" / "src" / "main" / "kotlin"
    src.mkdir(parents=True)
    (src / "Feed.kt").write_text('fun b() {\n  android.os.Trace.beginSection("AGENTTMP_by")\n}\n',
                                 encoding="utf-8")


def test_the_markers_still_in_the_sources_are_recorded_beside_what_the_agent_said(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    leave_markers(p)
    code, out, err = run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    check("the verdict stands: the count is recorded, it does not refuse", code == 0, err)
    cleanup = hunt_mod.load(p)["verdict_check"]["cleanup"]
    check("what the agent said", cleanup["claimed"] == "removed", cleanup)
    check("what echolot counted", cleanup["found"]["markers"] == 1
          and cleanup["found"]["files"] and cleanup["found"]["prefix"] == "AGENTTMP_", cleanup)
    check("and that they disagree", cleanup["agrees"] is False, cleanup)
    check("and it is said where the human reads", "1 AGENTTMP_ marker(s) still in" in err
          and "the verdict says removed" in err, err)
    check("the agent's own field is not rewritten",
          hunt_mod.load(p)["verdict"]["cleanup"] == "removed")


def test_a_clean_tree_agrees_with_removed_and_an_unstated_cleanup_is_still_counted(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, out, _ = run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    check("accepted", code == 0 and "no temporary markers in the sources" in out, out)
    check("agrees", hunt_mod.load(p)["verdict_check"]["cleanup"]["agrees"] is True)

    q = hunt_with(tmp_path / "other", ROUND_1)
    silent = {"summary": "s", "causes": []}
    code, _, _ = run(q, "hunt", "--done", "--verdict", write(q, silent))
    cleanup = hunt_mod.load(q)["verdict_check"]["cleanup"]
    check("counted even when the agent said nothing",
          code == 0 and cleanup["claimed"] is None and cleanup["found"]["markers"] == 0
          and cleanup["agrees"] is None, cleanup)


def test_the_agents_cleanup_vocabulary() -> None:
    clean, dirty = {"markers": 0, "lines": 0}, {"markers": 2, "lines": 0}
    check("removed", verdict_mod.cleanup_agrees("removed", clean) is True
          and verdict_mod.cleanup_agrees("removed", dirty) is False)
    check("none_added", verdict_mod.cleanup_agrees("none_added", dirty) is False)
    check("left", verdict_mod.cleanup_agrees("left", dirty) is True
          and verdict_mod.cleanup_agrees("left", clean) is False)
    check("a tagged line with no prefix beside it counts",
          verdict_mod.cleanup_agrees("removed", {"markers": 0, "lines": 1}) is False)


# --- the options around it ---------------------------------------------------

def test_the_text_form_is_what_it_was(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    code, out, _ = run(p, "hunt", "--done", "TextLayout on main — confidence high")
    check("closes", code == 0 and 'concluded: "cold start 2.1 s"' in out, out)
    h = hunt_mod.load(p)
    check("as a string, with no verdict beside it",
          h["conclusion"] == "TextLayout on main — confidence high"
          and "verdict" not in h and "verdict_check" not in h, h)
    code, out, _ = run(p, "hunt", "--show", "1")
    check("shown as before", "status: concluded · TextLayout on main" in out
          and "verdict" not in out, out)


def test_the_flags_that_need_each_other(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    code, _, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("--verdict alone", code == 2 and "goes with --done" in err, err)
    code, _, err = run(p, "hunt", "--done")
    check("--done with nothing", code == 2 and "--verdict" in err, err)
    check("neither closed it", hunt_mod.load(p)["status"] == "open")


def test_a_closed_or_missing_hunt_is_still_said_before_the_verdict_is_read(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    again = copy.deepcopy(VERDICT)
    again["summary"] = "another"
    code, _, err = run(p, "hunt", "--done", "--verdict", write(p, again))
    check("a second close is refused as the text form's is", code == 1
          and "already concluded" in err, err)
    check("and the first stands", hunt_mod.load(p)["verdict"] == VERDICT)

    empty = tmp_path / "nothing"
    empty.mkdir()
    code, _, err = run(empty, "hunt", "--done", "--verdict", "-", stdin="{}")
    check("no hunt", code == 1 and "no investigation is open" in err, err)


# --- reading it back ---------------------------------------------------------

def test_show_prints_the_verdict_as_text(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    code, out, _ = run(p, "hunt", "--show", "1")
    check("shows", code == 0, out)
    for want in ("verdict: Nearly all of the extra time", "inside: report 2 · AGENTTMP_fill",
                 "causes (1, in the order the agent gave them)",
                 "1. The store is filled in about 150 small transactions",
                 "where:      store/StoreFiller.kt:70-88",
                 "evidence:   report 1 · uninstrumented_cpu · arch_disk_io_0",
                 "report 2 · AGENTTMP_insert_cards",
                 "fix:        One transaction for the whole fill.",
                 "expected:   300–400 ms",
                 "confidence: high — three runs in each of two rounds",
                 "cleanup: removed — echolot found none left in the sources",
                 "verdict: 1 cause(s)"):
        check(f"says: {want}", want in out, out)
    check("the summary is not said twice", out.count("Nearly all of the extra time") == 1, out)


def test_list_and_the_recap_read_a_verdict_hunt_like_any_other(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    _, out, _ = run(p, "hunt", "--list")
    check("--list has the summary and that there is a verdict",
          "→ Nearly all of the extra time" in out and "verdict: 1 cause(s)" in out, out)
    _, out, _ = run(p, "hunt")
    check("bare hunt recaps the closed one", "→ Nearly all of the extra time" in out, out)
    _, out, _ = run(p)
    check("status", "concluded" in out, out)


@pytest.mark.parametrize("stored", [
    "not a verdict", [], {"causes": "none"}, {"causes": [1, None, {"evidence": 4}]},
    {"summary": 7, "inside": "x", "causes": [{"title": ["t"], "where": "w",
                                                "expected_ms": "soon", "confidence": 3}]},
])
def test_a_record_edited_by_hand_still_lists_and_shows(tmp_path: Path, stored: Any) -> None:
    """`hunt.json` is a file people open. A field of the wrong kind is skipped."""
    p = hunt_with(tmp_path, ROUND_1)
    run(p, "hunt", "--done", "by hand")
    h = hunt_mod.load(p)
    h["verdict"] = stored
    h["verdict_check"] = "?"
    hunt_mod.save(p, h)
    for argv in (("hunt", "--list"), ("hunt", "--show", "1"), ("hunt",), ()):
        code, out, err = run(p, *argv)
        check(f"{' '.join(argv) or 'status'} still answers", code == 0, out + err)
