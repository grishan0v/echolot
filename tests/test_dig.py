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

import copy
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
    check("and says where and how to name one", "to `echolot.yml` under `instrumentation:`, for example "
          "`allowed: [\"app/src/main\", \"feature/*/src/main\"]`" in refused.value.text, refused.value.text)


@pytest.mark.parametrize("allowed", ["[]", '["../elsewhere"]'])
def test_a_folder_no_client_may_edit_is_why_every_client_would_start_nothing(tmp_path, monkeypatch, allowed):
    """The reading before the press says what the press says: no screen offers
    a client the press then refuses for want of a folder (#813), and every
    client is refused alike, so none is offered in place of another (#863)."""
    path = config(tmp_path, allowed).path
    monkeypatch.setattr(dig, "layer_missing", lambda project: [])
    st = {"config": {"path": str(path), "scenario": None}, "hunt": None, "collect": None}
    monkeypatch.setattr(dig.state, "next_kind", lambda st: "hunt")
    claude = dig.Client(program="/c", version="2.1.300")
    with pytest.raises(Refusal) as refused:
        dig.allowed(Config.load(path))
    check("Claude Code is refused for the folder", dig.why_not(tmp_path, st, [], claude) == refused.value.text)
    check("and the folder is read the same for every client", dig.unplaced(st) == refused.value.text)
    path.write_text(path.read_text(encoding="utf-8").replace(f"allowed: {allowed}", 'allowed: ["app/src/main"]'),
                    encoding="utf-8")
    check("with a folder named, nothing of it stands in the way", dig.unplaced(st) is None
          and dig.why_not(tmp_path, st, [], claude) is None)
    check("a config that does not load is the start page's to say", dig.unplaced({}) is None
          and dig.unplaced({"config": {"path": str(tmp_path / "gone.yml")}}) is None)


def test_what_the_page_says_of_clients_it_cannot_start():
    claude = dig.Client(program="/c", key="claude")
    codex_ = dig.Client(program="/x", key="codex")
    absent = dig.Client(key="codex", why="Codex is not installed on this computer: there is no `codex` command here.")
    folder, rules = "no folder", "Not yet for Codex: no rules."
    check("#863: a reason both share, said once", dig.reasons([(claude, folder), (codex_, folder)]) == [folder])
    check("a client that is not installed is no reason beside one that is",
          dig.reasons([(claude, folder), (absent, absent.why)]) == [folder])
    check("unless its own screen is the one that says it", dig.reasons([(claude, folder), (absent, absent.why)],
                                                                      "codex") == [folder, absent.why])
    check("and where none is installed, every one says so", dig.reasons([(dig.Client(why="no claude"), "no claude"),
                                                                        (absent, absent.why)])
          == ["no claude", absent.why])
    check("#819: where one can start, why the other on this computer cannot",
          dig.aside([(claude, None), (codex_, rules)]) == [rules])
    check("not on the screen of the one that cannot, which says it itself",
          dig.aside([(claude, None), (codex_, rules)], "codex") == [])
    check("nothing of a client that is not installed", dig.aside([(claude, None), (absent, absent.why)]) == [])
    check("and nothing aside where none can start: those are the reasons",
          dig.aside([(claude, folder), (codex_, rules)]) == [])


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
    check("the verdict with the edit tool, since a heredoc that writes it is refused (#821)",
          rest[5] == "- Write `.echolot/verdict.json` with your Write or Edit tool. A shell command that writes it, "
                     "`cat >` or a heredoc, is refused like any other program.", rest[5])
    check("one command per call, since a loop or a chain with another program is refused",
          rest[6] == "- Run one command per call: a loop, or a chain with another program, is refused, and each "
                     "refusal costs a turn.")
    check("and how it ends", rest[7] == "- Close the investigation with your verdict, as /echolot-hunt says: "
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


STAMP = "2026-11-24T10:00:00+00:00"
WHERE = ".echolot/out/report.json"


def compared_row(location: str, change: str, after: float | None, *, before: float | None = None,
                 holds: bool | None = None, runs: str = "5/5", low: float | None = None, **more) -> dict:
    return {"location": location, "detector": "main_thread_waits", "metric": "self_ms", "change": change,
            "before": None if before is None else {"self_ms": before, "runs": runs},
            "after": None if after is None else {"self_ms": after, "runs": runs},
            "delta_ms": (after or 0.0) - (before or 0.0), "holds": holds,
            "shift": None if low is None else {"ms": 1.0, "low_ms": low, "high_ms": low + 50.0, "floor_ms": 10.0},
            **more}


def comparison_beside(root: Path, rows: list[dict], *, of: str = STAMP, comparable: bool = True,
                      warnings: tuple[str, ...] = ()) -> None:
    out = root / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps({
        "schema": dig.files_mod.COMPARISON_SCHEMA, "kind": "comparison", "comparable": comparable,
        "warnings": [{"id": w, "text": w} for w in warnings],
        "before": {"path": str(out / "earlier.json"), "runs": 5},
        "after": {"path": str(out / "report.json"), "runs": 5, "generated_at": of}, "rows": rows}), encoding="utf-8")


