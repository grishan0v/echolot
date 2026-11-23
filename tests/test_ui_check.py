#!/usr/bin/env python3
"""The button in `echolot ui`: Check the app, a run in progress, Stop, Check again.

What is pinned here:

  - what a press runs: `collect`, then `analyze --html`, then `compare` once a
    report was there before, each as a person would type it, with the scenario
    and the number of launches as arguments and nothing written to the config;
  - every state of the start page: before a run, each reason the button is
    disabled, a run at each step, stopped, failed at each step, interrupted,
    done, done with a comparison, several scenarios, recordings that wait;
  - that a run is read from files: `collect`'s own progress file says when
    each launch ends, and the page of a server that was never told shows it;
  - one run at a time, and what a second press is told;
  - what the server refuses: a press without the token, from another origin,
    under another name, with a body over the cap.

Nothing here records, needs a phone or the network, or waits on a timer. adb is
the double of `tests/test_ui.py`; the commands a press starts are doubles that
do each command's work at once (`Echolot`), run by the real job in this
process; `compare`, which only reads two reports, is the real one.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import check as check_mod  # noqa: E402
from echolot import job, recorder, state, ui  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot.pages import kit  # noqa: E402
from tests import answer_fixture as fx  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import known, read  # noqa: E402
from tests.test_ui import (  # noqa: E402
    ANCHORS, APP, BENCHMARK, DEBUG_ONLY, EMULATOR, LAUNCH, LOCKED, ONE, SEVERAL, Adb, Project, get, running, setting,
    site_of,
)

SLOW = LAUNCH + "\nrunner:\n  mode: launch\n  iterations: 5\n  duration_ms: 20000\n"
COOLING = LAUNCH + "\nrunner:\n  cooldown: {max_c: 38}\n"


# The project, the phone and the quiet run log of `tests/test_ui.py`.

@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


# --- the commands a press starts, as doubles ---------------------------------------------------

class Ran:
    """A command that did its work the moment it was started."""

    def __init__(self, code: int, pid: int, waits: int = 0, on_wait=None):
        self.returncode, self.pid, self.code, self.waits, self.on_wait = None, pid, code, waits, on_wait
        self.signals: list[int] = []

    def wait(self, timeout=None):
        import subprocess
        if self.waits > 0 and not self.signals:
            self.waits -= 1
            if self.on_wait:
                self.on_wait()
            raise subprocess.TimeoutExpired("echolot", timeout)
        self.returncode = -2 if self.signals else self.code
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)

    terminate = kill = lambda self: None


class Echolot:
    """`python -m echolot <command>` as the job starts it. `collect` writes
    the recordings and its progress file the way the real one does, `analyze`
    writes a report, and `compare` is the real command, run here.

    `fail` names a command that ends badly with `says`; `fail_at` is the
    launch `collect` stops at; `stop_at` is the launch after which `collect`
    is still running when the job looks, and `pressed` is called then.
    """

    def __init__(self, root: Path, *, fail: str | None = None, says: str = "", fail_at: int = 0,
                 stop_at: int | None = None, pressed=None, window: float | None = None, mode: str = "launch"):
        self.root, self.fail, self.says, self.fail_at = root, fail, says, fail_at
        self.stop_at, self.pressed, self.window, self.mode = stop_at, pressed, window, mode
        self.ran: list[list[str]] = []

    def __call__(self, argv, **kw):
        check("the same echolot that serves the page, by its module", argv[:3] == [sys.executable, "-m", "echolot"], argv)
        check("in the project's folder", Path(kw["cwd"]) == self.root, kw["cwd"])
        command, args = argv[3], [str(a) for a in argv[4:]]
        self.ran.append([command, *args])
        pid = 5000 + len(self.ran)
        return getattr(self, command)(args, kw["stdout"], pid)

    @staticmethod
    def _named(args: list[str], flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args else None

    def collect(self, args, out, pid) -> Ran:
        scenario = self._named(args, "--scenario") or "coldStart"
        n = int(self._named(args, "-n") or 5)
        traces = self.root / ".echolot" / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        progress = self.root / ".echolot" / "log" / "collect.json"
        said = {"scenario": scenario, "mode": self.mode, "started": time.time(), "pid": pid,
                "iterations": None if self.mode == "gradle" else n, "done": 0}

        def tell(**fields) -> None:
            said.update(fields)
            progress.write_text(json.dumps(said), encoding="utf-8")
        tell()
        for i in range(n):
            if self.fail == "collect" and i == self.fail_at:
                out.write(f"device EXAMPLE0001\ncollection error: {self.says}\n".encode())
                tell(finished=time.time(), exit=2, error=self.says)
                return Ran(2, pid)
            if self.stop_at == i:
                # Still recording this launch when the job looks: Stop finds it so.
                return Ran(0, pid, waits=1, on_wait=self.pressed)
            if i == 0:
                for old in traces.glob(f"{scenario}_iter*.perfetto-trace"):
                    old.unlink()
            (traces / f"{scenario}_iter{i:03d}.perfetto-trace").write_bytes(b"x")
            out.write(f"  [{i + 1}/{n}] {scenario}_iter{i:03d}.perfetto-trace\n".encode())
            if self.mode != "gradle":
                tell(done=i + 1)
        tell(finished=time.time(), exit=0, traces=n)
        return Ran(0, pid)

    def analyze(self, args, out, pid) -> Ran:
        if self.fail == "analyze":
            out.write(f"error: {self.says}\n".encode())
            return Ran(2, pid)
        scenario = self._named(args, "--scenario")
        folder = self.root / ".echolot" / "out" / (scenario or "")
        folder.mkdir(parents=True, exist_ok=True)
        data = known()
        if scenario:
            data["scenario"] = scenario
        if self.window:
            data["window"]["duration_ms"] = self.window
            del data["verdict"]
        (folder / "report.json").write_text(json.dumps(data), encoding="utf-8")
        out.write(f"→ {folder / 'report.json'}\n".encode())
        return Ran(0, pid)

    def compare(self, args, out, pid) -> Ran:
        if self.fail == "compare":
            out.write(f"error: {self.says}\n".encode())
            return Ran(2, pid)
        said = io.StringIO()
        with recorder.isolated(), contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = main_mod.main(["compare", *args])
        out.write(said.getvalue().encode())
        return Ran(code, pid)


def serving(project: Project, **how):
    """The site, with the commands of a press as doubles: a press runs them
    to their end before it is answered."""
    site = site_of(project)
    echolot = Echolot(project.root, **how)
    site.spawn = lambda root, name, plan: job.run(root, name, plan, popen=echolot, signals=False)
    return site, echolot


def press(site: ui.Site, path: str = "check", fields: dict | None = None, **headers: str) -> ui.Reply:
    """A form of the server's own page, sent as a browser sends it."""
    sent = {"Host": site.host, "Origin": site.origin, "Sec-Fetch-Site": "same-origin", "Content-Type": ui.FORM, **headers}
    return ui.answer(site, "POST", f"/{site.token}/{path}", sent, urlencode(fields or {}).encode())


def page_of(site: ui.Site):
    reply = get(site)
    check("the start page is drawn", reply.status == 200, reply.body[:300])
    return read(reply.body)


def rows(page) -> list[tuple[str, str, str]]:
    """The list of a run: each row's state, its name and what is said after it."""
    return [(li.attrs["class"], li.one("b").text, li.all("span")[-1].text) for li in page.one("ol", "run").all("li")]


def button(page):
    return page.one("button", id="check-go")


def hint(page) -> str:
    return page.one("p", id="check-hint").text


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- a run written by hand: the page is drawn from files, whoever wrote them ----------------------

def a_run(project: Project, at: str = "collect", *, done: int = 0, launches: int | None = 3, do: str = "record",
          mode: str = "launch", scenario: str = "coldStart", compare: bool = False, pid: int | None = None,
          own: bool = True, end: str | None = None, cooling: str | None = None, ago: float = 30.0) -> dict:
    """The files of a check that got as far as `at`, as the job and `collect`
    leave them: still running under this process's pid unless `end` says how
    it ended or `pid` names a process that is gone."""
    root, now = project.root, time.time()
    names = (["collect"] if do == "record" else []) + ["analyze"] + (["compare"] if compare else [])
    steps = []
    for name in names:
        before = names.index(name) < names.index(at)
        status = "done" if before else "waiting"
        if name == at:
            status = {"failed": "failed", "stopped": "stopped", "done": "done"}.get(end or "", "running")
        step = {"id": name, "argv": ["echolot", name], "shown": f"echolot {name} -c echolot.yml", "status": status}
        if status != "waiting":
            step.update(started=now - ago, log=f".echolot/log/check.{name}.log", pid=7000 + len(steps))
        if status in ("done", "failed", "stopped"):
            step.update(finished=now - 1, exit=0 if status == "done" else 2)
        steps.append(step)
    out = root / ".echolot" / "out"
    run = {"job": "check", "pid": pid or os.getpid(), "started": now - ago, "finished": (now - 1) if end else None,
           "what": {"do": do, "scenario": scenario, "mode": mode, "launches": launches if do == "record" else None,
                    "report": str(out / "report.json"), "before": str(out / "before.json") if compare else None},
           "steps": steps}
    if end:
        run["end"] = end
    log = root / ".echolot" / "log"
    log.mkdir(parents=True, exist_ok=True)
    (log / "check.json").write_text(json.dumps(run), encoding="utf-8")
    if do == "record" and own:
        said = {"scenario": scenario, "mode": mode, "started": now - ago + 1, "pid": 7000,
                "iterations": None if mode == "gradle" else launches, "done": done}
        if cooling:
            said["cooling"] = cooling
        if at != "collect" or end == "done":
            said.update(finished=now - 2, exit=0, traces=done)
        (log / "collect.json").write_text(json.dumps(said), encoding="utf-8")
    return run


