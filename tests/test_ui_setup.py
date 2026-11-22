#!/usr/bin/env python3
"""`echolot ui` sets a project up: the first config and its time limit, at a press.

What is pinned here:

  - the start page of a project with no config: "What do you want to check?"
    with its one answer, whose app it is, the time limit, what a press
    writes, and **Set it up**;
  - what a press writes: `echolot.yml` as `scan` drafts it, with the limit
    when one was given, and what `echolot init` writes where echolot was
    never here; the list on the page is held to the files that appear;
  - that nothing is written over: a config that is there, or that appears
    between the page and the press, stops the press;
  - each reason a project cannot be set up from the page, in `scan`'s words;
  - a config with no scenario, a setup under way, one that ended badly, and
    what the page says before the first check;
  - what the server refuses for the new addresses, the same as for a check.

Nothing here needs a phone or the network or waits on a timer. adb is the
double of `tests/test_ui.py`. `echolot init` is the real command, run by the
real job in this process without its self-check, which takes a minute: the
self-check is a line in the run log, as in the other tests of the page.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import codex, hosts, job, ready, recorder, state, ui  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import project_setup as setup_mod  # noqa: E402
from echolot import scan as scan_mod  # noqa: E402
from echolot.config import LAUNCH as LAUNCH_WINDOW  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.pages import kit, setup_page, start_page  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import _blocks, read  # noqa: E402
from tests.test_scan_benchmark_kinds import LAUNCH as STARTUP_CLASS  # noqa: E402
from tests.test_scan_benchmark_kinds import SECTION as SECTION_CLASS  # noqa: E402
from tests.test_scan_benchmark_kinds import project as benchmark_project  # noqa: E402
from tests.test_ui import (  # noqa: E402
    APP, BENCHMARK, GRADLE, HEAD, LAUNCH, PAGES, SEVERAL, Adb, Project, get, lines, running, said_by, setting, site_of,
)
from tests.test_ui_check import Ran, files, press, send  # noqa: E402

# A package the build script sets through a constant: `scan` cannot read it.
UNREAD = GRADLE.replace('applicationId = "com.example.app"', "applicationId = Versions.APPLICATION_ID")


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


# --- `echolot init`, as the job starts it ------------------------------------------------------

class Init:
    """`python -m echolot init --into <project> --no-input` as the job starts
    it: the real command, without the self-check. What the self-check found
    is the line it leaves in the run log, written here as it would be."""

    def __init__(self, project: Project, *, fails: bool = False):
        self.project, self.fails = project, fails
        self.ran: list[list[str]] = []

    def __call__(self, argv, **kw):
        root = self.project.root
        check("the same echolot that serves the page, by its module", argv[:3] == [sys.executable, "-m", "echolot"], argv)
        check("in the project's folder", Path(kw["cwd"]) == root, kw["cwd"])
        check("`init` for this project, asking nobody", argv[3:] == ["init", "--into", str(root), "--no-input"], argv)
        self.ran.append(list(argv[3:]))
        said = io.StringIO()
        with recorder.isolated(), contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = main_mod.main([*argv[3:], "--no-doctor"])
        if self.fails:
            said.write("self-check: 1 of 182 FAILED\n  ✗ the launch window: expected 1920.0, got 0.0\n")
            self.project.doctor(failed=("the launch window",))
            code = 1
        else:
            self.project.doctor()
        kw["stdout"].write(said.getvalue().encode())
        return Ran(code, 6001)


def serving(project: Project, **how):
    """The site, with `echolot init` run to its end before a press is answered."""
    site = site_of(project)
    init = Init(project, **how)
    site.spawn = lambda root, name, plan: job.run(root, name, plan, popen=init, signals=False)
    return site, init


def set_up(site: ui.Site, **fields: str) -> ui.Reply:
    return press(site, "setup", {"what": "launch", **fields})


def page_of(site: ui.Site):
    reply = get(site)
    check("the start page is drawn", reply.status == 200, reply.body[:300])
    return read(reply.body)


def listed(page) -> list[str]:
    """Every file the fold says a press writes."""
    return [li.one("code").text for li in page.one("details", id="setup-writes").one("ol", "writes").all("li")
            if li.parent.attrs.get("class") == "files"]


def changed(before: dict[str, bytes], root: Path) -> set[str]:
    after = files(root)
    return {name for name in after if before.get(name) != after[name]}


def a_setup(project: Project, *, end: str | None = None, pid: int | None = None, ago: float = 20.0) -> dict:
    """The files of a setup as a press leaves them: the config written, and
    `echolot init` running under this process's pid unless `end` says how it
    ended or `pid` names a process that is gone."""
    now = time.time()
    status = {"failed": "failed", "done": "done"}.get(end or "", "running")
    step = {"id": "init", "argv": ["echolot", "init"], "shown": "echolot init", "status": status,
            "started": now - ago, "log": ".echolot/log/setup.init.log", "pid": 7001}
    if end:
        step.update(finished=now - 1, exit=0 if end == "done" else 1)
    run = {"job": "setup", "pid": pid or os.getpid(), "started": now - ago, "finished": (now - 1) if end else None,
           "what": {"config": "echolot.yml"}, "steps": [step]}
    if end:
        run["end"] = end
    log = project.root / ".echolot" / "log"
    log.mkdir(parents=True, exist_ok=True)
    (log / "setup.json").write_text(json.dumps(run), encoding="utf-8")
    return run


# --- a project echolot has never seen ----------------------------------------------------------

def test_a_project_with_no_config_gets_the_question_and_one_button(project, adb):
    adb.listing = ""
    site, init = serving(project.app())
    page = page_of(site)
    form = page.one("form", id="setup-form")
    check("the question", page.one("h2", id="q-what").text == "What do you want to check?")
    check("a form of the page, sent to the server's own address", form.attrs["method"] == "post"
          and form.attrs["action"] == f"/{site.token}/setup")
    cards = form.one("div", "opts").all("label", "opt")
    check("one answer, a card that is a radio, chosen", len(cards) == 1 and cards[0].one("input").attrs == {
        "type": "radio", "name": "what", "value": "launch", "checked": None}, [c.one("input").attrs for c in cards])
    check("how the app opens, in plain words", cards[0].one("span", "opt-t").text == "How the app opens"
          and "From the tap on its icon until the first screen is there" in cards[0].one("span", "opt-d").text)
    app = page.one("div", id="setup-app").text
    check("whose app it is, by the two names a person knows: the package and the build", app.startswith("Is this your app?")
          and f"echolot found {APP} in app/build.gradle.kts, and will measure its benchmark build" in app, app)
    go = form.one("button", id="setup-go")
    check("one button, the screen's next step", go.text == "Set it up" and go.attrs["type"] == "submit"
          and "primary" in go.attrs["class"] and "disabled" not in go.attrs)
    check("with what a press does beside it", page.one("p", id="setup-hint").text == (
        "Writes echolot.yml and echolot's own files into this project, then checks this computer: about a minute. "
        "Nothing in your code changes."))
    check("no button that has nothing to press yet, and no step to copy in its place",
          not page.all("button", id="check-go") and not page.all("section", aria_labelledby="next-h"))
    check("with no phone at hand Refresh is not the next step: the project is set up without one",
          lines(page)["The phone"][0] == "red" and page.one("a", "btn").text == "Refresh"
          and "primary" not in page.one("a", "btn").attrs["class"])
    check("nothing is written by drawing it", not init.ran and not (project.root / "echolot.yml").exists())


def test_anything_else_stays_the_agents_setup_in_one_line(project):
    other = page_of(site_of(project.app())).one("div", id="setup-other")
    check("where echolot was never here it starts with init, to copy", "An agent sets that up" in other.text
          and other.one("code", id="setup-agent").text == "echolot init", other.text)
    form = page_of(site_of(project)).one("form", id="setup-form")
    order = [n.attrs.get("id") for n in form.walk() if n.attrs.get("id") in ("q-what", "limit", "setup-go", "setup-other")]
    check("it stands under the button: the page's own way comes first", order == ["q-what", "limit", "setup-go", "setup-other"], order)
    page = page_of(site_of(project.app().agents().doctor()))
    other = page.one("div", id="setup-other")
    check("then the agent's own way in, the one `next` names", other.one("code", id="setup-agent").text == "echolot guide setup"
          and "it learns from the code how to get there and where the wait begins and ends" in other.text, other.text)
    check("which the terminal says too", "echolot guide setup" in said_by([]).rsplit("\nnext", 1)[-1])
    copy = other.one("button", data_setup_copy="setup-agent")
    check("the button that copies it is script's", "needs-js" in copy.attrs["class"] and copy.attrs["type"] == "button")


def test_the_limit_is_a_sentence_with_a_field_in_it(project):
    page = page_of(site_of(project.app()))
    field = page.one("input", id="limit")
    check("a text field for a number with a point, sent with the form", field.attrs["type"] == "text"
          and field.attrs["inputmode"] == "decimal" and field.attrs["name"] == "limit" and "value" not in field.attrs
          and page.one("form", id="setup-form").all("input", id="limit"), field.attrs)
    check("the sentence around it", page.one("label", **{"for": "limit"}).text.split() == (
        "It should take no longer than seconds".split()))
    said = page.one("p", id=field.attrs["aria-describedby"]).text
    check("it is optional, and what it changes is said once, plainly", said == (
        "Optional, from 0.1 to 60. With a limit the report says whether the app opens fast enough. Without one it "
        "says how long it takes, and holds that against nothing."), said)
    check("no browser check stands in the way of the form: the server answers a wrong number in a sentence",
          "required" not in field.attrs and "pattern" not in field.attrs)


# --- what a press writes -----------------------------------------------------------------------

def test_the_fold_shows_the_config_as_it_will_be_written(project):
    site, _ = serving(project.app())
    page = page_of(site)
    fold = page.one("details", id="setup-writes")
    check("a fold of the browser's own, closed", fold.all("summary")[0].text == "What Set it up writes" and "open" not in fold.attrs)
    shown = fold.one("pre", "yaml").raw()
    drafted = "\n".join(scan_mod.skeleton(scan_mod.describe(project.root, devices=False)))
    check("the text is what scan drafts, every value with where it came from, under two lines that say who wrote it",
          shown.startswith("# Written by `echolot ui` from what `echolot scan` read in this repository.\n")
          and [row for row in shown.splitlines() if row and not row.lstrip().startswith("#")]
          == [row for row in drafted.splitlines() if row], shown[:300])
    check("no file of the person's is replaced, and that is said", "no file of yours is replaced" in fold.text
          and "An echolot.yml that is already there stops the press" in fold.text)
    check("and what the terminal would do instead", "The same as in a terminal: echolot init, then the config "
          "echolot scan proposes, saved as echolot.yml." in fold.text)
    check("a press writes that text", set_up(site).status == 303
          and (project.root / "echolot.yml").read_text(encoding="utf-8") == shown)


LAYOUTS = {
    "a bare folder": lambda root: None,
    "a git root": lambda root: (root / ".git").mkdir(),
    "a git root that ignores them already": lambda root: (
        (root / ".git").mkdir(), (root / ".gitignore").write_text("build/\n.echolot/\nlocal.yml\n", encoding="utf-8")),
    "a git root with a .gitignore of its own": lambda root: (
        (root / ".git").mkdir(), (root / ".gitignore").write_text("build/\n", encoding="utf-8")),
    "an AGENTS.md of the project's": lambda root: (root / "AGENTS.md").write_text("# Ours\n", encoding="utf-8"),
    "Cursor is used here": lambda root: (root / ".cursor").mkdir(),
    "Gemini is used here": lambda root: (root / ".gemini").mkdir(),
    "a GEMINI.md of the project's": lambda root: (root / "GEMINI.md").write_text("# Ours\n", encoding="utf-8"),
    "Codex is used here": lambda root: (root / ".codex").mkdir(),
    "instructions for Copilot of the project's": lambda root: (
        (root / ".github").mkdir(), (root / ".github" / "copilot-instructions.md").write_text("# Ours\n", encoding="utf-8")),
    "settings for Claude Code of the project's": lambda root: (
        (root / ".claude").mkdir(), (root / ".claude" / "settings.json").write_text('{"hooks": {}}\n', encoding="utf-8")),
}


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_the_page_lists_exactly_the_files_a_press_writes(project, layout):
    """Every file that is new or changed after a press is on the page before
    it, and the page names no other."""
    LAYOUTS[layout](project.app().root)
    site, init = serving(project)
    said = listed(page_of(site))
    check("no file is named twice", len(said) == len(set(said)), said)
    before = files(project.root)
    reply = set_up(site, limit="2")
    check("the press is taken", reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/", reply.body[:300])
    wrote = changed(before, project.root)
    check("the files that appeared or changed are the ones listed", wrote == set(said),
          (sorted(wrote - set(said)), sorted(set(said) - wrote)))
    check("no file that was there is gone", set(before) <= set(files(project.root)))
    check("init ran once, as the command it is", init.ran == [["init", "--into", str(project.root), "--no-input"]])


def test_a_file_of_the_projects_is_added_to_and_never_replaced(project):
    root = project.app().root
    (root / ".git").mkdir()
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Ours\n", encoding="utf-8")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text('{"hooks": {"ours": 1}}\n', encoding="utf-8")
    site, _ = serving(project)
    fold = page_of(site).one("details", id="setup-writes")
    notes = {li.one("code").text: li.text for li in fold.all("li") if li.parent.attrs.get("class") == "files"}
    check("the page says which files are the project's own and what is added to them",
          notes[".gitignore"] == ".gitignore yours already: two lines are added to it"
          and notes[".claude/settings.json"] == ".claude/settings.json yours already: one permission is added to it"
          and "AGENTS.md" not in notes, notes)
    check("taken", set_up(site).status == 303)
    check("what was in them is still there", (root / ".gitignore").read_text(encoding="utf-8").startswith("build/\n")
          and json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"] == {"ours": 1}
          and (root / "AGENTS.md").read_text(encoding="utf-8") == "# Ours\n")


def test_a_press_sets_the_project_up_and_the_page_is_the_buttons(project):
    site, init = serving(project.app())
    check("taken, and the browser is sent to the start page", set_up(site).status == 303)
    cfg = Config.load(project.root / "echolot.yml")
    check("the config it wrote loads: the launch, of this app, with no limit", cfg.scenario_window == LAUNCH_WINDOW
          and cfg.get("project.package") == APP and cfg.scenario_budget_ms is None and cfg.scenario_name == "coldStart")
    check("nothing in it is marked as confirmed by a person: the marks are scan's", cfg.confirmed() == {}
          and cfg.get("project._source") == "derived" and cfg.get("scenario.window._source") == "derived")
    check("echolot's own files are in", (project.root / ".claude" / "skills" / "echolot" / "SKILL.md").is_file() and init.ran)
    page = page_of(site)
    go = page.one("button", id="check-go")
    check("the page is the button's now", go.text == "Check the app" and "disabled" not in go.attrs
          and not page.all("form", id="setup-form") and not page.all("section", id="setup"), go.attrs)
    check("and every line is green", {tone for tone, _ in lines(page).values()} == {"green"}, lines(page))
    status = said_by(["status"])
    step = status.rsplit("\nnext", 1)[-1].strip()
    check("echolot in a terminal says the same next step the page shows", "echolot collect -c echolot.yml -n 5" in step
          and f"next: {step}".replace("`", "") in setting(page, "The next step").text, (step, setting(page, "The next step").text))


@pytest.mark.parametrize("typed, ms", [("2.5", 2500), ("2", 2000), ("0,5", 500), (" 3 ", 3000), ("0.1", 100), ("60", 60000),
                                        ("1.234", 1234), ("", None)])
def test_a_limit_is_written_as_the_scenarios_budget(project, typed, ms):
    site, _ = serving(project.app().agents().doctor())
    check("taken", set_up(site, limit=typed).status == 303)
    cfg = Config.load(project.root / "echolot.yml")
    check(f"{typed!r}: {ms} ms under the scenario", cfg.scenario_budget_ms == ms and cfg.get("scenario.budget_ms") == ms,
          cfg.get("scenario"))
    text = (project.root / "echolot.yml").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if "budget_ms" in row)
    row = setting(page_of(site), "Time limit")
    field = row.one("input", name="limit")
    if ms:
        check("the line says what it is, for whoever opens the file", line.startswith(f"  budget_ms: {ms}  # the time limit, ")
              and "change the number to change it" in line, line)
        check("and the page shows it afterwards, in the field that changes it (#485)", f"budget_ms: {ms}" in row.text
              and field.attrs.get("value") == f"{ms / 1000:g}" and "edit that line" not in row.text, (row.text, field.attrs))
    else:
        check("with none the line is there as a comment, ready to be switched on", line.startswith("  # budget_ms: 2000  # "), line)
        check("and the page says none is set, with the field that sets one (#485)", row.text.startswith("None is set.")
              and "value" not in field.attrs and "A line under scenario:" not in row.text, (row.text, field.attrs))


@pytest.mark.parametrize("typed", ["fast", "0", "0.05", "61", "100", "-1", "+2", "1e3", "2.5s", "1.2345", "1 000", "2.5.1",
                                    "0x10", "٣", ".5", "5."])
def test_a_limit_that_is_no_number_or_out_of_range_writes_nothing(project, typed):
    site, init = serving(project.app())
    before = files(project.root)
    reply = set_up(site, limit=typed)
    check("refused, on a page that says what a limit is", reply.status == 400
          and "a number of seconds from 0.1 to 60, like 2 or 2.5" in read(reply.body).one("p", "lead").text, reply.body[:300])
    check("with the way back", read(reply.body).one("a", "btn").attrs["href"] == f"/{site.token}/")
    check("nothing was written and nothing ran", files(project.root) == before and not init.ran)


@pytest.mark.parametrize("fields", [{}, {"what": ""}, {"what": "other"}, {"what": "record:coldStart"},
                                    {"what": "../echolot.yml"}, {"what": "launch\nscenario: x"}, {"what": "LAUNCH"}])
def test_a_choice_the_page_did_not_offer_writes_nothing(project, fields):
    site, init = serving(project.app())
    before = files(project.root)
    reply = press(site, "setup", fields)
    check("refused", reply.status == 400 and read(reply.body).one("h1").text == "This press cannot be read", reply.status)
    check("nothing was written and nothing ran", files(project.root) == before and not init.ran)


def test_nothing_from_the_form_reaches_the_file_but_one_number(project):
    site, _ = serving(project.app().agents().doctor())
    check("taken", press(site, "setup", {"what": "launch", "limit": "2", "name": "evil", "scenario": "x: 1",
                                         "path": "/tmp/elsewhere.yml", "package": "com.other"}).status == 303)
    written = {name for name in files(project.root) if not name.startswith(".echolot")}
    check("one file, under the one name", "echolot.yml" in written and not [n for n in written if n.endswith("elsewhere.yml")])
    cfg = Config.load(project.root / "echolot.yml")
    check("and it holds scan's draft and the number", cfg.get("project.package") == APP and cfg.scenario_name == "coldStart"
          and cfg.scenario_budget_ms == 2000)


# --- nothing is written over ---------------------------------------------------------------------

def test_a_config_that_appeared_between_the_page_and_the_press(project):
    site, init = serving(project.app())
    check("the page offers to set up", page_of(site).all("form", id="setup-form"))
    project.config(LAUNCH)
    before = files(project.root)
    reply = set_up(site, limit="2")
    page = read(reply.body)
    check("refused in words", reply.status == 409 and page.one("h1").text == "echolot.yml is already there"
          and "the page never writes over one" in page.one("p", "lead").text
          and "Nothing was written" in page.one("p", "lead").text, reply.body[:300])
    check("the file is as it was, and nothing else was written or run", files(project.root) == before and not init.ran)


def test_the_same_press_sent_twice_is_one_setup(project):
    """A double click where no script guards it: the second finds the file the first wrote."""
    site, init = serving(project.app().agents().doctor())
    check("taken", set_up(site, limit="2").status == 303)
    before = files(project.root)
    reply = set_up(site, limit="2")
    check("the second is answered as the first was, and writes nothing", reply.status == 303 and files(project.root) == before)
    reply = set_up(site, limit="3")
    check("a press that asks for another file is refused: the one that is there stays", reply.status == 409
          and "echolot.yml is already there" in reply.body and files(project.root) == before, reply.status)


def test_a_config_that_appears_in_the_instant_of_the_press(project, monkeypatch):
    """Between the last look and the write: the file is created or nothing is."""
    site, init = serving(project.app())
    real = setup_mod.config_text

    def meanwhile(drafted, limit_ms=None):
        (project.root / "echolot.yml").write_text("# somebody else's\n", encoding="utf-8")
        return real(drafted, limit_ms)
    monkeypatch.setattr(setup_mod, "config_text", meanwhile)
    reply = set_up(site)
    check("refused in words", reply.status == 409 and "appeared in this project while the page was open" in reply.body, reply.status)
    check("the other file stands, and init did not run",
          (project.root / "echolot.yml").read_text(encoding="utf-8") == "# somebody else's\n" and not init.ran)


def test_a_config_that_cannot_be_written(project, monkeypatch):
    site, init = serving(project.app())
    real = open

    def denied(path, mode="r", *args, **kw):
        if "x" in mode:
            raise PermissionError(13, "Permission denied")
        return real(path, mode, *args, **kw)
    monkeypatch.setattr("builtins.open", denied)
    reply = set_up(site)
    check("said, with the system's reason", reply.status == 500 and "echolot.yml could not be written" in reply.body
          and "Permission denied" in reply.body, reply.body[:300])
    check("and init did not run", not init.ran)


# --- where the page cannot set a project up ------------------------------------------------------

def cannot(project: Project, said: str, *words: str):
    """The page of a project that cannot be set up from here: its own
    sentence, `scan`'s words under it, and a press that writes nothing."""
    site, init = serving(project)
    page = page_of(site)
    box = page.one("div", id="setup-cannot")
    check("the page's own sentence comes first", box.all("p")[0].text.startswith("This page cannot set the project up by itself. " + said),
          box.all("p")[0].text)
    scan = re.sub(r"\s+", " ", said_by(["scan"]).replace("**", "").replace("`", ""))
    behind = [p.text[len(p.one("span", "who").text):].strip() for p in box.all("p", "says")]
    check("with what scan says under it", behind and all(p.one("span", "who").text == "echolot scan" for p in box.all("p", "says")), behind)
    for line in behind:
        check(f"in scan's own words: {line[:60]}", line.strip("_") in scan, scan[:600])
    for word in words:
        check(f"which names the reason: {word}", any(word in line for line in behind), behind)
    check("there is no form to send and no button to press", not page.all("form", id="setup-form") and not page.all("button", id="setup-go"))
    check("the agent's path is the step that is there already", page.all("section", aria_labelledby="next-h")
          and "An agent can set it up: the step under What to do next says how." in box.text, box.text)
    before = files(project.root)
    reply = set_up(site)
    check("a press that reaches the server all the same is refused, and writes nothing",
          reply.status == 409 and "cannot be set up from here" in reply.body and files(project.root) == before and not init.ran,
          (reply.status, reply.body[:200]))
    return page


