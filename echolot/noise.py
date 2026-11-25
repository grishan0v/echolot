"""How many rows hold with nothing changed: a set split against itself (#569).

The Holds column comes with one measured promise: a build split against
itself held a row past the floor in about two comparisons of a hundred
(`stats.shift`). That was one app on one phone. A project reading `yes` in
its own comparison cannot tell from it whether its scenario holds one row in
two hundred by chance or one in twenty, and it has nothing to choose
`--floor-ms` by but the default.

This measures it on the project's own set. The set's runs are split in two,
many times over, each pair of halves is merged and compared exactly as
`analyze` and `compare` do it, floor included, and the rows that hold are
counted. Nothing changed between the halves, so every row that holds is
noise.

The halves are merged from the repeats' own reports (`report.aggregate`),
not cut out of the merged one. A merged row keeps its runs' values without
the run each came from wherever some run did not find it, and a second
merge written here to cut it apart could read a row otherwise than the
first. The repeats' reports are what `analyze` holds just before it merges
them, so `analyze --noise` is where this runs: the traces are read once,
and every split after that is arithmetic over what they gave.

Two readings:

- At random: `SPLITS` splits drawn from `SEED`, no split twice, or every
  split there is when there are no more than that. The half with fewer
  runs is the before. Over the splits: the median and the 95th percentile
  of the rows with a verdict and of the rows that hold, how many splits held
  any row, how many held the window, and the floor at which the median
  split holds none.
- In order: the first runs against the last. A set from one directory is
  cut at its middle, and this is the drift of #508 in a comparison's units.
  A set whose traces come from several directories, the way `collect` sets
  each round aside, is cut where one directory ends nearest the middle: two
  rounds of one build, recorded apart, against each other. That is what a
  before and an after recorded in two sittings get with nothing changed, and
  random halves, which mix the sittings, cannot show it.

The same reports, splits and seed give the same numbers every time.
"""

from __future__ import annotations

import itertools
import json
import math
import random
from pathlib import Path
from statistics import median
from typing import Any

from . import compare as compare_mod
from . import report as report_mod
from . import table
from .words import figure as _n

# How many random splits, and the seed they are drawn from. Two hundred put
# the share of splits with a row that holds in steps of half a percent and
# its 95th percentile on ten splits, and take about half a minute for fifteen
# runs of three hundred rows; the seed is the issue's number and nothing else.
SPLITS = 200
SEED = 569

# The fewest runs a set needs: four a side, the fewest at which `stats.shift`
# judges a row at all whichever way the runs fall.
MIN_RUNS = 8

# The percentile beside the median: a split as bad as one in twenty.
PERCENTILE = 95

# How many of the rows that held first against last are named.
SHOWN = 5


class TooFew(ValueError):
    """A set with fewer runs than `MIN_RUNS`."""


def too_few(runs: int) -> str:
    """Why a set of `runs` is not split."""
    return (f"--noise splits a set in two, and needs {MIN_RUNS} runs or more: "
            f"{MIN_RUNS // 2} a side is the fewest at which a row's move can be "
            f"judged at all. This set has {runs}")