@pytest.fixture
def gone(monkeypatch):
    """Every process but this one is gone: a run under another pid was cut short."""
    monkeypatch.setattr(state, "_alive", lambda pid: pid == os.getpid())


# --- before a run -------------------------------------------------------------------------------

def test_before_a_run_the_button_says_what_a_press_does(project):
    site, echolot = serving(project.ready())
    page = page_of(site)
    go = button(page)
    check("Check the app, the page's one big button, ready to press",
          go.text == "Check the app" and "disabled" not in go.attrs and go.attrs["class"] == "btn primary press", go.attrs)
    check("how many launches, about how long, and that the code is not touched",
          hint(page) == "3 launches, about a minute. Nothing in your code changes.", hint(page))
    check("no run is shown, and nothing was started", not page.all("ol", "run") and not echolot.ran)
    check("the step is the button's, so no command is offered to copy", not page.all("code", id="next-cmd")
          and "What to do next" not in page.text)
    runs = [pre.text for pre in setting(page, "What a press runs").all("pre")]
    check("what a press runs is said under All settings, as a person would type it", runs == [
        "echolot collect -c echolot.yml -n 3",
        "echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace -c echolot.yml --html"], runs)


def test_how_long_it_takes_comes_from_the_config(project):
    project.app().config(SLOW).agents().doctor()
    check("the runs times the recording's length, plus the reading: 3 × (20 + 4) + 3 × 2.5 s",
          hint(page_of(site_of(project))) == "3 launches, about a minute and a half. Nothing in your code changes.")
    project.config(ANCHORS)
    check("a command of the project runs it: runs, and its own length",
          hint(page_of(site_of(project))) == "3 runs, about 45 seconds. Nothing in your code changes.")
    project.config(COOLING)
    check("a phone that is held cool may wait, and the line says so", hint(page_of(site_of(project))) == (
        "3 launches, about a minute. Nothing in your code changes. The phone may wait to cool down between launches."))


@pytest.mark.parametrize("seconds, said", [
    (2, "about 5 seconds"), (12, "about 10 seconds"), (47, "about 45 seconds"), (54, "about 55 seconds"),
    (55, "about a minute"), (74, "about a minute"), (80, "about a minute and a half"), (115, "about 2 minutes"),
    (150, "about 2 and a half minutes"), (240, "about 4 minutes"), (290, "about 5 minutes"), (1000, "about 17 minutes")])
def test_a_stretch_of_time_as_a_person_says_it(seconds, said):
    check(f"{seconds} s", check_mod.about(seconds) == said, check_mod.about(seconds))


def test_the_number_of_launches_is_under_all_settings_with_what_compare_needs(project):
    site, echolot = serving(project.ready())
    page = page_of(site)
    row = setting(page, "Launches in one check")
    field = row.one("input")
    check("three, unless it is changed", field.attrs["value"] == "3" and field.attrs["name"] == "launches"
          and field.attrs["inputmode"] == "numeric", field.attrs)
    check("nothing the browser would hold a press back for while the fold is closed",
          "required" not in field.attrs and "pattern" not in field.attrs and field.attrs.get("type") == "text")
    check("with what a comparison needs, in compare's own numbers",
          "a comparison needs four a side, or three against five" in row.text, row.text)
    check("and that nothing saves it", "Nothing saves this number" in row.text and "runner.iterations" in row.text)
    offers = json.loads(page.one("form", id="check-form").attrs["data-offers"])
    hints = offers["record:coldStart"]["hints"]
    check("the line beside the button is there for every number, for the script to show",
          sorted(hints, key=int) == [str(n) for n in range(1, check_mod.MOST + 1)]
          and hints["1"] == "1 launch, about 20 seconds. Nothing in your code changes."
          and hints["10"] == "10 launches, about 3 minutes. Nothing in your code changes.", (hints["1"], hints["10"]))
    before = (project.root / "echolot.yml").read_bytes()
    reply = press(site, fields={"what": "record:coldStart", "launches": "7"})
    check("the press is taken", reply.status == 303, reply.body)
    check("it goes to the command as an argument", echolot.ran[0] == [
        "collect", "-c", str(project.root / "echolot.yml"), "-n", "7"], echolot.ran[0])
    check("and the config is as it was", (project.root / "echolot.yml").read_bytes() == before)
    check("seven launches, seven rows", len([r for r in rows(page_of(site)) if r[1].startswith("Launch")]) == 7)


@pytest.mark.parametrize("launches", ["0", "21", "-1", "3.5", "three", "999", " 1 2 "])
def test_a_number_of_launches_that_is_none_starts_nothing(project, launches):
    site, echolot = serving(project.ready())
    reply = press(site, fields={"what": "record:coldStart", "launches": launches})
    check("refused, on a page that says what a number is", reply.status == 400 and not echolot.ran
          and "whole number from 1 to 20" in reply.body, (reply.status, reply.body[:200]))
    check("with the way back", read(reply.body).one("a", "btn").attrs["href"] == f"/{site.token}/")
    check("and no run was left behind", job.read(project.root, "check") is None)


# --- the reasons it is disabled -----------------------------------------------------------------

@pytest.fixture
def no_setup(monkeypatch):
    """The server without the addresses that set a project up: where there is
    no config its page keeps the button, disabled, beside the reason."""
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("setup")])


def blocked(project: Project, site: ui.Site | None = None, echolot: Echolot | None = None) -> str:
    """The reason beside a disabled button; and a press all the same starts nothing."""
    if site is None:
        site, echolot = serving(project)
    page = page_of(site)
    check("the button is there, and disabled", "disabled" in button(page).attrs, button(page).attrs)
    why = hint(page)
    check("the reason is beside it for every reader, not in a tooltip",
          page.one("p", id="check-hint").attrs.get("role") == "status" and "title" not in button(page).attrs)
    before = files(project.root)
    reply = press(site, fields={"what": "record:coldStart"})
    check("a press that reaches the server all the same is refused with the same reason",
          reply.status == 409 and read(reply.body).one("p", "lead").text == why, (reply.status, reply.body[:300]))
    check("nothing started, nothing written", not echolot.ran and files(project.root) == before)
    return why


def test_no_phone(project, adb):
    adb.listing = ""
    why = blocked(project.ready())
    check("in the words collect stops with", why == (
        "The phone is not ready: adb sees no devices at all. The line marked red says what to do."), why)


def test_several_phones(project, adb):
    adb.listing = ONE + EMULATOR
    why = blocked(project.ready())
    check("both named", why.startswith("The phone is not ready: several devices connected: EXAMPLE0001, emulator-5554."), why)


def test_a_phone_that_has_not_allowed_debugging(project, adb):
    adb.listing = LOCKED
    check("said", "debugging not authorised" in blocked(project.ready()))


def test_no_adb_on_this_computer(project, adb):
    adb.listing = None
    check("said", blocked(project.ready()).startswith("The phone is not ready: adb not found."))


def test_an_app_that_cannot_be_measured(project):
    why = blocked(project.app(profileable=False).config().agents().doctor())
    check("a recording would show nothing of it", why == (
        f"{APP} cannot be measured yet: a release-like build of it shows nothing of the app in a recording. "
        "The line marked red says what to do."), why)


def test_an_app_with_debug_builds_only(project):
    check("said", "has only debuggable builds" in blocked(project.app(gradle=DEBUG_ONLY).config().agents().doctor()))


def test_no_config(project, no_setup):
    why = blocked(project.app().agents().doctor())
    check("the app is not known yet, and what comes first", why == (
        "Not yet: echolot does not know this app. It needs an echolot.yml, and the step under What to do next "
        "writes one."), why)
    page = page_of(site_of(project))
    check("that step keeps its place, with its command", page.one("code", id="next-cmd").text == "echolot guide setup")
    check("with no scenario there is no number of launches to set", not page.all("input", name="launches"))


def test_a_config_that_does_not_load(project):
    why = blocked(project.app().config(LAUNCH + "  end: {name: x}\n").agents().doctor())
    check("said", why == "Not yet: echolot.yml does not load. What to do next names the line to fix.", why)


def test_a_project_not_set_up(project, no_setup):
    why = blocked(project.app())
    check("said", why.startswith("Not yet: echolot is not set up in this project."), why)
    check("and the step is init", page_of(site_of(project)).one("code", id="next-cmd").text == "echolot init")


def test_a_self_check_that_did_not_pass(project):
    why = blocked(project.app().config().agents().doctor(failed=("the launch window",)))
    check("no report made here could be trusted", "last self-check did not pass" in why, why)


