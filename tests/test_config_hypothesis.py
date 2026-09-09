#!/usr/bin/env python3
"""Property-based tests for echolot/config.py.

`merge` is the function `Config.load` uses to layer local.yml over
echolot.yml, and `Config.get` is how every other property on `Config`
reads the merged tree. Both are pure and both are small enough that a
manual fixture only ever exercises the one shape someone thought to write
down; hypothesis builds arbitrary nested config trees instead.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.config import Config, merge  # noqa: E402

leaf = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=10))
key = st.text(alphabet=st.characters(categories=("Ll",)), min_size=1,
              max_size=8)
# Bounded depth: an unbounded recursive strategy can build a tree deep enough
# to make merge's recursion (and this test) unreasonably slow.
nested_dict = st.recursive(
    st.dictionaries(key, leaf, max_size=4),
    lambda children: st.dictionaries(key, children, max_size=4),
    max_leaves=20,
)


@given(nested_dict)
def test_merge_with_empty_overlay_is_identity(base):
    assert merge(base, {}) == base


@given(nested_dict)
def test_merge_empty_base_is_the_overlay(over):
    assert merge({}, over) == over


@given(nested_dict, nested_dict)
def test_merge_is_idempotent_under_the_same_overlay(base, over):
    """Applying the same overlay twice is the same as applying it once.

    local.yml is read once per run, but this is the property that makes the
    merge safe to reason about at all: re-merging an already-merged config
    with the same overlay must not move anything further.
    """
    once = merge(base, over)
    twice = merge(once, over)
    assert twice == once


@given(nested_dict, nested_dict)
def test_merge_does_not_mutate_its_arguments(base, over):
    base_copy, over_copy = copy.deepcopy(base), copy.deepcopy(over)
    merge(base, over)
    assert base == base_copy
    assert over == over_copy


def test_merge_shares_sub_trees_with_the_overlay_it_was_given():
    """Documented, not fixed here: `merge` copies one level, not the tree.

    The test above says merge does not mutate what it was handed, which is
    true and is the half that matters for `Config.load`. It invites the
    stronger reading, which is not true: a sub-tree the overlay contributed
    whole is the overlay's own dict, not a copy of it, so writing into the
    merged config afterwards writes into the overlay too.

    Nothing does that today — a `Config` is read-only once loaded — so this
    is written down rather than fixed. It is the assumption anything that
    starts editing a merged config would break.
    """
    over = {"toolchain": {"tp_binary": "/opt/tp"}}
    merged = merge({}, over)
    assert merged["toolchain"] is over["toolchain"]
    merged["toolchain"]["tp_binary"] = "/elsewhere"
    assert over["toolchain"]["tp_binary"] == "/elsewhere"


@settings(max_examples=50)
@given(st.lists(key, min_size=1, max_size=4, unique=True), leaf)
def test_config_get_reads_back_a_dotted_path_it_was_given(path_parts, value):
    """Building `{"a": {"b": {"c": value}}}` and asking for "a.b.c" back."""
    node: dict = value
    for part in reversed(path_parts):
        node = {part: node}
    cfg = Config(node)
    assert cfg.get(".".join(path_parts)) == value


@given(st.text(alphabet=st.characters(exclude_characters="."), min_size=1,
               max_size=20))
def test_config_get_of_missing_key_is_the_default(missing_key):
    cfg = Config({})
    sentinel = object()
    assert cfg.get(missing_key, sentinel) is sentinel
    assert cfg.get(missing_key) is None


@given(st.lists(key, min_size=2, max_size=4, unique=True), leaf)
def test_config_get_stops_at_a_leaf_instead_of_walking_into_it(path_parts, value):
    """A dotted path that runs past the end of the tree is a miss, not a
    crash: `get("scenario.start.name")` on a `scenario.start` that is a
    plain string returns the default rather than raising.

    `_anchor` depends on this — it asks for a path and decides what to do
    from what comes back.
    """
    head, rest = path_parts[0], path_parts[1:]
    cfg = Config({head: "a string, not a mapping"})
    sentinel = object()
    assert cfg.get(".".join([head, *rest]), sentinel) is sentinel
