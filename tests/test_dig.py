#!/usr/bin/env python3
"""The launcher behind Dig deeper (`echolot/dig.py`), piece by piece.

What is pinned here:

  - whether Claude Code can be started from here: not installed, too old,
    signed out, signed in, and a status that says nothing;
  - the grant and the command line, as text, for one allowed folder and for
    several: no permission bypassed, edits only under the allowed folders
    and of the verdict file, nobody to answer a prompt, and an allowed
    folder the page cannot put into a rule refused by name;
  - the request an agent started from the page is handed;
  - what a press may name: ticked checks, matched against the report on disk;
  - the phase, read from the processes under the agent, and when anything
    last moved;
  - the one object Claude Code prints at the end, read back;
  - with real processes, and the fake client of `tests/fake_claude.py` in
    place of Claude Code: Stop ends the client and a child it left running,
    and a client that ends by itself leaves nothing in its group.

No test starts the real client, needs a phone or the network, or waits on a
timer: the real processes are waited for by their exit and by a pipe.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import dig, job  # noqa: E402
from echolot.check import Refusal  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import known  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_claude.py"


def said(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["claude"], code, stdout=stdout, stderr="")


def client_that(monkeypatch, version: str = "2.1.300 (Claude Code)", status: str | None = '{"loggedIn": true}'):
    """Claude Code as a test has it: on the PATH, answering the two questions."""
    asked = []
    monkeypatch.setattr(dig, "_which", lambda name: f"/opt/example/bin/{name}")

    def run(argv):
        asked.append(argv[1:])
        if argv[1:] == ["--version"]:
            return said(version)
        return said(status) if status is not None else None
    monkeypatch.setattr(dig, "_run", run)
    return asked


# --- can it be started from here ------------------------------------------------------------------

def test_no_claude_code_on_this_computer():
    found = dig.find()
    check("not there, and it says so", not found.can and found.program is None
          and found.why == "Claude Code is not installed on this computer: there is no `claude` command here.", found)


def test_an_old_claude_code_is_not_started(monkeypatch):
    client_that(monkeypatch, version="2.1.200 (Claude Code)")
    found = dig.find()
    check("too old to be told that nobody answers", not found.can and found.version == "2.1.200"
          and "older than 2.1.259" in found.why and "Update it" in found.why, found)


def test_a_claude_code_that_says_no_version(monkeypatch):
    client_that(monkeypatch, version="")
    check("it is not started blind", "did not say which version" in dig.find().why)


def test_a_signed_out_claude_code_is_not_started(monkeypatch):
    asked = client_that(monkeypatch, status='{"loggedIn": false, "authMethod": "none"}')
    found = dig.find()
    check("installed, signed out, and the way to sign in", not found.can and found.why ==
          "Claude Code is installed and not signed in. Sign in once in a terminal (`claude auth login`), then come back.")
    check("it was asked its version and whether it is signed in, nothing else", asked == [["--version"], ["auth", "status"]])


@pytest.mark.parametrize("status", [None, "", "not json", "[]"])
def test_a_status_that_says_nothing_readable(monkeypatch, status):
    client_that(monkeypatch, status=status)
    check("not started on a guess", "did not say whether it is signed in" in (dig.find().why or ""))


def test_a_signed_in_claude_code_with_its_account(monkeypatch):
    client_that(monkeypatch, status='{"loggedIn": true, "email": "you@example.com"}')
    found = dig.find()
    check("it can be started, under that account", found.can and found.account == "you@example.com"
          and found.program == "/opt/example/bin/claude" and found.version == "2.1.300", found)
    client_that(monkeypatch, status='{"loggedIn": true}')
    check("an account it does not name is not made up", dig.find().account is None)


def test_the_client_is_asked_as_no_nested_session(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("KEPT", "yes")
    env = dig._quiet_env()
    check("without the name a nested session reads", "CLAUDECODE" not in env and env["KEPT"] == "yes")


# --- the grant and the command line, as text -------------------------------------------------------

def config(tmp_path: Path, allowed: str) -> Config:
    path = tmp_path / "echolot.yml"
    path.write_text("project:\n  package: com.example.app\n  process: com.example.app\nscenario:\n  name: coldStart\n"
                    f"  window: launch\ninstrumentation:\n  allowed: {allowed}\n", encoding="utf-8")
    return Config.load(path)


def test_the_command_line_and_the_grant_for_one_allowed_folder(tmp_path):
    grant = dig.rules(dig.allowed(config(tmp_path, '["app/src/main"]')))
    check("three commands, edits under the one folder, the verdict file", grant == [
        "Bash(echolot *)", "Bash(./gradlew *)", "Bash(adb *)", "Edit(/app/src/main/**)", "Edit(/.echolot/verdict.json)"],
        grant)
    check("the command line, as text", shlex.join(dig.command("claude", "<the request>", grant)) == (
        "claude -p '<the request>' --output-format json --permission-mode dontAsk --permission-prompts none "
        "--setting-sources project --max-budget-usd 10 --allowedTools 'Bash(echolot *)' 'Bash(./gradlew *)' "
        "'Bash(adb *)' 'Edit(/app/src/main/**)' 'Edit(/.echolot/verdict.json)'"), dig.shown(grant))
    check("and as a person reads it beside the request", dig.shown(grant) == shlex.join(
        dig.command("claude", "<the request>", grant)))


def test_the_grant_for_several_allowed_folders(tmp_path):
    paths = dig.allowed(config(tmp_path, '["app/src/main", "feature/*/src/main", "./core/src/main/", "app/src/main"]'))
    check("each as a rule can name it, once", paths == ["app/src/main", "feature/*/src/main", "core/src/main"], paths)
    check("an edit rule each, anchored at the project", dig.rules(paths)[3:] == [
        "Edit(/app/src/main/**)", "Edit(/feature/*/src/main/**)", "Edit(/core/src/main/**)", "Edit(/.echolot/verdict.json)"])
    check("the whole project, where the config says so", dig.rules(dig.allowed(config(tmp_path, '["."]')))[3]
          == "Edit(/**)")
    check("a single folder written as text, not a list", dig.allowed(config(tmp_path, "app/src/main")) == ["app/src/main"])
    check("a doubled slash is one, as `echolot mark` reads it (#839)",
          dig.allowed(config(tmp_path, '["app//src/main"]')) == ["app/src/main"])


def test_no_permission_is_bypassed_and_nobody_is_waited_for(tmp_path):
    argv = dig.command("claude", "x", dig.rules(["app/src/main"]))
    check("no flag that skips permissions", not [a for a in argv if "dangerously" in a or a == "bypassPermissions"])
    check("what no rule allows is refused, not asked",
          argv[argv.index("--permission-mode") + 1] == "dontAsk" and argv[argv.index("--permission-prompts") + 1] == "none")
    check("the user's own settings stay out, the project's commands come in",
          argv[argv.index("--setting-sources") + 1] == "project")
    check("the request comes before the rules, which take every word after them",
          argv[1:3] == ["-p", "x"] and argv[argv.index("--allowedTools") + 1:] == dig.rules(["app/src/main"]))


@pytest.mark.parametrize("entry", ["../elsewhere", "/abs/path", "my folder", "src/(main)", "a/../b", "a;b"])
def test_an_allowed_folder_the_page_cannot_put_into_a_rule_is_refused(tmp_path, entry):
    with pytest.raises(Refusal) as refused:
        dig.allowed(config(tmp_path, json.dumps([entry])))
    check("refused, naming it", f"`{entry}`" in refused.value.text, refused.value.text)


@pytest.mark.parametrize("allowed", ["[]", "null"])
def test_no_allowed_folder_is_no_grant(tmp_path, allowed):
    with pytest.raises(Refusal) as refused:
        dig.allowed(config(tmp_path, allowed))
    check("the page hands an agent no edits anywhere", "names no folder" in refused.value.text)


# --- the request ----------------------------------------------------------------------------------

def test_the_request_of_an_agent_started_from_the_page():
    picks = [c for c in dig.report_page.checks(known()) if c["key"] == "main_thread_waits"]
    text = dig.request(known(), picks, ".echolot/out/report.json", 4, (".echolot/traces/coldStart-20261122-101500", 3),
                       ["app/src/main", "feature/*/src/main"])
    copy = dig.report_page.request_text(known(), picks, ".echolot/out/report.json")
    check("it is the request a person would copy, first", text.startswith(copy + "\n\n"))
    rest = text[len(copy) + 2:].split("\n")
    check("then that nobody can answer, and not to ask", rest[0].startswith("This run was started from echolot ui, "
          "and nobody is at the keyboard.") and "So do not ask." in rest[0])
    check("which investigation it works in", rest[1] == "- The investigation is open: #4, opened for this question by "
          "the page. Carry on in it, do not open another, and close it at the end. Run every `collect` and "
          "`analyze` in it with `--hunt 4`: without it nothing is filed under it.")
    check("round 1 is the recordings set aside, filed as one round by one analyze naming them all; every later "
          "round records anew", rest[2] == (
        "- Round 1's evidence is the 3 recordings behind the report, moved aside when it opened, into "
        "`.echolot/traces/coldStart-20261122-101500/`: file them as one round, with a single `echolot analyze "
        "--hunt 4` that names all 3 trace files there (it takes files, not a folder), and do not record them again. "
        "Every later round records anew, once markers are in: build and install the app, then "
        "`echolot collect --hunt 4` and `echolot analyze --hunt 4`."), rest[2])
    check("after which change, with nothing to set the build against", rest[3] == (
        "- After which change: unknown. Reference: none — no earlier build was recorded to set this one against, "
        "so the answer is where this build spends its time, not what changed."), rest[3])
    check("what it may do, and that the rest is refused", rest[4] == "- You may run `echolot`, `./gradlew` and `adb` "
          "without asking, and edit files under `app/src/main` and `feature/*/src/main`, and `.echolot/verdict.json`. "
          "Everything else is refused: git, any other program, any other file. Do not commit anything.")
    check("one command per call, since a loop or a chain with another program is refused",
          rest[5] == "- Run one command per call: a loop, or a chain with another program, is refused, and each "
                     "refusal costs a turn.")
    check("and how it ends", rest[6] == "- Close the investigation with your verdict, as /echolot-hunt says: "
          "`echolot hunt --done --verdict .echolot/verdict.json`.")
    one = dig.facts(4, (".echolot/traces/coldStart-20261122-101500", 1))[1]
    check("one recording set aside is said as one", one.startswith("- Round 1's evidence is the recording behind the "
          "report,") and "file it with `echolot analyze --hunt 4` and the trace file there (it takes files, not a "
          "folder), and do not record it again. Every later round records anew" in one, one)
    bare = dig.unattended(2, None, [""])
    check("with no recordings set aside, round 1 records them and every later round anew; the whole project is "
          "named so", "- No recordings were set aside when it opened: round 1 records them, with `echolot collect "
          "--hunt 2` and then `echolot analyze --hunt 2`, and every later round records anew, once markers are in: "
          "build and install the app, then the same two." in bare and "edit files under `.`" in bare, bare)


def test_the_investigation_s_question_is_one_line_its_page_can_head():
    from tests.test_report_page import entry, make, row
    from echolot import plain
    check("over the limit", dig.hunt_question(known()) == "What makes coldStart take 1,920 ms against a time limit "
          "of 500 ms?")
    within = plain.annotate(make([entry("main_thread_waits", [row("worker", 700.0)])], budget=3000))
    check("within it", dig.hunt_question(within) == "What is behind the 2,000 ms coldStart takes, within its time "
          "limit of 3,000 ms?")
    free = plain.annotate(make([entry("main_thread_waits", [row("worker", 700.0)])]))
    check("with none", dig.hunt_question(free) == "What is behind the 2,000 ms coldStart takes?")
    none = plain.annotate(make([entry("main_thread_waits", [row("worker", 700.0)])], duration=0))
    check("with no window", dig.hunt_question(none) == "What is behind what the report found in coldStart?")


# --- what a press may name -----------------------------------------------------------------------

def test_a_press_names_ticked_checks_by_key():
    check("a field each, and anything else is no tick",
          dig.asked({"pick.main_thread_waits": "1", "pick.binder_txn": "on", "what": "x"}) == ["main_thread_waits", "binder_txn"])


@pytest.mark.parametrize("form", [{}, {"what": "x"}, {"pick.": "1"}, {"pick.a/b": "1"}, {"pick.x y": "1"},
                                  {"pick.main_thread_waits": "yes"}, {"pick." + "a" * 65: "1"}])
def test_a_press_that_names_nothing_it_may_is_refused(form):
    with pytest.raises(Refusal) as refused:
        dig.asked(form)
    check("a 400 with a sentence", refused.value.status == 400 and refused.value.text)


def test_ticked_checks_are_matched_against_the_report_on_disk():
    picks = dig.picked(known(), ["binder_txn", "main_thread_waits"])
    check("in the report's own order", [c["key"] for c in picks] == ["main_thread_waits", "binder_txn"])
    for key in ("gc_pressure", "nowhere"):
        with pytest.raises(Refusal) as refused:
            dig.picked(known(), [key])
        check(f"{key}: one with nothing to show, or none, means the report changed",
              refused.value.title == "The report changed")


# --- the phase -----------------------------------------------------------------------------------

@pytest.mark.parametrize("args, key", [
    ("", "think"),
    ("/bin/zsh -c ./gradlew :app:assembleDebug", "build"),
    ("java -cp gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain :app:installDebug", "install"),
    ("/usr/bin/python3 /opt/example/bin/echolot collect -c echolot.yml -n 3", "record"),
    ("/usr/bin/python3 -m echolot analyze .echolot/traces/a.perfetto-trace", "read"),
    ("echolot compare .echolot/hunts/1/reports/001.json", "compare"),
    ("adb install -r app/build/outputs/apk/debug/app-debug.apk", "put"),
    ("adb shell am force-stop com.example.app", "phone"),
    ("echolot mark --apply", "mark"),
    ("echolot mark --remove", "unmark"),
    ("echolot hunt --done --verdict .echolot/verdict.json", "answer"),
    ("echolot hunt --verdict .echolot/verdict.json", "answer"),
    ("/usr/bin/python3 /opt/example/bin/echolot hunt", "look"),
    ("echolot hunt --show 1", "look"),
    ("echolot hunt --list", "look"),
    ("echolot doctor -q", "echolot"),
    ("grep -rn AGENTTMP_ .", "think"),
])
def test_the_phase_is_what_runs_under_the_agent(args, key):
    table = [{"pid": 4900, "ppid": 1, "age": 600.0, "args": "claude -p"}]
    if args:
        table.append({"pid": 5001, "ppid": 4900, "age": 42.0, "args": args})
    found = dig.phase(4900, table)
    check(f"{args or 'nothing'}: {key}", found["key"] == key, found)
    if key != "think":
        check("and for how long", found["for"] == 42.0)


def test_a_recording_is_said_over_the_adb_it_runs_and_only_what_is_under_the_agent_counts():
    table = [{"pid": 4900, "ppid": 1, "age": 600.0, "args": "claude -p"},
             {"pid": 5001, "ppid": 4900, "age": 90.0, "args": "/bin/zsh -c echolot collect -c echolot.yml"},
             {"pid": 5002, "ppid": 5001, "age": 89.0, "args": "/usr/bin/python3 /bin/echolot collect -c echolot.yml"},
             {"pid": 5003, "ppid": 5002, "age": 3.0, "args": "adb shell perfetto -c -"},
             {"pid": 7000, "ppid": 1, "age": 5.0, "args": "./gradlew assembleDebug"}]
    check("the recording, not the phone it drives", dig.phase(4900, table)["key"] == "record")
    check("a build that is not the agent's is none of its phase", dig.phase(4900, table[:1] + table[4:])["key"] == "think")
    check("no process known: thinking", dig.phase(None, table)["key"] == "think")


def test_when_anything_last_moved(tmp_path):
    now = 10_000.0
    run = {"started": now - 3000, "what": {"hunt": 4}, "steps": [{"id": "agent", "started": now - 2990, "pid": 4900}]}
    check("from the start, when nothing else is there", dig.moved(tmp_path, run, [], now) == now - 2990)
    record = tmp_path / ".echolot" / "hunt.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}", encoding="utf-8")
    os.utime(record, (now - 900, now - 900))
    check("the investigation's record written", dig.moved(tmp_path, run, [], now) == now - 900)
    report = tmp_path / ".echolot" / "hunts" / "4" / "reports" / "001.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    os.utime(report, (now - 600, now - 600))
    check("a report filed", dig.moved(tmp_path, run, [], now) == now - 600)
    table = [{"pid": 5001, "ppid": 4900, "age": 30.0, "args": "./gradlew assembleDebug"}]
    check("a process that started under it", dig.moved(tmp_path, run, table, now) == now - 30)


# --- the end of a run ------------------------------------------------------------------------------

def test_the_result_is_the_one_object_it_printed_at_the_end(tmp_path):
    log = job.log_path(tmp_path, dig.JOB, dig.STEP)
    log.parent.mkdir(parents=True)
    log.write_text("a line on stderr\n" + json.dumps({
        "type": "result", "is_error": False, "result": "done", "total_cost_usd": 3.104,
        "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "git status"}},
                               {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}},
                               {"tool_name": "AskUserQuestion", "tool_input": {}}, "junk"]}) + "\n", encoding="utf-8")
    found = dig.result(tmp_path)
    check("read back", found["result"] == "done")
    check("what it spent", dig.cost(found) == "$3.10")
    check("what it was refused, as it tried it",
          dig.denied(found) == ["Bash: git status", "Write: /etc/hosts", "AskUserQuestion"], dig.denied(found))
    log.write_text("Killed\n", encoding="utf-8")
    check("a run that printed none has none", dig.result(tmp_path) is None and dig.cost(None) is None)


def test_a_run_stopped_at_the_spending_cap_is_known_by_what_claude_code_says():
    check("by the result's subtype", dig.capped({"subtype": "error_max_budget_usd", "is_error": True}))
    check("or by why it ended", dig.capped({"subtype": "error_during_execution", "terminal_reason": "budget_exhausted"}))
    check("an error of another kind is not the cap", not dig.capped({"subtype": "error_during_execution"})
          and not dig.capped({"subtype": "success", "total_cost_usd": 12.0}) and not dig.capped(None))
    check("its words: the last message, or the errors in place of one",
          dig.last_words({"result": " done \n"}) == "done"
          and dig.last_words({"result": None, "errors": ["Reached the maximum budget", ""]}) == "Reached the maximum budget"
          and dig.last_words(None) == "")


def test_the_words_an_investigation_is_closed_with():
    check("stopped", dig.closing_words({"status": job.STOPPED, "rounds": 2})
          == "Stopped from echolot ui, after 2 rounds, with no answer. Closed from echolot ui.")
    check("an error before any round", dig.closing_words({"status": job.FAILED, "rounds": 0})
          == "Claude Code ended with an error, before its first round, with no answer. Closed from echolot ui.")
    check("cut short", dig.closing_words({"status": job.INTERRUPTED, "rounds": 1}).startswith(
        "Cut short, the process that ran Claude Code gone, after 1 round,"))
    check("finished with nothing", dig.closing_words({"status": job.DONE, "rounds": 3}).startswith(
        "Claude Code finished and wrote down nothing, after 3 rounds"))


# --- real processes, with the fake client ------------------------------------------------------------

def fake_step(tmp_path: Path) -> dict:
    argv = [sys.executable, str(FAKE)] + dig.command("claude", "the request", dig.rules(["app/src/main"]))[1:]
    return {"id": dig.STEP, "argv": argv, "shown": dig.shown(dig.rules(["app/src/main"])), "tree": True,
            "unset": list(dig.UNSET)}


def started(tmp_path: Path, monkeypatch, plan: dict) -> threading.Thread:
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps(plan))
    monkeypatch.setenv("CLAUDECODE", "1")
    return job._spawn(tmp_path, dig.JOB, {"steps": [fake_step(tmp_path)], "what": {"hunt": None}})


@pytest.mark.skipif(os.name != "posix", reason="process groups and signals are POSIX")
def test_stop_ends_a_real_client_and_the_child_it_left_running(tmp_path, monkeypatch):
    pipe = tmp_path / "child"
    os.mkfifo(pipe)
    seen = tmp_path / "seen.json"
    reaper = started(tmp_path, monkeypatch, {"do": "hang", "rounds": 0, "child_pipe": str(pipe), "seen": str(seen)})
    with open(pipe, encoding="utf-8") as child:
        pid = int(child.readline())           # the child runs, and the client is about to wait
        check("the client was asked as no nested session", json.loads(seen.read_text())["env"]["CLAUDECODE"] is None)
        check("asked to stop", dig.stop(tmp_path))
        check("the child ended: its end of the pipe is closed", child.read() == "")
    reaper.join(timeout=120)
    state = job.read(tmp_path, dig.JOB)
    check("the job says stopped", state["status"] == job.STOPPED and state["steps"][0]["status"] == job.STOPPED, state)
    check("and the child is not running any more",
          not [p for p in job.processes() if p["pid"] == pid and "while True" in p["args"]])


@pytest.mark.skipif(os.name != "posix", reason="process groups and signals are POSIX")
def test_a_real_client_that_ends_by_itself_leaves_nothing_in_its_group(tmp_path, monkeypatch):
    pipe = tmp_path / "child"
    os.mkfifo(pipe)
    reaper = started(tmp_path, monkeypatch, {"do": "nothing", "rounds": 0, "child_pipe": str(pipe)})
    with open(pipe, encoding="utf-8") as child:
        child.readline()
        check("the child it left ended with it", child.read() == "")
    reaper.join(timeout=120)
    state = job.read(tmp_path, dig.JOB)
    check("the client ended well, its last object printed", state["status"] == job.DONE
          and dig.result(tmp_path)["result"].startswith("I looked at the report"), state)


def test_the_fake_client_takes_the_command_line_the_page_builds_and_no_other(tmp_path):
    from tests import fake_claude
    argv = dig.command("claude", "x", dig.rules(["app/src/main"]))
    check("the page's own", fake_claude.parse(argv[1:]).request == "x")
    for wrong in (argv[1:] + ["--dangerously-skip-permissions"], [a for a in argv[1:] if a != "dontAsk"],
                  ["-p", "x", "--output-format", "text"]):
        with pytest.raises(fake_claude.Refused):
            fake_claude.parse(wrong)
