#!/usr/bin/env python3
"""An agent's sandbox refuses echolot a port on localhost, and echolot says so.

Codex runs every command in a sandbox with no network by default, and on
macOS that takes localhost with it. perfetto binds a socket for a free port
before it starts trace_processor, and adb's server listens on localhost:5037,
so all three commands that do the work failed there (#190): `analyze` in a
PermissionError traceback, `doctor` with "could not run — [Errno 1] Operation
not permitted", `collect` with twenty lines of adb's startup log. None of them
said sandbox, and each read as a broken install.

The refusal is made here the way the sandbox makes it: perfetto's port
request raises EPERM, and a stand-in adb prints what the real one printed in
Codex's sandbox. One case runs a real sandbox, where macOS has one to give.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from echolot import fixture, recorder, runner, sandbox, selftest, state, tp
from echolot.main import NOT_RUN, main

ROOT = Path(__file__).resolve().parent.parent
CODEX_VARS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")

# What adb printed on `adb devices` in Codex's sandbox on macOS, trimmed of the
# lines that repeat: the client could not reach the server, started one, and
# the server could not open its port.
ADB_IN_SANDBOX = """\
* daemon not running; starting now at tcp:5037
ADB server didn't ACK
Full server startup log: /tmp/adb.501.log
Server had pid: 22717
--- adb starting (pid 22717) ---
09-30 11:22:23.665 22717 10565968 F adb     : main.cpp:168 could not install *smartsocket* listener: Operation not permitted

* failed to start daemon
adb: failed to check server version: cannot connect to daemon
"""


@pytest.fixture
def outside(monkeypatch):
    """No agent's marks in the environment, whatever ran these tests."""
    for name in CODEX_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def in_codex(monkeypatch, tmp_path):
    """The marks Codex leaves on a command it runs with the network off.

    And a Codex home of its own, so a rule on the machine running the tests
    does not change what they read.
    """
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


@pytest.fixture
def port_refused(monkeypatch):
    """perfetto's request for a free port, refused the way a sandbox refuses it."""
    from perfetto.trace_processor.platform import PlatformDelegate

    def refuse(self, port):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(PlatformDelegate, "get_bind_addr", refuse)


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """The self-check's trace and config, and a trace_processor that exists.

    The binary is any file that is there: the port is asked for before it
    is started, so it never is.
    """
    (tmp_path / "echolot.yml").write_text(yaml.safe_dump(selftest.FIXTURE_CONFIG),
                                          encoding="utf-8")
    (tmp_path / "t.perfetto-trace").write_bytes(fixture.build())
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _analyze(capsys) -> tuple[int, str]:
    code = main(["analyze", "t.perfetto-trace", "-c", "echolot.yml",
                 "--tp-binary", sys.executable])
    return code, capsys.readouterr().err


# --- trace_processor -----------------------------------------------------------

def test_analyze_names_codex_and_the_rule_where_it_ended_in_a_traceback(
        project, port_refused, in_codex, capsys):
    code, err = _analyze(capsys)
    assert code == 2, err
    assert "Traceback" not in err, err
    assert err.startswith("error: trace_processor could not get a port on "
                          "localhost (Operation not permitted): this command "
                          "runs in Codex's sandbox"), err
    assert sandbox.CODEX_RULE in err and ".codex/rules/echolot.rules" in err, err
    assert "sandbox.excludedCommands" not in err, err


def test_with_no_agent_named_the_refusal_is_said_and_both_ways_out(
        project, port_refused, outside, capsys):
    """The refusal is certain and whose it was is not: both hosts are named."""
    code, err = _analyze(capsys)
    assert code == 2, err
    assert "an agent's sandbox is the likely cause" in err, err
    assert "Codex's sandbox, which" not in err, err
    # The command that writes the rule. The project never chose, so it does
    # not name Claude Code, which detection adds whatever is there: that
    # installed `.claude/` beside a plugin that brings the same skills.
    assert "`echolot init --for agents,codex`" in err, err
    assert "sandbox.excludedCommands" in err, err


@pytest.mark.parametrize("error,is_sandbox", [
    (PermissionError(errno.EPERM, "Operation not permitted"), True),
    (PermissionError(errno.EACCES, "Permission denied"), True),
    # The same errno over a file: a trace_processor_shell without its execute
    # bit. A sandbox is not why, and saying so would send the reader away
    # from the one thing to fix.
    (PermissionError(errno.EACCES, "Permission denied", "/opt/tp/trace_processor_shell"), False),
    (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), False),
    (FileNotFoundError(errno.ENOENT, "No such file or directory"), False),
    (RuntimeError("anything else"), False),
], ids=["eperm", "eacces", "eacces-on-a-file", "econnrefused", "enoent", "not-oserror"])
def test_only_a_refused_socket_reads_as_a_sandbox(error, is_sandbox):
    assert sandbox.refused(error) is is_sandbox


# --- doctor, and what status reads back ------------------------------------------

