#!/usr/bin/env python3
"""Dig deeper in `echolot ui`: the screen before an agent starts, the agent at
work on its investigation's page, Stop, and what is left after it.

What is pinned here, a test per state:

  - no client the page can start: no Start, and the request to copy;
  - the screen before it starts: under whose account, how long, what it
    costs, what it does, the exact list it may run and that the rest is
    refused, what changes, what Stop does, and the way out;
  - a press: the investigation opened with the question, then the client
    started as a job with the command line the server built;
  - the agent at work: each phase, from what runs under it, and nothing
    moved for about ten minutes, with Stop beside it;
  - after it: stopped with markers left and with none, failed, cut short,
    ended with no verdict, ended with one, a question nobody answered; the
    markers taken out and the investigation closed at a press;
  - one thing at a time: a check running, an agent running, a second press;
  - what the server refuses of the new requests.

A fake client (`tests/fake_claude.py`) stands in for Claude Code: it takes
the same command line, writes rounds and a verdict into the investigation as
an agent would, and is run by the real job in this process. echolot's own
commands (`hunt`, `mark --remove`) are the real ones, run here. adb is the
double of `tests/test_ui.py`. Nothing starts the real client, needs a phone or
the network, or waits on a timer.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import dig, job, recorder, ui  # noqa: E402
from echolot import hunt as hunt_mod  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot.pages import dig_page  # noqa: E402
from tests import fake_claude  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import known, read  # noqa: E402
from tests.test_ui import LAUNCH, SEVERAL, Adb, Project, get, site_of  # noqa: E402
from tests.test_ui_check import a_run  # noqa: E402

CONFIG = LAUNCH + 'instrumentation:\n  allowed: ["app/src/main"]\n  temp_prefix: AGENTTMP_\nloop:\n  max_rounds: 3\n'
PROGRAM = "/opt/example/bin/claude"
SIGNED_IN = dig.Client(program=PROGRAM, version="2.1.300", account="you@example.com")
MARKED = "app/src/main/java/com/example/app/Store.kt"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    monkeypatch.chdir(tmp_path)
    p = Project(tmp_path).app().config(CONFIG).agents().doctor().traces().report()
    for rel in dig.LAYER:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("the layer echolot init installs\n", encoding="utf-8")
    return p


class Echolot:
    """echolot's own commands as the page runs them and waits for them: the
    real ones, in this process."""

    def __init__(self):
        self.ran: list[list[str]] = []
        self.printed: list[str] = []

    def __call__(self, argv, **kw):
        check("the echolot that serves the page, by its module", argv[:3] == [sys.executable, "-m", "echolot"], argv)
        self.ran.append(list(argv[3:]))
        out, err = io.StringIO(), io.StringIO()
        here = Path.cwd()
        os.chdir(kw["cwd"])
        try:
            with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main_mod.main(list(argv[3:]))
        finally:
            os.chdir(here)
        self.printed.append(out.getvalue())
        return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())


class Table:
    """The processes of this computer, as a test names them; signals are
    recorded and nothing is sent to any real process."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sent: list[tuple[int, int]] = []

    def __call__(self):
        return list(self.rows)

    def kill(self, pid, signum):
        self.sent.append((pid, signum))


def serving(project: Project, plan: dict | None = None, client: dig.Client = SIGNED_IN, on_wait=None,
            table: Table | None = None):
    """The site, with the fake client in place of Claude Code, run to its end
    by the real job before the press is answered."""
    site = site_of(project)
    site.agent = lambda: client
    echolot = Echolot()
    site.run = echolot
    claude = fake_claude.Claude(PROGRAM, plan or {"do": "verdict"}, on_wait=on_wait)
    table = table or Table()
    site.procs = table
    site.spawn = lambda root, name, plan_: job.run(root, name, plan_, popen=claude, signals=False,
                                                   procs=table, kill=table.kill)
    return site, claude, echolot


def press(site: ui.Site, path: str, fields: dict | None = None, **headers: str) -> ui.Reply:
    sent = {"Host": site.host, "Origin": site.origin, "Sec-Fetch-Site": "same-origin", "Content-Type": ui.FORM, **headers}
    return ui.answer(site, "POST", f"/{site.token}/{path}", sent, urlencode(fields or {}).encode())


def ticked(*keys: str) -> dict:
    return {f"pick.{key}": "1" for key in keys}


def page(reply: ui.Reply):
    check("a page", reply.status == 200, (reply.status, reply.body[:400]))
    return read(reply.body)


def panel(site: ui.Site, n: int = 1):
    return page(get(site, f"hunt/{n}/answer")).one("section", id="dig")


# --- the way to it, on the report -----------------------------------------------------------------

