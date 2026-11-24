"""`echolot ui`: a local page that shows whether the setup is ready, checks
the app at the press of a button and opens the reports.

A server from the standard library on 127.0.0.1. It keeps no state: every
page is drawn when it is asked for, from the files under `.echolot/` and from
the functions that stand behind `status`, `doctor`, `scan` and `hunt --list`
(`echolot/ready.py`). It is a face for a person over the same commands and
files; the agent's path stays the CLI.

A page that is only read writes nothing. Two requests do more, and both are
a form a page of this server sends: one starts a check (`echolot/check.py`:
`collect`, then `analyze --html`, then `compare`, run as a process of their
own so that pages go on being drawn), the other asks the check that is
running to stop. What is running and how far is read from the files that
process and `collect` write, so a reloaded page, a second tab and a server
started again all show the same. A third sets a project up
(`echolot/project_setup.py`): it creates `echolot.yml` from the draft `scan`
proposes, never over a file that is there, and where echolot was never set up
it runs `echolot init` the same way, as a process of its own. A fourth sets
or changes the time limit afterwards (`echolot/time_limit.py`): it rewrites
the one line of that `echolot.yml` the page wrote for the limit, while the
line still reads as the page wrote it, and nothing else in the file. The
last ones dig deeper from a report (`echolot/dig.py`): they open an
investigation with the report's question and start the person's own Claude
Code or Codex in it, as a process of its own, with a command line and a grant
the server builds; they stop it, take out the markers it left, and close an
investigation it left open.

What it will and will not answer:

- It listens on 127.0.0.1 and nowhere else, and a request whose `Host` is not
  the server's own address is refused: a page of another site that got its
  name pointed at this machine reaches nothing.
- A token is made at every start. It is the first part of every address, the
  one the browser is opened with included, and a request without it is
  refused. It travels in the path and not in a cookie: a cookie is sent by
  the browser to whoever asks for the address, from any page, and is shared by
  every port of 127.0.0.1, so two servers at once would hand each other's
  over; an address has to be known to be asked for. The price is that the
  token is in the address bar and the history, where it stops meaning anything
  when the server stops.
- Anything other than GET also has to come from the server's own origin,
  and its body is a form of at most `BODY_CAP` bytes. Nothing in a form is
  used as a path or as a command: a scenario is one the config names, a
  number of launches is a small whole number, an investigation is one the
  project has, what to set up is one of the names the page offered, a time
  limit is a number of seconds in a stated range, the fingerprints of the
  config a page saw are compared with the file's, never opened, and a ticked
  check is one the report on disk has, a client named in an address is one
  the page starts; an agent's command line is built from the config and the
  report alone.
- It serves the pages it draws and nothing else. There is one table of
  routes, no route reads a file by a path taken from the address, and no page
  fetches anything from elsewhere: a served page may send a form to this
  server and ask it how far a run is (`kit.SERVED`), and the server sends
  that policy as a header too. A page written to disk keeps the strict one.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import shlex
import socketserver
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, unquote

from . import check, job, ready, recorder, state
from . import project_setup
from . import time_limit
from . import hunt as hunt_mod
from . import main as cli
from .config import Config, ConfigError
from .pages import answer_page, compare_page, kit, report_page, start_page
from .pages import setup_page
from .pages import limit_page
from . import dig
from .pages import dig_page

HOST = "127.0.0.1"
READS = ("GET", "HEAD")
# The most a request may carry in its body, in bytes, and the most fields a
# form may have: a press sends a scenario's name and a number.
BODY_CAP = 4096
FIELDS = 16
FORM = "application/x-www-form-urlencoded"

# What the browser is told with every answer, beside the policy each page
# carries in itself: nothing is fetched from elsewhere, framed, sniffed or
# kept, and no address of the server (the token is in it) is passed on as a
# referrer to any other origin. `same-origin` and not `no-referrer`: under
# `no-referrer` a browser sends `Origin: null` with a form, and the form of
# the server's own page would be refused with every other.
HEADERS = (
    ("Content-Security-Policy", kit.SERVED + "; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)


# ---------------------------------------------------------------------------
# the site: one project, one token, and the addresses of its pages


@dataclass
class Site:
    """What one running server shows: a project, and how its pages are
    reached. Nothing is remembered between two requests."""
    project: Path
    config: str
    src: ready.Sources
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    port: int = 0
    tp_binary: str | None = None
    # How a check's process is started. A test hands in a double.
    spawn: Callable[[Path, str, dict[str, Any]], Any] = job._spawn
    # Pages are drawn one at a time: reading `status` catches what it prints.
    # A press is answered under the same lock, so two of them cannot both
    # find nothing running.
    drawing: threading.Lock = field(default_factory=threading.Lock)
    # The agent a press of Dig deeper starts (`echolot/dig.py`): how the
    # clients of its table are found (one client or a list of them), how a
    # command of echolot's is run and waited for, and how the processes under
    # a running agent are listed. A test hands in doubles for all three.
    agent: Callable[[], Any] = dig.find_all
    run: Callable[..., Any] = subprocess.run
    procs: Callable[[], list[dict[str, Any]]] = job.processes

    @property
    def host(self) -> str:
        return f"{HOST}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.host}"

    @property
    def address(self) -> str:
        """Where the browser is sent: the start page, token included."""
        return f"{self.origin}/{self.token}/"

    def url(self, name: str, **params: Any) -> str | None:
        """The address of a page by its route's name, or None when there is
        no such page: whoever asked draws no link then. The same for an
        address a form is sent to: no route, no button."""
        route = next((r for r in ROUTES if r.name == name), None)
        if route is None:
            return None
        try:
            path = route.path.format(**{k: quote(str(v), safe="") for k, v in params.items()})
        except KeyError:
            return None
        return f"/{self.token}/{path}"


_UNREAD = object()


@dataclass
class Visit:
    """One request: the site, and what is read of the project once for it."""
    site: Site
    form: dict[str, str] = field(default_factory=dict)   # what a request that is no read sent
    _st: dict | None = None
    _last: Any = _UNREAD

    @property
    def st(self) -> dict:
        if self._st is None:
            self._st = state.project_state(self.site.project, self.site.config)
        return self._st

    @property
    def last_hunt(self) -> dict | None:
        """The newest investigation, open or not: the one `hunt --show` shows."""
        if self._last is _UNREAD:
            self._last = hunt_mod.last(self.site.project)
        return self._last

    def steps(self, here: str | None) -> list[tuple[str, str, bool]]:
        """The path in the top bar: every page that is a step and has
        something to show now. One step alone is no path."""
        found = [(r.step, self.site.url(r.name) or "", r.name == here)
                 for r in ROUTES if r.step and r.method == "GET" and r.there(self)]
        return found if len(found) > 1 else []


@dataclass
class Page:
    html: str
    status: int = 200
    location: str | None = None     # where the browser is sent instead, after a form


# ---------------------------------------------------------------------------
# the pages


def _start(visit: Visit, params: dict[str, str]) -> Page:
    site = visit.site
    view = ready.read(site.project, site.config, site.src, site.tp_binary, st=visit.st)
    found = check.view(site.project, visit.st, view["ready"]) if site.url("check") else None
    # Setting the project up, where the server takes that press: the question
    # in place of a button that has nothing to press yet.
    setup = setup_page.parts(project_setup.view(site.project, site.config, visit.st, view["facts"], view["next"],
                                                view["ready"]), site.url) if site.url("setup") else None
    if setup and setup.get("instead"):
        found = None
    # The time limit under All settings, where the server takes that press.
    limit = limit_page.setting(time_limit.view(site.project, site.config, visit.st), site.url)
    digging = (dig_page.on_start(dig.view(site.project, visit.st, procs=site.procs, count=False), site.url)
               if site.url("dig") else "")
    return Page(start_page.render(view, site.url, visit.steps("start"), check=found, setup=setup, limit=limit,
                                  dig_html=digging))


def _nothing(visit: Visit, title: str, text: str, status: int = 404) -> Page:
    return Page(start_page.notice(title, text, visit.site.url("start"), visit.steps(None)), status)


def _has_report(visit: Visit) -> bool:
    rep = visit.st.get("report")
    return bool(rep) and not rep.get("error")


def _report(visit: Visit, params: dict[str, str]) -> Page:
    """The last report: of the scenario this page is about, or of the one
    named, drawn from its report.json now and not read from a page on disk."""
    st, rep, mine = visit.st, visit.st.get("report"), True
    name = None
    if "scenario" in params:
        name = unquote(params["scenario"])
        found = next((sc for sc in st.get("scenarios") or [] if sc["name"] == name), None)
        if found is None:
            return _nothing(visit, "No such scenario", "The config names no scenario by that name.")
        rep, mine = found["report"], bool(found.get("focus"))
    if not rep:
        return _nothing(visit, "No report yet", "It appears here once the first recordings are read. "
                        "The start page says what to do next.")
    try:
        data = cli._load_report(rep["path"])
    except ConfigError as e:
        return _nothing(visit, "The report does not read", f"{e}. Run `echolot analyze` again to write it anew.")
    where = cli._shown_path(rep["path"], visit.site.project)
    # The time limit beside the verdict: this is a scenario's last report,
    # read with the limit the config had then.
    limit = limit_page.beside(time_limit.view(visit.site.project, visit.site.config, st, report=data, scenario=name),
                              visit.site.url)
    return Page(report_page.render(data, where=where, last=True, steps=visit.steps("report" if mine else None),
                                   policy=kit.SERVED, limit=limit, dig=_dig_parts(visit, data, params)))


def _hunt_report(visit: Visit, params: dict[str, str]) -> Page:
    """A report an investigation filed, as a report page."""
    n, k = int(params["n"]), int(params["k"])
    found = ready.archived(visit.site.project, n, k)
    if found is None:
        return _nothing(visit, "No such report", f"Investigation {n} filed no report {k} that reads.")
    data, path = found
    return Page(report_page.render(data, where=cli._shown_path(path, visit.site.project), last=False,
                                   steps=visit.steps(None), crumb=f"investigation {n}, report {k}",
                                   policy=kit.SERVED))


def _has_hunt(visit: Visit) -> bool:
    return visit.last_hunt is not None


def _answer(visit: Visit, params: dict[str, str]) -> Page:
    """An investigation as a page: the last one, or the one numbered. Drawn
    from its record and the reports it filed as they are now, the way `hunt
    --show --html` writes it, and not read from an `answer.html` on disk.

    The page has a state for whatever the investigation is, so every one
    that exists has something to show: the agent's answer with its causes,
    the text one was closed with, the rounds so far of one still open, the
    rounds of one that was left.
    """
    site, last = visit.site, visit.last_hunt
    if "n" in params:
        hunt = hunt_mod.find(site.project, params["n"])
        if hunt is None:
            return _nothing(visit, "No such investigation", f"This project has had no investigation {int(params['n'])}.")
    else:
        hunt = last
        if hunt is None:
            return _nothing(visit, "No investigation yet", "One opens when an agent is asked to dig deeper. "
                            "Its rounds, and then its answer, appear here.")
    reports = hunt_mod.archived_reports(site.project, hunt)
    again = cli._check_again_line(site.project, site.config, hunt, reports)
    mine = last is not None and hunt.get("n") == last.get("n")
    return Page(answer_page.render(hunt, reports, again=again, action_html=_again_button(visit, hunt),
                                   steps=visit.steps("answer" if mine else None), policy=kit.SERVED,
                                   agent=_agent_parts(visit, hunt), local_time=True))


def _comparison(visit: Visit, rep: dict | None = None) -> dict[str, Any] | None:
    """The comparison beside a scenario's last report (the one this page is
    about, unless another's is handed in), when it is of that report and
    carries its plain reading: what `compare --html` wrote, as a check that
    had a report before it does."""
    rep = rep or visit.st.get("report")
    return check.comparison(Path(rep["path"]).parent) if rep and not rep.get("error") else None


def _has_after(visit: Visit) -> bool:
    return _comparison(visit) is not None


def _after(visit: Visit, params: dict[str, str]) -> Page:
    """What changed between a scenario's last report and the one before it:
    the page `compare --html` writes, drawn from comparison.json now and not
    read from a `comparison.html` on disk. Of the scenario this page is
    about, or of the one named."""
    rep, mine = visit.st.get("report"), True
    if "scenario" in params:
        name = unquote(params["scenario"])
        found = next((sc for sc in visit.st.get("scenarios") or [] if sc["name"] == name), None)
        if found is None:
            return _nothing(visit, "No such scenario", "The config names no scenario by that name.")
        rep, mine = found["report"], bool(found.get("focus"))
    found = _comparison(visit, rep) if rep else None
    fault = check.comparison_fault(Path(rep["path"]).parent) if found is None and rep and not rep.get("error") else None
    if fault:
        return _nothing(visit, "The comparison does not read",
                        f"The comparison beside the last report does not hold what `echolot compare` writes: {fault}. "
                        "It was edited by hand or written by another tool. Running `echolot compare --html` "
                        "again writes it anew.")
    if found is None:
        return _nothing(visit, "No comparison yet", "It appears here once the app has been checked a second time: "
                        "the new report is then compared with the one before it. A comparison made in a terminal "
                        "shows here when it is of the last report and was written with `echolot compare --html`.")
    return Page(compare_page.render(found, steps=visit.steps("after" if mine else None), policy=kit.SERVED))


def _again_button(visit: Visit, hunt: dict[str, Any]) -> str:
    """The button in the answer's own place for it: the line that page gives
    to copy, started from the page. Nothing where the server takes no press,
    for an investigation with no number, or for a scenario the config no
    longer names."""
    site = visit.site
    if not site.url("check") or not hunt.get("n"):
        return ""
    lines = ready.lines(site.project, site.config, site.src, visit.st)
    found = check.view(site.project, visit.st, lines)
    choice = check.chosen(found["offer"], hunt.get("scenario"))
    if choice is None:
        return ""
    run = found["job"]
    running = check.running_words(run, visit.st.get("collect")) if run and run["status"] == job.RUNNING else None
    why = found["offer"].get("why") or choice.get("why")
    said = check.hint(check.RECORD, choice["mode"], choice.get("iterations"), choice.get("section") or {},
                      each_s=check.read_s(site.project, choice.get("name")))
    return start_page.again(choice, why, said, site.url, hunt["n"], running)


# ---------------------------------------------------------------------------
# what is no read: a press of the button, and Stop


def _press(visit: Visit, params: dict[str, str]) -> Page:
    """Start a check: the commands of `check.steps`, as a process of their
    own. The press is held against the project as it is now. Refused, it
    answers with a page that says why; started, it sends the browser to the
    start page, where the run is."""
    site = visit.site
    if project_setup.busy(site.project):
        return _nothing(visit, "Setting up is still under way", "echolot is checking this computer, which takes "
                        "about a minute. The start page shows how far it is, and the button once it is done.", 409)
    try:
        press = check.asked(visit.form)
        lines = ready.lines(site.project, site.config, site.src, visit.st)
        found = check.view(site.project, visit.st, lines)
        check.start(site.project, site.config, found["offer"], found["job"], press, spawn=site.spawn)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, site.url("start"))


def _stop(visit: Visit, params: dict[str, str]) -> Page:
    """Ask the running check to stop. With none running there is nothing to
    ask; either way the start page says where things stand."""
    check.stop(visit.site.project)
    return Page("", 303, visit.site.url("start"))


def _progress(visit: Visit, params: dict[str, str]) -> Page:
    """How far the run is: the rows of its list, for a page that shows a run
    and redraws them in place. Read from the run's two files alone."""
    return Page(start_page.progress(check.progress(visit.site.project)))


