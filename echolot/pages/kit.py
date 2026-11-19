"""The pieces a page is arranged from. Nothing here knows which page it is in.

Every function returns HTML as a string and escapes what it is given, unless
its argument is named `html` or its docstring says the text is already markup.
The classes are the ones `base.css`, `table.css` and `picks.css` style, and the
ids are the ones `base.js`, `table.js` and `picks.js` look for.
"""

from __future__ import annotations

import functools
import html
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..plain import FINE, NEEDS_ATTENTION, NOT_CHECKED, WORTH_A_LOOK

HERE = Path(__file__).parent


def e(text: Any) -> str:
    """Text for an element or for a quoted attribute."""
    return html.escape("" if text is None else str(text), quote=True)


@functools.lru_cache(maxsize=None)
def asset(name: str) -> str:
    """A stylesheet or a script of the package, as text."""
    return (HERE / name).read_text(encoding="utf-8").strip("\n")


_CODE = re.compile(r"`([^`]+)`")


def prose(text: Any) -> str:
    """A sentence of the report as markup: a name written in backticks, the
    way `plain` writes one, becomes <code>."""
    parts = _CODE.split("" if text is None else str(text))
    return "".join(f"<code>{e(p)}</code>" if i % 2 else e(p) for i, p in enumerate(parts))


# ---------------------------------------------------------------------------
# the document

# Nothing is fetched, and the browser is told to hold the page to that: with
# this policy an address that got into the page by any road is not loaded.
_POLICY = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
           "base-uri 'none'; form-action 'none'")


def document(title: str, body_html: str, *, css: Iterable[str], js: Iterable[str]) -> str:
    """One self-contained file: the named stylesheets and scripts of the
    package are written into it, in the order given.

    The root carries `no-js` until the first script runs, so a style can tell
    a reader with script switched off (`.needs-js` is hidden for them).
    """
    style = "\n".join(asset(name) for name in css)
    script = "\n".join(asset(name) for name in js)
    return (
        '<!doctype html>\n<html lang="en" class="no-js">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{_POLICY}">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{e(title)}</title>\n"
        '<script>document.documentElement.className = "js";</script>\n'
        f"<style>\n{style}\n</style>\n</head>\n<body>\n{body_html}\n"
        f"<script>\n{script}\n</script>\n</body>\n</html>\n")


MARK = ('<svg viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="13" fill="var(--mark)"/>'
        '<circle cx="14" cy="8" r="2.6" fill="var(--mark-a)"/><circle cx="9.5" cy="14.5" r="2.6" fill="var(--mark-b)"/>'
        '<circle cx="18.5" cy="14.5" r="2.6" fill="var(--mark-b)"/><circle cx="14" cy="20.5" r="2.6" fill="var(--mark-c)"/></svg>')


def topbar(crumbs: Sequence[str], steps: Sequence[tuple[str, str, bool]] = ()) -> str:
    """The bar at the top: the mark, the name, where this page is about (the
    app, the scenario), and the steps as a path when there are other pages to
    go to. A step is `(label, href, is_this_page)`."""
    crumb = '<i aria-hidden="true"></i>'.join(e(c) for c in crumbs if c)
    path = ""
    if steps:
        here_attr = ' aria-current="page"'
        links = "".join(f'<a href="{e(href)}"{here_attr if here else ""}>{e(label)}</a>'
                        for label, href, here in steps)
        path = f'<nav class="steps" aria-label="Steps">{links}</nav>'
    return (f'<header class="top"><div class="top-in"><span class="brand">{MARK}<span>echolot</span></span>'
            + (f'<span class="crumb">{crumb}</span>' if crumb else "") + path + "</div></header>")


# ---------------------------------------------------------------------------
# a level of attention: an icon with its own shape, a word, a tint

TONE = {NEEDS_ATTENTION: "red", WORTH_A_LOOK: "yellow", FINE: "green", NOT_CHECKED: "grey"}
WORD = {NEEDS_ATTENTION: "Needs attention", WORTH_A_LOOK: "Worth a look",
        FINE: "Fine", NOT_CHECKED: "Not checked"}