def test_no_client_the_page_can_start_leaves_the_request_to_copy(project):
    site = site_of(project)             # the conftest's computer: no Claude Code on it
    report = page(get(site, "report"))
    dialog = report.one("dialog", "ask")
    check("the dialog has no Start", not report.all("button", id="ask-start"))
    check("it says where to paste and why the page cannot start one",
          "Paste the request into Claude Code, Codex or Cursor opened on the project. The page cannot start one "
          "itself: Claude Code is not installed on this computer" in dialog.text, dialog.text)
    check("Copy the request is its one next step", dialog.one("button", id="ask-copy").attrs["class"] == "btn primary")
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("the screen before it has no Start either", not consent.all("button", id="dig-start"))
    check("and says why, with the way out", "Claude Code is not installed on this computer" in consent.one(
        "div", id="dig-why").text and "copy it into an agent of your own" in consent.one("div", id="dig-why").text)
    check("the request to copy is there, as the report puts it together", consent.one("pre", id="dig-request").text
          .startswith("/echolot-hunt `coldStart` takes 1,920 ms"))
    check("copying it is the one next step, and nothing is said to start", consent.one("button", id="dig-copy").attrs[
        "class"] == "btn primary needs-js" and "Exactly what starts" not in consent.text)
    site.agent = lambda: dig.Client(program=PROGRAM, version="2.1.300", why="Claude Code is installed and not "
                                    "signed in. Sign in once in a terminal (`claude auth login`), then come back.")
    report = page(get(site, "report"))
    check("signed out: no Start, and the sentence", not report.all("button", id="ask-start")
          and "not signed in" in report.one("dialog", "ask").text)


def test_a_report_leads_to_the_screen_before_it_starts(project):
    site, _, _ = serving(project)
    report = page(get(site, "report"))
    form = report.one("form", id="ask-start-form")
    check("the dialog's Start goes to that screen, and starts nothing", form.attrs["action"] ==
          f"/{site.token}/dig/ask" and report.one("button", id="ask-start").text == "Start it from here")
    check("Copy the request is the second choice beside it", report.one("button", id="ask-copy").attrs["class"]
          == "btn line")
    noscript = [n for n in report.walk() if n.tag == "noscript" and "Dig deeper" in n.text]
    check("with script off the same is a form of boxes", noscript, [n.text[:80] for n in report.all("noscript")])


def test_the_report_s_form_where_script_is_off(project):
    """The boxes of the `<noscript>` form, read as a browser with script off
    reads them, send what the screen before the agent takes."""
    site, _, _ = serving(project)
    body = get(site, "report").body
    start = body.index('<noscript><section class="stack gap-3 dig-no-js"')
    form = read(body[start:body.index("</noscript>", start)].replace("<noscript>", ""))
    names = [i.attrs["name"] for i in form.all("input")]
    check("a box per check that found something, by its key", names == [
        "pick.main_thread_waits", "pick.uninstrumented_cpu", "pick.binder_txn"], names)
    check("sent to the screen before it starts", form.one("form").attrs["action"] == f"/{site.token}/dig/ask")


# --- the screen before it starts ---------------------------------------------------------------------

def test_the_screen_before_it_starts_says_what_a_person_agrees_to(project, monkeypatch):
    monkeypatch.setattr(dig, "changed_files", lambda root: 2)
    site, _, _ = serving(project)
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("the question it gets", consent.one("p", "lead").text.startswith("The question it gets: coldStart takes "
          "1,920 ms, the median of 3 launches, against a time limit of 500 ms."))
    check("the ticked checks", [li.text for li in consent.one("ul", "dig-list").all("li")]
          == ["The screen waits for background work"])
    blocks = {b.one("h2").text: b.text for b in consent.all("section", "dig-block")}
    check("the blocks, in order", list(blocks) == ["Who does it", "How long", "What it does",
                                                   "What it may do without asking", "What changes", "Stop"], list(blocks))
    check("which client and whose account", "Claude Code 2.1.300, the one installed on this computer, under your "
          "own account, signed in as you@example.com." in blocks["Who does it"], blocks["Who does it"])
    check("what it costs, with the cap's number in that very sentence", "It costs what any session of yours costs, on "
          "your Claude plan or your API key, and it stops by itself once it has spent about $10, by its own estimate;"
          in blocks["Who does it"], blocks["Who does it"])
    check("how long, and that closing the page's server stops it", "Tens of minutes" in blocks["How long"]
          and "closing echolot ui stops the agent too" in blocks["How long"])
    check("what it does: markers under the allowed folder, rebuilds, the phone by name, rounds, cleanup",
          "temporary markers into files under app/src/main, rebuilds the app, puts it on your phone (Pixel_8) and "
          "records it again, for up to 3 rounds. At the end it takes the markers out" in blocks["What it does"],
          blocks["What it does"])
    may = [li.text for li in consent.one("ul", "dig-may").all("li")]
    check("the exact list it may run without asking", may == [
        "echolot any echolot command", "./gradlew any Gradle task of the project", "adb any command on the phone",
        "edit files under app/src/main, and write .echolot/verdict.json, its answer",
        "read the project's files, and run commands that only read, such as ls or grep"], may)
    check("everything else refused, not asked", "Everything else is refused without asking anyone: git, downloads, "
          "any other program, any other file. A question it wants to ask you is refused the same way"
          in blocks["What it may do without asking"])
    check("the working tree changes, is not committed, and what is changed already is named",
          "nothing is committed" in blocks["What changes"] and "2 files are already changed and not committed"
          in blocks["What changes"])
    check("the phone ends with a marker build", "Your phone ends with a build that has the markers in it"
          in blocks["What changes"])
    check("what Stop does", "It ends Claude Code and everything it started" in blocks["Stop"]
          and "takes them out at a press" in blocks["Stop"])
    start = consent.one("form", id="dig-start-form")
    check("Start, with the ticks again, to the server's own address", start.attrs["action"] == f"/{site.token}/dig/start"
          and [(i.attrs["name"], i.attrs["value"]) for i in start.all("input")] == [("pick.main_thread_waits", "1")])
    check("and the way out", consent.one("button", id="dig-copy").text == "Copy the request"
          and consent.one("a", "btn").text == "Back to the report")
    commands = [pre.text for pre in consent.all("pre", "dig-cmd")]
    check("exactly what starts: the investigation, then the client with its grant", commands == [
        shlex.join(["echolot", "hunt", dig.hunt_question(known()), "--since", "unknown"]),
        dig.shown(dig.rules(["app/src/main"]))], commands)
    check("nothing started and nothing opened", job.read(project.root, dig.JOB) is None
          and hunt_mod.load(project.root) is None)
    check("the policy lets it send its form here", "form-action 'self'" in consent.one(
        "meta", http_equiv="Content-Security-Policy").attrs["content"])


