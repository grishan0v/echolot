"""The first `echolot` in a project where echolot's plugin is already installed.

The plugin brings the skills, and its door says never to run plain
`echolot init`: that installs `.claude/` beside it, and Claude Code loads
every skill twice. The first `echolot` said exactly that command (#598).
`status` now looks for the plugin where each agent keeps it, and names
`init --for plugin` when it finds one; when it finds none it names both.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from echolot import layer, plugin
from echolot.main import main
from echolot.state import next_step, project_state
from tests.support import check


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """A home of its own: the user's ~/.claude, ~/.codex and ~/.cursor."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(plugin, "_home", lambda: h)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Run from Claude Code, the suite would read as Claude Code (#740).
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")
    return h


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "app"
    p.mkdir()
    (p / ".git").mkdir()
    return p


def status(project: Path) -> dict[str, str]:
    """`echolot` in `project`: its rows by their first word."""
    out = io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main([])
    finally:
        os.chdir(here)
    rows: dict[str, str] = {}
    for line in out.getvalue().splitlines():
        if line.strip():
            word, _, rest = line.partition(" ")
            rows.setdefault(word, rest.strip())
    return rows


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def installed(home: Path, *entries: dict) -> None:
    """Claude Code's record of installs, as version 2 of the file has it."""
    write(home / ".claude" / "plugins" / "installed_plugins.json",
          {"version": 2, "plugins": {"echolot@echolot": list(entries)}})


def says_plugin(project: Path, command: str = "echolot init --for plugin") -> None:
    rows = status(project)
    check("`next` is the plugin's init", rows.get("next", "").startswith(command)
          and "echolot init —" not in rows.get("next", ""), rows)
    check("and the layer line names it, not plain init",
          f"(`{command}`)" in rows.get("layer", "")
          and "echolot plugin is in" in rows.get("layer", ""), rows)


def test_enabled_in_the_users_settings(project, home):
    write(home / ".claude" / "settings.json", {"enabledPlugins": {"echolot@echolot": True}})
    says_plugin(project)


def test_enabled_in_the_projects_local_settings(project, home):
    """`claude plugin install … --scope local` writes the flag here."""
    write(project / ".claude" / "settings.local.json",
          {"enabledPlugins": {"echolot@echolot": True}})
    check("a settings file alone is no layer", layer.audit(project) is None)
    says_plugin(project)


def test_a_local_entry_of_installed_plugins_for_this_project(project, home):
    installed(home, {"scope": "local", "projectPath": str(project), "version": "0.17.0"})
    says_plugin(project)
    check("Claude Code named", "in Claude Code" in status(project)["layer"],
          status(project))


def test_a_local_entry_for_another_project_is_not_this_one(project, home, tmp_path):
    installed(home, {"scope": "local", "projectPath": str(tmp_path / "other")})
    check("not found", plugin.for_project(project) == [], plugin.for_project(project))


def test_turned_off_for_the_project_is_not_on(project, home):
    write(home / ".claude" / "settings.json", {"enabledPlugins": {"echolot@echolot": True}})
    write(project / ".claude" / "settings.local.json",
          {"enabledPlugins": {"echolot@echolot": False}})
    installed(home, {"scope": "local", "projectPath": str(project)})
    check("the nearer file decides", plugin.for_project(project) == [],
          plugin.for_project(project))


def test_a_linked_worktree_takes_the_main_checkouts_entry(tmp_path, home):
    """`--scope local` run in a worktree records the main checkout (#597)."""
    main_checkout = tmp_path / "main"
    gitdir = main_checkout / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    installed(home, {"scope": "local", "projectPath": str(main_checkout)})
    check("found from the worktree", plugin.for_project(wt) == ["claude"],
          plugin.for_project(wt))


def test_the_plugin_in_codex_adds_the_rule(project, home):
    (home / ".codex" / "plugins" / "cache" / "echolot" / "echolot" / "0.17.0").mkdir(
        parents=True)
    says_plugin(project, "echolot init --for plugin,codex")


def test_the_plugin_copied_into_cursor(project, home):
    copy = plugin.cursor_copy(home)
    write(copy / ".cursor-plugin" / "plugin.json", {"name": "echolot"})
    says_plugin(project)


def _in_cursor(home: Path) -> None:
    write(plugin.cursor_copy(home) / ".cursor-plugin" / "plugin.json", {"name": "echolot"})


