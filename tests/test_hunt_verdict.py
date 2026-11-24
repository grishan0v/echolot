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
    {"summary": "s", "causes": [], "suggestions": [{}, "x", {"fix": "f", "expected_ms": -1}],
     "ruled_out": "a", "also_measured": [1, ""]},
    {"summary": "s", "causes": [{"title": "t", "where": ["w"], "cost_of": "x", "cost_ms": -1,
                                 "evidence": [{"report": 1, "location": "q"}]}]},
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


def test_done_with_nothing_to_record_is_refused(tmp_path: Path) -> None:
    """`--verdict` alone is the check, below. `--done` alone has nothing to keep."""
    p = hunt_with(tmp_path, ROUND_1)
    code, _, err = run(p, "hunt", "--done")
    check("--done with nothing", code == 2 and "--verdict" in err, err)
    code, _, err = run(p, "hunt", "--done", "  ")
    check("or with only blanks", code == 2 and "--verdict" in err, err)
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

    # Beside the project: inside it, its echolot.yml is found above (#721).
    empty = tmp_path.parent / f"{tmp_path.name}-nothing"
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
                 # Set against nothing: its confidence is about what the place
                 # costs, and whether it is what changed was not measured (#538).
                 "against: nothing — no reference was recorded, so the causes are where the "
                 "build measured spends its time; none is shown to be what changed",
                 "confidence: high that it costs this — three runs in each of two rounds",
                 "changed:    not measured — nothing was recorded to set it against",
                 "cleanup: removed — echolot found none left in the sources",
                 "verdict: 1 cause(s), no reference"):
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
    {"summary": "s", "causes": [], "ruled_out": 5, "also_measured": {"a": 1},
     "suggestions": "x"},
    {"summary": "s", "ruled_out": [None, 3],
     "suggestions": [None, {"fix": ["a"], "expected_ms": []}]},
    {"summary": "s", "causes": [{"title": "t", "cost_ms": "soon", "cost_of": 3},
                                {"title": "u", "cost_of": "of the launch"}]},
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


# --- the shape, field by field -----------------------------------------------

def test_a_citation_has_to_be_an_object_with_a_whole_report_number_and_a_location() -> None:
    def cited(entry: Any) -> list[str]:
        return problems({"summary": "s", "causes": [{
            "title": "t", "where": ["w"], "evidence": [entry]}]}, ROUND_1)

    check("a string", "has to be an object" in cited("report 1")[0])
    for entry, field in (({"report": "1", "location": "x"}, "report"),
                         ({"report": True, "location": "x"}, "report"),
                         ({"report": 0, "location": "x"}, "report"),
                         ({"report": 1, "location": "  "}, "location"),
                         ({"report": 1, "location": "x", "detector": 3}, "detector")):
        why = cited(entry)
        check(f"{field} of {entry}", len(why) == 1 and f".{field}:" in why[0], why)
    check("a citation with a field of its own is refused",
          "no such field row" in cited({"report": 1, "location": "x", "row": 2})[0])


def test_what_a_cause_may_hold() -> None:
    def cause(**fields: Any) -> list[str]:
        return problems({"summary": "s", "causes": [
            {"title": "t", "where": ["w"],
             "evidence": [{"report": 1, "location": "Store.fill"}], **fields}]}, ROUND_1)

    check("a cause that is not an object", "has to be an object" in problems(
        {"summary": "s", "causes": ["store"]}, ROUND_1)[0])
    check("a number of milliseconds is an estimate", cause(expected_ms=350) == [])
    check("so is a range", cause(expected_ms=[300, 400]) == [])
    for bad in (-1, [400, 300], [1], [1, 2, 3], ["a", "b"], True, float("nan"), None):
        check(f"{bad!r} is not", cause(expected_ms=bad) != [], bad)
    for level in ("high", "medium", "low"):
        check(level, cause(confidence=level) == [])
    check("where is a list of places", cause(where="store.kt") != [] and cause(where=[]) != []
          and cause(where=["a", ""]) != [])


