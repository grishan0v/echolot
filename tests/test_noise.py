"""`analyze --noise`: a set split against itself, and what holds by chance (#569).

Built from reports by hand: the split is arithmetic over the repeats' own
reports, merged by `report.aggregate` and compared by `compare.build`, and
neither needs a trace. The fixture's traces go through the same path in
the self-check of the same name.
"""

from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import main as main_mod  # noqa: E402
from echolot import noise, stats  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report import one, row  # noqa: E402


def _set(values: list[list[float]], windows: list[float], places: list[str] | None = None) -> list[dict]:
    """One report per run: `values[run][i]` is row i's time in that run."""
    out = []
    for run, (rows, window) in enumerate(zip(values, windows, strict=True)):
        rep = one([row(f"work{i}", count=1, self_ms=v, total_ms=v, max_ms=v, detail="main")
                   for i, v in enumerate(rows)])
        rep["trace"] = f"{(places or ['set'] * len(values))[run]}/run{run}.perfetto-trace"
        rep["toolchain"] = {"echolot": "test"}
        rep["window"]["duration_ms"] = window
        out.append(rep)
    return out


def _exchangeable(runs: int, rows: int, seed: int = 1) -> list[dict]:
    """Rows found in every run whose values do not depend on the run: any
    run could have come first, and nothing moved between any two halves."""
    rng = random.Random(seed)
    values = [[round(rng.uniform(100, 200), 3) for _ in range(rows)] for _ in range(runs)]
    return _set(values, [round(rng.uniform(900, 1100), 3) for _ in range(runs)])


def test_the_number_of_splits_counts_a_split_and_its_mirror_once() -> None:
    check("fifteen runs split seven against eight 6435 ways", noise.possible(15) == 6435)
    check("fourteen split seven against seven 1716 ways, not 3432",
          noise.possible(14) == 1716)
    check("eight split 35 ways", noise.possible(8) == 35)


def test_the_splits_are_drawn_once_each_from_the_seed() -> None:
    drawn = noise.halves(15, 200, 569)
    check("as many as asked for", len(drawn) == 200, len(drawn))
    check("none twice", len(set(drawn)) == 200)
    check("seven before each time", all(len(p) == 7 for p in drawn))
    check("the same seed draws the same splits", drawn == noise.halves(15, 200, 569))
    check("another seed draws others", drawn != noise.halves(15, 200, 570))
    even = noise.halves(14, 2000, 569)
    check("with fewer splits than asked for, every one, in order",
          len(even) == 1716 and even == sorted(even), len(even))
    mirrors = {tuple(i for i in range(14) if i not in p) for p in even}
    check("and an equal split is not drawn again as its mirror", not mirrors & set(even))


def test_a_set_of_two_directories_is_cut_where_one_ends() -> None:
    def cut(places: list[str]) -> tuple[int, str]:
        return noise.in_order([{"trace": f"{p}/t.perfetto-trace"} for p in places])

    check("one directory: at the middle", cut(["a"] * 15) == (7, "middle"))
    check("two: where the first sitting ends, whatever its size",
          cut(["a"] * 6 + ["b"] * 9) == (6, "sittings"))
    check("three: at the end of the one nearest the middle",
          cut(["a"] * 4 + ["b"] * 4 + ["c"] * 7) == (8, "sittings"))


def test_rows_that_did_not_move_hold_about_as_often_as_95_percent_allows() -> None:
    """Rows found in every run, exchangeable across runs: held against zero,
    each misses as often as the interval allows, 2 × P(U ≤ cut) — 4.0% at
    seven against eight, counted rather than simulated. Not the 2% a real set
    gives, which is lower because a row missing from some runs almost never
    holds; and not "no row holds": a clean comparison of exchangeable rows is
    the exception."""
    k = stats.cut(7, 8)
    allowed = 2 * sum(stats._orderings(7, 8)[:k + 1]) / math.comb(15, 7)
    check("the bar at seven against eight is 4.0%", round(100 * allowed, 1) == 4.0, allowed)
    got = noise.read(_exchangeable(15, 60), splits=100, floor_ms=0.0, floor_ratio=0.0)
    rates = [s["holds"] / s["tested"] for s in got["splits"]]
    mean = sum(rates) / len(rates)
    check("every row has a verdict", got["random"]["tested"]["median"] == 60, got["random"])
    check("they hold about as often as the bar allows", abs(mean - allowed) < 0.01,
          (mean, allowed))
    check("and most splits hold one at least", got["random"]["with_any"] > 50, got["random"])


