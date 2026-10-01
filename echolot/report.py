"""The Marker Report — what the agent sees instead of the trace.

Two formats from one set of data:
  JSON      — the agent's input, stable schema
  Markdown  — for humans and for slides
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from statistics import median
from typing import Any

from . import stats, table

COLUMNS = ["location", "runs", "count", "self_ms", "total_ms", "max_ms",
           "covered_ms", "code", "detail"]
# Keys a row may carry that are not columns. `_table` renders anything it does
# not know as an extra column, which is right for a detector that invents one
# and wrong for bookkeeping the report writes itself. `places` is the json
# side of the `code` column — see place.py — and `stacks` the json side of
# what the evidence says the samples named — see stacks.py.
HIDDEN = {"spread", "places", "stacks"}
HEADERS = {
    "location": "Where",
    "runs": "Runs",
    "count": "N",
    "self_ms": "Self, ms",
    "total_ms": "Total, ms",
    "max_ms": "Max, ms",
    "covered_ms": "Instrumented, ms",
    "code": "In the code",
    "detail": "Evidence",
}

# Columns averaged by median when repeats are merged.
NUMERIC = ["count", "self_ms", "total_ms", "max_ms", "covered_ms"]

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

# Names that differ only by numbers are one phenomenon: 'Lock contention (owner
# tid: 1234)' and the same with tid 5678, `Choreographer#doFrame 55112` in one
# run and `55120` in the next, worker-2 and worker-5 of one pool.
_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")


def family(name: str, keep: str | None = None) -> str:
    """Collapses names that differ only by numbers.

    Used by `names` to keep an inventory of a real trace from running to
    thousands of rows, and by `compare` as the second matching pass: without
    it a thread pool that handed the work to another worker reads as one row
    vanishing and an unrelated one appearing.

    `keep` is a prefix whose numbers are nobody's accident. Everything this
    collapses — a tid inside a lock note, a frame number, worker-2 against
    worker-5 — is a number the runtime chose, and two names differing only
    there are one phenomenon. A marker the agent placed is the opposite case:
    it wrote the digits itself, to tell two things apart.

    That cost a real finding. An agent instrumenting a migration ladder wrote
    `AGENTTMP_fill_v4` and `AGENTTMP_fill_v6` around the two rungs, and got
    back one row — `AGENTTMP_fill_v# · N=2 · 1447 ms`. One stage that happens
    twice looks ordinary; two rungs where the second repeats the first is the
    bug it was hunting. The tool had both rows and merged them.
    """
    if keep and name.startswith(keep):
        return name
    return _DIGITS.sub("#", _HEX.sub("0x#", name))


def metric_of(row: dict[str, Any]) -> str:
    """Which column this row is judged by.

    Self time where a detector measures it, total time otherwise. One rule,
    used for ranking inside a report and for the delta between two.
    """
    return "self_ms" if row.get("self_ms") is not None else "total_ms"


def build(
    trace: str,
    window: dict[str, Any],
    results: list[dict[str, Any]],
    toolchain: dict[str, Any] | None = None,
    absent: list[str] | None = None,
    environment: dict[str, Any] | None = None,
    markers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`absent` — shipped detectors this config left out. See `to_markdown`.

    `environment` — the platform state every duration below is conditional on.
    Optional because a report can be built from a trace recorded before those
    sources existed; the key is then absent, which `compare` reads as "cannot
    be checked" and never as "it held steady".

    `markers` — the project's own names, measured whatever the detectors say.
    Optional for the same reason: a report read back from before the section
    existed has none, and a reader must not take that for "no markers".
    """
    fired = [r for r in results if r["rows"]]
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trace": trace,
        "toolchain": toolchain or {},
        "window": window,
        "environment": environment or {},
        "markers": markers or {"prefix": None, "globs": [], "rows": [], "absent": []},
        "summary": {
            "detectors_run": len(results),
            "detectors_fired": len(fired),
            "fired_ids": [r["id"] for r in fired],
            "absent_ids": sorted(absent or []),
        },
        "detectors": results,
    }


def _rank(row: dict[str, Any]) -> float:
    return row.get(metric_of(row)) or 0.0


def identity_of(detector: dict[str, Any]) -> tuple[str, ...]:
    """Which columns name a row of this detector's result.

    Declared in the .sql header and carried in the report; `location` alone
    for a report written before the field existed, which is what merging
    always assumed.
    """
    cols = detector.get("identity") or ["location"]
    return tuple(cols)


