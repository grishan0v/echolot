#!/usr/bin/env python3
"""The Codex adapter of the launcher behind Dig deeper (`echolot/dig_codex.py`).

What is pinned here:

  - whether Codex can be started from here: not installed, too old, no
    version, signed out, signed in with ChatGPT or with an API key;
  - whether the project can have it: a git repository, not marked untrusted,
    and the rules `init --for codex` writes, as they are now, older, or
    edited;
  - the command line, as text: no flag that bypasses approvals or the
    sandbox, the project as its working root, the request last;
  - the request a Codex started from the page is handed;
  - its events read back: its last message, the tokens of its turn, what its
    sandbox refused or Codex declined, and the errors of a turn that failed;
  - the files it changed outside the allowed folders, against what the page
    noted when it started;
  - the watchdog's plan;
  - with real processes and the fake client of `tests/fake_codex.py`: Stop
    ends the client and a child it left in a process group of its own, which
    a signal to the client's group would not reach;
  - the table of clients, and the order the page offers them in.

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
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import codex, dig, dig_codex, job  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_codex import OLDER  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_codex.py"
PROGRAM = "/opt/example/bin/codex"


def said(stdout: str = "", code: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["codex"], code, stdout=stdout, stderr=stderr)


def codex_that(monkeypatch, version: str = "codex-cli 0.159.2", status=("", 0, "Logged in using ChatGPT")):
    """Codex as a test has it: on the PATH, answering the two questions. It
    says whether it is signed in on stderr, as the real one does."""
    asked = []
    monkeypatch.setattr(dig, "_which", lambda name: f"/opt/example/bin/{name}")

    def run(argv):
        asked.append(argv[1:])
        if argv[1:] == ["--version"]:
            return said(version)
        return said(status[0], status[1], status[2]) if status is not None else None
    monkeypatch.setattr(dig, "_run", run)
    return asked


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """A Codex home of its own: no test reads the one of this computer."""
    h = tmp_path / "codex-home"
    h.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(h))
    return h


@pytest.fixture
def project(tmp_path, home) -> Path:
    """A git repository with the rules this echolot writes."""
    p = tmp_path / "app"
    (p / ".git").mkdir(parents=True)
    (p / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (p / codex.RULE_PATH).parent.mkdir(parents=True)
    (p / codex.RULE_PATH).write_text(codex.RULE_FILE, encoding="utf-8")
    return p


# --- can it be started from here ---------------------------------------------------------------------

def test_no_codex_on_this_computer():
    found = dig_codex.find()
    check("not there, and it says so", not found.can and found.key == "codex" and found.why
          == "Codex is not installed on this computer: there is no `codex` command here.", found)


def test_an_old_codex_is_not_started(monkeypatch):
    codex_that(monkeypatch, version="codex-cli 0.120.4")
    found = dig_codex.find()
    check("too old for the page to read what it prints", not found.can and found.version == "0.120.4"
          and "older than 0.159.0" in found.why and "Update it" in found.why, found)
    codex_that(monkeypatch, version="")
    check("one that says no version is not started blind", "did not say which version" in dig_codex.find().why)


def test_a_signed_out_codex_is_not_started(monkeypatch):
    asked = codex_that(monkeypatch, status=("", 1, "Not logged in"))
    found = dig_codex.find()
    check("installed, signed out, and the way to sign in", not found.can and found.why ==
          "Codex is installed and not signed in. Sign in once in a terminal (`codex login`), then come back.", found)
    check("it was asked its version and whether it is signed in, nothing else",
          asked == [["--version"], ["login", "status"]], asked)
    codex_that(monkeypatch, status=None)
    check("a status that did not come is no guess", "did not say whether it is signed in" in dig_codex.find().why)


def test_a_signed_in_codex_says_how_and_never_its_key(monkeypatch):
    codex_that(monkeypatch)
    found = dig_codex.find()
    check("it can be started, signed in with ChatGPT", found.can and found.how == "with ChatGPT"
          and found.program == PROGRAM and found.version == "0.159.2" and found.account is None, found)
    codex_that(monkeypatch, status=("", 0, "Logged in using an API key - sk-proj-***ABCD"))
    found = dig_codex.find()
    check("with an API key: said so, and the key is nowhere", found.how == "with an API key"
          and "sk-" not in repr(found), found)


# --- can this project have it ----------------------------------------------------------------------

def test_a_project_with_the_rules_this_echolot_writes_can_have_it(project):
    check("ready", dig_codex.ready(project) is None, dig_codex.ready(project))


def test_a_project_that_is_no_git_repository_is_not_one_codex_starts_in(tmp_path, home):
    bare = tmp_path / "copy"
    (bare / codex.RULE_PATH).parent.mkdir(parents=True)
    (bare / codex.RULE_PATH).write_text(codex.RULE_FILE, encoding="utf-8")
    why = dig_codex.ready(bare)
    check("said, with the way to make it one", why and why.startswith("Not here: Codex starts only in a git "
          "repository") and "`git init`, then a first commit" in why, why)


def test_a_project_the_person_marked_untrusted_is_left_alone(project, home):
    (home / "config.toml").write_text(f'[projects."{project.resolve()}"]\ntrust_level = "untrusted"\n',
                                      encoding="utf-8")
    why = dig_codex.ready(project)
    check("the page does not overrule the person's own settings", why and "mark this project untrusted" in why
          and "Change that in Codex first" in why, why)
    (home / "config.toml").write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n',
                                      encoding="utf-8")
    check("trusted is fine", dig_codex.ready(project) is None)


def test_rules_that_are_not_there_older_or_edited_are_said_with_the_init_that_puts_them_right(project):
    rule = project / codex.RULE_PATH
    rule.unlink()
    why = dig_codex.ready(project)
    check("none: init writes them", why.startswith("Not yet for Codex: this project has no `.codex/rules/echolot.rules`")
          and "`echolot init --for agents,codex`" in why, why)
    rule.write_text(OLDER, encoding="utf-8")
    why = dig_codex.ready(project)
    check("older: it lets echolot out, and not ./gradlew or adb", "is from an older echolot" in why
          and "not `./gradlew` or `adb`" in why and "brings it up to date" in why, why)
    rule.write_text(codex.RULE_FILE.replace('pattern = ["adb"]', 'pattern = ["adb", "devices"]'), encoding="utf-8")
    why = dig_codex.ready(project)
    check("edited: what it no longer lets out, by name", "was edited here, and does not let `adb` out" in why, why)
    rule.write_text(codex.RULE_FILE + "\n# a line of the team's own\n", encoding="utf-8")
    check("an edit that keeps the three rules is fine", dig_codex.ready(project) is None)


# --- the command line, as text ------------------------------------------------------------------------

def test_the_command_line_as_text(project):
    argv = dig_codex.command("codex", "<the request>", project)
    check("the command line", shlex.join(argv) == shlex.join(
        ["codex", "exec", "--json", "-s", "workspace-write", "-C", str(project.absolute()), "<the request>"]), argv)
    check("and as a person reads it", dig_codex.shown(project) == shlex.join(argv))
    check("the adapter builds the same", dig.adapter("codex").argv("codex", "<the request>", ["app/src/main"], project)
          == argv)


def test_no_approval_or_sandbox_is_bypassed():
    argv = dig_codex.command("codex", "x", Path("/work/app"))
    check("no flag that bypasses them", not [a for a in argv if "dangerously" in a or a in (
        "--full-auto", "--yolo", "--skip-git-repo-check", "--approve-for-me", "danger-full-access", "-c")], argv)
    check("the sandbox that writes inside the project", argv[argv.index("-s") + 1] == "workspace-write")
    check("events as JSON Lines, and the request last", "--json" in argv and argv[-1] == "x")


# --- the request ------------------------------------------------------------------------------------

def test_the_request_of_a_codex_started_from_the_page():
    text = dig_codex.unattended(4, (".echolot/traces/coldStart-20261122-101500", 3), ["app/src/main"])
    lines = text.split("\n")
    check("nobody can answer, and a run that waits is ended", lines[0].startswith("This run was started from echolot "
          "ui, and nobody is at the keyboard.") and "a run that waits for an answer is ended" in lines[0]
          and "So do not ask, and do not wait." in lines[0], lines[0])
    check("/echolot-hunt is Claude Code's: here the guide says the same", lines[1] == "- /echolot-hunt is Claude "
          "Code's command; here `echolot guide hunt` prints the same steps. Run it first, and follow it.")
    check("the three facts and the investigation, as for Claude Code", lines[2:5] == dig.unattended(
        4, (".echolot/traces/coldStart-20261122-101500", 3), ["app/src/main"]).split("\n")[1:4], lines[2:5])
    check("from the one place both are told them", lines[2:5] == dig.facts(
        4, (".echolot/traces/coldStart-20261122-101500", 3)))
    check("so it files its rounds under the investigation, as `--hunt` alone does", "Run every `collect` and "
          "`analyze` in it with `--hunt 4`: without it nothing is filed under it." in lines[2], lines[2])
    check("the recordings set aside are round 1, filed by name, and every later round records anew: a live Codex "
          "took 'do not record them again' for 'record nothing'", "naming each trace file there (it takes files, not a "
          "folder), and do not record them again. Every later round records anew, once markers are in: build and "
          "install the app, then `echolot collect --hunt 4` and `echolot analyze --hunt 4`." in lines[3], lines[3])
    check("what the rules let out, what it may edit, and that the rest is listed", lines[5] == "- The rules in "
          "`.codex/rules/echolot.rules` let `echolot`, `./gradlew` and `adb` out of your sandbox; anything else runs "
          "inside it, with no network. Edit files under `app/src/main`, and `.echolot/verdict.json`, and nothing "
          "else: the page lists every other file you change. Do not commit anything.", lines[5])
    check("one command at a time, since a rule lets nothing else out", lines[6].startswith("- Run each command on its "
          "own: a rule lets a command line out only when it is that command alone, with no pipe"), lines[6])
    check("and how it ends", lines[7] == "- Close the investigation with your verdict, as `echolot guide hunt` says: "
          "`echolot hunt --done --verdict .echolot/verdict.json`.")


# --- what it printed, read back ------------------------------------------------------------------------

def log_of(project: Path, *events: dict, text: str = dig_codex._NOISE[0] + "...\n") -> Path:
    log = job.log_path(project, dig.JOB, dig.STEP)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text + "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events), encoding="utf-8")
    return log


def item(kind: str, **fields) -> dict:
    return {"type": "item.completed", "item": {"id": "item_1", "type": kind, **fields}}


def test_its_events_are_read_back(tmp_path):
    log_of(tmp_path,
           {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
           item("agent_message", text="I'll start."),
           item("command_execution", command="/bin/zsh -lc 'git commit -am wip'",
                aggregated_output="fatal: Unable to create '.git/index.lock': Operation not permitted\n",
                exit_code=128, status="failed"),
           item("command_execution", command="/bin/zsh -lc ./gradlew", aggregated_output="BUILD FAILED\n",
                exit_code=1, status="failed"),
           item("command_execution", command=["bash", "-lc", "rm -rf /tmp/x"], aggregated_output="", exit_code=None,
                status="declined"),
           item("command_execution", command="/bin/zsh -lc 'echolot doctor -q'",
                aggregated_output="error: this command runs in Codex's sandbox, which has no network\n",
                exit_code=2, status="failed"),
           item("file_change", changes=[{"path": "/etc/hosts", "kind": "update"}], status="failed"),
           item("agent_message", text="Done, with what I had."),
           {"type": "turn.completed", "usage": {"input_tokens": 56235, "cached_input_tokens": 49280,
                                                "output_tokens": 244, "reasoning_output_tokens": 0}})
    found = dig_codex.result(tmp_path)
    check("its last message", dig_codex.last_words(found) == "Done, with what I had.")
    check("the tokens of its turn, read and written", dig_codex.cost(found) == "56,479 tokens", dig_codex.cost(found))
    check("what its sandbox refused or Codex declined, as it ran it, and a build that failed is none of it",
          dig.adapter("codex").denied(found) == ["git commit -am wip", "rm -rf /tmp/x", "echolot doctor -q",
                                                 "a change to /etc/hosts"], found["denied"])
    check("there is no spending cap to reach", not dig.adapter("codex").capped(found))


def test_a_turn_that_failed_says_why_and_a_run_that_printed_nothing_has_nothing(tmp_path):
    log_of(tmp_path, {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
           item("agent_message", text="I'll start."),
           {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}},
           {"type": "error", "message": "stream disconnected"})
    found = dig_codex.result(tmp_path)
    check("the errors in place of its last message", dig_codex.last_words(found)
          == "You've hit your usage limit. Try again later.\nstream disconnected", found)
    check("and no tokens where no turn ended", dig_codex.cost(found) is None)
    log_of(tmp_path, text="Reading additional input from stdin...\nerror: not signed in\n")
    check("no event: nothing read back, and what it said besides is its tail", dig_codex.result(tmp_path) is None
          and dig_codex.tail(tmp_path) == ["error: not signed in"], dig_codex.tail(tmp_path))
    check("the line it always says first is no news", dig_codex.last_words(None) == "")


# --- what it changed outside the allowed folders ---------------------------------------------------------

def tree_of(root: Path) -> None:
    for rel, text in {"app/src/main/java/com/example/app/Store.kt": "fun fill() {}\n",
                      "feature/login/src/main/Login.kt": "fun login() {}\n",
                      "feature/login/src/test/LoginTest.kt": "fun test() {}\n",
                      "app/build.gradle.kts": "plugins {}\n", "settings.gradle.kts": 'include(":app")\n',
                      "README.md": "# app\n", "app/build/intermediates/x.dex": "dex\n",
                      ".gradle/8.7/fileHashes.bin": "bin\n", ".echolot/out/report.json": "{}\n",
                      ".idea/workspace.xml": "<x/>\n", ".DS_Store": "x"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def run_of(n: int = 1, finished: float | None = None) -> dict:
    return {"what": {"client": "codex", "hunt": n}, "finished": finished}


def test_the_files_changed_outside_the_allowed_folders_are_listed(tmp_path):
    tree_of(tmp_path)
    paths = ["app/src/main", "feature/*/src/main"]
    dig_codex.before(tmp_path, 1, paths)
    noted = json.loads((tmp_path / dig_codex.SNAPSHOT).read_text())
    check("what is noted: outside the allowed folders, echolot's own and what builds and the IDE write",
          sorted(noted["files"]) == ["README.md", "app/build.gradle.kts", "feature/login/src/test/LoginTest.kt",
                                     "settings.gradle.kts"], sorted(noted["files"]))
    for rel in ("app/src/main/java/com/example/app/Store.kt", "feature/login/src/main/Login.kt",
                "app/build/intermediates/x.dex", ".echolot/out/report.json", ".idea/workspace.xml"):
        (tmp_path / rel).write_text("changed, where it may be\n", encoding="utf-8")
    (tmp_path / "app/build.gradle.kts").write_text("plugins { id(\"x\") }\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("mine\n", encoding="utf-8")
    (tmp_path / "README.md").unlink()
    found = dig_codex.outside(tmp_path, run_of(finished=time.time()))
    check("written, new and gone, outside them alone", found["checked"] and found["files"] == [
        {"path": "README.md", "how": "removed"}, {"path": "app/build.gradle.kts", "how": "changed"},
        {"path": "notes.md", "how": "new"}], found)
    check("with the folders it may edit", found["allowed"] == paths)


def test_a_file_written_after_the_run_ended_is_the_person_s(tmp_path):
    tree_of(tmp_path)
    dig_codex.before(tmp_path, 1, ["app/src/main"])
    ended = time.time() - 60
    path = tmp_path / "settings.gradle.kts"
    path.write_text("include(\":app\", \":core\")\n", encoding="utf-8")
    found = dig_codex.outside(tmp_path, run_of(finished=ended))
    check("not listed", found["checked"] and found["files"] == [], found)
    os.utime(path, (ended - 10, ended - 10))
    check("written before it ended, it is", dig_codex.outside(tmp_path, run_of(finished=ended))["files"]
          == [{"path": "settings.gradle.kts", "how": "changed"}])


def test_where_the_page_cannot_tell_it_says_so(tmp_path, monkeypatch):
    tree_of(tmp_path)
    found = dig_codex.outside(tmp_path, run_of())
    check("nothing noted", not found["checked"] and "did not note the project's files" in found["why"], found)
    dig_codex.before(tmp_path, 2, ["app/src/main"])
    check("noted for another run", not dig_codex.outside(tmp_path, run_of(n=1))["checked"])
    monkeypatch.setattr(dig_codex, "FILES_MAX", 2)
    dig_codex.before(tmp_path, 1, ["app/src/main"])
    found = dig_codex.outside(tmp_path, run_of(n=1))
    check("more files than the page notes", not found["checked"] and "more than 2 files outside" in found["why"],
          found)
    dig_codex.before(tmp_path, 1, [""])
    check("the whole project allowed: nothing is outside", dig_codex.outside(tmp_path, run_of(n=1)) == {
        "checked": True, "allowed": [""], "files": []})


# --- the watchdog's plan ---------------------------------------------------------------------------

def test_the_job_is_told_how_long_nothing_may_move_and_what_says_it_moved():
    plan = dig.adapter("codex").idle(3)
    check("half an hour, three times the page's own warning", plan["seconds"] == 1800.0 == 3 * dig.QUIET_S)
    check("its events, the investigation's record and its reports", plan["watch"] == [
        ".echolot/log/dig.agent.log", ".echolot/hunt.json", ".echolot/hunts/3/reports"], plan["watch"])
    check("and its own helpers, which run the whole time", plan["helpers"] == ["codex"])
    check("Claude Code has none: it refuses a question and goes on", dig.adapter("claude").idle(3) is None)


# --- the table of clients ---------------------------------------------------------------------------

def test_the_table_and_the_order_the_page_offers_them_in(tmp_path, monkeypatch):
    check("Claude Code, then Codex", [a.key for a in dig.clients()] == ["claude", "codex"])
    check("a run that names no client, or one the table lacks, is Claude Code's",
          dig.adapter(None) is dig.CLAUDE and dig.adapter("cursor") is dig.CLAUDE)
    monkeypatch.setattr(dig, "why_not", lambda project, st, lines, client: None)
    claude, codex_ = dig.Client(program="/c", key="claude"), dig.Client(program="/x", key="codex")
    check("the table's order where no run was started here",
          [c.key for c, _ in dig.offer(tmp_path, [claude, codex_], {}, [])] == ["claude", "codex"])
    state = job.state_path(tmp_path, dig.JOB)
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"job": "dig", "pid": 4242, "started": 1.0, "finished": 2.0, "end": "done",
                                 "what": {"client": "codex"}, "steps": []}), encoding="utf-8")
    offered = dig.offer(tmp_path, [claude, codex_], {}, [])
    check("the one the last run used first", [c.key for c, _ in offered] == ["codex", "claude"])
    check("a press names one by its key, or takes the first that can start",
          dig.pick(offered, "claude")[0] is claude and dig.pick(offered)[0] is codex_
          and dig.pick(offered, "cursor") is None)
    check("the first that can start, where the first cannot", dig.pick([(codex_, "signed out"), (claude, None)])[0]
          is claude and dig.pick([(codex_, "no"), (claude, "no")])[0] is codex_ and dig.pick([]) is None)


def test_codex_is_found_as_claude_code_is(monkeypatch):
    monkeypatch.setattr(dig, "_which", lambda name: None)
    found = dig.find_all()
    check("both, by key, neither installed here", [c.key for c in found] == ["claude", "codex"]
          and not any(c.can for c in found))


# --- real processes, with the fake client ------------------------------------------------------------

def fake_step(project: Path) -> dict:
    argv = [sys.executable, str(FAKE)] + dig_codex.command("codex", "the request", project)[1:]
    return {"id": dig.STEP, "argv": argv, "shown": dig_codex.shown(project), "tree": True,
            "unset": list(dig_codex.UNSET), "idle": dig_codex.idle(1)}


def started(project: Path, monkeypatch, plan: dict) -> threading.Thread:
    monkeypatch.setenv("FAKE_CODEX", json.dumps(plan))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    return job._spawn(project, dig.JOB, {"steps": [fake_step(project)], "what": {"client": "codex", "hunt": None}})


@pytest.mark.skipif(os.name != "posix", reason="process groups and signals are POSIX")
def test_stop_ends_a_real_codex_and_a_child_in_a_process_group_of_its_own(project, monkeypatch):
    """Codex starts each command in a process group of its own, so a signal
    to the client's group never reaches it (the spike saw a `sleep` outlive a
    SIGTERM to the group). The job finds what is under the client by each
    process's parent, and ends it that way."""
    pipe = project / "child"
    os.mkfifo(pipe)
    seen = project / "seen.json"
    reaper = started(project, monkeypatch, {"do": "ask", "rounds": 0, "child_pipe": str(pipe), "seen": str(seen)})
    with open(pipe, encoding="utf-8") as child:
        pid, group = (int(w) for w in child.readline().split())   # the child runs, and the client is about to wait
        client = job.read(project, dig.JOB)["steps"][0]["pid"]
        check("the child is in a process group of its own, which the client's is not",
              group == pid and os.getpgid(client) != group and os.getpgid(client) == client)
        check("and it is under the client all the same, found by its parent",
              pid in [p["pid"] for p in job.descendants(client, job.processes())])
        check("the client was started with none of the names it must not find",
              json.loads(seen.read_text())["env"] == {"CLAUDECODE": None, "CODEX_SANDBOX": None,
                                                      "CODEX_SANDBOX_NETWORK_DISABLED": None})
        check("asked to stop", dig.stop(project))
        check("the child ended: its end of the pipe is closed", child.read() == "")
    reaper.join(timeout=120)
    state = job.read(project, dig.JOB)
    check("the job says stopped, by the person", state["status"] == job.STOPPED and "why" not in state, state)
    check("and the child is not running any more",
          not [p for p in job.processes() if p["pid"] == pid and "while True" in p["args"]])
    found = dig_codex.result(project)
    check("what it printed before it was stopped reads back: the question it was waiting on",
          dig_codex.last_words(found) == "Which build should I measure, the debug one or the benchmark one?"
          and not found["completed"], found)


