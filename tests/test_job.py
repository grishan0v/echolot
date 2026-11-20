#!/usr/bin/env python3
"""Commands run one after another as a process of their own (`echolot/job.py`).

What is pinned here:

  - the commands run in order, each with its output kept as it said it, and
    the first that fails ends the job;
  - everything about a job is in its files: what is running, how it ended,
    and that one whose process is gone was interrupted and is not running;
  - a job stops when its file asks it to: the command in progress is asked
    to end the way Ctrl-C asks, and nothing after it starts;
  - one job of a name at a time.

The commands are doubles: nothing is started but, in the two tests that say
so, a Python that prints a line. No test waits on a timer: a double's `wait`
answers at once, and the real process is waited for by its exit.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import job  # noqa: E402
from tests.support import check  # noqa: E402


class Command:
    """One command the job starts: what it prints, how it ends, and how many
    times it is still running when asked."""

    def __init__(self, argv, out, says: str = "", code: int = 0, waits: int = 0, on_wait=None, stubborn: bool = False):
        self.argv, self.pid, self.returncode = argv, 4000 + len(argv), None
        self.code, self.waits, self.on_wait, self.stubborn = code, waits, on_wait, stubborn
        self.signals: list[int] = []
        self.killed = False
        out.write(says.encode("utf-8"))

    def wait(self, timeout=None):
        if self.killed:
            self.returncode = -9
            return self.returncode
        if self.signals and not self.stubborn:
            self.returncode = -signal.SIGINT
            return self.returncode
        if self.waits > 0 or self.stubborn:
            self.waits -= 1
            if self.on_wait:
                self.on_wait(self)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode = self.code
        return self.code

    def send_signal(self, signum):
        self.signals.append(signum)

    def terminate(self):
        self.signals.append(signal.SIGTERM)

    def kill(self):
        self.killed = True


class Commands:
    """`subprocess.Popen` for a job: each command by its first word."""

    def __init__(self, **how):
        self.how, self.started = how, []

    def __call__(self, argv, **kw):
        check("in the project's folder, with no terminal to read from", kw["stdin"] == subprocess.DEVNULL and kw["cwd"])
        check("what it prints is kept, both streams together", kw["stderr"] == subprocess.STDOUT)
        command = Command(argv, kw["stdout"], **self.how.get(argv[0], {}))
        self.started.append(command)
        return command


def steps(*names: str) -> list[dict]:
    return [{"id": name, "argv": [name, "--flag"], "shown": f"echolot {name}"} for name in names]


def run(root: Path, commands: Commands, plan_steps: list[dict], **kw) -> int:
    return job.run(root, "check", {"steps": plan_steps, "what": {"scenario": "coldStart"}}, popen=commands,
                   signals=False, **kw)


def test_the_commands_run_in_order_and_each_keeps_its_words(tmp_path):
    commands = Commands(collect={"says": "device EXAMPLE0001\n  [1/3] recorded\n"}, analyze={"says": "→ report.json\n"})
    seen = []
    code = run(tmp_path, commands, steps("collect", "analyze"), ready=lambda: seen.append(job.read(tmp_path, "check")))
    check("it ended well", code == 0)
    check("each command once, in order, as it was given", [c.argv for c in commands.started] == [
        ["collect", "--flag"], ["analyze", "--flag"]])
    check("the state was on disk before the first command, every step waiting",
          seen[0]["status"] == job.RUNNING and [s["status"] for s in seen[0]["steps"]] == ["waiting", "waiting"]
          and not commands.started[:0], seen)
    state = job.read(tmp_path, "check")
    check("done, and each step with it", state["status"] == job.DONE and [(s["id"], s["status"], s["exit"]) for s in state["steps"]]
          == [("collect", "done", 0), ("analyze", "done", 0)], state)
    check("with what it is about, kept as it was handed in", state["what"] == {"scenario": "coldStart"} and state["pid"] == os.getpid())
    check("what a command printed is its file", job.said(tmp_path, "check", "collect") == ["device EXAMPLE0001", "  [1/3] recorded"]
          and state["steps"][0]["log"] == ".echolot/log/check.collect.log")
    check("and how a person would type it is beside it", state["steps"][1]["shown"] == "echolot analyze")


def test_the_first_command_that_fails_ends_it(tmp_path):
    commands = Commands(collect={"says": "collection error: adb sees no devices at all.\n", "code": 2})
    code = run(tmp_path, commands, steps("collect", "analyze", "compare"))
    state = job.read(tmp_path, "check")
    check("failed", code == 1 and state["status"] == job.FAILED)
    check("nothing after it started", [c.argv[0] for c in commands.started] == ["collect"]
          and [s["status"] for s in state["steps"]] == ["failed", "waiting", "waiting"] and state["steps"][0]["exit"] == 2)
    check("and its words are there to show", job.said(tmp_path, "check", "collect") == [
        "collection error: adb sees no devices at all."])


def test_what_a_command_said_is_cut_to_its_last_lines(tmp_path):
    log = job.log_path(tmp_path, "check", "collect")
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(f"line {i}" for i in range(40)) + "\n\n" + "x" * 900 + "\n", encoding="utf-8")
    said = job.said(tmp_path, "check", "collect", lines=3, width=20)
    check("the last lines, blank ones left out", said[:2] == ["line 38", "line 39"], said)
    check("a long line is cut, and says so", said[2] == "x" * 20 + " …")
    check("no file, no words", job.said(tmp_path, "check", "analyze") == [])


def test_a_pattern_stands_for_the_files_that_match_when_its_step_starts(tmp_path):
    def collect_writes(argv, **kw):
        if argv[0] == "collect":
            for i in (1, 0):
                (tmp_path / f"coldStart_iter00{i}.perfetto-trace").write_bytes(b"x")
        return Command(argv, kw["stdout"])
    plan = [{"id": "collect", "argv": ["collect"], "shown": ""},
            {"id": "analyze", "argv": ["analyze", {"glob": str(tmp_path / "coldStart_iter*.perfetto-trace")}, "--html"], "shown": ""}]
    started = []

    def popen(argv, **kw):
        started.append(argv)
        return collect_writes(argv, **kw)
    job.run(tmp_path, "check", {"steps": plan}, popen=popen, signals=False)
    check("the files the first command wrote, each by its name, sorted", started[1] == [
        "analyze", str(tmp_path / "coldStart_iter000.perfetto-trace"), str(tmp_path / "coldStart_iter001.perfetto-trace"), "--html"],
        started)


def test_a_pattern_that_matches_nothing_fails_its_step_in_a_sentence(tmp_path):
    plan = [{"id": "analyze", "argv": ["analyze", {"glob": str(tmp_path / "none_iter*.perfetto-trace")}], "shown": ""}]
    commands = Commands()
    job.run(tmp_path, "check", {"steps": plan}, popen=commands, signals=False)
    state = job.read(tmp_path, "check")
    check("nothing was started", not commands.started and state["status"] == job.FAILED and state["steps"][0]["status"] == "failed")
    check("and the reason is where a command's words would be",
          job.said(tmp_path, "check", "analyze") == [f"no file matches {tmp_path / 'none_iter*.perfetto-trace'}"])


def test_a_command_that_cannot_be_started_fails_its_step(tmp_path):
    def popen(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory")
    job.run(tmp_path, "check", {"steps": steps("collect", "analyze")}, popen=popen, signals=False)
    state = job.read(tmp_path, "check")
    check("failed, the rest not started", state["status"] == job.FAILED and [s["status"] for s in state["steps"]] == ["failed", "waiting"])
    check("in a sentence", job.said(tmp_path, "check", "collect") == ["could not be started: No such file or directory"])


@pytest.mark.skipif(os.name != "posix", reason="Ctrl-C to one process is POSIX")
def test_stop_ends_the_command_the_way_ctrl_c_does_and_starts_nothing_more(tmp_path):
    def press_stop(command):
        check("asked through the job's own file", job.stop(tmp_path, "check"))
    commands = Commands(collect={"waits": 1, "on_wait": press_stop})
    code = run(tmp_path, commands, steps("collect", "analyze"))
    state = job.read(tmp_path, "check")
    check("the command was asked once, with SIGINT", commands.started[0].signals == [signal.SIGINT] and not commands.started[0].killed)
    check("it reads as stopped", code == 1 and state["status"] == job.STOPPED and state["steps"][0]["status"] == "stopped")
    check("and the next command never started", len(commands.started) == 1 and state["steps"][1]["status"] == "waiting")
    check("the request is gone with the job", not job.stop_path(tmp_path, "check").exists())
    check("a job that ended has nothing to stop", job.stop(tmp_path, "check") is False)


def test_a_stop_between_two_commands_starts_no_second(tmp_path):
    def collect_then_stop(argv, **kw):
        job.stop_path(tmp_path, "check").write_text(str(os.getpid()), encoding="utf-8")
        return Command(argv, kw["stdout"])
    started = []

    def popen(argv, **kw):
        started.append(argv[0])
        return collect_then_stop(argv, **kw)
    job.run(tmp_path, "check", {"steps": steps("collect", "analyze")}, popen=popen, signals=False)
    state = job.read(tmp_path, "check")
    check("the first one ran to its end", started == ["collect"] and state["steps"][0]["status"] == "done")
    check("and it reads as stopped, the second waiting", state["status"] == job.STOPPED and state["steps"][1]["status"] == "waiting")


def test_a_request_meant_for_another_process_is_not_this_ones(tmp_path):
    path = job.stop_path(tmp_path, "check")
    path.parent.mkdir(parents=True)
    path.write_text("1", encoding="utf-8")
    commands = Commands(collect={"waits": 2, "on_wait": lambda c: path.write_text("1", encoding="utf-8")})
    run(tmp_path, commands, steps("collect"))
    check("a request left by a job that is gone is cleared and ignored", job.read(tmp_path, "check")["status"] == job.DONE
          and not commands.started[0].signals)


def test_a_command_that_will_not_end_is_killed_after_a_while(tmp_path):
    now = [1000.0]

    def tick(command):
        job.stop(tmp_path, "check")
        now[0] += job.GRACE_S / 2
    commands = Commands(collect={"stubborn": True, "on_wait": tick})
    run(tmp_path, commands, steps("collect"), clock=lambda: now[0])
    check("asked first, killed when it stayed", commands.started[0].signals and commands.started[0].killed)
    check("and the job says it was stopped", job.read(tmp_path, "check")["status"] == job.STOPPED)


def test_a_signal_to_the_job_ends_it_as_interrupted(tmp_path):
    asked = job._Asked(job.stop_path(tmp_path, "check"))
    check("nothing asked yet", asked.how() is None)
    asked.by_signal(signal.SIGTERM, None)
    check("a signal is an interruption, whatever the file says", asked.how() == job.INTERRUPTED)


@pytest.mark.skipif(os.name != "posix", reason="signals to a process are POSIX")
def test_the_terminal_closing_ends_a_real_job_and_its_command(tmp_path):
    """The real process, with a command that waits to be told: SIGTERM to the
    job reaches the command as Ctrl-C, and the job says it was interrupted.
    The command says that it runs through a pipe the test reads: no timer."""
    going = tmp_path / "going"
    os.mkfifo(going)
    plan = {"steps": [{"id": "collect", "shown": "", "argv": [
        sys.executable, "-c", "import sys, time\ntry:\n    open(sys.argv[1], 'w').close()\n    time.sleep(600)\n"
                              "except BaseException as e:\n    print('put back:', type(e).__name__)\n    raise\n", str(going)]}]}
    proc = subprocess.Popen([sys.executable, "-m", "echolot.job", str(tmp_path), "check"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(json.dumps(plan).encode())
    proc.stdin.close()
    check("it says when its state is on disk", proc.stdout.readline().strip() == job.READY.encode())
    going.read_text()                       # comes back once the command has opened it: it runs
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=60)
    state = job.read(tmp_path, "check")
    check("interrupted, not running and not failed", state["status"] == job.INTERRUPTED and state["end"] == job.INTERRUPTED, state)
    check("the command was asked as Ctrl-C asks, and unwound", state["steps"][0]["status"] == job.STOPPED
          and job.said(tmp_path, "check", "collect")[0] == "put back: KeyboardInterrupt", job.said(tmp_path, "check", "collect"))


# --- reading a job ------------------------------------------------------------------------------

def write(root: Path, **state) -> None:
    path = job.state_path(root, "check")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"job": "check", "pid": 4242, "started": 100.0, "finished": None, "what": {},
                                "steps": [], **state}), encoding="utf-8")


def test_a_job_whose_process_is_gone_was_interrupted_and_is_not_running(tmp_path):
    write(tmp_path)
    check("running while its process is there", job.read(tmp_path, "check", alive=lambda pid: pid == 4242)["status"] == job.RUNNING)
    check("interrupted once it is gone", job.read(tmp_path, "check", alive=lambda pid: False)["status"] == job.INTERRUPTED)
    write(tmp_path, pid=None)
    check("one that never said which process is no running one", job.read(tmp_path, "check", alive=lambda pid: True)["status"]
          == job.INTERRUPTED)
    write(tmp_path, finished=200.0, end="stopped")
    check("one that ended says how, whatever became of the process", job.read(tmp_path, "check", alive=lambda pid: True)["status"]
          == job.STOPPED)
    write(tmp_path, finished=200.0, end="something else")
    check("an end it does not know is a failure", job.read(tmp_path, "check")["status"] == job.FAILED)


def test_the_process_is_asked_of_the_system_when_nobody_says(tmp_path):
    write(tmp_path, pid=os.getpid())
    check("this process is running", job.read(tmp_path, "check")["status"] == job.RUNNING)


@pytest.mark.parametrize("text", ["", "{ cut short", "[]", "5", '{"steps": 3, "started": 1}', '{"steps": []}'])
def test_a_file_that_is_no_state_is_no_job(tmp_path, text):
    path = job.state_path(tmp_path, "check")
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    check("nothing to say", job.read(tmp_path, "check") is None)
    check("and nothing to stop", job.stop(tmp_path, "check") is False)


def test_no_job_was_ever_started(tmp_path):
    check("nothing", job.read(tmp_path, "check") is None and job.stop(tmp_path, "check") is False)
    check("and nothing was written to find that out", not (tmp_path / ".echolot").exists())


# --- starting one -------------------------------------------------------------------------------

def test_one_job_of_a_name_at_a_time(tmp_path):
    write(tmp_path, pid=os.getppid())         # another process, and one that is there
    spawned = []
    with pytest.raises(job.Refused, match="already running"):
        job.start(tmp_path, "check", steps("collect"), spawn=lambda *a: spawned.append(a))
    check("nothing was started", not spawned)
    commands = Commands()
    code = run(tmp_path, commands, steps("collect"))
    check("and the process itself looks again before it runs anything", code == job.BUSY and not commands.started)
    check("the one that runs is left as it was", job.read(tmp_path, "check")["pid"] == os.getppid())


def test_starting_hands_the_plan_over_and_answers_with_the_state(tmp_path):
    handed = []

    def spawn(root, name, plan):
        handed.append((root, name, plan))
        job.run(root, name, plan, popen=Commands(), signals=False)
    state = job.start(tmp_path, "check", steps("collect"), {"scenario": "coldStart"}, spawn=spawn)
    check("the steps and what it is about", handed == [(tmp_path, "check", {"steps": steps("collect"), "what": {"scenario": "coldStart"}})])
    check("and the state as it is then", state["status"] == job.DONE and state["what"] == {"scenario": "coldStart"})
    with pytest.raises(job.Refused, match="left no state"):
        job.start(tmp_path / "elsewhere", "check", steps("collect"), spawn=lambda *a: None)


def test_the_real_process_says_when_it_is_ready_and_runs_to_its_end(tmp_path):
    """The one test that starts the job as `echolot ui` does: a Python that
    prints a line is its command. It is waited for by its exit."""
    plan = {"steps": [{"id": "one", "argv": [sys.executable, "-c", "print('as the command said it')"], "shown": "one"}],
            "what": {"k": 1}}
    reaper = job._spawn(tmp_path, "check", plan)
    state = job.read(tmp_path, "check")
    check("its state is on disk when the start comes back", state is not None and state["what"] == {"k": 1}
          and state["pid"] != os.getpid(), state)
    reaper.join(60)
    state = job.read(tmp_path, "check")
    check("done", not reaper.is_alive() and state["status"] == job.DONE and state["steps"][0]["exit"] == 0, state)
    check("with the command's words", job.said(tmp_path, "check", "one") == ["as the command said it"])


def test_a_process_that_ends_before_it_is_ready_is_a_refusal_with_its_last_words(tmp_path):
    elsewhere = tmp_path / "a-file"
    elsewhere.write_text("not a folder", encoding="utf-8")
    with pytest.raises(job.Refused) as refused:
        job._spawn(elsewhere, "check", {"steps": []})
    check("what it said last is the reason", "a-file" in str(refused.value) or "Error" in str(refused.value), str(refused.value))

    def no_python(*a, **kw):
        raise OSError(2, "No such file or directory")
    real = subprocess.Popen
    subprocess.Popen = no_python
    try:
        with pytest.raises(job.Refused, match="could not be started: No such file"):
            job._spawn(tmp_path, "check", {"steps": []})
    finally:
        subprocess.Popen = real


def test_the_module_run_by_hand_says_how_it_is_run(tmp_path, monkeypatch, capsys):
    check("no arguments", job.main([]) == 2 and "usage: python -m echolot.job" in capsys.readouterr().err)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{ cut short"))
    check("a plan that is no JSON", job.main([str(tmp_path), "check"]) == 2 and "not JSON" in capsys.readouterr().err)
