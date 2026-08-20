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


def test_the_documentation_index_counts_the_documents_it_lists():
    """The third count, and it had been wrong for longer than the other two.

    `docs/README.md` opens by saying how many documents there are. It said
    eight with nine on disk, then nine with ten — the number is updated when
    somebody remembers, and forgetting produces a sentence nobody re-reads.
    """
    index = ROOT / "docs/README.md"
    text = index.read_text(encoding="utf-8")
    on_disk = sorted(p.name for p in (ROOT / "docs").glob("*.md")
                     if p.name != "README.md")
    # Sentence-initial, so the alternation in NUMBER has to be case-blind.
    stated = _numbers("docs/README.md", r"(?i)" + NUMBER + r" documents")
    assert stated, "docs/README.md: nothing matched /N documents/"
    assert set(stated) == {len(on_disk)}, (
        f"the index says {sorted(set(stated))} documents, {len(on_disk)} are in docs/"
    )

    # The count going stale is the visible failure. The one that costs a reader
    # something is a document written and never linked from anywhere.
    linked = set(re.findall(r"\]\((?!https?://|#)([a-z-]+\.md)", text))
    assert not set(on_disk) - linked, (
        f"in docs/ and not linked from the index: {sorted(set(on_disk) - linked)}"
    )


@pytest.mark.parametrize("document,pattern", DETECTOR_TALLIES, ids=lambda v: v[:34])
def test_documented_detector_count_is_current(document, pattern):
    shipped = len(list((ROOT / "echolot/sql/detectors").glob("*.sql")))
    stated = _numbers(document, pattern)
    assert stated, f"{document}: nothing matched /{pattern}/ — the claim moved or went away"
    assert set(stated) == {shipped}, (
        f"{document} says {sorted(set(stated))} detectors, {shipped} are shipped"
    )


# --- the version, which is also a claim about the tool ----------------------

def test_the_version_comes_from_the_code_and_only_from_there():
    """`doctor` printed 0.1.0 against a pyproject that said 0.4.0.

    `importlib.metadata` answers from whatever dist-info exists, and an
    editable install keeps the one written when it was created. That number is
    not decoration: it goes into every line of `runs.jsonl`, into the
    `.claude/` layer manifest, and into the first line `doctor` prints — so a
    run log can record a version that has not existed for three releases.

    The fix is to have one source. This holds it there: a literal `version =`
    back in `pyproject.toml` is how the two start disagreeing again.
    """
    import echolot
    from echolot import recorder

    assert recorder.version() == echolot.__version__, (
        f"recorder says {recorder.version()}, the package says "
        f"{echolot.__version__}"
    )

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text, "pyproject stopped declaring it dynamic"
    assert 'attr = "echolot.__version__"' in text, "pyproject stopped reading the package"
    # A quoted literal, which is what a static version looks like. The
    # dynamic declaration below it is `version = { attr = … }` and must not
    # match — the first draft of this check caught its own fix.
    assert not re.search(r'^version = "', text, re.M), (
        "a literal `version =` is back in pyproject.toml — that is how the two "
        "started disagreeing"
    )
