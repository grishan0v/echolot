"""The report as a page a person reads: `report.html`, beside `report.json`.

`report.md` is a table for an agent and a terminal. This is the same report
for someone who has never opened a trace: a verdict, then one row per check
with what it looked at, what it found and how that touches the app, each
marked by how much attention it needs. Nothing technical is in a row; opening
one shows that check's measurements in a panel. Ticking rows and pressing
"Dig deeper" puts together the request for an agent, to copy.

Everything said about a check is the report's own: `plain`, `attention`,
`checked` and `reason` per detector, `verdict` at the top (`echolot/plain.py`).
The page adds labels and nothing else, and judges nothing: no level is worked
out here, except that a report written before those keys existed is read the
way `echolot report --plain` reads it. The technical report under the fold is
report.md itself, as HTML.

One file, nothing fetched, and the same report gives the same bytes: no clock
is read and nothing is numbered at random.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Sequence

from .. import launch as launch_mod
from .. import plain
from .. import report as report_mod
from ..tp import load_detectors
from . import kit, md
from .kit import e
from ..files import runs as _runs
from ..plain import found_in as _seen
from ..plain import with_reading as _read
from ..words import is_number as _is_number

CSS = ("base.css", "table.css", "picks.css", "report.css")
JS = ("base.js", "picks.js", "table.js")

# How many rows of one check go into the request for an agent, and how much of
# a row's evidence: the agent reads the rest from the report itself.
REQUEST_ROWS = 4
REQUEST_DETAIL = 120
# How much of a row's evidence the panel shows under its name. All of it is in
# the technical report under the fold.
PANEL_DETAIL = 160

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_FIRST_SENTENCE = re.compile(r"(.+?[.!?])\s+(.+)", re.S)


# ---------------------------------------------------------------------------
# reading the report


def _value(row: dict[str, Any]) -> float:
    try:
        return float(row.get(report_mod.metric_of(row)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _number(value: Any) -> str:
    """A number as the report holds it, without the `.0` of a whole float."""
    text = str(value)
    return text[:-2] if isinstance(value, float) and text.endswith(".0") else text


# ---------------------------------------------------------------------------
# a check: one detector's entry, as the page shows it


def _checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The detectors as rows of the table, the ones that need the reader first:
    by level, then by the largest thing found, then as the report lists them."""
    out = []
    for i, det in enumerate(report.get("detectors") or []):
        words = det.get("plain") or {}
        level = det.get("attention")
        did = str(det.get("id") or "")
        # In the report's order, which a report this version did not write
        # may not keep: a row in every repeat before a longer one seen once.
        rows = sorted(det.get("rows") or [], key=report_mod.standing, reverse=True)
        said = words.get("found") or (words.get("clean") if level == plain.FINE else words.get("reason")) or ""
        kind = kit.tone(level)
        out.append({
            "det": det, "id": did, "rows": rows, "level": level, "kind": kind,
            # An id is a file name and safe in a selector. One that is not, from
            # a report this version did not write, is replaced by its place.
            "key": did if _SAFE_ID.fullmatch(did) else f"check-{i}",
            "title": words.get("title") or det.get("title") or did,
            "why": words.get("why") or "",
            "said": said,
            # What can be ticked: a check that found something.
            "hot": bool(rows) and kind in ("red", "yellow"),
            "order": (kit.TONES.index(kind), -max((_value(r) for r in rows), default=0.0), i),
        })
    return sorted(out, key=lambda c: c["order"])


# The detectors whose one threshold is a floor under the very column a row is
# drawn by: a row exists only at or above it, so it can be drawn on the row's
# axis as "where the check fires". Read from the headers the package ships: a
# detector says so by calibrating exactly one threshold, from one of the two
# columns a row is judged by (`report.metric_of`). One with two thresholds
# fires on either (`binder_txn`: one long call, or many short ones), and one
# that is calibrated from another column (`main_thread_outlier`: the longest
# single occurrence) has its bar on another measure; a line on this axis would
# be wrong for both.
#
# A check of that shape which `calibrate` leaves alone is named here instead:
# `main_thread_block`'s bar is a frame, and a bar from a healthy tail
# silenced 100 ms blocks (#509). Its line is still drawn.
_UNCALIBRATED_FLOORS = {"main_thread_block": ("min_slice_ms", "self_ms")}