def test_an_open_investigation(project):
    project.ready().traces().report().hunt(1, "why is it slow", "open", current=True)
    why = blocked(project)
    check("an agent is at work, and a check would be mixed into its rounds", why.startswith(
        'Not now: an investigation is open ("why is it slow"), and an agent works with this app\'s recordings.'), why)


def test_a_recording_started_elsewhere(project):
    project.ready()
    log = project.root / ".echolot" / "log"
    (log / "collect.json").write_text(json.dumps({
        "scenario": "coldStart", "mode": "launch", "started": time.time() - 20, "pid": os.getpid(),
        "iterations": 5, "done": 1}), encoding="utf-8")
    why = blocked(project)
    check("what is running, how far, and that it was not this page", why == (
        "Not now: a recording of coldStart is already running, started from a terminal or by an agent: "
        "1 of 5 recorded so far. Wait for it to end, then press Refresh."), why)
    check("no Stop: it is not the page's to stop", not [b for b in page_of(site_of(project)).all("button") if b.text == "Stop"])


def test_a_grey_line_stops_nothing(project):
    """A config whose code is elsewhere: the app is not known, which is grey."""
    site, echolot = serving(project.config().agents().doctor())
    check("the button is ready", "disabled" not in button(page_of(site)).attrs)
    check("and a press is taken", press(site, fields={"what": "record:coldStart"}).status == 303 and echolot.ran)


# --- a press, to its end ------------------------------------------------------------------------

def test_a_press_records_reads_and_shows_the_short_version(project):
    site, echolot = serving(project.ready())
    reply = press(site, fields={"what": "record:coldStart"})
    check("the browser is sent to the start page, where the run is", reply.status == 303 and reply.body == ""
          and dict(reply.headers)["Location"] == f"/{site.token}/", (reply.status, reply.headers))
    config = str(project.root / "echolot.yml")
    traces = [str(project.root / ".echolot" / "traces" / f"coldStart_iter00{i}.perfetto-trace") for i in range(3)]
    check("collect with three launches, then analyze over each recording by name, as a page too", echolot.ran == [
        ["collect", "-c", config, "-n", "3"], ["analyze", *traces, "-c", config, "--html"]], echolot.ran)
    page = page_of(site)
    check("every launch recorded, the recordings read", rows(page) == [
        ("done", "Launch 1 of 3", "recorded"), ("done", "Launch 2 of 3", "recorded"), ("done", "Launch 3 of 3", "recorded"),
        ("done", "Reading the recordings", "done")], rows(page))
    check("done, and the short version", page.one("p", id="run-end").text == "Done. The short version:")
    counts = [li.text for li in page.one("ul", "counts").all("li")]
    check("the report's own counts, a word with every figure",
          counts == ["1 needs attention", "2 worth a look", "2 are fine", "1 not checked"], counts)
    check("no comparison: there was no report before", not page.all("p", id="run-compare"))
    see = page.one("form", id="check-form").one("a", "btn")
    check("See the report is the one next step", see.text == "See the report" and "primary" in see.attrs["class"]
          and see.attrs["href"] == f"/{site.token}/report" and get(site, "report").status == 200)
    check("and Check again stands beside it as the second choice", button(page).text == "Check again"
          and button(page).attrs["class"] == "btn line", button(page).attrs)
    check("a page that shows a run that ended does not load itself again", "http-equiv" not in get(site).body.split("</head>")[0]
          .replace('http-equiv="Content-Security-Policy"', ""))


def test_check_again_compares_with_the_report_before_it(project):
    project.ready().traces().report()
    earlier = (project.root / ".echolot" / "out" / "report.json").read_bytes()
    site, echolot = serving(project, window=1500.0)
    page = page_of(site)
    check("once a report exists the button is Check again", button(page).text == "Check again"
          and "primary" in button(page).attrs["class"])
    check("and the line says a comparison follows, and what three against three can say", hint(page) == (
        "3 launches, about a minute, then a comparison with the report before it. Nothing in your code changes. "
        "To tell a change from chance it takes four launches a side: All settings has the number."),
        hint(page))
    check("what a press runs ends with compare", setting(page, "What a press runs").all("pre")[-1].text
          == "echolot compare .echolot/out/before.json -c echolot.yml --html")
    check("pressed", press(site, fields={"what": "record:coldStart"}).status == 303)
    before = project.root / ".echolot" / "out" / "before.json"
    check("the report before it was kept beside the new one", before.read_bytes() == earlier)
    check("and compare was given it, after the two others", [r[0] for r in echolot.ran] == ["collect", "analyze", "compare"]
          and echolot.ran[2] == ["compare", str(before), "-c", str(project.root / "echolot.yml"), "--html"], echolot.ran[2])
    written = json.loads((project.root / ".echolot" / "out" / "comparison.json").read_text())
    check("what compare writes is kept where it writes it",
          (project.root / ".echolot" / "out" / "comparison.md").is_file() and written["kind"] == "comparison")
    check("with its plain reading and the page of it", "plain" in written
          and (project.root / ".echolot" / "out" / "comparison.html").is_file())
    page = page_of(site)
    check("the comparison is a row of the run", rows(page)[-1] == ("done", "Comparing with the report before it", "done"))
    said = page.one("p", id="run-compare").text
    verdict = written["plain"]["verdict"]
    check("one sentence says a comparison was made, in the words of its plain reading", said
          == f"It was compared with the report before it. {verdict['headline']}. {verdict['text']} See what changed"
          and verdict["headline"] == "Faster by 0.4 s", said)
    check("the new report is the one on show", "The launch takes 1.5 s" in page.one("ul", "reps").text)
    link = page.one("p", id="run-compare").one("a")
    check("and a link leads to the page of the comparison", link.attrs["href"] == f"/{site.token}/after")
    after = read(get(site, "after").body)
    check("which says the same, as a page", after.one("h1").text == "Faster by 0.4 s"
          and [a.text for a in after.one("nav", "steps").all("a")] == ["Start", "Report", "After the fix"]
          and after.one("nav", "steps").one("a", aria_current="page").text == "After the fix")


def test_the_link_to_the_comparison_appears_with_its_page(project, monkeypatch):
    project.ready().traces().report()
    site, _ = serving(project, window=1500.0)
    press(site, fields={"what": "record:coldStart"})
    link = page_of(site).one("p", id="run-compare").one("a")
    check("a route named `after` is all it takes", link.attrs["href"] == f"/{site.token}/after" and link.text == "See what changed")
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if r.name != "after"])
    said = page_of(site).one("p", id="run-compare")
    check("without the route there is no link: where the whole of it is", not said.all("a") and "comparison.md" in said.text,
          said.text)


def test_a_run_that_ended_is_history_once_something_newer_happened(project):
    site, _ = serving(project.ready())
    press(site, fields={"what": "record:coldStart"})
    check("shown while it is the last word", page_of(site).all("ol", "run"))
    report = project.root / ".echolot" / "out" / "report.json"
    later = time.time() + 60
    os.utime(report, (later, later))        # `analyze` by hand, a minute on
    page = page_of(site)
    check("a report written since: the run is not shown as the state of things", not page.all("ol", "run")
          and button(page).text == "Check again" and "primary" in button(page).attrs["class"])
    os.utime(report, (later - 3600, later - 3600))
    kept = (project.root / ".echolot" / "log" / "collect.json").read_bytes()
    (project.root / ".echolot" / "log" / "collect.json").write_text(json.dumps({
        "scenario": "coldStart", "started": later, "pid": 1, "iterations": 5, "done": 5, "finished": later + 9,
        "exit": 0}), encoding="utf-8")
    check("a recording made elsewhere since: the same", not page_of(site).all("ol", "run"))
    (project.root / ".echolot" / "log" / "collect.json").write_bytes(kept)
    run = job.read(project.root, "check")
    st = state.project_state(project.root, str(project.root / "echolot.yml"))
    check("with nothing newer it is news for a day", check_mod.reading(project.root, st, run, now=run["finished"] + 3600)
          and check_mod.reading(project.root, st, run, now=run["finished"] + check_mod.NEWS_S + 1) is None)


# --- a run in progress --------------------------------------------------------------------------

@pytest.mark.parametrize("done, expected, note", [
    (0, [("now", "Launch 1 of 3", "recording…"), ("wait", "Launch 2 of 3", ""), ("wait", "Launch 3 of 3", ""),
         ("wait", "Reading the recordings", "")], "You can watch the phone"),
    (1, [("done", "Launch 1 of 3", "recorded"), ("now", "Launch 2 of 3", "recording…"), ("wait", "Launch 3 of 3", ""),
         ("wait", "Reading the recordings", "")], "You can watch the phone"),
    (2, [("done", "Launch 1 of 3", "recorded"), ("done", "Launch 2 of 3", "recorded"), ("now", "Launch 3 of 3", "recording…"),
         ("wait", "Reading the recordings", "")], "You can watch the phone"),
])
def test_a_run_ticks_its_launches_off_from_collects_own_file(project, done, expected, note):
    a_run(project.ready(), "collect", done=done)
    site = site_of(project)
    page = page_of(site)
    check("every row is there from the start, and the launches are ticked off as collect counts them",
          rows(page) == expected, rows(page))
    check("the row in progress turns, the waiting ones are rings, a done one has its tick",
          [("spin" if li.all("i", "spin") else "ring" if li.all("i", "ring") else "icon") for li in page.one("ol", "run").all("li")]
          == [{"now": "spin", "wait": "ring", "done": "icon"}[state] for state, _, _ in expected])
    check("the phone may be watched", note in page.one("p", id="run-note").text)
    stop = [f for f in page.all("form") if f.attrs["action"] == f"/{site.token}/check/stop"]
    check("the button gave way to Stop", len(stop) == 1 and stop[0].one("button").text == "Stop"
          and not page.all("button", id="check-go") and not page.all("form", id="check-form"))
    check("the number of launches cannot be changed under a run", "disabled" in page.one("input", id="launches").attrs)
    check("a reader who does not see the list is told", page.one("p", id="run-said").attrs["role"] == "status"
          and page.one("p", id="run-said").text == f"A check of coldStart is running: launch {done + 1} of 3, recording.")


