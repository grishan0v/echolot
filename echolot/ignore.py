"""What echolot leaves in a project that must not reach the history.

Two things, and the documentation has described both as gitignored from the
first commit — `.echolot/` in the project layout, `local.yml` next to it,
"device serials, binary path; in .gitignore". Nothing ever put them there.

A trace is tens of megabytes and a collect writes five of them, so the first
`git add -A` after a run stages eighty megabytes of binary that nobody wants
in a repository, and the person notices when git does. `local.yml` is the
other half: a device serial and a path to somebody's own trace_processor
binary, machine-local by definition and wrong for everyone else.

So `init` writes the two lines. Appended, never rewritten — a .gitignore is
the project's file, and the same rule holds here as for settings.json. Only at
a git root; anywhere else it prints them instead of writing them.
"""

from __future__ import annotations

from pathlib import Path

# Trailing slash on the directory, none on the file: what a person would have
# typed. Both are anchored by the leading slash to the directory of the
# .gitignore that holds them, the project's root — a `local.yml` inside some
# vendored library is not ours to hide.
PATTERNS = ("/.echolot/", "/local.yml")
HEADER = "# echolot: traces and the machine-local config"


def _covers(line: str, pattern: str) -> bool:
    """Whether an existing .gitignore line already ignores this pattern.

    Compared with the decorations stripped, because `.echolot/`, `/.echolot`
    and `**/.echolot/` are the same intent written three ways, and adding a
    fourth spelling of a rule that is already there is noise in someone
    else's file. A negation (`!local.yml`) is deliberate and is left to win:
    the project has said it wants that file tracked.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return False
    body = pattern.strip("/")
    stripped = line.removeprefix("!").removeprefix("**/").strip("/")
    return stripped == body


def missing(text: str) -> list[str]:
    lines = text.splitlines()
    return [p for p in PATTERNS
            if not any(_covers(line, p) for line in lines)]


def _lines(patterns) -> str:
    """The patterns as `init` prints them for a person to copy, one a line."""
    return "\n".join(f"      {p}" for p in patterns)


def ensure(project: Path) -> str | None:
    """Add the two patterns to the project's .gitignore.

    Returns what `init` prints, or None when both patterns are already
    covered. Written only at a git root — `.git` in any form, a directory in
    a clone, a file in a worktree — because anywhere else a .gitignore is a
    file nobody asked for in a directory that may not be under git at all.

    Not written is said, with the lines. It used to be silent: `init` in an
    app that lives in a subdirectory of its repository printed nothing, and
    nothing then stood between the first `git add -A` after a collect and
    the traces. The patterns are anchored, so the place for them is a
    .gitignore in this directory — git reads one at every level — and not
    the repository's own.
    """
    path = project / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        # Appending to a file that could not be read would write it anew.
        return ("· .gitignore could not be read — left alone. Add these lines "
                "to it by hand:\n" + _lines(PATTERNS))
    want = missing(text)
    if not want:
        return None
    if not (project / ".git").exists():
        return ("· .gitignore not written — this directory is not a git root. "
                "If a\n    repository above tracks it, add these lines to a "
                ".gitignore here:\n" + _lines(want))
    block = "\n".join([HEADER, *want]) + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        block = "\n" + block
    path.write_text(text + block, encoding="utf-8")
    return f"{'+' if not text else '↑'} .gitignore ({', '.join(want)})"
