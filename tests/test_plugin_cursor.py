#!/usr/bin/env python3
"""`echolot plugin cursor`: the plugin, copied into Cursor's folder (#399).

For one person, Cursor installs nothing from a repository. It loads its
folder for local plugins and the plugins Claude Code has installed and
enabled. The README's way in was four shell commands: they needed git and a
POSIX shell, left a clone behind, and the copy they made stayed at its release
after `pipx upgrade echolot`, with nothing to say so.

What is held here: the copy is the package's plugin, whole; what is in the
way is left as it is; a failure halfway keeps the old copy; and `echolot`,
`doctor` and `echolot plugin` say when the copy is another release's. Every
case runs in a home of its own under tmp_path, never in the real one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import echolot
from echolot import plugin, selftest
from echolot.main import main
from tests.support import check

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "echolot"


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """A home with Cursor's folder in it, for the cases that write, and no
    sandbox marks from whatever ran this."""
    h = tmp_path / "home"
    (h / ".cursor").mkdir(parents=True)
    monkeypatch.setattr(plugin, "_home", lambda: h)
    for name in ("CURSOR_SANDBOX", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    return h


def _files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _an_older_copy(home: Path, version: str = "0.14.1") -> Path:
    """A copy from an earlier release, with a file that release had and this
    one dropped."""
    copy = plugin.cursor_copy(home)
    for kind in (".cursor-plugin", ".claude-plugin"):
        (copy / kind).mkdir(parents=True)
        (copy / kind / "plugin.json").write_text(
            json.dumps({"name": "echolot", "version": version}), encoding="utf-8")
    (copy / "skills" / "gone").mkdir(parents=True)
    (copy / "skills" / "gone" / "SKILL.md").write_text("a dropped skill", encoding="utf-8")
    return copy


# --- where the plugin's files come from ---------------------------------------------

def test_a_checkout_reads_the_plugin_folder_itself():
    """Here, as in an editable install: no copy inside the package."""
    check("the checkout's plugins/echolot", plugin.source() == PLUGIN, plugin.source())


def test_a_wheel_reads_the_copy_inside_the_package(tmp_path, monkeypatch):
    """setup.py puts the plugin into the wheel as echolot/plugin, and that is
    the one read, ahead of any folder beside the package."""
    package = tmp_path / "site-packages" / "echolot"
    for where in (package / "plugin", tmp_path / "site-packages" / "plugins" / "echolot"):
        (where / ".cursor-plugin").mkdir(parents=True)
        (where / ".cursor-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(plugin, "__file__", str(package / "plugin.py"))
    check("the copy inside the package", plugin.source() == package / "plugin",
          plugin.source())


def test_an_install_that_lost_the_plugin_says_so(tmp_path, monkeypatch, home):
    monkeypatch.setattr(plugin, "__file__", str(tmp_path / "echolot" / "plugin.py"))
    check("no plugin to be found", plugin.source() is None, plugin.source())
    with pytest.raises(plugin.Refused, match="carries no plugin"):
        plugin.install(home)
    check("nothing was written", not (home / ".cursor" / "plugins").exists(),
          list((home / ".cursor").iterdir()))


def test_setup_copies_the_one_plugin_into_the_wheel():
    """One plugin in the repository, and the build's copy of it."""
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    check("setup.py copies plugins/echolot",
          '"plugins" / "echolot"' in setup and '"echolot" / "plugin"' in setup, setup)
    check("and the sdist carries it, for the wheel built from it",
          "graft plugins/echolot" in manifest, manifest)


# --- the copy ------------------------------------------------------------------------

def test_the_copy_is_the_packages_plugin_whole(home):
    done = plugin.install(home)
    copy = plugin.cursor_copy(home)
    check("into ~/.cursor/plugins/local/echolot",
          done["copy"] == copy and copy == home / ".cursor" / "plugins" / "local" / "echolot",
          done)
    check("every file, byte for byte, the hidden manifests too",
          _files(copy) == _files(PLUGIN), sorted(_files(copy)))
    check("Cursor's manifest names this release",
          plugin.version_of(copy) == echolot.__version__, plugin.version_of(copy))
    check("nothing was replaced", not done["replaced"] and done["before"] is None, done)
    check("no staging folder left beside it",
          sorted(p.name for p in copy.parent.iterdir()) == ["echolot"],
          list(copy.parent.iterdir()))


