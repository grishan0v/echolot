"""The agent's answer as a page: `answer.html`, in the investigation's folder.

A hunt ends in a text in the agent's own window. This is the same answer for
the person who asked, in the shape the report page taught them to read: a
verdict, then one row per cause with where it is in the code and what to do
about it. Opening a row shows the measurements the cause stands on, from the
round they were measured in.

Everything said about a cause is the verdict's (`echolot/verdict.py`): the
page adds labels and nothing else. It ranks nothing and gives a cause no
level of attention. A level is worked out by the engine from measurements
(`echolot/plain.py`); a cause has the figures the agent gave. What it
measured (`cost_ms`) and what it estimates (`expected_ms`) are worded apart
and never look the same.

The page is drawn from two things on disk and nothing else: the
investigation's record (`hunt.json`) and the reports it filed. The rows a
cause cites are looked up in those reports when the page is written, by the
rule the check of a verdict uses (`verdict.rows_at`), and nothing is copied
into the record. A report that was deleted since leaves a sentence where its
rows were, and the page is written all the same.

An investigation with no verdict has a page too: one closed with a text shows
the text and its rounds, and an open one shows the rounds so far, which is
its progress.

One file, nothing fetched, and the same files give the same bytes: no clock
is read and no path of this machine is written.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

from .. import plain
from .. import merge as merge_mod
from .. import render as render_mod
from .. import verdict as verdict_mod
from .. import when as when_mod
from . import kit, report_page
from .kit import e
from ..files import runs as _runs
from ..words import as_dict as _dict
from ..words import as_list as _list
from ..words import counted as _count
from ..words import is_ms as _ms

CSS = ("base.css", "table.css", "answer.css")
JS = ("base.js", "table.js", "answer.js")

# A first sentence longer than this is no headline: the page says its own, and
# the sentence stands under it whole.
HEADLINE = 120
# How much of a row's evidence the panel shows under its name.
PANEL_DETAIL = 160
# How many of the rows a round is cited for are named in its line.
ROUND_NAMES = 6

_FIRST_SENTENCE = re.compile(r"(.+?[.!?])\s+(.+)", re.S)
_CONFIDENCE = {"high": "High", "medium": "Medium", "low": "Low"}


# ---------------------------------------------------------------------------
# reading what is stored: a record can be edited by hand, so a field of the
# wrong kind is left out and the page is written without it


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _span(value: Any) -> tuple[float, float] | None:
    """A figure in milliseconds as the verdict holds one: a number, or `[low, high]`."""
    if _ms(value):
        return float(value), float(value)
    if isinstance(value, list) and len(value) == 2 and all(_ms(v) for v in value) and value[0] <= value[1]:
        return float(value[0]), float(value[1])
    return None


def _hum(span: tuple[float, float]) -> str:
    """A figure of the agent's, kept as exact as it was given: "223 ms",
    "1.15 s", "120 to 160 ms". Seconds from one second up, and one unit for
    both ends of a range. (`plain.hum` rounds to a tenth of a second, which
    is right for a sentence about a launch and would turn 120 to 160 ms into
    "0.1 to 0.2 s".)"""
    low, high = span
    if high >= 1000:
        said = [f"{v / 1000:.2f}".rstrip("0").rstrip(".") for v in (low, high)]
        unit = "s"
    else:
        said = [f"{v:.0f}" if v >= 10 or v == int(v) else f"{v:.1f}" for v in (low, high)]
        unit = "ms"
    return f"{said[0]} {unit}" if said[0] == said[1] else f"{said[0]} to {said[1]} {unit}"


def _expected(span: tuple[float, float] | None) -> str:
    """What a change should win back is the agent's estimate, and reads as one."""
    if span is None:
        return ""
    return _hum(span) if span[0] != span[1] else f"about {_hum(span)}"


def _said(text: str) -> str:
    """The agent's words as markup: a name in backticks is code, a line stays a line."""
    return "<br>".join(kit.prose(line) for line in text.splitlines() if line.strip())


