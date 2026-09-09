#!/usr/bin/env python3
"""Property-based tests for echolot/table.py.

The manual fixtures in test_report.py and test_reflect.py each check one
table with one shape of row. What they cannot show is the property the
module's own docstring calls out as the reason it exists: a `|` inside a
cell must never be free to shift the columns of its row. Hypothesis throws
arbitrary strings — including `|`, backslashes, unicode, empty strings and
whitespace — at `render()` and checks that property holds no matter what a
detector puts in a cell.

`|` and `\\` are drawn from a small alphabet of their own alongside the full
unicode range, rather than left to turn up by chance. Both are one character
out of a million, and a strategy that only reaches them once in a few hundred
examples is a strategy that reaches them on some runs and not others — which
is the opposite of what the pinned seed in conftest.py is for. The characters
the invariant is about are generated on purpose, and the `@example` cases
below fix the shape of them so a seed change cannot quietly stop testing it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.table import _escape, columns, fmt, render  # noqa: E402

# Line-breaking characters (\n, \r, \v, \f, and the unicode line/paragraph
# separators U+2028/U+2029 that str.splitlines() also treats as breaks) are
# excluded from the general cell alphabet on purpose — see
# test_render_does_not_escape_line_breaks_in_cells below for why they get
# their own, separately documented, test.
_LINE_BREAKERS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"

# Cell values: strings that may contain pipes, backslashes and unicode — the
# things a slice name or a config value could actually contain. Non-string
# scalars go through fmt(); `render` also accepts None as "missing".
cell_text = st.one_of(
    # Half the string cells are drawn from the characters that make a table
    # a table, so `|` turns up in roughly one generated cell in four rather
    # than one in a few hundred. Mixing the two alphabets inside a single
    # st.text() does not do this: the generator settles on the simplest
    # branch and `|` stays about as rare as it is in the full unicode range.
    st.text(alphabet="ab |\\-—", min_size=0, max_size=12),
    st.text(alphabet=st.characters(exclude_categories=("Cs",),
                                   exclude_characters=_LINE_BREAKERS),
            min_size=0, max_size=40),
)
cell_value = st.one_of(
    st.none(),
    cell_text,
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
)
row_key = st.text(alphabet=st.characters(categories=("Ll", "Nd"),
                                          include_characters="_"),
                   min_size=1, max_size=12)
row = st.dictionaries(row_key, cell_value, min_size=0, max_size=6)
rows = st.lists(row, min_size=0, max_size=8)


def _bare_pipes(line: str) -> int:
    """Pipes that still separate cells: the ones `_escape` did not neutralise.

    A `|` preceded by a backslash was escaped and is content, not structure.
    (A cell that already held a backslash makes that reading wrong — see
    test_escape_leaves_an_existing_backslash_alone for that documented gap.)
    """
    return sum(1 for i, c in enumerate(line)
               if c == "|" and (i == 0 or line[i - 1] != "\\"))


@given(st.sampled_from(_LINE_BREAKERS), st.text(min_size=0, max_size=10).filter(
    lambda s: not any(c in _LINE_BREAKERS or c == "|" for c in s)))
def test_render_does_not_escape_line_breaks_in_cells(breaker, plain):
    """Documented gap, not fixed here: only `|` is escaped by `_escape`.

    A cell value containing `\\r`, `\\n`, or a unicode line/paragraph
    separator is *not* escaped, so it can add extra visual "lines" to the
    row it is in when the markdown is displayed — the same class of problem
    the module's docstring describes for `|` (an attacker-or-just-weird
    string reshaping a table an agent reads as data), just for rows instead
    of columns. `_escape` only replaces `|`; everything else, including
    every line-breaking character, passes through `str(text)` unchanged.
    """
    text = plain + breaker + plain
    out = render([{"name": text}])
    # The line breaker survives verbatim — this is the gap, stated as a
    # fact rather than asserted as correct.
    assert breaker in out
    # And because it survives, splitting the result the way a viewer would
    # (on any line-breaking character, as str.splitlines() does) yields more
    # "lines" than the table's 3 logical rows (header/rule/body).
    assert len(out.splitlines()) > 3


@given(rows)
@example([{"name": "a|b"}])           # the whole point of the module
@example([{"name": "|"}, {"name": ""}])
@example([{"a|b": "x"}])              # a pipe in a *heading*, not a cell
@example([{"name": "||"}, {"other": "|"}])
def test_render_has_one_bare_pipe_more_than_columns_per_line(rs):
    """Every line of the table has exactly len(cols)+1 *unescaped* pipes.

    That is what "the columns did not shift" means structurally: a `|` that
    leaked out of a cell unescaped would add a separator to that line and
    break this count. Escaped pipes (`\\|`) are content and are not counted —
    counting every `|` character instead would make this property simply
    false for any cell holding one, which is the case it exists to check.

    The `@example` rows above pin that case down by hand. Left to the
    strategy alone it is a matter of how the seed falls: `|` is one
    character out of a million, and a run that never generates one never
    tests the invariant while still reporting a pass.
    """
    out = render(rs)
    if not rs:
        assert out == "_empty_"
        return
    cols = columns(rs)
    if not cols:
        return  # see test_render_of_rows_without_keys_is_a_degenerate_table
    for line in out.splitlines():
        assert _bare_pipes(line) == len(cols) + 1


@given(st.lists(st.just({}), min_size=1, max_size=4))
def test_render_of_rows_without_keys_is_a_degenerate_table(rs):
    """Documented edge case, not fixed here: rows that carry no keys at all.

    `render` checks `if not rows` — is the *list* empty — before falling
    back to `empty`. A list of one-or-more all-empty dicts is not empty but
    has zero columns, so it renders a degenerate table of empty cells
    instead of the `_empty_` placeholder every other no-column case gets.

    Asserted rather than only described, so that fixing it is a visible
    change to this file rather than something that slips through green.
    """
    out = render(rs)
    assert out != "_empty_"
    assert out == "\n".join(["|  |", "||"] + ["|  |"] * len(rs))
    assert columns(rs) == []


@given(rows)
def test_render_rule_line_is_dashes_and_pipes_only(rs):
    """The separator row never depends on cell content, only on column count."""
    out = render(rs)
    if not rs:
        return
    lines = out.splitlines()
    rule = lines[1]
    assert set(rule) <= {"|", "-"}


@given(st.text(min_size=0, max_size=200))
@example("|")
@example("a|b|c")
def test_escape_removes_bare_pipes(text):
    """No `|` survives rendering a single cell without a backslash in front.

    _escape is "private" (leading underscore) but it is the one function
    the whole module's premise rests on, so it is exercised directly
    rather than only through render().
    """
    escaped = _escape(text)
    i = escaped.find("|")
    while i != -1:
        # `i > 0` is asserted, not assumed: `escaped[i - 1]` at i == 0 would
        # read the last character of the string instead and quietly pass.
        assert i > 0 and escaped[i - 1] == "\\", \
            f"bare pipe survived escaping: {escaped!r}"
        i = escaped.find("|", i + 1)


@given(st.text(alphabet="ab", min_size=0, max_size=5))
def test_escape_leaves_an_existing_backslash_alone(plain):
    """Documented gap, not fixed here: `_escape` escapes `|` and nothing else.

    A cell that already ends in a backslash comes out as `\\\\|`. A renderer
    that reads `\\\\` as one escaped backslash then sees the `|` after it as a
    live cell separator again — the same column shift the module exists to
    prevent, reached by a different route. Nothing echolot writes into a cell
    ends in a backslash today; a Windows path in a config value would.
    """
    assert _escape(plain + "\\|") == plain + "\\\\|"
    # And the render-level property above cannot see it, because "a pipe
    # after a backslash" is exactly what that property treats as escaped.
    assert _bare_pipes(render([{"c": plain + "\\|"}]).splitlines()[-1]) == 2


@given(cell_value)
def test_fmt_none_is_dash_everything_else_is_str(value):
    if value is None:
        assert fmt(value) == "—"
    else:
        assert fmt(value) == str(value)


@given(rows)
def test_columns_contains_every_key_from_every_row_exactly_once(rs):
    cols = columns(rs)
    assert len(cols) == len(set(cols))
    seen = set()
    for r in rs:
        seen |= set(r)
    assert set(cols) == seen


@given(rows, st.lists(row_key, min_size=0, max_size=4, unique=True))
def test_columns_respects_skip(rs, skip):
    cols = columns(rs, skip=skip)
    assert not (set(cols) & set(skip))


@settings(max_examples=25)
@given(rows)
def test_render_is_deterministic(rs):
    """Same rows in, same markdown out — twice in a row.

    This is the property the rest of the tool depends on: `render` sits
    below every report, and a report is only reproducible if everything
    under it is.
    """
    assert render(rs) == render(rs)
