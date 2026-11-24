#!/usr/bin/env python3
"""`echolot uninit`: what `init` wrote, and the markers, taken out again (#562).

The claim is the tree: `init` and then `uninit` leave a project as it was,
which in a repository is one line of `git status` — empty. Around it, what
`uninit` keeps and says rather than deletes: a file of the layer edited here,
the record of the work, the config, a section whose end is not known, a block
another checkout's private install needs.

Each test gets git as a fresh machine has it, as in test_private_init.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import exclude, hosts, layer, recorder, uninit  # noqa: E402
from echolot.main import main  # noqa: E402

PERMISSION = "Bash(echolot:*)"
EVERY_AGENT = "claude,agents,codex,gemini,cursor,copilot"


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
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    for name, text in {"README.md": "# app\n", **(files or {})}.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_bytes(text.encode("utf-8"))
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "the team's")
    return path


def _status(where: Path) -> list[str]:
    return _git(where, "status", "--porcelain", "--untracked-files=all",
                "--ignored").splitlines()


def _tree(project: Path) -> dict[str, bytes | None]:
    return {p.relative_to(project).as_posix(): (None if p.is_dir() else p.read_bytes())
            for p in sorted(project.rglob("*")) if ".git" not in p.relative_to(project).parts}


def _init(project: Path, capsys, *flags: str) -> str:
    code = main(["init", "--into", str(project), "--no-input", "--no-doctor", *flags])
    out = capsys.readouterr().out
    assert code == 0, out
    return out


def _uninit(project: Path, capsys, *flags: str, expect: int = 0) -> str:
    code = main(["uninit", "--into", str(project), *flags])
    got = capsys.readouterr()
    said = got.out + got.err
    assert code == expect, said
    return said


TEAM = {
    ".gitignore": "build/\n*.iml\n",
    "AGENTS.md": "# The team's rules\n\nReview before merge.\n",
    # Not in the layout `init` writes a merged file in: the committed bytes
    # are what comes back, not a reformatted copy.
    ".claude/settings.json": '{"hooks": {"Stop": [{"command": "make lint"}]}}\n',
}


# --- the round trip -------------------------------------------------------------

def test_init_then_uninit_leaves_git_status_empty(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app", TEAM)
    before = _tree(project)
    _init(project, capsys, "--for", EVERY_AGENT)
    assert _status(project), "init wrote nothing git sees"

    said = _uninit(project, capsys, "--yes")
    assert "Done: nothing of echolot's is left" in said, said
    assert _status(project) == [], _status(project)
    assert _tree(project) == before


def test_a_private_install_comes_out_and_the_exclude_file_is_as_it_was(
        tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app", TEAM)
    repo = exclude.find(project)
    assert repo is not None
    repo.exclude.write_text("# mine\n*.tmp\n", encoding="utf-8")
    before, ignored = _tree(project), repo.exclude.read_bytes()
    _init(project, capsys, "--private", "--for", EVERY_AGENT)
    assert exclude.read(repo), "the private install wrote no block"
    assert (project / ".claude" / layer.PRIVATE_SETTINGS).is_file()

    _uninit(project, capsys, "--yes")
    assert repo.exclude.read_bytes() == ignored
    assert _status(project) == []
    assert _tree(project) == before


def test_a_private_install_leaves_what_git_tracks_alone(tmp_path: Path, capsys) -> None:
    """A private install writes no tracked file, so `uninit` of one takes
    nothing out of one: a permission in the team's settings.json is theirs."""
    theirs = json.dumps({"permissions": {"allow": [PERMISSION]}}) + "\n"
    project = _repo(tmp_path / "app", {".claude/settings.json": theirs})
    _init(project, capsys, "--private")

    said = _uninit(project, capsys, "--yes")
    assert (project / ".claude" / "settings.json").read_text(encoding="utf-8") == theirs
    assert "≠ .claude/settings.json — tracked by git" in said, said
    assert not (project / ".claude" / layer.PRIVATE_SETTINGS).exists()
    assert _status(project) == []


def test_another_checkouts_private_install_keeps_the_block(tmp_path: Path, capsys) -> None:
    main_checkout = _repo(tmp_path / "app")
    _init(main_checkout, capsys, "--private")
    tree = tmp_path / "tree"
    _git(main_checkout, "worktree", "add", "-q", str(tree))
    _init(tree, capsys, "--private")
    repo = exclude.find(tree)
    block = exclude.read(repo)

    said = _uninit(tree, capsys, "--yes")
    assert exclude.read(repo) == block, "the block the main checkout needs went"
    assert "echolot's block stays" in said and str(main_checkout.resolve()) in said, said
    assert not (tree / ".claude").exists()
    assert _status(tree) == []
    # The main checkout's install is still kept from git, all of it.
    assert all(line.startswith("!! ") for line in _status(main_checkout)), \
        _status(main_checkout)


# --- what stays, and is said ----------------------------------------------------

