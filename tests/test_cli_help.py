"""What `rich-argparse` is allowed to change about the help, and what it is not.

The dependency is here for one thing: colour, when a person is looking at a
terminal. Everything else about the help — where the lines break, what the
section headings are called, which words end up on which row — is the output
this tool had before, and a rendering library is an easy way to lose it
quietly. Nobody reads `--help` in a diff.

So the shape is pinned against the only reference that cannot drift: argparse
itself. The same parser, formatted twice — once through Rich and once through
the stdlib formatter that would have been used without this dependency — has
to come out character for character the same. What is left over is colour, and
the two tests around it check that it appears when there is a terminal and is
gone when there is not.
"""

from __future__ import annotations

import argparse
import sys

import pytest

from echolot.main import build_parser, main

# Rich decides on colour from the environment before it looks at the stream, and
# a developer who exports FORCE_COLOR to keep colour through a pager would
# otherwise turn this file red for a reason that has nothing to do with echolot.
# NO_COLOR goes too: with it set the plain-text tests would pass without ever
# exercising the terminal detection they exist for.
COLOUR_ENV = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "NO_COLOR", "TERM")


@pytest.fixture(autouse=True)
def _neutral_colour_env(monkeypatch):
    for name in COLOUR_ENV:
        monkeypatch.delenv(name, raising=False)


def _parsers() -> dict[str, argparse.ArgumentParser]:
    """The main parser and every subcommand parser, by name."""
    root = build_parser()
    found = {"echolot": root}
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            found.update(action.choices)
    assert len(found) > 1, "no subparsers found: add_subparsers changed shape"
    return found


def _plain_counterpart(name: str) -> type[argparse.HelpFormatter]:
    """The stdlib formatter this parser's output has to keep matching.

    Named from what the parser is rather than from what the code picked for it,
    which is the whole point: reading `formatter_class` back off the parser and
    comparing it against itself would agree with any choice, including the
    wrong one.

    The main parser is Raw because its description is a table built by hand and
    its line breaks are the layout. A subcommand's description is an English
    paragraph written as a wrapped Python string, and argparse re-wraps it to
    the width of the terminal it is printed on.
    """
    return (argparse.RawDescriptionHelpFormatter if name == "echolot"
            else argparse.HelpFormatter)


@pytest.mark.skipif(sys.version_info < (3, 13), reason=(
    "argparse prints a short and long option as one pair only from 3.13; "
    "covered on every version by the two tests below"))
@pytest.mark.parametrize("name", sorted(_parsers()))
def test_help_is_what_argparse_would_have_printed(name, monkeypatch):
    """Colour is the only thing the dependency is allowed to add.

    Held at 80 columns, which is not an arbitrary choice: it is the width every
    reader that is not a terminal gets — a pipe, a CI log, an agent running
    `echolot --help` — so it is the rendering that has to stay reproducible.

    One difference does exist above that width and it is deliberate. argparse
    breaks a word at its hyphens when the line runs out, so at 110 columns
    `resume-or-new` comes out as `resume-` and `or-new` on the next row; Rich
    keeps the word whole. Since the hyphenated words here are literal values a
    person types, keeping them whole is the better of the two, and 80 columns
    is below the widths where the two disagree at all.
    """
    monkeypatch.setenv("COLUMNS", "80")
    parser = _parsers()[name]
    rich_output = parser.format_help()

    monkeypatch.setattr(parser, "formatter_class", _plain_counterpart(name))
    assert rich_output == parser.format_help()


def test_a_short_and_long_option_are_printed_as_one_pair():
    """The one thing that does not match argparse below 3.13, on purpose.

    3.13 changed how a flag with both spellings is listed: `-c, --config
    CONFIG` where every earlier version wrote `-c CONFIG, --config CONFIG` and
    pushed the help onto its own row. rich-argparse prints the newer form
    whatever it is running on.

    Which is why the comparison above is held to 3.13 and up rather than
    dropped: what happens below is that 3.10, 3.11 and 3.12 get the same help
    as 3.13 and 3.14 instead of the one their own argparse would have written.
    Five supported versions and one rendering is worth having, and it is worth
    a test rather than a footnote.
    """
    help_text = _parsers()["names"].format_help()
    assert "-c, --config CONFIG" in help_text
    assert "-c CONFIG, --config CONFIG" not in help_text


