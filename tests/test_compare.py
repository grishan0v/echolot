#!/usr/bin/env python3
"""Self-check for `echolot compare` — the delta between two Marker Reports.

Comparison is arithmetic, and arithmetic over two reports is always willing to
produce a number. What has to be pinned is everything around the number: when a
difference is called real, when it is called noise, when two reports may not be
compared at all, and when a pair of rows is the same thing under a new name.

Most cases build reports by hand — compare reads the report structure and never
touches a trace, so the fast checks need no trace_processor. `test_end_to_end`
runs the real pipeline, because the one failure none of the others would catch
is `analyze` writing a shape `compare` no longer reads; a few after it reuse
the reports it built.

    python -m pytest tests/test_compare.py
"""

from __future__ import annotations

import itertools
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import compare as compare_mod  # noqa: E402
from echolot import stats  # noqa: E402



# --- building reports by hand ----------------------------------------------

def row(location: str, value: float, *, metric: str = "self_ms",
        values: list[float] | None = None, count: float = 1.0,
        detail: str | None = None) -> dict:
    out: dict = {"location": location, "runs": "3/3", "count": count,
                 metric: value}
    if detail is not None:
        out["detail"] = detail
    if values:
        out["spread"] = {metric: {"min": min(values), "max": max(values),
                                  "values": values}}
    return out


def det(det_id: str, rows: list[dict], params: dict | None = None,
        identity: list[str] | None = None) -> dict:
    """`identity` defaults to absent, which is what a report written before the
    field existed carries, and what `report.identity_of` reads as `location`."""
    out = {"id": det_id, "title": det_id, "why": "", "rows": rows,
           "params": params or {}, "params_source": "default", "error": None}
    if identity is not None:
        out["identity"] = identity
    return out


def env(mhz: float | None = 1800.0, *, throttled: bool | None = False) -> dict:
    """A platform-state block. `mhz=None` is a trace recorded without it."""
    return {
        "cpu": {"mean_mhz": mhz, "min_mhz": 300.0, "max_mhz": 2400.0,
                "on_cpu_ms": 900.0, "measured_ms": 900.0} if mhz else None,
        "thermal": {"max_celsius": 40.0, "hottest_zone": "cpu-therm",
                    "throttled": throttled, "throttle_device":
                        "thermal-cpufreq-0" if throttled else None}
        if throttled is not None else None,
        "memory": None,
        "missing": [],
    }


def report(detectors: list[dict], *, window: dict | None = None,
           config: dict | None = None, runs: int = 3,
           environment: dict | None = None) -> dict:
    fired = [d["id"] for d in detectors if d["rows"]]
    return {
        "schema": 1,
        "generated_at": "2026-08-19T10:00:00+00:00",
        "trace": "fixture.perfetto-trace",
        "traces": [f"run{i}.perfetto-trace" for i in range(runs)] if runs > 1 else None,
        "runs": runs,
        "toolchain": {},
        "window": window or {"process": "com.example.app", "duration_ms": 1000.0},
        "config": config or {"sha": "aaaa", "defaults": False},
        # Absent unless asked for: that is what every report written before
        # the platform-state sources existed looks like, and the default here
        # keeps the other hundred cases honest about the shape they read.
        "environment": environment if environment is not None else {},
        "summary": {"detectors_run": len(detectors),
                    "detectors_fired": len(fired), "fired_ids": fired},
        "detectors": detectors,
    }


def compare(before: dict, after: dict, **kw) -> dict:
    return compare_mod.build(before, after, before_path="before.json",
                             after_path="after.json", **kw)


def changes(cmp: dict) -> dict[str, str]:
    return {r["location"]: r["change"] for r in cmp["rows"]}


def warned(cmp: dict) -> set[str]:
    return {w["id"] for w in cmp["warnings"]}


# --- what counts as movement ------------------------------------------------

def test_grew_shrank_steady() -> None:
    """The three verdicts on a row that exists in both, and their order."""
    before = report([det("main_thread_block", [
        row("A", 100.0), row("B", 100.0), row("C", 100.0)])])
    after = report([det("main_thread_block", [
        row("A", 200.0), row("B", 50.0), row("C", 104.0)])])
    cmp = compare(before, after)

    check("grew / shrank / steady", changes(cmp) ==
          {"A": "grew", "B": "shrank", "C": "steady"}, str(changes(cmp)))
    check("summary counts the moves",
          (cmp["summary"]["moved"], cmp["summary"]["steady"]) == (2, 1),
          str(cmp["summary"]))

    first = cmp["rows"][0]
    check("the biggest mover is first", first["location"] == "A", first["location"])
    check("delta is after minus before", first["delta_ms"] == 100.0,
          str(first["delta_ms"]))
    check("ratio is after over before", first["ratio"] == 2.0, str(first["ratio"]))
    check("shrank keeps its sign",
          next(r for r in cmp["rows"] if r["location"] == "B")["delta_ms"] == -50.0)


def test_floor_has_two_halves() -> None:
    """Absolute floor and relative floor, each doing the job the other cannot."""
    before = report([det("d", [row("big", 900.0), row("small", 20.0)])])
    after = report([det("d", [row("big", 940.0), row("small", 26.0)])])
    cmp = compare(before, after)

    check("40 ms on 900 is within the relative floor",
          changes(cmp)["big"] == "steady", str(changes(cmp)))
    check("6 ms on 20 clears the absolute floor",
          changes(cmp)["small"] == "grew", str(changes(cmp)))

    # And the absolute floor holding back what the relative one lets through:
    # +3 ms on 10 is +30%, past the 10% floor and under the 5 ms one (#277).
    before = report([det("d", [row("tiny", 10.0)])])
    after = report([det("d", [row("tiny", 13.0)])])
    check("3 ms on 10 is within the absolute floor",
          changes(compare(before, after))["tiny"] == "steady",
          str(changes(compare(before, after))))
    check("and only the absolute floor holds it",
          changes(compare(before, after, floor_ms=0))["tiny"] == "grew",
          str(changes(compare(before, after, floor_ms=0))))


