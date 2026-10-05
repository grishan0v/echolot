"""What `compare` calls a move, and what it warns about first.

A detector that failed on one side, a clock that dropped by a tenth, a row
that went from one repeat in five to all five, an end anchor that never
matched: in each case the table used to say something the two reports do not.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report import env, one, row  # noqa: E402


def _compare(before: dict, after: dict, **kw) -> dict:
    return compare_mod.build(before, after, before_path="before.json",
                             after_path="after.json", **kw)


def _ids(cmp: dict) -> list[str]:
    return [w["id"] for w in cmp["warnings"]]


def test_a_failed_detector_is_neither_silent_nor_a_change() -> None:
    before = one([row("collection_load", count=1, self_ms=60.0, total_ms=60.0,
                      max_ms=60.0, detail="main")], det_id="main_thread_block")
    before["detectors"][0]["params"] = {"min_slice_ms": 40}
    after = one([], det_id="main_thread_block")
    after["detectors"][0].update(params={"min_slice_ms": 16},
                                 error="no such table: foo")
    cmp = _compare(before, after)
    check("the failure is a warning that names the side and the error",
          any(w["id"] == "detector-failed" and "after side" in w["text"]
              and "no such table: foo" in w["text"] for w in cmp["warnings"]),
          cmp["warnings"])
    check("and no threshold moved", "thresholds" not in _ids(cmp), _ids(cmp))
    check("the detector changed state to failed, not silent",
          cmp["summary"]["state_changed"] == [{"id": "main_thread_block",
                                               "before": "1 row(s)",
                                               "after": "failed"}],
          cmp["summary"]["state_changed"])
    check("and its row is not listed as gone", cmp["rows"] == [], cmp["rows"])


def _clocked(mhz: float, ms: float) -> dict:
    return one([row("work", count=1, self_ms=ms, total_ms=ms, max_ms=ms,
                    detail="main")], environment=env(mhz))


def test_the_clock_check_is_the_row_floor() -> None:
    cmp = _compare(_clocked(2200.0, 1000.0), _clocked(2000.0, 1100.0))
    check("2200 to 2000 MHz grows a CPU-bound row by 10%, and is warned about",
          "environment" in _ids(cmp) and "10% apart" in cmp["warnings"][0]["text"],
          cmp["warnings"])
    cmp = _compare(_clocked(2000.0, 1000.0), _clocked(1860.0, 1075.27),
                   floor_ratio=0.05)
    warned = [w for w in cmp["warnings"] if w["id"] == "environment"]
    check("with a 5% floor a 7% drop is warned about, naming the 5%",
          warned and "the 5%" in warned[0]["text"], cmp["warnings"])
    cmp = _compare(_clocked(2000.0, 1000.0), _clocked(2100.0, 952.4),
                   floor_ratio=0.25)
    check("and with a 25% floor a 5% move is not",
          "environment" not in _ids(cmp), cmp["warnings"])


def _set(values: list[float | None]) -> dict:
    """A merged report of len(values) repeats; None is a repeat without the row."""
    reps = [one([row("sync", count=1, self_ms=v, total_ms=v, max_ms=v,
                     detail="main")] if v is not None else [])
            for v in values]
    return report_mod.aggregate(reps)


def test_a_row_found_in_every_repeat_after_one_is_not_steady() -> None:
    before = _set([120.0, None, None, None, None])
    after = _set([118.0, 119.0, 120.0, 121.0, 122.0])
    r = _compare(before, after)["rows"][0]
    check("1 of 5 to 5 of 5 at the same milliseconds grew",
          r["change"] == "grew" and r["shares"] == {"before": "1/5", "after": "5/5"},
          r)
    md = compare_mod.to_markdown(_compare(before, after))
    check("Before carries its share", "120.0 (1/5)" in md, md)

    after = _set([104.0, 105.0, 105.0, 106.0, 105.0])
    r = _compare(before, after)["rows"][0]
    check("and 120 ms once against 105 every time did not shrink",
          r["change"] == "grew", r)

    same = _compare(_set([100.0] * 5), _set([101.0] * 5))["rows"][0]
    check("a row found every time on both sides is read as before",
          same["change"] == "steady" and "shares" not in same, same)


def test_an_end_anchor_that_never_matched_is_warned_about() -> None:
    good = one([])
    good["window"].update(start_anchor={"glob": "AppStart", "matches": 1},
                          end_anchor={"glob": "Screen.firstFrame", "matches": 1})
    bad = one([])
    bad["window"].update(start_anchor={"glob": "AppStart", "matches": 1},
                         end_anchor={"glob": "collection_loaded", "matches": 0})
    cmp = _compare(good, bad)
    warned = [w for w in cmp["warnings"] if w["id"] == "anchor-after"]
    check("anchor-after names the end anchor",
          warned and "end anchor `collection_loaded`" in warned[0]["text"],
          cmp["warnings"])
