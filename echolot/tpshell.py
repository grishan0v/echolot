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
[::1] alone, and the client reaches whoever holds 127.0.0.1: another
session's shell, which is then fed this session's trace and runs its SQL.
Within a process that was closed by #487. Between processes it was not, and
on Linux, where the kernel hands a released port out again at random, test
workers starting shells side by side drew one port often enough to fail CI
in three runs of a hundred and twenty-four: `view _proc already exists`, `no
such table: process`, a connection reset (#807).

So a port is held from its draw until its shell listens on it, in two ways:
within this process by `_handed`, and between processes by a lease — a lock
on the port's byte of one file in the temporary directory, taken while the
socket that drew the port still holds it. A process that draws a port
another has leased draws again. Once its shell listens, the kernel keeps it.

And before anything is sent to the shell that answers, the shell this
session started says whether it is the one: it prints on stderr that its
port on 127.0.0.1 was taken, or goes on to [::1]. One whose port was taken
is stopped, and the session starts another on another port (`Taken`). That
covers what a lease cannot: an echolot from before it, or any other program
that drew the port in between.

A shell that does not come up is `Failed`, in words that say which binary,
how it ended — its exit code, or that it did not answer in time — and what
it printed (#872). So is one that stops while its trace is read, killed by
the system for memory as a rule, now that a set's traces are read side by
side (#873): how it ended and what it printed. Not an `Exception`, for the
reason `orphans.Stopped` is not one: every detector's query is read under
an `except Exception`, and a trace_processor that is gone is not one
query's failure. It read as the failure of every check after it, in a
report that exited 0. `Failed` ends the command with one `error:` line and
exit 2, whatever was reading, and nothing written that reads as whole.
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import os
import shutil
import signal
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
# How long a shell that answered has to say whether the answer was its own,
# and how many shells a session starts before it stops looking for a port.
SETTLE = 1.0
TRIES = 3

# What trace_processor prints on stderr as its server starts, v52 to v59:
#   [HTTP] Starting HTTP server on 127.0.0.1:<port>
#   Failed to listen on IPv4 socket: "127.0.0.1:<port>" (… Address already in use)
#   [HTTP] Starting HTTP server on [::1]:<port>
# The second line only when another process holds the port; the third once
# the first is behind it, either way.
TAKEN = "Failed to listen on IPv4"

_lock = threading.Lock()
_handed: set[int] = set()   # ports given to a shell that does not listen yet
_leases: int | None = None  # the lease file, once opened; -1 where it cannot be


class Taken(Exception):
    """The shell could not listen on its port on 127.0.0.1 — another process
    holds it, as a rule — so whatever answers there is not this shell. The
    shell is stopped, and nothing was sent to what answered."""


class Failed(BaseException):
    """trace_processor did not start, or cannot be read from: the command
    cannot go on, and says so in one sentence.

    Raised here without the trace, which `TraceSession` adds. `main` prints
    it as `error:` and exits 2, as every refusal reads.

    `again` is set for a socket error while no shell has stopped — what the
    error was — and such a failure is tried once more before it is said
    (#871).
    """

    def __init__(self, said: str, *, again: str | None = None):
        super().__init__(said)
        self.again = again


# How long to wait before a socket error is tried again.
PAUSE = 0.25


# What trace_processor prints as it serves — a line for each request its
# server takes, and how far the load is — which says nothing about why it
# stopped: left out of what it printed.
_SERVING = ("[HTTP]", "Loading trace ")
# How many of the last lines it printed an error quotes.
_LINES = 8


def _lease_file() -> int:
    """The file whose byte N is the lease on port N, opened once a process.

    One a user, so that a file another user made in a shared /tmp is not
    this one's to open. -1 where it cannot be opened, and then a port is
    held within this process alone, as before #807.
    """
    global _leases
    if _leases is None:
        uid = os.getuid() if hasattr(os, "getuid") else None
        name = "echolot-ports" if uid is None else f"echolot-ports-{uid}"
        try:
            _leases = os.open(os.path.join(tempfile.gettempdir(), name),
                              os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            _leases = -1
    return _leases


def _lease(port: int, take: bool = True) -> bool:
    """The lease on `port` taken (or let go), with `_lock` held; False when
    another process holds it.

    A byte-range lock, which the kernel drops when its process ends however
    it ends, so no lease outlives its echolot. Within a process it does not
    exclude (on POSIX a process does not conflict with itself): `_handed`
    does that. A lock refused for any reason but another's lease leaves the
    port unleased, as before #807.
    """
    fd = _lease_file()
    if fd < 0:
        return True
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, port, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.lockf(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take
                        else fcntl.LOCK_UN, 1, port)
    except OSError as e:
        return not take or e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
    return True


def _free_port() -> int:
    """A port no other shell is about to listen on: none of this process's
    (`_handed`) and none of another echolot's (its lease).

    Bound, leased while it is still bound — so that no echolot can draw it
    in between — and let go. A sandbox with no network refuses the bind with
    EPERM before anything starts, and that is the refusal sandbox.py names
    (#190). On 127.0.0.1, where the shell listens: a sandbox that lets a
    command bind localhost and nothing else lets this through as it lets the
    shell.
    """
    with _lock:
        for _ in range(64):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
                if port in _handed or not _lease(port):
                    continue
            _handed.add(port)
            return port
    raise OSError(errno.EADDRINUSE, "no free port on 127.0.0.1")


def _release(port: int) -> None:
    """The port handed back, and its lease: its shell listens on it now, or
    nobody will."""
    with _lock:
        if port in _handed:
            _handed.discard(port)
            _lease(port, take=False)


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


def printed(out: str, err: str) -> str:
    """What a shell printed, as a sentence an error ends with: its last
    lines, stderr's then stdout's, without what it prints as it serves."""
    lines = [ln.strip() for text in (err, out) for ln in text.splitlines()]
    lines = [ln[:300] for ln in lines
             if ln and not ln.startswith(_SERVING[1]) and _SERVING[0] not in ln]
    if not lines:
        return "It printed nothing."
    shown = lines[-_LINES:]
    head = ("It printed:" if len(shown) == len(lines)
            else f"The last {len(shown)} lines it printed:")
    return head + "".join(f"\n  {ln}" for ln in shown)


# The errors a session's connection to its shell ends in: a socket's, and
# http.client's for an answer cut off (IncompleteRead, RemoteDisconnected).
CONNECTION = (OSError, http.client.HTTPException)


def lost(shell: Shell, error: BaseException) -> Failed:
    """What a connection error from a session's shell means, as `Failed`:
    the shell stopped — how, and what it printed — or it runs and could not
    be reached.

    Asked before the shell is closed, which would stop it either way, and
    given a moment to end: a shell killed from outside drops its socket a
    little before it can be reaped.
    """
    how = shell.ended(within=0.25)
    if how is not None:
        return Failed(f"trace_processor stopped while it read this trace: it "
                      f"{how}. {shell.last_words()}")
    why = str(error) or type(error).__name__
    return Failed(f"could not reach trace_processor: {why}", again=why)


def gave_up(failed: Failed) -> str:
    """A socket error that came back when it was tried again, said."""
    return (f"could not reach trace_processor, tried twice: {failed.again}. "
            f"The machine's sockets failed, not the trace: run again, with "
            f"fewer traces at once (-j 2) if many are read side by side.")


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
        self.binary = binary
        try:
            self.port = _free_port()
        except OSError as e:
            # A socket bound to draw the port, which a machine short of
            # sockets refuses — ENOBUFS, with many shells at once (#871).
            # A sandbox's refusal is its own, said by sandbox.py (#190).
            if e.errno in (errno.EPERM, errno.EACCES):
                raise
            raise Failed(f"could not reach trace_processor: no port for it on "
                         f"127.0.0.1: {e}", again=str(e)) from e
        self._held = True
        self.asked = 0
        self.waited: float | None = None
        # How it ended and what it printed, once it ended on its own: kept
        # when it is closed, for the error that names it.
        self.exit_code: int | None = None
        self._last_words: str | None = None
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
        """Until the shell answers and the answer is its own, asking every
        `EVERY` seconds.

        A shell that ends first, or does not answer within `limit`, is
        `Failed`: which binary, its exit code or that it did not answer in
        time, and what it printed. A shell that says its port was another's
        is stopped, and `Taken`: what answered was the other one.

        Its own answer comes after it has said where it listens — it prints
        that before it serves — so it is taken at once. An answer before the
        shell has said is another's, from a shell that has not tried the port
        yet; it is given `SETTLE` to say so. A trace_processor that says
        neither, in words other than these, is taken at its answer as it was
        before #807, a second late.
        """
        deadline = self._started + limit
        answered: float | None = None
        try:
            while (proc := self._proc) is not None and proc.poll() is None:
                self.asked += 1
                if answered is None and _answers(self.port):
                    answered = time.monotonic()
                says = self.listens()
                if says is False:
                    break
                now = time.monotonic()
                if answered is not None and (says or now - answered >= SETTLE):
                    self.waited = now - self._started
                    return
                if now >= deadline:
                    break
                time.sleep(EVERY)
        finally:
            self._let_go()
        how = self.ended()
        said = self._said()
        self.close()
        if TAKEN in said[1]:
            raise Taken(
                f"Trace processor could not listen on {self.address}, so what "
                f"answers there is not this one.\nstderr: {said[1]}\n")
        if how is None:
            how = (f"did not answer within {limit:g} s, and was stopped")
        else:
            how += " before it answered"
        raise Failed(f"trace_processor did not start: {self.binary} {how}. "
                     f"{printed(*said)}")

    def ended(self, within: float = 0.0) -> str | None:
        """How the shell ended, in words — "exited with code 1", "was killed
        by signal 9 (SIGKILL)" — once it ended on its own; None while it runs,
        and once echolot stopped it. Waits up to `within` seconds for it to
        end: a shell killed from outside drops its sockets a moment before it
        can be reaped."""
        code = self.exit_code
        proc = self._proc
        if code is None and proc is not None:
            try:
                code = proc.wait(within) if within > 0 else proc.poll()
            except subprocess.TimeoutExpired:
                code = None
        if code is None:
            return None
        if code < 0:
            try:
                name = f" ({signal.Signals(-code).name})"
            except ValueError:
                name = ""
            return f"was killed by signal {-code}{name}"
        return f"exited with code {code}"

    def last_words(self) -> str:
        """What the shell printed, as `printed` says it: kept from the moment
        it was closed, or read now while it is open."""
        if self._last_words is not None:
            return self._last_words
        return printed(*self._output())

    def listens(self) -> bool | None:
        """Whether the shell says it listens on 127.0.0.1 at its port: False
        when it says another process holds the port, None until it has
        said either."""
        heard = self._heard()
        if TAKEN in heard:
            return False
        if f"[::1]:{self.port}" in heard:
            return True
        return None

    def _heard(self) -> str:
        """What the shell has printed on stderr so far.

        The file is the shell's stderr, and the offset it writes at is the
        one this side would move: read with pread, which leaves it alone.
        Windows has none, and there it is moved back to the end, where the
        next line goes.
        """
        f = self._err
        try:
            fd = f.fileno()
            size = os.fstat(fd).st_size
            if hasattr(os, "pread"):
                data = os.pread(fd, size, 0)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                data = os.read(fd, size)
                os.lseek(fd, 0, os.SEEK_END)
        except (AttributeError, OSError, ValueError):
            return ""
        return data.decode("utf-8", "replace")

    def _let_go(self) -> None:
        """The port and its lease handed back, once: from here the shell
        holds the port, or it is stopped."""
        held, self._held = self._held, False
        if held:
            _release(self.port)

    def _said(self) -> tuple[str, str]:
        """What the shell printed, once it has stopped printing."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            _, terminate = _process_tree()
            with contextlib.suppress(Exception):
                terminate(proc, self._job)
        return self._output()

    def _output(self) -> tuple[str, str]:
        """What the shell has printed so far, on stdout and on stderr."""
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
            code = proc.poll()
            if code is not None and self.exit_code is None:
                self.exit_code = code
                self._last_words = printed(*self._output())
            with contextlib.suppress(Exception):
                _, terminate = _process_tree()
                terminate(proc, job)
        for f in (self._out, self._err):
            if f is not None:
                with contextlib.suppress(Exception):
                    f.close()
        self._let_go()


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

    def reconnect(self) -> None:
        """The client's connection to the shell dropped: the next statement
        opens another (http.client does, on a connection closed)."""
        conn = getattr(getattr(self.client, "http", None), "conn", None)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    def close(self) -> None:
        self.shell.close()
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.close()
