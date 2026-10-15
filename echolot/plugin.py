"""The plugin echolot carries, and its copy in Cursor (#399).

Claude Code and Codex install the plugin from the marketplace in this
repository, at a release's tag. For one person, Cursor installs nothing from
a repository: its own marketplace reviews every plugin by hand, and
cursor.directory copies one skill's text at a time (#388). What Cursor does
load is its folder for local plugins, `~/.cursor/plugins/local`, and the
plugins Claude Code has installed and enabled (#204).

`echolot plugin cursor` copies the plugin into that folder from the files the
package carries, so the copy is the installed echolot's, with no git and no
network. A copy does not follow `pipx upgrade echolot`, so `echolot` and
`doctor` say when the one there is from another release.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from . import __version__

NAME = "echolot"

# What a copy leaves out: macOS's folder notes, and Python's caches, which a
# checkout's `plugins/echolot` never needs and a wheel never has.
_LEFT_OUT = shutil.ignore_patterns(".DS_Store", "__pycache__")


class Refused(Exception):
    """Why the copy was not made, as a sentence: nothing was written."""


def _home() -> Path:
    """Where Cursor and Claude Code keep their folders.

    A function so that the tests point it at an empty directory: `echolot`
    and `doctor` read Cursor's folder, and a test must not read whatever the
    machine running it has installed.
    """
    return Path.home()


def source() -> Path | None:
    """The plugin's files: inside the package, else beside it in a checkout.

    A wheel carries them as `echolot/plugin`, put there by setup.py. A
    checkout has `plugins/echolot` next to the package and no copy, and that
    is where an editable install and `python -m echolot` read them. None when
    neither holds Cursor's manifest: an install that lost its files.
    """
    package = Path(__file__).resolve().parent
    for where in (package / "plugin", package.parent / "plugins" / NAME):
        if (where / ".cursor-plugin" / "plugin.json").is_file():
            return where
    return None


def cursor_copy(home: Path | None = None) -> Path:
    """Where the copy goes: `~/.cursor/plugins/local/echolot`.

    Cursor's docs give this one path. On Windows `~` is the user's profile
    folder, and nobody has checked there that Cursor reads it.
    """
    return (home or _home()) / ".cursor" / "plugins" / "local" / NAME


def shown(path: Path, home: Path | None = None) -> str:
    """A path under the home directory as `~/…`, the way the docs write it."""
    try:
        return "~/" + path.relative_to(home or _home()).as_posix()
    except ValueError:
        return str(path)


def _manifest(folder: Path, kind: str) -> dict | None:
    try:
        data = json.loads((folder / kind / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def version_of(folder: Path) -> str | None:
    """The release a plugin folder is from, by Cursor's manifest in it."""
    version = (_manifest(folder, ".cursor-plugin") or {}).get("version")
    return version if isinstance(version, str) else None


def _is_ours(folder: Path) -> bool:
    """Whether a folder holds echolot's plugin, by either manifest's name.

    An empty folder counts: there is nothing in it to lose.
    """
    if not any(folder.iterdir()):
        return True
    return any((_manifest(folder, kind) or {}).get("name") == NAME
               for kind in (".cursor-plugin", ".claude-plugin"))


def in_claude_code(home: Path | None = None) -> bool:
    """Whether Claude Code has an echolot plugin installed and enabled.

    Cursor loads those as well. Its log names each plugin it loads from
    `~/.claude/plugins/installed_plugins.json` that `enabledPlugins` in
    `~/.claude/settings.json` turns on, and skips one turned off there (#204).
    Read in `~/.claude`, where Cursor reads, by any marketplace's name:
    `echolot@echolot` from this repository, or a catalog's.
    """
    claude = (home or _home()) / ".claude"
    try:
        installed = json.loads((claude / "plugins" / "installed_plugins.json")
                               .read_text(encoding="utf-8"))
        settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    plugins = installed.get("plugins") if isinstance(installed, dict) else None
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if not isinstance(plugins, dict) or not isinstance(enabled, dict):
        return False
    return any(key.split("@")[0] == NAME and enabled.get(key) is True
               for key in plugins)


def stale_line(home: Path | None = None) -> str | None:
    """For `echolot` and `doctor`: the copy in Cursor, when it is another
    release's. None when there is no copy, or it is this echolot's.

    A link is someone's own arrangement, a checkout linked in to work on the
    plugin, and is left out.
    """
    copy = cursor_copy(home)
    if copy.is_symlink() or not copy.is_dir():
        return None
    version = version_of(copy)
    if version == __version__:
        return None
    what = (f"from echolot {version}" if version
            else "from an echolot whose version cannot be read")
    return (f"the plugin in {shown(copy, home)} is {what}, and this echolot is "
            f"{__version__} → `echolot plugin cursor`, then restart Cursor")


def install(home: Path | None = None) -> dict:
    """Copy this echolot's plugin into Cursor's folder for local plugins.

    The copy replaces one from an earlier release whole, so a file a release
    dropped does not stay behind. It is made beside the old one and swapped
    in by renaming, so a failure halfway leaves the old copy where it was.

    Refused, with nothing written, when the package carries no plugin, when
    there is no `~/.cursor` (Cursor makes it on its first run), and when the
    place holds a link or anything that is not echolot's plugin: what is
    there is someone's, and echolot does not delete it.

    Returns what the caller says: where the copy went, the release it
    replaced (None for no copy, or one with no version), and whether
    Claude Code's plugin is loaded in Cursor as well.
    """
    home = home or _home()
    plugin = source()
    if plugin is None:
        raise Refused("this echolot carries no plugin: its install lost files. "
                      "`pipx reinstall echolot` puts them back")
    cursor = home / ".cursor"
    if not cursor.is_dir():
        raise Refused(f"there is no {shown(cursor, home)} here. Cursor makes it the "
                      f"first time it runs: install Cursor, open it once, and run "
                      f"this again")
    copy = cursor_copy(home)
    if copy.is_symlink():
        raise Refused(f"{shown(copy, home)} is a link to {os.readlink(copy)}, and "
                      f"echolot leaves it as it is. Remove the link to put a copy "
                      f"there")
    if copy.exists() and not (copy.is_dir() and _is_ours(copy)):
        raise Refused(f"{shown(copy, home)} holds something other than echolot's "
                      f"plugin, and echolot leaves it as it is")
    replaced = copy.exists()
    before = version_of(copy) if replaced else None

    copy.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".echolot-", dir=copy.parent))
    try:
        fresh = staging / NAME
        shutil.copytree(plugin, fresh, ignore=_LEFT_OUT)
        if replaced:
            copy.rename(staging / "old")
        try:
            fresh.rename(copy)
        except OSError:
            if replaced:
                (staging / "old").rename(copy)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"copy": copy, "replaced": replaced, "before": before,
            "claude_code": in_claude_code(home)}
