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
    log.write_text("the first megabytes\n" + "y" * job.TAIL_BYTES + "\nBUILD FAILED\nthe line that says why\n", encoding="utf-8")
    check("of a long file only the end is read, from a whole line on", job.said(tmp_path, "check", "collect")
          == ["BUILD FAILED", "the line that says why"], job.said(tmp_path, "check", "collect")[:1])


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


@pytest.mark.parametrize("change", [
    {"started": "now"}, {"started": True}, {"started": float("inf")}, {"finished": "then"}, {"pid": 0}, {"pid": -1},
    {"pid": "4242"}, {"pid": 2 ** 40}, {"what": []}, {"steps": [5]}, {"steps": [{"id": "collect"}]},
    {"steps": [{"id": 1, "status": "done"}]}, {"steps": [{"id": "collect", "status": "running", "started": "now"}]},
    {"steps": [{"id": "collect", "status": "failed", "exit": "2"}]}, {"steps": [{"id": "collect", "status": "done", "log": 5}]},
], ids=lambda change: json.dumps(change)[:40])
def test_a_file_with_a_field_of_the_wrong_type_is_no_job(tmp_path, change):
    """What a job never writes: text for a time, a process id that names this
    process's own group, a step that is no step. Whoever reads a job does
    sums with its times and asks the system about its process."""
    write(tmp_path, **change)
    check("no job", job.read(tmp_path, "check", alive=lambda pid: True) is None)
    check("so nothing to stop, and none that runs for ever", job.stop(tmp_path, "check") is False)
    commands = Commands()
    check("and the next one starts over it", run(tmp_path, commands, steps("collect")) == 0
          and job.read(tmp_path, "check")["status"] == job.DONE)


def test_what_a_job_writes_reads_back(tmp_path):
    write(tmp_path, pid=4242, what={"scenario": "coldStart"}, steps=[
        {"id": "collect", "argv": ["x"], "shown": "echolot collect", "status": "done", "started": 100.5, "finished": 130,
         "exit": 0, "log": ".echolot/log/check.collect.log", "pid": 77},
        {"id": "analyze", "argv": ["y"], "shown": "", "status": "waiting"}])
    check("a file with every field as a job leaves it is a job", job.read(tmp_path, "check", alive=lambda pid: True)["status"]
          == job.RUNNING)


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


@pytest.mark.skipif(os.name != "posix", reason="Ctrl-C to one process is POSIX")
def test_a_command_that_ended_just_as_it_was_asked_to(tmp_path):
    class Gone(Command):
        def send_signal(self, signum):
            self.signals.append(signum)
            raise ProcessLookupError(3, "No such process")

    def popen(argv, **kw):
        return Gone(argv, kw["stdout"], waits=1, on_wait=lambda c: job.stop(tmp_path, "check"))
    job.run(tmp_path, "check", {"steps": steps("collect")}, popen=popen, signals=False)
    check("nothing to signal is no failure of the job: it reads as stopped", job.read(tmp_path, "check")["status"] == job.STOPPED)


# --- a step that starts commands of its own ----------------------------------------------------

class Table:
    """The processes of this computer as `ps` would list them: the job's
    command (pid 4000 + the words of its command line), and under it the
    ones a test names, each with its parent. Signals go to `kill`, which
    takes a process off the list when it gives way, the way it would end."""

    def __init__(self, under: dict[int, int], stays: tuple[int, ...] = (), stubborn_to: int | None = None):
        self.rows = {pid: {"pid": pid, "ppid": parent, "age": 30.0, "args": f"process {pid}"}
                     for pid, parent in under.items()}
        self.stays, self.stubborn_to = set(stays), stubborn_to
        self.sent: list[tuple[int, int]] = []

    def __call__(self):
        return list(self.rows.values())

    def kill(self, pid: int, signum: int) -> None:
        self.sent.append((pid, signum))
        if signum == 0:
            if pid not in self.rows:
                raise ProcessLookupError(pid)
            return
        if pid in self.rows and (pid not in self.stays or signum == self.stubborn_to):
            del self.rows[pid]


def tree(name: str) -> list[dict]:
    return [{"id": name, "argv": [name, "--flag"], "shown": name, "tree": True, "unset": ["CLAUDECODE"]}]