def test_floor_is_configurable() -> None:
    before = report([det("d", [row("A", 100.0)])])
    after = report([det("d", [row("A", 130.0)])])
    check("30% is a move by default", changes(compare(before, after))["A"] == "grew")
    check("and is not with the floor raised",
          changes(compare(before, after, floor_ratio=0.5))["A"] == "steady")


def test_appeared_and_vanished() -> None:
    before = report([det("d", [row("gone", 88.0)])])
    after = report([det("d", [row("new", 1402.0)])])
    cmp = compare(before, after)

    check("a row only in the later report appeared",
          changes(cmp)["new"] == "appeared", str(changes(cmp)))
    check("a row only in the earlier one is gone",
          changes(cmp)["gone"] == "vanished", str(changes(cmp)))
    check("appeared sorts by what it is worth now",
          cmp["rows"][0]["location"] == "new", cmp["rows"][0]["location"])
    check("appeared has no before side",
          cmp["rows"][0]["before"] is None)
    check("and no ratio, because there is nothing to divide by",
          cmp["rows"][0]["ratio"] is None)


# --- the same thing under a new name ----------------------------------------

def test_family_match() -> None:
    """One worker of a pool handing over to another is not a finding."""
    before = report([det("uninstrumented_cpu", [
        row("DefaultDispatcher-worker-2", 300.0, metric="total_ms")])])
    after = report([det("uninstrumented_cpu", [
        row("DefaultDispatcher-worker-5", 340.0, metric="total_ms")])])
    cmp = compare(before, after)

    check("one row, not one gone and one new", len(cmp["rows"]) == 1,
          str(len(cmp["rows"])))
    check("matched through the name family",
          cmp["rows"][0]["matched_by"] == "family", cmp["rows"][0]["matched_by"])
    check("and it is a plain move", cmp["rows"][0]["change"] == "grew",
          cmp["rows"][0]["change"])


def test_family_refuses_to_guess() -> None:
    """Two candidates on one side: there is no honest pairing, so make none."""
    before = report([det("d", [
        row("worker-2", 300.0, metric="total_ms"),
        row("worker-3", 200.0, metric="total_ms")])])
    after = report([det("d", [row("worker-5", 340.0, metric="total_ms")])])
    cmp = compare(before, after)

    check("ambiguous families are left unpaired",
          sorted(changes(cmp).values()) == ["appeared", "vanished", "vanished"],
          str(changes(cmp)))
    check("nothing is matched by family",
          all(r["matched_by"] == "exact" for r in cmp["rows"]))


# --- one location, several rows ---------------------------------------------
#
# Most of the shipped detectors declare a second column in `@identity`, so
# a location carrying more than one row is ordinary. Paired on the name alone
# they collapsed onto whichever came last and the leftovers paired with it —
# which subtracts one phenomenon from another and prints the difference as a
# finding.

def starvation(rows: list[dict]) -> dict:
    """`runnable_starvation` as it really is: one row per thread AND state."""
    return report([det("runnable_starvation", rows,
                       identity=["location", "detail"])])


def test_rows_of_one_location_are_paired_by_identity() -> None:
    """The bug: `state R` before subtracted from `state R+` after.

    One thread, two states, both over the threshold. `R+` really did grow, and
    `R` really did not move. Keyed on the thread name, both before-rows found
    the same after-row and the report claimed two moves — one of them a 300 ms
    regression assembled out of two unrelated numbers, while the real `R`
    figure appeared nowhere at all.
    """
    before = starvation([
        row("main", 100.0, metric="total_ms", detail="state R"),
        row("main", 30.0, metric="total_ms", detail="state R+"),
    ])
    after = starvation([
        row("main", 105.0, metric="total_ms", detail="state R"),
        row("main", 400.0, metric="total_ms", detail="state R+"),
    ])
    cmp = compare(before, after)
    by_detail = {r["detail"]: r for r in cmp["rows"]}

    check("both rows survive as themselves", sorted(by_detail) ==
          ["state R", "state R+"], str(sorted(by_detail)))
    check("the state that grew is the one reported as grown",
          by_detail["state R+"]["change"] == "grew",
          str(by_detail["state R+"]))
    check("and it grew by its own number, not by somebody else's",
          by_detail["state R+"]["delta_ms"] == 370.0,
          str(by_detail["state R+"]["delta_ms"]))
    check("the state that held still is steady",
          by_detail["state R"]["change"] == "steady", str(by_detail["state R"]))
    check("so exactly one row moved", cmp["summary"]["moved"] == 1,
          str(cmp["summary"]))
    check("and nothing was invented or lost",
          cmp["summary"]["appeared"] == 0 and cmp["summary"]["vanished"] == 0,
          str(cmp["summary"]))


def test_the_second_column_reaches_the_table() -> None:
    """Pairing them right is half of it; printing them apart is the other half.

    Two rows of one location render as the same row twice unless what tells
    them apart is on the page. A reader cannot act on `main 100 → 105` sitting
    above `main 30 → 400`.
    """
    before = starvation([
        row("main", 100.0, metric="total_ms", detail="state R"),
        row("main", 30.0, metric="total_ms", detail="state R+"),
    ])
    after = starvation([
        row("main", 400.0, metric="total_ms", detail="state R"),
        row("main", 500.0, metric="total_ms", detail="state R+"),
    ])
    text = compare_mod.to_markdown(compare(before, after))

    check("the Evidence column is there", "Evidence" in text, text)
    for state in ("state R", "state R+"):
        check(f"and it names {state}", f"| {state} |" in text, text)


def test_a_row_that_only_one_side_has_is_still_new() -> None:
    """The pairing must not go the other way and match too much.

    A thread that was only ever runnable and then starts being preempted
    grows a second row. That row is new, and the one already there did not
    become it.
    """
    before = starvation([row("main", 100.0, metric="total_ms", detail="state R")])
    after = starvation([
        row("main", 100.0, metric="total_ms", detail="state R"),
        row("main", 250.0, metric="total_ms", detail="state R+"),
    ])
    cmp = compare(before, after)
    by_detail = {r["detail"]: r["change"] for r in cmp["rows"]}

    check("the new state is new", by_detail.get("state R+") == "appeared",
          str(by_detail))
    check("the old one is untouched", by_detail.get("state R") == "steady",
          str(by_detail))


