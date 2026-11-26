"""Where a report's findings are on the timeline, and the links that open them (#132).

Pinned here, beside the checks `doctor` runs (`timeline:` in selftest.py):

  - a detector's fourth query, `@longest`, comes off its file, renders with
    the first query's values and leaves its `sha` as it was;
  - the moments a report can open, in a fixed order, and what a link is made
    of: the zoom, the thread pinned by its track's title, nothing of this
    machine;
  - report.md's links, and a page that names no address outside itself:
    on disk the moments are words, served they lead to `echolot ui`'s own
    timeline page;
  - that page: Perfetto UI in a frame its policy allows and nothing else,
    the trace's bytes asked for by the report and the moment, never by a
    path, and a file that is no Perfetto trace not sent.
"""

from __future__ import annotations

import copy
import http.client
import json
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_report_page import SCHEME, outside, read  # noqa: E402
from tests.test_ui import LAUNCH, Project, adb, get, running, site_of  # noqa: E402,F401

from echolot import fixture, render, timeline, ui  # noqa: E402
from echolot.pages import md, report_page, timeline_page  # noqa: E402
from echolot.tp import Detector  # noqa: E402

TRACE = ".echolot/traces/coldStart_iter000.perfetto-trace"


# --- the fourth query -----------------------------------------------------

DETECTOR = """\
-- @id: demo
-- @title: Demo
-- @param: min_ms = 16

SELECT name AS location, COUNT(*) AS count, ROUND(MAX(dur) / 1e6, 2) AS max_ms
FROM _slice_win
GROUP BY name
HAVING SUM(dur) >= {{min_ms}} * 1000000;

-- @longest
--
-- the longest one; with a semicolon; in a comment

SELECT location, ts, dur, tid
FROM (SELECT r.location, s.ts, s.dur, s.tid,
             ROW_NUMBER() OVER (PARTITION BY r.location ORDER BY s.dur DESC) AS k
      FROM _slice_win s JOIN _rows r ON r.location = s.name
      WHERE s.dur >= {{min_ms}} * 1000000)
WHERE k = 1;
"""


def test_the_fourth_query_comes_off_the_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.sql"
    path.write_text(DETECTOR, encoding="utf-8")
    d = Detector.from_file(path)
    check("the first query is the first", "@longest" not in d.sql and "HAVING" in d.sql, d.sql)
    check("the fourth is kept", "ROW_NUMBER()" in d.longest_sql, d.longest_sql)
    rendered = d.render_longest({"min_ms": 32})
    check("it renders with the first query's values", rendered is not None and ">= 32 * 1000000" in rendered,
          rendered)
    check("and leaves the detector's sha as it was", replace(d, longest_sql="").sha == d.sha)
    path.write_text(DETECTOR.split("-- @longest")[0], encoding="utf-8")
    check("a detector without one renders none", Detector.from_file(path).render_longest({"min_ms": 16}) is None)


# --- the moments and their links -------------------------------------------

def _report(**window) -> dict:
    """A set of two repeats: the window, a row with a place in the second, a
    row placed in a repeat the set does not have, and one placed nowhere."""
    return {
        "trace": "sets/a/run0.perfetto-trace",
        "traces": ["sets/a/run0.perfetto-trace", "sets/a/run1.perfetto-trace"],
        "window": {"ts_start": 1_000, "ts_end": 9_000, "pid": 42, "main_thread_name": "example.app",
                   **window},
        "detectors": [
            {"id": "main_thread_block", "rows": [
                {"location": "inflate", "max_ms": 0.002,
                 "at": {"ts": 2_000, "dur": 2_000, "tid": 42, "thread": "example.app", "run": 1}},
                {"location": "elsewhere", "at": {"ts": 2_000, "dur": 1, "run": 5}},
                {"location": "nowhere"},
            ]},
            {"id": "frame_jank", "rows": [
                {"location": "App Deadline Missed", "at": {"ts": 3_000, "dur": 500, "run": 0}}]},
        ],
    }


def test_the_moments_are_the_window_then_each_detector_s_rows_in_order() -> None:
    found = timeline.moments(_report())
    check("the window first, of the first repeat, then each placed row in the repeat it names",
          [(m.detector, m.location, m.trace) for m in found] == [
              (None, None, "sets/a/run0.perfetto-trace"),
              ("main_thread_block", "inflate", "sets/a/run1.perfetto-trace"),
              ("frame_jank", "App Deadline Missed", "sets/a/run0.perfetto-trace")], found)
    check("the window is its stretch and its main thread",
          (found[0].ts, found[0].dur, found[0].thread, found[0].tid) == (1_000, 8_000, "example.app", 42), found[0])
    check("a window that is no stretch is no moment",
          [m.detector for m in timeline.moments(_report(ts_end=1_000))] == ["main_thread_block", "frame_jank"])
    many = _report()
    many["detectors"][0]["rows"] = [{"location": f"r{i}", "at": {"ts": i + 1, "dur": 1}} for i in range(9)]
    check("a detector's first rows, as many as a view shows",
          len(timeline.of_detector(timeline.moments(many), "main_thread_block")) == timeline.PER_DETECTOR)