def _at(stamp: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _stamp(stamp: Any, local: bool = False) -> str:
    """A moment as the record holds it. On a page `echolot ui` serves, in the
    local time of this machine with its zone, as the start page and `hunt
    --show` say the same moment (#734). In a file, in the one zone that needs
    no clock of this machine: the same file reads the same wherever the page
    is written."""
    at = _at(stamp)
    if at is None:
        return _str(stamp)
    return when_mod.local(at.isoformat(), seconds=False) if local else \
        at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _between(start: Any, end: Any) -> float | None:
    a, b = _at(start), _at(end)
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds()


def _lasted(seconds: float) -> str:
    """18 min 35 s · 2 h 5 min · 3 days."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min" + (f" {s % 60} s" if s % 60 else "")
    if s < 172800:
        return f"{s // 3600} h" + (f" {s % 3600 // 60} min" if s % 3600 // 60 else "")
    return f"{s / 86400:.0f} days"


def _ctx(report: dict[str, Any]) -> Any:
    """What a sentence needs to know of a report (`plain._Ctx`): how long its
    window is, and whether it is a launch. Read from a file, so a window that
    is not one reads as none."""
    window = _dict(report.get("window"))
    return plain._Ctx({"window": {**window, "duration_ms": window["duration_ms"] if _ms(window.get("duration_ms")) else 0}})


def _rounds(reports: verdict_mod.Reports) -> list[tuple[int, dict[str, Any] | None]]:
    """The reports an investigation filed, the oldest first. One round is one
    report: the app recorded, and the recordings analysed."""
    return [(n, reports[n]) for n in sorted(reports)]


def _none_recorded(hunt: dict[str, Any], rounds: int) -> bool:
    """Whether the investigation has rounds and recorded none of them itself:
    no `collect --hunt` ran in it, so what its rounds read was recorded
    before it opened or outside it. An agent started by Dig deeper that
    answers from the recordings behind the report it was handed files them
    as round 1 and records nothing, and the page said "1 round" as if it had
    (#823). A record that does not count its recordings says nothing."""
    collects = hunt.get("collects")
    return bool(rounds) and isinstance(collects, int) and not isinstance(collects, bool) and collects == 0


def _its(rounds: int) -> str:
    return "its round" if rounds == 1 else f"its {rounds} rounds"


def _causes(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in _list(verdict.get("causes")):
        if not isinstance(c, dict):
            continue
        confidence = c.get("confidence") if isinstance(c.get("confidence"), str) else ""
        out.append({
            "key": f"cause-{len(out) + 1}",
            "title": _str(c.get("title")) or "(no title)",
            "where": [str(p).strip() for p in _list(c.get("where")) if _str(p)],
            "mechanism": _str(c.get("mechanism")),
            "fix": _str(c.get("fix")),
            "cost": _span(c.get("cost_ms")),
            "cost_of": _str(c.get("cost_of")),
            "expected": _span(c.get("expected_ms")),
            "confidence": _CONFIDENCE.get(confidence, confidence.strip()),
            "why": _str(c.get("confidence_why")),
            "evidence": _list(c.get("evidence")),
        })
    return out


def _suggestions(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    """Changes that belong to no one cause. Each is a row after the causes."""
    found = [s for s in _list(verdict.get("suggestions")) if isinstance(s, dict) and _str(s.get("fix"))]
    return [{"key": f"more-{i}", "fix": _str(s["fix"]), "expected": _span(s.get("expected_ms")),
             "title": "A change not tied to one cause" + (f" ({i} of {len(found)})" if len(found) > 1 else "")}
            for i, s in enumerate(found, 1)]


# ---------------------------------------------------------------------------
# the evidence: the rows a cause cites, from the round they were measured in


class _Cited:
    """What the verdict cites, looked up once: how many rows it points at, how
    many of them the reports on disk still hold, and which round each is from."""

    def __init__(self, reports: verdict_mod.Reports) -> None:
        self.reports = reports
        self.asked = 0
        self.held = 0
        self.by_round: dict[int, list[str]] = {}

    def rows(self, entry: Any) -> tuple[list[tuple[str | None, dict[str, Any]]], str | None]:
        found, why = verdict_mod.rows_at(self.reports, entry)
        self.asked += 1
        if found:
            self.held += 1
            names = self.by_round.setdefault(entry["report"], [])
            if entry["location"] not in names:
                names.append(entry["location"])
        return found, why


def _source(report: dict[str, Any], det_id: str | None, location: str) -> str:
    """Where a cited row stands in its report, in words."""
    if det_id is None:
        prefix = _dict(report.get("markers")).get("prefix")
        temporary = isinstance(prefix, str) and prefix and location.startswith(prefix)
        return "a temporary marker" if temporary else "one of the project's own markers"
    for det in _list(report.get("detectors")):
        if isinstance(det, dict) and det.get("id") == det_id:
            title = _dict(det.get("plain")).get("title") or plain.fixed_texts(det_id, str(det.get("title") or ""))[0]
            return f'the check "{title}"'
    return det_id


def _evidence(c: dict[str, Any], cited: _Cited) -> str:
    """The rows a cause cites, as bars on one axis, and a sentence for each
    one that can no longer be shown."""
    marks, values, gone, several, nouns = [], [], [], [], []
    for entry in c["evidence"]:
        rows, why = cited.rows(entry)
        if not rows:
            gone.append((entry, why))
            continue
        n = entry["report"]
        report = cited.reports[n] or {}
        ctx = _ctx(report)
        many = _runs(report) > 1
        for det_id, row in rows:
            subs = [f"round {n} · {_source(report, det_id, entry['location'])}",
                    render_mod.clip(row.get("detail"), PANEL_DETAIL)]
            # A marker is drawn by the time between its two ends, which is the
            # number an agent reads off it; a detector's row by the column
            # the report judges it by.
            own = row.get("self_ms") if det_id is None and _ms(row.get("total_ms")) else None
            if own is not None:
                row = {k: v for k, v in row.items() if k != "self_ms"}
            try:
                mark, seen = report_page.chart_row(row, ctx, subs, many)
            except (TypeError, ValueError, AttributeError, KeyError):
                gone.append((entry, "the row is in the report and its figures do not read"))
                continue
            if _ms(own):
                mark["tip"].append(f"of that, its own work: {kit.exact(own)} ms")
            marks.append(mark)
            values += seen
            several.append(many)
            nouns.append(ctx.noun)
    out = []
    if marks:
        noun = nouns[0] if len(set(nouns)) == 1 else "recording"
        out.append(kit.chart(marks, kit.nice_top(max(values)), unit="ms, median" if all(several) else "ms",
                             title="The measurements behind it"))
        out.append(kit.chart_key("median" if any(several) else f"this {noun}",
                                 f"each {noun}" if any(several) else None))
    elif not gone:
        out.append('<p class="small sub">The answer cites no measurement for this cause.</p>')
    for entry, why in gone:
        if isinstance(entry, dict) and _str(entry.get("location")):
            what = (f"Cited from round {e(entry.get('report'))}, <code>{e(entry['location'])}</code>, "
                    "and it cannot be shown any more")
        else:
            what = "A measurement this cause cites cannot be shown"
        out.append(f'<p class="small sub gone">{what}: {e(why)}.</p>')
    return "".join(out)


# ---------------------------------------------------------------------------
# a cause: one row of the table, and what the panel shows for it


def _measured(c: dict[str, Any]) -> str:
    """What the cause costs, as the agent measured it: a figure, and what
    that figure measures. Nothing when the verdict gives none."""
    if c["cost"] is None:
        return ""
    return f'<b>{e(_hum(c["cost"]))}</b> measured' + (f": {kit.prose(c['cost_of'])}" if c["cost_of"] else "")


def _places(c: dict[str, Any]) -> str:
    return "".join(f'<span class="at-l">{kit.place(p)}</span>' for p in c["where"])


def _cell(cls: str, label: str, inner: str) -> str:
    """A cell that carries its column's name with it for the widths where
    the head is gone. An empty one takes no room there."""
    return f'<div class="{cls}{"" if inner else " none"}" role="cell" data-label="{e(label)}">{inner}</div>'


def _row(key: str, title: str, under: str, where: str, fix: str, back: str, group: str) -> str:
    lacks = ("" if where else " no-wh") + ("" if fix else " no-do")
    return (f'<div class="tr{lacks}" role="row" data-id="{e(key)}" data-group="{group}">'
            f'<div class="c-t" role="cell"><h3 class="tr-t">{e(title)}</h3>{under}</div>'
            + _cell("c-wh", "Where", where) + _cell("c-do", "What to do", fix)
            + _cell("c-bk", "Expected back", e(back))
            + f'<div class="c-go" role="cell">{kit.details_btn(key, title)}</div></div>')


def _cause_row(c: dict[str, Any]) -> str:
    cost = _measured(c)
    under = (f'<p class="tr-m">{cost}</p>' if cost else "") \
        + (f'<p class="tr-s">{_said(c["mechanism"])}</p>' if c["mechanism"] else "")
    return _row(c["key"], c["title"], under, _places(c), _said(c["fix"]), _expected(c["expected"]), "cause")


_ESTIMATE = "The agent's estimate, not a measurement."


def _do_block(where: str, fix: str, expected: str) -> str:
    rows = []
    if where:
        rows.append(("Where", where))
    if fix:
        rows.append(("What to do", fix))
    if expected:
        rows.append(("Expected back", f'{e(expected)}<br><span class="small sub">{_ESTIMATE}</span>'))
    return kit.pairs(rows, "do") if rows else ""


def _cause_panel(c: dict[str, Any], cited: _Cited) -> str:
    """The row again, then under a hairline how sure the agent is and the
    measurements the cause stands on."""
    cost = _measured(c)
    sure = ""
    if c["confidence"]:
        sure = kit.pairs([("How sure", f"<b>{e(c['confidence'])}.</b>" + (f" {_said(c['why'])}" if c["why"] else ""))])
    return kit.template(c["key"], (
        (f'<p class="small sub">{cost}</p>' if cost else "")
        + f'<h2 class="h2" id="peek-title">{e(c["title"])}</h2>'
        + (f'<p>{_said(c["mechanism"])}</p>' if c["mechanism"] else "")
        + _do_block(_places(c), _said(c["fix"]), _expected(c["expected"]))
        + kit.deep(sure + _evidence(c, cited))))


_NO_CAUSE = "It belongs to no single cause above."


def _more_row(s: dict[str, Any]) -> str:
    return _row(s["key"], s["title"], f'<p class="tr-s">{_NO_CAUSE}</p>', "", _said(s["fix"]),
                _expected(s["expected"]), "more")


def _more_panel(s: dict[str, Any]) -> str:
    return kit.template(s["key"], (
        f'<h2 class="h2" id="peek-title">{e(s["title"])}</h2><p>{_NO_CAUSE} One change like this can answer '
        "several of them at once.</p>"
        + _do_block("", _said(s["fix"]), _expected(s["expected"]))
        + '<p class="small sub">It cites no measurement of its own.</p>'))


_NO_SCRIPT = ("<noscript><div class=\"callout fig\"><p><b>Script is switched off in this browser.</b> Every cause, "
              "where it is and what to do are in the table below. The measurements behind a cause open in a "
              "panel, which needs script; they are also in the reports of the rounds, in the folder "
              "<code>reports</code> beside this page.</p></div></noscript>")


def _table(causes: list[dict[str, Any]], more: list[dict[str, Any]]) -> str:
    said = ("One cause." if len(causes) == 1 else f"{len(causes)} causes, in the order the agent gave them.") \
        if causes else ""
    if more:
        last = "The last row is a change" if len(more) == 1 else f"The last {len(more)} rows are changes"
        said += f" {last} that {'is' if len(more) == 1 else 'are'} not tied to one cause."
    said += (' A figure in bold was measured. "Expected back" is the agent\'s estimate of what the change '
             "wins.")
    head = kit.head([("c-t", "Cause", True), ("c-wh", "Where", True), ("c-do", "What to do", True),
                     ("c-bk", "Expected back", True), ("c-go", "Details", False)])
    rows = "".join(_cause_row(c) for c in causes) + "".join(_more_row(s) for s in more)
    return ('<section class="stack gap-3"><div class="stack gap-2"><h2 class="h2">What it found, and what to do</h2>'
            f'<p class="sub measure">{e(said.strip())}</p></div>'
            '<div class="tbw"><div class="tb causes" role="table" aria-label="What it found, and what to do">'
            f'{head}<div role="rowgroup">{rows}</div></div></div></section>')


# ---------------------------------------------------------------------------
# the blocks of the page, top to bottom


def _how_long(hunt: dict[str, Any], rounds: int, is_open: bool) -> str:
    """Beside the state: how many rounds, and how long it was open. Rounds of
    recordings made outside it are no work of its own, and are not counted
    as that (#823)."""
    if not rounds:
        said = "no round yet"
    elif _none_recorded(hunt, rounds):
        said = "no new recording" + (" yet" if is_open else "")
    else:
        said = _count(rounds, "round") + (" so far" if is_open else "")
    took = _between(hunt.get("opened_at"), hunt.get("closed_at")) if not is_open else None
    return said + (f" · {_lasted(took)}" if took else "")


def _headline(text: str, own: str) -> tuple[str, str]:
    """The first sentence as the headline and the rest under it; `own` is the
    page's headline for a text that opens with no sentence short enough."""
    first = text.splitlines()[0].strip() if text else ""
    m = _FIRST_SENTENCE.fullmatch(first)
    head = m.group(1) if m else first
    if not head or len(head) > HEADLINE:
        return own, text
    rest = text[text.index(head) + len(head):].strip()
    return head.rstrip("."), rest


def _head(state: str, beside: str, headline: str, lead: str, more: str = "") -> str:
    return (f'<div class="stack gap-3"><div class="rowf">{state}<span class="small sub">{e(beside)}</span></div>'
            f'<h1 class="h1">{e(headline)}</h1>'
            + (f'<p class="lead">{lead}</p>' if lead else "") + more + "</div>")


def _piece(verdict: dict[str, Any], cited: _Cited) -> str:
    """The piece that was found against the rest of the window, when the
    verdict names one (`inside`) and the report still holds it."""
    inside = verdict.get("inside")
    if not isinstance(inside, dict):
        return ""
    rows, _ = cited.rows(inside)
    if not rows:
        return ""
    n, location = inside["report"], inside["location"]
    report = cited.reports[n] or {}
    row = rows[0][1]
    value = row.get("total_ms") if _ms(row.get("total_ms")) else row.get(merge_mod.metric_of(row))
    window = _dict(report.get("window")).get("duration_ms")
    # A piece of the window is less than the window. Time summed over several
    # threads can pass it, and is no part of it to draw.
    if not _ms(value) or not _ms(window) or not 0 < value < window:
        return ""
    ctx = _ctx(report)
    runs = _runs(report)
    rest = window - value
    share = value / window * 100
    measured = f"median of {runs} {ctx.plural}" if runs > 1 else f"one {ctx.noun}"
    parts = [
        {"value": value, "figure": plain.hum(value), "name_html": f"<code>{e(location)}</code>",
         "tip": [location, f"{kit.exact(value)} ms", f"{share:.1f}% of the {ctx.noun}", f"{measured}, round {n}"]},
        {"value": rest, "figure": plain.hum(rest), "name_html": f"the rest of the {ctx.noun}",
         "tip": [f"Everything else in the {ctx.noun}", f"{kit.exact(rest)} ms",
                 f"{100 - share:.1f}% of {kit.exact(window)} ms"]},
    ]
    return f'<div class="fig">{kit.split(parts, f"measured in round {n}, {measured}")}</div>'


def _against(verdict: dict[str, Any], kind: str | None) -> str:
    """What the build measured was set against, under the headline (#538).

    With a reference the causes are what changed since it. With none they are
    where the build spends its time, which a hunt with nothing to compare
    finds whether or not anything changed, and the page says so whatever the
    summary's words. A verdict recorded before echolot asked says neither."""
    entry = verdict.get("against")
    if kind == "regression" and isinstance(entry, dict):
        what = _str(entry.get("what"))
        return (f'<p class="measure"><b>Set against report {e(str(entry.get("report")))}</b>'
                + (f", {_said(what)}" if what else "") + ": the causes are what changed since it.</p>")
    if kind == "hotspots":
        return ('<p class="measure"><b>No reference.</b> Nothing was recorded before the change to set '
                "this build against, so the causes are where it spends its time. None of them is shown to "
                "be what changed: a set of an earlier build, recorded under the investigation, would.</p>")
    return ""


def _nothing(verdict: dict[str, Any]) -> str:
    """A verdict with no cause: said plainly, with what was looked at shown
    at once, because that is what this answer holds."""
    lists = _lists(verdict)
    return ('<section class="stack gap-3"><h2 class="h2">No cause found</h2>'
            '<p class="measure">The investigation ran to its end and did not find one thing that makes this slow. '
            "That is a result, not a failure: what was looked at is on record, so the next look can start "
            "somewhere else.</p>" + lists + "</section>")


def _lists(verdict: dict[str, Any]) -> str:
    out = []
    for key, name in (("ruled_out", "Ruled out"), ("also_measured", "Also measured")):
        items = [_str(s) for s in _list(verdict.get(key)) if _str(s)]
        if items:
            out.append(f'<div class="stack gap-2"><h3 class="deep-h">{name}</h3><ul class="said">'
                       + "".join(f"<li>{_said(s)}</li>" for s in items) + "</ul></div>")
    return "".join(out)


def _again(again: str | None, action_html: str) -> str:
    """What to do once the code is changed. A file on disk cannot record the
    app, so it gives the command; `check-again-action` is the one place a
    page that is served puts a button that can."""
    said = ("<b>Changed the code?</b> Check the app again: this records it once more and shows what moved "
            "since the first round." if again else
            "<b>Changed the code?</b> Check the app again: record it once more and compare it with the first round.")
    command = ""
    if again:
        command = (f'<div class="cmd"><pre class="cmd-t" id="again-cmd" tabindex="0">{e(again)}</pre>'
                   '<button type="button" class="btn line needs-js" id="again-copy" data-copy="again-cmd" '
                   'data-said="again-said">Copy</button><span class="vh" id="again-said" role="status"></span></div>')
    return (f'<div class="stack gap-3" id="check-again"><p class="measure">{said}</p>{command}'
            f'<div class="rowf" id="check-again-action">{action_html}</div></div>')


# ---------------------------------------------------------------------------
# the folds


def _trust(causes: list[dict[str, Any]], check: dict[str, Any], cited: _Cited,
           kind: str | None = None) -> str:
    """How sure the agent is of each cause, in its own words, and what echolot
    looked at itself: the rows the answer cites, and the markers in the code.
    With no reference the agent's word is about what a place costs, and that
    it is what changed was not measured: two answers, the second echolot's."""
    out = []
    if any(c["confidence"] for c in causes):
        items = []
        for c in causes:
            said = (f"<b>{e(c['confidence'])}.</b>" + (f" {_said(c['why'])}" if c["why"] else "")) \
                if c["confidence"] else "The agent did not say."
            if kind == "hotspots":
                end = "." if c["confidence"] and c["why"] and not c["why"].rstrip().endswith((".", "!", "?")) \
                    else ""
                said = (f"That it costs this: {said}{end} That it is what changed: <b>not measured</b>, "
                        "with nothing recorded to set it against.")
            items.append(f"<li><b>{e(c['title'])}</b><p>{said}</p></li>")
        out.append(f'<ul class="sure">{"".join(items)}</ul>')
    elif causes:
        out.append('<p class="small sub">The agent did not say how sure it is of what it found.</p>')
    seen = []
    if cited.asked:
        if cited.held == cited.asked:
            seen.append(("green", "Every measurement it cites is in the reports",
                         f"The answer points at {_count(cited.asked, 'row')} of this investigation's reports, and "
                         f"{'it is' if cited.asked == 1 else 'all of them are'} there."))
        else:
            lost = cited.asked - cited.held
            seen.append(("yellow", "Some measurements it cites cannot be shown",
                         f"The answer points at {_count(cited.asked, 'row')} of this investigation's reports. "
                         f"{lost} of them {'is' if lost == 1 else 'are'} not in the reports on disk any more. "
                         "They were there when the investigation closed: the answer was checked against them then."))
    left = _markers_left(check)
    if left:
        seen.append(left)
    if seen:
        out.append(('<p class="small sub">What echolot looked at itself:</p>' if causes else "") + kit.quiet_list(seen))
    # With no cause there is nothing for an agent to be sure of: the fold is
    # then what echolot looked at, and is named so.
    name = "How sure the agent is" if causes else "What echolot looked at itself"
    return kit.fold(name, "".join(out), "big") if out else ""


_CLAIM = {"removed": "The agent says it removed its temporary markers",
          "none_added": "The agent says it added no temporary markers",
          "left": "The agent says it left temporary markers in the code"}


def _markers_left(check: dict[str, Any]) -> tuple[str, str, str] | None:
    """The markers echolot counted in the sources when the investigation
    closed, beside what the agent said of them. A tagged line `mark --remove`
    leaves is counted with its place, and goes by hand (#703)."""
    cleanup = _dict(check.get("cleanup"))
    found = _dict(cleanup.get("found"))
    if not found:
        return None
    claim = _CLAIM.get(cleanup.get("claimed"))
    markers = found.get("markers") if isinstance(found.get("markers"), int) else 0
    lines = found.get("lines") if isinstance(found.get("lines"), int) else 0
    kept = _list(found.get("kept"))
    them = "it" if len(kept) == 1 else "them"
    files = _count(len(_list(found.get("files"))), "file")
    if markers:
        seen = (f"echolot found {_count(markers, 'marker')} named {found.get('prefix') or ''}… in {files} when the "
                "investigation closed")
    elif lines:
        seen = (f"echolot found {_count(lines, 'line')} tagged echolot:mark in {files} when the investigation "
                "closed")
    elif kept:
        seen = (f"echolot found {_count(len(kept), 'line')} tagged echolot:mark when the investigation closed, with "
                f"more on {them} than the marker")
    else:
        if cleanup.get("claimed") == "left":
            return "yellow", "The agent and echolot disagree about the markers", \
                f"{claim}, and echolot found none in the sources when the investigation closed."
        return "green", "Nothing was left in the code", \
            (f"{claim}, and echolot found none" if claim else "echolot found no temporary markers") \
            + " in the sources when the investigation closed."
    removable = found.get("removable")
    how = (f" The command echolot mark --remove takes out {removable} of them."
           if markers and isinstance(removable, int) and removable > 0 else "")
    where = verdict_mod.places(kept)
    if kept and (markers or lines):
        how += (f" {_count(len(kept), 'line')} tagged echolot:mark {'has' if len(kept) == 1 else 'have'} more on "
                f"{them} than the marker, and echolot mark --remove leaves {them}: take the marker out by hand, at "
                f"{where}.")
    elif kept:
        how += f" The command echolot mark --remove leaves {them}: take the marker out by hand, at {where}."
    return "yellow", "Temporary markers are still in the code", \
        (f"{claim}, and {seen}." if claim else seen[0].upper() + seen[1:] + ".") + how


def _round(n: int, report: dict[str, Any] | None, opened: Any, cited: _Cited | None) -> str:
    """What the report of one round showed: the window, how many checks found
    something, how much of the main thread's time its rows name, how many of
    the agent's markers were in the app."""
    if report is None:
        return (f'<li><span class="when"></span><div><b>Round {n}</b>'
                '<p class="small sub">Its report is on disk and does not read as a report.</p></div></li>')
    since = _between(opened, report.get("generated_at"))
    when = f"after {_lasted(since)}" if since is not None else ""
    ctx = _ctx(report)
    runs = _runs(report)
    facts = []
    # A window whose anchor missed, in some recordings or in all, or that
    # never closed, is not the scenario's: the report page gives it no time
    # (#557), and nor does its round here. What was recorded is counted with
    # the scenario's own word, as for a round with no miss.
    kind, kinds = ctx.kind, ctx.kind + ("es" if ctx.kind == "launch" else "s")
    if plain.window_warning({"window": _dict(report.get("window"))}):
        facts.append(f"no measured {kind}")
    elif ctx.window_ms:
        facts.append(f"the {kind} takes {plain.hum(ctx.window_ms)}")
    facts.append(f"{_count(runs, kind, kinds)} recorded")
    summary = _dict(report.get("summary"))
    ran, fired = summary.get("detectors_run"), summary.get("detectors_fired")
    if isinstance(ran, int) and isinstance(fired, int) and ran:
        facts.append(f"{fired} of {_count(ran, 'check')} found something")
    markers = _dict(report.get("markers"))
    prefix = markers.get("prefix")
    if isinstance(prefix, str) and prefix:
        names = markers.get("in_trace")
        if not isinstance(names, list):
            names = [r.get("location") for r in _list(markers.get("rows"))
                     if isinstance(r, dict) and str(r.get("location") or "").startswith(prefix)]
        facts.append(f"{_count(len(names), 'temporary marker')} in the app" if names
                     else "no temporary markers in the app")
    line = " · ".join(facts)
    card = [f"<p>{e(line[0].upper() + line[1:])}</p>"]
    share = _dict(_dict(report.get("window")).get("main_thread")).get("in_rows_pct")
    if _ms(share):
        share = min(float(share), 100.0)
        card.append('<div class="rowf meter-h"><span class="sub">Rows name this much of the main thread\'s window</span>'
                    f'<b class="num">{share:.0f}%</b></div><div class="meter"><i style="width:{share:.1f}%"></i></div>')
    names = cited.by_round.get(n) if cited else None
    if names:
        shown = ", ".join(f"<code>{e(name)}</code>" for name in names[:ROUND_NAMES])
        more = f" and {len(names) - ROUND_NAMES} more" if len(names) > ROUND_NAMES else ""
        card.append(f'<p class="sub">The answer cites {_count(len(names), "row")} of this round: {shown}{more}.</p>')
    return (f'<li><span class="when">{e(when)}</span><div><b>Round {n}</b>'
            f'<div class="repcard">{"".join(card)}</div></div></li>')


def _timeline(rounds: list[tuple[int, dict[str, Any] | None]], hunt: dict[str, Any], cited: _Cited | None) -> str:
    if not rounds:
        return ('<p class="small sub measure">No round is on record yet. The first one appears here once the app '
                "has been recorded and the recordings analysed.</p>")
    loop = ("One round is one pass of the same loop: record the app, read the report, decide where to look next.")
    if _none_recorded(hunt, len(rounds)):
        loop = (f"Nothing was recorded in this investigation: {_its(len(rounds))} read recordings made before it "
                "opened, or outside it.")
    return (f'<p class="small sub measure">{loop} While an agent works, this list is what fills in.</p>'
            '<ol class="timeline">'
            + "".join(_round(n, report, hunt.get("opened_at"), cited) for n, report in rounds) + "</ol>")


def _about(hunt: dict[str, Any], rounds: list[tuple[int, dict[str, Any] | None]], is_open: bool,
           local: bool = False) -> str:
    rows = []
    if _str(hunt.get("question")):
        rows.append(("Asked", e(_str(hunt["question"]))))
    if _str(hunt.get("since")):
        rows.append(("After which change", e(_str(hunt["since"]))))
    if _str(hunt.get("scenario")):
        rows.append(("Scenario", f"<code>{e(_str(hunt['scenario']))}</code>"))
    if hunt.get("opened_at"):
        rows.append(("Opened", e(_stamp(hunt["opened_at"], local))))
    if is_open and hunt.get("touched_at"):
        rows.append(("Last worked on", e(_stamp(hunt["touched_at"], local))))
    if not is_open and hunt.get("closed_at"):
        took = _between(hunt.get("opened_at"), hunt["closed_at"])
        rows.append(("Closed", e(_stamp(hunt["closed_at"], local)) + (f", after {e(_lasted(took))}" if took else "")))
    recordings = sum(_runs(report) for _, report in rounds if report is not None)
    work = _count(len(rounds), "round") + (f", {_count(recordings, 'recording')} in all" if recordings else "")
    if _none_recorded(hunt, len(rounds)):
        work += (", none of them recorded in this investigation: they were made before it opened, or outside it"
                 if recordings else ", nothing recorded in this investigation")
    filed = hunt.get("reports")
    if isinstance(filed, int) and not isinstance(filed, bool) and filed > len(rounds):
        gone = filed - len(rounds)
        work += f". {_count(filed, 'report')} were filed, and {gone} of them {'is' if gone == 1 else 'are'} no longer on disk"
    rows.append(("Rounds", e(work)))
    n = hunt.get("n")
    files = "The reports of the rounds are in the folder <code>reports</code> beside this page."
    if isinstance(n, int) and not isinstance(n, bool):
        files += (f" <code>echolot hunt --show {n}</code> prints this investigation as text, and "
                  f"<code>echolot reflect --hunt {n}</code> reports how the agent that worked in it went.")
    rows.append(("Files", files))
    return kit.fold("About this investigation", kit.pairs(rows), "big")


def _process(rounds: list[tuple[int, dict[str, Any] | None]]) -> Any:
    for _, report in rounds:
        name = _dict(_dict(report).get("window")).get("process")
        if name:
            return name
    return None


def render(hunt: dict[str, Any], reports: verdict_mod.Reports, again: str | None = None,
           action_html: str = "", steps: Sequence[tuple[str, str, bool]] = (),
           crumb: str | Sequence[str] | None = None, policy: str | None = None,
           agent: dict[str, Any] | None = None, local_time: bool = False) -> str:
    """The page for an investigation, as one self-contained HTML document.

    `hunt` is its record as `hunt.json` holds it and `reports` are the
    reports it filed, by number (`hunt.archived_reports`). `again` is the
    command that checks the app once more, for the reader to copy.

    The rest is for a page that is served, and a file on disk passes none of
    it. `action_html` goes into the one place kept for a button that can
    start that check itself. `steps` is the path in the top bar to the other
    pages, each `(label, href, is_this_page)`, and `crumb` is what the bar
    says the page is about in place of the process and the scenario: one
    string, or several. Both go to `kit.topbar` as they are. `policy` is
    the content policy of a served page (`kit.SERVED`), which lets the button
    in `action_html` send its form. `local_time` says each moment in the
    local time of this machine, with its zone, as the start page does; a file
    keeps UTC, which reads the same wherever it was written.

    `agent` is an agent the server started in this investigation
    (`pages/dig_page.panel`): `html`, drawn under the page's headline, with
    its own `css` and `js`, `head` for a page that loads itself again, and
    `lead`, the line under the headline of an open investigation once no
    agent works in it.
    """
    hunt = _dict(hunt)
    rounds = _rounds(reports)
    status = hunt.get("status") or "open"
    is_open = status == "open"
    verdict = hunt.get("verdict") if isinstance(hunt.get("verdict"), dict) else None
    question = _str(hunt.get("question"))
    conclusion = _str(hunt.get("conclusion"))
    beside = _how_long(hunt, len(rounds), is_open)
    cited = _Cited(reports)
    parts: list[str] = []
    panels: list[str] = []
    folds: list[str] = []
    rows = False

    if verdict is not None:
        causes, more = _causes(verdict), _suggestions(verdict)
        rows = bool(causes or more)
        headline, rest = _headline(_str(verdict.get("summary")), "What the investigation found")
        # The summary is the agent's and stands alone: a sentence of the
        # page's after it said again what a summary says already.
        lead = _said(rest)
        # `--done "<text>" --verdict <file>` keeps both: the text is said too.
        words = f'<p class="measure">{_said(conclusion)}</p>' \
            if conclusion and conclusion != _str(verdict.get("summary")) else ""
        kind = verdict_mod.reads_as(verdict, hunt.get("verdict_check"))
        words += _against(verdict, kind)
        parts.append(_head(kit.state("Investigation finished", "found", large=True), beside, headline, lead, words))
        # The panels first: they look the cited rows up, and what the page
        # says of the rounds and of the citations is counted from that.
        piece = _piece(verdict, cited)
        panels = [_cause_panel(c, cited) for c in causes] + [_more_panel(s) for s in more]
        if rows:
            parts += [_NO_SCRIPT, piece, _table(causes, more), _again(again, action_html)]
        else:
            parts += [piece, _nothing(verdict)]
        folds.append(_trust(causes, _dict(hunt.get("verdict_check")), cited, kind))
        folds.append(kit.fold("How it got there, round by round", _timeline(rounds, hunt, cited), "big"))
        if rows and _lists(verdict):
            folds.append(kit.fold("What was ruled out, and what else was measured", _lists(verdict), "big"))
    elif is_open:
        parts.append(_head(kit.state("Investigation in progress", "open", large=True), beside,
                           question or "An investigation is open",
                           (agent or {}).get("lead") or
                           "No answer yet. Each round the agent records appears below, and the answer takes "
                           "this page's place when the investigation closes."))
        parts.append('<section class="stack gap-3"><h2 class="h2">The rounds so far</h2>'
                     + _timeline(rounds, hunt, None) + "</section>")
    elif status == "concluded":
        headline, rest = _headline(conclusion, "What the investigation came to")
        paragraphs = [p for p in re.split(r"\n\s*\n", rest) if p.strip()]
        lead = _said(paragraphs[0]) if paragraphs else ""
        text = "".join(f'<p class="measure">{_said(p)}</p>' for p in paragraphs[1:])
        note = ('<p class="small sub measure">This investigation was closed with a text and no structured answer, '
                "so there is no table of causes to show: the text stands as it was written.</p>")
        parts.append(_head(kit.state("Investigation finished", "found", large=True), beside, headline, lead,
                           text + note))
        parts.append(_again(again, action_html))
        folds.append(kit.fold("How it got there, round by round", _timeline(rounds, hunt, None), "big"))
    else:
        left = ("It was left without an answer: the next question was opened before this one was closed."
                if status == "abandoned" else "It has no answer on record.")
        parts.append(_head(kit.state("Investigation left unfinished", "left", large=True), beside,
                           question or "An investigation with no answer", left))
        parts.append('<section class="stack gap-3"><h2 class="h2">The rounds it went through</h2>'
                     + _timeline(rounds, hunt, None) + "</section>")
    folds.append(_about(hunt, rounds, is_open, local_time))
    if agent and agent.get("html"):
        parts.insert(1, agent["html"])

    main = ('<main><div class="wide stack gap-6">' + "".join(parts) + "<hr>"
            + f'<div>{"".join(folds)}</div>' + "".join(panels) + "</div></main>")
    crumbs = [_process(rounds), hunt.get("scenario")] if crumb is None else \
        [crumb] if isinstance(crumb, str) else list(crumb)
    body = (f'<div class="page">{kit.topbar(crumbs, steps)}{main}</div>'
            + (kit.peek("row", "Back to the answer") if rows else ""))
    title = "echolot answer" + (f": {render_mod.clip(question, 80)}" if question else "")
    extra = agent or {}
    return kit.document(title, body, css=CSS + tuple(extra.get("css") or ()), js=JS + tuple(extra.get("js") or ()),
                        policy=policy, head_html=extra.get("head") or "")