def test_before_collect_has_written_its_file_the_first_launch_is_in_progress(project):
    project.ready()
    (project.root / ".echolot" / "log" / "collect.json").write_text(json.dumps({
        "scenario": "coldStart", "started": time.time() - 3600, "pid": 1, "iterations": 5, "done": 5,
        "finished": time.time() - 3500, "exit": 0, "traces": 5}), encoding="utf-8")
    a_run(project, "collect", own=False)
    check("the file of the run before is not this run's: three launches, none done", rows(page_of(site_of(project))) == [
        ("now", "Launch 1 of 3", "recording…"), ("wait", "Launch 2 of 3", ""), ("wait", "Launch 3 of 3", ""),
        ("wait", "Reading the recordings", "")])


def test_a_phone_that_cools_down_between_launches(project):
    a_run(project.ready(), "collect", done=1, cooling="battery 41.2 °C, waiting for 38")
    check("a wait is said as a wait, not as a recording", rows(page_of(site_of(project)))[1] == (
        "now", "Launch 2 of 3", "waiting for the phone to cool down…"))


def test_reading_the_recordings_and_comparing(project):
    a_run(project.ready(), "analyze", done=3)
    site = site_of(project)
    page = page_of(site)
    check("the launches are done, the reading turns", rows(page) == [
        ("done", "Launch 1 of 3", "recorded"), ("done", "Launch 2 of 3", "recorded"), ("done", "Launch 3 of 3", "recorded"),
        ("now", "Reading the recordings", "reading…")], rows(page))
    check("nothing to watch on the phone now", page.one("p", id="run-note").text == "")
    a_run(project, "compare", done=3, compare=True)
    check("the comparison has a row of its own", rows(page_of(site))[-2:] == [
        ("done", "Reading the recordings", "done"), ("now", "Comparing with the report before it", "comparing…")])


def test_a_macrobenchmark_is_one_row(project):
    a_run(project.app().config(BENCHMARK).agents().doctor(), "collect", mode="gradle", launches=None, scenario="startup")
    page = page_of(site_of(project))
    check("the benchmark decides its launches: one row for it", rows(page) == [
        ("now", "The macrobenchmark", "running…"), ("wait", "Reading the recordings", "")], rows(page))
    check("and nothing is said of an app opening by itself", page.one("p", id="run-note").text == "")


def test_a_run_is_read_from_files_whoever_asks(project):
    """A page loaded again, a second tab and a server started again are all a
    site that reads the project: none was told of the run."""
    a_run(project.ready(), "collect", done=1)
    first, second = site_of(project), site_of(project)
    second.token, second.port = "another-server", 5555
    check("the same run, to the row", rows(page_of(first)) == rows(page_of(second))
          and rows(page_of(first))[1] == ("now", "Launch 2 of 3", "recording…"))


def test_with_script_off_the_page_of_a_run_loads_itself_again(project):
    a_run(project.ready(), "collect", done=1)
    site = site_of(project)
    body = get(site).body
    head = body.split("</head>")[0]
    check("in the head, for a browser with script off only",
          '<noscript><meta http-equiv="refresh" content="3"></noscript>' in head, head[-300:])
    check("Stop is a form, as the button is", read(body).one("div", id="run").one("form").attrs["method"] == "post")
    check("with script on the list is asked for instead", read(body).one("div", id="run").attrs["data-poll"]
          == f"/{site.token}/check/state")


def test_what_the_page_of_a_run_asks_for_while_it_runs(project):
    a_run(project.ready(), "collect", done=1)
    site = site_of(project)
    reply = get(site, "check/state")
    piece = read(reply.body).one("div", id="run-now")
    check("the rows as they stand, and that it runs", reply.status == 200 and piece.attrs["data-state"] == "running"
          and [(li.attrs["class"], li.one("b").text) for li in piece.all("li")][:2] == [
              ("done", "Launch 1 of 3"), ("now", "Launch 2 of 3")], reply.body[:300])
    check("with the sentence for a reader who does not see it", piece.attrs["data-said"]
          == "A check of coldStart is running: launch 2 of 3, recording.")
    check("the same rows the page was drawn with", [li.text for li in piece.all("li")]
          == [li.text for li in page_of(site).one("ol", "run").all("li")])
    a_run(project, "analyze", done=3, end="done")
    check("once it ended it says so, and the page draws itself anew",
          read(get(site, "check/state").body).one("div", id="run-now").attrs["data-state"] == "done")
    (project.root / ".echolot" / "log" / "check.json").unlink()
    check("no run at all is said too", read(get(site, "check/state").body).one("div", id="run-now").attrs["data-state"] == "none")


# --- one at a time ------------------------------------------------------------------------------

def test_a_second_press_is_refused_with_what_is_running(project):
    a_run(project.ready(), "collect", done=1)
    site, echolot = serving(project)
    before = files(project.root)
    reply = press(site, fields={"what": "record:coldStart"})
    page = read(reply.body)
    check("refused", reply.status == 409 and page.one("h1").text == "A check is already running", reply.status)
    check("with what is running, and that this press started nothing", page.one("p", "lead").text == (
        "A check of coldStart is running: launch 2 of 3, recording. This press started nothing: one check runs at a "
        "time. The start page shows how far it is."), page.one("p", "lead").text)
    check("nothing started and nothing written", not echolot.ran and files(project.root) == before)
    other = site_of(project)
    other.token, other.spawn = "another-tab-s-server", site.spawn
    check("from another tab, or another server on the project, the same",
          press(other, fields={"what": "record:coldStart"}).status == 409 and not echolot.ran)


def test_two_presses_are_answered_one_after_the_other(project):
    """A press is answered under the lock every page is drawn under, so the
    second finds what the first started."""
    site, echolot = serving(project.ready())
    seen = []

    def slow(root, name, plan):
        seen.append(site.drawing.locked())
        a_run(project, "collect")
    site.spawn = slow
    check("the first is taken", press(site, fields={"what": "record:coldStart"}).status == 303 and seen == [True])
    check("the second finds it running", press(site, fields={"what": "record:coldStart"}).status == 409 and len(seen) == 1)


# --- stop ---------------------------------------------------------------------------------------

