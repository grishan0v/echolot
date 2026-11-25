"""`echolot compare` — the delta between two Marker Reports.

The tool exists for the question "it was 3 s, now it is 7 s, where did that
go". Everything up to here answers one half of it: `analyze` says where the
time went in one set of traces. Comparing two sets was left to whoever read
both reports — which for an agent means holding two twenty-row tables in the
window and subtracting them by eye, in exactly the place this tool exists to
take work out of.

The output is one table, sorted by how much each row moved, with two
exceptions (`_rank`): a move of the main thread's own time that holds comes
before sums over other threads, and a move the runs do not back comes after
the rest. The top row is usually the answer. Under it, the slice names that
are in every run of one side and in no run of the other, which no detector
reads (`_one_sided`).

Three things it refuses to do quietly:

  * compare across a moved bar. Thresholds decide which rows appear at all, so
    "appeared" against different thresholds means "the bar dropped below it",
    which is a different sentence. Reports built with different detector
    parameters say so at the top and every appeared/vanished row is suspect.

  * call a difference real when the repeats do not support it. Every row
    that exists on both sides carries the range its move lies in, at 95%
    confidence, and a row whose range runs through zero moved less than the
    runs disagree among themselves; the report says it does not hold and
    leaves the conclusion alone. That needs the per-run values in `spread`,
    which `analyze` writes for two repeats or more — with a single trace on
    either side the column reads `—`.

  * call a row gone, or new, because one of the recordings could not look for
    it. A detector the trace did not give what it reads (`checked: false` in
    the report, with its reason) says nothing on that side, so its rows are
    left out of the table on both, and the comparison names the detector and
    the reason from each side that could not look. A report written before
    `checked` existed is unknown, and is compared as it always was.

Rows are paired on everything the detector declared in `@identity`, which the
report carries. Seven of the fifteen shipped detectors name a second column
there, and one location legitimately holding several rows is what that field
exists to say; see `_match` for what pairing on the name alone produced
instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from . import drift as drift_mod
from . import launch as launch_mod
from . import when as when_mod
from . import needs as needs_mod
from . import recording as recording_mod
from . import names as names_mod
from . import report as report_mod
from . import files, plain, stats, table
from .names import across_builds
from .report import identity_of, metric_of, short_runs
from .files import runs as _runs

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
    warnings, comparable = _check(before, after, floor_ratio)
    # Rows whose move may be the tool's rather than the app's (#507): every
    # row when one side cannot say which echolot read it, or when both sides
    # are the same recordings; the rows of a detector whose code differs
    # between the two, otherwise.
    tool_warnings, unjudged = _tool_moved(before, after)
    warnings.extend(tool_warnings)

    rows: list[dict[str, Any]] = []
    state_changed: list[dict[str, Any]] = []
    not_compared: list[dict[str, Any]] = []
    moved_into = _moved_into(before, after, temp_prefix)
    # Which thread a row is of, by the app's own name for its main thread.
    whose = plain._Ctx(after if (after.get("window") or {}).get("process") else before)

    by_id_before = {d["id"]: _upgraded(d, before) for d in before.get("detectors", [])}
    by_id_after = {d["id"]: _upgraded(d, after) for d in after.get("detectors", [])}

    for det_id in [*by_id_before, *(k for k in by_id_after if k not in by_id_before)]:
        db = by_id_before.get(det_id)
        da = by_id_after.get(det_id)
        rows_b = (db or {}).get("rows") or []
        rows_a = (da or {}).get("rows") or []

        # A detector that failed did not look, and it is neither silent nor
        # the reason the other side's rows are gone or new. It used to be
        # both, and the error was not mentioned.
        failed = [(side, d) for side, d in (("before", db), ("after", da))
                  if _failed(d)]
        if failed:
            for side, d in failed:
                warnings.append({
                    "id": "detector-failed",
                    "text": f"`{det_id}` failed on the {side} side: "
                            f"{d['error']}. It did not look there, so its rows "
                            f"are left out of the table rather than called "
                            f"gone or new.",
                })
            if db is not None and da is not None:
                _state_change(state_changed, det_id,
                              "failed" if _failed(db) else _state(rows_b),
                              "failed" if _failed(da) else _state(rows_a))
            continue

        # A detector whose trace did not hold what it reads is as blind as one
        # that failed, on its side: silent there says nothing, so the other
        # side's rows are not gone and its own are not new (#441). A report
        # from before `checked` existed is unknown, and is compared as it was.
        if needs_mod.could_not_run(db) or needs_mod.could_not_run(da):
            not_compared.append({
                "id": det_id,
                "before": db["reason"] if needs_mod.could_not_run(db) else None,
                "after": da["reason"] if needs_mod.could_not_run(da) else None,
            })
            if db is not None and da is not None:
                _state_change(state_changed, det_id,
                              "not checked" if needs_mod.could_not_run(db) else _state(rows_b),
                              "not checked" if needs_mod.could_not_run(da) else _state(rows_a))
            continue

        if db is not None and da is not None and bool(rows_b) != bool(rows_a):
            state_changed.append({
                "id": det_id,
                "before": _state(rows_b),
                "after": _state(rows_a),
            })

        identity = _identity(db, da)
        pairs = _under_bar(_match(rows_b, rows_a, identity, temp_prefix),
                           db, da, identity, temp_prefix)
        for rb, ra, how, under in pairs:
            row = _judged(det_id, identity, rb, ra, how, floor_ms,
                          floor_ratio, temp_prefix, moved_into)
            row["on_main"] = plain._on_main(det_id, ra if ra is not None else rb, whose)
            if under:
                row["under_bar"] = under
            rows.append(row)

    rows.sort(key=lambda r: abs(r["delta_ms"] or 0.0), reverse=True)
    frames = _frames_moved(before, after, floor_ratio)
    if frames is not None:
        warnings.append(frames["warning"])
        _per_frame(rows, frames["ratio"], floor_ratio)
    markers = _markers(before, after, floor_ms, floor_ratio, temp_prefix, moved_into)
    window = _window(before, after, floor_ms, floor_ratio)
    startup = _startup(before, after, floor_ms, floor_ratio)

    # Two processes, two windows, or a window that is not the scenario's: the
    # numbers stay, because a wrong-looking table is more useful than a
    # refusal, but a verdict that a move holds would claim a comparison that
    # is not there (#534). The window and the startup are no detector's, and
    # go with every row: a move the tool or one set read twice can account
    # for is not the app's either (#532).
    for r in [*rows, *markers]:
        if not comparable or unjudged is ALL or (
                "detector" in r and r["detector"] in unjudged):
            r["shift"], r["holds"] = None, None
            if r.get("calls"):
                r["calls"]["shift"], r["calls"]["holds"] = None, None
    if not comparable or unjudged is ALL:
        for r in [window, startup, *((startup or {}).get("reasons") or [])]:
            if r and "holds" in r:
                r["shift"], r["holds"] = None, None

    # Stable, so the size order holds inside each part. With the frames
    # warning the rows that moved against the frames come first: they are
    # the ones the frame count does not explain (#566). Inside that, `_rank`.
    news = _news_ms(before, after)
    rows.sort(key=lambda r: (
        frames is not None and (r["delta_ms"] or 0.0) * (frames["ratio"] - 1) >= 0,
        _rank(r, news)))

    warnings.extend(_not_compared_warnings(before, after))

    planted = _planted([*rows, *markers], temp_prefix)
    if planted:
        warnings.append({
            "id": "instrumentation",
            "text": f"{len(planted)} marker(s) that appeared were added "
                    f"between the two rounds, not new work: "
                    + _names(planted)
                    + ". They show where the time inside a blind spot went, "
                      "which is what they were added for — read them as a "
                      "breakdown of what was already there."
                    + _parents_said([*rows, *markers], "after"),
        })
    removed = _planted([*rows, *markers], temp_prefix, VANISHED)
    if removed:
        warnings.append({
            "id": "instrumentation-removed",
            "text": f"{len(removed)} marker(s) that are gone were removed "
                    f"between the two rounds, not work that went away: "
                    + _names(removed)
                    + ". They were a breakdown of time that is still there, "
                      "now counted in the slices they sat in."
                    + _parents_said([*rows, *markers], "before"),
        })

    counts = {k: 0 for k in (APPEARED, VANISHED, GREW, SHRANK, STEADY)}
    for r in rows:
        counts[r["change"]] += 1
    names = _one_sided(before, after, [*rows, *markers], temp_prefix)

    return {
        # Shared with the reader, which says why it goes up (`files`).
        "schema": files.COMPARISON_SCHEMA,
        "kind": "comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "comparable": comparable,
        "warnings": warnings,
        "before": _side(before, before_path),
        "after": _side(after, after_path),
        "window": window,
        # The platform's startup, when both sides have one; null otherwise.
        "startup": startup,
        "noise_floor": {"abs_ms": floor_ms, "ratio": floor_ratio},
        "confidence": stats.CONFIDENCE_PCT / 100,
        # What a row's range has to clear for `holds` (#506): the floor, each
        # row's in `shift.floor_ms`. A comparison without the field held its
        # rows against zero.
        "holds_against": "floor",
        "summary": {
            "moved": counts[GREW] + counts[SHRANK],
            "appeared": counts[APPEARED],
            "vanished": counts[VANISHED],
            "steady": counts[STEADY],
            "fired_before": (before.get("summary") or {}).get("fired_ids") or [],
            "fired_after": (after.get("summary") or {}).get("fired_ids") or [],
            "state_changed": state_changed,
            # Detectors one side or both could not run: out of the table, and
            # why, from each side that could not (null where it could).
            "not_compared": not_compared,
            "silent_both": _silent_both(before, after),
            # Rows of the markers table that moved, by time or by calls.
            "markers_moved": sum(1 for r in markers if _marker_moved(r)),
            # Slice names in every run of one side and in none of the other.
            "only_after": len((names or {}).get("after") or []),
            "only_before": len((names or {}).get("before") or []),
        },
        "rows": rows,
        # The report's markers table, paired by name: the only place an async
        # section the app writes is measured at all (#618).
        "markers": markers,
        # Null when either report holds no slice names (#610).
        "names": names,
    }


# Slice names on one side only (#610). How many runs a side takes before a
# name in every one of them and in none of the other's is news: two halves of
# ten cold starts of one build, on a busy app, set against each other, had a
# hundred such names of a millisecond or more with one run a side, five to
# seventeen with two, three or four with three, and one at most from four
# runs on: a method the JIT compiled in one half only.
NAMES_LEAST_RUNS = 3
# From this many runs, a name may be missing from one of them: the same
# halves gave no more names when one run in five was let off.
NAMES_ALL_BUT_ONE = 5
# How many names of each side the comparison prints; `--names` prints all.
NAMES_SHOWN = 10


def _one_sided(before: dict, after: dict, listed: list[dict],
               temp_prefix: str | None) -> dict[str, Any] | None:
    """The slice names in every run of one side and in no run of the other (#610).

    `compare` pairs detector rows, and a slice no detector reads never
    reaches it, however clearly it tells the two builds apart. A planted
    busy loop of 200 ms on the main thread, with no section of its own, grew
    the window by 277 ms and gave no row on either side; the one name in all
    ten traces after and in none of the ten before was the JIT compilation
    of the method with the loop in it, which is where the code is. Agents
    found it by setting two sets of `echolot names` against each other in a
    script.

    A name of one side is listed when the runs it is in are all of that
    side's, or all but one from NAMES_ALL_BUT_ONE runs, and no run of the
    other side holds it at all (`report._merge_names`). Not with fewer than
    NAMES_LEAST_RUNS runs on either side: the names a run happens to have
    then outnumber new ones, and the result says so (`too_few`). Names the
    tables already have as new or gone are left out, the markers that were
    planted between the rounds with them.

    None when a report holds no names: one written before they were kept,
    or by an echolot that held them some other way.
    """
    sides = {"before": before.get(report_mod.SLICE_NAMES),
             "after": after.get(report_mod.SLICE_NAMES)}
    if not sides["before"] or not sides["after"]:
        return None
    seen = {side: report_mod.names_seen(s) for side, s in sides.items()}
    if seen["before"] is None or seen["after"] is None:
        return None
    runs = {side: int(s.get("runs") or 1) for side, s in sides.items()}
    floor = max(float(s.get("floor_ms") or 0.0) for s in sides.values())
    out: dict[str, Any] = {"runs": runs, "floor_ms": floor,
                           "least_runs": NAMES_LEAST_RUNS, "after": [], "before": []}
    if min(runs.values()) < NAMES_LEAST_RUNS:
        out["too_few"] = True
        return out
    # By the name `report._merge_names` gives it, so that a row of the table
    # and the same slice here are seen to be one.
    own = frozenset(str(r["location"]) for r in listed if r.get("detector") == "markers")
    tabled = {names_mod.slice_across_builds(str(r["location"]), keep=temp_prefix, names=own)
              for r in listed if r["change"] in (APPEARED, VANISHED)}
    for side, other in (("after", "before"), ("before", "after")):
        n = runs[side]
        need = n - 1 if n >= NAMES_ALL_BUT_ONE else n
        for row in report_mod.names_rows(sides[side]):
            if (_found_in(row) < need or row["total_ms"] < floor
                    or row["name"] in tabled
                    or report_mod.name_digest(row["name"]) in seen[other]):
                continue
            out[side].append({**row, "other": f"0/{runs[other]}"})
    return out


def _frames_moved(before: dict, after: dict,
                  floor_ratio: float) -> dict[str, Any] | None:
    """Whether the two sides drew different numbers of frames in the window.

    In a frame-paced scenario the window is the gesture's length whatever
    the app does, and a build that janks draws fewer frames in it. Work done
    once a frame shrinks with them: on one screen five times fewer frames
    (657 → 117.5) made `postAndWait` 894 → 400 ms, a shrink that held, while
    each occurrence went from 1.33 to 2.48 ms (#566). The same fix the other
    way round reads as a list of regressions. Warned at the relative floor,
    the move a row needs to be called moved at all.

    None when either side has no frame timeline, or the counts are close.
    """
    fb = ((before.get("window") or {}).get("frames") or {}).get("count")
    fa = ((after.get("window") or {}).get("frames") or {}).get("count")
    if not fb or not fa:
        return None
    ratio = fa / fb
    if abs(ratio - 1) < floor_ratio:
        return None
    fewer = ratio < 1
    factor = (1 / ratio) if fewer else ratio
    side = "after" if fewer else "before"
    return {"ratio": ratio, "warning": {
        "id": "frames",
        "text": f"The {side} side drew {factor:.1f}× fewer frames in the window "
                f"({_int(fb)} before, {_int(fa)} after, medians), so work done "
                f"once a frame is {'smaller after' if fewer else 'larger after'} "
                f"for that reason alone. Rows whose count followed the frames "
                f"are given per occurrence beside the total and get no verdict "
                f"on the total; the rows that moved against the frames are "
                f"listed first.",
    }}


def _per_frame(rows: list[dict[str, Any]], ratio: float, floor_ratio: float) -> None:
    """Rows whose count moved with the frames: per occurrence, and no verdict on the total.

    A row whose count went the way the frames went, by more than the floor,
    is work the frame count carries. Its total moving with them says nothing
    about the work, so `holds` is cleared, and the time per occurrence on
    each side, medians over counts, says which way the work itself went. A
    row without a count on both sides cannot be told apart and keeps its
    verdict.
    """
    for r in rows:
        b, a = r["before"] or {}, r["after"] or {}
        cb, ca = b.get("count"), a.get("count")
        vb, va = b.get(r["metric"]), a.get(r["metric"])
        if not cb or not ca or vb is None or va is None:
            continue
        moved = ca / cb
        if abs(moved - 1) < floor_ratio or (moved - 1) * (ratio - 1) <= 0:
            continue
        r["per_occurrence"] = {"before": round(vb / cb, 2), "after": round(va / ca, 2)}
        r["shift"], r["holds"] = None, None


def _news_ms(before: dict, after: dict) -> float:
    """The least move of the scenario's own time that leads the table: a
    twentieth of the longer window, the bar the plain reading sets before a
    check's sum is news (`moves.LAUNCH_SHARE`)."""
    from .moves import LAUNCH_SHARE  # moves imports this module

    longest = max(float((rep.get("window") or {}).get("duration_ms") or 0.0)
                  for rep in (before, after))
    return LAUNCH_SHARE * longest


def _rank(r: dict[str, Any], news_ms: float) -> int:
    """Which part of the table a row is listed in, before its size counts.

    0  a move of the main thread's own time, or of the frames, that holds
       and is news: at least `_news_ms`;
    1  every other row;
    2  a move the runs do not back: Holds is no.

    Ranked by size alone, a sum over a pool of threads led the table above
    the cause it followed from. Faults injected into one app, 5 cold starts
    of 5.5 s against 18 of the control: a 400 ms `Thread.sleep` on the main
    thread in an initializer moved `main_thread_block` by +419 ms, and it was
    ninth. First sat `monitor_contention` on the coroutine workers, −3996 ms,
    holding — with the main thread held longer the workers contended less,
    and summed over the pool that is seconds — then rows of the pool that
    vanished or did not hold. A 300 ms wait of the main thread on a monitor
    was thirteenth, under the same pool row at −4286 ms (#595). No pool row
    is time the scenario spent: the main thread's is, and a frame's, as the
    plain reading counts it (`plain._on_main`).

    Not the main thread first whatever moved there: a cause off it, such as
    threads that burn the CPU, reaches the main thread as small waits, and
    those would bury it. So the main thread's row has to hold, and to be as
    large as a move the plain reading would call news. With too few runs for
    Holds, nothing holds and the order is by size. A row whose move the runs
    do not back goes after the rest: in both cases above, rows over the
    cause did not hold.
    """
    if r["change"] == STEADY:
        return 1
    if r.get("holds") is False:
        return 2
    if r.get("on_main") and r.get("holds") and abs(r["delta_ms"] or 0.0) >= news_ms:
        return 0
    return 1


def _markers(before: dict, after: dict, floor_ms: float, floor_ratio: float,
             temp_prefix: str | None,
             moved_into: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The markers tables of both reports, paired by name like a detector's rows.

    Every marker of the project's own is measured there, whatever fired:
    the domains the config lists and the temporary ones. An async section
    (`Trace.beginAsyncSection`) has no thread to answer for, so no detector
    reads it, and a load that ran twice per run instead of once reached no
    row of the comparison at all. Here it is judged by self time like any
    row, and by its calls per run on their own (`calls`).

    A report written before the table existed has none, and that side is
    unknown rather than without markers: nothing is paired against it.
    """
    if "markers" not in before or "markers" not in after:
        return []
    rows_b = (before.get("markers") or {}).get("rows") or []
    rows_a = (after.get("markers") or {}).get("rows") or []
    out = []
    for rb, ra, how in _match(rows_b, rows_a, ("location",), temp_prefix):
        r = _judged("markers", ("location",), rb, ra, how, floor_ms, floor_ratio,
                    temp_prefix, moved_into or {})
        calls = _calls(rb, ra, floor_ratio)
        if calls is not None:
            r["calls"] = calls
        out.append(r)
    out.sort(key=lambda r: abs(r["delta_ms"] or 0.0), reverse=True)
    return out


def _calls(rb: dict | None, ra: dict | None,
           floor_ratio: float) -> dict[str, Any] | None:
    """A marker's calls per run on both sides, and whether the move holds.

    Moved when the medians are a whole call apart and more than the relative
    floor: 1 → 2 is the shape of work done twice, 400 → 404 is not news. The
    range is read the way a time's is, from each run's count, with zeros for
    the repeats a side did not find it in when the shares differ.
    """
    if rb is None or ra is None or rb.get("count") is None or ra.get("count") is None:
        return None
    shares = _shares(rb, ra)
    b, a = _values(rb, "count", shares), _values(ra, "count", shares)
    was, now = rb["count"], ra["count"]
    if shares and b and a:
        was, now = median(b), median(a)
    delta = round(now - was, 2)
    shift = stats.shift(b, a) if b and a else None
    return {
        "before": was, "after": now, "delta": delta,
        "moved": abs(delta) >= 1 and abs(delta) > floor_ratio * was,
        "shift": None if shift is None else {"low": shift["low_ms"],
                                             "high": shift["high_ms"]},
        "holds": None if shift is None else (
            shift["low_ms"] > 0 or shift["high_ms"] < 0),
    }


def _marker_moved(r: dict[str, Any]) -> bool:
    """A markers row worth listing: its time moved, or its calls did."""
    return r["change"] != STEADY or bool((r.get("calls") or {}).get("moved"))


def _not_compared_warnings(before: dict, after: dict) -> list[dict[str, str]]:
    """One warning for each side that holds detectors which could not run.

    The detectors' own reasons, as the report says them. Not one warning per
    detector: a recording made without the platform's state loses two, and a
    comparison of such a recording with a full one would open with a screen of
    them.
    """
    out = []
    said = {side: needs_mod.said(report.get("detectors") or [])
            for side, report in (("before", before), ("after", after))}
    if said["before"] is not None and said["before"] == said["after"]:
        # The same detectors for the same reasons on both: one warning that
        # names both sides, where two of the same 69 words pushed what
        # differs between the rounds off the first screen (#585).
        count, why = said["before"]
        return [{
            "id": "not-checked-both",
            "text": f"{count} detector(s) could not run on either side, because "
                    f"neither side holds what they read. {why} They are "
                    f"left out of the comparison.",
        }]
    for side in ("before", "after"):
        found = said[side]
        if found is None:
            continue
        count, why = found
        out.append({
            "id": f"not-checked-{side}",
            "text": f"{count} detector(s) could not run on the {side} side, "
                    f"because that side does not hold what they read. "
                    f"{why} Their rows are left out of the table rather than "
                    f"called gone or new, and a detector that could not run on "
                    f"one side is not compared on the other.",
        })
    return out


def _planted(rows: list[dict], temp_prefix: str | None,
             change: str = APPEARED) -> list[str]:
    """Rows that appeared because the hunt instrumented them, not because of a change.

    Inside the loop, a round is compared against the one before it — and
    between the two the agent added `AGENTTMP_` markers on purpose. Those
    arrive as large new rows, which is exactly the shape a regression has. The
    difference is that they were planted, under the config's prefix, or
    `AGENTTMP_` when it names none: `cmd_compare` falls back the way
    `analyze` and `mark` do.

    Each name once: one marker found by two detectors, and by the markers
    table, is one marker, and listed per row two markers read as four.
    `change` VANISHED asks for the other direction: markers a round took out.
    """
    if not temp_prefix:
        return []
    return list(dict.fromkeys(
        r["location"] for r in rows
        if r["change"] == change and str(r["location"]).startswith(temp_prefix)))


def _names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names[:5]) + (" …" if len(names) > 5 else "")


def _moved_into(before: dict, after: dict,
                temp_prefix: str | None) -> dict[str, dict[str, Any]]:
    """The slices whose self time went into markers one round added, or came back from.

    A marker placed directly under a slice becomes its child, and self time
    is a slice less its direct children: `Compose:recompose` dropped from
    3714 to 105 ms self in the round that put a marker of 3885 ms under it,
    while its total did not move, and the drop opened the table as the
    largest improvement (#584). `analyze` records for each temporary marker
    the slices it sat in and for how long (`parents` in the markers table).

    Keyed by the parent's name: how many ms went into markers, which
    markers, and the side that has them — `after` for markers a round
    added, `before` for markers it took out.
    """
    if not temp_prefix:
        return {}

    def temp(rep: dict) -> dict[str, dict]:
        return {str(r["location"]): r for r in (rep.get("markers") or {}).get("rows") or []
                if str(r["location"]).startswith(temp_prefix)}

    tb, ta = temp(before), temp(after)
    out: dict[str, dict[str, Any]] = {}
    for side, have, other in (("after", ta, tb), ("before", tb, ta)):
        for name, r in have.items():
            if name in other:
                continue
            for parent, ms in (r.get("parents") or {}).items():
                into = out.setdefault(parent, {"ms": 0.0, "markers": [], "side": side})
                if into["side"] == side:
                    into["ms"] = round(into["ms"] + (ms or 0.0), 2)
                    into["markers"].append(name)
    return out


def _judged(det_id: str, identity: tuple[str, ...], rb: dict | None,
            ra: dict | None, how: str, floor_ms: float, floor_ratio: float,
            keep: str | None, moved_into: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One paired row, judged on its total where its self time went into markers.

    The total of a slice is what a marker under it cannot move, so moved,
    steady and Holds are read from that, and the row says how much self time
    the markers took (`into_markers`). A row that carries no total keeps its
    self time, and the warning still names it.
    """
    r = _row(det_id, identity, rb, ra, how, floor_ms, floor_ratio, keep)
    into = moved_into.get(str(r["location"]))
    if (into and r["metric"] == "self_ms" and rb is not None and ra is not None
            and rb.get("total_ms") is not None and ra.get("total_ms") is not None):
        r = _row(det_id, identity, rb, ra, how, floor_ms, floor_ratio, keep,
                 metric="total_ms")
        r["into_markers"] = into
    return r


def _parents_said(rows: list[dict], side: str) -> str:
    """The sentence naming the slices whose self time the markers took or gave back."""
    named = list(dict.fromkeys(
        f"`{r['location']}` ({r['into_markers']['ms']:g} ms)" for r in rows
        if (r.get("into_markers") or {}).get("side") == side))
    if not named:
        return ""
    if side == "after":
        return (" The self time of " + ", ".join(named) + " went into them, so "
                "it shrank by that much on the after side without getting any "
                "cheaper: those rows are judged on their total.")
    return (" The self time of " + ", ".join(named) + " came back from them, so "
            "it grew by that much without any new work: those rows are judged on "
            "their total.")


# --- what may be compared with what ----------------------------------------

def _check(before: dict, after: dict,
           floor_ratio: float = FLOOR_RATIO) -> tuple[list[dict[str, str]], bool]:
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

    # Two windows that were not defined the same way: anchors against the
    # launch, or a launch closed at its end against one that ran on to
    # `reportFullyDrawn`. The command refuses the pair before it gets here;
    # for any other caller the reason is at the top, as for two processes.
    apart = launch_mod.differ(before, after)
    if apart:
        out.append({
            "id": "window",
            "text": f"Not one window: {apart}. The two cover different "
                    f"stretches, every row moves for that reason alone, and "
                    f"nothing below is a comparison.",
        })
        comparable = False

    # Either anchor, as report.md warns about either. With no end anchor the
    # window runs on to the end of the recording, which is how ten detectors
    # once fired on a scenario that had not been cut out.
    for side, w in (("before", wb), ("after", wa)):
        warning, broken = _anchor_warning(side, w)
        if warning is not None:
            out.append(warning)
        # An anchor no repeat found leaves a window that is not the
        # scenario's: every row is measured over the edge of the trace, and
        # a verdict over that is a verdict about a different stretch (#534).
        if broken:
            comparable = False

    cb, ca = before.get("config") or {}, after.get("config") or {}
    if cb.get("sha") and ca.get("sha") and cb["sha"] != ca["sha"]:
        seen = _config_moved(before, after)
        out.append({
            "id": "config",
            "text": "The config changed between the two: "
                    + ("; ".join(seen) + f" (sha `{cb['sha']}` → `{ca['sha']}`)."
                       if seen else
                       f"sha `{cb['sha']}` → `{ca['sha']}`. Anchors, the "
                       f"process mask and thresholds all live there."),
        })
    if _defaults_moved(before, after):
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

    out.extend(_environment_moved(before, after, floor_ratio))
    out.extend(recording_mod.moved(before, after))
    out.extend(_sampling_moved(before, after))
    out.extend(_outside_moved(before, after))
    out.extend(_compose_names_moved(before, after))
    out.extend(_compile_moved(before, after))
    out.extend(drift_mod.warnings(before, after))

    nb, na = _runs(before), _runs(after)
    if nb != na:
        out.append({
            "id": "runs",
            "text": f"Different numbers of repeats — {nb} before, {na} after. "
                    f"Medians stay comparable, and the Holds column allows "
                    f"for the difference. The ± beside each median does not: "
                    f"it is built from unequal samples, and the narrower side "
                    f"is the smaller one.",
        })
    if nb == 1 or na == 1:
        out.append({
            "id": "single",
            "text": "A single trace on one side or both: there is no spread to "
                    "check a difference against, and the Holds column stays "
                    "empty. One run cannot tell a change from a hiccup.",
        })
    elif stats.cut(nb, na) is None:
        out.append({
            "id": "few",
            "text": f"{nb} repeats before and {na} after are too few to be "
                    f"{stats.CONFIDENCE_PCT}% sure of any move, so the Holds column "
                    f"stays empty. It takes four a side, or three against "
                    f"five.",
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


# Every row, for `_tool_moved`: the rows of no particular detector.
ALL = frozenset({"*"})


def _tool_moved(before: dict, after: dict) -> tuple[list[dict[str, str]], frozenset[str] | set[str]]:
    """What the tool itself changed between the two reports (#507), and the
    detectors whose rows that leaves without a verdict (`ALL` for every row).

    Each report says which echolot wrote it (`toolchain.echolot`), and each
    detector what it read the trace with (`sha`). Two versions that differ
    in a detector read one trace differently: 0.7.0 and 0.17.0 on the very
    same fifteen traces gave 11 rows that held, `monitor_contention` among
    them, from a double count fixed in between. A detector whose `sha`
    differs keeps its rows and loses their verdict; a side that does not say
    which echolot wrote it leaves every row without one, since which of its
    detectors read the trace the way the other side's did cannot be known.
    Two reports that both predate the stamp are compared as they always were.

    And the same recordings on both sides — a heuristic that needs nothing
    new in the report, so it works on old ones too: the same trace names,
    the same pid, the same window to the hundredth of a millisecond. Then
    nothing in the app can have moved, and every row that did is the tool's
    or the config's.
    """
    out: list[dict[str, str]] = []
    unjudged: frozenset[str] | set[str] = set()
    vb, va = _stamp(before), _stamp(after)

    same = _same_recordings(before, after)
    if same is not None:
        out.append({
            "id": "same-traces",
            "text": f"Both sides are the same recordings: {same}. Nothing in "
                    f"the app can have moved between them, so every move "
                    f"below is the tool's or the config's, and no row "
                    f"carries a verdict.",
        })
        unjudged = ALL

    if bool(vb) != bool(va):
        old, new = ("before", va) if not vb else ("after", vb)
        out.append({
            "id": "version",
            "text": f"The {old} report does not say which echolot wrote it: "
                    f"it predates reports that did (the other side is "
                    f"{new}). Whether its detectors read the trace the way "
                    f"the other side's did cannot be checked, so no row "
                    f"carries a verdict — a move may be the tool's. Analyze "
                    f"the {old} side's traces again to compare like with like.",
        })
        return out, ALL

    changed = sorted(
        d for d, (sb, sa) in _shas(before, after).items() if sb and sa and sb != sa)
    if vb and va and vb != va:
        text = f"Written by different versions of echolot: {vb} before, {va} after."
        if not changed:
            text += (" Every detector both ran read the trace the same way, so "
                     "their rows compare as they are.")
        out.append({"id": "version", "text": text})
    if changed:
        out.append({
            "id": "detector-changed",
            "text": f"{_names(changed)} read the trace differently on the "
                    f"two sides — the detector's code changed between the "
                    f"echolot that wrote one report and the one that wrote "
                    f"the other — so their rows carry no verdict: a move "
                    f"there may be the tool's. Analyze both sides' traces "
                    f"with one echolot to compare them.",
        })
        if unjudged is not ALL:
            unjudged = set(changed)
    return out, unjudged


def unjudged(before: dict, after: dict) -> frozenset[str] | set[str]:
    """The detectors whose rows carry no verdict because the tool may have
    moved them (`ALL` for every one), as `build` decides it."""
    return _tool_moved(before, after)[1]


def same_recordings(before: dict, after: dict) -> str | None:
    """Why the two sides look like one set of recordings, or None."""
    return _same_recordings(before, after)


def _stamp(report: dict) -> str | None:
    """Which echolot wrote a report, or None when it does not say."""
    tc = report.get("toolchain")
    v = tc.get("echolot") if isinstance(tc, dict) else None
    return v if isinstance(v, str) and v else None


def _shas(before: dict, after: dict) -> dict[str, tuple[str | None, str | None]]:
    """Each detector both sides ran, with what each read the trace with."""
    sb = {d["id"]: d.get("sha") for d in before.get("detectors") or []}
    sa = {d["id"]: d.get("sha") for d in after.get("detectors") or []}
    return {d: (sb[d], sa[d]) for d in sb if d in sa}


def _same_recordings(before: dict, after: dict) -> str | None:
    """Words for why the two sides look like one set of recordings, or None.

    The trace names alone are not enough: every round of one scenario is
    `<scenario>_iter000…`. The pid is what one recording has and another
    does not, and the window's median, shortest and longest, to the
    hundredth of a millisecond, is what one set has and another does not.
    """
    def names(rep: dict) -> list[str]:
        traces = rep.get("traces") or ([rep["trace"]] if rep.get("trace") else [])
        return sorted(Path(str(t)).name for t in traces)

    wb, wa = before.get("window") or {}, after.get("window") or {}
    if not names(before) or names(before) != names(after):
        return None
    if wb.get("pid") is None or wb.get("pid") != wa.get("pid"):
        return None
    spans = [tuple(None if (w.get(k) is None) else round(w[k], 2)
                   for k in ("duration_ms", "duration_ms_min", "duration_ms_max"))
             for w in (wb, wa)]
    if spans[0] != spans[1] or spans[0][0] is None:
        return None
    n = len(names(before))
    window = (f"median {spans[0][0]} ms, {spans[0][1]}–{spans[0][2]}"
              if spans[0][1] is not None else f"{spans[0][0]} ms")
    return (f"the same {n} trace name{'s' if n != 1 else ''}, the same process "
            f"(pid {wb['pid']}) and the same window ({window})")


def _anchor_warning(side: str, w: dict) -> tuple[dict[str, str] | None, bool]:
    """One side's anchors that did not match, worded from what its report says.

    A merged report keeps the fewest matches of any repeat, so one repeat
    without the anchor makes `matches` 0, and `missed` says how many repeats
    that was (#594). One warm start in fifteen is not a broken anchor, and
    "fix the config" sent the reader to the one file that was right. Only an
    anchor that no repeat found is broken, and only that makes the pair not
    comparable; even then the config is named only when the traces hold a
    launch, since a set that held none was not the scenario's recordings.

    The second value is whether an anchor matched in no repeat at all.
    """
    partial: list[str] = []
    never: list[str] = []
    for key in ("start", "end"):
        a = w.get(f"{key}_anchor") or {}
        if a.get("matches") != 0:
            continue
        label = f"{key} anchor `{a.get('glob')}`"
        some = short_runs(a.get("missed"))
        if some:
            partial.append(f"{label} was not found in {some[0]} of {some[1]} "
                           f"{side} repeats")
        else:
            never.append(label)
    if not partial and not never:
        return None, False
    bits: list[str] = []
    if never:
        text = (f"The {side} report's " + " and ".join(never)
                + " never matched in any repeat — its window is not the "
                  "scenario's: it runs over the process's own first and last "
                  "slices instead, so the Holds column stays empty.")
        if w.get("startup") is None:
            text += (" Its traces hold no app launch either: if the scenario "
                     "starts the app, those recordings were not launches "
                     "(check `runner.reset_policy` and what the runner said), "
                     "and the config may be right. If it does not, fix the "
                     "anchor in the config and analyze again.")
        else:
            text += " Fix the config and analyze again."
        bits.append(text)
    if partial:
        text = ("The " + "; the ".join(partial)
                + ". In those the window took the process's first or last "
                  "slice for the missing end, and their rows are in the medians.")
        types = str((w.get("startup") or {}).get("type") or "")
        if " and " in types:
            text += (f" The set mixes starts ({types}): the repeats without the "
                     f"anchor are likely the warm ones, without a process "
                     f"start. Leave those traces out and analyze again.")
        else:
            text += (" Leave those traces out and analyze again, or check "
                     "them against `probe`.")
        bits.append(text)
    return {"id": f"anchor-{side}", "text": " ".join(bits)}, bool(never)


def _config_moved(before: dict, after: dict) -> list[str]:
    """What the two reports themselves show changed in the config.

    The hash says only that something did. The anchors and the config's path
    are in both reports, and "end anchor `a` → `b`" explains a window that
    moved on its own; thresholds are named by `_params_moved` already.
    """
    out: list[str] = []
    cb, ca = before.get("config") or {}, after.get("config") or {}
    if cb.get("path") and ca.get("path") and cb["path"] != ca["path"]:
        out.append(f"config `{cb['path']}` → `{ca['path']}`")
    wb, wa = before.get("window") or {}, after.get("window") or {}
    for key in ("start", "end"):
        gb = (wb.get(f"{key}_anchor") or {}).get("glob")
        ga = (wa.get(f"{key}_anchor") or {}).get("glob")
        if gb != ga and (gb or ga):
            out.append(f"{key} anchor `{gb}` → `{ga}`"
                       if gb and ga else
                       f"{key} anchor `{gb or ga}` {'dropped' if gb else 'added'}")
    return out


# How far the clock may drift before the table below stops being about the
# app: the floor this comparison runs with, `--floor-pct`, FLOOR_RATIO by
# default. That is the bar a row has to clear to be called moved at all, so a
# clock that differs by as much can produce every row in the table on code
# nobody touched.
#
# The drift is the factor a CPU-bound row moves by, the faster clock over the
# slower, less one. Measured against the before clock, a drop from 2200 to
# 2000 MHz was 9.1% and passed in silence, while it makes such a row 10%
# longer: listed as grew, and the one case that looks like a regression.


def _environment_moved(before: dict, after: dict,
                       floor_ratio: float = FLOOR_RATIO) -> list[dict[str, str]]:
    """The platform state the two rounds were measured under.

    This is the warning the whole comparison rests on and the one it went
    longest without. A comparison subtracts durations, and a duration is the
    work done divided by the speed the machine was doing it at. Drop the clock
    by a third between rounds and every row grows by half, the table reads as
    a regression, and the honest answer is that nothing in the app moved.

    Two facts get a warning, and only these two: the clock our own threads ran
    at, and whether the kernel throttled. Both are measurements. Temperature
    on its own is not — a warm device that was never throttled lost nothing —
    and it stays in the report as context rather than becoming a caution here.

    They are kept independent because a live pair showed they do not move
    together. The same scenario on an SM-A515F, once at 76 °C with
    `thermal-cpufreq-1` throttled in two runs of three and again at 55 °C with
    none, came back at 2220 MHz and 2209 MHz — half a percent apart, nowhere
    near the bar below. Throttling takes away the headroom rather than the
    frequency the app was actually using, so a clock check on its own would
    have passed that pair in silence. It is also why 21 °C of difference
    warrants no warning by itself: on that pair the temperature moved and the
    speed did not.

    Silence has to mean "checked and steady", so a side with nothing recorded
    says so instead. What "nothing recorded" looks like is `_measured`'s
    business: `analyze` writes the block for every report, so a trace
    recorded without the platform-state sources does not arrive empty.
    """
    eb, ea = before.get("environment") or {}, after.get("environment") or {}
    if not _measured(eb) and not _measured(ea):
        # Neither side measured anything. The reports say so on their own
        # pages, and repeating it on every comparison of two such rounds
        # would be noise about a config rather than about these two rounds.
        return []

    out: list[dict[str, str]] = []
    cb, ca = eb.get("cpu") or {}, ea.get("cpu") or {}
    mb, ma = cb.get("mean_mhz"), ca.get("mean_mhz")
    if not mb or not ma:
        # A side without a clock, while something was measured on one side
        # or the other. Thermal is still worth checking below — a missing
        # clock is not a missing device.
        blank = [side for side, mhz in (("before", mb), ("after", ma)) if not mhz]
        sides = ("the before and after sides carry" if len(blank) == 2
                 else f"the {blank[0]} side carries")
        out.append({
            "id": "environment",
            "text": f"The clock could not be checked: {sides} no CPU frequency. "
                    "Whether the two rounds ran on the same machine speed is "
                    "unknown, so a grown row below may be the app or may be "
                    "the device. Record both sides with `runner.environment` "
                    "on to have this answered.",
        })
    else:
        drift = max(mb, ma) / min(mb, ma) - 1
        if drift >= floor_ratio:
            slower, faster = ("after", "before") if ma < mb else ("before", "after")
            out.append({
                "id": "environment",
                "text": f"The clock moved between the rounds: {mb:.0f} MHz "
                        f"before, {ma:.0f} MHz after — {drift * 100:.0f}% "
                        f"apart, weighted by the time this app held a core. "
                        f"That is at or above the {floor_ratio * 100:.0f}% "
                        f"a row must move to be called moved at all, so the "
                        f"table below cannot separate the app from the machine. "
                        f"The {slower} round ran slower than the {faster} one "
                        f"on the same code. Re-run both on a settled device.",
            })

    # A merged side whose clock or temperature came from some of its repeats
    # alone. The checks here compared those repeats with the other side, and
    # silence would say the whole set was checked and steady.
    for side, env in (("before", eb), ("after", ea)):
        partial = [f"its {what} in {short[0]} of {short[1]} repeats"
                   for what, block in (("clock", env.get("cpu")),
                                       ("thermal state", env.get("thermal")))
                   if (short := short_runs((block or {}).get("runs")))]
        if partial:
            out.append({
                "id": "environment",
                "text": f"The {side} side recorded " + " and ".join(partial)
                        + ". What this comparison says about the device stands "
                        "for those repeats alone; the others may have run on a "
                        "different machine speed. Record every repeat with "
                        "`runner.environment` on to have the whole set checked.",
            })

    # Both sides, or neither. A side that recorded no thermal at all has not
    # said it ran cool, and "throttled here and not there" about a round
    # nobody measured is the exact confusion the rest of this block exists to
    # prevent. It took a live pair to find: one round on a throttling A51
    # against one recorded with `runner.environment: false` produced a
    # confident sentence about a device that was never looked at.
    tb, ta = eb.get("thermal") or {}, ea.get("thermal") or {}
    if tb and ta and tb.get("throttled") != ta.get("throttled") and (
            tb.get("throttled") or ta.get("throttled")):
        hot = "before" if tb.get("throttled") else "after"
        out.append({
            "id": "environment-thermal",
            "text": f"The kernel throttled the device during the {hot} round "
                    f"and not the other one. Throttling takes capacity away, "
                    f"so the {hot} side is slower for a reason that has "
                    f"nothing to do with the code. Let the device cool and "
                    f"record that side again.",
        })
    elif bool(tb) != bool(ta) and (tb or ta).get("throttled"):
        # Throttled on one side, and no thermal record on the other (#533).
        # The rule above holds: nothing is said of the side nobody measured.
        # But silence here was the riskiest case — "window ×0.93" with no
        # word that one side was measured on a throttling phone — and what
        # the measured side says about itself is a fact either way.
        hot, blind = ("before", "after") if tb else ("after", "before")
        k_n = short_runs((tb or ta).get("throttled_runs"))
        runs = (f" in {k_n[0]} of {k_n[1]} repeats" if k_n else
                " in every repeat" if (tb or ta).get("throttled_runs") else "")
        out.append({
            "id": "environment-thermal-one-side",
            "text": f"The kernel throttled the device during the {hot} "
                    f"round{runs}. The {blind} round has no thermal record, "
                    f"so throttling cannot be ruled out there either: the "
                    f"{hot} numbers are a slowed device, and the comparison "
                    f"cannot say whether the {blind} ones are too. Record "
                    f"both rounds with `runner.environment` on to set the two "
                    f"against each other.",
        })
    return out


# How far apart, in points of the CPUs' time, the two sides' outside load
# may be before it is a warning, once one of them was busy.
OUTSIDE_GAP = 15


def _outside_moved(before: dict, after: dict) -> list[dict[str, str]]:
    """Whether other processes took a large part of the CPUs on one side (#596).

    Eight busy loops of the shell user took 68% of all eight cores during one
    round, and the comparison against a quiet round warned only that the
    clock was lower. The window grew ×3.2 and 43% of the rows held, led by
    the app's pools waiting for a CPU. Each report carries the share other
    processes took (`window.crowding.outside`), and the two are set side by
    side here as the clock is.
    """
    def of(rep: dict) -> dict:
        return (((rep.get("window") or {}).get("crowding") or {}).get("outside")) or {}
    ob, oa = of(before), of(after)
    pb, pa = ob.get("pct"), oa.get("pct")
    if pb is None or pa is None:
        return []
    # A report from before #914 counted the processes that draw the app as
    # other load, and one after leaves them out (`platform_pct`): a launch
    # read both ways differs by 40 points that no load put there.
    if ("platform_pct" in ob) != ("platform_pct" in oa):
        return []
    if max(pb, pa) < report_mod.OUTSIDE_PCT or abs(pa - pb) < OUTSIDE_GAP:
        return []
    busy, quiet = ("after", "before") if pa > pb else ("before", "after")
    who = report_mod.outside_who(oa if pa > pb else ob)
    return [{
        "id": "environment-load",
        "text": f"Other processes took {max(pb, pa):.0f}% of the CPUs' time in "
                f"the {busy} round's window and {min(pb, pa):.0f}% in the "
                f"{quiet} round's" + (f", most of it {who}" if who else "")
                + f". The {busy} round ran on a busy device, and a row that "
                  f"grew there may have grown with the load. Record "
                  f"that side again with nothing else running.",
    }]


def _sampling_moved(before: dict, after: dict) -> list[dict[str, str]]:
    """Whether a sampler ran on one side and not the other, or at another rate.

    The same kind of warning as a clock that moved: a cause outside the code
    that makes one side slower. A sampler interrupts the app on every tick to
    copy its stack; on the device it was measured on, a cold start of 1.36 s
    sampled at 100 Hz ran 186 ms longer than the same start without it.

    A report from before the field existed carries no `sampling` at all, and
    that side is unknown rather than unsampled, so it gets no sentence. What
    counts is whether the sampler ran: a round that asked for samples on a
    device that has no sampler ran like a plain one.
    """
    eb, ea = before.get("environment") or {}, after.get("environment") or {}
    if "sampling" not in eb or "sampling" not in ea:
        return []
    was, now = _sampled(eb["sampling"]), _sampled(ea["sampling"])
    if was == now:
        return []
    return [{
        "id": "sampling",
        "text": f"Callstack sampling was {was} before and {now} after. The "
                f"sampler takes time from the app, so a row can move for that "
                f"reason alone, on code nobody touched. Record both rounds "
                f"with the same `runner.sampling`.",
    }]


def _compose_names_moved(before: dict, after: dict) -> list[dict[str, str]]:
    """Whether one side was recorded with composable names and the other not.

    The names come with the app's Perfetto SDK loaded at its start, and that
    is time the app spent: on an SM-A515F a cold start of about 580 ms ran
    67 ms longer with it on the median. A report from before the field
    existed carries no `compose_names` and is unknown rather than without, so
    it gets no sentence.
    """
    eb, ea = before.get("environment") or {}, after.get("environment") or {}
    if "compose_names" not in eb or "compose_names" not in ea:
        return []
    was, now = _named(eb["compose_names"]), _named(ea["compose_names"])
    if was == now:
        return []
    return [{
        "id": "compose_names",
        "text": f"Composable names were {was} before and {now} after. They come "
                f"with the app's Perfetto SDK loaded at its start, which takes "
                f"time from the app, so a row can move for that reason alone. "
                f"Record both rounds with the same `runner.compose_names`.",
    }]


def _compile_moved(before: dict, after: dict) -> list[dict[str, str]]:
    """Whether the two rounds ran the app compiled the same way.

    Code compiled ahead of time from a profile runs at another speed than
    code the JIT compiles while the app runs. On an SM-A515F a large app's
    launch took ~1.4 s on its benchmark build and ~8.8 s on a debuggable
    build that nothing had compiled, and every row of a comparison between
    them grows for that reason alone. The reason counts for a profile's
    filter: a profile widened by background dexopt (`bg-dexopt`) compiles
    more than the one the install brought (`install-dm`). Which states are
    apart is `compiled_apart`'s.

    A report from before the field carries no `compile` and is unknown, and
    so is a side whose traces did not hold the state. That side's filter
    gets no sentence; its JIT count, which every trace holds, is compared
    instead (`_jit_moved`, #592). A side whose own repeats disagree gets
    one, whatever the other side did.
    """
    eb, ea = before.get("environment") or {}, after.get("environment") or {}
    if "compile" not in eb or "compile" not in ea:
        return []
    cb, ca = eb["compile"] or {}, ea["compile"] or {}
    out: list[dict[str, str]] = []
    was, now = _compiled(cb), _compiled(ca)
    if compiled_apart(before, after):
        out.append({
            "id": "compile",
            "text": f"The app was compiled differently: {was} before, {now} "
                    f"after. Code compiled ahead of time runs faster than code "
                    f"the JIT compiles as the app runs, so a row can move for "
                    f"that reason alone. Record both rounds on builds "
                    f"compiled the same way.",
        })
    elif not (was and now):
        out.extend(_jit_moved(cb, ca))
    for side, c in (("before", cb), ("after", ca)):
        if " and " in (c.get("filter") or "") or " and " in (c.get("reason") or ""):
            out.append({
                "id": "compile",
                "text": f"The {side} side's repeats ran on different compile "
                        f"states ({_compiled(c)}), so its medians mix two "
                        f"speeds of the same code. Record that side again on "
                        f"one build, installed one way.",
            })
    return out


# How far apart the JIT's work may be before it says the code ran another way:
# a factor, and a floor in methods so that 2 against 7 is not a finding. On an
# SM-A515F a scroll compiled from its profile JIT-compiled 64 methods in the
# window, the same APK compiled `verify` 328 and compiled `speed` 2 (#592).
JIT_FACTOR = 3.0
JIT_FLOOR_METHODS = 50


def _jit_moved(cb: dict, ca: dict) -> list[dict[str, str]]:
    """The JIT's work in the window, when a side could not say its compile state.

    A recording that begins with the process already running holds no
    compile state, and #415 left such a side without a sentence. Its JIT
    count is still a measurement, taken in the window, by any later
    `analyze`: code compiled ahead of time leaves the JIT little to do, and
    code nobody compiled leaves it a lot. Five times the methods is not the
    same build run twice.
    """
    jb, ja = (cb.get("jit") or {}).get("methods"), (ca.get("jit") or {}).get("methods")
    if jb is None or ja is None:
        return []
    low, high = sorted((jb, ja))
    if high - low < JIT_FLOOR_METHODS or high < JIT_FACTOR * max(low, 1):
        return []
    more = "after" if ja > jb else "before"
    return [{
        "id": "compile",
        "text": f"The JIT compiled {jb} methods in the window before and {ja} "
                f"after, and the traces do not say how the app was compiled. "
                f"That many more on the {more} side is what code compiled "
                f"another way looks like — `verify` or a cleared profile leave "
                f"the JIT more to do — so a row can move for that reason "
                f"alone. Check the compile state on the device "
                f"(`dumpsys package dexopt`), or record both rounds with the "
                f"app's process started after its last compile.",
    }]


def compiled_apart(before: dict, after: dict) -> tuple[str, str] | None:
    """The two sides' compile states in words, when the app ran compiled
    differently on them; None when they agree or a side does not say.

    The filter is what decides how fast the code runs. The reason counts
    only for a filter compiled from a profile (`speed-profile`): which
    profile it was depends on how the system came to compile it, and a
    profile widened by background dexopt is not the one the install brought.
    For any other filter the reason is only how it came about: `verify`
    after an install and `verify` after `cmd package compile` are the same
    code, and were warned about as two builds (#809). The reason stays in
    the words, beside the filter.
    """
    cb = (before.get("environment") or {}).get("compile") or {}
    ca = (after.get("environment") or {}).get("compile") or {}
    was, now = _compiled(cb), _compiled(ca)
    if not (was and now) or _build(cb) == _build(ca):
        return None
    return was, now


def _build(c: dict) -> tuple[str, str | None]:
    """What of a compile state moves the code's speed: the filter, and the
    reason beside a profile's."""
    f = str(c.get("filter"))
    return f, (c.get("reason") if "-profile" in f else None)


def _compiled(c: dict | None) -> str | None:
    """One side's compile state in words, for the sentences above."""
    if not c or not c.get("filter"):
        return None
    return f"`{c['filter']}`" + (f" ({c['reason']})" if c.get("reason") else "")


def _named(c: dict | None) -> str:
    """One side's composable names in words, for the sentence above."""
    if not c or not c.get("calls"):
        return "off"
    ran, _, total = (c.get("runs") or "").partition("/")
    if total and ran != total:
        return f"on in {ran} of {total} repeats"
    return "on"


def _sampled(s: dict | None) -> str:
    """One side's sampling in words, for the sentence above."""
    if not s or not s.get("started"):
        return "off"
    rate = f" at {s['hz']} Hz" if s.get("hz") else ""
    ran, _, total = (s.get("runs") or "").partition("/")
    if total and ran != total:
        return f"on in {ran} of {total} repeats{rate}"
    return f"on{rate}"


def _measured(env: dict) -> bool:
    """Whether a report's platform state holds any measurement at all.

    `analyze` always writes the block. A trace recorded without the sources —
    by an older echolot, or with `runner.environment: false` — comes back as
    `cpu`, `thermal` and `memory` all null and all three named in `missing`,
    which is a non-empty dict. The early return in `_environment_moved` used
    to test for an empty one, so two such reports — the one pair it was
    written to keep quiet — were told on every comparison that their clock
    could not be checked. Only a report written before the block existed
    carries none of it.
    """
    return any(env.get(key) for key in ("cpu", "thermal", "memory"))


def _params_moved(before: dict, after: dict) -> list[str]:
    """Detector parameters that differ, named one by one.

    Not a boolean: "thresholds differ" sends someone to diff two JSON files,
    while `main_thread_block.min_slice_ms 16 → 3089.5` explains the whole
    report on its own.
    """
    return [f"`{det_id}.{key}` {was} → {now}"
            for det_id, moved in params_moved_by(before, after).items()
            for key, was, now in moved]


def _defaults_moved(before: dict, after: dict) -> bool:
    """Whether `--defaults` on one side only changed what the two measured.

    The flag drops the config's thresholds and the detectors it turned off.
    A config with no `detectors:` section has neither, so a side read with
    `--defaults` ran the very thresholds the other did, and "read with
    different thresholds" was false on every check the answer page made
    after an agent's round (#820). What both reports ran is in them: each
    detector's `params`, and which detectors ran. Only a report written
    before `params` was kept leaves the flag as all there is to go by.
    """
    cb, ca = before.get("config") or {}, after.get("config") or {}
    if bool(cb.get("defaults")) == bool(ca.get("defaults")):
        return False
    dets = [*(before.get("detectors") or []), *(after.get("detectors") or [])]
    if not dets or any("params" not in d for d in dets):
        return True
    ran = [{d.get("id") for d in rep.get("detectors") or []} for rep in (before, after)]
    return bool(params_moved_by(before, after)) or ran[0] != ran[1]


def params_moved_by(before: dict, after: dict) -> dict[str, list[tuple[str, Any, Any]]]:
    """The parameters that differ, by detector: `(key, before, after)` each.
    `moves` says them of the one check they belong to (#713)."""
    # A failed side reports the shipped params, not the ones it would have
    # run with; set against the other side's calibrated ones, that read as a
    # threshold change nobody made.
    pb = {d["id"]: (d.get("params") or {}) for d in before.get("detectors", [])
          if not _failed(d)}
    pa = {d["id"]: (d.get("params") or {}) for d in after.get("detectors", [])
          if not _failed(d)}
    moved: dict[str, list[tuple[str, Any, Any]]] = {}
    for det_id in sorted(set(pb) & set(pa)):
        for key in sorted(set(pb[det_id]) | set(pa[det_id])):
            was, now = pb[det_id].get(key), pa[det_id].get(key)
            if was != now:
                moved.setdefault(det_id, []).append((key, was, now))
    return moved


def _side(report: dict, path: str) -> dict[str, Any]:
    cfg = report.get("config") or {}
    return {
        "path": path,
        "runs": _runs(report),
        "generated_at": report.get("generated_at"),
        "config_sha": cfg.get("sha"),
        "config_path": cfg.get("path"),
        "defaults": bool(cfg.get("defaults")),
    }


def _window(before: dict, after: dict, floor_ms: float = FLOOR_MS,
            floor_ratio: float = FLOOR_RATIO) -> dict[str, Any]:
    """The scenario's window on the two sides, tested as a row is (#532).

    The window is the number this tool exists for, and it was the one
    `compare` did not test: two medians and their difference, whatever the
    runs' own scatter. Now it gets a row's `change` against a row's floor,
    and a row's `shift` and `holds` from each run's window (`_judged_once`).

    `untested` names a side of more than one run whose report keeps no
    per-run windows: written before `window.values` existed. Its move
    stays the difference of the medians, and is said to be untested.
    """
    wb, wa = before.get("window") or {}, after.get("window") or {}
    b, a = wb.get("duration_ms"), wa.get("duration_ms")
    out: dict[str, Any] = {"before_ms": b, "after_ms": a,
                           "delta_ms": None, "ratio": None}
    if b is None or a is None:
        return out
    out.update(_judged_once(b, a, wb.get("values"), wa.get("values"),
                            floor_ms, floor_ratio))
    untested = [side for side, rep, w in (("before", before, wb), ("after", after, wa))
                if _runs(rep) > 1 and not w.get("values")]
    if untested:
        out["untested"] = untested
    return out


def _judged_once(b: float, a: float, vb: list[float] | None,
                 va: list[float] | None, floor_ms: float,
                 floor_ratio: float) -> dict[str, Any]:
    """One number of each side judged as a row is, held against zero.

    `change` is a row's, against a row's floor, and so are the move, its
    range and the runs it would take. `holds` asks the range to clear zero:
    the window, or the startup, is one number tested once, not one of two
    hundred rows tested at once, where chance alone holds a few. A floor
    there would be a tenth of the window, and a tenth of a five-second
    cold start is 490 ms: one app's six control sets of ten cold starts,
    against ten with a 400 ms sleep planted at startup, held the move
    against every control by zero and against none by that floor.
    """
    judged = _row("window", ("location",), _as_row(b, vb), _as_row(a, va),
                  "name", floor_ms, floor_ratio, metric="ms")
    shift = _shift(vb, va, judged["delta_ms"] or 0.0)
    return {"delta_ms": judged["delta_ms"], "ratio": judged["ratio"],
            "change": judged["change"], "shift": shift,
            # Off the rounded ends, as a row's is.
            "holds": None if shift is None else (
                shift["low_ms"] > 0 or shift["high_ms"] < 0)}


def _as_row(ms: float, values: list[float] | None) -> dict[str, Any]:
    """A side's window, or startup, or one reason of it, in the shape of a
    report's row: the median, and each run's value under `spread`."""
    row: dict[str, Any] = {"location": "window", "ms": ms}
    if values:
        row["spread"] = {"ms": {"min": min(values), "max": max(values),
                                "values": list(values)}}
    return row


def _startup(before: dict, after: dict, floor_ms: float = FLOOR_MS,
             floor_ratio: float = FLOOR_RATIO) -> dict[str, Any] | None:
    """The platform's startup on the two sides, tested as the window is (#532).

    Perfetto's own measure of the launch, from the intent to the first
    frame, and its split by reason: `bind_application`, `io`, `binder`,
    the time before the process ran. A set of fifteen cold starts against
    four of a later build moved the window by 7% and this by 35%, and
    `compare` never read it. Both sides need one: with a startup on one
    side only there is nothing to set it against, and the block is null.

    The reasons are rows of their own here, not of the detectors' table:
    the time they split is the same time the detectors' rows already
    count, `io` the disk waits and `binder` the calls, and the table's
    sums and the plain reading would count it twice. A dozen of them are
    judged at once, as rows are, so they go through `_row` and hold by
    the rows' rule; the startup as a whole is one number, as the window.
    """
    sb = (before.get("window") or {}).get("startup") or {}
    sa = (after.get("window") or {}).get("startup") or {}
    if sb.get("dur_ms") is None or sa.get("dur_ms") is None:
        return None
    out: dict[str, Any] = {
        "type": {"before": sb.get("type"), "after": sa.get("type")},
        "before_ms": sb["dur_ms"], "after_ms": sa["dur_ms"],
        **_judged_once(sb["dur_ms"], sa["dur_ms"], sb.get("values"), sa.get("values"),
                       floor_ms, floor_ratio),
    }
    untested = [side for side, rep, s in (("before", before, sb), ("after", after, sa))
                if _runs(rep) > 1 and not s.get("values")]
    if untested:
        out["untested"] = untested
    rb, ra = sb.get("reasons") or {}, sa.get("reasons") or {}
    vb, va = sb.get("reason_values") or {}, sa.get("reason_values") or {}
    reasons = []
    for name in [*rb, *(n for n in ra if n not in rb)]:
        r = _row("startup", ("location",),
                 _as_row(rb[name], vb.get(name)) if name in rb else None,
                 _as_row(ra[name], va.get(name)) if name in ra else None,
                 "name", floor_ms, floor_ratio, metric="ms")
        reasons.append({"reason": name,
                        "before_ms": rb.get(name), "after_ms": ra.get(name),
                        "delta_ms": r["delta_ms"], "ratio": r["ratio"],
                        "change": r["change"], "shift": r["shift"], "holds": r["holds"]})
    reasons.sort(key=lambda r: abs(r["delta_ms"] or 0.0), reverse=True)
    out["reasons"] = reasons
    return out


def _state(rows: list[dict]) -> str:
    return "silent" if not rows else f"{len(rows)} row(s)"


def _state_change(changed: list[dict[str, Any]], det_id: str, before: str, after: str) -> None:
    """A detector's state on the two sides, when it differs. "not checked" on
    both is the warning's to say, and was a row of this table that changed
    nothing in every comparison of two recordings alike (#716)."""
    if before != after:
        changed.append({"id": det_id, "before": before, "after": after})


def _failed(d: dict | None) -> bool:
    """A detector whose SQL failed and left nothing: `analyze` writes its
    error beside empty rows and the shipped params. One merged from repeats
    that failed only in some has rows from the others, and is compared."""
    return bool(d and d.get("error") and not d.get("rows"))


# --- pairing rows across two reports ---------------------------------------

# Detectors whose identity held `detail` until their evidence went there: the
# column that names a row in its place, and how to read it out of an older
# report's `detail`.
_FROM_DETAIL: dict[str, tuple[str, Any]] = {
    "runnable_starvation": ("state", lambda v: v.removeprefix("state ")),
    # `main thread` or `background`, and the thread's name: `detail` held
    # them as they are (#514).
    "binder_txn": ("thread", lambda v: v),
    "gc_pressure": ("thread", lambda v: v),
}


def _upgraded(d: dict, report: dict | None = None) -> dict:
    """A detector's block from an older report, read with the identity it has now.

    The main thread's row of `uninstrumented_cpu`, `runnable_starvation` and
    `monitor_contention` is `main` since #511. An older report names it as the
    kernel names the thread, the last fifteen characters of the process's
    name, and its rows carry no `threads`: read under the old name, the main
    thread's row would be gone on one side and new on the other. That report
    cannot tell the main thread from a worker that carried the same name, and
    it never could: its own plain reading took the name for the main thread.

    `runnable_starvation` named a row by `detail`, `state R` or `state R+`,
    until the main thread's evidence went into `detail`; the state is a column
    of its own since. Paired on what both sides carry, an older report against
    a newer one would be paired on `location` alone, and a thread's `R` row
    would meet its `R+` row: the mistake `_match` exists to prevent. So the
    older block gets its `state` read back out of `detail`. `binder_txn` and
    `gc_pressure` moved the same way, to `thread`, when their `detail` began
    to say who answered a call and what a collection freed (#514): an older
    report's thread is read back the same way, and so are the rows it kept
    under the bar.
    """
    d = _main_named(d, report)
    identity = identity_of(d)
    moved = _FROM_DETAIL.get(d.get("id"))
    if moved is None or moved[0] in identity or "detail" not in identity:
        return d
    column, read = moved

    def up(rows: list[dict]) -> list[dict]:
        return [{**row, column: read(str(row.get("detail") or ""))} for row in rows]

    out = {**d, "identity": [column if c == "detail" else c for c in identity],
           "rows": up(d.get("rows") or [])}
    below = d.get("below")
    if isinstance(below, dict) and isinstance(below.get("rows"), list):
        out["below"] = {**below, "rows": up(below["rows"])}
    return out


def _main_named(d: dict, report: dict | None) -> dict:
    """`_upgraded`'s first half: the main thread's row named `main`, among the
    rows and among what the side found under its bar (`below`, #715), where
    `_under_bar` looks a gone or new row up by the same name."""
    below = d.get("below")
    under = below.get("rows") if isinstance(below, dict) else None
    every = [*(d.get("rows") or []), *(under if isinstance(under, list) else [])]
    if (d.get("id") not in plain.NAMED_MAIN or not every
            or any(report_mod.THREADS in r for r in every)):
        return d
    kernel = plain.kernel_name(report or {})
    if not kernel:
        return d

    def named(rows: list[dict]) -> list[dict]:
        return [{**r, "location": plain.MAIN} if r.get("location") == kernel else r
                for r in rows]
    out = {**d, "rows": named(d.get("rows") or [])}
    if isinstance(under, list):
        out["below"] = {**below, "rows": named(under)}
    return out


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


def _family_key(row: dict, identity: tuple[str, ...],
                keep: str | None = None) -> tuple:
    """`keep` is the instrumentation prefix — see `names.across_builds`.

    Two markers the hunt planted are two different things by construction;
    folding `AGENTTMP_fill_v4` onto `AGENTTMP_fill_v6` would let one be
    reported as the other having moved.
    """
    return tuple(None if v is None else across_builds(str(v), keep=keep)
                 for v in _key(row, identity))


def _match(before_rows: list[dict], after_rows: list[dict],
           identity: tuple[str, ...], keep: str | None = None
           ) -> list[tuple[dict | None, dict | None, str]]:
    """Pairs rows of one detector, exactly first and by name family second.

    A row is named by every column in `identity`, never by `location` alone.
    Seven of the fifteen shipped detectors group by a second column —
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
        fam_before.setdefault(_family_key(rb, identity, keep), []).append(rb)
    fam_after: dict[tuple, list[dict]] = {}
    for ra in left_after:
        fam_after.setdefault(_family_key(ra, identity, keep), []).append(ra)

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


def _under_bar(pairs: list[tuple[dict | None, dict | None, str]],
               db: dict | None, da: dict | None, identity: tuple[str, ...],
               keep: str | None = None) -> list[tuple[dict | None, dict | None, str, str | None]]:
    """The pairs, with a row one side does not report looked up in what that
    side measured under its bar (`below`, see `report._merge_detector`), and the side
    it was found under the bar on.

    86 calls to the system of 83 ms together, after a fix that took each of
    them under 14.4 ms, were no row of `binder_txn`, and the 111 ms row of the
    round before read as gone, all of it saved (#715). Found under the bar,
    the row is compared with what it measured there. Not found, it was not
    there, or took under the side's `floor_ms` in every repeat, and is gone
    or new as before. A side with no `below`, a report written before it or
    one whose open query failed, cannot say, and its rows are what they were.
    """
    def under(d: dict | None) -> list[dict] | None:
        below = (d or {}).get("below")
        return below.get("rows") if isinstance(below, dict) and isinstance(below.get("rows"), list) else None

    below_b, below_a = under(db), under(da)
    gone = [rb for rb, ra, _ in pairs if ra is None and rb is not None]
    new = [ra for rb, ra, _ in pairs if rb is None and ra is not None]
    found_a = {id(rb): (ra, how) for rb, ra, how in _match(gone, below_a, identity, keep)
               if rb is not None and ra is not None} if gone and below_a else {}
    found_b = {id(ra): (rb, how) for rb, ra, how in _match(below_b, new, identity, keep)
               if rb is not None and ra is not None} if new and below_b else {}
    out: list[tuple[dict | None, dict | None, str, str | None]] = []
    for rb, ra, how in pairs:
        if ra is None and id(rb) in found_a:
            out.append((rb, *found_a[id(rb)], "after"))
        elif rb is None and id(ra) in found_b:
            other, by = found_b[id(ra)]
            out.append((other, ra, by, "before"))
        else:
            out.append((rb, ra, how, None))
    return out


def _row(det_id: str, identity: tuple[str, ...], rb: dict | None,
         ra: dict | None, how: str, floor_ms: float,
         floor_ratio: float, keep: str | None = None,
         metric: str | None = None) -> dict[str, Any]:
    metric = metric or metric_of(ra if ra is not None else rb)
    vb = (rb or {}).get(metric)
    va = (ra or {}).get(metric)
    loc, detail = _name(rb if rb is not None else ra, identity, how, keep)

    # How often the row was there is part of what moved. A median covers the
    # repeats a row was found in, so a row in 1 of 5 before and in 5 of 5
    # after, at the same milliseconds, read as steady though the work now
    # happens on every run. Where the two shares differ, each side's repeats
    # without the row count as zero, as `_merge_startup` counts a reason a
    # repeat lacked, and the move, its range and the runs it would take are
    # all read from those.
    shares = _shares(rb, ra)
    b_vals, a_vals = _values(rb, metric, shares), _values(ra, metric, shares)
    floor = 0.0
    if rb is None or ra is None:
        delta = (va or 0.0) if rb is None else -(vb or 0.0)
        ratio = None
        # A row one side does not have moved by its own size at most. Under
        # the floor that is no move, as it is no move between two rows found:
        # a 2.6 ms initializer the other side kept under its bar, or under
        # the 1 ms it keeps at all, read as gone or new (#843).
        if abs(delta) <= floor_ms:
            change, floor = STEADY, floor_ms
        else:
            change = APPEARED if rb is None else VANISHED
    else:
        mb, ma = vb or 0.0, va or 0.0
        if shares and b_vals and a_vals:
            mb, ma = median(b_vals), median(a_vals)
        delta = round(ma - mb, 2)
        ratio = round(ma / mb, 2) if mb else None
        floor = max(floor_ms, floor_ratio * mb)
        # Listed when it moves by more than the floor, as the legend and the
        # docs say: a move of exactly the floor is steady.
        if abs(delta) <= floor:
            change = STEADY
        else:
            change = GREW if delta > 0 else SHRANK

    shift = _shift(b_vals, a_vals, delta if rb and ra else 0.0, floor)
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
        "shift": shift,
        # Past the floor, not past zero (#506). A comparison judges two
        # hundred rows at once, and at 95% one in twenty that did not move
        # lands on one side of zero by chance: three sets of fifteen cold
        # starts of one build, each split seven against eight a hundred ways,
        # held 4.0 rows a split that way, 3.0 of them past the floor and
        # listed as moved. Asking
        # the range to clear the floor that listed them took that to 0.5
        # (1.3 five against five, from 5.8), while a row made twice as slow
        # still held in 88 splits of a hundred, against 91. Correcting the
        # 95% for the number of rows instead (Benjamini–Hochberg at 5%) took
        # it to 0.1 and the doubled row to 59, and to none at all five a
        # side: there the smallest p a row can reach, 0.008, is above what
        # sixty rows at once ask for. Read off the rounded ends and floor,
        # so the verdict and the numbers printed beside it cannot disagree.
        "holds": None if shift is None else (
            shift["low_ms"] > shift["floor_ms"]
            or shift["high_ms"] < -shift["floor_ms"]),
    }
    if detail is not None:
        row["detail"] = detail
    if shares:
        row["shares"] = shares
    return row


def _share(row: dict | None) -> tuple[int, int] | None:
    """`(k, n)` from a row's `runs`: found in k of n repeats."""
    try:
        k, n = (int(x) for x in str((row or {}).get("runs")).split("/"))
    except ValueError:
        return None
    return (k, n) if 0 < k <= n else None


def _shares(rb: dict | None, ra: dict | None) -> dict[str, str] | None:
    """Both sides' `runs`, when the share of repeats differs between them."""
    sb, sa = _share(rb), _share(ra)
    if sb is None or sa is None or sb[0] * sa[1] == sa[0] * sb[1]:
        return None
    return {"before": f"{sb[0]}/{sb[1]}", "after": f"{sa[0]}/{sa[1]}"}


def _values(row: dict | None, metric: str,
            shares: dict[str, str] | None) -> list[float] | None:
    """A side's per-repeat values; with `shares`, a zero for each repeat the
    row was not found in."""
    if row is None:
        return None
    values = ((row.get("spread") or {}).get(metric) or {}).get("values")
    if not values:
        return None
    share = _share(row) if shares else None
    return list(values) + [0.0] * (share[1] - share[0] if share else 0)


def _name(row: dict, identity: tuple[str, ...], how: str,
          keep: str | None = None) -> tuple[str, str | None]:
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
    if "detail" in identity:
        detail = row.get("detail")
    else:
        # A column of its own in the identity — `runnable_starvation`'s
        # `state`, `io_wait`'s `thread` — tells two rows of one location
        # apart the way `detail` does elsewhere, and is printed the same way.
        named = [f"{c} {row[c]}" for c in identity
                 if c != "location" and row.get(c) is not None]
        detail = " · ".join(named) or None
    if how == "family":
        loc = across_builds(loc, keep=keep)
        detail = None if detail is None else across_builds(str(detail), keep=keep)
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


def _shift(b: list[float] | None, a: list[float] | None,
           move: float, floor: float = 0.0) -> dict[str, Any] | None:
    """The move with the range it lies in, and what these runs could resolve.

    `stats.shift` pairs every run after with every run before; see there for
    why that replaced the ranges. `floor_ms` is the floor the range has to
    clear for the move to hold, the one that listed it. `resolves_ms` is how
    far the two sets' strays reach, in the direction this move went: the
    move holds when the medians moved further than the floor and that
    together. A move that does not hold gets `runs_needed`, about how many
    runs a side would settle it if the next ones stray as much as these did,
    and `more_runs`, the fewest more on each side, which with unequal sides
    may be the smaller one alone; null where more runs will not settle it.

    None where there is nothing to pair: a row on one side only, a side
    without per-run values, or too few runs to be 95% sure of anything.
    """
    if not b or not a:
        return None
    out: dict[str, Any] | None = stats.shift(b, a)
    if out is None:
        return None
    out["floor_ms"] = round(floor, 2) + 0.0
    out["resolves_ms"] = stats.resolves(b, a, upward=move >= 0)
    out["runs_needed"] = stats.runs_needed(len(b), len(a), out["resolves_ms"],
                                           move, out["floor_ms"])
    out["more_runs"] = stats.more_runs(len(b), len(a), out["resolves_ms"],
                                       move, out["floor_ms"])
    return out


# --- rendering --------------------------------------------------------------

CHANGE_LABEL = {APPEARED: "**new**", VANISHED: "**gone**"}

# The two tables this file prints, in the order they are read.
#
# `detail` sits next to the location because that is what it is for here: on a
# detector that groups by a second column, two rows share one location and the
# Evidence column is the only thing telling them apart. It is absent from the
# rows of every other detector, and `table.columns` leaves out a column no row
# carries — so the table grows this one exactly where it is needed.
MOVED_COLUMNS = ["location", "detail", "detector", "before", "after", "delta",
                 "count", "holds"]
MOVED_HEADERS = {"location": "Where", "detail": "Evidence",
                 "detector": "Detector", "before": "Before",
                 "after": "After", "delta": "Δ", "count": "N", "holds": "Holds"}
STATE_HEADERS = {"id": "detector", "before": "before", "after": "after"}


def to_markdown(cmp: dict[str, Any]) -> str:
    out: list[str] = ["# Comparison", ""]
    b, a = cmp["before"], cmp["after"]
    out.append(f"Before  `{b['path']}`  {b['runs']} run(s), {_when(b)}".rstrip(", "))
    out.append(f"After   `{a['path']}`  {a['runs']} run(s), {_when(a)}".rstrip(", "))
    out.append("")

    w = cmp["window"]
    if w["before_ms"] is not None and w["after_ms"] is not None:
        out.append(_moved_line("Scenario window", w))
    if cmp.get("startup"):
        out.append(_moved_line("Startup (platform)", cmp["startup"]))

    s = cmp["summary"]
    out.append(
        f"Moved **{s['moved']}** · appeared **{s['appeared']}** · "
        f"gone **{s['vanished']}** · steady {s['steady']}"
        + (f" · markers moved **{s['markers_moved']}**" if s.get("markers_moved") else "")
        + (f" · names only after **{s['only_after']}**" if s.get("only_after") else "")
        + (f" · only before **{s['only_before']}**" if s.get("only_before") else "")
    )
    out.append(
        f"Detectors fired: {len(s['fired_before'])} → {len(s['fired_after'])}"
    )
    against = _against_line(cmp)
    if against:
        out.append(against)
    out.append("")

    for warning in cmp["warnings"]:
        out.append(f"> ⚠️ {warning['text']}")
        out.append("")

    moved = [r for r in cmp["rows"] if r["change"] != STEADY]
    marked = [r for r in cmp.get("markers") or [] if _marker_moved(r)]
    if not moved and not marked:
        out.append("## Nothing moved")
        out.append("")
        out.append(
            f"Every row is within the floor "
            f"({cmp['noise_floor']['abs_ms']} ms or "
            f"{_pct(cmp['noise_floor']['ratio'])}). Either the change did "
            f"not land where these detectors look, or the two sets are the "
            f"same scenario in the same state."
        )
    elif moved:
        out.append("## What moved")
        out.append("")
        out.append(
            f"_by each detector's own measure; a row is listed when it moves by "
            f"more than {cmp['noise_floor']['abs_ms']} ms or "
            f"{_pct(cmp['noise_floor']['ratio'])}, whichever is larger. "
            f"± is the furthest a repeat got from the median. Holds gives the "
            f"range the move lies in, {stats.CONFIDENCE_PCT}% sure: yes when it "
            f"stays past that floor on one side, and a no whose range clears "
            f"zero alone names the floor._"
        )
        out.append("")
        out.append(_table(moved))
    out.append("")

    if marked:
        out.append("## Markers")
        out.append("")
        out.append(
            "_the report's markers table — the project's own sections, async "
            "ones included — paired by name: self time judged as a detector's "
            "rows are, and the calls per run (N) judged on their own in Calls._")
        out.append("")
        out.append(_markers_table(marked))
        out.append("")

    reasons = [r for r in (cmp.get("startup") or {}).get("reasons") or []
               if r["change"] != STEADY]
    if reasons:
        out.append("## Startup by reason")
        out.append("")
        out.append(
            "_the platform's startup split as Perfetto splits it, each reason "
            "judged as a row is. The same time the detectors' rows count, so "
            "not added to them._")
        out.append("")
        out.append(table.render(
            [{"reason": f"`{r['reason']}`",
              "before": "—" if r["before_ms"] is None else r["before_ms"],
              "after": "—" if r["after_ms"] is None else r["after_ms"],
              "delta": _delta(r),
              "holds": _holds(r)} for r in reasons],
            order=["reason", "before", "after", "delta", "holds"],
            headers={"reason": "Reason", "before": "Before", "after": "After",
                     "delta": "Δ", "holds": "Holds"}))
        out.append("")

    out.extend(_names_lines(cmp.get("names"), NAMES_SHOWN))

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

    quiet = s.get("silent_both") or []
    if quiet:
        out.append(f"**Silent in both:** {', '.join(quiet)}")
    return "\n".join(out).rstrip() + "\n"


def _moved_line(name: str, w: dict[str, Any]) -> str:
    """The window's line, or the startup's: the two medians, the move, and
    whether it holds, in the Holds column's words (#532).

    A side whose report keeps no per-run values gets the move without a
    verdict and says why; too few runs, or two reports that are not one
    thing, leave the verdict out and the warnings above say why.
    """
    line = f"{name}: **{w['before_ms']} → {w['after_ms']} ms**"
    kinds = w.get("type") or {}
    if kinds.get("before") != kinds.get("after"):
        line += f" ({kinds.get('before') or 'unknown'} → {kinds.get('after') or 'unknown'})"
    if w["delta_ms"] is not None:
        line += f"  ({w['delta_ms']:+.1f}"
        if w["ratio"]:
            line += f", ×{w['ratio']}"
        line += ")"
    if w.get("untested"):
        sides = " and ".join(w["untested"])
        line += (f" · untested: the {sides} report{'s' if len(w['untested']) > 1 else ''} "
                 f"keep{'' if len(w['untested']) > 1 else 's'} no per-run values; "
                 f"`analyze` the traces again to test the move")
    elif w.get("shift"):
        line += f" · holds: {_holds(w)}"
    return line


def _pct(ratio: float) -> str:
    """A ratio as a percentage, rounded: `int()` printed 0.29 as 28%."""
    return f"{round(ratio * 100, 4):g}%"


def _clears_zero(shift: dict[str, Any]) -> bool:
    return shift["low_ms"] > 0 or shift["high_ms"] < 0


def _against_line(cmp: dict[str, Any]) -> str | None:
    """The line under the header that says Holds asks for the floor (#506).

    How many listed rows hold, and how many more clear zero and no more:
    those held before the floor was asked for, and on one build set against
    itself they are most of what held. None when no listed row has a
    verdict, or for a comparison written before the floor was asked for.
    """
    if cmp.get("holds_against") != "floor":
        return None
    judged = [r for r in [*cmp["rows"], *(cmp.get("markers") or [])]
              if r["change"] in (GREW, SHRANK) and r.get("shift")
              and r.get("holds") is not None]
    if not judged:
        return None
    held = sum(1 for r in judged if r["holds"])
    short = sum(1 for r in judged if not r["holds"] and _clears_zero(r["shift"]))
    line = (f"Holds asks each range to clear the floor, not zero: "
            f"{held} of {len(judged)} row(s) with a verdict do")
    if short:
        line += f", and {short} more that clear zero alone read no"
    return line + "."


def _silent_both(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The detectors present on both sides, run on both, and silent on both.

    Built from the ids that fired, the line could never name one that fired
    on neither side, and it was never printed.
    """
    def quiet(rep: dict[str, Any]) -> set[str]:
        return {d["id"] for d in rep.get("detectors") or []
                if not d.get("rows") and not d.get("error")
                and d.get("checked") is not False}
    fired = set((before.get("summary") or {}).get("fired_ids") or []) \
        | set((after.get("summary") or {}).get("fired_ids") or [])
    return sorted((quiet(before) & quiet(after)) - fired)


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
                    + (" *(family)*" if r["matched_by"] == "family" else "")
                    + (" *(by total)*" if r.get("into_markers") else ""),
        **({"detail": r["detail"]} if r.get("detail") else {}),
        "detector": r["detector"],
        "before": _band(r["before"], r["metric"]) + _under(r, "before"),
        "after": _band(r["after"], r["metric"]) + _under(r, "after"),
        "delta": _delta(r),
        "count": _counts(r),
        "holds": _holds(r),
    } for r in rows], order=MOVED_COLUMNS, headers=MOVED_HEADERS)


def _under(r: dict[str, Any], side: str) -> str:
    """` *(under the bar)*` on the side a row was found under its detector's
    threshold, and not reported (#715)."""
    return " *(under the bar)*" if r.get("under_bar") == side else ""


MARKER_COLUMNS = ["location", "before", "after", "delta", "count", "holds", "calls"]
MARKER_HEADERS = {**MOVED_HEADERS, "calls": "Calls"}


def _markers_table(rows: list[dict[str, Any]]) -> str:
    """The markers that moved, in the moved table's columns, and their calls."""
    return table.render([{
        "location": r["location"]
                    + (" *(family)*" if r["matched_by"] == "family" else ""),
        "before": _band(r["before"], r["metric"]),
        "after": _band(r["after"], r["metric"]),
        "delta": _delta(r),
        "count": _counts(r),
        "holds": _holds(r),
        "calls": _calls_text(r.get("calls")),
    } for r in rows], order=MARKER_COLUMNS, headers=MARKER_HEADERS)


def _found_in(row: dict[str, Any]) -> int:
    """How many runs a `slice_names` row was found in: the k of its `k/n`."""
    try:
        return int(str(row.get("runs")).split("/")[0])
    except ValueError:
        return 0


NAMES_COLUMNS = ["name", "side", "runs", "total", "max", "threads"]
NAMES_HEADERS = {"name": "Where", "side": "Only", "runs": "Runs",
                 "total": "Total, ms", "max": "Longest, ms", "threads": "Thread"}


def _names_lines(names: dict[str, Any] | None, shown: int | None) -> list[str]:
    """The names on one side only: the section, or the line that says why
    there is none. Nothing when neither side has any, or a report holds no
    names to read. `shown` names of each side, every one with None."""
    if not names:
        return []
    if names.get("too_few"):
        runs = names["runs"]
        return [f"**Names on one side only:** not looked for with {runs['before']} "
                f"run(s) before and {runs['after']} after; it takes {names['least_runs']} "
                f"a side, as with fewer the names a run happens to have outnumber "
                f"new ones.", ""]
    after, before = names.get("after") or [], names.get("before") or []
    if not after and not before:
        return []
    out = ["## Names on one side only", "",
           f"_the slice names of the app's process in the window, numbers folded, that "
           f"are in every run of one side (all but one from {NAMES_ALL_BUT_ONE} runs) and "
           f"in no run of the other, {names['floor_ms']:g} ms or more a run, and that no "
           f"row above has as new or gone. Total is the median a run._", ""]
    picked, rest = [], []
    for side, rows in (("after", after), ("before", before)):
        cut = rows if shown is None else rows[:shown]
        picked += [(side, r) for r in cut]
        if len(rows) > len(cut):
            rest.append(f"{len(rows) - len(cut)} more {side}")
    out.append(table.render([{
        "name": r["name"],
        "side": side,
        "runs": f"{r['runs']} against {r['other']}",
        "total": f"{r['total_ms']:g}",
        "max": f"{r['max_ms']:g}",
        "threads": r.get("threads") or "",
    } for side, r in picked], order=NAMES_COLUMNS, headers=NAMES_HEADERS))
    if rest:
        out += ["", f"… and {' and '.join(rest)}: `compare --names` lists every one, "
                    f"and comparison.json has them under `names`."]
    out.append("")
    return out


def names_view(cmp: dict[str, Any]) -> str:
    """`compare --names`: every name on one side only, and nothing else."""
    lines = _names_lines(cmp.get("names"), None)
    if lines:
        return "\n".join(lines).rstrip() + "\n"
    if cmp.get("names") is None:
        return ("No names to compare: a report written before echolot kept the "
                "window's slice names holds none. Run `analyze` again on its traces.\n")
    return "No slice name is in every run of one side and in no run of the other.\n"


def _calls_text(calls: dict[str, Any] | None) -> str:
    """The verdict on a marker's calls per run, when they moved."""
    if not calls or not calls.get("moved"):
        return "—"
    text = f"{_int(calls['before'])} → {_int(calls['after'])}"
    shift = calls.get("shift")
    if shift is None:
        return text
    word = "yes" if calls["holds"] else "no"
    return f"{text}, {word}, {shift['low']:+g} … {shift['high']:+g}"


def _holds(r: dict[str, Any]) -> str:
    """The verdict first, then the range it was read from.

    The word is what a reader scanning the column needs; the range is what
    tells "at least 12 ms slower" from "at least 800". A `no` whose range is
    clear of zero names the floor it did not clear, so the two are not read
    as disagreeing (#506). A `no` adds what "record another round" leaves
    out: about how many runs would settle it — one more after, twenty a
    side, or more than a round of runs can give, which are different answers.
    """
    shift = r.get("shift")
    if shift is None:
        return "—"
    word = "yes" if r["holds"] else "no"
    text = f"{word}, {shift['low_ms']:+.1f} … {shift['high_ms']:+.1f}"
    if r["holds"]:
        return text
    if shift.get("floor_ms") and _clears_zero(shift):
        text += f" · floor {shift['floor_ms']:.1f}"
    return text + _runs_text(shift)


def _runs_text(shift: dict[str, Any]) -> str:
    """How many more runs would settle a row that does not hold.

    The smaller side alone when that is fewer runs in all; with fifteen
    before and four after, a row that needs seven a side needs one more
    after. A number past `stats.SETTLES_UP_TO` is no round of `collect`, and
    is said as that rather than printed.
    """
    needed = shift.get("runs_needed")
    if not needed:
        return ""
    more = shift.get("more_runs")
    if more is None:
        if "more_runs" in shift and needed > stats.SETTLES_UP_TO:
            return " · more runs will not settle it"
        return f" · ~{needed} runs a side"
    sides = [side for side in ("before", "after") if more[side]]
    if len(sides) == 1:
        return f" · ~{more[sides[0]]} more {sides[0]}"
    return f" · ~{needed} runs a side"


def _band(face: dict | None, metric: str) -> str:
    """The median with how far the repeats strayed from it, and in how many
    repeats the row was found when that was not all of them."""
    if face is None:
        return "—"
    value = face.get(metric)
    if value is None:
        return "—"
    text = f"{value}"
    if "min" in face and face["min"] != face["max"]:
        off = max(value - face["min"], face["max"] - value)
        text += f" ±{off:.0f}"
    share = _share(face)
    if share and share[0] < share[1]:
        text += f" ({share[0]}/{share[1]})"
    return text


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
    text = f"{_int(b)} → {_int(a)}"
    each = r.get("per_occurrence")
    if each:
        text += f" · {each['before']:g} → {each['after']:g} ms each"
    return text


def _int(v: Any) -> str:
    return "—" if v is None else f"{v:g}"


def _when(side: dict[str, Any]) -> str:
    """When `analyze` wrote that side's report, said as that (#585). The
    bare UTC stamp beside each side read as when it was recorded, in local
    time, and a reader set the two against each other."""
    at = when_mod.local(side.get("generated_at"))
    return f"report written {at}" if at else ""