WORSE_ROWS = [compared_row("inflate", "steady", 40.0, before=40.0, holds=False, low=-5.0),
              compared_row("Thread.sleep in an initializer", "appeared", 558.0, detail="the main thread waits"),
              compared_row("now and then", "appeared", 90.0, runs="2/5"),
              compared_row("loadConfig", "grew", 340.0, before=120.0, holds=True, low=180.0),
              compared_row("draw", "grew", 140.0, before=100.0, holds=False, low=-6.0),
              compared_row("bind", "shrank", 20.0, before=200.0, holds=True, low=-220.0),
              compared_row("AGENTTMP_parse", "appeared", 300.0),
              compared_row("PROBE_parse", "appeared", 300.0)]
NEW = ("- new in 5 of 5 runs: Thread.sleep in an initializer · main_thread_waits · 558 ms · the main thread waits")
GREW = "- grew for sure: loadConfig · main_thread_waits · 120 → 340 ms (+220 ms, at least +180 ms)"


def test_the_request_carries_what_the_last_comparison_found_worse_first(tmp_path):
    """#933: the comparison of this very report against the one before, its
    rows new in every run and those that grew and hold, after the question
    and the warnings and before the report's rows, each marked."""
    report = {**known(), "generated_at": STAMP}
    picks = [c for c in dig.report_page.checks(report) if c["key"] == "main_thread_waits"]
    plain = dig.report_page.request_text(report, picks, WHERE)
    comparison_beside(tmp_path, WORSE_ROWS)
    text = dig.request_text(tmp_path, report, picks, WHERE)
    head = dig.COMPARED_HEAD.format(source="`.echolot/out/comparison.md`, against `.echolot/out/earlier.json`")
    block = "\n".join([head, NEW, GREW, "- new in 5 of 5 runs: PROBE_parse · main_thread_waits · 300 ms"])
    check("the new and the grown, marked, in the comparison's order", block in text, text)
    check("before the report's rows, after the question and the warnings",
          text.index(block) < text.index(dig.report_page.ROWS_HEAD)
          and text.index(dig.report_page.WARN_HEAD) < text.index(block), text)
    check("and the rest of the request as it was", text.replace(block + "\n\n", "") == plain)
    check("a planted marker of the config's prefix is no new work",
          "PROBE_parse" not in dig.request_text(tmp_path, report, picks, WHERE, prefix="PROBE_")
          and "AGENTTMP_parse" not in text)
    handed = dig.request(report, picks, WHERE, 4, None, ["app/src/main"], project=tmp_path)
    check("the agent is handed the same", handed.startswith(text + "\n\n"))


@pytest.mark.parametrize("why, kw", [("of another report", {"of": "2026-11-23T09:00:00+00:00"}),
                                     ("two that cannot be compared", {"comparable": False}),
                                     ("the same recordings twice", {"warnings": ("same-traces",)})])
def test_a_comparison_that_is_not_this_report_s_adds_nothing(tmp_path, why, kw):
    report = {**known(), "generated_at": STAMP}
    picks = [c for c in dig.report_page.checks(report) if c["key"] == "main_thread_waits"]
    comparison_beside(tmp_path, WORSE_ROWS, **kw)
    check(why, dig.request_text(tmp_path, report, picks, WHERE) == dig.report_page.request_text(report, picks, WHERE))


def test_what_the_last_comparison_found_worse_and_what_it_leaves_out(tmp_path):
    report = {**known(), "generated_at": STAMP}
    check("no comparison beside the report", dig.worse(tmp_path, report, WHERE) is None)
    comparison_beside(tmp_path, WORSE_ROWS)
    check("a report that does not say when it was written is no report the comparison names",
          dig.worse(tmp_path, known(), WHERE) is None)
    comparison_beside(tmp_path, WORSE_ROWS[:1] + WORSE_ROWS[4:6])
    check("nothing new in every run, and nothing grown for sure: nothing", dig.worse(tmp_path, report, WHERE) is None)
    comparison_beside(tmp_path, WORSE_ROWS, warnings=("thresholds",))
    moved = dig.worse(tmp_path, report, WHERE) or ""
    check("the bar moved: a row it calls new says nothing, one that grew still holds",
          GREW in moved and "- new in" not in moved, moved)
    comparison_beside(tmp_path, [compared_row(f"init{i}", "appeared", 100.0 + i) for i in range(dig.COMPARED_ROWS + 3)])
    many = dig.worse(tmp_path, report, WHERE) or ""
    check("at most COMPARED_ROWS, and where the rest are", many.count("- new in 5 of 5 runs: init")
          == dig.COMPARED_ROWS and many.endswith("- and 3 more in `.echolot/out/comparison.md`"), many)
    one = compared_row("Thread.sleep", "appeared", 558.0, runs="")
    one["after"].pop("runs")
    comparison_beside(tmp_path, [one])
    check("a row of a side with no runs to count was in every run", "- new in 5 of 5 runs: Thread.sleep"
          in (dig.worse(tmp_path, report, WHERE) or ""))
    (tmp_path / ".echolot" / "out" / "comparison.json").write_text("{", encoding="utf-8")
    check("one that does not read adds nothing", dig.worse(tmp_path, report, WHERE) is None)


