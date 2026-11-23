"""Keeping trace_processor from outliving echolot (#570).

perfetto starts `trace_processor_shell -D --http-port N` in a session of its
own and stops it only in `TraceProcessor.close()`. A server with no idle
limit, it stayed behind, re-parented to init and holding the trace it had
loaded, whenever echolot ended without that call:

- SIGTERM and SIGHUP end Python where it stands, without unwinding — and
  they are what `timeout`, an agent's harness, a cancelled CI job and a
  closed terminal send. Being in a session of its own, the child never gets
  the signal sent to echolot's group either. `hold_signals` turns both into
  `SystemExit` while a session is open, so the stack unwinds through
  `TraceSession.close()`, and the exit code is the signal's.
- An interrupt inside perfetto's constructor — while the shell starts, or
  while the trace is fed to it — escaped it with the child still running:
  perfetto closes it only on its own parse error. `open_processor` stops
  whatever the constructor had started, on any exception.
- SIGKILL runs nothing in echolot at all. A watcher beside it, started once
  per process, holds the pid of every open session's child and kills those
  still held when echolot's end of its pipe closes, which the kernel does
  however echolot ends. What it cannot cover is a SIGKILL within the second
  perfetto spends starting the shell, before its pid is known here.

And one more way, since `analyze` reads the traces of a set side by side
(#488): the sessions are opened in worker threads, and none of the above
reaches a thread. Signals are Python's main thread's alone, and so is
Ctrl-C. A `Group` is what the main thread holds instead: every session its
workers open joins it, and `Group.stop` closes them all from the main
thread, and any that starts after as it starts.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading
from typing import Any, Callable, Iterable

_held = 0
_before: dict[int, Any] = {}


def _as_exit(signum, _frame):
    raise SystemExit(128 + signum)


def hold_signals() -> bool:
    """SIGTERM and SIGHUP raised as `SystemExit` until `let_go`.

    Counted, since sessions nest (`doctor` opens one inside another's
    lifetime). A handler someone else set stays theirs — `ui` has its own —
    and so does any signal outside the main thread, where none can be set.
    Returns whether there is something for `let_go` to undo.
    """
    global _held
    if threading.current_thread() is not threading.main_thread():
        return False
    if _held == 0:
        for name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            with contextlib.suppress(ValueError, OSError):
                if signal.getsignal(sig) == signal.SIG_DFL:
                    _before[sig] = signal.signal(sig, _as_exit)
    _held += 1
    return True


def let_go() -> None:
    global _held
    _held = max(_held - 1, 0)
    if _held:
        return
    for sig, handler in _before.items():
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)
    _before.clear()


def open_processor(cls, config, trace_for: Callable[[Callable[[], None]], Iterable[bytes]]):
    """`cls(trace=…, config=config)`, with nothing left running if it fails.

    The object is made first and initialised after, so that a failure part
    way through still has it to close. `trace_for` is handed what to call
    once the child is up: the trace is fed to it from a generator, and its
    first step is the first moment the child's pid can be read.
    """
    group = getattr(_joined, "group", None)
    check_joined()
    tp = cls.__new__(cls)

    def started() -> None:
        _watcher.hold(pid_of(tp))
        if group is not None:
            group.add(tp)

    try:
        tp.__init__(trace=trace_for(started), config=config)
    except BaseException as e:
        _stop(tp, e.__traceback__)
        raise
    return tp


class Stopped(BaseException):
    """The group this thread joined was stopped: its work is not wanted.

    Not an `Exception`, for the reason `KeyboardInterrupt` is not one: it is
    not a failure of whatever it interrupts. `analyze_trace` reads every
    detector under an `except Exception`, so that one broken query does not
    take the run with it, and a worker whose session was closed would have
    gone on through every detector, each failing at once.
    """


_joined = threading.local()


def check_joined() -> None:
    """`Stopped` here when this thread's group was stopped; nothing otherwise."""
    group = getattr(_joined, "group", None)
    if group is not None:
        group.check()


