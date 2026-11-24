#!/usr/bin/env python3
"""Dig deeper with Codex in `echolot ui` (#454): the screen before it starts,
the choice between two clients, Codex at work, and what is left after it.

What is pinned here, a test per state:

  - Codex alone can start: the report leads to its screen, and that screen
    says what differs from Claude Code: the trust it writes into the
    person's own settings, edits it cannot be kept from, no spending cap,
    the tokens at the end, and the half hour after which the page stops it;
  - both can start: the screen shows one, and a form leads to the other's;
    the one the last run used comes first; neither can start: each reason;
  - an address that names a client the page does not start, or one that
    cannot start now;
  - a press: the investigation opened, then Codex started in it with the
    command line the server built, the project's files noted;
  - Codex at work and after it: the phase, nothing moved for ten minutes,
    ended by the page after half an hour with its question shown, stopped,
    failed, ended with no verdict and with one, what its sandbox refused,
    and the files it changed outside the allowed folders, whichever way it
    ended;
  - the server writes nothing while it shows any of it.

A fake client (`tests/fake_codex.py`) stands in for Codex, run by the real job
in this process; `tests/fake_claude.py` for Claude Code where both are there.
echolot's own commands are the real ones. Nothing starts the real client,
needs a phone or the network, or waits on a timer.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import codex, dig, dig_codex, job  # noqa: E402
from echolot import hunt as hunt_mod  # noqa: E402
from tests import fake_claude, fake_codex  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_codex import OLDER  # noqa: E402
from tests.test_report_page import known, read  # noqa: E402
from tests.test_ui import Adb, Project, get, site_of  # noqa: E402
from tests.test_ui_dig import (CONFIG, MARKED, SIGNED_IN, Echolot, Table, a_dig, page, press,  # noqa: E402
                               ticked, under)

PROGRAM = "/opt/example/bin/codex"
CODEX_IN = dig.Client(program=PROGRAM, version="0.159.2", key="codex", how="with ChatGPT")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def home(monkeypatch, tmp_path_factory) -> Path:
    """A Codex home of its own: no test reads the settings of this computer."""
    h = tmp_path_factory.mktemp("codex-home")
    monkeypatch.setenv("CODEX_HOME", str(h))
    return h


@pytest.fixture
def project(tmp_path, monkeypatch, adb, home) -> Project:
    """A project set up for both clients: a git repository with Codex's rules
    as this echolot writes them, and Claude Code's commands."""
    monkeypatch.chdir(tmp_path)
    p = Project(tmp_path).app().config(CONFIG).agents().doctor().traces().report()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / codex.RULE_PATH).parent.mkdir(parents=True)
    (tmp_path / codex.RULE_PATH).write_text(codex.RULE_FILE, encoding="utf-8")
    for rel in dig.LAYER:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("the layer echolot init installs\n", encoding="utf-8")
    return p


def serving(project: Project, plan: dict | None = None, clients=(CODEX_IN,), on_wait=None, table: Table | None = None,
            clock=None):
    """The site, with the fake Codex (and the fake Claude Code) in place of the
    real ones, run to the end by the real job before the press is answered."""
    site = site_of(project)
    site.agent = lambda: list(clients)
    echolot = Echolot()
    site.run = echolot
    codex_ = fake_codex.Codex(PROGRAM, plan or {"do": "verdict"}, on_wait=on_wait)
    claude = fake_claude.Claude(SIGNED_IN.program, {"do": "verdict"})
    table = table or Table()
    site.procs = table

    def popen(argv, **kw):
        return (codex_ if argv[0] == PROGRAM else claude)(argv, **kw)
    more = {"clock": clock} if clock else {}
    site.spawn = lambda root, name, plan_: job.run(root, name, plan_, popen=popen, signals=False, procs=table,
                                                   kill=table.kill, **more)
    return site, codex_, echolot


def panel(site, n: int = 1):
    return page(get(site, f"hunt/{n}/answer")).one("section", id="dig")


