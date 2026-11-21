"""The start page of `echolot ui`: is the setup ready, the button that checks
the app, the reports there are, and every setting with what stands behind it.

It is drawn from `echolot.ready.read` and, for the button and a run, from
`echolot.check.view`, and judges nothing itself: every tone and every sentence
is in those readings, and the CLI's own words behind a sentence are shown
under "All settings". `link(name, **params)` gives the address of another
page of the same server, or None when the server has no such page: then no
link, and no button, is drawn.

The button is a form, and the page of a run refreshes itself, so everything
works with script switched off. With script on, the list of a run is redrawn
in place and the line beside the button follows the choices (`start.js`).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from . import kit
from .kit import e

CSS = ("base.css", "start.css")
JS = ("base.js", "start.js")

Link = Callable[..., "str | None"]
Steps = Sequence[tuple[str, str, bool]]

# How often the page of a run asks again where script is off, in seconds.
# With script on the list is asked for once a second and redrawn in place.
REFRESH_S = 3
_REFRESH = f'<noscript><meta http-equiv="refresh" content="{REFRESH_S}"></noscript>\n'

# The mark of a row that is waiting, and of the one in progress: a ring, and
# a ring that turns.
_RING, _SPIN = '<i class="ring"></i>', '<i class="spin"></i>'
_ROW_ICON = {"done": "green", "failed": "red", "stopped": "grey", "cut": "grey"}


def _ready(view: dict[str, Any], again: str, lead: bool = True) -> str:
    """The three sentences, each after its icon, and Refresh. The link is the
    screen's one next step while something is in the way, unless the page
    has another (`lead` is off): a project is set up with no phone at hand."""
    lines = view["ready"]
    items = "".join(
        f'<li>{kit.icon(line["tone"])}<span><span class="vh">{e(line["state"])}: </span>'
        f'<b>{e(line["lead"])}</b> {kit.prose(line["text"])}</span></li>' for line in lines)
    blocked = lead and any(line["tone"] == "red" for line in lines)
    button = (f'<a class="btn {"primary" if blocked else "line"}" href="{e(again)}">Refresh</a>')
    return (f'<div class="start-ready stack gap-3"><ul class="ready" aria-label="Is everything ready">{items}</ul>'
            f'<div class="rowf">{button}<span class="small sub">Reads everything anew.</span></div></div>')


def _next(step: dict[str, Any], red: bool = True) -> str:
    """The next step: a sentence, the command to copy, and where to run it."""
    said = " ".join(filter(None, [step.get("note"), step.get("red") if red else None]))
    note = (f'<div class="callout">{kit.icon("yellow")}<p class="small">{kit.prose(said)}</p></div>' if said else "")
    command = ""
    if step.get("command"):
        # A word of the command stays whole where it fits: a line never ends
        # inside `--scenario`. The text itself is the command, to the letter.
        words = " ".join(f'<span class="w">{e(word)}</span>' for word in step["command"].split(" "))
        command = ('<div class="command"><code id="next-cmd" tabindex="0">' + words + "</code>"
                   '<button type="button" class="btn line needs-js" data-copy="next-cmd">Copy</button>'
                   '<span class="vh" role="status" id="next-said"></span></div>')
    how = f'<p class="small sub measure">{kit.prose(step["how"])}</p>' if step.get("how") and command else ""
    return (f'<section class="stack gap-3" aria-labelledby="next-h"><h2 class="h2" id="next-h">What to do next</h2>'
            f'{note}<p class="measure">{kit.prose(step["text"])}</p>{command}{how}</section>')


# ---------------------------------------------------------------------------
# the button, and a run


def _plain(text: Any) -> str:
    """A sentence for a place that takes no markup: a name without its backticks."""
    return str(text or "").replace("`", "")


def run_rows(rows: Sequence[dict[str, str]]) -> str:
    """The rows of a run, every one there from the start: an icon that says
    the same as the words after the name."""
    out = []
    for row in rows:
        how = row["state"]
        mark = _SPIN if how == "now" else kit.icon(_ROW_ICON[how]) if how in _ROW_ICON else _RING
        out.append(f'<li class="{e(how)}"><span class="run-i">{mark}</span><span class="run-t"><b>{e(row["name"])}</b> '
                   f'<span>{e(row["note"])}</span></span></li>')
    return "".join(out)


def progress(found: dict[str, Any] | None) -> str:
    """What a page that shows a run asks for while it runs: the rows as they
    stand now, and whether it is still running. A piece of markup, not a page:
    `start.js` puts the rows where the old ones are."""
    status = (found or {}).get("status") or "none"
    rows = run_rows((found or {}).get("rows") or ())
    return (f'<div id="run-now" data-state="{e(status)}" data-said="{e(_plain((found or {}).get("words")))}">'
            f'<ol>{rows}</ol></div>')


def _counts(tally: Sequence[tuple[str, int, str]]) -> str:
    return '<ul class="counts">' + "".join(
        f"<li>{kit.icon(kind)}<b>{n}</b> {e(words)}</li>" for kind, n, words in tally) + "</ul>"


def _said(said: dict[str, Any] | None) -> str:
    """What the command that failed printed, as it printed it, under which
    command it was."""
    if not said:
        return ""
    lines = said.get("lines") or ["(it printed nothing)"]
    where = (f'<p class="small sub">Everything it printed is in <code>{e(said["log"])}</code>.</p>'
             if said.get("log") else "")
    return (f'<div class="stack gap-2"><p class="small"><code>{e(said["who"])}</code> said:</p>'
            f'<pre class="printed" tabindex="0">{e(chr(10).join(lines))}</pre>{where}</div>')


def _run(run: dict[str, Any], link: Link) -> str:
    """The last run: while it goes, Stop and the list that ticks itself; once
    it ended, the list as it stood and what the page says of the end."""
    going = run["status"] == "running"
    rows = f'<ol class="run" id="run-rows" aria-label="The check, step by step">{run_rows(run["rows"])}</ol>'
    if going:
        stop = link("check-stop")
        button = (f'<form method="post" action="{e(stop)}" class="rowf"><button type="submit" class="btn line press"'
                  f'{" disabled" if run["stopping"] else ""}>Stop</button></form>' if stop else "")
        poll = link("check-state")
        note = f'<p class="small sub measure" id="run-note">{kit.prose(run["note"])}</p>'
        return ('<div class="stack gap-3" id="run" data-state="running"'
                + (f' data-poll="{e(poll)}"' if poll else "") + f'>{button}{rows}{note}'
                f'<p class="vh" role="status" id="run-said">{e(_plain(run["words"]))}</p></div>')
    parts = [rows]
    if run["title"] or run["text"]:
        text = f" {kit.prose(run['text'])}" if run["text"] else ""
        parts.append(f'<p class="measure" id="run-end"><b>{e(run["title"])}</b>{text}</p>')
    if run["counts"]:
        parts.append(_counts(run["counts"]))
    if run["compare"]:
        after = link("after")
        more = (f' <a class="lnk" href="{e(after)}">See what changed</a>' if after else
                " The whole comparison is in <code>comparison.md</code>, beside the report.")
        parts.append(f'<p class="small sub measure" id="run-compare">It was compared with the report before it. '
                     f'<code>echolot compare</code> says: {e(run["compare"])}.{more}</p>')
    parts.append(_said(run["said"]))
    if run["todo"]:
        parts.append(f'<p class="small sub measure">{kit.prose(run["todo"])}</p>')
    return f'<div class="stack gap-3" id="run" data-state="{e(run["status"])}">{"".join(parts)}</div>'


def _choices(offer: dict[str, Any], frozen: bool) -> str:
    """One card per scenario of the config, when it names several. A radio:
    the one the page is about is chosen."""
    cards = "".join(
        f'<label class="opt"><input type="radio" name="what" form="check-form" value="{e(c["do"] + ":" + c["name"])}"'
        f'{" checked" if c["chosen"] else ""}{" disabled" if frozen else ""}><span class="rd"></span>'
        f'<span class="opt-t">{e(c["name"])}</span><span class="opt-d">{kit.prose(c["text"])}</span></label>'
        for c in offer["choices"])
    return ('<div class="stack gap-3"><h2 class="h2" id="q-what">What do you want to check?</h2>'
            f'<div class="opts" role="radiogroup" aria-labelledby="q-what">{cards}</div></div>')


def _why(offer: dict[str, Any], choice: dict[str, Any] | None) -> str | None:
    """Why a press would start nothing now: for every choice, or for this one."""
    return offer.get("why") or (choice or {}).get("why")


def _form(offer: dict[str, Any], run: dict[str, Any] | None, link: Link, report: str | None) -> str:
    """The button with the line beside it: what a press does, how long it
    takes, and that the code is not touched; or, disabled, why it cannot run.
    After a run that left a report, See the report comes first."""
    action = link("check")
    choices = offer["choices"]
    several = len(choices) > 1
    choice = next((c for c in choices if c["chosen"]), choices[0] if choices else None)
    why = _why(offer, choice)
    label = choice["label"] if choice else "Check the app"
    # With several scenarios and script off, the button cannot follow the
    # choice: it says what holds for every card, or the plain name.
    if several and len({c["label"] for c in choices}) > 1:
        label = "Check the app"
    said = why or (choice["hint"] if choice else "")
    if several and not why and len({c["hint"] for c in choices}) > 1:
        said = "Each choice says where it stands. Nothing in your code changes."
    seen = bool(run and run.get("report") and report)
    look = "line" if seen else "primary press"
    # What the script needs to follow the choice and the number of launches.
    offers = {f'{c["do"]}:{c["name"]}': {
        "label": c["label"], "hint": c["hint"], "why": _why(offer, c) or "", "hints": c["hints"],
        "anew": c["do"] == "read" and not offer.get("why") and not c.get("no_record")} for c in choices}
    hidden = "" if several or not choice else f'<input type="hidden" name="what" value="{e(choice["do"] + ":" + choice["name"])}">'
    # Recordings that wait can be read, or left: the second button records anew.
    anew = ""
    if choice and not offer.get("why") and any(c["do"] == "read" and not c.get("no_record") for c in choices):
        anew = (f'<button type="submit" name="anew" value="1" class="btn line{"" if seen else " press"}" id="check-anew"'
                f'{"" if choice["do"] == "read" else " hidden"}>Record again</button>')
    see = f'<a class="btn primary" href="{e(report)}">See the report</a>' if seen else ""
    status = ' role="status"' if why else ""
    return (f'<form method="post" action="{e(action)}" id="check-form" class="rowf go" data-offers="{e(json.dumps(offers))}">'
            f'{hidden}{see}<button type="submit" class="btn {look}" id="check-go"{" disabled" if why else ""}>{e(label)}</button>'
            f'{anew}<p class="small sub go-hint" id="check-hint"{status}>{kit.prose(said)}</p></form>')


def _check(found: dict[str, Any], step: dict[str, Any], link: Link) -> str:
    """The part of the page that checks the app: the choice of a scenario
    when there are several, a run when one is on show, and the button."""
    offer, run = found["offer"], found["run"]
    going = bool(run) and run["status"] == "running"
    parts = []
    if len(offer["choices"]) > 1:
        parts.append(_choices(offer, going))
    if step.get("note") and "what" in step:
        parts.append(f'<div class="callout">{kit.icon("yellow")}<p class="small">{kit.prose(step["note"])}</p></div>')
    if run:
        parts.append(_run(run, link))
    if not going:
        scenario = (run or {}).get("scenario")
        several = len(offer["choices"]) > 1
        report = (link("report-of", scenario=scenario) if several and scenario else link("report")) if run else None
        parts.append(_form(offer, run, link, report))
    return f'<section class="stack gap-5" id="check" aria-label="Check the app">{"".join(parts)}</section>'


def _launches(offer: dict[str, Any], frozen: bool) -> tuple[str, str] | None:
    """The one setting the page takes: how many launches one press records.
    It belongs to the button's form and is sent with it; nothing keeps it."""
    if not any(c.get("counted") for c in offer["choices"]):
        return None
    # No `required` and no `pattern`: a field inside a fold that is closed
    # cannot be shown as wrong by the browser, and the button would do
    # nothing. A number that is none is answered by the server, in a sentence.
    field = (f'<label class="field" for="launches">Record <input type="text" inputmode="numeric" maxlength="2" '
             f'id="launches" name="launches" form="check-form" value="{offer["launches"]}" '
             f'autocomplete="off" aria-describedby="launches-h"{" disabled" if frozen else ""}> launches at each press</label>')
    says = (f'<p class="small sub" id="launches-h">From 1 to {offer["most"]}. Three is quick and already shows how much '
            'the launches differ. To be sure which changes hold, a comparison needs four a side, or three against '
            'five. Nothing saves this number: <code>runner.iterations</code> in the config stays what '
            '<code>echolot collect</code> takes in a terminal.</p>')
    return "Launches in one check", field + says