def _set_up(visit: Visit, params: dict[str, str]) -> Page:
    """Set the project up (`echolot/project_setup.py`): write the config
    `scan` drafts and, where echolot was never here, run `echolot init`. The
    press is held against the project as it is now: the repository is read
    anew. Refused, it answers with a page that says why and that nothing was
    written; taken, it sends the browser to the start page."""
    site = visit.site
    try:
        press = project_setup.asked(visit.form)
        view = ready.read(site.project, site.config, site.src, site.tp_binary, st=visit.st)
        project_setup.start(site.project, site.config, visit.st, view["facts"], view["next"], press, spawn=site.spawn)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, site.url("start"))


def _setup_progress(visit: Visit, params: dict[str, str]) -> Page:
    """How far a setup is, for the page that shows one and redraws its rows
    in place. Read from the job's file alone."""
    return Page(setup_page.progress(project_setup.progress(visit.site.project)))


def _set_limit(visit: Visit, params: dict[str, str]) -> Page:
    """Set or change the time limit (`echolot/time_limit.py`): rewrite the one
    line of `echolot.yml` the page wrote for it, as the file is now. Refused,
    it answers with a page that says why and that nothing was written; taken,
    or the same press again, it sends the browser back to the limit."""
    site = visit.site
    try:
        press = time_limit.asked(visit.form)
        time_limit.write(site.project, site.config, press)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, f"{site.url(press['back'])}#{limit_page.ID}")