@functools.lru_cache(maxsize=1)
def _floors() -> dict[str, tuple[str, str]]:
    out = dict(_UNCALIBRATED_FLOORS)
    for d in load_detectors(plain.DETECTOR_DIR):
        if len(d.calibrations) == 1 and d.calibrations[0].column in ("self_ms", "total_ms"):
            out[d.id] = (d.calibrations[0].param, d.calibrations[0].column)
    return out


def _fires_at(det: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, float, str] | None:
    """`(param, value, column)` when this check's threshold is on the measure
    its rows are drawn by, else None."""
    floor = _floors().get(str(det.get("id")))
    if not floor or not rows:
        return None
    param, column = floor
    value = (det.get("params") or {}).get(param)
    if not _is_number(value) or value <= 0:
        return None
    if any(report_mod.metric_of(r) != column for r in rows):
        return None
    return param, float(value), column


_SOURCE = {"config": "set in the config", "scenario": "set for this scenario", "cli": "set with --set"}
_OF = {"self_ms": " of self time", "total_ms": " in total"}


def _thresholds(det: dict[str, Any], fires: tuple[str, float, str] | None) -> str:
    """A check's thresholds in one line: each number, where it came from, and
    what the shipped one is when it was moved. A pattern is not a threshold
    and is left to the technical report."""
    defaults = det.get("defaults") or {}
    bits = []
    for name, value in (det.get("params") or {}).items():
        if not _is_number(value):
            continue
        if fires and name == fires[0]:
            text = f"fires at {_number(value)} ms{_OF.get(fires[2], '')} ({name})"
        else:
            text = f"{name} {_number(value)}"
        if _is_number(defaults.get(name)) and defaults[name] != value:
            text += f", built-in is {_number(defaults[name])}"
        bits.append(text)
    if not bits:
        return "no threshold"
    source = [_SOURCE.get(part, part) for part in str(det.get("params_source") or "default").split("+")
              if part != "default"]
    return " · ".join(bits + [" and ".join(source) if source else "built-in"])


def _mark(row: dict[str, Any], ctx: Any, subs: list[str], several: bool) -> tuple[dict[str, Any], list[float]]:
    """One row of the report as a row of a chart: the bar to its value (the
    median over the recordings), a dot for each recording it was found in."""
    metric = report_mod.metric_of(row)
    value = _value(row)
    name = str(row.get("location")) if row.get("location") not in (None, "") else "(no name)"
    spread = (row.get("spread") or {}).get(metric) or {}
    dots = [float(v) for v in spread.get("values") or [] if _is_number(v)] if several else []
    seen = _seen(row)
    notes, tip = [], [name]
    if several:
        if dots and min(dots) != max(dots):
            notes.append(f"{kit.ms(min(dots))} – {kit.ms(max(dots))}")
        tip.append(f"median: {kit.exact(value)} ms")
        if dots:
            tip.append(f"each {ctx.noun}: " + " · ".join(kit.exact(v) for v in dots))
        if seen:
            notes.append(f"{seen[0]} of {seen[1]} {ctx.plural}")
            tip.append(f"found in {seen[0]} of {seen[1]} {ctx.plural}")
    else:
        notes.append(f"one {ctx.noun}")
        tip.append(f"{kit.exact(value)} ms")
    if _is_number(row.get("count")):
        tip.append(f"occurrences: {row['count']:,}")
    if _is_number(row.get("max_ms")):
        tip.append(f"longest single one{f' in any {ctx.noun}' if several else ''}: "
                   f"{kit.exact(row['max_ms'])} ms")
    # A pool's workers share one cut name, and the bar is their time together
    # (#511).
    threads = row.get(report_mod.THREADS)
    if isinstance(threads, int) and threads > 1:
        notes.append(f"{threads} threads together")
        tip.append(f"threads added up: {threads}")
    mark = {"name": name, "subs": subs, "value": value, "dots": dots,
            "figure": f"{kit.ms(value)} ms", "notes": notes, "tip": tip}
    return mark, [value, *dots]


# The same under a name another page can call: the answer draws the rows a
# cause cites the way the report draws its own.
chart_row = _mark


