#!/usr/bin/env python3
"""`compare` refuses two reports whose windows were not defined the same way (#446).

A launch closed at its end against one that ran on to `reportFullyDrawn()`,
either against a window cut by anchors, and a merged report whose own
repeats did not agree. The comparison marks the pair not comparable, and the
command refuses it the way it refuses two scenarios.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_launch_report import _cli, _report  # noqa: E402
from tests.test_launch_window import LAUNCH  # noqa: E402

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402


# --- compare refuses two windows that were not defined the same way ------------------

def _compared(before: dict, after: dict) -> dict:
    return compare_mod.build(before, after, before_path="before.json", after_path="after.json")


@pytest.mark.parametrize("before, after, said", [
    (_report("launch"), _report("launch+reportFullyDrawn", "app"),
     "as the launch, closed at its end before, as the launch, closed on `reportFullyDrawn` after"),
    (_report(None), _report("launch"),
     "by the config's start and end anchors before, as the launch, closed at its end after"),
    (_report("launch+reportFullyDrawn", "app"), _report(None),
     "as the launch, closed on `reportFullyDrawn` before, by the config's start and end "
     "anchors after"),
])
def test_two_definitions_are_not_one_window(before: dict, after: dict, said: str) -> None:
    cmp = _compared(before, after)
    warning = next((w["text"] for w in cmp["warnings"] if w["id"] == "window"), "")
    check("not comparable, as two processes are not", cmp["comparable"] is False, cmp["warnings"])
    check("and the reason names both", said in warning, warning)


def test_one_definition_on_both_sides_is_compared_as_ever() -> None:
    for defined, source in (("launch", None), ("launch+reportFullyDrawn", "app"), (None, None)):
        cmp = _compared(_report(defined, source), _report(defined, source, ms=640.0))
        check(f"{defined}: comparable, and no word about the window",
              cmp["comparable"] is True and "window" not in {w["id"] for w in cmp["warnings"]},
              cmp["warnings"])
    cmp = _compared(_report("launch+reportFullyDrawn", "app"),
                    _report("launch+reportFullyDrawn", "system_server"))
    check("the same call read from two places is one definition", cmp["comparable"] is True,
          cmp["warnings"])


def test_a_set_that_closed_two_ways_is_compared_with_nothing() -> None:
    mixed = report_mod.aggregate([_report("launch+reportFullyDrawn", "app")] * 4
                                 + [_report("launch")])
    for other in (mixed, _report("launch"), _report("launch+reportFullyDrawn", "app")):
        cmp = _compared(other, mixed)
        warning = next((w["text"] for w in cmp["warnings"] if w["id"] == "window"), "")
        check("not comparable, itself included", cmp["comparable"] is False, cmp["warnings"])
        check("and the counts say why", "`launch` in 1/5, `launch+reportFullyDrawn` in 4/5"
              in warning and "mix two windows" in warning, warning)


def test_the_command_refuses_the_pair_as_it_refuses_two_scenarios(tmp_path: Path,
                                                                 monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "echolot.yml").write_text(yaml.safe_dump(LAUNCH), encoding="utf-8")
    (tmp_path / "anchors.yml").write_text(yaml.safe_dump(
        {**selftest.FIXTURE_CONFIG, "scenario": {**selftest.FIXTURE_CONFIG["scenario"],
                                                 "name": "startup"}}), encoding="utf-8")
    plain, drawn = tmp_path / "plain.perfetto-trace", tmp_path / "drawn.perfetto-trace"
    plain.write_bytes(fixture.build())
    drawn.write_bytes(fixture.build(fully_drawn="app"))
    for trace, config, out in ((plain, "echolot.yml", "launch"), (drawn, "echolot.yml", "drawn"),
                               (plain, "anchors.yml", "anchors"), (drawn, "echolot.yml", "again")):
        code, _, err = _cli("analyze", str(trace), "-c", config, "-o", out)
        check(f"analyze into {out}: exit 0", code == 0, err[-600:])

    code, out, err = _cli("compare", "launch/report.json", "drawn/report.json")
    check("a launch against a launch that ran on to reportFullyDrawn: exit 2, in a sentence",
          code == 2 and "cannot be compared: the windows were defined differently" in err
          and "closed at its end before" in err and "closed on `reportFullyDrawn` after" in err
          and "Traceback" not in err, err[-700:])
    check("and no table", "| " not in out, out[:300])
    code, _, err = _cli("compare", "anchors/report.json", "launch/report.json")
    check("anchors against the launch window, under one scenario name: exit 2",
          code == 2 and "by the config's start and end anchors before" in err, err[-700:])
    code, out, err = _cli("compare", "drawn/report.json", "again/report.json")
    check("two reports of one definition are compared", code == 0 and "Traceback" not in err,
          (out[-300:], err[-500:]))
