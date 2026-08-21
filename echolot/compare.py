"""`echolot compare` — the delta between two Marker Reports.

The tool exists for the question "it was 3 s, now it is 7 s, where did that
go". Everything up to here answers one half of it: `analyze` says where the
time went in one set of traces. Comparing two sets was left to whoever read
both reports — which for an agent means holding two twenty-row tables in the
window and subtracting them by eye, in exactly the place this tool exists to
take work out of.

The output is one table, sorted by how much each row moved. The top row is
usually the answer.

Two things it refuses to do quietly:

  * compare across a moved bar. Thresholds decide which rows appear at all, so
    "appeared" against different thresholds means "the bar dropped below it",
    which is a different sentence. Reports built with different detector
    parameters say so at the top and every appeared/vanished row is suspect.

  * call a difference real when the repeats do not support it. A row whose
    before and after ranges overlap moved less than the runs disagree among
    themselves; the report says `overlap` and leaves the conclusion alone.
    That needs `spread` in the input, which `analyze` writes for two repeats
    or more — with a single trace on either side the column reads `—`.

Rows are paired on everything the detector declared in `@identity`, which the
report carries. Half the shipped detectors name a second column there, and one
location legitimately holding several rows is what that field exists to say;
see `_match` for what pairing on the name alone produced instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import table
from .report import family, identity_of, metric_of

# What counts as movement worth a row. Absolute floor first, so a 4 ms wobble
# on a 6 ms slice is not "×1.7 slower"; relative floor second, so a 40 ms move
# on a 900 ms slice is not a finding either.
FLOOR_MS = 5.0
FLOOR_RATIO = 0.10

APPEARED, VANISHED, GREW, SHRANK, STEADY = (
    "appeared", "vanished", "grew", "shrank", "steady")


def build(before: dict[str, Any], after: dict[str, Any], *,
          before_path: str, after_path: str,
          floor_ms: float = FLOOR_MS,
          floor_ratio: float = FLOOR_RATIO,
          temp_prefix: str | None = None) -> dict[str, Any]:
    warnings, comparable = _check(before, after)

    rows: list[dict[str, Any]] = []
    state_changed: list[dict[str, Any]] = []

    by_id_before = {d["id"]: d for d in before.get("detectors", [])}
    by_id_after = {d["id"]: d for d in after.get("detectors", [])}

    for det_id in [*by_id_before, *(k for k in by_id_after if k not in by_id_before)]:
        db = by_id_before.get(det_id)
        da = by_id_after.get(det_id)
        rows_b = (db or {}).get("rows") or []
        rows_a = (da or {}).get("rows") or []

        if db is not None and da is not None and bool(rows_b) != bool(rows_a):
            state_changed.append({
                "id": det_id,
                "before": _state(rows_b),
                "after": _state(rows_a),
            })

        identity = _identity(db, da)
        for rb, ra, how in _match(rows_b, rows_a, identity):
            rows.append(
                _row(det_id, identity, rb, ra, how, floor_ms, floor_ratio))

    rows.sort(key=lambda r: abs(r["delta_ms"] or 0.0), reverse=True)

    planted = _planted(rows, temp_prefix)
    if planted:
        warnings.append({
            "id": "instrumentation",
            "text": f"{len(planted)} row(s) that appeared are markers added "
                    f"between the two rounds, not new work: "
                    + ", ".join(f"`{name}`" for name in planted[:5])
                    + (" …" if len(planted) > 5 else "")
                    + ". They show where the time inside a blind spot went, "
                      "which is what they were added for — read them as a "
                      "breakdown of what was already there.",
        })

    counts = {k: 0 for k in (APPEARED, VANISHED, GREW, SHRANK, STEADY)}
    for r in rows:
        counts[r["change"]] += 1

    return {
        "schema": 1,
        "kind": "comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "comparable": comparable,
        "warnings": warnings,
        "before": _side(before, before_path),
        "after": _side(after, after_path),
        "window": _window(before, after),
        "noise_floor": {"abs_ms": floor_ms, "ratio": floor_ratio},
        "summary": {
            "moved": counts[GREW] + counts[SHRANK],
            "appeared": counts[APPEARED],
            "vanished": counts[VANISHED],
            "steady": counts[STEADY],
            "fired_before": (before.get("summary") or {}).get("fired_ids") or [],
            "fired_after": (after.get("summary") or {}).get("fired_ids") or [],
            "state_changed": state_changed,
        },
        "rows": rows,
    }


def _planted(rows: list[dict], temp_prefix: str | None) -> list[str]:
    """Rows that appeared because the hunt instrumented them, not because of a change.

    Inside the loop, a round is compared against the one before it — and
    between the two the agent added `AGENTTMP_` markers on purpose. Those
    arrive as large new rows, which is exactly the shape a regression has. The
    difference is that they were planted, and only the config knows the prefix
    they were planted with.
    """
    if not temp_prefix:
        return []
    return [r["location"] for r in rows
            if r["change"] == APPEARED
            and str(r["location"]).startswith(temp_prefix)]


# --- what may be compared with what ----------------------------------------

def _check(before: dict, after: dict) -> tuple[list[dict[str, str]], bool]:
    """Everything that makes two reports say less about each other than it looks.

    A comparison is arithmetic and will happily subtract numbers about two
    different processes. Nothing here stops the subtraction — the numbers stay
    on the page, because a wrong-looking table is more useful than a refusal.
    What it does is put the reason at the top, where the existing report
    already puts an anchor that never matched.
    """
    out: list[dict[str, str]] = []
    comparable = True

    wb, wa = before.get("window") or {}, after.get("window") or {}
    if wb.get("process") and wa.get("process") and wb["process"] != wa["process"]:
        out.append({
            "id": "process",
            "text": f"Different processes: `{wb['process']}` before, "
                    f"`{wa['process']}` after. These are two apps, and nothing "
                    f"below is a comparison.",
        })
        comparable = False

    for side, w in (("before", wb), ("after", wa)):
        anchor = w.get("start_anchor") or {}
        if anchor.get("matches") == 0:
            out.append({
                "id": f"anchor-{side}",
                "text": f"The {side} report's start anchor `{anchor.get('glob')}` "
                        f"never matched — its window is the whole trace, not the "
                        f"scenario. Fix the config and analyze again.",
            })

    cb, ca = before.get("config") or {}, after.get("config") or {}
    if cb.get("sha") and ca.get("sha") and cb["sha"] != ca["sha"]:
        out.append({
            "id": "config",
            "text": f"The config changed between the two: sha `{cb['sha']}` → "
                    f"`{ca['sha']}`. Anchors, the process mask and thresholds "
                    f"all live there.",
        })
    if bool(cb.get("defaults")) != bool(ca.get("defaults")):
        out.append({
            "id": "defaults",
            "text": "One side ran with `--defaults` and the other did not, so "
                    "the two used different thresholds throughout.",
        })

    moved = _params_moved(before, after)
    if moved:
        out.append({
            "id": "thresholds",
            "text": "Thresholds differ, so **appeared** and **gone** below say "
                    "nothing — the bar moved, and a row can cross it without "
                    "anything in the app changing: "
                    + "; ".join(moved)
                    + ". Re-run both sides with `--defaults` to compare like "
                      "with like.",
        })

    nb, na = _runs(before), _runs(after)
    if nb != na:
        out.append({
            "id": "runs",
            "text": f"Different numbers of repeats — {nb} before, {na} after. "
                    f"Medians stay comparable; the ranges are built from "
                    f"unequal samples and the narrower side is the smaller one.",
        })
    if nb == 1 or na == 1:
        out.append({
            "id": "single",
            "text": "A single trace on one side or both: there is no spread to "
                    "check a difference against, and the Ranges column stays "
                    "empty. One run cannot tell a change from a hiccup.",
        })

    only_before = {d["id"] for d in before.get("detectors", [])} - \
                  {d["id"] for d in after.get("detectors", [])}
    only_after = {d["id"] for d in after.get("detectors", [])} - \
                 {d["id"] for d in before.get("detectors", [])}
    if only_before or only_after:
        bits = []
        if only_before:
            bits.append("only before: " + ", ".join(sorted(only_before)))
        if only_after:
            bits.append("only after: " + ", ".join(sorted(only_after)))
        out.append({
            "id": "detectors",
            "text": "The two runs did not use the same set of detectors (" +
                    "; ".join(bits) + "). Rows from a detector that ran once "
                    "are listed as appeared or gone for that reason alone.",
        })
    return out, comparable


def _params_moved(before: dict, after: dict) -> list[str]:
    """Detector parameters that differ, named one by one.

    Not a boolean: "thresholds differ" sends someone to diff two JSON files,
    while `main_thread_block.min_slice_ms 16 → 3089.5` explains the whole
    report on its own.
    """
    pb = {d["id"]: (d.get("params") or {}) for d in before.get("detectors", [])}
    pa = {d["id"]: (d.get("params") or {}) for d in after.get("detectors", [])}
    moved = []
    for det_id in sorted(set(pb) & set(pa)):
        for key in sorted(set(pb[det_id]) | set(pa[det_id])):
            was, now = pb[det_id].get(key), pa[det_id].get(key)
            if was != now:
                moved.append(f"`{det_id}.{key}` {was} → {now}")
    return moved


def _runs(report: dict) -> int:
    return int(report.get("runs") or len(report.get("traces") or []) or 1)


def _side(report: dict, path: str) -> dict[str, Any]:
    cfg = report.get("config") or {}
    return {
        "path": path,
        "runs": _runs(report),
        "generated_at": report.get("generated_at"),
        "config_sha": cfg.get("sha"),
        "defaults": bool(cfg.get("defaults")),
    }


def _window(before: dict, after: dict) -> dict[str, Any]:
    wb, wa = before.get("window") or {}, after.get("window") or {}
    b, a = wb.get("duration_ms"), wa.get("duration_ms")
    out: dict[str, Any] = {"before_ms": b, "after_ms": a,
                           "delta_ms": None, "ratio": None}
    if b is not None and a is not None:
        out["delta_ms"] = round(a - b, 2)
        if b:
            out["ratio"] = round(a / b, 2)
    return out


def _state(rows: list[dict]) -> str:
    return "silent" if not rows else f"{len(rows)} row(s)"


# --- pairing rows across two reports ---------------------------------------

def _identity(db: dict | None, da: dict | None) -> tuple[str, ...]:
    """The columns that tell one row of this detector from another.

    Written into every report by `analyze`, from the detector's `@identity`
    header, and read back here rather than assumed — the same field, for the
    same reason, that `report.aggregate` reads when it merges repeats.

    Only what BOTH reports carry can be matched on. A report written before
    the field existed says `location` alone, and it means it: that report
    genuinely cannot tell two of its own rows apart, and comparing it against
    a newer one on a column it never had would be reading a distinction into
    it that is not there.
    """
    before = identity_of(db) if db is not None else ()
    after = identity_of(da) if da is not None else ()
    if not before or not after:
        return before or after or ("location",)
    return tuple(c for c in before if c in after) or ("location",)


def _key(row: dict, identity: tuple[str, ...]) -> tuple:
    return tuple(row.get(c) for c in identity)


def _family_key(row: dict, identity: tuple[str, ...]) -> tuple:
    return tuple(None if v is None else family(str(v))
                 for v in _key(row, identity))


def _match(before_rows: list[dict], after_rows: list[dict],
           identity: tuple[str, ...]
           ) -> list[tuple[dict | None, dict | None, str]]:
    """Pairs rows of one detector, exactly first and by name family second.

    A row is named by every column in `identity`, never by `location` alone.
    Five of the ten shipped detectors group by a second column —
    `runnable_starvation` by the thread's state, `binder_txn` by whether the
    thread is the main one, `anr` by which record it is — so one location
    carrying several rows is ordinary rather than a corner.

    Keyed on the location alone those rows collapse onto whichever came last,
    and the ones left over pair with it. On a real shape — one thread with
    `state R` at 100 ms and `state R+` at 30 ms before, 105 and 400 after —
    the report subtracted `R` before from `R+` after and called the 300 ms a
    regression, while the real `R` figure never appeared at all. Both rows
    then printed as `main`, because what told them apart was the column not
    being matched on.

    `report.aggregate` made this exact mistake once, over repeats instead of
    over rounds, and `@identity` is what was added to end it. The field was
    already in the report; this file was not reading it.

    The second pass is what keeps a thread pool from reading as a disaster:
    `DefaultDispatcher-worker-2` in one set and `-worker-5` in the next is the
    same pool doing the same work, and matching on the literal name reports one
    row gone and another appeared, both of them large.

    It only fires when the family is unambiguous — exactly one unmatched row on
    each side. With two workers before and three after there is no honest way
    to say which became which, and guessing there would invent a delta out of
    nothing.
    """
    out: list[tuple[dict | None, dict | None, str]] = []

    # A list per key, not a row per key. Two rows of one report should never
    # share a full identity — the detector groups by it — but a hand-edited
    # report can, and dropping one silently is what this whole function is
    # being fixed for.
    by_key_after: dict[tuple, list[dict]] = {}
    for ra in after_rows:
        by_key_after.setdefault(_key(ra, identity), []).append(ra)

    left_before: list[dict] = []
    taken: set[int] = set()
    for rb in before_rows:
        same = by_key_after.get(_key(rb, identity))
        if same:
            ra = same.pop(0)
            taken.add(id(ra))
            out.append((rb, ra, "exact"))
        else:
            left_before.append(rb)
    # In the report's own order rather than the grouping's.
    left_after = [ra for ra in after_rows if id(ra) not in taken]

    fam_before: dict[tuple, list[dict]] = {}
    for rb in left_before:
        fam_before.setdefault(_family_key(rb, identity), []).append(rb)
    fam_after: dict[tuple, list[dict]] = {}
    for ra in left_after:
        fam_after.setdefault(_family_key(ra, identity), []).append(ra)

    paired_b: set[int] = set()
    paired_a: set[int] = set()
    for fam, bs in fam_before.items():
        as_ = fam_after.get(fam) or []
        if len(bs) == 1 and len(as_) == 1:
            out.append((bs[0], as_[0], "family"))
            paired_b.add(id(bs[0]))
            paired_a.add(id(as_[0]))

    for rb in left_before:
        if id(rb) not in paired_b:
            out.append((rb, None, "exact"))
    for ra in left_after:
        if id(ra) not in paired_a:
            out.append((None, ra, "exact"))
    return out


def _row(det_id: str, identity: tuple[str, ...], rb: dict | None,
         ra: dict | None, how: str, floor_ms: float,
         floor_ratio: float) -> dict[str, Any]:
    metric = metric_of(ra if ra is not None else rb)
    vb = (rb or {}).get(metric)
    va = (ra or {}).get(metric)
    loc, detail = _name(rb if rb is not None else ra, identity, how)

    if rb is None:
        change, delta, ratio = APPEARED, va or 0.0, None
    elif ra is None:
        change, delta, ratio = VANISHED, -(vb or 0.0), None
    else:
        delta = round((va or 0.0) - (vb or 0.0), 2)
        ratio = round((va or 0.0) / vb, 2) if vb else None
        floor = max(floor_ms, floor_ratio * (vb or 0.0))
        if abs(delta) < floor:
            change = STEADY
        else:
            change = GREW if delta > 0 else SHRANK

    row = {
        "location": loc,
        "detector": det_id,
        "metric": metric,
        "change": change,
        "matched_by": how,
        "before": _face(rb, metric),
        "after": _face(ra, metric),
        "delta_ms": round(delta, 2) if delta is not None else None,
        "ratio": ratio,
        "overlap": _overlap(rb, ra, metric),
    }
    if detail is not None:
        row["detail"] = detail
    return row


def _name(row: dict, identity: tuple[str, ...],
          how: str) -> tuple[str, str | None]:
    """What to call this row: the location, and what tells it from its twins.

    `detail` is carried only where the detector named it in `@identity` —
    there it is the difference between two rows of one location, and leaving
    it out prints them as duplicates of each other. Where a detector does not
    group by it, `detail` is evidence that varies between the two sides and
    would be a coin toss to show.

    A pair matched by family is two different names for one thing, so both
    halves are printed in the form they were matched in.
    """
    loc = str(row["location"])
    detail = row.get("detail") if "detail" in identity else None
    if how == "family":
        loc = family(loc)
        detail = None if detail is None else family(str(detail))
    return loc, None if detail is None else str(detail)


def _face(row: dict | None, metric: str) -> dict[str, Any] | None:
    """One side of a comparison row: the number, its spread, and the count."""
    if row is None:
        return None
    out: dict[str, Any] = {metric: row.get(metric)}
    band = (row.get("spread") or {}).get(metric)
    if band:
        out["min"], out["max"] = band["min"], band["max"]
        out["values"] = band["values"]
    if row.get("count") is not None:
        out["count"] = row["count"]
    if row.get("runs"):
        out["runs"] = row["runs"]
    if row.get("covered_ms") is not None:
        out["covered_ms"] = row["covered_ms"]
    if row.get("detail") is not None:
        out["detail"] = row["detail"]
    return out


def _overlap(rb: dict | None, ra: dict | None, metric: str) -> bool | None:
    """Do the two sets of repeats disagree by more than they moved?

    True means the ranges intersect: some run before was as slow as some run
    after, and the difference in medians is within the noise the runs already
    carry. False means they are apart — every repeat after was outside
    everything seen before, which is as close to proof as five runs get.
    None means one side had a single trace and there is nothing to test.
    """
    if rb is None or ra is None:
        return None
    b = (rb.get("spread") or {}).get(metric)
    a = (ra.get("spread") or {}).get(metric)
    if not b or not a:
        return None
    return not (a["max"] < b["min"] or b["max"] < a["min"])


# --- rendering --------------------------------------------------------------

CHANGE_LABEL = {APPEARED: "**new**", VANISHED: "**gone**"}
RANGE_LABEL = {True: "overlap", False: "apart", None: "—"}

# The two tables this file prints, in the order they are read.
#
# `detail` sits next to the location because that is what it is for here: on a
# detector that groups by a second column, two rows share one location and the
# Evidence column is the only thing telling them apart. It is absent from the
# rows of every other detector, and `table.columns` leaves out a column no row
# carries — so the table grows this one exactly where it is needed.
MOVED_COLUMNS = ["location", "detail", "detector", "before", "after", "delta",
                 "count", "ranges"]
MOVED_HEADERS = {"location": "Where", "detail": "Evidence",
                 "detector": "Detector", "before": "Before",
                 "after": "After", "delta": "Δ", "count": "N", "ranges": "Ranges"}
STATE_HEADERS = {"id": "detector", "before": "before", "after": "after"}


def to_markdown(cmp: dict[str, Any]) -> str:
    out: list[str] = ["# Comparison", ""]
    b, a = cmp["before"], cmp["after"]
    out.append(f"Before  `{b['path']}`  {b['runs']} run(s)  {_when(b)}")
    out.append(f"After   `{a['path']}`  {a['runs']} run(s)  {_when(a)}")
    out.append("")

    w = cmp["window"]
    if w["before_ms"] is not None and w["after_ms"] is not None:
        line = f"Scenario window: **{w['before_ms']} → {w['after_ms']} ms**"
        if w["delta_ms"] is not None:
            line += f"  ({w['delta_ms']:+.1f}"
            if w["ratio"]:
                line += f", ×{w['ratio']}"
            line += ")"
        out.append(line)

    s = cmp["summary"]
    out.append(
        f"Moved **{s['moved']}** · appeared **{s['appeared']}** · "
        f"gone **{s['vanished']}** · steady {s['steady']}"
    )
    out.append(
        f"Detectors fired: {len(s['fired_before'])} → {len(s['fired_after'])}"
    )
    out.append("")

    for warning in cmp["warnings"]:
        out.append(f"> ⚠️ {warning['text']}")
        out.append("")

    moved = [r for r in cmp["rows"] if r["change"] != STEADY]
    if not moved:
        out.append("## Nothing moved")
        out.append("")
        out.append(
            f"Every row is within the floor "
            f"({cmp['noise_floor']['abs_ms']} ms or "
            f"{int(cmp['noise_floor']['ratio'] * 100)}%). Either the change did "
            f"not land where these detectors look, or the two sets are the "
            f"same scenario in the same state."
        )
    else:
        out.append("## What moved")
        out.append("")
        out.append(
            f"_by each detector's own measure; a row is listed when it moves by "
            f"more than {cmp['noise_floor']['abs_ms']} ms or "
            f"{int(cmp['noise_floor']['ratio'] * 100)}%, whichever is larger. "
            f"± is the furthest a repeat got from the median._"
        )
        out.append("")
        out.append(_table(moved))
    out.append("")

    if s["state_changed"]:
        out.append("## Detectors that changed state")
        out.append("")
        out.append(table.render(
            [{"id": f"`{row['id']}`", "before": row["before"],
              "after": row["after"]} for row in s["state_changed"]],
            order=list(STATE_HEADERS), headers=STATE_HEADERS))
        out.append("")

    steady = [r for r in cmp["rows"] if r["change"] == STEADY]
    if steady:
        shown = steady[:8]
        # Same reason the table carries the column: two steady rows of one
        # location read as the same row listed twice without it.
        bits = [
            f"`{r['location']}`"
            + (f" ({r['detail']})" if r.get("detail") else "")
            + f" {_num(r['before'], r['metric'])} → "
              f"{_num(r['after'], r['metric'])}"
            for r in shown
        ]
        rest = len(steady) - len(shown)
        tail = f" · and {rest} more" if rest else ""
        out.append("## Steady")
        out.append("")
        out.append(" · ".join(bits) + tail)
        out.append("")

    quiet = sorted(set(_all_ids(cmp)) - set(s["fired_before"]) - set(s["fired_after"]))
    if quiet:
        out.append(f"**Silent in both:** {', '.join(quiet)}")
    return "\n".join(out).rstrip() + "\n"


def _all_ids(cmp: dict[str, Any]) -> list[str]:
    ids = list(cmp["summary"]["fired_before"]) + list(cmp["summary"]["fired_after"])
    ids += [r["detector"] for r in cmp["rows"]]
    ids += [r["id"] for r in cmp["summary"]["state_changed"]]
    return ids


def _table(rows: list[dict[str, Any]]) -> str:
    """The moved rows, through the one renderer that escapes.

    A location is a slice name or a thread name — an arbitrary string chosen
    by whoever wrote the trace{} call — and this file used to assemble its own
    header, its own separator and its own `_escape`, byte for byte the copy
    `table` exists to have ended. The other table below escaped nothing at
    all, so one pipe in a detector id would have shifted its row.
    """
    return table.render([{
        "location": r["location"]
                    + (" *(family)*" if r["matched_by"] == "family" else ""),
        **({"detail": r["detail"]} if r.get("detail") else {}),
        "detector": r["detector"],
        "before": _band(r["before"], r["metric"]),
        "after": _band(r["after"], r["metric"]),
        "delta": _delta(r),
        "count": _counts(r),
        "ranges": RANGE_LABEL[r["overlap"]],
    } for r in rows], order=MOVED_COLUMNS, headers=MOVED_HEADERS)


def _band(face: dict | None, metric: str) -> str:
    """The median with how far the repeats strayed from it."""
    if face is None:
        return "—"
    value = face.get(metric)
    if value is None:
        return "—"
    if "min" in face and face["min"] != face["max"]:
        off = max(value - face["min"], face["max"] - value)
        return f"{value} ±{off:.0f}"
    return f"{value}"


def _num(face: dict | None, metric: str) -> str:
    return "—" if face is None else str(face.get(metric, "—"))


def _delta(r: dict[str, Any]) -> str:
    label = CHANGE_LABEL.get(r["change"])
    if label:
        return label
    text = f"{r['delta_ms']:+.1f}"
    if r["ratio"] and (r["ratio"] >= 1.5 or r["ratio"] <= 0.67):
        text += f" ×{r['ratio']}"
    return f"**{text}**" if r["change"] == GREW and abs(r["delta_ms"]) >= 100 else text


def _counts(r: dict[str, Any]) -> str:
    b = (r["before"] or {}).get("count")
    a = (r["after"] or {}).get("count")
    if b is None and a is None:
        return "—"
    return f"{_int(b)} → {_int(a)}"


def _int(v: Any) -> str:
    return "—" if v is None else f"{v:g}"


def _when(side: dict[str, Any]) -> str:
    ts = side.get("generated_at") or ""
    return ts.replace("T", " ").replace("+00:00", "").rstrip("Z")