def blocks(consent) -> dict[str, str]:
    """Each block of the screen before the start, by its heading, without it."""
    return {b.one("h2").text: b.text[len(b.one("h2").text):] for b in consent.all("section", "dig-block")}


def a_codex(project: Project, why: str | None = None, **kw) -> None:
    """A Codex run's files as the job leaves them (`test_ui_dig.a_dig`), with
    the client the page started named."""
    a_dig(project, **kw)
    path = project.root / ".echolot" / "log" / "dig.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["what"]["client"] = "codex"
    del state["what"]["budget_usd"]
    if why:
        state["why"] = why
    path.write_text(json.dumps(state), encoding="utf-8")


# --- where Codex alone can start ------------------------------------------------------------------

def test_a_report_leads_to_codex_where_it_alone_can_start(project):
    site, _, _ = serving(project)
    report = page(get(site, "report"))
    check("the dialog's Start goes to the screen before it", report.one("form", id="ask-start-form").attrs["action"]
          == f"/{site.token}/dig/ask")
    check("and says who does it", "Codex, the one on this computer, does it under your own account. The next screen "
          "says what it will do, and nothing starts before you press Start there." in report.one("dialog", "ask").text)


def test_the_screen_before_codex_starts_says_what_differs(project, monkeypatch):
    monkeypatch.setattr(dig, "changed_files", lambda root: 2)
    site, _, _ = serving(project)
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    said = blocks(consent)
    check("the blocks, in Claude Code's order", list(said) == ["Who does it", "How long", "What it does",
                                                               "What it may do without asking", "What changes", "Stop"])
    check("which client, how it is signed in, and no spending cap", said["Who does it"] == (
        "Codex 0.159.2, the one installed on this computer, under your own account, signed in with ChatGPT. It costs "
        "what any session of yours costs, on your ChatGPT plan or your API key. There is no spending cap the page "
        "can set for Codex: the page shows the tokens it reports at the end."), said["Who does it"])
    check("how long, and that the page stops it after half an hour of nothing", "If it runs nothing and writes "
          "nothing for 30 minutes, the page stops it: that is how Codex waits for an answer to a question, and nobody "
          "is here to give one." in said["How long"], said["How long"])
    may = [li.text for li in consent.one("ul", "dig-may").all("li")]
    check("what it may run, and that its edits cannot be kept to the allowed folders", may == [
        "echolot any echolot command", "./gradlew any Gradle task of the project", "adb any command on the phone",
        "change any file of the project, not only app/src/main: Codex's edits cannot be kept to those folders, so "
        "the page lists afterwards every file it changed outside them",
        "run other commands in Codex's sandbox, which has no network and writes nothing outside the project"], may)
    check("the rules that let the three out, in every session", "The first three run outside the sandbox, by the "
          "rules in .codex/rules/echolot.rules, which echolot init wrote: Codex runs them without asking in every "
          "session in this project." in said["What it may do without asking"])
    check("the trust it writes into the person's own settings, a change outside the project", "Codex marks this "
          f"project trusted in your own Codex settings, {codex.config_file()}, as it does the first time it works in a "
          "project. That is a change outside the project, and it stays" in said["What changes"], said["What changes"])
    check("the files noted, and listed afterwards", "When it starts, the page notes the project's files, and "
          "afterwards lists the ones changed outside app/src/main. Change none of them yourself meanwhile, or the "
          "list holds yours too." in said["What changes"])
    check("what is changed already, as for Claude Code", "2 files are already changed and not committed"
          in said["What changes"])
    check("what Stop does", "It ends Codex and everything it started" in said["Stop"])
    start = consent.one("form", id="dig-start-form")
    check("Start names Codex in its address, with the ticks", start.attrs["action"]
          == f"/{site.token}/dig/with/codex/start"
          and [(i.attrs["name"], i.attrs["value"]) for i in start.all("input")] == [("pick.main_thread_waits", "1")])
    commands = [pre.text for pre in consent.all("pre", "dig-cmd")]
    check("exactly what starts: the investigation, then Codex in the project", commands == [
        shlex.join(["echolot", "hunt", dig.hunt_question(known()), "--since", "unknown"]),
        shlex.join(["codex", "exec", "--json", "-s", "workspace-write", "-C", str(project.root), "<the request>"])],
        commands)
    check("then Codex is started in it", "then Codex is started in it, with the request below" in consent.text)
    check("nothing started and nothing opened", job.read(project.root, dig.JOB) is None
          and hunt_mod.load(project.root) is None)