def _measurements(c: dict[str, Any], ctx: Any, several: bool) -> str:
    """The technical layer of one check: its name in echolot, its thresholds,
    its rows as bars. For a check with no rows, that it has none and why."""
    det, rows = c["det"], c["rows"]
    fires = _fires_at(det, rows)
    out = [f'<p class="small sub"><code>{e(c["id"])}</code> {e(det.get("title") or "")}<br>{e(_thresholds(det, fires))}</p>']
    if not rows:
        why = "found nothing above its limit" if c["level"] == plain.FINE else "could not run"
        out.append(f'<p class="small sub">No rows: the check {why}.</p>')
        if det.get("error"):
            out.append(f'<p class="small sub">The error: <code>{e(det["error"])}</code></p>')
        return "".join(out)
    marks, values = [], [fires[1]] if fires else []
    for row in rows:
        mark, seen = _mark(row, ctx, [row.get("code"), report_mod.clip(row.get("detail"), PANEL_DETAIL)], several)
        marks.append(mark)
        values += seen
    out.append(kit.chart(marks, kit.nice_top(max(values)), mark=fires[1] if fires else None,
                         unit="ms, median" if several else "ms"))
    out.append(kit.chart_key("median" if several else f"this {ctx.noun}",
                             f"each {ctx.noun}" if several else None,
                             "where the check fires" if fires else None))
    # Rows from a part of the recordings: the rest could not be read, or failed.
    if det.get("checked") is False and det.get("reason"):
        out.append(f'<p class="small sub">{kit.prose(det["reason"])}</p>')
    part = report_mod.short_runs(det.get("failed_runs"))
    if part:
        out.append(f'<p class="small sub">Failed in {part[0]} of {part[1]} repeats: {e(det.get("error"))}. '
                   'The rows are from the others.</p>')
    return "".join(out)


def _request_lines(c: dict[str, Any]) -> str:
    """What goes to the agent for one check: its first rows in the report's
    order, each as the report names it, one to a line."""
    out = []
    for row in c["rows"][:REQUEST_ROWS]:
        figure = f"{_number(row.get(report_mod.metric_of(row)))} ms"
        if row.get("runs"):
            figure += f", {row['runs']} runs"
        bits = [str(row.get("location")), c["id"], figure]
        if row.get("detail"):
            bits.append(report_mod.clip(row["detail"], REQUEST_DETAIL))
        out.append("- " + " · ".join(bits))
    more = len(c["rows"]) - REQUEST_ROWS
    if more > 0:
        out.append(f"- and {more} more of {c['id']}")
    return "\n".join(out)


def _weight(c: dict[str, Any]) -> float:
    """How large the largest thing a check found is: what "the largest"
    means when the page suggests where to start."""
    return max((_value(r) for r in c["rows"]), default=0.0)


def _row(c: dict[str, Any]) -> str:
    tick = kit.tick_box(c["key"], c["kind"], c["title"], _request_lines(c), weight=_weight(c)) if c["hot"] else ""
    return (f'<div class="tr{"" if c["hot"] else " quiet"}" role="row" data-id="{e(c["key"])}" data-group="{c["kind"]}">'
            f'<div class="c-ck" role="cell">{tick}</div>'
            f'<div class="c-t" role="cell"><h3 class="tr-t">{e(c["title"])}</h3><p class="tr-f">{kit.prose(c["said"])}</p></div>'
            f'<div class="c-w" role="cell">{e(c["why"])}</div>'
            f'<div class="c-l" role="cell">{kit.pill(c["level"])}</div>'
            f'<div class="c-go" role="cell">{kit.details_btn(c["key"], c["title"])}</div></div>')


def _panel(c: dict[str, Any], ctx: Any, several: bool) -> str:
    """What the panel shows for a check: the row's own sentences again, the
    same tick, and under a hairline its measurements."""
    tick = ""
    if c["hot"]:
        tick = (f'<div class="tickrow">{kit.tick_box(c["key"], c["kind"], c["title"], _request_lines(c), twin=True, weight=_weight(c))}'
                '<span>Tick this check</span></div>')
    quiet = "" if c["hot"] else ' class="sub"'
    said = f'<p{quiet}>{kit.prose(c["said"])}</p>' if c["said"] else ""
    why = f'<p class="small sub">{e(c["why"])}</p>' if c["why"] else ""
    return kit.template(c["key"], f'<div>{kit.pill(c["level"])}</div><h2 class="h2" id="peek-title">{e(c["title"])}</h2>'
                                  f'{said}{why}{tick}{kit.deep(_measurements(c, ctx, several))}')