def test_an_estimate_and_a_row_read_as_text_whatever_their_shape() -> None:
    cause = {"title": "t", "where": ["w"], "expected_ms": 350, "evidence": ["loose text"]}
    text = "\n".join(verdict_mod.lines({"summary": "s", "causes": [cause]}))
    check("a single number is 'about'", "expected:   about 350 ms" in text, text)
    check("a row that is not an object is shown as it is", "loose text" in text, text)
    found = {"cleanup": {"found": {"markers": 0, "lines": 2, "files": ["a.kt"]}}}
    text = "\n".join(verdict_mod.lines({"summary": "s", "cleanup": "removed"}, found))
    check("a tagged line with no prefix is counted as what it is",
          "2 line(s) tagged `// echolot:mark`" in text, text)


def test_a_detector_that_stayed_silent_is_named_as_that() -> None:
    quiet = report(gc_pressure=[])
    why = problems({"summary": "s", "causes": [{
        "title": "t", "where": ["w"],
        "evidence": [{"report": 1, "detector": "gc_pressure", "location": "x"}]}]}, quiet)
    check("silent, rather than a list of nothing", "it is silent in that report" in why[0], why)


# --- the three lists: what was ruled out, what else was measured, what to change

FULL: dict[str, Any] = {
    **VERDICT,
    "causes": [{**VERDICT["causes"][0], "cost_ms": 1150,
                "cost_of": "wall time of the inserts, as the marker measured it"}],
    "suggestions": [
        {"fix": "Ship the rows in a prebuilt database.", "expected_ms": [1000, 1400]},
        {"fix": "Open the database once, before the first screen."},
    ],
    "ruled_out": ["Layout inflation: 41 ms in each of three runs, as before",
                  "Garbage collection: no pause above 8 ms"],
    "also_measured": ["AGENTTMP_fill: 1,480 ms", "AGENTTMP_insert_cards: 1,150 ms"],
}


