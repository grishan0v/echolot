"""What `scan` read of a repository, kept between two pages of `echolot ui`
(#931).

The start page says what `scan` says: the app, its builds, the benchmark, the
config it would draft. It used to run `scan` at every view, and once more for
every press that asks whether a recording can start. On a repository of about
a million lines that is 9.6 s of CPU on an idle machine and 88 s on a busy
one: pages took 40 to 155 s there, and a press two minutes to start its run,
where every other page answers in a tenth of a second.

What `scan` says comes from a few files: the build scripts, the settings, the
version catalogs, the manifests, `.gitmodules`, the build code under
`buildSrc` and the builds the settings include, and the sources of a
benchmark module. And from
which sources there are at all: a module is a folder with sources under
`src/main`, a benchmark is a source that holds a MacrobenchmarkRule. So the
reading is kept together with a stamp of the tree, and taken again when the
stamp differs:

- a build script, a settings file, a version catalog, a manifest or
  `.gitmodules` changed (its time or its size), or one came or went;
- a file under the build code changed;
- a source of a benchmark module changed;
- a source came or went anywhere.

A source edited in any other module leaves the reading as it is: nothing
`scan` says follows from it, and a change in the code is exactly when the
page is opened again, to check the app once more.

The stamp is one walk of the tree, pruned as `scan`'s own walk is, with a
`stat` of each file that counts by its contents; nothing is read but the
settings, for the builds they include. On a tree of 18,600 files on a
machine under heavy load the walk took 0.4 to 0.7 s where `scan` took 17.7 s.
Left out: a constant a benchmark names from another
module's file, edited there (#516). **Refresh** on the start page forgets the
reading, and the next page takes it anew.

The devices are asked at every page, as they were: a phone comes and goes
without a file changing.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import scan as scan_mod
from .domains import SKIP_DIRS, _is_worktree

# What `scan` reads by name, wherever it is in the tree: `.gitmodules` says
# which modules are another repository's (#1003).
NAMED = frozenset({"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                   "AndroidManifest.xml", ".gitmodules"})
CATALOG = ".versions.toml"
# A file the stamp takes in: one read by name, or a source or a piece of
# build code (`scan._BUILD_CODE_EXT` holds the sources' extensions too).
_EXT = scan_mod._BUILD_CODE_EXT

# A file by its path from the root: the time it was last changed, in
# nanoseconds, and its size, for one that counts by its contents; None for
# one that counts by being there.
Stamp = dict[str, tuple[int, int] | None]


def _wanted(name: str) -> bool:
    return name in NAMED or name.endswith(CATALOG) or name.endswith(_EXT)


def _stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def stamp(root: Path, counts: Callable[[str], bool]) -> Stamp:
    """Every file of the tree `scan` could read, pruned as `scan` prunes
    it, with the time and size of each that `counts` by its contents. The
    rest are only listed: a `stat` of every source doubled the time."""
    out: Stamp = {}
    top = str(root)
    for base, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not _is_worktree(Path(base, d))]
        # Paths as text: on a tree of twenty thousand files `Path` arithmetic
        # took longer than the walk.
        rel = os.path.relpath(base, top).replace(os.sep, "/")
        prefix = "" if rel == "." else rel + "/"
        for name in names:
            if _wanted(name):
                key = prefix + name
                out[key] = _stat(os.path.join(base, name)) if counts(key) else None
    return out


def _under(rel: str, dirs: tuple[str, ...]) -> bool:
    return any(rel.startswith(d + "/") for d in dirs)


def watched(root: Path, facts: scan_mod.Facts | None) -> Callable[[str], bool]:
    """Which files count by their contents, not only by being there: the
    ones read by name, the build code, and a benchmark module's sources."""
    gradle = scan_mod.gradle_dir(root, facts.app if facts else None)
    code = tuple(d.relative_to(root).as_posix() for d in scan_mod.build_code_dirs(root, gradle))
    benchmarks = tuple(str(b["dir"]).strip("/") for b in (facts.benchmarks if facts else ())
                       if b.get("dir") and str(b["dir"]).strip("/") not in ("", "."))

    def counts(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return name in NAMED or name.endswith(CATALOG) or _under(rel, code) or _under(rel, benchmarks)
    return counts


@dataclass
class Kept:
    """`scan`'s reading of one repository, kept while what it read is as it
    was. Called as `scan` is, with the root; it gives a copy of the facts
    each time, with the devices asked anew: a page adds notes to the facts
    it is given, and the next page must not find them there."""
    describe: Callable[[Path], scan_mod.Facts] = lambda root: scan_mod.describe(root, devices=False)
    ask_devices: Callable[[scan_mod.Facts], None] = scan_mod.ask_devices
    clock: Callable[[], float] = time.time
    # When the facts handed out last were read, for the page to say.
    read_at: float | None = None
    # How many times the repository was read, for whoever checks the keeping.
    reads: int = 0
    _root: Path | None = None
    _facts: scan_mod.Facts | None = None
    _stamp: Stamp = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, root: Path) -> scan_mod.Facts:
        with self._lock:
            kept = self._facts if self._root == root else None
            now = stamp(root, watched(root, kept))
            if kept is None or now != self._stamp:
                # The stamp is taken before the reading: a file changed while
                # `scan` reads differs from it, and the next page reads again.
                self._facts, self.read_at = None, None
                kept = self.describe(root)
                # A benchmark module this reading found counts by its
                # sources' contents from now on.
                counts = watched(root, kept)
                for rel, seen in now.items():
                    if seen is None and counts(rel):
                        now[rel] = _stat(str(root / rel))
                self._root, self._facts, self._stamp = root, kept, now
                self.read_at = self.clock()
                self.reads += 1
            facts = copy.deepcopy(kept)
        self.ask_devices(facts)
        return facts

    def forget(self) -> None:
        """Drop the reading: the next page reads the repository anew."""
        with self._lock:
            self._root, self._facts, self._stamp, self.read_at = None, None, {}, None