def test_a_report_without_identity_is_matched_on_the_name_alone() -> None:
    """A report written before the field existed says `location`, and means it.

    Such a report genuinely cannot tell two of its own rows apart, and reading
    a distinction into it that it never recorded would be inventing one.
    """
    before = report([det("d", [row("A", 100.0, metric="total_ms",
                                   detail="was one thing")])])
    after = report([det("d", [row("A", 300.0, metric="total_ms",
                                  detail="now says another")])])
    cmp = compare(before, after)

    check("one row, paired on the name", len(cmp["rows"]) == 1,
          str(cmp["rows"]))
    check("it moved", cmp["rows"][0]["change"] == "grew", str(cmp["rows"][0]))
    check("and the evidence that varies is not printed as a difference",
          "detail" not in cmp["rows"][0], str(cmp["rows"][0]))


def test_only_what_both_reports_declare_is_matched_on() -> None:
    """One side upgraded, the other not: match on what they share.

    The older report has no second column to match on, so the newer one's is
    not used either — otherwise every row of the pair would read as one gone
    and one new for no reason but the version that wrote it.
    """
    before = report([det("d", [row("A", 100.0, metric="total_ms",
                                   detail="x")])])
    after = report([det("d", [row("A", 300.0, metric="total_ms", detail="y")],
                        identity=["location", "detail"])])
    cmp = compare(before, after)

    check("still one row", len(cmp["rows"]) == 1, str(cmp["rows"]))
    check("and it is a move, not a swap",
          cmp["rows"][0]["change"] == "grew", str(cmp["rows"][0]))


# --- did the repeats move, or only the median -------------------------------

def one_row(before: list[float], after: list[float]) -> dict:
    """One row compared across two sets of runs, each with its values."""
    b = report([det("d", [row("A", median(before), values=before)])],
               runs=len(before))
    a = report([det("d", [row("A", median(after), values=after)])],
               runs=len(after))
    return compare(b, a)


def test_the_orders_are_counted_not_guessed() -> None:
    """What the interval stands on, against every order written out.

    Small enough to enumerate: each way of placing n runs before and m after
    in order of speed, and in each the pairs where the run after was the
    slower one. The before run in place s, with i before runs ahead of it,
    has m - s + i runs after behind it.
    """
    for n in range(1, 6):
        for m in range(1, 6):
            tally = [0] * (n * m + 1)
            for places in itertools.combinations(range(n + m), n):
                tally[sum(m - s + i for i, s in enumerate(places))] += 1
            check(f"{n} before, {m} after: every order counted",
                  stats._orderings(n, m) == tuple(tally),
                  f"{stats._orderings(n, m)} vs {tuple(tally)}")
    counts = stats._orderings(40, 40)
    check("forty a side: the counts add up to the orders",
          sum(counts) == math.comb(80, 40))
    check("and are symmetric", counts == counts[::-1])


def test_the_cut_is_the_textbook_one() -> None:
    """The critical values of the Mann–Whitney test, two-sided at 5%.

    The table printed in the back of every statistics book, read here as how
    many differences come off each end. None where even the widest interval
    is not 95% sure.
    """
    table = {(5, 5): 2, (6, 6): 5, (7, 7): 8, (8, 8): 13, (10, 10): 23,
             (15, 15): 64, (20, 20): 127, (4, 4): 0, (3, 5): 0, (5, 3): 0,
             (2, 8): 0, (1, 39): 0,
             (3, 3): None, (3, 4): None, (2, 7): None, (1, 38): None}
    for (n, m), want in table.items():
        got = stats.cut(n, m)
        check(f"{n} before, {m} after: {want}", got == want, str(got))


def test_many_runs_take_the_curve_and_err_wide() -> None:
    """Past a hundred runs a side the count would take seconds, then minutes.

    The curve has to agree with the count where both can be had, and where
    it does not, keep one difference more — a wider interval, never a
    narrower one. Five hundred a side must come back at once.
    """
    for n, m in [(20, 20), (30, 30), (40, 40), (50, 150), (80, 80), (100, 100)]:
        counted = stats.cut(n, m)
        curve = stats._cut_by_curve(n, m)
        check(f"{n} before, {m} after: the curve is the count or one short",
              counted - 1 <= curve <= counted, f"{curve} vs {counted}")
    check("a hundred a side is still counted",
          100 * 100 * 100 <= stats.COUNTED_UP_TO)
    check("five hundred a side takes the curve",
          stats.cut(500, 500) == stats._cut_by_curve(500, 500))


def test_the_interval_on_a_hand_count() -> None:
    """Five runs a side, small enough to check with a pencil.

    Twenty-five differences, run after minus run before: 8 to 21, median 14.
    Two come off each end, so the interval is the third smallest to the
    third largest.
    """
    cmp = one_row([1.0, 2.0, 3.0, 4.0, 5.0], [13.0, 16.0, 17.0, 19.0, 22.0])
    r = cmp["rows"][0]
    check("the move and the range it lies in",
          {k: r["shift"][k] for k in ("ms", "low_ms", "high_ms")}
          == {"ms": 14.0, "low_ms": 10.0, "high_ms": 19.0}, str(r["shift"]))
    check("above zero, so it holds", r["holds"] is True, str(r["holds"]))
    check("the level is stated once, at the top", cmp["confidence"] == 0.95,
          str(cmp.get("confidence")))
    check("and the verdict it replaced is gone", "overlap" not in r, str(r))


def test_an_interval_that_ends_at_zero_does_not_hold() -> None:
    """Zero on the edge is zero inside: the runs allow for no move at all."""
    r = one_row([1.0, 2.0, 3.0, 4.0, 5.0], [3.0, 6.0, 7.0, 9.0, 12.0])["rows"][0]
    check("the interval starts at zero",
          r["shift"] and r["shift"]["low_ms"] == 0.0, str(r["shift"]))
    check("and does not hold", r["holds"] is False, str(r["holds"]))