def test_a_tree_step_asks_everything_under_it_as_ctrl_c_does(tmp_path):
    def press_stop(command):
        job.stop(tmp_path, "check")
    commands = Commands(agent={"waits": 1, "on_wait": press_stop})
    # The agent (4002) runs a shell (5001), which runs a recording (5002), which runs adb (5003).
    table = Table({5001: 4002, 5002: 5001, 5003: 5002, 6000: 1})
    run(tmp_path, commands, tree("agent"), procs=table, kill=table.kill)
    sent = [(pid, signum) for pid, signum in table.sent if signum]
    check("Ctrl-C to everything under it, the deepest first, before anything stronger",
          sent[:3] == [(5003, signal.SIGINT), (5002, signal.SIGINT), (5001, signal.SIGINT)], sent)
    check("and to the command itself", commands.started[0].signals == [signal.SIGINT])
    check("a process that is not under it is left alone", all(pid != 6000 for pid, _ in table.sent))
    check("nothing had to be ended harder", not any(s in (signal.SIGTERM, signal.SIGKILL) for _, s in sent[:3]))
    check("what is left in its own group is ended once it is gone", sent[-1] == (-4002, signal.SIGTERM), sent)
    state = job.read(tmp_path, "check")
    check("it reads as stopped", state["status"] == job.STOPPED and state["steps"][0]["status"] == job.STOPPED)
    check("and the state says what kind of step it was", state["steps"][0]["tree"] is True
          and state["steps"][0]["unset"] == ["CLAUDECODE"])


def test_what_stays_under_a_tree_step_is_ended_then_killed(tmp_path):
    now = [1000.0]

    def tick(command):
        job.stop(tmp_path, "check")
        now[0] += job.GRACE_S
    commands = Commands(agent={"stubborn": True, "on_wait": tick})
    table = Table({5001: 4002, 5002: 5001}, stays=(5001, 5002), stubborn_to=signal.SIGKILL)
    run(tmp_path, commands, tree("agent"), procs=table, kill=table.kill, clock=lambda: now[0])
    sent = [(pid, signum) for pid, signum in table.sent if signum and pid > 0]
    check("asked, then ended, then killed, each time the deepest first",
          sent == [(5002, signal.SIGINT), (5001, signal.SIGINT), (5002, signal.SIGTERM), (5001, signal.SIGTERM),
                   (5002, signal.SIGKILL), (5001, signal.SIGKILL)], sent)
    command = commands.started[0]
    check("the command too: Ctrl-C, then the end, then killed",
          command.signals == [signal.SIGINT, signal.SIGTERM] and command.killed, command.signals)
    check("and nothing is left", not table.rows)


def test_a_process_listed_before_is_not_ended_once_its_id_is_anothers(tmp_path):
    now = [1000.0]
    table = Table({5001: 4002}, stays=(5001,))

    def tick(command):
        job.stop(tmp_path, "check")
        now[0] += job.GRACE_S
        if any(signum == signal.SIGINT for _, signum in table.sent):
            # It ended, and the id went to another program at once.
            table.rows[5001] = {"pid": 5001, "ppid": 1, "age": 1.0, "args": "somebody else"}
    commands = Commands(agent={"stubborn": True, "on_wait": tick})
    run(tmp_path, commands, tree("agent"), procs=table, kill=table.kill, clock=lambda: now[0])
    check("Ctrl-C reached it while it was the one", (5001, signal.SIGINT) in table.sent)
    check("and nothing stronger went to the program that has its id now",
          not [s for pid, s in table.sent if pid == 5001 and s in (signal.SIGTERM, signal.SIGKILL)], table.sent)


def test_what_a_tree_step_leaves_in_its_group_when_it_ends_by_itself_is_ended(tmp_path):
    commands = Commands(agent={})
    table = Table({})
    code = run(tmp_path, commands, tree("agent"), procs=table, kill=table.kill)
    check("it ended well", code == 0 and job.read(tmp_path, "check")["status"] == job.DONE)
    check("and its group was told to end, nothing else", table.sent == [(-4002, signal.SIGTERM)], table.sent)
    plain = Table({})
    run(tmp_path, Commands(collect={}), steps("collect"), procs=plain, kill=plain.kill)
    check("a step that is no tree is left to end as it ends", plain.sent == [])