def test_no_app_in_the_folder(project):
    page = cannot(project.agents().doctor(), "echolot found no Android app in this folder", "declares a launcher Activity")
    check("and the agent's command is there to copy", page.one("code", id="next-cmd").text == "echolot guide setup")


def test_no_app_in_a_folder_echolot_has_never_seen(project):
    page = cannot(project, "echolot found no Android app in this folder", "declares a launcher Activity")
    check("the step is init, as it was", page.one("code", id="next-cmd").text == "echolot init")


def test_a_package_the_build_files_do_not_say(project):
    cannot(project.app(gradle=UNREAD).agents().doctor(), "echolot found the app and could not read its name from the build files",
           "applicationId ?", "applicationId not read from app/build.gradle.kts")


def test_a_macrobenchmark_that_measures_a_section_of_its_own(project):
    benchmark_project(project.root, FeedBenchmark=SECTION_CLASS)
    cannot(project.agents().doctor(), "This project has a macrobenchmark that measures something of its own", "FeedBenchmark")


def test_a_repository_that_cannot_be_read(project):
    def unreadable(root):
        raise OSError("permission denied")
    site = site_of(project.agents().doctor(), ready.Sources(scan=unreadable, pick_device=lambda serial: "EXAMPLE0001"))
    box = page_of(site).one("div", id="setup-cannot")
    check("said, with no words of a scan that said none", "echolot could not read this folder" in box.text
          and not box.all("p", "says"), box.text)
    check("and a press writes nothing", set_up(site).status == 409 and not (project.root / "echolot.yml").exists())


