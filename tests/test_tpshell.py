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
    # limit runs from its first line, so it is the wait that is measured,
    # with room for a loaded machine to ask more than three times.
    printed = time.monotonic() + 10
    while os.fstat(shell._err.fileno()).st_size == 0 and time.monotonic() < printed:
        time.sleep(0.01)
    with pytest.raises(TraceProcessorException) as e:
        shell.wait(limit=time.monotonic() - shell._started + 1.0)
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
