#!/usr/bin/env python3
"""The labels a pull request gets, and the sections of the notes they lead to.

A release's notes are grouped by label (.github/release.yml), and the labels
are set by a workflow (.github/workflows/labels.yml) from the type a pull
request's title opens with. Neither half runs anywhere but on GitHub: the
labelling on a pull request, and the grouping only on a release tag, which
cannot be taken back. So the program the workflow runs is read out of the
workflow and run here, against a stand-in for `gh` that answers from a
table and writes down what it was asked to change.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "labels.yml"
RELEASE = ROOT / ".github" / "release.yml"

# What the stand-in prints for the two reads, and how it writes down a change.
FAKE_GH = """\
#!{python}
import json, os, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
if "-X" in args:
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(json.dumps(args) + "\\n")
elif args[-1] == ".[].name":
    print("\\n".join(state["labels"]))
elif args[-1] == ".[].filename":
    print("\\n".join(state["files"]))
else:
    sys.exit(f"the stand-in for gh does not know: {{args}}")
"""


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _program() -> str:
    """The python the workflow runs, read out of it rather than copied."""
    steps = _workflow()["jobs"]["kind"]["steps"]
    body = re.search(r"<<'PY'\n(.*?)\n *PY\s*$", steps[0]["run"], re.S)
    assert body, "the labelling step is no longer a `python3 - <<'PY'` heredoc"
    return textwrap.dedent(body.group(1))


def _kinds() -> dict[str, str]:
    """The program's KINDS table, the type of a title and its label."""
    scope: dict = {}
    table = re.search(r"^KINDS = \{.*?^\}", _program(), re.S | re.M)
    assert table, "the program no longer declares KINDS"
    exec(table.group(0), scope)
    return scope["KINDS"]


def _label(tmp_path: Path, title: str, labels=(), files=()) -> list[list[str]]:
    """Runs the workflow's program on one pull request; returns the changes."""
    run = Path(tempfile.mkdtemp(dir=tmp_path))   # a test may run it twice
    bin_dir = run / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    state = run / "state.json"
    state.write_text(json.dumps({"labels": list(labels), "files": list(files)}),
                     encoding="utf-8")
    log = run / "log"
    done = subprocess.run(
        [sys.executable, "-"], input=_program(), capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "FAKE_GH_STATE": str(state), "FAKE_GH_LOG": str(log),
             "GH_REPO": "owner/repo", "NUMBER": "7", "TITLE": title},
    )
    assert done.returncode == 0, done.stderr
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _added(changes) -> set[str]:
    return {arg.removeprefix("labels[]=") for change in changes
            if change[2] == "POST" for arg in change if arg.startswith("labels[]=")}


def _removed(changes) -> set[str]:
    return {change[3].rsplit("/", 1)[1] for change in changes if change[2] == "DELETE"}


@pytest.mark.parametrize("title, label", [
    ("feat: a blind spot names what ran there", "enhancement"),
    ("fix(report): a whole count stays whole", "bug"),
    ("docs(readme): the quick start installs the plugin", "documentation"),
    ("build(deps): bump actions/checkout from 7.0.0 to 7.0.1", "maintenance"),
    ("ci!: one more job", "maintenance"),
])
def test_the_title_names_the_kind(tmp_path, title, label):
    assert _label(tmp_path, title) == [
        ["api", "-X", "POST", "repos/owner/repo/issues/7/labels",
         "-f", f"labels[]={label}"]]


def test_a_corrected_title_moves_the_pull_request(tmp_path):
    """From Features to Fixes, and dependabot's own labels stay."""
    changes = _label(tmp_path, "fix: it was a fix after all",
                     labels=["enhancement", "dependencies"])
    assert _added(changes) == {"bug"}
    assert _removed(changes) == {"enhancement"}


def test_a_title_without_a_known_type_leaves_the_labels_alone(tmp_path):
    """A label put on by hand is the only kind such a pull request has."""
    assert _label(tmp_path, "report: an older title", labels=["bug"]) == []
    assert _label(tmp_path, "Improve the help") == []
    # The type has to open the title, with its colon.
    assert _label(tmp_path, "a fix: in the middle") == []


def test_a_label_already_there_is_not_set_again(tmp_path):
    assert _label(tmp_path, "fix: again", labels=["bug", "good first issue"]) == []


def test_a_change_to_a_detector_is_labelled_detector(tmp_path):
    changes = _label(tmp_path, "fix: counts a wait once",
                     files=["echolot/sql/detectors/monitor_contention.sql",
                            "tests/test_detectors.py"])
    assert _added(changes) == {"bug", "detector"}
    # One call for both.
    assert len(changes) == 1


def test_detector_is_never_taken_off(tmp_path):
    assert _label(tmp_path, "docs: what a detector reads",
                  labels=["detector", "documentation"],
                  files=["docs/detectors.md"]) == []


def test_every_label_has_a_section():
    """A label no section names would land under Other, which is no grouping."""
    sections = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["changelog"]["categories"]
    named = {label for section in sections for label in section["labels"]}
    for label in {*_kinds().values(), "detector"}:
        assert label in named, f"{label} is set by labels.yml and grouped by nothing"
    assert sections[-1]["labels"] == ["*"], "the last section must take the rest"
    assert sections[0]["labels"] == ["detector"], (
        "Detectors must come first: a pull request goes under the first "
        "section it matches, and a fix to a detector is about the detector"
    )


def test_the_workflow_never_runs_the_pull_requests_code():
    """`pull_request_target` gives a fork's pull request a token that can write.

    That is safe while the job reads the title and the list of files and runs
    nothing from the pull request. A checkout, or the title inside the script,
    would end that.
    """
    workflow = _workflow()
    # PyYAML reads the key `on` as True.
    assert set(workflow[True]) == {"pull_request_target"}
    steps = workflow["jobs"]["kind"]["steps"]
    assert not any("uses" in step for step in steps), "no action runs in this job"
    for step in steps:
        assert "${{" not in step["run"], "an expression inside the script"
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["kind"]["permissions"] == {"pull-requests": "write"}
