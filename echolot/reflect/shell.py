"""Where a shell line runs a command, and with what words.

`reflect` reads command lines an agent typed, and a word can sit in one in
two ways: as a command the shell runs, or as text one of them is handed. A
regex over the line cannot tell `echolot collect -n 5` from
`echo "next: echolot collect -n 5"`, a `sed -n '/echolot report/p'`, or
`gh issue view 5 -R owner/echolot`; on one real session about seventeen
"calls" in eighty-one were strings (#529).

So the line is split the way a shell splits it, as far as reflect needs: words
with their quotes taken off, simple commands at `;`, `&&`, `||`, `|`, `&`,
newlines and parentheses, and the commands inside `$(…)` and backticks —
inside double quotes too, where they still run. A command's position is its
first word after assignments (`FOO=1`) and the wrappers that run what follows
them (`time`, `nohup`, `env`, `uv run`, …). It is no shell: no expansion, no
aliases, and heredoc bodies are the caller's to remove first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?=")
# Commands that run the command after them, with options of their own first.
_WRAPPERS = {"time", "nohup", "exec", "command", "builtin", "noglob", "sudo", "env",
             "nice", "caffeinate", "stdbuf", "uvx", "!"}
_TAKES_A_VALUE = {"timeout": 1, "gtimeout": 1}
_RUNNERS = {"uv": "run", "pipx": "run", "poetry": "run", "pdm": "run", "hatch": "run"}
_OPERATORS = set(";&|\n()")


@dataclass(frozen=True)
class Word:
    value: str        # quotes and escapes taken off
    start: int        # where it starts in the text read, quotes included
    end: int


@dataclass(frozen=True)
class Command:
    words: tuple[Word, ...]
    end: int          # where the command ends in the text: its operator or the end


def _matching(text: str, i: int, open_: str, close: str) -> int:
    """The index just past the `close` that matches the `open_` at i - 1,
    skipping quoted stretches; the end of the text when there is none."""
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and open_ != "`":
            j = text.find("'", i + 1)
            i = len(text) if j < 0 else j + 1
            continue
        if ch == close and (open_ != close or depth == 1):
            depth -= 1
            if depth == 0:
                return i + 1
        elif ch == open_ and open_ != close:
            depth += 1
        i += 1
    return len(text)


@lru_cache(maxsize=8192)
def commands(text: str, offset: int = 0) -> tuple[Command, ...]:
    """The simple commands of a line, those of its substitutions included, in
    the order the shell starts them (a substitution before the command it
    sits in)."""
    out: list[Command] = []
    words: list[Word] = []
    value: list[str] = []
    start: int | None = None
    i = 0
    n = len(text)

    def end_word(at: int) -> None:
        nonlocal start
        if start is not None:
            words.append(Word("".join(value), offset + start, offset + at))
            value.clear()
            start = None

    def end_command(at: int) -> None:
        end_word(at)
        if words:
            out.append(Command(tuple(words), offset + at))
            words.clear()

    def substitution(inner_start: int, inner_end: int) -> None:
        out.extend(commands(text[inner_start:inner_end], offset + inner_start))

    while i < n:
        ch = text[i]
        if ch in " \t":
            end_word(i)
            i += 1
        elif ch == "#" and start is None:
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif ch in _OPERATORS:
            end_command(i)
            i += 1
        elif ch == "\\":
            start = i if start is None else start
            if i + 1 < n and text[i + 1] != "\n":
                value.append(text[i + 1])
            i += 2
        elif ch == "'":
            start = i if start is None else start
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            value.append(text[i + 1:j])
            i = j + 1
        elif ch == '"':
            start = i if start is None else start
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    value.append(text[i + 1])
                    i += 2
                elif text.startswith("$(", i):
                    j = _matching(text, i + 2, "(", ")")
                    substitution(i + 2, j - 1)
                    value.append(text[i:j])
                    i = j
                elif text[i] == "`":
                    j = _matching(text, i + 1, "`", "`")
                    substitution(i + 1, j - 1)
                    value.append(text[i:j])
                    i = j
                else:
                    value.append(text[i])
                    i += 1
            i += 1
        elif text.startswith("$(", i):
            start = i if start is None else start
            j = _matching(text, i + 2, "(", ")")
            substitution(i + 2, j - 1)
            value.append(text[i:j])
            i = j
        elif ch == "`":
            start = i if start is None else start
            j = _matching(text, i + 1, "`", "`")
            substitution(i + 1, j - 1)
            value.append(text[i:j])
            i = j
        else:
            start = i if start is None else start
            value.append(ch)
            i += 1
    end_command(n)
    return tuple(out)


def head(cmd: Command) -> int | None:
    """The index of the word that names what runs, past assignments and
    wrappers; None when there is none (`FOO=1` alone)."""
    w = cmd.words
    i = 0
    while i < len(w):
        v = w[i].value
        if _ASSIGNMENT.match(v):
            i += 1
        elif v in _WRAPPERS:
            i += 1
            while i < len(w) and (w[i].value.startswith("-") or _ASSIGNMENT.match(w[i].value)):
                i += 1
        elif v in _TAKES_A_VALUE:
            i += 1
            while i < len(w) and w[i].value.startswith("-"):
                i += 1
            i += _TAKES_A_VALUE[v]
        elif v in _RUNNERS and i + 1 < len(w) and w[i + 1].value == _RUNNERS[v]:
            i += 2
            while i < len(w) and w[i].value.startswith("-"):
                i += 1
        else:
            return i
    return None


def name(word: str) -> str:
    """A command word as its program: `~/.local/bin/echolot` is `echolot`."""
    return word.rstrip("/").rsplit("/", 1)[-1]


@lru_cache(maxsize=8192)
def runs(text: str) -> tuple[tuple[Command, int, str], ...]:
    """Every simple command, the index of its program word, and the text its
    positions are in — the line's, or for what `bash -c '…'` and its kind
    run, that string's."""
    out: list[tuple[Command, int, str]] = []
    for cmd in commands(text):
        h = head(cmd)
        if h is None:
            continue
        out.append((cmd, h, text))
        prog = name(cmd.words[h].value)
        if prog in ("bash", "sh", "zsh", "dash") and h + 2 < len(cmd.words) \
                and cmd.words[h + 1].value == "-c":
            out += runs(cmd.words[h + 2].value)
    return tuple(out)
