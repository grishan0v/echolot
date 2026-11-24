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
is a file, and the exclude file lives in the repository it points to. That
one file is read in every checkout of the repository, so a private install
in one checkout and `init` in another share the block: `--shared` leaves it
while another checkout's private install needs it, and a private `init`
keeps the other installs' paths in it (#608).
"""

from __future__ import annotations

import re
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


_SPECIAL = re.compile(r"([\\\[\]*?])")


def escaped(rel: str) -> str:
    """A path as an ignore line names it, character for character.

    In an ignore file `[v2]` is a class matching `v` or `2`, `*` and `?` are
    wildcards, and trailing spaces are dropped. A project in `android [v2]`
    had its whole private install in plain sight of git while `init` said it
    was hidden (#738). A backslash makes each of them literal. The line
    starts with `/`, so a leading `#` or `!` never reaches the start of it.
    """
    rel = _SPECIAL.sub(r"\\\1", rel)
    stripped = rel.rstrip(" ")
    return stripped + "\\ " * (len(rel) - len(stripped))


@dataclass(frozen=True)
class Repo:
    """The repository a project sits in, as far as a private install needs it."""

    top: Path        # the working tree's top, where the patterns are anchored
    exclude: Path    # its info/exclude, wherever git keeps it
    project: Path    # the project, at the top or under it

    def pattern(self, path: Path, directory: bool = False) -> str:
        """`path` as a line of the exclude file: anchored, so it names this file and no other."""
        rel = escaped(path.resolve().relative_to(self.top).as_posix())
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


def checkouts(repo: Repo) -> list[Path]:
    """The other checkouts of this repository: the main one and every linked
    worktree but this one.

    Git keeps one `info/exclude` for all of them, in the common git
    directory, and has no exclude file of a worktree's own. The patterns are
    anchored at each checkout's top, so echolot's block hides the same paths
    in every checkout at once (#608).
    """
    out = _git(repo.project, "worktree", "list", "--porcelain")
    found = []
    # One block of lines per worktree, a blank line between them; a bare
    # repository's has no checkout to hide anything in.
    for block in (out or "").split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree ") or "bare" in lines:
            continue
        path = Path(lines[0][len("worktree "):])
        try:
            if path.resolve() != repo.top and path.is_dir():
                found.append(path.resolve())
        except OSError:
            continue
    return found


def private_elsewhere(repo: Repo) -> list[Path]:
    """The project as it sits in each other checkout, where `init --private` installed.

    The block in the shared exclude file is what keeps those installs from
    git, so taking it out or writing it anew from here changes them too.
    """
    from . import hosts  # late: the choice each checkout saved
    rel = repo.project.relative_to(repo.top)
    return [top / rel for top in checkouts(repo) if hosts.load_private(top / rel)]


def unseen(repo: Repo, paths: list[Path]) -> list[Path] | None:
    """Which of `paths` git does not ignore, as it reads the ignore files now.

    Asked of git, not of the block: a line written is not a line that
    matches, and `init` and `echolot` said git ignored a private install
    that it saw whole (#738). None when git could not be asked.
    """
    if not paths:
        return []
    try:
        done = subprocess.run(
            ["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=repo.project,
            input="\0".join(str(p) for p in paths) + "\0", capture_output=True,
            text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 0: some are ignored, 1: none is; anything else is git failing.
    if done.returncode not in (0, 1):
        return None
    ignored = {Path(p).resolve() for p in done.stdout.split("\0") if p}
    ignored |= {(repo.project / p).resolve() for p in done.stdout.split("\0") if p}
    return [p for p in paths if p.resolve() not in ignored]


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