def test_a_copy_from_an_earlier_release_is_replaced_whole(home):
    """A file the new release dropped does not stay behind to be loaded."""
    _an_older_copy(home)
    done = plugin.install(home)
    copy = plugin.cursor_copy(home)
    check("the earlier release is said", done["before"] == "0.14.1", done)
    check("the dropped skill is gone", not (copy / "skills" / "gone").exists(),
          sorted(_files(copy)))
    check("and the copy is this release's", _files(copy) == _files(PLUGIN),
          sorted(_files(copy)))


def test_an_empty_folder_in_the_way_is_taken(home, capsys):
    """Nothing in it to lose, the way a copy cut short can leave it."""
    plugin.cursor_copy(home).mkdir(parents=True)
    assert main(["plugin", "cursor"]) == 0
    out = capsys.readouterr().out
    check("replaced, from no release it can name", "in place of an earlier copy" in out, out)
    check("and the copy is this release's",
          plugin.version_of(plugin.cursor_copy(home)) == echolot.__version__, out)


def test_without_cursors_folder_nothing_is_written(tmp_path):
    """Cursor makes ~/.cursor on its first run: none means no Cursor here."""
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(plugin.Refused, match=r"no ~/\.cursor here"):
        plugin.install(bare)
    check("no ~/.cursor made", not (bare / ".cursor").exists(), list(bare.iterdir()))


def test_a_link_is_left_as_it_is(home, tmp_path):
    """A checkout linked in by someone working on the plugin: theirs."""
    theirs = tmp_path / "checkout" / "plugins" / "echolot"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("their work", encoding="utf-8")
    copy = plugin.cursor_copy(home)
    copy.parent.mkdir(parents=True)
    copy.symlink_to(theirs)
    with pytest.raises(plugin.Refused, match="is a link to"):
        plugin.install(home)
    check("the link stays", copy.is_symlink() and copy.resolve() == theirs.resolve(), copy)
    check("and what it points at", (theirs / "SKILL.md").read_text("utf-8") == "their work",
          sorted(_files(theirs)))
    check("a link is nobody's stale copy", plugin.stale_line(home) is None,
          plugin.stale_line(home))


def test_a_folder_that_is_not_echolots_is_left_as_it_is(home):
    copy = plugin.cursor_copy(home)
    copy.mkdir(parents=True)
    (copy / "notes.md").write_text("someone's", encoding="utf-8")
    with pytest.raises(plugin.Refused, match="something other than echolot's plugin"):
        plugin.install(home)
    check("left as it was", _files(copy) == {"notes.md": b"someone's"}, _files(copy))


def test_a_failed_swap_keeps_the_old_copy(home, monkeypatch):
    """The new copy is made beside the old one and renamed into place: when
    the rename fails, the old one goes back, and nothing is left over."""
    _an_older_copy(home)
    rename = Path.rename

    def refused(self, target):
        if self.name == "echolot" and self.parent.name.startswith(".echolot-"):
            raise OSError("the disk is full")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", refused)
    with pytest.raises(OSError, match="the disk is full"):
        plugin.install(home)
    copy = plugin.cursor_copy(home)
    check("the old copy is where it was", plugin.version_of(copy) == "0.14.1",
          sorted(_files(copy)))
    check("with all of its files", (copy / "skills" / "gone" / "SKILL.md").is_file(),
          sorted(_files(copy)))
    check("no staging folder left", sorted(p.name for p in copy.parent.iterdir())
          == ["echolot"], list(copy.parent.iterdir()))


# --- Claude Code's plugin, which Cursor loads as well --------------------------------

def _claude_code(home: Path, installed: list[str], enabled: dict[str, bool]) -> None:
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {key: [{"version": "0.15.0"}] for key in installed}}),
        encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


@pytest.mark.parametrize("installed, enabled, loaded", [
    (["echolot@echolot"], {"echolot@echolot": True}, True),
    (["echolot@claude-community"], {"echolot@claude-community": True}, True),
    (["echolot@echolot"], {"echolot@echolot": False}, False),
    (["echolot@echolot"], {}, False),
    (["echolotx@other"], {"echolotx@other": True}, False),
])
def test_claude_codes_plugin_counts_when_installed_and_enabled(
        tmp_path, installed, enabled, loaded):
    """What Cursor loads from ~/.claude, by any marketplace's name (#204)."""
    _claude_code(tmp_path, installed, enabled)
    check(f"{installed} {enabled}", plugin.in_claude_code(tmp_path) is loaded,
          plugin.in_claude_code(tmp_path))


def test_no_claude_code_is_no_claude_code_plugin(tmp_path):
    check("nothing in ~/.claude", plugin.in_claude_code(tmp_path) is False, tmp_path)


# --- the command -----------------------------------------------------------------------