def _merge_budget(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The window budget across repeats, bucket by bucket, by median.

    The median per bucket rather than the budget of the median run: the
    question a reader asks of it is "how much of a typical run went here",
    one bucket at a time.

    The consequence is that the medians need not add up to the median window.
    Three runs whose kernel time peaked in different ones each contribute
    their middle value, and the sum of those lands wherever it lands — on a
    live three-run set it came to 383.09 ms against a median window of 380.54,
    and the report said it had accounted for 100.7% of the scenario. Every one
    of those runs accounted for exactly 100.0% on its own.

    So the share is the median of the per-run shares rather than a ratio of
    two independent medians. Coverage is a property of a run — this run
    explained this much of its own window — and a typical run's coverage is
    the answer to the question being asked. `accounted_ms` stays the sum of
    the buckets above it, because that is what makes the printed numbers add
    up, and it is deliberately not the numerator of the percentage: those are
    two different questions and only one of them can be answered by a
    division.

    The share of the window the findings cover is a property of a run for
    the same reason, and is merged the same way. Each run's share is of that
    run's own findings, which is what a typical run looks like; a detector
    left out of the count in any run is named.
    """
    budgets = [r["window"].get("main_thread") for r in reports
               if (r.get("window") or {}).get("main_thread")]
    if not budgets:
        return None
    out: dict[str, Any] = {}
    for key in ("on_cpu", "waiting_for_cpu", "in_kernel", "sleeping", "other"):
        out[key] = round(median([b.get(key) or 0.0 for b in budgets]), 2)
    windows = [b["window_ms"] for b in budgets if b.get("window_ms")]
    out["accounted_ms"] = round(sum(out.values()), 2)
    out["window_ms"] = round(median(windows), 2) if windows else None
    shares = [b["accounted_pct"] for b in budgets
              if b.get("accounted_pct") is not None]
    out["accounted_pct"] = round(median(shares), 1) if shares else None
    covered = [b for b in budgets if b.get("in_rows_pct") is not None]
    if covered:
        out["in_rows_ms"] = round(median([b["in_rows_ms"] for b in covered]), 2)
        out["in_rows_pct"] = round(
            median([b["in_rows_pct"] for b in covered]), 1)
        out["in_rows_uncounted"] = sorted(
            {d for b in covered for d in b.get("in_rows_uncounted") or []})
    out["runs"] = f"{len(budgets)}/{len(reports)}"
    return out



def _merge_startup(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The startup across repeats, reason by reason, by median, like the budget.

    A reason one repeat did not have counts as nothing in it. The type is the
    one every repeat agrees on, or all of them named: a set that mixes cold
    starts with warm ones is two measurements, and a median over both is
    neither.
    """
    found = [r["window"]["startup"] for r in reports
             if (r.get("window") or {}).get("startup")]
    if not found:
        return None
    names = sorted({name for s in found for name in s.get("reasons") or {}})
    reasons = {name: round(median([(s.get("reasons") or {}).get(name, 0.0) for s in found]), 2)
               for name in names}
    types = sorted({s.get("type") or "unknown" for s in found})
    out: dict[str, Any] = {
        "type": types[0] if len(types) == 1 else " and ".join(types),
        "dur_ms": round(median([s["dur_ms"] for s in found]), 2),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    for key in ("from_window_start_ms", "from_window_end_ms", "in_window_ms"):
        out[key] = round(median([s.get(key) or 0.0 for s in found]), 2)
    several = [s["startups"] for s in found if s.get("startups")]
    if several:
        out["startups"] = max(several)
    out["runs"] = f"{len(found)}/{len(reports)}"
    return out

def _merge_environment(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The platform state across repeats — median for the clock, worst case for the rest.

    The median matches what every other number in the merged report is: the
    typical repeat rather than the luckiest or the unluckiest. Its `spread`
    twin is here for the same reason it exists on the rows — a set whose clock
    ran from 900 to 2000 MHz is a set to throw away, and a single median hides
    exactly that.

    Throttling does not take a median. One repeat out of ten recorded on a
    throttled device is a fact about the set, and rounding it away by majority
    would be the tool quietly deciding the reader did not need to know.
    """
    envs = [r.get("environment") or {} for r in reports]
    cpus = [e["cpu"] for e in envs if e.get("cpu")]
    thermals = [e["thermal"] for e in envs if e.get("thermal")]
    memories = [e["memory"] for e in envs if e.get("memory")]

    out: dict[str, Any] = {"cpu": None, "thermal": None, "memory": None}
    if cpus:
        means = [c["mean_mhz"] for c in cpus if c.get("mean_mhz")]
        out["cpu"] = {
            "mean_mhz": round(median(means), 1) if means else None,
            "mean_mhz_min": min(means) if means else None,
            "mean_mhz_max": max(means) if means else None,
            "min_mhz": min(c["min_mhz"] for c in cpus),
            "max_mhz": max(c["max_mhz"] for c in cpus),
            "on_cpu_ms": round(median([c["on_cpu_ms"] for c in cpus]), 2),
            "measured_ms": round(median([c["measured_ms"] for c in cpus]), 2),
            # How many of the repeats had a clock to read at all, in the same
            # form the rows use: a set where two traces of ten carry it is not
            # a set the comparison may lean on.
            "runs": f"{len(cpus)}/{len(reports)}",
        }
    if thermals:
        hot = [t["max_celsius"] for t in thermals if t.get("max_celsius") is not None]
        throttled = [t for t in thermals if t.get("throttled")]
        out["thermal"] = {
            "max_celsius": max(hot) if hot else None,
            "hottest_zone": next((t.get("hottest_zone") for t in thermals
                                  if t.get("max_celsius") == (max(hot) if hot else None)),
                                 None),
            "throttled": bool(throttled),
            "throttle_device": throttled[0].get("throttle_device") if throttled else None,
            "throttled_runs": f"{len(throttled)}/{len(reports)}",
        }
    if memories:
        avail = [m["available_mb_min"] for m in memories
                 if m.get("available_mb_min") is not None]
        faults = [m["major_faults"] for m in memories
                  if m.get("major_faults") is not None]
        out["memory"] = {
            "available_mb_min": min(avail) if avail else None,
            "major_faults": round(median(faults)) if faults else None,
        }
    out["sampling"] = _merge_sampling([e.get("sampling") for e in envs])
    out["missing"] = sorted(k for k in ("cpu", "thermal", "memory")
                            if out[k] is None)
    return out


def _merge_sampling(samplings: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Callstack sampling across repeats: medians of the runs it ran in, and how many.

    `runs` is the fact that matters most. One `collect` samples every repeat
    or none, but a set can be assembled from two, and a set that is half
    sampled has the sampler's cost in its spread. The rate is kept only when
    every repeat that asked for one asked for the same.

    `runs_with_stack` counts, among those, the repeats that got any stack at
    all. A profileable app loses every stack of a cold start now and then —
    one recording of six on a device, with the other five complete — and a
    median over the six reads "all with a stack" for a repeat that had none.
    """
    asked = [s for s in samplings if s]
    if not asked:
        return None
    ran = [s for s in asked if s.get("started")]
    rates = {s.get("hz") for s in asked}
    merged = {
        "hz": rates.pop() if len(rates) == 1 else None,
        "started": bool(ran),
        "samples": round(median([s["samples"] for s in ran])) if ran else 0,
        "with_stack": round(median([s["with_stack"] for s in ran])) if ran else 0,
        "runs": f"{len(ran)}/{len(samplings)}",
        "runs_with_stack": f"{sum(1 for s in ran if s.get('with_stack'))}/{len(ran)}",
    }
    # How the app's methods were named: the most any repeat saw. A minified
    # method in one repeat is the warning's whole point, and a median would
    # vote it away.
    names = [s["names"] for s in ran if s.get("names")]
    if names:
        merged["names"] = {key: max(n.get(key) or 0 for n in names) for key in names[0]}
    return merged


def merge_rows(per_run: list[list[dict[str, Any]]], identity: tuple[str, ...],
               total: int) -> list[dict[str, Any]]:
    """The rows of one detector across its repeats, folded to one row per identity.

    `per_run` is one list of rows per repeat, in report order. The repeat's
    index is what the `runs` column counts, and counting rows instead of
    repeats is how a detector with two rows per run once printed "6/3".
    """
    groups: dict[tuple, list[tuple[int, dict]]] = {}
    for i, rows in enumerate(per_run):
        for row in rows:
            groups.setdefault(tuple(row.get(c) for c in identity),
                              []).append((i, row))

    out = []
    for key, seen in groups.items():
        found = [r for _, r in seen]
        row: dict[str, Any] = dict(zip(identity, key, strict=True))
        row["runs"] = f"{len({i for i, _ in seen})}/{total}"
        spread = {}
        for col in NUMERIC:
            values = [f[col] for f in found
                      if f.get(col) is not None]
            if not values:
                continue
            row[col] = round(median(values), 2)
            if col in SPREAD:
                # In report order, and only the repeats where this row was
                # found at all — which is what the `runs` column counts.
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
        if spread:
            row["spread"] = spread
        # Evidence comes from the worst repeat: that is where it says most.
        # Unless it is part of what names the row, in which case it is the
        # same in every repeat by construction and already set above.
        worst = max(found, key=_rank)
        # Unless it is a repeat whose sampler lost the process, while the
        # others got stacks: its evidence would say nothing ran that anyone
        # could name. On a device one recording of six lost every stack, and
        # for a thread it happened to be the worst in, the merged row said
        # "none with a stack" over five repeats that named the work. The worst
        # of the repeats with stacks speaks for the row then.
        stacked = [f for f in found if (f.get("stacks") or {}).get("with_stack")]
        if stacked and not (worst.get("stacks") or {}).get("with_stack"):
            worst = max(stacked, key=_rank)
        if "detail" not in identity and worst.get("detail") is not None:
            row["detail"] = worst["detail"]
        # What the samples named goes with the evidence that quotes it: from
        # one repeat, the same one, or the row would name functions in one
        # place and other shares of them in the other.
        if worst.get("stacks") is not None:
            row["stacks"] = worst["stacks"]
        out.append(row)
    return sorted(out, key=_rank, reverse=True)


def _merge_markers(reports: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """The markers table across repeats: the same fold as a detector's rows.

    A name the config lists is reported absent only when no repeat held it;
    one that came and went is a row with a `runs` column short of the total,
    which says the same thing more precisely.
    """
    sections = [r.get("markers") or {} for r in reports]
    head = next((s for s in sections if s), {})
    seen = {row["location"] for s in sections for row in s.get("rows") or []}
    return {
        "prefix": head.get("prefix"),
        "globs": head.get("globs") or [],
        "rows": merge_rows([s.get("rows") or [] for s in sections], ("location",), total),
        "absent": [g for g in head.get("globs") or []
                   if g in set(head.get("absent") or []) and not any(
                       fnmatch.fnmatchcase(n, g) for n in seen)],
    }


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Merges N repeats into a single report using the median.

    Median rather than mean: one outlier must not drag the conclusion along.
    And not the maximum either: then any random hiccup becomes a "finding".

    The `runs` column is the whole point of repeating. "3/3" means reproducible,
    "1/3" means you happened to catch it once, and those are different news even
    when the milliseconds match.
    """
    if len(reports) == 1:
        return reports[0]

    total = len(reports)
    merged = dict(reports[0])
    merged["traces"] = [r["trace"] for r in reports]
    merged["runs"] = total

    windows = [r["window"].get("duration_ms") for r in reports
               if r["window"].get("duration_ms") is not None]
    if windows:
        merged["window"] = dict(reports[0]["window"])
        merged["window"]["duration_ms"] = round(median(windows), 2)
        merged["window"]["duration_ms_min"] = min(windows)
        merged["window"]["duration_ms_max"] = max(windows)
        merged["window"]["main_thread"] = _merge_budget(reports)
        merged["window"]["startup"] = _merge_startup(reports)

    merged["environment"] = _merge_environment(reports)

    by_id: dict[str, list[dict]] = {}
    for report in reports:
        for det in report["detectors"]:
            by_id.setdefault(det["id"], []).append(det)

    detectors = []
    for runs in by_id.values():
        # The first repeat that got as far as running, for everything the
        # merged entry says about itself. A detector that failed reports its
        # `params` as the shipped defaults — `analyze_trace` has no resolved
        # values to report when `render` is what raised — and taking those
        # from repeat one meant a run whose first trace happened to trip a
        # SQL error described its thresholds as the built-in ones. `compare`
        # then read that against the next report and announced a threshold
        # change nobody had made.
        head = dict(next((r for r in runs if not r["error"]), runs[0]))
        head["rows"] = merge_rows([run["rows"] for run in runs],
                                  identity_of(head), total)
        head["error"] = next((r["error"] for r in runs if r["error"]), None)
        detectors.append(head)

    merged["detectors"] = detectors
    merged["markers"] = _merge_markers(reports, total)
    fired = [d for d in detectors if d["rows"]]
    merged["summary"] = {
        "detectors_run": len(detectors),
        "detectors_fired": len(fired),
        "fired_ids": [d["id"] for d in fired],
        # The same config produced every repeat, so the same detectors were
        # left out of all of them.
        "absent_ids": (reports[0]["summary"] or {}).get("absent_ids") or [],
    }
    return merged


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def _traces_line(traces: list[str], limit: int = 8) -> str:
    """The files behind `Runs: N`, so a stranger among them is visible.

    The count alone hid the failure this exists for. A probe capture left in
    `.echolot/traces/` was picked up by the documented
    `analyze .echolot/traces/*.perfetto-trace`, and `Runs: 4` after a
    `collect` that recorded three read as ordinary. The medians were taken
    across two sittings, one row came from the stray trace alone, and the
    header said nothing a reader could act on. Named, it is obvious at a
    glance which file does not belong.

    A pid would have been the precise signal and is not available: every cold
    start launches the process again, so the repeats of one healthy round
    already carry three different pids.
    """
    names = [Path(t).name.removesuffix(".perfetto-trace") for t in traces]
    shown = names if len(names) <= limit else names[:limit - 1]
    line = ", ".join(f"`{n}`" for n in shown)
    if len(shown) < len(names):
        line += f" and {len(names) - len(shown)} more"
    return "Traces: " + line


# What each bucket is called on the page. Plain words rather than the state
# letters: `D` means nothing to a reader who has not spent an afternoon in
# thread_state, and the whole point of this line is that it is read at a
# glance.
BUDGET_LABELS = (
    ("on_cpu", "on a CPU"),
    ("waiting_for_cpu", "waiting for a CPU"),
    ("in_kernel", "blocked in the kernel"),
    ("sleeping", "sleeping"),
    ("other", "other"),
)


def _budget_lines(budget: dict[str, Any] | None) -> list[str]:
    """One line saying where the window went, above the findings that explain part of it.

    "Detectors fired: 5 of 12" counts detectors. This counts the window, which
    is the number a reader actually needs to decide whether a quiet report
    means a clean run. It also makes a compound stall legible: 40% waiting for
    a CPU next to 35% blocked in the kernel is one sentence here and two
    unrelated table rows anywhere else.

    Buckets under a percent are dropped from the line and kept in the JSON.
    A tail of "0% this, 0% that" is how a summary stops being read.
    """
    if not budget:
        return []
    window = budget.get("window_ms") or 0
    if not window:
        return []

    parts = []
    for key, label in BUDGET_LABELS:
        ms = budget.get(key) or 0
        share = ms / window * 100
        if share >= 1:
            parts.append(f"{share:.0f}% {label}")
    if not parts:
        return []

    out = ["Main thread: " + " · ".join(parts)]
    out.extend(_in_rows_line(budget))
    accounted = budget.get("accounted_pct")
    # Anything much short of the whole window means the thread was not there
    # for all of it. Silence would read as "the rest was nothing".
    if accounted is not None and accounted < 95:
        out.append(
            f"> ⚠️ Only {accounted:.0f}% of the window is accounted for on the "
            f"main thread. It did not exist for the rest of it — the process "
            f"started inside the window, or the recording has a hole there — "
            f"so the shares above are of what was seen, not of the scenario."
        )
    return out


def _in_rows_line(budget: dict[str, Any]) -> list[str]:
    """How much of that window the findings below stand for, each moment once.

    Under the budget, because it is the other half of the same account: the
    line above says what the main thread was doing, this one how much of it
    the rows below describe. It is also the number a reader used to work out
    by adding up the milliseconds down the page, which counts a disk wait
    inside a slice twice and can pass 100% without a single row being wrong.
    The markers table is not in it: those are the project's own names,
    measured whether or not anything is wrong with them.
    """
    pct = budget.get("in_rows_pct")
    if pct is None:
        return []
    line = (f"Covered by the findings below: **{pct:.0f}%** of the main "
            f"thread's window, each moment counted once")
    uncounted = budget.get("in_rows_uncounted") or []
    if uncounted:
        # A detector without `@intervals`, or one whose second query failed
        # (stderr says so). Either way the number is short by an unknown
        # amount, and a reader has to know which rows it leaves out.
        line += (" — not counting " + ", ".join(f"`{d}`" for d in uncounted)
                 + ", whose rows could not be placed on the main thread's "
                   "timeline")
    return [line]



# The thread states among the startup's reasons, in the budget's words. The
# other reasons are the names the standard library gives them, kept as they
# are: a reader can look them up there.
STARTUP_STATES = {
    "Running": "on a CPU",
    "R": "waiting for a CPU",
    "R+": "waiting for a CPU",
    "D": "blocked in the kernel",
    "DK": "blocked in the kernel",
    "S": "sleeping",
}


def _startup_lines(startup: dict[str, Any] | None) -> list[str]:
    """The app's startup by Perfetto's own account, under the window's.

    The budget says where the window went by thread state. This says where
    the startup went by reason — `bind_application`, `binder`, `io`, the
    thread states — as the standard library divides it. It is the platform's
    measure, from the launch to the first frame, and a second line says how it
    sits against the window whenever the two do not coincide: a window from
    `bindApplication` misses the process start, and one that stops before the
    first frame misses the end of the startup.

    Reasons under a percent are dropped from the line and kept in the JSON,
    as in the budget.
    """
    if not startup or not startup.get("dur_ms"):
        return []
    dur = startup["dur_ms"]
    shares: dict[str, float] = {}
    for reason, ms in (startup.get("reasons") or {}).items():
        label = STARTUP_STATES.get(reason, f"`{reason}`")
        shares[label] = shares.get(label, 0.0) + ms / dur * 100
    parts = [f"{share:.0f}% {label}" for label, share
             in sorted(shares.items(), key=lambda kv: (-kv[1], kv[0])) if share >= 1]
    kind = startup.get("type") or "of a kind Perfetto could not tell"
    line = f"Startup: {kind}, **{dur:.0f} ms** from the launch to the first frame"
    if startup.get("startups"):
        line += (f" (the one of {startup['startups']} in the trace that shares "
                 f"the most with the window)")
    if parts:
        line += ": " + " · ".join(parts)
    return [line, *_startup_against_window(startup)]


def _startup_against_window(startup: dict[str, Any]) -> list[str]:
    """Where the startup began and ended against the scenario's window, when not with it."""
    began = startup.get("from_window_start_ms") or 0.0
    ended = startup.get("from_window_end_ms") or 0.0
    if not startup.get("in_window_ms"):
        return [
            "> ⚠️ The startup does not overlap the scenario's window: its "
            "shares are of the launch, and the findings below are of the "
            "window."
        ]
    if abs(began) < 1 and abs(ended) < 1:
        return []
    start = ("began with the window" if abs(began) < 1 else
             f"began {abs(began):.0f} ms {'before' if began < 0 else 'after'} the window opened")
    end = ("ended with it" if abs(ended) < 1 else
           f"ended {abs(ended):.0f} ms {'before' if ended < 0 else 'after'} it closed")
    return [f"That is the platform's measure rather than the window: the startup "
            f"{start}, and {end}."]

def _environment_lines(env: dict[str, Any]) -> list[str]:
    """One line for the machine the numbers below were measured on.

    Placed above the findings rather than under them: it is the condition
    every duration in the report is stated under, and a reader who learns
    after the table that the device was throttled has already drawn the
    conclusion.

    Kept to one line while there is nothing wrong, because on a settled device
    there is nothing to act on and a paragraph of context would push the
    findings down the page for no reason. Throttling gets its own warning,
    since it means the numbers below understate the app.
    """
    if not env:
        return []
    out: list[str] = []
    cpu, thermal = env.get("cpu"), env.get("thermal")

    bits: list[str] = []
    if cpu and cpu.get("mean_mhz"):
        bit = f"clock **{cpu['mean_mhz']:.0f} MHz**"
        low, high = cpu.get("mean_mhz_min"), cpu.get("mean_mhz_max")
        if low is not None and high is not None and high > low:
            bit += f" (from {low:.0f} to {high:.0f} across repeats)"
        # Below the whole, the mean is an average over the part we could see,
        # and saying so is cheaper than having it believed of all of it.
        covered, total = cpu.get("measured_ms"), cpu.get("on_cpu_ms")
        if covered and total and covered < total * 0.99:
            bit += f", measured over {covered / total * 100:.0f}% of on-CPU time"
        bits.append(bit)
    if thermal and thermal.get("max_celsius") is not None:
        bits.append(f"peak {thermal['max_celsius']:.0f} °C")
    memory = env.get("memory")
    if memory and memory.get("available_mb_min") is not None:
        bits.append(f"{memory['available_mb_min']:.0f} MB free at the low point")
    if bits:
        out.append("Device: " + ", ".join(bits))

    if thermal and thermal.get("throttled"):
        where = thermal.get("throttle_device")
        runs = thermal.get("throttled_runs")
        out.append(
            "> ⚠️ The kernel throttled the device during this window"
            + (f" (`{where}`)" if where else "")
            + (f", in {runs} repeats" if runs else "")
            + ". Capacity was taken away while these numbers were measured, "
              "so they are the app on a slowed machine. Let it cool and "
              "record again before comparing anything to them."
        )

    # Absence is a third answer and has to look like one. Without this the
    # header simply says nothing about the device, which reads as a device
    # with nothing to say.
    missing = env.get("missing") or []
    if missing:
        out.append(
            f"Device state not recorded: {', '.join(missing)} — this trace "
            f"carries no platform-state sources, so `compare` cannot tell a "
            f"slower machine from a slower app."
        )
    out.extend(_sampling_lines(env.get("sampling")))
    out.extend(_names_lines((env.get("sampling") or {}).get("names")))
    return out


# The share of the app's sampled methods that has to read as minified before
# the header warns. Some code arrives minified from elsewhere — SDKs ship that
# way — and no mapping of this build names it: on a phone, a build that kept
# its names had 38 of its 3,309 sampled methods so, a little over 1%. A build
# R8 minified reads that way in most of its own code and of the libraries it
# ships, and so does one given another build's mapping.
MINIFIED_SHARE = 0.10


def _names_lines(names: dict[str, Any] | None) -> list[str]:
    """Whether the app's sampled methods came back by name.

    Nothing for a build that kept its names and was given no mapping. A
    warning for a minified build without one: the frames of the app read as
    `a.b.c`, and `project.mapping` is the fix. With a mapping, one line saying
    how many methods it named back — or a warning when many still read as
    minified, which is what a mapping from another build leaves: it renames
    the frames it happens to match, wrongly, and misses the rest, and the rows
    then mix real names with minified ones as if they were one list.
    """
    if not names:
        return []
    total, left = names.get("methods") or 0, names.get("minified") or 0
    many = bool(total) and left / total >= MINIFIED_SHARE
    if "renamed" not in names:
        if not many:
            return []
        return [
            f"> ⚠️ {left} of the app's {total} sampled methods read as minified, "
            f"`a.b.c`: a reader cannot find them, and neither can `code`. Set "
            f"`project.mapping` to the build's `mapping.txt` and the frames "
            f"come back by name."
        ]
    if many:
        return [
            f"> ⚠️ {left} of the app's {total} sampled methods still read as "
            f"minified after `project.mapping` renamed {names['renamed']}. A "
            f"mapping from another build renames the frames it happens to "
            f"match, wrongly, and misses the rest: check that it is this "
            f"build's."
        ]
    line = (f"`project.mapping` named {names['renamed']} of the app's {total} "
            f"sampled methods back")
    if not left:
        return [f"{line}, and none reads as minified."]
    return [f"{line}. {left} still read as minified, too few to be this build's "
            f"own: code that arrived minified, as some SDKs ship, which no "
            f"mapping of this build names."]


def _sampling_lines(s: dict[str, Any] | None) -> list[str]:
    """What sampled the app while it was measured, and what came of it.

    Nothing when nothing did: that is how every trace was recorded before
    `runner.sampling`, and the report said nothing about it then either.

    Otherwise one line, and a warning in place of it for the three ways it
    comes back empty: no stacks, no samples of this process, no sampler. Each
    points somewhere else — the app's manifest, how the recording was
    filtered, the device. No stacks has a second cause that looks the same
    from here: a profileable app whose start the sampler lost, which on a
    device happened in one cold start of six. Only another round tells them
    apart, so the warning names both.
    """
    if not s:
        return []
    rate = f" at {s['hz']} Hz" if s.get("hz") else ""
    if not s.get("started"):
        return [
            f"> ⚠️ The recording asked for callstack samples{rate} and none "
            f"arrived: the device's sampler, `traced_perf`, did not run. The "
            f"app was not slowed by it, and there are no stacks to read."
        ]
    runs = s.get("runs") or ""
    ran, _, total = runs.partition("/")
    part = f" in {ran} of {total} repeats" if total and ran != total else ""
    cost = ("The sampler takes time from the app, so compare these numbers "
            "only with another round sampled at the same rate.")
    if part:
        cost = ("The repeats were not recorded alike, and the sampler takes "
                "time from the app, so its cost is part of the spread below.")
    samples, stacked = s.get("samples") or 0, s.get("with_stack") or 0
    head = f"Sampled callstacks{rate}{part}"
    if not samples:
        return [
            f"> ⚠️ {head}, and none of the samples in the window is this "
            f"process's. A sampler filtered by process name misses a process "
            f"it first meets under zygote's name, which is how a cold start "
            f"begins; `runner.sampling` records without that filter. {cost}"
        ]
    # A single trace has no `runs_with_stack`: it is one repeat, with a stack
    # or without.
    got, _, tried = (s.get("runs_with_stack") or f"{int(bool(stacked))}/1").partition("/")
    if got == "0":
        return [
            f"> ⚠️ {head}: {samples} samples of this process in the window, "
            f"none with a stack. Either the app is not profileable or "
            f"debuggable, which a `user` build of Android needs before the "
            f"sampler unwinds it — `<profileable android:shell=\"true\" />` "
            f"in the manifest — or the sampler lost the process as it "
            f"started, which a cold start does now and then. If another round "
            f"comes back with stacks, it was the second. {cost}"
        ]
    if got != tried:
        return [
            f"{head}: {samples} samples of this process in the window. They "
            f"came with a stack in {got} of {tried} repeats and with none in "
            f"the rest: the sampler lost the process as it started, which a "
            f"cold start does now and then, and those repeats have no stacks "
            f"to read. {cost}"
        ]
    # Rounded, a share can read 100% with samples missing their stack, or 0%
    # with some carrying one. Neither end is said unless it is exact.
    share = ("all" if stacked == samples
             else f"{min(max(round(stacked / samples * 100), 1), 99)}%")
    return [
        f"{head}: {samples} samples of this process in the window, {share} "
        f"with a stack. {cost}"
    ]


def to_markdown(report: dict[str, Any]) -> str:
    w = report["window"]
    out: list[str] = []
    out.append("# Marker Report")
    out.append("")
    traces = report.get("traces")
    if traces:
        out.append(f"Runs: **{len(traces)}**, numbers are medians across them")
        out.append(_traces_line(traces))
    else:
        out.append(f"Trace: `{report['trace']}`")
    if w.get("process"):
        out.append(f"Process: `{w['process']}` (pid {w.get('pid')})")
    alts = w.get("process_alternatives")
    if alts:
        names = ", ".join(f"`{a['name']}`" for a in alts)
        total = w.get("process_alternatives_total") or len(alts)
        if total > len(alts):
            names += f" … and {total - len(alts)} more"
        out.append(
            f"> ⚠️ The mask matched more than one process. The largest by slice "
            f"count was taken; skipped: {names}. If this is the wrong one, "
            f"narrow `project.process`."
        )
    if w.get("duration_ms") is not None:
        line = f"Scenario window: **{w['duration_ms']} ms**"
        low, high = w.get("duration_ms_min"), w.get("duration_ms_max")
        if low is not None:
            line += f" (from {low} to {high})"
        out.append(line)
        # Repeats of one scenario cannot differ several-fold. If they do, they
        # are not repeats, and a median across them means nothing.
        if low and high and high > 2 * low:
            out.append(
                f"> ⚠️ Repeat windows diverged {high / low:.1f}x. These are not "
                f"repeats of one scenario: either the anchors did not match or "
                f"the runs did different things. A median over such numbers is "
                f"meaningless."
            )

    out.extend(_budget_lines(w.get("main_thread")))
    out.extend(_startup_lines(w.get("startup")))

    # An anchor that never matched silently collapses the window onto the whole
    # trace. That has to be shouted, not hidden: otherwise the report looks
    # plausible and leads somewhere else entirely.
    for key, label in (("start_anchor", "Start"), ("end_anchor", "End")):
        anchor = w.get(key)
        if anchor and anchor.get("matches") == 0:
            out.append(
                f"> ⚠️ {label} anchor `{anchor['glob']}` was not found in the "
                f"trace — the window expanded to the whole trace. Check against "
                f"`probe`; the numbers below are not about your scenario."
            )

    # A partial account looks exactly like a complete one, which is the only
    # reason this is worth a line. Slices keep their real length across the
    # boundary; thread states are clipped, so the beginning of a stall that
    # started before the anchor is simply not in the report.
    inside = w.get("opened_inside")
    if inside and inside.get("material"):
        out.append(
            f"> ⚠️ The window opened with the main thread already blocked: "
            f"state `{inside['state']}` for {inside['total_ms']} ms, of which "
            f"{inside['before_ms']} ms happened before the anchor matched. "
            f"Whatever put it there is outside this window, so the waiting "
            f"below is the tail of it rather than the whole. Move "
            f"`scenario.start` earlier to see the cause."
        )

    out.extend(_environment_lines(report.get("environment") or {}))

    s = report["summary"]
    out.append(
        f"Detectors fired: **{s['detectors_fired']} of {s['detectors_run']}**"
    )
    # "3 of 6" reads as though six were all there is, so a detector that did
    # not run is named. It used to be named as an oversight — the config's
    # `detectors:` section was an allowlist, and every detector it happened
    # not to mention was off. This warning was written for that, and on a real
    # project it printed at the top of three reports in a row while six of ten
    # detectors sat out; being told is not the same as having chosen. Now the
    # only way to be here is to have written `false`, so it says so.
    absent = s.get("absent_ids") or []
    if absent:
        out.append(
            f"> ⚠️ {len(absent)} detector(s) are turned off in this config and "
            f"did not run: {', '.join(f'`{a}`' for a in absent)}. Remove the "
            f"`false` to bring one back, or run `--defaults` to ignore the "
            f"config's thresholds entirely."
        )
    line = _config_line(report)
    if line:
        out.append(line)
    out.append("")

    out.extend(_markers_lines(report.get("markers") or {}))

    # Nothing fired still ends the way every report does, with the Silent
    # line and the toolchain footer. It used to return here, before both —
    # and the footer is where a trace_processor other than the pinned one is
    # named, so a clean report from a binary that bypassed the pin did not
    # say so.
    if s["detectors_fired"] == 0:
        out.append("_No detector fired._")
        out.append("")
        out.append(
            "Either the run is clean or the config is wrong — check that the "
            "process name and the scenario anchors match the trace "
            "(`echolot probe`)."
        )
        out.append("")

    for d in report["detectors"]:
        if not d["rows"]:
            continue
        out.append(f"## {d['title']}")
        if d.get("why"):
            out.append(f"_{d['why']}_")
        out.append("")
        out.append(_table(d["rows"]))
        out.append("")
        out.append(f"<sub>detector `{d['id']}`, params: {d['params']}"
                   f"{_source_note(d)}</sub>")
        out.append("")

    quiet = [d["id"] for d in report["detectors"] if not d["rows"]]
    if quiet:
        out.append(f"**Silent:** {', '.join(quiet)}")

    tc = report.get("toolchain") or {}
    # Any source but the pin is somebody's own binary, and the footer says
    # whose. Only `--tp-binary` used to be marked, so a binary named by
    # `toolchain.tp_binary` in a gitignored local.yml left the report looking
    # pinned. Said even when its version could not be read: which binary ran
    # matters more than what it calls itself.
    custom = tc.get("source") not in (None, "pinned")
    if tc.get("trace_processor") or custom:
        note = f"trace_processor {tc.get('trace_processor') or 'unknown'}"
        if custom:
            note += f" (custom binary from {tc['source']}, pin bypassed)"
        out.append("")
        out.append(f"<sub>{note}</sub>")
    return "\n".join(out)


# --- views: what `echolot report` prints ---------------------------------------
#
# report.json is the agent's, and on a real hunt the agent cut it up sixteen
# times with jq and python one-liners: the keys, the window, which detectors
# fired with which thresholds, the top rows of one detector with the
# evidence shortened. Each of those is a view the report can offer directly,
# and each one-liner was a window's worth of json read to get at a line.

# What the evidence column means where it is not obvious. `main_thread_block`
# groups by thread as well as by name, and the thread is always the main
# one — so the column shows its comm, which the kernel cuts to fifteen
# characters: `m.example.myapp` for `com.example.myapp`, while
# `com.example.app` is fifteen exactly and arrives whole. Kept rather than
# renamed because it is part of the row's identity, and a rename would make
# every earlier report's rows vanish in `compare`.
EVIDENCE_LEGEND = {
    "main_thread_block": "the thread's comm — always the main thread here; "
                         "the kernel cuts the name to 15 characters",
    "uninstrumented_cpu": "the share of the thread's CPU time outside every "
                          "top-level slice; when the recording sampled "
                          "callstacks, how many stacks fell there, what ran on "
                          "them — the first named Kotlin or Java method from "
                          "the top — and after `ours:` the nearest frame of the "
                          "project's own code: `none` where a whole stack had "
                          "nothing of it, `cut` where the stack ended in the "
                          "framework before its thread's start",
}


def clip(text: Any, width: int) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= width else text[:width - 1] + "…"


def _header_lines(report: dict[str, Any]) -> list[str]:
    w = report.get("window") or {}
    out = []
    traces = report.get("traces")
    what = f"{len(traces)} runs, medians" if traces else f"`{report.get('trace')}`"
    line = f"**{what}** · process `{w.get('process')}`"
    if w.get("duration_ms") is not None:
        line += f" · window **{w['duration_ms']} ms**"
        if w.get("duration_ms_min") is not None:
            line += f" ({w['duration_ms_min']}–{w['duration_ms_max']})"
    if report.get("generated_at"):
        line += f" · {report['generated_at']}"
    out.append(line)
    anchors = []
    for key, label in (("start_anchor", "start"), ("end_anchor", "end")):
        a = w.get(key)
        if a:
            hit = a.get("matches")
            anchors.append(f"{label} `{a.get('glob')}` "
                           + ("⚠️ 0 matches" if hit == 0 else f"{hit} match(es)"))
    if anchors:
        out.append("Anchors: " + " · ".join(anchors))
    alts = w.get("process_alternatives")
    if alts:
        out.append(f"⚠️ {w.get('process_alternatives_total') or len(alts)} other "
                   f"process(es) matched the mask; the largest was taken")
    out.extend(_budget_lines(w.get("main_thread")))
    out.extend(_startup_lines(w.get("startup")))
    out.extend(_environment_lines(report.get("environment") or {}))
    return out


def overview(report: dict[str, Any]) -> str:
    """One screen: the window, and every detector with what it found."""
    out = ["# Report", ""]
    out.extend(_header_lines(report))
    m = report.get("markers") or {}
    rows, absent = m.get("rows") or [], m.get("absent") or []
    if rows or absent:
        out.append(f"Markers: {len(rows)} measured"
                   + (f", {len(absent)} listed in `domains` and not in the window" if absent else "")
                   + " — `echolot report --markers`")
    out.append("")
    table_rows = []
    for d in report.get("detectors") or []:
        found = d.get("rows") or []
        top = found[0] if found else None
        table_rows.append({
            "detector": d["id"],
            "rows": len(found) if found else "—",
            "thresholds": d.get("params_source") or "default",
            "top row": clip(top.get("location"), 48) if top else ("failed" if d.get("error") else "silent"),
            "metric": (f"{metric_of(top).replace('_ms', '')} {top.get(metric_of(top))} ms"
                       if top and top.get(metric_of(top)) is not None else ""),
        })
    out.append(table.render(table_rows))
    absent_ids = (report.get("summary") or {}).get("absent_ids") or []
    if absent_ids:
        out.append("")
        out.append("Turned off in this config: " + ", ".join(f"`{a}`" for a in absent_ids))
    out.append("")
    out.append("One detector's rows: `echolot report --detector <id> --top 5`; "
               "the window and the device: `--window`; the markers: `--markers`; "
               "any of them as json: `--json`.")
    return "\n".join(out)


def detector_view(report: dict[str, Any], det_id: str, top: int = 5,
                  wide: bool = False) -> str:
    """One detector's rows, the longest first, with the evidence kept short."""
    d = next((x for x in report.get("detectors") or [] if x["id"] == det_id), None)
    if d is None:
        known = ", ".join(x["id"] for x in report.get("detectors") or [])
        return f"no detector `{det_id}` in this report — it has: {known}"
    out = [f"## {d.get('title') or det_id} (`{det_id}`)"]
    if d.get("why"):
        out.append(f"_{d['why']}_")
    out.append(f"params: {d.get('params')}{_source_note(d)}")
    if d.get("error"):
        out.append(f"⚠️ the detector failed: {d['error']}")
    out.append("")
    rows = d.get("rows") or []
    if not rows:
        out.append("_silent — no row cleared the thresholds_")
        return "\n".join(out)
    shown = []
    for r in rows[:top]:
        r = dict(r)
        if not wide:
            r["location"] = clip(r.get("location"), 60)
            # Evidence that ends in what the samples named is kept whole: that
            # end is what a reader of the row came for, and it is short by
            # construction — two names a list, each cut to stacks.LONGEST.
            if r.get("detail") is not None and not r.get("stacks"):
                r["detail"] = clip(r["detail"], 100)
        shown.append(r)
    out.append(_table(shown))
    if len(rows) > top:
        out.append("")
        out.append(f"_Showing {top} of {len(rows)}; `--top {len(rows)}` for all._")
    legend = EVIDENCE_LEGEND.get(det_id)
    if legend:
        out.append("")
        out.append(f"Evidence: {legend}.")
    if any(r.get("places") for r in rows[:top]):
        out.append("")
        out.append("`--json` carries `places`: file and line for every symbol the rows name.")
    if any(r.get("stacks") for r in rows[:top]):
        out.append("")
        out.append("`--json` carries `stacks`: the ten methods that ran most often, and "
                   "the ten nearest frames of the project's own, with their shares.")
    return "\n".join(out)


def markers_view(report: dict[str, Any], top: int = 15) -> str:
    m = report.get("markers") or {}
    rows = m.get("rows") or []
    if not rows and not m.get("absent"):
        return "_no markers: nothing carries the prefix and `domains` lists nothing_"
    out = _markers_lines({**m, "rows": rows[:top]})
    if len(rows) > top:
        out.append(f"_Showing {top} of {len(rows)}; `--top {len(rows)}` for all._")
    return "\n".join(out)


def window_view(report: dict[str, Any]) -> str:
    out = ["## Window", ""]
    out.extend(_header_lines(report))
    w = report.get("window") or {}
    inside = w.get("opened_inside")
    if inside and inside.get("material"):
        out.append(f"⚠️ opened with the main thread already blocked: `{inside['state']}` "
                   f"for {inside['total_ms']} ms, {inside['before_ms']} ms of it before the anchor")
    return "\n".join(out)


def select(report: dict[str, Any], detectors: list[str], top: int | None,
           window: bool, markers: bool) -> dict[str, Any]:
    """The json for a view: only the parts asked for, rows cut to `top`."""
    if not detectors and not window and not markers:
        return {
            "window": report.get("window"),
            "environment": report.get("environment"),
            "summary": report.get("summary"),
            "config": report.get("config"),
            "markers": {"rows": len((report.get("markers") or {}).get("rows") or []),
                        "absent": (report.get("markers") or {}).get("absent") or []},
            "detectors": [{"id": d["id"], "rows": len(d.get("rows") or []),
                           "params_source": d.get("params_source"),
                           "error": d.get("error")}
                          for d in report.get("detectors") or []],
        }
    out: dict[str, Any] = {}
    if window:
        out["window"] = report.get("window")
        out["environment"] = report.get("environment")
    if markers:
        m = dict(report.get("markers") or {})
        if top is not None:
            m["rows"] = (m.get("rows") or [])[:top]
        out["markers"] = m
    if detectors:
        out["detectors"] = []
        for d in report.get("detectors") or []:
            if d["id"] in detectors:
                entry = dict(d)
                if top is not None:
                    entry["rows"] = (d.get("rows") or [])[:top]
                out["detectors"].append(entry)
    return out


def _markers_lines(markers: dict[str, Any]) -> list[str]:
    """The project's own names, measured — above the findings, whatever fired.

    A detector shows a marker only where a threshold says so, and an agent
    that planted one wants the number every time. Absent names are listed
    rather than dropped: a `domains` entry the window never held is a map
    pointing at something this scenario does not run.
    """
    rows = markers.get("rows") or []
    absent = markers.get("absent") or []
    if not rows and not absent:
        return []
    out = ["## Markers"]
    prefix = markers.get("prefix")
    out.append(f"_the names `domains` lists and the `{prefix}` ones; medians per run, "
               f"self time with children subtracted_" if prefix else
               "_the names `domains` lists; medians per run_")
    out.append("")
    if rows:
        out.append(table.render(rows, order=COLUMNS,
                                headers={**HEADERS, "location": "Marker", "detail": "Threads"},
                                skip=HIDDEN))
        out.append("")
    if absent:
        out.append("Not in the window: " + ", ".join(f"`{g}`" for g in absent)
                   + " — listed in `domains`, never seen in this scenario.")
        out.append("")
    return out


def _config_line(report: dict[str, Any]) -> str | None:
    """Which config, and where the thresholds came from — one line.

    A report against calibrated numbers and one against the shipped defaults
    look identical otherwise, and the difference decides whether "silent"
    means clean or means the bar was set above the problem.
    """
    cfg = report.get("config")
    sources = {d.get("params_source", "default") for d in report["detectors"]}
    if not cfg and sources == {"default"}:
        return None
    parts = []
    if cfg and cfg.get("path"):
        parts.append(f"`{cfg['path']}`" + (f" (sha {cfg['sha']})" if cfg.get("sha") else ""))
    if cfg and cfg.get("local"):
        parts.append(f"local `{cfg['local']}`")
    if cfg and cfg.get("defaults"):
        parts.append("thresholds: **built-in defaults** (`--defaults`, the config's "
                     "detectors section ignored)")
    else:
        n_cfg = sum(1 for d in report["detectors"]
                    if "config" in d.get("params_source", ""))
        n_cli = sum(1 for d in report["detectors"]
                    if "cli" in d.get("params_source", ""))
        n_all = len(report["detectors"])
        if n_cfg == 0 and n_cli == 0:
            parts.append("thresholds: built-in defaults")
        else:
            bits = []
            if n_cfg:
                bits.append(f"from the config for {n_cfg} of {n_all} detectors")
            if n_cli:
                bits.append(f"`--set` on {n_cli}")
            parts.append("thresholds: " + ", ".join(bits))
    return "Config: " + " · ".join(parts)


def _source_note(d: dict[str, Any]) -> str:
    src = d.get("params_source", "default")
    if src == "default":
        return ""
    note = f" — from the {src.replace('+', ' + ')}"
    if d.get("defaults"):
        note += f", defaults would be {d['defaults']}"
    return note


def _table(rows: list[dict[str, Any]]) -> str:
    return table.render(rows, order=COLUMNS, headers=HEADERS, skip=HIDDEN)
