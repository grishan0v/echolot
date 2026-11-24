"""The lines `status` and `hunt` offer to run by hand, run as they are (#719).

They said `-c echolot.yml` and `.echolot/traces/...` whatever config was
given and wherever the command ran, so with `perf.yml`, or from a subfolder
with `-c ../echolot.yml`, the line pasted back stopped on "config not found".
Each case here takes the line from the output and runs it from the same
folder.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, state  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = {
    "project": {"package": "com.example.app", "process": "com.example.app"},
    "scenario": {"name": "first_frame", "start": {"name": "AppStart"},
                 "end": {"name": "Screen.firstFrame"}},
    "runner": {"mode": "launch"},
}


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture(scope="module")
def trace_bytes() -> bytes:
    return fixture.build()


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def project(root: Path, trace_bytes: bytes, name: str = "echolot.yml") -> Path:
    """A project set up for the plugin, with two repeats of its scenario."""
    root.mkdir(parents=True)
    (root / name).write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    code, out, err = run("init", "--into", str(root), "--for", "plugin", "--no-input",
                         "--no-doctor")
    assert code == 0, out + err
    traces = root / ".echolot" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (traces / f"first_frame_iter{i:03d}.perfetto-trace").write_bytes(trace_bytes)
    return root


def said(out: str, key: str) -> str:
    found = re.search(rf"^{key} +(.*)$", out, re.M)
    assert found, out
    return found.group(1)


def by_hand(line: str) -> str:
    found = re.search(r"or by hand: (echolot .*)$", line)
    assert found, line
    return found.group(1)


def runs(line: str) -> None:
    argv = shlex.split(line)
    assert argv[0] == "echolot", line
    code, out, err = run(*argv[1:], "-o", "out-of-the-line")
    check(f"`{line}` runs from here", code == 0, (out + err)[-600:])


def test_a_config_of_another_name_is_named(tmp_path: Path, trace_bytes: bytes,
                                           monkeypatch) -> None:
    root = project(tmp_path / "p", trace_bytes, name="perf.yml")
    monkeypatch.chdir(root)
    code, out, _ = run("status", "-c", "perf.yml")
    check("status ran", code == 0, out)
    check("the config line names the file given", said(out, "config").startswith("perf.yml · "),
          said(out, "config"))
    line = by_hand(said(out, "next"))
    check("and so does the line", "-c perf.yml" in line and "echolot.yml" not in line, line)
    runs(line)
    code, _, err = run("hunt", "-c", "perf.yml", "why slow startup")
    by = next(ln for ln in err.splitlines() if ln.startswith("By hand: "))
    check("hunt's line names it too", code == 0 and by.count("-c perf.yml") == 2
          and "echolot.yml" not in by, by)


@pytest.mark.parametrize("named", [True, False], ids=["with-c", "found-above"])
def test_from_a_subfolder_the_line_runs_from_there(tmp_path: Path, trace_bytes: bytes,
                                                   monkeypatch, named: bool) -> None:
    root = project(tmp_path / "p", trace_bytes)
    (root / "app").mkdir()
    monkeypatch.chdir(root / "app")
    code, out, _ = run("status", *(["-c", "../echolot.yml"] if named else []))
    check("status ran", code == 0, out)
    line = by_hand(said(out, "next"))
    check("paths and -c from the subfolder", "-c ../echolot.yml" in line
          and "../.echolot/traces/first_frame_iter000.perfetto-trace" in line, line)
    runs(line)


def test_the_page_s_line_is_from_the_project_s_folder(tmp_path: Path) -> None:
    """Without `here`, as the page offers it: from the project, with its name."""
    root = tmp_path / "p"
    root.mkdir()
    shown = state.shown_from(root, str((root / "perf.yml")), None)
    check("named from the project", shown == {"config": "perf.yml", "base": ""}, shown)
    elsewhere = tmp_path / "p" / "app" / "src"
    elsewhere.mkdir(parents=True)
    shown = state.shown_from(root, "echolot.yml", elsewhere)
    check("and from two folders down", shown == {"config": "../../echolot.yml",
                                                 "base": "../.."}, shown)
    line = state.analyze_line([".echolot/traces/a_iter000.perfetto-trace"], shown=shown)
    check("each path through the base", line == "echolot analyze "
          "../../.echolot/traces/a_iter000.perfetto-trace -c ../../echolot.yml", line)
