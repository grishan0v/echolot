#!/usr/bin/env python3
"""Analyse trace files: `echolot ui` reads recordings from a folder elsewhere (#479).

What is pinned here:

  - the start page asks for a folder under the button, where the button is;
  - a look says what the folder holds before anything is read: the
    recordings by what their names say (`collect`'s, a macrobenchmark's,
    names that say nothing), how many, and names that repeat; and each state
    where there is nothing to read: nothing typed, no such folder, a file
    that is no recording, a folder with none in it but some in the folders
    inside it;
  - a named folder is listed, never opened, and what a form names is held
    to a path: no control character, not too long, `~` and the project's
    folder as a person means them;
  - Read them runs `analyze` alone over the recordings chosen, with the
    window of the scenario chosen, into a report of its own: the scenario's
    report stays as it was, and the start page shows the run and the report;
  - what is refused: a press while a check runs, while an investigation is
    open, of recordings that went, of a scenario the config does not name;
    and the address of a press, read, starts nothing.

adb is the double of `tests/test_ui.py`; `analyze` is a double that writes a
report where `-o` says, run by the real job in this process.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import elsewhere, job, ui  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import known, read  # noqa: E402
from tests.test_ui import SEVERAL, Adb, Project, get, site_of  # noqa: E402
from tests.test_ui_check import Echolot, Ran, a_run, page_of, press  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "app-project"
    root.mkdir()
    return Project(root)


class Reads(Echolot):
    """`analyze` as the job starts it: a report where `-o` says, and the
    command kept for the test to look at."""

    def analyze(self, args, out, pid) -> Ran:
        if self.fail == "analyze":
            out.write(f"error: {self.says}\n".encode())
            return Ran(2, pid)
        folder = Path(self._named(args, "-o"))
        folder.mkdir(parents=True, exist_ok=True)
        data = known()
        data["traces"] = [a for a in args if a.endswith(elsewhere.SUFFIXES)]
        data["scenario"] = self._named(args, "--scenario") or "coldStart"
        (folder / "report.json").write_text(json.dumps(data), encoding="utf-8")
        out.write(f"→ {folder / 'report.json'}\n".encode())
        return Ran(0, pid)


def serving(project: Project, **how):
    site = site_of(project)
    echolot = Reads(project.root, **how)
    site.spawn = lambda root, name, plan: job.run(root, name, plan, popen=echolot, signals=False)
    return site, echolot


def recordings(folder: Path, *names: str, size: int = 1) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x" * size)
    return folder


def look(site, folder) -> tuple[int, object]:
    reply = press(site, "elsewhere/look", {"folder": str(folder)})
    return reply.status, read(reply.body) if reply.body else None


def lead(page) -> str:
    return page.one("p", "lead").text


# --- the start page ---------------------------------------------------------------------------

def test_the_start_page_asks_for_a_folder_under_the_button(project):
    site = site_of(project.ready())
    page = page_of(site)
    fold = page.one("div", id="elsewhere")
    check("a fold under the button", fold.one("summary").text == "Analyse trace files", fold.text)
    form = fold.one("form")
    check("a form of the server's own that looks in the folder typed",
          form.attrs["action"] == f"/{site.token}/elsewhere/look" and form.attrs["method"] == "post"
          and form.one("input", name="folder").attrs["type"] == "text", form.attrs)
    check("which says nothing is read by looking", "before anything is read" in fold.text, fold.text)


def test_no_folder_is_asked_for_where_there_is_no_button(project):
    page = page_of(site_of(project.app().agents().doctor()))
    check("a project with no config has no way to read recordings yet", not page.all("div", id="elsewhere"))


def test_while_a_check_runs_the_folder_cannot_be_looked_in(project):
    project.ready()
    a_run(project, "collect", done=1)
    page = page_of(site_of(project))
    check("the field and its button wait", "disabled" in page.one("input", name="folder").attrs
          and "disabled" in page.one("div", id="elsewhere").one("button").attrs)


# --- a look ---------------------------------------------------------------------------------------

def test_recordings_named_by_collect_are_one_scenario(project):
    site = site_of(project.ready())
    folder = recordings(project.root.parent / "ci-artefact", *[f"coldStart_iter{i:03d}.perfetto-trace" for i in range(3)],
                        "notes.txt")
    status, page = look(site, folder)
    check("a page of its own", status == 200, status)
    check("it says how many, and of which scenario",
          lead(page) == f"echolot found 3 recordings of the scenario coldStart in {folder}.", lead(page))
    check("and what they are, and how long reading them takes", "3 recordings, 1 KB, the newest made" in page.text
          and "Named by echolot collect after the scenario coldStart of your config. Reading them takes about"
          in page.text, page.text)
    form = page.one("form", id="far-read")
    check("Read them sends the folder and the recordings chosen",
          form.attrs["action"] == f"/{site.token}/elsewhere/read"
          and form.one("input", name="folder").attrs["value"] == str(folder)
          and form.one("input", name="group").attrs["value"] == "coldStart"
          and form.one("input", name="scenario").attrs["value"] == "coldStart", form.attrs)
    button = form.one("button")
    check("the press can be made", button.text == "Read them" and "disabled" not in button.attrs)
    check("and says what it does not do", "nothing in the folder is written" in form.text, form.text)
    check("whose report it is", "They become a report of their own, in .echolot/out/elsewhere" in form.text)
    pre = page.one("pre", "yaml").text
    check("the command, as a person would type it",
          pre == f"echolot analyze {folder}/coldStart_iter*.perfetto-trace -c echolot.yml --html "
                 "-o .echolot/out/elsewhere", pre)


def test_a_macrobenchmarks_folder_is_one_kind_per_test_and_repeats_are_said(project):
    site = site_of(project.ready())
    folder = recordings(project.root.parent / "macro",
                        "StartupBenchmark_startup_iter000_2026-11-20-140212.perfetto-trace",
                        "StartupBenchmark_startup_iter001_2026-11-20-140230.perfetto-trace",
                        "StartupBenchmark_startup_iter000_2026-11-08-093001.perfetto-trace",
                        "ScrollBenchmark_scroll_iter000_2026-11-20-141000.trace",
                        "trace-from-a-colleague.pftrace")
    status, page = look(site, folder)
    check("several kinds: choose one", lead(page) == (
        f"echolot found 5 recordings in {folder}, of 3 kinds by their names. A report is of one kind: choose which "
        "to read."), lead(page))
    titles = [opt.one("span", "opt-t").text for opt in page.all("label", "opt")]
    check("a card each, the ones whose names say nothing last",
          set(titles) == {"StartupBenchmark_startup", "ScrollBenchmark_scroll", "Names that say no scenario"}
          and titles[-1] == "Names that say no scenario", titles)
    startup = next(o for o in page.all("label", "opt") if "StartupBenchmark_startup" in o.text)
    check("a test is said to be a macrobenchmark's", "A macrobenchmark's: the test StartupBenchmark_startup." in startup.text)
    check("and a repeated iteration is several sittings", "The names repeat (iteration 0 is there more than once)"
          in startup.text, startup.text)
    checked = [o.one("input").attrs["value"] for o in page.all("label", "opt") if "checked" in o.one("input").attrs]
    check("one is chosen", len(checked) == 1, checked)


def test_a_recording_named_by_itself_is_a_folder_of_one(project):
    site = site_of(project.ready())
    folder = recordings(project.root.parent / "sent", "from-a-colleague.perfetto-trace")
    status, page = look(site, folder / "from-a-colleague.perfetto-trace")
    check("one recording", lead(page) == f"echolot found one recording in {folder}/from-a-colleague.perfetto-trace.",
          lead(page))
    check("said as a sentence says it", "One recording, 1 KB" in page.text, page.text)
    check("whose name says no scenario", "Their names do not say which scenario they are." in page.text)
    check("and the config's scenario gives its window", page.one("input", name="scenario").attrs["value"] == "coldStart")


@pytest.mark.parametrize("named, says, status", [
    ("", "Name the folder the recordings are in", 400),
    ("missing", "There is no folder", 200),
    ("notes.txt", "is a file, and no recording: echolot reads files whose names end in .perfetto-trace, .pftrace or "
                  ".trace.", 200),
])
def test_where_there_is_nothing_to_read_the_page_says_why_and_offers_the_field_again(project, named, says, status):
    site = site_of(project.ready())
    base = recordings(project.root.parent / "here", "notes.txt")
    reply = press(site, "elsewhere/look", {"folder": str(base / named) if named else "  "})
    page = read(reply.body)
    check("the status", reply.status == status, reply.status)
    check("the sentence", says in lead(page), lead(page))
    check("no press that reads", not page.all("form", id="far-read"))
    check("and the field again, with what was typed", page.one("input", name="folder").attrs.get("value", "")
          == (str(base / named) if named else ""), page.one("input", name="folder").attrs)


def test_a_folder_with_sets_inside_offers_each(project):
    site = site_of(project.ready())
    top = project.root.parent / "traces"
    recordings(top / "coldStart-20261120-1402", *[f"coldStart_iter{i:03d}.perfetto-trace" for i in range(5)])
    recordings(top / "coldStart-20261121-0911", "coldStart_iter000.perfetto-trace")
    (top / "empty").mkdir()
    status, page = look(site, top)
    check("none at the top", lead(page).startswith(f"echolot found no recordings in {top}.")
          and lead(page).endswith("Folders inside it hold some: name the one to read."), lead(page))
    inside = page.one("ul", "far-inside")
    offered = [(li.one("button").text, li.one("input").attrs["value"], li.one("span").text) for li in inside.all("li")]
    check("each that holds recordings, as a press that looks in it", offered == [
        ("coldStart-20261120-1402", str(top / "coldStart-20261120-1402"), "5 recordings"),
        ("coldStart-20261121-0911", str(top / "coldStart-20261121-0911"), "1 recording")], offered)


def test_a_folder_is_listed_and_nothing_in_it_is_opened(project):
    if os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode")
    site = site_of(project.ready())
    folder = recordings(project.root.parent / "locked", "coldStart_iter000.perfetto-trace")
    locked = folder / "coldStart_iter000.perfetto-trace"
    locked.chmod(0)
    try:
        status, page = look(site, folder)
    finally:
        locked.chmod(0o644)
    check("a recording no one may read is listed all the same: its name and size, never its bytes",
          status == 200 and "found one recording of the scenario coldStart" in lead(page), lead(page))


def test_a_path_is_taken_as_a_person_means_it(project, monkeypatch):
    project.ready()
    home = project.root.parent / "home"
    monkeypatch.setenv("HOME", str(home))
    recordings(home / "Downloads" / "ci", "coldStart_iter000.perfetto-trace")
    recordings(project.root / "build-output", "coldStart_iter000.perfetto-trace")
    check("from the home folder", elsewhere.asked(project.root, {"folder": "~/Downloads/ci"}) == home / "Downloads" / "ci")
    check("in quotes a terminal would need", elsewhere.asked(project.root, {"folder": "'~/Downloads/ci'"})
          == home / "Downloads" / "ci")
    check("from the project's folder", elsewhere.asked(project.root, {"folder": "build-output"})
          == project.root / "build-output")
    check("said from the home folder", elsewhere.shown(project.root, home / "Downloads" / "ci") == "~/Downloads/ci")
    check("and from the project's", elsewhere.shown(project.root, project.root / "build-output") == "build-output")


@pytest.mark.parametrize("text", ["/tmp/a\nb", "/tmp/\x00x", "/" + "a" * elsewhere.PATH_CAP])
def test_what_is_no_path_is_refused(project, text):
    site = site_of(project.ready())
    reply = press(site, "elsewhere/look", {"folder": text})
    check("refused, and nothing is looked at", reply.status == 400 and "That is not a folder" in reply.body,
          (reply.status, reply.body[:300]))


def test_a_look_from_another_site_is_refused(project):
    site = site_of(project.ready())
    reply = press(site, "elsewhere/look", {"folder": "/"}, Origin="http://example.com")
    check("as any press is", reply.status == 403, reply.status)


@pytest.mark.parametrize("path", ["elsewhere/look", "elsewhere/read"])
def test_the_address_of_a_press_read_starts_nothing(project, path):
    site = site_of(project.ready())
    reply = get(site, path)
    check("it sends the browser to the start page", reply.status == 303
          and dict(reply.headers)["Location"] == f"/{site.token}/", (reply.status, reply.headers))


# --- Read them ------------------------------------------------------------------------------------

def test_read_them_makes_a_report_of_their_own_and_the_scenarios_stays(project):
    project.ready().report()
    mine = (project.root / ".echolot" / "out" / "report.json").read_bytes()
    site, echolot = serving(project)
    folder = recordings(project.root.parent / "ci", *[f"coldStart_iter{i:03d}.perfetto-trace" for i in range(4)])
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    check("started, the browser goes to the start page", reply.status == 303, (reply.status, reply.body[:400]))
    check("analyze alone, over the four, into a report of its own", echolot.ran == [[
        "analyze", *[str(folder / f"coldStart_iter{i:03d}.perfetto-trace") for i in range(4)],
        "-c", str(project.root / "echolot.yml"), "--html", "-o", str(project.root / ".echolot" / "out" / "elsewhere")]],
        echolot.ran)
    check("the scenario's own report is as it was",
          (project.root / ".echolot" / "out" / "report.json").read_bytes() == mine)
    page = page_of(site)
    end = page.one("p", id="run-end").text
    check("the start page says what came of it", end == (
        f"Done. The short version: 4 recordings from {folder}, read as a report of their own: your scenarios' "
        "reports stay as they were."), end)
    see = [a for a in page.all("a", "btn") if a.text == "See the report"]
    check("See the report opens the report they made", see and see[0].attrs["href"] == f"/{site.token}/elsewhere",
          [a.attrs for a in see])
    cards = [li for li in page.one("ul", "reps").all("li") if "Recordings from elsewhere" in li.text]
    check("and the list of reports has it, apart from the scenario's", len(cards) == 1
          and f"from 4 recordings in {folder}" in cards[0].text, [li.text for li in cards])
    report = get(site, "elsewhere")
    check("its page is drawn", report.status == 200 and "recordings from elsewhere" in read(report.body).one(
        "span", "crumb").text, report.status)


def test_several_scenarios_name_the_window_they_are_read_with(project):
    project.app().config(SEVERAL).agents().doctor()
    site, echolot = serving(project)
    folder = recordings(project.root.parent / "ci", "feedScroll_iter000.perfetto-trace")
    status, page = look(site, folder)
    chosen = [o.one("input").attrs["value"] for o in page.all("label", "opt") if "checked" in o.one("input").attrs]
    check("the scenario the names say is chosen", chosen == ["feedScroll"], chosen)
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "feedScroll", "scenario": "feedScroll"})
    check("started", reply.status == 303, reply.body[:300])
    check("with --scenario, the one chosen", echolot.ran[0][-5:-3] == ["--scenario", "feedScroll"], echolot.ran)


def test_read_them_is_held_against_the_folder_as_it_is_now(project):
    site, echolot = serving(project.ready())
    folder = recordings(project.root.parent / "ci", "coldStart_iter000.perfetto-trace")
    (folder / "coldStart_iter000.perfetto-trace").unlink()
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    check("recordings that went are not read", reply.status == 409 and "no recordings in" in reply.body,
          (reply.status, reply.body[:300]))
    recordings(folder, "other_iter000.perfetto-trace")
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    check("nor ones named otherwise in their place", reply.status == 409 and "no recordings named so" in reply.body,
          (reply.status, reply.body[:300]))
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "other", "scenario": "feedScroll"})
    check("a scenario the config does not name", reply.status == 400 and "No such scenario" in reply.body,
          (reply.status, reply.body[:300]))
    check("and nothing ran", echolot.ran == [], echolot.ran)


def test_one_thing_runs_at_a_time(project):
    project.ready()
    a_run(project, "collect", done=1)
    site, echolot = serving(project)
    folder = recordings(project.root.parent / "ci", "coldStart_iter000.perfetto-trace")
    status, page = look(site, folder)
    button = page.one("form", id="far-read").one("button")
    check("the look says why it waits", "disabled" in button.attrs
          and "is running" in page.one("p", "go-hint").text, page.one("p", "go-hint").text)
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    check("and a press is refused", reply.status == 409 and "A check is already running" in reply.body,
          (reply.status, reply.body[:300]))
    check("nothing ran", echolot.ran == [])


def test_an_open_investigation_keeps_recordings_from_being_read(project):
    project.ready().hunt(3, "why is it slow", "open", current=True)
    site, echolot = serving(project)
    folder = recordings(project.root.parent / "ci", "coldStart_iter000.perfetto-trace")
    reply = press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    check("the button's own reason", reply.status == 409 and "an investigation is open" in reply.body,
          (reply.status, reply.body[:300]))
    check("nothing ran", echolot.ran == [])


def test_a_read_that_fails_says_what_analyze_said(project):
    site, echolot = serving(project.ready(), fail="analyze", says="no trace in it reads")
    folder = recordings(project.root.parent / "ci", "coldStart_iter000.perfetto-trace")
    press(site, "elsewhere/read", {"folder": str(folder), "group": "coldStart", "scenario": "coldStart"})
    page = page_of(site)
    check("the button's own ending", page.one("p", id="run-end").text.startswith(
        "Reading the recordings did not finish."), page.one("p", id="run-end").text)
    check("and the command's words", "no trace in it reads" in page.one("pre", "printed").text)


def test_running_words_name_the_folder(project):
    project.ready()
    run = {"status": job.RUNNING, "what": {"do": "elsewhere", "folder": "~/ci"}, "steps": [{"id": "analyze"}]}
    from echolot import check as check_mod
    check("what is running, in a sentence", check_mod.running_words(run, None)
          == "The recordings from `~/ci` are being read.")


def test_the_report_of_its_own_says_where_it_came_from(project):
    project.ready()
    out = project.root / ".echolot" / "out" / "elsewhere"
    out.mkdir(parents=True)
    data = known()
    data["traces"] = [str(project.root.parent / "ci" / f"x_iter00{i}.perfetto-trace") for i in range(2)]
    data["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 120))
    (out / "report.json").write_text(json.dumps(data), encoding="utf-8")
    said = elsewhere.latest(project.root)
    check("when, from how many, from where", said["plain"].startswith(
        f"Made 2 minutes ago from 2 recordings in `{project.root.parent / 'ci'}`"), said["plain"])
    (out / "report.json").write_text("{", encoding="utf-8")
    check("one that does not read is said", elsewhere.latest(project.root)["verdict"] is None)
    reply = get(site_of(project), "elsewhere")
    check("and its page says so", reply.status == 404 and "does not read" in reply.body, reply.status)


def test_no_report_of_its_own_yet(project):
    reply = get(site_of(project.ready()), "elsewhere")
    check("a page that says how one comes", reply.status == 404 and "Analyse trace files" in reply.body, reply.status)
    check("the server is the one routes table", ui.find("GET", "elsewhere")[0].name == "elsewhere-report")