def test_one_slow_run_does_not_hide_a_move() -> None:
    """What the range test got wrong, and why it was replaced.

    Fifteen runs a side, every run after about 30 ms slower than the runs
    before — and one run before that caught a hiccup and came in slower than
    all of them. The min–max ranges touch, and the range test called that an
    overlap. The move is plain in every pair the hiccup is not in.
    """
    before = [100.0 + i for i in range(14)] + [400.0]
    after = [130.0 + i for i in range(15)]
    r = one_row(before, after)["rows"][0]
    check("the ranges touch", max(before) >= min(after))
    check("the row grew", r["change"] == "grew", r["change"])
    check("and the move holds anyway", r["holds"] is True, str(r))
    check("all of the interval above zero",
          r["shift"] and r["shift"]["low_ms"] > 0, str(r["shift"]))


def test_the_same_runs_do_not_hold() -> None:
    """Two sets of the same numbers: whatever the order, nothing moved."""
    runs = [100.0, 131.0, 95.0, 120.0, 88.0, 142.0]
    r = one_row(runs, list(reversed(runs)))["rows"][0]
    check("the move is zero", r["shift"] and r["shift"]["ms"] == 0.0,
          str(r["shift"]))
    check("the interval runs through it",
          r["shift"] and r["shift"]["low_ms"] < 0 < r["shift"]["high_ms"],
          str(r["shift"]))
    check("so it does not hold", r["holds"] is False, str(r["holds"]))


def test_a_move_the_runs_cannot_settle() -> None:
    """Medians moved, but the runs disagree among themselves by more."""
    cmp = one_row([10.0, 100.0, 300.0, 90.0, 180.0],
                  [12.0, 150.0, 320.0, 130.0, 60.0])
    r = cmp["rows"][0]
    check("the move is still reported", r["change"] == "grew", r["change"])
    check("and does not hold", r["holds"] is False, str(r))


def test_too_few_runs_for_a_verdict() -> None:
    """Three a side cannot be 95% sure of anything, and the page says so.

    The range test gave these a verdict anyway, 90% sure at best. Four a side
    is enough, and so is three against five.
    """
    cmp = one_row([100.0, 102.0, 104.0], [200.0, 205.0, 210.0])
    r = cmp["rows"][0]
    check("no verdict", r["holds"] is None and r["shift"] is None, str(r))
    check("and the reason is at the top", "few" in warned(cmp), str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "few")
    check("naming what would be enough", "four a side" in text, text)

    four = one_row([100.0, 102.0, 104.0, 101.0], [200.0, 205.0, 210.0, 207.0])
    check("four a side has one", four["rows"][0]["holds"] is True,
          str(four["rows"][0]))
    check("and no warning", "few" not in warned(four), str(warned(four)))
    three_five = one_row([100.0, 102.0, 104.0],
                         [200.0, 205.0, 210.0, 207.0, 203.0])
    check("three against five has one", three_five["rows"][0]["holds"] is True,
          str(three_five["rows"][0]))


# --- what the runs can resolve ----------------------------------------------

def test_resolves_is_where_the_verdict_turns() -> None:
    """Exact, not an estimate.

    A run after minus a run before is the move of the medians plus what the
    two strayed from their own medians, so the interval is the strays' own,
    moved along. A move of the medians just past `resolves` holds, and one
    just short of it does not, whatever the runs.
    """
    before = [16.3, 38.0, 47.3, 55.1, 78.3]
    shape = [33.9, 95.2, 121.9, 140.3, 209.9]
    reach = stats.resolves(before, shape)
    gap = median(shape) - median(before)
    for past, want in ((0.05, True), (-0.05, False)):
        after = [y + reach + past - gap for y in shape]
        r = one_row(before, after)["rows"][0]
        check(f"a move {past:+} ms from {reach} ms {'holds' if want else 'does not'}",
              r["holds"] is want, str(r["shift"]))


def test_one_set_resolves_the_same_both_ways() -> None:
    """Against as many runs spread the same way, before there is a second set.

    One slow run in five sets it: four pairs with it at each end, and five a
    side only takes two off each.
    """
    runs = [118.2, 121.0, 125.4, 133.7, 340.1]
    check("up and down agree for one set",
          stats.resolves(runs) == stats.resolves(runs, upward=False),
          f"{stats.resolves(runs)} vs {stats.resolves(runs, upward=False)}")
    check("and the slow run is what it comes to", stats.resolves(runs) == 214.7,
          str(stats.resolves(runs)))
    check("fewer than four is no answer", stats.resolves(runs[:3]) is None)


def test_runs_needed_goes_by_the_square_root() -> None:
    check("half the move the runs resolve takes four times the runs",
          stats.runs_needed(5, 5, 10.0, 5.0) == 20)
    check("five against ten count as about seven a side",
          stats.runs_needed(5, 10, 10.0, 5.0) == 27)
    check("and it is always at least one run more",
          stats.runs_needed(5, 5, 10.0, 9.99) == 6)
    check("a move already resolved needs nothing",
          stats.runs_needed(5, 5, 10.0, 12.0) is None)
    check("a move of nothing is never settled",
          stats.runs_needed(5, 5, 10.0, 0.0) is None)


def test_a_move_that_does_not_hold_says_how_many_runs() -> None:
    """What "record another round" left out: five more, or five hundred."""
    cmp = one_row([16.3, 38.0, 47.3, 55.1, 78.3],
                  [33.9, 95.2, 121.9, 140.3, 209.9])
    r = cmp["rows"][0]
    check("the move does not hold", r["holds"] is False, str(r))
    check("these runs resolve 88 ms upward", r["shift"]["resolves_ms"] == 88.0,
          str(r["shift"]))
    check("so 74.6 needs about seven a side", r["shift"]["runs_needed"] == 7,
          str(r["shift"]))
    text = compare_mod.to_markdown(cmp)
    check("and the cell says so",
          "| no, -13.4 … +162.6 · ~7 runs a side |" in text, text)

    held = one_row([10.4, 11.3, 12.1, 13.0, 14.0],
                   [843.4, 861.0, 883.4, 897.2, 923.4])["rows"][0]
    check("a move that holds needs no more runs",
          held["shift"]["runs_needed"] is None
          and held["shift"]["resolves_ms"] == 40.0, str(held["shift"]))