def test_a_file_edited_here_stays_until_all(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    hunter = project / ".claude" / "agents" / "perf-hunter.md"
    hunter.write_text(hunter.read_text(encoding="utf-8") + "\nOur modules: :core.\n",
                      encoding="utf-8")

    said = _uninit(project, capsys, "--yes")
    assert hunter.is_file() and "≠ .claude/agents/perf-hunter.md — edited here" in said
    assert (project / ".claude" / layer.LAYER_MANIFEST).is_file()
    assert not (project / ".claude" / "skills").exists()

    _uninit(project, capsys, "--yes", "--all")
    assert not (project / ".claude").exists()
    assert _status(project) == []


def test_a_layer_a_newer_echolot_wrote_goes_by_its_manifest(tmp_path: Path, capsys) -> None:
    """`init` refuses a newer layer so as not to roll it back. Taking out what
    that release wrote, as it wrote it, rolls nothing back: a file is judged
    by the hash its manifest recorded, a file this release does not ship
    included."""
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    claude = project / ".claude"
    newer = claude / "skills" / "echolot" / "references" / "later.md"
    newer.write_text("a topic of a later release\n", encoding="utf-8")
    changed = claude / "agents" / "perf-hunter.md"
    changed.write_text("the hunter as a later release words it\n", encoding="utf-8")
    manifest = json.loads((claude / layer.LAYER_MANIFEST).read_text(encoding="utf-8"))
    manifest["echolot"] = "999.0"
    manifest["files"].update({"skills/echolot/references/later.md": layer.sha(newer),
                              "agents/perf-hunter.md": layer.sha(changed),
                              "../../outside.md": "0" * 16})
    (claude / layer.LAYER_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("not echolot's\n", encoding="utf-8")
    assert layer.ahead(project)

    _uninit(project, capsys, "--yes")
    assert not claude.exists()
    assert outside.is_file(), "a manifest key reached outside .claude/"
    assert _status(project) == []


def test_the_record_of_the_work_stays_unless_data(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app", {".gitignore": "build/\n"})
    _init(project, capsys)
    (project / ".echolot" / "out").mkdir(parents=True)
    (project / ".echolot" / "out" / "report.json").write_text("{}", encoding="utf-8")
    (project / "echolot.yml").write_text("project:\n  process: a.b\n", encoding="utf-8")
    _git(project, "add", "echolot.yml")
    _git(project, "commit", "-q", "-m", "the config")

    said = _uninit(project, capsys, "--yes")
    assert (project / ".echolot" / "out" / "report.json").is_file()
    assert not hosts.choice_path(project).exists()
    assert (project / "echolot.yml").is_file()
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    # The line that keeps the reports from git stays with them; the one for
    # a local.yml that is not there goes.
    assert "/.echolot/" in gitignore and "/local.yml" not in gitignore, gitignore
    assert "`--data` takes it" in said, said

    _uninit(project, capsys, "--yes", "--data")
    assert not (project / ".echolot").exists() and not (project / "echolot.yml").exists()
    assert (project / ".gitignore").read_text(encoding="utf-8") == "build/\n"
    assert _status(project) == [" D echolot.yml"]


def test_settings_lose_echolots_permission_and_nothing_else() -> None:
    template = json.loads((layer.CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
    theirs = {"permissions": {"allow": ["Bash(ls:*)", PERMISSION], "deny": []},
              "enabledPlugins": {"x@y": True}}
    assert uninit.unfold(template, theirs) == {
        "permissions": {"allow": ["Bash(ls:*)"], "deny": []},
        "enabledPlugins": {"x@y": True}}
    # A container echolot's part emptied goes; one empty before stays.
    assert uninit.unfold(template, {"permissions": {"allow": [PERMISSION]}}) \
        is uninit._GONE
    assert uninit.unfold(template, {"permissions": {"allow": []}}) == \
        {"permissions": {"allow": []}}


def test_settings_that_do_not_parse_are_named_and_left(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    settings = project / ".claude" / "settings.json"
    settings.write_text('{"permissions": {"allow": ["Bash(echolot:*)"]', encoding="utf-8")
    said = _uninit(project, capsys, "--yes")
    assert "! .claude/settings.json — not JSON" in said, said
    assert settings.read_text(encoding="utf-8").endswith('"Bash(echolot:*)"]')


# --- the pointers ---------------------------------------------------------------

@pytest.mark.parametrize("text, left", [
    ("{s}\n", ""),
    ("# Rules\n\n{s}\n", "# Rules\n"),
    ("{s}\n\n# Rules\n", "# Rules\n"),
    ("# One\n\n{s}\n\n# Two\n", "# One\n\n# Two\n"),
    ("# One\r\n\r\n{s}\r\n\r\n# Two\r\n", "# One\r\n\r\n# Two\r\n"),
    ("# One\n{s}\n# Two\n", "# One\n# Two\n"),
])
def test_a_section_comes_out_with_the_lines_around_it_as_they_were(text, left) -> None:
    section = hosts.section(hosts.BY_KEY["gemini"].render()).rstrip("\n")
    if "\r\n" in text:
        section = section.replace("\n", "\r\n")
    assert uninit.without_section(text.format(s=section)) == left


def test_a_pointer_from_before_the_end_marker(tmp_path: Path, capsys) -> None:
    """Untouched, it is what an older echolot wrote, whole; edited, where it
    stops is not known, and it stays for a person."""
    project = _repo(tmp_path / "app")
    old = hosts._without_an_end(hosts.BY_KEY["gemini"].render())
    (project / "GEMINI.md").write_text(old, encoding="utf-8")
    edited = hosts._without_an_end(hosts.BY_KEY["copilot"].render()) + "\nOurs.\n"
    copilot = project / ".github" / "copilot-instructions.md"
    copilot.parent.mkdir()
    copilot.write_text(edited, encoding="utf-8")

    said = _uninit(project, capsys, "--yes")
    assert not (project / "GEMINI.md").exists()
    assert copilot.read_text(encoding="utf-8") == edited
    assert "! .github/copilot-instructions.md — echolot's section from an older" in said


def test_cursors_rule_with_its_frontmatter_edited_stays_until_all(
        tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys, "--for", "cursor")
    rule = project / ".cursor" / "rules" / "echolot.mdc"
    rule.write_text(rule.read_text(encoding="utf-8").replace(
        "alwaysApply: true", "alwaysApply: false"), encoding="utf-8")
    edited = rule.read_bytes()

    said = _uninit(project, capsys, "--yes")
    assert rule.read_bytes() == edited, "a rule with an edited header lost its body"
    assert "frontmatter edited here" in said, said
    _uninit(project, capsys, "--yes", "--all")
    assert not (project / ".cursor").exists()


# --- the markers ----------------------------------------------------------------

def test_the_markers_come_out_and_a_line_with_more_on_it_is_named(
        tmp_path: Path, capsys) -> None:
    source = ("package a\n\nclass App {\n    fun onCreate() {\n"
              '        android.os.Trace.beginSection("echolot.App") // echolot:mark\n'
              "        try { // echolot:mark\n"
              "            work()\n"
              "        } finally { android.os.Trace.endSection() } // echolot:mark\n"
              "        other() // echolot:mark\n"
              "    }\n}\n")
    project = _repo(tmp_path / "app", {"app/src/main/java/a/App.kt": source})
    said = _uninit(project, capsys, "--yes")
    text = (project / "app" / "src" / "main" / "java" / "a" / "App.kt").read_text(
        encoding="utf-8")
    assert "beginSection" not in text and "work()" in text
    assert "other() // echolot:mark" in text, "a line carrying code went"
    assert "3 line(s) `mark --apply` wrote" in said, said
    # Numbered as `mark --remove` numbers it: after the pass.
    assert "App.kt:6: other() // echolot:mark" in said, said
    assert "build and install the app again" in said, said


# --- asking first ---------------------------------------------------------------

def test_nothing_moves_without_a_yes(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    installed = _tree(project)
    said = _uninit(project, capsys, expect=1)
    assert "nothing removed" in said and "--yes" in said, said
    assert _tree(project) == installed
    said = _uninit(project, capsys, "--dry-run")
    assert "Nothing removed: --dry-run" in said, said
    assert _tree(project) == installed


def test_a_project_without_echolot_has_nothing_to_take_out(tmp_path: Path, capsys) -> None:
    project = _repo(tmp_path / "app", TEAM)
    said = _uninit(project, capsys)
    assert "Nothing of echolot's in" in said, said
    assert _status(project) == []


def test_a_running_job_of_the_page_refuses(tmp_path: Path, capsys, monkeypatch) -> None:
    from echolot import job
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    installed = _tree(project)
    monkeypatch.setattr(job, "read", lambda where, name, alive=None: (
        {"status": job.RUNNING} if name == "check" else None))
    said = _uninit(project, capsys, "--yes", expect=1)
    assert "the page's `check` job is running" in said, said
    assert _tree(project) == installed


# --- the run log ----------------------------------------------------------------

def test_the_run_log_does_not_bring_echolot_back(tmp_path: Path, capsys,
                                                 monkeypatch) -> None:
    """`main` writes a line after every command, under `.echolot/`. After
    an `uninit` that took the folder out, that line would put it back."""
    monkeypatch.delenv("ECHOLOT_NO_RECORD")
    project = _repo(tmp_path / "app")
    _init(project, capsys)
    assert (project / recorder.LOG_FILE).is_file()
    _uninit(project, capsys, "--yes")
    assert not (project / ".echolot").exists()
    assert _status(project) == []

    # Where the record of the work stays, the run is in its log.
    _init(project, capsys)
    (project / ".echolot" / "traces").mkdir()
    (project / ".echolot" / "traces" / "cold-1.perfetto-trace").write_bytes(b"x")
    _uninit(project, capsys, "--yes")
    runs = recorder.read(project / recorder.LOG_FILE)
    assert runs[-1]["cmd"] == "uninit" and runs[-1]["facts"]["uninit"] == "done", runs[-1]
