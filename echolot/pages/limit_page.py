"""The time limit as `echolot ui` sets it (`echolot.time_limit`): the sentence
with a field in it, beside the report's verdict and under "All settings",
with the line of `echolot.yml` a press changes; why the page leaves the file
to the person and which line to edit, where it does; and, while the
report is one made with another limit, that it is, with the button that
reads the recordings again.

Drawn from `time_limit.view`, it judges nothing. The field and the button
are forms, so both work with script switched off; the one script opens "All
settings" when a press sends the browser back to the limit inside it. A
report written to disk has none of this: a file cannot change a config.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import plain
from . import kit
from .kit import e

CSS = ("limit.css",)
JS = ("limit.js",)
ID = "time-limit"

Link = Callable[..., "str | None"]


def _does(found: dict[str, Any]) -> str:
    """What a press writes, in a sentence: the one line it changes or adds."""
    line, under = found.get("line"), found.get("under") or "scenario"
    if found.get("kind") == "insert":
        return (f"A press adds one line to `echolot.yml`, `budget_ms` under `{under}:` as line {line}, and changes "
                "nothing else in it.")
    return f"A press changes line {line} of `echolot.yml`, `budget_ms` under `{under}:`, and nothing else in it."


def _field(found: dict[str, Any], action: str, back: str) -> str:
    """The sentence with the field, as on the page that sets a project up, the
    limit in force in it, and the button: one form."""
    low, high = found["range"]
    value = f' value="{e(found["seconds"])}"' if found.get("seconds") else ""
    scenario = (f'<input type="hidden" name="scenario" value="{e(found["scenario"])}">'
                if found.get("scenario") else "")
    empty = ", or empty for no limit" if found.get("off", True) else ""
    return (f'<form method="post" action="{e(action)}" class="lim-form" id="{ID}-form">'
            f'<input type="hidden" name="seen" value="{e(found["seen"])}">'
            f'<input type="hidden" name="rest" value="{e(found["rest"])}">'
            f'<input type="hidden" name="back" value="{e(back)}">{scenario}'
            f'<div class="lim-row"><label class="lim-field" for="{ID}-in">It should take no longer than '
            f'<input type="text" inputmode="decimal" maxlength="6" id="{ID}-in" name="limit"{value} autocomplete="off" '
            f'aria-describedby="{ID}-h"> seconds</label>'
            '<button type="submit" class="btn line">Set the limit</button></div>'
            f'<p class="small sub measure" id="{ID}-h">From {low:g} to {high:g}{empty}. {kit.prose(_does(found))}</p>'
            '</form>')


def _behind(found: dict[str, Any], link: Link, look: str) -> str:
    """The report is one made with another limit: a sentence, and the press
    that reads the recordings again."""
    behind = found.get("behind")
    if not behind:
        return ""
    was = "no time limit" if behind["was"] is None else f"a limit of {plain.hum_limit(behind['was'])}"
    text = (f"<b>This report was made with {e(was)}.</b> echolot reads the limit when it reads the recordings, so "
            "the report changes once they are read again.")
    action = link("check")
    press = ""
    if action and behind.get("n") and behind.get("what"):
        why = behind.get("why")
        status = ' role="status"' if why else ""
        press = (f'<form method="post" action="{e(action)}" class="rowf lim-go">'
                 f'<input type="hidden" name="what" value="{e(behind["what"])}">'
                 '<input type="hidden" name="again" value="1">'
                 f'<button type="submit" class="btn {look}"{" disabled" if why else ""}>Read the recordings again</button>'
                 f'<p class="small sub lim-hint"{status}>{kit.prose(why or behind["hint"])}</p></form>')
    elif not behind.get("n"):
        text += " No recordings are here to read: the next check holds the launch against the limit."
    return (f'<div class="callout lim-was" id="{ID}-was">{kit.icon("grey")}<div class="stack gap-3">'
            f'<p class="small">{text}</p>{press}</div></div>')


def beside(found: dict[str, Any] | None, link: Link) -> dict[str, Any] | None:
    """What stands beside the verdict of a report the server shows: the field,
    or why the page changes nothing, in one quiet sentence with the line to
    edit; and whether the report is one made with another limit. None where
    the page has nothing to say of the limit, and `css` is the part's own."""
    action = link("limit")
    if found is None or not action:
        return None
    if found["can"]:
        body = _field(found, action, "report")
    else:
        at = f", line {found['line']}" if found.get("line") else ""
        where = ("in `local.yml`" if found.get("reason") == "local" else
                 f"`budget_ms` under `{found['under']}:` in `echolot.yml`{at}")
        body = (f'<p class="small sub measure" id="{ID}-no">{kit.prose(found["why"])} The limit is {kit.prose(where)}, '
                'in milliseconds.</p>')
    return {"html": f'<section class="lim" id="{ID}" aria-label="Time limit">{body}{_behind(found, link, "primary")}'
                    '</section>', "css": CSS, "js": JS}


def setting(found: dict[str, Any] | None, link: Link) -> dict[str, Any] | None:
    """What "All settings" shows under Time limit beside what it reads: the
    field, which stands in place of the line to edit (`keep` off), or why the
    page changes nothing, before that line (`keep` on). None where the page
    has nothing to add to the row."""
    action = link("limit")
    if found is None or not action:
        return None
    if found["can"]:
        body, keep = _field(found, action, "start"), False
    else:
        at = f" It is line {found['line']} of `echolot.yml`." if found.get("line") else ""
        body, keep = f'<p id="{ID}-no">{kit.prose(found["why"] + at)}</p>', True
    return {"html": f'<div class="lim" id="{ID}">{body}{_behind(found, link, "line")}</div>', "keep": keep, "css": CSS,
            "js": JS}
