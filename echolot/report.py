"""The Marker Report — what the agent sees instead of the trace.

Two formats from one set of data:
  JSON      — the agent's input, stable schema
  Markdown  — for humans and for slides

Built and merged across repeats here, section by section; a detector's rows
are merged in merge.py, and both formats are written in render.py.
"""

from __future__ import annotations

import base64
import fnmatch
import gzip
import hashlib
import json
from datetime import datetime, timezone
from statistics import median
from typing import Any

from . import files
from . import launch as launch_mod
from . import needs as needs_mod
from . import recording as recording_mod
from .merge import _FLOORED, _OVER, _merge_debuggable, merge_rows, short_runs

# Importable from here for one release more (#992): `to_markdown` and
# `to_json` live in render.py now, as `merge_rows`, imported above, in merge.py.
from .render import to_json as to_json, to_markdown as to_markdown


def build(
    trace: str,
    window: dict[str, Any],
    results: list[dict[str, Any]],
    toolchain: dict[str, Any] | None = None,
    absent: list[str] | None = None,
    environment: dict[str, Any] | None = None,
    markers: dict[str, Any] | None = None,
    scenario: str | None = None,
    budget_ms: float | None = None,
    recording: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`absent` — shipped detectors this config left out. See `to_markdown`.

    `recording` — how the trace was recorded (recording.py): what it asked
    the device for, and what `collect` kept beside it. `compare` sets the
    two sides' against each other. A report written before it existed has
    none, which `compare` reads as "cannot be checked".

    `scenario` — which of the config's scenarios the window is, so that a
    report says what it measured and `compare` can refuse two of them. A
    report written before it existed has none.

    `budget_ms` — what that scenario is allowed to take, from the config's
    `scenario.budget_ms`, so that a reader of the report alone can set the
    window against it (`plain.verdict`). None when the config sets none; a
    report written before it existed has no such key, which reads the same.

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
        # Shared with the reader, which says when it goes up (`files`).
        "schema": files.REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trace": trace,
        "scenario": scenario,
        "budget_ms": budget_ms,
        "toolchain": toolchain or {},
        "window": window,
        "environment": environment or {},
        "recording": recording,
        "markers": markers or {"prefix": None, "globs": [], "rows": [], "absent": []},
        "summary": {
            "detectors_run": len(results),
            "detectors_fired": len(fired),
            "fired_ids": [r["id"] for r in fired],
            "absent_ids": sorted(absent or []),
        },
        "detectors": results,
    }


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
    # A launch window's stretch before the app's process existed (launch.py).
    late = [b["before_process_ms"] for b in budgets
            if b.get("before_process_ms") is not None]
    if late:
        out["before_process_ms"] = round(median(late), 2)
    covered = [b for b in budgets if b.get("in_rows_pct") is not None]
    if covered:
        out["in_rows_ms"] = round(median([b["in_rows_ms"] for b in covered]), 2)
        out["in_rows_pct"] = round(
            median([b["in_rows_pct"] for b in covered]), 1)
        out["in_rows_uncounted"] = sorted(
            {d for b in covered for d in b.get("in_rows_uncounted") or []})
    out["runs"] = f"{len(budgets)}/{len(reports)}"
    return out