def test_a_draft_with_a_value_scan_could_not_fill_in(project, monkeypatch):
    """A startup macrobenchmark whose gradle task does not read from the build files."""
    benchmark_project(project.root, StartupBenchmark=STARTUP_CLASS)
    monkeypatch.setattr(scan_mod, "gradle_tasks", lambda bench, variants: [])
    site, init = serving(project.agents().doctor())
    box = page_of(site).one("div", id="setup-cannot")
    check("the page names the value, and writes no question mark into a config", "a value echolot could not fill in "
          "from the repository: runner.gradle_task." in box.text, box.text)
    check("and a press writes nothing", set_up(site).status == 409 and not (project.root / "echolot.yml").exists() and not init.ran)


def test_a_config_with_no_scenario_that_scan_cannot_draft_one_for(project):
    page = page_of(site_of(project.app(gradle=UNREAD).config(HEAD).agents().doctor()))
    box = page.one("div", id="setup-cannot")
    check("why, and that the file stays as it is", "could not read its name from the build files" in box.text
          and "The file that is there stays as it is. An agent can write the scenario into it." in box.text, box.text)
    check("no lines to add that would hold a placeholder", not page.all("pre", id="setup-lines"))


def test_a_draft_that_is_no_config_is_not_written(project):
    """A name in the build files that would be two lines of YAML."""
    nasty = GRADLE.replace('applicationId = "com.example.app"', 'applicationId = "com.example.app: [\n  evil"')
    site, init = serving(project.app(gradle=nasty).agents().doctor())
    page = page_of(site)
    check("the page says it does not write it", "does not read as one, so the page does not write it"
          in page.one("div", id="setup-cannot").text, page.one("div", id="setup-cannot").text)
    check("and a press writes nothing", set_up(site).status == 409 and not (project.root / "echolot.yml").exists() and not init.ran)


