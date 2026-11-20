#!/usr/bin/env python3
"""The agent's answer as a page: `echolot hunt --show [n] --html`, `hunt --done … --html`.

`echolot/pages/answer_page.py` writes an investigation for a person as one HTML
file, from its record and the reports it filed. What is pinned here:

  - the file is its own: nothing in it names an address, and a string out of a
    verdict or a trace cannot become markup;
  - the same files give the same bytes, whatever the process and its time zone;
  - every word about a cause is the verdict's, and a cause carries no level:
    what was measured and what was estimated are worded apart;
  - "Details" shows the rows a cause cites, from the round they were measured
    in, looked up by the rule the check of a verdict uses; a report deleted
    since is said in words and the page is written all the same;
  - each state a page can be in: many causes, one, none, a text and no verdict,
    an investigation still open, one left unfinished, long names and places,
    a record edited by hand.

The commands that write it are in `tests/test_answer_commands.py`.

What the page does when it is clicked is script, and is looked at in a browser:
here the script is only held to parsing, where node is there to parse it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import echolot  # noqa: E402
from echolot import verdict as verdict_mod  # noqa: E402
from echolot.pages import answer_page, kit, report_page  # noqa: E402
from tests import answer_fixture as fx  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import NASTY, SCHEME, _blocks, outside, read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(echolot.__file__).resolve().parent / "pages"
LEVELS = {"red", "yellow", "green", "grey"}


@pytest.fixture(scope="module")
def states(demo_report):
    """Every state of the fixture investigation: name -> (record, reports)."""
    return fx.states(demo_report)


def page_of(state, **kw):
    hunt, reports = state
    return read(answer_page.render(hunt, reports, **kw))


def rows_of(page):
    return {r.attrs["data-id"]: r for r in page.all("div", "tr")}


def panel_of(page, key):
    return page.one("template", id=f"peek-{key}")


def chart_rows(node):
    """The rows of a chart, by the name each is drawn under: the lines under
    the name, the figure, the tooltip."""
    out = {}
    for r in node.all("div", "cr"):
        if "ax" in r.attrs["class"].split():
            continue
        name = r.one("div", "cr-n").kids[0].text
        out[name] = {"subs": [s.text for s in r.all("span", "cr-s")], "figure": r.one("div", "cr-v").one("b").text,
                     "dots": len(r.all("i", "dot")), "tip": (r.attrs.get("data-tip") or "").split("\n")}
    return out


def place_of(node):
    """A place as the page shows it, put back together: the folders it is in, then the file."""
    return "".join(s.text for s in node.all("span", "at-in")) + node.one("code", "at").text


def places_of(cell):
    return [place_of(line) for line in cell.all("span", "at-l")]


def top(page):
    """The line above the headline: the state, and beside it how many rounds."""
    return page.one("main").all("div", "rowf")[0]


def fold(page, name):
    found = [d for d in page.all("details") if d.one("summary").text == name]
    assert len(found) == 1, f"{len(found)} folds named {name!r}"
    return found[0]


def changed(state, **verdict_keys):
    """The state with keys of its verdict replaced, and the check recorded for it."""
    hunt, reports = copy.deepcopy(state)
    hunt["verdict"].update(verdict_keys)
    return hunt, reports


# --- the fixture is a real one ------------------------------------------------------------

def test_the_fixture_verdicts_are_ones_the_check_accepts(states):
    for name in ("many", "one", "nothing", "long"):
        hunt, reports = states[name]
        problems = verdict_mod.validate(hunt["verdict"], reports)
        check(f"{name}: accepted against its reports", not problems, problems)
    hunt, reports = states["gone"]
    check("and with a report gone it would be refused now", verdict_mod.validate(hunt["verdict"], reports))


def test_the_rows_shown_are_the_rows_the_check_accepts(states):
    """`rows_at` and `validate` ask the same question of a citation."""
    for name in ("many", "gone"):
        hunt, reports = states[name]
        index = verdict_mod._Index(reports)
        for cause in hunt["verdict"]["causes"]:
            for entry in cause["evidence"]:
                rows, why = verdict_mod.rows_at(reports, entry)
                refused = verdict_mod.unresolved(index, entry["report"], entry["location"], entry.get("detector"))
                check(f"{name}: {entry} is shown exactly when it resolves", bool(rows) == (refused is None)
                      and why == refused, (rows, why, refused))
                check("and every row is at the place cited", all(r["location"] == entry["location"] for _, r in rows))
    _, reports = states["many"]
    rows, _ = verdict_mod.rows_at(reports, {"report": 2, "location": "AGENTTMP_find_item"})
    check("with no detector named, a marker's own row stands for it", [d for d, _ in rows] == [None])
    rows, _ = verdict_mod.rows_at(reports, {"report": 1, "location": "inflate"})
    check("and where no marker has the name, every detector's row there",
          [d for d, _ in rows] == ["main_thread_block", "main_thread_outlier"], [d for d, _ in rows])
    rows, _ = verdict_mod.rows_at(reports, {"report": 1, "detector": "main_thread_outlier", "location": "inflate"})
    check("a detector named means that detector's row", [d for d, _ in rows] == ["main_thread_outlier"])
    for bad in ("x", None, {"report": "1", "location": "inflate"}, {"report": 1}, {"report": 0, "location": "x"},
                {"report": 1, "location": "inflate", "detector": 5}):
        rows, why = verdict_mod.rows_at(reports, bad)
        check(f"{bad!r} is no citation, and says so", not rows and "does not read as a citation" in why, why)


# --- one file, nothing fetched ------------------------------------------------------------

def test_the_page_names_no_address_of_any_kind(states):
    for name, (hunt, reports) in states.items():
        text = answer_page.render(hunt, reports, again=fx.AGAIN)
        check(f"{name}: nothing loads and nothing reaches out", not outside(text), outside(text))
        check(f"{name}: no address anywhere in the file, as text either",
              not SCHEME.search(text) and "//www." not in text, SCHEME.findall(text)[:5])
        page = read(text)
        check(f"{name}: two scripts, both written into the file",
              [s.attrs for s in page.all("script")] == [{}, {}], [s.attrs for s in page.all("script")])
        check(f"{name}: one stylesheet, written into the file", len(page.all("style")) == 1)
        check(f"{name}: the browser is told to fetch nothing",
              "default-src 'none'" in page.one("meta", http_equiv="Content-Security-Policy").attrs["content"])


def test_a_string_out_of_a_verdict_or_a_trace_cannot_become_markup(states):
    hunt, reports = copy.deepcopy(states["many"])
    reports[2]["markers"]["rows"][0]["location"] = NASTY
    reports[2]["markers"]["rows"][0]["detail"] = NASTY
    reports[1]["window"]["process"] = NASTY
    v = hunt["verdict"]
    v["summary"] = f"{NASTY}. {NASTY}"
    v["inside"] = {"report": 2, "location": NASTY}
    cause = v["causes"][0]
    cause |= {"title": NASTY, "where": [NASTY], "mechanism": NASTY, "fix": NASTY, "cost_of": NASTY,
              "confidence_why": NASTY, "confidence": NASTY}
    cause["evidence"] = [{"report": 2, "location": NASTY}, {"report": 9, "location": NASTY}]
    v["suggestions"] = [{"fix": NASTY}]
    v["ruled_out"] = [NASTY]
    v["also_measured"] = [NASTY]
    hunt |= {"question": NASTY, "since": NASTY, "scenario": NASTY, "conclusion": NASTY}
    text = answer_page.render(hunt, reports, again=NASTY)
    page = read(text)
    check("no element came out of a string", not outside(text.replace("https:", "")), outside(text))
    check("still two scripts and no image", len(page.all("script")) == 2 and not page.all("img"))
    check("and no template was closed early", len(page.all("template")) == len(rows_of(page)) == 4)
    row = rows_of(page)["cause-1"]
    check("the title is there as text, whole", NASTY in row.one("h3").text)
    check("and so is the place, the change and the command",
          NASTY.replace("`", "") in row.one("div", "c-do").text.replace("`", "")
          and places_of(row.one("div", "c-wh")) == [NASTY] and page.one("pre", "cmd-t").text == NASTY)
    check("the row it cites is drawn under its own name", NASTY in chart_rows(panel_of(page, "cause-1")))
    check("the title of the document too", NASTY[:20] in page.one("title").text)


def test_an_address_in_a_verdict_stays_text(states):
    hunt, reports = changed(states["one"], summary="See https://example.com/issue/12 for the report.")
    text = answer_page.render(hunt, reports)
    check("it is shown", "https://example.com/issue/12" in read(text).one("main").text)
    check("and nothing points at it", not outside(text), outside(text))


# --- the same files, the same bytes -------------------------------------------------------

def test_the_same_files_give_the_same_bytes(states):
    for name, (hunt, reports) in states.items():
        first = answer_page.render(hunt, reports, again=fx.AGAIN)
        check(f"{name}: twice the same", first == answer_page.render(copy.deepcopy(hunt), copy.deepcopy(reports),
                                                                      again=fx.AGAIN))
        back = {int(k): v for k, v in json.loads(json.dumps(reports)).items()}
        check(f"{name}: and read back from JSON, the same page",
              first == answer_page.render(json.loads(json.dumps(hunt)), back, again=fx.AGAIN))


def test_the_bytes_do_not_depend_on_the_process_or_its_clock(tmp_path, states):
    """No clock, no time zone, no random number, no order a hash decides."""
    hunt, reports = states["many"]
    (tmp_path / "hunt.json").write_text(json.dumps(hunt), encoding="utf-8")
    (tmp_path / "reports.json").write_text(json.dumps(reports), encoding="utf-8")
    code = ("import hashlib, json, sys; from echolot.pages import answer_page; "
            "hunt = json.load(open(sys.argv[1], encoding='utf-8')); "
            "reports = {int(k): v for k, v in json.load(open(sys.argv[2], encoding='utf-8')).items()}; "
            "print(hashlib.sha256(answer_page.render(hunt, reports, again='x').encode()).hexdigest())")
    here = hashlib.sha256(answer_page.render(hunt, reports, again="x").encode()).hexdigest()
    for seed, zone in (("1", "UTC"), ("4242", "Asia/Tokyo"), ("7", "America/Los_Angeles")):
        out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "hunt.json"), str(tmp_path / "reports.json")],
                             cwd=ROOT, env={"PYTHONHASHSEED": seed, "TZ": zone, "PATH": "", "ECHOLOT_NO_RECORD": "1"},
                             capture_output=True, text=True, check=True).stdout.strip()
        check(f"hash seed {seed}, time zone {zone}", out == here, (out, here))


def test_rendering_leaves_the_record_and_the_reports_as_they_were(states):
    for name, (hunt, reports) in states.items():
        before = copy.deepcopy((hunt, reports))
        answer_page.render(hunt, reports, again=fx.AGAIN)
        check(f"{name}: nothing was written into what it read", (hunt, reports) == before)


# --- the verdict at the top ---------------------------------------------------------------

def test_the_verdict_is_the_summarys_first_sentence_and_how_many_rounds(states):
    page = page_of(states["many"])
    state = top(page)
    check("a state that is no level, with its word", state.one("span", "st").text == "Investigation finished"
          and "plain" in state.one("span", "st").attrs["class"])
    check("how many rounds, and how long it took", state.one("span", "sub").text == "2 rounds · 18 min 35 s")
    check("the first sentence of the summary is the headline, without its full stop",
          page.one("h1").text == "Most of the launch is one screen being built: collection_load takes 0.9 s of the 1.1 s launch")
    check("the rest of it follows, and the page adds no sentence of its own to it",
          page.one("p", "lead").text == "Three things inside it cost the most.")
    check("the title of the file is the question", page.one("title").text == f"echolot answer: {fx.QUESTION}")
    check("the top bar says the app and the scenario, and has no steps",
          page.one("span", "crumb").text == "com.example.appcoldStart" and not page.all("nav"))


def test_a_summary_with_no_short_first_sentence_stands_whole_under_the_pages_headline(states):
    long = "The launch is slow for many reasons at once and " + "no single one of them stands out, " * 6 + "at all."
    page = page_of(changed(states["one"], summary=long))
    check("the page's own headline", page.one("h1").text == "What the investigation found")
    check("and the summary whole under it", long in page.one("p", "lead").text)


def test_a_text_given_with_the_verdict_is_said_too(states):
    hunt, reports = copy.deepcopy(states["one"])
    hunt["conclusion"] = "Fixed in the 2.5 branch already."
    check("`--done \"<text>\" --verdict` keeps both, and so does the page",
          "Fixed in the 2.5 branch already." in read(answer_page.render(hunt, reports)).one("main").text)


# --- the piece that was found against the rest --------------------------------------------

def test_the_split_bar_is_the_piece_inside_against_the_rest_of_the_window(states):
    page = page_of(states["many"])
    bar = page.one("div", "split")
    parts = bar.all("i")
    check("two parts, in the share each has of the window",
          [p.attrs["style"] for p in parts] == ["flex:923.8 1 0", "flex:212.25 1 0"], [p.attrs["style"] for p in parts])
    check("each with its figure printed in it", [p.text for p in parts] == ["0.9 s", "0.2 s"])
    key = page.one("p", "split-k").text
    check("the key names the piece as the report names it, and the rest in words",
          "collection_load, 0.9 s, 81%" in key and "the rest of the launch, 0.2 s, 19%" in key, key)
    check("and where it was measured", key.endswith("measured in round 1, median of 5 launches"), key)
    check("the exact figures are a tooltip away, by keyboard too",
          parts[0].attrs["tabindex"] == "0" and parts[0].attrs["data-tip"].split("\n")
          == ["collection_load", "923.80 ms", "81.3% of the launch", "median of 5 launches, round 1"])
    check("it is a figure, not as wide as the table", "fig" in bar.parent.parent.attrs["class"])


def test_no_split_bar_without_a_piece_that_is_a_part_of_the_window(states):
    hunt, reports = changed(states["many"])
    del hunt["verdict"]["inside"]
    check("none when the verdict names no piece", not read(answer_page.render(hunt, reports)).all("div", "split"))
    check("none when its report is gone", not page_of(changed(states["gone"], inside={"report": 2, "location": "AGENTTMP_find_item"})).all("div", "split"))
    hunt, reports = changed(states["many"])
    reports[1]["markers"]["rows"][0]["total_ms"] = 5000.0
    check("none when the row is longer than the window: it is no part of it",
          not read(answer_page.render(hunt, reports)).all("div", "split"))
    hunt, reports = changed(states["many"], inside=["collection_load"])
    check("and none for an `inside` that does not read", not read(answer_page.render(hunt, reports)).all("div", "split"))


def test_a_part_too_narrow_for_its_figure_keeps_it_in_the_key():
    bar = read(kit.split([{"value": 970, "figure": "1.0 s", "name_html": "<code>a</code>", "tip": ["a"]},
                          {"value": 30, "figure": "30 ms", "name_html": "the rest"}], "measured in round 1"))
    thin = bar.all("i", "s-b")[0]
    check("no figure inside a sliver", thin.text == "" and "s-t" in thin.attrs["class"])
    check("and none is focusable without exact figures to show", "tabindex" not in thin.attrs)
    check("the key still says it", "the rest, 30 ms, 3%" in bar.one("p").text, bar.one("p").text)


# --- the table of causes --------------------------------------------------------------------

def test_a_cause_is_a_row_with_where_what_to_do_and_what_to_expect(states):
    hunt, _ = states["many"]
    page = page_of(states["many"])
    table = page.one("div", "tb")
    check("a table for a screen reader, named", table.attrs["role"] == "table"
          and table.attrs["aria-label"] == "What it found, and what to do")
    check("its head", [h.text for h in table.all("span", role="columnheader")]
          == ["Cause", "Where", "What to do", "Expected back", "Details"])
    rows = rows_of(page)
    check("the causes in the order the agent gave them, then what belongs to none",
          list(rows) == ["cause-1", "cause-2", "cause-3", "more-1"], list(rows))
    cause = hunt["verdict"]["causes"][1]
    row = rows["cause-2"]
    check("the title is the verdict's", row.one("h3").text == cause["title"])
    check("what happens is its mechanism, a name in backticks as code",
          row.one("p", "tr-s").text == cause["mechanism"].replace("`", "")
          and [c.text for c in row.one("p", "tr-s").all("code")] == ["find", "update"])
    check("every place in the code, each on a line of its own, whole",
          places_of(row.one("div", "c-wh")) == cause["where"])
    check("read file first: the file with its lines in ink, then the folders it is in",
          [c.text for c in row.one("div", "c-wh").all("code")] == ["StoreRepository.kt:30", "StoreRepository.kt:61"]
          and [s.text for s in row.one("div", "c-wh").all("span", "at-in")]
          == ["feature/collection/src/main/java/com/example/app/data/"] * 2)
    check("the same in the panel", places_of(panel_of(page, "cause-2").one("dl", "do")) == cause["where"]
          and [c.text for c in panel_of(page, "cause-2").one("dl", "do").all("code", "at")]
          == ["StoreRepository.kt:30", "StoreRepository.kt:61"])
    check("what to do is its fix", row.one("div", "c-do").text == cause["fix"].replace("`", ""))
    check("every cell carries its column's name for the widths with no head",
          [c.attrs.get("data-label") for c in row.all("div", role="cell")]
          == [None, "Where", "What to do", "Expected back", None])
    check("and one way in for the keyboard", row.one("button", "rowbtn").attrs["aria-label"] == f"Details: {cause['title']}")
    said = page.one("section").one("p", "sub").text
    check("the line under the heading counts them and says which figures are whose",
          said == "3 causes, in the order the agent gave them. The last row is a change that is not tied to one "
                  'cause. A figure in bold was measured. "Expected back" is the agent\'s estimate of what the change wins.',
          said)


def test_a_cause_carries_no_level(states):
    """A level is worked out by the engine from measurements; a cause has the
    figures the agent gave."""
    for name in ("many", "one", "long", "gone"):
        page = page_of(states[name])
        pills = page.all("span", "st")
        check(f"{name}: the one pill is the state of the investigation, in no status colour",
              len(pills) == 1 and not LEVELS & set(pills[0].attrs["class"].split()), [p.attrs for p in pills])
        for node in list(rows_of(page).values()) + page.all("template"):
            check(f"{name}: no level's icon in a row or in its panel", not node.all("svg", "ico"))


def test_what_was_measured_and_what_was_estimated_never_look_the_same(states):
    page = page_of(states["many"])
    rows = rows_of(page)
    cost = rows["cause-1"].one("p", "tr-m")
    check("what it costs is a figure in bold, said to be measured, with what it measures",
          cost.one("b").text == "223 ms"
          and cost.text == "223 ms measured: of the main thread's own work, as inflate measured it", cost.text)
    check("a range as it was given", rows["cause-2"].one("p", "tr-m").text == "62 to 66 ms measured: the main thread spent waiting")
    back = rows["cause-1"].one("div", "c-bk")
    check("what is expected back stands in a column of its own, and is never bold",
          back.text == "120 to 160 ms" and not back.all("b"))
    check("one number of the agent's reads as an estimate", rows["cause-2"].one("div", "c-bk").text == "about 60 ms")
    bare = rows["cause-3"]
    check("a cause with neither shows neither", not bare.all("p", "tr-m") and bare.one("div", "c-bk").text == ""
          and "none" in bare.one("div", "c-bk").attrs["class"])
    check("and one with no fix has an empty cell, marked so that it takes no room in a list",
          bare.one("div", "c-do").text == "" and "no-do" in bare.attrs["class"])
    panel = panel_of(page, "cause-1")
    labels = [(d.one("dt").text, d.one("dd").text) for d in panel.one("dl", "do").all("div")]
    check("the panel says the estimate is one, in words",
          labels[2] == ("Expected back", "120 to 160 msThe agent's estimate, not a measurement."), labels)
    check("and the measured cost above the title", panel.kids[0].text.startswith("223 ms measured"))


@pytest.mark.parametrize("value, said", [
    (223, "223 ms"), ([120, 160], "120 to 160 ms"), (1150, "1.15 s"), ([800, 1200], "0.8 to 1.2 s"),
    ([1000, 1400], "1 to 1.4 s"), (0.4, "0.4 ms"), (60, "60 ms"), (0, "0 ms"), ([62, 62], "62 ms"), (12400, "12.4 s")])
def test_a_figure_of_the_agents_is_kept_as_exact_as_it_was_given(value, said):
    check(f"{value} reads {said}", answer_page._hum(answer_page._span(value)) == said,
          answer_page._hum(answer_page._span(value)))


@pytest.mark.parametrize("value", ["300", None, [1], [3, 2], [1, 2, 3], -1, True, float("nan"), {"low": 1}])
def test_a_figure_that_does_not_read_is_left_out(value):
    check(f"{value!r} is no figure", answer_page._span(value) is None)


def test_the_suggestions_are_the_rows_after_the_causes(states):
    hunt, _ = states["many"]
    page = page_of(states["many"])
    row = rows_of(page)["more-1"]
    check("named by what it is", row.one("h3").text == "A change not tied to one cause"
          and row.one("p", "tr-s").text == "It belongs to no single cause above.")
    check("the change is the suggestion's, with what it is expected to win",
          row.one("div", "c-do").text == hunt["verdict"]["suggestions"][0]["fix"]
          and row.one("div", "c-bk").text == "600 to 800 ms")
    check("it names no place, and says so to the layout", row.one("div", "c-wh").text == "" and "no-wh" in row.attrs["class"])
    panel = panel_of(page, "more-1")
    check("its panel has the change, and says it stands on no measurement of its own",
          [d.one("dt").text for d in panel.one("dl", "do").all("div")] == ["What to do", "Expected back"]
          and "It cites no measurement of its own." in panel.text and not panel.all("div", "chart"))
    two = page_of(changed(states["many"], suggestions=[{"fix": "One."}, {"fix": "Another.", "expected_ms": 5},
                                                       {"expected_ms": 5}, "x"]))
    check("several are told apart, and what is no suggestion is left out",
          [r.one("h3").text for k, r in rows_of(two).items() if k.startswith("more")]
          == ["A change not tied to one cause (1 of 2)", "A change not tied to one cause (2 of 2)"])
    check("and the line under the heading counts them",
          "The last 2 rows are changes that are not tied to one cause." in two.one("section").one("p", "sub").text)


# --- the evidence -------------------------------------------------------------------------

def test_details_shows_the_rows_a_cause_cites_from_the_round_they_were_measured_in(states):
    hunt, reports = states["many"]
    page = page_of(states["many"])
    panel = panel_of(page, "cause-1")
    check("the panel is labelled by its title", panel.one("h2").attrs["id"] == "peek-title"
          and panel.one("h2").text == hunt["verdict"]["causes"][0]["title"])
    check("the technical layer is under a hairline, named Details", panel.one("div", "deep").one("h3").text == "Details")
    check("how sure the agent is, in its words", panel.one("dl", "props").text
          == "How sureHigh. five launches in each of two rounds, within 10% of each other")
    rows = chart_rows(panel)
    check("one bar per row cited, in the order cited", list(rows) == ["inflate", "AGENTTMP_bind_item_cards"], list(rows))
    check("a detector's row says its round and the check it is a row of, in the report's plain words",
          rows["inflate"]["subs"] == ['round 1 · the check "Heavy work on the main thread"', "com.example.app"],
          rows["inflate"]["subs"])
    check("drawn by the column the report judges it by, with a dot per launch",
          rows["inflate"]["figure"] == "223 ms" and rows["inflate"]["dots"] == 5)
    marker = rows["AGENTTMP_bind_item_cards"]
    check("a marker's row says it is one of the agent's, from its round",
          marker["subs"][0] == "round 2 · a temporary marker", marker["subs"])
    check("and is drawn by the time between its two ends, its own work a tooltip away",
          marker["figure"] == "237 ms" and marker["dots"] == 5
          and "of that, its own work: 38.40 ms" in marker["tip"], marker)
    check("the figures are the reports', not copied into the record",
          "236.9" not in json.dumps(hunt) and reports[2]["markers"]["rows"][0]["total_ms"] == 236.9)
    check("the key under the chart", panel.one("p", "key").text == "medianeach launch")
    own = chart_rows(panel_of(page, "cause-2"))["com.example.app"]
    check("a name the project gave a marker is told from a temporary one",
          own["subs"][0] == 'round 1 · the check "Threads waiting for each other"', own["subs"])
    hunt, reports = changed(states["many"], causes=[{"title": "T", "where": ["a.kt:1"], "evidence": [
        {"report": 1, "location": "collection_load"}, {"report": 1, "location": "inflate"}]}])
    rows = chart_rows(panel_of(read(answer_page.render(hunt, reports)), "cause-1"))
    check("one of the project's own markers says so", rows["collection_load"]["subs"][0]
          == "round 1 · one of the project's own markers", rows["collection_load"]["subs"])


def test_a_report_deleted_since_says_so_in_words_and_the_page_is_still_written(states):
    page = page_of(states["gone"])
    panel = panel_of(page, "cause-1")
    check("the row whose report is there is still drawn", list(chart_rows(panel)) == ["inflate"])
    gone = [p.text for p in panel.all("p", "gone")]
    check("the other says, in words, that it cannot be shown and why",
          gone == ["Cited from round 2, AGENTTMP_bind_item_cards, and it cannot be shown any more: report 2 is not "
                   "one of this investigation's reports (it has 1)."], gone)
    trust = fold(page, "How sure the agent is")
    said = [(li.one("b").text, li.one("p").text) for li in trust.one("ul", "quiet-list").all("li")]
    check("what echolot looked at says how many are gone, and that they were there when it closed",
          said[0] == ("Some measurements it cites cannot be shown",
                      "The answer points at 6 rows of this investigation's reports. 2 of them are not in the "
                      "reports on disk any more. They were there when the investigation closed: the answer was "
                      "checked against them then."), said[0])
    rounds = fold(page, "How it got there, round by round")
    check("the rounds are the reports that are there", [li.all("b")[0].text for li in rounds.one("ol").all("li")] == ["Round 1"])
    about = dict((d.one("dt").text, d.one("dd").text) for d in fold(page, "About this investigation").one("dl").all("div"))
    check("and the record still knows how many were filed",
          about["Rounds"] == "1 round, 5 recordings in all. 2 reports were filed, and 1 of them is no longer on disk",
          about["Rounds"])
    hunt, _ = states["gone"]
    none = read(answer_page.render(hunt, {}))
    check("with every report gone the page is written all the same",
          len(rows_of(none)) == 4 and not none.all("div", "chart") and not none.all("div", "split")
          and "this investigation has no archived reports" in panel_of(none, "cause-1").text)


def test_a_report_that_does_not_read_and_a_citation_that_does_not(states):
    hunt, reports = copy.deepcopy(states["many"])
    reports[2] = None
    hunt["verdict"]["causes"][2]["evidence"] = ["inflate", {"report": 1, "location": "no such row"}]
    page = read(answer_page.render(hunt, reports))
    check("a report filed and unreadable is said as that, in the panel",
          "report 2 is filed but does not read as a report" in panel_of(page, "cause-1").text)
    rounds = fold(page, "How it got there, round by round")
    check("and in its round", rounds.one("ol").all("li")[1].text
          == "Round 2Its report is on disk and does not read as a report.")
    said = [p.text for p in panel_of(page, "cause-3").all("p", "gone")]
    check("a citation that is none, and one of a row that is not there, each in a sentence",
          said == ["A measurement this cause cites cannot be shown: it does not read as a citation.",
                   'Cited from round 1, no such row, and it cannot be shown any more: report 1 has no row at "no such '
                   'row", in any detector or among its markers.'], said)
    reports[1]["detectors"][8]["rows"][0]["spread"] = ["not", "a", "spread"]
    hunt["verdict"]["causes"][2]["evidence"] = [{"report": 1, "detector": reports[1]["detectors"][8]["id"],
                                                 "location": reports[1]["detectors"][8]["rows"][0]["location"]}]
    said = [p.text for p in panel_of(read(answer_page.render(hunt, reports)), "cause-3").all("p", "gone")]
    check("a row that is there and whose figures do not read is said as that",
          len(said) == 1 and said[0].endswith("the row is in the report and its figures do not read."), said)
    check("a detector the report does not list is named by its id", answer_page._source({}, "some_check", "x") == "some_check")
    hunt["verdict"]["causes"][2]["evidence"] = []
    check("a cause that cites nothing says so",
          "The answer cites no measurement for this cause." in panel_of(read(answer_page.render(hunt, reports)), "cause-3").text)


# --- the states -----------------------------------------------------------------------------

def test_one_cause(states):
    page = page_of(states["one"], again=fx.AGAIN)
    check("one row", list(rows_of(page)) == ["cause-1"])
    check("said as one", page.one("section").one("p", "sub").text.startswith("One cause. A figure in bold was measured."))
    check("the rest of the summary stands under the headline as the agent wrote it",
          page.one("p", "lead").text == "Three things inside it cost the most.")
    check("no fold for lists the verdict does not have",
          [d.one("summary").text for d in page.all("details")]
          == ["How sure the agent is", "How it got there, round by round", "About this investigation"])
    check("the panel counts one of one for a script to say", page.one("dialog", "peek") is not None)


def test_a_verdict_with_no_cause_is_a_result_and_is_said_plainly(states):
    page = page_of(states["nothing"], again=fx.AGAIN)
    main_text = page.one("main").text
    check("the summary is the headline, and a summary of one sentence has nothing under it",
          page.one("h1").text == "Nothing stands out: the launch is spread thin over many small pieces of work"
          and not page.all("p", "lead"))
    check("it is finished, like any other", page.one("span", "st").text == "Investigation finished")
    check("said plainly, as a result", page.one("section").one("h2").text == "No cause found"
          and "That is a result, not a failure" in main_text)
    check("what was looked at is shown at once, not under a fold",
          [h.text for h in page.one("section").all("h3")] == ["Ruled out", "Also measured"]
          and not any(d.one("summary").text.startswith("What was ruled out") for d in page.all("details")))
    check("each line as the agent wrote it", [li.text for li in page.one("section").all("li")]
          == fx.VERDICT["ruled_out"] + fx.VERDICT["also_measured"])
    check("no table, no panel, and nothing to check again",
          not page.all("div", "tb") and not page.all("dialog") and not page.all("template")
          and not page.all("div", id="check-again"))
    trust = fold(page, "What echolot looked at itself")
    check("with no cause there is nothing to be sure of: the fold is what echolot looked at, and the count is still said",
          "The agent says it added no temporary markers, and echolot found none in the sources when the "
          "investigation closed." in trust.text, trust.text)


def test_an_investigation_closed_with_a_text_shows_the_text_and_its_rounds(states):
    page = page_of(states["text"], again=fx.AGAIN)
    check("finished", page.one("span", "st").text == "Investigation finished")
    check("the first sentence of the text is the headline",
          page.one("h1").text == "The first screen waits for the whole collection to be built")
    check("the rest of its first paragraph under it", page.one("p", "lead").text
          == "Inflating every item card on the main thread costs about 0.2 s, and a lock in StoreRepository about 60 ms more.")
    main_text = page.one("main").text
    check("the paragraphs after it", "Build only the cards that fit the screen first." in main_text)
    check("and why there is no table", "closed with a text and no structured answer" in main_text
          and not page.all("div", "tb") and not page.all("dialog"))
    check("the rounds are there, and nothing about how sure an agent is",
          [d.one("summary").text for d in page.all("details")]
          == ["How it got there, round by round", "About this investigation"])
    check("the change can be checked", page.one("pre", "cmd-t").text == fx.AGAIN)


def test_an_open_investigation_shows_its_rounds_as_its_progress(states):
    page = page_of(states["open"], again=fx.AGAIN)
    state = top(page)
    check("in progress, with the rounds so far",
          state.one("span", "st").text == "Investigation in progress" and state.one("span", "sub").text == "2 rounds so far")
    check("the question is the headline", page.one("h1").text == fx.QUESTION)
    check("no answer is claimed", page.one("p", "lead").text.startswith("No answer yet."))
    section = page.one("section")
    check("the rounds are on the page itself, not under a fold",
          section.one("h2").text == "The rounds so far" and [li.all("b")[0].text for li in section.one("ol").all("li")]
          == ["Round 1", "Round 2"] and not section.inside("details"))
    check("nothing to check again, nothing to open, and only the fold about it",
          not page.all("div", id="check-again") and not page.all("dialog")
          and [d.one("summary").text for d in page.all("details")] == ["About this investigation"])
    about = dict((d.one("dt").text, d.one("dd").text) for d in page.one("dl", "props").all("div"))
    check("when it was last worked on, and no closing", about["Last worked on"] == "2026-03-02 09:16 UTC" and "Closed" not in about)
    empty = page_of(states["open-empty"])
    check("before the first round it says so", top(empty).one("span", "sub").text == "no round yet"
          and "No round is on record yet." in empty.one("section").text and not empty.all("ol"))
    check("and the top bar has only what is known", empty.one("span", "crumb").text == "coldStart")


def test_an_investigation_left_unfinished_says_so(states):
    page = page_of(states["left"])
    check("its state", page.one("span", "st").text == "Investigation left unfinished")
    check("why there is no answer", page.one("p", "lead").text
          == "It was left without an answer: the next question was opened before this one was closed.")
    check("and what it went through", page.one("section").one("h2").text == "The rounds it went through"
          and len(page.one("section").one("ol").all("li")) == 1)
    hunt, reports = copy.deepcopy(states["left"])
    hunt["status"] = "paused"
    check("a status this version does not know claims nothing",
          read(answer_page.render(hunt, reports)).one("p", "lead").text == "It has no answer on record.")


def test_long_names_and_long_places_are_kept_whole(states):
    hunt, _ = states["long"]
    cause = hunt["verdict"]["causes"][0]
    text = answer_page.render(*states["long"])
    page = read(text)
    row = rows_of(page)["cause-1"]
    check("the title whole, in the row and in the panel",
          row.one("h3").text == cause["title"] == panel_of(page, "cause-1").one("h2").text)
    check("each place whole: nothing is cut", places_of(row.one("div", "c-wh")) == cause["where"])
    lines = row.one("div", "c-wh").all("span", "at-l")
    check("the file is what stands first, however long its name",
          [line.one("code", "at").text for line in lines]
          == ["CollectionItemCardAdapterWithAVeryLongNameThatHasNoSlashToBreakAt.kt:41-58", "CollectionAdapter.kt:41-58"])
    check("the folders may break after every slash",
          all(len(line.one("span", "at-in").all("wbr")) == line.one("span", "at-in").text.count("/") for line in lines))
    check("a break is offered after the slash, not before it", "main/<wbr>java/<wbr>com" in text)


def test_a_record_edited_by_hand_still_gives_a_page(states):
    """A field of the wrong kind is left out; nothing fails."""
    _, reports = states["many"]
    odd = {"n": "three", "question": 7, "status": "concluded", "opened_at": "yesterday", "closed_at": 5,
           "reports": "many", "scenario": ["x"],
           "verdict": {"summary": ["no"], "inside": "collection_load", "causes": [
               "x", {"title": 5, "where": "a.kt", "mechanism": ["m"], "fix": 9, "cost_ms": "1.2 s", "cost_of": 4,
                     "expected_ms": [5, 1], "confidence": 3, "confidence_why": {}, "evidence": "inflate"},
               {"title": "A real one", "where": ["a.kt:1", 5, ""], "evidence": [{"report": 1, "location": "inflate"}]}],
               "suggestions": {"fix": "x"}, "ruled_out": "all of it", "also_measured": [5, "", "one line"]},
           "verdict_check": {"cleanup": "removed", "cited": "six"}}
    page = read(answer_page.render(odd, reports))
    rows = rows_of(page)
    check("the causes that are objects are rows", [r.one("h3").text for r in rows.values()] == ["(no title)", "A real one"])
    check("with the places that read", [c.text for c in rows["cause-2"].one("div", "c-wh").all("code")] == ["a.kt:1"])
    check("and what did not read is left out", not rows["cause-1"].all("p", "tr-m")
          and rows["cause-1"].one("div", "c-bk").text == "" and not page.all("div", "split"))
    check("the lists keep the lines that are text",
          [li.text for li in fold(page, "What was ruled out, and what else was measured").all("li")] == ["one line"])
    for hunt, held in (({}, {}), ({"status": "concluded"}, {}), ({"verdict": {}}, reports),
                       ({"status": "open", "question": "q"}, {1: {}, 2: {"window": 5, "markers": [], "summary": "x"}}),
                       ({"verdict": {"causes": []}, "status": "concluded"}, {1: {"detectors": None}})):
        text = answer_page.render(hunt, held)
        check(f"{str(hunt)[:40]}: a page, one headline, nothing that points outside",
              len(read(text).all("h1")) == 1 and not outside(text))


# --- the folds ------------------------------------------------------------------------------

def test_how_it_got_there_is_read_from_each_rounds_report(states):
    page = page_of(states["many"])
    rounds = fold(page, "How it got there, round by round")
    items = rounds.one("ol", "timeline").all("li")
    check("a round per report, the oldest first", [li.all("b")[0].text for li in items] == ["Round 1", "Round 2"])
    check("when its report was written, from when the investigation opened",
          [li.one("span", "when").text for li in items] == ["after 1 min 17 s", "after 7 min 37 s"])
    cards = [li.one("div", "repcard") for li in items]
    check("the window, the recordings, how many checks found something, the agent's markers",
          [c.all("p")[0].text for c in cards]
          == ["The launch takes 1.1 s · 5 launches recorded · 5 of 16 checks found something · no temporary markers in the app",
              "The launch takes 1.1 s · 5 launches recorded · 5 of 16 checks found something · 2 temporary markers in the app"],
          [c.all("p")[0].text for c in cards])
    check("how much of the main thread's window the rows name, as the report holds it",
          cards[0].one("div", "meter-h").text == "Rows name this much of the main thread's window42%"
          and cards[0].one("div", "meter").one("i").attrs["style"] == "width:42.0%")
    check("and which of its rows the answer cites",
          [c.all("p")[1].text for c in cards]
          == ["The answer cites 4 rows of this round: collection_load, inflate, com.example.app, TextLayout:initLayout.",
              "The answer cites 2 rows of this round: AGENTTMP_bind_item_cards, AGENTTMP_find_item."],
          [c.all("p")[1].text for c in cards])
    hunt, reports = copy.deepcopy(states["open"])
    del reports[1]["generated_at"], reports[1]["window"]["main_thread"]["in_rows_pct"], reports[1]["markers"]["prefix"]
    del reports[2]["markers"]["in_trace"]
    reports[1]["runs"] = 1
    reports[1]["traces"] = reports[1]["traces"][:1]
    items = read(answer_page.render(hunt, reports)).one("ol").all("li")
    check("what a report does not hold is left out, not guessed",
          items[0].one("span", "when").text == "" and not items[0].all("div", "meter")
          and items[0].one("p").text == "The launch takes 1.1 s · 1 launch recorded · 5 of 16 checks found something")
    check("an older report's markers are counted from its rows",
          items[1].one("p").text.endswith("2 temporary markers in the app"))


def test_how_sure_is_the_agents_words_and_what_echolot_looked_at_itself(states):
    page = page_of(states["many"])
    trust = fold(page, "How sure the agent is")
    said = [(li.all("b")[0].text, li.one("p").text) for li in trust.one("ul", "sure").all("li")]
    check("per cause, the agent's own word and why",
          said == [("Every item card is inflated on the main thread, one after another",
                    "High. five launches in each of two rounds, within 10% of each other"),
                   ("The main thread waits for a lock the store holds",
                    "Medium. the wait is measured; that the snapshot removes all of it is not"),
                   ("Text is laid out again for every card", "The agent did not say.")], said)
    check("the agent's word has no level's icon", not trust.one("ul", "sure").all("svg"))
    seen = [(li.one("b").text, li.one("p").text) for li in trust.one("ul", "quiet-list").all("li")]
    check("then what echolot looked at: the rows cited, and the markers in the code",
          seen == [("Every measurement it cites is in the reports",
                    "The answer points at 6 rows of this investigation's reports, and all of them are there."),
                   ("Nothing was left in the code",
                    "The agent says it removed its temporary markers, and echolot found none in the sources when "
                    "the investigation closed.")], seen)
    hunt, reports = copy.deepcopy(states["one"])
    for cause in hunt["verdict"]["causes"]:
        cause.pop("confidence"), cause.pop("confidence_why")
    check("an agent that did not say is said not to have",
          "The agent did not say how sure it is of what it found." in fold(read(answer_page.render(hunt, reports)),
                                                                           "How sure the agent is").text)


@pytest.mark.parametrize("claimed, left, title, said", [
    ("removed", {"markers": 3, "removable": 2, "lines": 2, "files": ["a.kt", "b.kt"], "prefix": "AGENTTMP_"},
     "Temporary markers are still in the code",
     "The agent says it removed its temporary markers, and echolot found 3 markers named AGENTTMP_… in 2 files when "
     "the investigation closed. The command echolot mark --remove takes out 2 of them."),
    (None, {"markers": 1, "removable": 0, "lines": 0, "files": ["a.kt"], "prefix": "PERF_"},
     "Temporary markers are still in the code",
     "Echolot found 1 marker named PERF_… in 1 file when the investigation closed."),
    ("removed", {"markers": 0, "removable": 0, "lines": 2, "files": ["a.kt"], "prefix": "AGENTTMP_"},
     "Temporary markers are still in the code",
     "The agent says it removed its temporary markers, and echolot found 2 lines tagged echolot:mark in 1 file when "
     "the investigation closed."),
    ("left", fx.CLEAN, "The agent and echolot disagree about the markers",
     "The agent says it left temporary markers in the code, and echolot found none in the sources when the "
     "investigation closed."),
    (None, fx.CLEAN, "Nothing was left in the code",
     "echolot found no temporary markers in the sources when the investigation closed."),
])
def test_the_markers_echolot_counted_stand_beside_what_the_agent_said(states, claimed, left, title, said):
    hunt, reports = copy.deepcopy(states["one"])
    hunt["verdict"].pop("cleanup")
    if claimed:
        hunt["verdict"]["cleanup"] = claimed
    hunt["verdict_check"] = fx.check_of(hunt["verdict"], reports, left)
    last = fold(read(answer_page.render(hunt, reports)), "How sure the agent is").one("ul", "quiet-list").all("li")[-1]
    check(f"{claimed}, {left['markers']} left: {title}", (last.one("b").text, last.one("p").text) == (title, said),
          (last.one("b").text, last.one("p").text))
    check("a level of echolot's own has its icon", len(last.all("svg", "ico")) == 1)


def test_what_was_ruled_out_and_what_else_was_measured(states):
    page = page_of(states["many"])
    lists = fold(page, "What was ruled out, and what else was measured")
    check("two lists, each named", [h.text for h in lists.all("h3")] == ["Ruled out", "Also measured"])
    check("each line as the agent wrote it, in its order",
          [li.text for li in lists.all("li")] == fx.VERDICT["ruled_out"] + fx.VERDICT["also_measured"])
    only = page_of(changed(states["many"], ruled_out=[], also_measured=["a line\nand its second line"]))
    lists = fold(only, "What was ruled out, and what else was measured")
    check("an empty list is not shown, and a line break stays one",
          [h.text for h in lists.all("h3")] == ["Also measured"] and len(lists.one("li").all("br")) == 1)


def test_about_this_investigation(states):
    page = page_of(states["many"])
    about = [(d.one("dt").text, d.one("dd").text) for d in fold(page, "About this investigation").one("dl", "props").all("div")]
    check("what was asked, after which change, where and when, and where the files are",
          about == [("Asked", fx.QUESTION), ("After which change", "the 2.4 release"), ("Scenario", "coldStart"),
                    ("Opened", "2026-03-02 09:00 UTC"), ("Closed", "2026-03-02 09:18 UTC, after 18 min 35 s"),
                    ("Rounds", "2 rounds, 10 recordings in all"),
                    ("Files", "The reports of the rounds are in the folder reports beside this page. "
                              "echolot hunt --show 3 prints this investigation as text.")], about)
    check("the folds, in their order", [d.one("summary").text for d in page.all("details")]
          == ["How sure the agent is", "How it got there, round by round",
              "What was ruled out, and what else was measured", "About this investigation"])
    check("each a large one", all("big" in d.attrs["class"] for d in page.all("details")))


@pytest.mark.parametrize("seconds, said", [(0, "0 s"), (45, "45 s"), (60, "1 min"), (1115, "18 min 35 s"),
                                           (3600, "1 h"), (7500, "2 h 5 min"), (172800, "2 days"), (300000, "3 days")])
def test_how_long_something_took(seconds, said):
    check(f"{seconds} s reads {said}", answer_page._lasted(seconds) == said, answer_page._lasted(seconds))


def test_a_moment_reads_the_same_in_every_time_zone():
    check("in the one zone that needs no clock", answer_page._stamp("2026-03-02T10:00:00+01:00") == "2026-03-02 09:00 UTC")
    check("a stamp with no zone is taken as written", answer_page._stamp("2026-03-02T09:00:00") == "2026-03-02 09:00 UTC")
    check("and one that does not parse is shown as it is", answer_page._stamp("last Tuesday") == "last Tuesday")
    check("an end before its start is no span", answer_page._between("2026-03-02T09:00:00+00:00", "2026-03-01T09:00:00+00:00") is None)


# --- checking again -------------------------------------------------------------------------

def test_check_again_is_a_sentence_a_command_to_copy_and_one_place_for_a_button(states):
    page = page_of(states["many"], again=fx.AGAIN)
    block = page.one("div", id="check-again")
    check("after the table, before the folds", block.parent.kids.index(block) > block.parent.kids.index(page.one("section")))
    check("one sentence", block.one("p").text == "Changed the code? Check the app again: this records it once more "
                                                 "and shows what moved since the first round.")
    check("the command as it was given, in a box that takes focus",
          block.one("pre").text == fx.AGAIN and block.one("pre").attrs["tabindex"] == "0")
    button = block.one("button")
    check("Copy needs script and is not shown without it",
          button.text == "Copy" and "needs-js" in button.attrs["class"] and button.attrs["data-copy"] == block.one("pre").attrs["id"])
    check("what it did is said to a screen reader", page.one("span", id=button.attrs["data-said"]).attrs["role"] == "status")
    slot = page.one("div", id="check-again-action")
    check("the one place for a button that can record is there and empty in a file", slot.kids == [])
    served = page_of(states["many"], again=fx.AGAIN, action_html='<button type="button" class="btn primary">Check again</button>')
    check("a page that is served puts its button there",
          served.one("div", id="check-again-action").one("button").text == "Check again")
    bare = page_of(states["many"])
    check("with no command to give, the sentence says what to do and there is nothing to copy",
          bare.one("div", id="check-again").one("p").text == "Changed the code? Check the app again: record it once "
          "more and compare it with the first round." and not bare.all("pre"))


def test_what_a_reader_gets_with_script_switched_off(states):
    page = page_of(states["many"], again=fx.AGAIN)
    check("the root says so until a script runs", page.one("html").attrs["class"] == "no-js")
    note = page.one("noscript").text
    check("a note says what is there and what is not", "Script is switched off" in note and "reports of the rounds" in note)
    check("what only script can work is marked", all("needs-js" in b.attrs["class"] for b in page.all("button", "rowbtn")))
    check("every cause, its place and its change are in the page itself, outside any template",
          all(not r.inside("template") for r in rows_of(page).values()))
    check("and the folds need no script", len(page.all("details")) == 4)
    check("a page with no table has no such note", not page_of(states["open"]).all("noscript"))


# --- made of the shared pieces --------------------------------------------------------------

def test_the_state_of_an_investigation_is_no_level():
    for kind in kit.STATES:
        pill = read(kit.state("A word", kind, large=True)).one("span")
        check(f"{kind}: an icon and a word, as a level has", pill.text == "A word" and len(pill.all("svg")) == 1)
        check(f"{kind}: in ink, on no status colour", pill.attrs["class"] == "st plain lg"
              and not re.search(r"--(red|amber|green|grey)", kit.state("x", kind)))


def test_label_and_value_and_a_place_in_the_code():
    block = read(kit.pairs([("Where", "<code>a</code>"), ("What <to> do", "it")], "do")).one("dl")
    check("rows of a label and a value", block.attrs["class"] == "do"
          and [(d.one("dt").text, d.one("dd").text) for d in block.all("div")] == [("Where", "a"), ("What <to> do", "it")])
    check("a list of facts by default", read(kit.pairs([("a", "b")])).one("dl").attrs["class"] == "props")
    place = read(kit.place("a/b<c>/d.kt:1")).one("span", "at-p")
    check("a place reads file first: the file with its lines as code",
          place.kids[0].tag == "code" and place.one("code", "at").text == "d.kt:1")
    check("then the folders it is in, with a break offered after each slash",
          place.one("span", "at-in").text == "a/b<c>/" and len(place.one("span", "at-in").all("wbr")) == 2)
    check("nothing is cut, and read aloud it is the file in its folders",
          place_of(place) == "a/b<c>/d.kt:1" and place.text == "d.kt:1 in a/b<c>/")
    for bare in ("Store.kt:12", "the database builder", "a/b/"):
        one = read(kit.place(bare))
        check(f"{bare!r} has no file to put first and is one line, as code",
              one.one("code", "at").text == bare and not one.all("span", "at-in"))


def test_every_colour_is_a_token():
    base = kit.asset("base.css")
    light = _blocks(base, ":root {")[0]
    dark = _blocks(base, ':root:not([data-theme="light"]) {') + _blocks(base, ':root[data-theme="dark"] {')
    rest = base
    for block in [light, *dark]:
        rest = rest.replace(block, "")
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    rest += kit.asset("answer.css")
    check("nothing below the tokens names a colour of its own, the answer's stylesheet included",
          not literal.search(rest), literal.findall(rest))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", kit.asset("answer.css")))
    defined = set(re.findall(r"(--[a-z0-9-]+):", base))
    check("and every token the answer uses is one the page defines", used <= defined, used - defined)
    icons = "".join(kit.state("x", k) for k in kit.STATES)
    check("nor does a state's icon", not literal.search(icons), literal.findall(icons))


def test_light_and_dark_and_a_phone(states):
    page = page_of(states["many"])
    css = page.one("style").text
    check("the viewport is the device's", page.one("meta", name="viewport").attrs["content"].startswith("width=device-width"))
    check("both schemes are announced", page.one("meta", name="color-scheme").attrs["content"] == "light dark")
    check("dark by the system's choice, and by an attribute",
          '@media (prefers-color-scheme: dark)' in css and ':root[data-theme="dark"]' in css)
    check("the table re-flows by the width it is given, at both steps",
          "@container (max-width: 899px)" in kit.asset("answer.css") and "@container (max-width: 559px)" in kit.asset("answer.css"))
    check("the language is said", page.one("html").attrs["lang"] == "en")


def test_the_pieces_ship_with_the_package(states):
    names = set(answer_page.CSS) | set(answer_page.JS)
    check("every stylesheet and script the page names is inside the package",
          all((PAGES / n).is_file() for n in names), sorted(n for n in names if not (PAGES / n).is_file()))
    check("it takes the shared ones first and its own last",
          answer_page.CSS == ("base.css", "table.css", "answer.css") and answer_page.JS == ("base.js", "table.js", "answer.js"))
    text = answer_page.render(*states["many"])
    at = [text.index(kit.asset(n)) for n in answer_page.CSS + answer_page.JS]
    check("each is written in whole, in the order named", at == sorted(at))
    check("the report's page is made of the same base", report_page.CSS[0] == answer_page.CSS[0])


def test_a_cited_row_is_drawn_the_way_the_report_draws_its_own():
    check("the report's page names its chart row for the other pages", report_page.chart_row is report_page._mark)
    source = (PAGES / "answer_page.py").read_text(encoding="utf-8")
    check("and the answer calls it by that name, and nothing of the report's page by a private one",
          "report_page.chart_row(" in source and not re.search(r"\b(report_page|rp)\._", source))
    check("how many recordings a report is of: what it says, else the traces it lists, else one",
          [answer_page._runs(r) for r in ({"runs": 3, "traces": ["a"]}, {"traces": ["a", "b"]}, {},
                                          {"runs": True, "traces": "ab"}, {"runs": 0})] == [3, 2, 1, 1, 1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the scripts")
@pytest.mark.parametrize("name", answer_page.JS)
def test_the_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(PAGES / name)], capture_output=True, text=True)
    check(f"{name} is JavaScript", done.returncode == 0, done.stderr)


def test_ids_are_unique_and_every_details_button_has_its_panel(states):
    for name, state in states.items():
        page = page_of(state, again=fx.AGAIN)
        ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs and not n.inside("template")]
        check(f"{name}: no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
        opens = {b.attrs["data-open"] for b in page.all("button", "rowbtn")}
        check(f"{name}: every Details button has its panel",
              opens == {t.attrs["id"][5:] for t in page.all("template")} == set(rows_of(page)))
        check(f"{name}: each panel has the title the dialog is labelled by",
              all(len(t.all("h2", id="peek-title")) == 1 for t in page.all("template")))
        check(f"{name}: one headline", len(page.all("h1")) == 1)
        check(f"{name}: the panel is there exactly when there is a row to open", bool(page.all("dialog", "peek")) == bool(opens))
