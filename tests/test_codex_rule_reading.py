"""What counts as a rule that lets echolot out of Codex's sandbox, and where.

A rule set to `prompt` or commented out, the same rule in other valid
Starlark, a rule above the project's root, a home rule behind an untrusted
project's, a refused `python -m echolot`, and a path that only begins like
the home directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import codex, sandbox  # noqa: E402
from tests.support import check  # noqa: E402

RULE = 'prefix_rule(pattern = ["echolot"], decision = "allow")\n'


@pytest.mark.parametrize("text", [
    "prefix_rule(pattern = ['echolot'], decision = 'allow')",
    'prefix_rule(decision = "allow", pattern = ["echolot"])',
    'prefix_rule(pattern = ["echolot",], decision = "allow")',
    'prefix_rule(\n  pattern = [\n    "echolot",\n  ],\n  decision = "allow",\n)',
    codex.RULE_FILE,
])
def test_valid_spellings_let_echolot_out(text: str) -> None:
    check("read as the rule", sandbox.lets_echolot_out(text), text)


@pytest.mark.parametrize("text", [
    'prefix_rule(pattern = ["echolot"], decision = "prompt")',
    'prefix_rule(pattern = ["echolot"], decision = "forbidden")',
    '# prefix_rule(pattern = ["echolot"], decision = "allow")',
    'prefix_rule(pattern = ["echolot", "doctor"], decision = "allow")',
])
def test_a_rule_that_does_not_allow_echolot_is_not_one(text: str) -> None:
    check("not read as the rule", not sandbox.lets_echolot_out(text), text)


def _git(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_a_rule_set_to_prompt_is_edited_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    project = tmp_path / "app"
    _git(project)
    rules = project / codex.RULE_PATH
    rules.parent.mkdir(parents=True)
    rules.write_text(codex.RULE_FILE.replace('decision = "allow"', 'decision = "prompt"'),
                     encoding="utf-8")
    a = codex.assess(project, ["codex"])
    check("the edit is seen", a["verdict"] == "edited-out", a)


def test_the_search_stops_at_the_project_s_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    above = tmp_path / "work" / ".codex" / "rules"
    above.mkdir(parents=True)
    (above / "echolot.rules").write_text(RULE, encoding="utf-8")
    app = tmp_path / "work" / "app"
    _git(app)
    check("a rule above the repository is not the project's",
          sandbox.project_rule(app) is None, sandbox.project_rule(app))
    check("while one in the repository, above the directory, is",
          sandbox.project_rule(above.parent.parent) is not None)


def test_a_home_rule_is_not_hidden_by_an_untrusted_project_s(
        tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "echolot.rules").write_text(RULE, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    project = tmp_path / "app"
    _git(project)
    (project / codex.RULE_PATH).parent.mkdir(parents=True)
    (project / codex.RULE_PATH).write_text(codex.RULE_FILE, encoding="utf-8")
    a = codex.assess(project, ["codex"])
    check("the rule Codex reads is the one that counts", a["verdict"] == "home", a)


def test_trust_is_looked_up_by_the_path_as_given(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    real = tmp_path / "real"
    _git(real)
    link = tmp_path / "link"
    link.symlink_to(real)
    (home / "config.toml").write_text(f'[projects."{link}"]\ntrust_level = "trusted"\n',
                                      encoding="utf-8")
    (real / codex.RULE_PATH).parent.mkdir(parents=True)
    (real / codex.RULE_PATH).write_text(codex.RULE_FILE, encoding="utf-8")
    check("install's answer and assess's agree",
          codex.trust(link) == "trusted"
          and codex.assess(link, ["codex"])["verdict"] == "current",
          codex.assess(link, ["codex"]))


def test_a_refused_python_m_echolot_is_told_why(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "echolot.rules").write_text(RULE, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sandbox, "_through_python_m", lambda: True)
    said = sandbox.message("x refused", sandbox.CODEX)
    check("the rule covers a line that starts with echolot",
          "started as `python -m echolot`" in said and "A glob" not in said, said)


def test_a_home_rule_gets_the_session_start_sentence(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "echolot.rules").write_text(RULE, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sandbox, "_through_python_m", lambda: False)
    said = sandbox.message("x refused", sandbox.CODEX)
    check("a rule added mid-session is not read yet", "session started before" in said, said)


def test_a_path_that_only_begins_like_home_is_not_under_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "dev"))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    check("not `~2/…`",
          sandbox.shown(tmp_path / "dev2" / "x.rules") == str(tmp_path / "dev2" / "x.rules"),
          sandbox.shown(tmp_path / "dev2" / "x.rules"))
    check("and under it, from `~/`",
          sandbox.shown(tmp_path / "dev" / "x.rules") == "~/x.rules")
