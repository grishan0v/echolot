"""What two sets of runs say about a move, and how small a move they can see.

`compare` asks one question of the per-run values `analyze` keeps under
`spread`: how far the runs after sit from the runs before, 95% sure. `shift`
answers it. The report asks the other one, per row, before there is anything
to compare: how small a move could this many runs, spread this much, call
real at all. `resolves` answers that, the same way, so the two cannot
disagree.

Nothing is assumed about the shape of the runs, which on a cold start is
anything but a bell, and nothing is drawn at random: the same values give the
same answer every time. Here rather than in compare.py because the report
needs the second answer, and report.py is what compare.py imports.
"""

from __future__ import annotations

import functools
import math
from statistics import NormalDist, median

# How sure a verdict has to be before it says a move holds. The usual level,
# and not a flag: a bar that could differ between two comparisons would be one
# more thing they differ by without anyone seeing it. A percentage, so the
# test in `cut` stays in whole numbers.
CONFIDENCE_PCT = 95

# How much counting `cut` does before it takes the curve instead: a hundred
# runs a side, 86 ms. The work grows with the smaller side times both, so
# twice that is eight times the wait, and five hundred a side a minute.
COUNTED_UP_TO = 1_000_000


def shift(before: list[float], after: list[float]) -> dict[str, float] | None:
    """How far the runs after sit from the runs before, and how sure that is.

    Every run after is paired with every run before, and the differences are
    what is read. Their median is the move, `ms`; the interval, `low_ms` to
    `high_ms`, is what is left once `cut` has taken as many differences off
    each end as it can while the chance of the true move lying outside stays
    at 5% or less — the Hodges–Lehmann estimate and its Moses interval.

    What it replaced in `compare` asked whether the min–max ranges of the two
    sides touched. That is this same interval with nothing taken off — from
    the smallest difference to the largest — and so a test whose bar rose with
    every run recorded: 90% sure at three a side, 99.2% at five, and at
    fifteen a side sure to all but one chance in 77 million, which one slow
    run among the thirty is enough to deny. It was checked on fifteen runs of
    one build of a real app — the eleven rows found in at least ten of them
    — split at random into a before and an after, with every run after made
    a fifth slower. Seven against eight, the ranges caught the move in 15
    comparisons of a hundred and this interval in 51; five against five, in
    27 and 36. More runs made the old test see less; they make this one see
    more.

    The price is the other mistake. On the same splits with nothing made
    slower, a row cleared the floor and held in 2 comparisons of a hundred
    seven against eight, and in 1.6 five against five; the ranges had called
    0.1 and 0.8 apart.

    None with too few runs to be 95% sure of anything. Four a side is
    enough; with fewer on one side the other has to make up for it — three
    need five against them, two need eight, one needs thirty-nine.
    """
    k = cut(len(before), len(after))
    if k is None:
        return None
    diffs = sorted(y - x for y in after for x in before)
    # `+ 0.0` turns a -0.0 from the rounding into the zero it is.
    return {"ms": round(median(diffs), 2) + 0.0,
            "low_ms": round(diffs[k], 2) + 0.0,
            "high_ms": round(diffs[-1 - k], 2) + 0.0}


def resolves(before: list[float], after: list[float] | None = None,
             upward: bool = True) -> float | None:
    """The smallest move of the medians these runs could call real.

    Each difference `shift` reads, a run after minus a run before, is the
    move of the medians plus what the two runs strayed from their own
    medians. So the interval `shift` gives is the interval of the strays
    alone, moved along by the move of the medians — exactly, not roughly —
    and it clears zero when that move is larger than the strays' end on the
    far side. That end is what this returns: a move of the medians no larger
    cannot hold, whatever the runs; one larger does. `upward` picks the end,
    for a move that grew or one that shrank.

    With one set, the set against as many runs spread the same way: what the
    next comparison of this row can see, before there is a second set. The
    strays of a set against themselves are symmetric, so the two ends agree.

    None with too few runs for a verdict at all, the same four a side
    `shift` needs.
    """
    after = before if after is None else after
    k = cut(len(before), len(after))
    if k is None:
        return None
    mb, ma = median(before), median(after)
    strays = sorted((y - ma) - (x - mb) for y in after for x in before)
    end = -strays[k] if upward else strays[-1 - k]
    return round(max(end, 0.0), 2) + 0.0