def test_the_command_says_where_and_what_to_do_next(home, capsys):
    _an_older_copy(home)
    assert main(["plugin", "cursor"]) == 0
    out = capsys.readouterr().out
    check("where, and in place of which release",
          f"copied the plugin of echolot {echolot.__version__} into "
          "~/.cursor/plugins/local/echolot, in place of 0.14.1's" in out, out)
    check("Cursor loads it after a restart", "restart Cursor" in out, out)
    check("no word of Claude Code without its plugin", "Claude Code" not in out, out)


def test_the_command_names_claude_codes_plugin_when_cursor_loads_both(home, capsys):
    _claude_code(home, ["echolot@echolot"], {"echolot@echolot": True})
    assert main(["plugin", "cursor"]) == 0
    out = capsys.readouterr().out
    check("one of the two is enough", "one of the two is enough" in out, out)
    check("and how to keep Claude Code's",
          "delete ~/.cursor/plugins/local/echolot" in out, out)


def test_a_refusal_is_a_sentence_and_exit_2(tmp_path, monkeypatch, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setattr(plugin, "_home", lambda: bare)
    assert main(["plugin", "cursor"]) == 2
    err = capsys.readouterr().err
    check("the reason, as an error line", err.startswith("error: there is no ~/.cursor"),
          err)


def test_cursors_sandbox_is_named_when_it_refuses(home, monkeypatch, capsys):
    """Run by an agent inside Cursor's sandbox, which lets a command write in
    the project only (#204)."""
    def refused(home=None):
        raise PermissionError(1, "Operation not permitted", str(home))

    monkeypatch.setattr(plugin, "install", refused)
    monkeypatch.setenv("CURSOR_SANDBOX", "seatbelt")
    assert main(["plugin", "cursor"]) == 2
    err = capsys.readouterr().err
    check("the sandbox is named", "Cursor's sandbox did not let echolot write" in err, err)
    check("and the ways out", "in a terminal" in err and "outside the sandbox" in err, err)

    monkeypatch.delenv("CURSOR_SANDBOX")
    assert main(["plugin", "cursor"]) == 2
    err = capsys.readouterr().err
    check("outside Cursor, the error as it is", "sandbox" not in err
          and "Operation not permitted" in err, err)


def test_alone_it_says_how_each_agent_installs_the_plugin(home, capsys):
    assert main(["plugin"]) == 0
    out = capsys.readouterr().out
    for line in ("claude plugin install echolot@echolot", "codex plugin add echolot@echolot",
                 "echolot plugin cursor"):
        check(f"says `{line}`", line in out, out)
    check("no copy, no word of one", "cursor:" not in out, out)


# --- a copy from another release -----------------------------------------------------

def test_no_copy_or_this_releases_copy_says_nothing(home):
    check("no copy", plugin.stale_line(home) is None, plugin.stale_line(home))
    plugin.install(home)
    check("this release's copy", plugin.stale_line(home) is None, plugin.stale_line(home))


def test_an_older_copy_is_said_with_the_command_that_replaces_it(home):
    _an_older_copy(home)
    line = plugin.stale_line(home) or ""
    check("both releases", f"from echolot 0.14.1, and this echolot is "
          f"{echolot.__version__}" in line, line)
    check("and the command", "`echolot plugin cursor`, then restart Cursor" in line, line)


def test_a_copy_whose_version_cannot_be_read_is_said_too(home):
    copy = _an_older_copy(home)
    (copy / ".cursor-plugin" / "plugin.json").write_text("{", encoding="utf-8")
    line = plugin.stale_line(home) or ""
    check("said, without a number", "whose version cannot be read" in line, line)


def test_status_and_doctor_say_it_and_doctor_quiet_keeps_its_lines(
        home, monkeypatch, tmp_path, capsys):
    """`echolot` is what the plugin's door runs first, and `doctor` is the
    full account. `doctor -q` keeps the lines its help counts."""
    _an_older_copy(home)
    monkeypatch.setattr(selftest, "run", lambda tp_binary=None: [("a check", None)])
    project = tmp_path / "app"
    project.mkdir()
    os.chdir(project)

    assert main([]) == 0
    out = capsys.readouterr().out
    line = next((ln for ln in out.splitlines() if ln.startswith("cursor")), "")
    check("`echolot` has a cursor line", "from echolot 0.14.1" in line, out)

    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    check("`doctor` has a section", "## The plugin in Cursor" in out
          and "from echolot 0.14.1" in " ".join(out.split()), out)

    assert main(["doctor", "-q"]) == 0
    out = capsys.readouterr().out
    check("`doctor -q` does not", "0.14.1" not in out, out)