def test_a_project_codex_trusts_already_and_rules_of_its_own(project, home):
    (home / "config.toml").write_text(f'[projects."{project.root.resolve()}"]\ntrust_level = "trusted"\n',
                                      encoding="utf-8")
    (project.root / ".codex" / "rules" / "team.rules").write_text('prefix_rule(pattern = ["git"], decision = "allow")\n',
                                                                  encoding="utf-8")
    (home / "rules").mkdir()
    (home / "rules" / "default.rules").write_text('prefix_rule(pattern = ["npm"], decision = "allow")\n',
                                                  encoding="utf-8")
    site, _, _ = serving(project)
    said = blocks(page(press(site, "dig/ask", ticked("main_thread_waits"))))
    check("trusted: nothing changes there", f"Your Codex settings already trust this project ({codex.config_file()}), "
          "and that stays as it is." in said["What changes"], said["What changes"])
    check("the other rules files it reads, by name", "Codex also reads these rules files, and what they allow applies "
          f"too: .codex/rules/team.rules and {home / 'rules' / 'default.rules'}." in said["What it may do without asking"],
          said["What it may do without asking"])


# --- which client --------------------------------------------------------------------------------------

def test_with_both_the_person_picks_on_the_screen_before_the_start(project):
    site, _, _ = serving(project, clients=(SIGNED_IN, CODEX_IN))
    report = page(get(site, "report"))
    check("the dialog names both", "Claude Code or Codex, both on this computer, does it under your own account. The "
          "next screen says which one and what it will do" in report.one("dialog", "ask").text)
    first = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("Claude Code first, as the table has it", blocks(first)["Who does it"].startswith("Claude Code 2.1.300")
          and first.one("form", id="dig-start-form").attrs["action"] == f"/{site.token}/dig/start")
    switch = first.one("form", "dig-other")
    check("and a form to Codex's own screen, with the ticks, that works with script off", switch.attrs["action"]
          == f"/{site.token}/dig/with/codex/ask" and switch.one("button").text == "Use Codex instead"
          and [(i.attrs["name"], i.attrs["value"]) for i in switch.all("input")] == [("pick.main_thread_waits", "1")]
          and "What it may do differs: its own screen says how." in switch.text, switch.text)
    check("outlined beside Start, which stays the one next step", switch.one("button").attrs["class"] == "btn line"
          and first.one("button", id="dig-start").attrs["class"] == "btn primary press")
    other = page(press(site, "dig/with/codex/ask", ticked("main_thread_waits")))
    check("Codex's screen, with its own terms and its own Start", blocks(other)["Who does it"].startswith(
        "Codex 0.159.2") and other.one("form", id="dig-start-form").attrs["action"]
          == f"/{site.token}/dig/with/codex/start")
    check("and the way back", other.one("form", "dig-other").attrs["action"] == f"/{site.token}/dig/with/claude/ask"
          and other.one("form", "dig-other").one("button").text == "Use Claude Code instead")


def test_the_client_the_last_run_used_comes_first(project):
    a_codex(project, end="done")
    site, _, _ = serving(project, clients=(SIGNED_IN, CODEX_IN))
    project.hunt(1, dig.hunt_question(known()), "concluded", current=True)
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("Codex's screen, and Claude Code one press away", blocks(consent)["Who does it"].startswith("Codex")
          and consent.one("form", "dig-other").one("button").text == "Use Claude Code instead")


