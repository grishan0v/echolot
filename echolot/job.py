"""Commands run one after another as a process of their own, for a page to
start and to watch.

`echolot ui` draws pages and must go on drawing them while something runs, and
it keeps no state. So what a button starts is a process beside the server:
this module, run as `python -m echolot.job`. It runs the commands it was
handed, in order, each exactly as a person would type it, and stops at the
first that fails.

Everything about a job is in files under `.echolot/log/`, named after the job:

    <name>.json        where it stands: the process, each command with its
                       state and exit code, how it ended
    <name>.<id>.log    what one command printed, stdout and stderr together,
                       as it said it
    <name>.stop        a request to stop, with the process it is meant for

Whoever reads the state (`read`) needs nothing else: a reloaded page, a second
tab and a restarted server all read the same. A job whose file says it never
finished and whose process is gone was cut short from outside, and reads as
`interrupted`, never as still running.

To stop one, `stop` leaves the request in a file and the job's own process
finds it: it asks the command that is running to end the way Ctrl-C asks
(SIGINT, so a recording unwinds and puts the phone back), waits for it, and
starts nothing more. A signal to the job itself (the terminal closed, the
server's Ctrl-C reaching its children) ends it the same way and is recorded as
`interrupted`.

Only the standard library is imported here: the process starts fast, and the
page that started it waits for its first word.
"""

from __future__ import annotations

import contextlib
import glob as glob_mod
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

LOG_DIR = Path(".echolot") / "log"

# How often a job asks whether it was told to stop, in seconds.
POLL_S = 0.25
# How long a command may take to end once asked to, before it is killed. A
# recording that unwinds puts the phone's settings back, one adb call each.
GRACE_S = 30.0

RUNNING, DONE, FAILED, STOPPED, INTERRUPTED = "running", "done", "failed", "stopped", "interrupted"
WAITING = "waiting"
# What a job says on stdout once its state is on disk: the one word its
# starter waits for.
READY = "started"
# The exit code of a job that found another one running under its name.
BUSY = 3


def state_path(project: Path, name: str) -> Path:
    return project / LOG_DIR / f"{name}.json"


def stop_path(project: Path, name: str) -> Path:
    return project / LOG_DIR / f"{name}.stop"


def log_path(project: Path, name: str, step: str) -> Path:
    return project / LOG_DIR / f"{name}.{step}.log"


