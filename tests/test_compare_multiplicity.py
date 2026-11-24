"""Two hundred rows judged at once: Holds asks for the floor, not zero (#506).

A comparison judges every row that both sides have, about two hundred on a
cold start of a large app, and at 95% one in twenty of the rows that did not
move lands on one side of zero by chance. Fifteen cold starts of one build,
split seven against eight, held four such rows a split. Holds now asks the
range to clear the floor that listed the row as moved. These tests hold it
to that on synthetic sets: one build set against itself, split at random,
and the same with one row made slower on the after side.
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import stats  # noqa: E402
from tests.support import check  # noqa: E402

DETECTORS = ("main_thread_block", "binder_txn", "io_wait", "monitor_contention")
RUNS = 15
SPLITS = 40


def population(seed: int = 506, rows: int = 200) -> dict[tuple[str, str], list[float]]:
    """Fifteen runs of one build: rows of 8 ms to 800 ms, each straying by
    5% to 60% of itself from run to run, heavier on the slow side."""
    rng = random.Random(seed)
    out = {}
    for i in range(rows):
        median = math.exp(rng.uniform(math.log(8), math.log(800)))
        sigma = math.exp(rng.uniform(math.log(0.05), math.log(0.6)))
        out[(DETECTORS[i % len(DETECTORS)], f"Row{i:03d}.load")] = [
            round(median * math.exp(rng.gauss(0.0, sigma)), 2) for _ in range(RUNS)]
    return out


def side(rows: dict[tuple[str, str], list[float]], picks: list[int]) -> dict:
    """A merged report of the runs `picks`, a row in every one of them."""
    by_det: dict[str, list[dict]] = {}
    for (det_id, location), values in rows.items():
        kept = [values[i] for i in picks]
        by_det.setdefault(det_id, []).append({
            "location": location, "detail": "com.example.app",
            "runs": f"{len(picks)}/{len(picks)}", "count": 1,
            "self_ms": statistics.median(kept),
            "spread": {"self_ms": {"min": min(kept), "max": max(kept), "values": kept}}})
    return {"schema": 1, "runs": len(picks),
            "traces": [f"run{i:03d}.perfetto-trace" for i in picks],
            "toolchain": {"echolot": "1.0.0"},
            "window": {"process": "com.example.app", "duration_ms": 5480.0},
            "config": {"sha": "aaaa"}, "environment": {},
            "summary": {"fired_ids": list(by_det)},
            "detectors": [{"id": d, "title": d, "why": "", "params": {},
                           "params_source": "default", "error": None,
                           "sha": "a" * 12, "rows": found}
                          for d, found in by_det.items()]}


def splits(seed: int = 508) -> list[tuple[list[int], list[int]]]:
    rng = random.Random(seed)
    out = []
    for _ in range(SPLITS):
        before = sorted(rng.sample(range(RUNS), 7))
        out.append((before, [i for i in range(RUNS) if i not in before]))
    return out


def build(rows: dict, before: list[int], after: list[int]) -> dict:
    return compare_mod.build(side(rows, before), side(rows, after),
                             before_path="a.json", after_path="b.json")


def counts(cmp: dict) -> tuple[int, int]:
    """Listed rows that hold, and listed rows whose range clears zero."""
    listed = [r for r in cmp["rows"] if r["change"] in ("grew", "shrank") and r["shift"]]
    return (sum(1 for r in listed if r["holds"]),
            sum(1 for r in listed if compare_mod._clears_zero(r["shift"])))


def test_one_build_against_itself_holds_a_fraction_of_what_clears_zero() -> None:
    """Forty random splits of one synthetic build, seven against eight.

    Rows found in every run, all drawn from one spread a side: clearing zero
    is what Holds was, and it happens at the rate the interval misses, about
    4% of the rows tested at seven against eight (`stats.cut`), a share of
    them past the floor. Clearing the floor happens a fraction as often.
    """
    rows = population()
    held = zero = 0
    for before, after in splits():
        h, z = counts(build(rows, before, after))
        held, zero = held + h, zero + z
    check("clearing zero alone is several rows a split", zero / SPLITS >= 3,
          f"{zero / SPLITS:.2f} a split")
    check("clearing the floor is under a third of that", held * 3 <= zero,
          f"{held} against {zero}")
    check("and under one row a split", held / SPLITS < 1.0, f"{held / SPLITS:.2f}")


def test_the_miss_rate_of_one_row_is_what_the_counts_say() -> None:
    """Seven against eight, the interval misses in 4.0% of orders, not 5%.

    `stats.cut` takes ten differences off each end, and a miss is ten or
    fewer pairs one way: 2 × P(U ≤ 10) of the 6435 orders. That is the rate
    clearing zero is held to on rows that did not move.
    """
    k = stats.cut(7, 8)
    counts_ = stats._orderings(7, 8)
    rate = 2 * sum(counts_[:k + 1]) / math.comb(15, 7)
    check("ten off each end", k == 10, str(k))
    check("a miss in 4.0% of orders", round(rate * 100, 1) == 4.0, f"{rate:.4f}")


def test_a_row_made_slower_still_holds_and_leads() -> None:
    """A 400 ms sleep on the main thread in one row, after; the rest one build.

    The fault the table's order was fixed for (#595). It holds, against the
    floor, and leads the table: a move of the main thread's own time that
    holds and is a twentieth of the 5.5 s window or more. Twice as slow, a
    row of its own size holds as well, wherever its size puts it.
    """
    rows = population()
    key = ("main_thread_block", "Row000.load")
    held = led = doubled = 0
    for before, after in splits():
        planted = {**rows, key: [v + 400.0 if i in after else v
                                 for i, v in enumerate(rows[key])]}
        cmp = build(planted, before, after)
        row = next(r for r in cmp["rows"] if (r["detector"], r["location"]) == key)
        held += bool(row["holds"])
        led += cmp["rows"][0] is row
        twice = {**rows, key: [v * 2 if i in after else v
                               for i, v in enumerate(rows[key])]}
        cmp = build(twice, before, after)
        doubled += bool(next(r for r in cmp["rows"]
                             if (r["detector"], r["location"]) == key)["holds"])
    check("the planted sleep holds in every split", held == SPLITS, f"{held} of {SPLITS}")
    check("and is the first row of the table", led == SPLITS, f"{led} of {SPLITS}")
    check("a row made twice as slow holds in every split", doubled == SPLITS,
          f"{doubled} of {SPLITS}, row {statistics.median(rows[key]):.1f} ms")


def test_the_header_says_what_holds_asks_for() -> None:
    rows = population()
    before, after = splits()[0]
    cmp = build(rows, before, after)
    held, zero = counts(cmp)
    judged = sum(1 for r in cmp["rows"] if r["change"] in ("grew", "shrank") and r["shift"])
    text = compare_mod.to_markdown(cmp)
    line = (f"Holds asks each range to clear the floor, not zero: {held} of {judged} "
            f"row(s) with a verdict do")
    check("one line says it, with the counts", line in text, text[:600])
    check("and how many more clear zero alone",
          zero == held or f"and {zero - held} more that clear zero alone read no" in text,
          text[:600])
    check("comparison.json says what holds was read against",
          cmp["holds_against"] == "floor", str(cmp.get("holds_against")))
    old = {**cmp, "holds_against": None}
    check("a comparison from before the field gets no such line",
          "Holds asks each range" not in compare_mod.to_markdown(old))


def test_a_range_clear_of_zero_but_not_the_floor_does_not_hold() -> None:
    """+3 … +19 on a 100 ms row: past zero, inside the 10 ms floor."""
    rows = {("main_thread_block", "Row.load"):
            [100.0, 102.0, 98.0, 101.0, 99.0, 112.0, 114.0, 110.0, 113.0, 111.0]}
    cmp = build(rows, [0, 1, 2, 3, 4], [5, 6, 7, 8, 9])
    r = cmp["rows"][0]
    check("listed as grew", r["change"] == "grew", r["change"])
    check("clear of zero", compare_mod._clears_zero(r["shift"]), str(r["shift"]))
    check("short of the floor", r["shift"]["low_ms"] <= r["shift"]["floor_ms"] == 10.0,
          str(r["shift"]))
    check("so it does not hold", r["holds"] is False, str(r))
    cell = compare_mod._holds(r)
    check("and the cell names the floor it did not clear", "· floor 10.0" in cell, cell)
