#!/usr/bin/env python3
"""The report as a page: `echolot analyze --html`, `echolot report --html`.

`echolot/pages/report_page.py` writes a report for a person as one HTML file.
What is pinned here:

  - the file is its own: nothing in it names an address, and a name out of a
    trace cannot become markup;
  - the same report gives the same bytes;
  - every word about a check is the report's (`plain`, `attention`, `checked`,
    `reason`, `verdict`), and the page works no level out by itself;
  - each state a page can be in: every level, a check with no rows, a report
    with no budget, one from before the plain keys, a detector that failed,
    many rows, a long name, one recording;
  - the technical report under the fold holds what report.md holds;
  - the request for an agent is put together from the rows of what is ticked.

What the page does when it is clicked is script, and is looked at in a browser:
here the script is only held to parsing, where node is there to parse it.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import echolot  # noqa: E402
from echolot import demo, plain, recorder, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import kit, md, report_page  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(echolot.__file__).resolve().parent / "pages"
MAIN = "com.example.app"


# --- a report by hand -------------------------------------------------------------------

def row(location, total, runs="3/3", **kw):
    out = {"location": location, "runs": runs, "count": kw.pop("count", 1), "total_ms": total}
    out.update(kw)
    return out


def spread(*values):
    return {"total_ms": {"min": min(values), "max": max(values), "values": list(values)}}


def entry(did, rows, **extra):
    """An entry as `analyze` writes it."""
    return {"id": did, "title": f"title of {did}", "why": "", "params": {}, "params_source": "default",
            "identity": ["location"], "rows": rows, "error": None, "checked": True, **extra}


def make(detectors, duration=2000.0, budget=None, runs=3, startup=True, **extra):
    window = {"process": MAIN, "pid": 4100, "duration_ms": duration}
    if startup:
        window["startup"] = {"type": "cold", "dur_ms": duration}
    if runs and runs > 1:
        window |= {"duration_ms_min": duration - 20, "duration_ms_max": duration + 60}
    fired = [d["id"] for d in detectors if d["rows"]]
    rep = {"schema": 1, "trace": "t.perfetto-trace", "scenario": "coldStart", "budget_ms": budget,
           "window": window, "detectors": detectors,
           "summary": {"detectors_run": len(detectors), "detectors_fired": len(fired),
                       "fired_ids": fired, "absent_ids": []}}
    if runs:
        rep["runs"] = runs
        rep["traces"] = [f"t{i}.perfetto-trace" for i in range(runs)]
    return rep


WAITS = [row("worker_pool_#", 700.0, count=259, max_ms=15.2, detail="idle · 71% blocked in loadConfig",
             spread=spread(690.0, 700.0, 721.0))]
CPU = [row("worker_pool_0", 500.0, detail="99.0% of CPU outside slices", spread=spread(480.0, 500.0, 530.0)),
       row("Sync Worker", 300.0, runs="2/3", detail="97.0% of CPU outside slices", spread=spread(290.0, 310.0))]


def known(**kw):
    """A launch of 1.9 s against 0.5 s: one red, two yellow, two fine, one that could not look."""
    return plain.annotate(make([
        entry("gc_pressure", []),
        entry("io_wait", [], checked=False,
              reason="These recordings do not carry the phone's state. Record with it on."),
        entry("binder_txn", [row("Sync Worker", 41.0, count=15, max_ms=18.0, detail="background",
                                 spread=spread(38.0, 41.0, 47.0))],
              params={"min_txn_ms": 10, "max_total_ms": 50, "name_glob": "binder transaction*"}),
        entry("uninstrumented_cpu", copy.deepcopy(CPU), params={"min_running_ms": 50, "max_covered_pct": 50}),
        entry("monitor_contention", []),
        entry("main_thread_waits", copy.deepcopy(WAITS), params={"min_total_ms": 16, "lock_glob": "*contention*"}),
    ], duration=1920.0, budget=500.0, **kw))


# --- the page, read back ------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids = tag, dict(attrs), parent, []

    def walk(self):
        for kid in self.kids:
            if isinstance(kid, Node):
                yield kid
                yield from kid.walk()

    def all(self, tag=None, cls=None, **attrs):
        return [n for n in self.walk()
                if (tag is None or n.tag == tag)
                and (cls is None or cls in (n.attrs.get("class") or "").split())
                and all(n.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())]

    def one(self, tag=None, cls=None, **attrs):
        found = self.all(tag, cls, **attrs)
        assert len(found) == 1, f"{len(found)} of <{tag} class={cls} {attrs}>"
        return found[0]

    def raw(self):
        return "".join(kid if isinstance(kid, str) else kid.raw() for kid in self.kids)

    @property
    def text(self):
        return re.sub(r"\s+", " ", self.raw()).strip()

    def inside(self, tag):
        p = self.parent
        while p is not None:
            if p.tag == tag:
                return True
            p = p.parent
        return False


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.at = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.kids.append(node)
        if tag not in VOID:
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.kids.append(Node(tag, attrs, self.at))

    def handle_endtag(self, tag):
        at = self.at
        while at is not None and at.tag != tag:
            at = at.parent
        assert at is not None, f"</{tag}> closes nothing"
        assert at is self.at, f"</{tag}> closes <{self.at.tag}> first"
        self.at = at.parent

    def handle_data(self, data):
        self.at.kids.append(data)


def read(page: str) -> Node:
    reader = _Reader()
    reader.feed(page)
    reader.close()
    assert reader.at is reader.root, f"<{reader.at.tag}> is left open"
    return reader.root


def page_of(report, **kw) -> Node:
    return read(report_page.render(report, **kw))


def rows_of(page: Node) -> dict[str, Node]:
    return {r.attrs["data-id"]: r for r in page.all("div", "tr")}


def panel_of(page: Node, did: str) -> Node:
    return page.one("template", id=f"peek-{did}")


@pytest.fixture(scope="module")
def fixture_report():
    """The self-check's trace, one recording: a problem planted for every detector,
    and a report as `analyze_trace` leaves it, before the plain reading."""
    with recorder.isolated():
        return selftest.build_report()


# --- one file, nothing fetched ------------------------------------------------------------

# Whatever could make a browser ask for something: an element that loads, an
# attribute that names where from, a style or a script that reaches out.
LOADING_TAGS = {"link", "img", "iframe", "frame", "object", "embed", "video", "audio", "source", "track",
                "picture", "base", "form", "image", "use", "feimage", "applet", "portal"}
ADDRESS_ATTRS = {"src", "srcset", "href", "xlink:href", "action", "formaction", "poster", "data", "background",
                 "cite", "ping", "manifest", "codebase", "longdesc", "usemap", "xmlns", "xmlns:xlink"}
REACHING_OUT = ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import(", "importScripts",
                "new Image", "window.open", ".src", "location.href", "location.assign", "location.replace",
                "serviceWorker", "document.write", "eval(", "new Function", "innerHTML", "insertAdjacentHTML")
IN_A_STYLE = ("url(", "@import", "@font-face", "image-set(", "expression(", "@namespace")
# An address by its scheme. `--data:` is a colour token, not one.
SCHEME = re.compile(r"(?i)(?<![-\w])(?:https?|ftp|wss?|data|blob|file|javascript|mailto):")


def outside(page_text: str) -> list[str]:
    """Every way the file points outside itself. Empty for a page that is its own."""
    page = read(page_text)
    found = []
    for n in page.walk():
        if n.tag in LOADING_TAGS:
            found.append(f"<{n.tag}>")
        for name, value in n.attrs.items():
            if name in ADDRESS_ATTRS and not (name == "href" and (value or "").startswith("#")):
                found.append(f"<{n.tag} {name}={value!r}>")
            if name.startswith("on"):
                found.append(f"<{n.tag} {name}>: a handler in the markup")
            if name == "style" and any(word in (value or "") for word in IN_A_STYLE):
                found.append(f"<{n.tag} style={value!r}>")
            if n.tag == "meta" and name == "http-equiv" and (value or "").lower() == "refresh":
                found.append("<meta http-equiv=refresh>")
    for style in page.all("style"):
        found += [f"style: {word}" for word in IN_A_STYLE if word in style.text]
        found += [f"style: {m.group(0)}" for m in SCHEME.finditer(style.text)]
    for script in page.all("script"):
        found += [f"script: {word}" for word in REACHING_OUT if word in script.text]
        found += [f"script: {m.group(0)}" for m in SCHEME.finditer(script.text)]
    return found


def test_the_page_names_no_address_of_any_kind(demo_report, fixture_report):
    for name, rep in (("the demo", demo_report), ("the fixture", fixture_report), ("by hand", known())):
        text = report_page.render(rep, where=".echolot/out/report.json")
        check(f"{name}: nothing loads and nothing reaches out", not outside(text), outside(text))
        check(f"{name}: no address anywhere in the file, as text either",
              not SCHEME.search(text) and "//www." not in text, SCHEME.findall(text)[:5])
        page = read(text)
        check(f"{name}: two scripts, both written into the file",
              [s.attrs for s in page.all("script")] == [{}, {}], [s.attrs for s in page.all("script")])
        check(f"{name}: one stylesheet, written into the file", len(page.all("style")) == 1)


def test_the_browser_is_told_to_hold_the_page_to_it():
    page = page_of(known())
    policy = page.one("meta", http_equiv="Content-Security-Policy").attrs["content"]
    check("nothing may be fetched", "default-src 'none'" in policy, policy)
    check("only what is written into the file runs", "http" not in policy and "*" not in policy, policy)
    check("it cannot be pointed elsewhere", "base-uri 'none'" in policy and "form-action 'none'" in policy, policy)


NASTY = '"><script src=https://example.com/x.js></script><img src=x onerror=alert(1)>`</template>&amp;'


def test_a_name_out_of_a_trace_cannot_become_markup():
    """A slice name is an arbitrary string chosen by whoever wrote the trace{} call."""
    rep = make([
        entry("main_thread_waits", [row(NASTY, 700.0, detail=NASTY, code=NASTY, spread=spread(690.0, 700.0, 710.0))],
              title=NASTY, params={"min_total_ms": 16}),
        entry("io_wait", [], checked=False, reason=NASTY),
        entry("anr", [], error=NASTY, checked=True),
        entry(NASTY, [row("x", 20.0)]),
    ], budget=500.0)
    rep["scenario"] = NASTY
    rep["window"]["process"] = NASTY
    rep = plain.annotate(rep)
    rep["detectors"][0]["plain"] |= {"title": NASTY, "why": NASTY, "found": f"It waited for `{NASTY}`."}
    text = report_page.render(rep, where=NASTY)
    page = read(text)
    check("no element came out of a name", not outside(text.replace("https:", "")), outside(text))
    check("still two scripts and no image", len(page.all("script")) == 2 and not page.all("img"))
    check("and no template was closed early", len(page.all("template")) == 4, len(page.all("template")))
    rows = rows_of(page)
    check("the name is there as text, whole",
          NASTY in rows["main_thread_waits"].one("h3").text and NASTY in rows["main_thread_waits"].one("div", "c-w").text)
    check("an id that is no file name is not used as one", "check-3" in rows, sorted(rows))
    tick = rows["main_thread_waits"].one("input")
    check("the request carries the name as it is", NASTY in tick.attrs["data-req"], tick.attrs["data-req"])
    check("and the last line names the file as it was given", NASTY in page.one("dialog", "ask").attrs["data-tail"])
    check("the title too", NASTY in page.one("title").text)


def test_an_address_in_a_trace_stays_text():
    rep = plain.annotate(make([entry("main_thread_waits", [row("GET https://example.com/items", 700.0)])]))
    text = report_page.render(rep)
    check("it is shown", "GET https://example.com/items" in read(text).text)
    check("and nothing points at it", not outside(text), outside(text))


# --- the same report, the same bytes ------------------------------------------------------

def test_the_same_report_gives_the_same_bytes(demo_report):
    first = report_page.render(demo_report, where=".echolot/out/report.json")
    check("twice the same", first == report_page.render(copy.deepcopy(demo_report), where=".echolot/out/report.json"))
    check("and it survives the file: read back from JSON, the same page",
          first == report_page.render(json.loads(report_mod.to_json(demo_report)), where=".echolot/out/report.json"))


def test_the_bytes_do_not_depend_on_the_process(tmp_path):
    """No clock, no random number, no order a hash decides."""
    (tmp_path / "report.json").write_text(report_mod.to_json(known()), encoding="utf-8")
    code = ("import hashlib, json, sys; from echolot.pages import report_page; "
            "print(hashlib.sha256(report_page.render(json.load(open(sys.argv[1], encoding='utf-8')), "
            "where='report.json').encode()).hexdigest())")
    here = hashlib.sha256(report_page.render(known(), where="report.json").encode()).hexdigest()
    for seed in ("1", "4242"):
        out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "report.json")], cwd=ROOT,
                             env={"PYTHONHASHSEED": seed, "PATH": "", "ECHOLOT_NO_RECORD": "1"},
                             capture_output=True, text=True, check=True).stdout.strip()
        check(f"hash seed {seed}", out == here, (out, here))


def test_rendering_leaves_the_report_as_it_was(fixture_report):
    for rep in (known(), fixture_report):
        before = copy.deepcopy(rep)
        report_page.render(rep)
        check("not a key touched", rep == before)


# --- the verdict --------------------------------------------------------------------------

def test_the_verdict_is_the_reports_first_sentence_and_the_rest():
    page = page_of(known())
    main_ = page.one("main")
    check("the headline is the first sentence", main_.one("h1").text == "The launch takes 1.9 s", main_.one("h1").text)
    check("the rest, then how it was measured",
          main_.one("p", "lead").text == "You allowed 0.5 s, so it takes 3.8 times as long. "
                                         "Measured on 3 launches, from 1.9 to 2.0 s.", main_.one("p", "lead").text)
    pill = main_.one("span", "lg")
    check("the level: its word, its tint and its icon together",
          pill.text == "Needs attention" and "red" in pill.attrs["class"] and pill.all("svg"), pill.attrs)
    check("the scenario beside it", "coldStart" in pill.parent.text)


def test_a_report_with_no_budget_is_grey_and_says_why():
    rep = plain.annotate(make([entry("gc_pressure", [])], duration=1136.0, budget=None, runs=5))
    main_ = page_of(rep).one("main")
    pill = main_.one("span", "lg")
    check("not checked, never fine", pill.text == "Not checked" and "grey" in pill.attrs["class"], pill.attrs)
    check("the launch is still said", main_.one("h1").text == "The launch takes 1.1 s")
    check("and that there is nothing to hold it against",
          main_.one("p", "lead").text.startswith("No time limit is set, so there is nothing to compare it with. "
                                                 "Measured on 5 launches"), main_.one("p", "lead").text)


def test_one_recording_and_a_stretch_that_is_no_launch():
    rep = plain.annotate(make([entry("main_thread_waits", [row("worker", 700.0, runs=None)])],
                              runs=None, startup=False, budget=4000.0))
    del rep["detectors"][0]["rows"][0]["runs"]
    page = page_of(rep)
    check("a run, and one of them", page.one("p", "lead").text.endswith("Measured on one run."), page.one("p", "lead").text)
    panel = panel_of(page, "main_thread_waits")
    check("no dot for a run that is the bar itself", not panel.all("i", "dot") and len(panel.all("i", "bar")) == 1)
    check("and the key says what the bar is", panel.one("p", "key").text == "this run", panel.one("p", "key").text)
    check("the axis is in ms, of no median", panel.one("span", "ax-u").text == "ms")


def test_a_report_with_no_window_has_a_verdict_and_no_measure():
    rep = plain.annotate(make([entry("gc_pressure", [])]))
    rep["window"] = {}
    rep = plain.annotate(rep)
    main_ = page_of(rep).one("main")
    check("one sentence, all of it the headline", main_.one("h1").text == "This recording has no measured run to judge")
    check("nothing under it", not main_.all("p", "lead"))


# --- the table: every level ---------------------------------------------------------------

def test_a_row_per_check_the_ones_that_need_the_reader_first():
    page = page_of(known())
    rows = rows_of(page)
    check("one row per detector", len(rows) == 6)
    check("red, then yellow by what it found, then fine, then not checked",
          list(rows) == ["main_thread_waits", "uninstrumented_cpu", "binder_txn",
                         "gc_pressure", "monitor_contention", "io_wait"], list(rows))
    check("grouped for the filter", [r.attrs["data-group"] for r in rows.values()]
          == ["red", "yellow", "yellow", "green", "green", "grey"])
    table = page.one("div", "tb")
    check("it is a table to a screen reader", table.attrs["role"] == "table"
          and [h.text for h in table.all("span", role="columnheader")]
          == ["Tick", "Check", "How it touches the app", "Level", "Details"])
    for did, r in rows.items():
        check(f"{did}: a row of five cells", r.attrs["role"] == "row" and len(r.all("div", role="cell")) == 5)


def test_every_word_in_a_row_is_the_reports():
    rep = known()
    rows = rows_of(page_of(rep))
    for det in rep["detectors"]:
        r, words = rows[det["id"]], det["plain"]
        said = words["found"] or (words["clean"] if det["attention"] == "fine" else words["reason"])
        check(f"{det['id']}: the title", r.one("h3").text == words["title"])
        check(f"{det['id']}: what it found, or what clean means, or why it could not look",
              r.one("p", "tr-f").text == said.replace("`", ""), (r.one("p", "tr-f").text, said))
        check(f"{det['id']}: how it touches the app", r.one("div", "c-w").text == words["why"])
        check(f"{det['id']}: the level's word", r.one("span", "st").text == kit.WORD[det["attention"]])
    waits = rows["main_thread_waits"].one("p", "tr-f")
    check("a name in a sentence is code", [c.text for c in waits.all("code")] == ["worker_pool_#", "loadConfig"])


def test_a_level_is_never_colour_alone():
    page = page_of(known())
    for pill in page.all("span", "st"):
        check("an icon and a word in every pill", pill.all("svg") and pill.text, pill.attrs)
    shapes = {k: kit.icon(k) for k in kit.TONES}
    check("and each level has a shape of its own", len(set(shapes.values())) == 4)
    check("a triangle for the amber one, which is pale on white", "M8 1.7 15 14H1" in shapes["yellow"])


def test_the_filter_is_the_counts():
    views = page_of(known()).one("div", "views").all("button")
    check("all, then one per level, with its count",
          [(b.attrs["data-view"], b.one("span", "v-w").text, b.one("b").text) for b in views]
          == [("all", "All", "6"), ("red", "Needs attention", "1"), ("yellow", "Worth a look", "2"),
              ("green", "Fine", "2"), ("grey", "Not checked", "1")])
    check("all is the one pressed", [b.attrs["aria-pressed"] for b in views] == ["true"] + ["false"] * 4)
    check("each level with its icon, all without", [len(b.all("svg")) for b in views] == [0, 1, 1, 1, 1])


def test_only_a_check_that_found_something_can_be_ticked():
    page = page_of(known())
    rows = rows_of(page)
    for did, hot in (("main_thread_waits", True), ("binder_txn", True), ("gc_pressure", False), ("io_wait", False)):
        check(f"{did}: a tick box in the row", bool(rows[did].all("input")) == hot)
        check(f"{did}: and its twin in the panel", bool(panel_of(page, did).all("input")) == hot)
        check(f"{did}: quiet rows are marked so", ("quiet" in rows[did].attrs["class"].split()) != hot)
    tick, twin = rows["binder_txn"].one("input"), panel_of(page, "binder_txn").one("input")
    check("the two are one tick", tick.attrs["data-id"] == twin.attrs["data-id"] == "binder_txn"
          and tick.attrs["data-req"] == twin.attrs["data-req"])
    check("only the row's has the id", tick.attrs.get("id") == "pick-binder_txn" and "id" not in twin.attrs)
    check("it says what it ticks", tick.attrs["aria-label"] == "Tick: Calls to the system or to other apps")
    check("the bar counts what can be ticked", page.one("button", id="dock-all").attrs["data-n"] == "3")


def test_a_report_with_nothing_to_tick_has_no_bar_and_no_request():
    page = page_of(plain.annotate(make([entry("gc_pressure", []), entry("anr", [])], budget=4000.0)))
    check("no bar and no request", not page.all("div", "dock") and not page.all("dialog", "ask"))
    check("and no advice to tick", "Dig deeper" not in page.one("main").text)
    check("the panel is still there", len(page.all("dialog", "peek")) == 1)


def test_no_check_at_all():
    rep = plain.annotate(make([]))
    page = page_of(rep)
    check("it says so", page.one("p", "tb-none").text == "No check ran on these recordings.")
    check("0 checks", "0 checks, the ones" in page.one("main").text)


def test_checks_switched_off_in_the_config_are_counted():
    rep = known()
    rep["summary"]["absent_ids"] = ["frame_jank", "anr"]
    check("as the terminal view counts them",
          "6 checks, the ones that need you first." in page_of(rep).text
          and "2 more checks are switched off in the config." in page_of(rep).text)


def test_a_detector_and_a_level_this_version_does_not_know():
    rep = known()
    rep["detectors"].append(entry("from_the_future", [row("x", 50.0)]) | {
        "attention": "on_fire", "plain": {"title": "Something new", "why": "", "clean": "", "found": "It found x.",
                                          "reason": None}})
    page = page_of(rep)
    r = rows_of(page)["from_the_future"]
    pill = r.one("span", "st")
    check("its word is shown as it is, in grey: not called fine, nor asked to be ticked",
          pill.text == "On fire" and "grey" in pill.attrs["class"] and not r.all("input"), pill.attrs)
    check("and its rows are still drawn", len(panel_of(page, "from_the_future").all("i", "bar")) == 1)


# --- the panel: one check's measurements ---------------------------------------------------

def test_the_panel_says_the_rows_sentences_again_and_then_measures():
    page = page_of(known())
    panel = panel_of(page, "main_thread_waits")
    det = known()["detectors"][-1]
    check("the level, the title, what was found, how it touches the app",
          panel.one("span", "st").text == "Needs attention" and panel.one("h2").text == det["plain"]["title"]
          and det["plain"]["found"].replace("`", "") in panel.text and det["plain"]["why"] in panel.text)
    check("the title is what the dialog is called", panel.one("h2").attrs["id"] == "peek-title")
    deep = panel.one("div", "deep")
    check("then Details, shown at once: no fold inside", deep.one("h3").text == "Details" and not deep.all("details"))
    check("the detector by its id and its own title",
          deep.one("code").text == "main_thread_waits" and "title of main_thread_waits" in deep.text)
    check("nothing technical is in the row itself",
          "main_thread_waits" not in rows_of(page)["main_thread_waits"].text.replace("pick-", ""))


def test_a_row_of_the_report_is_a_bar_with_a_dot_per_recording():
    panel = panel_of(page_of(known()), "uninstrumented_cpu")
    marks = [m for m in panel.all("div", "cr") if "ax" not in m.attrs["class"].split()]
    check("a row each, the largest first",
          [m.one("span", "mono").text for m in marks] == ["worker_pool_0", "Sync Worker"])
    check("the bar ends at the median, on an axis that holds every recording",
          [m.one("i", "bar").attrs["style"] for m in marks] == ["width:83.33%", "width:50.00%"],
          [m.one("i", "bar").attrs["style"] for m in marks])
    check("a dot per recording it was found in", [len(m.all("i", "dot")) for m in marks] == [3, 2])
    check("the figures in text", marks[0].one("div", "cr-v").text == "500 ms480 – 5303 of 3 launches"
          and "2 of 3 launches" in marks[1].one("div", "cr-v").text, marks[0].one("div", "cr-v").text)
    check("round ticks, and the unit said once",
          [s.text for s in panel.one("div", "ticks").all("span")] == ["0", "150", "300", "450", "600"]
          and panel.one("span", "ax-u").text == "ms, median")
    check("the evidence under the name", marks[0].one("span", "cr-s").text == "99.0% of CPU outside slices")
    tip = marks[0].attrs["data-tip"].split("\n")
    check("the exact figures for the tooltip, and the row can take focus",
          marks[0].attrs["tabindex"] == "0" and tip == [
              "worker_pool_0", "median: 500.00 ms", "each launch: 480.00 · 500.00 · 530.00",
              "found in 3 of 3 launches", "occurrences: 1"], tip)


def test_the_threshold_is_drawn_where_the_check_has_one_on_the_measure():
    page = page_of(known())
    cpu = panel_of(page, "uninstrumented_cpu")
    check("a line at the threshold", [t.attrs["style"] for t in cpu.all("i", "thr")] == ["left:8.33%"] * 2)
    check("named in the key", cpu.one("p", "key").text == "medianeach launchwhere the check fires")
    check("and in words, with the thresholds that are not on the axis",
          "fires at 50 ms in total (min_running_ms) · max_covered_pct 50 · built-in" in cpu.text, cpu.text)
    binder = panel_of(page, "binder_txn")
    check("a check that fires on either of two has no single line", not binder.all("i", "thr")
          and binder.one("p", "key").text == "medianeach launch")
    check("its numbers are said, its pattern is not",
          "min_txn_ms 10 · max_total_ms 50 · built-in" in binder.text and "name_glob" not in binder.text, binder.text)


def test_a_threshold_that_was_moved_says_from_what():
    rep = known()
    rep["detectors"][-1] |= {"params": {"min_total_ms": 83.3, "lock_glob": "*contention*"},
                             "params_source": "config+cli", "defaults": {"min_total_ms": 16}}
    text = panel_of(page_of(rep), "main_thread_waits").text
    check("where it is now, what was shipped, and who set it",
          "fires at 83.3 ms in total (min_total_ms), built-in is 16 · set in the config and set with --set" in text, text)


def test_which_checks_get_a_line_is_read_from_their_headers():
    """A detector with one calibrated threshold, on a time column, has a floor under its rows."""
    check("these, today: a new detector that joins or leaves them should be looked at",
          report_page._floors() == {
              "composables": ("min_self_ms", "self_ms"), "io_wait": ("min_io_wait_ms", "total_ms"),
              "main_thread_block": ("min_slice_ms", "self_ms"),
              "main_thread_kernel_waits": ("min_total_ms", "total_ms"),
              "main_thread_waits": ("min_total_ms", "total_ms"), "repeated_work": ("min_total_ms", "total_ms"),
              "runnable_starvation": ("min_runnable_ms", "total_ms"),
              "uninstrumented_cpu": ("min_running_ms", "total_ms")}, report_page._floors())
    rep = plain.annotate(make([entry("main_thread_outlier", [row("inflate", 86.0, max_ms=86.0)],
                                     params={"factor": 4, "min_abs_ms": 40, "min_occurrences": 5})]))
    check("a threshold on another column than the one drawn is no line",
          not panel_of(page_of(rep), "main_thread_outlier").all("i", "thr"))


def test_no_row_is_below_the_line_drawn_for_its_check(demo_report, fixture_report):
    """The line says "where the check fires": a row to the left of it would make it wrong."""
    drawn = []
    for rep in (demo_report, fixture_report):
        for det in rep["detectors"]:
            fires = report_page._fires_at(det, det["rows"])
            if fires:
                drawn.append(det["id"])
                below = [r["location"] for r in det["rows"] if r[fires[2]] < fires[1]]
                check(f"{det['id']}: every row is at or above {fires[0]} = {fires[1]}", not below, below)
    check("and the fixture has such checks to hold this on",
          {"main_thread_block", "main_thread_waits", "io_wait", "uninstrumented_cpu"} <= set(drawn), sorted(set(drawn)))


def test_a_check_with_no_rows_says_that_it_has_none_and_why():
    page = page_of(known())
    fine, blind = panel_of(page, "gc_pressure"), panel_of(page, "io_wait")
    check("fine: nothing above its limit", "No rows: the check found nothing above its limit." in fine.text, fine.text)
    check("not checked: it could not run, and the reason is the report's",
          "No rows: the check could not run." in blind.text
          and "These recordings do not carry the phone's state. Record with it on." in blind.text, blind.text)
    check("neither draws a chart", not fine.all("div", "chart") and not blind.all("div", "chart"))
    check("a check without thresholds says so", "no threshold" in fine.text)


def test_a_detector_that_failed_is_not_checked_and_shows_its_error():
    rep = plain.annotate(make([entry("gc_pressure", [], error="no such table: slice_x")], budget=500.0))
    page = page_of(rep)
    r = rows_of(page)["gc_pressure"]
    check("grey, with the reason the report gives",
          r.one("span", "st").text == "Not checked" and r.one("p", "tr-f").text == "It failed to run on these recordings.")
    panel = panel_of(page, "gc_pressure")
    check("the error itself is in the panel, as code", "The error:" in panel.text
          and "no such table: slice_x" in [c.text for c in panel.all("code")])


def test_rows_from_a_part_of_the_recordings_say_so():
    rep = plain.annotate(make([
        entry("io_wait", [row("main", 300.0, runs="3/5")], checked=False,
              reason="The recording does not carry the phone's own state (2 of the 5 recordings)."),
        entry("gc_pressure", [row("HeapTaskDaemon", 130.0, runs="3/5")], error="boom", failed_runs="2/5"),
    ], runs=5))
    page = page_of(rep)
    check("could not look in some: the rows are drawn and the reason is under them",
          "(2 of the 5 recordings)." in panel_of(page, "io_wait").text and panel_of(page, "io_wait").all("i", "bar"))
    check("failed in some: the same",
          "Failed in 2 of 5 repeats: boom. The rows are from the others." in panel_of(page, "gc_pressure").text)


def test_many_rows_are_all_drawn_and_four_go_to_the_agent():
    many = [row(f"piece_{i:02d}", 400.0 - i, detail=f"evidence {i}") for i in range(30)]
    page = page_of(plain.annotate(make([entry("main_thread_block", many)])))
    panel = panel_of(page, "main_thread_block")
    check("every row in the panel", len(panel.all("i", "bar")) == 30)
    lines = rows_of(page)["main_thread_block"].one("input").attrs["data-req"].split("\n")
    check("the four largest, and how many more", lines == [
        "- piece_00 · main_thread_block · 400 ms, 3/3 runs · evidence 0",
        "- piece_01 · main_thread_block · 399 ms, 3/3 runs · evidence 1",
        "- piece_02 · main_thread_block · 398 ms, 3/3 runs · evidence 2",
        "- piece_03 · main_thread_block · 397 ms, 3/3 runs · evidence 3",
        "- and 26 more of main_thread_block"], lines)


def test_a_long_name_is_kept_whole_and_a_long_evidence_is_cut():
    name = "com.example.app.feature.collection." + "VeryLongGeneratedClassName" * 9 + ".invoke"
    detail = "monitor contention with owner " + "x" * 400
    page = page_of(plain.annotate(make([entry("monitor_contention", [row(name, 62.0, detail=detail)])])))
    panel = panel_of(page, "monitor_contention")
    check("the name whole", panel.one("span", "mono").text == name)
    shown = panel.one("span", "cr-s").text
    check("the evidence cut where the panel cuts it", len(shown) == report_page.PANEL_DETAIL and shown.endswith("…"))
    line = rows_of(page)["monitor_contention"].one("input").attrs["data-req"]
    check("and shorter for the agent, who reads the report",
          line.endswith("x…") and len(line.split(" · ")[-1]) == report_page.REQUEST_DETAIL, line)
    check("all of it is under the fold", detail in page.one("div", "md").text)


# --- a report from before the keys ---------------------------------------------------------

def test_a_report_from_before_the_plain_keys_is_read_as_the_terminal_reads_it(fixture_report):
    check("the fixture is such a report", "verdict" not in fixture_report
          and "plain" not in fixture_report["detectors"][0])
    page = page_of(fixture_report)
    want = plain.annotate(copy.deepcopy(fixture_report))
    rows = rows_of(page)
    check("a row per detector", len(rows) == len(want["detectors"]) == 16)
    for det in want["detectors"]:
        check(f"{det['id']}: the level the plain reading gives",
              rows[det["id"]].one("span", "st").text == kit.WORD[det["attention"]])
    check("and its verdict", page.one("h1").text in want["verdict"]["text"])


def test_a_report_from_before_checked_says_not_checked_and_why():
    silent = entry("gc_pressure", [])
    del silent["checked"]
    r = rows_of(page_of(make([silent])))["gc_pressure"]
    check("not checked, never fine", r.one("span", "st").text == "Not checked")
    check("with the reason", r.one("p", "tr-f").text.startswith("This report was written before echolot recorded"))


# --- the request for an agent --------------------------------------------------------------

def test_the_request_is_put_together_from_what_the_page_holds():
    page = page_of(known(), where=".echolot/out/report.json")
    ask = page.one("dialog", "ask").attrs
    lead = "/echolot-hunt coldStart takes 1,920 ms against a limit of 500 ms. "
    check("for one row", ask["data-one"] == lead + "Find what is behind this row of the last report and where it is in the code:")
    check("for several", ask["data-many"] == lead + "Find what is behind these rows of the last report and where it is in the code:")
    check("which traces, and after which change",
          ask["data-tail"] == "Traces: the 3 behind .echolot/out/report.json. After which change: unknown.")
    tick = rows_of(page)["main_thread_waits"].one("input")
    check("the rows of a ticked check, as the report names them",
          tick.attrs["data-req"] == "- worker_pool_# · main_thread_waits · 700 ms, 3/3 runs · idle · 71% blocked in loadConfig")
    check("the title and the level for the list in the request",
          tick.attrs["data-title"] == "The screen waits for background work" and tick.attrs["data-level"] == "red")
    check("an icon of each level to copy from", sorted(s.attrs["data-icon"] for s in page.all("span")
                                                       if "data-icon" in s.attrs) == ["green", "grey", "red", "yellow"])


def test_the_request_of_a_report_named_by_hand_and_of_one_with_no_budget():
    rep = plain.annotate(make([entry("main_thread_waits", [row("worker", 700.0, runs=None)])], runs=None))
    ask = page_of(rep, where="/tmp/before.json", last=False).one("dialog", "ask").attrs
    check("not called the last report, no limit, one trace",
          ask["data-one"] == "/echolot-hunt coldStart takes 2,000 ms. Find what is behind this row of the report "
                             "and where it is in the code:"
          and ask["data-tail"] == "Traces: the one behind /tmp/before.json. After which change: unknown.", ask)
    rep["scenario"] = None
    check("a report with no scenario names the window",
          page_of(rep).one("dialog", "ask").attrs["data-one"].startswith("/echolot-hunt The window takes 2,000 ms."))


def test_the_request_in_the_guide_is_the_demos(demo_report):
    """docs/analysing.md shows the request for two ticked checks of the demo app."""
    page = page_of(demo_report, where=".echolot/out/report.json")
    ask, rows = page.one("dialog", "ask").attrs, rows_of(page)
    request = "\n".join([ask["data-many"], rows["frame_jank"].one("input").attrs["data-req"],
                         rows["main_thread_block"].one("input").attrs["data-req"], ask["data-tail"]])
    guide = (ROOT / "docs" / "analysing.md").read_text(encoding="utf-8")
    check("the sample in the guide is what the page puts together", f"```text\n{request}\n```" in guide, request)
    check("and the two checks are called what the guide calls them",
          rows["frame_jank"].one("h3").text == "Frames that came late"
          and rows["main_thread_block"].one("h3").text == "Heavy work on the main thread")


def test_the_request_dialog_says_what_an_agent_does_and_where_to_paste():
    ask = page_of(known()).one("dialog", "ask")
    check("its heading names it", ask.attrs["aria-labelledby"] == "ask-h" and ask.one("h2").text == "Dig deeper with an agent")
    check("three sentences on what the agent does", ask.all("p")[0].text.count(". ") == 2 and ask.all("p")[0].text.endswith("."))
    check("where to paste", "Paste the request into Claude Code, Codex or Cursor opened on the project." in ask.text)
    check("the text under a fold, and Copy first", ask.one("summary").text == "The request text"
          and [b.text for b in ask.all("button")] == ["Copy the request", "Close"])


# --- the folds ------------------------------------------------------------------------------

def test_how_the_colours_are_chosen_says_the_rule_with_its_numbers():
    fold = [d for d in page_of(known()).all("details") if d.one("summary").text == "How the colours are chosen"][0]
    said = {li.one("b").text: li.one("p").text for li in fold.all("li")}
    check("the four levels, in order", list(said) == ["Needs attention", "Worth a look", "Fine", "Not checked"])
    check("the numbers are the rule's own", f"{plain.SHARE:.0%}" == "15%" and plain.FLOOR_MS == 150.0
          and said["Needs attention"] == "One thing the main thread spent or lost holds at least 15% of the launch "
                                         "and at least 0.15 s, in every launch.", said["Needs attention"])
    check("green always means looked at", said["Not checked"].endswith("Green always means looked at and fine."))


def squeezed(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text)


def test_the_technical_report_holds_what_report_md_holds(demo_report, fixture_report):
    for name, rep in (("the demo", demo_report), ("the fixture", fixture_report), ("by hand", known())):
        page = page_of(rep)
        fold = [d for d in page.all("details") if d.one("summary").text == "The full technical report"][0]
        got = squeezed(fold.one("div", "md").text)
        text = report_mod.to_markdown(plain.annotate(copy.deepcopy(rep)))
        lines = [re.sub(r"</?sub>", "", line) for line in text.split("\n")]
        missing = [line for line in lines if squeezed(line) and squeezed(line) not in got]
        check(f"{name}: every line of report.md is under the fold", not missing, missing[:3])


def test_the_technical_report_draws_the_window_and_every_row(demo_report):
    page = page_of(demo_report)
    fold = [d for d in page.all("details") if d.one("summary").text == "The full technical report"][0]
    charts = {c.one("p", "chart-h").text: c for c in fold.all("div", "chart")}
    check("two charts above report.md", list(charts) == ["Where the main thread was during the window",
                                                         "Every row on one axis"], list(charts))
    states = [m.one("div", "cr-n").text for m in charts["Where the main thread was during the window"].all("div", "cr")
              if "ax" not in m.attrs["class"].split()]
    check("the main thread by its state, in report.md's words",
          states == ["on a CPU", "waiting for a CPU", "blocked in the kernel", "sleeping"], states)
    first = charts["Where the main thread was during the window"].all("div", "cr")[1]
    check("with the exact figures for the tooltip",
          first.attrs["data-tip"].split("\n") == ["on a CPU", "456.08 ms", "40.1% of 1,136.05 ms"], first.attrs["data-tip"])
    every = [m for m in charts["Every row on one axis"].all("div", "cr") if "ax" not in m.attrs["class"].split()]
    total = sum(len(d["rows"]) for d in demo_report["detectors"])
    check("every row of every detector, on one axis", len(every) == total == 8, (len(every), total))
    values = [float(m.one("i", "bar").attrs["style"][6:-1]) for m in every]
    check("the largest first", values == sorted(values, reverse=True), values)
    check("each says which detector it is of", every[0].one("span", "cr-s").text == "uninstrumented_cpu")


def test_a_report_too_bare_to_write_out_still_gives_a_page():
    page = page_of({"detectors": [entry("gc_pressure", [])]})
    check("the table is there", len(rows_of(page)) == 1)
    check("and the fold says what is missing", "The technical report cannot be written out" in page.one("div", "md").text)


# --- report.md as HTML ----------------------------------------------------------------------

def test_the_markdown_of_a_report_as_html():
    got = md.to_html("\n".join([
        "# Marker Report", "", "Runs: **5**, numbers are medians", "Scenario: `coldStart`",
        "> ⚠️ The mask matched more than one process: `a`, `b`.", "",
        "## Where <the> main thread spent its time", "_measured as SELF time_", "",
        "| Where | Runs | Self, ms | Evidence |", "|---|---|---|---|",
        "| a\\|b <init> | 5/5 | 222.68 | `not code` **not bold** |", "| c | 2/5 | — | x |", "",
        "<sub>detector `main_thread_block`, params: {'min_slice_ms': 16}</sub>", "**Silent:** anr, io_wait"]))
    page = read(got)
    check("headings", page.one("h2").text == "Marker Report" and page.one("h3").text == "Where <the> main thread spent its time")
    check("a fact to a line", [p.text for p in page.all("p")][:2] == ["Runs: 5, numbers are medians", "Scenario: coldStart"])
    check("bold and code inside a line", page.all("p")[0].one("b").text == "5" and page.all("p")[1].one("code").text == "coldStart")
    note = page.one("div", "callout")
    check("a warning is a callout with its icon and the word for it",
          note.all("svg") and note.text == "Warning: The mask matched more than one process: a, b."
          and "⚠" not in got)
    cells = [[c.text for c in tr.all("td")] for tr in page.all("tr")][1:]
    check("a pipe inside a cell stays in the cell, and a cell is shown as it is",
          cells == [["a|b <init>", "5/5", "222.68", "`not code` **not bold**"], ["c", "2/5", "—", "x"]], cells)
    check("a column of figures lines up at the right",
          [th.attrs.get("class") for th in page.all("th")] == [None, None, "r", None])
    check("the table scrolls in its own box", page.one("table").parent.attrs["class"] == "tblwrap")
    check("the small print and the last line", "detector main_thread_block, params: {'min_slice_ms': 16}" in page.text
          and page.all("p")[-1].text == "Silent: anr, io_wait")
    check("everything is escaped", "<the>" not in got and "<init>" not in got)


# --- with script switched off ---------------------------------------------------------------

def test_what_a_reader_gets_with_script_switched_off():
    page = page_of(known())
    check("the page says so itself until a script runs",
          page.one("html").attrs["class"] == "no-js" and page.all("script")[0].text == 'document.documentElement.className = "js";')
    note = page.one("noscript")
    check("and tells the reader what works without it", "Script is switched off" in note.text
          and "the full technical report" in note.text)
    for r in rows_of(page).values():
        check("every row is in the markup with its three sentences, not in a template",
              not r.inside("template") and r.one("h3").text and r.one("p", "tr-f").text and r.one("div", "c-w").text)
    for what in (page.all("label", "ck") + page.all("button", "rowbtn") + page.all("div", "dock")):
        check("what only script can work is not shown without it", "needs-js" in what.attrs["class"].split(), what.attrs)
    check("the filter is counts and no buttons until script enables them",
          all("disabled" in b.attrs for b in page.one("div", "views").all("button")))
    check("the measurements are under a fold that needs no script",
          any(d.one("summary").text == "The full technical report" for d in page.all("details")))


# --- the shared pieces ----------------------------------------------------------------------

@pytest.mark.parametrize("value, top", [(0, 1.0), (0.04, 0.04), (1.0, 1.0), (61.6, 80.0), (449.0, 500.0),
                                        (541.0, 600.0), (700.0, 800.0), (801.0, 1000.0), (1214.18, 1600.0),
                                        (9000.0, 10000.0), (float("nan"), 1.0)])
def test_an_axis_ends_at_a_round_number_that_quarters_into_round_ticks(value, top):
    check(f"{value} is held by {top}", kit.nice_top(value) == top, kit.nice_top(value))


@pytest.mark.parametrize("value, said", [(1919.4, "1,919"), (427.51, "428"), (61.6, "61.6"), (3.0, "3"),
                                         (0.44, "0.44"), (0.5, "0.5"), (0.0, "0")])
def test_a_figure_keeps_the_digits_that_say_something(value, said):
    check(f"{value} reads {said}", kit.ms(value) == said, kit.ms(value))


def test_a_mark_past_the_end_of_the_axis_is_not_drawn():
    marks = [{"name": "a", "value": 40.0, "figure": "40 ms"}]
    check("a threshold the axis holds is a line", "thr" in kit.chart(marks, 100.0, mark=50.0))
    check("one beyond it is left out, not pinned to the edge", "thr" not in kit.chart(marks, 100.0, mark=500.0))
    check("a row without exact figures is not a tab stop", "tabindex" not in kit.chart(marks, 100.0))


def test_the_top_bar_takes_the_steps_of_the_pages_to_come():
    bar = read(kit.topbar(["com.example.app", None, "coldStart"],
                          [("Start", "#start", False), ("Report", "#report", True)]))
    check("where the page is about, without what is not known", bar.one("span", "crumb").text == "com.example.appcoldStart")
    steps = bar.one("nav").all("a")
    check("the steps as a path, the current one marked for a screen reader",
          [(a.text, a.attrs.get("aria-current")) for a in steps] == [("Start", None), ("Report", "page")])
    check("and none where there is no other page", not read(kit.topbar(["com.example.app"])).all("nav"))


def test_light_and_dark_and_a_phone():
    page = page_of(known())
    css = page.one("style").text
    check("the viewport is the device's", page.one("meta", name="viewport").attrs["content"].startswith("width=device-width"))
    check("both schemes are announced", page.one("meta", name="color-scheme").attrs["content"] == "light dark")
    check("dark by the system's choice, and by an attribute",
          '@media (prefers-color-scheme: dark)' in css and ':root:not([data-theme="light"])' in css
          and ':root[data-theme="dark"]' in css)
    check("the language is said", page.one("html").attrs["lang"] == "en")


def _blocks(css: str, opener: str) -> list[str]:
    out, at = [], 0
    while (start := css.find(opener, at)) >= 0:
        end = css.index("}", start)
        out.append(css[css.index("{", start) + 1:end])
        at = end
    return out


def test_every_colour_is_a_token():
    base = kit.asset("base.css")
    light = _blocks(base, ":root {")[0]
    dark = _blocks(base, ':root:not([data-theme="light"]) {') + _blocks(base, ':root[data-theme="dark"] {')
    check("dark is written twice and both say the same",
          len(dark) == 2 and dark[0].split() == dark[1].split())
    names = lambda block: set(re.findall(r"(--[a-z0-9-]+):", block))  # noqa: E731
    check("every colour dark changes is one light defines", names(dark[0]) <= names(light), names(dark[0]) - names(light))
    rest = base
    for block in [light, *dark]:
        rest = rest.replace(block, "")
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    for name in ("table.css", "picks.css", "report.css"):
        rest += kit.asset(name)
    check("nothing below the tokens names a colour of its own", not literal.search(rest), literal.findall(rest))
    icons = kit.MARK + "".join(kit.icon(k) for k in kit.TONES) + kit.CHECK + kit.CLOSE + kit.CHEV
    check("nor does an icon", not literal.search(icons), literal.findall(icons))


def test_the_pieces_ship_with_the_package():
    names = set(report_page.CSS) | set(report_page.JS)
    check("every stylesheet and script a page names is inside the package",
          all((PAGES / n).is_file() for n in names), sorted(n for n in names if not (PAGES / n).is_file()))
    check("and nothing lies there unused", {p.name for p in PAGES.iterdir() if p.suffix in (".css", ".js")} == names)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("the wheel is told to carry them", '"pages/*.css"' in pyproject and '"pages/*.js"' in pyproject)


def test_the_page_is_made_of_the_shared_pieces_in_order():
    text = report_page.render(known())
    at = [text.index(kit.asset(n)) for n in report_page.CSS + report_page.JS]
    check("each is written in whole, in the order named", at == sorted(at))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the scripts")
@pytest.mark.parametrize("name", report_page.JS)
def test_the_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(PAGES / name)], capture_output=True, text=True)
    check(f"{name} is JavaScript", done.returncode == 0, done.stderr)


def test_ids_are_unique_outside_the_templates(demo_report):
    page = page_of(demo_report)
    ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs and not n.inside("template")]
    check("no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
    opens = {b.attrs["data-open"] for b in page.all("button", "rowbtn")}
    check("every Details button has its panel", opens == {t.attrs["id"][5:] for t in page.all("template")})


# --- the commands ---------------------------------------------------------------------------

def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The demo app with one recording, and a limit its launch runs far over."""
    traces = demo.write(tmp_path)
    cfg = tmp_path / "echolot.yml"
    cfg.write_text(demo.CONFIG.replace("  name: coldStart\n", "  name: coldStart\n  budget_ms: 100\n"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path, str(traces[0])


def test_analyze_html_writes_the_page_beside_the_report(project):
    root, trace = project
    code, _, err = run("analyze", trace, "--html")
    out = root / ".echolot" / "out"
    check("a launch far over its limit gates nothing", code == 0, err)
    check("three files", sorted(p.name for p in out.iterdir()) == ["report.html", "report.json", "report.md"])
    check("and the page is named with them", f"→ {out / 'report.html'}" in err, err[-400:])
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    page = (out / "report.html").read_text(encoding="utf-8")
    check("the page is that report's, and names it as the project does",
          page == report_page.render(rep, where=".echolot/out/report.json", last=True))
    check("red at the top", read(page).one("main").one("span", "lg").text == "Needs attention")
    check("nothing in it points outside", not outside(page), outside(page))

    code, text, err = run("report", "--html")
    check("report --html writes the same page for the last report", code == 0 and text.strip() == f"→ {out / 'report.html'}"
          and (out / "report.html").read_text(encoding="utf-8") == page, (code, text, err))


def test_a_page_that_stands_there_is_written_again_and_none_is_written_unasked(project):
    """The page beside a report is of that report, and `analyze` takes nothing away."""
    root, trace = project
    out = root / ".echolot" / "out"
    code, _, err = run("analyze", trace)
    check("no page was asked for, and none is written", code == 0 and not (out / "report.html").exists()
          and "report.html" not in err, err[-300:])
    run("analyze", trace, "--html")
    first = (out / "report.html").read_text(encoding="utf-8")

    # Another analysis of the same recording, one detector's bar set where it finds nothing.
    code, _, err = run("analyze", trace, "--set", "main_thread_block.min_slice_ms=100000")
    page = (out / "report.html").read_text(encoding="utf-8")
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    check("the page that stood there is written again, without --html", code == 0 and page != first, err[-300:])
    check("from the report just written",
          page == report_page.render(rep, where=".echolot/out/report.json", last=True)
          and rows_of(read(page))["main_thread_block"].one("span", "st").text == "Fine"
          and rows_of(read(first))["main_thread_block"].one("span", "st").text != "Fine")
    check("and named on stderr with the other files",
          f"→ {out / 'report.html'}  (it stood there from an earlier report: written again from this one)" in err
          and "removed" not in err, err[-400:])

    theirs = root / "elsewhere"
    code, _, err = run("analyze", trace, "-o", str(theirs))
    check("a directory with no page gets none", code == 0 and sorted(p.name for p in theirs.iterdir())
          == ["report.json", "report.md"], sorted(p.name for p in theirs.iterdir()))


def test_report_html_for_a_report_named_by_hand(project):
    root, trace = project
    code, _, err = run("analyze", trace, "-o", "elsewhere", "--html")
    check("analysed into a directory of its own", code == 0, err)
    page = read((root / "elsewhere" / "report.html").read_text(encoding="utf-8"))
    check("which is not what `echolot report` calls the last report",
          "of the report and" in page.one("dialog", "ask").attrs["data-one"]
          and "behind elsewhere/report.json." in page.one("dialog", "ask").attrs["data-tail"])
    kept = root / "kept" / "003.json"
    kept.parent.mkdir()
    shutil.copy(root / "elsewhere" / "report.json", kept)
    code, text, _ = run("report", str(kept), "--html")
    check("the page goes beside the report read, under its name",
          code == 0 and text.strip() == f"→ {kept.with_suffix('.html')}" and kept.with_suffix(".html").is_file(), text)
    check("and the request names that file",
          "behind kept/003.json." in read(kept.with_suffix(".html").read_text(encoding="utf-8")).one("dialog", "ask").attrs["data-tail"])


def test_html_is_a_view_of_its_own(project):
    root, trace = project
    run("analyze", trace)
    for extra in (["--plain"], ["--json"], ["--detector", "frame_jank"], ["--window"]):
        code, out, err = run("report", "--html", *extra)
        check(f"refused with {extra[0]}", code == 2 and "is a view of its own" in err and not out, (code, err))
    check("and nothing was written", not (root / ".echolot" / "out" / "report.html").exists())
    code, _, err = run("report", "nowhere.json", "--html")
    check("a report that is not there is an error, not a page", code == 2 and "report not found" in err, err)
