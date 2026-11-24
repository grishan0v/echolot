#!/usr/bin/env python3
"""A row too small to have moved past the floor is not gone or new (#843).

`below` used to keep only what took 5 ms in some repeat, at most 200 rows,
so a 2.6 ms initializer one side showed and the other kept under its bar
read as gone, and a recording set against a set that contained it had
findings appear from nowhere. Every row under the bar is kept from 1 ms now,
and a row one side lacks that is no larger than the floor of a move is
steady: it cannot have moved by more.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import det, report, row  # noqa: E402


def test_a_recording_against_a_set_that_holds_it_has_nothing_gone_or_new(tmp_path: Path) -> None:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    analysed = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    one = report_mod.aggregate([copy.deepcopy(analysed)])
    three = report_mod.aggregate([copy.deepcopy(analysed) for _ in range(3)])
    cmp = compare_mod.build(one, three, before_path="b.json", after_path="a.json")
    moved = [(r["detector"], r["location"], r["change"]) for r in cmp["rows"]
             if r["change"] in (compare_mod.APPEARED, compare_mod.VANISHED)]
    check("nothing gone, nothing new", moved == [], moved)


def test_a_small_row_one_side_lacks_is_steady() -> None:
    before = report([det("app_init", [row("ProfileInstallerInitializer", 3.0, metric="self_ms")])])
    after = report([det("app_init", [])])
    cmp = compare_mod.build(before, after, before_path="b.json", after_path="a.json")
    got = [r for r in cmp["rows"] if r["location"] == "ProfileInstallerInitializer"]
    check("steady, not gone", len(got) == 1 and got[0]["change"] == compare_mod.STEADY, got)
    big = report([det("app_init", [row("ProfileInstallerInitializer", 30.0, metric="self_ms")])])
    cmp = compare_mod.build(big, after, before_path="b.json", after_path="a.json")
    got = [r for r in cmp["rows"] if r["location"] == "ProfileInstallerInitializer"]
    check("a row past the floor still goes", got and got[0]["change"] == compare_mod.VANISHED, got)
