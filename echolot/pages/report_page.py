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

import copy
import functools
import re
from typing import Any, Sequence

from .. import plain
from .. import report as report_mod
from ..tp import load_detectors
from . import kit, md
from .kit import e

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


def _read(report: dict[str, Any]) -> dict[str, Any]:
    """The report with its plain reading. One written before those keys gets
    them worked out on a copy, as `plain.render` does: the file is not touched."""
    dets = report.get("detectors") or []
    if "verdict" in report and all("plain" in d and "attention" in d for d in dets):
        return report
    return plain.annotate(copy.deepcopy(report))


def _value(row: dict[str, Any]) -> float:
    try:
        return float(row.get(report_mod.metric_of(row)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _seen(row: dict[str, Any]) -> tuple[int, int] | None:
    """`(k, n)` from a row's `runs`: found in k of n recordings."""
    try:
        k, n = (int(x) for x in str(row["runs"]).split("/"))
    except (KeyError, ValueError):
        return None
    return k, n


def _runs(report: dict[str, Any]) -> int:
    n = report.get("runs")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return n
    return len(report.get("traces") or []) or 1


def _number(value: Any) -> str:
    """A number as the report holds it, without the `.0` of a whole float."""
    text = str(value)
    return text[:-2] if isinstance(value, float) and text.endswith(".0") else text


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        rows = sorted(det.get("rows") or [], key=lambda r: -_value(r))
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
@functools.lru_cache(maxsize=1)
def _floors() -> dict[str, tuple[str, str]]:
    out = {}
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
        tip.append(f"longest single one: {kit.exact(row['max_ms'])} ms")
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
    """What goes to the agent for one check: its largest rows, each as the
    report names it, one to a line."""
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


def _row(c: dict[str, Any]) -> str:
    tick = kit.tick_box(c["key"], c["kind"], c["title"], _request_lines(c)) if c["hot"] else ""
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
        tick = (f'<div class="tickrow">{kit.tick_box(c["key"], c["kind"], c["title"], _request_lines(c), twin=True)}'
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
    rest = " ".join(part for part in (rest, _measured(report, ctx)) if part)
    scenario = f'<span class="small sub">{e(report["scenario"])}</span>' if report.get("scenario") else ""
    return (f'<div class="stack gap-3"><div class="rowf">{kit.pill(v.get("level"), large=True)}{scenario}</div>'
            f'<h1 class="h1">{e(headline.rstrip("."))}</h1>'
            + (f'<p class="lead">{e(rest)}</p>' if rest else "") + "</div>")


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
    a, b = plain.hum(low), plain.hum(high)
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
    views = kit.views([("all", "", "All", total)]
                      + [(k, kit.icon(k), kit.word(level), count[k]) for level, k in kit.TONE.items()])
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
        ("green", kit.WORD[plain.FINE], "The check ran and found nothing above its limit."),
        ("grey", kit.WORD[plain.NOT_CHECKED],
         "The check could not run on these recordings. Green always means looked at and fine."),
    ]))


def _next_step(checks: list[dict[str, Any]]) -> str:
    """What to do with the rows that found something: tick them, dig deeper."""
    hot = [c for c in checks if c["hot"]]
    if not hot:
        return ""
    kind = "red" if any(c["kind"] == "red" for c in hot) else "yellow"
    if len(hot) == 1:
        text = ("<b>Not sure what to do with this?</b> Tick the one that is red or yellow and press <b>Dig deeper</b>. "
                "An agent takes it into your code and comes back with the exact places and what to change.")
    else:
        text = (f"<b>Not sure what to do with these?</b> Tick the {len(hot)} that are red or yellow and press "
                "<b>Dig deeper</b>. An agent takes them into your code and comes back with the exact places and "
                "what to change.")
    return f'<div class="callout fig needs-js">{kit.icon(kind)}<p>{text}</p></div>'


def _request(report: dict[str, Any], where: str | None, last: bool) -> tuple[str, str, str]:
    """The first line of the request, for one row and for several, and its
    last: what takes how long, on which traces, after which change. The rows
    between them are the ticked checks' own."""
    w = report.get("window") or {}
    lead = "/echolot-hunt "
    if _is_number(w.get("duration_ms")) and w["duration_ms"] > 0:
        lead += f"{report.get('scenario') or 'The window'} takes {w['duration_ms']:,.0f} ms"
        budget = (report.get("verdict") or {}).get("budget_ms")
        if _is_number(budget):
            lead += f" against a limit of {budget:,.0f} ms"
        lead += ". "
    elif report.get("scenario"):
        lead += f"Scenario {report['scenario']}. "
    which = "the last report" if last else "the report"
    one = f"{lead}Find what is behind this row of {which} and where it is in the code:"
    many = f"{lead}Find what is behind these rows of {which} and where it is in the code:"
    n = _runs(report)
    traces = "the one" if n == 1 else f"the {n}"
    tail = f"Traces: {traces} behind {where or 'that report'}. After which change: unknown."
    return one, many, tail


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


def render(report: dict[str, Any], where: str | None = None, last: bool = True, *,
           steps: Sequence[tuple[str, str, bool]] = (), crumb: str | None = None,
           policy: str | None = None, limit: dict[str, Any] | None = None) -> str:
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
    """
    report = _read(report)
    ctx = plain._Ctx(report)
    several = _runs(report) > 1
    checks = _checks(report)
    hot = sum(1 for c in checks if c["hot"])
    w = report.get("window") or {}
    main = ('<main><div class="wide stack gap-6">'
            + _verdict(report, ctx) + (limit or {}).get("html", "") + _NO_SCRIPT + _table(checks, report, ctx) + _next_step(checks)
            + "<hr>" + _technical(report, ctx, several)
            + "".join(_panel(c, ctx, several) for c in checks) + "</div></main>")
    body = (f'<div class="page">{kit.topbar([w.get("process"), report.get("scenario"), crumb], steps)}{main}</div>'
            + (kit.dock(hot) + kit.ask(*_request(report, where, last)) + kit.sprites() if hot else "")
            + kit.peek("check", "Back to the report"))
    title = "echolot report" + (f": {report['scenario']}" if report.get("scenario") else "")
    return kit.document(title, body, css=CSS + tuple((limit or {}).get("css") or ()), js=JS, policy=policy)
