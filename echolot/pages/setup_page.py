"""The part of the start page that sets a project up: "What do you want to
check?", the time limit, what a press writes, and **Set it up**.

It is drawn from `echolot.project_setup.view` and judges nothing: every state
and every sentence is in that reading. `parts` hands the start page what to
put where: a block that stands in place of the button and the next step (the
question while there is no config, the list while setup is under way), or a
block before them (why the page cannot set this project up, the lines to add
to a config that names no scenario, a setup that ended badly, what comes
before the first check).

The press is a form, and the page of a setup under way refreshes itself, so
every step works with script switched off. With script on the list is redrawn
in place by the start page's own script, and a second click sends nothing.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import kit, start_page
from .kit import e

CSS = ("setup.css",)
JS = ("setup.js",)

QUESTION = "What do you want to check?"


def _copy(ident: str, command: str) -> str:
    """A command to copy, each word kept whole where it fits."""
    words = " ".join(f'<span class="w">{e(word)}</span>' for word in command.split(" "))
    return (f'<div class="command"><code id="{e(ident)}" tabindex="0">{words}</code>'
            f'<button type="button" class="btn line needs-js" data-setup-copy="{e(ident)}">Copy</button>'
            '<span class="vh" role="status"></span></div>')


def _other(other: dict[str, Any]) -> str:
    """Anything but the launch: one plain sentence, and the command to copy."""
    command = _copy("setup-agent", other["command"]) if other.get("command") else ""
    how = f'<p class="small sub measure">{kit.prose(other["how"])}</p>' if other.get("how") else ""
    return (f'<div class="stack gap-2" id="setup-other"><p class="small sub measure">{kit.prose(other["text"])}</p>'
            f'{command}{how}</div>')


def _says(pairs: Sequence[tuple[str, str]]) -> str:
    """The CLI's own words under a sentence of the page's."""
    return "".join(f'<p class="says small sub"><span class="who">{e(who)}</span> {kit.prose(said)}</p>'
                   for who, said in pairs if said)


def _writes(found: dict[str, Any]) -> str:
    """The fold: every file a press writes, by what it is for, and the text
    of the config."""
    groups = []
    for group in found["writes"]:
        files = "".join(f'<li><code>{e(path)}</code>' + (f' <span class="sub">{e(note)}</span>' if note else "") + "</li>"
                        for path, note in group["files"])
        groups.append(f'<li><p>{kit.prose(group["title"])}</p><ul class="files">{files}</ul></li>')
    same = ("The same as in a terminal: <code>echolot init</code>, then the config <code>echolot scan</code> "
            "proposes, saved as <code>echolot.yml</code>." if found["init"] else
            "The same as in a terminal: the config <code>echolot scan</code> proposes, saved as "
            "<code>echolot.yml</code>.")
    body = ('<p class="small measure">Every file is new unless it says otherwise, and no file of yours is replaced. '
            'An <code>echolot.yml</code> that is already there stops the press.</p>'
            f'<ol class="writes small">{"".join(groups)}</ol>'
            '<p class="small measure" id="setup-yaml-h">The text of <code>echolot.yml</code>. With a limit, its '
            '<code>budget_ms</code> line holds the limit in milliseconds.</p>'
            f'<pre class="yaml" tabindex="0" aria-labelledby="setup-yaml-h">{e(found["config"])}</pre>'
            f'<p class="small sub measure">{same}</p>')
    return (f'<details class="tg" id="setup-writes"><summary>{kit.TW}<span>What Set it up writes</span></summary>'
            f'<div class="tg-b">{body}</div></details>')


def _offer(found: dict[str, Any], action: str) -> str:
    """The question with its answer, who the app is, the limit, the fold and
    the button: one form."""
    cards = "".join(
        f'<label class="opt"><input type="radio" name="what" value="{e(c["name"])}"{" checked" if i == 0 else ""}>'
        f'<span class="rd"></span><span class="opt-t">{e(c["title"])}</span>'
        f'<span class="opt-d">{kit.prose(c["text"])}</span></label>' for i, c in enumerate(found["choices"]))
    build = (f", and will measure its <code>{e(found['variant'])}</code> build" if found.get("variant") else
             ". Which build to measure could not be read: the one on your phone is the one measured")
    where = f" in <code>{e(found['script'])}</code>" if found.get("script") else ""
    app = (f'<div class="stack gap-2" id="setup-app"><p class="measure"><b>Is this your app?</b> echolot found '
           f'<code>{e(found["package"])}</code>{where}{build}. If that is your app, set it up. If the name is '
           f'another app\'s, do not: an agent sets up the right one.</p>{_says(found["noted"])}</div>')
    low, high = found["limit"]
    limit = ('<div class="stack gap-2"><label class="field" for="limit">It should take no longer than '
             '<input type="text" inputmode="decimal" maxlength="6" id="limit" name="limit" autocomplete="off" '
             'aria-describedby="limit-h"> seconds</label>'
             f'<p class="small sub measure" id="limit-h">Optional, from {low:g} to {high:g}. With a limit the report '
             'says whether the app opens fast enough. Without one it says how long it takes, and holds that '
             'against nothing.</p></div>')
    button = ('<div class="rowf go"><button type="submit" class="btn primary press" id="setup-go">Set it up</button>'
              f'<p class="small sub go-hint" id="setup-hint">{kit.prose(found["hint"])}</p></div>')
    return (f'<section class="stack gap-5" id="setup" aria-labelledby="q-what">'
            f'<form method="post" action="{e(action)}" id="setup-form" class="stack gap-5">'
            f'<div class="stack gap-3"><h2 class="h2" id="q-what">{QUESTION}</h2>'
            f'<div class="opts" role="radiogroup" aria-labelledby="q-what">{cards}</div>{_other(found["other"])}</div>'
            f'{app}{limit}{_writes(found)}{button}</form></section>')


def _cannot(found: dict[str, Any]) -> str:
    """Why this project cannot be set up from the page: a sentence of the
    page's, `scan`'s own words under it, and where the agent's path is."""
    way = ("The file that is there stays as it is. An agent can write the scenario into it."
           if found.get("exists") else "An agent can set it up: the step under What to do next says how.")
    return (f'<section class="stack gap-3" id="setup" aria-labelledby="q-what"><h2 class="h2" id="q-what">{QUESTION}</h2>'
            f'<div class="callout" id="setup-cannot">{kit.icon("grey")}<div class="stack gap-2">'
            f'<p class="measure"><b>This page cannot set the project up by itself.</b> {kit.prose(found["plain"])}</p>'
            f'{_says(found["says"])}<p class="small sub measure">{e(way)}</p></div></div></section>')


