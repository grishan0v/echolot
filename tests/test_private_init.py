#!/usr/bin/env python3
"""`echolot init --private`: an install that leaves a repository as it was found (#155).

The claim is one line of `git status`, and every case here ends on it: a
person trying echolot on a repository that is not theirs to change runs
`init --private` and git shows nothing — not the layer, not a pointer, not
the permission, not a line in `.gitignore`.

Each test gets git as a fresh machine has it. This one has
`.claude/settings.local.json` in its global ignore file, put there by Claude
Code, and a test that passed because of that would prove nothing about a
machine where Claude Code never wrote the file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import exclude, hosts, layer  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

PERMISSION = "Bash(echolot:*)"


@pytest.fixture(autouse=True)
def _git_as_a_fresh_machine_has_it(tmp_path: Path, monkeypatch) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=where, check=True, capture_output=True, text=True).stdout


def _repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """A repository with one commit, holding `files` and a README."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    for name, text in {"README.md": "# app\n", **(files or {})}.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "the team's")
    return path


def _status(where: Path) -> list[str]:
    return _git(where, "status", "--porcelain", "--untracked-files=all").splitlines()


def _init(project: Path, capsys, *flags: str) -> str:
    code = main(["init", "--into", str(project), "--no-input", "--no-doctor", *flags])
    out = capsys.readouterr().out
    assert code == 0, out
    return out


def _block(project: Path) -> list[str]:
    repo = exclude.find(project)
    assert repo is not None
    return exclude.read(repo) or []


# --- the install --------------------------------------------------------------