def _merge_crowding(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The app's crowd across repeats: medians, like the budget, and how many repeats had one."""
    found = [r["window"]["crowding"] for r in reports
             if (r.get("window") or {}).get("crowding")]
    if not found:
        return None
    out = {
        "threads": round(median([c["threads"] for c in found])),
        "cores": max(c["cores"] for c in found),
        "peak": round(median([c["peak"] for c in found])),
        "above_cores_ms": round(median([c["above_cores_ms"] for c in found]), 2),
        "runs": f"{len(found)}/{len(reports)}",
    }
    # Other processes' share: the median, like the rest, and the processes
    # by theirs. Copied whole from the repeat where others took most, the
    # names were that one repeat's: an app that led in one repeat of eight,
    # with system_server first in the other seven, was the one the warning
    # sent the reader to close, beside a share that was not part of the
    # median printed with it (#710).
    outside = [c["outside"] for c in found if c.get("outside")]
    if outside:
        worst = max(outside, key=lambda o: o.get("pct") or 0)
        out["outside"] = {
            "pct": round(median([o["pct"] for o in outside]), 1),
            "pct_max": worst.get("pct"),
            "top": _merge_outside_top([o.get("top") or [] for o in outside]),
            "runs": f"{len(outside)}/{len(reports)}",
        }
        # What the processes that draw the app took, kept apart from it (#914).
        platform = [o["platform_pct"] for o in outside if o.get("platform_pct") is not None]
        if platform:
            out["outside"]["platform_pct"] = round(median(platform), 1)
    return out


def _merge_outside_top(tops: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The processes that took the CPUs across repeats, each by the median
    of its share over every repeat: zero where it was not among a repeat's
    largest. Most first, as many as one repeat lists."""
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    shares: dict[tuple[Any, Any], list[float]] = {}
    for at, top in enumerate(tops):
        for p in top:
            key = (p.get("name"), p.get("uid"))
            seen.setdefault(key, dict(p))
            seen[key]["processes"] = max(seen[key].get("processes") or 1,
                                         p.get("processes") or 1)
            shares.setdefault(key, [0.0] * len(tops))[at] = p.get("pct") or 0.0
    merged = [{**seen[k], "pct": round(median(v), 1)} for k, v in shares.items()]
    merged.sort(key=lambda p: (-p["pct"], str(p.get("name"))))
    return merged[:max((len(t) for t in tops), default=0)]


def _merge_frames(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The frames drawn in the window across repeats: the median, and each run's.

    The frames the app made late (`late`) the same way, with each run's in
    `late_values` in the order of `values`, so that a run's late frames are
    read against its own: the share of each run is what `compare --plain`
    judges a frame-paced scenario by (#586). Left out when a repeat does not
    say, rather than counted as none.
    """
    drawn = [f for r in reports
             if (f := (r.get("window") or {}).get("frames")) and f.get("count") is not None]
    if not drawn:
        return None
    counts = [f["count"] for f in drawn]

    def whole(values: list) -> float:
        mid = median(values)
        return int(mid) if mid == int(mid) else round(mid, 1)

    out = {"count": whole(counts), "values": counts, "runs": f"{len(counts)}/{len(reports)}"}
    late = [f.get("late") for f in drawn]
    if all(isinstance(n, int) for n in late):
        out["late"], out["late_values"] = whole(late), late
    unjudged = _merge_unjudged([f.get("unjudged") or [] for f in drawn], whole)
    if unjudged:
        out["unjudged"] = unjudged
    return out


def _merge_unjudged(runs: list[list[dict[str, Any]]], whole) -> list[dict[str, Any]]:
    """The surfaces the timeline does not hold, across the repeats (#1011):
    per surface, the median of what it presented and of what the timeline
    held of it, each repeat's in `values`, none in a repeat that did not list
    it, and in how many it was listed. The thread and the call are the first
    repeat's that listed it: a thread the app did not name is `Thread-18` in
    one start and `Thread-19` in the next."""
    by_surface: dict[str, list[dict[str, Any] | None]] = {}
    for at, listed in enumerate(runs):
        for s in listed:
            by_surface.setdefault(s["surface"], [None] * len(runs))[at] = s
    out = []
    for surface, seen in by_surface.items():
        first = next(s for s in seen if s)
        presents = [s["presents"] if s else 0 for s in seen]
        out.append({"surface": surface, "thread": first.get("thread"), "call": first.get("call"),
                    "presents": whole(presents), "values": presents,
                    "judged": whole([(s.get("judged") or 0) if s else 0 for s in seen]),
                    "runs": f"{sum(1 for s in seen if s)}/{len(runs)}"})
    out.sort(key=lambda s: (-s["presents"], s["surface"]))
    return out


def _merge_startup(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The startup across repeats, reason by reason, by median, like the budget.

    A reason one repeat did not have counts as nothing in it. The type is the
    one every repeat agrees on, or all of them named: a set that mixes cold
    starts with warm ones is two measurements, and a median over both is
    neither.

    Each repeat's own numbers stay beside the medians, in the order of
    `traces`, so that `compare` can ask of the startup what it asks of a
    row (#532): `values` its length in each repeat that had one, and
    `reason_values` each reason's, a reason the repeat did not have as 0.
    """
    found = [r["window"]["startup"] for r in reports
             if (r.get("window") or {}).get("startup")]
    if not found:
        return None
    names = sorted({name for s in found for name in s.get("reasons") or {}})
    each = {name: [(s.get("reasons") or {}).get(name, 0.0) for s in found] for name in names}
    reasons = {name: round(median(values), 2) for name, values in each.items()}
    types = sorted({s.get("type") or "unknown" for s in found})
    order = sorted(reasons, key=lambda name: (-reasons[name], name))
    out: dict[str, Any] = {
        "type": types[0] if len(types) == 1 else " and ".join(types),
        "dur_ms": round(median([s["dur_ms"] for s in found]), 2),
        "values": [s["dur_ms"] for s in found],
        "reasons": {name: reasons[name] for name in order},
        "reason_values": {name: each[name] for name in order},
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
            # Out of the repeats that recorded it: one repeat with a
            # temperature among five read `0/5`, as if all five were checked.
            "throttled_runs": f"{len(throttled)}/{len(thermals)}",
            "runs": f"{len(thermals)}/{len(reports)}",
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
    out["cooldown"] = _merge_cooldown([e.get("cooldown") for e in envs])
    out["sampling"] = _merge_sampling([e.get("sampling") for e in envs])
    out["compose_names"] = _merge_compose_names([e.get("compose_names") for e in envs])
    out["compile"] = _merge_compile([e.get("compile") for e in envs])
    out["debuggable"] = _merge_debuggable([e.get("debuggable") for e in envs])
    out["missing"] = sorted(k for k in ("cpu", "thermal", "memory")
                            if out[k] is None)
    return out


def _merge_cooldown(blocks: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """`runner.cooldown` across the repeats, as `collect` kept it beside the set.

    `{"on": True, "max_c", "sensor", "before_c_max"}` when every repeat waited
    for it, `{"on": False}` when collect recorded every one with it off, and
    None when any repeat has no record or the repeats differ: then the report
    cannot say how the set was cooled (#522).
    """
    if not blocks or any(not isinstance(b, dict) for b in blocks):
        return None
    on = [b for b in blocks if b.get("on")]
    if not on:
        return {"on": False}
    if len(on) < len(blocks):
        return None
    limits = [b["max_c"] for b in on if isinstance(b.get("max_c"), (int, float))]
    before = [b["before_c"] for b in on if isinstance(b.get("before_c"), (int, float))]
    return {"on": True, "max_c": max(limits) if limits else None,
            "sensor": on[0].get("sensor"),
            "before_c_max": max(before) if before else None}


def _merge_compile(blocks: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """The compile state across repeats: the one every repeat agrees on, or all of them.

    Read like a startup's type. A set that mixes an app compiled from its
    profile with one nobody compiled is two measurements, and a median over
    both is neither, so the values it mixes are named together and the
    header and `compare` both say so. `runs` counts the repeats whose trace
    held the state at all. The JIT and the verification take medians, like
    every other number here.
    """
    seen = [b for b in blocks if b]
    if not seen:
        return None
    known = [b for b in seen if b.get("filter")]
    # In the order of the filters, so that two builds read pair by pair:
    # `run-from-apk and speed-profile`, `unknown and install-dm`.
    states = sorted({(b["filter"], b.get("reason") or "", b.get("status") or "")
                     for b in known})

    def agreed(at: int) -> str | None:
        values = list(dict.fromkeys(st[at] for st in states if st[at]))
        return " and ".join(values) if values else None

    def mid(block: str, key: str, digits: int = 2) -> float:
        return round(median([(b.get(block) or {}).get(key) or 0 for b in seen]), digits)

    return {
        "filter": agreed(0),
        "reason": agreed(1),
        "status": agreed(2),
        "jit": {"methods": round(mid("jit", "methods")), "ms": mid("jit", "ms")},
        "verified": {"classes": round(mid("verified", "classes")),
                     "ms": mid("verified", "ms"),
                     "main_ms": mid("verified", "main_ms")},
        "runs": f"{len(known)}/{len(blocks)}",
    }


def _merge_compose_names(blocks: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Composable names across repeats: the median calls of the runs that had
    any, and how many runs that was — a set half recorded with names has the
    SDK's cost in its spread, as a half-sampled one has the sampler's."""
    seen = [b for b in blocks if b]
    if not seen:
        return None
    named = [b for b in seen if b.get("calls")]
    return {
        "asked": any(b.get("asked") for b in seen),
        "calls": round(median([b["calls"] for b in named])) if named else 0,
        "runs": f"{len(named)}/{len(blocks)}",
    }


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
    #
    # The whole block from the repeat with the largest minified share, which
    # is what the warning tests. Each key's maximum on its own came from
    # different repeats and made a block no repeat had: three minified of
    # eight after one repeat's mapping renamed eight, while the repeat that
    # had three minified renamed five.
    names = [s["names"] for s in ran if s.get("names")]
    if names:
        merged["names"] = dict(max(names, key=lambda n: (
            (n.get("minified") or 0) / (n.get("methods") or 1), n.get("minified") or 0)))
    return merged


# Where `analyze` hands the merge the rows a detector with `@fires` measured
# and its predicate did not pass (#832). Never written to a report either:
# the merge reads them with the rows shown, as one measurement, and writes
# what fired in no repeat to `below`.
UNDER_ROWS = "under_rows"
# Under this much of its metric a row a detector measured and did not show
# is not kept: absent means "under it in every repeat", the same on both
# sides of a comparison. Measured on ten real launch traces with every
# threshold open: 1,466 rows a trace with no floor, 136 at 1 ms (20 KB of
# JSON), 66 at 5 ms; a report showed about 18.
FLOOR_MS = 1.0



def _merge_detector(runs: list[dict[str, Any]], identity: tuple[str, ...],
                    total: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """One detector's rows over its repeats, and its `below`.

    A detector with `@fires` handed every row it measured, shown or not
    (`UNDER_ROWS`): they are merged as one measurement, and a row is shown
    where it fired in some repeat (#832). What fired in none is its `below`,
    every row of it at least `FLOOR_MS`, for `compare` to look a row up in
    that the other side shows (#715). A detector without `@fires`, one a
    project wrote itself, shows every row its query returns and has none.
    """
    if any(UNDER_ROWS in run for run in runs):
        per_run = [[{**r, _OVER: True} for r in run.get("rows") or []]
                   + [{**r, _OVER: False} for r in run.get(UNDER_ROWS) or []]
                   for run in runs]
        shown: list[dict[str, Any]] = []
        under: list[dict[str, Any]] = []
        for row in merge_rows(per_run, identity, total):
            (shown if row.pop(_OVER, False) else under).append(row)
        return shown, {"floor_ms": FLOOR_MS, "rows": under}
    return merge_rows([run.get("rows") or [] for run in runs], identity, total), None


# What an open marker's number is raised to, to see whether a merged number
# depends on it. Larger than any duration a trace can hold.
_UNBOUNDED = 1e12


def _merged_floors(sections: list[dict[str, Any]], rows: list[dict[str, Any]],
                   total: int) -> None:
    """Which merged numbers of a marker are floors, from the repeats it was open in.

    A marker that never closed in one repeat ran there to the end of the
    window, so that repeat's number is a floor: the real one is that or more.
    Whether the merged number is a floor too depends on where it sits. With
    14 closed runs around 1.7 s and one open at 6.1 s, the median is the
    same whatever the open run really took, and printing it as `≥` called a
    measurement a bound (#513). So the merge is done again with every open
    run's numbers raised as far as they go: a merged number that moves is a
    floor, one that does not is exact.

    `unfinished_runs` counts the repeats it was open in; `unfinished` and
    `floors` say which numbers are floors, for the `≥` in the table.
    """
    opened: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        for row in section.get("rows") or []:
            if row.get("unfinished"):
                opened.setdefault(row["location"], []).append(row)
    if not opened:
        return
    raised = [[{**r, **{c: _UNBOUNDED for c in _FLOORED if r.get(c) is not None}}
               if r.get("unfinished") else r for r in section.get("rows") or []]
              for section in sections]
    upper = {r["location"]: r for r in merge_rows(raised, ("location",), total)}
    for row in rows:
        runs = opened.get(row["location"])
        if not runs:
            continue
        row["unfinished_runs"] = f"{len(runs)}/{total}"
        row["open_ms"] = [r.get("total_ms") for r in runs]
        floors = [c for c in _FLOORED if row.get(c) is not None
                  and upper[row["location"]].get(c) != row[c]]
        if floors:
            row["unfinished"] = True
            row["floors"] = floors


def _merge_markers(reports: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """The markers table across repeats: the same fold as a detector's rows.

    A name the config lists is reported absent only when no repeat held it;
    one that came and went is a row with a `runs` column short of the total,
    which says the same thing more precisely.
    """
    sections = [r.get("markers") or {} for r in reports]
    head = next((s for s in sections if s), {})
    seen = {row["location"] for s in sections for row in s.get("rows") or []}
    rows = merge_rows([s.get("rows") or [] for s in sections], ("location",), total,
                      counts=True)
    _merged_floors(sections, rows, total)
    # Where each temporary marker sat, by the median of the repeats it was
    # found in, as every number of its row is.
    for row in rows:
        per_run = [r.get("parents") or {} for s in sections for r in s.get("rows") or []
                   if r["location"] == row["location"]]
        names = sorted({name for p in per_run for name in p})
        if names:
            row["parents"] = dict(sorted(
                ((name, round(median([p.get(name, 0.0) for p in per_run]), 2))
                 for name in names), key=lambda kv: (-kv[1], kv[0])))
    merged = {
        "prefix": head.get("prefix"),
        "globs": head.get("globs") or [],
        "rows": rows,
        "absent": [g for g in head.get("globs") or []
                   if g in set(head.get("absent") or []) and not any(
                       fnmatch.fnmatchcase(n, g) for n in seen)],
    }
    # The prefixed names any repeat held, the window or not. Only when every
    # repeat says: one read back from before the key would count as holding
    # none, and the markers in the code as missing from it.
    if sections and all("in_trace" in s for s in sections):
        merged["in_trace"] = sorted({n for s in sections for n in s["in_trace"]})
    # Temporary markers in some repeats and not others: the set is two rounds,
    # one with them and one without, merged as repeats (#584). The slices they
    # sit in had their self time measured two ways, and a median over both is
    # neither.
    prefix = head.get("prefix")
    mixed = [{"location": row["location"], "runs": row["runs"],
              "parents": list(row.get("parents") or {})}
             for row in rows
             if prefix and str(row["location"]).startswith(prefix)
             and short_runs(row.get("runs"))]
    if mixed:
        merged["mixed"] = mixed
    return merged


# The slice names of the window, for `compare` to set against another set's
# (#610). `analyze_trace` hands the merge every name of each repeat under
# NAMES_ALL; the merge keeps SLICE_NAMES and takes the rest out.
NAMES_ALL = "names_all"
SLICE_NAMES = "slice_names"
# The least a name takes a run, the median over the runs it is in, to be
# kept with its numbers. A JIT compilation of one method takes a few ms, and
# it is there or not whatever it took.
NAMES_FLOOR_MS = 1.0
# How a name is held in `seen`: the first four bytes of its SHA-1.
NAMES_HASH = "sha1:4"


def name_digest(name: str) -> bytes:
    """A name as `seen` holds it, see `_merge_names`."""
    return hashlib.sha1(name.encode("utf-8")).digest()[:4]


def names_seen(section: dict[str, Any]) -> set[bytes] | None:
    """Every name a `slice_names` section saw, as `name_digest` gives it;
    None for a section that holds them some other way."""
    if section.get("hash") != NAMES_HASH:
        return None
    raw = base64.b64decode(section.get("seen") or "")
    return {raw[i:i + 4] for i in range(0, len(raw), 4)}


def names_rows(section: dict[str, Any]) -> list[dict[str, Any]]:
    """The names a `slice_names` section kept with their numbers."""
    packed = section.get("rows_gz")
    if not packed:
        return []
    return json.loads(gzip.decompress(base64.b64decode(packed)))


def _merge_names(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The slice names of the window across the repeats, for `compare` (#610).

    A slice no detector reads never reaches a comparison, however clearly it
    tells the two builds apart, and a name that is in every run of one side
    and in no run of the other is that: a method JIT-compiled only after it
    became hot, a section, a binder call or a thread that only one build
    has. Two sets of `echolot names` set against each other by hand in a
    script found such a method behind a busy loop no row showed.

    Two things are kept, and both are needed: `compare` reads one side's
    names against the other's runs.

      rows_gz  the names in all runs but one or more of this set, at least
               NAMES_FLOOR_MS a run by the median of the runs it is in: what
               the comparison can list, with its numbers (`names_rows`).
               All but one, not all: how many runs `compare` asks for is its
               own rule.
      seen     every name any run held, whatever it took, as the first four
               bytes of its SHA-1, sorted (`names_seen`): what the other
               side's names are looked up in. With a floor here, a name both
               sides have would read as one side's wherever the other ran it
               under the floor. A name not in `seen` was in no run; two
               names share four bytes about once in four billion, and then
               a name is missed, never made up.

    Both packed in base64, the rows as gzipped JSON. Ten cold starts of a
    busy app held 17,753 names in the window and kept 946 of them, and as
    JSON those rows alone were 228 KB beside the 285 KB of the rest of a
    report that exists to stay small. Packed, rows and `seen` are 133 KB,
    and `compare` is their reader; what one trace holds is what `echolot
    names` prints.

    Names are folded by `names.slice_across_builds`. None when a repeat has no names: a
    report written before the section existed, or one whose query failed.
    The other side then cannot be read against it, which is not "none".
    """
    found_by_run = [r.pop(NAMES_ALL, None) for r in reports]
    if not found_by_run or any(f is None for f in found_by_run):
        return None
    total = len(found_by_run)
    least = max(1, total - 1)
    union: set[str] = set().union(*found_by_run)
    rows = []
    for name in union:
        found = [run[name] for run in found_by_run if name in run]
        if len(found) < least:
            continue
        ms = round(median(f["total_ms"] for f in found), 2)
        if ms < NAMES_FLOOR_MS:
            continue
        threads = sorted({t for f in found for t in f["threads"]})
        rows.append({
            "name": name,
            "runs": f"{len(found)}/{total}",
            "total_ms": ms,
            "max_ms": max(f["max_ms"] for f in found),
            "threads": ", ".join(threads[:3]) + (
                f" +{len(threads) - 3}" if len(threads) > 3 else ""),
        })
    rows.sort(key=lambda r: (-r["total_ms"], r["name"]))
    # No time in the gzip header: the same names make the same report.
    packed = gzip.compress(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
                           .encode("utf-8"), compresslevel=9, mtime=0)
    return {
        "runs": total,
        "floor_ms": NAMES_FLOOR_MS,
        "names": len(union),
        "kept": len(rows),
        "rows_gz": base64.b64encode(packed).decode("ascii"),
        "hash": NAMES_HASH,
        "seen": base64.b64encode(b"".join(sorted(name_digest(n) for n in union))).decode("ascii"),
    }


def _merge_drawn(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The frames the app drew, across the repeats: the median, and in how many
    of the repeats that could tell it drew none (#536)."""
    counts = [d["count"] for d in ((r.get("window") or {}).get("drawn") for r in reports)
              if isinstance(d, dict) and isinstance(d.get("count"), int)]
    if not counts:
        return None
    return {"count": round(median(counts)), "none": sum(1 for c in counts if c == 0),
            "runs": len(counts)}


def _merge_boot_receivers(reports: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The app's `BOOT_COMPLETED` receivers across the repeats (#910): per
    receiver, its median time over the repeats that ran it, the least and the
    most, and in how many it ran."""
    total = len(reports)
    by_name: dict[str, list[float]] = {}
    for r in reports:
        for row in (r.get("window") or {}).get("boot_receivers") or []:
            by_name.setdefault(row["name"], []).append(row["ms"])
    return [{"name": name, "ms": round(median(ms), 2), "ms_min": min(ms), "ms_max": max(ms),
             "runs": f"{len(ms)}/{total}"}
            for name, ms in sorted(by_name.items())] or None


def _merge_window_checks(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """What the window says about how it was cut, for every repeat at once.

    These fields are checks rather than measurements, and a check holds for
    the set only if it held in each repeat. Copied from the first repeat, a
    start anchor that missed in the third read `matches: 1`: that repeat's
    window ran from the process's first slice, its rows were in the medians, and the warning
    that has to be shouted never was. So the worst case speaks: the fewest
    matches per anchor, with `missed` counting the repeats where it found
    nothing; `opened_inside` from a repeat where it is material; and the
    process names, when the repeats measured more than one.
    """
    total = len(reports)
    windows = [r.get("window") or {} for r in reports]
    out: dict[str, Any] = {}
    for key in ("start_anchor", "end_anchor"):
        anchors = [w.get(key) for w in windows if w.get(key)]
        if not anchors:
            continue
        worst = dict(min(anchors, key=lambda a: a.get("matches") or 0))
        missed = sum(1 for a in anchors if not a.get("matches"))
        if missed:
            worst["missed"] = f"{missed}/{total}"
        # An end that never closed in any repeat ran that window to the end
        # of its trace, whichever repeat matched least.
        if any(a.get("unfinished") for a in anchors):
            worst["unfinished"] = True
        out[key] = worst
    material = [w["opened_inside"] for w in windows
                if (w.get("opened_inside") or {}).get("material")]
    if material:
        out["opened_inside"] = dict(max(material, key=lambda o: o.get("before_ms") or 0),
                                    runs=f"{len(material)}/{total}")
    out.update(launch_mod.merged(windows, total))
    names = sorted({w["process"] for w in windows if w.get("process")})
    if len(names) > 1:
        out["processes"] = names
    alternatives = next((w for w in windows if w.get("process_alternatives")), None)
    if alternatives:
        out["process_alternatives"] = alternatives["process_alternatives"]
        out["process_alternatives_total"] = alternatives.get("process_alternatives_total")
    return out


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The merged report (see `_merge_runs`), with its plain reading in it."""
    from . import plain  # plain takes `metric_of` from this module
    return plain.annotate(_merge_runs(reports))


def _merge_runs(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Merges N repeats into a single report using the median.

    Median rather than mean: one outlier must not drag the conclusion along.
    And not the maximum either: then any random hiccup becomes a "finding".

    The `runs` column is the whole point of repeating. "3/3" means reproducible,
    "1/3" means you happened to catch it once, and those are different news even
    when the milliseconds match.
    """
    names = _merge_names(reports)
    if len(reports) == 1:
        for det in reports[0].get("detectors") or []:
            # Through the merge, as the rows of a set go: one recording and a
            # set of copies of it say the same of every row (#831). Where the
            # one report used a path of its own, a pool's `threads` and the
            # rows under the bar came out otherwise (#714, #845). It has no
            # repeats to count or spread, so its rows keep neither.
            rows, below = _merge_detector([det], identity_of(det), 1)
            det.pop(UNDER_ROWS, None)
            det["rows"] = [{k: v for k, v in row.items() if k not in ("runs", "spread")}
                           for row in rows]
            if below is not None and not det.get("error"):
                det["below"] = below
        if names is not None:
            reports[0][SLICE_NAMES] = names
        return reports[0]

    total = len(reports)
    merged = dict(reports[0])
    merged["traces"] = [r["trace"] for r in reports]
    # When each began, beside `traces` and in their order; the first's alone
    # would read as the set's (#848).
    merged.pop("recorded_at", None)
    if any(r.get("recorded_at") for r in reports):
        merged["recorded_at"] = [r.get("recorded_at") for r in reports]
    merged["runs"] = total
    # A repeat's own section, when the repeats are reports read back, is of
    # one run and not of the set.
    merged.pop(SLICE_NAMES, None)
    if names is not None:
        merged[SLICE_NAMES] = names

    windows = [r["window"].get("duration_ms") for r in reports
               if r["window"].get("duration_ms") is not None]
    if windows:
        merged["window"] = dict(reports[0]["window"])
        merged["window"]["duration_ms"] = round(median(windows), 2)
        merged["window"]["duration_ms_min"] = min(windows)
        merged["window"]["duration_ms_max"] = max(windows)
        # Every repeat's window, in the order of `traces`: what `compare`
        # tests the window's move with, as it tests a row's (#532). The
        # median, shortest and longest alone could not say whether two sets
        # differ by more than their own runs do.
        merged["window"]["values"] = windows
        merged["window"]["main_thread"] = _merge_budget(reports)
        merged["window"]["crowding"] = _merge_crowding(reports)
        merged["window"]["frames"] = _merge_frames(reports)
        merged["window"]["drawn"] = _merge_drawn(reports)
        merged["window"]["boot_receivers"] = _merge_boot_receivers(reports)
        merged["window"]["startup"] = _merge_startup(reports)
        merged["window"].update(_merge_window_checks(reports))

    merged["environment"] = _merge_environment(reports)
    merged["recording"] = recording_mod.merge([r.get("recording") for r in reports])

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
        # Only the repeats it ran in, and their count for `runs`. Counted
        # against every repeat, a row found each time the detector ran read
        # `1/3` when it had failed in the other two, and `1/3` means caught
        # once. How many failed is said beside the rows instead.
        ran = [run for run in runs if not run["error"]]
        head["rows"], below = (_merge_detector(ran, identity_of(head), len(ran))
                               if ran else ([], None))
        head.pop(UNDER_ROWS, None)
        if below is not None:
            head["below"] = below
        else:
            head.pop("below", None)
        head["error"] = next((r["error"] for r in runs if r["error"]), None)
        if head["error"]:
            head["failed_runs"] = f"{len(runs) - len(ran)}/{len(runs)}"
        # Did it look: checked only where every repeat held what it reads.
        head.pop("checked", None)
        head.pop("reason", None)
        head.update(needs_mod.merged(runs))
        detectors.append(head)

    merged["detectors"] = detectors
    # Whether the runs were alike, read off the rows just merged (#508).
    from . import drift
    merged["drift"] = drift.of(merged)
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


def select(report: dict[str, Any], detectors: list[str], top: int | None,
           window: bool, markers: bool) -> dict[str, Any]:
    """The json for a view: only the parts asked for, rows cut to `top`."""
    if not detectors and not window and not markers:
        m = report.get("markers") or {}
        return {
            "window": report.get("window"),
            "environment": report.get("environment"),
            "summary": report.get("summary"),
            "config": report.get("config"),
            "markers": {"rows": len(m.get("rows") or []), "absent": m.get("absent") or [],
                        **{k: m[k] for k in ("unbuilt", "removed") if m.get(k)}},
            "detectors": [{"id": d["id"], "rows": len(d.get("rows") or []),
                           "params_source": d.get("params_source"),
                           "error": d.get("error"),
                           "checked": d.get("checked")}
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


