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

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from echolot import selftest

ROOT = Path(__file__).resolve().parent.parent

# The prose spells small numbers out, so the patterns have to read them. The
# list ended at ten and the eleventh document went in as a claim nothing
# checked — a counter whose vocabulary runs out is a counter that stops
# counting, quietly.
WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
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
# the top of the README, the label inside the flowchart under it, and the
# sample report's own tally.
#
# That last one is the reason this list grew. The two above it were kept
# current through two detectors being added; the sample report went on saying
# "5 of 8" because no pattern looked at it. A number inside an example is a
# claim about the tool exactly like a number in a sentence — and the example is
# the part people read first.
#
# Only the second number counts. The first is how many fired on the run that
# produced the sample, which is a fact about that run and not about the tool.
DETECTOR_TALLIES = [
    ("README.md", r"runs " + NUMBER + r" SQL detectors"),
    ("README.md", NUMBER + r" SQL detectors<br/>"),
    ("README.md", r"Detectors fired: \*\*\d+ of " + NUMBER + r"\*\*"),
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


def _tag_check_program() -> str:
    """The python the publish workflow runs to compare the tag and the version.

    Read out of the workflow rather than copied here. A copy would pass while
    the workflow it describes was failing, which is the whole failure this
    test exists to end.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    named = [s for s in steps if "tag matches" in (s.get("name") or "")]
    assert len(named) == 1, (
        f"publish.yml has {len(named)} steps checking the tag against the "
        f"version; this test knows how to read exactly one"
    )
    body = re.search(r"<<'PY'\n(.*?)\n *PY\s*$", named[0]["run"], re.S)
    assert body, "the tag check is no longer a `python - <<'PY'` heredoc"
    return textwrap.dedent(body.group(1))


def _run_tag_check(tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-"], input=_tag_check_program(), cwd=ROOT,
        env={**os.environ, "GITHUB_REF_NAME": tag},
        capture_output=True, text=True,
    )


def test_the_publish_workflow_can_read_the_version():
    """The release gate, run here instead of on a tag that cannot be taken back.

    Nothing exercises a workflow until a tag is pushed, and a tag is
    irreversible. That gap swallowed a whole release: #33 moved the version
    out of `pyproject.toml` into the package, and the publish workflow went on
    asking `tomllib` for `project.version`. The key no longer existed, the
    step raised KeyError, `build` failed, and `publish` and `release` never
    ran — so a tag would have created nothing at all.

    The two halves are held apart by the test above, which requires the
    version to be dynamic. So this one runs the workflow's own program against
    the real checkout: the same source, the same tag, the same exit code.
    """
    import echolot

    good = _run_tag_check(f"v{echolot.__version__}")
    assert good.returncode == 0, (
        f"the publish workflow cannot read the version it is about to "
        f"release:\n{good.stdout}{good.stderr}"
    )

    # And it has to be a check rather than a formality: a tag naming another
    # version must stop the release.
    bad = _run_tag_check("v0.0.0-not-this-one")
    assert bad.returncode != 0, (
        "the publish workflow accepted a tag that does not match the version"
    )
