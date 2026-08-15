"""The Marker Report — what the agent sees instead of the trace.

Two formats from one set of data:
  JSON      — the agent's input, stable schema
  Markdown  — for humans and for slides
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median
from typing import Any

COLUMNS = ["location", "runs", "count", "self_ms", "total_ms", "max_ms",
           "covered_ms", "detail"]
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
    value = row.get("self_ms")
    if value is None:
        value = row.get("total_ms")
    return value or 0.0


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
        groups: dict[str, list[dict]] = {}
        for run in runs:
            for row in run["rows"]:
                groups.setdefault(row["location"], []).append(row)

        rows = []
        for location, found in groups.items():
            row = {"location": location, "runs": f"{len(found)}/{total}"}
            for col in NUMERIC:
                values = [f[col] for f in found
                          if f.get(col) is not None]
                if values:
                    row[col] = round(median(values), 2)
            # Evidence comes from the worst repeat: that is where it says most.
            worst = max(found, key=_rank)
            if worst.get("detail") is not None:
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
        out.append(f"<sub>detector `{d['id']}`, params: {d['params']}</sub>")
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


def _table(rows: list[dict[str, Any]]) -> str:
    cols = [c for c in COLUMNS if any(c in r for r in rows)]
    extra = [k for k in rows[0] if k not in cols]
    cols += extra
    head = "| " + " | ".join(HEADERS.get(c, c) for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = [
        "| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |"
        for r in rows
    ]
    return "\n".join([head, sep, *body])


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    return str(v).replace("|", "\\|")