# ---------------------------------------------------------------------------
# Dig deeper: the person's own agent, started from a report (`echolot/dig.py`)


def _config_path(visit: Visit) -> str:
    known = visit.st.get("config") or {}
    return str(known.get("path") or visit.site.config)


def _dig_report(visit: Visit, params: dict[str, str]) -> tuple[dict[str, Any], str, str | None]:
    """The report a press of Dig deeper is about: the last one of the
    scenario the page is about, or of the one its address names, as it is
    on disk now. Its name as the request gives it, and the scenario's name
    when the address named one."""
    st, rep = visit.st, visit.st.get("report")
    named = None
    if "scenario" in params:
        named = unquote(params["scenario"])
        found = next((sc for sc in st.get("scenarios") or [] if sc["name"] == named), None)
        if found is None:
            raise check.Refusal("No such scenario", "The config names no scenario by that name.", 404)
        rep = found["report"]
    if not rep or rep.get("error"):
        raise check.Refusal("No report to dig into", "There is no report that reads. The start page says what to do "
                            "next.", 409)
    try:
        data = cli._load_report(rep["path"])
    except ConfigError as e:
        raise check.Refusal("The report does not read", f"{e}. Run `echolot analyze` again to write it anew.",
                            409) from None
    return data, cli._shown_path(rep["path"], visit.site.project), named