def test_stop_asks_the_run_to_end_and_the_page_says_it_is_stopping(project):
    a_run(project.ready(), "collect", done=1)
    site = site_of(project)
    reply = press(site, "check/stop")
    check("the browser goes back to the start page", reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/")
    check("the request is left for the run's own process", job.stop_path(project.root, "check").read_text() == str(os.getpid()))
    page = page_of(site)
    check("until it has ended the page says it is stopping", "Stopping: the launch in progress is being ended."
          in page.one("p", id="run-note").text and "disabled" in page.one("div", id="run").one("button").attrs)
    check("a second Stop changes nothing", press(site, "check/stop").status == 303)


def test_stopped_the_recordings_already_finished_are_kept_and_nothing_is_analysed(project):
    site, echolot = serving(project.ready())
    echolot.stop_at, echolot.pressed = 2, lambda: check_mod.stop(project.root)
    check("pressed", press(site, fields={"what": "record:coldStart"}).status == 303)
    check("collect was asked to end, and analyze never started", [r[0] for r in echolot.ran] == ["collect"])
    page = page_of(site)
    check("the rows as they stood", rows(page) == [
        ("done", "Launch 1 of 3", "recorded"), ("done", "Launch 2 of 3", "recorded"), ("stopped", "Launch 3 of 3", "stopped"),
        ("wait", "Reading the recordings", "")], rows(page))
    check("and what was kept, in a sentence", page.one("p", id="run-end").text
          == "Stopped. 2 recordings are kept, and nothing was analysed.", page.one("p", id="run-end").text)
    check("the two recordings are there", sorted(p.name for p in (project.root / ".echolot" / "traces").iterdir())
          == ["coldStart_iter000.perfetto-trace", "coldStart_iter001.perfetto-trace"])
    check("no report was written", not (project.root / ".echolot" / "out").exists())
    check("what is kept can be read, or recorded anew", button(page).text == "Read the recordings"
          and page.one("button", id="check-anew").text == "Record again")


def test_stopped_before_the_first_launch_ended(project):
    site, echolot = serving(project.ready())
    echolot.stop_at, echolot.pressed = 0, lambda: check_mod.stop(project.root)
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("nothing was finished, and it says so", page.one("p", id="run-end").text
          == "Stopped. No recording was finished, and nothing was analysed.")
    check("the button is as it was", button(page).text == "Check the app")


def test_stopped_while_reading_or_before_it(project):
    a_run(project.ready().traces(), "analyze", done=3, end="stopped")
    os.utime(project.root / ".echolot" / "log" / "check.json")
    check("while reading", page_of(site_of(project)).one("p", id="run-end").text
          == "Stopped. The recordings are kept, and no report was written from them.")
    run = a_run(project, "collect", done=3, end="done")
    run.update(end="stopped")
    (project.root / ".echolot" / "log" / "check.json").write_text(json.dumps(run), encoding="utf-8")
    check("between the recording and the reading", page_of(site_of(project)).one("p", id="run-end").text
          == "Stopped. 3 recordings are kept, and nothing was analysed.")
    a_run(project, "compare", done=3, compare=True, end="stopped")
    check("before the comparison", page_of(site_of(project)).one("p", id="run-end").text
          == "Stopped. The new report is ready, and no comparison was made.")


# --- a run that failed --------------------------------------------------------------------------

def test_a_recording_that_fails_shows_what_collect_said(project):
    said = "com.example.app is not installed on EXAMPLE0001. Install the build you mean to measure, and collect again."
    site, echolot = serving(project.ready(), fail="collect", says=said)
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("a sentence of the page's own first", page.one("p", id="run-end").text
          == "The recording did not finish. Nothing was analysed.", page.one("p", id="run-end").text)
    check("then the command, as a person would type it", page.one("div", id="run").all("code")[0].text
          == "echolot collect -c echolot.yml -n 3")
    check("and what it said, in its own words, as text", page.one("pre", "printed").raw()
          == f"device EXAMPLE0001\ncollection error: {said}", page.one("pre", "printed").raw())
    check("with where everything it printed is", ".echolot/log/check.collect.log" in page.one("div", id="run").text
          and (project.root / ".echolot" / "log" / "check.collect.log").is_file())
    check("and what to do", "Put that right, then press the button again." in page.one("div", id="run").text)
    check("the first launch is the one that did not finish", rows(page)[0] == ("failed", "Launch 1 of 3", "did not finish")
          and rows(page)[-1] == ("wait", "Reading the recordings", ""))
    check("the button is there to press again", button(page).text == "Check the app" and "disabled" not in button(page).attrs)


def test_a_recording_that_fails_midway_keeps_what_it_recorded(project):
    site, _ = serving(project.ready(), fail="collect", fail_at=2, says="repeat 3 of 3: the screen went dark.")
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("two were finished and are kept", page.one("p", id="run-end").text == (
        "The recording did not finish. 2 recordings were finished before that and are kept. Nothing was analysed."))
    check("the third is the one that failed", [r[0] for r in rows(page)] == ["done", "done", "failed", "wait"])


def test_what_a_command_said_is_shown_as_text(project):
    site, _ = serving(project.ready(), fail="collect", says='<script>alert(1)</script> & "quotes" `ticks`')
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("to the letter, and as no markup", page.one("pre", "printed").raw().endswith(
        'collection error: <script>alert(1)</script> & "quotes" `ticks`') and len(page.all("script")) == 2)


def test_reading_that_fails_keeps_the_recordings(project):
    site, _ = serving(project.ready(), fail="analyze", says="no process matches com.example.app in coldStart_iter000")
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("said, with what is kept", page.one("p", id="run-end").text == (
        "Reading the recordings did not finish. The recordings are kept, and no report was written from them."))
    check("analyze's own words", page.one("pre", "printed").raw() == "error: no process matches com.example.app in coldStart_iter000"
          and page.one("div", id="run").all("code")[0].text.startswith("echolot analyze .echolot/traces/coldStart_iter*"))
    check("the launches stay recorded, the reading is what failed", [r[0] for r in rows(page)] == ["done", "done", "done", "failed"])
    check("and the recordings are the button's to read", button(page).text == "Read the recordings")


def test_a_comparison_that_fails_leaves_the_report(project):
    project.ready().traces().report()
    site, _ = serving(project, fail="compare", says="these reports measured two scenarios")
    press(site, fields={"what": "record:coldStart"})
    page = page_of(site)
    check("the report is ready all the same", page.one("p", id="run-end").text == (
        "The new report is ready. The comparison was not made. The report stands as it is; only the comparison with "
        "the one before it is missing."))
    check("compare's own words", page.one("pre", "printed").raw() == "error: these reports measured two scenarios")
    check("the report is offered", page.one("form", id="check-form").one("a", "btn").text == "See the report"
          and page.one("ul", "counts").all("li"))


def test_a_command_that_printed_nothing(project):
    site, _ = serving(project.ready(), fail="analyze", says="")
    (project.root / ".echolot" / "log").mkdir(parents=True, exist_ok=True)
    press(site, fields={"what": "record:coldStart"})
    (project.root / ".echolot" / "log" / "check.analyze.log").write_text("", encoding="utf-8")
    check("is said to have printed nothing", page_of(site).one("pre", "printed").raw() == "(it printed nothing)")


# --- a run cut short from outside ---------------------------------------------------------------

@pytest.mark.parametrize("at, done, said", [
    ("collect", 0, "No recording was finished, and nothing was analysed."),
    ("collect", 2, "2 recordings are kept, and nothing was analysed."),
    ("analyze", 3, "The recordings are kept, and no report was written from them."),
    ("compare", 3, "The new report is ready, and no comparison was made."),
])
def test_a_run_whose_process_is_gone_reads_as_cut_short_never_as_running(project, gone, at, done, said):
    a_run(project.ready(), at, done=done, pid=424242, compare=at == "compare")
    site = site_of(project)
    page = page_of(site)
    check("it is not shown as running: no Stop, no page that loads itself, the button back",
          not [b for b in page.all("button") if b.text == "Stop"] and "http-equiv=\"refresh\"" not in get(site).body
          and page.all("button", id="check-go"))
    check("it says that it was cut short, why that happens, and what was kept", page.one("p", id="run-end").text == (
        "This check was cut short. The process that ran it is gone: echolot ui was stopped, the terminal was closed, "
        f"or the computer went to sleep. {said}"), page.one("p", id="run-end").text)
    check("the row it was at says so", ("cut", "cut short") in [(r[0], r[2]) for r in rows(page)], rows(page))
    check("and the page that asks how far it is hears that it ended",
          read(get(site, "check/state").body).one("div", id="run-now").attrs["data-state"] == "interrupted")


def test_a_recording_that_outlived_its_check_keeps_the_button_waiting(project, monkeypatch):
    run = a_run(project.ready(), "collect", done=1, pid=424242)
    collect = json.loads((project.root / ".echolot" / "log" / "collect.json").read_text())
    monkeypatch.setattr(state, "_alive", lambda pid: pid == collect["pid"])
    page = page_of(site_of(project))
    check("the check reads as cut short", run["pid"] == 424242 and "This check was cut short." in page.one("p", id="run-end").text)
    check("and the button waits for the recording it left, which ends by itself", "disabled" in button(page).attrs and hint(page) == (
        "Not now: the recording of coldStart of a check that was cut short is still running, 1 of 3 recorded so far. "
        "It ends by itself. Then press Refresh."), hint(page))


# --- recordings that are already there ----------------------------------------------------------

def _log(project, *runs):
    path = project.root / ".echolot" / "log" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for run in runs:
            f.write((run if isinstance(run, str) else json.dumps(run)) + "\n")


def _analyzed(ms, traces, *argv, exit_=0):
    return {"cmd": "analyze", "argv": ["analyze", *argv], "exit": exit_, "ms": ms, "facts": {"traces": traces}}


def test_reading_takes_what_the_projects_last_analyze_took(project, adb):
    adb.listing = ""
    project.ready().traces(n=4)
    _log(project, _analyzed(2000, 2), _analyzed(33950, 5),
         _analyzed(900000, 5, exit_=1), _analyzed(100, 5, "--scenario", "feedScroll"))
    page = page_of(serving(project)[0])
    check("6.8 s a recording, from the log: four take about 25 seconds", hint(page)
          == "4 recordings are already here. Reading them takes about 25 seconds. The phone is not needed.", hint(page))


def test_the_estimate_prefers_the_same_scenario_and_falls_back_to_the_default(tmp_path):
    check("nothing in the log: the default", check_mod.read_s(tmp_path) == check_mod.READ_S)
    project = type("P", (), {"root": tmp_path})()
    _log(project, _analyzed(10000, 2, "--scenario", "coldStart"), "[1, 2]", _analyzed(3000, 3))
    check("one of the same scenario wins over a later one that names none", check_mod.read_s(tmp_path, "coldStart") == 5.0)
    check("another scenario reads the one that names none", check_mod.read_s(tmp_path, "feedScroll") == 1.0)
    check("a config of one scenario reads the last", check_mod.read_s(tmp_path) == 1.0)


def test_recordings_that_are_there_are_read_without_the_phone(project, adb):
    adb.listing = ""        # no phone at all
    site, echolot = serving(project.ready().traces(n=4))
    page = page_of(site)
    check("the button reads them", button(page).text == "Read the recordings" and "disabled" not in button(page).attrs)
    check("and says that the phone is not needed", hint(page)
          == "4 recordings are already here. Reading them takes about 10 seconds. The phone is not needed.", hint(page))
    check("the hidden field says so too", page.one("form", id="check-form").one("input", type="hidden").attrs["value"] == "read:coldStart")
    check("no number of launches: none is recorded", not page.all("input", name="launches"))
    check("recording anew is not offered while the phone is in the way", not page.all("button", id="check-anew"))
    check("pressed", press(site, fields={"what": "read:coldStart"}).status == 303)
    check("analyze alone, over the recordings that were there", [r[0] for r in echolot.ran] == ["analyze"]
          and len([a for a in echolot.ran[0] if a.endswith(".perfetto-trace")]) == 4, echolot.ran)
    page = page_of(site)
    check("one row, and the short version", rows(page) == [("done", "Reading the recordings", "done")]
          and page.one("p", id="run-end").text == "Done. The short version:")
    check("the phone was asked for nothing but its list", all(a[:2] == ["adb", "devices"] for a in adb.asked))


def test_recordings_newer_than_the_report_wait_to_be_read_and_can_be_left(project):
    project.ready().report().traces(n=3)
    hour = time.time() - 3600
    os.utime(project.root / ".echolot" / "out" / "report.json", (hour, hour))
    site, echolot = serving(project)
    page = page_of(site)
    check("they are read, and compared with the report before them", button(page).text == "Read the recordings" and hint(page) == (
        "3 recordings are already here. Reading them takes about 10 seconds, then a comparison with the report before it. "
        "The phone is not needed."), hint(page))
    anew = page.one("button", id="check-anew")
    check("or left: Record again is the second choice", anew.text == "Record again" and "line" in anew.attrs["class"]
          and anew.attrs["name"] == "anew" and "hidden" not in anew.attrs)
    check("pressed", press(site, fields={"what": "read:coldStart"}).status == 303)
    check("reading reads, then compares with the report that was there", [r[0] for r in echolot.ran] == ["analyze", "compare"]
          and (project.root / ".echolot" / "out" / "before.json").is_file(), echolot.ran)
    echolot.ran.clear()
    check("pressed again, to record anew", press(site, fields={"what": "read:coldStart", "anew": "1"}).status == 303)
    check("recording anew records, three launches, then reads and compares",
          [r[0] for r in echolot.ran] == ["collect", "analyze", "compare"] and "-n" in echolot.ran[0])


def test_reading_where_there_is_nothing_to_read_is_refused(project):
    site, echolot = serving(project.ready())
    reply = press(site, fields={"what": "read:coldStart"})
    check("refused, with what to do", reply.status == 409 and "No recordings of <code>coldStart</code> wait to be read" in reply.body
          and not echolot.ran, reply.body[:300])


# --- several scenarios --------------------------------------------------------------------------

def several(project: Project) -> Project:
    project.app().config(SEVERAL).agents().doctor()
    project.traces("feedScroll", n=2)
    made = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    return project.report(".echolot/out/coldStart", data={**known(), "generated_at": made})


def test_several_scenarios_are_choice_cards(project):
    site, _ = serving(several(project))
    page = page_of(site)
    check("the question is asked", page.one("h2", id="q-what").text == "What do you want to check?"
          and page.one("div", "opts").attrs == {"class": "opts", "role": "radiogroup", "aria-labelledby": "q-what"})
    cards = page.one("div", "opts").all("label", "opt")
    check("a card per scenario: its name, what it measures and where it stands", [
        (c.one("span", "opt-t").text, c.one("span", "opt-d").text) for c in cards] == [
        ("coldStart", "The launch of the app, as the phone records it. Last checked 2 hours ago."),
        ("feedScroll", "The stretch from feed_scroll_start to feed_scroll_end. 2 recordings wait to be read.")],
        [c.text for c in cards])
    radios = [c.one("input") for c in cards]
    check("each is a radio of the button's form; the one the page is about is chosen",
          [(r.attrs["type"], r.attrs["name"], r.attrs["form"], r.attrs["value"], "checked" in r.attrs) for r in radios] == [
              ("radio", "what", "check-form", "record:coldStart", True), ("radio", "what", "check-form", "read:feedScroll", False)])
    check("the cards carry the choice, so the form has no hidden one", not page.one("form", id="check-form").all("input"))
    check("with script off the button cannot follow the choice: it says what holds for every card",
          button(page).text == "Start")
    check("and the line says where to look", hint(page) == "Each choice says where it stands. Nothing in your code changes.")
    offers = json.loads(page.one("form", id="check-form").attrs["data-offers"])
    check("the script has each choice's own button and line", {k: (v["label"], v["anew"]) for k, v in offers.items()} == {
        "record:coldStart": ("Check again", False), "read:feedScroll": ("Read the recordings", True)}
        and offers["read:feedScroll"]["hint"].startswith("2 recordings are already here."), offers)


def test_the_scenario_chosen_goes_to_the_commands_as_an_argument(project):
    site, echolot = serving(several(project))
    config = str(project.root / "echolot.yml")
    before = (project.root / "echolot.yml").read_bytes()
    check("the other one, pressed", press(site, fields={"what": "read:feedScroll"}).status == 303)
    check("its recordings are read, by its name", echolot.ran == [["analyze", *[
        str(project.root / ".echolot" / "traces" / f"feedScroll_iter00{i}.perfetto-trace") for i in range(2)],
        "-c", config, "--scenario", "feedScroll", "--html"]], echolot.ran)
    check("its report is its own", (project.root / ".echolot" / "out" / "feedScroll" / "report.json").is_file())
    page = page_of(site)
    check("the report on offer after the run is that scenario's",
          page.one("form", id="check-form").one("a", "btn").attrs["href"] == f"/{site.token}/report/feedScroll")
    echolot.ran.clear()
    check("the first one, checked again", press(site, fields={"what": "record:coldStart", "launches": "4"}).status == 303)
    check("named in every command, and compared with its own report before", echolot.ran[0] == [
        "collect", "-c", config, "-n", "4", "--scenario", "coldStart"] and echolot.ran[2] == [
        "compare", str(project.root / ".echolot" / "out" / "coldStart" / "before.json"), "-c", config, "--scenario", "coldStart",
        "--html"],
        echolot.ran)
    check("and nothing was written to the config", (project.root / "echolot.yml").read_bytes() == before)


def test_a_scenario_the_config_does_not_name_is_refused(project):
    site, echolot = serving(several(project))
    reply = press(site, fields={"what": "record:../../etc"})
    check("no such scenario", reply.status == 400 and "No such scenario" in reply.body and not echolot.ran)
    reply = press(site, fields={"what": "erase:coldStart"})
    check("and a press that asks for something else is no press", reply.status == 400 and not echolot.ran)


def test_the_cards_are_held_while_a_run_is_on(project):
    a_run(several(project), "analyze", do="read", scenario="feedScroll")
    page = page_of(site_of(project))
    check("no choice can be made under a run", all("disabled" in c.attrs for c in page.one("div", "opts").all("input")))


def test_the_press_with_no_fields_is_the_page_s_own_scenario(project):
    site, echolot = serving(project.ready())
    check("taken", press(site).status == 303 and echolot.ran[0][0] == "collect" and "-n" in echolot.ran[0])


# --- the answer's page --------------------------------------------------------------------------

def answered(project: Project, demo_report) -> Project:
    hunt, reports = fx.states(demo_report)["many"]
    return project.ready().traces().report().investigation(hunt, reports)


def test_the_answers_page_has_the_button_in_its_place(project, demo_report):
    site, echolot = serving(answered(project, demo_report))
    page = read(get(site, "answer").body)
    place = page.one("div", id="check-again-action")
    form = place.one("form")
    check("Check again, a form of the server's", form.attrs["action"] == f"/{site.token}/check" and form.attrs["method"] == "post"
          and form.one("button").text == "Check again" and "primary" in form.one("button").attrs["class"])
    check("about the investigation's scenario, against the investigation's first report",
          {i.attrs["name"]: i.attrs["value"] for i in form.all("input")} == {"what": "record:coldStart", "hunt": str(fx.N)})
    check("the line beside it says how long, with the config's own number of launches",
          form.one("p").text == "5 launches, about a minute and a half. Nothing in your code changes.", form.one("p").text)
    check("the page may send it", "form-action 'self'" in page.one("meta", http_equiv="Content-Security-Policy").attrs["content"])
    check("pressed", press(site, fields={"what": "record:coldStart", "hunt": str(fx.N)}).status == 303)
    first = project.root / ".echolot" / "hunts" / str(fx.N) / "reports" / "001.json"
    config = str(project.root / "echolot.yml")
    check("the line the page gives to copy, started from the page: collect as the config has it", echolot.ran[0] == [
        "collect", "-c", config], echolot.ran[0])
    check("and compare against the first report the investigation filed", echolot.ran[2] == ["compare", str(first), "-c", config, "--html"])
    check("no copy of a report was needed", not (project.root / ".echolot" / "out" / "before.json").exists())


def test_the_answers_button_while_a_check_runs_and_when_it_cannot(project, demo_report, adb):
    answered(project, demo_report)
    a_run(project, "collect", done=1)
    site = site_of(project)
    place = read(get(site, "answer").body).one("div", id="check-again-action")
    check("under a run it says what runs and where to see it", not place.all("button") and place.one("p").text
          == "A check of coldStart is running: launch 2 of 3, recording." and place.one("a").attrs["href"] == f"/{site.token}/"
          and place.one("a").text == "See how far it is")
    (project.root / ".echolot" / "log" / "check.json").unlink()
    (project.root / ".echolot" / "log" / "collect.json").unlink()
    adb.listing = ""
    place = read(get(site, "answer").body).one("div", id="check-again-action")
    check("with no phone it is disabled, with the reason", "disabled" in place.one("button").attrs
          and place.one("p").text.startswith("The phone is not ready"))


def test_an_investigation_that_filed_nothing_has_nothing_to_compare_with(project, demo_report):
    site, echolot = serving(answered(project, demo_report))
    reply = press(site, fields={"what": "record:coldStart", "hunt": "9"})
    check("refused", reply.status == 400 and "No such investigation" in reply.body and not echolot.ran)
    reply = press(site, fields={"what": "record:coldStart", "hunt": "../1"})
    check("and an investigation is a number", reply.status == 400 and not echolot.ran)


def test_a_file_on_disk_has_no_button(project, demo_report):
    hunt, reports = fx.states(demo_report)["many"]
    from echolot.pages import answer_page
    text = answer_page.render(hunt, reports, again=fx.AGAIN)
    page = read(text)
    check("the place is empty, and the page may send no form", not page.one("div", id="check-again-action").kids
          and "form-action 'none'" in page.one("meta", http_equiv="Content-Security-Policy").attrs["content"]
          and kit.SERVED not in text)


# --- a press that did not start -----------------------------------------------------------------

def test_a_process_that_does_not_start_is_said_and_nothing_is_left_running(project):
    site = site_of(project.ready())

    def broken(root, name, plan):
        raise job.Refused("the process that runs it could not be started: Permission denied")
    site.spawn = broken
    reply = press(site, fields={"what": "record:coldStart"})
    check("said on a page", reply.status == 500 and "The check did not start" in reply.body
          and "Permission denied" in reply.body, reply.body[:300])
    check("and the start page has its button", button(page_of(site)).text == "Check the app")


def test_a_press_that_falls_over_says_so_and_points_at_the_start_page(project, capsys):
    site = site_of(project.ready())

    def falls_over(root, name, plan):
        raise RuntimeError("the disk is <full>")
    site.spawn = falls_over
    reply = press(site, fields={"what": "record:coldStart"})
    page = read(reply.body)
    check("a page of its own, not one about a page that could not be drawn", reply.status == 500
          and page.one("h1").text == "That did not work", page.one("h1").text)
    check("with the error as text, and where to see whether anything started", page.one("p", "lead").text == (
        "echolot ran into an error: RuntimeError: the disk is <full>. The start page shows whether a check is running.")
        and page.one("a", "btn").attrs["href"] == f"/{site.token}/")
    err = capsys.readouterr().err
    check("the terminal hears of it in one line, without the address", err.strip()
          == "echolot ui: could not answer `check`: RuntimeError: the disk is <full>" and site.token not in err, err)
    check("and the server goes on", page_of(site).all("button", id="check-go"))


# --- what the server refuses --------------------------------------------------------------------

def noting(started: list):
    """A press that is taken starts this: it notes the plan, and leaves a run that ended at once."""
    def spawn(root, name, plan):
        started.append(plan)
        job.run(root, name, {"steps": [], "what": plan["what"]}, signals=False)
    return spawn


def send(server, path: str, body: bytes = b"what=record%3AcoldStart", host: str | None = "", length: str | None = None,
         **headers: str):
    """A POST as a browser sends a form, with whatever is changed for the test."""
    conn = http.client.HTTPConnection(ui.HOST, server.site.port, timeout=10)
    try:
        conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host or server.site.host)
        sent = {"Content-Type": ui.FORM, "Content-Length": str(len(body)) if length is None else length, **headers}
        for name, value in sent.items():
            if value != "":
                conn.putheader(name.replace("_", "-"), value)
        conn.endheaders()
        conn.send(body)
        reply = conn.getresponse()
        return reply.status, dict(reply.getheaders()), reply.read().decode("utf-8")
    finally:
        conn.close()