def _commands(offer: dict[str, Any]) -> tuple[str, str] | None:
    """What a press runs, as a person would type it: the commands of the
    choice the page is about, one after another."""
    choice = next((c for c in offer["choices"] if c["chosen"]), None)
    if choice is None or not choice.get("runs"):
        return None
    several = len(offer["choices"]) > 1
    lines = "".join(f'<pre class="yaml" tabindex="0">{e(line)}</pre>' for line in choice["runs"])
    whose = f" for <code>{e(choice['name'])}</code>" if several else ""
    return "What a press runs", (
        f"<p>The button runs these{whose}, one after another, in the project's folder: the commands you would "
        f"type in a terminal, so the report is the same one.</p>{lines}")


def _reports(view: dict[str, Any], link: Link) -> str:
    """The last report of each scenario, then every investigation with the
    reports it filed. A report opens; what does not read is said, not linked."""
    several = len(view["latest"]) > 1
    cards = []
    for rep in view["latest"]:
        href = link("report-of", scenario=rep["key"]) if rep["key"] else link("report")
        verdict = rep.get("verdict")
        if rep.get("path") is None or verdict is None or not href:
            name = f'<b>{e(rep["scenario"])}</b> ' if several and rep.get("scenario") else ""
            cards.append(f'<li class="rep empty"><p class="sub">{name}{kit.prose(rep["plain"])}</p></li>')
            continue
        name = f'<span class="small sub">{e(rep["scenario"])}</span>' if rep.get("scenario") else ""
        cards.append(
            f'<li class="rep"><div class="rowf">{kit.pill(verdict.get("level"))}{name}</div>'
            f'<p class="measure">{e(verdict.get("text") or "")}</p>'
            f'<p class="small sub measure">{kit.prose(rep["plain"])}</p>'
            f'<div class="rowf"><a class="btn primary" href="{e(href)}">See the report</a></div></li>')
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
    did = f'<p class="small sub">{kit.prose(inv["plain"])}</p>'
    came = f'<p class="small">{e(inv["conclusion"])}</p>' if inv.get("conclusion") else ""
    opens = []
    # Its own page first: the agent's answer, or the rounds of one without.
    own = link("answer-of", n=n) if n else None
    if own:
        opens.append(f'<a href="{e(own)}" aria-label="Investigation {e(n)}: {e(inv["page"])}">{e(inv["page"])}</a>')
    for k in inv["reports"] if n else ():
        href = link("hunt-report", n=n, k=k)
        if href:
            opens.append(f'<a href="{e(href)}" aria-label="Investigation {e(n)}, report {k}">Report {k}</a>')
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


