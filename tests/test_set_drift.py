"""A set whose runs drift from the first to the last is said to (#508).

A device that warms up, heats or settles between the first run of a set and
the last makes the runs a slope, and the median of a slope is no one state
of it. The test is one for the whole set: how closely the rows found in
every run follow the order of the runs, against orders drawn at random.
"""

from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report, warned  # noqa: E402

from echolot import compare as compare_mod  # noqa: E402
from echolot import drift  # noqa: E402
from echolot import report as report_mod  # noqa: E402


def runs_of(seed: int, n: int, rows: int = 30, slope: float = 0.0,
            shared: float = 0.15, own: float = 0.15) -> list[list[float]]:
    """Rows of `n` runs: each row its own level, each run slower or faster
    for every row at once by `shared`, each value off by `own`, and, with a
    `slope`, each row growing (or, one in three, shrinking) by that share of
    itself from one run to the next."""
    rng = random.Random(seed)
    per_run = [math.exp(rng.gauss(0, shared)) for _ in range(n)]
    out = []
    for _ in range(rows):
        level = 20 + rng.random() * 400
        way = slope if rng.random() < 2 / 3 else -slope
        out.append([round(level * per_run[i] * math.exp(rng.gauss(0, own)) * (1 + way * i), 2)
                    for i in range(n)])
    return out


