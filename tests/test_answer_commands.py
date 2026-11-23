#!/usr/bin/env python3
"""The commands that write an investigation as a page.

`echolot hunt --show [n] --html` writes the page of the last investigation, or
of the one named, into that investigation's own folder; `echolot hunt --done …
--html` writes it as the investigation closes. What is pinned here: where the
page goes and what is printed; that it is drawn from the record and the filed
reports and holds nothing of its own; that a page which stands there is
written again when its investigation moves on, and none is written unasked;
and that the page gates nothing: no exit code depends on it, except that
asking for a page which cannot be written is an error.

The page itself is `tests/test_answer_page.py`.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import demo, recorder  # noqa: E402
from echolot import hunt as hunt_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import answer_page  # noqa: E402
from tests import answer_fixture as fx  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_answer_page import chart_rows, fold, panel_of, rows_of  # noqa: E402
from tests.test_report_page import outside, read  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The demo app's project, with nothing recorded yet."""
    (tmp_path / "echolot.yml").write_text(demo.CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def file_round(root: Path, report: dict) -> None:
    """One round of the open investigation: a report, filed as `analyze` files it."""
    out = root / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    assert hunt_mod.record_report(root, out) is not None


def open_with_rounds(root: Path, demo_report: dict) -> Path:
    code, _, err = run("hunt", fx.QUESTION, "--since", "the 2.4 release")
    assert code == 0, err
    for report in fx.rounds(demo_report).values():
        file_round(root, report)
    return root / ".echolot" / "hunts" / "1"


def test_show_html_writes_the_page_into_the_investigations_own_folder(project, demo_report):
    code, out, err = run("hunt", "--show", "--html")
    check("with no investigation there is nothing to show", code == 1 and not out and "no investigations yet" in err, err)
    home = open_with_rounds(project, demo_report)
    code, out, err = run("hunt", "--show", "--html")
    page = home / "answer.html"
    check("the last one, written where its reports are, and named", code == 0 and out.strip() == f"→ {page}" and page.is_file(),
          (code, out, err))
    text = page.read_text(encoding="utf-8")
    check("an open one shows its progress", read(text).one("span", "st").text == "Investigation in progress"
          and len(read(text).one("ol").all("li")) == 2)
    check("nothing in it points outside", not outside(text), outside(text))
    run("hunt", "--show", "--html")
    check("the same files give the same bytes", page.read_text(encoding="utf-8") == text)
    code, out, _ = run("hunt", "--show", "1", "--html")
    check("named by its number, the same page", code == 0 and out.strip() == f"→ {page}"
          and page.read_text(encoding="utf-8") == text)
    code, out, _ = run("hunt", "--show", "first screen", "--html")
    check("or by a part of its question", code == 0 and out.strip() == f"→ {page}")
    code, out, err = run("hunt", "--show", "9", "--html")
    check("one that is not there is said, and nothing is written", code == 1 and "no investigation matches" in err)
    code, out, _ = run("hunt", "--show")
    check("without --html, --show with no number prints the last one as text",
          code == 0 and out.startswith(f'#1  "{fx.QUESTION}"'), out[:80])


def test_done_with_a_verdict_and_html_writes_the_answer_as_it_closes(project, demo_report):
    home = open_with_rounds(project, demo_report)
    (project / "verdict.json").write_text(json.dumps(fx.VERDICT), encoding="utf-8")
    code, out, err = run("hunt", "--done", "--verdict", "verdict.json", "--html")
    page = home / "answer.html"
    check("closed, and the page named after the lines of the closing",
          code == 0 and out.rstrip().endswith(f"→ {page}  (the answer as a page: open it in a browser)"), (code, out, err))
    text = page.read_text(encoding="utf-8")
    hunt = hunt_mod.load(project)
    reports = hunt_mod.archived_reports(project, hunt)
    again = ("echolot collect && echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace "
             "&& echolot compare .echolot/hunts/1/reports/001.json")
    check("it is the record's and the reports', with the command that checks again",
          text == answer_page.render(hunt, reports, again=again))
    shown = read(text)
    check("the answer", shown.one("span", "st").text == "Investigation finished" and len(rows_of(shown)) == 4
          and shown.one("pre", "cmd-t").text == again)
    check("with the evidence from the filed reports", "AGENTTMP_bind_item_cards" in chart_rows(panel_of(shown, "cause-1")))
    check("the record holds nothing of the page", set(hunt) & {"page", "html", "answer"} == set()
          and "236.9" not in json.dumps(hunt))
    code, out, _ = run("hunt", "--show", "--html")
    check("asked for again, the same bytes", code == 0 and page.read_text(encoding="utf-8") == text)
    (home / "reports" / "002.json").unlink()
    code, out, _ = run("hunt", "--show", "1", "--html")
    after = read(page.read_text(encoding="utf-8"))
    check("a report deleted since: the page is still written, and says which rows are gone",
          code == 0 and len(rows_of(after)) == 4 and after.all("p", "gone"))


def test_a_verdict_that_is_refused_leaves_no_page(project, demo_report):
    home = open_with_rounds(project, demo_report)
    bad = copy.deepcopy(fx.VERDICT)
    bad["causes"][0]["evidence"][0]["report"] = 7
    (project / "verdict.json").write_text(json.dumps(bad), encoding="utf-8")
    code, out, err = run("hunt", "--done", "--verdict", "verdict.json", "--html")
    check("refused as without --html, and no page", code == 2 and "was refused" in err and not (home / "answer.html").exists(),
          (code, err))
    check("the investigation is still open", hunt_mod.load(project)["status"] == "open")


def test_html_goes_with_show_or_with_done(project, demo_report):
    home = open_with_rounds(project, demo_report)
    (project / "verdict.json").write_text(json.dumps(fx.VERDICT), encoding="utf-8")
    for argv in (["hunt", "--html"], ["hunt", "--verdict", "verdict.json", "--html"], ["hunt", "--resume", "--html"]):
        code, out, err = run(*argv)
        check(f"{' '.join(argv)} is refused with what it goes with", code == 2 and not out
              and "it goes with --show" in err and "--done" in err, (code, out, err))
    check("and nothing was written or closed", not (home / "answer.html").exists()
          and hunt_mod.load(project)["status"] == "open")


def test_done_with_a_text_and_html_shows_the_text(project, demo_report):
    home = open_with_rounds(project, demo_report)
    code, out, err = run("hunt", "--done", "It was the lock in StoreRepository. Fixed by reading a snapshot.", "--html")
    page = read((home / "answer.html").read_text(encoding="utf-8"))
    check("closed and written", code == 0 and f"→ {home / 'answer.html'}" in out, (code, out, err))
    check("the text is the answer, with its rounds", page.one("h1").text == "It was the lock in StoreRepository"
          and page.one("p", "lead").text == "Fixed by reading a snapshot." and not page.all("div", "tb")
          and len(fold(page, "How it got there, round by round").one("ol").all("li")) == 2)


def test_a_page_that_stands_there_is_written_again_when_its_investigation_moves_on(project, demo_report):
    """Left as it was, the page of the state before would read as the state now."""
    home = open_with_rounds(project, demo_report)
    page = home / "answer.html"
    code, out, err = run("hunt", "--done", "nothing found")
    check("no page was asked for, and none is written", code == 0 and not page.exists() and "answer.html" not in out + err)

    code, _, _ = run("hunt", "is the list slow to scroll")
    home = project / ".echolot" / "hunts" / "2"
    page = home / "answer.html"
    file_round(project, fx.rounds(demo_report)[1])
    run("hunt", "--show", "--html")
    first = page.read_text(encoding="utf-8")
    check("the open one's page has one round", len(read(first).one("ol").all("li")) == 1)

    trace = demo.write(project)[0]
    code, _, err = run("analyze", str(trace), "--hunt")
    again = read(page.read_text(encoding="utf-8"))
    check("a round more: the page that stood there is written again by analyze, and named with the other files",
          code == 0 and len(again.one("ol").all("li")) == 2
          and f"→ {page}  (this investigation's page stood there: written again with this round)" in err, err[-500:])

    code, out, err = run("hunt", "--done", "the list is fine")
    closed = read(page.read_text(encoding="utf-8"))
    check("closed without --html: written again with the conclusion",
          code == 0 and closed.one("span", "st").text == "Investigation finished" and closed.one("h1").text == "the list is fine"
          and f"→ {page}  (the page stood there: written again with the conclusion)" in err, (out, err))

    run("hunt", "a third question")
    third = project / ".echolot" / "hunts" / "3" / "answer.html"
    run("hunt", "--show", "--html")
    check("the third one's page says it is in progress", read(third.read_text(encoding="utf-8")).one("span", "st").text
          == "Investigation in progress")
    code, _, _ = run("hunt", "a fourth question")
    check("another question opened over it: its page says it was left unfinished",
          code == 0 and read(third.read_text(encoding="utf-8")).one("span", "st").text == "Investigation left unfinished")
    check("and the page of the one closed before is as it was", read(page.read_text(encoding="utf-8")).one("h1").text == "the list is fine")
    check("an investigation with no page gets none from any of this",
          not (project / ".echolot" / "hunts" / "1" / "answer.html").exists()
          and not (project / ".echolot" / "hunts" / "4" / "answer.html").exists())


def test_the_command_that_checks_again_names_the_scenario_where_there_are_several(project, demo_report):
    (project / "echolot.yml").write_text(
        "project:\n  package: com.example.app\n  process: com.example.app\n"
        "scenarios:\n  coldStart:\n    start: {name: bindApplication}\n    end: {name: collection_load}\n"
        "  openList:\n    start: {name: bindApplication}\n    end: {name: collection_load}\n", encoding="utf-8")
    code, _, err = run("hunt", "the list opens slowly", "--scenario", "openList")
    assert code == 0, err
    run("hunt", "--done", "it was the lock", "--html")
    page = read((project / ".echolot" / "hunts" / "1" / "answer.html").read_text(encoding="utf-8"))
    check("named in each step that takes it, and with no report filed there is nothing to compare with",
          page.one("pre", "cmd-t").text == "echolot collect --scenario openList && echolot analyze "
          ".echolot/traces/openList_iter*.perfetto-trace --scenario openList", page.one("pre", "cmd-t").text)


def test_a_config_under_another_name_is_named_in_the_command(project, demo_report):
    (project / "echolot.yml").rename(project / "perf.yml")
    code, _, err = run("hunt", "-c", "perf.yml", fx.QUESTION)
    assert code == 0, err
    file_round(project, fx.rounds(demo_report)[1])
    run("hunt", "-c", "perf.yml", "--done", "found it", "--html")
    page = read((project / ".echolot" / "hunts" / "1" / "answer.html").read_text(encoding="utf-8"))
    check("every step that reads the config names it, by its name and not by where this machine keeps it",
          page.one("pre", "cmd-t").text == "echolot collect -c perf.yml && echolot analyze "
          ".echolot/traces/coldStart_iter*.perfetto-trace -c perf.yml && echolot compare "
          ".echolot/hunts/1/reports/001.json", page.one("pre", "cmd-t").text)


def test_a_page_that_cannot_be_written_again_is_said_and_stops_nothing(project, demo_report, monkeypatch):
    """Writing it again is a courtesy of a command whose work is something else."""
    home = open_with_rounds(project, demo_report)
    run("hunt", "--show", "--html")
    before = (home / "answer.html").read_text(encoding="utf-8")

    def broken(*args, **kw):
        raise RuntimeError("a record nobody foresaw")
    monkeypatch.setattr(answer_page, "render", broken)
    code, out, err = run("hunt", "--done", "found it")
    check("the investigation is closed and the exit code is the closing's",
          code == 0 and hunt_mod.load(project)["status"] == "concluded", (code, err))
    check("the page is left as it was, and said to be of an earlier state",
          (home / "answer.html").read_text(encoding="utf-8") == before
          and "was not written again and may show an earlier state: a record nobody foresaw" in err, err)


def test_writing_the_page_is_in_the_run_log(project, demo_report):
    open_with_rounds(project, demo_report)
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        main(["hunt", "--show", "--html"])
        shown = recorder.facts()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        main(["hunt", "--done", "found it", "--html"])
        closed = recorder.facts()
    check("shown as a page", shown.get("hunt") == "page", shown)
    check("closed, with a page", closed.get("hunt") == "concluded" and closed.get("page") == "answer", closed)


def test_a_record_with_no_number_has_no_folder_for_a_page(project):
    state = project / ".echolot"
    state.mkdir()
    (state / "hunt.json").write_text(json.dumps({"question": "an old one", "status": "concluded",
                                                 "conclusion": "found"}), encoding="utf-8")
    code, out, err = run("hunt", "--show", "old", "--html")
    check("said, exit 2, and nothing written", code == 2 and not out and "cannot be written" in err
          and "no number" in err and not list(state.rglob("*.html")), (code, out, err))


def test_a_page_that_cannot_be_written_does_not_undo_the_closing(project, demo_report):
    home = open_with_rounds(project, demo_report)
    (home / "answer.html").mkdir()
    code, out, err = run("hunt", "--done", "found it", "--html")
    check("the investigation is closed, exit 0, and the page is said not to be there",
          code == 0 and hunt_mod.load(project)["status"] == "concluded"
          and "its page could not be written" in err, (code, out, err))
    code, out, err = run("hunt", "--show", "--html")
    check("asked for by itself, a page that cannot be written is an error", code == 2 and "cannot be written" in err, err)