_SHAPE = {
    "red": ('<circle cx="8" cy="8" r="7" fill="var(--red)"/>'
            '<path d="M8 4.3v4.4" stroke="var(--glyph)" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="11.4" r="1.05" fill="var(--glyph)"/>'),
    "yellow": ('<path d="M8 1.7 15 14H1L8 1.7Z" fill="var(--amber)" stroke="var(--amber)" stroke-width="1.2" stroke-linejoin="round"/>'
               '<path d="M8 6.1v3.6" stroke="var(--glyph-amber)" stroke-width="1.6" stroke-linecap="round"/>'
               '<circle cx="8" cy="11.9" r=".95" fill="var(--glyph-amber)"/>'),
    "green": ('<circle cx="8" cy="8" r="7" fill="var(--green)"/>'
              '<path d="m4.7 8.3 2.2 2.2 4.4-4.8" stroke="var(--glyph)" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'),
    "grey": ('<circle cx="8" cy="8" r="6.2" fill="none" stroke="var(--grey)" stroke-width="1.6"/>'
             '<path d="M5.2 8h5.6" stroke="var(--grey)" stroke-width="1.6" stroke-linecap="round"/>'),
}
TONES = tuple(_SHAPE)

TW = '<svg class="tw" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.8 11 8l-5 4.2Z" fill="currentColor"/></svg>'
_LINE = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.6 8.4 2.9 2.9 5.9-6.5"/></svg>'
CLOSE = f'<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" {_LINE}/></svg>'
CHEV = f'<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" {_LINE}/></svg>'
BACK = f'<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m10 4-4 4 4 4" {_LINE}/></svg>'
UP = f'<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 10 4-4 4 4" {_LINE}/></svg>'
DOWN = f'<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" {_LINE}/></svg>'


def tone(level: Any) -> str:
    """The colour family of a level. A level this version does not know is
    grey: it is not claimed to be fine, nor to need anything."""
    return TONE.get(level, "grey")


def word(level: Any) -> str:
    return WORD.get(level) or str(level or "unknown").replace("_", " ").capitalize()


def icon(kind: str) -> str:
    return f'<svg class="ico" viewBox="0 0 16 16" aria-hidden="true">{_SHAPE[kind]}</svg>'


def pill(level: Any, large: bool = False) -> str:
    """A level as it is always shown: the icon, the word and the tint together."""
    kind = tone(level)
    return f'<span class="st {kind}{" lg" if large else ""}">{icon(kind)}{e(word(level))}</span>'


def sprites() -> str:
    """One icon of each level, hidden, for a script to copy from."""
    return '<div hidden>' + "".join(f'<span data-icon="{k}">{icon(k)}</span>' for k in TONES) + "</div>"


# ---------------------------------------------------------------------------
# a fold, a list


def fold(summary: str, body_html: str, cls: str = "") -> str:
    """<details>: a triangle and a name, no box. `big` is a large fold."""
    return (f'<details class="tg{" " + cls if cls else ""}"><summary>{TW}<span>{e(summary)}</span></summary>'
            f'<div class="tg-b">{body_html}</div></details>')


def quiet_list(items: Iterable[tuple[str, str, str]]) -> str:
    """Rows of an icon, a name and a sentence: `(tone, name, sentence)`."""
    return '<ul class="quiet-list">' + "".join(
        f"<li>{icon(k)}<div><b>{e(name)}</b><p>{e(said)}</p></div></li>" for k, name, said in items) + "</ul>"


# ---------------------------------------------------------------------------
# figures and charts


def ms(v: float) -> str:
    """Milliseconds as a figure: 1,919 · 61.6 · 0.44."""
    if v >= 100:
        return f"{v:,.0f}"
    text = f"{v:.1f}" if v >= 1 else f"{v:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def exact(v: float) -> str:
    """The same number as the report holds it, for a tooltip."""
    return f"{v:,.2f}"


_STEPS = (1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10)


def nice_top(v: float) -> float:
    """A round maximum for an axis: the first of 1, 1.2, 1.6, 2, 2.4, 3, 4, 5,
    6, 8 times a power of ten that holds `v`. Each quarters into round ticks."""
    if not v or v <= 0 or not math.isfinite(v):
        return 1.0
    power = 10.0 ** math.floor(math.log10(v))
    for step in _STEPS:
        if step * power >= v * (1 - 1e-9):
            return round(step * power, 6)
    return round(10 * power, 6)


def _pct(v: float, top: float) -> str:
    return f"{max(0.0, min(v, top)) / top * 100:.2f}%"