def test_more_than_three_ticked_is_said_to_be_too_much_for_one_question(project):
    data = known()
    for key in ("gc_pressure", "monitor_contention"):
        det = next(d for d in data["detectors"] if d["id"] == key)
        det["rows"] = [{"location": "Sync Worker", "runs": "3/3", "count": 1, "total_ms": 40.0, "detail": "background"}]
        del det["plain"], det["attention"]
    del data["verdict"]
    project.report(data=data)
    site, _, _ = serving(project)
    consent = page(press(site, "dig/ask", ticked("main_thread_waits", "uninstrumented_cpu", "binder_txn",
                                                  "gc_pressure")))
    note = [c for c in consent.all("div", "callout") if "One investigation looks at one question" in c.text]
    check("one investigation looks at one question, and fewer is suggested", note and "With 4 checks ticked"
          in note[0].text and "Tick fewer" in note[0].text, [c.text for c in consent.all("div", "callout")])
    check("Start is still there: the person decides", consent.all("button", id="dig-start"))


def test_where_the_agent_may_not_edit_is_said_and_nothing_starts(project):
    project.config(LAUNCH)
    site, _, _ = serving(project)
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("no Start", not consent.all("button", id="dig-start"))
    check("and the line to add", "instrumentation.allowed in echolot.yml names no folder"
          in consent.one("div", id="dig-why").text, consent.one("div", id="dig-why").text)
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("a press is refused the same", reply.status == 409 and "names no folder" in reply.body)


# --- a press ------------------------------------------------------------------------------------

def test_a_press_opens_the_investigation_then_starts_the_client_in_it(project, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    site, claude, echolot = serving(project, {"do": "verdict"})
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("sent to the investigation's page", reply.status == 303 and dict(reply.headers)["Location"]
          == f"/{site.token}/hunt/1/answer", (reply.status, reply.body[:300]))
    question = dig.hunt_question(known())
    check("the investigation was opened first, as a person opens one", echolot.ran[0] == [
        "hunt", question, "--since", "unknown", "-c", str(project.root / "echolot.yml")], echolot.ran)
    record = hunt_mod.find(project.root, "1")
    check("with the question", record["question"] == question)
    aside = [d.name for d in (project.root / ".echolot" / "traces").iterdir() if d.is_dir()]
    check("the recordings behind the report were set aside for it", len(aside) == 1 and aside[0].startswith("coldStart-"))
    argv = claude.argv
    check("then the client, with the command line the server built", argv[0] == PROGRAM and argv[1] == "-p"
          and argv[3:] == dig.command(PROGRAM, "", dig.rules(["app/src/main"]))[3:], argv[3:])
    request = argv[2]
    check("the request: the report's question and rows, then what nobody there means", request.startswith(
        "/echolot-hunt `coldStart` takes 1,920 ms") and "- worker_pool_# · main_thread_waits" in request
        and "- The investigation is open: #1, opened for this question by the page." in request
        and f"into `.echolot/traces/{aside[0]}/`" in request, request)
    check("without the name that makes a nested session", "CLAUDECODE" not in claude.env and claude.env["PYTHONUNBUFFERED"] == "1")
    state = job.read(project.root, dig.JOB)
    check("its job says which investigation and which checks", state["what"]["hunt"] == 1
          and state["what"]["picks"] == ["main_thread_waits"] and state["steps"][0]["tree"] is True)


def test_with_several_scenarios_the_investigation_is_about_the_report_s(project):
    project.config(SEVERAL.replace("scenarios:", 'instrumentation:\n  allowed: ["app/src/main"]\nscenarios:', 1))
    project.report(".echolot/out/coldStart").report(".echolot/out/feedScroll", data={**known(), "scenario": "feedScroll"})
    project.traces("feedScroll")
    site, _, echolot = serving(project, {"do": "rounds", "rounds": 0})
    report = page(get(site, "report/feedScroll"))
    check("the report of a scenario named leads to its own screen", report.one("form", id="ask-start-form").attrs[
        "action"] == f"/{site.token}/dig/ask/feedScroll")
    consent = page(press(site, "dig/ask/feedScroll", ticked("main_thread_waits")))
    check("whose Start is about it too", consent.one("form", id="dig-start-form").attrs["action"]
          == f"/{site.token}/dig/start/feedScroll")
    reply = press(site, "dig/start/feedScroll", ticked("main_thread_waits"))
    check("started", reply.status == 303, reply.body[:300])
    check("the investigation is opened about that scenario", echolot.ran[0][-2:] == ["--scenario", "feedScroll"],
          echolot.ran[0])
    check("a scenario the config does not name is no page", press(site, "dig/ask/nowhere", ticked("x")).status == 404)


# --- the agent at work ----------------------------------------------------------------------------

def a_dig(project: Project, n: int = 1, ago: float = 120.0, pid: int | None = None, end: str | None = None,
          code: int | None = None, rounds: int = 1, log: str = "") -> None:
    """The files of an agent the page started, as the job leaves them, and
    the investigation it works in: still running under this process unless
    `end` says how it ended or `pid` names a process that is gone."""
    root, now = project.root, time.time()
    project.hunt(n, dig.hunt_question(known()), "open", reports=rounds, current=True)
    status = {"done": "done", "failed": "failed", "stopped": "stopped"}.get(end or "", "running")
    step = {"id": dig.STEP, "argv": [PROGRAM, "-p", "the request"], "shown": "claude -p '<the request>'",
            "status": status, "started": now - ago, "log": ".echolot/log/dig.agent.log", "pid": 4900, "tree": True}
    if end in ("done", "failed", "stopped"):
        step.update(finished=now - 1, exit=code if code is not None else (0 if end == "done" else 1))
    state = {"job": dig.JOB, "pid": pid or os.getpid(), "started": now - ago,
             "finished": (now - 1) if end in ("done", "failed", "stopped") else None,
             "what": {"client": "claude", "hunt": n, "question": dig.hunt_question(known()), "scenario": "coldStart",
                      "picks": ["main_thread_waits"], "report": ".echolot/out/report.json", "budget_usd": 10},
             "steps": [step]}
    if end in ("done", "failed", "stopped"):
        state["end"] = end
    folder = root / ".echolot" / "log"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dig.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "dig.agent.log").write_text(log, encoding="utf-8")
    for path in (root / ".echolot" / "hunt.json", folder / "dig.agent.log",
                 *(root / ".echolot" / "hunts" / str(n) / "reports").glob("*")):
        os.utime(path, (now - ago, now - ago))


