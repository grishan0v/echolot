"""A trace_processor that fails is one sentence and exit 2.

One model for every way it fails (tpshell.Failed): a shell that does not
start (#872). The error names the trace, the binary and how it ended, and
quotes what it printed; no command ends in a traceback, and none writes a
report that reads as whole.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import demo  # noqa: E402
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