def _clients(site: Site) -> list[dig.Client]:
    """The clients the site finds: one, or a list of them, as it was handed."""
    found = site.agent()
    return [found] if isinstance(found, dig.Client) else list(found)


def _whys(offered: list[tuple[dig.Client, str | None]]) -> list[str] | None:
    """Why none of the clients can start, each reason once, in their order."""
    return list(dict.fromkeys(why for _, why in offered if why)) or None


def _dig_parts(visit: Visit, data: dict[str, Any], params: dict[str, str]) -> dict[str, Any] | None:
    """What a served report adds to start an agent from it, where the server
    takes that press: the way to the screen before it starts, or why there
    is none. The phone is not asked here: that screen asks it."""
    site = visit.site
    action = site.url("dig-ask-of", scenario=unquote(params["scenario"])) if "scenario" in params else site.url("dig-ask")
    if not action:
        return None
    offered = dig.offer(site.project, _clients(site), visit.st, [])
    can = [dig.adapter(c.key).name for c, why in offered if why is None]
    return dig_page.report_parts(report_page.checks(data), action, bool(can), None if can else _whys(offered),
                                 who=can)


def _phone(lines: list[dict[str, Any]]) -> str | None:
    """The phone's name as the ready line says it, when it is connected."""
    line = next((line for line in lines if line.get("lead") == "The phone"), None)
    if not line or line.get("tone") != ready.GREEN:
        return None
    text = str(line.get("text") or "")
    if "is connected:" not in text:
        return None
    return text.split("is connected:", 1)[1].strip().rstrip(".") or None