def test_a_name_from_the_build_files_cannot_become_markup(project):
    nasty = GRADLE.replace('applicationId = "com.example.app"', 'applicationId = "com.example.<i>app</i><script>alert(1)</script>"')
    site, _ = serving(project.app(gradle=nasty).agents().doctor())
    reply = get(site)
    page = read(reply.body)
    check("the name is text on the page", "com.example.<i>app</i><script>alert(1)</script>" in page.one("div", id="setup-app").text
          and not page.one("section", id="setup").all("i") and len(page.all("script")) == 2)
    check("and in the file it is the package, whole", set_up(site).status == 303
          and Config.load(project.root / "echolot.yml").get("project.package") == "com.example.<i>app</i><script>alert(1)</script>")


def test_a_startup_macrobenchmark_is_set_up_as_the_launch_it_measures(project):
    benchmark_project(project.root, StartupBenchmark=STARTUP_CLASS)
    site, init = serving(project.agents().doctor())
    page = page_of(site)
    card = page.one("label", "opt")
    check("the same answer, and who opens the app", card.one("span", "opt-t").text == "How the app opens" and card.one(
        "span", "opt-d").text == "The project's own macrobenchmark opens the app, and echolot reads what it records.")
    check("only the config is written here: echolot's files are in already",
          listed(page) == ["echolot.yml"] and page.one("p", id="setup-hint").text == (
              "Writes echolot.yml into this project. Nothing in your code changes."))
    before = files(project.root)
    check("taken", set_up(site, limit="1.5").status == 303)
    cfg = Config.load(project.root / "echolot.yml")
    check("the launch window, run by the macrobenchmark", cfg.scenario_window == LAUNCH_WINDOW
          and cfg.runner.get("mode") == "gradle" and cfg.scenario_budget_ms == 1500, cfg.runner)
    check("one file and no init", changed(before, project.root) == {"echolot.yml"} and not init.ran)


