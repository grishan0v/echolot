#!/usr/bin/env python3
"""`echolot ui` sets or changes the time limit it wrote (#485).

What is pinned here:

  - the field, beside the report's verdict and under "All settings", with
    the limit in force in it: no limit yet, a limit set, the limit taken off;
  - that a press rewrites exactly one line, the `budget_ms` line the page
    wrote, and that every other byte of the file stays, on files with
    comments, blank lines, other keys and odd spacing around that line;
  - each reason the page leaves the file alone, said in a sentence, with
    nothing written; a number that is none; a file that changed between the
    page and the press; a press sent twice;
  - that the report is the old one until the recordings are read again, and
    the press that reads them again without the phone;
  - what the server refuses for this request, the same as for the others,
    and that a press writes that one file and nothing else.

Nothing here needs a phone or the network or waits on a timer. A project is
set up from the page itself, as a newcomer's is: its `echolot.yml` is the
page's own file.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, job, plain, project_setup, recorder, time_limit, ui  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.pages import kit, limit_page, report_page, start_page  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import REACHING_OUT, _blocks, known, make, read  # noqa: E402
from tests.test_ui import LAUNCH, PAGES, SEVERAL, Adb, Project, get, running, setting, site_of  # noqa: E402
from tests.test_ui_check import Echolot, a_run, files, press, send  # noqa: E402
from tests.test_ui_setup import serving as setting_up  # noqa: E402
from tests.test_ui_setup import set_up  # noqa: E402

CONFIG = "echolot.yml"
FORM = f"{limit_page.ID}-form"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def written(project: Project, limit: str = "") -> Path:
    """A project set up from the page, with the limit typed there: the config
    is the page's own file."""
    site, _ = setting_up(project.app().agents().doctor())
    check("set up from the page", set_up(site, limit=limit).status == 303)
    return project.root / CONFIG


def served(project: Project) -> tuple[ui.Site, Echolot]:
    """The site, with the commands of a press as doubles (`tests/test_ui_check.py`)."""
    site = site_of(project)
    echolot = Echolot(project.root)
    site.spawn = lambda root, name, plan: job.run(root, name, plan, popen=echolot, signals=False)
    return site, echolot


def page_of(site: ui.Site, path: str = ""):
    reply = get(site, path)
    check(f"the page /{path} is drawn", reply.status == 200, reply.body[:300])
    return read(reply.body)


def the_form(page):
    return page.one("form", id=FORM)


def sent_by(form) -> dict[str, str]:
    """What the form sends as it stands: its hidden fields and its field."""
    return {n.attrs["name"]: n.attrs.get("value", "") for n in form.all("input") if "name" in n.attrs}


def set_limit(site: ui.Site, typed: str, page=None, **more: str) -> ui.Reply:
    """A press of Set the limit with `typed` in the field, from the page as it was drawn."""
    page = page if page is not None else page_of(site)
    return press(site, "limit", {**sent_by(the_form(page)), "limit": typed, **more})


