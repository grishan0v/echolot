#!/usr/bin/env python3
"""The checks `echolot doctor` runs, one pytest test each.

The checks themselves stay in `echolot/selftest.py` and the trace they run on
in `echolot/fixture.py`. Those are not test scaffolding: `doctor` is a shipped
command that answers "does this machine compute traces correctly", and it has
to work on a user's laptop where pytest is not installed. This file only points
pytest at the same list, so that a developer gets one report over everything
and can run a single check by name.

    pytest -k uninstrumented          # one check, by part of its name

The Marker Report is built once for the session (see conftest.py) and every
check reads it, which is what `doctor` does too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, selftest  # noqa: E402


@pytest.mark.parametrize(
    "claim", [name for name, _ in selftest.CHECKS],
    # The check's own sentence is the test id, so a failure names the claim
    # that stopped holding rather than a number.
    ids=[name for name, _ in selftest.CHECKS],
)
def test_selfcheck(claim, marker_report):
    fn = dict(selftest.CHECKS)[claim]
    # Several checks run commands of their own — `init` into a temp directory.
    # Their notes must not land in the run log of whatever hosts them, which is
    # the same reason `doctor` wraps the whole loop.
    with recorder.isolated():
        fn(marker_report)


def test_doctor_exits_zero():
    """The command itself, not just the checks it runs.

    `doctor` is the one thing in echolot whose exit code is a contract: CI
    gates on it and the agent layer routes on it. It builds its own report, so
    this costs a second pass over the fixture — worth it for the one command
    that must never be wrong about whether it passed.
    """
    from echolot.main import main
    assert main(["doctor", "-q"]) == 0


def test_doctor_q_is_actually_quiet(capsys, tmp_path, monkeypatch):
    """`-q` exists to be short, and it had stopped being short.

    "Three lines and every failure — for a subagent, a CI step, a `| head`."
    It was printing thirty-one: several checks run real commands, `init` into
    a temp directory and `status` against a config in another, and those
    commands printed into the output of the run hosting them. The layer
    installed somewhere in /tmp, a `next` step for a project that no longer
    exists, a Cursor stub — all true about a directory nobody will ever see
    again, and all between a reader and the verdict.

    Bounded rather than pinned to a number: the three lines are a promise
    about the shape, and a failure adds two lines per failing check.
    """
    from echolot.main import main

    monkeypatch.chdir(tmp_path)
    assert main(["doctor", "-q"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) <= 4, (
        f"`doctor -q` printed {len(printed)} lines:\n  "
        + "\n  ".join(printed))
    assert printed[0].startswith("echolot "), printed
    assert printed[-1].startswith("self-check:"), printed


def test_a_failing_bare_assert_is_not_reported_as_a_pass(monkeypatch):
    """The hole this file found on its first run.

    `selftest.run` returns (name, None) for a pass and (name, message) for a
    failure, and the caller decides with `if why`. A bare `assert x in y`
    raises with no message, `str(e)` is then the empty string, and the empty
    string is falsy — so a failing check was counted as passing. `doctor`
    printed 65 of 65 while one of them had never held, from the first commit.

    Stubbed rather than run for real: the point is the reporting contract, and
    there is no reason to spend a trace_processor pass on it.
    """
    def bare(report):
        assert False            # noqa: B011 — no message, which is the point

    monkeypatch.setattr(selftest, "build_report", lambda tp_binary=None: {})
    monkeypatch.setattr(selftest, "CHECKS", [("a bare assert", bare)])
    results = selftest.run()
    assert [(name, bool(why)) for name, why in results] == [("a bare assert", True)], \
        f"a failure with no message must still read as a failure: {results}"