def test_several_apps_in_one_repository_are_said_beside_the_name(project):
    project.app()
    other = project.root / "wear" / "src" / "main"
    other.mkdir(parents=True)
    shutil.copy(project.root / "app" / "src" / "main" / "AndroidManifest.xml", other / "AndroidManifest.xml")
    (project.root / "wear" / "build.gradle.kts").write_text(GRADLE.replace(APP, "com.example.wear"), encoding="utf-8")
    box = page_of(site_of(project)).one("div", id="setup-app")
    said = box.one("p", "says").text
    check("scan's note stands under the question, in its words", said.startswith("echolot scan several modules declare a "
          "launcher Activity") and "the first is read; the others are apps too" in said, said)
    check("which scan prints", said[len("echolot scan "):] in re.sub(r"\s+", " ", said_by(["scan"])))


# --- other steps that come first -------------------------------------------------------------------

@pytest.mark.parametrize("how", ["a self-check that did not pass", "agents chosen and no layer", "another config named",
                                 "echolot's lines in AGENTS.md", "a rule for Codex of an earlier install"])
def test_where_another_step_comes_first_the_page_is_as_it_was(project, monkeypatch, how):
    """`init` writes new files only into a project echolot was never in.
    Anywhere else it brings files up to date and says itself which, so it
    stays the command to copy, and the page writes nothing."""
    project.app()
    if how == "a self-check that did not pass":
        project.agents().doctor(failed=("the launch window",))
    if how == "agents chosen and no layer":
        project.agents("claude")
    if how == "echolot's lines in AGENTS.md":
        (project.root / "AGENTS.md").write_text(hosts.BY_KEY["agents"].render() + "\n# Ours\n", encoding="utf-8")
    if how == "a rule for Codex of an earlier install":
        (project.root / ".codex" / "rules").mkdir(parents=True)
        (project.root / codex.RULE_PATH).write_text("# an older rule\n", encoding="utf-8")
    site = site_of(project)
    if how == "another config named":
        site.config = "other.yml"
    body = get(site).body
    before = files(project.root)
    reply = set_up(site)
    check("a press is refused and writes nothing", reply.status == 409 and files(project.root) == before, reply.status)
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("setup")])
    check("the page is the one a server without setup draws, byte for byte", get(site).body == body)