def _write(path: Path, state: dict[str, Any]) -> None:
    """The whole state, replaced in one move: a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read(project: Path, name: str, alive: Callable[[int], bool] | None = None) -> dict[str, Any] | None:
    """Where the job stands, or None when none was ever started here.

    `status` is added to what the file holds: `running` while its process is
    there, `interrupted` when the file says it never finished and the process
    is gone, else how it ended: `done`, `failed`, `stopped`, `interrupted`.
    """
    try:
        state = json.loads(state_path(project, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("steps"), list) or "started" not in state:
        return None
    if state.get("finished") is None:
        if alive is None:
            from .state import _alive
            alive = _alive
        pid = state.get("pid")
        state["status"] = RUNNING if isinstance(pid, int) and alive(pid) else INTERRUPTED
    else:
        state["status"] = state.get("end") if state.get("end") in (DONE, FAILED, STOPPED, INTERRUPTED) else FAILED
    return state


# How much of the end of a command's output is read to find its last lines.
TAIL_BYTES = 64 * 1024


def said(project: Path, name: str, step: str, lines: int = 12, width: int = 400) -> list[str]:
    """The last lines a command printed, as it printed them: what a page shows
    under its own sentence when the command failed. Long lines are cut, and
    of a long file only the end is read: a build that failed can print
    megabytes before the line that says why."""
    try:
        with open(log_path(project, name, step), "rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            log.seek(max(0, size - TAIL_BYTES))
            text = log.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    if size > TAIL_BYTES:
        text = text.split("\n", 1)[-1]      # the first line read is the end of a longer one
    kept = [line.rstrip() for line in text.splitlines() if line.strip()]
    return [line if len(line) <= width else line[:width] + " …" for line in kept[-lines:]]


# ---------------------------------------------------------------------------
# starting one, and asking it to stop: what the server does


class Refused(Exception):
    """A job that was not started, with the sentence that says why."""


def _spawn(project: Path, name: str, plan: dict[str, Any]) -> threading.Thread:
    """Start the job's process and wait for its first word.

    The plan goes in on stdin and one line comes back on stdout once the
    state is on disk, so the page drawn next finds the job there. The wait is
    for that line and for nothing else: no timer. What comes back is the
    thread that collects the process's exit, for whoever wants to wait for
    the job to end.
    """
    try:
        proc = subprocess.Popen([sys.executable, "-m", "echolot.job", str(project), name],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise Refused(f"the process that runs it could not be started: {e.strerror or e}") from None
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    try:
        proc.stdin.write(json.dumps(plan).encode("utf-8"))
        proc.stdin.close()
        word = proc.stdout.readline().decode("utf-8", "replace").strip()
    except OSError:
        word = ""
    if word != READY:
        # It ended before it said so: what it left on stderr says why.
        why = proc.stderr.read().decode("utf-8", "replace").strip().splitlines()
        proc.wait()
        raise Refused(why[-1] if why else "the process that runs it ended at once and said nothing")
    proc.stdout.close()
    proc.stderr.close()
    # Its exit is collected when it comes: a process nobody waited for stays
    # in the table of processes, and would read as still running.
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    return reaper


def start(project: Path, name: str, steps: list[dict[str, Any]], what: dict[str, Any] | None = None, *,
          spawn: Callable[[Path, str, dict[str, Any]], Any] = _spawn) -> dict[str, Any]:
    """Start the commands as a job and return its state once it is on disk.

    A step is `{"id", "argv", "shown"}`: a name unique in the job, the
    command, and the command as a person would type it. An argument that is
    `{"glob": pattern}` stands for the files that match when its step
    starts, sorted, as a shell would put them there. `what` is whatever the
    starter wants kept beside the steps; it is not read here.

    Raises `Refused` when a job of this name is running, or when its process
    did not start.
    """
    current = read(project, name)
    if current is not None and current["status"] == RUNNING:
        raise Refused("one is already running")
    spawn(project, name, {"steps": steps, "what": what or {}})
    state = read(project, name)
    if state is None:
        raise Refused("the process that runs it left no state behind")
    return state


def stop(project: Path, name: str) -> bool:
    """Ask the running job to stop. False when none is running."""
    state = read(project, name)
    if state is None or state["status"] != RUNNING:
        return False
    path = stop_path(project, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(state["pid"]), encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# the job itself: what `python -m echolot.job <project> <name>` does


def _expand(argv: list[Any]) -> tuple[list[str], str | None]:
    """The command with every `{"glob": pattern}` replaced by the files that
    match now. The second value names a pattern that matched nothing."""
    out: list[str] = []
    for arg in argv:
        if isinstance(arg, dict):
            pattern = str(arg.get("glob") or "")
            found = sorted(glob_mod.glob(pattern))
            if not found:
                return out, pattern
            out += found
        else:
            out.append(str(arg))
    return out, None


class _Asked:
    """Whether the job was asked to end, and by whom: the stop file, or a
    signal sent to the job's own process."""

    def __init__(self, path: Path):
        self.path = path
        self.signal: int | None = None

    def by_signal(self, signum: int, _frame: Any) -> None:
        self.signal = signum

    def how(self) -> str | None:
        if self.signal is not None:
            return INTERRUPTED
        try:
            meant = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return STOPPED if meant == str(os.getpid()) else None


def _end(proc: Any, clock: Callable[[], float]) -> None:
    """Ask a command to end the way Ctrl-C does, and wait for it. One that is
    still there after `GRACE_S` is killed."""
    try:
        if os.name == "posix":
            proc.send_signal(signal.SIGINT)
        else:
            # No Ctrl-C can be sent to one process there: it is ended.
            proc.terminate()
    except OSError:
        pass
    until = clock() + GRACE_S
    while True:
        try:
            proc.wait(timeout=POLL_S)
            return
        except subprocess.TimeoutExpired:
            if clock() >= until:
                with contextlib.suppress(OSError):
                    proc.kill()
                proc.wait()
                return