def test_a_trend_holds_first_against_last_and_mostly_not_at_random() -> None:
    """One row climbs from the first run to the last. Set first against
    last it holds; split at random, it is the exchangeable rows' noise like
    any other, since a random half holds early and late runs alike."""
    rng = random.Random(2)
    values = [[round(rng.uniform(100, 110), 3) for _ in range(30)] + [100.0 + 20 * run]
              for run in range(15)]
    got = noise.read(_set(values, [1000.0 + rng.uniform(0, 10) for _ in range(15)]),
                     splits=100)
    order = got["in_order"]
    check("first against last, the climbing row holds, and only it",
          [r["location"] for r in order["rows"]] == ["work30"], order["rows"])
    check("as many as few random splits did", order["as_many"] * 20 <= 100, order)
    check("at random it holds in few splits", got["random"]["with_any"] <= 20, got["random"])
    check("the reading says the runs change", "the runs change from the first to the last"
          in noise.to_markdown(got), noise.to_markdown(got))


def test_two_sittings_apart_are_read_as_what_a_before_and_after_get() -> None:
    rng = random.Random(3)
    values = [[round(rng.uniform(100, 110), 3) + (40 if run >= 7 else 0) for _ in range(10)]
              for run in range(15)]
    got = noise.read(_set(values, [1000.0] * 15, ["first"] * 7 + ["second"] * 8),
                     splits=100)
    order = got["in_order"]
    check("cut where the second sitting begins",
          (order["at"], order["sizes"]) == ("sittings", [7, 8]), order)
    check("where every row holds", order["holds"] == 10, order)
    text = noise.to_markdown(got)
    check("and the reading says to record the sides back to back",
          "Record the two sides back to back" in text, text)


def test_the_floor_it_names_leaves_the_median_split_with_none() -> None:
    """With the ratio's part of the floor out of the way, so that the
    absolute one is the floor of every row."""
    reports = _exchangeable(15, 60, seed=4)
    got = noise.read(reports, splits=60, floor_ratio=0.0)
    floor = got["random"]["floor_ms"]
    check("a floor above the default is named", floor is not None and floor > 5, got["random"])
    again = noise.read(reports, splits=60, floor_ms=float(floor), floor_ratio=0.0)
    check("and at that floor the median split holds no row",
          again["random"]["holds"]["median"] == 0, again["random"])
    check("which the reading says", f"`compare --floor-ms {floor}`" in noise.to_markdown(got))


def test_the_reports_are_left_as_they_were() -> None:
    reports = _exchangeable(8, 5)
    reports[0]["names_all"] = {"a": {"total_ms": 1.0, "max_ms": 1.0, "threads": ["main"]}}
    kept = copy.deepcopy(reports)
    noise.read(reports, splits=5)
    check("merging each half takes nothing out of the repeats", reports == kept)


def test_too_few_runs_are_refused_by_read() -> None:
    with pytest.raises(noise.TooFew, match="needs 8 runs or more"):
        noise.read(_exchangeable(7, 3))


@pytest.mark.parametrize("argv, said", [
    (["--splits", "10"], "--splits only goes with --noise"),
    (["--seed", "1"], "--seed only goes with --noise"),
    (["--noise"], "needs 8 runs or more"),
])
def test_analyze_refuses_before_reading_a_trace(argv, said, tmp_path, capsys) -> None:
    traces = []
    for i in range(3):
        trace = tmp_path / f"t{i}.perfetto-trace"
        trace.write_bytes(b"not a trace: refused before it is read")
        traces.append(str(trace))
    code = main_mod.main(["analyze", *traces, "-c", str(tmp_path / "none.yml"), *argv])
    err = capsys.readouterr().err
    check("exit 2", code == 2, (code, err))
    check("and why", said in err, err)