def test_a_step_runs_without_the_names_it_leaves_out(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ECHOLOT_KEPT", "yes")
    seen = []

    def popen(argv, **kw):
        seen.append(kw["env"])
        return Command(argv, kw["stdout"])
    table = Table({})
    job.run(tmp_path, "check", {"steps": tree("agent") + steps("collect")}, popen=popen, signals=False,
            procs=table, kill=table.kill)
    check("the name is not there for the step that leaves it out", "CLAUDECODE" not in seen[0]
          and seen[0]["ECHOLOT_KEPT"] == "yes" and seen[0]["PYTHONUNBUFFERED"] == "1")
    check("and is there for the next", seen[1]["CLAUDECODE"] == "1")


@pytest.mark.parametrize("text, seconds", [("05:12", 312.0), ("1:02:03", 3723.0), ("2-01:00:00", 176400.0),
                                           ("0", 0.0), ("ab:cd", None), ("1:2:3:4", None)])
def test_how_long_a_process_ran_is_read_as_ps_says_it(text, seconds):
    check(f"{text}", job._etime(text) == seconds, job._etime(text))


def test_the_processes_of_this_computer_are_read_from_ps():
    said = ("    1     0 2-00:00:01 /sbin/launchd\n  4002     1      12:00 claude -p the request\n"
            "  5001  4002      01:30 /bin/zsh -c ./gradlew installDebug\n  oops\n"
            "  5002  5001      01:29 java -cp gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain installDebug\n")
    asked = []

    def ps(argv, **kw):
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=said, stderr="")
    table = job.processes(run=ps)
    check("ps is asked for every process with what the page needs", asked == [["ps", "-A", "-o", "pid=,ppid=,etime=,args="]]
          or os.name != "posix", asked)
    if os.name != "posix":
        return
    check("a row each, a line that is none left out", [p["pid"] for p in table] == [1, 4002, 5001, 5002])
    check("with its parent, its age and its whole command line", table[2] == {
        "pid": 5001, "ppid": 4002, "age": 90.0, "args": "/bin/zsh -c ./gradlew installDebug"})
    check("what is under a process, the deepest first", [p["pid"] for p in job.descendants(4002, table)] == [5002, 5001])
    check("nothing is under a process with no children", job.descendants(5002, table) == [])

    def broken(argv, **kw):
        raise OSError("no ps here")
    check("no ps: no processes, and no error", job.processes(run=broken) == [])


# --- a step that ends itself once nothing moves under it (#454) ------------------------------------

HELPER = "/opt/example/codex/bin/codex-code-mode-host"


def idle_tree(root: Path, seconds: float = 1800.0) -> list[dict]:
    """A tree step whose plan says how long nothing may move: a log it
    watches, and the client's own helpers, which run the whole time."""
    return [{**tree("agent")[0], "idle": {"seconds": seconds, "watch": ["watched.log"], "helpers": ["codex"]}}]


def watched(root: Path, at: float) -> Path:
    path = root / "watched.log"
    path.write_text("an event\n", encoding="utf-8")
    os.utime(path, (at, at))
    return path


def test_a_step_with_nothing_moving_under_it_for_its_time_is_ended_and_says_why(tmp_path):
    now = [1000.0]
    watched(tmp_path, 1000.0)

    def tick(command):
        now[0] += 700.0                                   # three looks: at 700, 1400 and 2100 seconds
    commands = Commands(agent={"waits": 10, "on_wait": tick})
    # Under it, only the client's own helper, which has run since before it started.
    table = Table({5001: 4002})
    table.rows[5001].update(args=HELPER, age=99999.0)
    run(tmp_path, commands, idle_tree(tmp_path), procs=table, kill=table.kill, clock=lambda: now[0])
    state = job.read(tmp_path, "check")
    check("ended as a stop is, and the state says why", state["status"] == job.STOPPED
          and state["steps"][0]["status"] == job.STOPPED and state["why"] == job.IDLE, state)
    check("once its time had passed, not before", now[0] == 3100.0, now)
    check("Ctrl-C to what is under it and to the command, as Stop sends it",
          (5001, signal.SIGINT) in table.sent and commands.started[0].signals == [signal.SIGINT], table.sent)
    check("the plan's time is kept beside the step", state["steps"][0]["idle"]["seconds"] == 1800.0)


