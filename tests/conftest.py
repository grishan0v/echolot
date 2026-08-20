"""Shared fixtures, and the two things every test here needs.

Before this, each test file carried its own collector, its own runner and its
own summary line, and CI ran three of them as three steps with three output
formats. None of that was about echolot; it was about having nowhere else to
put it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, selftest  # noqa: E402


@pytest.fixture(autouse=True)
def no_record(monkeypatch):
    """Tests must not append to whatever run log they happen to run beside."""
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture(autouse=True)
def keep_cwd():
    """Several cases chdir into a temp project, and one of them may fail there.

    The old runners restored the directory in a `finally` around every case,
    because a test that dies mid-chdir takes the rest of the run with it.
    """
    here = Path.cwd()
    yield
    os.chdir(here)


@pytest.fixture(scope="session")
def marker_report():
    """The fixture trace, analysed once for the whole session.

    Building the trace is instant; running every detector over it through
    trace_processor is not, and every check in echolot/selftest.py reads the
    same report. `doctor` pays this once too.
    """
    with recorder.isolated():
        return selftest.build_report()