def test_the_three_lists_are_kept_as_the_agent_wrote_them(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, _, err = run(p, "hunt", "--done", "--verdict", write(p, FULL))
    check("accepted", code == 0, err)
    check("whole and unchanged", hunt_mod.load(p)["verdict"] == FULL)
    check("and they are not counted as cited rows",
          hunt_mod.load(p)["verdict_check"]["cited"] == 3)


def test_show_prints_the_three_lists_after_the_causes(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    run(p, "hunt", "--done", "--verdict", write(p, FULL))
    code, out, _ = run(p, "hunt", "--show", "1")
    check("shows", code == 0, out)
    for want in ("suggestions (2, not tied to one cause):",
                 "1. Ship the rows in a prebuilt database.",
                 "expected:   1000–1400 ms",
                 "2. Open the database once, before the first screen.",
                 "ruled out (2):",
                 "- Layout inflation: 41 ms in each of three runs, as before",
                 "- Garbage collection: no pause above 8 ms",
                 "also measured (2):",
                 "- AGENTTMP_fill: 1,480 ms",
                 "cost:       1150 ms — wall time of the inserts, as the marker measured it"):
        check(f"says: {want}", want in out, out)
    where = [out.index(s) for s in ("causes (1", "suggestions (2", "ruled out (2",
                                    "also measured (2", "cleanup:")]
    check("causes, suggestions, ruled out, also measured, then the cleanup",
          where == sorted(where), where)
    where = [out.index(s) for s in ("evidence:", "cost:       1150 ms", "fix:        One",
                                    "expected:   300–400 ms")]
    check("a cause reads evidence, its cost, then the fix and what it should win back",
          where == sorted(where), where)


def test_an_empty_list_is_an_answer_that_has_nothing_to_show(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    bare = {"summary": "s", "causes": [], "ruled_out": [], "also_measured": [],
            "suggestions": []}
    code, _, err = run(p, "hunt", "--done", "--verdict", write(p, bare))
    check("accepted", code == 0, err)
    _, out, _ = run(p, "hunt", "--show", "1")
    check("and shown as no block", "ruled out" not in out and "suggestions" not in out
          and "also measured" not in out, out)


def test_what_the_three_lists_may_hold() -> None:
    def top(**fields: Any) -> list[str]:
        return problems({"summary": "s", "causes": [], **fields}, ROUND_1)

    for key in ("ruled_out", "also_measured"):
        check(f"{key}: lines", top(**{key: ["a", "b"]}) == [])
        check(f"{key}: an empty list", top(**{key: []}) == [])
        for not_a_list in ("one line", None, {"a": "b"}, 3):
            why = top(**{key: not_a_list})
            check(f"{key}: {not_a_list!r} is not a list",
                  len(why) == 1 and why[0].startswith(f"{key}: has to be a list"), why)
        for bad in ("", "  ", 3, None, ["x"]):
            why = top(**{key: ["fine", bad]})
            check(f"{key}: {bad!r} is not a line",
                  len(why) == 1 and why[0].startswith(f"{key}[1]: has to be a non-empty"), why)

    check("a suggestion is a fix", top(suggestions=[{"fix": "f"}]) == [])
    check("with an estimate as in a cause, a number or a range", top(suggestions=[
        {"fix": "f", "expected_ms": 5}, {"fix": "g", "expected_ms": [1, 2]}]) == [])
    check("an empty list is no suggestion", top(suggestions=[]) == [])
    for bad, want in (({}, "suggestions[0].fix: required"),
                      ({"fix": " "}, "suggestions[0].fix: required"),
                      ({"fix": 3}, "suggestions[0].fix: required"),
                      ({"fix": "f", "expected_ms": -1}, "suggestions[0].expected_ms"),
                      ({"fix": "f", "expected_ms": [2, 1]}, "suggestions[0].expected_ms"),
                      ({"fix": "f", "expected_ms": "soon"}, "suggestions[0].expected_ms"),
                      ({"fix": "f", "why": "x"}, "suggestions[0]: no such field why"),
                      ({"fix": "f", "report": 1}, "suggestions[0]: no such field report")):
        why = top(suggestions=[bad])
        check(f"{bad}", len(why) == 1 and want in why[0], why)
    check("one that is not an object",
          "suggestions[0]: has to be an object" in top(suggestions=["do it"])[0])
    check("a second one is the second",
          top(suggestions=[{"fix": "f"}, {"fix": ""}])[0].startswith("suggestions[1].fix"))
    check("not a list", top(suggestions="do it")[0].startswith("suggestions: has to be a list"))


def test_the_three_lists_are_held_to_their_shape_and_nothing_else() -> None:
    """They point at no row, so a line that names a place no report holds is as
    good as any other, and the same verdict is sound with or without reports."""
    v = {"summary": "s", "causes": [], "ruled_out": ["report 9, location nowhere"],
         "also_measured": ["AGENTTMP_ghost: 1 ms"], "suggestions": [{"fix": "do it"}]}
    check("against reports that hold none of it", problems(v, ROUND_1) == [], problems(v, ROUND_1))
    check("and against none at all", problems(v) == [], problems(v))


def test_a_field_that_is_none_of_them_is_still_refused_with_the_fields_named() -> None:
    why = problems({"summary": "s", "causes": [], "ruledout": ["x"]}, ROUND_1)
    check("refused, once", len(why) == 1 and "no such field ruledout" in why[0], why)
    for field in verdict_mod.TOP_FIELDS:
        check(f"and {field} is one of the ones it names", field in why[0], why[0])
    check("the three are among them", {"suggestions", "ruled_out", "also_measured"}
          <= set(verdict_mod.TOP_FIELDS))


def test_the_three_lists_read_back_whatever_their_shape() -> None:
    text = "\n".join(verdict_mod.lines({
        "summary": "s", "causes": [],
        "ruled_out": "one line",
        "also_measured": [None, 3, "", "AGENTTMP_a: 1 ms", "two\nlines"],
        "suggestions": [1, {"fix": None}, {"fix": "do it", "expected_ms": "soon"},
                        {"fix": "also", "expected_ms": 350}]}))
    check("a list that is not one is skipped", "ruled out" not in text, text)
    check("only the lines are counted", "also measured (2):" in text, text)
    check("a line of several lines keeps its place",
          "    - two\n      lines" in text, text)
    check("a suggestion with no fix is still a suggestion",
          "suggestions (3, not tied to one cause):" in text and "(no fix)" in text, text)
    check("an estimate that is not one is left out and one that is is said",
          "expected:   about 350 ms" in text and "soon" not in text, text)


# --- what a cause costs, as measured: cost_ms and cost_of --------------------

def cause_problems(**fields: Any) -> list[str]:
    return problems({"summary": "s", "causes": [
        {"title": "t", "where": ["w"],
         "evidence": [{"report": 1, "location": "Store.fill"}], **fields}]}, ROUND_1)


def test_the_cost_of_a_cause_is_held_to_the_shape_of_an_estimate() -> None:
    check("a number of milliseconds", cause_problems(cost_ms=1150) == [])
    check("a range, when the runs differ", cause_problems(cost_ms=[1100, 1200]) == [])
    check("nothing is a cost that was measured", cause_problems(cost_ms=0) == [])
    for bad in (-1, [1200, 1100], [1], [1, 2, 3], ["a", "b"], True, float("nan"), "soon", None):
        why = cause_problems(cost_ms=bad)
        check(f"{bad!r} is not", len(why) == 1 and why[0].startswith("causes[0].cost_ms:"), why)
    for value in (350, [300, 400], 0, -1, [400, 300], "soon", None, True, [1]):
        a, b = cause_problems(cost_ms=value), cause_problems(expected_ms=value)
        check(f"{value!r}: one rule for both figures",
              [m.replace("cost_ms", "expected_ms") for m in a] == b, (a, b))


def test_cost_of_says_what_the_number_measures_and_goes_only_with_it() -> None:
    check("with it", cause_problems(cost_ms=5, cost_of="of the launch") == [])
    check("the number alone is a cost", cause_problems(cost_ms=5) == [])
    why = cause_problems(cost_of="of the launch")
    check("the description alone is refused, and says why",
          len(why) == 1 and why[0].startswith("causes[0].cost_of: goes with cost_ms"), why)
    for bad in ("", "  ", 3, None, ["x"]):
        why = cause_problems(cost_ms=5, cost_of=bad)
        check(f"{bad!r} is not a description",
              len(why) == 1 and why[0] == "causes[0].cost_of: has to be a non-empty string", why)
    check("both wrong is two problems, each named",
          [w.split(":")[0] for w in cause_problems(cost_ms=-1, cost_of="")]
          == ["causes[0].cost_ms", "causes[0].cost_of"])
    check("a bad description with no number is its shape and its pairing",
          len(cause_problems(cost_of="")) == 2)


def test_a_cost_is_not_held_to_the_rows_it_came_from() -> None:
    """The agent took the number from its rows and echolot does not recompute
    it: a cost larger than the whole window, or one that matches no row, is a
    sound verdict. Only the shape is held."""
    check("larger than the window of the report", cause_problems(cost_ms=50000) == []
          and ROUND_1["window"]["duration_ms"] < 50000)
    check("and the estimate beside it may be anything of the right shape",
          cause_problems(cost_ms=100, expected_ms=[5000, 9000]) == [])


def test_a_suggestion_has_no_cost_and_a_wrong_field_names_the_cost_ones() -> None:
    why = problems({"summary": "s", "causes": [],
                    "suggestions": [{"fix": "f", "cost_ms": 5}]}, ROUND_1)
    check("a suggestion is a fix and its estimate",
          len(why) == 1 and "suggestions[0]: no such field cost_ms" in why[0], why)
    why = cause_problems(cost=5)
    check("a misspelt cost is refused, with the right names given",
          len(why) == 1 and "no such field cost" in why[0] and "cost_ms, cost_of" in why[0], why)


def test_a_cost_reads_as_a_measurement_and_an_estimate_as_an_estimate() -> None:
    def shown(**fields: Any) -> str:
        return "\n".join(verdict_mod.lines({"summary": "s", "causes": [{"title": "t", **fields}]}))

    def cost_line(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if "cost:" in ln]

    text = shown(cost_ms=1150, expected_ms=350)
    check("a single number is said as measured, with no 'about'",
          cost_line(text) == ["       cost:       1150 ms"], text)
    check("beside the estimate, which keeps its 'about'",
          "expected:   about 350 ms" in text, text)
    check("a range, with what it measures",
          cost_line(shown(cost_ms=[1100, 1200], cost_of="of the launch"))
          == ["       cost:       1100–1200 ms — of the launch"])
    check("a description over several lines keeps its place",
          "       cost:       5 ms — one\n" + " " * 19 + "two"
          in shown(cost_ms=5, cost_of="one\ntwo"))
    for odd in ({"cost_ms": "soon", "cost_of": "of the launch"}, {"cost_of": "of the launch"},
                {"cost_ms": [2, "x"]}, {"cost_ms": None}):
        check(f"{odd}: nothing to show, and nothing fails", cost_line(shown(**odd)) == [],
              shown(**odd))
    check("a description that is not text is left out",
          cost_line(shown(cost_ms=5, cost_of=3)) == ["       cost:       5 ms"])


# --- what the build is set against (#538) ------------------------------------
#
# Five blind hunts with no build from before the change each named the same
# long-standing hotspot, a control with no change among them, and the answer
# form called it the place of the regression. A verdict names the report of
# its reference in `against`; without one its causes read as where the build
# measured spends its time, whatever its summary says.

REFERENCE = report(uninstrumented_cpu=["pool-1-thread-1"], main_thread_block=["Store.fill"])
AGAINST = {"report": 1, "what": "the release before the change, 4.1, on the same phone"}
SET_AGAINST: dict[str, Any] = {
    **VERDICT, "against": AGAINST,
    "inside": {"report": 3, "location": "AGENTTMP_fill"},
    "causes": [{**VERDICT["causes"][0], "evidence": [
        {"report": 1, "detector": "main_thread_block", "location": "Store.fill"},
        {"report": 2, "detector": "uninstrumented_cpu", "location": "arch_disk_io_0"},
        {"report": 3, "location": "AGENTTMP_insert_cards"},
    ]}],
}


def test_a_verdict_set_against_a_reference_reads_as_a_regression(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, REFERENCE, ROUND_1, ROUND_2)
    code, out, err = run(p, "hunt", "--verdict", write(p, SET_AGAINST))
    check("the check accepts it and says what it is set against", code == 0
          and "against report 1: a regression — each cause cites a row of the build measured, "
              "outside the reference" in out, out + err)
    code, out, err = run(p, "hunt", "--done", "--verdict", write(p, SET_AGAINST))
    check("closing says it too", code == 0 and "against report 1: a regression" in out, out + err)
    h = hunt_mod.load(p)
    check("kept as it came, and the reference beside it",
          h["verdict"] == SET_AGAINST and h["verdict_check"]["against"] == 1, h["verdict_check"])
    _, out, _ = run(p, "hunt", "--show", "1")
    check("--show names the reference", "against: report 1 — the release before the change, 4.1, "
          "on the same phone" in out, out)
    check("and its confidence is the agent's about the change, with no second answer",
          "confidence: high — three runs" in out and "changed:" not in out
          and "that it costs this" not in out, out)
    check("--list says what it was set against",
          "verdict: 1 cause(s) against report 1" in run(p, "hunt", "--list")[1])


def test_a_verdict_with_no_reference_reads_as_where_the_build_spends_its_time(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, out, _ = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("accepted, and said what it is without one", code == 0
          and "against nothing: no reference, so its causes read as where this build spends "
              "its time, not as what changed" in out, out)
    run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    check("recorded as set against nothing",
          hunt_mod.load(p)["verdict_check"]["against"] is None, hunt_mod.load(p)["verdict_check"])


def test_a_verdict_recorded_before_echolot_asked_reads_as_it_did() -> None:
    """A record with no `against` in its check is from before the field: its
    silence says nothing about a reference, and nothing is claimed for it."""
    check_then = {"checked_at": "2026-10-01T10:00:00+00:00", "reports": [1, 2], "cited": 3}
    text = "\n".join(verdict_mod.lines(VERDICT, check_then))
    check("no line about a reference", "against:" not in text and "changed:" not in text, text)
    check("and the confidence as the agent gave it", "confidence: high — three runs" in text, text)
    check("nor in --list's line", verdict_mod.reads_as(VERDICT, check_then) is None)


@pytest.mark.parametrize("entry, says", [
    (1, 'against: has to be an object like {"report": 1, "what": "the release before the change"}'),
    ({"report": 1}, "against.what: required, a non-empty string — which build the reference is"),
    ({"report": 1, "what": " "}, "against.what: required"),
    ({"report": 0, "what": "4.1"}, "against.report: the number of the report this investigation "
                                   "filed for the reference, 1 or more"),
    ({"report": "1", "what": "4.1"}, "against.report: the number of the report"),
    ({"report": 9, "what": "4.1"}, "against: report 9 is not one of this investigation's reports "
                                   "(it has 1, 2, 3)"),
    ({"report": 1, "what": "4.1", "build": "x"}, "against: no such field build — the fields are "
                                                 "report, what"),
])
def test_what_against_may_hold(entry: Any, says: str) -> None:
    why = problems({**SET_AGAINST, "against": entry}, REFERENCE, ROUND_1, ROUND_2)
    check(f"refused: {says}", any(says in w for w in why), why)


def test_a_reference_has_to_be_set_against_a_row_of_the_build_measured(tmp_path: Path) -> None:
    """A cause whose every row is the reference's is the earlier build's, and
    says nothing of what changed; nor is the piece found the reference's."""
    only_before = {**SET_AGAINST, "inside": {"report": 1, "location": "Store.fill"},
                   "causes": [{**SET_AGAINST["causes"][0], "evidence": [
                       {"report": 1, "detector": "main_thread_block", "location": "Store.fill"}]}]}
    why = problems(only_before, REFERENCE, ROUND_1, ROUND_2)
    check("both refused, by name", len(why) == 2
          and why[0] == "inside: report 1 is the reference — the piece found is the build "
                        "measured's, in a report other than the reference"
          and why[1] == "causes[0].evidence: every row it cites is in report 1, the reference — "
                        "a change shows in a row of the build measured, in a report other than "
                        "the reference", why)
    p = hunt_with(tmp_path, REFERENCE, ROUND_1, ROUND_2)
    err = refused(p, only_before)
    check("and the close says it on stderr", "every row it cites is in report 1, the reference"
          in err, err)
    check("a hunt that found nothing against its reference is still a verdict",
          problems({**SET_AGAINST, "inside": {"report": 2, "location": "Store.fill"},
                    "causes": []}, REFERENCE, ROUND_1, ROUND_2) == [])


def test_a_reference_of_another_scenario_is_refused() -> None:
    scroll = {**REFERENCE, "scenario": "feed_scroll"}
    launch = {**ROUND_1, "scenario": "coldStart"}
    later = {**ROUND_2, "scenario": "coldStart"}
    why = problems(SET_AGAINST, scroll, launch, later)
    check("each report it is set against, named", why == [
        "against: report 1 measured `feed_scroll` and report 2 measured `coldStart` — a "
        "reference is the same scenario on another build",
        "against: report 1 measured `feed_scroll` and report 3 measured `coldStart` — a "
        "reference is the same scenario on another build"], why)
    check("the same scenario passes", problems(SET_AGAINST, {**REFERENCE, "scenario": "coldStart"},
                                               launch, later) == [])
    check("and a report that names none is taken as it is",
          problems(SET_AGAINST, scroll, ROUND_1, ROUND_2) == [])


def test_opening_with_no_change_named_says_what_the_hunt_can_answer(tmp_path: Path) -> None:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    for since in (["--since", "unknown"], ["--since", " Unknown "], []):
        code, out, err = run(tmp_path, "hunt", "the list stutters", *since)
        check(f"{since}: opened, and said there is nothing to set the build against", code == 0
              and "the answer is where this build spends its time, not what changed" in out
              and '`echolot guide hunt`, "A reference"' in out, out + err)
    code, out, _ = run(tmp_path, "hunt", "cold start 3 s → 7 s", "--since", "the tab redesign")
    check("with a change named, the change and nothing more",
          code == 0 and "  after: the tab redesign\n" in out and "spends its time" not in out, out)
    check("change_named", [hunt_mod.change_named(s) for s in (None, "", " unknown", "UNKNOWN",
                                                              "4.1", "since Tuesday")]
          == [False, False, False, False, True, True])
