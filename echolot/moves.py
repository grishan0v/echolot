"""A comparison in plain words, for someone who has just changed their code.

`comparison.md` is a table for an agent and a terminal: the rows that grew,
shrank, appeared and vanished, with a Holds column. A person who pressed
"Check again" wants three answers in words, and a model writes none of them:
the same two reports give the same words every time.

  - a verdict by the scenario's window: better, worse, or no clear change, in
    a sentence with the two times and how many launches a side. A window
    that runs to the end of the recording is as long as the recording, and
    is judged by the share of frames the app made late (`FRAMES_FLOOR`);
  - notes under it, when they apply: whether the time limit is met
    (`scenario.budget_ms`), that the repeats are too few to say which single
    changes hold, and that the two sides were not recorded alike. The last
    two are `compare`'s own warnings, said for a person;
  - one item per check with which way it moved, and a sentence of what
    changed, in the words the report's plain reading has for that check.

A movement is not a level. On a report a colour says how much attention a
check needs (`attention`: needs_attention, worth_a_look, fine, not_checked).
Here it says which way a check moved (`movement`):

  better       what it found, added up, is clearly less
  worse        clearly more
  gone         it found something before and finds nothing now
  new          it found nothing before and finds something now
  no_change    it found something on a side, and no move can be called
  fine_both    it looked both times and found nothing
  not_checked  it could not look on one side or on both
  not_compared it looked both times, but not by one measure: its thresholds
               changed, the tool that read it did, or both sides are the
               same recordings, so whatever moved is not the app's (#713)

The two words are kept apart in the keys: a check's item carries `movement`,
and what each report said of it stays under `before` and `after` as
`attention`.

A check is judged by the sum of its rows, not by its largest one. A fix can
merge five rows under one section: the largest row grows while the check as a
whole shrinks, and judged by its largest row the fix reads as a regression.
The sum is of a typical launch: each row's median over every launch of that
side, a launch without the row counted as nothing, so a row seen in one
launch of three adds nothing. A check with rows on the main thread and on
others is judged by the main thread's alone when those moved and the sum did
not move their way (`_threads`).

When a move is called, and what that rule was measured on, is by `SHARE`,
`LAUNCH_SHARE` and `LEAST_RUNS` below.

`read` is the reading of two reports and the comparison built from them.
`annotate` puts it into the comparison under `plain`. `render` is the same
for a terminal: `echolot compare --plain`.
"""

from __future__ import annotations

import copy
import shutil
import textwrap
from statistics import median
from typing import Any

from . import compare, plain, stats
from .report import metric_of

BETTER, WORSE, NO_CHANGE, GONE, NEW, FINE_BOTH, NOT_CHECKED = (
    "better", "worse", "no_change", "gone", "new", "fine_both", "not_checked")
# The verdict's own fourth answer: two reports that are not of one thing. A
# check's too: one not measured alike on the two sides.
NOT_COMPARED = "not_compared"
MOVEMENTS = (WORSE, NEW, BETTER, GONE, NO_CHANGE, FINE_BOTH, NOT_CHECKED, NOT_COMPARED)

# When a move is called. The same three conditions for the window and for a
# check's sum:
#
#   - at least three launches a side;
#   - the two differ by a fifth of the larger one or more (and a check's sum
#     also by a twentieth of the launch, so that 30 ms in a two-second launch
#     is not news);
#   - the launches agree. For the window: more than half of the faster side's
#     launches beat the fastest launch of the slower side. For a check: the
#     most its sum can have been in any launch of the lower side is under the
#     least it can have been in any launch of the higher side.
#
# Measured on 190 recordings of one app on one phone, each analysed on its
# own and merged the way `analyze` merges repeats, under two configs (anchors
# with calibrated thresholds; a launch window with the shipped ones). Pairs of
# sets with nothing changed between them, three to seven launches a side:
#
#                        pairs   the verdict moved   a check moved
#   one session          1,300   0                   0.1 to 0.2% of checks
#   one day, sessions      139   0                   0.2 to 0.4%
#   two days                40   0                   0.7 to 0.8%
#
# "Of checks" counts the checks that found something on either side; at least
# one of them read as moved in 1% of the pairs of one session, 1 to 3% of one
# day and 5% of two days. A real fix (2.1 s to 1.4 s) reads as better, and the
# three checks it emptied or halved read as gone or better.
#
# Why a fifth: the same build recorded on two days differed by up to 18% with
# every launch of one day slower than every launch of the other, so 10%
# (`compare`'s floor for a row) called it a change in half of those pairs and
# 15% in a third. Why not the floor alone: on a check's sum it called a move
# in a quarter of the checks of an unchanged app; three of them wait for
# things outside the app and swing by half between launches. Why three: with
# one launch a side one slow launch in fourteen was enough to read as a
# change in a tenth of the pairs, whatever the floor. All of it is one app on
# one phone, and the first thing to check on another.
SHARE = 0.20
LAUNCH_SHARE = 0.05
LEAST_RUNS = 3