def under(*args: str, age: float = 60.0) -> Table:
    """The agent (4900) and the processes under it, one a level below the last."""
    rows = [{"pid": 4900, "ppid": 1, "age": 900.0, "args": f"{PROGRAM} -p the request"}]
    for i, line in enumerate(args):
        rows.append({"pid": 5001 + i, "ppid": 4900 + (i and 100 + i), "age": age, "args": line})
    for i in range(1, len(rows) - 1):
        rows[i + 1]["ppid"] = rows[i]["pid"]
    return Table(rows)


@pytest.mark.parametrize("args, said", [
    ((), "Reading the report and the code, and deciding what to do next."),
    (("/bin/zsh -c ./gradlew :app:assembleDebug",), "Building the app for a minute."),
    (("/bin/zsh -c ./gradlew :app:installDebug", "java org.gradle.wrapper.GradleWrapperMain :app:installDebug"),
     "Building the app and putting it on the phone for a minute."),
    (("/bin/zsh -c echolot collect -c echolot.yml", "/usr/bin/python3 /bin/echolot collect -c echolot.yml",
      "adb shell perfetto -c -"), "Recording the app on the phone for a minute."),
    (("/usr/bin/python3 -m echolot analyze .echolot/traces/coldStart_iter000.perfetto-trace",),
     "Reading the recordings for a minute."),
    (("adb install -r app-debug.apk",), "Putting the app on the phone for a minute."),
    (("echolot mark --apply",), "Putting markers into the code for a minute."),
    (("echolot hunt --done --verdict .echolot/verdict.json",), "Writing down the answer for a minute."),
])
def test_each_phase_is_said_from_what_runs_under_the_agent(project, args, said):
    a_dig(project)
    site, _, _ = serving(project, table=under(*args))
    seen = panel(site)
    check("running, with what it does now", seen.attrs["data-state"] == "running"
          and seen.one("b", id="dig-phase").text == said, seen.one("b", id="dig-phase").text)
    check("how long it has run and its rounds so far", seen.one("p", id="dig-ran").text.startswith(
        "Claude Code has been at it for 2 minutes, with 1 round so far."))
    check("Stop, beside what it does", seen.one("form").attrs["action"] == f"/{site.token}/dig/stop"
          and seen.one("button").text == "Stop" and "disabled" not in seen.one("button").attrs)
    check("nothing has stood still for long: no watchdog", "hidden" in seen.one("div", id="dig-quiet").attrs)
    fragment = read(get(site, "dig/state").body).one("div", id="dig-now")
    check("the question once a second says the same, read from files and processes", fragment.attrs["data-state"]
          == "running" and fragment.attrs["data-said"] == said)


def test_how_long_it_has_worked_keeps_up_between_rounds(project):
    """The page is drawn anew when a round comes in; between two rounds the
    question once a second says how long it has worked, so that at minute five
    the line does not still say a minute."""
    a_dig(project, ago=70)
    site, _, _ = serving(project, table=under())
    check("drawn at the first minute", panel(site).one("p", id="dig-ran").text
          == "Claude Code has been at it for a minute, with 1 round so far.")
    a_dig(project, ago=310)
    fragment = read(get(site, "dig/state").body).one("div", id="dig-now")
    check("asked at the fifth, the answer says the fifth", fragment.attrs["data-ran"]
          == "Claude Code has been at it for 5 minutes, with 1 round so far.", fragment.attrs)
    check("and the page's script puts it in place", "ran.textContent = long" in get(site, "hunt/1/answer").body)
    a_dig(project, end="stopped")
    check("an agent that ended has no such line to keep up",
          read(get(site, "dig/state").body).one("div", id="dig-now").attrs["data-ran"] == "")


