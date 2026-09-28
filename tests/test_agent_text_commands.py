"""Every `echolot` command the agent text tells a model to run, run past the parser.

The skill, the hunter, the three commands and the printed guide are
instructions a model follows literally. When one of them names a flag the CLI
does not have, nothing fails where the text lives: the agent types it, argparse
refuses it, and the session spends a turn — or a whole route — finding out
that the page was wrong. Most of what the 2026-09-28 audit found in the agent
layer was drift of that kind, sitting in text no test read.

So the invocations are read out of the text, the way a model reads them —
inside inline code and fenced blocks — and each one is checked against
`main.build_parser()`: the verb has to be a subcommand, and every flag it uses
has to be one that subcommand accepts. Placeholders (`<n>`, `"<question>"`,
`…`) are values, not flags, and pass through. One value is checked as well,
the topic after `echolot guide`, because a topic that does not exist is the
same dead end as a flag that does not. What this does not check is whether
other values make sense, or whether prose outside code names a real flag; the
text is held to what it puts in front of the agent to copy.

Run over the text as it stood when this was written, it found nothing: that
drift was in what the commands meant, not in how they were spelled. It is
here for the next rename, which the text would otherwise learn about from a
session.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from echolot.layer import CLAUDE_DIR, GUIDE_DIR
from echolot.main import build_parser

ROOT = Path(__file__).resolve().parent.parent

# The text an agent is handed: what `init` installs for Claude Code — the
# references included, which the hunter reads instead of working the schema
# out — and what `echolot guide` prints for every other client.
SOURCES = sorted(
    [CLAUDE_DIR / "skills" / "echolot" / "SKILL.md",
     *(CLAUDE_DIR / "skills" / "echolot" / "references").glob("*.md"),
     CLAUDE_DIR / "agents" / "perf-hunter.md",
     *(CLAUDE_DIR / "commands").glob("*.md"),
     *GUIDE_DIR.glob("*.md")])

FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)
SPAN = re.compile(r"`([^`]+)`")
# `echolot <verb>`, and not `/echolot <arg>` (the slash command, whose words
# are not all verbs), `echolot-hunt` (a command file) or `.echolot/` (a path).
CALL = re.compile(r"(?<![/\w.-])echolot[ \t]+([a-z][a-z-]*)")
QUOTED = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")
# Where a command stops and the words around it begin: the column gap in a
# cheat sheet, a comment, an arrow, a dash, a clause of prose.
END = re.compile(r"\s{2,}|\s#|→|—|;|,|\||&&|`|\)|\s\(|$")
FLAG = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")


def _calls(text: str) -> list[tuple[int, str, str]]:
    """(line, verb, the command's own words) for every invocation in code."""
    found: list[tuple[int, str, str]] = []

    def scan(chunk: str, offset: int) -> None:
        for m in CALL.finditer(chunk):
            line = text.count("\n", 0, offset + m.start()) + 1
            # Quotes first, so a comma inside "3 s, now 7 s" does not end the
            # command before its `--since`.
            rest = QUOTED.sub("Q", chunk[m.end():].split("\n", 1)[0])
            words = rest[:END.search(rest).start()]
            found.append((line, m.group(1), words))

    fenced = [(f.start(1), f.group(1)) for f in FENCE.finditer(text)]
    for start, body in fenced:
        scan(body, start)
    # Inline code is looked for outside the fences only, and a span may be
    # broken across two lines of the paragraph — markdown joins them.
    prose = FENCE.sub(lambda f: "\n" * f.group(0).count("\n"), text)
    for s in SPAN.finditer(prose):
        scan(s.group(1).replace("\n", " "), s.start(1))
    return found


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    root = build_parser()
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("no subcommands registered")


def _accepted(parser: argparse.ArgumentParser) -> set[str]:
    # `--tp-binary` rides on every subcommand through a shared parent parser.
    return {s for a in parser._actions for s in a.option_strings} | {"--tp-binary"}


def _topic(words: str) -> str | None:
    """The topic `echolot guide` was given, when it is a word and not a placeholder."""
    first = words.split()[:1]
    return first[0] if first and re.fullmatch(r"[a-z][a-z-]*", first[0]) else None


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_command_in_the_agent_text_parses(path: Path) -> None:
    verbs = _subparsers()
    topics = {p.stem for p in GUIDE_DIR.glob("*.md")}
    text = path.read_text(encoding="utf-8")
    wrong = []
    for line, verb, words in _calls(text):
        if verb not in verbs:
            wrong.append(f"{path.name}:{line}  echolot {verb} — no such command")
            continue
        unknown = [f for f in FLAG.findall(words) if f not in _accepted(verbs[verb])]
        if unknown:
            wrong.append(f"{path.name}:{line}  echolot {verb} {words.strip()} — "
                         f"{verb} has no {', '.join(unknown)}")
        if verb == "guide" and _topic(words) not in (None, *topics):
            wrong.append(f"{path.name}:{line}  echolot guide {_topic(words)} — "
                         f"no such topic; there is {', '.join(sorted(topics))}")
    assert not wrong, "the agent text names commands the CLI refuses:\n  " + \
        "\n  ".join(wrong)


def test_the_extraction_sees_the_forms_the_text_uses() -> None:
    """The reader has to find calls where they are, or the test above is vacuous.

    Written against a page shaped like the real ones: a cheat-sheet column, a
    comment, a quoted question with a comma in it, a span broken across a
    line, the slash command beside the CLI, and prose after a call inside a
    fence.
    """
    page = "\n".join([
        "Run `echolot hunt \"3 s, now 7 s\" --since \"a bump\"` first, then `echolot",
        "mark --pools`. `/echolot setup` is the slash command, `echolot-hunt` a file.",
        "```",
        "echolot report -d monitor_contention --top 5       one detector's rows;",
        "echolot doctor -q      # three lines",
        "   no instrumentation → echolot mark, then echolot mark --apply",
        "echolot            # where things stand",
        "```",
    ])
    got = [(verb, FLAG.findall(words)) for _, verb, words in _calls(page)]
    assert ("hunt", ["--since"]) in got, got
    assert ("mark", ["--pools"]) in got, got
    assert ("report", ["-d", "--top"]) in got, got
    assert ("doctor", ["-q"]) in got, got
    assert ("mark", []) in got and ("mark", ["--apply"]) in got, got
    assert not any(verb == "setup" for verb, _ in got), \
        "the slash command was read as a CLI verb"
    assert len(got) == 6, got


def test_a_flag_the_parser_does_not_have_is_caught() -> None:
    """The check has to be able to fail: a made-up flag on a real verb."""
    verbs = _subparsers()
    [(_, verb, words)] = _calls("`echolot names <trace> --top 200 --no-such-flag`")
    unknown = [f for f in FLAG.findall(words) if f not in _accepted(verbs[verb])]
    assert unknown == ["--no-such-flag"], unknown
    [(_, verb, words)] = _calls("`echolot guide hnut`")
    assert verb == "guide" and _topic(words) == "hnut", (verb, words)
    [(_, verb, words)] = _calls("`echolot guide <topic>`")
    assert _topic(words) is None, "a placeholder was read as a topic"
