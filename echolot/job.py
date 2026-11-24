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

A step marked `tree` is a command that starts commands of its own, in groups
of their own (an agent: its builds, its recordings). Ending it alone would
leave them running, so the processes under it are listed before anything is
sent, and Ctrl-C goes to each of them as well; whatever is still there after
the grace is ended, then killed. When such a step ends by itself, what is
left in its process group is ended too. Such a step may also say how long
nothing may move under it (`idle`): no file it names written, no process
under it started, nothing running under it but the client's own. Its own are
its helpers, known by name, and whatever started under it in the first
seconds of the step (`STARTUP_S`): the programs its own settings start with
it, such as MCP servers, run the whole time and say nothing, while a command
it runs starts after its first reply. Once that long has passed, it is ended
the same way, and the state says why (`"why": "idle"`): a client that waits
for an answer nobody gives does not hold the page for hours. Where the
processes cannot be listed (no `ps`), nothing reads as idle: a long build
could not be told from a wait.

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
from typing import Any, Callable, Sequence

from . import files

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

    A file that does not read (`files.read_job`): one that does not parse,
    or holds in a field what a job never writes there, is no job: it was
    edited by hand or written by something else under this name, and the
    next job writes it anew.
    """
    got = files.read_job(state_path(project, name))
    if isinstance(got, files.Refusal):
        return None
    state = dict(got)
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


def path_first(first: Sequence[str], path: str | None) -> str:
    """`PATH` with these folders first, in this order, and each folder once."""
    ahead = [str(p) for p in first if p]
    rest = [p for p in (path or "").split(os.pathsep) if p and p not in ahead]
    return os.pathsep.join([*ahead, *rest])


def _env(step: dict[str, Any]) -> dict[str, str]:
    """What a command runs with: this process's environment, Python told to
    say each line as it comes, without the names the step asks to leave out
    (`unset`), and with the folders it asks for first in `PATH` (`path`)."""
    gone = set(step.get("unset") or ())
    env = {**{k: v for k, v in os.environ.items() if k not in gone}, "PYTHONUNBUFFERED": "1"}
    if step.get("path"):
        env["PATH"] = path_first(step["path"], env.get("PATH"))
    return env


# ---------------------------------------------------------------------------
# the processes under a command


# How long what is under a command has to end once it was told to, before it
# is killed.
TERM_S = 5.0


def _etime(text: str) -> float | None:
    """`ps`'s elapsed time, `[[dd-]hh:]mm:ss`, in seconds."""
    days, _, rest = text.strip().rpartition("-")
    try:
        parts = [int(p) for p in rest.split(":")]
        seconds = int(days) * 86400 if days else 0
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3:
        return None
    clock = 0
    for part in parts:
        clock = clock * 60 + part
    return float(seconds + clock)


def processes(run: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]]:
    """Every process of this computer as `ps` lists it: its id, its parent's,
    how long it has run in seconds (`age`), and its command line. Empty where
    there is no `ps` (Windows) or it says nothing."""
    if os.name != "posix":
        return []
    try:
        out = run(["ps", "-A", "-o", "pid=,ppid=,etime=,args="], capture_output=True, text=True,
                  timeout=10, errors="replace").stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    table = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table.append({"pid": pid, "ppid": ppid, "age": _etime(parts[2]), "args": parts[3] if len(parts) > 3 else ""})
    return table


