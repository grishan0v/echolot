"""The start page of `echolot ui`: is the setup ready, what to do next, the
reports there are, and every setting with what stands behind it.

The reading part of the start screen. What starts a recording (the choice of
what to check, the limit field, the button, a run in progress) is not here.

It is drawn from `echolot.ready.read` and judges nothing itself: every tone and
every sentence is in the reading, and the CLI's own words behind a sentence
are shown under "All settings". `link(name, **params)` gives the address of
another page of the same server, or None when the server has no such page:
then no link is drawn.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from . import kit
from .kit import e

CSS = ("base.css", "start.css")
JS = ("base.js", "start.js")

Link = Callable[..., "str | None"]
Steps = Sequence[tuple[str, str, bool]]

_FIRST_OF = {"green": "ready", "red": "in the way", "grey": "not checked"}


def _ready(view: dict[str, Any], again: str) -> str:
    """The three sentences, each after its icon, and Check again. The button
    is the screen's one next step while something is in the way."""
    lines = view["ready"]
    items = "".join(
        f'<li>{kit.icon(line["tone"])}<span><span class="vh">{e(_FIRST_OF[line["tone"]])}: </span>'
        f'<b>{e(line["lead"])}</b> {kit.prose(line["text"])}</span></li>' for line in lines)
    blocked = any(line["tone"] == "red" for line in lines)
    button = (f'<a class="btn {"primary" if blocked else "line"}" href="{e(again)}">Check again</a>')
    return (f'<div class="start-ready stack gap-3"><ul class="ready" aria-label="Is everything ready">{items}</ul>'
            f'<div class="rowf">{button}<span class="small sub">Reads everything anew.</span></div></div>')


def _next(step: dict[str, Any]) -> str:
    """The next step: a sentence, the command to copy, and where to run it."""
    note = (f'<div class="callout">{kit.icon("yellow")}<p class="small">{kit.prose(step["note"])}</p></div>'
            if step.get("note") else "")
    command = ""
    if step.get("command"):
        # A word of the command stays whole where it fits: a line never ends
        # inside `--scenario`. The text itself is the command, to the letter.
        words = " ".join(f'<span class="w">{e(word)}</span>' for word in step["command"].split(" "))
        command = ('<div class="cmd"><code id="next-cmd" tabindex="0">' + words + "</code>"
                   '<button type="button" class="btn line needs-js" data-copy="next-cmd">Copy</button>'
                   '<span class="vh" role="status" id="next-said"></span></div>')
    how = f'<p class="small sub measure">{kit.prose(step["how"])}</p>' if step.get("how") and command else ""
    return (f'<section class="stack gap-3" aria-labelledby="next-h"><h2 class="h2" id="next-h">What to do next</h2>'
            f'{note}<p class="measure">{kit.prose(step["text"])}</p>{command}{how}</section>')


def _reports(view: dict[str, Any], link: Link) -> str:
    """The last report of each scenario, then every investigation with the
    reports it filed. A report opens; what does not read is said, not linked."""
    several = len(view["latest"]) > 1
    cards = []
    for rep in view["latest"]:
        href = link("report-of", scenario=rep["key"]) if rep["key"] else link("report")
        verdict = rep.get("verdict")
        if rep.get("path") is None or verdict is None or not href:
            if several or rep["said"] not in ("none yet", "no report yet"):
                name = f'<b>{e(rep["scenario"])}</b> ' if several and rep.get("scenario") else ""
                cards.append(f'<li class="rep none"><p class="small sub">{name}{e(rep["said"])}</p></li>')
            continue
        name = f'<span class="small sub">{e(rep["scenario"])}</span>' if rep.get("scenario") else ""
        cards.append(
            f'<li class="rep"><div class="rowf">{kit.pill(verdict.get("level"))}{name}</div>'
            f'<p class="measure">{e(verdict.get("text") or "")}</p>'
            f'<p class="small sub says">{e(rep["said"])}</p>'
            f'<div class="rowf"><a class="btn primary" href="{e(href)}">See the report</a></div></li>')
    if not cards:
        cards.append('<li class="rep none"><p class="sub">No report yet. It appears here once the first '
                     'recordings are read.</p></li>')
    past = "".join(_investigation(inv, link) for inv in view["investigations"])
    earlier = (f'<div class="stack gap-3"><h3 class="h3">Investigations</h3>'
               f'<p class="small sub measure">What an agent was asked to find, newest first, with the reports '
               f'each one filed.</p><ul class="invs">{past}</ul></div>' if past else "")
    return (f'<section class="stack gap-5" aria-labelledby="reps-h"><div class="stack gap-3">'
            f'<h2 class="h2" id="reps-h">Reports</h2><ul class="reps">{"".join(cards)}</ul></div>{earlier}</section>')


