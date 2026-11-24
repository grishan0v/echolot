#!/usr/bin/env python3
"""After the fix: the comparison as a page of `echolot ui`.

The server draws the page `compare --html` writes, from `comparison.json` as
it is at that moment. What is pinned here is when there is such a page:

  - once the last report has been compared, with the plain reading kept;
  - not for a comparison written as a table alone, nor for one older than
    the report it stands beside, nor for a file that is no comparison;
  - for each scenario its own.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import kit  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare_page import every_movement  # noqa: E402
from tests.test_report_page import read  # noqa: E402
from tests.test_ui import SEVERAL, Adb, Project, get, site_of  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def project(tmp_path, monkeypatch):
    Adb(monkeypatch)
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def compared(project, flags=("--html",), where=".echolot/out"):
    """A project whose last report has been compared with the one before it."""
    before, after = every_movement()
    before["generated_at"], after["generated_at"] = "2026-10-15T12:23:02+00:00", "2026-10-15T15:41:27+00:00"
    project.report(where, data=after)
    folder = project.root / where
    (folder / "before.json").write_text(json.dumps(before), encoding="utf-8")
    code, _, err = run("compare", str(folder / "before.json"), str(folder / "report.json"), "-o", str(folder), *flags)
    check("the comparison was made", code == 0, err)
    return folder


def test_the_after_page_is_the_page_compare_writes(project):
    folder = compared(project.ready())
    site = site_of(project)
    reply = get(site, "after")
    page = read(reply.body)
    check("it is drawn", reply.status == 200 and page.one("h1").text == "Faster by 0.7 s")
    check("as a step of the path, the one the reader is on",
          [a.text for a in page.one("nav", "steps").all("a")] == ["Start", "Report", "After the fix"]
          and page.one("nav", "steps").one("a", aria_current="page").text == "After the fix")
    check("under the server's policy", page.one("meta", http_equiv="Content-Security-Policy").attrs["content"] == kit.SERVED)
    on_disk = read((folder / "comparison.html").read_text(encoding="utf-8"))
    check("the same page as the file, row for row", [r.text for r in page.all("div", "tr")] == [r.text for r in on_disk.all("div", "tr")]
          and page.one("p", "lead").text == on_disk.one("p", "lead").text)
    check("every link of it leads to a page of the server",
          all(get(site, a.attrs["href"].split(f"/{site.token}/", 1)[1]).status == 200 for a in page.all("a")))
    start = read(get(site).body)
    check("and the start page has the step", "After the fix" in [a.text for a in start.one("nav", "steps").all("a")])


def test_it_is_drawn_from_the_file_as_it_is_now(project):
    folder = compared(project.ready())
    (folder / "comparison.html").unlink()
    check("with no page on disk it is drawn all the same", get(site_of(project), "after").status == 200)


def test_no_comparison_no_step_and_a_page_that_says_so(project):
    project.ready().report()
    site = site_of(project)
    reply = get(site, "after")
    check("there is nothing to show yet", reply.status == 404 and "No comparison yet" in reply.body)
    steps = [a.text for a in read(get(site).body).one("nav", "steps").all("a")]
    check("and no step leads there", steps == ["Start", "Report"], steps)


def test_a_comparison_written_without_its_reading_is_not_a_page(project):
    compared(project.ready(), flags=())
    site = site_of(project)
    check("the table alone is for a terminal", get(site, "after").status == 404
          and "echolot compare --html" in read(get(site, "after").body).text)
    check("so there is no step", "After the fix" not in read(get(site).body).text)


def test_a_comparison_older_than_the_report_is_of_the_one_before(project):
    folder = compared(project.ready())
    site = site_of(project)
    check("it is there", get(site, "after").status == 200)
    late = time.time() + 60
    os.utime(folder / "report.json", (late, late))
    check("a report written since is not what was compared", get(site, "after").status == 404
          and "After the fix" not in read(get(site).body).text)


def test_a_comparison_of_two_other_reports_is_not_this_ones(project):
    folder = compared(project.ready())
    found = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    site = site_of(project)
    check("its newer side is the report beside it, so it is drawn",
          found["after"]["generated_at"] == report["generated_at"] and get(site, "after").status == 200)
    found["after"] = {**found["after"], "path": ".echolot/hunts/3/reports/003.json",
                      "generated_at": "2026-10-15T12:23:02+00:00"}
    (folder / "comparison.json").write_text(json.dumps(found), encoding="utf-8")
    reply = get(site, "after")
    check("a comparison of other reports written here is not what changed in this one",
          reply.status == 404 and "No comparison yet" in reply.body, reply.status)
    check("and no step leads there", "After the fix" not in [a.text for a in read(get(site).body).one("nav", "steps").all("a")])
    for broken in (None, "abc"):
        found["after"] = broken
        (folder / "comparison.json").write_text(json.dumps(found), encoding="utf-8")
        check(f"nor one whose newer side is {broken!r}", get(site, "after").status == 404)


def test_two_other_reports_compared_by_the_command_are_not_this_ones(project):
    folder = compared(project.ready())
    site = site_of(project)
    (folder / "again.json").write_text((folder / "before.json").read_text(encoding="utf-8"), encoding="utf-8")
    code, _, err = run("compare", str(folder / "before.json"), str(folder / "again.json"), "-o", str(folder), "--html")
    check("the other comparison was made, over the one before", code == 0, err)
    check("so the page says there is none of the last report", get(site, "after").status == 404
          and "After the fix" not in read(get(site).body).text)


@pytest.mark.parametrize("text", ["", "{", "[]", '{"kind": "report"}', '{"kind": "comparison", "plain": []}'])
def test_a_file_that_is_no_comparison_is_no_page(project, text):
    folder = compared(project.ready())
    (folder / "comparison.json").write_text(text, encoding="utf-8")
    site = site_of(project)
    check("it is not drawn, and the server goes on", get(site, "after").status == 404 and get(site).status == 200)


def _all_strings(node):
    """Every field of a check but its `id`, as text."""
    return {k: (v if k == "id" else "zz") for k, v in node.items()}


@pytest.mark.parametrize("change, said", [
    (lambda p: p.update(notes="abc"), "`plain.notes` is text, not a list"),
    (lambda p: p.update(checks=["x"]), "`plain.checks[0]` is text, not an object"),
    (lambda p: p["checks"].__setitem__(0, _all_strings(p["checks"][0])), "`plain.checks[0].before` is text, not an object"),
    (lambda p: p["window"].update(before="zz", after="zz"), "`plain.window.before` is text, not an object"),
])
def test_a_comparison_with_a_field_of_the_wrong_kind_says_it_does_not_read(project, change, said):
    folder = compared(project.ready())
    found = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    change(found["plain"])
    (folder / "comparison.json").write_text(json.dumps(found), encoding="utf-8")
    site = site_of(project)
    reply = get(site, "after")
    text = read(reply.body).text
    check("it is no error, and the page says the comparison does not read and how to write it anew",
          reply.status == 404 and "The comparison does not read" in text and "echolot compare --html" in text, reply.status)
    check("naming what is wrong", said.replace("`", "") in text.replace("`", ""), text)
    check("the start page still draws, with no step that leads there",
          get(site).status == 200 and "After the fix" not in read(get(site).body).one("nav", "steps").text)


def test_each_scenario_has_its_own(project):
    project.app().config(SEVERAL).agents().doctor()
    project.report(".echolot/out/coldStart")
    compared(project, where=".echolot/out/feedScroll")
    site = site_of(project)
    check("the one that was compared", get(site, "after/feedScroll").status == 200)
    check("not the one that was not", get(site, "after/coldStart").status == 404)
    check("and no scenario the config does not name", get(site, "after/nope").status == 404
          and "No such scenario" in get(site, "after/nope").body)