def test_a_press_is_refused_without_the_token_from_another_origin_or_under_another_name(project):
    project.ready()
    started = []
    with running(project) as server:
        site = server.site
        site.spawn = noting(started)
        before = files(project.root)
        own = {"Origin": site.origin}
        for name, path, how, expected in (
                ("no token", "/check", own, 403),
                ("a wrong token", "/nope/check", own, 403),
                ("the token in the body and not in the address", "/check", own, 403),
                ("no origin", f"/{site.token}/check", {}, 403),
                ("another origin", f"/{site.token}/check", {"Origin": "https://evil.example"}, 403),
                ("the origin `null`", f"/{site.token}/check", {"Origin": "null"}, 403),
                ("its own origin, sent from another site", f"/{site.token}/check", {**own, "Sec_Fetch_Site": "cross-site"}, 403),
                ("another name for this machine", f"/{site.token}/check", {**own, "host": f"localhost:{site.port}"}, 403),
                ("no name at all", f"/{site.token}/check", {**own, "host": None}, 403),
                ("the same for Stop", f"/{site.token}/check/stop", {"Origin": "https://evil.example"}, 403)):
            how = dict(how)
            host = how.pop("host", "")
            status, _, body = send(server, path, b"what=record%3AcoldStart&token=" + site.token.encode(), host=host, **how)
            check(f"{name}: {expected}", status == expected, (status, body[:120]))
            check(f"{name}: told nothing", "<" not in body and site.token not in body and "coldStart" not in body, body)
        check("nothing started, nothing written", not started and files(project.root) == before, started)
        status, headers, _ = send(server, f"/{site.token}/check", **own, Sec_Fetch_Site="same-origin")
        check("and the server's own page is taken", status == 303 and headers["Location"] == f"/{site.token}/" and len(started) == 1)