def test_the_page_of_a_running_agent_keeps_up_with_script_on_and_off(project):
    a_dig(project)
    site, _, _ = serving(project, table=under())
    body = get(site, "hunt/1/answer").body
    check("with script off it loads itself again", '<noscript><meta http-equiv="refresh" content="5"></noscript>' in body)
    check("with script on it asks how far it is", f'data-poll="/{site.token}/dig/state"' in body
          and "fetch(url" in body)
    check("the rounds so far are the answer page's own", "The rounds so far" in body and "Round 1" in body)


def test_nothing_moved_for_about_ten_minutes_is_said_beside_stop(project):
    a_dig(project, ago=dig.QUIET_S + 120)
    site, _, _ = serving(project, table=under())
    seen = panel(site)
    quiet = seen.one("div", id="dig-quiet")
    check("said plainly", "hidden" not in quiet.attrs and quiet.text.startswith("Nothing has moved for 12 minutes: "
          "no round, no build, no recording.") and "Stop ends it" in quiet.text, quiet.text)
    check("with Stop beside it", seen.one("button").text == "Stop")
    a_dig(project, ago=dig.QUIET_S + 120)
    site, _, _ = serving(project, table=under("./gradlew assembleDebug", age=dig.QUIET_S + 60))
    check("a build that has run as long is named, not called stuck", panel(site).one("div", id="dig-quiet").text
          .startswith("Nothing has changed for 11 minutes: building the app is still going on."))
    a_dig(project, ago=dig.QUIET_S + 120)
    site, _, _ = serving(project, table=under("./gradlew assembleDebug", age=30))
    check("one that just started is movement", "hidden" in panel(site).one("div", id="dig-quiet").attrs)


