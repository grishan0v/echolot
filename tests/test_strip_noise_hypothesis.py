#!/usr/bin/env python3
"""`mark.strip_noise` reads a text the way it always did, only faster (#559).

It used to ask `_noise_at` at every character and blank one character at a
time; it now jumps from one place a comment or a string can start to the
next. That is a change of how, not of what, and it is held here to the
reading it replaced, written out as it was: on texts made of the very
characters that decide where a comment or a string starts and ends — quotes,
triple quotes, slashes, stars, backslashes, newlines — and on anything else.
Brace matching, `return` detection and `--pools` all run on its output, so a
character blanked one place over would move a marker.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.mark import _noise_at, strip_noise  # noqa: E402


def one_character_at_a_time(text: str, strings: bool = True) -> str:
    """strip_noise before #559: `_noise_at` asked at every index."""
    out = list(text)
    i = 0
    while i < len(text):
        end, is_string = _noise_at(text, i)
        if end is None:
            i += 1
            continue
        if strings or not is_string:
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
        i = end
    return "".join(out)


# What decides where noise starts and ends, in pieces a source is made of,
# and a few characters that decide nothing.
PIECES = ['"', "'", '"""', "/", "*", "//", "/*", "*/", "\\", "\n", "\r\n", " ",
          "a", "{", "}", "é", "\t"]
source_like = st.lists(st.sampled_from(PIECES), max_size=60).map("".join)


@given(text=st.one_of(source_like, st.text(max_size=200)), strings=st.booleans())
@example(text='Thread(r, "io") // "not a name"', strings=False)
@example(text='"unclosed\nnext', strings=True)
@example(text='"ends in a backslash\\', strings=True)
@example(text="/* never closed", strings=True)
@example(text='"""a "quote" inside', strings=False)
def test_the_noise_is_what_it_was(text: str, strings: bool) -> None:
    got = strip_noise(text, strings)
    assert got == one_character_at_a_time(text, strings)
    assert len(got) == len(text)
    assert [i for i, c in enumerate(got) if c == "\n"] == \
        [i for i, c in enumerate(text) if c == "\n"]