def _by_hand(found: dict[str, Any]) -> str:
    """A config with no scenario: the lines that make the launch its
    scenario, for a person to add."""
    more = ""
    if found["sections"]:
        named = " and ".join(f"<code>{e(key)}</code>" for key in found["sections"])
        more = f" The {named} section{'s are' if len(found['sections']) > 1 else ' is'} missing too, and is among the lines."
    return (f'<section class="stack gap-3" id="setup" aria-labelledby="q-what"><h2 class="h2" id="q-what">{QUESTION}</h2>'
            '<p class="measure" id="setup-by-hand"><code>echolot.yml</code> is there and names no scenario, so a '
            'check measures everything a recording holds. To check how the app opens, add these lines to it. '
            f'The page does not write into a file that is yours.{more}</p>'
            f'<pre class="yaml" id="setup-lines" tabindex="0">{e(found["lines"])}</pre>'
            '<div class="rowf"><button type="button" class="btn line needs-js" data-setup-copy="setup-lines">Copy '
            'the lines</button><span class="vh" role="status"></span>'
            '<span class="small sub">Then press Refresh.</span></div>'
            f'{_other(found["other"])}</section>')


def _running(found: dict[str, Any], poll: str | None) -> str:
    """A setup under way: both rows from the start. The ids are the ones the
    start page's script keeps a run current by."""
    rows = f'<ol class="run" id="run-rows" aria-label="Setting up, step by step">{start_page.run_rows(found["rows"])}</ol>'
    return ('<section class="stack gap-3" id="setup" aria-labelledby="setup-h"><h2 class="h2" id="setup-h">Setting up</h2>'
            '<div class="stack gap-3" id="run" data-state="running"' + (f' data-poll="{e(poll)}"' if poll else "")
            + f'>{rows}<p class="small sub measure" id="run-note">{kit.prose(found["note"])}</p>'
            f'<p class="vh" role="status" id="run-said">{e(found["words"])}</p></div></section>')


def _ended(found: dict[str, Any]) -> str:
    """A setup that ended badly: what was written, and what `init` said."""
    rows = f'<ol class="run" aria-label="Setting up, step by step">{start_page.run_rows(found["rows"])}</ol>'
    return (f'<section class="stack gap-3" id="setup" aria-label="Setting up" data-state="{e(found["status"])}">{rows}'
            f'<p class="measure" id="setup-end"><b>{e(found["title"])}</b> {kit.prose(found["text"])}</p>'
            f'{start_page._said(found["said"])}<p class="small sub measure">{kit.prose(found["todo"])}</p></section>')


def _first(text: str) -> str:
    """What comes before the first check: a note beside the page, not a step of it."""
    return f'<aside class="callout" id="setup-first">{kit.icon("yellow")}<p class="small">{kit.prose(text)}</p></aside>'


def parts(found: dict[str, Any] | None, link: start_page.Link) -> dict[str, Any] | None:
    """What the start page puts where, for one reading of the setup: `instead`
    stands in place of the button and the next step, `before` goes in front
    of them, `going` says the page shows something under way, and `css` and
    `js` are this part's own files. None where there is nothing to draw."""
    action = link("setup")
    if found is None or not action:
        return None
    out: dict[str, Any] = {"css": CSS, "js": JS, "instead": None, "before": "", "going": False}
    kind = found.get("state")
    if kind == "offer":
        out["instead"] = _offer(found, action)
    elif kind == "running":
        out.update(instead=_running(found, link("setup-state")), going=True)
    elif kind == "cannot":
        out["before"] = _cannot(found)
    elif kind == "by-hand":
        out["before"] = _by_hand(found)
    elif kind == "ended":
        out["before"] = _ended(found)
    if found.get("first"):
        out["before"] += _first(found["first"])
    return out if out["instead"] or out["before"] else None


def progress(found: dict[str, Any] | None) -> str:
    """What the page of a setup under way asks for: its rows as they stand
    now, in the shape the start page's script reads a run in."""
    return start_page.progress(found)