def blind(site: ui.Site, path: Path, typed: str = "2") -> ui.Reply:
    """A press with the fingerprints of the file as it is now, the way a page
    drawn this instant would send them: what a page that declines would get."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return press(site, "limit", {"limit": typed, "seen": digest, "rest": digest, "back": "start"})


def the_line(text: str) -> str:
    """The one line of the file that names `budget_ms`."""
    found = [line for line in text.splitlines() if "budget_ms" in line]
    check("one line names the limit", len(found) == 1, found)
    return found[0]


def limit_row(site: ui.Site):
    return setting(page_of(site), "Time limit")


# --- the three states -----------------------------------------------------------------------------

def test_no_limit_yet_the_field_sets_one(project):
    path = written(project)
    before = path.read_text(encoding="utf-8")
    site, _ = served(project)
    row = limit_row(site)
    field = row.one("input", name="limit")
    check("All settings: what the row reads, then the field, empty", row.text.startswith("None is set.")
          and "It should take no longer than" in row.text and "value" not in field.attrs, (row.text, field.attrs))
    check("the same sentence and field as on the setup page", field.attrs["inputmode"] == "decimal"
          and field.attrs["type"] == "text" and row.one("button").text == "Set the limit")
    reply = set_limit(site, "2.5")
    check("taken, and the browser is sent back to the limit", reply.status == 303
          and dict(reply.headers)["Location"] == f"/{site.token}/#{limit_page.ID}", reply.headers)
    after = path.read_text(encoding="utf-8")
    check("one line changed, to the page's own line for the limit",
          after == before.replace(project_setup.limit_line(None), project_setup.limit_line(2500), 1)
          and the_line(after) == "  budget_ms: 2500  # the time limit, 2.5 s: change the number to change it", the_line(after))
    check("the config loads and reads it", Config.load(path).scenario_budget_ms == 2500)
    row = limit_row(site)
    check("and the field shows it", row.one("input", name="limit").attrs.get("value") == "2.5"
          and "2.5 s (budget_ms: 2500)" in row.text, row.text)


def test_a_limit_that_is_set_is_changed(project):
    path = written(project, "2")
    before = path.read_text(encoding="utf-8")
    site, _ = served(project)
    check("the field holds the limit in force", limit_row(site).one("input", name="limit").attrs.get("value") == "2")
    check("taken", set_limit(site, "3,25").status == 303)
    after = path.read_text(encoding="utf-8")
    check("that line, and nothing else", after == before.replace(project_setup.limit_line(2000),
                                                                 project_setup.limit_line(3250), 1), the_line(after))
    check("read as 3.25 s", Config.load(path).scenario_budget_ms == 3250)


def test_an_empty_field_takes_the_limit_off_and_the_file_is_as_the_page_first_wrote_it(project):
    path = written(project)
    first = path.read_bytes()
    site, _ = served(project)
    check("set", set_limit(site, "2").status == 303 and Config.load(path).scenario_budget_ms == 2000)
    check("taken off", set_limit(site, "").status == 303 and Config.load(path).scenario_budget_ms is None)
    check("the line is the commented form again, and the file is byte for byte the first one", path.read_bytes() == first
          and the_line(path.read_text(encoding="utf-8")).startswith("  # budget_ms: 2000  # "))
    row = limit_row(site)
    check("the field is empty again", "value" not in row.one("input", name="limit").attrs
          and row.text.startswith("None is set."), row.text)


# --- one line, and every other byte as it was -------------------------------------------------------

def _around(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1)


EDITS = {
    "a comment above the line and one below": lambda t, line: _around(t, line, f"  # set after the first report\n{line}\n  # ↑ ours"),
    "blank lines around it": lambda t, line: _around(t, line, f"\n\n{line}\n\n"),
    "other keys after it, and odd spacing": lambda t, line: _around(t, line, f"{line}\n  runner:  {{iterations:   3}}")
    + "detectors:\n    gc_pressure:   {min_pause_ms:  20}\n",
    "the line first in its block": lambda t, line: _around(_around(t, f"{line}\n", ""), "scenario:\n", f"scenario:\n{line}\n"),
    "a comment on the scenario's own line": lambda t, line: _around(t, "scenario:\n", "scenario:    # the launch\n"),
    "other sections edited by hand": lambda t, line: t.replace("  iterations: 5", "  iterations:    7   # mine")
    + "\n# the end\nloop:\n  max_rounds: 3\n",
    "lines that end the Windows way": lambda t, line: t.replace("\n", "\r\n"),
    "no end to the last line": lambda t, line: t.rstrip("\n"),
    "the number changed by hand, as its comment says": lambda t, line: _around(t, line, line.replace("2000", "4500")),
    "the first # taken off, as its comment says": lambda t, line: _around(t, line, line.replace("  # budget_ms", "  budget_ms", 1)),
    "words in another script elsewhere": lambda t, line: t + "# заметка: 起動時間\n",
}


@pytest.mark.parametrize("edit", list(EDITS))
def test_a_press_rewrites_that_one_line_and_every_other_byte_stays(project, edit):
    path = written(project)
    line = project_setup.limit_line(None)
    path.write_bytes(EDITS[edit](path.read_text(encoding="utf-8"), line).encode("utf-8"))
    before = path.read_bytes()
    site, _ = served(project)
    page = page_of(site)
    held = re.search(r"  (?:# )?budget_ms: [0-9]+  # [^\r\n]*", before.decode("utf-8")).group(0)
    check(f"{edit}: the page offers the field", page.all("form", id=FORM), setting(page, "Time limit").text)
    check(f"{edit}: taken", set_limit(site, "1.5", page).status == 303)
    after = path.read_bytes()
    wanted = before.replace(held.encode(), project_setup.limit_line(1500).encode(), 1)
    check(f"{edit}: the line's words replaced, every other byte as it was", after == wanted,
          (before[-400:], after[-400:]))
    check(f"{edit}: the config loads, with the limit and everything else as it read before",
          Config.load(path).scenario_budget_ms == 1500)


# --- where the page declines -------------------------------------------------------------------------

def _replaced(old: str, new: str):
    def edit(project: Project, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        check("the edit applies", old in text, old)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return edit


def _header_gone(project: Project, path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(project_setup.HEADER, "", 1), encoding="utf-8")


def _by_hand(project: Project, path: Path) -> None:
    path.write_text(LAUNCH, encoding="utf-8")


def _several(project: Project, path: Path) -> None:
    path.write_text(project_setup.HEADER + SEVERAL.replace("scenarios:\n", "scenarios:\n  # budget_ms: 2000\n", 1),
                    encoding="utf-8")


def _local(project: Project, path: Path) -> None:
    (project.root / "local.yml").write_text("scenario:\n  budget_ms: 900\n", encoding="utf-8")


def _link(project: Project, path: Path) -> None:
    other = project.root / "shared.yml"
    other.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(other)


def _hard_link(project: Project, path: Path) -> None:
    os.link(path, project.root / "shared.yml")


def _bom(project: Project, path: Path) -> None:
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _in_a_string(project: Project, path: Path) -> None:
    """The page's line as part of a quoted value, with a limit of the same number set by hand below it:
    YAML reads that limit, and rewriting the line would change the name instead."""
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(LINE, f'  _note: "set by hand,\n{LINE}\n  "\n  budget_ms:    2000', 1), encoding="utf-8")
    check("a config that loads", Config.load(path).scenario_budget_ms == 2000)


def _moved(project: Project, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    line = project_setup.limit_line(2000)
    text = text.replace(line + "\n", "", 1).replace("  mode: launch\n", f"  mode: launch\n{line}\n", 1)
    path.write_text(text, encoding="utf-8")


def _gone(project: Project, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(project_setup.limit_line(2000) + "\n", "", 1), encoding="utf-8")


def _copied(project: Project, path: Path) -> None:
    line = project_setup.limit_line(2000)
    path.write_text(path.read_text(encoding="utf-8").replace(line, f"{line}\n{line}", 1), encoding="utf-8")


def _twice(project: Project, path: Path) -> None:
    # The page's line turned off, as its comment says, and a limit of one's
    # own below it. The same key written twice does not load at all (#582).
    line = project_setup.limit_line(2000)
    off = project_setup.limit_line(None)
    path.write_text(path.read_text(encoding="utf-8").replace(line, f"{off}\n  budget_ms: 3000", 1), encoding="utf-8")


def _broken(project: Project, path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "runner: [\n", encoding="utf-8")


LINE = project_setup.limit_line(2000)
DECLINES = {
    "a word added to the line": (_replaced(LINE, LINE + " (ours)"), "edited", "was changed by hand"),
    "a comment of one's own on it": (_replaced(LINE, "  budget_ms: 2000  # mine"), "edited", "was changed by hand"),
    "its spacing changed": (_replaced(LINE, LINE.replace("budget_ms: ", "budget_ms:  ")), "edited", "was changed by hand"),
    "a space after it": (_replaced(LINE, LINE + " "), "edited", "was changed by hand"),
    "a number that is no whole one": (_replaced(LINE, LINE.replace("2000", "2000.5")), "edited", "was changed by hand"),
    "a number with a leading zero, which YAML reads another way": (_replaced(LINE, LINE.replace("2000", "02000")), "edited",
                                                                   "was changed by hand"),
    # The same key twice in one block: the config does not load (#582).
    "the line twice": (_copied, "broken", "does not load"),
    "the line inside a quoted value, a limit set below it": (_in_a_string, "edited", "was changed by hand"),
    "the line removed": (_gone, "removed", "is no longer in echolot.yml"),
    "the line moved to another block": (_moved, "moved", "is no longer under scenario:"),
    "another line sets the limit too": (_twice, "twice", "that one counts"),
    "the page's first lines removed": (_header_gone, "foreign", "The page did not write this echolot.yml"),
    "a config written by hand": (_by_hand, "foreign", "The page did not write this echolot.yml"),
    "several scenarios": (_several, "several", "names several scenarios"),
    "a limit in local.yml": (_local, "local", "local.yml, beside echolot.yml, sets the limit too"),
    "a link to another file": (_link, "link", "is a link to another file"),
    "a second name for the file": (_hard_link, "link", "has another name as well"),
    "a mark an editor put before the first line": (_bom, "foreign", "The page did not write this echolot.yml"),
    "a file that does not load": (_broken, "broken", "does not load"),
}


@pytest.mark.parametrize("name", list(DECLINES))
def test_where_the_page_declines_it_says_why_and_writes_nothing(project, name):
    edit, reason, words = DECLINES[name]
    path = written(project, "2")
    project.traces().report(data=known())
    edit(project, path)
    if reason == "several":
        # The report of the scenario the page is about, where `analyze` puts it.
        project.report(".echolot/out/coldStart", data=known())
    found = time_limit.reading(project.root, str(path))
    check(f"{name}: declined, for its reason", found is not None and not found["can"] and found["reason"] == reason, found)
    site, _ = served(project)
    before = files(project.root)
    if reason != "broken":
        page = page_of(site)
        check(f"{name}: no field on the start page", not page.all("form", id=FORM))
        row = setting(page, "Time limit")
        check(f"{name}: the row says why, then the line to edit as before", words in row.one("p", id=f"{limit_page.ID}-no").text
              and re.search(r"(edit that line|A line) under scenarios?[.\w]*: in echolot\.yml", row.text), row.text)
        report = page_of(site, "report")
        quiet = report.one("p", id=f"{limit_page.ID}-no")
        check(f"{name}: beside the verdict, one quiet sentence with the line to edit", words in quiet.text
              and not report.all("form", id=FORM) and ("budget_ms" in quiet.text or "local.yml" in quiet.text), quiet.text)
    reply = blind(site, path)
    check(f"{name}: a press is refused in words", reply.status == 409 and "Nothing was written" in reply.body
          and read(reply.body).one("h1").text == "The limit was not changed", reply.body[:400])
    check(f"{name}: nothing was written", files(project.root) == before)


def test_a_config_named_by_hand_is_not_the_one_the_page_wrote(project):
    path = written(project, "2")
    other = project.root / "other.yml"
    other.write_bytes(path.read_bytes())
    site = ui.Site(project=project.root, config=str(other), src=site_of(project).src, token="t", port=4321)
    found = time_limit.reading(project.root, str(other))
    check("a server started with -c reads another file, and the page changes none", not found["can"]
          and found["reason"] == "other" and "This page reads other.yml" in found["why"].replace("`", ""), found)
    before = files(project.root)
    check("refused", blind(site, other).status == 409 and files(project.root) == before)


@pytest.mark.parametrize("typed", ["fast", "0", "0.05", "61", "100", "-1", "+2", "1e3", "2.5s", "1.2345", "1 000",
                                    "2.5.1", "0x10", "٣", ".5", "5.", "2\nscenario: x"])
def test_a_limit_that_is_no_number_or_out_of_range_writes_nothing(project, typed):
    written(project, "2")
    site, _ = served(project)
    before = files(project.root)
    reply = set_limit(site, typed)
    check("refused, on a page that says what a limit is", reply.status == 400
          and "a number of seconds from 0.1 to 60, like 2 or 2.5" in read(reply.body).one("p", "lead").text, reply.body[:300])
    check("nothing was written", files(project.root) == before)


@pytest.mark.parametrize("fields", [{"seen": "", "rest": ""}, {"seen": "x" * 64}, {"rest": "../echolot.yml"},
                                    {"seen": "A" * 64}, {"seen": "0" * 63}])
def test_a_press_that_does_not_say_which_file_it_saw_writes_nothing(project, fields):
    written(project)
    site, _ = served(project)
    before = files(project.root)
    reply = set_limit(site, "2", **fields)
    check("refused", reply.status == 400 and read(reply.body).one("h1").text == "This press cannot be read", reply.status)
    check("nothing was written", files(project.root) == before)


# --- between the page and the press ----------------------------------------------------------------

def test_a_file_that_changed_between_the_page_and_the_press_is_left_as_it_is(project):
    path = written(project)
    site, _ = served(project)
    page = page_of(site)
    path.write_text(path.read_text(encoding="utf-8") + "# a note somebody wrote meanwhile\n", encoding="utf-8")
    before = files(project.root)
    reply = set_limit(site, "2", page)
    check("refused in words", reply.status == 409 and "changed after this page was drawn" in reply.body
          and "Nothing was written" in reply.body, reply.body[:400])
    check("the file is as the person left it", files(project.root) == before)
    check("a page drawn now takes the press", set_limit(site, "2").status == 303
          and Config.load(path).scenario_budget_ms == 2000)


def test_a_limit_changed_from_another_tab_meanwhile_is_not_written_over(project):
    path = written(project)
    site, _ = served(project)
    first, second = page_of(site), page_of(site)
    check("the first tab sets 2 s", set_limit(site, "2", first).status == 303)
    after = files(project.root)
    reply = set_limit(site, "3", second)
    check("the second, drawn before, is refused", reply.status == 409 and "changed after this page was drawn" in reply.body)
    check("and 2 s stays", files(project.root) == after and Config.load(path).scenario_budget_ms == 2000)


def test_the_same_press_sent_twice_is_one_press(project):
    """A double click where no script guards it: the second finds the file the first wrote."""
    path = written(project)
    site, _ = served(project)
    page = page_of(site)
    check("taken", set_limit(site, "2", page).status == 303)
    after = files(project.root)
    reply = set_limit(site, "2", page)
    check("the second is answered as the first was, and writes nothing", reply.status == 303 and files(project.root) == after)
    check("the same number pressed again from a page drawn now writes nothing either",
          set_limit(site, "2").status == 303 and files(project.root) == after)
    check("the limit is the one asked for", Config.load(path).scenario_budget_ms == 2000)


def test_a_file_that_changes_in_the_instant_of_the_press_is_left_as_it_is(project, monkeypatch):
    path = written(project)
    site, _ = served(project)
    real = Config.load

    def meanwhile(where, *args, **kwargs):
        loaded = real(where, *args, **kwargs)
        if Path(where).name.endswith(".tmp"):
            path.write_text(path.read_text(encoding="utf-8") + "# meanwhile\n", encoding="utf-8")
        return loaded
    page = page_of(site)
    monkeypatch.setattr(time_limit.Config, "load", meanwhile)
    reply = set_limit(site, "2", page)
    check("refused", reply.status == 409 and "in the instant of the press" in reply.body, reply.body[:300])
    check("the file holds what was written meanwhile, and no half of anything",
          path.read_text(encoding="utf-8").endswith("# meanwhile\n") and the_line(path.read_text(encoding="utf-8"))
          == project_setup.limit_line(None))
    check("and nothing is left beside it", not list(project.root.glob(".echolot.yml.*")))


def test_a_file_that_cannot_be_written(project, monkeypatch):
    path = written(project)
    site, _ = served(project)
    before = path.read_bytes()

    def refuses(src, dst):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(time_limit.os, "replace", refuses)
    reply = set_limit(site, "2")
    check("said, and nothing changed", reply.status == 500 and "Permission denied" in reply.body
          and path.read_bytes() == before and not list(project.root.glob(".echolot.yml.*")), reply.body[:300])


# --- the request itself ------------------------------------------------------------------------------

def test_a_press_writes_that_one_file_and_nothing_else(project):
    written(project)
    project.traces().report(data=known())
    site, _ = served(project)
    before = files(project.root)
    check("taken", set_limit(site, "2").status == 303)
    after = files(project.root)
    check("echolot.yml changed, and no other file is new, gone or changed",
          {n for n in set(before) | set(after) if before.get(n) != after.get(n)} == {CONFIG}, sorted(set(after) ^ set(before)))


def test_nothing_from_the_form_is_a_path_or_a_piece_of_yaml(project):
    path = written(project)
    site, _ = served(project)
    reply = set_limit(site, "2", back="../../elsewhere", config="/tmp/x.yml", path="other.yml",
                      scenario="x: 1", budget_ms="9999")
    check("taken, and a page name it does not know sends the browser to the start",
          reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/#{limit_page.ID}")
    check("one file changed, by one number", Config.load(path).scenario_budget_ms == 2000
          and not (project.root / "other.yml").exists() and "x: 1" not in path.read_text(encoding="utf-8"))


def test_the_request_is_refused_as_every_other_press_is(project):
    path = written(project)
    with running(project) as server:
        site = server.site
        before = files(project.root)
        body = ("limit=2&seen=" + "0" * 64 + "&rest=" + "0" * 64).encode()
        own = {"Origin": site.origin}
        for name, where, how, expected in (
                ("no token", "/limit", own, 403),
                ("a wrong token", "/nope/limit", own, 403),
                ("no origin", f"/{site.token}/limit", {}, 403),
                ("another origin", f"/{site.token}/limit", {"Origin": "https://evil.example"}, 403),
                ("the origin `null`", f"/{site.token}/limit", {"Origin": "null"}, 403),
                ("its own origin, sent from another site", f"/{site.token}/limit", {**own, "Sec_Fetch_Site": "cross-site"}, 403),
                ("another name for this machine", f"/{site.token}/limit", {**own, "host": f"localhost:{site.port}"}, 403)):
            how = dict(how)
            host = how.pop("host", "")
            status, _, said = send(server, where, body, host=host, **how)
            check(f"{name}: {expected}", status == expected, (status, said[:120]))
            check(f"{name}: told nothing", "<" not in said and site.token not in said and "echolot.yml" not in said, said)
        check("nothing written", files(project.root) == before)
        status, _, said = send(server, f"/{site.token}/limit", b"limit=2&pad=" + b"x" * ui.BODY_CAP, **own)
        check("a body over the cap is refused unread", status == 413 and files(project.root) == before)
        status, _, _ = send(server, f"/{site.token}/limit", b"limit=\xff", **own)
        check("bytes that are no text", status == 400 and files(project.root) == before)
        page = read(get(site).body)
        fields = sent_by(the_form(page))
        status, headers, _ = send(server, f"/{site.token}/limit",
                                  "&".join(f"{k}={v}" for k, v in {**fields, "limit": "2"}.items()).encode(),
                                  **own, Sec_Fetch_Site="same-origin")
        check("and the server's own page is taken", status == 303 and headers["Location"] == f"/{site.token}/#{limit_page.ID}"
              and Config.load(path).scenario_budget_ms == 2000, (status, headers))
    site = site_of(project)
    reply = get(site, "limit")
    check("a read of its address changes nothing", reply.status == 405 and dict(reply.headers)["Allow"] == "POST")


# --- the report is the old one until the recordings are read again -----------------------------------

def no_limit_report() -> dict:
    return plain.annotate(make([], duration=1920.0, budget=None))


def test_after_a_change_the_report_says_it_is_the_old_one_and_reads_the_recordings_again(project, adb):
    adb.listing = ""        # no phone at all
    written(project)
    project.traces(n=3).report(data=no_limit_report())
    site, echolot = served(project)
    page = page_of(site, "report")
    check("beside the verdict: the field, and no word of an old report yet", the_form(page)
          and not page.all("div", id=f"{limit_page.ID}-was") and "No time limit is set" in page.one("h1").text
          + page.one("p", "lead").text)
    reply = set_limit(site, "1.5", page)
    check("taken, and the browser is sent back to the report", reply.status == 303
          and dict(reply.headers)["Location"] == f"/{site.token}/report#{limit_page.ID}")
    page = page_of(site, "report")
    was = page.one("div", id=f"{limit_page.ID}-was")
    check("the verdict is the report's, as it was: nothing is worked out in the page",
          "No time limit is set" in page.one("p", "lead").text, page.one("p", "lead").text)
    said = was.all("p")[0].text
    check("one sentence says why", said == "This report was made with no time limit. echolot reads the limit "
          "when it reads the recordings, so the report changes once they are read again.", said)
    go = was.one("form")
    check("and the press that reads them again goes to the button's own address", go.attrs["action"] == f"/{site.token}/check"
          and sent_by(go) == {"what": "read:coldStart", "again": "1"} and go.one("button").text == "Read the recordings again"
          and "disabled" not in go.one("button").attrs)
    check("with what it does, and that the phone is not needed", go.one("p").text
          == "The 3 recordings that are here are read again: about 10 seconds. The phone is not needed.", go.one("p").text)
    check("the start page says the same under All settings", setting(page_of(site), "Time limit").all(
        "div", id=f"{limit_page.ID}-was"))
    check("pressed", press(site, "check", sent_by(go)).status == 303)
    check("analyze alone, over the recordings that are here, with no comparison: they are the same recordings",
          [r[0] for r in echolot.ran] == ["analyze"] and len([a for a in echolot.ran[0] if a.endswith(".perfetto-trace")]) == 3
          and not (project.root / ".echolot" / "out" / "before.json").exists(), echolot.ran)
    check("the phone was asked for nothing but its list", all(a[:2] == ["adb", "devices"] for a in adb.asked))


def test_a_report_made_with_the_limit_in_force_says_nothing_more(project):
    written(project, "0.5")
    project.traces().report(data=known())       # made against 0.5 s
    page = page_of(served(project)[0], "report")
    check("the field, with the limit, and no callout", the_form(page)
          and the_form(page).one("input", name="limit").attrs["value"] == "0.5"
          and not page.all("div", id=f"{limit_page.ID}-was"))


def test_with_no_recordings_here_the_next_check_holds_the_launch_against_the_limit(project):
    written(project, "2")
    project.report(data=no_limit_report())
    page = page_of(served(project)[0], "report")
    was = page.one("div", id=f"{limit_page.ID}-was")
    check("said, with no button", "the next check holds the launch against the limit" in was.text and not was.all("form"),
          was.text)


def test_while_a_check_runs_the_recordings_are_not_read_again(project):
    written(project, "2")
    project.traces().report(data=no_limit_report())
    a_run(project)
    page = page_of(served(project)[0], "report")
    button = page.one("div", id=f"{limit_page.ID}-was").one("button")
    check("the button is there, disabled, and says why", "disabled" in button.attrs
          and "a check is running" in page.one("div", id=f"{limit_page.ID}-was").one("p", "lim-hint").text)


def test_reading_again_where_nothing_is_here_is_refused(project):
    written(project, "2")
    project.report(data=no_limit_report())
    site, echolot = served(project)
    reply = press(site, "check", {"what": "read:coldStart", "again": "1"})
    check("refused, and nothing ran", reply.status == 409 and "wait to be read" in reply.body and not echolot.ran,
          reply.body[:300])


def test_the_new_limit_is_the_one_analyze_reads_in_a_terminal(project, tmp_path):
    """`echolot analyze` on the config as the press left it: the report holds the launch against the new limit."""
    path = written(project)
    site, _ = served(project)
    check("taken", set_limit(site, "1.5").status == 303)
    trace = project.root / ".echolot" / "traces" / "coldStart_iter000.perfetto-trace"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_bytes(fixture.build(fully_drawn="app"))
    said = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        code = main_mod.main(["analyze", str(trace), "-c", str(path), "-o", str(tmp_path / "out")])
    check("analyze: exit 0", code == 0, said.getvalue()[-800:])
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    check("the report carries the limit, and its verdict is the launch against it",
          report["budget_ms"] == 1500 and report["verdict"]["budget_ms"] == 1500
          and "You allowed 1.5 s" in report["verdict"]["text"], report.get("verdict"))


# --- the page itself ---------------------------------------------------------------------------------

def test_a_report_written_to_disk_has_no_field(project):
    html = report_page.render(known())
    check("no field, no form, no word of the limit's part", limit_page.ID not in html and "<form" not in html
          and kit.asset("limit.css") not in html)


def test_with_script_off_the_field_is_a_form(project):
    written(project)
    project.traces().report(data=no_limit_report())
    site, _ = served(project)
    for where in ("", "report"):
        form = the_form(page_of(site, where))
        check(f"/{where}: a form of the browser's own", form.attrs["method"] == "post"
              and form.attrs["action"] == f"/{site.token}/limit" and form.one("button").attrs["type"] == "submit"
              and form.one("input", name="limit").attrs["type"] == "text")
        needs = [n for n in form.walk() if "needs-js" in (n.attrs.get("class") or "").split()]
        check(f"/{where}: nothing in it needs script", not needs)


def test_all_settings_says_the_limit_is_the_one_thing_it_changes(project):
    written(project)
    page = page_of(served(project)[0])
    intro = page.one("details", id="settings").all("p", "small")[0].text
    check("the fold's first sentence names the limit and its line", "but the number of launches" in intro
          and "and the time limit, in the one line of echolot.yml the page wrote for it" in intro, intro)


def test_each_state_is_one_page_with_ids_of_its_own(project):
    written(project)
    project.traces().report(data=no_limit_report())
    site, _ = served(project)
    check("set", set_limit(site, "2").status == 303)
    for where in ("", "report"):
        body = get(site, where).body
        page = read(body)
        ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs]
        check(f"/{where}: no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
        mine = page.one(None, id=limit_page.ID)
        primary = [n for n in page.walk() if "primary" in (n.attrs.get("class") or "").split() and "disabled" not in n.attrs]
        ours = [n for n in mine.walk() if "primary" in (n.attrs.get("class") or "").split() and "disabled" not in n.attrs]
        if where:
            check("/report: reading again is the one next step that can be taken", len(primary) == 1 and ours == primary,
                  [n.text for n in primary])
        else:
            check("/: the start page keeps its own next step: the limit's part adds none", not ours, [n.text for n in ours])
        check(f"/{where}: its own styles and script are written in, once", body.count(kit.asset("limit.css")) == 1
              and body.count(kit.asset("limit.js")) == 1)


def test_reading_writes_nothing_in_any_state_of_the_limit(project, tmp_path_factory):
    for name, build in {"no limit": lambda p: written(p), "a limit": lambda p: written(p, "2"),
                        "declined": lambda p: _by_hand(p, written(p, "2")),
                        "behind": lambda p: (written(p, "2"), p.traces().report(data=no_limit_report()))}.items():
        root = tmp_path_factory.mktemp("state")
        os.chdir(root)
        build(Project(root))
        site = site_of(Project(root))
        before = files(root)
        for path in ("", "report"):
            get(site, path)
        check(f"{name}: every file is as it was, and none is new", files(root) == before)


def test_without_the_address_of_the_limit_the_pages_are_as_they_were(project, monkeypatch):
    written(project)
    project.traces().report(data=no_limit_report())
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if r.name != "limit"])
    site, _ = served(project)
    page = page_of(site)
    check("no field, and the row says which line to edit", not page.all("form", id=FORM)
          and "A line under scenario: in echolot.yml sets one" in setting(page, "Time limit").text)
    check("nothing beside the verdict", not page_of(site, "report").all("section", id=limit_page.ID))
    check("and the fold's sentence is as it was", "and the time limit" not in page.one("details", id="settings").text)


def test_the_limits_own_styles_keep_to_the_tokens():
    css = kit.asset("limit.css")
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    check("no colour of its own", not literal.search(css), literal.findall(css))
    check("nothing is fetched by it", not any(word in css for word in ("url(", "@import", "@font-face")))
    check("the tokens are the shared ones", not _blocks(css, ":root {"))
    mine = set(re.findall(r"^\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S), flags=re.M))
    shared = "".join(kit.asset(n) for n in ("base.css", "start.css", "table.css", "picks.css", "report.css", "answer.css",
                                             "setup.css", "compare.css"))
    theirs = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", shared, flags=re.S)))
    check("a class it names by itself is no other sheet's", not mine & theirs, sorted(mine & theirs))
    check("and its files are inside the package", all((PAGES / n).is_file() for n in limit_page.CSS + limit_page.JS))
    check("the start page's own sheets are untouched by it", all(n not in start_page.CSS for n in limit_page.CSS))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the script")
def test_the_script_parses():
    done = subprocess.run(["node", "--check", str(PAGES / "limit.js")], capture_output=True, text=True)
    check("limit.js is JavaScript", done.returncode == 0, done.stderr)


def test_the_script_asks_no_server_anything_and_opens_the_fold_alone():
    script = kit.asset("limit.js")
    check("nothing is fetched, sent or loaded", not [word for word in REACHING_OUT if word in script])
    check("it reads the address and opens a fold, nothing more", "location.hash" in script and ".open = true" in script
          and "submit" not in script)
