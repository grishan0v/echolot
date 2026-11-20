"""`echolot ui`: a local page that shows whether the setup is ready and opens
the reports.

A server from the standard library on 127.0.0.1. It keeps no state and writes
nothing: every page is drawn when it is asked for, from the files under
`.echolot/` and from the functions that stand behind `status`, `doctor`,
`scan` and `hunt --list` (`echolot/ready.py`). It is a face for a person over
the same commands and files; the agent's path stays the CLI.

What it will and will not answer, decided here before any request can start a
command (the next step, a button that records, adds such requests):

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
- Anything other than GET also has to come from the server's own origin. No
  such request exists yet; the refusal does.
- It serves the pages it draws and nothing else. There is one table of
  routes, no route reads a file by a path taken from the address, and no page
  fetches anything: the pages carry the report page's content policy, and the
  server sends it as a header too.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote

from . import main as cli
from . import ready, recorder, state
from .config import ConfigError
from .pages import kit, report_page, start_page

HOST = "127.0.0.1"
READS = ("GET", "HEAD")

# What the browser is told with every answer, beside the policy each page
# carries in itself: nothing is fetched, framed, sniffed or kept, and no
# address of the server (the token is in it) is passed on as a referrer.
HEADERS = (
    ("Content-Security-Policy", kit._POLICY + "; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
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
    # Pages are drawn one at a time: reading `status` catches what it prints.
    drawing: threading.Lock = field(default_factory=threading.Lock)

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
        no such page: whoever asked draws no link then."""
        route = next((r for r in ROUTES if r.name == name and r.method == "GET"), None)
        if route is None:
            return None
        try:
            path = route.path.format(**{k: quote(str(v), safe="") for k, v in params.items()})
        except KeyError:
            return None
        return f"/{self.token}/{path}"


@dataclass
class Visit:
    """One request: the site, and the project's state read once for it."""
    site: Site
    _st: dict | None = None

    @property
    def st(self) -> dict:
        if self._st is None:
            self._st = state.project_state(self.site.project, self.site.config)
        return self._st

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


# ---------------------------------------------------------------------------
# the pages


def _start(visit: Visit, params: dict[str, str]) -> Page:
    site = visit.site
    view = ready.read(site.project, site.config, site.src, site.tp_binary, st=visit.st)
    return Page(start_page.render(view, site.url, visit.steps("start")))


def _nothing(visit: Visit, title: str, text: str, status: int = 404) -> Page:
    return Page(start_page.notice(title, text, visit.site.url("start"), visit.steps(None)), status)


def _has_report(visit: Visit) -> bool:
    rep = visit.st.get("report")
    return bool(rep) and not rep.get("error")


def _report(visit: Visit, params: dict[str, str]) -> Page:
    """The last report: of the scenario this page is about, or of the one
    named, drawn from its report.json now and not read from a page on disk."""
    st, rep, mine = visit.st, visit.st.get("report"), True
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
    return Page(report_page.render(data, where=where, last=True, steps=visit.steps("report" if mine else None)))


def _hunt_report(visit: Visit, params: dict[str, str]) -> Page:
    """A report an investigation filed, as a report page."""
    n, k = int(params["n"]), int(params["k"])
    found = ready.archived(visit.site.project, n, k)
    if found is None:
        return _nothing(visit, "No such report", f"Investigation {n} filed no report {k} that reads.")
    data, path = found
    return Page(report_page.render(data, where=cli._shown_path(path, visit.site.project), last=False,
                                   steps=visit.steps(None), crumb=f"investigation {n}, report {k}"))


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


def answer(site: Site, method: str, target: str, headers: Mapping[str, str]) -> Reply:
    """What the server says to one request. Every check that guards the pages
    is here, in the order a request has to pass them."""
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
    route, params, known = find("GET" if method in READS else method, rest)
    visit = Visit(site)
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
        print(f"echolot ui: could not draw the page `{name}`: {type(e).__name__}: {e}", file=sys.stderr)
        html = start_page.notice("This page could not be drawn",
                                 f"echolot ran into an error while reading the project: {type(e).__name__}: {e}",
                                 site.url("start"))
        return Reply(500, html)
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

    def _handle(self, method: str) -> None:
        reply = answer(self.server.site, method, self.path, self.headers)  # type: ignore[attr-defined]
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