def test_a_place_that_is_not_one_is_left_out() -> None:
    rep = _report()
    for bad in ({"ts": "2000", "dur": 5}, {"ts": 2_000, "dur": 0}, {"ts": True, "dur": 5}, "here",
                {"ts": 2_000, "dur": 5, "run": -1}):
        rep["detectors"][1]["rows"][0]["at"] = bad
        check(f"{bad!r} is no moment", not timeline.of_detector(timeline.moments(rep), "frame_jank"))
    one = {"trace": "one.perfetto-trace", "window": {"ts_start": 1, "ts_end": 2}, "detectors": []}
    check("one recording is the report's trace", [m.trace for m in timeline.moments(one)] == ["one.perfetto-trace"])


def test_a_link_zooms_to_the_moment_and_pins_its_thread() -> None:
    m = timeline.moments(_report())[1]
    link = timeline.url(m)
    head, _, cmds = link.partition("&startupCommands=")
    check("Perfetto UI, zoomed to the occurrence", head == f"{timeline.UI}/#!/?visStart=2000&visEnd=4000", link)
    check("and the thread pinned by its track's title, dots and all",
          json.loads(unquote(cmds)) == [{"id": "dev.perfetto.PinTracksByRegex", "args": [r"^example\.app 42$"]}],
          unquote(cmds))
    check("nothing in it a browser could read as the end of a Markdown link", ")" not in link and " " not in link)
    check("a moment on no thread is the zoom alone", "startupCommands" not in timeline.url(timeline.moments(_report())[2]))
    odd = replace(m, thread="поток")
    check("a thread whose name is not plain ASCII is not guessed at", timeline.commands(odd) == [])
    special = replace(m, thread="a+b (c)|[d]")
    check("what a regular expression reads as more than itself is escaped",
          timeline.commands(special)[0]["args"] == [r"^a\+b \(c\)\|\[d\] 42$"])


def test_report_md_says_where_to_look_with_the_same_text_for_the_same_set(marker_report) -> None:
    rep = copy.deepcopy(marker_report)
    text = render.to_markdown(rep)
    check("the same bytes twice", text == render.to_markdown(copy.deepcopy(marker_report)))
    lines = [line for line in text.splitlines() if timeline.UI in line]
    check("the window's line first, and how a link is used, once", lines[0].startswith("Timeline: [the scenario window")
          and text.count(timeline.HOW) == 1, lines[:1])
    check("a line under each detector with places", all(line.startswith(("Timeline: ", "On the timeline: "))
                                                         for line in lines), lines)
    check("no address of this machine", "127.0.0.1" not in text and "localhost" not in text)
    without = copy.deepcopy(marker_report)
    for det in without["detectors"]:
        for row in det["rows"]:
            row.pop("at", None)
    check("a report from before #132 has the window's line alone",
          sum(timeline.UI in line for line in render.to_markdown(without).splitlines()) == 1)


# --- the pages ---------------------------------------------------------------

def test_a_link_in_report_md_is_its_words_on_a_page_unless_the_server_has_a_page_for_it() -> None:
    line = f"see [the longest of `x`, 1.0 ms, in `run`]({timeline.UI}/#!/?visStart=1&visEnd=2) here"
    check("on disk: the words, no address", md.inline(line) == "see the longest of <code>x</code>, 1.0 ms, in "
          "<code>run</code> here", md.inline(line))
    served = md.inline(line, {f"{timeline.UI}/#!/?visStart=1&visEnd=2": "/t/timeline/3"})
    check("served: the words lead to the server's page", 'href="/t/timeline/3"' in served
          and "ui.perfetto.dev" not in served, served)
    other = "[a page](https://example.com/x)"
    check("a link to anywhere else is left as the text it is", md.inline(other) == md.inline(other, {}) and
          "href" not in md.inline(other), md.inline(other))


def test_a_served_report_page_links_its_moments_to_the_timeline_page(marker_report) -> None:
    rep = copy.deepcopy(marker_report)
    count = len(timeline.moments(rep))
    page = report_page.render(rep, timeline=lambda i: f"/t/timeline/{i}")
    hrefs = {a.attrs["href"] for a in read(page).all("a") if a.attrs.get("href", "").startswith("/t/")}
    check("every moment has its link", hrefs == {f"/t/timeline/{i}" for i in range(count)}, sorted(hrefs))
    check("and nothing else leaves the page", not SCHEME.search(page), SCHEME.findall(page)[:3])
    none = report_page.render(rep, timeline=lambda i: None)
    check("a moment whose recording is not there has no link", "/timeline/" not in none and not outside(none))


