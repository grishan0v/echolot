#!/usr/bin/env python3
"""Property-based tests for pure parsers in echolot/reflect/facts.py.

`subcommands` and `_glob_score` both read shell command text a person or an
agent actually typed — arbitrary quoting, arbitrary paths, arbitrary
subcommand-shaped words that are not real subcommands. The manual fixtures
in test_reflect.py exercise real transcripts; hypothesis fills in the
shapes those transcripts did not happen to contain.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.reflect.facts import (  # noqa: E402
    _glob_score, is_invocation, subcommands, verbs,
)

# The real subcommands, read from the parser the way facts.py reads them.
# Written down here as a fixture instead, this drifts: a renamed verb leaves
# the test asserting things about a word that is no longer a subcommand,
# and every test below that filters on "is this real" quietly stops
# asserting anything at all.
REAL = verbs()

# Words that are shaped like a subcommand and are not one — the English that
# `is_invocation` exists to keep out of the tally.
prose = st.sampled_from(["ran", "call", "calls", "without", "reads", "the",
                          "analysing", "collected"])

path_segment = st.text(alphabet=st.characters(categories=("Ll", "Nd")),
                        min_size=1, max_size=10)
glob_path = st.lists(path_segment, min_size=1, max_size=5).map("/".join)


@given(glob_path, st.text(min_size=0, max_size=60))
def test_glob_score_is_bounded_by_the_number_of_path_segments(missing, argv):
    parts = missing.strip().split("/")
    score = _glob_score(argv, missing)
    assert 0 <= score <= len(parts)


@given(glob_path)
def test_glob_score_is_maximal_when_argv_contains_the_full_glob(missing):
    """An argv holding the whole (unquoted) missing path scores every segment."""
    parts = missing.strip().split("/")
    argv = f"echolot collect --out {missing}"
    assert _glob_score(argv, missing) == len(parts)


@given(glob_path, glob_path)
def test_glob_score_prefers_the_argv_that_matches_more_of_the_path(head, tail):
    """The tie-break `_skipped_by_glob` routes on: more trailing segments in
    common with the missing glob is a better match than fewer.

    `_glob_score` is only ever read as a comparison between two invocations
    in the same command, so the ordering is the property, not the number.
    """
    missing = f"{head}/{tail}"
    only_the_tail = f"echolot collect --out somewhere/else/{tail}"
    the_whole_path = f"echolot collect --out {missing}"
    assert _glob_score(the_whole_path, missing) >= _glob_score(only_the_tail, missing)


@given(st.one_of(prose, st.sampled_from(sorted(REAL)),
                 st.text(min_size=0, max_size=15)))
def test_is_invocation_admits_flags_and_known_verbs_and_nothing_else(word):
    """A flag is `echolot --help`, a known verb is a call, and everything
    else after `echolot` is English.

    Stated as the three cases rather than as `word.startswith("-") or word in
    known`: repeating the implementation's own expression back to it can only
    fail if someone edits both lines, which is not a check.
    """
    called = is_invocation(word, REAL)
    if word.startswith("-"):
        assert called is True         # `--help`, `-q`, and any future flag
    elif word in REAL:
        assert called is True
    else:
        assert called is False        # prose, empty string, a renamed verb


@given(st.text(min_size=0, max_size=400))
@settings(max_examples=200)
def test_subcommands_never_raises_on_arbitrary_shell_text(command):
    """Whatever a transcript contains, reading it for `echolot <verb>` must
    not itself be the thing that crashes the reader."""
    result = subcommands(command)
    assert isinstance(result, list)
    assert all(isinstance(s, str) for s in result)


@given(st.sampled_from(sorted(REAL)), path_segment)
def test_subcommands_finds_a_real_invocation_verbatim(verb, arg):
    """A plain `echolot <verb> <arg>` line is read back as that verb.

    Drawn from `verbs()` itself, so there is no shape of this test where it
    runs and asserts nothing.
    """
    command = f"echolot {verb} {arg}"
    assert verb in subcommands(command)


@given(prose, path_segment)
def test_subcommands_does_not_read_prose_as_an_invocation(word, arg):
    """The failure this function was written for: a report whose "By
    subcommand" line read like a sentence, because `echolot ran`, `echolot
    calls` and `echolot without` had each been counted as a call."""
    assert subcommands(f"echolot {word} {arg}") == []


def test_subcommands_ignores_a_heredoc_body():
    """`cat > x.yml <<EOF … echolot calibrate … EOF` writes a config; it does
    not invoke calibrate. This is the one case the manual fixtures already
    cover — kept here as the property's negative control."""
    command = "cat > echolot.yml <<'EOF'\ndetectors:\n  # echolot calibrate\nEOF"
    assert subcommands(command) == []