def test_a_body_over_the_cap_is_refused_unread(project):
    project.ready()
    started = []
    with running(project) as server:
        site = server.site
        site.spawn = noting(started)
        own = {"Origin": site.origin}
        big = b"what=record%3AcoldStart&pad=" + b"x" * ui.BODY_CAP
        status, _, body = send(server, f"/{site.token}/check", big, **own)
        check("over the cap: refused, in a sentence", status == 413 and "bytes at most" in body and "<" not in body, (status, body))
        status, _, _ = send(server, f"/{site.token}/check", b"", length=str(10 ** 9), **own)
        check("a length that is only claimed is refused before a byte is read", status == 413)
        status, _, _ = send(server, f"/{site.token}/check", b"what=x", length="many", **own)
        check("a length that is no number", status == 413)
        status, _, _ = send(server, f"/{site.token}/check", b"what=x", length="-1", **own)
        check("or less than none", status == 413)
        status, _, _ = send(server, f"/{site.token}/check", b"0\r\n\r\n", length="", Transfer_Encoding="chunked", **own)
        check("a body of no stated length", status == 413)
        status, _, _ = send(server, f"/{site.token}/check", big, Origin="https://evil.example")
        check("a stranger hears the same as with any body", status == 403)
        with socket.create_connection((ui.HOST, site.port), timeout=10) as raw:
            # Half a form: it says 40 bytes and ends after 22. "launches=12" cut to "launches=1" would be a form too.
            raw.sendall((f"POST /{site.token}/check HTTP/1.0\r\nHost: {site.host}\r\nOrigin: {site.origin}\r\n"
                         f"Content-Type: {ui.FORM}\r\nContent-Length: 40\r\n\r\nwhat=record%3AcoldStart").encode())
            raw.shutdown(socket.SHUT_WR)
            answered = raw.makefile("rb").read().decode()
        check("a body that ends before the length it stated is no body", answered.startswith("HTTP/1.0 413")
              and "and carry them" in answered and not started, answered[:200])
        exact = b"what=record%3AcoldStart&pad=" + b"x" * (ui.BODY_CAP - 28)
        status, _, _ = send(server, f"/{site.token}/check", exact, **own)
        check("at the cap it is read", len(exact) == ui.BODY_CAP and status == 303 and len(started) == 1, status)
        check("nothing else started", len(started) == 1)


def test_a_body_that_is_no_form(project):
    site, echolot = serving(project.ready())
    many = urlencode([("f", str(i)) for i in range(ui.FIELDS + 1)]).encode()
    sent = {"Host": site.host, "Origin": site.origin, "Content-Type": ui.FORM}
    check("more fields than a press has", ui.answer(site, "POST", f"/{site.token}/check", sent, many).status == 400)
    check("bytes that are no text", ui.answer(site, "POST", f"/{site.token}/check", sent, b"what=\xff\xfe").status == 400)
    check("nothing started", not echolot.ran)
    reply = ui.answer(site, "POST", f"/{site.token}/check", {**sent, "Content-Type": "application/json"}, b'{"what": "read:x"}')
    check("a body of another kind has no fields: the page's own scenario is checked", reply.status == 303
          and echolot.ran[0][0] == "collect")