class Group:
    """The sessions a set of worker threads opens, to be closed together.

    A thread joins with `with group.joined():`, and each session it opens
    from then on is the group's from the moment its trace_processor is up —
    the same moment the watcher learns its pid. `stop`, called from any
    thread, closes every one of them, and a session asked for after it
    raises `Stopped` instead of starting, or as soon as its shell is up.
    Closed from outside, a worker's session raises `Stopped` at its next
    query (`check_joined`), and the worker ends instead of reading the rest
    of its trace.
    """

    def __init__(self) -> None:
        self._open: list[Any] = []
        self._lock = threading.Lock()
        self._stopped = False

    @contextlib.contextmanager
    def joined(self):
        before = getattr(_joined, "group", None)
        _joined.group = self
        try:
            yield self
        finally:
            _joined.group = before

    def check(self) -> None:
        if self._stopped:
            raise Stopped("the run was stopped")

    def add(self, tp) -> None:
        with self._lock:
            self.check()
            self._open.append(tp)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            held, self._open = self._open, []
        for tp in held:
            close(tp)


def close(tp) -> None:
    pid = pid_of(tp)
    with contextlib.suppress(Exception):
        tp.close()
    if pid:
        _watcher.release(pid)


def pid_of(tp) -> int | None:
    proc = getattr(getattr(tp, "http", None), "subprocess", None)
    return proc.pid if isinstance(proc, subprocess.Popen) else None


def _stop(tp, tb) -> None:
    """Whatever a failed constructor started.

    Closed through the object when it got as far as holding the child. Before
    that — an interrupt in the wait for the shell to answer — the child is a
    local of the interrupted call, and only the traceback's frames hold it.
    """
    close(tp)
    while tb is not None:
        for value in list(tb.tb_frame.f_locals.values()):
            with contextlib.suppress(Exception):
                for proc in (value, getattr(value, "subprocess", None)):
                    if isinstance(proc, subprocess.Popen) and proc.poll() is None:
                        _kill(proc)
        tb = tb.tb_next


def _kill(proc: subprocess.Popen) -> None:
    """The child and what it started: perfetto has it lead its own group."""
    try:
        if os.name == "posix" and os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


# The watcher's whole program. It reads `+pid` and `-pid` lines until its
# stdin ends, then kills the group of every pid still held — a process-group
# leader only, so that a pid the system has since given to someone else is
# left alone. In a session of its own, so the signals sent to echolot's group
# do not end it first; SIGINT and SIGHUP are ignored for the same reason.
_WATCH = r"""
import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
held = set()
for line in sys.stdin:
    sign, pid = line[:1], line[1:].strip()
    if pid.isdigit():
        (held.add if sign == "+" else held.discard)(int(pid))
for pid in held:
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
"""


class _Watcher:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._owner: int | None = None
        self._lock = threading.Lock()

    def hold(self, pid: int | None) -> None:
        self._say("+", pid)

    def release(self, pid: int | None) -> None:
        self._say("-", pid)

    def _say(self, sign: str, pid: int | None) -> None:
        # POSIX only: on Windows perfetto puts the child in a job that is
        # killed with its last handle, which is what this is for.
        if not pid or os.name != "posix" or not sys.executable:
            return
        with self._lock:
            try:
                # After a fork the parent's watcher is not this process's.
                if (self._proc is None or self._owner != os.getpid()
                        or self._proc.poll() is not None):
                    if sign == "-":
                        return
                    self._start()
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(f"{sign}{pid}\n".encode())
                self._proc.stdin.flush()
            except (OSError, ValueError):
                # No watcher: the sessions still close as they always did.
                pass

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _WATCH],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self._owner = os.getpid()
        atexit.register(self._end, self._proc)

    @staticmethod
    def _end(proc: subprocess.Popen) -> None:
        """At a normal exit: its stdin closed, which ends it, and reaped."""
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)


_watcher = _Watcher()
