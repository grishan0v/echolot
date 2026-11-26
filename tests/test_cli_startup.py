"""What a command imports before it runs (#561).

Every call of `echolot` builds the parser, parses, and runs one command. The
parsing used to import every module any command stands on, and rich with
them: some 270 modules for `echolot --version`, the same as for `echolot
analyze`. The self-check holds the set of modules imported before a command
runs to an exact list; these tests hold it here too, and pin the stand-in
that lets `main` name a module without importing it.
"""

from __future__ import annotations

import pytest

from echolot import flags, main, selftest


@pytest.mark.parametrize("argv", selftest.PARSED_ONLY, ids=" ".join)
def test_a_command_is_parsed_before_its_own_modules_are_imported(argv) -> None:
    loaded = selftest.imported_before_any_command(argv)
    assert not {n for n in loaded if n.split(".")[0] == "rich"}, loaded
    assert loaded <= selftest.BEFORE_ANY_COMMAND, sorted(loaded - selftest.BEFORE_ANY_COMMAND)


def test_what_every_command_imports_is_what_main_imports_at_the_top() -> None:
    """The list is not padded: each module on it is imported before any
    command, so a stale entry cannot hide a module that should leave it."""
    loaded = selftest.imported_before_any_command(["status"])
    assert loaded == selftest.BEFORE_ANY_COMMAND, (
        sorted(selftest.BEFORE_ANY_COMMAND - loaded), sorted(loaded - selftest.BEFORE_ANY_COMMAND))


def test_a_stand_in_becomes_the_module_where_it_stood(monkeypatch) -> None:
    stand_in = main._OnFirstUse("_stand_in", ".flags")
    monkeypatch.setitem(vars(main), "_stand_in", stand_in)
    assert stand_in.SEED == flags.SEED
    assert vars(main)["_stand_in"] is flags


def test_a_stand_in_leaves_alone_what_a_test_put_in_its_place(monkeypatch) -> None:
    stand_in = main._OnFirstUse("_stand_in", ".flags")
    mine = object()
    monkeypatch.setitem(vars(main), "_stand_in", mine)
    assert stand_in.SPLITS == flags.SPLITS
    assert vars(main)["_stand_in"] is mine


def test_a_stand_in_passes_a_patch_to_the_module(monkeypatch) -> None:
    stand_in = main._OnFirstUse("_stand_in", ".flags")
    monkeypatch.setattr(stand_in, "SEED", 1)
    assert flags.SEED == 1


def test_the_values_the_parser_prints_are_the_ones_the_modules_use() -> None:
    """flags.py holds them; compare, noise and state read them from there,
    under the names they always had."""
    from echolot import compare, noise, state

    assert (compare.FLOOR_MS, compare.FLOOR_RATIO, compare.NAMES_SHOWN) == (
        flags.FLOOR_MS, flags.FLOOR_RATIO, flags.NAMES_SHOWN)
    assert (noise.SPLITS, noise.SEED, noise.MIN_RUNS) == (flags.SPLITS, flags.SEED, flags.MIN_RUNS)
    assert state.NEXT_KINDS is flags.NEXT_KINDS
