"""Whether the code still writes the names a scenario's window is cut by (#500).

An anchor is a slice name in echolot.yml, and nothing held it against
anything before a trace was read: an end anchor the code had stopped writing
two months earlier read as a sound config in `echolot`, `collect -n 5`
recorded five traces, and only `analyze` said that the window was the whole
trace. The sources answer most of that without a phone. A name the app
writes is a string in its code, and `mark.named` already finds the strings
that start with a given text, the way it finds the temporary markers.

A name in no string of the code is no proof that nothing writes it. The
platform and the libraries write sections too, `bindApplication` or
`RV Scroll`, and no file of the project holds them. So the ones echolot knows
are left alone (`PLATFORM`), a name the scenario's last report found in its
recordings is left alone, and the rest is said as a question, with the
command that answers it. The words are firm only where the code says more:
a name it does hold that reads like the anchor (`list_shown` for
`list_shown_mark`), or the prefix of the temporary markers, which only `mark`
and the agent put in.

Nothing here stops a run. The build on the phone is not the code (#393), and
a set recorded under a wrong name is still the scenario's: `analyze` cuts the
window when it reads the traces, so the same set is read again once the name
is right.
"""

from __future__ import annotations

import dataclasses
import difflib
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from .config import NO_ANCHOR, Config, ConfigError

# What `scan` writes for an end anchor a probe has to pick. As a GLOB it
# matches a one-letter name, which no scenario ends on.
UNPICKED = "?"

# The sections the platform and AndroidX write in an app's process, as GLOBs,
# matched against the anchor as it is written. No string of the project holds
# them, and a window is often cut by one: `scan` drafts `bindApplication` for
# a start, and `probe` lists the frames and the Activity's steps among the
# longest slices. The list is short on purpose. A library's section missing
# from it gets a question, not an instruction, and the question goes once a
# report of the scenario has found the name.
PLATFORM = (
    # ActivityThread and LoadedApk: the process's start, each Activity's steps.
    "bindApplication", "makeApplication", "ActivityThreadMain", "PostFork",
    "activity[A-Z]*",            # activityStart, activityResume, activityPause, …
    "perform[A-Z]*:*",           # performCreate:<class> and the rest, Android 12 on
    "reportFullyDrawn*",
    "launching: *", "launchingActivity#*",   # the launch as system_server records it
    # The frame.
    "Choreographer#*", "traversal", "measure", "layout", "draw",
    "Record View#draw()", "DrawFrame*", "animation", "input", "inflate",
    # AndroidX.
    "RV *",                      # RecyclerView
    "Compose:*",
    "Startup",                   # androidx.startup's AppInitializer
)

# Where an anchor's fixed text ends: SQLite's GLOB takes `*`, `?` and `[…]`.
_WILD = re.compile(r"[*?\[]")
# The words of a name, in any of the cases a marker is written in:
# `list_shown`, `ListShown`, `Screen.firstFrame`, `HTTP_READY`.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
# How alike a name has to read to be offered in an anchor's place:
# `list_shown` beside `list_shown_mark` is 0.8, `Screen.loaded` beside
# `Screen.firstFrameMark` 0.47.
NEAR = 0.6


def platform(glob: str) -> bool:
    """Whether the anchor names one of the sections the platform writes."""
    return any(fnmatch.fnmatchcase(glob, p) for p in PLATFORM)


def words(name: str) -> set[str]:
    """The words of a name, lowercased, the short ones left out."""
    return {w.lower() for w in _WORD.findall(name) if len(w) >= 3}


def nearest(glob: str, names, n: int = 3) -> list[str]:
    """The names that read like the anchor, the likeliest first.

    Alike by words, then by spelling: a name shares a word with the anchor
    and reads at least `NEAR` like it. `list_shown` for `list_shown_mark`,
    `Screen.firstFrame` for `Screen.firstFrameMark`.
    """
    text = _WILD.sub(" ", glob).strip()
    mine = words(text)
    if not mine:
        return []
    low = text.lower()
    ranked = []
    for name in set(names):
        shared = mine & words(name)
        if not shared or name == glob:
            continue
        ratio = difflib.SequenceMatcher(None, low, name.lower()).ratio()
        if ratio >= NEAR:
            ranked.append((-len(shared), -ratio, name))
    return [name for *_, name in sorted(ranked)[:n]]