def descendants(pid: int, table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The processes under `pid`, children of children included, the deepest
    first: the order to end them in, so that nothing is started anew by a
    parent that is still there."""
    children: dict[int, list[dict[str, Any]]] = {}
    for p in table:
        children.setdefault(p["ppid"], []).append(p)
    out: list[tuple[int, dict[str, Any]]] = []
    level, depth, seen = [pid], 0, {pid}
    while level:
        depth += 1
        below = [kid for parent in level for kid in children.get(parent, ()) if kid["pid"] not in seen]
        seen.update(kid["pid"] for kid in below)
        out += [(depth, kid) for kid in below]
        level = [kid["pid"] for kid in below]
    return [p for _, p in sorted(out, key=lambda pair: -pair[0])]


def _send(pids: list[int], signum: int, kill: Callable[[int, int], None]) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):
            kill(pid, signum)


def _still(under: list[dict[str, Any]], procs: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Which of the processes listed before are still there: the same id with
    the same command line. An id alone could by now be another process's."""
    now = {(p["pid"], p["args"]) for p in procs()}
    return [p for p in under if (p["pid"], p["args"]) in now]


def _quiet(proc: Any, under: list[dict[str, Any]], clock: Callable[[], float], seconds: float,
           procs: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    """Wait for the command and what was under it to be gone, for at most
    `seconds`. None when they are, else what is still there."""
    until = clock() + seconds
    while True:
        try:
            proc.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            pass
        else:
            under = _still(under, procs)
            if not under:
                return None
            if clock() < until:
                time.sleep(POLL_S)
        if clock() >= until:
            return _still(under, procs)


def _end_tree(proc: Any, clock: Callable[[], float], procs: Callable[[], list[dict[str, Any]]],
              kill: Callable[[int, int], None]) -> None:
    """Ctrl-C to a command and to everything under it, as a terminal sends it
    to a whole job: a recording unwinds and puts the phone back, a build is
    cancelled. Whatever is still there after `GRACE_S` is ended, and after
    `TERM_S` more, killed. The list is taken before anything is sent: a
    process whose parent has gone is no longer found under it."""
    under = descendants(proc.pid, procs())
    signal_int = getattr(signal, "SIGINT", 2)
    _send([p["pid"] for p in under], signal_int, kill)
    with contextlib.suppress(OSError):
        if os.name == "posix":
            proc.send_signal(signal_int)
        else:
            proc.terminate()
    left = _quiet(proc, under, clock, GRACE_S, procs)
    if left is None:
        return
    _send([p["pid"] for p in left], getattr(signal, "SIGTERM", 15), kill)
    with contextlib.suppress(OSError):
        proc.terminate()
    left = _quiet(proc, left, clock, TERM_S, procs)
    if left is None:
        return
    _send([p["pid"] for p in left], getattr(signal, "SIGKILL", 9), kill)
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait()


# How often a step that ends itself once nothing moves looks whether anything
# did, in seconds: each look lists the processes of the computer.
IDLE_EVERY_S = 15.0
# What the state says of a run ended that way (`why`), beside `stopped`.
IDLE = "idle"
# How long after a step starts a process under it may start and still be the
# client's own, in seconds: its helpers and the servers its settings start
# (MCP servers) start with it, and a command it runs only after its first
# reply. The plan's `idle` may say another number (`startup_s`).
STARTUP_S = 30.0


def _newest(path: Path) -> float:
    """When a path last changed: a file's time, or for a folder the newest of
    its own and its entries'. 0 where there is nothing."""
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0
    if path.is_dir():
        with contextlib.suppress(OSError):
            for entry in os.scandir(path):
                with contextlib.suppress(OSError):
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
    return newest


def _helper(args: str, helpers: tuple[str, ...]) -> bool:
    """Whether a process is one of the client's own helpers, by the name of
    the program it runs: `codex` or `codex-…` for `codex`."""
    first = args.split(None, 1)[0] if args.strip() else ""
    name = os.path.basename(first)
    return any(name == h or name.startswith(h + "-") for h in helpers)


class _Idle:
    """Whether nothing under a step has moved for as long as its plan allows.

    The plan's `idle` says how long (`seconds`), which files say it moved
    when they are written (`watch`, relative to the project), and the names
    of the client's own helper processes, which run the whole time and say
    nothing (`helpers`). Nothing moved means all three: no file it watches
    was written, no process under it started, and nothing runs under it but
    the client's own: those helpers, and any process that itself started in
    the step's first `STARTUP_S` (MCP servers its settings start, and the
    client's real program under a wrapper that starts it). A process that
    started later is something that runs, however it is related to an early
    one: a build or a recording that runs long never reads as idle.
    """

    def __init__(self, project: Path, step: dict[str, Any], clock: Callable[[], float]):
        given = step["idle"]
        self.project, self.clock = project, clock
        self.seconds = float(given["seconds"])
        self.watch = [str(w) for w in given.get("watch") or ()]
        self.helpers = tuple(str(h) for h in given.get("helpers") or ())
        self.startup = float(given.get("startup_s", STARTUP_S))
        self.started = float(step.get("started") or clock())
        self.next = clock()

    def due(self) -> bool:
        """Whether it is time to look again: at once, then every `IDLE_EVERY_S`."""
        now = self.clock()
        if now < self.next:
            return False
        self.next = now + IDLE_EVERY_S
        return True

    def passed(self, pid: int, procs: Callable[[], list[dict[str, Any]]]) -> bool:
        now = self.clock()
        table = procs()
        if not table:
            # No list of processes (no `ps`, as on Windows): a build that runs
            # long cannot be told from a wait, so nothing reads as idle.
            return False
        under = descendants(pid, table)
        if any(not self._own(p, now) for p in under):
            return False
        stamps = [self.started, *(_newest(self.project / rel) for rel in self.watch)]
        stamps += [now - p["age"] for p in under if isinstance(p.get("age"), (int, float))]
        return now - max(stamps) >= self.seconds

    def _own(self, p: dict[str, Any], now: float) -> bool:
        """Whether a process under the step is the client's own: a helper by
        name, or one that itself started in the step's first seconds. One
        whose age is not known is not: it may be a command that runs."""
        if _helper(p.get("args") or "", self.helpers):
            return True
        age = p.get("age")
        return isinstance(age, (int, float)) and not isinstance(age, bool) and now - age <= self.started + self.startup


def _reap(proc: Any, kill: Callable[[int, int], None]) -> None:
    """What is left in the process group of a command that ended: it was
    started in a session of its own, so the group is its own and nobody
    else's. A process that made a session of its own (a daemon) is in
    another group, and stays."""
    if os.name != "posix" or not isinstance(getattr(proc, "pid", None), int):
        return
    with contextlib.suppress(OSError):
        kill(-proc.pid, getattr(signal, "SIGTERM", 15))


def run(project: Path, name: str, plan: dict[str, Any], *,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
        ready: Callable[[], None] = lambda: None,
        signals: bool = True,
        procs: Callable[[], list[dict[str, Any]]] | None = None,
        kill: Callable[[int, int], None] | None = None) -> int:
    """Run the plan's commands in order and keep the state file current.

    `ready` is called once the state is on disk. `popen`, `clock`,
    `signals`, and for a step marked `tree` the list of processes (`procs`)
    and the way a signal is sent (`kill`), are a test's to replace.
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
    for step, given in zip(steps, plan.get("steps") or [], strict=True):
        if given.get("tree"):
            step["tree"] = True
        if given.get("unset"):
            step["unset"] = [str(v) for v in given["unset"]]
        if given.get("path"):
            step["path"] = [str(v) for v in given["path"]]
        if given.get("tree") and isinstance(given.get("idle"), dict) and given["idle"].get("seconds"):
            step["idle"] = dict(given["idle"])
    state: dict[str, Any] = {"schema": files.JOB_SCHEMA, "job": name, "pid": os.getpid(), "started": clock(), "finished": None,
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
                             env=_env(step), start_new_session=(os.name == "posix"))
            except OSError as e:
                out.write(f"could not be started: {e.strerror or e}\n".encode("utf-8"))
                step.update(status=FAILED, finished=clock(), exit=None)
                end = FAILED
                break
            step["pid"] = proc.pid
            _write(path, state)
            how = None
            idle = _Idle(project, step, clock) if step.get("idle") else None
            while True:
                try:
                    code = proc.wait(timeout=POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    how = asked.how()
                    if not how and idle is not None and idle.due() and idle.passed(proc.pid, procs or processes):
                        # Nothing has moved for as long as the plan allows: it
                        # is ended as a stop is, and the state says why, at
                        # once, for a page drawn while it ends.
                        how = STOPPED
                        state["why"] = IDLE
                        _write(path, state)
                    if how:
                        if step.get("tree"):
                            _end_tree(proc, clock, procs or processes, kill or os.kill)
                        else:
                            _end(proc, clock)
                        code = proc.returncode
                        break
            if step.get("tree"):
                _reap(proc, kill or os.kill)
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
