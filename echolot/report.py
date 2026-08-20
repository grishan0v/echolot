"""The Marker Report — what the agent sees instead of the trace.

Two formats from one set of data:
  JSON      — the agent's input, stable schema
  Markdown  — for humans and for slides
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from statistics import median
from typing import Any

from . import table

COLUMNS = ["location", "runs", "count", "self_ms", "total_ms", "max_ms",
           "covered_ms", "detail"]
# Keys a row may carry that are not columns. `_table` renders anything it does
# not know as an extra column, which is right for a detector that invents one
# and wrong for bookkeeping the report writes itself.
HIDDEN = {"spread"}
HEADERS = {
    "location": "Where",
    "runs": "Runs",
    "count": "N",
    "self_ms": "Self, ms",
    "total_ms": "Total, ms",
    "max_ms": "Max, ms",
    "covered_ms": "Instrumented, ms",
    "detail": "Evidence",
}

# Columns averaged by median when repeats are merged.
NUMERIC = ["count", "self_ms", "total_ms", "max_ms", "covered_ms"]

# Columns whose per-run values survive the merge, in `spread`. The median alone
# cannot say whether a number is steady: 120 ms from (118, 119, 121) and 120 ms
# from (12, 120, 890) read identically, and only the second one means the next
# run will say something else. Two columns rather than all five, because the
# whole point of the report is that it stays small:
#   the ranking metric — every conclusion is drawn from it, so its stability is
#   what decides whether a conclusion holds;
#   max_ms — where a single slow occurrence shows up at all, and a median over
#   maxima across repeats is exactly what hides one.
SPREAD = ["self_ms", "total_ms", "max_ms"]

# Names that differ only by numbers are one phenomenon: 'Lock contention (owner
# tid: 1234)' and the same with tid 5678, `Choreographer#doFrame 55112` in one
# run and `55120` in the next, worker-2 and worker-5 of one pool.
_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")


def family(name: str) -> str:
    """Collapses names that differ only by numbers.

    Used by `names` to keep an inventory of a real trace from running to
    thousands of rows, and by `compare` as the second matching pass: without
    it a thread pool that handed the work to another worker reads as one row
    vanishing and an unrelated one appearing.
    """
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
) -> dict[str, Any]:
    fired = [r for r in results if r["rows"]]
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trace": trace,
        "toolchain": toolchain or {},
        "window": window,
        "summary": {
            "detectors_run": len(results),
            "detectors_fired": len(fired),
            "fired_ids": [r["id"] for r in fired],
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

    by_id: dict[str, list[dict]] = {}
    for report in reports:
        for det in report["detectors"]:
            by_id.setdefault(det["id"], []).append(det)

    detectors = []
    for det_id, runs in by_id.items():
        head = dict(runs[0])
        identity = identity_of(head)
        # (which repeat, the row) — the repeat's index is what the `runs`
        # column counts, and counting rows instead of repeats is how a
        # detector with two rows per run once printed "6/3".
        groups: dict[tuple, list[tuple[int, dict]]] = {}
        for i, run in enumerate(runs):
            for row in run["rows"]:
                groups.setdefault(tuple(row.get(c) for c in identity),
                                  []).append((i, row))

        rows = []
        for key, seen in groups.items():
            found = [r for _, r in seen]
            row: dict[str, Any] = dict(zip(identity, key))
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
                    spread[col] = {
                        "min": round(min(values), 2),
                        "max": round(max(values), 2),
                        "values": [round(v, 2) for v in values],
                    }
            if spread:
                row["spread"] = spread
            # Evidence comes from the worst repeat: that is where it says most.
            # Unless it is part of what names the row, in which case it is the
            # same in every repeat by construction and already set above.
            worst = max(found, key=_rank)
            if "detail" not in identity and worst.get("detail") is not None:
                row["detail"] = worst["detail"]
            rows.append(row)

        head["rows"] = sorted(rows, key=_rank, reverse=True)
        head["error"] = next((r["error"] for r in runs if r["error"]), None)
        detectors.append(head)

    merged["detectors"] = detectors
    fired = [d for d in detectors if d["rows"]]
    merged["summary"] = {
        "detectors_run": len(detectors),
        "detectors_fired": len(fired),
        "fired_ids": [d["id"] for d in fired],
    }
    return merged


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def to_markdown(report: dict[str, Any]) -> str:
    w = report["window"]
    out: list[str] = []
    out.append("# Marker Report")
    out.append("")
    traces = report.get("traces")
    if traces:
        out.append(f"Runs: **{len(traces)}**, numbers are medians across them")
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

    s = report["summary"]
    out.append(
        f"Detectors fired: **{s['detectors_fired']} of {s['detectors_run']}**"
    )
    line = _config_line(report)
    if line:
        out.append(line)
    out.append("")

    if s["detectors_fired"] == 0:
        out.append("_No detector fired._")
        out.append("")
        out.append(
            "Either the run is clean or the config is wrong — check that the "
            "process name and the scenario anchors match the trace "
            "(`echolot probe`)."
        )
        return "\n".join(out)

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
    if tc.get("trace_processor"):
        note = f"trace_processor {tc['trace_processor']}"
        if tc.get("source") == "--tp-binary":
            note += " (custom binary, pin bypassed)"
        out.append("")
        out.append(f"<sub>{note}</sub>")
    return "\n".join(out)


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