# ---------------------------------------------------------------------------
# the blocks of the page, top to bottom


def _verdict(report: dict[str, Any], ctx: Any) -> str:
    """The large pill with the scenario's name beside it, the first sentence of
    the verdict as the headline, the rest under it with how it was measured."""
    v = report.get("verdict") or {}
    text = str(v.get("text") or "").strip()
    m = _FIRST_SENTENCE.fullmatch(text)
    headline, rest = (m.group(1), m.group(2)) if m else (text, "")
    warning = v.get("warning")
    # How the window was measured is a range of recordings' lengths when the
    # window is the recording, and says nothing of the scenario.
    rest = " ".join(part for part in (rest, "" if warning else _measured(report, ctx)) if part)
    scenario = f'<span class="small sub">{e(report["scenario"])}</span>' if report.get("scenario") else ""
    # Above the headline, where a person reads first (#557): a window that is
    # not the scenario's makes every number under it about something else.
    first = (f'<div class="callout" id="window-warning" role="note">{kit.icon("red")}'
             f'<p>{kit.prose(warning)}</p></div>' if warning else "")
    return (f'<div class="stack gap-3">{first}<div class="rowf">{kit.verdict_pill(v.get("level"), large=True)}{scenario}</div>'
            f'<h1 class="h1">{e(headline.rstrip("."))}</h1>'
            + (f'<p class="lead">{e(rest)}</p>' if rest else "") + _notes(report) + "</div>")


def window_notes(report: dict[str, Any]) -> list[str]:
    """What the CLI says of the window beside its length, in its own words:
    repeats that diverged several-fold, and a scenario with no start or end
    anchor, whose window runs to the edge of what the process did. The
    lines `report.md` writes, from the functions that write them; the page
    had neither (#927)."""
    w = report.get("window") or {}
    try:
        lines = report_mod._diverged_lines(w) + report_mod._window_warning_lines(w)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return []
    return [re.sub(r"^>\s*(?:⚠️\s*)?", "", line).replace("**", "") for line in lines]


def _notes(report: dict[str, Any]) -> str:
    notes = window_notes(report)
    if not notes:
        return ""
    said = "".join(f"<p>{kit.prose(n)}</p>" for n in notes)
    return f'<div class="callout" id="window-notes" role="note">{kit.icon("yellow")}<div class="stack gap-2">{said}</div></div>'


def _measured(report: dict[str, Any], ctx: Any) -> str:
    """How many recordings the window was measured on, and their range."""
    w = report.get("window") or {}
    if not w.get("duration_ms"):
        return ""
    n = _runs(report)
    if n == 1:
        return f"Measured on one {ctx.noun}."
    low, high = w.get("duration_ms_min"), w.get("duration_ms_max")
    if not _is_number(low) or not _is_number(high):
        return f"Measured on {n} {ctx.plural}."
    a, b = plain.hum_apart(low, high)
    # "from 1.9 to 2.0 s": the unit is said once when both have the same one.
    for unit in (" ms", " s"):
        if a.endswith(unit) and b.endswith(unit):
            a = a[:-len(unit)]
            break
    return f"Measured on {n} {ctx.plural}, from {a} to {b}."


_NO_SCRIPT = ("<noscript><div class=\"callout fig\"><p><b>Script is switched off in this browser.</b> Every check, "
              "what it found and how that touches the app are in the table below, and the measurements are in "
              "the full technical report at the end. The filter by level, the panel with one check's "
              "measurements and the request for an agent need script.</p></div></noscript>")


