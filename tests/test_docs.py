#!/usr/bin/env python3
"""The numbers the documentation states about the tool, checked against it.

Prose goes stale silently. Over one afternoon the self-check tally in the
README read 46, then 49, then 51, then 53, then 65, while `doctor` answered
something else each time and every build stayed green — the count is written in
four places and grows whenever a check is added, which is the point of adding
one. The detector total drifted the same way: a detector landed, the sentence
above the diagram was updated, the label inside the diagram was not.

Neither number is hard to keep current. What was missing is anything that
notices, and the same argument the workflow makes about the Python range
applies here: claiming a number without running it is how the claim goes stale.

So the counts are read out of the documents and compared against the things
they describe. `selftest.CHECKS` is what `doctor` counts; the detector files
are what the pipeline runs. A claim in a shape none of these patterns match is
not checked, which is why a failure prints the pattern that found nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from echolot import selftest

ROOT = Path(__file__).resolve().parent.parent

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _numbers(document: str, pattern: str) -> list[int]:
    """Every number a pattern finds, digits or spelled out."""
    found = []
    for match in re.finditer(pattern, (ROOT / document).read_text(encoding="utf-8")):
        for group in match.groups():
            found.append(int(group) if group.isdigit() else WORDS[group.lower()])
    return found


NUMBER = r"(\d+|" + "|".join(WORDS) + r")"

# Every place a document states how many checks `doctor` runs. The last is the
# sample output in determinism.md, which prints the tally twice on one line.
CHECK_TALLIES = [
    ("README.md", NUMBER + r" checks inside"),
    ("docs/determinism.md", r"All " + NUMBER + r" checks passed"),
    ("docs/determinism.md", r"self-check: " + NUMBER + r" of " + NUMBER + r" passed"),
]

# Every place a document states how many detectors there are: the sentence at
# the top of the README, and the label inside the flowchart under it.
DETECTOR_TALLIES = [
    ("README.md", r"runs " + NUMBER + r" SQL detectors"),
    ("README.md", NUMBER + r" SQL detectors<br/>"),
]


@pytest.mark.parametrize("document,pattern", CHECK_TALLIES, ids=lambda v: v[:34])
def test_documented_check_count_is_current(document, pattern):
    stated = _numbers(document, pattern)
    assert stated, f"{document}: nothing matched /{pattern}/ — the claim moved or went away"
    assert set(stated) == {len(selftest.CHECKS)}, (
        f"{document} says {sorted(set(stated))} checks, doctor runs "
        f"{len(selftest.CHECKS)}"
    )


@pytest.mark.parametrize("document,pattern", DETECTOR_TALLIES, ids=lambda v: v[:34])
def test_documented_detector_count_is_current(document, pattern):
    shipped = len(list((ROOT / "echolot/sql/detectors").glob("*.sql")))
    stated = _numbers(document, pattern)
    assert stated, f"{document}: nothing matched /{pattern}/ — the claim moved or went away"
    assert set(stated) == {shipped}, (
        f"{document} says {sorted(set(stated))} detectors, {shipped} are shipped"
    )
