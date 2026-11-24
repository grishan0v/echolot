"""Whether a set's runs drift from the first to the last (#508).

A set's runs are read as one state of the device measured again and again:
`analyze` takes their medians and `compare` pairs every run after with every
run before. Neither asks whether the runs were alike in the first place. A
device that warms up, heats, settles its caches or catches up with
background work between the first run and the last makes them a slope, and
the median of a slope is no one state of it. A comparison then reads that
slope: two sets of fifteen cold starts of one build on an SM-A515F, each
split in two, held 12 and 7 rows past the floor first half against second
half, where a random split of the same sets held 3 and 1. A before and an
after are always recorded one after the other, so they get it in full.

The window did not show it. Its own trend against the run's index was
weak in two of three such sets, and with the first two runs left out it
was gone in all three, while the halves still differed. Nor did any one
row: tested row by row, a few dozen rows a set, none survived a
correction for testing that many. What moved was many rows a little,
together. So the test here is one test of the whole set: for every row
found in every run, how closely its values follow the order of the runs
(Spearman's ρ: ranks, so one slow run moves it no more than any other),
squared so that a row that grew and a row that shrank both count, and
averaged over the rows — `score`. With nothing tied to the order it
averages 1 / (runs − 1), `chance`. How often an order of the runs drawn
at random scores as high as the real one is `p`: the same orders for
every row, so rows that move together because one run was slow in all of
them move together under every order too, and the test does not mistake
them for a hundred separate findings. A set drifts when `p` is 5% or
less, the same 95% the Holds column asks for.

On the two sets above it gives 0.031 and 0.026, and 0.105 on a third of
the same hour, whose halves held 6 rows, as many as random halves of it
held about one time in eight. Shuffled, forty-nine real sets of 10 to 60 runs
came out at or under 5% in 52 tries of 980: 5.3%, as a test at 95%
should.

The order is the set's, the order the recordings were made in, by each
trace's own clock (`main._in_recording_order`, #848); by directory, then by
name, numbers as numbers, for a set whose traces do not all say when
(`main.run_order`). The orders are
drawn from a fixed seed, so the same report gives the same answer every
time. Rows below `FLOOR_MS` are left out: `compare` calls no move there,
whatever their order.
"""

from __future__ import annotations

import math
import random
from operator import mul
from statistics import median
from typing import Any

from . import stats

# Fewer runs than this are not judged: eight are the fewest `compare` splits
# four against four, and with fewer a slope cannot be told from chance by
# a test of a few dozen rows.
MIN_RUNS = 8

# `compare.FLOOR_MS`: a row under it is never called moved, so its order
# cannot mislead a comparison. Not imported, since compare imports the
# report; a test holds the two equal.
FLOOR_MS = 5.0

# How many orders of the runs the real one is placed among, and the seed
# they are drawn from. A thousand puts `p` in steps of 0.001; the seed is
# the issue's number and nothing else.
ORDERS = 999
SEED = 508

# The bar: the 95% the Holds column asks for, as the share left over.
LEVEL = (100 - stats.CONFIDENCE_PCT) / 100

# How many of the rows that follow the order best are named.
NAMED = 3