def _in_codex(home: Path) -> None:
    (home / ".codex" / "plugins" / "cache" / "echolot" / "echolot" / "0.17.0").mkdir(
        parents=True)


@pytest.mark.parametrize("where, title", [(_in_cursor, "Cursor"), (_in_codex, "Codex")])
def test_in_claude_code_with_the_plugin_elsewhere_next_is_plain_init(
        project, home, monkeypatch, where, title):
    """`--for plugin` keeps .claude/ out, and a plugin in Cursor or Codex
    brings Claude Code nothing: it was left with no skills (#740)."""
    where(home)
    monkeypatch.setenv("CLAUDECODE", "1")
    rows = status(project)
    check("`next` is plain init, for Claude Code",
          rows.get("next", "").startswith("echolot init — installs the .claude/ layer for "
                                          "Claude Code") and "--for plugin" not in rows["next"]
          and title in rows["next"], rows)
    check("and the layer line says where the plugin is",
          rows.get("layer") == f"none installed here, and Claude Code has no echolot plugin "
          f"(it is in {title}) (`echolot init`)", rows)
    code = main(["init", "--into", str(project), "--no-input", "--no-doctor"])
    check("following it installs the layer", code == 0 and layer.audit(project) is not None,
          code)


def test_in_claude_code_with_its_own_plugin_next_is_still_the_plugins_init(
        project, home, monkeypatch):
    """#598 stands: Claude Code's plugin brings the skills."""
    write(home / ".claude" / "settings.json", {"enabledPlugins": {"echolot@echolot": True}})
    _in_cursor(home)
    monkeypatch.setenv("CLAUDECODE", "1")
    says_plugin(project)
    check("and names no other command", "In Claude Code" not in status(project)["next"],
          status(project))


def test_with_the_plugin_elsewhere_and_no_agent_known_next_names_both(project, home):
    _in_cursor(home)
    rows = status(project)
    check("the plugin's init first", rows.get("next", "").startswith(
        "echolot init --for plugin — the echolot plugin is in Cursor"), rows)
    check("and plain init for Claude Code beside it",
          "In Claude Code, which has no echolot plugin: `echolot init`" in rows["next"], rows)
    check("the layer line says Claude Code is not one of them",
          "echolot plugin is in Cursor, not Claude Code" in rows.get("layer", ""), rows)


def test_without_a_plugin_found_next_names_both(project, home):
    rows = status(project)
    check("plain init, as before", rows.get("layer") == "none installed here "
          "(`echolot init`)", rows)
    check("and the plugin's init beside it",
          rows.get("next", "").startswith("echolot init — installs the .claude/ layer")
          and "With the echolot plugin installed: `echolot init --for plugin` instead"
          in rows.get("next", ""), rows)


def test_after_init_for_plugin_the_layer_is_left_out(project, home):
    write(project / ".claude" / "settings.local.json",
          {"enabledPlugins": {"echolot@echolot": True}})
    code = main(["init", "--into", str(project), "--for", "plugin", "--no-input",
                 "--no-doctor"])
    check("init runs", code == 0)
    st = project_state(project)
    check("the layer is left out on purpose", st["layer_verdict"] == "opted-out",
          st["layer_verdict"])
    check("and nothing asks for init again", "init" not in next_step(st).split(" —")[0],
          next_step(st))


@pytest.mark.parametrize("case", ["codex-used", "in-claude-code", "no-codex"])
def test_with_the_plugin_in_claude_code_only_codex_gets_its_own_init(
        project, home, monkeypatch, case):
    """The mirror of #740: `--for plugin` gives Codex no pointer and no
    rule, and sends it to a plugin skill it does not have (#841)."""
    write(home / ".claude" / "settings.json", {"enabledPlugins": {"echolot@echolot": True}})
    if case != "no-codex":
        (project / ".codex").mkdir()
    if case == "in-claude-code":
        monkeypatch.setenv("CLAUDECODE", "1")
    rows = status(project)
    nxt = rows.get("next", "")
    check("the plugin's init first, for Claude Code", nxt.startswith(
        "echolot init --for plugin — the echolot plugin is in Claude Code"), rows)
    if case != "codex-used":
        check("and nothing else (#598)", "In Codex" not in nxt, rows)
        return
    command = "echolot init --for agents,codex"
    check("and Codex's own init beside it, said as Codex's",
          f"In Codex, which has no echolot plugin: `{command}`, run outside its sandbox"
          in nxt, rows)
    check("the same command the codex line names", f"`{command}`" in rows.get("codex", ""),
          rows)
