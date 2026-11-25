"""Rows merged across repeats: one row per name, from every run that measured it.

`merge_rows` and what it reads a row by: the columns merged by median, the
threads a row adds up, the runs it was found in. `report.aggregate` merges a
set's reports section by section and hands each detector's rows here;
render.py reads the same columns back to say what a merged row means.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from . import stats

# Columns merged across repeats: by median, except `max_ms`, the largest.
NUMERIC = ["count", "self_ms", "total_ms", "max_ms", "covered_ms"]

# How many threads of one name a row adds up, on the detectors that group by
# thread (#511). The kernel cuts a name to fifteen characters, so a pool's
# workers arrive under one, and a row of 3.8 s of waiting in a 5.7 s window is
# eight threads' time and not the window's. Merged, the largest any repeat
# found; it is not part of a row's name, and `compare` pairs rows without it.
THREADS = "threads"

# Columns whose per-run values survive the merge, in `spread`. The median alone
# cannot say whether a number is steady: 120 ms from (118, 119, 121) and 120 ms
# from (12, 120, 890) read identically, and only the second one means the next
# run will say something else. Three columns rather than all five, because the
# whole point of the report is that it stays small:
#   self_ms and total_ms — one of the two is the ranking metric (`metric_of`),
#   every conclusion is drawn from it, so its stability is what decides
#   whether a conclusion holds;
#   max_ms — where a single slow occurrence shows up at all, and a median over
#   maxima across repeats is exactly what hides one.
SPREAD = ["self_ms", "total_ms", "max_ms"]

def metric_of(row: dict[str, Any]) -> str:
    """Which column this row is judged by.

    Self time where a detector measures it, total time otherwise. One rule,
    used for ranking inside a report and for the delta between two.
    """
    return "self_ms" if row.get("self_ms") is not None else "total_ms"



def _rank(row: dict[str, Any]) -> float:
    return row.get(metric_of(row)) or 0.0



def _merge_debuggable(values: list[Any]) -> dict[str, int] | None:
    """How many repeats ran a debuggable build, of those that said:
    `{"repeats": k, "of": n}`; None when none of them said (#925)."""
    said = [v for v in values if isinstance(v, bool)]
    if not said:
        return None
    return {"repeats": sum(said), "of": len(said)}


def short_runs(runs: Any) -> tuple[int, int] | None:
    """`(k, n)` from a `runs` field such as `"1/5"`, when k is short of n."""
    try:
        k, n = (int(x) for x in str(runs).split("/"))
    except ValueError:
        return None
    return (k, n) if k < n else None



def _one_run(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows of one repeat that a folded name made one: added up.

    Two owners' tids in one run are two stretches of one wait, so their
    times and counts add; the longest single one is the longer of the two.
    The evidence is the larger row's, as `merge_rows` takes it from the
    worst repeat.
    """
    if len(rows) == 1:
        return rows[0]
    out = dict(max(rows, key=_rank))
    for col in ("count", "self_ms", "total_ms", "covered_ms", THREADS):
        values = [r[col] for r in rows if r.get(col) is not None]
        if values:
            out[col] = round(sum(values), 2)
    tops = [r["max_ms"] for r in rows if r.get("max_ms") is not None]
    if tops:
        out["max_ms"] = max(tops)
    return out



# What marks a row the predicate passed, inside the merge.
_OVER = "over_bar"



# What `merge_rows` sets itself, or leaves out on purpose, rather than take
# from the worst repeat with the rest of its columns.
_MERGED_ELSEWHERE = frozenset({*NUMERIC, THREADS, "runs", "fired", "spread", "variants",
                               "stacks", _OVER})


def merge_rows(per_run: list[list[dict[str, Any]]], identity: tuple[str, ...],
               total: int, *, counts: bool = False) -> list[dict[str, Any]]:
    """The rows of one detector across its repeats, folded to one row per identity.

    `per_run` is one list of rows per repeat, in report order. The repeat's
    index is what the `runs` column counts, and counting rows instead of
    repeats is how a detector with two rows per run once printed "6/3".

    `counts` keeps the per-run `count` in `spread` too, beside the times.
    The markers table asks for it: "one call became two" is the shape of
    work done twice, and `compare` can only judge it from each run (#618).

    A row's name is folded before it gets here, once, by the detector that
    grouped it (`_name_family`, #831): the merge pairs rows by their names as
    they are. The names the trace had under a folded one are the row's
    `variants`, kept from every repeat.

    A row a detector with `@fires` marked (`_OVER`) is found in a repeat
    whether or not it passed there: `runs` counts the repeats that found it,
    and `fired` those where it passed, when that is fewer (#505, #832). Its
    median covers every repeat it was found in. The evidence comes from a
    repeat it passed in: the rows under the bar carry no stacks.
    """
    def key_of(row: dict[str, Any]) -> tuple:
        return tuple(row.get(c) for c in identity)

    groups: dict[tuple, dict[int, list[dict]]] = {}
    variants: dict[tuple, dict[str, set]] = {}

    def named(key: tuple, row: dict[str, Any]) -> None:
        for col, names in (row.get("variants") or {}).items():
            variants.setdefault(key, {}).setdefault(col, set()).update(names)

    for i, rows in enumerate(per_run):
        for row in rows:
            key = key_of(row)
            groups.setdefault(key, {}).setdefault(i, []).append(row)
            named(key, row)

    out = []
    for key, by_run in groups.items():
        present = dict(by_run)
        # Rows a detector with `@fires` marked (#832): every repeat that
        # found the row is in `runs`, and those where it passed the
        # predicate are `fired`. A repeat that marked none is still a
        # measurement, and counts as one.
        marked = any(_OVER in r for rows in by_run.values() for r in rows)
        over = ({i: rows for i, rows in by_run.items() if any(r.get(_OVER) for r in rows)}
                if marked else by_run)
        fired = [_one_run(rows) for rows in (over or by_run).values()]
        # In report order: the values of `spread` are read as the run index.
        values_by_run = {i: _one_run(rows) for i, rows in present.items()}
        found = [values_by_run[i] for i in sorted(values_by_run)]
        row: dict[str, Any] = dict(zip(identity, key, strict=True))
        row["runs"] = f"{len(present)}/{total}"
        if len(present) > len(over):
            row["fired"] = f"{len(over)}/{total}"
        if marked:
            row[_OVER] = bool(over)
        if key in variants:
            row["variants"] = {col: sorted(names)
                               for col, names in variants[key].items()}
        spread = {}
        for col in NUMERIC:
            values = [f[col] for f in found
                      if f.get(col) is not None]
            if not values:
                continue
            # The longest occurrence is the longest any repeat saw: a median
            # of each repeat's longest is the one number that hides a single
            # slow occurrence, and printed under "Max" it read as one (#512).
            # How long it was in the other repeats is in `spread.max_ms`.
            mid = round(max(values) if col == "max_ms" else median(values), 2)
            # The median of an even number of counts is the mean of the middle
            # two, a float even when the two are equal: two repeats of one
            # trace printed `N 6.0` where one printed `6`. A whole one stays
            # whole; one between two counts keeps its half.
            row[col] = int(mid) if col == "count" and mid == int(mid) else mid
            if col in SPREAD:
                # In report order, and only the repeats where this row was
                # found at all — which is what the `runs` column counts —
                # over the bar or under it.
                kept = [round(v, 2) for v in values]
                spread[col] = {
                    "min": round(min(values), 2),
                    "max": round(max(values), 2),
                    "values": kept,
                }
                # The smallest move `compare` could call real in this row,
                # against as many runs spread the same way. From the values
                # as kept, so the report and a later comparison agree.
                reach = stats.resolves(kept)
                if reach is not None:
                    spread[col]["resolves_ms"] = reach
                    spread[col]["resolves_pct"] = (
                        round(reach / row[col] * 100, 1) if row[col] else None)
        # How many threads the row adds up: the most any repeat found. It is
        # what the pool is, not a measurement with a middle — a worker that
        # did nothing in one repeat was not counted there (#511).
        pools = [f[THREADS] for f in found if f.get(THREADS) is not None]
        if pools:
            row[THREADS] = max(pools)
        calls = [f["count"] for f in found if f.get("count") is not None]
        if counts and calls:
            spread["count"] = {"min": min(calls), "max": max(calls), "values": calls}
        if spread:
            row["spread"] = spread
        # Evidence comes from the worst repeat: that is where it says most.
        # Unless it is part of what names the row, in which case it is the
        # same in every repeat by construction and already set above.
        worst = max(fired, key=_rank)
        # Unless it is a repeat whose sampler lost the process, while the
        # others got stacks: its evidence would say nothing ran that anyone
        # could name. On a device one recording of six lost every stack, and
        # for a thread it happened to be the worst in, the merged row said
        # "none with a stack" over five repeats that named the work. The worst
        # of the repeats with stacks speaks for the row then.
        stacked = [f for f in fired if (f.get("stacks") or {}).get("with_stack")]
        if stacked and not (worst.get("stacks") or {}).get("with_stack"):
            worst = max(stacked, key=_rank)
        if "detail" not in identity and worst.get("detail") is not None:
            row["detail"] = worst["detail"]
        # What the samples named goes with the evidence that quotes it: from
        # one repeat, the same one, or the row would name functions in one
        # place and other shares of them in the other.
        if worst.get("stacks") is not None:
            row["stacks"] = worst["stacks"]
        # A column of the detector's own beyond the shared ones, such as
        # what its predicate is held against (`frame_jank`'s
        # `verdict_frames`, #832), comes from the same repeat.
        for col, value in worst.items():
            if col not in row and col not in _MERGED_ELSEWHERE:
                row[col] = value
        out.append(row)
    return sorted(out, key=standing, reverse=True)


def standing(row: dict[str, Any]) -> tuple[float, float]:
    """Where a row stands among its detector's: its median, times the share of
    the repeats that found it; the median breaks a tie.

    The median is over the repeats that found the row, so a row seen once has
    that one value for a median, and ranked by it alone a 200 ms spike in one
    repeat of five came before 60 ms in all five. A view shows the first rows
    and says how many it left out (#495) — `report -d` five, the overview
    one, the page's request to an agent four — and the first rows were the
    spikes. Times its share, the spike counts for 40 ms and the steady row
    for 60. A row every repeat found, and a row of one trace, which has no
    `runs`, stand by the median as they always did.
    """
    try:
        value = float(row.get(metric_of(row)) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    part = short_runs(row.get("runs"))
    return (value * part[0] / part[1] if part and part[1] else value, value)



_FLOORED = ("self_ms", "total_ms", "max_ms")

