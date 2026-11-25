"""The comparison as a page a person reads: `comparison.html`, beside
`comparison.json`.

`comparison.md` is a table for an agent and a terminal. This is the same
comparison for someone who has just changed their code and pressed "Check
again": a verdict (better, worse, no clear change), the notes under it, the
whole window before and after as a chart, then one row per check with which
way it moved and its findings added up on each side. Opening a row shows what
each report said of that check and its rows as bars, before above after.

Everything said about a check is the comparison's own plain reading
(`echolot/moves.py`, the key `plain`): the verdict, the notes, each check's
movement and sentence. The page adds labels and nothing else, and judges
nothing. The bars are the comparison's rows as they are, and the technical
comparison under the last fold is comparison.md itself, as HTML.

A colour here says which way something moved. On the report the same colour
says how much attention a check needs, and the page says so in words, once,
under the verdict, where a reader meets the first pill.

One file, nothing fetched, and the same comparison gives the same bytes.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from .. import compare as compare_mod
from .. import files, moves, plain
from .. import report as report_mod
from . import kit, md
from .kit import e
from ..words import is_number as _is_number

# report.css is taken for the typography of the Markdown under the last fold,
# which comparison.md shares with report.md.
CSS = ("base.css", "table.css", "report.css", "compare.css")
JS = ("base.js", "table.js")

# How many rows the chart of every row holds: the ones that moved most. All of
# them are in the table under the last fold.
ROWS_SHOWN = 40
# How much of a row's evidence the panel shows under its name.
PANEL_DETAIL = 160

# What the filter above the table shows together: a movement's direction.
GROUP = {moves.BETTER: "better", moves.GONE: "better", moves.WORSE: "worse", moves.NEW: "worse",
         moves.NO_CHANGE: "same", moves.FINE_BOTH: "fine", moves.NOT_CHECKED: "unchecked",
         moves.NOT_COMPARED: "uncompared"}
_QUIET = (moves.FINE_BOTH, moves.NOT_CHECKED)

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
# What became of one row, as the bars say it beside the figures.
_BECAME = {compare_mod.VANISHED: "gone", compare_mod.APPEARED: "new", compare_mod.GREW: "larger",
           compare_mod.SHRANK: "smaller", compare_mod.STEADY: "about the same"}


def has_reading(cmp: Any) -> bool:
    """Whether a comparison carries the plain reading a page is drawn from:
    one written by `compare --plain` or `--html`."""
    got = cmp.get("plain") if isinstance(cmp, dict) else None
    return (isinstance(got, dict) and isinstance(got.get("verdict"), dict)
            and isinstance(got.get("checks"), list) and isinstance(got.get("window"), dict))


def misshapen(cmp: dict[str, Any]) -> str | None:
    """The first part of a comparison that does not hold what `compare`
    writes there, in words, or None: `files.check_comparison`, the one
    reader of comparison.json (#833). A field of the wrong type ended the
    "After the fix" page of `echolot ui` in a 500, in the plain reading
    (#733) and in the rows (#870). `has_reading` is asked first."""
    got = files.check_comparison(cmp)
    return got.why if isinstance(got, files.Refusal) else None


def _words(noun: Any) -> tuple[str, str]:
    noun = noun if noun in ("launch", "run") else "launch"
    return noun, noun + ("es" if noun == "launch" else "s")


# ---------------------------------------------------------------------------
# the verdict, its notes, and the window before and after


def _colours(noted: bool) -> str:
    """What a colour means here, said once: a direction. The notes under the
    verdict, where there are any, carry a level of attention as the report
    does, so the sentence names them."""
    where = "On the report, and beside the notes above," if noted else "On the report"
    return ("A colour on this page says which way something moved: green got better, grey did not change, "
            f"red got worse. {where} a colour says how much attention something needs.")


def _verdict(reading: dict[str, Any]) -> str:
    """The large pill with the scenario's name beside it, the headline, the
    sentence, the notes, and what a colour means on this page."""
    v = reading["verdict"]
    scenario = f'<span class="small sub">{e(reading["scenario"])}</span>' if reading.get("scenario") else ""
    noted = kit.notes((kit.tone(n.get("attention")), str(n.get("title") or ""), str(n.get("text") or ""))
                      for n in reading.get("notes") or [])
    return (f'<div class="stack gap-3"><div class="rowf">{kit.move_pill(v.get("movement"), large=True)}{scenario}</div>'
            f'<h1 class="h1">{e(v.get("headline"))}</h1>'
            f'<p class="lead">{kit.prose(v.get("text"))}</p>{noted}'
            f'<p class="small sub measure">{e(_colours(bool(noted)))}</p></div>')


def _window_row(name: str, side: dict[str, Any], noun: str, plural: str, quiet: bool) -> tuple[dict[str, Any], list[float]]:
    """One side of the window as a row of a chart. A report keeps the median,
    the fastest and the slowest launch: with three launches those are all of
    them, with more they are the two ends."""
    ms, low, high, runs = float(side["ms"]), float(side["low_ms"]), float(side["high_ms"]), side.get("runs") or 1
    if runs == 1:
        dots, notes = [ms], [f"one {noun}"]
        tip = [name, f"{kit.exact(ms)} ms"]
    else:
        dots = [low, ms, high] if runs == 3 else [low, high]
        notes = [f"{plain.hum(low)} – {plain.hum(high)}"] if low != high else []
        notes.append(f"{runs} {plural}")
        tip = [name, f"median: {kit.exact(ms)} ms",
               (f"each {noun}: " + " · ".join(kit.exact(v) for v in dots)) if runs == 3 else
               f"fastest {kit.exact(low)} ms, slowest {kit.exact(high)} ms, of {runs} {plural}"]
    return ({"name": name, "mono": False, "value": ms, "dots": dots, "figure": plain.hum(ms),
             "notes": notes, "tip": tip, "quiet": quiet}, [ms, low, high])


def _window(reading: dict[str, Any]) -> str:
    """The whole window before and after, on one axis, with the time limit."""
    w = reading["window"]
    b, a = w.get("before") or {}, w.get("after") or {}
    if not all(_is_number(s.get(k)) and s[k] > 0 for s in (b, a) for k in ("ms", "low_ms", "high_ms")):
        return ""
    noun, plural = _words(w.get("noun"))
    before, seen_b = _window_row("Before", b, noun, plural, quiet=True)
    after, seen_a = _window_row("After", a, noun, plural, quiet=False)
    budget = w.get("budget_ms") if _is_number(w.get("budget_ms")) and w["budget_ms"] > 0 else None
    top = kit.nice_top(max(seen_b + seen_a + ([budget] if budget else [])))
    exact = b.get("runs") == 3 and a.get("runs") == 3
    several = (b.get("runs") or 1) > 1 or (a.get("runs") or 1) > 1
    dot = (f"each {noun}" if exact else f"the fastest and the slowest {noun}") if several else None
    key = kit.chart_key("median" if several else f"the {noun}", dot,
                        f"your limit, {plain.hum_limit(budget)}" if budget else None)
    return ('<div class="fig lead-chart">'
            + kit.chart([before, after], top, mark=budget, unit="ms, median" if several else "ms",
                        title=f"The whole {noun}, before and after") + key + "</div>")


# ---------------------------------------------------------------------------
# a check: one item of the reading, as the page shows it


def _checks(reading: dict[str, Any]) -> list[dict[str, Any]]:
    """The reading's items, in its order, each with a key that is safe in a
    selector and one of its own."""
    out, taken = [], set()
    for i, item in enumerate(reading.get("checks") or []):
        did = str(item.get("id") or "")
        key = f"mv-{did}" if _SAFE_ID.fullmatch(did) else f"mv-check-{i}"
        if key in taken:
            key = f"mv-check-{i}"
        taken.add(key)
        movement = item.get("movement")
        out.append({"item": item, "id": did, "key": key, "movement": movement,
                    "group": GROUP.get(movement, "unchecked"), "quiet": movement in _QUIET or movement not in GROUP,
                    "title": str(item.get("title") or did), "text": str(item.get("text") or ""),
                    "why": str(item.get("why") or ""),
                    "before": item.get("before") or {}, "after": item.get("after") or {}})
    return out


def _figure(c: dict[str, Any], side: dict[str, Any]) -> str:
    """A side's findings added up, as a cell says it: a time, `nothing` for a
    side that found none, `at times` for findings no typical launch has, and
    a dash where there is no figure to give."""
    if c["movement"] == moves.FINE_BOTH or side.get("attention") == plain.NOT_CHECKED:
        return "–"
    ms = side.get("ms")
    if ms is None:
        return "–" if c["movement"] == moves.NOT_CHECKED else "nothing"
    if not _is_number(ms):
        return "–"
    return plain.hum(ms) if ms > 0 else "at times"


def _row(c: dict[str, Any]) -> str:
    return (f'<div class="tr{" quiet" if c["quiet"] else ""}" role="row" data-id="{e(c["key"])}" data-group="{c["group"]}">'
            f'<div class="c-t" role="cell"><h3 class="tr-t">{e(c["title"])}</h3><p class="tr-f">{kit.prose(c["text"])}</p></div>'
            f'<div class="c-l" role="cell">{kit.move_pill(c["movement"])}</div>'
            f'<div class="c-b c-n" role="cell" data-label="Before">{e(_figure(c, c["before"]))}</div>'
            f'<div class="c-a c-n" role="cell" data-label="After">{e(_figure(c, c["after"]))}</div>'
            f'<div class="c-go" role="cell">{kit.details_btn(c["key"], c["title"])}</div></div>')


def _side_face(face: Any, metric: str) -> dict[str, Any] | None:
    """One side of a comparison row as a lane: the bar to its median, a dot
    for each launch it was found in."""
    if not isinstance(face, dict) or not _is_number(face.get(metric)):
        return None
    return {"value": float(face[metric]), "dots": [float(v) for v in face.get("values") or [] if _is_number(v)]}


def _said(face: Any, lane: dict[str, Any] | None, when: str, noun: str, plural: str) -> str:
    """One side's exact figures, for the tooltip."""
    if lane is None:
        return f"{when}: not found"
    text = f"{when}: {kit.exact(lane['value'])} ms"
    if len(lane["dots"]) > 1:
        text += f" · each {noun}: " + " · ".join(kit.exact(v) for v in lane["dots"])
    runs = report_mod.short_runs(face.get("runs")) if isinstance(face, dict) else None
    if runs:
        text += f" · in {runs[0]} of {runs[1]} {plural}"
    return text


def _mark(row: dict[str, Any], noun: str, plural: str, subs: list[Any]) -> tuple[dict[str, Any], list[float]]:
    """One row of the comparison as a row of a two-lane chart."""
    metric = str(row.get("metric") or "total_ms")
    before, after = _side_face(row.get("before"), metric), _side_face(row.get("after"), metric)
    name = str(row.get("location")) if row.get("location") not in (None, "") else "(no name)"
    if before and after:
        figure = f"{kit.ms(before['value'])} → {kit.ms(after['value'])} ms"
    elif before:
        figure = f"{kit.ms(before['value'])} ms → none"
    elif after:
        figure = f"none → {kit.ms(after['value'])} ms"
    else:
        figure = "–"
    notes = [_BECAME.get(row.get("change"), "")]
    if row.get("matched_by") == "family":
        notes.append("one thing under two names")
    seen = [v for lane in (before, after) if lane for v in (lane["value"], *lane["dots"])]
    mark = {"name": name, "subs": subs, "before": before, "after": after, "figure": figure, "notes": notes,
            "tip": [name, _said(row.get("before"), before, "before", noun, plural),
                    _said(row.get("after"), after, "after", noun, plural)]}
    return mark, seen


def _evidence(row: dict[str, Any]) -> str | None:
    """What tells this row from its twins, or what the detector said of it on
    the side that has it."""
    if row.get("detail"):
        return report_mod.clip(row["detail"], PANEL_DETAIL)
    for side in ("after", "before"):
        face = row.get(side)
        if isinstance(face, dict) and face.get("detail"):
            return report_mod.clip(face["detail"], PANEL_DETAIL)
    return None


def _bars(rows: list[dict[str, Any]], noun: str, plural: str, several: bool, by_check: bool = False,
          title: str = "") -> str:
    marks, values = [], []
    for row in rows:
        subs = [str(row.get("detector") or "")] if by_check else [_evidence(row)]
        mark, seen = _mark(row, noun, plural, subs)
        marks.append(mark)
        values += seen
    if not marks or not values:
        return ""
    return (kit.chart2(marks, kit.nice_top(max(values)), unit="ms, median" if several else "ms", title=title)
            + kit.chart2_key(f"each {noun}" if several else None))


def _measurements(c: dict[str, Any], rows: list[dict[str, Any]], noun: str, plural: str, several: bool) -> str:
    """The technical layer of one check: its name in echolot, and its rows
    before and after as bars. For a check with no rows, that it has none."""
    out = [f'<p class="small sub"><code>{e(c["id"])}</code> is this check\'s name in the technical comparison.</p>']
    bars = _bars(rows, noun, plural, several)
    if bars:
        out.append(bars)
    elif c["movement"] == moves.NOT_CHECKED:
        out.append('<p class="small sub">No rows to compare: the check could not look on one side or on both, '
                   "so the comparison leaves its rows out.</p>")
    else:
        out.append('<p class="small sub">No rows: the check found nothing on either side.</p>')
    return "".join(out)


def _side(side: dict[str, Any]) -> str:
    """What one report said of a check, and its level there in words: a level
    is the report's, and has no colour on this page."""
    level = side.get("attention")
    said = side.get("found") or (side.get("reason") if level == plain.NOT_CHECKED else None) or (
        "It found nothing above its limit." if level == plain.FINE else "")
    words = f'<span class="small sub">Its level in that report: {e(kit.word(level).lower())}.</span>' if level else ""
    return f'<div class="side"><span>{kit.prose(said)}</span>{words}</div>'


def _panel(c: dict[str, Any], rows: list[dict[str, Any]], noun: str, plural: str, several: bool) -> str:
    """What the panel shows for a check: the row's own sentence again, what
    each report said of the check, and under a hairline its rows as bars."""
    quiet = ' class="sub"' if c["quiet"] else ""
    said = f'<p{quiet}>{kit.prose(c["text"])}</p>' if c["text"] else ""
    why = f'<p class="small sub">{e(c["why"])}</p>' if c["why"] else ""
    sides = kit.pairs([("Before", _side(c["before"])), ("After", _side(c["after"]))])
    return kit.template(c["key"], f'<div>{kit.move_pill(c["movement"])}</div><h2 class="h2" id="peek-title">{e(c["title"])}</h2>'
                                  f'{said}{why}{sides}{kit.deep(_measurements(c, rows, noun, plural, several))}')


# ---------------------------------------------------------------------------
# the blocks of the page, top to bottom


_NO_SCRIPT = ("<noscript><div class=\"callout fig\"><p><b>Script is switched off in this browser.</b> Every check "
              "and which way it moved are in the table below, and every row is in the technical comparison at "
              "the end. The filter and the panel with one check's rows need script.</p></div></noscript>")


def _views(checks: list[dict[str, Any]]) -> str:
    count = {g: sum(1 for c in checks if c["group"] == g)
             for g in ("better", "same", "worse", "uncompared", "fine", "unchecked")}
    items = [("all", "", "All", len(checks)),
             ("better", kit.move_icon(moves.BETTER), "Got better", count["better"]),
             ("same", kit.move_icon(moves.NO_CHANGE), "Did not change", count["same"])]
    if count["worse"]:
        items.append(("worse", kit.move_icon(moves.WORSE), "Got worse", count["worse"]))
    if count["uncompared"]:
        items.append(("uncompared", kit.move_icon(moves.NOT_COMPARED), "Not compared", count["uncompared"]))
    items += [("fine", kit.icon("green"), "Fine both times", count["fine"]),
              ("unchecked", kit.icon("grey"), "Not checked", count["unchecked"])]
    return kit.views(items)


def _rule(noun: str, plural: str) -> str:
    """How a movement is decided, in a sentence each, with the numbers `moves` holds."""
    share, launch = f"{moves.SHARE:.0%}", f"{moves.LAUNCH_SHARE:.0%}"
    rows = [
        (moves.BETTER, "Better, worse",
         f"What the check found, added up, changed by at least {share} and by at least {launch} of the {noun}, "
         f"and every {noun} of one side is beyond every {noun} of the other. It takes {moves.LEAST_RUNS} {plural} a side."),
        (moves.GONE, "Gone, new", "The same, where one of the two sides found nothing at all."),
        (moves.NO_CHANGE, "No change",
         f"The check found something on a side, and the change is smaller than that, or single {plural} of the two "
         f"sides overlap, or the {plural} are too few. Its sentence says which."),
        (moves.NOT_COMPARED, "Not compared",
         "The check ran both times, but not by one measure: its thresholds changed, the version of echolot "
         "that read it did, or both sides are the same recordings. Its sentence says which."),
        (moves.FINE_BOTH, "Fine both times", "The check ran both times and found nothing above its limit."),
        (moves.NOT_CHECKED, "Not checked",
         "The check could not run on one side or on both, so there is nothing to compare."),
    ]
    listed = '<ul class="quiet-list">' + "".join(
        f"<li>{kit.move_icon(m)}<div><b>{e(name)}</b><p>{e(said)}</p></div></li>" for m, name, said in rows) + "</ul>"
    tail = (f'<p class="small sub measure">What a check found is added up as a typical {noun} has it: something '
            f"seen in one {noun} of three adds nothing. The verdict at the top is decided the same way, by the "
            f"whole {noun}: a change of at least {share}, with more than half of the faster side's {plural} "
            f"beating the fastest {noun} of the slower side, and, where both reports keep each {noun}'s window, a "
            f"move that holds as the technical comparison's rows do, 95% sure. A window that runs to the end of "
            f"the recording is as "
            f"long as the recording, and is judged by the share of frames the app made late instead, the same way "
            f"and by at least one frame in a hundred.</p>")
    return kit.fold("How a movement is decided", listed + tail)


def _table(checks: list[dict[str, Any]], noun: str, plural: str) -> str:
    total = len(checks)
    said = (f"{total} {'check' if total == 1 else 'checks'}, the ones that got worse first. Each says what it "
            "looked at and what changed between the two.")
    head = kit.head([("c-t", "Check", True), ("c-l", "Change", True), ("c-b", "Before", True),
                     ("c-a", "After", True), ("c-go", "Details", False)])
    rows = "".join(_row(c) for c in checks)
    return (f'<section class="stack gap-3"><div class="stack gap-2"><h2 class="h2">What changed, check by check</h2>'
            f'<p class="sub measure">{e(said)}</p></div>'
            f'<div>{_views(checks)}<div class="tbw"><div class="tb moves" role="table" aria-label="What changed, check by check">'
            f'{head}<div role="rowgroup">{rows}</div></div></div></div>{_rule(noun, plural)}</section>')


def _every_row(cmp: dict[str, Any], noun: str, plural: str, several: bool) -> str:
    """Every row of the comparison on one axis, the largest moves first."""
    rows = [r for r in cmp.get("rows") or [] if isinstance(r, dict)]
    shown = rows[:ROWS_SHOWN]
    bars = _bars(shown, noun, plural, several, by_check=True)
    if not bars:
        return ""
    more = len(rows) - len(shown)
    said = (f"The {len(shown)} rows that moved most, of {len(rows)}. The others are in the table of the technical "
            "comparison below." if more > 0 else
            f"{len(rows)} {'row' if len(rows) == 1 else 'rows'}, the ones that moved most first.")
    return kit.fold("Every row, before and after", f'<p class="small sub measure">{e(said)}</p><div>{bars}</div>', "big")


def _technical(cmp: dict[str, Any]) -> str:
    """The last fold: comparison.md as it is written."""
    try:
        written = md.to_html(compare_mod.to_markdown(cmp))
    except (KeyError, TypeError, AttributeError, ValueError) as err:
        # A file that is a comparison by its reading and holds little else.
        written = (f'<p class="sub">The technical comparison cannot be written out: this comparison.json lacks '
                   f'what it is written from ({e(type(err).__name__)}: {e(err)}).</p>')
    return kit.fold("The full technical comparison", f'<div class="md">{written}</div>', "big")


def render(cmp: dict[str, Any], *, steps: Sequence[tuple[str, str, bool]] = (),
           policy: str | None = None) -> str:
    """The page for a comparison, as one self-contained HTML document.

    `cmp` is a comparison with its plain reading in it (`has_reading`): what
    `compare --html` builds and what it writes to comparison.json. `steps` is
    the path in the top bar, for a page that has others to go to (`echolot
    ui`); a page written to disk has none. `policy` is the content policy of
    a page that is served (`kit.SERVED`).
    """
    if not has_reading(cmp):
        raise ValueError("this comparison carries no plain reading: write it with `echolot compare --html`")
    wrong = misshapen(cmp)
    if wrong:
        raise ValueError(f"this comparison does not read: {wrong}; write it again with `echolot compare --html`")
    reading = cmp["plain"]
    w = reading["window"]
    noun, plural = _words(w.get("noun"))
    several = max((w.get("before") or {}).get("runs") or 1, (w.get("after") or {}).get("runs") or 1) > 1
    checks = _checks(reading)
    by_check: dict[str, list[dict[str, Any]]] = {}
    for row in cmp.get("rows") or []:
        if isinstance(row, dict):
            by_check.setdefault(str(row.get("detector") or ""), []).append(row)
    table = (_NO_SCRIPT + _table(checks, noun, plural)) if checks else ""
    main = ('<main><div class="wide stack gap-6">'
            + _verdict(reading) + _window(reading) + table
            + "<hr>" + _every_row(cmp, noun, plural, several) + _technical(cmp)
            + "".join(_panel(c, by_check.get(c["id"], []), noun, plural, several) for c in checks)
            + "</div></main>")
    body = (f'<div class="page">{kit.topbar([reading.get("process"), reading.get("scenario")], steps)}{main}</div>'
            + kit.peek("check", "Back to the comparison"))
    title = "echolot comparison" + (f": {reading['scenario']}" if reading.get("scenario") else "")
    return kit.document(title, body, css=CSS, js=JS, policy=policy)