def test_a_single_trace_has_no_verdict() -> None:
    before = report([det("d", [row("A", 100.0)])], runs=1)
    after = report([det("d", [row("A", 200.0)])], runs=1)
    cmp = compare(before, after)
    check("no spread means no verdict",
          cmp["rows"][0]["holds"] is None and cmp["rows"][0]["shift"] is None,
          str(cmp["rows"][0]))
    check("and a single trace is said out loud", "single" in warned(cmp),
          str(warned(cmp)))
    check("once: too few is the same news", "few" not in warned(cmp),
          str(warned(cmp)))


def test_the_verdict_reaches_the_table() -> None:
    """The word first, then the range it was read from."""
    text = compare_mod.to_markdown(
        one_row([1.0, 2.0, 3.0, 4.0, 5.0], [13.0, 16.0, 17.0, 19.0, 22.0]))
    check("the Holds column", "| Holds |" in text, text)
    check("with the verdict and its range", "| yes, +10.0 … +19.0 |" in text, text)
    shaky = compare_mod.to_markdown(one_row(
        [10.0, 100.0, 300.0, 90.0, 180.0], [12.0, 150.0, 320.0, 130.0, 60.0]))
    check("a move that does not hold says no", "| no, -" in shaky, shaky)


# --- when two reports may not be compared -----------------------------------

def test_thresholds_moved() -> None:
    """The bar decides which rows exist, so a moved bar invents rows."""
    before = report([det("d", [row("A", 100.0)], params={"min_slice_ms": 16})])
    after = report([det("d", [row("A", 100.0), row("B", 20.0)],
                        params={"min_slice_ms": 5})])
    cmp = compare(before, after)

    check("a threshold change is a warning", "thresholds" in warned(cmp),
          str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "thresholds")
    check("naming the parameter and both values",
          "min_slice_ms" in text and "16" in text and "5" in text, text)


def test_process_differs() -> None:
    before = report([det("d", [row("A", 100.0)])],
                    window={"process": "com.example.app", "duration_ms": 1000.0})
    after = report([det("d", [row("A", 100.0)])],
                   window={"process": "com.other.app", "duration_ms": 1000.0})
    cmp = compare(before, after)
    check("two apps are not comparable", cmp["comparable"] is False)
    check("and the reason is named", "process" in warned(cmp), str(warned(cmp)))


def test_anchor_never_matched() -> None:
    before = report([det("d", [row("A", 100.0)])], window={
        "process": "com.example.app", "duration_ms": 1000.0,
        "start_anchor": {"glob": "AppStart", "matches": 0}})
    after = report([det("d", [row("A", 100.0)])])
    check("a window that is the whole trace is a warning",
          "anchor-before" in warned(compare(before, after)),
          str(warned(compare(before, after))))


def test_config_and_defaults() -> None:
    before = report([det("d", [row("A", 100.0)])],
                    config={"sha": "aaaa", "defaults": False})
    after = report([det("d", [row("A", 100.0)])],
                   config={"sha": "bbbb", "defaults": True})
    w = warned(compare(before, after))
    check("an edited config is a warning", "config" in w, str(w))
    check("--defaults on one side only is a warning", "defaults" in w, str(w))


def test_unequal_repeats() -> None:
    before = report([det("d", [row("A", 100.0)])], runs=3)
    after = report([det("d", [row("A", 100.0)])], runs=10)
    check("different repeat counts are said out loud",
          "runs" in warned(compare(before, after)),
          str(warned(compare(before, after))))


def test_a_steady_clock_says_nothing() -> None:
    """Silence has to be earned, and a settled device earns it."""
    before = report([det("d", [row("A", 100.0)])], environment=env(1800.0))
    after = report([det("d", [row("A", 100.0)])], environment=env(1750.0))
    w = warned(compare(before, after))
    check("a 3% drift is not worth a word", "environment" not in w, str(w))


def test_a_dropped_clock_is_not_a_regression() -> None:
    """The failure this whole block exists for.

    The same code on a slower machine grows in the table exactly the way a
    regression does. The comparison cannot refuse to subtract — the numbers
    stay on the page — but it must not let the reader take the table for a
    statement about the app.
    """
    before = report([det("d", [row("A", 100.0)])], environment=env(1800.0))
    after = report([det("d", [row("A", 258.0)])], environment=env(700.0))
    cmp = compare(before, after)

    check("the row still grows — nothing is hidden",
          changes(cmp)["A"] == "grew", str(changes(cmp)))
    check("and the clock is named as the reason it might not be the app",
          "environment" in warned(cmp), str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "environment")
    check("with both speeds in it", "1800" in text and "700" in text, text)


def test_a_clock_nobody_recorded_is_unknown_not_steady() -> None:
    """The distinction the block is built on: not measured is a third answer."""
    both_old = compare(report([det("d", [row("A", 100.0)])]),
                       report([det("d", [row("A", 100.0)])]))
    check("two reports from before the sources existed stay quiet",
          "environment" not in warned(both_old), str(warned(both_old)))

    half = compare(report([det("d", [row("A", 100.0)])], environment=env(1800.0)),
                   report([det("d", [row("A", 100.0)])], environment=env(None)))
    check("one side without a clock is said out loud",
          "environment" in warned(half), str(warned(half)))
    text = next(w["text"] for w in half["warnings"] if w["id"] == "environment")
    check("and it says which side and that the answer is unknown",
          "after" in text and "unknown" in text, text)


# The block `analyze` writes for a trace recorded without the platform-state
# sources. Never an empty dict: every key is there, and every measurement in
# it is null.
UNMEASURED = {"cpu": None, "thermal": None, "memory": None,
              "missing": ["cpu", "memory", "thermal"]}


