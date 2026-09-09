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
from hypothesis import settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, selftest  # noqa: E402

# Property-based tests (test_*_hypothesis.py) generate their own input, and a
# generated-but-random input in CI is the one kind of flake this project
# cannot have: the whole premise is that the same input yields the same
# answer, run after run. `derandomize=True` makes hypothesis pick its
# examples from a fixed hash of the test itself rather than from an
# actually-random seed, and `database=None` stops it from remembering
# machine-local failures between runs — so a red run is reproducible from the
# diff alone, on any machine, without a shared example database.
#
# The profile is loaded unconditionally rather than only under CI, which is
# what "everywhere" names: a laptop that explored different examples than the
# runner would report a different answer for the same commit, and finding out
# which examples ran would mean reading a seed out of a log.
#
# What the pin costs is worth writing down next to it. A fixed set of
# examples is a fixed set of blind spots: a property that is false for one
# input in a thousand passes, quietly and repeatably, until a hypothesis
# upgrade reshuffles the choice and turns CI red on a commit that changed
# nothing. That is not a reason to unpin it — it is the reason each test in
# test_*_hypothesis.py carries `@example` cases for the inputs it actually
# exists to check, so the case that matters is never left to the seed.
settings.register_profile("echolot", derandomize=True, database=None,
                           deadline=None, print_blob=True)
settings.load_profile("echolot")


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