def _dig_url(site: Site, name: str, client: str | None, scenario: str | None) -> str | None:
    """The address of a press about the agent: with the client named in it
    where one is, and the scenario where the report's address named one."""
    params = {"client": client} if client else {}
    if scenario:
        params["scenario"] = scenario
    return site.url(name + ("-with" if client else "") + ("-of" if scenario else ""), **params)


def _dig_ask(visit: Visit, params: dict[str, str]) -> Page:
    """The screen before an agent starts: what a press of Start would do,
    for the checks ticked on the report, with the client the address names
    or, where it names none, the first the page offers (`dig.offer`), and a
    way to each other one that can start. Nothing is started here."""
    site = visit.site
    try:
        data, where, named = _dig_report(visit, params)
        picks = dig.picked(data, dig.asked(visit.form))
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    lines = ready.lines(site.project, site.config, site.src, visit.st)
    offered = dig.offer(site.project, _clients(site), visit.st, lines)
    chosen = dig.pick(offered, params.get("client"))
    if chosen is None:
        return _nothing(visit, "No such agent", "The page starts Claude Code or Codex, and this address names "
                        "neither.")
    client, why = chosen
    if why and not any(w is None for _, w in offered):
        # None can start: every reason, once each.
        why = _whys(offered)
    agent = dig.adapter(client.key)
    paths = None
    known = visit.st.get("config") or {}
    try:
        cfg = Config.load(known["path"], scenario=known.get("scenario"))
        paths = dig.allowed(cfg)
        rounds = cfg.get("loop.max_rounds") or 3
        several = len(cfg.scenarios) > 1
    except check.Refusal as e:
        why, rounds, several = why or e.text, 3, False
    except (ConfigError, KeyError, OSError):
        rounds, several = 3, False
    scenario = data.get("scenario")
    opening = shlex.join(["echolot", "hunt", dig.hunt_question(data), "--since", "unknown",
                          *(["--scenario", str(scenario)] if several and scenario else [])])
    back = site.url("report-of", scenario=named) if named else site.url("report")
    # Start names its client in the address, unless it is Claude Code: a
    # press of Start without one starts Claude Code, as it did before Codex.
    action = _dig_url(site, "dig", None if client.key == dig.PROGRAM else client.key, named)
    others = [(dig.adapter(c.key).name, _dig_url(site, "dig-ask", c.key, named))
              for c, w in offered if w is None and c.key != client.key]
    return Page(dig_page.consent({
        "question": report_page.question(data), "picks": picks, "client": client, "why": why, "paths": paths,
        "phone": _phone(lines), "rounds": rounds, "changed": dig.changed_files(site.project),
        **agent.terms(site.project), "commands": dig.COMMANDS,
        "verdict": dig.VERDICT, "opening": opening,
        "running": agent.shown(paths, site.project) if paths is not None else "",
        "copy": report_page.request_text(data, picks, where, last=True), "action": action, "back": back or "",
        "keys": [c["key"] for c in picks], "many": len(picks) > report_page.FOCUS,
        "others": [(name, url) for name, url in others if url],
    }, site.url, visit.steps(None)))


def _dig_start(visit: Visit, params: dict[str, str]) -> Page:
    """Start the agent: open the investigation, then Claude Code in it as a
    process of its own. Held against the project as it is now. Refused, it
    answers with a page that says why; started, it sends the browser to the
    investigation's page, where the agent's work shows."""
    site = visit.site
    if project_setup.busy(site.project):
        return _nothing(visit, "Setting up is still under way", "echolot is checking this computer, which takes "
                        "about a minute. Dig deeper once it is done.", 409)
    try:
        data, where, _ = _dig_report(visit, params)
        picks = dig.picked(data, dig.asked(visit.form))
        lines = ready.lines(site.project, site.config, site.src, visit.st)
        # The client the address names, Claude Code where it names none: the
        # screen before the start drew its Start that way.
        key = params.get("client") or dig.PROGRAM
        client = next((c for c in _clients(site) if c.key == key), None)
        if client is None:
            raise check.Refusal("No such agent", "The page starts Claude Code or Codex, and this address names "
                                "neither.", 404)
        started = dig.start(site.project, visit.st, lines, data, picks, where, client,
                            run=site.run, spawn=site.spawn)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, site.url("answer-of", n=started["what"]["hunt"]))