@dataclass(frozen=True)
class Note:
    """One anchor the code may not write, and what the code has instead."""

    scenario: str
    key: str                     # "start" or "end"
    glob: str
    kind: str                    # "unpicked", "temporary", "near" or "unknown"
    near: tuple[tuple[str, str], ...] = ()   # (name, path:line) the code has

    def text(self, named: bool = False) -> str:
        """The finding, in a few words: `named` says whose scenario it is."""
        who = f"{self.scenario}'s {self.key}" if named else self.key
        if self.kind == "unpicked":
            return (f"{who} anchor is `?`, the question `scan` left in the draft: "
                    f"no slice is named that")
        said = (f"{who} anchor `{self.glob}` is a temporary marker no code here "
                f"puts in any more" if self.kind == "temporary" else
                f"{who} anchor `{self.glob}` is in no Kotlin or Java string here")
        if self.near:
            return said + "; the code has " + ", ".join(
                f"`{name}`" + (f" ({where})" if where else "") for name, where in self.near)
        if self.kind == "unknown":
            return (said + ", nor one of the platform's sections echolot knows: "
                          "a library's, or a marker the code no longer writes?")
        return said

    @property
    def sure(self) -> bool:
        """Whether the code says what to write instead, or that nothing will do."""
        return self.kind != "unknown"


def _start(glob: str) -> str:
    """The anchor's text before its first wildcard."""
    m = _WILD.search(glob)
    return glob[:m.start()] if m else glob


def _needle(start: str) -> str | None:
    """What every string that can be the anchor starts with: its first word.

    The text `mark.named` looks for, so that a name that differs from the
    anchor after its first word is read too, to be offered beside it.
    """
    m = re.match(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", start)
    word = m.group() if m else ""
    if len(word) >= 3:
        return word
    return start[:3] if len(start) >= 3 else None


def _held(glob: str, start: str, needle: str, named) -> bool:
    """Whether a string of the code is, or can be put together into, the anchor.

    A name put together at run time counts by its fixed start (`mark.head`):
    `"list_${state}"` can be `list_shown_mark`. The first word alone is no
    such start: `"list"` is a string of every other file.
    """
    if any(fnmatch.fnmatchcase(s, glob) for s in named.exact):
        return True
    whole = start == glob
    return any(h != needle and (start.startswith(h) or (not whole and h.startswith(start)))
               for h in named.heads)


def unwritten(root: Path, globs, prefix: str) -> dict[str, Note] | None:
    """The names no string of the code under `root` is or can be put
    together into, each with what the code has that reads like it.

    A name the platform writes is left out, and so is one with no fixed
    start of three letters to look for: `*_shown` could be anything. The
    notes carry no scenario. None when there is no Kotlin or Java code here
    to hold the names against.
    """
    from . import mark  # mark imports more than this needs at load

    out: dict[str, Note] = {}
    walked: dict[str, object] = {}
    for glob in dict.fromkeys(globs):
        if glob in (NO_ANCHOR, UNPICKED) or platform(glob):
            continue
        start = _start(glob)
        needle = _needle(start)
        if needle is None:
            continue
        if needle not in walked:
            walked[needle] = mark.named(Path(root), needle)
        named = walked[needle]
        if not named.files:
            return None
        if _held(glob, start, needle, named):
            continue
        near = tuple((n, named.where.get(n, "")) for n in nearest(glob, named.exact))
        kind = "temporary" if glob.startswith(prefix) else "near" if near else "unknown"
        out[glob] = Note("", "", glob, kind, near)
    return out


def check(cfg: Config, found: dict[str, set[str]] | None = None,
          scenarios: list[str] | None = None) -> list[Note] | None:
    """Every anchor of the config's scenarios that the code may not write.

    `found` is, for each scenario, the anchors its last report found in the
    recordings: such a name is written by something, the platform or a
    library, and is not asked about. `scenarios` limits the check to those
    named, every scenario by default. None when the config cannot be read
    this far.
    """

    try:
        root = cfg.sources
        prefix = cfg.temp_prefix
        wanted: list[tuple[str, str, str]] = []
        for name in scenarios if scenarios is not None else cfg.scenarios:
            one = Config(cfg.raw, cfg.path, cfg.local_path).select(name)
            if one.scenario_window is not None:
                continue
            wanted += [(name, "start", one.scenario_start), (name, "end", one.scenario_end)]
    except ConfigError:
        return None

    # No code here, a config copied out of the checkout: only `?` is said.
    missing = unwritten(root, [glob for *_, glob in wanted], prefix) or {}
    notes: list[Note] = []
    for name, key, glob in wanted:
        if glob.strip() == UNPICKED:
            notes.append(Note(name, key, glob, "unpicked"))
            continue
        note = missing.get(glob)
        if note is None or (note.kind == "unknown" and glob in (found or {}).get(name, set())):
            continue
        notes.append(dataclasses.replace(note, scenario=name, key=key))
    return notes
