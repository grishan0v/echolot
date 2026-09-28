#!/usr/bin/env python3
"""The last places where the tool said more than it did, before 0.8.0.

- `init` closed on "the self-check failed (see above)", and `next` said "the
  last self-check failed", for a self-check that never ran as well. #118
  logs one as `checks: 0` beside one entry in `failed`, and since #126
  `echolot`'s doctor line says it did not run.

Wherever a download is attempted here, HOME is an empty directory and PATH
holds a fake `curl` and nothing else, so the real one cannot run.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from echolot import recorder, selftest, state, tp
from echolot.main import ASSERTS_SKIPPED, NOT_RUN, main
from tests.support import check

ROOT = Path(__file__).resolve().parent.parent
PIN = tp.pinned_build()
needs_a_pin = pytest.mark.skipif(PIN is None, reason="the pin has no build for this machine")


def run(*argv: str) -> tuple[int, str, str]:
    """A command, with what it printed on each stream."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(monkeypatch, tmp_path) -> Path:
    """An empty project directory to run from, and the recorder put back after.

    `main` points the recorder at the project it runs in, and that global
    would otherwise outlive the test.
    """
    root = tmp_path / "app"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(recorder, "_root", None)
    return root


def fake_curl(bin_dir: Path, body: str) -> str:
    """A PATH with one program on it: a `curl` that does `body`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    curl.chmod(0o755)
    return str(bin_dir)


# --- `next`: a self-check that never ran did not fail -----------------------

def _next_after(root: Path, facts: dict) -> str:
    """The `next` step over a run log whose last doctor noted `facts`."""
    log = root / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "ts": "2026-09-28T10:00:00+00:00", "cmd": "doctor",
        "argv": ["doctor", "-q"], "exit": 1, "facts": facts,
    }) + "\n", encoding="utf-8")
    st = state.project_state(root)
    # An absent layer outranks a failed doctor in `next_kind`, and the layer
    # is not what this is about.
    st["layer_verdict"] = "current"
    check("routed back to doctor", state.next_kind(st) == "doctor", state.next_kind(st))
    return state.next_step(st)


@pytest.mark.parametrize("facts,said,unsaid", [
    ({"checks": 0, "failed": [NOT_RUN]}, "the last self-check did not run", "failed"),
    ({"checks": 143, "failed": ["a check"]}, "the last self-check failed", "did not run"),
], ids=["did-not-run", "failed"])
def test_next_says_whether_the_last_self_check_ran(project, facts, said, unsaid):
    """`checks: 0` and `NOT_RUN` is what `_record_not_run` writes."""
    step = _next_after(project, facts)
    check("the step is still doctor", step.startswith("echolot doctor —"), step)
    check(f"it says {said!r}", said in step, step)
    check(f"and not {unsaid!r}", unsaid not in step, step)


# --- init's closing line: the same words ------------------------------------

def _next_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith("next")), "")


def _init(*flags: str) -> tuple[int, str, str]:
    """`echolot init` into the project, with its closing check."""
    code, out, err = run("init", "--no-input", "--for", "claude", *flags)
    return code, _next_line(out), out + err


def _did_not_run(line: str) -> None:
    check("the next line says the self-check did not run",
          "the self-check did not run" in line, line)
    check("and not that it failed", "failed" not in line, line)


@needs_a_pin
def test_init_without_trace_processor_says_the_self_check_did_not_run(
        project, monkeypatch, tmp_path):
    """The pin is not here and cannot be downloaded: nothing was checked."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", fake_curl(tmp_path / "bin", "exit 6\n"))
    monkeypatch.setattr(tp, "_FAILED", {})
    code, line, said = _init()
    check("exit 2", code == 2, f"exit {code}\n{said[-2000:]}")
    check("with the download's error", "could not be downloaded" in said, said[-2000:])
    _did_not_run(line)


def test_init_on_a_binary_that_is_not_there_says_the_self_check_did_not_run(
        project, tmp_path):
    """`--tp-binary` pointing at nothing: the self-check could not start."""
    code, line, said = _init("--tp-binary", str(tmp_path / "nowhere" / "trace_processor_shell"))
    check("exit 1", code == 1, f"exit {code}\n{said[-2000:]}")
    check("with why", "self-check: could not run" in said, said[-2000:])
    _did_not_run(line)


def test_init_under_python_O_says_the_self_check_did_not_run(tmp_path):
    """Refused before anything is checked. A subprocess: `-O` belongs to the
    interpreter, and only a fresh one can have it."""
    done = subprocess.run([sys.executable, "-O", "-m", "echolot.main", "init",
                           "--no-input", "--for", "claude"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, PYTHONPATH=str(ROOT),
                                   ECHOLOT_NO_RECORD="1"))
    check("exit 1", done.returncode == 1, done.stdout + done.stderr)
    check("the refusal is said", ASSERTS_SKIPPED in done.stdout.splitlines(), done.stdout)
    _did_not_run(_next_line(done.stdout))


def test_init_keeps_failed_for_a_self_check_that_ran_and_failed(project, monkeypatch):
    def a_mismatch(tp_binary=None):
        return [("a check that holds", None), ("a check that moved", "5 != 6")]

    monkeypatch.setattr(selftest, "run", a_mismatch)
    code, line, said = _init()
    check("exit 1", code == 1, f"exit {code}\n{said[-2000:]}")
    check("the next line says it failed", "the self-check failed" in line, line)
    check("and not that it did not run", "did not run" not in line, line)