@pytest.fixture
def logged(monkeypatch, tmp_path) -> Path:
    """A project in tmp_path whose run log is actually written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    monkeypatch.setattr(recorder, "_root", None)
    return tmp_path / recorder.LOG_FILE


@pytest.fixture
def self_check_refused(monkeypatch):
    """The self-check stopped where the sandbox stops it: at the port."""
    def run(tp_binary=None):
        raise sandbox.trace_processor_refused(
            PermissionError(errno.EPERM, "Operation not permitted"))

    monkeypatch.setattr(selftest, "run", run)


@pytest.mark.parametrize("argv", [["doctor", "-q"], ["doctor"]], ids=["quiet", "full"])
def test_doctor_says_the_sandbox_and_logs_it_as_a_fact(
        logged, self_check_refused, in_codex, capsys, argv):
    """Exit 1 as before; the reason, and a verdict that is not "broken".

    "The environment is broken" sent a reader to reinstall what a sandbox had
    stopped. The log carries `sandbox` beside the usual not-run entry, for
    `status` and `next` to read without matching a sentence.
    """
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "could not run" in out and "runs in Codex's sandbox" in out, out
    assert "The environment is broken" not in out, out

    doctors = [r for r in recorder.read(logged) if r.get("cmd") == "doctor"]
    assert len(doctors) == 1, doctors
    facts = doctors[0].get("facts") or {}
    assert facts.get("checks") == 0 and facts.get("failed") == [NOT_RUN], facts
    assert facts.get("sandbox") == "codex", facts


def test_status_and_next_say_whose_sandbox_stopped_the_self_check(
        logged, self_check_refused, in_codex, capsys, tmp_path):
    """The door reads these two lines; they now name the cause."""
    main(["doctor", "-q"])
    capsys.readouterr()

    assert main([]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("doctor"))
    assert "Codex's sandbox refused trace_processor a port on localhost" in line, line
    assert "run `echolot doctor` to see why" not in line, line

    st = state.project_state(tmp_path)
    st["layer_verdict"] = "current"
    assert state.next_kind(st) == "doctor"
    assert state.next_step(st).startswith("run echolot outside Codex's sandbox"), \
        state.next_step(st)


def test_a_self_check_that_failed_for_another_reason_keeps_its_words(
        logged, monkeypatch, capsys):
    """No `sandbox` fact, and the lines stay what they were."""
    def offline(tp_binary=None):
        raise RuntimeError("could not download trace_processor: offline")

    monkeypatch.setattr(selftest, "run", offline)
    main(["doctor"])
    out = capsys.readouterr().out
    assert "The environment is broken" in out, out
    facts = next(r for r in recorder.read(logged) if r.get("cmd") == "doctor")["facts"]
    assert "sandbox" not in facts, facts

    main([])
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("doctor"))
    assert "run `echolot doctor` to see why" in line, line


# --- adb ----------------------------------------------------------------------------

@pytest.fixture
def adb_in_sandbox(tmp_path, monkeypatch) -> Path:
    """An adb on PATH that answers the way the real one did in the sandbox."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "said.txt"
    log.write_text(ADB_IN_SANDBOX, encoding="utf-8")
    adb = bin_dir / "adb"
    adb.write_text(f"#!/bin/sh\ncat '{log}' >&2\nexit 1\n", encoding="utf-8")
    adb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return adb


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in adb is a shell script")
def test_adb_refused_its_server_is_said_as_the_sandbox(adb_in_sandbox, in_codex):
    with pytest.raises(runner.RunnerError) as caught:
        runner._run(["adb", "devices"])
    e = caught.value
    assert e.gist.startswith("adb could not start or reach its server on "
                             "localhost (adb: failed to check server version: "
                             "cannot connect to daemon): this command runs in "
                             "Codex's sandbox"), e.gist
    # adb's startup log stays out: it says everything but sandbox.
    assert "smartsocket" not in str(e) and "Server had pid" not in str(e), str(e)
    assert sandbox.CODEX_RULE in str(e), str(e)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in adb is a shell script")