def _settings(view: dict[str, Any], launches: Sequence[tuple[str, str]] = ()) -> str:
    """The fold: the one number the button takes and the commands it runs
    (`launches`), every setting the page reads, then what `echolot` prints
    behind the sentences the page says in its own words. All but the number
    is read-only."""
    rows = list(launches or ())
    for row in [*view["settings"], *view["behind"]]:
        more = "".join(f'<pre class="yaml" tabindex="0">{e(text)}</pre>' if "\n" in text
                       else f"<p>{kit.prose(text)}</p>" for text in row.get("more") or ())
        each = ""
        if row.get("each"):
            items = "".join(
                "<li>" + (f'<b>{e(t["title"])}</b> ' if t.get("title") else "") + f'<code>{e(t["id"])}</code> '
                + (f'<span>{e(t["numbers"])}</span> ' if t.get("numbers") else "")
                + f'<span class="sub">{e(t["source"])}</span></li>' for t in row["each"])
            each = kit.fold("Each threshold", f'<ul class="each">{items}</ul>')
        said = f'<p>{kit.prose(row["text"])}</p>' if row.get("text") else ""
        rows.append((row["label"], f'{said}{more}{each}{_behind(row.get("behind") or ())}'))
    one = (" but the number of launches, which is for the button and is not saved"
           if any(label == "Launches in one check" for label, _ in launches) else "")
    intro = ('<p class="small sub measure">What this page reads, and what <code>echolot</code> and '
             f'<code>echolot scan</code> say behind each line. Nothing here is changed from the page{one}: '
             'the values live in <code>echolot.yml</code>.</p>')
    folder = f'<p class="small sub">Read from <code>{e(view["project"])}</code>.</p>'
    return ('<div class="start-set"><details class="tg big" id="settings"><summary>' + kit.TW
            + f'<span>All settings</span></summary><div class="tg-b">{intro}{kit.pairs(rows, "props settings")}'
            f'{folder}</div></details></div>')