def _dig_back(visit: Visit) -> str | None:
    """Where a press about the agent sends the browser: its investigation's page."""
    run = job.read(visit.site.project, dig.JOB)
    n = ((run or {}).get("what") or {}).get("hunt")
    return visit.site.url("answer-of", n=n) if isinstance(n, int) else visit.site.url("start")


def _dig_stop(visit: Visit, params: dict[str, str]) -> Page:
    """Ask the agent to stop. With none running there is nothing to ask;
    either way its investigation's page says where things stand."""
    dig.stop(visit.site.project)
    return Page("", 303, _dig_back(visit))


def _dig_unmark(visit: Visit, params: dict[str, str]) -> Page:
    """Take out the temporary markers an agent left: `echolot mark --remove`."""
    try:
        dig.unmark(visit.site.project, _config_path(visit), run=visit.site.run)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, _dig_back(visit))


def _dig_close(visit: Visit, params: dict[str, str]) -> Page:
    """Close the investigation an agent left open, with what came of it."""
    try:
        dig.close(visit.site.project, _config_path(visit), visit.st, run=visit.site.run)
    except check.Refusal as e:
        return _nothing(visit, e.title, e.text, e.status)
    return Page("", 303, _dig_back(visit))


def _dig_progress(visit: Visit, params: dict[str, str]) -> Page:
    """What the agent is doing, for its investigation's page, which says it in
    place and draws itself anew when a round comes in or the agent ends."""
    return Page(dig_page.progress(dig.progress(visit.site.project, procs=visit.site.procs)))


def _agent_parts(visit: Visit, hunt: dict[str, Any]) -> dict[str, Any] | None:
    """The agent the server started in this investigation, for its page."""
    site = visit.site
    if not site.url("dig") or not hunt.get("n"):
        return None
    run = job.read(site.project, dig.JOB)
    if run is None or (run.get("what") or {}).get("hunt") != hunt.get("n"):
        return None
    seen = dig.view(site.project, visit.st, run, procs=site.procs)
    return dig_page.answer_parts(seen, site.url) if seen else None


# ---------------------------------------------------------------------------
# the routes: one table. A page is one entry here; a request that changes
# something is one entry with another `method`.


def _always(visit: Visit) -> bool:
    return True


@dataclass(frozen=True)
class Route:
    name: str                                   # what a page calls it when it links to it
    path: str                                   # under the token: "hunt/{n}/report/{k}"
    draw: Callable[[Visit, dict[str, str]], Page]
    method: str = "GET"
    step: str | None = None                     # its name in the top bar's path, when it is a step
    there: Callable[[Visit], bool] = _always    # whether it has something to show now


ROUTES: list[Route] = [
    Route("start", "", _start, step="Start"),
    Route("report", "report", _report, step="Report", there=_has_report),
    Route("report-of", "report/{scenario}", _report),
    Route("hunt-report", "hunt/{n}/report/{k}", _hunt_report),
    Route("answer", "answer", _answer, step="Agent's answer", there=_has_hunt),
    Route("answer-of", "hunt/{n}/answer", _answer),
    Route("after", "after", _after, step="After the fix", there=_has_after),
    Route("after-of", "after/{scenario}", _after),
    Route("check", "check", _press, method="POST"),
    Route("check-stop", "check/stop", _stop, method="POST"),
    Route("check-state", "check/state", _progress),
    Route("setup", "setup", _set_up, method="POST"),
    Route("setup-state", "setup/state", _setup_progress),
    Route("limit", "limit", _set_limit, method="POST"),
    Route("dig-ask", "dig/ask", _dig_ask, method="POST"),
    Route("dig-ask-of", "dig/ask/{scenario}", _dig_ask, method="POST"),
    Route("dig", "dig/start", _dig_start, method="POST"),
    Route("dig-of", "dig/start/{scenario}", _dig_start, method="POST"),
    Route("dig-stop", "dig/stop", _dig_stop, method="POST"),
    Route("dig-unmark", "dig/unmark", _dig_unmark, method="POST"),
    Route("dig-close", "dig/close", _dig_close, method="POST"),
    Route("dig-state", "dig/state", _dig_progress),
    Route("dig-ask-with", "dig/with/{client}/ask", _dig_ask, method="POST"),
    Route("dig-ask-with-of", "dig/with/{client}/ask/{scenario}", _dig_ask, method="POST"),
    Route("dig-with", "dig/with/{client}/start", _dig_start, method="POST"),
    Route("dig-with-of", "dig/with/{client}/start/{scenario}", _dig_start, method="POST"),
]