def test_collect_ends_on_the_sandbox_and_not_on_adbs_log(
        adb_in_sandbox, in_codex, tmp_path, monkeypatch, capsys):
    (tmp_path / "echolot.yml").write_text(yaml.safe_dump({
        **selftest.FIXTURE_CONFIG,
        "runner": {"mode": "command", "command": "true", "duration_ms": 1000}}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["collect", "-n", "1"]) == 2
    err = capsys.readouterr().err
    assert "collection error: adb could not start or reach its server" in err, err
    assert "Server had pid" not in err, err


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in adb is a shell script")
def test_an_adb_error_of_another_kind_is_passed_on_as_before(tmp_path, monkeypatch):
    """A device node adb may not open is a different fix, in adb's own words."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    adb = bin_dir / "adb"
    adb.write_text("#!/bin/sh\necho 'adb: error: failed to get feature set: "
                   "no devices/emulators found' >&2\nexit 1\n", encoding="utf-8")
    adb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    with pytest.raises(runner.RunnerError) as caught:
        runner._run(["adb", "shell", "true"])
    assert caught.value.gist.startswith("adb shell true: adb: error:"), caught.value.gist


# --- the first download ----------------------------------------------------------------

def test_a_download_that_failed_in_codex_leads_with_the_sandbox(in_codex):
    pin = tp.pinned_build()
    if pin is None:
        pytest.skip("no pinned trace_processor for this platform")
    said = tp._cannot_fetch(pin, "curl exited with status 7")
    ways = [ln for ln in said.splitlines() if ln.startswith("  - ")]
    assert "Codex's sandbox" in ways[0] and "`echolot init --for" in ways[0], said
    assert len(ways) == 4, said


def test_a_download_that_failed_elsewhere_keeps_its_three_ways(outside):
    pin = tp.pinned_build()
    if pin is None:
        pytest.skip("no pinned trace_processor for this platform")
    said = tp._cannot_fetch(pin, "curl exited with status 7")
    assert "sandbox" not in said, said
    assert len([ln for ln in said.splitlines() if ln.startswith("  - ")]) == 3, said


# --- a real sandbox, where the platform has one ------------------------------------------

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


@pytest.mark.skipif(sys.platform != "darwin" or not Path(SANDBOX_EXEC).exists(),
                    reason="sandbox-exec is macOS's")
def test_under_a_real_sandbox_with_no_network_analyze_says_so(project):
    """The profile allows everything but the network, as Codex's does by default.

    Codex's own profile is stricter everywhere else; the network is the part
    this is about. A child process, because the sandbox is the process's.
    """
    env = {k: v for k, v in os.environ.items() if k not in CODEX_VARS}
    env["PYTHONPATH"] = str(ROOT)
    run = subprocess.run(
        [SANDBOX_EXEC, "-p", "(version 1)(allow default)(deny network*)",
         sys.executable, "-m", "echolot.main", "analyze", "t.perfetto-trace",
         "-c", "echolot.yml", "--tp-binary", shutil.which("true") or sys.executable],
        capture_output=True, text=True, env=env, timeout=120)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "Traceback" not in run.stderr, run.stderr
    assert "trace_processor could not get a port on localhost" in run.stderr, run.stderr


# --- a rule that is there and did not match ----------------------------------------

RULE = 'prefix_rule(\n    pattern = ["echolot"],\n    decision = "allow",\n)\n'


@pytest.mark.parametrize("where", ["project", "codex-home"])
def test_with_the_rule_in_place_the_refusal_names_the_command_line(
        project, port_refused, in_codex, capsys, tmp_path, monkeypatch, where):
    """What a live Codex session stopped on (#189): `analyze
    .echolot/traces/*.perfetto-trace` with the rule in place. Codex does not
    look inside a line with a glob, so the whole line stayed in the sandbox,
    and "add the rule" was advice about a rule that was already there."""
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    rules = project / ".codex" / "rules" if where == "project" else home / "rules"
    rules.mkdir(parents=True)
    (rules / "echolot.rules").write_text(RULE, encoding="utf-8")
    home.mkdir(exist_ok=True)
    _trusted(home, project)

    code, err = _analyze(capsys)
    assert code == 2, err
    assert "lets echolot out of it" in err, err
    assert "A glob such as `*.perfetto-trace`" in err, err
    assert "put prefix_rule" not in err and "let it out for good" not in err, err
    # A session that started before the rule was there has not read it; a
    # rule for every project is read by every session there is.
    unread = "the session started before the rule was there" in err
    assert unread is (where == "project"), err


def _trusted(home: Path, project: Path, level: str = "trusted") -> None:
    """The line Codex writes into its config when a person trusts a project."""
    (home / "config.toml").write_text(
        f'[projects."{project.resolve()}"]\ntrust_level = "{level}"\n',
        encoding="utf-8")


@pytest.mark.parametrize("level", [None, "untrusted"])
def test_a_rule_in_a_project_codex_does_not_trust_is_said_to_be_unread(
        project, port_refused, in_codex, capsys, tmp_path, level):
    """Codex reads a project's `.codex/` only once it trusts the project, so
    a refusal there is about trust, and never about the command line."""
    home = tmp_path / "codex-home"
    home.mkdir()
    if level:
        _trusted(home, project, level)
    rules = project / ".codex" / "rules"
    rules.mkdir(parents=True)
    (rules / "echolot.rules").write_text(RULE, encoding="utf-8")

    code, err = _analyze(capsys)
    assert code == 2, err
    assert "Codex has not read it" in err, err
    said = "marks this project untrusted" if level else "does not list this project as trusted"
    assert said in err, err
    assert "codex-home/rules/" in err, err
    assert "A glob" not in err, err


def test_a_rules_file_about_another_command_is_not_ours(
        project, port_refused, in_codex, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    rules = project / ".codex" / "rules"
    rules.mkdir(parents=True)
    (rules / "gh.rules").write_text('prefix_rule(pattern = ["gh", "pr", "view"])\n',
                                    encoding="utf-8")
    code, err = _analyze(capsys)
    assert code == 2, err
    assert "let it out for good" in err and "lets echolot out of it" not in err, err
