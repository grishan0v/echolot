#!/usr/bin/env python3
"""`echolot ui`: a local page that shows whether the setup is ready and opens the reports.

What is pinned here:

  - what the server refuses: a request under another name, one without the
    token, anything that is not a read from anywhere but its own pages, and a
    file asked for by its path;
  - that it writes nothing and fetches nothing;
  - that a page says what the CLI says: the next step and the words behind
    every line are `echolot`'s and `echolot scan`'s own;
  - each state a project can be in: no config, no phone, several phones, a
    phone that has not allowed debugging, an app that cannot be measured, no
    report, a report from before the plain keys, a macrobenchmark, several
    scenarios;
  - the edges of the command: a port that is taken, a browser that does not
    open, Ctrl-C, two servers at once.

The server is started on a free port in a thread and asked for pages. adb is a
double at the one place echolot runs it, so the sentences are the runner's own;
the self-check is a line in the run log. Nothing needs a phone or the network.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import echolot  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import ready, recorder, runner, ui  # noqa: E402
from echolot import scan as scan_mod  # noqa: E402
from echolot.pages import kit, start_page  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import SCHEME, _blocks, known, outside, read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(echolot.__file__).resolve().parent / "pages"
APP = "com.example.app"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:name=".App">
        {profileable}
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
GRADLE = """plugins { id("com.android.application") }
android {
    namespace = "com.example.app"
    defaultConfig { applicationId = "com.example.app" }
    buildTypes {
        debug { isDebuggable = true }
        release { isMinifyEnabled = false }
        create("benchmark") { initWith(getByName("release")) }
    }
}
"""
# A release type is always there; one that is debuggable too leaves nothing to measure on.
DEBUG_ONLY = GRADLE.replace("release { isMinifyEnabled = false }", "release { isDebuggable = true }").replace(
    '        create("benchmark") { initWith(getByName("release")) }\n', "")
HEAD = f"project:\n  package: {APP}\n  process: {APP}\n\n"
LAUNCH = HEAD + "scenario:\n  name: coldStart\n  window: launch\n  budget_ms: 500\n"
ANCHORS = HEAD + """scenario:
  name: coldStart
  start: {name: bindApplication}
  end: {name: "reportFullyDrawn() for MainActivity", _source: confirmed_by_user}

detectors:
  main_thread_block:
    min_slice_ms: 40
  frame_jank: false

runner:
  mode: command
  command: "adb shell am start -W -n com.example.app/.MainActivity"
  iterations: 3
  duration_ms: 9000
"""
BENCHMARK = HEAD + """scenario:
  name: startup
  start: {name: bindApplication}
  end: {name: first_screen}

runner:
  mode: gradle
  gradle_task: ":benchmark:connectedBenchmarkAndroidTest"
"""
SEVERAL = HEAD + """scenarios:
  coldStart:
    window: launch
    budget_ms: 500
  feedScroll:
    start: {name: feed_scroll_start}
    end: {name: feed_scroll_end}
    detectors:
      frame_jank:
        min_frames: 2