def _table(checks: list[dict[str, Any]], report: dict[str, Any], ctx: Any) -> str:
    total = len(checks)
    count = {k: sum(1 for c in checks if c["kind"] == k) for k in kit.TONES}
    # One filter per colour. Grey holds the checks that could not run and the
    # markers to move, and its name says both when both are there.
    grey = (kit.word(plain.NOT_CHECKED) if not any(c["level"] == plain.MOVE_MARKER for c in checks)
            else "Not checked, or a marker to move")
    named = {"red": kit.word(plain.NEEDS_ATTENTION), "yellow": kit.word(plain.WORTH_A_LOOK),
             "green": kit.word(plain.FINE), "grey": grey}
    views = kit.views([("all", "", "All", total)]
                      + [(k, kit.icon(k), named[k], count[k]) for k in kit.TONES])
    said = (f"{total} {'check' if total == 1 else 'checks'}, the ones that need you first. "
            "Each says what it looked at, why that matters to the app, and what it found.")
    off = len((report.get("summary") or {}).get("absent_ids") or [])
    if off:
        said += f" {off} more {'check is' if off == 1 else 'checks are'} switched off in the config."
    head = kit.head([("c-ck", "Tick", False), ("c-t", "Check", True), ("c-w", "How it touches the app", True),
                     ("c-l", "Level", True), ("c-go", "Details", False)])
    rows = "".join(_row(c) for c in checks) or '<p class="tb-none">No check ran on these recordings.</p>'
    return (f'<section class="stack gap-3"><div class="stack gap-2"><h2 class="h2">What echolot checked</h2>'
            f'<p class="sub measure">{e(said)}</p></div>'
            f'<div>{views}<div class="tbw"><div class="tb report" role="table" aria-label="What echolot checked">'
            f'{head}<div role="rowgroup">{rows}</div></div></div></div>{_rule(ctx.noun)}</section>')


def _rule(noun: str) -> str:
    """How a level is chosen, in a sentence each, with the numbers `plain` holds."""
    share, floor = f"{plain.SHARE:.0%}", f"{plain.FLOOR_MS / 1000:g} s"
    return kit.fold("How the colours are chosen", kit.quiet_list([
        ("red", kit.WORD[plain.NEEDS_ATTENTION],
         f"One thing the main thread spent or lost holds at least {share} of the {noun} and at least {floor}, in every {noun}."),
        ("yellow", kit.WORD[plain.WORTH_A_LOOK],
         f"The check found something else: smaller, on another thread, or not in every {noun}."),
        ("grey", kit.WORD[plain.MOVE_MARKER],
         "The check found only a marker put one level off: advice on where it sits, not a finding."),
        ("green", kit.WORD[plain.FINE], "The check ran and found nothing above its limit."),
        ("grey", kit.WORD[plain.NOT_CHECKED],
         "The check could not run on these recordings. Green always means looked at and fine."),
    ]))