def render(view: dict[str, Any], link: Link, steps: Steps = (), check: dict[str, Any] | None = None,
           setup: dict[str, Any] | None = None) -> str:
    """The start page for one reading of the project (`ready.read`).

    `check` is the button and the last run (`check.view`), for a server that
    takes a press. Without it the page reads only, and the next step is a
    command to copy.

    `setup` is the part that sets a project up, already drawn
    (`setup_page.parts`): what stands in place of the button and the next
    step, or in front of them, with its own styles and script.
    """
    name = view.get("name")
    step = view["next"]
    head = (f'<div class="start-head stack gap-3"><h1 class="h1">Let\'s see how fast {e(name or "your app")} is</h1>'
            '<p class="lead">echolot opens the app on your phone a few times, records what happens inside and '
            'tells you in plain words what is fine and what needs attention. Nothing in your code changes.</p></div>')
    launches: list[tuple[str, str]] = []
    going = False
    if check is not None and link("check"):
        going = bool(check["run"]) and check["run"]["status"] == "running"
        launches = [row for row in (_launches(check["offer"], going), _commands(check["offer"])) if row]
        # The button takes the step a person would take by hand, so that
        # step is not said twice. A step that is not the button's (set the
        # project up, write a config) keeps its place, after the button.
        body = _check(check, step, link) + ("" if "what" in step else _next(step, red=False))
    else:
        body = _next(step)
    css, js, lead = CSS, JS, True
    if setup:
        going = going or bool(setup.get("going"))
        # Where setup has the screen's one next step, Refresh is not it.
        lead = not setup.get("instead")
        body = setup.get("instead") or (setup.get("before") or "") + body
        css, js = CSS + tuple(setup.get("css") or ()), JS + tuple(setup.get("js") or ())
    body = f'<div class="start-body stack gap-6">{body}{_reports(view, link)}</div>'
    main = (f'<main><div class="wide start">{head}{_ready(view, link("start") or "", lead)}{body}'
            f'{_settings(view, launches)}</div></main>')
    page = f'<div class="page no-dock">{kit.topbar([name, view.get("scenario")], steps)}{main}</div>'
    return kit.document("echolot" + (f": {name}" if name else ""), page, css=css, js=js, policy=kit.SERVED,
                        head_html=_REFRESH if going else "")