@pytest.mark.parametrize("name", sorted(_parsers()))
def test_headings_keep_the_words_argparse_uses(name):
    """Runs everywhere, including where the comparison above is skipped.

    rich-argparse title-cases the section headings by default — `Usage:`,
    `Options:`, `Positional Arguments:` — and `group_name_formatter = str` is
    the one line that turns that off. Nothing else would notice it going away
    on 3.10 through 3.12.
    """
    help_text = _parsers()[name].format_help()
    assert help_text.startswith("usage: echolot")
    assert "\noptions:\n" in help_text
    for titled in ("Usage:", "\nOptions:\n", "Positional Arguments:"):
        assert titled not in help_text


def test_the_command_table_keeps_its_own_line_breaks():
    """Why the main parser is the one that does keep the Raw formatter.

    Its description is not a paragraph: it is a table this module builds, with
    a heading per audience and a column of commands under each. Without Raw
    argparse re-flows it and the whole thing congeals into one block of prose.
    Runs on every version, where the comparison is held to 3.13 and up.
    """
    help_text = _parsers()["echolot"].format_help()
    for heading in ("\nYours:\n", "\nThe pipeline:", "\nImproving the tool:\n"):
        assert heading in help_text, f"the command table lost {heading!r}"
    assert "\n  status " in help_text


@pytest.mark.parametrize("name", sorted(set(_parsers()) - {"echolot"}))
def test_subcommand_help_is_no_wider_than_argparse_makes_it(name, monkeypatch):
    """The property the comparison can only check at one width, and one version.

    This is the failure that handing every subparser the Raw formatter causes,
    and the reason it is worth a test of its own: Raw does not wrap at all, so
    `echolot anr --help` goes out as one 290-character line and the terminal
    breaks it in the middle of a word. Nothing raises.

    Measured against argparse at the same width rather than against the width
    itself, because argparse overruns it too and always has: a mutually
    exclusive group cannot be broken across rows, so `echolot reflect --help`
    at 60 columns has a 67-character usage line on main and on 3.14. The claim
    that holds is the relative one — Rich is never the wider of the two.

    The main parser is left out. Its header is a table 107 columns wide built
    by hand, and both formatters print it untouched.
    """
    for width in (60, 80, 100, 140):
        monkeypatch.setenv("COLUMNS", str(width))

        parser = _parsers()[name]
        widest = max(len(line) for line in parser.format_help().splitlines())

        parser.formatter_class = _plain_counterpart(name)
        argparse_widest = max(
            len(line) for line in parser.format_help().splitlines())

        assert widest <= argparse_widest, (
            f"`echolot {name} --help` at {width} columns is {widest} "
            f"characters wide where argparse would be {argparse_widest}"
        )


@pytest.mark.parametrize("argv, expected", [
    (["--help"], "Yours:"),
    (["init", "--help"], "The one command to know."),
])
def test_help_carries_no_escape_codes_when_captured(capsys, argv, expected):
    """Rich disables terminal control codes when stdout is not a TTY.

    Which is how the help reaches every reader that is not a person: an agent
    running `echolot --help`, a CI log, anything behind a pipe.
    """
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert expected in output
    assert "\x1b[" not in output


def test_parse_errors_carry_no_escape_codes_when_captured(capsys):
    """The usage line argparse prints before an error goes through Rich too.

    The error sentence itself does not — argparse writes that one straight to
    stderr — so this covers the half that could arrive with escape codes in it.
    """
    with pytest.raises(SystemExit) as exc_info:
        main(["init", "--unknown"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "error: unrecognized arguments: --unknown" in error
    assert "\x1b[" not in error


def test_a_terminal_gets_colour(capsys, monkeypatch):
    """The other half, and the reason the dependency is in the file at all.

    Without this the tests above are satisfied by a Rich that emits plain text
    under every condition, and the dependency could stop doing its job without
    anything going red.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")

    with pytest.raises(SystemExit):
        main(["--help"])

    assert "\x1b[" in capsys.readouterr().out
