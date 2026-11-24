"""Recordings made elsewhere, on the pages of `echolot ui` (#479): the fold
on the start page that asks for a folder, the screen that says what the folder
holds before anything is read, and the card of the report they make.

Drawn from `echolot.elsewhere`, which judges: every sentence here is one it
says. Every press is a form, so it all works with script switched off.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from . import kit
from .kit import e
from .start_page import CSS as START_CSS
from .start_page import JS as START_JS

CSS = START_CSS + ("elsewhere.css",)
JS = START_JS

Link = Callable[..., "str | None"]


def field(action: str, value: str = "", frozen: bool = False, label: str = "Look in this folder") -> str:
    """The folder as text, and the press that looks in it. Nothing is read
    by looking: the next screen says what is there."""
    return (f'<form method="post" action="{e(action)}" class="far-form">'
            '<label class="far-label" for="far-path">The folder the recordings are in</label>'
            f'<div class="rowf far-row"><input type="text" id="far-path" name="folder" value="{e(value)}" '
            'autocomplete="off" autocapitalize="off" spellcheck="false" inputmode="url" '
            f'placeholder="~/Downloads/benchmark-results" aria-describedby="far-h"{" disabled" if frozen else ""}>'
            f'<button type="submit" class="btn line"{" disabled" if frozen else ""}>{e(label)}</button></div></form>')


def on_start(action: str, frozen: bool) -> str:
    """The fold on the start page, under the button."""
    said = ('<p class="small sub measure" id="far-h">A CI artefact, a macrobenchmark\'s output folder, a recording '
            'a colleague sent: type the folder they are in, as a path on this computer. echolot says what it '
            'finds there before anything is read, and reads them into a report of their own, the way the '
            'button reads recordings.</p>')
    return ('<div class="far" id="elsewhere">'
            + kit.fold("Analyse trace files", said + field(action, frozen=frozen)) + "</div>")


def _groups(view: dict[str, Any]) -> str:
    groups = view["groups"]
    if len(groups) == 1:
        g = groups[0]
        return (f'<input type="hidden" name="group" value="{e(g["key"])}">'
                f'<p class="measure">{kit.prose(g["text"])}</p>')
    cards = "".join(
        f'<label class="opt"><input type="radio" name="group" value="{e(g["key"])}"'
        f'{" checked" if g["key"] == view["group"] else ""}><span class="rd"></span>'
        f'<span class="opt-t">{e(g["title"])}</span><span class="opt-d">{kit.prose(g["text"])}</span></label>'
        for g in groups)
    return ('<div class="stack gap-3"><h2 class="h2" id="far-which">Which recordings to read</h2>'
            f'<div class="opts" role="radiogroup" aria-labelledby="far-which">{cards}</div></div>')


def _scenarios(view: dict[str, Any]) -> str:
    names = view["scenarios"]
    if len(names) <= 1:
        value = names[0] if names else ""
        return (f'<input type="hidden" name="scenario" value="{e(value)}">' if value else "")
    cards = "".join(
        f'<label class="opt"><input type="radio" name="scenario" value="{e(name)}"'
        f'{" checked" if name == view["scenario"] else ""}><span class="rd"></span>'
        f'<span class="opt-t">{e(name)}</span><span class="opt-d">Read with the window of this scenario.</span></label>'
        for name in names)
    return ('<div class="stack gap-3"><h2 class="h2" id="far-window">Whose window they are read with</h2>'
            '<p class="small sub measure">The scenario of your config whose start and end mark the stretch a '
            'report measures. The report is not that scenario\'s: it is one of its own.</p>'
            f'<div class="opts" role="radiogroup" aria-labelledby="far-window">{cards}</div></div>')


def _inside(view: dict[str, Any]) -> str:
    """The folders inside one that holds no recordings, where some do: each
    a press that looks in it."""
    if not view.get("inside"):
        return ""
    items = "".join(
        f'<li><form method="post" action="{e(view["look"])}" class="rowf">'
        f'<input type="hidden" name="folder" value="{e(path)}">'
        f'<button type="submit" class="btn line">{e(name)}</button>'
        f'<span class="small sub">{e(count)}</span></form></li>'
        for name, path, count in view["inside"])
    return f'<ul class="far-inside stack gap-2" aria-label="Folders inside it">{items}</ul>'


def look(view: dict[str, Any], link: Link, steps: Sequence[tuple[str, str, bool]] = ()) -> str:
    """The screen a look draws: what the folder holds and, where it holds
    recordings, the press that reads them. `view` is `ui._look`'s, from
    `echolot.elsewhere`."""
    home = link("start")
    back = f'<a class="btn quiet" href="{e(home)}">Back to the start</a>' if home else ""
    parts = [f'<div class="stack gap-3"><h1 class="h1">Recordings in a folder</h1>'
             f'<p class="lead">{kit.prose(view["summary"])}</p></div>']
    if view.get("groups") and view.get("action"):
        why = view.get("why")
        said = why or view["hint"]
        status = ' role="status"' if why else ""
        command = (kit.fold("What a press runs", '<p>The page runs this, in the project\'s folder: the command you '
                            'would type in a terminal, so the report is the same one.</p>'
                            f'<pre class="yaml" tabindex="0">{e(view["command"])}</pre>') if view.get("command")
                   else "")
        parts.append(
            f'<form method="post" action="{e(view["action"])}" class="stack gap-5" id="far-read">'
            f'<input type="hidden" name="folder" value="{e(view["path"])}">'
            f'{_groups(view)}{_scenarios(view)}'
            f'<p class="measure">{kit.prose(view["whose"])}</p>'
            f'<div class="rowf go"><button type="submit" class="btn primary press"{" disabled" if why else ""}>'
            f'Read them</button>{back}<p class="small sub go-hint"{status}>'
            f'{kit.prose(said)}</p></div></form>{command}')
    else:
        parts.append(_inside(view))
        if back:
            parts.append(f'<div class="rowf">{back}</div>')
    if view.get("look"):
        parts.append('<div class="stack gap-3"><h2 class="h2">Another folder</h2>'
                     + field(view["look"], view.get("typed") or "", label="Look in it") + "</div>")
    main = f'<main><div class="col stack gap-6">{"".join(parts)}</div></main>'
    return kit.document("echolot: recordings in a folder",
                        f'<div class="page no-dock">{kit.topbar([], steps)}{main}</div>',
                        css=CSS, js=JS, policy=kit.SERVED)


def card(latest: dict[str, Any] | None, link: Link) -> str:
    """The report of recordings from elsewhere, in the start page's list."""
    if not latest:
        return ""
    href = link("elsewhere-report")
    name = '<span class="small sub">Recordings from elsewhere</span>'
    verdict = latest.get("verdict")
    if verdict is None or not href:
        return f'<li class="rep empty"><p class="sub"><b>Recordings from elsewhere</b> {kit.prose(latest["plain"])}</p></li>'
    return (f'<li class="rep"><div class="rowf">{kit.verdict_pill(verdict.get("level"))}{name}</div>'
            f'<p class="measure">{e(verdict.get("text") or "")}</p>'
            f'<p class="small sub measure">{kit.prose(latest["plain"])}</p>'
            f'<div class="rowf"><a class="btn line" href="{e(href)}">See the report</a></div></li>')