def of(report: dict[str, Any]) -> dict[str, Any] | None:
    """The drift block of a merged report: whether its runs drift, and how.

    None for one trace, which has no order. `drifts` is None, with no `p`,
    when there are fewer than `MIN_RUNS` runs or no row to read; otherwise
    True or False, and when True, `most` names the rows that follow the
    order closest, with the median of their first and last third of runs.
    Works on any merged report, old ones included: it reads nothing but
    the rows' `spread` values.
    """
    from .report import identity_of, metric_of

    runs = report.get("runs")
    if not isinstance(runs, int) or isinstance(runs, bool):
        runs = len(report.get("traces") or [])
    if runs < 2:
        return None
    rows = []
    for det in report.get("detectors") or []:
        identity = identity_of(det)
        for row in det.get("rows") or []:
            values = _values(row, metric_of(row), runs)
            if values is not None:
                rows.append((det.get("id"), _name(row, identity), values))
    out: dict[str, Any] = {"runs": runs, "rows": len(rows)}
    if runs < MIN_RUNS or not rows:
        out["drifts"] = None
        return out
    score, p, rhos = test([v for _, _, v in rows])
    out["score"] = round(score, 3)
    out["chance"] = round(1 / (runs - 1), 3)
    out["p"] = round(p, 3)
    out["drifts"] = p <= LEVEL
    if out["drifts"]:
        third = max(1, runs // 3)
        closest = sorted(zip(rows, rhos, strict=True), key=lambda x: -abs(x[1]))[:NAMED]
        out["most"] = [{"detector": det_id, "location": name,
                        "rho": round(rho, 2) + 0.0,
                        "first_ms": round(median(values[:third]), 1),
                        "last_ms": round(median(values[-third:]), 1)}
                       for (det_id, name, values), rho in closest]
    return out


def _values(row: dict[str, Any], metric: str, runs: int) -> list[float] | None:
    """A row's value in each run, in the set's order, or None when it cannot
    be read by run: not found in every run (`spread` keeps only the runs that
    found it, without saying which), under the floor, or the same in all."""
    if str(row.get("runs")) != f"{runs}/{runs}":
        return None
    values = ((row.get("spread") or {}).get(metric) or {}).get("values")
    if not isinstance(values, list) or len(values) != runs:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return None
    if median(values) < FLOOR_MS or min(values) == max(values):
        return None
    return [float(v) for v in values]


def _name(row: dict[str, Any], identity: tuple[str, ...]) -> str:
    """The row as its detector names it: the location, then the rest of its
    identity, as `compare` prints it."""
    parts = [row.get("location")] + [row.get(c) for c in identity if c != "location"]
    return " · ".join(str(p) for p in parts if p not in (None, ""))


def test(rows: list[list[float]], orders: int = ORDERS,
         seed: int = SEED) -> tuple[float, float, list[float]]:
    """`score`, `p` and each row's ρ, for rows of one length, in run order.

    Each row's ranks, centred and scaled to length one, are `a`; the runs'
    indices the same way are `b`. ρ is a·b, and the score, the mean of the
    squares, is bᵀMb with M the mean of the rows' aaᵀ: the rows fold into
    one matrix of runs × runs, and each order drawn costs that much
    arithmetic rather than a pass over every row.
    """
    n = len(rows[0])
    units = [_unit(_ranks(r)) for r in rows]
    unit = [u for u in units if u is not None]
    b = _unit([float(i) for i in range(n)])
    if not unit or b is None:
        return 0.0, 1.0, [0.0] * len(rows)
    k = len(unit)
    m = [[sum(a[i] * a[j] for a in unit) / k for j in range(n)] for i in range(n)]

    def score(order: list[int]) -> float:
        bp = [b[i] for i in order]
        return sum(x * sum(map(mul, row, bp)) for x, row in zip(bp, m, strict=True))

    order = list(range(n))
    seen = score(order)
    rng = random.Random(seed)
    # One order equals another in exact arithmetic — the reversed one always
    # does — and may differ from it in the last bit of a float: equal counts.
    tie = 1e-9 * max(1.0, abs(seen))
    as_high = 0
    for _ in range(orders):
        rng.shuffle(order)
        if score(order) >= seen - tie:
            as_high += 1
    rhos = [sum(map(mul, a, b)) if a is not None else 0.0 for a in units]
    return seen, (as_high + 1) / (orders + 1), rhos


def _ranks(values: list[float]) -> list[float]:
    """Ranks from 0, ties sharing the mean of theirs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2
        i = j + 1
    return ranks


def _unit(values: list[float]) -> list[float] | None:
    """Centred and scaled to length one; None when all are equal."""
    mean = sum(values) / len(values)
    centred = [v - mean for v in values]
    length = math.sqrt(sum(v * v for v in centred))
    return [v / length for v in centred] if length else None


# --- what the report and the comparison say -----------------------------------

def _most(block: dict[str, Any]) -> str:
    """The rows that follow the order closest, as a reader checks them."""
    said = []
    for r in block.get("most") or []:
        said.append(f"`{r['detector']}` {r['location']} {r['first_ms']:g} → "
                    f"{r['last_ms']:g} ms (ρ {r['rho']:+.2f})")
    return "; ".join(said)


def lines(block: dict[str, Any] | None) -> list[str]:
    """The report's warning, above its findings, when its runs drift."""
    if not block or not block.get("drifts"):
        return []
    most = _most(block)
    return [
        f"> ⚠️ The runs drift from the first to the last: the {block['rows']} "
        f"rows found in every run follow the order of the runs more closely "
        f"than {block['runs']} runs in a random order would (p = {block['p']:g})."
        + (f" Closest: {most}." if most else "")
        + " A median over them is no one state of the device, and a comparison "
          "with this set can read the drift as a move. Where only the first "
          "runs stand apart, the device was still settling, and the set "
          "without them is one state; where the values climb or fall all the "
          "way, `runner.cooldown` holds the device to one temperature. Record "
          "the two sides of a comparison back to back."
    ]


def warnings(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    """`compare`'s warning when the runs of either side drift.

    A report written before the block existed is read on the spot, from
    the rows it already keeps. Only said: the verdicts stand, as for every
    condition `compare` names.
    """
    sides = []
    for side, report in (("before", before), ("after", after)):
        block = report.get("drift") if "drift" in report else of(report)
        if block and block.get("drifts"):
            sides.append((side, block))
    if not sides:
        return []
    named = "; ".join(
        f"{side}, p = {block['p']:g}" + (f" — closest: {_most(block)}" if block.get("most") else "")
        for side, block in sides)
    whose = "on both sides" if len(sides) == 2 else sides[0][0]
    return [{
        "id": "drift",
        "text": f"The runs {whose} drift from the first to the last ({named}). "
                f"A median over such runs is no one state of the device: rows "
                f"that drift with them can read as moved, and hold, with "
                f"nothing changed in the app. Before acting on a row that "
                f"holds, record the two sides back to back and compare again.",
    }]