def again(choice: dict[str, Any] | None, why: str | None, hint: str, link: Link, hunt: Any,
          running: str | None = None) -> str:
    """The button for the answer's page, in the place that page keeps for it:
    Check again, which records the app once more and sets the new report
    against the investigation's first. `why` is why it cannot run now, and
    `running` what to say while a check runs already."""
    action, home = link("check"), link("start")
    if not action or choice is None:
        return ""
    if running:
        where = f'<a class="btn line" href="{e(home)}">See how far it is</a>' if home else ""
        return f'<p class="small sub" role="status">{kit.prose(running)}</p>{where}'
    said = why or hint
    return (f'<form method="post" action="{e(action)}" class="rowf go">'
            f'<input type="hidden" name="what" value="{e("record:" + choice["name"])}">'
            f'<input type="hidden" name="hunt" value="{e(hunt)}">'
            f'<button type="submit" class="btn primary"{" disabled" if why else ""}>Check again</button>'
            f'<p class="small sub go-hint">{kit.prose(said)}</p></form>')


def notice(title: str, text: str, back: str | None = None, steps: Steps = ()) -> str:
    """A page that has one thing to say: there is no such page, nothing to
    show on it yet, or a press that started nothing. `text` is prose, with
    names in backticks."""
    way = f'<div class="rowf"><a class="btn line" href="{e(back)}">Back to the start</a></div>' if back else ""
    main = (f'<main><div class="col stack gap-5"><div class="stack gap-3"><h1 class="h1">{e(title)}</h1>'
            f'<p class="lead">{kit.prose(text)}</p></div>{way}</div></main>')
    return kit.document(f"echolot: {title}", f'<div class="page no-dock">{kit.topbar([], steps)}{main}</div>',
                        css=CSS, js=JS, policy=kit.SERVED)