@pytest.mark.parametrize("config", [LAUNCH, SEVERAL, BENCHMARK], ids=["one scenario", "several", "a macrobenchmark"])
def test_where_a_config_with_scenarios_exists_nothing_changes(project, monkeypatch, config):
    site = site_of(project.app().config(config).agents().doctor().traces().report())
    body = get(site).body
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("setup")])
    check("the page is the one a server without setup draws, byte for byte", get(site).body == body)


def test_files_written_by_a_newer_echolot(project, monkeypatch):
    monkeypatch.setattr(state, "next_kind", lambda st: "upgrade")
    site, init = serving(project.app())
    check("no setup while the files here are a newer echolot's", not page_of(site).all("section", id="setup")
          and set_up(site).status == 409 and not init.ran)


# --- a config with no scenario ---------------------------------------------------------------------

def test_a_config_with_no_scenario_gets_the_lines_to_add(project):
    site, init = serving(project.app().config(HEAD).agents().doctor())
    page = page_of(site)
    said = page.one("p", id="setup-by-hand").text
    check("the question, and what the config measures as it is", page.one("h2", id="q-what").text == "What do you want to check?"
          and "echolot.yml is there and names no scenario, so a check measures everything a recording holds" in said, said)
    check("the page writes into no file of a person's, and says so", "The page does not write into a file that is yours." in said
          and "The runner section is missing too" in said, said)
    shown = page.one("pre", id="setup-lines").raw()
    check("the lines are scan's scenario and runner, and no section the file has",
          shown.startswith("scenario:\n  name: coldStart\n  window: {name: launch") and "\n\nrunner:\n  mode: launch" in shown
          and "project:" not in shown and "instrumentation:" not in shown, shown)
    check("with the limit's line among them, as a comment to switch on", "\n  # budget_ms: 2000  # a time limit" in shown)
    check("a button copies them, and Refresh reads the file anew",
          page.one("button", data_setup_copy="setup-lines").text == "Copy the lines" and "Then press Refresh." in page.one(
              "section", id="setup").text)
    check("there is no form of setup, and the button of the config that is there keeps its place",
          not page.all("form", id="setup-form") and page.one("button", id="check-go").text == "Check the app")
    check("anything else is the agent's, by its own command and not the step's",
          page.one("code", id="setup-agent").text == "echolot guide setup")
    check("one thing to do first, not two", not page.all("aside", id="setup-first"))
    before = files(project.root)
    check("a press that reaches the server writes nothing", set_up(site).status == 409 and files(project.root) == before
          and not init.ran)
    project.config(HEAD + shown)
    cfg = Config.load(project.root / "echolot.yml")
    check("added as they are, they make the launch the scenario", cfg.scenario_window == LAUNCH_WINDOW
          and cfg.scenario_name == "coldStart" and cfg.runner.get("mode") == "launch")
    check("and the question is gone with them", not page_of(site).all("section", id="setup"))


def test_a_config_with_no_scenario_and_a_runner_of_its_own(project):
    page = page_of(site_of(project.app().config(HEAD + "runner:\n  mode: launch\n  iterations: 3\n").agents().doctor()))
    shown = page.one("pre", id="setup-lines").raw()
    check("only the scenario is added: a section written twice is no YAML", shown.startswith("scenario:\n")
          and "runner:" not in shown and "missing too" not in page.one("p", id="setup-by-hand").text, shown)


# --- before the first check ------------------------------------------------------------------------

def test_what_comes_before_the_first_check(project, adb):
    site = site_of(project.ready())
    note = page_of(site).one("aside", id="setup-first").text
    check("the build that is measured has to be on the phone, and how it gets there", note == (
        f"Before the first check, {APP} has to be on the phone as the build you want measured: echolot opens what is "
        "installed there, and installs nothing. Run that build from Android Studio once, or install it with "
        "./gradlew :app:installBenchmark in a terminal."), note)
    check("the task is the one that installs the build the page measures on",
          "can be measured on the benchmark build" in lines(page_of(site))["The app"][1])
    check("the button is there and ready", "disabled" not in page_of(site).one("button", id="check-go").attrs)
    adb.listing = ""
    note = page_of(site).one("aside", id="setup-first").text
    check("with no phone, that too, from the line that knows", note.endswith(
        "And the phone has to be connected: the line marked red says how."), note)
    adb.listing = "EXAMPLE0001          device product:shiba model:Pixel_8 device:shiba transport_id:1\n"
    project.traces()
    check("once it was recorded the note is gone", not page_of(site).all("aside", id="setup-first"))


def test_a_macrobenchmark_installs_its_own_build(project):
    page = page_of(site_of(project.app().config(BENCHMARK).agents().doctor()))
    check("nothing to install by hand", not page.all("aside", id="setup-first"))


def test_a_config_whose_code_is_elsewhere_names_no_task(project):
    note = page_of(site_of(project.config().agents().doctor())).one("aside", id="setup-first").text
    check("said without a task it cannot know", note.endswith("Run that build from Android Studio once."), note)


# --- a setup under way, and one that ended badly -------------------------------------------------------

def rows(page) -> list[tuple[str, str, str]]:
    return [(li.attrs["class"], li.one("b").text, li.all("span")[-1].text) for li in page.one("ol", "run").all("li")]


