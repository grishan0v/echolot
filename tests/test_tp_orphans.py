"""However echolot ends, the trace_processor it started ends with it (#570).

perfetto starts trace_processor_shell, a server with no idle limit, in a
session of its own and stops it only in `close()`. SIGTERM, SIGHUP, SIGKILL,
and Ctrl-C while the shell started or read its trace each left one running,
re-parented to init, for good.

Every child here is looked up by pid and waited for with a deadline, never a
fixed sleep, so the tests hold under `pytest -n auto`; a test that fails kills
what it found before it reports.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import fixture, orphans  # noqa: E402
from echolot.tp import TraceSession  # noqa: E402
from tests.support import check  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX signals")

# A session opened, its child's pid printed, then a wait to be ended in.
HOLDER = """
import sys, time
from echolot import orphans
from echolot.tp import TraceSession
with TraceSession(sys.argv[1]) as tp:
    print(orphans.pid_of(tp._tp), flush=True)
    time.sleep(120)
"""


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("orphans") / "fx.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def _alive(pid: int) -> bool:
    """Running, as opposed to gone or a zombie waiting to be reaped."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _kill_left(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _my_live_children() -> list[int]:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat=,command="],
                         capture_output=True, text=True).stdout
    me = os.getpid()
    found = []
    for line in out.splitlines():
        f = line.split(None, 3)
        if (len(f) == 4 and int(f[1]) == me and not f[2].startswith("Z")
                and "trace_processor" in f[3]):
            found.append(int(f[0]))
    return found


@pytest.mark.parametrize("signame, to_group", [
    ("SIGTERM", False), ("SIGTERM", True), ("SIGHUP", False), ("SIGHUP", True),
    ("SIGKILL", False),
])
def test_a_signal_ends_the_child_too(trace: Path, signame: str, to_group: bool) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(trace)], stdout=subprocess.PIPE,
        text=True, cwd=ROOT, start_new_session=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT), ECHOLOT_NO_RECORD="1"))
    child = None
    try:
        line = holder.stdout.readline().strip()
        check("the session opened", line.isdigit(), line)
        child = int(line)
        check("its child is running", _alive(child))
        sig = getattr(signal, signame)
        if to_group:
            os.killpg(holder.pid, sig)
        else:
            holder.send_signal(sig)
        code = holder.wait(timeout=30)
        if sig != signal.SIGKILL:
            check("echolot exits with the signal's code", code == 128 + sig, code)
        check("and the child is gone", _gone_within(child, 10), child)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        if child:
            _kill_left(child)


def test_ctrl_c_while_the_shell_starts(trace: Path, monkeypatch) -> None:
    """Inside perfetto's wait for the shell to answer: the child is a local
    of that call, and the interrupt used to leave it running."""
    from perfetto.trace_processor import shell

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(shell.request, "urlretrieve", interrupted)
    before = set(_my_live_children())
    with pytest.raises(KeyboardInterrupt):
        TraceSession(trace)
    left = set(_my_live_children()) - before
    for pid in left:
        _kill_left(pid)
    check("no trace_processor left", not left, left)


def test_ctrl_c_while_the_trace_is_read(trace: Path, monkeypatch) -> None:
    from perfetto.trace_processor import http

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(http.TraceProcessorHttp, "parse", interrupted)
    before = set(_my_live_children())
    with pytest.raises(KeyboardInterrupt):
        TraceSession(trace)
    left = set(_my_live_children()) - before
    for pid in left:
        _kill_left(pid)
    check("no trace_processor left", not left, left)
    check("and the signals are as they were",
          signal.getsignal(signal.SIGTERM) == signal.SIG_DFL)


def test_signals_are_held_only_while_a_session_is_open(trace: Path) -> None:
    if signal.getsignal(signal.SIGTERM) != signal.SIG_DFL:
        pytest.skip("SIGTERM already has a handler here")
    with TraceSession(trace) as outer:
        check("held while open", signal.getsignal(signal.SIGTERM) is orphans._as_exit)
        with TraceSession(trace):
            pass
        check("still held while the outer one is open",
              signal.getsignal(signal.SIGTERM) is orphans._as_exit)
        check("the session answers", outer.query("SELECT 1 AS one") == [{"one": 1}])
    check("let go after", signal.getsignal(signal.SIGTERM) == signal.SIG_DFL)
    check("and SIGHUP too", signal.getsignal(signal.SIGHUP) == signal.SIG_DFL)