def test_two_rounds_that_measured_nothing_say_nothing_about_it() -> None:
    """The pair the quiet case was written for, in the shape it really has.

    The case above builds its old reports with no `environment` at all, which
    is what a report written before the block existed looks like. A trace
    recorded without the sources looks different: `analyze` writes the block
    anyway, with every measurement null. A test for an empty dict let that
    pair through, and every comparison of two such rounds opened with a
    warning about a clock neither side had tried to read.
    """
    quiet = compare(
        report([det("d", [row("A", 100.0)])], environment=dict(UNMEASURED)),
        report([det("d", [row("A", 100.0)])], environment=dict(UNMEASURED)))
    w = warned(quiet)
    check("no clock warning between two rounds that measured nothing",
          "environment" not in w, str(w))
    check("and no thermal one either", "environment-thermal" not in w, str(w))

    half = compare(
        report([det("d", [row("A", 100.0)])], environment=env(1800.0)),
        report([det("d", [row("A", 100.0)])], environment=dict(UNMEASURED)))
    check("one measured round against one that measured nothing is still said",
          "environment" in warned(half), str(warned(half)))


def test_throttling_on_one_side_only() -> None:
    before = report([det("d", [row("A", 100.0)])],
                    environment=env(1800.0, throttled=False))
    after = report([det("d", [row("A", 140.0)])],
                   environment=env(1780.0, throttled=True))
    cmp = compare(before, after)
    check("a throttled round is not comparable to a cool one",
          "environment-thermal" in warned(cmp), str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"]
                if w["id"] == "environment-thermal")
    check("and the slowed side is named", "after" in text, text)


def sampled(hz: int | None = 100, *, started: bool = True,
            runs: str | None = None) -> dict:
    """A platform-state block from a recording that ran a callstack sampler."""
    sampling = {"hz": hz, "started": started,
                "samples": 300 if started else 0,
                "with_stack": 290 if started else 0}
    if runs:
        sampling["runs"] = runs
    return {**env(1800.0), "sampling": sampling}


def plain() -> dict:
    """The same block from a recording nothing sampled."""
    return {**env(1800.0), "sampling": None}


def test_a_sampler_on_one_side_only_is_said() -> None:
    """A cause outside the code that makes one side slower, like a clock that fell."""
    cmp = compare(report([det("d", [row("A", 100.0)])], environment=plain()),
                  report([det("d", [row("A", 130.0)])], environment=sampled()))
    check("a round sampled against a plain one is warned about",
          "sampling" in warned(cmp), str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "sampling")
    check("and the warning says which side ran it, and at what rate",
          "off before and on at 100 Hz after" in text, text)


def test_rounds_sampled_alike_say_nothing() -> None:
    for label, side in (("both sampled", sampled), ("both plain", plain)):
        cmp = compare(report([det("d", [row("A", 100.0)])], environment=side()),
                      report([det("d", [row("A", 100.0)])], environment=side()))
        check(f"{label}: no word about sampling",
              "sampling" not in warned(cmp), str(warned(cmp)))


def test_another_rate_is_another_cost() -> None:
    cmp = compare(report([det("d", [row("A", 100.0)])], environment=sampled(100)),
                  report([det("d", [row("A", 100.0)])], environment=sampled(250)))
    text = next((w["text"] for w in cmp["warnings"] if w["id"] == "sampling"), "")
    check("two rates are two different costs, and both are named",
          "at 100 Hz before" in text and "at 250 Hz after" in text, text)


def test_a_sampler_that_never_ran_slowed_nothing() -> None:
    """Asked for on a device without a sampler: the round ran like a plain one."""
    cmp = compare(report([det("d", [row("A", 100.0)])], environment=plain()),
                  report([det("d", [row("A", 100.0)])],
                         environment=sampled(started=False)))
    check("no warning against a plain round",
          "sampling" not in warned(cmp), str(warned(cmp)))


def test_a_report_from_before_sampling_is_unknown_not_plain() -> None:
    """No `sampling` key at all is a report older than the field: nothing to say."""
    cmp = compare(report([det("d", [row("A", 100.0)])], environment=env(1800.0)),
                  report([det("d", [row("A", 100.0)])], environment=sampled()))
    check("an old report against a sampled one stays quiet about sampling",
          "sampling" not in warned(cmp), str(warned(cmp)))


def test_a_half_sampled_set_is_named_as_one() -> None:
    cmp = compare(
        report([det("d", [row("A", 100.0)])], runs=6, environment=sampled(runs="3/6")),
        report([det("d", [row("A", 100.0)])], runs=6, environment=sampled(runs="6/6")))
    text = next((w["text"] for w in cmp["warnings"] if w["id"] == "sampling"), "")
    check("the set sampled in only some repeats is said to be",
          "on in 3 of 6 repeats at 100 Hz before and on at 100 Hz after" in text,
          text or str(warned(cmp)))


def test_detector_sets_differ() -> None:
    before = report([det("one", [row("A", 100.0)])])
    after = report([det("one", [row("A", 100.0)]), det("two", [row("B", 50.0)])])
    check("a detector that ran once is a warning",
          "detectors" in warned(compare(before, after)),
          str(warned(compare(before, after))))


# --- the rest of the report -------------------------------------------------

def test_planted_markers() -> None:
    """A marker the hunt added between rounds is not a new regression."""
    before = report([det("main_thread_block", [row("A", 100.0)])])
    after = report([det("main_thread_block", [
        row("A", 100.0), row("AGENTTMP_loadTeams", 400.0)])])

    plain = compare(before, after)
    check("without the prefix it is just a new row",
          "instrumentation" not in warned(plain), str(warned(plain)))

    told = compare(before, after, temp_prefix="AGENTTMP_")
    check("with it, the row is named as planted",
          "instrumentation" in warned(told), str(warned(told)))
    text = next(w["text"] for w in told["warnings"] if w["id"] == "instrumentation")
    check("and the warning names it", "AGENTTMP_loadTeams" in text, text)
    check("the row stays in the table",
          changes(told)["AGENTTMP_loadTeams"] == "appeared", str(changes(told)))