def test_stopping_is_said_and_stop_cannot_be_pressed_twice(project):
    a_dig(project)
    site, _, _ = serving(project, table=under())
    reply = press(site, "dig/stop")
    check("back to its page", reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/hunt/1/answer")
    check("the request is in the job's file", job.stop_path(project.root, dig.JOB).read_text() == str(os.getpid()))
    seen = panel(site)
    check("the page says it is being stopped", seen.one("b", id="dig-phase").text.startswith("Stopping:")
          and "disabled" in seen.one("button").attrs)


# --- after it ------------------------------------------------------------------------------------

def test_stopped_with_markers_left_says_what_hunt_counts_and_takes_them_out(project):
    def press_stop(process):
        check("it works in its investigation while it runs", dig.view(project.root, None)["status"] == job.RUNNING)
        dig.stop(project.root)
    site, claude, echolot = serving(project, {"do": "hang", "rounds": 2, "markers": MARKED}, on_wait=press_stop)
    check("started", press(site, "dig/start", ticked("main_thread_waits")).status == 303)
    state = job.read(project.root, dig.JOB)
    check("it was stopped, the way Ctrl-C ends it", state["status"] == job.STOPPED)
    seen = panel(site)
    text = seen.text
    check("the page's words", "Stopped. Claude Code and everything it had started were ended." in text, text)
    check("the markers still in the code, what --remove takes out", "1 temporary marker is still in the code, in 1 "
          "file. echolot mark --remove takes out 1 of them." in text and MARKED in text, text)
    check("the phone keeps a marker build", "The app on your phone may still be a build with markers in it" in text)
    check("the investigation stays open, and can be closed", "The investigation stays open with the rounds it has"
          in text and [b.text for b in seen.all("button")] == ["Take the markers out", "Close the investigation"])
    check("stopped, it printed nothing at the end, and no empty block says so", not seen.all("pre", "dig-said"))
    check("the investigation's own line says no agent works in it now", "no agent works in it now"
          in page(get(site, "hunt/1/answer")).one("p", "lead").text)
    start = page(get(site, ""))
    check("the start page says the agent ended and its investigation is open", "The agent the page started has "
          "ended, and its investigation is still open." in start.one("div", id="dig-on").text)
    check("and Check the app waits, saying why", "the investigation the page started is still open" in
          start.one("p", id="check-hint").text and "disabled" in start.one("button", id="check-go").attrs)

    reply = press(site, "dig/unmark")
    check("the removal is a press of its own", reply.status == 303 and echolot.ran[-1][:2] == ["mark", "--remove"],
          echolot.ran[-1])
    check("and the markers are out", "AGENTTMP_" not in (project.root / MARKED).read_text())
    seen = panel(site)
    check("the page counts again", "No temporary markers are left in the code." in seen.text
          and [b.text for b in seen.all("button")] == ["Close the investigation"])

    reply = press(site, "dig/close")
    check("closing is a press of its own", reply.status == 303 and echolot.ran[-1][:2] == ["hunt", "--done"])
    record = hunt_mod.find(project.root, "1")
    check("closed with what came of it", record["status"] == "concluded" and record["conclusion"]
          == "Stopped from echolot ui, after 2 rounds, with no answer. Closed from echolot ui.", record)
    check("then Check the app can run", "disabled" not in page(get(site, "")).one("button", id="check-go").attrs)
    check("and there is nothing more to close", press(site, "dig/close").status == 409)


def test_a_tagged_line_the_press_leaves_is_named_and_the_code_is_never_called_clean(project):
    """An agent's own marker: the begin on a line of its own, the end folded
    onto the call it wraps. The press takes the begin out and leaves the
    wrap; the page said no markers were left (#703)."""
    def by_hand(process):
        path = project.root / MARKED
        text = path.read_text()
        check("the fake's block ends the function", text.endswith("\n}\n"), text)
        path.write_text(text[:-len("}\n")] + '    android.os.Trace.beginSection("AGENTTMP_parse") // echolot:mark\n'
                        "    try { parse() } finally { android.os.Trace.endSection() } // echolot:mark\n}\n")
        dig.stop(project.root)
    site, _, echolot = serving(project, {"do": "hang", "rounds": 1, "markers": MARKED}, on_wait=by_hand)
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    text = seen.text
    check("both begins are markers the press takes out", "2 temporary markers are still in the code, in 1 file. "
          "echolot mark --remove takes out 2 of them." in text, text)
    check("and the wrap is said apart, as one that goes by hand", "1 line tagged // echolot:mark is still in the "
          "code, with more on it than the marker, so echolot mark --remove leaves it. Take the marker out of it by "
          "hand and keep the app's own code." in text, text)
    check("by its file and line", f"{MARKED}:9" in [li.text for li in seen.all("li")], text)
    check("and the press is there for the rest", [b.text for b in seen.all("button")]
          == ["Take the markers out", "Close the investigation"])

    press(site, "dig/unmark")
    check("the press left the wrap, and said so", f"! {MARKED}:5: try {{ parse() }}" in echolot.printed[-1],
          echolot.printed[-1])
    seen = panel(site)
    text = seen.text
    check("the page never says the code is clean while it is there", "No temporary markers are left" not in text, text)
    check("it says what is left and how it goes", "1 line tagged // echolot:mark is still in the code, with more "
          "on it than the marker, so echolot mark --remove leaves it." in text, text)
    check("at the place the press named", [li.text for li in seen.all("li")] == [f"{MARKED}:5"], text)
    check("the phone still has a build with markers in it", "build with markers in it" in text, text)
    check("and no press takes it out", [b.text for b in seen.all("button")] == ["Close the investigation"])


def test_stopped_with_no_markers_left(project):
    site, _, _ = serving(project, {"do": "hang", "rounds": 1}, on_wait=lambda p: dig.stop(project.root))
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("no markers, and no press to take them out", "No temporary markers are left in the code." in seen.text
          and [b.text for b in seen.all("button")] == ["Close the investigation"], seen.text)
    check("nor the warning about the phone", "build with markers" not in seen.text)


def test_failed_says_so_with_the_client_s_words_and_its_log(project):
    site, _, _ = serving(project, {"do": "fail"})
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("in the page's words", seen.attrs["data-state"] == "failed" and "Claude Code stopped with an error. It ended "
          "before it had an answer." in seen.text, seen.text)
    check("then its own", seen.one("pre", "dig-said").text
          == "Failed to authenticate: OAuth session expired and could not be refreshed")
    check("and where all it printed is", "Everything it printed is in .echolot/log/dig.agent.log." in seen.text)
    check("what it spent is said where it says it", "reports $0.00 of usage" in seen.text)


@pytest.mark.parametrize("code", [1, 0])
def test_a_run_that_reached_the_spending_cap_says_so(project, code):
    site, _, echolot = serving(project, {"do": "budget", "rounds": 2, "code": code})
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check(f"exit {code}: the cap is the reason, said plainly", seen.text.startswith(
        "It reached the spending cap of $10. Claude Code stopped by itself when its own estimate of what it had spent "
        "reached the cap the page sets, before it had an answer. The investigation is still open, with the rounds it "
        "has."), seen.text)
    check("what it spent, which its estimate can put past the cap", "reports $10.42 of usage, by its own estimate"
          in seen.text)
    check("its own words, which an error lists in place of a last message",
          seen.one("pre", "dig-said").text == "Reached the maximum budget ($10.00)")
    check("the investigation can be closed", [b.text for b in seen.all("button")] == ["Close the investigation"])
    press(site, "dig/close")
    check("closed saying it was the cap", hunt_mod.find(project.root, "1")["conclusion"]
          == "Claude Code stopped at its spending cap of $10, after 2 rounds, with no answer. Closed from echolot ui.")


def test_cut_short_says_the_process_is_gone(project):
    a_dig(project, pid=2 ** 22 + 1)                  # a process that is gone
    site, _, _ = serving(project)
    seen = panel(site)
    check("cut short, and why it may be", seen.attrs["data-state"] == "interrupted" and "This run was cut short. The "
          "process that ran Claude Code is gone: echolot ui was stopped, the terminal was closed, or the computer "
          "went to sleep." in seen.text, seen.text)
    check("the investigation can be closed", [b.text for b in seen.all("button")] == ["Close the investigation"])
    check("and it is not said to have run until now: its end is not on record", "It ran for" not in seen.text)


def test_ended_without_a_verdict(project):
    site, _, _ = serving(project, {"do": "rounds", "rounds": 2})
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("it says so", "Claude Code finished, and wrote down no answer. The investigation is still open, with the "
          "rounds it has." in seen.text, seen.text)
    check("with its last words", seen.one("pre", "dig-said").text
          == "I looked at the report and the code, and have nothing to add.")
    check("the rounds are the investigation's page", "Round 2" in get(site, "hunt/1/answer").body)


def test_a_question_nobody_answered_is_refused_and_said(project):
    site, _, _ = serving(project, {"do": "ask", "rounds": 1})
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("it went on and said what it would have asked", "I would have asked which build to measure"
          in seen.one("pre", "dig-said").text)
    fold = seen.one("details")
    check("and what it was refused is listed", fold.one("summary").text == "What it was refused (2)"
          and [li.text for li in fold.all("li")] == ["AskUserQuestion", "Bash: git stash"], fold.text)


def test_ended_with_a_verdict_lands_on_the_answer(project):
    site, _, _ = serving(project, {"do": "verdict"})
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    answer = page(get(site, dict(reply.headers)["Location"][len(f"/{site.token}/"):]))
    check("the answer is the page", answer.one("h1").text == "The launch waits for the store to be read")
    seen = answer.one("section", id="dig")
    check("the agent's part is one quiet line", seen.attrs["data-state"] == "done" and seen.text.startswith(
        "Claude Code finished. Its answer is on this page.") and "reports $1.23 of usage" in seen.text, seen.text)
    check("nothing to close or clear", not seen.all("button"))
    check("and the start page has nothing to say of it", not page(get(site, "")).all("div", id="dig-on"))


def test_an_investigation_closed_with_words_and_no_verdict(project):
    site, _, _ = serving(project, {"do": "text", "rounds": 1})
    press(site, "dig/start", ticked("main_thread_waits"))
    seen = panel(site)
    check("finished, with a few words", "Claude Code finished. It closed the investigation with a few words and no "
          "table of causes" in seen.text and not seen.all("button"), seen.text)


# --- one thing at a time -------------------------------------------------------------------------

def test_a_check_running_keeps_the_agent_from_starting(project):
    a_run(project)                                  # a check, recording its first launch
    site, _, echolot = serving(project)
    report = page(get(site, "report"))
    check("the dialog says which one runs", "a check is running on the phone" in report.one("dialog", "ask").text
          and not report.all("button", id="ask-start"))
    consent = page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("so does the screen before it, with no Start", "a check is running" in consent.one("div", id="dig-why").text
          and not consent.all("button", id="dig-start"))
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("a press is refused, and nothing is opened", reply.status == 409 and "a check is running" in reply.body
          and not echolot.ran and hunt_mod.load(project.root) is None)


def test_an_agent_running_keeps_a_check_from_starting_and_the_start_page_says_which(project):
    a_dig(project)
    site, _, _ = serving(project, table=under("./gradlew assembleDebug"))
    start = page(get(site, ""))
    on = start.one("div", id="dig-on")
    check("the start page says an agent is at work, and what it does", on.text.startswith(
        "An agent is digging deeper. Building the app.") and on.one("a").attrs["href"] == f"/{site.token}/hunt/1/answer")
    check("Check the app is disabled, saying why", "disabled" in start.one("button", id="check-go").attrs
          and start.one("p", id="check-hint").text.startswith("Not now: an agent is digging into this app"))
    reply = press(site, "check", {"what": "record:coldStart"})
    check("and a press of it is refused", reply.status == 409 and "an agent is digging" in reply.body)


def test_a_second_press_starts_nothing(project):
    a_dig(project)
    site, claude, echolot = serving(project)
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("refused while one runs", reply.status == 409 and "an agent is already digging" in reply.body)
    check("nothing opened, nothing started", not echolot.ran and claude.argv is None)


def test_an_investigation_open_elsewhere_is_not_taken_over(project):
    project.hunt(1, "the list stutters", "open", current=True)
    site, _, echolot = serving(project)
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("refused, naming it", reply.status == 409 and "an investigation is open" in reply.body
          and "the list stutters" in reply.body and not echolot.ran)


def test_the_markers_are_not_taken_out_while_it_works(project):
    a_dig(project)
    site, _, echolot = serving(project)
    reply = press(site, "dig/unmark")
    check("refused", reply.status == 409 and not echolot.ran)
    check("nor is its investigation closed", press(site, "dig/close").status == 409 and not echolot.ran)


def test_a_press_against_a_report_that_changed(project):
    site, _, echolot = serving(project)
    reply = press(site, "dig/start", ticked("gc_pressure"))
    check("a check that finds nothing in the report now is refused", reply.status == 409
          and "The report changed" in reply.body and not echolot.ran)


# --- what the server refuses --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["dig/ask", "dig/start", "dig/stop", "dig/unmark", "dig/close"])
def test_the_new_requests_pass_the_checks_that_are_there(project, path):
    a_dig(project, end="stopped")
    site, _, echolot = serving(project)
    fields = ticked("main_thread_waits")
    for name, headers in (("another origin", {"Origin": "http://evil.example"}),
                          ("cross-site", {"Sec-Fetch-Site": "cross-site"}),
                          ("a null origin", {"Origin": "null"})):
        reply = press(site, path, fields, **headers)
        check(f"{path}, {name}: refused", reply.status == 403, (reply.status, reply.body))
    reply = ui.answer(site, "POST", f"/{site.token}/{path}", {"Host": "evil.example:4321", "Origin": site.origin,
                                                                "Content-Type": ui.FORM}, b"")
    check(f"{path}: another Host is refused", reply.status == 403)
    reply = ui.answer(site, "POST", f"/not-the-token/{path}", {"Host": site.host, "Origin": site.origin}, b"")
    check(f"{path}: no token, no answer", reply.status == 403)
    reply = ui.answer(site, "POST", f"/{site.token}/{path}", {"Host": site.host, "Origin": site.origin,
                                                                "Content-Type": ui.FORM}, None)
    check(f"{path}: a body over the cap is not read", reply.status == 413)
    check(f"{path}: a read of it is no request for it", get(site, path).status == 405)
    check(f"{path}: nothing ran", not echolot.ran, echolot.ran)


@pytest.mark.parametrize("fields, status", [({}, 400), ({"pick.a/b": "1"}, 400), ({"pick.main_thread_waits": "x"}, 400),
                                            ({"pick.nowhere": "1"}, 409)])
def test_nothing_from_a_form_is_a_path_or_a_command(project, fields, status):
    site, _, echolot = serving(project)
    for path in ("dig/ask", "dig/start"):
        reply = press(site, path, fields)
        check(f"{path} {fields}: refused with a sentence", reply.status == status and "<h1" in reply.body,
              (reply.status, reply.body[:200]))
    check("and nothing ran", not echolot.ran)


def test_the_page_of_a_running_agent_reaches_this_server_and_nothing_else(project):
    from tests.test_report_page import outside
    a_dig(project)
    site, _, _ = serving(project, table=under())
    own = f"/{site.token}/"
    for path in ("hunt/1/answer", "report"):
        body = get(site, path).body
        strangers = [f for f in outside(body) if not re.fullmatch(rf"<(a href|form action)='{re.escape(own)}[^']*'>", f)
                     and f not in ("<form>", "<meta http-equiv=refresh>")]
        reaching = {"script: fetch(", "script: location.replace"} if path.startswith("hunt") else set()
        check(f"{path}: nothing loads, and only this server is asked", set(strangers) <= reaching, strangers)


def test_the_server_writes_nothing_while_it_shows_an_agent(project, monkeypatch):
    from tests.test_ui import snapshot
    a_dig(project, end="stopped")
    monkeypatch.setattr(dig, "changed_files", lambda root: None)
    site, _, _ = serving(project)
    before = snapshot(project.root)
    for path in ("", "report", "hunt/1/answer", "dig/state"):
        get(site, path)
    page(press(site, "dig/ask", ticked("main_thread_waits")))
    check("every file is as it was", snapshot(project.root) == before)


def test_a_client_that_did_not_start_leaves_no_investigation_open(project):
    site, _, echolot = serving(project)

    def cannot(root, name, plan):
        raise job.Refused("the process that runs it could not be started: no such file")
    site.spawn = cannot
    reply = press(site, "dig/start", ticked("main_thread_waits"))
    check("the press says so, in a sentence", reply.status == 500 and "echolot could not start it" in reply.body
          and "closed again" in reply.body, reply.body[:400])
    record = hunt_mod.find(project.root, "1")
    check("the investigation it was to work in was closed, saying why", record["status"] == "concluded"
          and record["conclusion"].startswith("Not started: echolot ui could not start Claude Code"), record)
    check("so Check the app is not held back by it", "disabled" not in page(get(site, "")).one(
        "button", id="check-go").attrs)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the scripts")
@pytest.mark.parametrize("name", dig_page.JS)
def test_the_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(Path(dig_page.__file__).parent / name)], capture_output=True, text=True)
    check(f"{name} is JavaScript", done.returncode == 0, done.stderr)