def test_a_private_install_leaves_git_status_as_it_was(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--private", "--for", "claude,agents,cursor,codex,gemini,copilot")

    check("git shows nothing", _status(project) == [], _status(project))
    block = _block(project)
    layer_files = [f"/.claude/{src.relative_to(layer.CLAUDE_DIR).as_posix()}"
                   for src in layer.template_files() if src.name != "settings.json"]
    check("the block names every file of the layer, the manifest and the private settings",
          set(layer_files + ["/.claude/echolot-layer.json", "/.claude/settings.local.json"])
          <= set(block), block)
    check("every pointer it created, and Codex's rule",
          {"/AGENTS.md", "/GEMINI.md", "/.cursor/rules/echolot.mdc",
           "/.github/copilot-instructions.md", "/.codex/rules/echolot.rules"} <= set(block),
          block)
    check("and the traces, the machine-local config and the config setup writes later",
          block[-3:] == ["/.echolot/", "/local.yml", "/echolot.yml"], block[-3:])
    check("the permission is in the per-machine settings, and settings.json is not written",
          PERMISSION in (project / ".claude/settings.local.json").read_text(encoding="utf-8")
          and not (project / ".claude/settings.json").exists())
    check("no .gitignore is written", not (project / ".gitignore").exists())
    check("and the choice is kept", hosts.load_private(project))

    (project / "echolot.yml").write_text("project: {}\n", encoding="utf-8")
    (project / "local.yml").write_text("devices: []\n", encoding="utf-8")
    check("the config setup writes later stays out of sight too", _status(project) == [],
          _status(project))


def test_a_file_git_tracks_is_left_as_it_is(tmp_path: Path, capsys) -> None:
    team = {".gitignore": "build/\n",
            ".claude/settings.json": '{"permissions": {"allow": ["Bash(ls:*)"]}}\n',
            "AGENTS.md": "# Team rules\n\nBe kind.\n"}
    project = _repo(tmp_path / "app", team)
    out = _init(project, capsys, "--private", "--for", "claude,agents")

    check("git shows nothing: no tracked file changed", _status(project) == [],
          _status(project))
    check("the team's files are byte for byte theirs",
          all((project / name).read_text(encoding="utf-8") == text
              for name, text in team.items()))
    check("AGENTS.md is named as left alone, with the section to paste",
          "AGENTS.md is tracked by git" in out and hosts.MARKER in out
          and hosts.END_MARKER in out, out)
    check("the permission went to settings.local.json",
          PERMISSION in (project / ".claude/settings.local.json").read_text(encoding="utf-8"))
    check("and the block does not name a tracked file",
          not {"/AGENTS.md", "/.claude/settings.json", "/.gitignore"} & set(_block(project)))


def test_a_project_in_a_subdirectory_is_anchored_where_it_is(tmp_path: Path, capsys) -> None:
    top = _repo(tmp_path / "monorepo", {"android/build.gradle": "\n"})
    _init(top / "android", capsys, "--private", "--for", "claude")
    block = _block(top / "android")
    check("every pattern starts at the project, from the repository's top",
          all(p.startswith("/android/") for p in block), block)
    check("and git shows nothing", _status(top) == [], _status(top))


def test_a_worktree_keeps_its_block_where_git_reads_it(tmp_path: Path, capsys) -> None:
    main_tree = _repo(tmp_path / "app")
    _git(main_tree, "worktree", "add", "-q", str(tmp_path / "feature"))
    worktree = tmp_path / "feature"
    check("in a worktree `.git` is a file", (worktree / ".git").is_file())
    _init(worktree, capsys, "--private", "--for", "claude")
    check("git shows nothing in the worktree", _status(worktree) == [], _status(worktree))
    check("the block is in the exclude file git reads for it",
          exclude.BEGIN in (main_tree / ".git/info/exclude").read_text(encoding="utf-8"))


# --- later runs -----------------------------------------------------------------

def test_a_plain_init_stays_private_and_writes_the_block_anew(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--private", "--for", "claude")
    repo = exclude.find(project)
    text = repo.exclude.read_text(encoding="utf-8")
    # A line of the person's own before the block, and a path an earlier
    # release installed and this one does not, inside it.
    repo.exclude.write_text("*.swp\n" + text.replace(
        exclude.END, "/.claude/commands/gone.md\n" + exclude.END), encoding="utf-8")

    out = _init(project, capsys)
    check("still private, said so", "A private install" in out, out)
    after = repo.exclude.read_text(encoding="utf-8")
    check("the person's own line is kept", after.startswith("*.swp\n"), after)
    check("the path no release installs any more has dropped out",
          "/.claude/commands/gone.md" not in after, after)
    check("and git shows nothing", _status(project) == [], _status(project))


def test_shared_takes_the_block_out_and_installs_for_the_team(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--private", "--for", "claude")
    repo = exclude.find(project)
    repo.exclude.write_text(repo.exclude.read_text(encoding="utf-8") + "*.swp\n",
                            encoding="utf-8")

    out = _init(project, capsys, "--shared")
    check("the block is gone, and said to be", exclude.read(repo) is None
          and "block taken out" in out, out)
    check("the person's own line is not", "*.swp" in repo.exclude.read_text(encoding="utf-8"))
    check("the .gitignore lines and the permission go where the team sees them",
          "/.echolot/" in (project / ".gitignore").read_text(encoding="utf-8")
          and PERMISSION in (project / ".claude/settings.json").read_text(encoding="utf-8"))
    check("the choice is shared again", not hosts.load_private(project))
    check("and git sees the layer now",
          any(".claude/skills/echolot/SKILL.md" in line for line in _status(project)),
          _status(project))


def test_outside_git_there_is_nothing_to_keep_from_it(tmp_path: Path, capsys) -> None:
    project = tmp_path / "loose"
    project.mkdir()
    out = _init(project, capsys, "--private", "--for", "claude")
    check("it says so", "not inside a git repository" in out, out)
    check("and installs as usual, with nothing kept as private",
          (project / ".claude/settings.json").exists() and not hosts.load_private(project))


def test_the_plugin_way_in_keeps_its_two_lines_private(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--private", "--for", "plugin")
    check("no .claude/ and no .gitignore", not (project / ".claude").exists()
          and not (project / ".gitignore").exists())
    check("the block holds what the plugin's way in leaves",
          _block(project) == ["/.echolot/", "/local.yml", "/echolot.yml"], _block(project))
    check("and git shows nothing", _status(project) == [], _status(project))


# --- what status and doctor say ---------------------------------------------------

def test_the_layer_line_says_private_and_reads_the_private_settings(tmp_path: Path,
                                                                   capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--private", "--for", "claude")
    verdict, line = layer.one_line(project)
    check("current, and private to this clone",
          verdict == "current" and layer.PRIVATE_NOTE in line, line)

    (project / ".claude/settings.local.json").write_text("{ not json", encoding="utf-8")
    verdict, line = layer.one_line(project)
    check("the settings it reads are the private ones, and named so",
          verdict == "unreadable" and ".claude/settings.local.json" in line, line)


# --- the exclude file itself --------------------------------------------------------

def test_a_block_without_its_end_is_left_for_a_person(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    repo = exclude.find(project)
    mine = f"{exclude.BEGIN}\n/.claude/x.md\n*.mine\n"
    repo.exclude.write_text(mine, encoding="utf-8")
    said = exclude.write(repo, ["/.echolot/"])
    check("nothing is written, and the way out is named",
          said.startswith("!") and exclude.END in said
          and repo.exclude.read_text(encoding="utf-8") == mine, said)
    check("and --shared leaves it too", not exclude.remove(repo)
          and repo.exclude.read_text(encoding="utf-8") == mine)
