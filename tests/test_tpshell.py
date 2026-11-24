"""trace_processor_shell started by echolot and asked every 10 ms (#487).

perfetto slept a second whenever its first ask found a starting shell not
listening yet, which was every session but a process's first. These hold the
start to what replaced it: a session ready in well under a second, a shell
that ends or never answers reported with what it said and left running
nowhere, and a port handed to one shell of this process not handed to
another before that shell listens on it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import fixture, orphans, tpshell  # noqa: E402
from echolot.tp import TraceSession  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="a shell script as the binary")


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("tpshell") / "fx.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def _binary(tmp_path: Path, body: str) -> str:
    path = tmp_path / "trace_processor_shell"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_sessions_after_the_first_are_ready_in_well_under_a_second(trace: Path) -> None:
    """The first session of a process was the one perfetto did not make
    wait; each after it waited a second. Three in a row, each held to it."""
    for _ in range(3):
        with TraceSession(trace) as tp:
            shell = tp._tp.shell
            assert shell.waited is not None and shell.waited < 0.9, shell.waited
            assert tp.query("SELECT 1 AS one") == [{"one": 1}]


def test_a_shell_that_ends_before_it_answers_says_so_at_once(tmp_path: Path) -> None:
    from perfetto.trace_processor import TraceProcessorException

    binary = _binary(tmp_path, 'echo "boom: not a trace_processor" >&2; exit 3')
    started = time.monotonic()
    shell = tpshell.Shell(binary)
    pid = shell.pid
    with pytest.raises(TraceProcessorException) as e:
        shell.wait()
    assert time.monotonic() - started < 5, "waited out the limit for a shell that had ended"
    assert "Trace processor failed to start." in str(e.value)
    assert "boom: not a trace_processor" in str(e.value)
    assert shell.pid is None and not _running(pid)
    assert shell.port not in tpshell._handed


def test_a_shell_that_never_answers_is_stopped_at_the_limit(tmp_path: Path) -> None:
    from perfetto.trace_processor import TraceProcessorException

    shell = tpshell.Shell(_binary(tmp_path, "echo listening-nowhere >&2; exec sleep 60"))
    pid = shell.pid
    # A script written a moment ago takes ~0.4 s to start on macOS: the
    # limit runs from its first line, so it is the wait that is measured.
    printed = time.monotonic() + 10
    while os.fstat(shell._err.fileno()).st_size == 0 and time.monotonic() < printed:
        time.sleep(0.01)
    with pytest.raises(TraceProcessorException) as e:
        shell.wait(limit=time.monotonic() - shell._started + 0.3)
    assert "listening-nowhere" in str(e.value)
    assert shell.asked > 3, shell.asked
    assert not _running(pid), "the shell outlived the error"
    assert shell.port not in tpshell._handed


def test_a_binary_that_cannot_run_hands_back_its_port(tmp_path: Path) -> None:
    binary = tmp_path / "trace_processor_shell"
    binary.write_text("not a program\n", encoding="utf-8")   # no execute bit
    before = set(tpshell._handed)
    with pytest.raises(PermissionError) as e:
        tpshell.Shell(str(binary))
    assert e.value.filename == str(binary), "a sandbox's refusal names no file"
    assert tpshell._handed == before


def test_a_port_handed_to_a_shell_is_not_handed_again_until_it_listens(monkeypatch) -> None:
    """The kernel can name a port it was just given back — at random on
    Linux. Within a process that is a shell still starting, and a second
    shell on its port would leave one of the two sessions reading the
    other's trace."""
    names = iter([40001, 40001, 40002, 40001])

    class Socket:
        def __init__(self, *_):
            self.port = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def bind(self, address):
            self.port = next(names)

        def getsockname(self):
            return ("127.0.0.1", self.port)

    monkeypatch.setattr(tpshell.socket, "socket", Socket)
    monkeypatch.setattr(tpshell, "_handed", set())
    first = tpshell._free_port()
    second = tpshell._free_port()
    assert (first, second) == (40001, 40002)
    tpshell._release(first)
    assert tpshell._free_port() == 40001, "a port its shell listens on is the kernel's to keep"
    for port in (first, second):
        tpshell._release(port)
    assert not tpshell._handed