def runs_needed(before: int, after: int, resolution: float,
                move: float) -> int | None:
    """About how many runs a side would resolve a move this size.

    The strays shrink with the square root of the runs, so a move half the
    size the runs resolve takes four times the runs. Two sides of unequal
    size count as the equal number that strays as much, 2nm / (n + m): five
    against ten resolve what seven against seven do. An estimate, and it
    assumes the next runs stray as much as these did. None for a move of
    nothing, which no number of runs settles, and for one the runs already
    resolve.
    """
    if not move or resolution <= abs(move):
        return None
    equal = 2 * before * after / (before + after)
    return max(math.floor(equal) + 1,
               math.ceil(equal * (resolution / abs(move)) ** 2))


@functools.lru_cache(maxsize=None)
def cut(before: int, after: int) -> int | None:
    """How many differences come off each end of the interval, or None.

    If nothing moved, which runs came out slower is chance, and every order
    the runs can come in is as likely as any other. `_orderings` counts, for
    each number of (before, after) pairs in which the run after was slower,
    how many orders give it. Taking k differences off each end leaves an
    interval that misses the true move in the orders with k such pairs or
    fewer, and in as many at the other end; k is the largest that keeps the
    two together at 5% or less.

    None when even k = 0 misses too often. Three runs a side come in 20
    orders, and the widest interval misses in 2 of them — 10%.
    """
    if min(before, after) * before * after > COUNTED_UP_TO:
        return _cut_by_curve(before, after)
    counts = _orderings(before, after)
    total = math.comb(before + after, before)
    k, below = None, 0
    for u, count in enumerate(counts):
        below += count
        # below / total > (1 - confidence) / 2, in whole numbers
        if below * 200 > (100 - CONFIDENCE_PCT) * total:
            break
        k = u
    return k


def _cut_by_curve(before: int, after: int) -> int:
    """The same cut, from the bell curve the counts tend to with many runs.

    Checked against the count from twenty to a hundred and twenty runs a
    side: the same k, or one less — an interval one difference wider, never
    narrower. Plain arithmetic in floating point, so the same on every run.
    """
    z = NormalDist().inv_cdf(1 - (100 - CONFIDENCE_PCT) / 200)
    pairs = before * after
    spread = math.sqrt(pairs * (before + after + 1) / 12)
    return max(0, math.floor(pairs / 2 - z * spread - 0.5))


@functools.lru_cache(maxsize=None)
def _orderings(before: int, after: int) -> tuple[int, ...]:
    """Entry u: how many orders of the runs have u pairs with the run after slower.

    Counted, not simulated: the counts are the coefficients of the Gaussian
    binomial coefficient, built one run of the smaller side at a time — times
    (1 - x^(m+i)), then divided by (1 - x^i), both exactly and in whole
    numbers. They add up to the number of orders, (n + m choose n), and are
    symmetric; the tests hold them to both and to a count by brute force.
    Forty runs a side take milliseconds, and a pair of sizes is counted once;
    past `COUNTED_UP_TO`, `cut` does not ask.
    """
    n, m = sorted((before, after))
    counts = [1]
    for i in range(1, n + 1):
        grown = counts + [0] * (m + i)
        for j in range(len(grown) - 1, m + i - 1, -1):
            grown[j] -= grown[j - m - i]
        for j in range(i, len(grown)):
            grown[j] += grown[j - i]
        counts = grown[:i * m + 1]
    return tuple(counts)