# A window that runs to the end of the recording is as long as the recording,
# and a scripted scroll or drag is recorded for a fixed time: five pairs of
# sets of one drag, a build that recomposed a list on every move of a panel
# against the build before it and the fix after it, all read "No clear
# change. The run took 14.9 s before and 14.9 s after", the window 8.6 ms
# apart at most (#586). What moved was the share of frames the app itself
# made late, 3% of them in the good build, 21% in the bad one and 10% after
# the fix. So such a window is judged by that share, run by run, with the
# window's own rule: a fifth of the larger share, and the better side's
# typical run under the worse side's best one. And by at least one frame in
# a hundred, so that 0.4% against 0.6% of the frames, a fifth apart and three
# frames in seven hundred, is not news.
FRAMES_FLOOR = 0.01

# `compare`'s warnings that say the two sides were recorded or read under
# different conditions, each as a person would say it. A time can move for
# any of these alone, so the reading names them beside the verdict.
_UNLIKE = {
    "anchor-before": "the scenario's start or end was not found in the recordings before",
    "anchor-after": "the scenario's start or end was not found in the recordings after",
    "config": "the config changed",
    "defaults": "one side was read with the built-in thresholds and the other with the config's",
    "thresholds": "the checks' thresholds differ",
    "detectors": "not the same checks ran",
    "environment": "the phone's speed differed or could not be checked",
    "environment-thermal": "the phone ran hot during one of them",
    "environment-load": "other apps kept the phone busy during one of them",
    "sampling": "stack sampling was not on the same way",
    "compose_names": "composable names were recorded on one side only",
    "compile": "the app was compiled differently",
    "environment-thermal-one-side": "the phone slowed down from heat on one side, and the "
                                    "other side's heat was not recorded",
    "recording": "the two sides were not recorded the same way",
    "recording-mixed": "one side's recordings were not all made the same way",
    "recording-unknown": "how one side was recorded is not known",
    "version": "different versions of echolot read them",
    "detector-changed": "some checks read the recordings differently on the two sides",
}


# ---------------------------------------------------------------------------
# reading the two reports


def _read(report: dict[str, Any]) -> dict[str, Any]:
    """The report with its plain reading. One written before those keys gets
    them worked out on a copy, as `plain.render` does."""
    if plain.is_read(report):
        return report
    return plain.annotate(copy.deepcopy(report))


def _runs(report: dict[str, Any]) -> int:
    n = report.get("runs")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return n
    return len(report.get("traces") or []) or 1


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _launches(row: dict[str, Any], runs: int) -> list[float]:
    """What one row measured in each launch of its side: its value where it
    was found, nothing where it was not."""
    metric = metric_of(row)
    value = _number(row.get(metric)) or 0.0
    try:
        k, n = (int(x) for x in str(row["runs"]).split("/"))
    except (KeyError, ValueError):
        k = n = runs
    k, n = max(0, min(k, n)), max(n, 1)
    kept = [v for v in ((row.get("spread") or {}).get(metric) or {}).get("values") or []
            if _number(v) is not None]
    found = [float(v) for v in kept][:k] if kept else [value] * k
    found += [value] * (k - len(found))
    return found + [0.0] * (n - k)


def _size(rows: list[dict[str, Any]], runs: int) -> tuple[float, float, float]:
    """A check's findings added up, for a typical launch, and the least and
    the most any one launch can have had."""
    size = low = high = 0.0
    for row in rows:
        values = _launches(row, runs)
        size += median(values)
        low += min(values)
        high += max(values)
    return round(size, 2), round(low, 2), round(high, 2)