def where_to_start(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The checks to start from, as the button under the table ticks them:
    the ones that need attention, or with none of those the one or two that
    found the largest thing. One investigation answers one question, and
    every yellow at once is no question.

    Of more than `kit.FOCUS` that need attention, the `kit.FOCUS` that found
    the largest thing: the dialog says more is too much for one question,
    and the page suggested every red, four of them, and then told the reader
    who ticked them to tick fewer (#932)."""
    hot = [c for c in checks if c["hot"]]
    red = [c for c in hot if c["kind"] == "red"]
    return sorted(red or hot, key=lambda c: -_weight(c))[:kit.FOCUS if red else 2]


def _start_words(checks: list[dict[str, Any]]) -> tuple[str, str]:
    """Which checks the page suggests, in words: for the sentence under the
    table, and for the button that ticks them."""
    hot = [c for c in checks if c["hot"]]
    red = [c for c in hot if c["kind"] == "red"]
    if len(red) == 1:
        return "the one that needs attention", "Tick the one that needs attention"
    if len(red) > kit.FOCUS:
        return (f"the {kit.FOCUS} largest of the {len(red)} that need attention",
                f"Tick the {kit.FOCUS} largest that need attention")
    if red:
        return f"the {len(red)} that need attention", "Tick the ones that need attention"
    if len(hot) == 1:
        return "the one that is worth a look", "Tick the one worth a look"
    return "the two largest", "Tick the two largest"


def _next_step(checks: list[dict[str, Any]]) -> str:
    """What to do with the rows that found something: tick where to start,
    dig deeper."""
    hot = [c for c in checks if c["hot"]]
    if not hot:
        return ""
    kind = "red" if any(c["kind"] == "red" for c in hot) else "yellow"
    which, _ = _start_words(checks)
    text = (f"<b>Not sure where to start?</b> Tick {e(which)} and press <b>Dig deeper</b>. One investigation "
            "answers one question: an agent takes it into your code and comes back with the exact places and "
            "what to change.")
    return f'<div class="callout fig needs-js">{kit.icon(kind)}<p>{text}</p></div>'


# The request's first words: the command that hunts, in an agent that has
# echolot's layer. The blocks after the question are named in `kit`.
REQUEST_LEAD = "/echolot-hunt "
WARN_HEAD, ROWS_HEAD, FOCUS, MANY_NOTE = kit.WARN_HEAD, kit.ROWS_HEAD, kit.FOCUS, kit.MANY_NOTE


def not_found(report: dict[str, Any], who: str = "it") -> tuple[str, bool] | None:
    """What of the scenario's window echolot did not find, as both questions
    say it, from the facts `plain.window_warning` reads: "where it starts in
    1 of 5 recordings", "where it ends". True beside it when every part was
    missed in some recordings only, so that the others measured the
    scenario. None for a window that is the scenario's.

    The screen before Dig deeper said "has no measured window" where the
    start was missed in 1 of 5, and the investigation it opened asked about
    "the 545 ms startup takes", a time the report does not judge (#861)."""
    if not plain.window_warning(report):
        return None
    window = report.get("window") or {}
    said, some = [], True
    for key, part in (("start_anchor", "starts"), ("end_anchor", "ends")):
        anchor = window.get(key)
        if not isinstance(anchor, dict):
            continue
        seen = plain._of(anchor.get("missed"))
        if not anchor.get("matches") and anchor.get("before_start"):
            said.append(f"where {who} ends")
            some = False
        elif seen and seen[0] < seen[1]:
            said.append(f"where {who} {part} in {seen[0]} of {seen[1]} recordings")
        elif seen or not anchor.get("matches"):
            said.append(f"where {who} {part}")
            some = False
        elif anchor.get("unfinished"):
            said.append(f"where {who} ends")
            some = False
    said = list(dict.fromkeys(said))
    if not said:
        return f"where {who} starts or ends", False
    return " or ".join(said), some


def question(report: dict[str, Any]) -> str:
    """The one question an investigation answers, in words: how long the
    scenario takes, against its limit when one is set, and what to find."""
    report = _read(report)
    w = report.get("window") or {}
    name = f"`{report['scenario']}`" if report.get("scenario") else "The window"
    window = w.get("duration_ms")
    missed = not_found(report, name)
    if missed is not None:
        # No time of the scenario's: the report judges none (#861).
        words, some = missed
        if some:
            return (f"echolot did not find {words}. In those the rows below are about everything the app did in "
                    "the recording, and they are part of the numbers. Find what is behind the rows, and where "
                    "that is in the code.")
        return (f"{name} has no measured window: echolot did not find {not_found(report)[0]}, so the rows below are about "
                "everything the app did in the recording. Find what is behind them, and where that is in the code.")
    if not (_is_number(window) and window > 0):
        return f"{name} has no measured window. Find what is behind the rows below, and where that is in the code."
    ctx = plain._Ctx(report)
    n = _runs(report)
    if launch_mod.is_whole(w):
        # No scenario's length, and no limit to hold it against: the
        # recording is as long as somebody let it run (#519).
        took = f"The whole trace runs {window:,.0f} ms" + (
            f" (the median of {n} recordings)" if n > 1 else "")
        return (f"{took}, and no scenario cuts it. Find what is behind the rows below, "
                "and where that is in the code.")
    took = f"{name} takes {window:,.0f} ms" + (f", the median of {n} {ctx.plural}," if n > 1 else "")
    budget = (report.get("verdict") or {}).get("budget_ms")
    if _is_number(budget) and budget > 0 and window > budget:
        return (f"{took} against a time limit of {budget:,.0f} ms. Find what makes it slower than the limit, "
                "and where that is in the code.")
    if _is_number(budget) and budget > 0:
        return (f"{took} and stays within its time limit of {budget:,.0f} ms. Find what is behind the rows below, "
                "and where that is in the code.")
    return f"{took} and has no time limit set. Find what is behind the rows below, and where that is in the code."


def warnings(report: dict[str, Any]) -> list[str]:
    """What the report warns of at its top, in its own words: the lines
    `report.md` marks with ⚠️ above the first check. A missed anchor first:
    it says whether the window is the scenario's at all."""
    try:
        written = report_mod.to_markdown(report)
    except (KeyError, TypeError, AttributeError):
        return []
    found = []
    for line in written.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("> ⚠️"):
            found.append(line[len("> ⚠️"):].strip())
    return sorted(found, key=lambda said: 0 if " anchor `" in said else 1)


def _request(report: dict[str, Any], where: str | None, last: bool) -> tuple[str, list[str], str]:
    """The request's blocks that do not depend on what is ticked: the
    question, the report's warnings, and the last line, which names the
    report and its traces and says after which change. The rows between
    them are the ticked checks' own."""
    n = _runs(report)
    traces = "the one" if n == 1 else f"the {n}"
    which = " (the last report)" if last and where else ""
    tail = f"Traces: {traces} behind {where or 'that report'}{which}. After which change: unknown."
    return REQUEST_LEAD + question(report), warnings(_read(report)), tail


def request_text(report: dict[str, Any], picked: Sequence[dict[str, Any]], where: str | None = None,
                 last: bool = True) -> str:
    """The request for the ticked checks, put together as the page's script
    puts it together: the same blocks in the same order."""
    lead, warned, tail = _request(report, where, last)
    rows = [line for c in picked for line in _request_lines(c).split("\n")]
    blocks = [lead]
    if warned:
        blocks.append("\n".join([WARN_HEAD, *(f"- {said}" for said in warned)]))
    blocks.append("\n".join([ROWS_HEAD, *rows]))
    blocks.append(tail)
    return "\n\n".join(blocks)


def checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The checks of a report as the page lists them, for whoever matches
    what was ticked against the report: each with its `key`, `hot`, `kind`,
    `title` and rows."""
    return _checks(_read(report))


# The stretch of a launch window before the app's process existed, as the
# budget line of report.md calls it.
_BEFORE_PROCESS = "before the process started"


def _technical(report: dict[str, Any], ctx: Any, several: bool) -> str:
    """The fold at the end: where the main thread was, every row of every
    detector on one axis, and report.md as it is written."""
    parts = []
    unit = "ms, median" if several else "ms"
    budget = (report.get("window") or {}).get("main_thread") or {}
    window = budget.get("window_ms") or 0
    if window:
        states = [(label, float(budget.get(key) or 0)) for key, label in report_mod.BUDGET_LABELS]
        states = [(label, v) for label, v in states if v / window * 100 >= 1]
        marks = [{"name": label, "mono": False, "value": v, "figure": f"{kit.ms(v)} ms",
                  "notes": [f"{v / window * 100:.0f}% of the window"],
                  "tip": [label, f"{kit.exact(v)} ms", f"{v / window * 100:.1f}% of {kit.exact(window)} ms"]}
                 for label, v in states]
        # A window that is the launch opens before the app's process exists,
        # and the main thread has no state until it does (launch.py). Without
        # this bar the chart stops short of such a window by a tenth of it,
        # which reads as a hole in the recording. It is no state of the
        # thread, so it is drawn quiet, says what it is, and comes last, in
        # the words of the line under the chart. Only a launch window has
        # the key.
        late = float(budget.get("before_process_ms") or 0)
        if late / window * 100 >= 1:
            marks.append({"name": _BEFORE_PROCESS, "mono": False, "value": late, "quiet": True,
                          "subs": ["no state of the main thread: its process did not exist yet"],
                          "figure": f"{kit.ms(late)} ms",
                          "notes": [f"{late / window * 100:.0f}% of the window"],
                          "tip": [_BEFORE_PROCESS, f"{kit.exact(late)} ms",
                                  f"{late / window * 100:.1f}% of {kit.exact(window)} ms"]})
        if marks:
            parts.append("<div>" + kit.chart(marks, kit.nice_top(max(m["value"] for m in marks)), unit=unit,
                                             title="Where the main thread was during the window") + "</div>")
    marks, values = [], []
    for det in report.get("detectors") or []:
        for row in det.get("rows") or []:
            mark, seen = _mark(row, ctx, [str(det.get("id") or "")], several)
            marks.append(mark)
            values += seen
    if marks:
        marks.sort(key=lambda m: -m["value"])
        parts.append("<div>" + kit.chart(marks, kit.nice_top(max(values)), unit=unit, title="Every row on one axis")
                     + kit.chart_key("median" if several else f"this {ctx.noun}",
                                     f"each {ctx.noun}" if several else None) + "</div>")
    try:
        written = md.to_html(report_mod.to_markdown(report))
    except (KeyError, TypeError, AttributeError) as err:
        # A file that is a report by its `detectors` and holds little else.
        written = (f'<p class="sub">The technical report cannot be written out: this report.json lacks '
                   f'what it is written from ({e(type(err).__name__)}: {e(err)}).</p>')
    parts.append(f'<div class="md">{written}</div>')
    return kit.fold("The full technical report", "".join(parts), "big")


_NO_SCRIPT_SERVED = ("<noscript><div class=\"callout fig\"><p><b>Script is switched off in this browser.</b> Every check, "
                     "what it found and how that touches the app are in the table below, and the measurements are in "
                     "the full technical report at the end. The filter by level and the panel with one check's "
                     "measurements need script. To dig deeper, tick the checks in the list under the table.</p>"
                     "</div></noscript>")


def render(report: dict[str, Any], where: str | None = None, last: bool = True, *,
           steps: Sequence[tuple[str, str, bool]] = (), crumb: str | None = None,
           policy: str | None = None, limit: dict[str, Any] | None = None,
           dig: dict[str, Any] | None = None) -> str:
    """The page for a report, as one self-contained HTML document.

    `where` is how the request for an agent names the report.json the page
    was written from, and `last` says whether that is the scenario's last
    report or one named by hand. Neither changes anything else.

    `steps` is the path in the top bar, for a page that has others to go to
    (`echolot ui`); a page written to disk has none. `crumb` says which report
    this is when it is not the scenario's last: "investigation 3, report 2".
    `policy` is the content policy of a page that is served (`kit.SERVED`).
    `limit` is the time limit beside the verdict, already drawn, with its own
    styles (`limit_page.beside`): only a served page has it, since a page on
    disk cannot change a config.

    `dig` is what a served page adds to start an agent from it
    (`pages/dig_page.report_parts`): `ask`, markup for the request dialog;
    `noscript`, the same as a form for a browser with script off; and its
    own `css` and `js`. A page written to disk has none.
    """
    report = _read(report)
    ctx = plain._Ctx(report)
    several = _runs(report) > 1
    checks = _checks(report)
    hot = sum(1 for c in checks if c["hot"])
    w = report.get("window") or {}
    dig = dig if dig and hot else None
    main = ('<main><div class="wide stack gap-6">'
            + _verdict(report, ctx) + (limit or {}).get("html", "") + (_NO_SCRIPT_SERVED if dig else _NO_SCRIPT)
            + _table(checks, report, ctx) + _next_step(checks) + ((dig.get("noscript") or "") if dig else "")
            + "<hr>" + _technical(report, ctx, several)
            + "".join(_panel(c, ctx, several) for c in checks) + "</div></main>")
    lead, warned, tail = _request(report, where, last)
    body = (f'<div class="page">{kit.topbar([w.get("process"), report.get("scenario"), crumb], steps)}{main}</div>'
            + (kit.dock(hot, _start_words(checks)[1])
               + kit.ask(lead, warned, tail, start_html=(dig or {}).get("ask") or "", starts=bool((dig or {}).get("starts")))
               + kit.sprites() if hot else "")
            + kit.peek("check", "Back to the report"))
    title = "echolot report" + (f": {report['scenario']}" if report.get("scenario") else "")
    extra = (limit or {}, dig or {})
    return kit.document(title, body, css=CSS + tuple(c for part in extra for c in part.get("css") or ()),
                        js=JS + tuple(j for part in extra for j in part.get("js") or ()), policy=policy)
