"""Which project a run works on: decided once, before the command starts.

Every command used to answer this itself, each by its own rule: the config's
folder, the working directory, `echolot.yml` up the tree stopping at `.git`
or not stopping at all, `--root` or not. A command run from `app/` and the
run log of the same command then disagreed about which project it was (#830).

`resolve` answers it once, in `main()`, and every command and the run log
read the answer:

1. the config `-c` names, if it names one;
2. else the nearest `echolot.yml` from here upwards, stopping at the top of
   the git checkout, the way git finds `.git`;
3. else here, as a project not set up yet.

`reflect --project` and `init --into` name the project folder itself, and its
`echolot.yml` is the config.

The checkout the sources are read from is one more answer: `--root`, else
`project.root`, else the config's folder.
"""

from __future__ import annotations

import argparse
import contextlib
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePath

CONFIG = "echolot.yml"


@dataclass(frozen=True)
class Project:
    """The project a run works on, and how it was found.

    `found` is how: `named` (-c), `folder` (`--project` / `--into`), `here`
    (`echolot.yml` in the working directory), `above` (in a folder above it)
    or `none` (nowhere: the working directory, not set up yet).
    """

    folder: Path            # the project: where `.echolot/` and the run log live
    config: Path            # its config; not there before setup, or when -c names a typo
    found: str
    here: Path              # where the command was run from
    root_flag: Path | None = None   # `--root`, as given
    local: Path | None = None       # `--local`, as given

    @property
    def echolot(self) -> Path:
        """`.echolot/`: the investigation, the reports, the run log."""
        return self.folder / ".echolot"

    @property
    def set_up(self) -> bool:
        """A config is there to read."""
        return self.config.is_file()

    @property
    def named(self) -> bool:
        """`-c` named the config."""
        return self.found == "named"

    @cached_property
    def root(self) -> Path:
        """The checkout the sources are read from: `--root`, else
        `project.root` from the config's folder, else the config's folder.

        A config that does not load names no `project.root`: the command that
        reads it says why, and this is the config's folder meanwhile.
        """
        if self.root_flag is not None:
            return self.root_flag.resolve()
        home = self.config.parent if self.set_up else self.folder
        if not self.set_up:
            return home
        from .config import Config
        with contextlib.suppress(Exception):
            return Config.load(self.config, self.local).sources
        return home

    def shown(self, path: Path | None = None) -> str:
        """`path` (the config by default) as a person types it from here:
        `echolot.yml`, `../echolot.yml`; absolute on another drive."""
        path = self.config if path is None else path
        try:
            return PurePath(os.path.relpath(path, self.here)).as_posix()
        except (OSError, ValueError):
            return path.as_posix()


def _given(value, here: Path) -> Path | None:
    """A path flag as given, from `here`."""
    return here / Path(value).expanduser() if value not in (None, "") else None


def nearest(here: Path) -> Path | None:
    """The `echolot.yml` of `here` or the nearest folder above it that holds
    one, up to the top of the git checkout `here` is in."""
    for folder in (here, *here.parents):
        found = folder / CONFIG
        with contextlib.suppress(OSError):
            if found.is_file():
                return found
        with contextlib.suppress(OSError):
            if (folder / ".git").exists():
                return None
    return None


def resolve(args: argparse.Namespace | None = None, here: Path | None = None) -> Project:
    """The project `args` work on, from `here` (the working directory)."""
    args = args if args is not None else argparse.Namespace()
    here = (here or Path.cwd()).resolve()
    root_flag = _given(getattr(args, "root", None), here)
    local = _given(getattr(args, "local", None), here)
    config = getattr(args, "config", None)
    named = config not in (None, "", CONFIG)

    folder = None
    for flag in ("project", "into"):        # reflect --project, init --into
        value = getattr(args, flag, None)
        if isinstance(value, str) and value:
            folder = (here / Path(value).expanduser()).resolve()
    if named:
        path = (here / Path(config).expanduser()).resolve()
        if folder is None:
            folder = path.parent if path.is_file() else here
        return Project(folder, path, "named", here, root_flag, local)
    if folder is not None:
        return Project(folder, folder / CONFIG, "folder", here, root_flag, local)
    found = nearest(here)
    if found is None:
        return Project(here, here / CONFIG, "none", here, root_flag, local)
    return Project(found.parent, found, "here" if found.parent == here else "above",
                   here, root_flag, local)


def notices(project: Project) -> list[str]:
    """What the project's config holds that is going away, a line each.

    `project.source_root` is read by no command: placing a row, the leftover
    check and `mark --remove` walk the whole checkout, which `project.root`
    names when it is not the config's folder. Three keys called "root" with
    three meanings was one of the ways a command chose its own (#830). Said
    in this release, refused in the next.
    """
    if not project.set_up:
        return []
    from .config import _load_yaml
    try:
        raw = _load_yaml(project.config)
    except Exception:       # the command that reads it says why
        return []
    section = raw.get("project") if isinstance(raw, dict) else None
    if not isinstance(section, dict) or "source_root" not in section:
        return []
    return [f"[!] {project.shown()}: project.source_root is read by no command, and "
            f"the next release refuses it. Take the line out; project.root names the "
            f"checkout when it is not the config's folder."]