# A process that opens sessions one after another, each drawing its port out
# of the few from argv[1] on: Linux's random draw, squeezed until processes
# side by side draw one port all the time. A port a closed shell left in
# TIME_WAIT stays drawable, as it is to trace_processor, which binds with
# SO_REUSEADDR; one someone listens on is not. Prints, per session, what it
# read or the error it ended in.
_SIDE_BY_SIDE = r"""
import errno, json, random, socket, sys, types
from pathlib import Path
from echolot import tpshell
from echolot.tp import TraceSession

base, k, sessions = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
traces = sys.argv[4:]


class Narrow(socket.socket):
    def bind(self, address):
        if address[1]:
            return super().bind(address)
        self.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for port in random.sample(range(base, base + k), k):
            try:
                return super().bind((address[0], port))
            except OSError as e:
                if e.errno != errno.EADDRINUSE:
                    raise
        raise OSError(errno.EADDRINUSE, "all taken")


tpshell.socket = types.SimpleNamespace(
    socket=Narrow, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM)
out = []
for i in range(sessions):
    trace = traces[i % len(traces)]
    try:
        with TraceSession(Path(trace)) as tp:
            tp.query("CREATE VIEW _proc AS SELECT 1 AS one")
            out.append([trace, tp.query("SELECT COUNT(*) AS n FROM sched")[0]["n"]])
    except Exception as e:
        out.append([trace, f"{type(e).__name__}: {str(e).strip()[-160:]}"])
print(json.dumps(out))
"""


@pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")),
                    reason="the squeezed draw leans on how Linux and macOS reuse ports")
def test_processes_side_by_side_drawing_one_port_each_read_their_own_trace(tmp_path: Path) -> None:
    """Test workers starting trace_processors side by side on Linux drew one
    port now and then, and one session's SQL ran in another's shell: `view
    _proc already exists`, `no such table: process`, a connection reset — in
    three CI runs of a hundred and twenty-four (#807). Here four processes
    draw out of eight ports, where they collide on most sessions: every
    session reads its own trace. Before the lease and the shell's own word on
    its port, one in six or seven did not."""
    import json
    import random
    import subprocess

    crowded, plain = tmp_path / "crowded.perfetto-trace", tmp_path / "plain.perfetto-trace"
    crowded.write_bytes(fixture.build(crowded=True))
    plain.write_bytes(fixture.build())
    alone = {}
    for t in (crowded, plain):
        with TraceSession(t) as tp:
            alone[str(t)] = tp.query("SELECT COUNT(*) AS n FROM sched")[0]["n"]
    assert alone[str(crowded)] != alone[str(plain)], alone

    # Below the ephemeral ranges (Linux 32768+, macOS 49152+), where no
    # other program is handed a port by the kernel.
    base = random.randrange(20000, 30000, 8)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    workers = [subprocess.Popen(
        [sys.executable, "-c", _SIDE_BY_SIDE, str(base), "8", "6",
         *((str(crowded), str(plain)) if i % 2 else (str(plain), str(crowded)))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        for i in range(4)]
    read = []
    for w in workers:
        out, err = w.communicate(timeout=120)
        assert w.returncode == 0, err[-2000:]
        read += json.loads(out)
    wrong = [(Path(t).name, n) for t, n in read if n != alone[t]]
    assert len(read) == 24, read
    assert not wrong, f"{len(wrong)} of {len(read)} sessions did not read their own trace: {wrong}"


def test_the_watcher_holds_the_shell_from_its_start(trace: Path, monkeypatch) -> None:
    """Before the wait, not after it: a SIGKILL while the shell starts is
    covered too. perfetto's start told its pid only once it had answered."""
    events: list[str] = []
    monkeypatch.setattr(orphans._watcher, "hold", lambda pid: events.append("hold"))
    real_wait = tpshell.Shell.wait

    def wait(self, *a, **k):
        events.append("wait")
        return real_wait(self, *a, **k)

    monkeypatch.setattr(tpshell.Shell, "wait", wait)
    with TraceSession(trace):
        pass
    assert events == ["hold", "wait"], events
