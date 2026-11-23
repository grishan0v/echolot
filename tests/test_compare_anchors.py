"""An anchor that did not match, and what `compare` makes of it (#534, #594).

An anchor no repeat found leaves a window that is not the scenario's, and the
pair is then no comparison: the numbers stay and the verdicts go, as for two
different processes. An anchor that missed in some repeats only is worded from
`missed`, with the warm repeats or the missing launches named as the likely
cause, and "fix the config" is kept for the case where the config is wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report, row  # noqa: E402

SPREAD_B = [100.0, 102.0, 98.0, 101.0, 99.0]
SPREAD_A = [200.0, 204.0, 196.0, 202.0, 198.0]


def _pair(after_window: dict, *, before_window: dict | None = None
          ) -> tuple[dict, dict]:
    base = {"process": "com.example.app", "duration_ms": 1000.0,
            "start_anchor": {"glob": "AppStart", "matches": 1},
            "end_anchor": {"glob": "list_shown", "matches": 1},
            "startup": {"type": "cold", "dur_ms": 800.0}}
    before = report([det("d", [row("A", 100.0, values=SPREAD_B)])], runs=5,
                    window={**base, **(before_window or {})})
    after = report([det("d", [row("A", 200.0, values=SPREAD_A)])], runs=5,
                   window={**base, **after_window})
    return before, after


def _anchor_text(cmp: dict, side: str = "after") -> str:
    return next((w["text"] for w in cmp["warnings"] if w["id"] == f"anchor-{side}"), "")


def test_the_pair_holds_a_verdict_when_both_anchors_matched() -> None:
    cmp = compare(*_pair({}))
    check("comparable, and the move holds",
          cmp["comparable"] is True and cmp["rows"][0]["holds"] is True, cmp["rows"])


def test_an_anchor_no_repeat_found_makes_the_pair_no_comparison() -> None:
    cmp = compare(*_pair({"end_anchor": {"glob": "list_shown_mark", "matches": 0,
                                         "missed": "5/5"}}))
    text = _anchor_text(cmp)
    check("not comparable", cmp["comparable"] is False, cmp["warnings"])
    check("the numbers stay, the verdicts go",
          cmp["rows"][0]["delta_ms"] == 100.0
          and all(r["holds"] is None and r["shift"] is None for r in cmp["rows"]),
          cmp["rows"])
    check("never matched, and the config is named, as the traces hold a launch",
          "never matched in any repeat" in text and "Fix the config" in text, text)


def test_a_single_trace_without_the_anchor_is_no_comparison_either() -> None:
    cmp = compare(*_pair({"end_anchor": {"glob": "list_shown_mark", "matches": 0}}))
    check("a report of one trace carries no `missed`",
          cmp["comparable"] is False and "never matched" in _anchor_text(cmp),
          cmp["warnings"])


def test_two_different_processes_get_no_verdict() -> None:
    cmp = compare(*_pair({"process": "com.other.app"}))
    check("not comparable, and no row holds",
          cmp["comparable"] is False and all(r["holds"] is None for r in cmp["rows"]),
          cmp["rows"])


def test_an_anchor_missed_in_one_repeat_is_said_as_such() -> None:
    cmp = compare(*_pair({
        "start_anchor": {"glob": "bindApplication", "matches": 0, "missed": "1/15"},
        "end_anchor": {"glob": "screen_shown", "matches": 0, "missed": "1/15"},
        "startup": {"type": "cold and warm", "dur_ms": 800.0}}))
    text = _anchor_text(cmp)
    check("the partial wording, from `missed`",
          "was not found in 1 of 15 after repeats" in text
          and "never matched" not in text and "Fix the config" not in text, text)
    check("the warm repeats are named, and leaving them out is the remedy",
          "cold and warm" in text and "Leave those traces out" in text, text)
    check("still a comparison: fourteen repeats of fifteen are the scenario's",
          cmp["comparable"] is True and cmp["rows"][0]["holds"] is True, cmp["rows"])


def test_an_anchor_in_a_set_with_no_launch_points_at_the_runner() -> None:
    cmp = compare(*_pair({"start_anchor": {"glob": "bindApplication", "matches": 0,
                                           "missed": "5/5"},
                          "startup": None}))
    text = _anchor_text(cmp)
    check("no launch in the traces: the runner is named, the config is not blamed",
          "hold no app launch" in text and "reset_policy" in text
          and "Fix the config" not in text, text)
    check("and the pair is still no comparison", cmp["comparable"] is False)


def test_the_config_warning_names_the_anchor_that_changed() -> None:
    before, after = _pair({"end_anchor": {"glob": "list_shown", "matches": 1}},
                          before_window={"end_anchor": {"glob": "list_shown_mark",
                                                        "matches": 1}})
    before["config"] = {"sha": "aaaa", "path": "/p/echolot.yml", "defaults": False}
    after["config"] = {"sha": "bbbb", "path": "/p/echolot.yml", "defaults": False}
    cmp = compare(before, after)
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "config")
    check("both globs, and the hash beside them",
          "end anchor `list_shown_mark` → `list_shown`" in text and "aaaa" in text, text)


def test_the_config_warning_falls_back_to_the_hash() -> None:
    before, after = _pair({})
    before["config"] = {"sha": "aaaa", "defaults": False}
    after["config"] = {"sha": "bbbb", "defaults": False}
    text = next(w["text"] for w in compare(before, after)["warnings"] if w["id"] == "config")
    check("nothing visible changed: the hash and where to look",
          "sha `aaaa` → `bbbb`" in text and "thresholds all live there" in text, text)


def test_each_side_carries_its_config_path() -> None:
    before, after = _pair({})
    before["config"] = {"sha": "aaaa", "path": "/p/a.yml", "defaults": False}
    after["config"] = {"sha": "bbbb", "path": "/p/b.yml", "defaults": False}
    cmp = compare(before, after)
    check("beside config_sha",
          cmp["before"]["config_path"] == "/p/a.yml"
          and cmp["after"]["config_path"] == "/p/b.yml"
          and cmp["after"]["config_sha"] == "bbbb", cmp["before"])
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "config")
    check("and a config that moved file is named", "`/p/a.yml` → `/p/b.yml`" in text, text)