def test_with_several_scenarios_the_client_and_the_scenario_are_both_in_the_address(project):
    from tests.test_ui import SEVERAL
    project.config(SEVERAL.replace("scenarios:", 'instrumentation:\n  allowed: ["app/src/main"]\nscenarios:', 1))
    project.report(".echolot/out/coldStart").report(".echolot/out/feedScroll", data={**known(), "scenario": "feedScroll"})
    project.traces("feedScroll")
    site, codex_, echolot = serving(project, {"do": "rounds", "rounds": 0}, clients=(SIGNED_IN, CODEX_IN))
    first = page(press(site, "dig/ask/feedScroll", ticked("main_thread_waits")))
    check("the way to Codex keeps the scenario", first.one("form", "dig-other").attrs["action"]
          == f"/{site.token}/dig/with/codex/ask/feedScroll")
    other = page(press(site, "dig/with/codex/ask/feedScroll", ticked("main_thread_waits")))
    check("and so does its Start", other.one("form", id="dig-start-form").attrs["action"]
          == f"/{site.token}/dig/with/codex/start/feedScroll")
    reply = press(site, "dig/with/codex/start/feedScroll", ticked("main_thread_waits"))
    check("started, about that scenario", reply.status == 303 and echolot.ran[0][-2:] == ["--scenario", "feedScroll"]
          and codex_.argv[0] == PROGRAM, (reply.status, echolot.ran))


def test_where_neither_can_start_each_reason_is_said_once(project):
    (project.root / codex.RULE_PATH).write_text(OLDER, encoding="utf-8")
    out = dig.Client(program=SIGNED_IN.program, version="2.1.300", why="Claude Code is installed and not signed in. "
                     "Sign in once in a terminal (`claude auth login`), then come back.")
    site, _, _ = serving(project, clients=(out, CODEX_IN))
    dialog = page(get(site, "report")).one("dialog", "ask")
    check("no Start, the request to copy, and why for each", "The page cannot start one itself: Claude Code is "
          "installed and not signed in." in dialog.text and "Not yet for Codex: this project's "
          ".codex/rules/echolot.rules is from an older echolot." in dialog.text and not page(get(site, "report")).all("button", id="ask-start"),
          dialog.text)
    check("each reason on a line of its own", [s.text for s in page(get(site, "report")).one("dialog", "ask").all(
        "span", "dig-why1")] == [w.replace("`", "") for w in (out.why, dig_codex.ready(project.root))])
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    why = consent.one("div", id="dig-why").text
    check("the screen says both too, with the way out", "not signed in" in why and "older echolot" in why
          and "copy it into an agent of your own" in why and not consent.all("button", id="dig-start"), why)
    check("and copying is its one next step", consent.one("button", id="dig-copy").attrs["class"]
          == "btn primary needs-js")
    check("and offers no other client", not consent.all("form", "dig-other"))


def test_a_codex_that_cannot_start_leaves_claude_code_alone(project):
    (project.root / codex.RULE_PATH).write_text(OLDER, encoding="utf-8")
    site, codex_, echolot = serving(project, clients=(SIGNED_IN, CODEX_IN))
    first = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("Claude Code's screen, and no way to a Codex that cannot start", blocks(first)["Who does it"].startswith(
        "Claude Code") and not first.all("form", "dig-other"))
    other = page(press(site, "dig/with/codex/ask", ticked("main_thread_waits")))
    check("Codex's own screen says why, and leads back to Claude Code", "is from an older echolot" in other.one(
        "div", id="dig-why").text and "echolot init --for codex brings it up to date"
          in other.one("div", id="dig-why").text and other.one("form", "dig-other").attrs["action"]
          == f"/{site.token}/dig/with/claude/ask" and not other.all("button", id="dig-start"),
          other.one("div", id="dig-why").text)
    check("where this one cannot start, the other is the one next step, and copying the second",
          other.one("form", "dig-other").one("button").attrs["class"] == "btn primary"
          and other.one("button", id="dig-copy").attrs["class"] == "btn line needs-js")
    reply = press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    check("a press is refused the same, and nothing is opened", reply.status == 409 and "older echolot" in reply.body
          and not echolot.ran and codex_.argv is None)


