#!/usr/bin/env python3
"""Property-based tests for echolot/anr.py's `_state`.

`_state` maps two sources' spellings of a thread state onto one
vocabulary: Crashlytics writes `timed waiting`, ART writes `TimedWaiting`,
and the manual fixtures in test_anr.py only ever exercise the handful of
states that appear in the two sample dumps. Hypothesis instead throws
arbitrary "word-shaped" text at it and checks the properties the function
relies on: it never leaves an uppercase letter behind, and it never drops
or reorders a character — it only ever inserts spaces at the boundaries
`_state` itself introduces.

The input is built as enum names rather than as letter salad. What the
function does is split on an inner capital, and a random 30-character
string of mixed-case letters exercises that incidentally at best; a
concatenation of capitalised words is the shape ART actually emits.
"""

from __future__ import annotations

import string
import sys
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.anr import _state  # noqa: E402

# "word-shaped": the states this function actually receives are single
# tokens like "Runnable" or "TimedWaiting" — letters only, no whitespace.
letters = st.text(alphabet=string.ascii_letters, min_size=0, max_size=30)
# The shape the split is for: `Waiting`, `TimedWaiting`, `WaitingForGcToComplete`.
enum_name = st.lists(
    st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8).map(str.capitalize),
    min_size=1, max_size=4,
).map("".join)
word = st.one_of(letters, enum_name)


@given(word)
def test_state_output_is_never_upper_case(w):
    result = _state(w)
    assert not any(c.isupper() for c in result)


@given(word)
def test_state_only_inserts_spaces_never_drops_characters(w):
    """Stripping the spaces `_state` may have inserted gets back the
    original word, case-folded."""
    assert _state(w).replace(" ", "") == w.lower()


@given(st.text(alphabet=string.ascii_lowercase + " ", min_size=1, max_size=20))
def test_state_of_an_already_lower_word_is_unchanged(w):
    """Generated already-lower, rather than generated at random and then
    skipped unless it happens to be lower — which is how a test ends up
    running a hundred examples and asserting on two of them."""
    assert _state(w) == w


@example("TimedWaiting")
@example("Runnable")
@example("")
@given(word)
def test_state_is_idempotent_once_split(w):
    """Feeding the already-split, already-lower result back through
    changes nothing further — the vocabulary settles in one pass."""
    once = _state(w)
    assert _state(once) == once


@given(enum_name)
def test_state_agrees_with_the_spelling_the_other_source_uses(name):
    """The whole job: ART's `TimedWaiting` and Crashlytics' `timed waiting`
    have to arrive at the same string, or the two sources disagree about a
    thread state for no reason a reader could see.

    Built from the same words in both spellings, so the assertion is that
    the two routes meet — not that either one produces a particular string.
    """
    words = [w for w in _state(name).split(" ") if w]
    art = "".join(w.capitalize() for w in words)
    crashlytics = " ".join(words)
    assert _state(art) == _state(crashlytics)


@example("IOWait")
@example("HTTPTimeout")
@example("TIMEDWAITING")
@given(st.text(alphabet=string.ascii_uppercase, min_size=2, max_size=12))
def test_state_does_not_split_a_name_written_in_capitals(w):
    """Documented, not fixed: the split needs a lower-case letter *before*
    the capital, so a run of capitals is never a word boundary.

    `IOWait` becomes `iowait`, not `io wait`; a source that shouted
    `TIMEDWAITING` would get `timedwaiting` and its own entry in a
    vocabulary meant to have one. Neither source emits a state in either
    shape today — the ART enum names are all `TimedWaiting`-shaped, and
    Crashlytics writes the words out with the space already in them — which
    is why this is written down rather than changed. It is the thing that
    would surprise whoever adds a third source.
    """
    result = _state(w)
    assert " " not in result
    assert result == w.lower()


@given(st.text(alphabet=string.ascii_letters + " ", min_size=0, max_size=30))
def test_state_of_a_word_that_already_has_spaces(w):
    """A state that already contains a space — not a shape ART or
    Crashlytics produces, but not one the type signature rules out either.

    Both properties the rest of this file rests on survive it: no upper-case
    is left behind, and nothing is dropped or reordered. What does *not*
    survive is the reading that a space in the output marks a boundary
    `_state` chose — here they are indistinguishable from the ones the input
    arrived with, which is why the spelling-folding tests above generate
    single tokens instead.
    """
    result = _state(w)
    assert not any(c.isupper() for c in result)
    assert result.replace(" ", "") == w.lower().replace(" ", "")