def test_the_timeline_page_frames_perfetto_ui_and_nothing_else(marker_report) -> None:
    m = timeline.moments(marker_report)[1]
    page = timeline_page.render(m, "/t/timeline/1/trace")
    frame = read(page).one("iframe")
    check("Perfetto UI at the moment, in a frame", frame.attrs["src"] == f"{timeline.UI}/#!/?{timeline.fragment(m)}",
          frame.attrs)
    check("the trace asked of this server", frame.attrs["data-trace"] == "/t/timeline/1/trace", frame.attrs)
    policy = read(page).one("meta", http_equiv="Content-Security-Policy").attrs["content"]
    check("its policy frames Perfetto UI and nothing more than a served page's",
          policy == f"{timeline_page.POLICY}" and "frame-src https://ui.perfetto.dev" in policy
          and policy.count("http") == 1, policy)


# --- echolot ui -----------------------------------------------------------------

@pytest.fixture
def placed(tmp_path, monkeypatch, adb, marker_report):  # noqa: F811
    """A project whose last report is the fixture's, its recording where the
    report names it, by a path from the project's root."""
    monkeypatch.chdir(tmp_path)
    project = Project(tmp_path).ready()
    trace = tmp_path / TRACE
    trace.parent.mkdir(parents=True)
    trace.write_bytes(fixture.build())
    rep = copy.deepcopy(marker_report)
    rep["trace"] = TRACE
    project.report(data=rep)
    return project


def test_the_report_s_page_opens_each_moment_on_the_timeline_page(placed) -> None:
    site = site_of(placed)
    page = read(get(site, "report").body)
    hrefs = sorted({a.attrs["href"] for a in page.all("a") if "/timeline/" in a.attrs.get("href", "")})
    check("a link for each moment, to this server", hrefs and all(h.startswith(f"/{site.token}/timeline/")
                                                                  for h in hrefs), hrefs)
    reply = get(site, "timeline/1")
    check("the page opens", reply.status == 200, reply.body[:300])
    policy = dict(reply.headers)["Content-Security-Policy"]
    check("sent with the timeline's policy, framed by nobody", policy == timeline_page.POLICY + "; frame-ancestors 'none'",
          policy)
    frame = read(reply.body).one("iframe")
    check("its trace asked of the same address", frame.attrs["data-trace"] == f"/{site.token}/timeline/1/trace",
          frame.attrs)


def test_the_trace_is_sent_by_the_report_and_the_moment_and_nothing_else(placed) -> None:
    with running(placed) as server:
        conn = http.client.HTTPConnection(ui.HOST, server.site.port, timeout=10)
        try:
            conn.request("GET", f"/{server.site.token}/timeline/1/trace", headers={"Host": server.site.host})
            reply = conn.getresponse()
            body = reply.read()
        finally:
            conn.close()
    sent = (placed.root / TRACE).read_bytes()
    check("the recording as it is", reply.status == 200 and body == sent, (reply.status, len(body)))
    check("as bytes, of its length", reply.getheader("Content-Type") == "application/octet-stream"
          and reply.getheader("Content-Length") == str(len(sent)), reply.getheaders())
    check("kept nowhere, sent to this server's pages alone", reply.getheader("Cache-Control") == "no-store"
          and reply.getheader("Cross-Origin-Resource-Policy") == "same-origin", reply.getheaders())


def test_a_moment_that_is_not_there_or_a_file_that_is_no_trace_is_a_page_that_says_so(placed) -> None:
    site = site_of(placed)
    for path in ("timeline/999", "timeline/999/trace", "timeline/nowhere/1", "hunt/9/report/1/timeline/0",
                 "elsewhere/timeline/0/trace"):
        reply = get(site, path)
        check(f"{path}: no such thing", reply.status == 404 and reply.file is None, (reply.status, reply.body[:200]))
    (placed.root / TRACE).write_text("echolot.yml is no trace", encoding="utf-8")
    for path in ("timeline/1", "timeline/1/trace"):
        reply = get(site, path)
        check(f"{path}: a file that does not begin as a Perfetto trace is not sent",
              reply.status == 404 and reply.file is None and "The recording is not here" in reply.body,
              (reply.status, reply.body[:200]))
    page = read(get(site, "report").body)
    check("and the report's page has no link to it",
          not [a for a in page.all("a") if "/timeline/" in a.attrs.get("href", "")])