def test_a_setup_under_way_shows_both_rows_and_keeps_itself_current(project):
    a_setup(project.app().config(LAUNCH))
    site, init = serving(project)
    reply = get(site)
    page = read(reply.body)
    check("both rows from the start: the config written, init under way", rows(page) == [
        ("done", "The config, echolot.yml", "written"),
        ("now", "echolot's own files, and a check of this computer", "under way, about a minute…")], rows(page))
    run = page.one("div", id="run")
    check("it asks its own address how far it is", run.attrs["data-state"] == "running"
          and run.attrs["data-poll"] == f"/{site.token}/setup/state")
    check("how long, and that the page moves on by itself", "This takes about a minute" in page.one("p", id="run-note").text)
    check("with script off it loads itself again", '<noscript><meta http-equiv="refresh"' in reply.body)
    check("no button of either kind meanwhile", not page.all("form"))
    state_now = read(get(site, "setup/state").body).one("div", id="run-now")
    check("what it asks for is the rows as they stand", state_now.attrs["data-state"] == "running"
          and [li.one("b").text for li in state_now.all("li")] == [name for _, name, _ in rows(page)])
    reply = press(site, "check", {"what": "record:coldStart"})
    check("a check waits for it", reply.status == 409 and read(reply.body).one("h1").text == "Setting up is still under way")
    reply = set_up(site)
    check("and a second press of setup starts nothing", reply.status == 409 and not init.ran
          and read(reply.body).one("h1").text == "Setting up is already under way", reply.body[:200])


def test_how_far_a_setup_is_when_there_is_none(project):
    site = site_of(project.app())
    check("nothing to say", read(get(site, "setup/state").body).one("div", id="run-now").attrs["data-state"] == "none")
    check("and asking wrote nothing", not (project.root / ".echolot").exists())


def test_a_setup_whose_init_fails_says_what_init_said(project):
    site, init = serving(project.app(), fails=True)
    check("the press is taken: the config is written before init runs", set_up(site).status == 303 and init.ran
          and (project.root / "echolot.yml").is_file())
    page = page_of(site)
    box = page.one("section", id="setup")
    check("the page's own sentence", box.one("p", id="setup-end").text == (
        "Setting up did not finish. echolot.yml is written. echolot init, which installs echolot's own files and "
        "checks this computer, ended with an error."), box.one("p", id="setup-end").text)
    check("then init's words as it printed them, and where the rest is", "the launch window: expected 1920.0, got 0.0"
          in box.one("pre", "printed").text and ".echolot/log/setup.init.log" in box.text)
    check("the row says which step it was", rows(page)[-1] == (
        "failed", "echolot's own files, and a check of this computer", "did not finish"))
    check("and the step that follows is the one echolot names", page.one("code", id="next-cmd").text == "echolot doctor"
          and lines(page)["This computer"][0] == "red")
    project.doctor()
    check("once the self-check passes it is history", not page_of(site).all("section", id="setup"))


def test_a_setup_that_was_cut_short(project, monkeypatch):
    monkeypatch.setattr(state, "_alive", lambda pid: pid == os.getpid())
    a_setup(project.app().config(LAUNCH), pid=os.getpid() + 1)
    page = page_of(site_of(project))
    said = page.one("p", id="setup-end").text
    check("said, with what is there and what may not be", said.startswith("Setting up was cut short. The process that ran it "
          "is gone") and "echolot.yml is written, and echolot's own files may be half there." in said, said)
    check("the step that finishes it is the one that was always there",
          "The step under What to do next finishes it." in page.one("section", id="setup").text
          and page.one("code", id="next-cmd").text == "echolot init")
    check("no words of a command that said none", not page.one("section", id="setup").all("pre"))


def test_a_setup_that_ended_badly_long_ago_is_history(project):
    run = a_setup(project.app().config(LAUNCH), end="failed")
    check("shown while it is news", page_of(site_of(project)).all("p", id="setup-end"))
    shown = setup_mod.reading(project.root, state.project_state(project.root), job.read(project.root, "setup"),
                              now=run["finished"] + 25 * 3600)
    check("a day later it is not", shown is None)


def test_init_that_cannot_be_started(project):
    site = site_of(project.app())

    def refused(root, name, plan):
        raise job.Refused("the process that runs it could not be started: No such file or directory")
    site.spawn = refused
    reply = set_up(site)
    check("said, with what is written and what is not", reply.status == 500 and "Setting up stopped half way" in reply.body
          and "echolot.yml</code> is written" in reply.body and "No such file or directory" in reply.body, reply.body[:400])
    check("the config is there", (project.root / "echolot.yml").is_file())


# --- what the server refuses ---------------------------------------------------------------------

def test_a_press_is_refused_without_the_token_from_another_origin_or_under_another_name(project):
    project.app()
    started = []

    def spawn(root, name, plan):
        started.append(plan)
        job.run(root, name, {"steps": [], "what": plan["what"]}, signals=False)
    with running(project) as server:
        site = server.site
        site.spawn = spawn
        before = files(project.root)
        own = {"Origin": site.origin}
        for name, path, how, expected in (
                ("no token", "/setup", own, 403),
                ("a wrong token", "/nope/setup", own, 403),
                ("no origin", f"/{site.token}/setup", {}, 403),
                ("another origin", f"/{site.token}/setup", {"Origin": "https://evil.example"}, 403),
                ("the origin `null`", f"/{site.token}/setup", {"Origin": "null"}, 403),
                ("its own origin, sent from another site", f"/{site.token}/setup", {**own, "Sec_Fetch_Site": "cross-site"}, 403),
                ("another name for this machine", f"/{site.token}/setup", {**own, "host": f"localhost:{site.port}"}, 403),
                ("no name at all", f"/{site.token}/setup", {**own, "host": None}, 403)):
            how = dict(how)
            host = how.pop("host", "")
            status, _, body = send(server, path, b"what=launch&limit=2&token=" + site.token.encode(), host=host, **how)
            check(f"{name}: {expected}", status == expected, (status, body[:120]))
            check(f"{name}: told nothing", "<" not in body and site.token not in body and "echolot.yml" not in body, body)
        check("nothing started, nothing written", not started and files(project.root) == before, started)
        big = b"what=launch&pad=" + b"x" * ui.BODY_CAP
        status, _, body = send(server, f"/{site.token}/setup", big, **own)
        check("a body over the cap is refused unread", status == 413 and "bytes at most" in body and files(project.root) == before)
        status, _, _ = send(server, f"/{site.token}/setup", b"what=launch", length="many", **own)
        check("a length that is no number", status == 413 and files(project.root) == before)
        status, headers, _ = send(server, f"/{site.token}/setup", b"what=launch&limit=2", **own, Sec_Fetch_Site="same-origin")
        check("and the server's own page is taken", status == 303 and headers["Location"] == f"/{site.token}/"
              and len(started) == 1 and (project.root / "echolot.yml").is_file())