# A part of an address that names something. Numbers are digits; a name is
# anything but a slash, and is only ever compared with the names the project
# has: nothing is opened by it.
_PART = {"n": "[0-9]{1,9}", "k": "[0-9]{1,9}"}


def _pattern(path: str) -> re.Pattern[str]:
    pieces = re.split(r"\{(\w+)\}", path)
    return re.compile("".join(
        f"(?P<{p}>{_PART.get(p, '[^/]+')})" if i % 2 else re.escape(p) for i, p in enumerate(pieces)))


def find(method: str, path: str) -> tuple[Route | None, dict[str, str], bool]:
    """The route for a request, the names its address carries, and whether
    the address is one the server knows under another method."""
    known = False
    for route in ROUTES:
        m = _pattern(route.path).fullmatch(path)
        if m is None:
            continue
        if route.method == method:
            return route, m.groupdict(), True
        known = True
    return None, {}, known


# ---------------------------------------------------------------------------
# a request, and the answer to it


@dataclass
class Reply:
    status: int
    body: str
    kind: str = "text/html; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


def _refused(why: str) -> Reply:
    """A refusal says nothing a stranger could use: no address, no token."""
    return Reply(403, f"echolot ui: refused. {why}\n", "text/plain; charset=utf-8")


def _form(headers: Mapping[str, str], body: bytes) -> dict[str, str] | None:
    """The fields of a form, each once. None for one with too many fields or
    one that is not text; a body that is no form has no fields."""
    if (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != FORM:
        return {}
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=FIELDS)
    except ValueError:
        return None
    return {name: values[0] for name, values in fields.items()}


def answer(site: Site, method: str, target: str, headers: Mapping[str, str], body: bytes | None = b"") -> Reply:
    """What the server says to one request. Every check that guards the pages
    is here, in the order a request has to pass them. `body` is what the
    request carried, or None when that was more than `BODY_CAP` bytes or of
    no stated length: it was not read then."""
    # 1. The request names this server and no other: a name that resolves
    #    here by somebody else's doing is not the server's own address.
    if headers.get("Host") != site.host:
        return _refused("This address is not the one echolot printed.")
    # 2. The token, the first part of the path.
    path = target.split("?", 1)[0].split("#", 1)[0]
    token, _, rest = path[1:].partition("/") if path.startswith("/") else ("", "", "")
    if not secrets.compare_digest(token.encode("utf-8", "backslashreplace"), site.token.encode()):
        return _refused("Open the address echolot printed when it started: it carries the token.")
    # 3. Whatever is not a read comes from the server's own pages.
    if method not in READS:
        if headers.get("Origin") != site.origin or headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            return _refused("Only a page of echolot ui may ask for this.")
        # 4. And carries a small form, or nothing.
        if body is None:
            return Reply(413, f"echolot ui: refused. A request may carry {BODY_CAP} bytes at most, "
                         "and has to say how many it carries and carry them.\n", "text/plain; charset=utf-8")
    route, params, known = find("GET" if method in READS else method, rest)
    fields = {} if method in READS else _form(headers, body or b"")
    if fields is None:
        return Reply(400, "echolot ui: refused. That is no form this server reads.\n", "text/plain; charset=utf-8")
    visit = Visit(site, fields)
    if route is None:
        if known:
            allowed = sorted({r.method for r in ROUTES if _pattern(r.path).fullmatch(rest)})
            return Reply(405, "echolot ui: this address takes no such request.\n", "text/plain; charset=utf-8",
                         (("Allow", ", ".join(allowed)),))
        if method not in READS:
            return Reply(404, "echolot ui: no such address.\n", "text/plain; charset=utf-8")
    name = route.name if route else "no such page"
    try:
        with site.drawing:
            page = (route.draw(visit, params) if route else
                    _nothing(visit, "No such page", "This address is no page of `echolot ui`."))
    except Exception as e:  # a page that fails says so, and the server goes on
        if method in READS:
            print(f"echolot ui: could not draw the page `{name}`: {type(e).__name__}: {e}", file=sys.stderr)
            html = start_page.notice("This page could not be drawn",
                                     f"echolot ran into an error while reading the project: {type(e).__name__}: {e}",
                                     site.url("start"))
        else:
            # A press that fell over may or may not have started something:
            # the start page reads it from the files, and says.
            print(f"echolot ui: could not answer `{name}`: {type(e).__name__}: {e}", file=sys.stderr)
            html = start_page.notice("That did not work",
                                     f"echolot ran into an error: {type(e).__name__}: {e}. The start page shows "
                                     "whether a check is running.", site.url("start"))
        return Reply(500, html)
    if page.location:
        return Reply(page.status, "", "text/plain; charset=utf-8", (("Location", page.location),))
    return Reply(page.status, page.html)


