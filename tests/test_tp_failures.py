"""A trace_processor that fails is one sentence and exit 2.

One model for every way it fails (tpshell.Failed): a shell that does not
start (#872), and one that stops while its trace is read (#873). The error
names the trace, the binary or how the shell ended, and quotes what it
printed; no command ends in a traceback, and none writes a report that
reads as whole.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import demo, orphans, tpshell  # noqa: E402
from echolot import tp as tp_mod  # noqa: E402
from echolot.main import main as cli  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="a shell script as the binary")


@pytest.fixture(scope="module")
def app(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    demo.write(root)
    return root


def _binary(tmp_path: Path, body: str) -> str:
    path = tmp_path / "trace_processor_shell"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _args(command: str, app: Path, out: Path) -> list[str]:
    trace = str(app / "coldStart_iter000.perfetto-trace")
    config = ["-c", str(app / "echolot.yml")]
    return {
        "analyze": ["analyze", *config, "-o", str(out), trace],
        "probe": ["probe", trace],
        "names": ["names", *config, trace],
        "calibrate": ["calibrate", *config, trace],
    }[command]


@pytest.mark.parametrize("command", ["analyze", "probe", "names", "calibrate"])
def test_a_trace_processor_that_does_not_start_is_one_error_line_and_exit_2(
        app: Path, tmp_path: Path, capsys, command: str) -> None:
    binary = _binary(tmp_path, 'echo "cannot download trace_processor: no network" >&2; exit 7')
    out = tmp_path / "out"
    code = cli(["--tp-binary", binary, *_args(command, app, out)])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "Traceback" not in err, err
    said = [ln for ln in err.splitlines() if ln.startswith("error:")]
    assert len(said) == 1, err
    assert "coldStart_iter000.perfetto-trace: trace_processor did not start: " in said[0], err
    assert f"{binary} exited with code 7 before it answered." in said[0], err
    assert "cannot download trace_processor: no network" in err, err
    assert not (out / "report.json").exists()


def _kill_at(monkeypatch, at: int) -> list[int]:
    """The session's shell killed with SIGKILL as its `at`-th statement is
    sent, as the system kills one for memory. The count, as it ends."""
    asked = [0]
    real = tp_mod.TraceSession._ask

    def ask(self, sql):
        asked[0] += 1
        if asked[0] == at:
            os.kill(self._tp.shell.pid, signal.SIGKILL)
        return real(self, sql)

    monkeypatch.setattr(tp_mod.TraceSession, "_ask", ask)
    return asked


@pytest.fixture(scope="module")
def statements(app: Path, tmp_path_factory) -> int:
    """How many statements `analyze` sends for one of the demo's traces."""
    with pytest.MonkeyPatch.context() as mp:
        asked = _kill_at(mp, 0)
        out = tmp_path_factory.mktemp("whole")
        assert cli(_args("analyze", app, out)) == 0
    assert (out / "report.json").exists()
    return asked[0]


@pytest.mark.parametrize("when", ["window", "detectors"])
def test_a_trace_processor_killed_during_analyze_ends_the_run_and_says_so(
        app: Path, tmp_path: Path, capsys, monkeypatch, statements: int, when: str) -> None:
    """Killed while the window was built, it was a traceback; killed between
    two detectors, the rest of them read "Failed" in a report written whole,
    with exit 0 (#873)."""
    capsys.readouterr()
    _kill_at(monkeypatch, 3 if when == "window" else statements - statements // 4)
    out = tmp_path / "out"
    code = cli(_args("analyze", app, out))
    err = capsys.readouterr().err
    assert code == 2, err
    assert "Traceback" not in err, err
    said = [ln for ln in err.splitlines() if ln.startswith("error:")]
    assert len(said) == 1, err
    assert said[0].endswith(
        "coldStart_iter000.perfetto-trace: trace_processor stopped while it read this "
        "trace: it was killed by signal 9 (SIGKILL). It printed nothing."), err
    assert not out.exists() or not any(out.iterdir()), list(out.iterdir())


def test_a_trace_processor_killed_while_its_trace_is_fed_to_it_says_so(
        app: Path, monkeypatch) -> None:
    shells: list[tpshell.Shell] = []
    wait = tpshell.Shell.wait

    def waited(self, *a, **k):
        wait(self, *a, **k)
        shells.append(self)

    opener = orphans.open_processor

    def open_processor(binary, connect):
        def killed_first(address):
            os.kill(shells[-1].pid, signal.SIGKILL)
            return connect(address)
        return opener(binary, killed_first)

    monkeypatch.setattr(tpshell.Shell, "wait", waited)
    monkeypatch.setattr(orphans, "open_processor", open_processor)
    trace = app / "coldStart_iter001.perfetto-trace"
    with pytest.raises(tpshell.Failed) as e:
        tp_mod.TraceSession(trace)
    assert str(e.value) == (
        f"{trace.resolve()}: trace_processor stopped while it read this trace: it was "
        f"killed by signal 9 (SIGKILL). It printed nothing."), str(e.value)
    assert shells[-1].pid is None, "the session was left open"


def test_what_a_shell_printed_leaves_out_what_it_prints_as_it_serves() -> None:
    err = ("[HTTP] Starting HTTP server on 127.0.0.1:5000\n"
           "[12.3]      http_server.cc:331 [HTTP] POST /query [body=31B]\n"
           "Loading trace 12.21 MB (0.0 MB/s)\n"
           "[12.4] trace_processor_shell.cc:99 out of memory\n")
    assert tpshell.printed("", err) == "It printed:\n  [12.4] trace_processor_shell.cc:99 out of memory"
    assert tpshell.printed("", "[HTTP] Starting HTTP server on [::1]:5000\n") == "It printed nothing."
    many = "".join(f"line {i}\n" for i in range(20))
    assert tpshell.printed(many, "").startswith("The last 8 lines it printed:\n  line 12")