def test_a_body_that_is_no_form(project):
    site, init = serving(project.app())
    before = files(project.root)
    many = urlencode([("f", str(i)) for i in range(ui.FIELDS + 1)]).encode()
    sent = {"Host": site.host, "Origin": site.origin, "Content-Type": ui.FORM}
    check("more fields than a press has", ui.answer(site, "POST", f"/{site.token}/setup", sent, many).status == 400)
    check("bytes that are no text", ui.answer(site, "POST", f"/{site.token}/setup", sent, b"what=\xff\xfe").status == 400)
    reply = ui.answer(site, "POST", f"/{site.token}/setup", {**sent, "Content-Type": "application/json"}, b'{"what": "launch"}')
    check("a body of another kind has no fields, and a press with no choice is none", reply.status == 400)
    check("nothing written", files(project.root) == before and not init.ran)


def test_the_addresses_take_the_request_they_are_for_and_no_other(project):
    site, init = serving(project.app())
    reply = get(site, "setup")
    check("a read of the address of a press starts nothing", reply.status == 405 and dict(reply.headers)["Allow"] == "POST")
    check("how far a setup is can only be read", press(site, "setup/state").status == 405)
    check("nothing written", not init.ran and not (project.root / "echolot.yml").exists())


def test_reading_writes_nothing_in_any_state_of_setup(project, tmp_path_factory):
    """Files are written on a press, and by nothing else."""
    builds = {
        "the question": lambda p: p.app(),
        "cannot be set up": lambda p: p.app(gradle=UNREAD).agents().doctor(),
        "no scenario": lambda p: p.app().config(HEAD).agents().doctor(),
        "before the first check": lambda p: p.ready(),
        "under way": lambda p: a_setup(p.app().config(LAUNCH)),
        "ended badly": lambda p: a_setup(p.app().config(LAUNCH), end="failed"),
    }
    for name, build in builds.items():
        root = tmp_path_factory.mktemp("state")
        os.chdir(root)
        build(Project(root))
        site = site_of(Project(root))
        before = files(root)
        for path in ("", "setup/state", "report", "nowhere"):
            get(site, path)
        check(f"{name}: every file is as it was, and none is new", files(root) == before)


def test_without_the_addresses_of_setup_the_page_is_as_it_was(project, monkeypatch):
    project.app()
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("setup")])
    page = page_of(site_of(project))
    check("no question and no form of it", not page.all("section", id="setup") and not page.all("form", id="setup-form"))
    check("the step is the command to copy", page.one("code", id="next-cmd").text == "echolot init")


# --- the page itself -----------------------------------------------------------------------------

STATES = {
    "the question": lambda p: p.app(),
    "cannot be set up": lambda p: p.app(gradle=UNREAD).agents().doctor(),
    "no scenario": lambda p: p.app().config(HEAD).agents().doctor(),
    "before the first check": lambda p: p.ready(),
    "under way": lambda p: a_setup(p.app().config(LAUNCH)),
    "ended badly": lambda p: a_setup(p.app().config(LAUNCH), end="failed"),
}


@pytest.mark.parametrize("name", list(STATES))
def test_every_state_is_one_page_with_ids_of_its_own_and_one_next_step(project, name):
    STATES[name](project)
    body = get(site_of(project)).body
    page = read(body)
    ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs]
    check("no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
    check("one headline, one script and one style", len(page.all("h1")) == 1 and len(page.all("script")) == 2
          and len(page.all("style")) == 1)
    primary = [n for n in page.walk() if "primary" in (n.attrs.get("class") or "").split() and "disabled" not in n.attrs]
    check("at most one next step that can be taken", len(primary) <= 1, [n.text for n in primary])
    check("its own styles and script are written into it, after the start page's",
          [body.index(kit.asset(n)) for n in (*start_page.CSS, *setup_page.CSS)] == sorted(
              body.index(kit.asset(n)) for n in (*start_page.CSS, *setup_page.CSS))
          and body.index(kit.asset("start.js")) < body.index(kit.asset("setup.js")))


def test_with_script_switched_off_every_step_is_a_form_or_a_fold(project):
    page = page_of(site_of(project.app()))
    form = page.one("form", id="setup-form")
    needs = [n for n in page.walk() if "needs-js" in (n.attrs.get("class") or "").split()]
    check("only the buttons that copy need script, and they are hidden without it",
          needs and all(n.tag == "button" and "data-setup-copy" in n.attrs for n in needs), [n.attrs for n in needs])
    check("the choice, the limit and the button are the form's own", form.one("input", type="radio").attrs["name"] == "what"
          and form.one("input", id="limit").attrs["name"] == "limit" and form.one("button", id="setup-go").attrs["type"] == "submit")
    check("what it writes is a fold of the browser's own", form.one("details", id="setup-writes").all("summary"))
    check("the command to copy can be reached and read without the button", form.one("code", id="setup-agent").attrs["tabindex"] == "0")


def test_the_setup_pages_own_styles_keep_to_the_tokens():
    css = kit.asset("setup.css")
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    check("no colour of its own", not literal.search(css), literal.findall(css))
    check("nothing is fetched by it", not any(word in css for word in ("url(", "@import", "@font-face")))
    check("the tokens are the shared ones", not _blocks(css, ":root {"))
    mine = set(re.findall(r"^\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S), flags=re.M))
    shared = "".join(kit.asset(n) for n in ("base.css", "start.css", "table.css", "picks.css", "report.css", "answer.css"))
    theirs = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", shared, flags=re.S)))
    check("a class it names by itself is no other sheet's", not mine & theirs, sorted(mine & theirs))
    check("and its files are inside the package", all((PAGES / n).is_file() for n in setup_page.CSS + setup_page.JS))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the script")
def test_the_script_parses():
    done = subprocess.run(["node", "--check", str(PAGES / "setup.js")], capture_output=True, text=True)
    check("setup.js is JavaScript", done.returncode == 0, done.stderr)


def test_the_script_asks_no_server_anything():
    script = kit.asset("setup.js")
    check("it copies, and guards a second click: nothing is fetched", "fetch(" not in script and "XMLHttpRequest" not in script
          and "location" not in script)
