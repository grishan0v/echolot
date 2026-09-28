#!/usr/bin/env python3
"""What the six changes of the 2026-09-28 audit left saying the old thing.

Each of them fixed its own ground and stopped at its edge, and past those
edges the tool went on printing what had stopped being true:

- `echolot` read a doctor that never ran its self-check — `checks: 0` and
  one entry in `failed`, as #118 records it — as "1 check(s) FAILED".
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from echolot import recorder
from echolot.main import NOT_RUN, main
from tests.support import check


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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "_root", None)
    return tmp_path


# --- status: a self-check that never ran has no tally -----------------------

def _doctor_line(root: Path, facts: dict) -> str:
    """The doctor line `echolot` prints over a run log holding one doctor."""
    log = root / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "ts": "2026-09-28T10:00:00+00:00", "cmd": "doctor",
        "argv": ["doctor", "-q"], "exit": 1, "facts": facts,
        "error": "self-check: could not run — could not download "
                 "trace_processor: offline",
    }) + "\n", encoding="utf-8")
    code, out, _ = run()
    assert code == 0, out
    return next(ln for ln in out.splitlines() if ln.startswith("doctor"))


def test_a_self_check_that_did_not_run_is_not_given_a_count(project):
    """What `_record_not_run` writes: `checks: 0` and the one `NOT_RUN` entry."""
    line = _doctor_line(project, {"checks": 0, "failed": [NOT_RUN]})
    check("says the self-check did not run", "the self-check did not run" in line, line)
    check("and points at doctor", "`echolot doctor`" in line, line)
    check("with no count of checks that never ran",
          "check(s)" not in line and "FAILED" not in line, line)
    check("and no pass", "passed" not in line, line)


@pytest.mark.parametrize("facts,said", [
    ({"checks": 143, "failed": ["a check", "another"]}, "2 check(s) FAILED"),
    ({"checks": 143, "failed": []}, "passed"),
], ids=["failed", "passed"])
def test_a_self_check_that_ran_keeps_its_tally(project, facts, said):
    line = _doctor_line(project, facts)
    check(f"the line says {said!r}", said in line, line)
    check("and not that it did not run", "did not run" not in line, line)
