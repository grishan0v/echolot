#!/usr/bin/env python3
"""The check that a pull request says whether, and how, an LLM was used.

The policy is in CONTRIBUTING.md under "LLM assistance"; the template asks the
question, and .github/workflows/disclosure.yml reads the answer. The workflow
runs only on GitHub, and only on pull requests from outside the project, so
the program it runs is read out of it and run here: against the template
itself, filled in the ways an author fills it, and left the ways a program
that skips it leaves it. Filling the real template keeps the two in step: a
box reworded in one and not the other fails here first.
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

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "disclosure.yml"
TEMPLATE = ROOT / ".github" / "pull_request_template.md"
FORMS = ROOT / ".github" / "ISSUE_TEMPLATE"

NO_LLM = "No LLM was used"
USED = "An LLM was used"
READ = "I have read and understand"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step() -> dict:
    steps = _workflow()["jobs"]["llm"]["steps"]
    assert len(steps) == 1, "the job is one step, the program below"
    return steps[0]


def _program() -> str:
    """The python the workflow runs, read out of it rather than copied."""
    body = re.search(r"<<'PY'\n(.*?)\n *PY\s*$", _step()["run"], re.S)
    assert body, "the step is no longer a `python3 - <<'PY'` heredoc"
    return textwrap.dedent(body.group(1))


def _check(body: str) -> tuple[bool, str]:
    """Runs the program on one description; whether it passed, and what it said."""
    done = subprocess.run(
        [sys.executable, "-"], input=_program(), capture_output=True, text=True,
        env={**os.environ, "BODY": body, "GITHUB_REPOSITORY": "owner/repo"},
    )
    assert not done.stderr, done.stderr
    return done.returncode == 0, done.stdout


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _tick(body: str, *boxes: str) -> str:
    for box in boxes:
        body, count = re.subn(rf"^(\s*- )\[ \]( {re.escape(box)})", r"\1[x]\2", body,
                              flags=re.M)
        assert count == 1, f"the template has no box that starts {box!r}"
    return body


def _fill(body: str, **lines: str) -> str:
    """Writes an answer after each label, where the template has a hint."""
    for label, value in lines.items():
        label = {"what": "What the model wrote or changed"}.get(label, label.capitalize())
        body, count = re.subn(rf"^(\s*- {label}:).*$", lambda m, value=value: f"{m.group(1)} {value}",
                              body, flags=re.M)
        assert count == 1, f"the template has no line labelled {label!r}"
    return body


USED_LINES = {
    "tool": "Claude Code",
    "model": "an example model 4.1",
    "effort": "high",
    "what": "the new detector and its test; I wrote the docs",
}


def test_the_template_as_it_comes_fails():
    passed, said = _check(_template())
    assert not passed
    assert "Neither" in said and "not ticked" in said
    assert "CONTRIBUTING.md#llm-assistance" in said


def test_no_llm_and_the_last_box_pass():
    assert _check(_tick(_template(), NO_LLM, READ))[0]


def test_a_used_llm_named_on_all_four_lines_passes():
    body = _fill(_tick(_template(), USED, READ), **USED_LINES)
    assert _check(body) == (True, "LLM assistance: answered, an LLM was used and named.\n")
    # GitHub sends a description typed in the browser with \r\n.
    assert _check(body.replace("\n", "\r\n"))[0]


@pytest.mark.parametrize("line", ["tool", "model", "effort", "what"])
@pytest.mark.parametrize("value", ["", "<!-- left as the hint -->", " - "])
def test_a_used_llm_with_a_line_left_empty_fails(line, value):
    body = _fill(_tick(_template(), USED, READ), **{**USED_LINES, line: value})
    passed, said = _check(body)
    assert not passed
    assert said.count("::error") == 1, said


@pytest.mark.parametrize("line", ["tool", "model", "what"])
@pytest.mark.parametrize("value", ["n/a", "None.", "TBD", "unknown"])
def test_an_answer_that_names_nothing_fails(line, value):
    body = _fill(_tick(_template(), USED, READ), **{**USED_LINES, line: value})
    assert not _check(body)[0]


@pytest.mark.parametrize("effort", ["default", "no such setting", "n/a", "max"])
def test_effort_may_be_any_answer_at_all(effort):
    """Not every tool has a reasoning level; the template says what to write then."""
    body = _fill(_tick(_template(), USED, READ), **{**USED_LINES, "effort": effort})
    assert _check(body)[0]


def test_what_the_model_did_may_take_several_lines():
    body = _fill(_tick(_template(), USED, READ), **{**USED_LINES, "what": ""})
    body = body.replace("What the model wrote or changed: \n",
                        "What the model wrote or changed:\n    - the detector\n    - its test\n")
    assert _check(body)[0]


def test_both_answers_at_once_fail():
    body = _fill(_tick(_template(), NO_LLM, USED, READ), **USED_LINES)
    passed, said = _check(body)
    assert not passed
    assert "Both" in said


def test_no_llm_with_a_model_named_fails():
    passed, said = _check(_fill(_tick(_template(), NO_LLM, READ), model="an example model"))
    assert not passed
    assert "name one anyway" in said


def test_no_llm_with_the_lines_answered_none_passes():
    body = _fill(_tick(_template(), NO_LLM, READ),
                 tool="none", model="n/a", effort="", what="-")
    assert _check(body)[0]


@pytest.mark.parametrize("answer", [NO_LLM, USED])
def test_the_last_box_is_required_either_way(answer):
    passed, said = _check(_fill(_tick(_template(), answer), **USED_LINES)
                          if answer == USED else _tick(_template(), answer))
    assert not passed
    assert "read and understand" in said


def test_the_section_may_be_trimmed_to_the_answer():
    """An author who deletes the hints and the answer that is not theirs passes."""
    assert _check(textwrap.dedent(f"""\
        ## Summary

        Counts a wait once.

        ## LLM assistance

        - [x] {NO_LLM} for any part of this pull request.
        - [x] {READ} every line of this diff, and I ran the checks under Validation myself.
        """))[0]


@pytest.mark.parametrize("body", [
    "",
    "Fixes #1. This PR improves performance and adds robust error handling.",
    "## Summary\n\nFixes #1.\n\n## Validation\n\n- [x] `pytest -q` passes locally.\n",
])
def test_a_description_without_the_section_fails(body):
    passed, said = _check(body)
    assert not passed
    assert "no LLM assistance section" in said


def test_the_description_is_never_printed():
    """A line of it starting with `::` would be a command to the runner."""
    body = _fill(_tick(_template(), USED, READ),
                 **{**USED_LINES, "tool": "::add-mask::x", "model": "::warning::y"})
    body = body.replace("## Summary\n", "## Summary\n::notice::z\n")
    for case in (body, body.replace(f"[x] {READ}", f"[ ] {READ}")):
        _, said = _check(case)
        for line in said.splitlines():
            assert not line.startswith("::") or line.startswith("::error title=LLM assistance::"), line
        assert "add-mask" not in said and "::warning" not in said and "::notice" not in said


def test_the_workflow_never_runs_the_pull_requests_code():
    """`pull_request`, no token permissions, no checkout, no action, no expression in the script."""
    workflow = _workflow()
    # PyYAML reads the key `on` as True.
    assert set(workflow[True]) == {"pull_request"}
    assert set(workflow[True]["pull_request"]["types"]) == {
        "opened", "edited", "reopened", "synchronize"}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["llm"]
    assert "permissions" not in job
    step = _step()
    assert "uses" not in step
    assert "${{" not in step["run"], "an expression inside the script"
    assert step["env"] == {"BODY": "${{ github.event.pull_request.body }}"}


def test_the_project_and_its_apps_are_not_checked():
    condition = " ".join(_workflow()["jobs"]["llm"]["if"].split())
    assert "github.event.pull_request.user.type != 'Bot'" in condition
    assert ("!contains(fromJSON('[\"OWNER\", \"MEMBER\", \"COLLABORATOR\"]'), "
            "github.event.pull_request.author_association)") in condition


def test_every_pointer_lands_on_the_policy():
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert re.search(r"^## LLM assistance$", contributing, re.M)
    assert "CONTRIBUTING.md#llm-assistance" in _program()
    assert "CONTRIBUTING.md#llm-assistance" in _template()


@pytest.mark.parametrize("form", ["bug_report.yml", "feature_request.yml"])
def test_every_issue_form_asks_about_llm_assistance(form):
    fields = {field.get("id"): field
              for field in yaml.safe_load((FORMS / form).read_text(encoding="utf-8"))["body"]}
    llm = fields.get("llm")
    assert llm, f"{form} has no LLM assistance field"
    assert llm["validations"]["required"] is True
    assert "CONTRIBUTING.md#llm-assistance" in llm["attributes"]["description"]
