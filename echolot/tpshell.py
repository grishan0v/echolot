"""trace_processor_shell, started here and asked every 10 ms (#487).

perfetto starts the shell itself when `TraceProcessor` is handed a trace and
no address, and waits for it in `shell.load_shell`: it asks the shell's
/status, and when nothing listens yet it sleeps a whole second before asking
again. A shell listens 20–30 ms after it starts, so the first ask finds
nothing whenever it is quick — and only the first ask in a process is slow
enough not to, while urllib sets itself up (its openers, the system's
proxies; some 15 ms). Every session but a process's first paid a second for
a 30 ms start: `doctor` spent 34 of its 40 s waiting, and `analyze -j 1` a
second per trace after the first.

So the shell is started here, as perfetto starts it — `-D --http-port <port>
--no-ftrace-raw`, in a session of its own (a job of its own on Windows), its
output kept for the error when it does not come up — and asked every 10 ms.
The client is perfetto's own, given the address: the same binary reads the
same trace over the same HTTP, and only the wait is shorter.

The port is asked of the kernel and let go before the shell takes it, as
perfetto does: trace_processor cannot be handed a socket. A shell whose port
was taken in between does not stop. It says so on stderr and listens on
[::1] alone, and the client reaches whoever holds 127.0.0.1 — with the
traces of a set read side by side (#488), another session of the same run.
So within this process a port handed to a shell is not handed out again
until that shell listens on it, and from then on the kernel keeps it.
Between processes it is as it was: on macOS a released port comes round
again only after the other sixteen thousand, on Linux at random.
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

# How often a starting shell is asked whether it listens, and for how long.
# The limit is perfetto's `load_timeout`.
EVERY = 0.01
LIMIT = 30.0

_lock = threading.Lock()
_handed: set[int] = set()   # ports given to a shell that does not listen yet


def _free_port() -> int:
    """A port no shell of this process is about to listen on.

    Bound and let go. A sandbox with no network refuses the bind with EPERM
    before anything starts, and that is the refusal sandbox.py names (#190).
    On 127.0.0.1, where the shell listens: a sandbox that lets a command bind
    localhost and nothing else lets this through as it lets the shell.
    """
    with _lock:
        for _ in range(64):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            if port not in _handed:
                _handed.add(port)
                return port
    raise OSError(errno.EADDRINUSE, "no free port on 127.0.0.1")


def _release(port: int) -> None:
    with _lock:
        _handed.discard(port)


def _answers(port: int) -> bool:
    """Whether a shell listens on `port` and answers its /status.

    Through http.client, as perfetto's client talks to it, and not urllib,
    which would route a request for localhost through a proxy the
    environment names.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        conn.request("GET", "/status")
        with conn.getresponse() as r:
            r.read()
            return r.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _process_tree():
    """perfetto's own teardown of the shell, so it ends as it always did:
    the group killed on POSIX, the job closed on Windows."""
    from perfetto.trace_processor.process_tree import (
        create_kill_on_close_job,
        terminate_process_tree,
    )
    return create_kill_on_close_job, terminate_process_tree


class Shell:
    """A trace_processor_shell this process started, and its port.

    `asked` is how many times it was asked whether it listens, and `waited`
    how long from its start until it answered — what the self-check holds
    the wait to.
    """

    def __init__(self, binary: str):
        self.port = _free_port()
        self.asked = 0
        self.waited: float | None = None
        self._proc: subprocess.Popen | None = None
        self._job: Any = None
        self._out = self._err = None
        try:
            self._out = tempfile.TemporaryFile()
            self._err = tempfile.TemporaryFile()
            # What perfetto runs: a shell that is not an .exe on Windows is
            # its Python wrapper.
            exe = [binary]
            if os.name == "nt" and not binary.endswith(".exe"):
                exe = [sys.executable if not getattr(sys, "frozen", False)
                       else shutil.which("python") or "python", binary]
            self._started = time.monotonic()
            self._proc = subprocess.Popen(
                [*exe, "-D", "--http-port", str(self.port), "--no-ftrace-raw"],
                stdin=subprocess.DEVNULL, stdout=self._out, stderr=self._err,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                               if sys.platform == "win32" else 0),
                start_new_session=sys.platform != "win32")
            create_job, _ = _process_tree()
            self._job = create_job(self._proc)
        except BaseException:
            self.close()
            raise

    @property
    def pid(self) -> int | None:
        """While it runs; None once closed."""
        proc = self._proc
        return proc.pid if proc is not None else None

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def wait(self, limit: float = LIMIT) -> None:
        """Until the shell answers, asking every `EVERY` seconds.

        A shell that ends first, or does not answer within `limit`, is an
        error in perfetto's words, with what the shell printed.
        """
        from perfetto.trace_processor import TraceProcessorException

        deadline = self._started + limit
        try:
            while (proc := self._proc) is not None and proc.poll() is None:
                self.asked += 1
                if _answers(self.port):
                    self.waited = time.monotonic() - self._started
                    return
                if time.monotonic() >= deadline:
                    break
                time.sleep(EVERY)
        finally:
            _release(self.port)
        said = self._said()
        self.close()
        raise TraceProcessorException(
            f"Trace processor failed to start.\nstdout: {said[0]}\n"
            f"stderr: {said[1]}\n")

    def _said(self) -> tuple[str, str]:
        """What the shell printed, once it has stopped printing."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            _, terminate = _process_tree()
            with contextlib.suppress(Exception):
                terminate(proc, self._job)
        out = []
        for f in (self._out, self._err):
            try:
                f.seek(0)
                out.append(f.read().decode("utf-8", "replace"))
            except (AttributeError, OSError, ValueError):
                out.append("")
        return out[0], out[1]

    def close(self) -> None:
        """The shell and whatever it started, killed and reaped. Again is a
        no-op, from any thread."""
        proc, self._proc = self._proc, None
        job, self._job = self._job, None
        if proc is not None:
            with contextlib.suppress(Exception):
                _, terminate = _process_tree()
                terminate(proc, job)
        for f in (self._out, self._err):
            if f is not None:
                with contextlib.suppress(Exception):
                    f.close()
        _release(self.port)


class Processor:
    """perfetto's client, over a shell started here.

    What a session holds and an `orphans.Group` closes: `query` is the
    client's, and `close` ends the shell first, so that a query in flight on
    another thread ends at once rather than when it is answered.
    """

    def __init__(self, shell: Shell):
        self.shell = shell
        self.client: Any = None

    def query(self, sql: str):
        return self.client.query(sql)

    def close(self) -> None:
        self.shell.close()
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.close()
