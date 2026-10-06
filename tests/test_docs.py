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
    ("docs/determinism.md", NUMBER + r" checks inside"),
    ("docs/determinism.md", r"All " + NUMBER + r" checks passed"),
    ("docs/determinism.md", r"self-check: " + NUMBER + r" of " + NUMBER + r" passed"),
]

# Every place a document states how many detectors there are: the sentence at
# the top of the README and the sample report's own tally.
#
# That last one is the reason this list grew. The sentence was kept current
# through two detectors being added; the sample report went on saying "5 of 8"
# because no pattern looked at it. A number inside an example is a claim about
# the tool exactly like a number in a sentence — and the example is the part
# people read first.
#
# Only the second number counts. The first is how many fired on the run that
# produced the sample, which is a fact about that run and not about the tool.
#
# The README's pictures state the count too. They read it out of this same
# tally when docs/assets/render.py draws them, and test_readme_pictures.py
# fails when a committed picture differs from what the script draws now.
DETECTOR_TALLIES = [
    ("README.md", r"runs " + NUMBER + r" SQL detectors"),
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


def test_the_fixture_s_findings_are_the_detectors_that_fire_on_it():
    """determinism.md says how many findings a report on the fixture would
    show: every shipped detector but the ones `SILENT_ON_FIXTURE` names. It
    said eleven while twelve fired (#248)."""
    shipped = len(list((ROOT / "echolot/sql/detectors").glob("*.sql")))
    stated = _numbers("docs/determinism.md", r"show the reader " + NUMBER + r" findings")
    assert stated, "docs/determinism.md: the fixture's findings are no longer counted"
    assert set(stated) == {shipped - len(selftest.SILENT_ON_FIXTURE)}, stated


# --- the sample output, which is a claim like any other ---------------------

# The comparison table is printed in two documents, and it is the one sample
# in the set that a reader is invited to match against their own output
# column by column.
# The README shows the same rows as a picture, drawn from the table in
# docs/compare.md by docs/assets/render.py; test_readme_pictures.py holds the
# picture to that table, and this holds the table to what compare prints.
COMPARE_SAMPLES = ["docs/compare.md"]


@pytest.mark.parametrize("document", COMPARE_SAMPLES)
def test_the_sample_comparison_has_the_columns_compare_prints(document):
    """A column moved and the examples went on showing the old shape.

    Adding `Evidence` to the comparison changed every table the tool prints
    and nothing looked at the two in the documentation, which is the same
    hole the tallies above were written for: a number in an example is a
    claim about the tool, and so is a heading.

    Rendered as a table rather than fenced, so this reads the page as GitHub
    and PyPI do — and a fence would put the check back to sleep.
    """
    from echolot.compare import MOVED_COLUMNS, MOVED_HEADERS

    want = [MOVED_HEADERS[c] for c in MOVED_COLUMNS]
    text = (ROOT / document).read_text(encoding="utf-8")
    # Not inside a fence: a fenced table is source on the page, not a table.
    outside = re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)
    # A cell that IS "Detector", not a line that mentions it — the index
    # table in the README links to `docs/detectors.md` and matched the
    # looser rule.
    rows = ([cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in outside.splitlines() if line.startswith("|"))
    headers = [cells for cells in rows if "Detector" in cells]
    assert headers, (
        f"{document} has no rendered comparison table — either it moved, or "
        f"it is inside a ``` fence, where it shows as source instead"
    )
    for got in headers:
        assert got == want, f"{document} shows {got}, compare prints {want}"


# --- anchors, which a heading can stop answering ----------------------------

# The documents a link inside the repository may point into.
LINKING = ["README.md", "CONTRIBUTING.md",
           *sorted(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md"))]


def _unfenced(path: Path) -> str:
    return re.sub(r"^```.*?^```", "", path.read_text(encoding="utf-8"),
                  flags=re.S | re.M)


def _slug(heading: str) -> str:
    """A heading's anchor, made the way GitHub and the link checker make it.

    Lower case; letters, digits, `-` and `_` kept; whitespace to `-`;
    everything else dropped. The text of inline code counts, and so does the
    label of a link — the markup around them does not.
    """
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).replace("*", "")
    return "".join("-" if ch.isspace() else ch
                   for ch in text.strip().lower()
                   if ch.isalnum() or ch in "-_" or ch.isspace())


def _anchors(path: Path) -> set[str]:
    """Every anchor the headings of one document make, repeats numbered."""
    seen: dict[str, int] = {}
    out: set[str] = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", _unfenced(path), re.M):
        slug = _slug(heading)
        out.add(slug if slug not in seen else f"{slug}-{seen[slug]}")
        seen[slug] = seen.get(slug, 0) + 1
    return out


@pytest.mark.parametrize("document", LINKING)
def test_every_anchor_a_link_names_is_a_heading_there(document):
    """A link into a heading outlives the heading, and nothing says so.

    `docs/compare.md` sent readers to `../README.md#there-is-no-ci-gate-on-purpose`
    for weeks after the heading it named had been renamed. GitHub opens such
    a link at the top of the page without a word, the link checker ran with
    fragments off, and the reader lands somewhere unrelated to the sentence
    that sent them.

    Links inside the repository only, the in-page ones and the ones into
    another document here. An absolute URL is the link checker's business.
    """
    here = ROOT / document
    broken = []
    for target in re.findall(r'(?:\]\(|href=")([^)"\s]*#[^)"\s]+)', _unfenced(here)):
        if re.match(r"[a-z]+:", target):
            continue
        name, fragment = target.split("#", 1)
        page = (here.parent / name).resolve() if name else here
        if page.suffix != ".md" or not page.is_file():
            continue
        if fragment not in _anchors(page):
            broken.append(target)
    assert not broken, (
        f"{document} links to anchors no heading makes: {', '.join(broken)}")


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


# --- the trace config, written down twice -----------------------------------

# The runner builds it, and `references/collect.md` prints it verbatim so that
# a capture by hand records the same thing. Two copies of one fact, and the one
# in prose has no way of noticing when the other moves.
RECIPE = "echolot/claude/skills/echolot/references/collect.md"


def test_the_documented_capture_records_what_the_runner_records():
    """Every source the runner asks for is named in the copy-paste recipe.

    A capture by hand that leaves one out produces a trace that analyses
    cleanly and answers a narrower question than the reader thinks — a missing
    `power/cpu_frequency` costs no detector and quietly turns `compare` back
    into something that cannot tell a slower machine from a slower app. The
    difference never surfaces as an error, which is exactly the kind of drift
    this file exists for.
    """
    from echolot import runner

    text = (ROOT / RECIPE).read_text(encoding="utf-8")
    wanted = re.findall(r'ftrace_events: "([^"]+)"', runner.TRACE_CONFIG)
    wanted += runner.ENVIRONMENT_EVENTS
    wanted += re.findall(r'name: "([^"]+)"', runner.TRACE_CONFIG)
    wanted += re.findall(r'name: "([^"]+)"', runner.SYS_STATS_SOURCE)
    wanted += runner.DEFAULT_CATEGORIES

    absent = sorted({w for w in wanted if w not in text})
    assert not absent, (
        f"{RECIPE} does not mention what the runner records: "
        f"{', '.join(absent)}"
    )


def test_the_prose_names_the_platform_state_it_explains():
    """`docs/collecting.md` argues for these four events; it has to name them.

    Narrower than the recipe above on purpose: this document is prose about
    what a config must contain rather than a config to paste, and holding it
    to every line of the template would make it one.
    """
    from echolot import runner

    text = (ROOT / "docs/collecting.md").read_text(encoding="utf-8")
    absent = [e for e in runner.ENVIRONMENT_EVENTS if e not in text]
    assert not absent, (
        f"docs/collecting.md explains the platform state without naming: "
        f"{', '.join(absent)}"
    )