"""

ONE = "EXAMPLE0001          device product:shiba model:Pixel_8 device:shiba transport_id:1\n"
EMULATOR = "emulator-5554        device product:sdk model:sdk_gphone64 device:emu64a transport_id:2\n"
LOCKED = "EXAMPLE0001          unauthorized transport_id:1\n"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


class Adb:
    """adb, at the one place echolot runs it. It answers `adb devices` and
    nothing else: a page that asked the phone for more fails its test."""

    def __init__(self, monkeypatch):
        self.listing: str | None = ONE
        self.asked: list[list[str]] = []
        monkeypatch.setattr(runner, "_run", self.run)

    def run(self, args, stdin=None, timeout=120):
        self.asked.append(list(args))
        assert args[:2] == ["adb", "devices"], f"the page asked adb for {args}"
        if self.listing is None:
            raise runner.AdbNotFound("adb not found. It ships in the Android SDK platform-tools; "
                                     "make sure that directory is on PATH.")
        return "List of devices attached\n" + self.listing


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


class Project:
    """A project on disk, put together a piece at a time."""

    def __init__(self, root: Path):
        self.root = root

    def app(self, profileable: bool = True, gradle: str = GRADLE) -> "Project":
        main = self.root / "app" / "src" / "main"
        main.mkdir(parents=True, exist_ok=True)
        (main / "AndroidManifest.xml").write_text(
            MANIFEST.format(profileable='<profileable android:shell="true" />' if profileable else ""),
            encoding="utf-8")
        (self.root / "app" / "build.gradle.kts").write_text(gradle, encoding="utf-8")
        (self.root / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
        return self

    def config(self, text: str = LAUNCH) -> "Project":
        (self.root / "echolot.yml").write_text(text, encoding="utf-8")
        return self

    def agents(self, *hosts: str) -> "Project":
        """The choice of agents as `init` remembers it. None chosen is a
        project that declined the layer: nothing to install, nothing stale."""
        path = self.root / ".echolot" / "hosts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hosts": list(hosts)}), encoding="utf-8")
        return self

    def doctor(self, failed: tuple[str, ...] = ()) -> "Project":
        log = self.root / recorder.LOG_FILE
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        log.write_text(json.dumps({"ts": ts, "cmd": "doctor", "exit": 1 if failed else 0,
                                   "facts": {"checks": 178, "failed": list(failed)}}) + "\n", encoding="utf-8")
        return self

    def traces(self, scenario: str = "coldStart", n: int = 3) -> "Project":
        folder = self.root / ".echolot" / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (folder / f"{scenario}_iter{i:03d}.perfetto-trace").write_bytes(b"x")
        return self

    def report(self, where: str = ".echolot/out", data: dict | None = None) -> "Project":
        folder = self.root / where
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps(data or known()), encoding="utf-8")
        return self

    def hunt(self, n: int, question: str, status: str = "concluded", reports: int = 1, current: bool = False,
             **more) -> "Project":
        home = self.root / ".echolot" / "hunts" / str(n)
        (home / "reports").mkdir(parents=True, exist_ok=True)
        for k in range(1, reports + 1):
            (home / "reports" / f"{k:03d}.json").write_text(json.dumps(known()), encoding="utf-8")
        record = {"n": n, "question": question, "status": status, "scenario": "coldStart",
                  "opened_at": f"2026-11-1{n}T09:00:00+00:00", "collects": reports, "analyzes": reports,
                  "reports": reports, **more}
        where = self.root / ".echolot" / "hunt.json" if current else home / "hunt.json"
        where.write_text(json.dumps(record), encoding="utf-8")
        return self

    def ready(self) -> "Project":
        """Everything in place: the app, a config, a passed self-check."""
        return self.app().config().agents().doctor()


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    # Where there is no config, `echolot` reads the working directory.
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def site_of(project: Project, src: ready.Sources | None = None) -> ui.Site:
    config = project.root / "echolot.yml"
    return ui.Site(project=project.root, config=str(config) if config.is_file() else "echolot.yml",
                   src=src or ready.real_sources(), token="the-token-of-a-test", port=4321)


def get(site: ui.Site, path: str = "", method: str = "GET", **headers: str) -> ui.Reply:
    return ui.answer(site, method, f"/{site.token}/{path}", {"Host": site.host, **headers})


def start(project: Project, src: ready.Sources | None = None):
    reply = get(site_of(project, src))
    check("the start page is drawn", reply.status == 200, reply.body[:300])
    return read(reply.body)


def lines(page) -> dict[str, tuple[str, str]]:
    """The ready sentences by what each is about: its tone, and what it says."""
    out = {}
    for item in page.one("ul", "ready").all("li"):
        tone = re.search(r"var\(--(red|green|grey)\)", str(item.one("svg").kids[0].attrs)).group(1)
        text = item.text
        for name in ("This computer", "The phone"):
            if name in text:
                out[name] = (tone, text)
        if "This computer" not in text and "The phone" not in text:
            out["The app"] = (tone, text)
    return out


def setting(page, label: str):
    rows = [kid for kid in page.one("dl", "props").kids if not isinstance(kid, str)]
    for block in rows:
        if block.kids[0].text == label:
            return block.kids[1]
    raise AssertionError(f"no setting {label!r}: " + ", ".join(b.kids[0].text for b in rows))


def said_by(command: list[str]) -> str:
    out = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        main_mod.main(command)
    return out.getvalue()


# --- the server, asked as a browser asks ----------------------------------------------------

@contextlib.contextmanager
def running(project: Project, **kw):
    config = project.root / "echolot.yml"
    server = ui.make_server(project.root, str(config) if config.is_file() else "echolot.yml", **kw)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def ask(server, path: str, method: str = "GET", host: str | None = "", **headers: str):
    """One request, with the `Host` a browser would send unless another is named."""
    conn = http.client.HTTPConnection(ui.HOST, server.site.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host or server.site.host)
        for name, value in headers.items():
            conn.putheader(name.replace("_", "-"), value)
        conn.endheaders()
        reply = conn.getresponse()
        return reply.status, dict(reply.getheaders()), reply.read().decode("utf-8")
    finally:
        conn.close()


# --- what the server refuses ----------------------------------------------------------------

def test_it_listens_on_this_machine_only(project):
    project.ready()
    with running(project) as server:
        check("bound to 127.0.0.1", server.server_address[0] == "127.0.0.1", server.server_address)
        check("on a port of its own", server.site.port > 0 and server.site.address.startswith("http://127.0.0.1:"))
        status, _, body = ask(server, f"/{server.site.token}/")
        check("and the start page answers there", status == 200 and "Let's see how fast" in body, status)


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "localhost:{port}", "127.0.0.1",
                                  "127.0.0.1:1", "[::1]:{port}", None])
def test_a_request_under_another_name_is_refused(project, host):
    project.ready()
    with running(project) as server:
        named = host.format(port=server.site.port) if host else host
        status, _, body = ask(server, f"/{server.site.token}/", host=named)
        check("refused", status == 403, (status, body[:200]))
        check("with nothing to go on: no page, no token", "<" not in body and server.site.token not in body, body)


@pytest.mark.parametrize("path", ["/", "/report", "/wrong-token/", "/{token}x/", "/x{token}/", "/x/{token}/",
                                  "/%74oken/", "", "/{short}/", "/?{token}", "/?token={token}", "/#{token}"])
def test_a_request_without_the_token_is_refused(project, path):
    project.ready().report()
    with running(project) as server:
        token = server.site.token
        status, _, body = ask(server, path.format(token=token, short=token[:-1]) or "/")
        check("refused", status == 403, (status, body[:200]))
        check("and told nothing of the project", "example" not in body and token not in body, body)


def test_the_token_is_new_at_every_start_and_long(project):
    project.ready()
    tokens = {ui.make_server(project.root, "echolot.yml").site.token for _ in range(3)}
    check("three starts, three tokens", len(tokens) == 3, tokens)
    check("each too long to guess", all(len(t) >= 32 and re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in tokens), tokens)


def test_the_token_travels_in_the_address_and_in_no_cookie(project):
    project.ready().report()
    with running(project) as server:
        status, headers, body = ask(server, f"/{server.site.token}/")
        check("no cookie is set", "Set-Cookie" not in headers, headers)
        links = [a.attrs["href"] for a in read(body).all("a")]
        check("every link of the page carries it", links and all(h.startswith(f"/{server.site.token}/") for h in links), links)
        status, _, _ = ask(server, f"/{server.site.token}/report", Cookie=f"token={server.site.token}")
        check("and a page opens by its address", status == 200)
        status, _, _ = ask(server, "/report", Cookie=f"token={server.site.token}")
        check("never by a cookie", status == 403)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_what_is_not_a_read_needs_the_servers_own_origin_and_the_token(project, method):
    project.ready()
    with running(project) as server:
        site = server.site
        for name, path, headers, expected in (
                ("no origin", f"/{site.token}/", {}, 403),
                ("another origin", f"/{site.token}/", {"Origin": "https://evil.example"}, 403),
                ("the same host under another scheme", f"/{site.token}/", {"Origin": f"https://{site.host}"}, 403),
                ("the origin `null` of a sandboxed page", f"/{site.token}/", {"Origin": "null"}, 403),
                ("its own origin, sent from another site", f"/{site.token}/",
                 {"Origin": site.origin, "Sec_Fetch_Site": "cross-site"}, 403),
                ("its own origin and no token", "/", {"Origin": site.origin}, 403),
                ("its own origin and a wrong token", "/nope/", {"Origin": site.origin}, 403),
                # Nothing takes such a request yet: past the checks there is no route for it.
                ("its own origin and the token", f"/{site.token}/", {"Origin": site.origin}, 405),
                ("the same, to no page at all", f"/{site.token}/nowhere", {"Origin": site.origin}, 404)):
            status, got, body = ask(server, path, method=method, **headers)
            check(f"{method}, {name}: {expected}", status == expected, (status, body[:120]))
            if expected == 405:
                check("and it says what the address does take", got.get("Allow") == "GET", got)


def test_a_request_that_changes_something_is_one_entry_and_is_guarded(project, monkeypatch):
    """What the next step adds: a route with another method. It is reached
    through the same checks and no other way."""
    project.ready()
    done = []

    def record(visit, params):
        done.append(params)
        return ui.Page("<p>done</p>")
    monkeypatch.setattr(ui, "ROUTES", [*ui.ROUTES, ui.Route("record", "record", record, method="POST")])
    with running(project) as server:
        site = server.site
        for headers in ({}, {"Origin": "https://evil.example"}, {"Origin": site.origin, "Sec_Fetch_Site": "same-site"}):
            status, _, _ = ask(server, f"/{site.token}/record", method="POST", **headers)
            check("refused before it runs", status == 403 and not done, (status, done))
        status, _, _ = ask(server, "/record", method="POST", Origin=site.origin)
        check("refused without the token", status == 403 and not done)
        status, _, _ = ask(server, f"/{site.token}/record", method="POST", Origin=site.origin,
                           Sec_Fetch_Site="same-origin")
        check("and runs for the server's own page", status == 200 and done == [{}], (status, done))
        status, got, _ = ask(server, f"/{site.token}/record")
        check("a read of it is no request for it", status == 405 and got.get("Allow") == "POST" and len(done) == 1)
        check("and no page links to it", site.url("record") is None)


SECRET = "what-a-file-of-the-project-holds"


@pytest.mark.parametrize("path", [".echolot/out/report.json", "echolot.yml", "local.yml", "../echolot.yml",
                                  "report/../echolot.yml", "report/../../etc/passwd", "/etc/passwd",
                                  "%2e%2e/echolot.yml", "..%2fecholot.yml", "hunt/1/report/../../../echolot.yml",
                                  "hunt/1/report/001.json", "hunt/1/reports/001.json", "report.json",
                                  "app/build.gradle.kts", ".echolot/log/runs.jsonl", "report/", "hunt/x/report/1",
                                  "hunt/1/report/-1", "hunt/1/report/1/", "report/coldStart/extra"])
def test_no_file_is_served_by_its_path(project, path):
    project.ready().report().hunt(1, "why is it slow")
    (project.root / "local.yml").write_text(f"runner:\n  device: {SECRET}\n", encoding="utf-8")
    (project.root / "echolot.yml").write_text(LAUNCH + f"# {SECRET}\n", encoding="utf-8")
    with running(project) as server:
        status, headers, body = ask(server, f"/{server.site.token}/{path}")
        check("no such page", status == 404, (status, body[:200]))
        check("and it is a page of the server's, not a file", headers["Content-Type"].startswith("text/html")
              and "No such" in body and SECRET not in body and "package:" not in body, body[:300])


def test_the_pages_fetch_nothing_and_the_browser_is_told_so(project):
    project.ready().traces().report().hunt(1, "why is it slow", reports=2)
    with running(project) as server:
        site = server.site
        for name, path in (("start", ""), ("report", "report"), ("an archived report", "hunt/1/report/2"),
                           ("no page", "nowhere")):
            _, headers, body = ask(server, f"/{site.token}/{path}")
            own = f"/{site.token}/"
            strangers = [found for found in outside(body) if not re.fullmatch(rf"<a href='{re.escape(own)}[^']*'>", found)]
            check(f"{name}: nothing loads and nothing reaches out", not strangers, strangers)
            check(f"{name}: no address of another place anywhere in it", not SCHEME.search(body), SCHEME.findall(body)[:5])
            policy = headers["Content-Security-Policy"]
            check(f"{name}: the policy is sent, the page's own and no frame",
                  "default-src 'none'" in policy and "frame-ancestors 'none'" in policy and "http" not in policy, policy)
            check(f"{name}: and is in the page as well",
                  read(body).one("meta", http_equiv="Content-Security-Policy").attrs["content"] in policy)
            check(f"{name}: nothing is kept, sniffed or passed on",
                  headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
                  and headers["Referrer-Policy"] == "no-referrer" and headers["X-Frame-Options"] == "DENY", headers)
            check(f"{name}: the server does not say what it runs on",
                  "Python" not in headers.get("Server", "") and "BaseHTTP" not in headers.get("Server", ""), headers)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_server_writes_nothing(project, monkeypatch):
    project.ready().traces().report().hunt(1, "why is it slow", reports=2).hunt(2, "and now", "open", current=True)
    monkeypatch.delenv("ECHOLOT_NO_RECORD")
    before = snapshot(project.root)
    with running(project) as server:
        for path in ("", "report", "hunt/1/report/1", "hunt/9/report/1", "nowhere"):
            ask(server, f"/{server.site.token}/{path}")
            ask(server, f"/{server.site.token}/{path}", method="POST", Origin=server.site.origin)
    check("every file is as it was, and none is new", snapshot(project.root) == before,
          sorted(set(snapshot(project.root)) ^ set(before)))


def test_the_phone_is_asked_for_the_list_of_devices_and_nothing_else(project, adb):
    project.ready().traces().report()
    site = site_of(project)
    for path in ("", "report"):
        get(site, path)
    check("adb was asked", adb.asked)
    check("only for its list", all(args[:2] == ["adb", "devices"] and len(args) <= 3 for args in adb.asked), adb.asked)


def test_a_head_request_gets_the_headers_alone(project):
    project.ready()
    with running(project) as server:
        status, headers, body = ask(server, f"/{server.site.token}/", method="HEAD")
        check("answered, with no page", status == 200 and body == "" and int(headers["Content-Length"]) > 1000, headers)
        status, _, _ = ask(server, "/", method="HEAD")
        check("and refused like any other without the token", status == 403)


def test_a_page_that_cannot_be_drawn_says_so_and_the_server_goes_on(project, monkeypatch, capsys):
    project.ready()

    def broken(root):
        raise RuntimeError("the reading fell over <b>")
    with running(project, src=ready.Sources(scan=broken, pick_device=runner.pick_device)) as server:
        status, _, body = ask(server, f"/{server.site.token}/")
        check("the page says what went wrong", status == 500 and "could not be drawn" in body, (status, body[:200]))
        check("as text", "fell over &lt;b&gt;" in body and not read(body).one("p", "lead").all("b"))
        status, _, _ = ask(server, f"/{server.site.token}/nowhere")
        check("and the next request is answered", status == 404)
    err = capsys.readouterr().err
    check("the terminal hears of it once, without the address", "could not draw the page `start`" in err
          and server.site.token not in err, err)


def test_requests_are_not_logged_with_their_token(project, capfd):
    project.ready()
    with running(project) as server:
        ask(server, f"/{server.site.token}/")
        ask(server, "/nope/")
    out, err = capfd.readouterr()
    check("nothing is printed per request", server.site.token not in out + err and "GET" not in out + err, out + err)


# --- the routes -------------------------------------------------------------------------------

def test_a_page_is_one_entry_and_its_link_appears_with_it(project, monkeypatch):
    """The agent's answer and the comparison are not built: no link leads to
    them. Adding the page is adding its route, and the link is there."""
    project.ready().hunt(1, "why is it slow", verdict={"causes": []})
    page = start(project)
    check("no link to an answer while there is no such page",
          not [a for a in page.all("a") if "answer" in a.attrs["href"]], [a.attrs["href"] for a in page.all("a")])
    monkeypatch.setattr(ui, "ROUTES", [*ui.ROUTES, ui.Route(
        "answer", "hunt/{n}/answer", lambda visit, params: ui.Page(f"<p>answer {params['n']}</p>"),
        step="Agent's answer")])
    site = site_of(project)
    page = read(get(site).body)
    link = [a for a in page.all("a") if a.attrs["href"].endswith("/hunt/1/answer")]
    check("with the route, the investigation that has a verdict links to it", len(link) == 1 and link[0].text == "Agent's answer")
    check("and the page opens", get(site, "hunt/1/answer").body == "<p>answer 1</p>")


def test_every_link_of_every_page_leads_to_a_page(project):
    project.app().config(SEVERAL).agents().doctor().traces()
    project.report(".echolot/out/coldStart").report(".echolot/out/feedScroll")
    project.hunt(1, "why is it slow", reports=2).hunt(2, "and now", "open", reports=1, current=True)
    site = site_of(project)
    seen, queue = set(), [f"/{site.token}/"]
    while queue:
        href = queue.pop()
        if href in seen:
            continue
        seen.add(href)
        reply = ui.answer(site, "GET", href, {"Host": site.host})
        check(f"{href.replace(site.token, 'T')} opens", reply.status == 200, reply.status)
        queue += [a.attrs["href"] for a in read(reply.body).all("a") if not a.attrs["href"].startswith("#")]
    opened = {href.replace(f"/{site.token}/", "") for href in seen}
    check("the start, each scenario's report and every report an investigation filed",
          opened == {"", "report", "report/coldStart", "report/feedScroll", "hunt/1/report/1", "hunt/1/report/2",
                     "hunt/2/report/1"}, sorted(opened))


def test_the_steps_are_a_path_once_there_are_two(project):
    project.ready()
    check("one page is no path", not start(project).all("nav", "steps"))
    project.report()
    site = site_of(project)
    steps = read(get(site).body).one("nav", "steps").all("a")
    check("Start, Report", [a.text for a in steps] == ["Start", "Report"], [a.text for a in steps])
    check("this page is marked", [a.attrs.get("aria-current") for a in steps] == ["page", None])
    report = read(get(site, "report").body)
    steps = report.one("nav", "steps").all("a")
    check("on the report, the report", [a.attrs.get("aria-current") for a in steps] == [None, "page"])
    check("and the way back is the start page", steps[0].attrs["href"] == f"/{site.token}/")


# --- the start page: ready -------------------------------------------------------------------

def test_everything_ready(project):
    page = start(project.ready())
    said = lines(page)
    check("this computer", said["This computer"] == ("green", "ready: This computer is ready."), said)
    check("the phone, by the model adb gives", said["The phone"] == ("green", "ready: The phone is connected: Pixel_8."), said)
    check("the app, and which build", said["The app"] == ("green", f"ready: {APP} can be measured on the benchmark build."), said)
    check("the app is in the headline", page.one("h1").text == f"Let's see how fast {APP} is")
    again = [a for a in page.all("a", "btn") if a.text == "Check again"]
    check("Check again reads the page anew, and is not the screen's one step now",
          len(again) == 1 and again[0].attrs["href"].endswith("/the-token-of-a-test/")
          and "line" in again[0].attrs["class"], [a.attrs for a in again])


def test_a_level_is_never_colour_alone(project, adb):
    adb.listing = ""
    page = start(project.app().config().agents())
    words = [item.one("span", "vh").text for item in page.one("ul", "ready").all("li")]
    check("each sentence says its state to a reader who cannot see the icon",
          words == ["not checked:", "in the way:", "ready:"], words)
    check("and every icon is hidden from that reader", all(s.attrs.get("aria-hidden") == "true" for s in page.all("svg")))


def test_no_phone(project, adb):
    adb.listing = ""
    page = start(project.ready())
    tone, text = lines(page)["The phone"]
    check("red, in the words collect stops with", tone == "red" and text == (
        "in the way: The phone is not ready: adb sees no devices at all. Plug in a phone or start an emulator."), text)
    again = [a for a in page.all("a", "btn") if a.text == "Check again"][0]
    check("Check again is the one step of the screen", "primary" in again.attrs["class"])
    check("and the recording step says what it waits for",
          "Fix what is marked red first" in page.one("div", "callout").text)
    check("what scan says of the devices is under All settings",
          "none attached" in setting(page, "The phone").text, setting(page, "The phone").text)


def test_several_phones(project, adb):
    adb.listing = ONE + EMULATOR
    tone, text = lines(start(project.ready()))["The phone"]
    check("red, with both named and how to pick", tone == "red" and "several devices connected: EXAMPLE0001, emulator-5554" in text
          and "runner.device in local.yml" in text, text)


def test_several_phones_and_the_config_names_one(project, adb):
    adb.listing = ONE + EMULATOR
    project.ready()
    (project.root / "local.yml").write_text("runner:\n  device: emulator-5554\n", encoding="utf-8")
    tone, text = lines(start(project))["The phone"]
    check("the one collect would record on", (tone, text) == (
        "green", "ready: The phone is connected: sdk_gphone64, an emulator."), text)


def test_a_phone_that_has_not_allowed_debugging(project, adb):
    adb.listing = LOCKED
    tone, text = lines(start(project.ready()))["The phone"]
    check("red, and what to do on its screen", tone == "red" and "debugging not authorised" in text
          and "Allow USB debugging?" in text, text)


def test_no_adb_on_this_computer(project, adb):
    adb.listing = None
    page = start(project.ready())
    tone, text = lines(page)["The phone"]
    check("red, and where adb comes from", tone == "red" and "adb not found" in text and "platform-tools" in text, text)
    check("scan's own note is in the settings", "adb is not on PATH, so no device was asked"
          in setting(page, "What scan noted").text, setting(page, "What scan noted").text)


def test_an_app_that_cannot_be_measured(project):
    page = start(project.app(profileable=False).config().agents().doctor())
    tone, text = lines(page)["The app"]
    check("red: a recording would show nothing of it", tone == "red" and "cannot be measured yet" in text, text)
    check("with what to add and where", '<profileable android:shell="true" />' in text
          and "app/src/main/AndroidManifest.xml" in text, text)
    check("and scan's verdict behind it", "profileable: no" in setting(page, APP).text, setting(page, APP).text)


def test_an_app_with_debug_builds_only(project):
    tone, text = lines(start(project.app(gradle=DEBUG_ONLY).config().agents().doctor()))["The app"]
    check("red: a debug build is not what a user gets", tone == "red" and "only debuggable builds" in text, text)


def test_a_config_whose_code_is_elsewhere(project):
    page = start(project.config().agents().doctor().report())
    tone, text = lines(page)["The app"]
    check("not known is grey, never red: the reports still open", tone == "grey" and "not in this folder" in text, text)
    check("and the report is offered", [a.text for a in page.all("a", "btn")] == ["Check again", "See the report"])


def test_no_app_and_no_config(project):
    page = start(project.agents())
    tone, text = lines(page)["The app"]
    check("red, and where to start instead", tone == "red" and "Start echolot ui in the folder of the app's project" in text, text)
    check("the headline has no name to say", page.one("h1").text == "Let's see how fast your app is")


def test_a_repository_that_cannot_be_read_and_one_whose_builds_cannot(project):
    def unreadable(root):
        raise OSError("permission denied")
    project.config().agents().doctor()
    said = lines(start(project, ready.Sources(scan=unreadable, pick_device=lambda serial: "EXAMPLE0001")))
    check("the page is drawn without it: the phone by its serial", said["The phone"] == (
        "green", "ready: The phone is connected: EXAMPLE0001."), said)
    check("and the app is not known", said["The app"][0] == "grey", said)
    facts = scan_mod.Facts(root=str(project.root), app={
        "module": ":app", "dir": "app", "manifest": "app/src/main/AndroidManifest.xml", "build_script": None,
        "launcher": ".MainActivity", "application_id": APP, "namespace": APP, "process": None,
        "profileable": True, "application_class": None},
        devices=[{"serial": "EXAMPLE0001", "state": "device", "model": "Pixel_8", "device": "shiba", "emulator": False}])
    page = start(project, ready.Sources(scan=lambda root: facts, pick_device=lambda serial: "EXAMPLE0001"))
    check("an app whose variants cannot be read can be measured, and the page says what it does not know",
          lines(page)["The app"] == ("green", f"ready: {APP} can be measured. Which build to take could not be read "
                                     "from its build script."), lines(page)["The app"])
    check("the settings say the same", "names no variant echolot can read" in setting(page, "Build to measure").text)


def test_the_self_check_never_ran_failed_and_passed(project):
    project.app().config().agents()
    tone, text = lines(start(project))["This computer"]
    check("never: not known, and what checks it", tone == "grey" and "echolot doctor" in text, text)
    project.doctor(failed=("the launch window",))
    page = start(project)
    tone, text = lines(page)["This computer"]
    check("failed: red, no report is to be trusted", tone == "red" and "did not pass" in text, text)
    check("and the next step is the self-check", page.one("code", id="next-cmd").text == "echolot doctor")
    check("status's own line is behind it", "1 check(s) FAILED" in setting(page, "This computer").text)
    project.doctor()
    check("passed: green", lines(start(project))["This computer"][0] == "green")


# --- the start page: the next step ------------------------------------------------------------

def next_of(page) -> tuple[str, str | None]:
    section = next(s for s in page.all("section") if s.attrs.get("aria-labelledby") == "next-h")
    code = section.all("code", id="next-cmd")
    return section.all("p")[0].text if not section.all("div", "callout") else section.all("p", "measure")[0].text, (
        code[0].text if code else None)


def test_a_first_visit(project, adb):
    adb.listing = ""
    page = start(project.app())
    text, command = next_of(page)
    check("the step is init", command == "echolot init" and "not set up in this project yet" in text, (text, command))
    check("the settings say there is no config, and show what scan would start one from",
          "None yet" in setting(page, "Config").text and "window: {name: launch" in setting(page, "A config to start from").text)
    check("no report, said once", "No report yet" in page.text and not page.all("nav", "steps"))


def test_no_config_yet(project):
    page = start(project.app().agents().doctor())
    text, command = next_of(page)
    check("an agent writes it", "no echolot.yml" in text and command == "echolot guide setup", (text, command))
    main_mod.main(["init", "--into", str(project.root), "--for", "claude", "--no-input", "--no-doctor"])
    project.doctor()
    text, command = next_of(start(project))
    check("through the door this project chose", command == "/echolot", command)


def test_a_config_that_does_not_load(project):
    page = start(project.app().config(LAUNCH + "  end: {name: x}\n").agents().doctor())
    text, command = next_of(page)
    check("nothing to copy: the config is to be fixed", command is None and "does not load" in text, (text, command))
    check("what is wrong is said in the settings", "two ways to say where the window is" in setting(page, "Config").text)
    check("and status's line is behind the step", "fix echolot.yml" in setting(page, "The next step").text)


def test_nothing_recorded_yet_then_recorded(project):
    project.ready()
    text, command = next_of(start(project))
    check("record", command == "echolot collect -c echolot.yml -n 5" and "no recordings of coldStart yet" in text, (text, command))
    project.traces(n=2)
    text, command = next_of(start(project))
    check("then read them, each file named", command == (
        "echolot analyze .echolot/traces/coldStart_iter000.perfetto-trace "
        ".echolot/traces/coldStart_iter001.perfetto-trace -c echolot.yml") and "2 of them" in text, (text, command))


@pytest.mark.parametrize("build", ["first", "setup", "broken", "record", "analyze", "several", "open"])
def test_the_page_says_what_echolot_says(project, build):
    """Every line shown as the CLI's is a line `echolot` and `echolot scan`
    print here, and the command to copy is in the `next` line."""
    project.app()
    if build != "first":
        project.agents().doctor()
    if build in ("record", "analyze", "open"):
        project.config(ANCHORS)
    if build == "broken":
        project.config("project: [\n")
    if build == "several":
        project.config(SEVERAL).traces("feedScroll")
    if build in ("analyze", "open"):
        project.traces().report()
    if build == "open":
        project.hunt(1, "why is it slow", "open", current=True, touched_at="2026-11-11T09:30:00+00:00")
    page = start(project)
    status = said_by(["status"] if (project.root / "echolot.yml").is_file() else [])
    scan = said_by(["scan"]).replace("**", "")
    behind = [(p.one("span", "who").text, p.text.split(" ", 1 if " " not in p.one("span", "who").text else 2)[-1])
              for p in page.all("p", "says") if p.all("span", "who")]
    check("there are such lines", len(behind) >= 4, behind)
    for who, words in behind:
        source = status if who == "echolot" else scan
        key, _, value = words.partition(": ")
        flat = re.sub(r"\s+", " ", source).replace("`", "")
        check(f"`{who}` says it: {words[:60]}", value in flat or words in flat, source[-600:])
    _, command = next_of(page)
    if command and command.startswith("echolot "):
        check("the command is the one `next` names", command in status.rsplit("\nnext", 1)[-1], status[-400:])


def test_an_open_investigation_is_the_agents_to_resume(project):
    project.ready().traces().report()
    project.hunt(1, "why <i>is</i> it slow", "open", current=True, touched_at="2026-11-11T09:30:00+00:00")
    page = start(project)
    text, command = next_of(page)
    check("the question, as text", 'An investigation is open: "why <i>is</i> it slow"' in text
          and not page.one("div", "start-body").all("i"), text)
    check("and the agent's way in", command == "echolot guide", command)


def test_a_layer_edited_here_does_not_stop_the_step(project):
    main_mod.main(["init", "--into", str(project.root), "--for", "claude", "--no-input", "--no-doctor"])
    project.app().config().doctor()
    skill = project.root / ".claude" / "skills" / "echolot" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nours\n", encoding="utf-8")
    # With no record of what was installed, a file that differs may be an edit made here.
    (project.root / ".claude" / "echolot-layer.json").unlink()
    page = start(project)
    text, command = next_of(page)
    check("the step is still the recording", command == "echolot collect -c echolot.yml -n 5", (text, command))
    check("and what only a person can settle is said beside it", "may carry edits made here" in page.one("div", "callout").text
          and "echolot init --all" in page.one("div", "callout").text, page.one("div", "callout").text)


def test_the_command_copies_as_it_is_written(project):
    project.app().config(SEVERAL).agents().doctor()
    page = start(project)
    code = page.one("code", id="next-cmd")
    check("the text is the command, to the letter", code.raw() == "echolot collect -c echolot.yml -n 5 --scenario coldStart", code.raw())
    check("each word stays whole where it fits", [w.text for w in code.all("span", "w")] == code.raw().split(" "))
    button = page.one("button", data_copy="next-cmd")
    check("the button is script's, and hidden where script is off", "needs-js" in button.attrs["class"])


# --- the start page: all settings -------------------------------------------------------------

def test_all_settings_of_a_launch_window(project):
    page = start(project.ready())
    check("it is a fold, closed", page.one("details", id="settings").attrs.get("open") is None)
    check("nothing in it can be changed", not page.all("input") and not page.all("select") and not page.all("textarea")
          and not page.all("form"))
    check("the build, and whether to measure on it, in scan's words",
          "benchmark. Measure on it: yes — release-like and profileable. It installs as com.example.app."
          in setting(page, "Build to measure").text, setting(page, "Build to measure").text)
    check("the others are named too", "debug: no — debuggable" in setting(page, "Build to measure").text)
    check("how it runs", "runner.mode: launch" in setting(page, "How it runs on the phone").text)
    check("the window is the launch", "window: launch" in setting(page, "What is measured").text
          and "reportFullyDrawn" in setting(page, "What is measured").text)
    check("runs and their length, with where each number is from",
          setting(page, "Runs").text == "5, the default." and setting(page, "Each run records for").text == "12 s, the default.")
    check("the limit, as a person says it and as the config writes it",
          setting(page, "Time limit").text.startswith("0.5 s (budget_ms: 500)"), setting(page, "Time limit").text)
    check("the thresholds", "All 16 checks run on their built-in thresholds." in setting(page, "Thresholds").text)
    check("and the versions", re.match(r"echolot \S+ · trace_processor \S+$", setting(page, "Toolchain").text),
          setting(page, "Toolchain").text)


def test_all_settings_of_anchors_a_command_and_tuned_thresholds(project):
    page = start(project.app().config(ANCHORS).agents().doctor())
    check("where the window opens", setting(page, "The window opens at").text == "bindApplication.")
    check("where it closes, and that a person confirmed it",
          setting(page, "The window closes at").text == "reportFullyDrawn() for MainActivity. Confirmed by you in the config.")
    check("the command that runs it", "adb shell am start -W -n com.example.app/.MainActivity"
          in setting(page, "How it runs on the phone").text)
    check("the runs the config asks for", setting(page, "Runs").text.startswith("3, from runner.iterations."))
    check("and for how long", setting(page, "Each run records for").text == "9 s, from runner.duration_ms.")
    check("no limit, and how one is set", "None is set" in setting(page, "Time limit").text
          and "budget_ms" in setting(page, "Time limit").text)
    thresholds = setting(page, "Thresholds")
    check("how many are the config's", "1 of 15 checks run on thresholds set in the config, the other 14 on the built-in ones. "
          "1 more is switched off in the config." in thresholds.text, thresholds.text)
    each = {li.one("code").text: li.text for li in thresholds.one("ul", "each").all("li")}
    check("each threshold with where it comes from", "min_slice_ms 40, built-in is 16 set in the config" in each["main_thread_block"]
          and each["io_wait"].endswith("min_io_wait_ms 20 built-in") and each["frame_jank"].endswith("switched off in the config"),
          (each["main_thread_block"], each["io_wait"], each["frame_jank"]))
    check("a pattern is no threshold", "glob" not in thresholds.text)
    check("status's line is behind it", "thresholds from the config" in thresholds.text)


def test_a_project_that_runs_a_macrobenchmark(project):
    bench = project.root / "benchmark" / "src" / "main" / "java" / "com" / "example" / "benchmark"
    bench.mkdir(parents=True)
    (bench / "StartupBenchmark.kt").write_text(
        "package com.example.benchmark\nimport androidx.benchmark.macro.junit4.MacrobenchmarkRule\n"
        "class StartupBenchmark {\n    @get:Rule val rule = MacrobenchmarkRule()\n    @Test\n    fun startup() = Unit\n}\n",
        encoding="utf-8")
    page = start(project.app().config(BENCHMARK).agents().doctor())
    text, command = next_of(page)
    check("the step runs the benchmark", "runs its macrobenchmark on the phone" in text
          and command == "echolot collect -c echolot.yml -n 5", (text, command))
    check("how it runs is the gradle task", "./gradlew :benchmark:connectedBenchmarkAndroidTest"
          in setting(page, "How it runs on the phone").text, setting(page, "How it runs on the phone").text)
    check("the benchmark decides the runs", "The macrobenchmark decides" in setting(page, "Runs").text)
    check("and no length of a run is claimed", "Each run records for" not in page.one("dl", "props").text)
    check("the crumb names its scenario", "startup" in page.one("span", "crumb").text)


def test_a_project_with_several_scenarios(project):
    project.app().config(SEVERAL).agents().doctor().traces("feedScroll")
    project.report(".echolot/out/feedScroll")
    site = site_of(project)
    page = read(get(site).body)
    check("the page is about the first, and says so", "names 2 scenarios. This page is about coldStart"
          in setting(page, "Config").text and "feedScroll" in setting(page, "Config").text, setting(page, "Config").text)
    check("status's line of each is behind it", "scenario: feedScroll · own thresholds · 3 trace(s)" in setting(page, "Config").text)
    _, command = next_of(page)
    check("the step names its scenario", command.endswith("--scenario coldStart"), command)
    reports = page.one("ul", "reps").all("li")
    check("each scenario's last report, or that it has none",
          "coldStart no report yet" in reports[0].text and reports[1].one("a").attrs["href"].endswith("/report/feedScroll"),
          [r.text for r in reports])
    check("the first scenario has no report, so Report is no step", not page.all("nav", "steps"))
    check("the other one's opens", get(site, "report/feedScroll").status == 200)
    check("a name the config does not have is no page", get(site, "report/nope").status == 404
          and get(site, "report/coldStart").status == 404)
    each = {li.one("code").text: li.text for li in setting(page, "Thresholds").one("ul", "each").all("li")}
    check("the thresholds are the first scenario's", each["frame_jank"].endswith("built-in"), each["frame_jank"])
    project.hunt(1, "the feed stutters", "open", current=True, scenario="feedScroll")
    page = read(get(site_of(project)).body)
    each = {li.one("code").text: li.text for li in setting(page, "Thresholds").one("ul", "each").all("li")}
    check("with an investigation open, the page is about its scenario", "This page is about feedScroll" in setting(page, "Config").text
          and "min_frames 2, built-in is 3" in each["frame_jank"] and each["frame_jank"].endswith("set for this scenario"),
          each["frame_jank"])


# --- the reports ------------------------------------------------------------------------------

def test_no_report_yet(project):
    site = site_of(project.ready().traces())
    page = read(get(site).body)
    check("said once, with nothing to open", "No report yet" in page.one("ul", "reps").text
          and [a.text for a in page.all("a", "btn")] == ["Check again"])
    reply = get(site, "report")
    check("its address is a page that says so", reply.status == 404 and "No report yet" in reply.body)
    check("with the way back", read(reply.body).one("a", "btn").attrs["href"] == f"/{site.token}/")


def test_the_report_is_drawn_from_the_file_when_it_is_asked_for(project):
    project.ready().traces().report()
    (project.root / ".echolot" / "out" / "report.html").write_text("<p>an old page on disk</p>", encoding="utf-8")
    site = site_of(project)
    page = read(get(site).body)
    card = page.one("ul", "reps").one("li")
    check("the start page gives its first line and its level",
          card.one("span", "st").text == "Needs attention" and "The launch takes 1.9 s." in card.text, card.text)
    check("and what status says of it", ".echolot/out/report.json" in card.one("p", "says").text
          and "run(s)" in card.one("p", "says").text)
    body = get(site, "report").body
    check("the page is the report's, not the file beside it", "an old page on disk" not in body
          and "What echolot checked" in body and read(body).one("h1").text == "The launch takes 1.9 s")
    check("the request for an agent names the report as the project does", ".echolot/out/report.json" in body
          and "the last report" in body)
    data = known()
    data["window"]["duration_ms"] = 400.0
    del data["verdict"]
    project.report(data=data)
    check("a new report is a new page at once", read(get(site, "report").body).one("h1").text == "The launch takes 0.4 s")


def test_a_report_from_before_the_plain_keys(project):
    old = known()
    del old["verdict"]
    for det in old["detectors"]:
        del det["plain"], det["attention"]
        det.pop("checked", None)
        det.pop("reason", None)
    project.ready().traces().report(data=old)
    site = site_of(project)
    card = read(get(site).body).one("ul", "reps").one("li")
    check("it is read as `report --plain` reads it", card.one("span", "st").text == "Needs attention"
          and "The launch takes 1.9 s. You allowed 0.5 s" in card.text, card.text)
    page = read(get(site, "report").body)
    check("and its page has a level for every check", len(page.all("div", "tr")) == 6
          and all(r.all("span", "st") for r in page.all("div", "tr")))
    check("the file is not touched", json.loads((project.root / ".echolot/out/report.json").read_text()) == old)


@pytest.mark.parametrize("text", ["", "{ cut short", '{"kind": "comparison"}', '{"schema": 1}'])
def test_a_file_that_is_no_report(project, text):
    project.ready().traces()
    (project.root / ".echolot" / "out").mkdir(parents=True)
    (project.root / ".echolot" / "out" / "report.json").write_text(text, encoding="utf-8")
    site = site_of(project)
    page = read(get(site).body)
    check("the start page offers nothing to open", [a.text for a in page.all("a", "btn")] == ["Check again"])
    reply = get(site, "report")
    check("and its address says why", reply.status == 404 and ("does not read" in reply.body or "No report yet" in reply.body))


def test_the_investigations_and_what_each_filed(project):
    project.ready().traces().report()
    project.hunt(1, "why is it <slow>", reports=3, conclusion="the lock is held over a read", closed_at="2026-11-11T11:00:00+00:00")
    project.hunt(2, "the list stutters", "abandoned", reports=0)
    project.hunt(3, "over the limit", "open", reports=2, current=True)
    (project.root / ".echolot/hunts/3/reports/002.json").write_text("{ cut short", encoding="utf-8")
    site = site_of(project)
    page = read(get(site).body)
    items = page.one("ul", "invs").all("li")
    check("newest first, as hunt --list has them", [i.one("b").text for i in items] == ["#3", "#2", "#1"])
    check("the open one is said to be open", "open now" in items[0].one("p", "inv-h").text)
    check("what it did and what it came to", "3 collect(s) · 3 analyze(s) · open for 2h · 3 report(s) kept" in items[2].text
          and "the lock is held over a read" in items[2].text, items[2].text)
    check("a question is text", "why is it <slow>" in items[2].text and not page.all("slow"))
    listed = said_by(["hunt", "--list"])
    check("every question and conclusion is one hunt --list prints",
          all(q in listed for q in ("why is it <slow>", "the list stutters", "over the limit", "the lock is held over a read")))
    check("each report it filed opens", [a.attrs["href"].split("/hunt/")[1] for a in items[2].all("a")]
          == ["1/report/1", "1/report/2", "1/report/3"])
    check("one that does not read is said, not linked", [a.text for a in items[0].all("a")] == ["Report 1"]
          and "Report 2 is there and does not read." in items[0].text, items[0].text)
    check("an investigation with none has no link", not items[1].all("a"))
    archived = read(get(site, "hunt/1/report/2").body)
    check("an archived report is a report page", archived.one("h1").text == "The launch takes 1.9 s")
    check("that says which one it is", "investigation 1, report 2" in archived.one("span", "crumb").text)
    check("its request names the file, and not as the last report",
          ".echolot/hunts/1/reports/002.json" in archived.one("dialog", "ask").attrs["data-tail"]
          and "the last report" not in archived.one("dialog", "ask").attrs["data-one"])
    check("neither step is this page", [a.attrs.get("aria-current") for a in archived.one("nav", "steps").all("a")] == [None, None])
    for path in ("hunt/3/report/2", "hunt/2/report/1", "hunt/9/report/1", "hunt/1/report/4", "hunt/0/report/1"):
        check(f"{path} is no page", get(site, path).status == 404)


# --- the pages themselves ---------------------------------------------------------------------

def test_a_name_from_the_project_cannot_become_markup(project):
    nasty = '"><script src=x></script><img onerror=alert(1)>`</code>'
    project.app().agents().doctor().traces().report()
    project.config(ANCHORS.replace("adb shell am start -W -n com.example.app/.MainActivity",
                                   "echo '<script>alert(1)</script>' `x`"))
    project.hunt(1, nasty, conclusion=nasty)
    body = get(site_of(project)).body
    page = read(body)
    check("one script and one style, the page's own", len(page.all("script")) == 2 and len(page.all("style")) == 1
          and not page.all("img"))
    check("the names are there as text", nasty in page.one("ul", "invs").text
          and "<script>alert(1)</script>" in setting(page, "How it runs on the phone").text)


def test_ids_are_unique_and_the_page_is_light_dark_and_a_phones(project):
    project.ready().traces().report().hunt(1, "why", reports=2)
    page = start(project)
    ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs]
    check("no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
    check("the viewport is the device's", page.one("meta", name="viewport").attrs["content"].startswith("width=device-width"))
    check("both schemes are announced", page.one("meta", name="color-scheme").attrs["content"] == "light dark")
    check("the language is said", page.one("html").attrs["lang"] == "en")
    check("one headline", len(page.all("h1")) == 1)


def test_the_start_pages_own_styles_keep_to_the_tokens_and_to_themselves():
    css = kit.asset("start.css")
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    check("no colour of its own", not literal.search(css), literal.findall(css))
    check("nothing is fetched by it", not any(word in css for word in ("url(", "@import", "@font-face")))
    mine = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))
    shared = "".join(kit.asset(n) for n in ("base.css", "table.css", "picks.css", "report.css"))
    theirs = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", shared, flags=re.S)))
    # A class the start page styles and a shared sheet styles too is one it
    # means to build on; any other meeting of the two is an accident.
    meant = {"page", "ico", "rowf"} & mine
    check("no class of it is another sheet's by accident", not (mine & theirs) - meant, sorted((mine & theirs) - meant))
    check("the tokens are the shared ones", not _blocks(css, ":root {"))


def test_the_pieces_are_written_into_the_page_in_order(project):
    body = get(site_of(project.ready())).body
    at = [body.index(kit.asset(n)) for n in start_page.CSS + start_page.JS]
    check("each in whole, in the order named", at == sorted(at))
    check("and they are inside the package", all((PAGES / n).is_file() for n in start_page.CSS + start_page.JS))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the script")
def test_the_script_parses():
    done = subprocess.run(["node", "--check", str(PAGES / "start.js")], capture_output=True, text=True)
    check("start.js is JavaScript", done.returncode == 0, done.stderr)


def test_what_a_reader_gets_with_script_switched_off(project):
    page = start(project.ready().traces().report())
    code = page.one("code", id="next-cmd")
    check("the command is there to select", code.text.startswith("echolot analyze") and code.attrs.get("tabindex") == "0")
    needs = [n for n in page.walk() if "needs-js" in (n.attrs.get("class") or "").split()]
    check("only the Copy button needs script", [n.tag for n in needs] == ["button"])
    check("Check again and See the report are links", {a.text for a in page.all("a", "btn")} == {"Check again", "See the report"})
    check("and All settings is a fold of the browser's own", page.one("details", id="settings").all("summary")[0].text == "All settings")


# --- the command ------------------------------------------------------------------------------

def serve(project: Project, monkeypatch, capsys, *, opener=None, port=None, open_browser=True, stop=None):
    """`ui.serve` up to the moment it would wait: Ctrl-C comes at once."""
    seen = {}

    def forever(self, poll_interval=0.5):
        seen["site"] = self.site
        if stop:
            stop(self)
        raise KeyboardInterrupt
    monkeypatch.setattr(ui.Server, "serve_forever", forever)
    code = ui.serve(project.root, "echolot.yml", port=port, open_browser=open_browser,
                    opener=opener or (lambda url: True))
    out, err = capsys.readouterr()
    return code, out, err, seen.get("site")


def test_it_opens_the_browser_on_the_address_and_leaves_cleanly_on_ctrl_c(project, monkeypatch, capsys):
    project.ready()
    opened = []
    code, out, err, site = serve(project, monkeypatch, capsys, opener=lambda url: opened.append(url) or True)
    check("exit 0 after Ctrl-C", code == 0, (code, err))
    check("the address is what it prints, alone on stdout", out == site.address + "\n", out)
    check("and what the browser is opened with, token included", opened == [site.address]
          and re.fullmatch(rf"http://127\.0\.0\.1:{site.port}/[A-Za-z0-9_-]{{32,}}/", site.address), opened)
    check("it says how to stop, and that it stopped", "Ctrl-C stops it" in err and "echolot ui stopped." in err, err)
    with socket.socket() as probe:
        probe.bind((ui.HOST, site.port))
    check("the port is free again", True)


def test_no_open_prints_the_address_and_leaves_the_browser_alone(project, monkeypatch, capsys):
    project.ready()
    opened = []
    code, out, err, site = serve(project, monkeypatch, capsys, opener=opened.append, open_browser=False)
    check("not opened", code == 0 and opened == [])
    check("the address is printed", out.strip() == site.address and "No browser" not in err, (out, err))


@pytest.mark.parametrize("opener", ["says no", "raises"])
def test_a_browser_that_cannot_be_opened(project, monkeypatch, capsys, opener):
    project.ready()

    def refuse(url):
        if opener == "raises":
            raise webbrowser.Error("no runnable browser")
        return False
    code, out, err, site = serve(project, monkeypatch, capsys, opener=refuse)
    check("the server runs all the same", code == 0 and site is not None)
    check("the address is on stdout", out.strip() == site.address, out)
    check("and the terminal says to open it by hand", "No browser could be opened from here: open the address above yourself." in err, err)


def test_a_port_can_be_named(project, monkeypatch, capsys):
    project.ready()
    with socket.socket() as probe:
        probe.bind((ui.HOST, 0))
        free = probe.getsockname()[1]
    code, out, _, site = serve(project, monkeypatch, capsys, port=free)
    check("it listens there", code == 0 and site.port == free and f":{free}/" in out, (site.port, free))


def test_a_port_that_is_taken(project, monkeypatch, capsys):
    project.ready()
    with running(project) as first:
        taken = first.site.port
        code, out, err, site = serve(project, monkeypatch, capsys, port=taken)
        check("refused with exit 2, before anything is printed or opened", code == 2 and out == "" and site is None, (code, out))
        check("and says what to do", f"port {taken} on 127.0.0.1 is taken" in err and "Leave --port out" in err, err)
        status, _, _ = ask(first, f"/{first.site.token}/")
        check("the first server is untouched", status == 200)


def test_two_servers_at_once(project, tmp_path_factory):
    project.ready().report()
    other = Project(tmp_path_factory.mktemp("other")).app().config(BENCHMARK).agents()
    with running(project) as one, running(other) as two:
        check("each on its own port, with its own token", one.site.port != two.site.port and one.site.token != two.site.token)
        a = ask(one, f"/{one.site.token}/report")
        b = ask(two, f"/{two.site.token}/")
        check("each shows its own project", a[0] == 200 and "What echolot checked" in a[2] and b[0] == 200
              and "startup" in read(b[2]).one("span", "crumb").text)
        check("neither takes the other's token", ask(one, f"/{two.site.token}/")[0] == 403
              and ask(two, f"/{one.site.token}/")[0] == 403)
        check("nor a request named for the other", ask(one, f"/{one.site.token}/", host=two.site.host)[0] == 403)


def test_the_command_line(project, monkeypatch, capsys):
    project.ready()
    seen = {}

    def serve_double(root, config, **kw):
        seen.update(root=root, config=config, **kw)
        return 0
    monkeypatch.setattr(ui, "serve", serve_double)
    check("echolot ui", main_mod.main(["ui"]) == 0 and seen["root"] == project.root and seen["port"] is None
          and seen["open_browser"] is True, seen)
    check("the config is the project's", Path(seen["config"]) == project.root / "echolot.yml")
    main_mod.main(["ui", "--port", "8123", "--no-open"])
    check("--port and --no-open", seen["port"] == 8123 and seen["open_browser"] is False, seen)
    capsys.readouterr()
    for bad in ("0", "70000", "http", "-1"):
        with pytest.raises(SystemExit) as stopped:
            main_mod.main(["ui", "--port", bad])
        check(f"--port {bad} is no port", stopped.value.code == 2 and "port" in capsys.readouterr().err)
    with pytest.raises(SystemExit):
        main_mod.main(["ui", "--help"])
    said = capsys.readouterr().out
    check("the help says what it is and what it will not do", "127.0.0.1" in said and "--no-open" in said
          and "changes nothing" in said.replace("\n", " "), said)


def test_the_command_through_main_runs_until_ctrl_c(project, monkeypatch, capsys):
    project.ready().report()
    asked = {}

    def forever(self, poll_interval=0.5):
        asked["reply"] = ui.answer(self.site, "GET", f"/{self.site.token}/report", {"Host": self.site.host})
        raise KeyboardInterrupt
    monkeypatch.setattr(ui.Server, "serve_forever", forever)
    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    code = main_mod.main(["ui", "--no-open"])
    out = capsys.readouterr().out
    check("exit 0, the address printed", code == 0 and re.fullmatch(r"http://127\.0\.0\.1:\d+/[\w-]+/\n", out), out)
    check("and while it ran, it served this project's report", asked["reply"].status == 200
          and "What echolot checked" in asked["reply"].body)


@pytest.mark.skipif(os.name != "posix", reason="Ctrl-C is sent as SIGINT")
def test_ctrl_c_ends_the_real_process_with_exit_0(project):
    """The whole command as a process: it prints its address, answers, and
    leaves on Ctrl-C with exit 0 while a connection is still held open."""
    project.app().config().agents().doctor()
    proc = subprocess.Popen([sys.executable, "-m", "echolot", "ui", "--no-open"], cwd=project.root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "ECHOLOT_NO_RECORD": "1", "PYTHONPATH": str(ROOT)})
    try:
        address = proc.stdout.readline().strip()
        check("the first line of stdout is the address", re.fullmatch(r"http://127\.0\.0\.1:\d+/[\w-]{32,}/", address), address)
        port = int(address.split(":")[2].split("/")[0])
        conn = http.client.HTTPConnection(ui.HOST, port, timeout=10)
        conn.request("GET", "/")
        check("it answers, and refuses a request without the token", conn.getresponse().status == 403)
        conn.close()
        idle = socket.create_connection((ui.HOST, port))    # a browser's spare connection, saying nothing
        proc.send_signal(signal.SIGINT)
        code = proc.wait(timeout=30)
        idle.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    err = proc.stderr.read()
    check("exit 0", code == 0, (code, err[-300:]))
    check("it said that it stopped, and no traceback", "echolot ui stopped." in err and "Traceback" not in err, err[-300:])
    check("and wrote nothing", not (project.root / ".echolot" / "log" / "collect.json").exists()
          and sorted(p.name for p in (project.root / ".echolot").iterdir()) == ["hosts.json", "log"])


def test_it_is_among_the_commands_a_person_types():
    said = main_mod.build_parser().format_help()
    yours = said.split("Yours:")[1].split("The pipeline:")[0]
    check("listed under Yours, after status", re.search(r"status\s.*\n\s+ui\s+a page in the browser", yours), yours)
    check("and it has its place in the order", "ui" in main_mod.ORDER)