@pytest.mark.skipif(os.name != "posix", reason="process groups and signals are POSIX")
def test_a_real_codex_that_ends_by_itself_leaves_its_events(project, monkeypatch):
    reaper = started(project, monkeypatch, {"do": "nothing", "rounds": 0})
    reaper.join(timeout=120)
    state = job.read(project, dig.JOB)
    found = dig_codex.result(project)
    check("it ended well, with its turn's end printed", state["status"] == job.DONE and found["completed"]
          and dig_codex.cost(found) == "186,550 tokens", (state, found))
    check("and its last words", dig_codex.last_words(found).startswith("I looked at the report"))


def test_the_fake_client_takes_the_command_line_the_page_builds_and_no_other(project):
    from tests import fake_codex
    argv = dig_codex.command("codex", "x", project)
    check("the page's own", fake_codex.parse(argv[1:]).prompt == "x")
    for wrong in (argv[1:] + ["--dangerously-bypass-approvals-and-sandbox"],
                  [a for a in argv[1:] if a != "--json"],
                  ["exec", "--json", "-s", "danger-full-access", "-C", str(project), "x"],
                  ["exec", "--json", "-s", "workspace-write", "-C", str(project), "--skip-git-repo-check", "x"],
                  ["exec", "--json", "--full-auto", "-C", str(project), "x"],
                  ["--json", "-s", "workspace-write", "-C", str(project), "x"]):
        with pytest.raises(fake_codex.Refused):
            fake_codex.parse(wrong)