def test_the_addresses_of_a_press_take_no_read_and_the_others_no_press(project):
    site, echolot = serving(project.ready())
    for path in ("check", "check/stop"):
        reply = get(site, path)
        check(f"{path}: a read of it starts nothing", reply.status == 405 and dict(reply.headers)["Allow"] == "POST")
    check("how far a run is can only be read", press(site, "check/state").status == 405)
    check("nothing started", not echolot.ran and job.read(project.root, "check") is None)


def test_reading_writes_nothing_with_a_run_on_show(project):
    a_run(project.ready().traces().report(), "collect", done=1)
    site = site_of(project)
    before = files(project.root)
    for path in ("", "check/state", "report", "answer", "nowhere"):
        get(site, path)
    check("every file is as it was", files(project.root) == before)


def test_without_the_addresses_of_a_press_the_page_reads_only(project, monkeypatch):
    project.ready()
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("check")])
    page = page_of(site_of(project))
    check("no button, no form, no field", not page.all("form") and not page.all("button", id="check-go") and not page.all("input"))
    check("the step is a command to copy again", page.one("code", id="next-cmd").text == "echolot collect -c echolot.yml -n 5")


# --- the edges ----------------------------------------------------------------------------------

def test_files_written_by_a_newer_echolot(project, monkeypatch):
    project.ready()
    monkeypatch.setattr(state, "next_kind", lambda st: "upgrade")
    check("the button waits for the upgrade", blocked(project)
          == "Not yet: this project's files were written by a newer echolot. Upgrade it first.")


def test_a_recording_length_that_is_no_number_still_draws_the_page(project):
    project.app().config(LAUNCH + "\nrunner:\n  duration_ms: soon\n").agents().doctor()
    check("the line counts with the usual length; collect will say what is wrong with the number",
          hint(page_of(site_of(project))) == "3 launches, about a minute. Nothing in your code changes.")


def test_a_report_that_cannot_be_kept_starts_nothing(project):
    project.ready().traces().report()
    (project.root / ".echolot" / "out" / "before.json").mkdir()
    site, echolot = serving(project)
    reply = press(site, fields={"what": "record:coldStart"})
    check("said, and nothing recorded over a report that has no copy", reply.status == 500
          and "could not be kept for the comparison" in reply.body and not echolot.ran, reply.body[:300])


def test_between_two_commands_a_run_is_still_a_run(project):
    run = a_run(project.ready(), "analyze", done=3)
    run["steps"][1].update(status="waiting")
    (project.root / ".echolot" / "log" / "check.json").write_text(json.dumps(run), encoding="utf-8")
    site = site_of(project)
    page = page_of(site)
    check("running, with nothing in progress to name", page.one("p", id="run-said").text == "A check of coldStart is running."
          and rows(page)[-1] == ("wait", "Reading the recordings", ""), page.one("p", id="run-said").text)


def test_a_comparison_that_left_nothing_to_quote_is_not_quoted(project):
    project.ready().traces().report()
    a_run(project, "compare", done=3, compare=True, end="done")
    page = page_of(site_of(project))
    check("done, and no sentence of compare's is made up", page.one("p", id="run-end").text == "Done. The short version:"
          and not page.all("p", id="run-compare"))
    (project.root / ".echolot" / "out" / "comparison.md").write_text("# Comparison\n\nMoved **1** · steady 5\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(project.root / ".echolot" / "out" / "comparison.md", (old, old))
    check("nor is one that an earlier comparison left there", not page_of(site_of(project)).all("p", id="run-compare"))


def test_a_reading_stopped_before_it_began_and_an_end_nobody_explains(project):
    run = a_run(project.ready().traces(), "analyze", do="read", end="stopped")
    run["steps"][0] = {**run["steps"][0], "status": "waiting"}
    path = project.root / ".echolot" / "log" / "check.json"
    path.write_text(json.dumps(run), encoding="utf-8")
    check("stopped before anything", page_of(site_of(project)).one("p", id="run-end").text == "Stopped. Nothing was read.")
    run.update(end="failed")
    path.write_text(json.dumps(run), encoding="utf-8")
    check("a failure with no command to blame is still said", page_of(site_of(project)).one("p", id="run-end").text
          == "The check did not finish.")


def test_the_answers_page_without_a_button(project, demo_report, monkeypatch):
    hunt, reports = fx.states(demo_report)["many"]
    project.ready().traces().report().investigation({**hunt, "scenario": "another"}, reports)
    site = site_of(project)
    check("an investigation of a scenario the config no longer names gets no button",
          not read(get(site, "answer").body).one("div", id="check-again-action").kids)
    project.investigation(hunt, reports)
    monkeypatch.setattr(ui, "ROUTES", [r for r in ui.ROUTES if not r.name.startswith("check")])
    check("nor does any, on a server that takes no press", not read(get(site, "answer").body).one("div", id="check-again-action").kids)
    check("an address that needs a name is none without it", site.url("report-of") is None)


# --- a file with a field of the wrong type -----------------------------------------------------

def test_a_number_of_launches_in_the_config_that_is_no_number(project, demo_report):
    """`runner.iterations: five` loads. The start page's button names its own
    number; the answer's button runs `collect` with the config's, and `collect`
    writes "five" into its progress file before it stops and says so."""
    hunt, reports = fx.states(demo_report)["many"]
    project.app().config(LAUNCH + "\nrunner:\n  iterations: five\n").agents().doctor().traces().report()
    project.investigation(hunt, reports)
    site = site_of(project)
    check("the start page counts with its own three", hint(page_of(site)).startswith("3 launches, about a minute"))
    reply = get(site, "answer")
    place = read(reply.body).one("div", id="check-again-action")
    check("the answer's page is drawn, and its button's line claims no number", reply.status == 200 and place.one("p").text
          == "As many launches as runner.iterations in the config says. Nothing in your code changes.", reply.body[:200])
    run = a_run(project, "collect", launches=None, end="failed")
    log = project.root / ".echolot" / "log"
    (log / "collect.json").write_text(json.dumps({
        "scenario": "coldStart", "mode": "launch", "started": run["steps"][0]["started"] + 1, "pid": 7000,
        "iterations": "five", "done": 0, "finished": time.time() - 2, "exit": 2,
        "error": "-n / runner.iterations: 'five' is not a number of repeats."}), encoding="utf-8")
    (log / "check.collect.log").write_text(
        "collection error: -n / runner.iterations: 'five' is not a number of repeats. One or more, in digits.\n", encoding="utf-8")
    page = page_of(site)
    check("the run that collect stopped is shown: one row for the recording, no launches made up", rows(page) == [
        ("failed", "The recording", "did not finish"), ("wait", "Reading the recordings", "")], rows(page))
    check("with what collect said of the number", "'five' is not a number of repeats" in page.one("pre", "printed").raw())
    check("and the page that asks how far a run is answers too", get(site, "check/state").status == 200)


@pytest.mark.parametrize("change", [{"started": "yesterday"}, {"done": "one"}, {"pid": 0}, {"iterations": "five"}, None],
                         ids=["a time that is text", "a count that is text", "a pid that is none", "launches that are text",
                              "no object"])
def test_a_progress_file_that_is_none_ticks_nothing_off(project, change):
    a_run(project.ready(), "collect", done=1)
    path = project.root / ".echolot" / "log" / "collect.json"
    good = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**good, **change}) if change is not None else "[1, 2]", encoding="utf-8")
    site = site_of(project)
    page = page_of(site)
    told = change == {"iterations": "five"}
    check("the run is drawn all the same, three launches as the press asked", [r[1] for r in rows(page)][:3] == [
        "Launch 1 of 3", "Launch 2 of 3", "Launch 3 of 3"], rows(page))
    check("a file that says nothing of a collect ticks nothing off", rows(page)[0][0] == ("done" if told else "now"), rows(page))
    check("and the page that asks how far a run is answers", get(site, "check/state").status == 200)


@pytest.mark.parametrize("change", [{"steps": [5]}, {"started": "now"}, {"pid": 0}, {"what": []},
                                    {"finished": 1.0, "end": "done", "what": {"report": 5}}],
                         ids=["a step that is none", "a time that is text", "a pid that is none", "no object in what",
                              "a report that is a number"])
def test_a_run_file_that_is_none_is_no_run(project, change):
    run = a_run(project.ready(), "collect", done=1)
    if "finished" in change:
        change = {**change, "finished": time.time()}        # it ended a moment ago: it is still news
    (project.root / ".echolot" / "log" / "check.json").write_text(json.dumps({**run, **change}), encoding="utf-8")
    (project.root / ".echolot" / "log" / "collect.json").unlink()
    site, echolot = serving(project)
    page = page_of(site)
    check("the page is drawn, with its button", button(page).text == "Check the app", get(site).status)
    check("nothing reads as a run that goes on for ever", not [b for b in page.all("button") if b.text == "Stop"])
    check("and a press starts a check over it", press(site, fields={"what": "record:coldStart"}).status == 303 and echolot.ran)