def test_a_codex_that_cannot_start_says_why_beside_claude_code(project):
    """#819: Codex installed and signed in, the project set up for Claude Code
    alone. The page offered Claude Code and never said a word of Codex, the
    reason it had read included."""
    (project.root / codex.RULE_PATH).unlink()
    site, _, _ = serving(project, clients=(SIGNED_IN, CODEX_IN))
    lacks = dig_codex.ready(project.root)
    check("what Codex lacks names the file and the command that writes it",
          lacks.startswith("Not yet for Codex: this project has no `.codex/rules/echolot.rules`")
          and "`echolot init --for " in lacks and lacks.endswith(", in a terminal in the project's folder, writes it."),
          lacks)
    report = page(get(site, "report"))
    dialog = report.one("dialog", "ask")
    check("the dialog still starts Claude Code", report.one("button", id="ask-start").text == "Start it from here"
          and "Claude Code, the one on this computer, does it" in dialog.text, dialog.text)
    check("and says, beneath, why not Codex", [p.text for p in dialog.all("p", "dig-aside")]
          == [lacks.replace("`", "")], [p.text for p in dialog.all("p", "dig-aside")])
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("the screen before Claude Code starts says it too", [p.text for p in consent.all("p", "dig-aside")]
          == [lacks.replace("`", "")] and blocks(consent)["Who does it"].startswith("Claude Code"))
    check("with no button to a screen that would refuse it, and Start the one next step",
          not consent.all("form", "dig-other")
          and consent.one("button", id="dig-start").attrs["class"] == "btn primary press")
    other = page(press(site, "dig/with/codex/ask", ticked("main_thread_waits")))
    check("Codex's own screen says it once, in its callout", "Not yet for Codex" in other.one("div", id="dig-why").text
          and not other.all("p", "dig-aside"))
    absent = dig.Client(key="codex", why="Codex is not installed on this computer: there is no `codex` command here.")
    site, _, _ = serving(project, clients=(SIGNED_IN, absent))
    check("a Codex that is not installed is not mentioned", not page(get(site, "report")).one("dialog", "ask").all(
        "p", "dig-aside") and not page(press(site, "dig/ask", ticked("main_thread_waits"))).all("p", "dig-aside"))


@pytest.mark.parametrize("clients", ["both", "codex not installed"])
def test_with_no_folder_to_mark_no_client_is_offered_and_the_folder_is_said_once(project, clients):
    """#813 and #863: with `instrumentation.allowed` naming no folder, the
    dialog offered Start it from here, Claude Code's screen led to Codex's and
    Codex's back, and a press of either was refused for the folder."""
    from tests.test_ui import LAUNCH
    project.config(LAUNCH)
    absent = dig.Client(key="codex", why="Codex is not installed on this computer: there is no `codex` command here.")
    site, codex_, echolot = serving(project, clients=(SIGNED_IN, CODEX_IN if clients == "both" else absent))
    report = page(get(site, "report"))
    dialog = report.one("dialog", "ask")
    check("the dialog offers no Start", not report.all("button", id="ask-start") and "Start it from here"
          not in dialog.text and "on this computer, does it" not in dialog.text, dialog.text)
    check("it says the folder once, where to name it and how, and nothing else",
          dialog.text.count("names no folder") == 1 and "so the page starts none until it names one" in dialog.text
          and 'to echolot.yml under instrumentation:, for example allowed: ["app/src/main", "feature/*/src/main"]'
          in dialog.text and not dialog.all("span", "dig-why1") and "not installed" not in dialog.text, dialog.text)
    paths = ["dig/ask", "dig/with/claude/ask"] + (["dig/with/codex/ask"] if clients == "both" else [])
    for path in paths:
        consent = page(press(site, path, ticked("main_thread_waits")))
        why = consent.one("div", id="dig-why").text
        check(f"{path}: the folder, once", why.count("names no folder") == 1 and "not installed" not in why, why)
        check(f"{path}: no Start, and no way to another client", not consent.all("button", id="dig-start")
              and not consent.all("form", "dig-other") and not consent.all("p", "dig-aside"))
        check(f"{path}: copying the request is the one next step",
              consent.one("button", id="dig-copy").attrs["class"] == "btn primary needs-js")
    for path in ["dig/start"] + (["dig/with/codex/start"] if clients == "both" else []):
        reply = press(site, path, ticked("main_thread_waits"))
        check(f"{path}: a press is refused for the folder", reply.status == 409 and "names no folder" in reply.body,
              (reply.status, reply.body[:300]))
    check("and nothing ran", not echolot.ran and codex_.argv is None)


