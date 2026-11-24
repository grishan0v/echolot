"""Dig deeper from the page: what `echolot ui` shows around an agent it starts.

Four pieces, each drawn from a reading of `echolot/dig.py` and judging
nothing itself:

- on the report, the way to it: a button in the request dialog that leads to
  the screen before anything starts, and the same as a form for a browser
  with script off (`report_parts`);
- that screen (`consent`): what will run, under whose account, for how long,
  what the agent may do and what it may not, what changes, what Stop does,
  and the way out, the request to copy;
- on the investigation's page, while the agent works and once it ended
  (`panel`): what it is doing, Stop, how long nothing has moved, how it
  ended, what it left in the code, and the presses that clear that up;
- on the start page, which of the two is running (`on_start`).

Everything is a form, so it works with script switched off: the page of a
run then loads itself again. With script on, the phase is asked for every two
seconds and the page is drawn anew when a round comes in or the run ends
(`dig_poll.js`).
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from . import kit
from .kit import e

CSS = ("dig.css",)
# The script of the report and of the screen before the agent starts, and the
# one that asks how far an agent is, for its investigation's page alone.
JS = ("dig.js", "dig_poll.js")
FORM_JS, POLL_JS = JS[:1], JS[1:]

Link = Callable[..., "str | None"]

# How often a page of a run loads itself again where script is off, in seconds.
REFRESH_S = 5
HEAD = f'<noscript><meta http-equiv="refresh" content="{REFRESH_S}"></noscript>\n'


def _hidden(keys: Sequence[str]) -> str:
    return "".join(f'<input type="hidden" name="pick.{e(key)}" value="1">' for key in keys)


def _why(why: str | Sequence[str] | None) -> str:
    """Why nothing can start: a sentence, or where each client has a reason
    of its own, each on a line of its own, and what follows on the next."""
    if not why:
        return ""
    if isinstance(why, str):
        return kit.prose(why)
    if len(why) == 1:
        return kit.prose(why[0])
    return "".join(f'<span class="dig-why1">{kit.prose(w)}</span> <br>' for w in why)


# ---------------------------------------------------------------------------
# on the report


def _aside(aside: Sequence[str]) -> str:
    """Why each other client on this computer cannot start, a line each."""
    return "".join(f'<p class="small sub dig-aside">{kit.prose(w)}</p>' for w in aside)


def report_parts(checks: Sequence[dict[str, Any]], action: str, can: bool, why: str | Sequence[str] | None,
                 who: Sequence[str] = ("Claude Code",), aside: Sequence[str] = ()) -> dict[str, Any]:
    """What a served report adds: in the request dialog, the button that leads
    to the screen before the agent starts, or the reason there is none; and
    the same as a form for a browser with script off. `who` names the
    clients that can start, in the order the page offers them, and `aside`
    says why each other one on this computer cannot (`dig.aside`)."""
    hot = [c for c in checks if c["hot"]]
    if can:
        doer = (f"{e(who[0])}, the one on this computer, does it under your own account. The next screen says what it "
                "will do" if len(who) < 2 else
                f"{e(' or '.join(who))}, both on this computer, does it under your own account. The next screen says "
                "which one and what it will do")
        ask = (f'<form method="post" action="{e(action)}" id="ask-start-form" class="stack gap-2">'
               '<div class="rowf"><button type="submit" class="btn primary" id="ask-start">Start it from here</button></div>'
               f'<p class="small sub">{doer}, and nothing starts before you press Start there. Or copy the request '
               f'into an agent of your own.</p>{_aside(aside)}</form>')
    else:
        ask = ('<p class="small sub">Paste the request into Claude Code, Codex or Cursor opened on the project. '
               f'The page cannot start one itself: {_why(why)}</p>')
    boxes = "".join(
        f'<li><label class="dig-pick"><input type="checkbox" name="pick.{e(c["key"])}" value="1">'
        f'{kit.icon(c["kind"])}<span>{e(c["title"])}</span></label></li>' for c in hot)
    noscript = ('<noscript><section class="stack gap-3 dig-no-js" aria-labelledby="dig-h">'
                '<h2 class="h2" id="dig-h">Dig deeper</h2>'
                '<p class="measure">Tick the checks to start from and press Dig deeper. The next screen shows the '
                'request for an agent'
                + (", and can start one on this computer." if can else ".") + '</p>'
                f'<form method="post" action="{e(action)}" class="stack gap-3"><ul class="dig-picks">{boxes}</ul>'
                '<div class="rowf"><button type="submit" class="btn primary">Dig deeper</button></div></form>'
                '</section></noscript>')
    return {"ask": ask, "starts": can, "noscript": noscript, "css": CSS, "js": FORM_JS}


# ---------------------------------------------------------------------------
# the screen before anything starts


def _picked(picks: Sequence[dict[str, Any]]) -> str:
    return '<ul class="dig-list">' + "".join(
        f"<li>{kit.icon(c['kind'])}<span>{e(c['title'])}</span></li>" for c in picks) + "</ul>"


def _block(title: str, *paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" if not p.startswith("<") else p for p in paragraphs if p)
    return f'<section class="dig-block"><h2 class="h3">{e(title)}</h2>{body}</section>'


def _code_list(items: Sequence[str]) -> str:
    items = [f"<code>{e(i)}</code>" for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def consent(c: dict[str, Any], link: Link, steps: Sequence[tuple[str, str, bool]] = ()) -> str:
    """The screen between a press of Dig deeper and the agent: everything a
    person agrees to by pressing Start, in plain words, and the way out.

    `c` holds: `question`, `picks`, `client` (`dig.Client`), `why` (why it
    cannot start now, or None), `paths` (the allowed folders, or None when
    they could not be read), `phone`, `rounds`, `changed`, `opening` and
    `running` (the two commands, as typed), `copy` (the request to copy),
    `action` (where Start is sent), `back` (the report), `keys` (what was
    ticked), `many` (more were ticked than one question takes), `others`
    (each other client that can start, by name, with the address of its own
    screen), `aside` (why each other client on this computer cannot), and
    what the client's adapter says of itself (`terms`): for
    Claude Code `own_allow` and `budget`, for Codex `trust`, `config`,
    `rules_file`, `other_rules` and `idle`.
    """
    client = c["client"]
    picks = c["picks"]
    name = "Codex" if client.key == "codex" else "Claude Code"
    head = ('<div class="stack gap-3"><h1 class="h1">Before the agent starts</h1>'
            f'<p class="lead">The question it gets: {kit.prose(c["question"])}</p></div>')
    parts = [head, '<div class="stack gap-2"><p class="small sub">From the checks you ticked:</p>'
                   f'{_picked(picks)}</div>']
    if c["many"]:
        parts.append(f'<div class="callout">{kit.icon("yellow")}<p class="small">'
                     f'{e(kit.MANY_NOTE.replace("{n}", str(len(picks))))} '
                     f'<a class="lnk" href="{e(c["back"])}">Back to the report</a></p></div>')
    if c["why"]:
        parts.append(f'<div class="callout" id="dig-why">{kit.icon("yellow")}<p>{_why(c["why"])} The request is '
                     'below: copy it into an agent of your own.</p></div>')
    starts = bool(c.get("action") and not c["why"])
    for other, action in c.get("others") or ():
        # Another client can do it too: its own screen, for its own terms; the
        # one next step where the client on this screen cannot start.
        parts.append(f'<form method="post" action="{e(action)}" class="rowf dig-other">{_hidden(c["keys"])}'
                     f'<p class="small sub">{e(other)} is on this computer too, and can do it instead. What it may '
                     f'do differs: its own screen says how.</p><button type="submit" class="btn '
                     f'{"line" if starts else "primary"}">Use {e(other)} instead</button></form>')
    if c.get("aside"):
        # Another client is on this computer and cannot do it: what it lacks,
        # with no button that leads to a screen that would refuse it (#819).
        parts.append(f'<div class="stack gap-2">{_aside(c["aside"])}</div>')
    paths = c.get("paths")
    where = ("any file of the project" if paths == [""] else
             "files under " + _code_list([p or "." for p in paths]) if paths else "")
    if client.program and not c["why"]:
        terms = _codex_terms if client.key == "codex" else _claude_terms
        parts.append('<div class="stack gap-5 dig-terms">' + "".join(terms(c, where)) + "</div>")
    buttons = []
    if starts:
        buttons.append(f'<form method="post" action="{e(c["action"])}" id="dig-start-form">{_hidden(c["keys"])}'
                       '<button type="submit" class="btn primary press" id="dig-start">Start</button></form>')
    copy_first = not starts and not c.get("others")
    buttons.append(f'<button type="button" class="btn {"primary" if copy_first else "line"} needs-js" id="dig-copy" '
                   'data-copy="dig-request">Copy the request</button>')
    buttons.append(f'<a class="btn quiet" href="{e(c["back"])}">Back to the report</a>')
    parts.append(f'<div class="rowf dig-go">{"".join(buttons)}<span class="vh" role="status" id="dig-copied"></span></div>')
    runs = "".join(f'<pre class="dig-cmd" tabindex="0">{e(line)}</pre>' for line in (c.get("opening"), c.get("running"))
                   if line)
    if runs and starts:
        parts.append(kit.fold("Exactly what starts", '<p class="small sub measure">These two, one after the other, in '
                              "the project's folder: the investigation is opened first, then "
                              f"{name} is started in it, with the request below and nothing to answer it.</p>" + runs))
    parts.append(kit.fold("The request text", f'<pre class="req" id="dig-request" tabindex="0">{e(c["copy"])}</pre>'))
    main = f'<main><div class="col stack gap-6">{"".join(parts)}</div></main>'
    page = f'<div class="page no-dock">{kit.topbar(["Dig deeper"], steps)}{main}</div>'
    return kit.document("echolot: dig deeper", page, css=("base.css", "picks.css", *CSS), js=("base.js", *FORM_JS),
                        policy=kit.SERVED)


def _phone(c: dict[str, Any]) -> str:
    return f"your phone (<b>{e(c['phone'])}</b>)" if c.get("phone") else "your phone"


def _tree(c: dict[str, Any]) -> str:
    changed = c.get("changed")
    tree = ("Your code changes on disk while it works, and nothing is committed: what it leaves is yours to "
            "look at with the tools you use.")
    if changed:
        tree += (f" {changed} file{'s are' if changed != 1 else ' is'} already changed and not committed: "
                 "commit them first if you want to tell its changes from yours.")
    return tree


def _does(c: dict[str, Any], where: str) -> str:
    return (f"It puts temporary markers into {where}, rebuilds the app, puts it on {_phone(c)} and records it "
            f"again, for up to {c['rounds']} rounds. At the end it takes the markers out and writes down what "
            "it found: the causes, where they are in the code, and what to change.")


_PHONE_BUILD = ("Your phone ends with a build that has the markers in it, until the app is built and installed "
                "again without them.")
_STAY = ("Keep <code>echolot ui</code> running, and the phone connected and unlocked: closing "
         "<code>echolot ui</code> stops the agent too.")


def _claude_terms(c: dict[str, Any], where: str) -> list[str]:
    """What Claude Code does, may do and changes, block by block."""
    client = c["client"]
    who = "Claude Code" + (f" {e(client.version)}" if client.version else "")
    account = f", signed in as <b>{e(client.account)}</b>" if client.account else ""
    # The number is in the sentence about what it costs, and nowhere else.
    money = (f"It costs what any session of yours costs, on your Claude plan or your API key, and it stops by "
             f"itself once it has spent about <b>${c['budget']}</b>, by its own estimate; the page shows what it "
             "reports at the end.")
    allow_more = ""
    if c.get("own_allow"):
        allow_more = ("<p>This project's own settings for Claude Code (<code>.claude/settings.json</code>) allow "
                      f"more, and those apply too: {_code_list(c['own_allow'])}.</p>")
    return [
        _block("Who does it",
               f"{who}, the one installed on this computer, under your own account{account}. {money}"),
        _block("How long", f"Tens of minutes, usually. {_STAY}"),
        _block("What it does", _does(c, where)),
        _block("What it may do without asking",
               '<ul class="dig-may">'
               + "".join(f"<li><code>{e(name)}</code> {e(said)}</li>" for name, _, said in c["commands"])
               + f"<li>edit {where}, and write <code>{e(c['verdict'])}</code>, its answer</li>"
               "<li>read the project's files, and run commands that only read, such as <code>ls</code> or "
               "<code>grep</code></li></ul>",
               "Everything else is refused without asking anyone: git, downloads, any other program, any other "
               "file. A question it wants to ask you is refused the same way, and it goes on with what it has.",
               allow_more),
        _block("What changes",
               _tree(c), _PHONE_BUILD,
               "Claude Code keeps its own record of the session on this computer, as it does for every session."),
        _block("Stop",
               "Stop is on the next page the whole time. It ends Claude Code and everything it started: a build, "
               "a recording, a command on the phone. Markers it had put in stay in the code: the page then "
               "counts them, and takes them out at a press."),
    ]


def _codex_terms(c: dict[str, Any], where: str) -> list[str]:
    """What Codex does, may do and changes, block by block, where it differs
    from Claude Code: the trust it writes into the person's own settings,
    edits it cannot be kept from, no spending cap, and the watchdog."""
    client = c["client"]
    who = "Codex" + (f" {e(client.version)}" if client.version else "")
    how = f", signed in <b>{e(client.how)}</b>" if client.how else ""
    allowed = where.removeprefix("files under ") if where.startswith("files under ") else "the folders it may edit"
    rules = f"<code>{e(c.get('rules_file') or '.codex/rules/echolot.rules')}</code>"
    config = f"<code>{e(c.get('config') or '~/.codex/config.toml')}</code>"
    more = ""
    if c.get("other_rules"):
        more = (f"<p>Codex also reads these rules files, and what they allow applies too: "
                f"{_code_list(c['other_rules'])}.</p>")
    if c.get("trust") == "trusted":
        trust = f"Your Codex settings already trust this project ({config}), and that stays as it is."
    else:
        trust = (f"Codex marks this project trusted in your own Codex settings, {config}, as it does the first time "
                 "it works in a project. That is a change outside the project, and it stays: from then on Codex "
                 "reads this project's <code>.codex</code> folder in every session here.")
    minutes = _minutes(c.get("idle") or 1800)
    return [
        _block("Who does it",
               f"{who}, the one installed on this computer, under your own account{how}. It costs what any session "
               "of yours costs, on your ChatGPT plan or your API key. There is no spending cap the page can set for "
               "Codex: the page shows the tokens it reports at the end."),
        _block("How long",
               f"Tens of minutes, usually. {_STAY} If it runs nothing and writes nothing for {minutes}, the page "
               "stops it: that is how Codex waits for an answer to a question, and nobody is here to give one."),
        _block("What it does", _does(c, where)),
        _block("What it may do without asking",
               '<ul class="dig-may">'
               + "".join(f"<li><code>{e(name)}</code> {e(said)}</li>" for name, _, said in c["commands"])
               + f"<li>change any file of the project, not only {allowed}: Codex's edits cannot be kept to those "
               "folders, so the page lists afterwards every file it changed outside them</li>"
               "<li>run other commands in Codex's sandbox, which has no network and writes nothing outside the "
               "project</li></ul>",
               f"The first three run outside the sandbox, by the rules in {rules}, which <code>echolot init</code> "
               "wrote: Codex runs them without asking in every session in this project. Nothing else is asked "
               "either: what the sandbox does not allow, such as a download, a write outside the project or a "
               "commit, is refused, and a question it wants to ask you gets no answer.",
               more),
        _block("What changes",
               trust, _tree(c),
               f"When it starts, the page notes the project's files, and afterwards lists the ones changed outside "
               f"{allowed}. Change none of them yourself meanwhile, or the list holds yours too.",
               _PHONE_BUILD,
               "Codex keeps its own record of the session on this computer, as it does for every session."),
        _block("Stop",
               "Stop is on the next page the whole time. It ends Codex and everything it started: a build, "
               "a recording, a command on the phone. Markers it had put in stay in the code: the page then "
               "counts them, and takes them out at a press."),
    ]


# ---------------------------------------------------------------------------
# on the investigation's page


def _minutes(seconds: float) -> str:
    whole = int(seconds // 60)
    if whole < 1:
        return "less than a minute"
    return "a minute" if whole == 1 else f"{whole} minutes"


def quiet_words(seen: dict[str, Any]) -> str:
    """The watchdog's sentence: nothing has moved for this long, and what was
    running then. Empty while things move (`dig.QUIET_S`)."""
    if not seen.get("stuck"):
        return ""
    long = _minutes(seen.get("quiet") or 0)
    doing = seen.get("phase") or {}
    if doing.get("key") not in (None, "think"):
        words = doing["words"][0].lower() + doing["words"][1:]
        return (f"Nothing has changed for {long}: {words} is still going on. That can take this long. If you "
                "think it is stuck, Stop ends it.")
    if seen.get("ends_idle"):
        # A client that waits for an answer nobody gives, and that the job
        # ends once nothing has moved for that long.
        return (f"Nothing has moved for {long}: no command, no round, no build, no recording. That is how "
                f"{_who(seen)} waits for an answer to a question, and nobody is here to give one: if nothing moves "
                f"for {_minutes(seen['ends_idle'])}, the page stops it. Stop ends it now.")
    return (f"Nothing has moved for {long}: no round, no build, no recording. An agent can think this long; if "
            "you think it is stuck, Stop ends it.")


def _ran(seen: dict[str, Any]) -> str:
    """How long it has worked and how many rounds it has, in a sentence. The
    question once a second carries it too, so that it does not stand still
    between two rounds."""
    rounds = seen.get("rounds") or 0
    return (f"{_who(seen)} has been at it for {_minutes(seen.get('ran') or 0)}, with "
            f"{rounds} round{'s' if rounds != 1 else ''} so far.")


def _who(seen: dict[str, Any]) -> str:
    """The client of the run, by name: Claude Code where it says none."""
    return seen.get("who") or "Claude Code"


def _now(seen: dict[str, Any]) -> str:
    """What the agent is doing, in a sentence."""
    if seen.get("stopping") and seen.get("idle"):
        return (f"Stopping: nothing had moved for {_minutes(seen.get('ends_idle') or 0)}, so {_who(seen)} and what "
                "it started are being ended.")
    if seen.get("stopping"):
        return f"Stopping: {_who(seen)} and what it started are being ended."
    doing = seen.get("phase") or {}
    long = f" for {_minutes(doing['for'])}" if doing.get("for") else ""
    return f"{doing.get('words') or 'Working'}{long}."


def _said(said: dict[str, Any] | None) -> str:
    if not said:
        return ""
    lines = said.get("lines") or ["(it printed nothing)"]
    where = (f'<p class="small sub">Everything it printed is in <code>{e(said["log"])}</code>.</p>'
             if said.get("log") else "")
    return (f'<div class="stack gap-2"><p class="small">{e(said["who"])} said, at the end:</p>'
            f'<pre class="dig-said" tabindex="0">{e(chr(10).join(lines))}</pre>{where}</div>')


def _left(seen: dict[str, Any], link: Link) -> str:
    """What the agent left in the code, as `hunt` counts it, and the press
    that takes it out.

    A tagged line the press leaves, each by its place, and never "no markers
    are left" while one is there: an agent's one-line `try { … } finally {
    …endSection() }` stayed after the press, and the page said the code was
    clean (#703)."""
    left = seen.get("left")
    if left is None:
        return ""
    out = []
    kept = left.get("kept") or []
    if left["markers"]:
        hand = left["markers"] - left["removable"]
        files = len(left["files"])
        text = (f"{left['markers']} temporary marker{'s are' if left['markers'] != 1 else ' is'} still in the code, "
                f"in {files} file{'s' if files != 1 else ''}. <code>echolot mark --remove</code> takes out "
                f"{left['removable']} of them")
        text += (f"; {hand} {'were' if hand != 1 else 'was'} added by hand and {'have' if hand != 1 else 'has'} "
                 "to go by hand." if hand else ".")
        out.append(f'<p class="measure">{text}</p>')
        out.append('<ul class="dig-files">' + "".join(f"<li><code>{e(f)}</code></li>" for f in left["files"][:12])
                   + (f'<li class="sub">and {files - 12} more</li>' if files > 12 else "") + "</ul>")
    elif left["lines"]:
        out.append(f'<p class="measure">{left["lines"]} line{"s" if left["lines"] != 1 else ""} tagged '
                   "<code>// echolot:mark</code> are still in the code, with no marker beside them. "
                   "<code>echolot mark --remove</code> takes them out.</p>")
    elif not kept:
        out.append('<p class="measure">No temporary markers are left in the code.</p>')
    if kept:
        n = len(kept)
        them = "it" if n == 1 else "them"
        out.append(f'<p class="measure">{n} line{"s" if n != 1 else ""} tagged <code>// echolot:mark</code> '
                   f"{'are' if n != 1 else 'is'} still in the code, with more on {them} than the marker, so "
                   f"<code>echolot mark --remove</code> leaves {them}. Take the marker out of "
                   f"{'it' if n == 1 else 'each'} by hand and keep the app's own code.</p>")
        out.append('<ul class="dig-files">' + "".join(f"<li><code>{e(p)}</code></li>" for p in kept[:12])
                   + (f'<li class="sub">and {n - 12} more</li>' if n > 12 else "") + "</ul>")
    if left["markers"] or left["lines"] or kept:
        out.append('<p class="small sub measure">The app on your phone may still be a build with markers in it: '
                   "build and install it again before you check it.</p>")
    action = link("dig-unmark")
    if seen.get("can_unmark") and action:
        out.append(f'<form method="post" action="{e(action)}" class="rowf">'
                   '<button type="submit" class="btn line">Take the markers out</button>'
                   '<span class="small sub">Runs <code>echolot mark --remove</code>.</span></form>')
    return '<div class="stack gap-3">' + "".join(out) + "</div>"


def _close(seen: dict[str, Any], link: Link) -> str:
    action = link("dig-close")
    if not seen.get("can_close") or not action:
        return ""
    return (f'<div class="stack gap-2"><p class="measure">The investigation stays open with the rounds it has, and '
            "Check the app waits while it is open. Close it to check the app, or to ask anew.</p>"
            f'<form method="post" action="{e(action)}" class="rowf"><button type="submit" class="btn line">'
            "Close the investigation</button></form></div>")


def _spent(seen: dict[str, Any]) -> str:
    """How long it ran, and what the client says it spent: Claude Code an
    estimate in dollars, Codex the tokens it counted, and of those, where it
    says, how many it read again from its cache."""
    bits = []
    if seen.get("ran"):
        bits.append(f"It ran for {_minutes(seen['ran'])}")
    if seen.get("cost"):
        by = "by its own count" if seen.get("client") == "codex" else "by its own estimate"
        bits.append(f"{'and ' if bits else 'It '}reports {seen['cost']} of usage, {by}"
                    + (f", {seen['cached']} of them read again from its cache" if seen.get("cached") else ""))
    return f'<p class="small sub">{e(" ".join(bits) + ".")}</p>' if bits else ""


def _refused(seen: dict[str, Any]) -> str:
    """What it tried and was refused, as it tried it, folded."""
    refused = seen.get("denied") or []
    if not refused:
        return ""
    items = "".join(f"<li><code>{e(r)}</code></li>" for r in refused[:20])
    said = ("What its sandbox refused it, or Codex declined, without asking anyone:" if seen.get("client") == "codex"
            else "What it tried without a rule that allows it, refused without asking anyone:")
    return kit.fold(f"What it was refused ({len(refused)})",
                    f'<p class="small sub">{said}</p><ul class="dig-files">{items}</ul>')


# How each file it changed outside the allowed folders is said.
_HOW = {"changed": "changed", "new": "new", "removed": "removed"}


def _outside(seen: dict[str, Any]) -> str:
    """The files a client whose edits its grant cannot keep to the allowed
    folders changed outside them, whatever way it ended; nothing for one
    whose grant can (`dig` gives no `outside` then)."""
    found = seen.get("outside")
    if not found:
        return ""
    allowed = found.get("allowed") or []
    where = _code_list([p or "." for p in allowed]) if allowed else "the folders it may edit"
    if not found.get("checked"):
        return f'<p class="measure" id="dig-outside">{kit.prose(found.get("why") or "")}</p>'
    files = found.get("files") or []
    if not files:
        return (f'<p class="measure" id="dig-outside">Nothing outside {where} was changed: the page noted the '
                f"project's files when {e(_who(seen))} started, and looked again now.</p>")
    count = len(files)
    items = "".join(f'<li><code>{e(f["path"])}</code> <span class="sub">{_HOW.get(f["how"], e(f["how"]))}</span></li>'
                    for f in files[:20])
    more = f'<li class="sub">and {count - 20} more</li>' if count > 20 else ""
    return (f'<div class="stack gap-2" id="dig-outside"><p class="measure"><b>{e(_who(seen))} changed {count} '
            f'file{"s" if count != 1 else ""} outside {where}.</b> Its edits cannot be kept to those folders, so '
            "look at these before you keep them, with the tools you use: <code>git status</code> and "
            f'<code>git diff</code> among them.</p><ul class="dig-files">{items}{more}</ul></div>')


def panel(seen: dict[str, Any], link: Link) -> str:
    """The agent on its investigation's page: while it runs, what it is doing
    and Stop; once it ended, how, and what is left to clear up."""
    if seen["status"] == "running":
        stop = link("dig-stop")
        stopping = seen.get("stopping")
        button = (f'<form method="post" action="{e(stop)}" class="rowf"><button type="submit" class="btn line press"'
                  f'{" disabled" if stopping else ""}>Stop</button>'
                  f'<span class="small sub">Ends {e(_who(seen))} and everything it started.</span></form>' if stop else "")
        quiet = quiet_words(seen)
        now = _now(seen)
        ran = _ran(seen)
        poll = link("dig-state")
        rev = f'{seen.get("rounds")}:{seen.get("hunt_status")}'
        return (f'<section class="dig stack gap-3" id="dig" data-state="running" data-rev="{e(rev)}"'
                + (f' data-poll="{e(poll)}"' if poll else "") + ' aria-label="The agent at work">'
                f'<div class="dig-now"><i class="dig-spin" aria-hidden="true"></i><p><b id="dig-phase">{e(now)}</b></p></div>'
                f'<p class="small sub measure" id="dig-ran">{e(ran)}</p>'
                f'<div class="callout" id="dig-quiet"{"" if quiet else " hidden"}>{kit.icon("yellow")}'
                f'<p class="small" id="dig-quiet-t">{e(quiet)}</p></div>'
                f'{button}<p class="vh" role="status" id="dig-said">{e(now)}</p></section>')
    if seen["status"] == "done" and seen.get("verdict"):
        return (f'<section class="dig stack gap-2" id="dig" data-state="done">'
                f'<p class="small sub"><b>{e(seen["title"])}</b> {kit.prose(seen["text"])}</p>{_spent(seen)}'
                f'{_refused(seen)}{_outside(seen)}</section>')
    parts = [f'<p class="measure"><b>{e(seen["title"])}</b> {kit.prose(seen["text"])}</p>',
             _spent(seen), _said(seen.get("said")), _refused(seen), _outside(seen), _left(seen, link),
             _close(seen, link)]
    return f'<section class="dig stack gap-4" id="dig" data-state="{e(seen["status"])}">{"".join(parts)}</section>'


def answer_parts(seen: dict[str, Any], link: Link) -> dict[str, Any]:
    """What the investigation's page adds for its agent: the panel, its
    styles, and while it runs the script that asks how far it is and the
    line that loads the page again where script is off."""
    going = seen["status"] == "running"
    # Once it ended with no answer, the investigation's own line ("each round
    # the agent records appears below") is no longer so: no agent works in it.
    lead = "" if going or seen.get("hunt_status") != "open" else \
        "No answer, and no agent works in it now: the one the page started has ended. Its rounds are below."
    return {"html": panel(seen, link), "css": CSS, "js": POLL_JS if going else (), "head": HEAD if going else "",
            "lead": lead}


def progress(seen: dict[str, Any] | None) -> str:
    """What a page of a run asks for while it runs: the phase, how long it
    has worked, the watchdog's sentence, and a mark that changes when the page
    has to be drawn anew."""
    seen = seen or {"status": "none"}
    now = _now(seen)
    going = seen.get("status") == "running"
    quiet = quiet_words(seen) if going else ""
    ran = _ran(seen) if going else ""
    rev = f'{seen.get("rounds")}:{seen.get("hunt_status")}'
    return (f'<div id="dig-now" data-state="{e(seen.get("status") or "none")}" data-rev="{e(rev)}" '
            f'data-said="{e(now)}" data-ran="{e(ran)}" data-quiet="{e(quiet)}" '
            f'data-stopping="{"1" if seen.get("stopping") else ""}"></div>')


# ---------------------------------------------------------------------------
# on the start page


def on_start(seen: dict[str, Any] | None, link: Link) -> str:
    """Which one is running, said on the start page: an agent the page
    started, with the way to its page. Nothing once it is no news."""
    if not seen or not seen.get("hunt"):
        return ""
    href = link("answer-of", n=seen["hunt"])
    go = f' <a class="lnk" href="{e(href)}">See how far it is</a>' if href else ""
    if seen["status"] == "running":
        doing = (seen.get("phase") or {}).get("words") or "Working"
        return (f'<div class="callout" id="dig-on"><p><b>An agent is digging deeper.</b> '
                f'{e(doing)}.{go}</p></div>')
    if seen.get("can_close"):
        go = f' <a class="lnk" href="{e(href)}">See what it left</a>' if href else ""
        return (f'<div class="callout" id="dig-on">{kit.icon("yellow")}<p><b>The agent the page started has '
                f'ended, and its investigation is still open.</b> {e(seen["title"])}{go}</p></div>')
    return ""