def _side(det: dict[str, Any] | None, runs: int,
          under: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One side of a check: what that report said of it. `blind` is a side
    that says nothing about the check: it did not run there, could not read
    what it needs, failed, or the report is too old to say. `under` is what
    `compare` found of it under the bar on this side (`_under`): it adds to
    the sum, and is no finding."""
    if det is None:
        return {"attention": plain.NOT_CHECKED, "ms": None, "findings": 0, "found": None,
                "reason": "It did not run on this side.", "blind": True}
    rows = det.get("rows") or []
    words = det.get("plain") or {}
    # With rows from some of its repeats `compare` still leaves a check out
    # when the others could not read what it needs.
    partly = det.get("checked") is False and not det.get("error")
    out: dict[str, Any] = {"attention": det.get("attention"), "ms": None, "findings": len(rows),
                           "found": words.get("found"),
                           "reason": words.get("reason") or (det.get("reason") if partly else None),
                           "blind": det.get("attention") == plain.NOT_CHECKED or partly}
    if rows or under:
        out["ms"], out["low_ms"], out["high_ms"] = _size([*rows, *(under or [])], runs)
    return out


def _under(rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    """The rows `compare` found under the bar on one side (#715), as rows of
    that side's report: a time a launch, in the launches it was found in.
    86 calls of 83 ms together after a fix are 83 ms of the check's sum,
    where leaving them out counted the whole 111 ms before as saved."""
    out = []
    for r in rows:
        face = r.get(side)
        if r.get("under_bar") != side or not isinstance(face, dict):
            continue
        metric = r.get("metric") or "total_ms"
        value = _number(face.get(metric))
        if value is None:
            continue
        out.append({"location": r.get("location"), "_on_main": r.get("on_main"), metric: value,
                    "runs": face.get("runs"),
                    "spread": {metric: {"values": [v for v in face.get("values") or [] if _number(v) is not None]}}})
    return out


def _part(rows: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    """Some of a check's rows on one side, added up as `_side` adds up all of them."""
    if not rows:
        return {"ms": None}
    ms, low, high = _size(rows, runs)
    return {"ms": ms, "low_ms": low, "high_ms": high}


def _threads(did: str, db: dict[str, Any] | None, da: dict[str, Any] | None,
             ctx: "_Ctx", under: tuple[list[dict[str, Any]], list[dict[str, Any]]] = ([], [])
             ) -> dict[str, dict[str, dict[str, Any]]] | None:
    """A check's rows of the main thread and of the other threads, added up
    apart on each side; None unless it has rows of both.

    `monitor_contention` adds up every thread's waits. A 300 ms wait of the
    main thread on a monitor came with 4.3 s less waiting among the coroutine
    workers, and the check's sum read as better: the cause went under the
    knock-on it had on a pool (#595).
    """
    def split(det: dict[str, Any] | None, extra: list[dict[str, Any]], main: bool) -> list[dict[str, Any]]:
        return [r for r in (det or {}).get("rows") or [] if plain._on_main(did, r, ctx.whose) == main] + [
            r for r in extra if bool(r.get("_on_main")) == main]
    parts = {"main": (split(db, under[0], True), split(da, under[1], True)),
             "rest": (split(db, under[0], False), split(da, under[1], False))}
    if not any(parts["main"]) or not any(parts["rest"]):
        return None
    return {name: {"before": _part(b, ctx.nb), "after": _part(a, ctx.na)}
            for name, (b, a) in parts.items()}


def _texts(det: dict[str, Any] | None) -> tuple[str, str, str]:
    """What was checked, how it touches the app, what a clean result says."""
    det = det or {}
    words = det.get("plain") or {}
    title, why, clean = plain.fixed_texts(det.get("id", ""), det.get("title", ""))
    return words.get("title") or title, words.get("why") or why, words.get("clean") or clean


class _Ctx:
    """What a sentence needs to know about the pair it is read from."""

    def __init__(self, before: dict[str, Any], after: dict[str, Any]):
        ctx = plain._Ctx(after if (after.get("window") or {}).get("duration_ms") else before)
        self.noun, self.plural = ctx.noun, ctx.plural
        # Whose thread a row is of, as the plain reading of a report tells it.
        self.whose = ctx
        self.nb, self.na = _runs(before), _runs(after)
        wb, wa = before.get("window") or {}, after.get("window") or {}
        self.window_ms = max(_number(wb.get("duration_ms")) or 0.0,
                             _number(wa.get("duration_ms")) or 0.0)

    @property
    def enough(self) -> bool:
        return min(self.nb, self.na) >= LEAST_RUNS

    def counted(self, n: int) -> str:
        return f"one {self.noun}" if n == 1 else f"{n} {self.plural}"

    @property
    def a_side(self) -> str:
        """"3 launches before and 3 after", "one launch before and one after"."""
        if self.na == 1:
            return f"{self.counted(self.nb)} before and one after"
        return f"{self.counted(self.nb)} before and {self.na} after"


def _percent(was: float, now: float) -> str:
    """` 33% less`, ` 12% more`, or nothing for a move under one percent."""
    if was <= 0:
        return ""
    pct = round(abs(now - was) / was * 100)
    return f"{pct}% {'less' if now < was else 'more'}" if pct >= 1 else ""


# ---------------------------------------------------------------------------
# the verdict


def _window_side(report: dict[str, Any]) -> dict[str, Any]:
    w = report.get("window") or {}
    ms = _number(w.get("duration_ms"))
    low, high = _number(w.get("duration_ms_min")), _number(w.get("duration_ms_max"))
    return {"ms": ms, "low_ms": ms if low is None else low, "high_ms": ms if high is None else high,
            "runs": _runs(report)}


def _open(window: dict[str, Any]) -> bool:
    """Does the window run to the end of the recording, by what its report
    says: no end anchor, or one that missed, and no launch to close it? A
    window whose report names no anchors at all is not taken for one."""
    if not window or plain._closed(window):
        return False
    return "end_anchor" in window or plain._missed(window)


def _late_side(report: dict[str, Any]) -> dict[str, Any] | None:
    """The frames the app made late on one side, each run's share of its own
    frames. None without a frame timeline, or in a report written before
    they were counted."""
    frames = (report.get("window") or {}).get("frames") or {}
    count, late = _number(frames.get("count")), _number(frames.get("late"))
    if not count or late is None:
        return None
    shares = [n / of for n, of in zip(frames.get("late_values") or [], frames.get("values") or [], strict=False)
              if _number(n) is not None and _number(of)] or [late / count]
    return {"late": late, "count": count, "share": round(median(shares), 4),
            "low": round(min(shares), 4), "high": round(max(shares), 4), "runs": len(shares)}


def _no_frames(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Why a window that runs to the end of the recording cannot be judged by
    its late frames either."""
    def has(report: dict[str, Any], key: str) -> bool:
        return key in ((report.get("window") or {}).get("frames") or {})
    timeline = [side for side, rep in (("before", before), ("after", after))
                if "frames" in (rep.get("window") or {}) and not has(rep, "count")]
    if timeline:
        where = "either side" if len(timeline) == 2 else f"the {timeline[0]} side"
        return f"There is no frame timeline on {where}, so no late frames to judge it by: read the checks below."
    return ("The frames the app made late, which judge such a window, are counted only in reports written by "
            "a newer echolot: analyse the recordings again.")


def _share_of(share: float) -> str:
    """`21%`, `3.2%`, or `none` of the frames."""
    pct = share * 100
    if pct == 0:
        return "none"
    return f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%".replace(".0%", "%")


def _whole(n: float) -> str:
    return f"{n:.0f}" if n == int(n) else f"{n:.1f}"


def _by_frames(b: dict[str, Any], a: dict[str, Any], ctx: _Ctx) -> tuple[str, str]:
    """Whether the share of late frames moved, by the window's rule (see
    `FRAMES_FLOOR`), and the sentence of why not when it did not."""
    if min(b["runs"], a["runs"]) < LEAST_RUNS:
        nb, na = b["runs"], a["runs"]
        if max(nb, na) == 1:
            return NO_CHANGE, f"One {ctx.noun} a side cannot tell a change from a hiccup: it takes {LEAST_RUNS} a side."
        return NO_CHANGE, (f"{ctx.counted(nb)[0].upper() + ctx.counted(nb)[1:]} before and {na} after are too few "
                           f"to tell a change from a hiccup: it takes {LEAST_RUNS} a side.")
    lower, higher = (a, b) if a["share"] < b["share"] else (b, a)
    gap = higher["share"] - lower["share"]
    if gap < max(FRAMES_FLOOR, SHARE * higher["share"]):
        alike = _share_of(b["share"]) == _share_of(a["share"])
        return NO_CHANGE, "" if alike else (f"A change under {SHARE:.0%}, or under one frame in a hundred, "
                                            "is not counted.")
    if not lower["share"] < higher["low"]:
        return NO_CHANGE, (f"That is more than {SHARE:.0%}, but single {ctx.plural} of the two sides overlap, "
                           "so it is not counted as a change.")
    return (BETTER if a["share"] < b["share"] else WORSE), ""


def _by_window(b: dict[str, Any], a: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    """Better, worse or no clear change, by the window's length: the
    movement, the headline, what the window did (`took`) and why that is not
    a change (`why`). `few` when the launches are too few for any."""
    was, now = plain.hum(b["ms"]), plain.hum(a["ms"])
    pct = _percent(b["ms"], a["ms"])
    fast, slow = (a, b) if a["ms"] < b["ms"] else (b, a)
    far = abs(a["ms"] - b["ms"]) >= SHARE * slow["ms"]
    # More than half of the faster side's launches beat the fastest of the slower.
    agree = fast["ms"] < slow["low_ms"]
    if ctx.enough and far and agree:
        faster = a["ms"] < b["ms"]
        gap = plain.hum(abs(a["ms"] - b["ms"]))
        return {"movement": BETTER if faster else WORSE,
                "headline": f"{'Faster' if faster else 'Slower'} by {gap}",
                "took": f"The {ctx.noun} went from {was} to {now}, {pct}.", "why": "", "few": False}
    # Two times that read alike are said without the few percent between them:
    # "1.1 s before and 1.1 s after, 4% more" asks a reader which of the two to believe.
    alike = was == now
    took = f"The {ctx.noun} took {was} before and {now} after" + (f", {pct}" if pct and not alike else "") + "."
    if not ctx.enough:
        few = (f"One {ctx.noun} a side cannot tell a change from a hiccup"
               if max(ctx.nb, ctx.na) == 1 else
               f"{ctx.a_side[0].upper() + ctx.a_side[1:]} are too few to tell a change from a hiccup")
        # The reason has the launches a side in it: they are not said twice.
        return {"movement": NO_CHANGE, "headline": "No clear change", "took": took,
                "why": f"{few}: it takes {LEAST_RUNS} a side.", "few": True}
    if alike:
        why = ""
    elif not far:
        why = (f"The same app differs by that much between two rounds of recordings, so a change under "
               f"{SHARE:.0%} is not counted.")
    else:
        why = (f"That is more than {SHARE:.0%}, but single {ctx.plural} of the two sides overlap, so it is "
               "not counted as a change.")
    return {"movement": NO_CHANGE, "headline": "No clear change", "took": took, "why": why, "few": False}


def _said(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _same(before: dict[str, Any], after: dict[str, Any], cmp: dict[str, Any]) -> str | None:
    """Why the two sides are one set of recordings, when `compare` says so."""
    if not any(w.get("id") == "same-traces" for w in cmp.get("warnings") or []):
        return None
    return compare.same_recordings(before, after) or "the same recordings"


def _threshold(key: str, was: Any, now: Any) -> str:
    """`min_slice_ms from 83.3 to 16 ms`."""
    def said(v: Any) -> str:
        return "nothing" if v is None else f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
    unit = " ms" if key.endswith("_ms") and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in (was, now)) else ""
    return f"{key} from {said(was)} to {said(now)}{unit}"


def _held(before: dict[str, Any], after: dict[str, Any], cmp: dict[str, Any]) -> dict[str, str] | str | None:
    """Why a check is not compared, as a sentence: one for every check (a
    string), one per check that is not (a dict), or None.

    The same rows `compare` takes the verdict from (`compare.unjudged`), and
    the checks whose own thresholds differ: a threshold of 83.3 ms against
    16 ms made 14 more findings of the same recordings, and read as worse.
    """
    if _same(before, after, cmp):
        return ("Both sides are the same recordings, so whatever moved here is the tool's or the "
                "config's, not the app's.")
    unjudged = compare.unjudged(before, after)
    if unjudged is compare.ALL:
        return ("One of the two reports does not say which version of echolot wrote it, so a move here "
                "may be the tool's.")
    out: dict[str, list[str]] = {}
    for did in unjudged:
        out.setdefault(did, []).append(
            "The two versions of echolot that wrote the reports read this check differently, so a move "
            "here may be the tool's.")
    for did, moved in compare.params_moved_by(before, after).items():
        said = "; ".join(_threshold(*m) for m in moved)
        out.setdefault(did, []).append(
            f"Its thresholds changed between the two ({said}), so it did not count by the same measure "
            "on both sides.")
    return {did: " ".join(words) for did, words in out.items()} or None


def _verdict(before: dict[str, Any], after: dict[str, Any], cmp: dict[str, Any],
             ctx: _Ctx) -> dict[str, Any]:
    """Better, worse or no clear change, by the scenario's window; by the
    frames the app made late when the window runs to the end of the
    recording and so cannot move (`FRAMES_FLOOR`). Not compared at all when
    both sides are one set of recordings: nothing in the app moved (#713)."""
    b, a = _window_side(before), _window_side(after)
    out: dict[str, Any] = {"movement": NOT_COMPARED, "before_ms": b["ms"], "after_ms": a["ms"]}
    if not cmp.get("comparable", True):
        why = [w["id"] for w in cmp.get("warnings") or [] if w.get("id") in ("process", "window")]
        if "process" in why:
            wb, wa = before.get("window") or {}, after.get("window") or {}
            said = (f"The first report is of `{wb.get('process')}` and the second of `{wa.get('process')}`: "
                    "two apps, so nothing here is a change in one.")
        else:
            said = ("The two reports do not measure the same stretch of the app, so every time differs "
                    "for that reason alone.")
        return {**out, "headline": "These two cannot be compared", "text": said}
    if not b["ms"] or not a["ms"]:
        return {**out, "headline": "No time to compare",
                "text": f"One of the two reports has no measured {ctx.noun}, so there are no two times to "
                        "hold against each other."}
    same = _same(before, after, cmp)
    if same:
        return {**out, "headline": "The same recordings on both sides",
                "text": f"Both sides are the same recordings: {same}. Nothing in the app can have moved "
                        "between them, so no check below is called better or worse: what moved is the "
                        "tool's or the config's."}
    measured = f"Measured on {ctx.a_side}."
    w = _by_window(b, a, ctx)
    out["by"] = "window"
    by_window = {**out, "movement": w["movement"], "headline": w["headline"],
                 "text": _said(w["took"], w["why"], "" if w["few"] else measured)}
    if not (_open(before.get("window") or {}) or _open(after.get("window") or {})):
        return by_window
    # The window runs to the end of the recording: its length is the
    # recording's, which a script may fix whatever the app does.
    fb, fa = _late_side(before), _late_side(after)
    moved = w["movement"] != NO_CHANGE
    if fb is None or fa is None:
        if moved or w["few"]:
            return by_window
        return {**by_window, "text": _said(
            w["took"][:-1] + ", but the window runs to the end of the recording, so its length may be the "
            "recording's and not the app's.", _no_frames(before, after), measured)}
    out["frames"] = {"before": fb, "after": fa}
    f, why = _by_frames(fb, fa, ctx)
    late = (f"The app made {_share_of(fb['share'])} of its frames late before and {_share_of(fa['share'])} "
            f"after: {_whole(fb['late'])} of {_whole(fb['count'])} frames a {ctx.noun}, then "
            f"{_whole(fa['late'])} of {_whole(fa['count'])}.")
    if moved:
        if f in (NO_CHANGE, w["movement"]):
            return {**by_window, "frames": out["frames"], "text": _said(w["took"], late, measured)}
        return {**by_window, "frames": out["frames"], "movement": NO_CHANGE,
                "headline": f"{'Faster' if w['movement'] == BETTER else 'Slower'}, but "
                            f"{'more' if f == WORSE else 'fewer'} frames came late",
                "text": _said(w["took"], late, "One reads as better and the other as worse, so neither is "
                                                "called a change.", measured)}
    length = (w["took"][:-1] + ": the window runs to the end of the recording, so its length is the "
              "recording's and not the app's.")
    headline = {BETTER: "Fewer frames came late", WORSE: "More frames came late"}.get(f, "No clear change")
    few = min(fb["runs"], fa["runs"]) < LEAST_RUNS
    return {**out, "by": "frames", "movement": f, "headline": headline,
            "text": _said(late, why, length, "" if few else measured)}


# ---------------------------------------------------------------------------
# the notes under it


def _notes(before: dict[str, Any], after: dict[str, Any], cmp: dict[str, Any],
           ctx: _Ctx) -> list[dict[str, str]]:
    """What follows the verdict, in the order a reader needs it.

    Launches too few for any move to be called come first: that note is why
    the verdict and every check below it say no change, and a reader who
    pressed the button twice on the same code has to meet it before anything
    else. Launches that are enough for a verdict and too few for `compare`'s
    Holds column are a smaller thing, and come after the limit.
    """
    out = []
    if not ctx.enough:
        said = ctx.a_side[0].upper() + ctx.a_side[1:]
        out.append({"id": "few_runs", "attention": plain.WORTH_A_LOOK,
                    "title": f"Too few {ctx.plural} to tell.",
                    "text": f"{said} cannot tell a change from a hiccup, so nothing here is called one. "
                            f"Record {LEAST_RUNS} {ctx.plural} or more a side for that, and four a side, or three "
                            "against five, to be sure which single changes hold."})
    now, was = after.get("verdict") or {}, before.get("verdict") or {}
    ratio = _number(now.get("ratio"))
    if ratio is not None:
        if ratio > 1:
            still = (_number(was.get("ratio")) or 0) > 1
            title = "Still over the limit." if still else "Over the limit."
        else:
            title = "Within the limit."
        # The level and the sentence are the report's own, of the launch as it is now.
        out.append({"id": "budget", "attention": now.get("level") or plain.NOT_CHECKED,
                    "title": title, "text": str(now.get("text") or "")})
    if ctx.enough and stats.cut(ctx.nb, ctx.na) is None:
        out.append({"id": "few_runs", "attention": plain.WORTH_A_LOOK,
                    "title": "Not certain check by check yet.",
                    "text": f"{ctx.a_side[0].upper() + ctx.a_side[1:]} are too few to say which single changes "
                            "hold. It takes four a side, or three against five."})
    # The warnings' ids go with the note, for a reader that is not a person:
    # the GitHub Action's `unlike` output is this list (#535).
    unlike, ids = [], []
    for warning in cmp.get("warnings") or []:
        said = _UNLIKE.get(warning.get("id"))
        if said is None:
            continue
        if warning["id"] not in ids:
            ids.append(warning["id"])
        if said not in unlike:
            unlike.append(said)
    if unlike and cmp.get("comparable", True):
        things = "one thing" if len(unlike) == 1 else f"{len(unlike)} things"
        out.append({"id": "unlike", "attention": plain.WORTH_A_LOOK,
                    "title": "Not recorded alike.",
                    "text": f"Besides the app, {things} differed between the two: {'; '.join(unlike)}. "
                            "A time can move for that reason alone. The technical comparison says more.",
                    "warnings": ids})
    named = _one_side_note(cmp.get("names") or {}, ctx)
    if named is not None:
        out.append(named)
    return out


# How many names of each side the note below says by name.
NAMES_SAID = 3


def _one_side_note(names: dict[str, Any], ctx: _Ctx) -> dict[str, str] | None:
    """The names in every launch of one side and in none of the other (#610).

    No check reads them, so no check below moves for them, and they are
    often the one sign of what the change did: a method compiled only once
    it became hot names the code it is in.
    """
    parts = []
    for side, other in (("after", "before"), ("before", "after")):
        rows = names.get(side) or []
        if not rows:
            continue
        said = ", ".join(f"`{r['name']}` ({plain.hum(r['total_ms'])} a {ctx.noun}"
                         + (f", {r['threads']}" if r.get("threads") else "") + ")"
                         for r in rows[:NAMES_SAID])
        more = f", and {len(rows) - NAMES_SAID} more" if len(rows) > NAMES_SAID else ""
        parts.append(f"In every {ctx.noun} {side} and in none {other}: {said}{more}.")
    if not parts:
        return None
    return {"id": "names", "attention": plain.WORTH_A_LOOK,
            "title": "Work only one side has.",
            "text": " ".join(parts) + " No check reads these, so no check moves for them; "
                                      "`echolot compare --names` lists them all."}


# ---------------------------------------------------------------------------
# one check


def _moved(b: dict[str, Any], a: dict[str, Any], ctx: _Ctx) -> tuple[bool, str]:
    """Whether a check's sum moved, and when not, why not: `few` launches, a
    `share` of itself or of the `launch` that is too small, or launches that
    `overlap`. A side with nothing found is nothing in every launch."""
    bm, am = b["ms"] or 0.0, a["ms"] or 0.0
    if not ctx.enough:
        return False, "few"
    gap = abs(am - bm)
    if gap < max(compare.FLOOR_MS, SHARE * max(am, bm)):
        return False, "share"
    if gap < LAUNCH_SHARE * ctx.window_ms:
        return False, "launch"
    lower, higher = (a, b) if am < bm else (b, a)
    if not lower.get("high_ms", 0.0) < higher.get("low_ms", 0.0):
        return False, "overlap"
    return True, ""


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _findings(rows: list[dict[str, Any]]) -> dict[str, int]:
    gone = sum(1 for r in rows if r["change"] == compare.VANISHED)
    new = sum(1 for r in rows if r["change"] == compare.APPEARED)
    return {"gone": gone, "new": new, "kept": len(rows) - gone - new}


def _turnover(found: dict[str, int]) -> str:
    """` 5 of its findings are gone, 1 is new.`, or nothing when none came or went."""
    gone, new = found["gone"], found["new"]
    if gone and new:
        return f" {gone} of its findings {'is' if gone == 1 else 'are'} gone, {new} {'is' if new == 1 else 'are'} new."
    if gone:
        return f" {gone} of its findings {'is' if gone == 1 else 'are'} gone."
    if new:
        return f" {_counted(new, 'finding is', 'findings are')} new."
    return ""


def _amount(side: dict[str, Any]) -> str:
    """A side's sum as a sentence has it. Findings that add up to nothing are
    ones no typical launch has: each was in a minority of the launches."""
    if side["ms"] is None:
        return "nothing"
    return plain.hum(side["ms"]) if side["ms"] > 0 else "next to nothing"


def _at_times(b: dict[str, Any], a: dict[str, Any], ctx: _Ctx) -> str:
    """` What it finds now is not in every launch.`, for a sum of nothing."""
    before, now = b["ms"] == 0, a["ms"] == 0
    if before and now:
        return f" What it found, before and now, was not in every {ctx.noun}."
    if before:
        return f" What it found before was not in every {ctx.noun}."
    if now:
        return f" What it finds now is not in every {ctx.noun}."
    return ""


def _sentence(movement: str, why: str, b: dict[str, Any], a: dict[str, Any],
              found: dict[str, int], clean: str, ctx: _Ctx) -> str:
    """What changed, in a sentence or two."""
    if movement == FINE_BOTH:
        return clean
    if movement == NOT_COMPARED:
        return f"{why} Added up, what it found was {_amount(b)} before and {_amount(a)} after."
    if movement == NOT_CHECKED:
        when = "either time" if b["blind"] and a["blind"] else "after" if a["blind"] else "before"
        reason = (a["reason"] if a["blind"] else None) or b["reason"] or plain._COULD_NOT
        return f"Not checked {when}, so there is nothing to compare. {reason}"
    if movement == GONE:
        return f"This check finds nothing now. Before: {b['found']}"
    if movement == NEW:
        return f"This check found nothing before. Now: {a['found']}"
    if movement in (BETTER, WORSE):
        return (f"Added up, what this check found went from {_amount(b)} to {_amount(a)}."
                + _turnover(found) + _at_times(b, a, ctx))
    # No move that can be called, and the reason in words.
    short = f"{_few(ctx)} to call that a change." if why == "few" else None
    if b["ms"] is None or a["ms"] is None:
        had, lead = ((b, "This check finds nothing now. Before it found") if a["ms"] is None else
                     (a, "This check found nothing before. Now it finds"))
        if short:
            return f"{lead} {_amount(had)}. {short}"
        if had["ms"] == 0:
            return f"{lead} something, but not in every {ctx.noun}, so that is not counted as a change."
        return f"{lead} {_amount(had)}. That is too small, or in too few of the {ctx.plural}, to call a change."
    lead = f"Added up, what this check found was {_amount(b)} before and {_amount(a)} after."
    if short:
        return f"{lead} {short}"
    if why == "overlap":
        return (f"{lead} Single {ctx.plural} of the two sides overlap, so it is not counted as a change."
                + _at_times(b, a, ctx))
    said = ""
    if _amount(b) != _amount(a):
        said = (f" A change under {SHARE:.0%} is not counted." if why == "share" else
                f" That is under {LAUNCH_SHARE:.0%} of the {ctx.noun}, which is not counted.")
    return lead + said + _turnover(found) + _at_times(b, a, ctx)


def _few(ctx: _Ctx) -> str:
    if max(ctx.nb, ctx.na) == 1:
        return f"One {ctx.noun} a side is too few"
    return f"{ctx.a_side[0].upper() + ctx.a_side[1:]} are too few"


def _check(did: str, db: dict[str, Any] | None, da: dict[str, Any] | None,
           rows: list[dict[str, Any]], ctx: _Ctx, held: str | None = None) -> dict[str, Any]:
    """`held` is why the check is not compared, though it looked both times."""
    under = (_under(rows, "before"), _under(rows, "after"))
    b, a = _side(db, ctx.nb, under[0]), _side(da, ctx.na, under[1])
    title, why_it_matters, clean = _texts(da if da is not None else db)
    found = _findings(rows)
    why = ""
    if b["blind"] or a["blind"]:
        movement = NOT_CHECKED
    elif b["ms"] is None and a["ms"] is None:
        movement = FINE_BOTH
    elif held:
        movement, why = NOT_COMPARED, held
    else:
        moved, why = _moved(b, a, ctx)
        if not moved:
            movement = NO_CHANGE
        elif (a["ms"] or 0.0) < (b["ms"] or 0.0):
            movement = GONE if a["ms"] is None else BETTER
        else:
            movement = NEW if b["ms"] is None else WORSE
    text = _sentence(movement, why, b, a, found, clean, ctx)
    main = None
    parts = _threads(did, db, da, ctx, under) if movement not in (NOT_CHECKED, FINE_BOTH, NOT_COMPARED) else None
    if parts:
        mb, ma = parts["main"]["before"], parts["main"]["after"]
        if _moved(mb, ma, ctx)[0]:
            way = BETTER if (ma["ms"] or 0.0) < (mb["ms"] or 0.0) else WORSE
            # The whole check's way, gone a kind of better and new of worse.
            if {BETTER: BETTER, GONE: BETTER, WORSE: WORSE, NEW: WORSE}.get(movement) != way:
                rb, ra = parts["rest"]["before"], parts["rest"]["after"]
                movement, main = way, {"before": mb["ms"], "after": ma["ms"]}
                text = (f"On the main thread, what this check found went from {_amount(mb)} to "
                        f"{_amount(ma)}. On the other threads, added up, it went from {_amount(rb)} to "
                        f"{_amount(ra)}.")
    for side in (b, a):
        for key in ("low_ms", "high_ms", "blind"):
            side.pop(key, None)
    out = {"id": did, "title": title, "why": why_it_matters, "movement": movement, "text": text,
           "before": b, "after": a, "findings": found}
    if main:
        out["main"] = main
    return out


# The order of a list: what got worse, what got better, what did not move,
# and the checks with nothing to say. Gone is a kind of better, new of worse.
_RANK = {WORSE: 0, NEW: 0, BETTER: 1, GONE: 1, NO_CHANGE: 2, NOT_COMPARED: 2, FINE_BOTH: 3, NOT_CHECKED: 4}


def _order(check: dict[str, Any], place: int) -> tuple:
    """Within a kind, the largest move first; then as the reports list them.
    A check judged by its main thread's rows moved by as much as they did."""
    sides = check.get("main") or {k: check[k]["ms"] for k in ("before", "after")}
    b, a = sides["before"] or 0.0, sides["after"] or 0.0
    return _RANK[check["movement"]], -abs(a - b), -max(a, b), place


# ---------------------------------------------------------------------------
# the reading


def read(before: dict[str, Any], after: dict[str, Any], cmp: dict[str, Any]) -> dict[str, Any]:
    """The plain reading of a comparison: `cmp` is `compare.build` of the two
    reports. Nothing here reads a clock, so the same two reports give the same
    reading."""
    before, after = _read(before), _read(after)
    ctx = _Ctx(before, after)
    verdict = _verdict(before, after, cmp, ctx)
    budget = _number((after.get("verdict") or {}).get("budget_ms"))
    out: dict[str, Any] = {
        # What the two reports are of, as they name it: a page's heading.
        "scenario": after.get("scenario") or before.get("scenario"),
        "process": (after.get("window") or {}).get("process") or (before.get("window") or {}).get("process"),
        "verdict": verdict,
        "window": {"noun": ctx.noun, "before": _window_side(before), "after": _window_side(after),
                   "budget_ms": budget},
        "notes": _notes(before, after, cmp, ctx),
        "checks": [],
    }
    if not cmp.get("comparable", True):
        # Two apps, or two stretches: no check of one is a change in the other.
        return out
    by_before = {d.get("id"): d for d in before.get("detectors") or []}
    by_after = {d.get("id"): d for d in after.get("detectors") or []}
    by_check: dict[Any, list[dict[str, Any]]] = {}
    for row in cmp.get("rows") or []:
        by_check.setdefault(row.get("detector"), []).append(row)
    held = _held(before, after, cmp)
    checks = []
    for place, did in enumerate([*by_before, *(k for k in by_after if k not in by_before)]):
        why = held if isinstance(held, str) or held is None else held.get(str(did))
        check = _check(str(did), by_before.get(did), by_after.get(did), by_check.get(did, []), ctx, why)
        checks.append((_order(check, place), check))
    out["checks"] = [check for _, check in sorted(checks, key=lambda pair: pair[0])]
    return out


def annotate(cmp: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Writes the reading into a comparison, in place, under `plain`."""
    cmp["plain"] = read(before, after, cmp)
    return cmp


# ---------------------------------------------------------------------------
# for a terminal

LABEL = {BETTER: "BETTER", WORSE: "WORSE", NO_CHANGE: "NO CHANGE", GONE: "GONE", NEW: "NEW",
         FINE_BOTH: "FINE BOTH TIMES", NOT_CHECKED: "NOT CHECKED", NOT_COMPARED: "NOT COMPARED"}
_NOTE_MARK = {plain.NEEDS_ATTENTION: "!", plain.WORTH_A_LOOK: "~", plain.FINE: "ok"}


def render(reading: dict[str, Any], width: int | None = None) -> str:
    """The reading as a list a person reads: the verdict, its notes, the
    checks that found something on either side, and the rest as a count."""
    width = width or max(60, min(100, shutil.get_terminal_size((100, 24)).columns))

    def wrap(text: str, first: str, rest: str) -> list[str]:
        return textwrap.wrap(text, width=width, initial_indent=first,
                             subsequent_indent=rest) or [first.rstrip()]

    v = reading["verdict"]
    label = LABEL.get(v["movement"]) or str(v["movement"]).upper().replace("_", " ")
    lines = wrap(f"{v['headline']}. {v['text']}", f"{label:<14}", " " * 14)
    if reading["notes"]:
        lines.append("")
    for note in reading["notes"]:
        mark = _NOTE_MARK.get(note["attention"], "-")
        lines += wrap(f"{note['title']} {note['text']}", f" {mark:<2} ", "    ")
    listed = [c for c in reading["checks"] if c["movement"] not in (FINE_BOTH, NOT_CHECKED)]
    if listed:
        lines.append("")
    for c in listed:
        lines.append(f" {LABEL[c['movement']]:<10} {c['title']}")
        lines += wrap(c["text"], " " * 12, " " * 12)
    fine = sum(1 for c in reading["checks"] if c["movement"] == FINE_BOTH)
    blind = sum(1 for c in reading["checks"] if c["movement"] == NOT_CHECKED)
    parts = []
    if fine:
        parts.append(f"ok {fine} {'check is' if fine == 1 else 'checks are'} fine both times")
    if blind:
        parts.append(f"-  {blind} could not be checked on one side or both")
    if parts:
        lines += ["", " " + "     ".join(parts)]
    return "\n".join(lines)