def _tick(v: float) -> str:
    text = f"{v:,.2f}".rstrip("0").rstrip(".")
    return text or "0"


def chart(rows: Sequence[dict[str, Any]], top: float, *, mark: float | None = None,
          unit: str = "ms, median", title: str = "") -> str:
    """One form for every chart: a row per thing measured, a thin bar from a
    common baseline, a dot per run, the figures in text at the right.

    A row is a dict: `name`, and `mono` when the name is an identifier; `subs`,
    the lines under the name; `value`, where the bar ends; `dots`, one per run;
    `figure`, the bold text at the right, and `notes`, the lines under it;
    `tip`, the exact figures for the tooltip (a row with one can take focus).
    `mark` is where the check fires on the same axis, or None.

    A row with `quiet` is drawn in the chart's soft neutral: it stands beside
    the others to make up the whole and is no measure of what they measure.
    The colour is set on that bar alone and not by a class, because the
    stylesheet is written into every page: a page with no such row keeps the
    bytes it had.
    """
    out = [f'<p class="chart-h">{e(title)}</p>' if title else "",
           '<div class="cr ax" aria-hidden="true"><span></span><div class="ticks">'
           + "".join(f'<span style="left:{i * 25}%">{_tick(top * i / 4)}</span>' for i in range(5))
           + f'</div><span class="ax-u">{e(unit)}</span></div>']
    line = f'<i class="thr" style="left:{_pct(mark, top)}"></i>' if mark and mark <= top else ""
    for r in rows:
        name = f'<span class="mono">{e(r["name"])}</span>' if r.get("mono", True) else f'<span>{e(r["name"])}</span>'
        subs = "".join(f'<span class="cr-s">{e(s)}</span>' for s in r.get("subs") or () if s)
        dots = "".join(f'<i class="dot" style="left:{_pct(v, top)}"></i>' for v in r.get("dots") or ())
        notes = "".join(f"<span>{e(n)}</span>" for n in r.get("notes") or () if n)
        tip = r.get("tip")
        focus = f' tabindex="0" data-tip="{e(chr(10).join(tip))}"' if tip else ""
        fill = ";background:var(--data-soft)" if r.get("quiet") else ""
        out.append(f'<div class="cr"{focus}><div class="cr-n">{name}{subs}</div>'
                   f'<div class="cr-p">{line}<i class="bar" style="width:{_pct(r["value"], top)}{fill}"></i>{dots}</div>'
                   f'<div class="cr-v"><b>{e(r["figure"])}</b>{notes}</div></div>')
    return f'<div class="chart">{"".join(out)}</div>'


def chart_key(bar: str = "median", dot: str | None = None, mark: str | None = None) -> str:
    """What the marks of a chart are, under it: the bar, the dot, the threshold."""
    return (f'<p class="key"><span><i class="k-bar"></i>{e(bar)}</span>'
            + (f'<span><i class="k-dot"></i>{e(dot)}</span>' if dot else "")
            + (f'<span><i class="k-thr"></i>{e(mark)}</span>' if mark else "") + "</p>")


# ---------------------------------------------------------------------------
# the table: a filter by level, a grid with table roles, one panel


def views(items: Sequence[tuple[str, str, str, int]], label: str = "Show") -> str:
    """The filter above a table, which is the page's counts as well:
    `(group, icon_html, word, count)`, the first one pressed. A button is
    written disabled and the script enables it, so that it reads as a count
    and nothing else where script is off, or where it counts nothing."""
    return f'<div class="views" role="group" aria-label="{e(label)}">' + "".join(
        f'<button type="button" class="view" data-view="{e(k)}" data-n="{n}" aria-pressed="{"true" if i == 0 else "false"}" disabled>'
        f'<span class="v-i">{ic}</span><span class="v-w">{e(w)}</span><b>{n}</b></button>'
        for i, (k, ic, w, n) in enumerate(items)) + "</div>"


def head(cells: Sequence[tuple[str, str, bool]]) -> str:
    """The head row: `(class, text, shown)`. A column with no visible name
    still has one for a screen reader."""
    return '<div class="tb-h" role="row">' + "".join(
        f'<span role="columnheader" class="{cls}">' + (e(text) if shown else f'<span class="vh">{e(text)}</span>') + "</span>"
        for cls, text, shown in cells) + "</div>"