def run(project: Path, name: str, plan: dict[str, Any], *,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
        ready: Callable[[], None] = lambda: None,
        signals: bool = True) -> int:
    """Run the plan's commands in order and keep the state file current.

    `ready` is called once the state is on disk. `popen`, `clock` and
    `signals` are a test's to replace.
    """
    path, stopping = state_path(project, name), stop_path(project, name)
    current = read(project, name)
    if current is not None and current["status"] == RUNNING and current.get("pid") != os.getpid():
        print(f"another `{name}` job is running here (process {current.get('pid')})", file=sys.stderr)
        return BUSY
    # A request left for a job that is gone is not meant for this one.
    with contextlib.suppress(OSError):
        stopping.unlink()

    asked = _Asked(stopping)
    if signals:
        for signame in ("SIGINT", "SIGTERM", "SIGHUP"):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                signal.signal(getattr(signal, signame), asked.by_signal)

    steps = [{"id": str(s["id"]), "argv": list(s["argv"]), "shown": str(s.get("shown") or ""),
              "status": WAITING} for s in plan.get("steps") or []]
    state: dict[str, Any] = {"job": name, "pid": os.getpid(), "started": clock(), "finished": None,
                             "what": plan.get("what") or {}, "steps": steps}
    _write(path, state)
    ready()

    end = DONE
    for step in steps:
        how = asked.how()
        if how:
            end = how
            break
        argv, missing = _expand(step["argv"])
        log = log_path(project, name, step["id"])
        step.update(status=RUNNING, started=clock(), log=log.relative_to(project).as_posix())
        _write(path, state)
        if missing is not None:
            log.write_text(f"no file matches {missing}\n", encoding="utf-8")
            step.update(status=FAILED, finished=clock(), exit=None)
            end = FAILED
            break
        with open(log, "wb") as out:
            try:
                # A session of its own on POSIX: a Ctrl-C in the terminal the
                # server runs in reaches this process, and it is this process
                # that asks the command to end, once.
                proc = popen(argv, cwd=str(project), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=(os.name == "posix"))
            except OSError as e:
                out.write(f"could not be started: {e.strerror or e}\n".encode("utf-8"))
                step.update(status=FAILED, finished=clock(), exit=None)
                end = FAILED
                break
            step["pid"] = proc.pid
            _write(path, state)
            how = None
            while True:
                try:
                    code = proc.wait(timeout=POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    how = asked.how()
                    if how:
                        _end(proc, clock)
                        code = proc.returncode
                        break
        step.update(finished=clock(), exit=code)
        if how:
            step["status"] = STOPPED
            end = how
            break
        if code != 0:
            step["status"] = FAILED
            end = FAILED
            break
        step["status"] = DONE
        _write(path, state)

    state.update(finished=clock(), end=end)
    _write(path, state)
    with contextlib.suppress(OSError):
        stopping.unlink()
    return 0 if end == DONE else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m echolot.job <project> <name>, with the plan as JSON on stdin", file=sys.stderr)
        return 2
    project, name = Path(args[0]), args[1]
    try:
        plan = json.loads(sys.stdin.read())
    except ValueError as e:
        print(f"the plan on stdin is not JSON: {e}", file=sys.stderr)
        return 2

    def ready() -> None:
        # The one word the starter waits for; after it nobody reads here.
        sys.stdout.write(READY + "\n")
        sys.stdout.flush()
        with contextlib.suppress(OSError, ValueError):
            null = os.open(os.devnull, os.O_RDWR)
            os.dup2(null, sys.stdout.fileno())
            os.dup2(null, sys.stderr.fileno())
            os.close(null)

    return run(project, name, plan, ready=ready)


if __name__ == "__main__":
    raise SystemExit(main())