def possible(runs: int) -> int:
    """How many ways `runs` split into two halves, the smaller one before.

    An even number splits into equal halves, and a split and its mirror are
    one split: fifteen runs split 6435 ways, fourteen 1716, eight 35.
    """
    ways = math.comb(runs, runs // 2)
    return ways // 2 if runs % 2 == 0 else ways


def halves(runs: int, splits: int = SPLITS, seed: int = SEED) -> list[tuple[int, ...]]:
    """The runs that go before in each split, by their place in the set.

    `splits` distinct ones drawn from `seed`, in the order drawn; every one,
    in order, when there are no more than that. Equal halves are told apart
    by the one holding the first run, so a split is not drawn again as its
    mirror.
    """
    half = runs // 2

    def key(pick: tuple[int, ...]) -> tuple[int, ...]:
        if runs % 2:
            return pick
        rest = tuple(i for i in range(runs) if i not in pick)
        return min(pick, rest)

    if possible(runs) <= splits:
        return [p for p in itertools.combinations(range(runs), half) if key(p) == p]
    rng = random.Random(seed)
    seen: set[tuple[int, ...]] = set()
    out: list[tuple[int, ...]] = []
    while len(out) < splits:
        pick = tuple(sorted(rng.sample(range(runs), half)))
        if key(pick) in seen:
            continue
        seen.add(key(pick))
        out.append(pick)
    return out


def in_order(reports: list[dict[str, Any]]) -> tuple[int, str]:
    """Where the set is cut first against last, and why there: `sittings`
    where its traces move from one directory to the next nearest the
    middle, `middle` for a set from one directory."""
    runs = len(reports)
    places = [str(Path(str(r.get("trace") or "")).parent) for r in reports]
    cuts = [i for i in range(1, runs) if places[i] != places[i - 1]]
    if not cuts:
        return runs // 2, "middle"
    return min(cuts, key=lambda i: (abs(2 * i - runs), i)), "sittings"


def read(reports: list[dict[str, Any]], *, splits: int = SPLITS, seed: int = SEED,
         floor_ms: float = compare_mod.FLOOR_MS,
         floor_ratio: float = compare_mod.FLOOR_RATIO,
         temp_prefix: str | None = None) -> dict[str, Any]:
    """The noise reading of a set, from its repeats' reports in set order.

    The reports are not changed: each half is merged from copies.
    """
    runs = len(reports)
    if runs < MIN_RUNS:
        raise TooFew(too_few(runs))
    texts = _texts(reports)

    def compared(before: list[int]) -> dict[str, Any]:
        after = [i for i in range(runs) if i not in before]
        return _tally(before, compare_mod.build(
            _merged(texts, before), _merged(texts, after),
            before_path="before", after_path="after",
            floor_ms=floor_ms, floor_ratio=floor_ratio, temp_prefix=temp_prefix))

    drawn = [compared(list(pick)) for pick in halves(runs, splits, seed)]
    cut, why = in_order(reports)
    ordered = compared(list(range(cut)))
    return {
        "runs": runs,
        # Which set, in its order: `before` below is by place in this list.
        "traces": [r.get("trace") for r in reports],
        "floor": {"abs_ms": floor_ms, "ratio": floor_ratio},
        "random": _random(drawn, runs, seed, floor_ms),
        "in_order": _ordered(ordered, drawn, cut, runs, why),
        # Every split as it came out, for any floor above this one: a row
        # holds against an absolute floor F when its `clears_ms` is past F.
        "splits": [{k: s[k] for k in ("before", "tested", "holds", "clears_ms", "window")}
                   for s in drawn],
    }


def _texts(reports: list[dict[str, Any]]) -> list[str]:
    """Each repeat's report as JSON, once, for every half to be merged from.

    The merge takes things out of the reports it is given, so each half
    gets its own, read back from the text the way a report written to disk
    is: in a fraction of the time a deep copy takes. Without the slice names
    each repeat read (`report.NAMES_ALL`): they make the list of names on
    one side only, beside the table, and no row with a verdict. Seventeen
    thousand names a run on a busy app, merged and packed again for every
    half, would cost more than the rest of the split; and the merge of the
    whole set has taken them out of the repeats already.
    """
    return [json.dumps({k: v for k, v in r.items() if k != report_mod.NAMES_ALL},
                       ensure_ascii=False)
            for r in reports]


def _merged(texts: list[str], picked: list[int]) -> dict[str, Any]:
    """The half of the set at `picked`, merged as `analyze` merges a set."""
    return report_mod.aggregate([json.loads(texts[i]) for i in picked])


def _clears(row: dict[str, Any]) -> float:
    """How far past zero the range of a row that holds stays: the floor a
    row would need before it held no more, the ratio's part left as it is."""
    s = row["shift"]
    return round(s["low_ms"] if s["low_ms"] > s["floor_ms"] else -s["high_ms"], 2) + 0.0


def _tally(before: list[int], cmp: dict[str, Any]) -> dict[str, Any]:
    """What one split's comparison held."""
    tested = [r for r in cmp["rows"] if r.get("holds") is not None]
    held = sorted((r for r in tested if r["holds"]), key=_clears, reverse=True)
    return {"before": before, "tested": len(tested), "holds": len(held),
            "clears_ms": [_clears(r) for r in held],
            "window": (cmp.get("window") or {}).get("holds"),
            "held": held}


def _rank(values: list[float], pct: int) -> float:
    """The nearest-rank percentile: a value the splits gave, not one between."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"median": round(median(values), 2) + 0.0,
            "p95": round(_rank(values, PERCENTILE), 2) + 0.0,
            "max": round(max(values), 2) + 0.0}


def _share(tally: dict[str, Any]) -> float | None:
    """Rows that held as a percentage of those with a verdict."""
    return round(100 * tally["holds"] / tally["tested"], 2) if tally["tested"] else None


def _random(drawn: list[dict[str, Any]], runs: int, seed: int,
            floor_ms: float) -> dict[str, Any]:
    shares = [s for s in map(_share, drawn) if s is not None]
    window = [s["window"] for s in drawn if s["window"] is not None]
    # The largest `clears_ms` of each split, none as 0: the median split
    # holds no row past the floor at its middle value, since more than half
    # of the splits clear no further.
    most = sorted(s["clears_ms"][0] if s["clears_ms"] else 0.0 for s in drawn)
    middle = most[len(most) // 2]
    return {
        "splits": len(drawn),
        "of": possible(runs),
        "seed": seed,
        "sizes": [runs // 2, runs - runs // 2],
        "tested": _spread([s["tested"] for s in drawn]),
        "holds": _spread([s["holds"] for s in drawn]),
        "holds_pct": _spread(shares),
        "with_any": sum(1 for s in drawn if s["holds"]),
        # Of the splits whose window had a verdict at all.
        "window_held": sum(window) if window else None,
        "window_tested": len(window),
        # Null when the median split holds no row at the floor given.
        "floor_ms": math.ceil(middle) if middle > floor_ms else None,
    }


def _ordered(tally: dict[str, Any], drawn: list[dict[str, Any]], cut: int, runs: int,
             why: str) -> dict[str, Any]:
    return {
        "at": why,
        "sizes": [cut, runs - cut],
        "tested": tally["tested"],
        "holds": tally["holds"],
        "holds_pct": _share(tally),
        "window": tally["window"],
        # How many random splits held as many rows or more.
        "as_many": sum(1 for s in drawn if s["holds"] >= tally["holds"]),
        "rows": [{"detector": r["detector"], "location": r["location"],
                  **({"detail": r["detail"]} if r.get("detail") is not None else {}),
                  "delta_ms": r["delta_ms"], "clears_ms": _clears(r)}
                 for r in tally["held"][:SHOWN]],
    }


# --- what analyze prints and writes -------------------------------------------

def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def to_markdown(block: dict[str, Any]) -> str:
    """The reading for a person: one table and what it says."""
    rnd, order = block["random"], block["in_order"]
    floor = block["floor"]
    a, b = rnd["sizes"]
    oa, ob = order["sizes"]
    k = rnd["splits"]
    first = ("the first sitting against the second" if order["at"] == "sittings"
             else "the first runs against the last")
    out = [
        "# Noise: the set against itself",
        "",
        f"{block['runs']} runs split in two with nothing changed between the halves, so "
        f"every row that holds is noise. Each pair of halves is merged as `analyze` "
        f"merges a set and compared as `compare` compares two, at a floor of "
        f"{_n(floor['abs_ms'])} ms or {_n(floor['ratio'] * 100)}%.",
        "",
    ]
    held = rnd["holds"] or {}
    shares = rnd["holds_pct"] or {}
    rows = [
        {"split": f"at random: {k} of {rnd['of']} splits, seed {rnd['seed']}",
         "runs": f"{a} against {b}",
         "tested": _n((rnd["tested"] or {}).get("median", 0)),
         "holds": f"{_n(held.get('median', 0))}, {_n(held.get('p95', 0))}",
         "share": f"{_pct(shares.get('median'))}, {_pct(shares.get('p95'))}",
         "window": ("—" if rnd["window_held"] is None
                    else f"{rnd['window_held']} of {rnd['window_tested']}")},
        {"split": first,
         "runs": f"{oa} against {ob}",
         "tested": str(order["tested"]),
         "holds": str(order["holds"]),
         "share": _pct(order["holds_pct"]),
         "window": {None: "—", True: "yes", False: "no"}[order["window"]]},
    ]
    out.append(table.render(rows, headers={
        "split": "Split", "runs": "Runs", "tested": "With a verdict", "holds": "Hold",
        "share": "Hold, of those with a verdict", "window": "Window held"}))
    out += ["", f"At random: the median over the splits, then the {PERCENTILE}th "
                f"percentile, a split as bad as one in twenty.", ""]
    if rnd["with_any"]:
        out.append(
            f"- A row held in {rnd['with_any']} of the {k} random splits "
            f"({100 * rnd['with_any'] / k:.0f}%): in a "
            f"comparison of {a} against {b} runs of this scenario, a `yes` on its own is "
            f"what nothing at all gives that often. Read it with the rows around it.")
    else:
        out.append(f"- No row held in any random split: at {a} against {b} runs, a `yes` "
                   f"is not this scenario's noise.")
    if rnd["floor_ms"] is not None:
        out.append(
            f"- `compare --floor-ms {rnd['floor_ms']}` leaves the median split with no row "
            f"that holds; the floor here is {_n(floor['abs_ms'])} ms. A move under that is "
            f"within what this scenario does by itself.")
    else:
        out.append(f"- The median split holds no row at the {_n(floor['abs_ms'])} ms "
                   f"floor already.")
    said = f"- {first.capitalize()} held {order['holds']}, as many as {order['as_many']} " \
           f"of {k} random splits did: "
    if order["as_many"] * 20 > k:
        said += "within what random halves hold."
    elif order["at"] == "sittings":
        said += (f"the two sittings differ by more than the runs of one do, and a before "
                 f"and an after recorded apart hold about {order['holds']} rows with "
                 f"nothing changed. Record the two sides back to back.")
    else:
        said += ("the runs change from the first to the last, and a comparison reads "
                 "that as a move: see the report's drift line.")
    out.append(said)
    if order["window"] and rnd["window_held"] is not None:
        out.append(
            f"- The window held {first}, and in {rnd['window_held']} of "
            f"{rnd['window_tested']} random splits: "
            + ("within what random halves hold." if rnd["window_held"] * 20 > rnd["window_tested"]
               else "the window itself differs between them by more than its runs do."))
    if order["rows"]:
        out += ["", f"Held {first}:", ""]
        out.append(table.render(
            [{"detector": f"`{r['detector']}`",
              "location": r["location"] + (f" · {r['detail']}" if r.get("detail") else ""),
              "delta": f"{r['delta_ms']:+g} ms", "clears": f"{r['clears_ms']:g} ms"}
             for r in order["rows"]],
            headers={"detector": "Detector", "location": "Where", "delta": "Δ",
                     "clears": "Holds past"}))
    out += ["", f"Measured at {a} against {b} runs: a comparison of more runs a side "
                f"resolves smaller moves, and holds a little less by chance."]
    return "\n".join(out)