def details_btn(ident: str, what: str) -> str:
    """The keyboard's way into the panel: one per row."""
    return (f'<button type="button" class="rowbtn needs-js" data-open="{e(ident)}" aria-expanded="false" aria-controls="peek" '
            f'aria-label="Details: {e(what)}">Details{CHEV}</button>')


def template(ident: str, inner_html: str) -> str:
    """What the panel shows for one row, kept until the row is opened."""
    return f'<template id="peek-{e(ident)}">{inner_html}</template>'


def deep(inner_html: str) -> str:
    """The technical layer of a panel, under a hairline: shown at once."""
    return f'<div class="deep"><h3 class="deep-h">Details</h3>{inner_html}</div>'


def peek(what: str = "check", back: str = "Back to the report") -> str:
    """The one panel of a page. `what` names a row in the labels of Previous
    and Next; `back` is what Close reads on a phone."""
    return (
        '<dialog class="peek" id="peek"><div class="peek-bar">'
        f'<button type="button" class="btn quiet" id="peek-close"><span class="on-desk">{CLOSE}</span><span class="on-phone">{BACK}</span>'
        f'<span class="on-desk">Close</span><span class="on-phone">{e(back)}</span></button><span class="pos" id="peek-pos"></span>'
        f'<button type="button" class="btn quiet" id="peek-prev" aria-label="The previous {e(what)}"><span class="on-desk">Previous</span><span class="on-phone">{UP}</span></button>'
        f'<button type="button" class="btn quiet" id="peek-next" aria-label="The next {e(what)}"><span class="on-desk">Next</span><span class="on-phone">{DOWN}</span></button>'
        '</div><div class="peek-b" id="peek-b"></div></dialog>')


# ---------------------------------------------------------------------------
# ticking rows, and the request for an agent


def tick_box(ident: str, kind: str, title: str, request: str, twin: bool = False) -> str:
    """A box to tick. `request` is what goes to the agent when it is ticked,
    one line per row. A `twin` is the same tick drawn a second time, in the
    panel: the two move together."""
    own = "" if twin else f'id="pick-{e(ident)}" '
    return (f'<label class="ck needs-js"><input type="checkbox" class="pick" {own}data-id="{e(ident)}" data-level="{e(kind)}" '
            f'data-title="{e(title)}" data-req="{e(request)}" aria-label="Tick: {e(title)}">'
            f'<span class="ck-box">{CHECK}</span></label>')


def dock(total: int) -> str:
    """The bar at the bottom that leads to the agent, there before anything
    is ticked. `total` is how many rows can be ticked."""
    return (f'<div class="dock needs-js" id="dock"><div class="dock-in"><p class="dock-t" id="dock-t" role="status">Nothing ticked yet</p><div class="dock-b">'
            f'<button type="button" class="btn quiet" id="dock-all" data-n="{total}">Tick everything red and yellow</button>'
            '<button type="button" class="btn quiet" id="dock-clear" hidden>Clear</button></div>'
            '<button type="button" class="btn primary" id="dock-go" disabled>Dig deeper</button></div></div>')


def ask(one: str, many: str, tail: str) -> str:
    """The request for an agent, to copy. The text is put together in the page
    from the rows of what is ticked, between a first line (`one` for a single
    row, `many` for several) and a last one (`tail`)."""
    return (
        f'<dialog class="ask" id="ask" aria-labelledby="ask-h" data-one="{e(one)}" data-many="{e(many)}" data-tail="{e(tail)}"><div class="ask-in">'
        '<h2 class="h2" id="ask-h">Dig deeper with an agent</h2>'
        '<p>An agent takes these items into your code. It puts temporary markers around the suspect places and runs '
        'the app again. It comes back with the exact lines and what to change.</p>'
        '<ul class="ask-list" id="ask-list"></ul>'
        '<p class="small sub">Paste the request into Claude Code, Codex or Cursor opened on the project.</p>'
        f'<details class="tg"><summary>{TW}<span>The request text</span></summary>'
        '<div class="tg-b"><pre class="req" id="ask-text" tabindex="0"></pre></div></details>'
        '<div class="rowf"><button type="button" class="btn primary" id="ask-copy" autofocus>Copy the request</button>'
        '<button type="button" class="btn quiet" id="ask-close">Close</button></div>'
        '<p class="vh" id="ask-said" role="status"></p></div></dialog>')