def merged(values: list[list[float]], *, detector: str = "main_thread_block") -> dict:
    """A merged report of `len(values[0])` runs whose rows have these values."""
    n = len(values[0])
    rows = []
    for i, v in enumerate(values):
        rows.append({"location": f"work{i}", "runs": f"{n}/{n}", "count": 1,
                     "self_ms": sorted(v)[n // 2],
                     "spread": {"self_ms": {"min": min(v), "max": max(v), "values": v}}})
    return report([det(detector, rows)], runs=n)


def test_a_steady_set_does_not_drift_and_a_sloped_one_does() -> None:
    steady = drift.test(runs_of(1, 15))
    sloped = drift.test(runs_of(1, 15, slope=0.03))
    check("rows that do not follow the order: over 5%", steady[1] > drift.LEVEL, steady[:2])
    check("rows that grow 3% a run: well under 5%", sloped[1] <= 0.01, sloped[:2])
    check("with nothing tied to the order the score sits near 1/(n−1)",
          abs(steady[0] - 1 / 14) < 0.05, steady[0])


def test_a_steady_set_drifts_about_one_time_in_twenty() -> None:
    # The whole point of drawing the orders at random: a set with nothing
    # tied to its order is called drifting as often as the bar says, here
    # 4 of 100 seeded sets, and slopes the device makes are found: 95.
    steady = sum(drift.test(runs_of(100 + k, 12, rows=20))[1] <= drift.LEVEL for k in range(100))
    sloped = sum(drift.test(runs_of(100 + k, 12, rows=20, slope=0.04))[1] <= drift.LEVEL
                 for k in range(100))
    check("steady sets: about 5 in 100", 1 <= steady <= 10, steady)
    check("sets growing 4% a run: most", sloped >= 80, sloped)


def test_one_slow_run_does_not_make_a_drift() -> None:
    # Ranks: the slowest run is the slowest, however much slower it was.
    values = runs_of(2, 12, shared=0.02, own=0.05)
    for v in values:
        v[0] *= 50
    _, p, _ = drift.test(values)
    check("a first run fifty times slower in every row is one run out of order",
          p > drift.LEVEL, p)


def test_the_answer_is_the_same_every_time_and_for_the_reversed_order() -> None:
    values = runs_of(3, 10, slope=0.02)
    first = drift.test(values)
    check("same values, same answer", drift.test(copy.deepcopy(values)) == first)
    back = drift.test([list(reversed(v)) for v in values])
    # The same score; `p` from the same orders, which place the reversed
    # rows a little differently, so within what a thousand orders can say.
    check("a set that shrinks is as much a drift as one that grows",
          round(back[0], 9) == round(first[0], 9) and abs(back[1] - first[1]) < 0.03,
          (back[:2], first[:2]))


def test_the_block_says_what_it_read() -> None:
    block = drift.of(merged(runs_of(4, 12, slope=0.05)))
    check("judged", block["drifts"] is True, block)
    check("every row read", block["rows"] == 30 and block["runs"] == 12, block)
    check("chance is 1/(runs − 1)", block["chance"] == round(1 / 11, 3), block)
    most = block["most"]
    check("the three rows that follow the order closest, closest first",
          len(most) == 3 and abs(most[0]["rho"]) >= abs(most[-1]["rho"]), most)
    check("each with its first and last third of runs", all(
        set(m) == {"detector", "location", "rho", "first_ms", "last_ms"} for m in most), most)
    steady = drift.of(merged(runs_of(4, 12)))
    check("a set that does not drift names no rows", steady["drifts"] is False
          and "most" not in steady, steady)


def test_too_few_runs_or_no_row_to_read_is_not_judged() -> None:
    block = drift.of(merged(runs_of(5, drift.MIN_RUNS - 1, slope=0.2)))
    check("under MIN_RUNS: no verdict, no p", block["drifts"] is None and "p" not in block, block)
    check("one trace has no order", drift.of(report([], runs=1)) is None)
    empty = drift.of(merged([[1.0] * 10]))
    check("rows under the floor are not read", empty == {"runs": 10, "rows": 0, "drifts": None},
          empty)


def test_only_rows_found_in_every_run_above_the_floor_are_read() -> None:
    values = runs_of(6, 10)
    rep = merged(values)
    rows = rep["detectors"][0]["rows"]
    rows[0]["runs"] = "9/10"
    rows[0]["spread"]["self_ms"]["values"] = rows[0]["spread"]["self_ms"]["values"][:9]
    rows[1]["spread"]["self_ms"]["values"] = [3.0] * 5 + [4.0] * 5
    rows[2]["spread"]["self_ms"]["values"] = [50.0] * 10
    check("a row in 9 of 10, one under the floor and one the same in every run are left out",
          drift.of(rep)["rows"] == 27, drift.of(rep))


def test_the_floor_is_compares() -> None:
    check("drift.FLOOR_MS is compare.FLOOR_MS", drift.FLOOR_MS == compare_mod.FLOOR_MS)


def test_merging_repeats_writes_the_block_and_the_report_says_it() -> None:
    values = runs_of(7, 10, rows=12, slope=0.06)

    def run(i: int) -> dict:
        rows = [{"location": f"work{r}", "count": 1, "self_ms": values[r][i],
                 "total_ms": values[r][i], "max_ms": values[r][i]} for r in range(len(values))]
        return {"schema": 1, "trace": f"s_iter{i:03d}.perfetto-trace",
                "window": {"duration_ms": 1000.0 + i, "process": "com.example.app"},
                "summary": {"detectors_run": 1, "detectors_fired": 1,
                            "fired_ids": ["main_thread_block"]},
                "detectors": [{"id": "main_thread_block", "title": "t", "why": "",
                               "params": {}, "params_source": "default", "error": None,
                               "identity": ["location"], "rows": rows}]}

    rep = report_mod.aggregate([run(i) for i in range(10)])
    check("report.json carries the block", rep["drift"]["drifts"] is True, rep.get("drift"))
    whole, overview = report_mod.to_markdown(rep), report_mod.overview(rep)
    check("report.md says it above the findings",
          "> ⚠️ The runs drift from the first to the last" in whole.split("## ")[0], whole)
    check("so does the overview", "The runs drift from the first to the last" in overview,
          overview)

    # Interleaved: an order whose ρ against the recorded one is −0.04, as
    # a shuffle drawn at random would usually be.
    shuffled = [run(i) for i in (4, 9, 0, 6, 2, 8, 1, 5, 7, 3)]
    for i, r in enumerate(shuffled):
        r["trace"] = f"s_iter{i:03d}.perfetto-trace"
    calm = report_mod.aggregate(shuffled)
    check("the same runs out of order do not drift", calm["drift"]["drifts"] is False,
          calm["drift"])
    check("and the report says nothing of it",
          "drift" not in report_mod.to_markdown(calm).split("## ")[0], calm["drift"])


def test_compare_warns_when_either_side_drifts() -> None:
    sloped, steady = merged(runs_of(8, 12, slope=0.05)), merged(runs_of(9, 12))
    for rep in (sloped, steady):
        rep["drift"] = drift.of(rep)
    cmp = compare(steady, sloped)
    check("after drifts: one warning", warned(cmp) >= {"drift"}, cmp["warnings"])
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "drift")
    check("named by side", text.startswith("The runs after drift"), text)
    both = next(w["text"] for w in compare(sloped, sloped)["warnings"] if w["id"] == "drift")
    check("both sides: one warning that says so", both.startswith("The runs on both sides"), both)
    check("two steady sides: none", "drift" not in warned(compare(steady, steady)))


def test_compare_reads_a_report_from_before_the_block() -> None:
    sloped, steady = merged(runs_of(10, 12, slope=0.05)), merged(runs_of(11, 12))
    check("no block, worked out from its rows", "drift" not in sloped
          and "drift" in warned(compare(sloped, steady)))
    sloped["drift"] = None
    check("a block that says nothing is taken at its word",
          "drift" not in warned(compare(sloped, steady)))