def test_the_styles_and_scripts_ship_with_the_package():
    folder = Path(dig_page.__file__).parent
    check("each is inside the package", all((folder / n).is_file() for n in dig_page.CSS + dig_page.JS))
    check("the form's script reaches out to nothing; the one that asks how far is the answer page's alone",
          "fetch(" not in (folder / "dig.js").read_text() and "fetch(" in (folder / "dig_poll.js").read_text()
          and dig_page.FORM_JS == ("dig.js",) and dig_page.POLL_JS == ("dig_poll.js",))


def test_the_served_report_has_the_time_limit_and_dig_deeper(tmp_path, monkeypatch, adb):
    """#485 puts the time limit beside the verdict of the same page: both are
    there, the limit where #485 put it and Dig deeper where this puts it."""
    from echolot.pages import limit_page
    from tests.test_ui_limit import written
    monkeypatch.chdir(tmp_path)
    made = Project(tmp_path)
    written(made)
    made.traces().report()
    for rel in dig.LAYER:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("the layer echolot init installs\n", encoding="utf-8")
    site, _, _ = serving(made)
    body = get(site, "report").body
    shown = page(get(site, "report"))
    check("the time limit beside the verdict", shown.all("section", id=limit_page.ID)
          and body.index(f'id="{limit_page.ID}"') < body.index('class="tb report"'))
    check("the way to the agent in the request dialog", shown.one("form", id="ask-start-form").attrs["action"]
          == f"/{site.token}/dig/ask")
    check("and the same as boxes where script is off", 'class="stack gap-3 dig-no-js"' in body)
    check("each brings its own styles", "/* Dig deeper from the page" in body and "lim" in body)