def _investigation(inv: dict[str, Any], link: Link) -> str:
    n = inv.get("n")
    state = "open now" if inv["current"] and inv["status"] == "open" else inv["status"]
    head = (f'<p class="inv-h"><b>{e("#" + str(n) if n else "An earlier one")}</b>'
            f'<span class="small sub">{e(inv["when"])} · {e(state)}</span></p>')
    question = f'<p>{e(inv["question"])}</p>' if inv["question"] else ""
    did = f'<p class="small sub">{e(" · ".join(inv["did"]))}</p>' if inv["did"] else ""
    came = f'<p class="small">{e(inv["conclusion"])}</p>' if inv.get("conclusion") else ""
    opens = []
    for k in inv["reports"] if n else ():
        href = link("hunt-report", n=n, k=k)
        if href:
            opens.append(f'<a href="{e(href)}" aria-label="Investigation {e(n)}, report {k}">Report {k}</a>')
    # The agent's answer, once the server has a page for it.
    answer = link("answer", n=n) if n and inv.get("answered") else None
    if answer:
        opens.append(f'<a href="{e(answer)}" aria-label="Investigation {e(n)}, the agent\'s answer">Agent\'s answer</a>')
    filed = f'<p class="inv-r small">{"".join(opens)}</p>' if opens else ""
    lost = ""
    if inv["unread"]:
        which = ", ".join(str(k) for k in inv["unread"])
        lost = (f'<p class="small sub">Report {e(which)} is there and does not read.</p>' if len(inv["unread"]) == 1
                else f'<p class="small sub">Reports {e(which)} are there and do not read.</p>')
    return f'<li class="inv">{head}{question}{did}{came}{filed}{lost}</li>'


def _behind(pairs: Sequence[tuple[str, str]]) -> str:
    """The CLI's own words behind a row: which command says it, and what."""
    return "".join(f'<p class="says"><span class="who">{e(who)}</span> {kit.prose(said)}</p>' for who, said in pairs)


def _settings(view: dict[str, Any]) -> str:
    """The fold: every setting the page reads, then what stands behind the
    three ready sentences and the next step. Read-only."""
    rows = []
    for row in view["settings"]:
        more = "".join(f'<pre class="yaml" tabindex="0">{e(text)}</pre>' if "\n" in text
                       else f"<p>{kit.prose(text)}</p>" for text in row.get("more") or ())
        each = ""
        if row.get("each"):
            items = "".join(
                "<li>" + (f'<b>{e(t["title"])}</b> ' if t.get("title") else "") + f'<code>{e(t["id"])}</code> '
                + (f'<span>{e(t["numbers"])}</span> ' if t.get("numbers") else "")
                + f'<span class="sub">{e(t["source"])}</span></li>' for t in row["each"])
            each = kit.fold("Each threshold", f'<ul class="each">{items}</ul>')
        rows.append(f'<div><dt>{e(row["label"])}</dt><dd><p>{kit.prose(row["text"])}</p>{more}{each}'
                    f'{_behind(row.get("behind") or ())}</dd></div>')
    for line in view["ready"]:
        rows.append(f'<div><dt>{e(line["lead"])}</dt><dd><p>{e(_FIRST_OF[line["tone"]].capitalize())}.</p>'
                    f'{_behind(line["behind"])}</dd></div>')
    step = view["next"]
    if step.get("behind"):
        rows.append(f'<div><dt>The next step</dt><dd>{_behind(step["behind"])}</dd></div>')
    intro = ('<p class="small sub measure">What this page reads, and what <code>echolot</code> and '
             '<code>echolot scan</code> say behind each line. Nothing here is changed from the page: '
             'the values live in <code>echolot.yml</code>.</p>')
    folder = f'<p class="small sub">Read from <code>{e(view["project"])}</code>.</p>'
    return ('<div class="start-set"><details class="tg big" id="settings"><summary>' + kit.TW
            + f'<span>All settings</span></summary><div class="tg-b">{intro}<dl class="props">{"".join(rows)}</dl>'
            f'{folder}</div></details></div>')


def render(view: dict[str, Any], link: Link, steps: Steps = ()) -> str:
    """The start page for one reading of the project (`ready.read`)."""
    name = view.get("name")
    head = (f'<div class="start-head stack gap-3"><h1 class="h1">Let\'s see how fast {e(name or "your app")} is</h1>'
            '<p class="lead">echolot opens the app on your phone a few times, records what happens inside and '
            'tells you in plain words what is fine and what needs attention. Nothing in your code changes.</p></div>')
    body = f'<div class="start-body stack gap-6">{_next(view["next"])}{_reports(view, link)}</div>'
    main = (f'<main><div class="wide start">{head}{_ready(view, link("start") or "")}{body}{_settings(view)}'
            "</div></main>")
    page = f'<div class="page plain">{kit.topbar([name, view.get("scenario")], steps)}{main}</div>'
    return kit.document("echolot" + (f": {name}" if name else ""), page, css=CSS, js=JS)


def notice(title: str, text: str, back: str | None = None, steps: Steps = ()) -> str:
    """A page that has one thing to say: there is no such page, or nothing to
    show on it yet. `text` is prose, with names in backticks."""
    way = f'<div class="rowf"><a class="btn line" href="{e(back)}">Back to the start</a></div>' if back else ""
    main = (f'<main><div class="col stack gap-5"><div class="stack gap-3"><h1 class="h1">{e(title)}</h1>'
            f'<p class="lead">{kit.prose(text)}</p></div>{way}</div></main>')
    return kit.document(f"echolot: {title}", f'<div class="page plain">{kit.topbar([], steps)}{main}</div>',
                        css=CSS, js=JS)