# --- which echolot the agent runs (#704) ----------------------------------------------------------

def shell_says(found: str, version: str | None = None, asked: list | None = None):
    def answer(argv, cwd, env):
        if asked is not None:
            asked.append((list(argv), env.get("PATH", ""), cwd))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"echolot {version}\n" if version else "", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout=f"Last login: today\n{dig._AT}{found}\n", stderr="")
    return answer


@pytest.fixture
def own(tmp_path, monkeypatch) -> Path:
    """The page's own echolot, as a test has it: a file in a folder of its own."""
    home = tmp_path / "venv" / "bin"
    home.mkdir(parents=True)
    (home / "echolot").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(dig, "own_bin", lambda: home)
    return home


@pytest.mark.parametrize("key, name", [("claude", "Claude Code"), ("codex", "Codex")])
def test_an_agent_whose_shell_runs_another_echolot_does_not_start(tmp_path, monkeypatch, own, key, name):
    asked: list = []
    monkeypatch.setattr(dig, "_shell", shell_says("/opt/elsewhere/bin/echolot", "0.14.1", asked))
    why = dig.runs_own(dig.Client(program="/x", key=key), tmp_path)
    check("it says which echolot the agent would run, and which the page is", why == (
        f"Not with this echolot: {name} runs commands in your login shell, and there `echolot` is "
        f"`/opt/elsewhere/bin/echolot`, echolot 0.14.1, while this page is echolot {dig.__version__} "
        f"(`{dig._whole(own / 'echolot')}`). The investigation would run on another echolot than the page that "
        f"reads it. Put `{dig._whole(own)}` first in your shell's PATH, or remove the other echolot, then come back."),
        why)
    argv, path, cwd = asked[0]
    check("asked in the project's folder, with the page's own folder first in PATH",
          cwd == tmp_path and path.split(os.pathsep)[0] == str(own), (cwd, path))
    if key == "claude":
        check("as Claude Code's shell runs it: a login shell, the rc file sourced first where there is one",
              argv[1:3] == ["-c", "-l"] and argv[3].endswith(dig.LOOK), argv)
    else:
        check("as Codex runs it: the login shell", argv[1:] == ["-lc", dig.LOOK], argv)
    check("and the other one asked its version", asked[1][0] == ["/opt/elsewhere/bin/echolot", "--version"])


def test_the_page_s_own_echolot_or_one_of_its_version_is_no_reason(tmp_path, monkeypatch, own):
    claude = dig.Client(program="/x", key="claude")
    link = tmp_path / "bin" / "echolot"
    link.parent.mkdir()
    link.symlink_to(own / "echolot")
    asked: list = []
    monkeypatch.setattr(dig, "_shell", shell_says(str(link), "0.14.1", asked))
    check("a link to the page's own is the page's own, with no version asked",
          dig.runs_own(claude, tmp_path) is None and len(asked) == 1, asked)
    monkeypatch.setattr(dig, "_shell", shell_says("/opt/elsewhere/bin/echolot", dig.__version__))
    check("another install of this same version runs as this one", dig.runs_own(claude, tmp_path) is None)