@pytest.mark.parametrize("path", ["dig/with/cursor/ask", "dig/with/cursor/start"])
def test_an_address_that_names_a_client_the_page_does_not_start(project, path):
    site, _, echolot = serving(project)
    reply = press(site, path, ticked("main_thread_waits"))
    check("no such page, in a sentence", reply.status == 404 and "The page starts Claude Code or Codex" in reply.body,
          (reply.status, reply.body[:300]))
    check("and nothing ran", not echolot.ran)


# --- a press -----------------------------------------------------------------------------------------

def test_a_press_opens_the_investigation_then_starts_codex_in_it(project, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    site, codex_, echolot = serving(project, {"do": "rounds", "rounds": 1})
    reply = press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    check("sent to the investigation's page", reply.status == 303 and dict(reply.headers)["Location"]
          == f"/{site.token}/hunt/1/answer", (reply.status, reply.body[:300]))
    check("the investigation was opened first", echolot.ran[0][:2] == ["hunt", dig.hunt_question(known())])
    argv = codex_.argv
    check("then Codex, with the command line the server built", argv[:-1] == dig_codex.command(
        PROGRAM, "", project.root)[:-1], argv[:-1])
    request = argv[-1]
    check("the request: the report's, then what nobody there means for Codex", request.startswith(
        "/echolot-hunt `coldStart` takes 1,920 ms") and "- /echolot-hunt is Claude Code's command; here `echolot "
        "guide hunt` prints the same steps." in request and "- The investigation is open: #1" in request
        and "with `--hunt 1`: without it nothing is filed under it." in request, request)
    check("without the names it must not find", not {"CLAUDECODE", "CODEX_SANDBOX_NETWORK_DISABLED"} & set(codex_.env))
    state = job.read(project.root, dig.JOB)
    check("its job names the client, and has no spending cap", state["what"]["client"] == "codex"
          and "budget_usd" not in state["what"])
    check("and the watchdog's plan", state["steps"][0]["idle"]["seconds"] == dig_codex.IDLE_S)
    noted = json.loads((project.root / dig_codex.SNAPSHOT).read_text(encoding="utf-8"))
    check("the project's files were noted before it started", noted["hunt"] == 1 and noted["complete"]
          and "echolot.yml" in noted["files"] and not any(f.startswith("app/src/main/") for f in noted["files"]))


# --- Codex at work -----------------------------------------------------------------------------------

def test_codex_at_work_says_its_name_and_its_phase(project):
    a_codex(project)
    site, _, _ = serving(project, table=under("/bin/zsh -lc './gradlew :app:assembleDebug'"))
    seen = panel(site)
    check("what it does now, from what runs under it", seen.one("b", id="dig-phase").text == "Building the app for a "
          "minute.", seen.one("b", id="dig-phase").text)
    check("who, for how long", seen.one("p", id="dig-ran").text == "Codex has been at it for 2 minutes, with 1 round "
          "so far.")
    check("and Stop, saying what it ends", seen.one("form").text.endswith("Ends Codex and everything it started."))


def test_nothing_moved_for_ten_minutes_says_when_the_page_stops_it(project):
    a_codex(project, ago=dig.QUIET_S + 120)
    site, _, _ = serving(project, table=under())
    quiet = panel(site).one("div", id="dig-quiet")
    check("said plainly, with the half hour", "hidden" not in quiet.attrs and quiet.text == (
        "Nothing has moved for 12 minutes: no command, no round, no build, no recording. That is how Codex waits for "
        "an answer to a question, and nobody is here to give one: if nothing moves for 30 minutes, the page stops "
        "it. Stop ends it now."), quiet.text)
    fragment = read(get(site, "dig/state").body).one("div", id="dig-now")
    check("the question once a second says the same", fragment.attrs["data-quiet"] == quiet.text)
    a_codex(project, ago=dig.QUIET_S + 120)
    site, _, _ = serving(project, table=under("./gradlew assembleDebug", age=dig.QUIET_S + 60))
    check("a build that has run as long is still named, never called a wait", panel(site).one(
        "div", id="dig-quiet").text.startswith("Nothing has changed for 11 minutes: building the app is still going on."))


def test_the_page_says_it_is_stopping_a_run_that_has_not_moved(project):
    a_codex(project, why=job.IDLE)
    site, _, _ = serving(project, table=under())
    seen = panel(site)
    check("why it is being ended, and no second Stop", seen.one("b", id="dig-phase").text == "Stopping: nothing had "
          "moved for 30 minutes, so Codex and what it started are being ended." and "disabled" in seen.one(
              "button").attrs, seen.text)


# --- after it ---------------------------------------------------------------------------------------

def test_a_codex_waiting_on_a_question_is_ended_after_half_an_hour_and_its_question_shown(project):
    offset = [0.0]

    def half_an_hour_on(process):
        # Nothing it prints, nothing under it: the clock moves past the half hour.
        offset[0] = dig_codex.IDLE_S + 60
    site, _, echolot = serving(project, {"do": "ask", "rounds": 1}, on_wait=half_an_hour_on,
                               clock=lambda: time.time() + offset[0], table=under())
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    state = job.read(project.root, dig.JOB)
    check("ended by the page, and the state says why", state["status"] == job.STOPPED and state["why"] == job.IDLE)
    seen = panel(site)
    check("said plainly", seen.text.startswith("Stopped: nothing had moved for 30 minutes. Codex had run nothing, "
          "written nothing and recorded nothing for that long. That is how it waits for an answer to a question, and "
          "nobody is here to give one, so the page ended it and everything it had started."), seen.text)
    check("with the question it was waiting on", seen.one("pre", "dig-said").text == fake_codex.QUESTION)
    check("and the investigation can be closed", [b.text for b in seen.all("button")][-1] == "Close the investigation")
    press(site, "dig/close")
    check("closed saying so", hunt_mod.find(project.root, "1")["conclusion"] == "Stopped from echolot ui once Codex had "
          "done nothing for 30 minutes, after 1 round, with no answer. Closed from echolot ui.")


def test_stopped_codex_says_what_it_changed_outside_and_what_it_left(project):
    outside = {"change": ["app/build.gradle.kts"], "add": ["notes.md"], "remove": ["settings.gradle.kts"]}
    site, _, _ = serving(project, {"do": "hang", "rounds": 1, "markers": MARKED, "outside": outside},
                         on_wait=lambda p: dig.stop(project.root))
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("stopped, in its name", seen.text.startswith("Stopped. Codex and everything it had started were ended."),
          seen.text)
    listed = seen.one("div", id="dig-outside")
    check("the files it changed outside the allowed folder, however it ended", listed.one("p").text.startswith(
        "Codex changed 3 files outside app/src/main. Its edits cannot be kept to those folders, so look at these "
        "before you keep them") and [li.text for li in listed.all("li")] == [
        "app/build.gradle.kts changed", "notes.md new", "settings.gradle.kts removed"], listed.text)
    check("the markers it put into the allowed folder are counted, not listed", MARKED not in listed.text
          and "1 temporary marker is still in the code" in seen.text)


def test_failed_says_so_in_codex_s_name_with_its_words(project):
    site, _, _ = serving(project, {"do": "fail"})
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("in the page's words", seen.attrs["data-state"] == "failed" and "Codex stopped with an error. It ended "
          "before it had an answer. What it said is below: a sign-in that ran out, or a limit of your plan, is put "
          "right in Codex itself." in seen.text, seen.text)
    check("then its own", seen.one("pre", "dig-said").text == "You've hit your usage limit. Try again later.")
    check("no tokens where its turn did not end", "tokens" not in seen.text)


def test_a_codex_run_the_page_closed_says_the_page_closed_it(project):
    """Codex wrote no verdict, the page closed the investigation, and the
    panel said Codex had closed it, under a headline that said otherwise;
    of a run that failed, that it failed after the close (#864)."""
    site, _, _ = serving(project, {"do": "rounds", "rounds": 1})
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    press(site, "dig/close")
    seen = panel(site)
    check("finished with nothing written down, and closed from the page", "Codex finished, and wrote down no answer. "
          "It left the investigation open, and it was closed from this page after that" in seen.text
          and "It closed the investigation" not in seen.text, seen.text)
    site, _, _ = serving(project, {"do": "fail"})
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    press(site, "dig/close")
    seen = panel(site, 2)
    check("failed, then closed from the page, in that order", "Codex stopped with an error. It ended before it had an "
          "answer, and the investigation was closed from this page after that." in seen.text, seen.text)


def test_ended_without_a_verdict_reports_its_tokens(project):
    site, _, _ = serving(project, {"do": "rounds", "rounds": 2})
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("it says so", "Codex finished, and wrote down no answer." in seen.text, seen.text)
    check("with the tokens it counted, how many it read again from its cache, and no money figure",
          "reports 186,550 tokens of usage, by its own count, 150,016 of them read again from its cache." in seen.text
          and "$" not in seen.text, seen.text)
    check("with its last words", seen.one("pre", "dig-said").text
          == "I looked at the report and the code, and have nothing to add.")
    check("and nothing changed outside the allowed folder, said once checked", seen.one("p", id="dig-outside").text
          .startswith("Nothing outside app/src/main was changed: the page noted the project's files when Codex started"))


def test_ended_with_a_verdict_lands_on_the_answer(project):
    site, _, _ = serving(project, {"do": "verdict"})
    reply = press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    answer = page(get(site, dict(reply.headers)["Location"][len(f"/{site.token}/"):]))
    check("the answer is the page", answer.one("h1").text == "The launch waits for the store to be read")
    seen = answer.one("section", id="dig")
    check("the agent's part is one quiet line, with its tokens", seen.text.startswith("Codex finished. Its answer is "
          "on this page.") and "reports 186,550 tokens of usage, by its own count" in seen.text, seen.text)
    check("and what it changed outside the allowed folder, nothing", "Nothing outside app/src/main was changed"
          in seen.text and not seen.all("button"))


def test_what_its_sandbox_refused_is_listed(project):
    site, _, _ = serving(project, {"do": "refused", "rounds": 1})
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    fold = panel(site).one("details")
    check("from the events, since its exit code is 0", job.read(project.root, dig.JOB)["status"] == job.DONE
          and fold.one("summary").text == "What it was refused (3)", fold.text)
    check("in Codex's terms, as it ran each", "What its sandbox refused it, or Codex declined, without asking anyone:"
          in fold.text and [li.text for li in fold.all("li")] == ["git commit -am wip", "./gradlew --version | tail -3",
                                                                  "a change to /etc/hosts"], fold.text)


def test_the_server_writes_nothing_while_it_shows_codex(project, monkeypatch):
    from tests.test_ui import snapshot
    site, _, _ = serving(project, {"do": "rounds", "rounds": 1}, clients=(SIGNED_IN, CODEX_IN))
    press(site, "dig/with/codex/start", ticked("main_thread_waits"))
    monkeypatch.setattr(dig, "changed_files", lambda root: None)
    before = snapshot(project.root)
    for path in ("", "report", "hunt/1/answer", "dig/state"):
        get(site, path)
    for path in ("dig/ask", "dig/with/codex/ask"):
        page(press(site, path, ticked("main_thread_waits")))
    check("every file is as it was, the files it noted and the project's own alike", snapshot(project.root) == before)
