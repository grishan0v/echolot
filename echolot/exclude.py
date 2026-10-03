"""A private install: what `init --private` writes, kept out of git's sight.

`init` was built for a team that commits its layer. The other common case is
a person trying echolot on a repository the team shares, or one a client
owns, before anyone has agreed to add a tool to it — and `init` left that
person a diff: the layer's files, a pointer for every other agent, two lines
in `.gitignore`, a permission in `.claude/settings.json`. All of it one
`git add -A` away from a commit, the hazard `ignore.py` closed for the traces.

A private install writes the same files and adds every path it wrote to the
clone's own ignore file, `info/exclude`, which git reads like a `.gitignore`
and never commits. It writes nothing to a file git tracks: an exclude file
hides untracked files only, and a change to a tracked one shows regardless.

The paths sit in one block between two marker lines, written from the top of
the repository, since the project may live in a subdirectory of it. Every
private `init` writes the block anew, so a file a later release stops
installing drops out of it; everything outside the markers is the person's
own and is never touched. Where the file is, git says: in a worktree `.git`
is a file, and the exclude file lives in the repository it points to.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

BEGIN = "# >>> echolot: a private install — `echolot init --shared` takes this block out"
END = "# <<< echolot"
# A block is found by the start of its first line, so a later release can
# word the line differently and still find the blocks earlier ones wrote.
_OPENS = "# >>> echolot"


class BrokenBlock(Exception):
    """A block whose end marker is gone: where echolot's lines stop is unknown."""


@dataclass(frozen=True)
class Repo:
    """The repository a project sits in, as far as a private install needs it."""

    top: Path        # the working tree's top, where the patterns are anchored
    exclude: Path    # its info/exclude, wherever git keeps it
    project: Path    # the project, at the top or under it

    def pattern(self, path: Path, directory: bool = False) -> str:
        """`path` as a line of the exclude file: anchored, so it names this file and no other."""
        rel = path.resolve().relative_to(self.top).as_posix()
        return f"/{rel}/" if directory else f"/{rel}"

    def show(self) -> str:
        """The exclude file as `init` names it: from the project when it is near."""
        try:
            return self.exclude.relative_to(self.project).as_posix()
        except ValueError:
            return str(self.exclude)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def find(project: Path) -> Repo | None:
    """The repository `project` is in; None outside one, or with no git to ask."""
    out = _git(project, "rev-parse", "--show-toplevel", "--git-path", "info/exclude")
    lines = (out or "").splitlines()
    if len(lines) < 2:
        return None
    exclude = Path(lines[1])
    if not exclude.is_absolute():
        exclude = project / exclude
    return Repo(top=Path(lines[0]).resolve(), exclude=exclude.resolve(),
                project=project.resolve())


def tracked(repo: Repo, paths: list[Path]) -> set[Path]:
    """Which of `paths` git tracks: the files a private install may not write."""
    if not paths:
        return set()
    out = _git(repo.project, "ls-files", "-z", "--full-name", "--",
               *(str(p) for p in paths))
    named = {repo.top / name for name in (out or "").split("\0") if name}
    return {p for p in paths if p.resolve() in named}


def _span(text: str) -> tuple[int, int] | None:
    """Where echolot's block is in the file: from its first line to past its last.

    None when there is none. A block that opens and never closes raises
    BrokenBlock: the lines after its opening may be the person's own, and
    writing a new block there would put an end marker under them, so the
    next rewrite would take them away with echolot's.
    """
    start = 0 if text.startswith(_OPENS) else text.find("\n" + _OPENS) + 1
    if start == 0 and not text.startswith(_OPENS):
        return None
    end = text.find("\n" + END, start)
    if end < 0:
        raise BrokenBlock
    end += len(END) + 1
    if text[end:end + 1] == "\n":
        end += 1
    return start, end


def _broken(repo: Repo) -> str:
    return (f"! {repo.show()} has echolot's block without its end line — left "
            f"alone, and nothing was kept from git. Put `{END}` on its own line "
            f"where the block ends, or delete the block, then run `echolot init` "
            f"again.")


def read(repo: Repo) -> list[str] | None:
    """The block's lines, or None when there is no block."""
    try:
        text = repo.exclude.read_text(encoding="utf-8")
        span = _span(text)
    except (OSError, BrokenBlock):
        return None
    if span is None:
        return None
    return text[span[0]:span[1]].splitlines()[1:-1]


def write(repo: Repo, patterns: list[str]) -> str:
    """Put the block in place, every pattern once; returns what `init` prints."""
    try:
        text = repo.exclude.read_text(encoding="utf-8") if repo.exclude.exists() else ""
    except OSError as e:
        return f"! {repo.show()} could not be read ({e.strerror}) — nothing was kept from git"
    kept = list(dict.fromkeys(patterns))
    block = "\n".join([BEGIN, *kept, END]) + "\n"
    try:
        span = _span(text)
    except BrokenBlock:
        return _broken(repo)
    if span is not None:
        new = text[:span[0]] + block + text[span[1]:]
    else:
        new = text + ("\n" if text and not text.endswith("\n") else "") + block
    if new == text:
        return f"= {repo.show()} ({len(kept)} paths kept from git, current)"
    try:
        repo.exclude.parent.mkdir(parents=True, exist_ok=True)
        repo.exclude.write_text(new, encoding="utf-8")
    except OSError as e:
        return f"! {repo.show()} could not be written ({e.strerror}) — nothing was kept from git"
    return f"{'↑' if span else '+'} {repo.show()} ({len(kept)} paths kept from git)"


def remove(repo: Repo) -> bool:
    """Take echolot's block out of the exclude file. True when there was one;
    a block without its end line is left for a person, as `write` leaves it."""
    try:
        text = repo.exclude.read_text(encoding="utf-8")
        span = _span(text)
    except (OSError, BrokenBlock):
        return False
    if span is None:
        return False
    repo.exclude.write_text(text[:span[0]] + text[span[1]:], encoding="utf-8")
    return True