def test_what_the_page_says_where_the_shell_answers_otherwise(tmp_path, monkeypatch, own):
    claude = dig.Client(program="/x", key="claude")
    monkeypatch.setattr(dig, "_shell", shell_says("/opt/elsewhere/bin/echolot"))
    check("an echolot that does not say its version is not taken for this one",
          "`/opt/elsewhere/bin/echolot`, an echolot that does not say its version (`echolot --version`)"
          in (dig.runs_own(claude, tmp_path) or ""))
    monkeypatch.setattr(dig, "_shell", shell_says(""))
    check("no echolot in the shell", (dig.runs_own(claude, tmp_path) or "").startswith(
        "Not yet: Claude Code runs commands in your login shell, and that shell has no `echolot` command "
        "(`") and f"Put `{dig._whole(own)}` first in your shell's PATH" in (dig.runs_own(claude, tmp_path) or ""))
    monkeypatch.setattr(dig, "_shell", shell_says("alias echolot='python -m echolot'"))
    check("an alias", "an alias or a function of your shell" in (dig.runs_own(claude, tmp_path) or ""))
    monkeypatch.setattr(dig, "_shell", lambda argv, cwd, env: None)
    check("a shell that does not answer", dig.runs_own(claude, tmp_path) == (
        "The page could not tell which `echolot` Claude Code would run: the login shell it runs commands in did "
        f"not answer within {dig.SHELL_S} s. Check that `{Path(dig.claude_shell('')[0]).name} -lic 'command -v "
        "echolot'` answers in a terminal, by itself, then come back."))
    monkeypatch.setattr(dig, "_shell", lambda argv, cwd, env: subprocess.CompletedProcess(argv, 1, "", "zsh: oops"))
    check("one that says nothing the page reads", "said nothing the page could read" in (
        dig.runs_own(claude, tmp_path) or ""))


def test_claude_code_s_shell_is_its_snapshot_s(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(dig.Path, "home", classmethod(lambda cls: tmp_path))
    check("no rc file: a login shell alone", dig.claude_shell("x") == ["/bin/zsh", "-c", "-l", "x"])
    (tmp_path / ".zshrc").write_text("", encoding="utf-8")
    check("with one, sourced first", dig.claude_shell("x") == [
        "/bin/zsh", "-c", "-l", f"source {shlex.quote(str(tmp_path / '.zshrc'))} < /dev/null; x"])


def test_the_shell_is_asked_last_and_only_where_the_caller_asks(tmp_path, monkeypatch, own):
    monkeypatch.setattr(dig, "layer_missing", lambda project: [])
    monkeypatch.setattr(dig.state, "next_kind", lambda st: "hunt")
    path = config(tmp_path, '["app/src/main"]').path
    st = {"config": {"path": str(path), "scenario": None}, "hunt": None, "collect": None}
    asked: list = []
    monkeypatch.setattr(dig, "_shell", shell_says("/opt/elsewhere/bin/echolot", "0.14.1", asked))
    claude = dig.Client(program="/c", version="2.1.300")
    check("the report's dialog asks no shell", dig.why_not(tmp_path, st, [], claude) is None and asked == [])
    check("the screen before the start and the press do", dig.why_not(tmp_path, st, [], claude, shell=True)
          == dig.runs_own(claude, tmp_path) and asked)
    asked.clear()
    st["hunt"] = {"status": "open", "question": "why"}
    check("and not where another reason stands first", dig.why_not(tmp_path, st, [], claude, shell=True)
          .startswith("Not now: an investigation is open") and asked == [])


def test_the_job_puts_the_folders_it_is_given_first_in_path(monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/opt/own/bin", "/bin"]))
    env = job._env({"path": ["/opt/own/bin"], "unset": []})
    check("first, and once", env["PATH"] == os.pathsep.join(["/opt/own/bin", "/usr/bin", "/bin"]), env["PATH"])
    check("a step that names none keeps PATH as it is", job._env({})["PATH"] == os.environ["PATH"])


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


def test_a_start_missed_in_some_recordings_is_one_question_with_no_time():
    """The screen said "no measured window" and the investigation asked about
    "the 545 ms startup takes", which the report does not judge (#861)."""
    from echolot import plain
    from echolot.pages import report_page
    some = copy.deepcopy(known())
    some["window"]["start_anchor"] = {"glob": "bindApplication", "matches": 4, "missed": "1/5"}
    some = plain.annotate(some)
    asked, opened = report_page.question(some), dig.hunt_question(some)
    check("both say the marker was missed in 1 of 5 recordings",
          "echolot did not find where `coldStart` starts in 1 of 5 recordings" in asked
          and opened == "What is behind what the report found in coldStart? echolot did not find where coldStart "
                        "starts in 1 of 5 recordings.", (asked, opened))
    check("and neither says the window is missing, or a time", "no measured window" not in asked
          and not any(" ms" in q or " s " in q for q in (asked, opened)), (asked, opened))
    every = copy.deepcopy(known())
    every["window"]["start_anchor"] = {"glob": "bindApplication", "matches": 0, "missed": "5/5"}
    every = plain.annotate(every)
    asked, opened = report_page.question(every), dig.hunt_question(every)
    check("missed in every recording: no window, and still no time", asked.startswith(
          "`coldStart` has no measured window: echolot did not find where it starts, so the rows")
          and opened == "What is behind what the report found in coldStart? echolot did not find where coldStart "
                        "starts." and "ms" not in asked + opened, (asked, opened))


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