def test_a_build_that_runs_long_under_it_is_never_idle(tmp_path):
    now = [1000.0]
    watched(tmp_path, 1000.0)

    def tick(command):
        now[0] += 700.0
        if now[0] >= 1000.0 + 5 * 700.0:
            job.stop(tmp_path, "check")                     # a person ends it, after an hour
    commands = Commands(agent={"waits": 10, "on_wait": tick})
    table = Table({5001: 4002, 5002: 5001})
    table.rows[5001].update(args=HELPER, age=99999.0)
    table.rows[5002].update(args="java org.gradle.wrapper.GradleWrapperMain :app:installDebug", age=99999.0)
    run(tmp_path, commands, idle_tree(tmp_path), procs=table, kill=table.kill, clock=lambda: now[0])
    state = job.read(tmp_path, "check")
    check("it ran until it was stopped, and was not ended for being idle",
          state["status"] == job.STOPPED and "why" not in state and now[0] == 4500.0, (state, now))


def test_a_file_it_watches_written_is_movement(tmp_path):
    now = [1000.0]
    log = watched(tmp_path, 1000.0)

    def tick(command):
        now[0] += 700.0
        os.utime(log, (now[0] - 1.0, now[0] - 1.0))         # the client said something just now
        if now[0] >= 1000.0 + 5 * 700.0:
            job.stop(tmp_path, "check")
    commands = Commands(agent={"waits": 10, "on_wait": tick})
    table = Table({5001: 4002})
    table.rows[5001].update(args=HELPER, age=99999.0)
    run(tmp_path, commands, idle_tree(tmp_path), procs=table, kill=table.kill, clock=lambda: now[0])
    check("a client that writes is not idle", "why" not in job.read(tmp_path, "check"))


def test_where_the_processes_cannot_be_listed_nothing_is_idle(tmp_path):
    """No `ps` (Windows): a build that runs for an hour would look like a wait."""
    now = [1000.0]
    watched(tmp_path, 1000.0)

    def tick(command):
        now[0] += 700.0
        if now[0] >= 1000.0 + 5 * 700.0:
            job.stop(tmp_path, "check")
    table = Table({})
    run(tmp_path, Commands(agent={"waits": 10, "on_wait": tick}), idle_tree(tmp_path), procs=table, kill=table.kill,
        clock=lambda: now[0])
    check("it ran until it was stopped", "why" not in job.read(tmp_path, "check") and now[0] == 4500.0, now)


def test_a_process_that_started_under_it_is_movement_and_a_step_without_a_time_is_never_idle(tmp_path):
    now = [1000.0]
    watched(tmp_path, 1000.0)
    table = Table({5001: 4002})

    def tick(command):
        now[0] += 700.0
        # A command it runs now and then, just started each time it is looked at.
        table.rows[5001].update(args="/bin/zsh -lc 'echolot hunt --show 1'", age=5.0)
        if now[0] >= 1000.0 + 5 * 700.0:
            job.stop(tmp_path, "check")
    run(tmp_path, Commands(agent={"waits": 10, "on_wait": tick}), idle_tree(tmp_path), procs=table, kill=table.kill,
        clock=lambda: now[0])
    check("a process just started under it is movement", "why" not in job.read(tmp_path, "check"))
    now[0] = 1000.0
    empty = Table({})

    def later(command):
        now[0] += 700.0
        if now[0] >= 1000.0 + 5 * 700.0:
            job.stop(tmp_path, "check")
    run(tmp_path, Commands(agent={"waits": 10, "on_wait": later}), tree("agent"), procs=empty, kill=empty.kill,
        clock=lambda: now[0])
    state = job.read(tmp_path, "check")
    check("a step whose plan says no time runs until it is stopped", "why" not in state
          and "idle" not in state["steps"][0])
    run(tmp_path, Commands(collect={}), [{**steps("collect")[0], "idle": {"seconds": 1}}], procs=empty, kill=empty.kill)
    check("and only a tree step takes one", "idle" not in job.read(tmp_path, "check")["steps"][0])


@pytest.mark.parametrize("args, helper", [(HELPER, True), ("codex exec --json", True), ("/usr/local/bin/codex", True),
                                          ("codexfoo", False), ("/bin/zsh -lc codex", False), ("", False),
                                          ("./gradlew assembleDebug", False)])
def test_the_client_s_own_helpers_are_known_by_the_program_s_name(args, helper):
    check(f"{args!r}", job._helper(args, ("codex",)) is helper)


def test_a_state_whose_why_is_no_word_is_no_job(tmp_path):
    write(tmp_path, why=3)
    check("refused as any other field of the wrong type", job.read(tmp_path, "check") is None)
