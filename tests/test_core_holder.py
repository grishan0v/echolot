#!/usr/bin/env python3
"""Who held the core the main thread waited for, and the app's crowd (#416).

What `analyze` reads off a trace is held by the self-check on the fixture's
crowded variant ("runnable_starvation: who held the core …"). Here: what
`compare` makes of a report written before `runnable_starvation` named its
rows by `state`, and what the merge and the header make of `window.crowding`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report, row  # noqa: E402

HELD = (" · its core held by the app 45% (DefaultDispatch most), other processes "
        "48%, idle 7% · 0.3 other cores idle on average")


def old(r_ms: float, rplus_ms: float) -> dict:
    """A report from before: the state in `detail`, and `detail` in the identity."""
    return report([det("runnable_starvation", [
        row("main", r_ms, metric="total_ms", detail="state R"),
        row("main", rplus_ms, metric="total_ms", detail="state R+"),
    ], identity=["location", "detail"])])


def new(r_ms: float, rplus_ms: float) -> dict:
    """A report from now: the state a column of its own, `detail` evidence."""
    r = row("main", r_ms, metric="total_ms", detail="state R" + HELD)
    plus = row("main", rplus_ms, metric="total_ms", detail="state R+")
    r["state"], plus["state"] = "R", "R+"
    return report([det("runnable_starvation", [r, plus], identity=["location", "state"])])


def test_an_older_report_is_paired_state_by_state() -> None:
    """Paired on what both declare, the two would share `location` alone, and
    the main thread's `R` would meet its `R+`. The older side's state is read
    back out of its `detail` instead."""
    cmp = compare(old(100.0, 30.0), new(105.0, 400.0))
    by = {r["detail"]: r for r in cmp["rows"]}
    check("one row per state, named by it", sorted(by) == ["state R", "state R+"], by)
    check("R held still, R+ grew by its own number",
          by["state R"]["change"] == "steady" and by["state R+"]["delta_ms"] == 370.0, by)
    check("nothing appeared or vanished",
          cmp["summary"]["appeared"] == 0 and cmp["summary"]["vanished"] == 0,
          cmp["summary"])


def test_the_state_is_what_the_evidence_column_prints() -> None:
    """The evidence in `detail` changes from one round to the next; the state
    is the row's name, and the table prints the name."""
    text = compare_mod.to_markdown(compare(new(100.0, 30.0), new(400.0, 500.0)))
    for state in ("state R", "state R+"):
        check(f"the table names {state}", f"| {state} |" in text, text)
    check("and not the evidence", "DefaultDispatch most" not in text, text)


def test_crowding_merges_by_median_and_counts_its_repeats() -> None:
    def rep(peak: int, above: float) -> dict:
        return {"window": {"crowding": {"threads": 135, "cores": 8, "peak": peak,
                                        "above_cores_ms": above}}}
    merged = report_mod._merge_crowding([rep(27, 245.0), rep(28, 276.0), rep(26, 230.0),
                                         {"window": {}}])
    check("medians, the cores, and the repeats that had it",
          merged == {"threads": 135, "cores": 8, "peak": 27, "above_cores_ms": 245.0,
                     "runs": "3/4"}, merged)
    check("no repeat with it, no block",
          report_mod._merge_crowding([{"window": {}}]) is None)


def test_the_header_speaks_of_the_crowd_only_when_there_was_one() -> None:
    lines = report_mod._crowding_lines({"threads": 135, "cores": 8, "peak": 27,
                                        "above_cores_ms": 244.51})
    check("one line, the peak, the cores and how long",
          len(lines) == 1 and lines[0].startswith(
              "App threads: up to 27 running or ready at once on 8 cores, more than 8 "
              "for 245 ms of the window."), lines)
    check("never more than the cores, nothing said",
          report_mod._crowding_lines({"threads": 13, "cores": 9, "peak": 5,
                                      "above_cores_ms": 0.0}) == [])
    check("not recorded, nothing said", report_mod._crowding_lines(None) == [])