def test_state_changed() -> None:
    before = report([det("binder_txn", [])])
    after = report([det("binder_txn", [row("t", 214.0, metric="total_ms")])])
    cmp = compare(before, after)
    changed = cmp["summary"]["state_changed"]
    check("silence turning into rows is recorded", len(changed) == 1, str(changed))
    check("with both states", changed and changed[0]["before"] == "silent",
          str(changed))


def test_metric_falls_back_to_total() -> None:
    before = report([det("d", [row("A", 100.0, metric="total_ms")])])
    after = report([det("d", [row("A", 200.0, metric="total_ms")])])
    check("a detector without self time is judged by total",
          compare(before, after)["rows"][0]["metric"] == "total_ms")


def test_window_delta() -> None:
    before = report([det("d", [])], window={"process": "p", "duration_ms": 1184.0})
    after = report([det("d", [])], window={"process": "p", "duration_ms": 2960.0})
    w = compare(before, after)["window"]
    check("the window carries its own delta", w["delta_ms"] == 1776.0, str(w))
    check("and its ratio", w["ratio"] == 2.5, str(w))


def test_markdown() -> None:
    before = report([det("main_thread_block", [row("A", 100.0), row("C", 100.0)],
                         params={"min_slice_ms": 16})])
    after = report([det("main_thread_block", [row("A", 900.0), row("C", 101.0)],
                        params={"min_slice_ms": 5})])
    text = compare_mod.to_markdown(compare(before, after))

    check("the moved row is in the table", "| A |" in text, text[:200])
    check("the steady one is not", "| C |" not in text)
    check("but it is listed as steady", "## Steady" in text and "`C`" in text)
    check("the threshold warning is at the top",
          text.index("⚠️") < text.index("## What moved"))
    check("no python None reaches the page", "None" not in text)


def test_markdown_nothing_moved() -> None:
    before = report([det("d", [row("A", 100.0)])])
    after = report([det("d", [row("A", 101.0)])])
    text = compare_mod.to_markdown(compare(before, after))
    check("a comparison with no movement says so",
          "## Nothing moved" in text, text[:300])


# --- the shape analyze actually writes --------------------------------------

FIXTURE_CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""


def test_end_to_end(tmp_path: Path) -> None:
    """The real pipeline: analyze writes it, compare reads it.

    Everything above builds reports by hand, which pins the logic and nothing
    about the contract between the two commands. This is the case that fails
    when a column is renamed on one side only.
    """
    env = dict(os.environ, ECHOLOT_NO_RECORD="1")
    trace = tmp_path / "fixture.perfetto-trace"
    run = subprocess.run([sys.executable, "-m", "echolot.fixture", str(trace)],
                         capture_output=True, text=True, env=env)
    if run.returncode != 0:
        check("the fixture builds", False, run.stderr[-300:])
        return
    (tmp_path / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")

    def cli(*argv) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                              capture_output=True, text=True, cwd=tmp_path, env=env)

    done = cli("analyze", str(trace), str(trace), str(trace),
               "-c", "echolot.yml")
    if done.returncode != 0:
        check("analyze runs on the fixture", False, done.stderr[-400:])
        return

    written = json.loads((tmp_path / ".echolot/out/report.json").read_text())
    banded = [r for d in written["detectors"] for r in d["rows"] if r.get("spread")]
    check("analyze writes the spread compare reads", bool(banded))
    if banded:
        band = next(iter(banded[0]["spread"].values()))
        check("with min, max and the per-run values",
              set(band) == {"min", "max", "values"}, str(sorted(band)))
        check("one value per repeat the row was found in",
              len(band["values"]) == int(banded[0]["runs"].split("/")[0]),
              f"{band['values']} vs {banded[0]['runs']}")

    # The same report with one worker renamed: a real report shape going
    # through the family pass.
    before = tmp_path / "before.json"
    before.write_text(json.dumps(written), encoding="utf-8")
    after = json.loads(json.dumps(written))
    for d in after["detectors"]:
        for r in d["rows"]:
            r["location"] = r["location"].replace("DefaultDispatcher-worker-1",
                                                  "DefaultDispatcher-worker-3")
    (tmp_path / "after.json").write_text(json.dumps(after), encoding="utf-8")

    done = cli("compare", "before.json", "after.json", "-c", "echolot.yml")
    check("compare exits 0", done.returncode == 0, done.stderr[-400:])
    check("and writes both files",
          (tmp_path / ".echolot/out/comparison.json").exists()
          and (tmp_path / ".echolot/out/comparison.md").exists())

    if (tmp_path / ".echolot/out/comparison.json").exists():
        cmp = json.loads((tmp_path / ".echolot/out/comparison.json").read_text())
        worker = [r for r in cmp["rows"] if "DefaultDispatcher" in r["location"]]
        check("the renamed worker is one row, matched by family",
              len(worker) == 1 and worker[0]["matched_by"] == "family",
              str([(r["location"], r["matched_by"]) for r in worker]))
        check("and nothing is reported as moved",
              cmp["summary"]["moved"] == 0, str(cmp["summary"]))

    # A comparison is not a Marker Report, and feeding one back in is a mistake
    # worth naming rather than a stack trace.
    done = cli("compare", ".echolot/out/comparison.json", "before.json",
               "-c", "echolot.yml")
    check("a comparison is refused as input", done.returncode == 2,
          f"exit {done.returncode}")
    check("with a sentence that says why",
          "not a Marker Report" in done.stderr, done.stderr[-200:])