class _Handler(BaseHTTPRequestHandler):
    server_version = "echolot"
    sys_version = ""
    # One request, one answer, then the connection is closed: nothing to keep.
    protocol_version = "HTTP/1.0"
    # A connection that is opened and says nothing is let go.
    timeout = 30

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: a request line carries the token, and the terminal is not
        the place to repeat it at every click."""

    def _body(self, method: str) -> bytes | None:
        """What the request carries: nothing for a read, at most `BODY_CAP`
        bytes otherwise. More than that, or a length that is no number, is
        not read at all; and a body that ends before the length it stated is
        no body: half a form could be a whole one that says something else."""
        if method in READS:
            return b""
        if self.headers.get("Transfer-Encoding"):
            return None
        if self.headers.get("Content-Length") is None:
            return b""
        try:
            length = int(self.headers["Content-Length"])
        except ValueError:
            return None
        if not 0 <= length <= BODY_CAP:
            return None
        body = self.rfile.read(length)
        return body if len(body) == length else None

    def _handle(self, method: str) -> None:
        reply = answer(self.server.site, method, self.path, self.headers, self._body(method))  # type: ignore[attr-defined]
        body = reply.body.encode("utf-8")
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (*HEADERS, *reply.headers):
            self.send_header(name, value)
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")


# What a socket says once its connection has been let go under the thread that
# holds it: a closed descriptor on POSIX, "not a socket" on Windows.
_LET_GO = (errno.EBADF, errno.ENOTSOCK)


def _hung_up(e: BaseException | None) -> bool:
    """Whether a request fell over because its connection went away: the other
    end left, said nothing for too long, or the server closed it as it stopped."""
    return isinstance(e, (ConnectionError, TimeoutError)) or (isinstance(e, OSError) and e.errno in _LET_GO)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # A port just let go may be taken again at once; one that is held may
    # not. On POSIX that is what this option means. On Windows it would let a
    # second program listen on a port the first still holds, so it is off there.
    allow_reuse_address = os.name == "posix"

    def __init__(self, site: Site, port: int = 0):
        super().__init__((HOST, port), _Handler)
        self.site = site
        site.port = self.server_address[1]

    def server_bind(self) -> None:
        """Bind as `HTTPServer` does, without asking the resolver for a name.

        `HTTPServer.server_bind` sets `server_name` from `socket.getfqdn(host)`:
        a reverse lookup of 127.0.0.1 that can take half a minute on a Mac with
        mDNS or a VPN, before the address is printed (#489). Nothing here reads
        `server_name`; the `Host` header is checked against the site's own host.
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request: Any, client_address: Any) -> None:
        """What a request that fell over leaves in the terminal.

        The standard library prints a traceback here, headed by the address
        of whoever asked. A connection that went away is not news: a browser
        drops its spare connection, a page is left while it loads, and Ctrl-C
        can land while a connection accepted a moment before is handed to its
        thread, which then finds the socket closed under it (#472). Of those
        the server says nothing. Anything else is one line, as for a page
        that cannot be drawn.
        """
        e = sys.exc_info()[1]
        if _hung_up(e):
            return
        print(f"echolot ui: a request could not be answered: {type(e).__name__}: {e}", file=sys.stderr)


def make_server(project: Path, config: str, *, port: int = 0, src: ready.Sources | None = None,
                tp_binary: str | None = None) -> Server:
    """A server for the project, bound and not yet serving. Port 0 is a free one."""
    return Server(Site(project=project, config=config, src=src or ready.real_sources(), tp_binary=tp_binary), port)


def serve(project: Path, config: str, *, port: int | None = None, open_browser: bool = True,
          tp_binary: str | None = None, src: ready.Sources | None = None,
          opener: Callable[[str], bool] = webbrowser.open) -> int:
    """Run until Ctrl-C. The address goes to stdout, everything else to stderr."""
    try:
        server = make_server(project, config, port=port or 0, src=src, tp_binary=tp_binary)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"error: port {port} on {HOST} is taken: another program listens there, perhaps another "
                  f"`echolot ui`. Leave --port out and echolot picks a free one.", file=sys.stderr)
        else:
            print(f"error: cannot listen on {HOST}" + (f":{port}" if port else "")
                  + f": {e.strerror or e}", file=sys.stderr)
        recorder.failed(f"ui: cannot listen: {e.strerror or e}")
        return 2
    site = server.site
    print(site.address)
    sys.stdout.flush()
    opened = False
    if open_browser:
        try:
            opened = bool(opener(site.address))
        except (webbrowser.Error, OSError):
            opened = False
        if not opened:
            print("No browser could be opened from here: open the address above yourself.", file=sys.stderr)
    print(f"echolot ui shows {site.project}. Ctrl-C stops it.", file=sys.stderr)
    recorder.note(port=site.port, opened=opened)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\necholot ui stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0