def test_a_trace_without_the_sources_compares_as_unmeasured(
        marker_report, tmp_path: Path) -> None:
    """The same quiet pair, written by the pipeline instead of by hand.

    What broke was the contract between the two commands: `analyze` wrote a
    shape that `compare` did not read as "nothing recorded". Merged as well
    as single, because merging repeats writes the block a second time.
    """
    import copy

    from echolot import fixture, selftest
    from echolot import report as report_mod
    from echolot.config import Config
    from echolot.main import analyze_trace

    trace = tmp_path / "bare.perfetto-trace"
    trace.write_bytes(fixture.build(environment=False))
    bare = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    merged = report_mod.aggregate([copy.deepcopy(bare), copy.deepcopy(bare)])

    for label, one in (("single", bare), ("merged", merged)):
        w = warned(compare(one, one))
        check(f"{label}: two unmeasured rounds carry no platform-state warning",
              not w & {"environment", "environment-thermal"}, str(w))

    # The session's report is the fixture with its platform state recorded.
    cmp = compare(copy.deepcopy(marker_report), bare)
    text = next((w["text"] for w in cmp["warnings"] if w["id"] == "environment"), "")
    check("against a measured round the missing clock is named, on its side",
          "the after side carries no CPU frequency" in text,
          text or str(warned(cmp)))


def test_a_sampled_round_is_told_from_a_plain_one_as_analyze_writes_them(
        marker_report, sampled_report) -> None:
    """The contract between the two commands, for sampling.

    Single and merged: merging repeats writes the block again, with `runs`.
    """
    import copy

    from echolot import report as report_mod

    def merged(one: dict) -> dict:
        return report_mod.aggregate([copy.deepcopy(one) for _ in range(3)])

    for label, plain_side, sampled_side in (
            ("single", marker_report, sampled_report),
            ("merged", merged(marker_report), merged(sampled_report))):
        cmp = compare(copy.deepcopy(plain_side), copy.deepcopy(sampled_side))
        text = next((w["text"] for w in cmp["warnings"] if w["id"] == "sampling"), "")
        check(f"{label}: the sampled side is named, with its rate",
              "off before and on at 100 Hz after" in text, text or str(warned(cmp)))
        same = compare(copy.deepcopy(sampled_side), copy.deepcopy(sampled_side))
        check(f"{label}: two sampled rounds say nothing about it",
              "sampling" not in warned(same), str(warned(same)))


# --- which two reports, when nobody named them ------------------------------

def test_pair_resolution(tmp_path: Path) -> None:
    """The forms that take their arguments from the open investigation.

    This is the shape the loop uses: change something, record again, ask what
    that did. Getting the pair wrong here compares a round against itself and
    reports that nothing happened.
    """
    from echolot import hunt as hunt_mod

    (tmp_path / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    out = tmp_path / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    hunt_mod.open_new(tmp_path, "cold start 3s -> 7s", scenario="fixture")

    def round_of(value: float) -> None:
        """One analyze: the latest report, then a copy filed under the hunt."""
        body = report([det("d", [row("A", value)])])
        (out / "report.json").write_text(json.dumps(body), encoding="utf-8")
        (out / "report.md").write_text("#", encoding="utf-8")
        hunt_mod.record_report(tmp_path, out)

    for value in (100.0, 400.0, 900.0):
        round_of(value)

    env = dict(os.environ, ECHOLOT_NO_RECORD="1")

    def cli(*argv) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                              capture_output=True, text=True, cwd=tmp_path, env=env)

    def delta_of(done: subprocess.CompletedProcess) -> float | None:
        if done.returncode != 0:
            return None
        body = json.loads((out / "comparison.json").read_text())
        return body["rows"][0]["delta_ms"] if body["rows"] else None

    bare = cli("compare")
    check("bare compare takes the previous round against the latest",
          delta_of(bare) == 500.0, f"{delta_of(bare)} / {bare.stderr[-200:]}")

    whole = cli("compare", "--hunt", "1")
    check("--hunt takes the first round against the last",
          delta_of(whole) == 800.0, f"{delta_of(whole)} / {whole.stderr[-200:]}")

    one = cli("compare", ".echolot/hunts/1/reports/001.json")
    check("one path is that report against the latest",
          delta_of(one) == 800.0, f"{delta_of(one)} / {one.stderr[-200:]}")

    missing = cli("compare", "--hunt", "9")
    check("an investigation that does not exist is refused",
          missing.returncode == 2 and "hunt --list" in missing.stderr,
          missing.stderr[-200:])

    too_many = cli("compare", "a.json", "b.json", "c.json")
    check("three reports are refused",
          too_many.returncode == 2 and "at most two" in too_many.stderr,
          too_many.stderr[-200:])


def test_nothing_to_compare(tmp_path: Path) -> None:
    """No investigation, no arguments: say what to do rather than guess."""
    (tmp_path / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "echolot.main", "compare"],
                          capture_output=True, text=True, cwd=tmp_path,
                          env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("nothing to compare is refused", done.returncode == 2,
          f"exit {done.returncode}")
    check("and the message names the three forms",
          "--hunt" in done.stderr and "two reports" in done.stderr,
          done.stderr[-250:])



def test_a_missing_clock_does_not_hide_throttling() -> None:
    """Two independent facts, and the first one going unmeasured hides neither.

    The clock and the cooling device come from different sources, so one side
    can carry the second without the first. An early return on the missing
    clock swallowed the throttling warning, which is the more actionable of
    the two.
    """
    before = report([det("d", [row("A", 100.0)])],
                    environment=env(None, throttled=False))
    after = report([det("d", [row("A", 140.0)])],
                   environment=env(1800.0, throttled=True))
    w = warned(compare(before, after))
    check("the unmeasured clock is said", "environment" in w, str(w))
    check("and the throttling is not lost with it",
          "environment-thermal" in w, str(w))


def test_an_unmeasured_side_is_never_called_cool() -> None:
    """Found on a live pair, not by reading the code.

    One round on an A51 that was throttling against one recorded with
    `runner.environment: false` produced "the kernel throttled during the
    before round and not the other one" — a confident sentence about a device
    nobody had looked at. A side with no thermal block has not reported a cool
    device; it has reported nothing.
    """
    hot = report([det("d", [row("A", 140.0)])], environment=env(1800.0, throttled=True))
    unmeasured = report([det("d", [row("A", 100.0)])],
                        environment=env(None, throttled=None))
    w = warned(compare(hot, unmeasured))
    check("no claim about the side that was not measured",
          "environment-thermal" not in w, str(w))
    check("and the missing platform state is still said",
          "environment" in w, str(w))
