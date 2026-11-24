#!/usr/bin/env python3
"""The comparison as a page: `comparison.html`, beside `comparison.json`.

The page is drawn from a comparison and its plain reading (`echolot/moves.py`)
and adds labels alone, so what is pinned here is that:

  - it is one file that names no address, and the same comparison gives the
    same bytes;
  - every word about a check is the reading's, and a movement is an icon and
    a word, never a colour alone;
  - it says once, under the verdict, that a colour here is a direction;
  - `compare --html` writes it beside the comparison. That `echolot ui` serves
    the same page is in `tests/test_ui_after.py`.
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
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import echolot  # noqa: E402
from echolot import compare as compare_mod  # noqa: E402
from echolot import moves, recorder  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import compare_page, kit  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_moves import entry, known, report, row, steady  # noqa: E402
from tests.test_report_page import SCHEME, outside, read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(echolot.__file__).resolve().parent / "pages"


def comparison(before, after):
    """What `compare --html` builds: the comparison with its plain reading."""
    cmp = compare_mod.build(before, after, before_path="before.json", after_path="after.json")
    return moves.annotate(cmp, before, after)


def page_of(before, after, **kw):
    return read(compare_page.render(comparison(before, after), **kw))


def rows_of(page):
    return {r.attrs["data-id"]: r for r in page.all("div", "tr")}


def panel_of(page, did):
    return page.one("template", id=f"peek-mv-{did}")


@pytest.fixture(scope="module")
def demo(demo_report, demo_changed_report):
    """The demo app's five repeats against the same five with its one change planted."""
    return comparison(demo_report, demo_changed_report)


def every_movement():
    """A pair with a check of every movement in it."""
    def pair(did, b, a, **kw):
        return entry(did, b, **kw), entry(did, a, **kw)
    pairs = [
        pair("main_thread_waits", [steady("worker_pool_#", 700, detail="blocked in loadConfig")], []),
        pair("uninstrumented_cpu", [steady("worker_pool_0", 500), steady("worker_pool_1", 400)], [steady("worker_pool_0", 200)]),
        pair("main_thread_block", [steady("inflate", 300)], [steady("inflate", 600), steady("bind", 250)]),
        pair("repeated_work", [], [steady("loadConfig", 240, detail="entered from 2: a, b")]),
        pair("binder_txn", [row("main", 40, 200, 230)], [row("main", 45, 50, 52)]),
        pair("gc_pressure", [], []),
        pair("io_wait", [], [], checked=False, reason="The recording does not carry the phone's own state."),
    ]
    before = report([b for b, _ in pairs], launches=(1968.0, 2118.0, 2166.0), budget=520.0)
    after = report([a for _, a in pairs], launches=(1418.0, 1421.0, 1477.0), budget=520.0)
    return before, after


# --- one file, nothing fetched ----------------------------------------------------------


def test_the_page_names_no_address_of_any_kind(demo):
    for name, cmp in (("the demo", demo), ("by hand", comparison(*every_movement()))):
        text = compare_page.render(cmp)
        check(f"{name}: nothing loads and nothing reaches out", not outside(text), outside(text))
        check(f"{name}: no address anywhere in the file, as text either",
              not SCHEME.search(text) and "//www." not in text, SCHEME.findall(text)[:5])
        page = read(text)
        check(f"{name}: two scripts, both written into the file",
              [s.attrs for s in page.all("script")] == [{}, {}], [s.attrs for s in page.all("script")])
        check(f"{name}: one stylesheet, written into the file", len(page.all("style")) == 1)


def test_the_browser_is_told_to_hold_the_page_to_it():
    cmp = comparison(*known())
    strict = read(compare_page.render(cmp)).one("meta", http_equiv="Content-Security-Policy").attrs["content"]
    check("a page on disk fetches nothing and sends nothing", "default-src 'none'" in strict
          and "form-action 'none'" in strict and "connect-src" not in strict, strict)
    served = read(compare_page.render(cmp, policy=kit.SERVED)).one("meta", http_equiv="Content-Security-Policy")
    check("a served one carries the server's policy", served.attrs["content"] == kit.SERVED)


NASTY = '"><script src=https://example.com/x.js></script><img src=x onerror=alert(1)>`</template>&amp;'


def test_a_name_out_of_a_trace_cannot_become_markup():
    before = report([entry("main_thread_waits", [steady(NASTY, 700, detail=NASTY)])], launches=(1968.0, 2118.0, 2166.0))
    after = report([entry("main_thread_waits", [steady(NASTY, 100, detail=NASTY)])], launches=(1418.0, 1421.0, 1477.0))
    after["scenario"] = before["scenario"] = NASTY
    text = compare_page.render(comparison(before, after))
    page = read(text)
    check("one script before the body's and one after, no third", len(page.all("script")) == 2)
    check("no element a name could have brought", not page.all("img") and not outside(text), outside(text))
    check("the name is there, as text", NASTY.split("`")[0] in page.one("template").raw())


# --- the same comparison, the same bytes ------------------------------------------------


def test_the_same_comparison_gives_the_same_bytes(demo):
    first = compare_page.render(demo)
    check("twice the same", first == compare_page.render(copy.deepcopy(demo)))
    check("and it survives the file: read back from JSON, the same page",
          first == compare_page.render(json.loads(report_mod.to_json(demo))))


def test_the_bytes_do_not_depend_on_the_process(tmp_path):
    """No clock, no random number, no order a hash decides: the page is of the
    two reports, and of nothing about when or where it was drawn."""
    before, after = every_movement()
    (tmp_path / "before.json").write_text(report_mod.to_json(before), encoding="utf-8")
    (tmp_path / "after.json").write_text(report_mod.to_json(after), encoding="utf-8")
    code = ("import hashlib, json, sys; from echolot import compare, moves; from echolot.pages import compare_page; "
            "b, a = (json.load(open(p, encoding='utf-8')) for p in sys.argv[1:3]); "
            "cmp = moves.annotate(compare.build(b, a, before_path='before.json', after_path='after.json'), b, a); "
            "print(hashlib.sha256(compare_page.render(cmp).encode()).hexdigest())")
    here = hashlib.sha256(compare_page.render(comparison(before, after)).encode()).hexdigest()
    for seed in ("1", "4242"):
        time.sleep(1.1)   # the comparison is stamped to the second, and the page must not show it
        out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "before.json"), str(tmp_path / "after.json")],
                             cwd=ROOT, env={"PYTHONHASHSEED": seed, "PATH": "", "ECHOLOT_NO_RECORD": "1"},
                             capture_output=True, text=True, check=True).stdout.strip()
        check(f"hash seed {seed}, a second later", out == here, (out, here))


def test_rendering_leaves_the_comparison_as_it_was(demo):
    kept = copy.deepcopy(demo)
    compare_page.render(demo)
    check("nothing is written into what was read", demo == kept)


def test_a_comparison_without_its_reading_is_no_page():
    before, after = known()
    bare = compare_mod.build(before, after, before_path="before.json", after_path="after.json")
    check("it says it has none", not compare_page.has_reading(bare) and compare_page.has_reading(comparison(before, after)))
    with pytest.raises(ValueError, match="compare --html"):
        compare_page.render(bare)
    for broken in (None, [], {"plain": "yes"}, {"plain": {"verdict": {}, "checks": []}}):
        check(f"nor does {broken!r}", not compare_page.has_reading(broken))


def _fields(node, path=()):
    """Every place in a part of a comparison: the keys of its objects and the
    first items of its lists."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,)
            yield from _fields(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):
            yield path + (i,)
            yield from _fields(v, path + (i,))


ODD = ["zz", ["x"], {"a": 1}, 7, None, True, [{"a": 1}], -1.5]


def test_a_reading_of_the_wrong_shape_says_where_and_any_other_still_draws():
    """Each field of the reading, and of the rows, put to each odd kind of
    value: either the file says which part does not read (`misshapen`), or
    the page is drawn. It never fails (#733)."""
    before, after = every_movement()
    cmp = comparison(before, after)
    check("what compare writes reads", compare_page.misshapen(cmp) is None)
    places = [("plain", *p) for p in _fields(cmp["plain"])] + [("rows",)] + [("rows", 0, k) for k in cmp["rows"][0]]
    failed, said = [], set()
    for place in places:
        for value in ODD:
            odd = copy.deepcopy(cmp)
            node = odd
            for k in place[:-1]:
                node = node[k]
            node[place[-1]] = value
            if not compare_page.has_reading(odd):
                continue
            wrong = compare_page.misshapen(odd)
            if wrong:
                said.add(wrong)
                with pytest.raises(ValueError, match="does not read"):
                    compare_page.render(odd)
                continue
            try:
                compare_page.render(odd)
            except Exception as err:  # noqa: BLE001
                failed.append(f"{place} = {value!r}: {type(err).__name__}: {err}")
    check("every one is drawn or said not to read", not failed, "\n".join(failed[:20]))
    for named in ("`plain.notes` is text, not a list", "`plain.checks[0]` is text, not an object",
                  "`plain.checks[0].before` is text, not an object", "`plain.window.before` is text, not an object",
                  "`plain.window.after.runs` is text, not a whole number"):
        check(f"it names the part: {named}", named in said)


# --- top to bottom ----------------------------------------------------------------------


def test_the_page_from_the_verdict_to_the_technical_comparison():
    text = compare_page.render(comparison(*every_movement()))
    marks = ['<h1 class="h1">', '<ul class="notes">', "A colour on this page", 'class="fig lead-chart"', "What changed, check by check",
             'class="views"', 'role="table"', "How a movement is decided", "Every row, before and after",
             "The full technical comparison", "<template"]
    at = [text.find(m) for m in marks]
    check("each block is there, in this order", all(i >= 0 for i in at) and at == sorted(at), list(zip(marks, at, strict=True)))


def test_the_verdict_is_the_readings_and_the_notes_follow_it():
    before, after = every_movement()
    cmp = comparison(before, after)
    page = read(compare_page.render(cmp))
    top = page.one("main").all("div", "stack")[1]
    v = cmp["plain"]["verdict"]
    check("the large pill says the movement, icon and word", top.one("span", "lg").text == "Better"
          and "green" in top.one("span", "lg").attrs["class"] and top.one("span", "lg").all("svg"))
    check("the headline and the sentence are the reading's", page.one("h1").text == v["headline"] == "Faster by 0.7 s"
          and page.one("p", "lead").text == v["text"])
    check("the scenario stands beside the pill", top.one("div", "rowf").one("span", "sub").text == "coldStart")
    notes = page.one("ul", "notes").all("li")
    check("a note per note of the reading, its first words bold",
          [(n.one("b").text, n.one("p").text) for n in notes]
          == [(n["title"], f"{n['title']} {n['text']}") for n in cmp["plain"]["notes"]]
          and [n.one("b").text for n in notes] == ["Still over the limit.", "Not certain check by check yet."])
    check("each with the icon of how much attention it needs", all(n.all("svg") for n in notes))


def test_what_a_colour_means_is_said_once_where_the_first_pill_is():
    text = compare_page.render(comparison(*every_movement()))
    page = read(text)
    said = [p for p in page.all("p") if p.text.startswith("A colour on this page says which way something moved")]
    check("once", len(said) == 1 and text.count("A colour on this page") == 1)
    check("in words: the three directions, and that the report means something else",
          "green got better, grey did not change, red got worse" in said[0].text
          and "On the report, and beside the notes above, a colour says how much attention" in said[0].text, said[0].text)
    check("in the verdict's own block, under its pill", said[0].parent is page.one("h1").parent)


def test_a_verdict_with_nothing_to_note_has_no_list():
    four = (2000.0, 2010.0, 2020.0, 2030.0)
    page = page_of(report([entry("gc_pressure")], launches=four), report([entry("gc_pressure")], launches=four))
    check("no notes, no empty list", not page.all("ul", "notes"))
    check("and the sentence on colour names no notes", "On the report a colour says how much attention something needs."
          in page.text and "notes above" not in page.text)
    check("and the verdict is grey: no change", "grey" in page.one("span", "lg").attrs["class"]
          and page.one("span", "lg").text == "No change" and page.one("h1").text == "No clear change")


def test_the_whole_window_before_and_after():
    page = page_of(*every_movement())
    fig = page.one("div", "lead-chart")
    lanes = fig.all("div", "cr")[1:]
    check("a row before and a row after", [r.one("div", "cr-n").text for r in lanes] == ["Before", "After"])
    check("each with its median and its range", [r.one("div", "cr-v").one("b").text for r in lanes] == ["2.1 s", "1.4 s"]
          and lanes[0].one("div", "cr-v").all("span")[0].text == "2.0 s – 2.2 s")
    check("before is drawn in the soft neutral, after in the colour of measured time",
          "var(--data-soft)" in lanes[0].one("i", "bar").attrs["style"]
          and "var(--data-soft)" not in lanes[1].one("i", "bar").attrs["style"])
    check("a dot per launch", [len(r.all("i", "dot")) for r in lanes] == [3, 3])
    check("the time limit is a line through both", [len(r.all("i", "thr")) for r in lanes] == [1, 1])
    key = [s.text for s in fig.one("p", "key").all("span")]
    check("and the key names it", key == ["median", "each launch", "your limit, 0.5 s"], key)
    check("the exact figures are in the tooltip", lanes[0].attrs["data-tip"]
          == "Before\nmedian: 2,118.00 ms\neach launch: 1,968.00 · 2,118.00 · 2,166.00", lanes[0].attrs["data-tip"])


def test_more_than_three_launches_show_the_two_ends_and_say_so(demo):
    page = read(compare_page.render(demo))
    fig = page.one("div", "lead-chart")
    lanes = fig.all("div", "cr")[1:]
    check("a report keeps the fastest and the slowest of five", [len(r.all("i", "dot")) for r in lanes] == [2, 2])
    check("and the key does not call them every launch", "the fastest and the slowest launch" in fig.one("p", "key").text
          and "each launch" not in fig.one("p", "key").text, fig.one("p", "key").text)
    check("nor does the tooltip", "fastest" in lanes[0].attrs["data-tip"] and "of 5 launches" in lanes[0].attrs["data-tip"])


def test_no_window_no_chart():
    before, after = known()
    after["window"]["duration_ms"] = None
    page = page_of(before, after)
    check("there are no two times to draw", not page.all("div", "lead-chart") and page.one("h1").text == "No time to compare")


# --- the table --------------------------------------------------------------------------


def test_a_row_per_check_in_the_readings_order_and_words():
    cmp = comparison(*every_movement())
    page = read(compare_page.render(cmp))
    rows = page.all("div", "tr")
    items = cmp["plain"]["checks"]
    check("a row per item, in its order", [r.attrs["data-id"] for r in rows] == [f"mv-{c['id']}" for c in items])
    for r, c in zip(rows, items, strict=True):
        check(f"{c['id']}: the title and the sentence are the reading's",
              r.one("h3").text == c["title"] and r.one("p", "tr-f").text == c["text"].replace("`", ""), r.one("p", "tr-f").text)
    check("a name in a sentence is code", [c.text for c in rows_of(page)["mv-main_thread_waits"].one("p", "tr-f").all("code")] == ["worker_pool_#", "loadConfig"])
    head = [h.text for h in page.one("div", "tb-h").all("span", role="columnheader")]
    check("the columns", head == ["Check", "Change", "Before", "After", "Details"], head)


def test_a_movement_is_an_icon_and_a_word_never_colour_alone():
    page = page_of(*every_movement())
    got = {did[3:]: (r.one("div", "c-l").one("span", "st").text, r.one("div", "c-l").one("span", "st").attrs["class"].split()[1],
                     len(r.one("div", "c-l").all("svg")))
           for did, r in rows_of(page).items()}
    check("each movement in its word and its tint, with an icon", got == {
        "main_thread_block": ("Worse", "red", 1), "repeated_work": ("New", "red", 1),
        "main_thread_waits": ("Gone", "green", 1), "uninstrumented_cpu": ("Better", "green", 1),
        "binder_txn": ("No change", "grey", 1), "gc_pressure": ("Fine both times", "green", 1),
        "io_wait": ("Not checked", "grey", 1)}, got)
    shapes = {m: kit.move_icon(m) for m in kit.MOVES}
    check("better, worse and no change have shapes of their own",
          len({shapes[moves.BETTER], shapes[moves.WORSE], shapes[moves.NO_CHANGE], shapes[moves.GONE], shapes[moves.NEW]}) == 5)
    check("a movement this version does not know is grey and says its name",
          kit.move_pill("sideways") == f'<span class="st grey">{kit.move_icon("sideways")}Sideways</span>')
    check("every movement of the reading has a word and a tint", set(moves.MOVEMENTS) | {moves.NOT_COMPARED}
          <= set(kit.MOVE_WORD) & set(kit.MOVE_TONE) & set(kit.MOVES))


def test_the_figures_of_a_row():
    page = page_of(*every_movement())
    got = {did[3:]: (r.one("div", "c-b").text, r.one("div", "c-a").text) for did, r in rows_of(page).items()}
    check("a time, nothing for a side that found none, a dash where there is no figure", got == {
        "main_thread_block": ("0.3 s", "0.9 s"), "repeated_work": ("nothing", "0.2 s"),
        "main_thread_waits": ("0.7 s", "nothing"), "uninstrumented_cpu": ("0.9 s", "0.2 s"),
        "binder_txn": ("0.2 s", "50 ms"), "gc_pressure": ("–", "–"), "io_wait": ("–", "–")}, got)
    cells = rows_of(page)["mv-binder_txn"]
    check("on a phone each figure says which it is", (cells.one("div", "c-b").attrs["data-label"],
          cells.one("div", "c-a").attrs["data-label"]) == ("Before", "After"))
    odd = page_of(report([entry("main_thread_waits", [row("worker_pool_#", 456.0, of=3)])]),
                  report([entry("main_thread_waits")]))
    check("findings no typical launch has are `at times`",
          (odd.one("div", "c-b").text, odd.one("div", "c-a").text) == ("at times", "nothing"))


def test_the_rows_with_nothing_to_say_are_quiet():
    rows = rows_of(page_of(*every_movement()))
    quiet = {did[3:] for did, r in rows.items() if "quiet" in r.attrs["class"].split()}
    check("fine both times and not checked", quiet == {"gc_pressure", "io_wait"}, quiet)


def test_the_filter_is_by_movement_and_is_the_counts():
    page = page_of(*every_movement())
    views = [(b.attrs["data-view"], b.one("span", "v-w").text, b.one("b").text) for b in page.all("button", "view")]
    check("all, got better, did not change, got worse, fine both times, not checked", views == [
        ("all", "All", "7"), ("better", "Got better", "2"), ("same", "Did not change", "1"), ("worse", "Got worse", "2"),
        ("fine", "Fine both times", "1"), ("unchecked", "Not checked", "1")], views)
    groups = {did[3:]: r.attrs["data-group"] for did, r in rows_of(page).items()}
    check("gone is a kind of better and new of worse", groups["main_thread_waits"] == "better"
          and groups["repeated_work"] == "worse" and groups["binder_txn"] == "same", groups)
    check("every row is in a group the filter has", set(groups.values()) <= {v[0] for v in views})
    calm = page_of(*known())
    check("with nothing worse there is no such button", "worse" not in [b.attrs["data-view"] for b in calm.all("button", "view")])


def test_two_apps_have_a_verdict_and_no_table():
    before, after = known()
    after["window"]["process"] = "com.example.other"
    text = compare_page.render(comparison(before, after))
    page = read(text)
    check("the verdict says they cannot be compared", page.one("h1").text == "These two cannot be compared"
          and page.one("span", "lg").text == "Not compared")
    check("no table, no filter, no panel", not page.all("div", "tr") and not page.all("button", "view")
          and not page.all("template"))
    check("and the technical comparison still says why", "Different processes" in text)


# --- the panel --------------------------------------------------------------------------


def test_the_panel_says_the_row_again_then_each_side_then_the_rows():
    cmp = comparison(*every_movement())
    page = read(compare_page.render(cmp))
    item = next(c for c in cmp["plain"]["checks"] if c["id"] == "uninstrumented_cpu")
    panel = panel_of(page, "uninstrumented_cpu")
    check("the movement, the title, the sentence, how it touches the app",
          panel.one("span", "st").text == "Better" and panel.one("h2", id="peek-title").text == item["title"]
          and [p.text for p in panel.all("p")[:2]] == [item["text"], item["why"]])
    sides = panel.one("dl", "props").all("div", "side")
    check("what each report said of it, in that report's own sentence",
          [s.all("span")[0].text for s in sides] == [item["before"]["found"].replace("`", ""), item["after"]["found"].replace("`", "")]
          and [d.text for d in panel.one("dl", "props").all("dt")] == ["Before", "After"])
    check("and its level there in words, with no pill of a level on this page",
          [s.one("span", "sub").text for s in sides] == ["Its level in that report: worth a look."] * 2
          and len(panel.all("span", "st")) == 1)
    check("then, under a hairline, its name in echolot", panel.one("div", "deep").one("code").text == "uninstrumented_cpu")


def test_a_row_of_the_comparison_is_two_lanes_before_above_after():
    page = page_of(*every_movement())
    chart = panel_of(page, "uninstrumented_cpu").one("div", "chart")
    lanes = {r.one("span", "mono").text: r for r in chart.all("div", "cr")[1:]}
    kept, gone = lanes["worker_pool_0"], lanes["worker_pool_1"]
    check("a row on both sides has a bar and its dots in each lane",
          [len(kept.all("i", "bar", )), len(kept.one("div", "two").all("i", "b")), len(kept.one("div", "two").all("i", "a"))] == [2, 4, 4])
    check("a row that is gone has the lane before alone", len(gone.one("div", "two").all("i", "b")) == 4
          and not gone.one("div", "two").all("i", "a"))
    check("the figures say from what to what, and what became of the row",
          (kept.one("div", "cr-v").one("b").text, kept.one("div", "cr-v").one("span").text) == ("504 → 204 ms", "smaller")
          and (gone.one("div", "cr-v").one("b").text, gone.one("div", "cr-v").one("span").text) == ("404 ms → none", "gone"))
    check("the tooltip has each launch of each side", gone.attrs["data-tip"]
          == "worker_pool_1\nbefore: 404.00 ms · each launch: 400.00 · 404.00 · 408.00\nafter: not found", gone.attrs["data-tip"])
    check("and the key names the two lanes and the dot",
          [s.text for s in panel_of(page, "uninstrumented_cpu").one("p", "key").all("span")] == ["before", "after", "each launch"])
    new = panel_of(page, "repeated_work").one("div", "chart").all("div", "cr")[1]
    check("a row that is new has the lane after alone", not new.one("div", "two").all("i", "b")
          and new.one("div", "cr-v").one("b").text == "none → 244 ms" and new.one("div", "cr-v").one("span").text == "new")
    check("with what the check said of it under its name", new.one("span", "cr-s").text == "entered from 2: a, b")


def test_a_check_with_no_rows_says_that_it_has_none_and_why():
    page = page_of(*every_movement())
    fine, blind = panel_of(page, "gc_pressure"), panel_of(page, "io_wait")
    check("fine both times", "No rows: the check found nothing on either side." in fine.text and not fine.all("div", "chart"))
    check("not checked", "No rows to compare: the check could not look on one side or on both" in blind.text)
    check("and the side that could not look says why", "The recording does not carry the phone's own state."
          in blind.one("dl", "props").text and "Its level in that report: not checked." in blind.one("dl", "props").text)


# --- the folds --------------------------------------------------------------------------


def test_how_a_movement_is_decided_says_the_rule_with_its_numbers():
    page = page_of(*every_movement())
    fold = next(d for d in page.all("details") if d.one("summary").text == "How a movement is decided")
    names = [li.one("b").text for li in fold.all("li")]
    check("a sentence per movement", names == ["Better, worse", "Gone, new", "No change", "Not compared", "Fine both times",
                                             "Not checked"])
    check("with the numbers the reading is made with", "changed by at least 20% and by at least 5% of the launch" in fold.text
          and "It takes 3 launches a side." in fold.text and "seen in one launch of three adds nothing" in fold.text, fold.text)
    check("each with the icon of its movement", all(li.all("svg") for li in fold.all("li")))


def test_every_row_on_one_axis_the_largest_moves_first():
    cmp = comparison(*every_movement())
    page = read(compare_page.render(cmp))
    fold = next(d for d in page.all("details") if d.one("summary").text == "Every row, before and after")
    names = [r.one("span", "mono").text for r in fold.one("div", "chart").all("div", "cr")[1:]]
    check("every row of the comparison, in its order", names == [r["location"] for r in cmp["rows"]], names)
    check("each says whose it is", fold.one("div", "chart").all("div", "cr")[1].one("span", "cr-s").text == cmp["rows"][0]["detector"])
    many = [steady(f"step_{i:02d}", 100 + i) for i in range(compare_page.ROWS_SHOWN + 5)]
    long = page_of(report([entry("main_thread_block", many)]), report([entry("main_thread_block", many[:1])]))
    fold = next(d for d in long.all("details") if d.one("summary").text == "Every row, before and after")
    check("a long comparison draws the ones that moved most, and says so",
          len(fold.one("div", "chart").all("div", "cr")) - 1 == compare_page.ROWS_SHOWN
          and f"The {compare_page.ROWS_SHOWN} rows that moved most, of {compare_page.ROWS_SHOWN + 5}." in fold.text)


def squeezed(text: str) -> str:
    return re.sub(r"[\s*`_\\|]+", "", text)


def test_the_last_fold_is_comparison_md_as_it_is_written(demo):
    for name, cmp in (("the demo", demo), ("by hand", comparison(*every_movement()))):
        page = read(compare_page.render(cmp))
        last = page.one("main").all("details")[-1]
        check(f"{name}: the last fold is the technical comparison", last.one("summary").text == "The full technical comparison")
        written = squeezed(last.one("div", "md").text)
        for line in compare_mod.to_markdown(cmp).splitlines():
            line = line.lstrip("#> ").replace("⚠️", "").strip()
            if not line or set(line) <= set("|-: "):
                continue
            for cell in ([c for c in line.strip("|").split(" | ")] if line.startswith("|") else [line]):
                check(f"{name}: it holds `{cell[:40]}`", squeezed(cell) in written, cell)


def test_a_comparison_too_bare_to_write_out_still_gives_a_page():
    cmp = comparison(*known())
    del cmp["summary"]
    page = read(compare_page.render(cmp))
    check("the plain layer stands", page.one("h1").text == "Faster by 0.7 s" and page.all("div", "tr"))
    check("and the fold says what is missing", "The technical comparison cannot be written out" in page.text)


# --- what the page is made of -----------------------------------------------------------


def test_what_a_reader_gets_with_script_switched_off():
    text = compare_page.render(comparison(*every_movement()))
    page = read(text)
    check("the root says so until a script runs", page.one("html").attrs["class"] == "no-js")
    check("a note says what works without it", "Script is switched off" in page.one("noscript").text)
    check("the filter reads as counts: its buttons are written disabled", all("disabled" in b.attrs for b in page.all("button", "view")))
    check("and what only script can open is marked", all("needs-js" in b.attrs["class"].split() for b in page.all("button", "rowbtn")))


def test_the_top_bar_takes_the_steps_and_this_one_is_after_the_fix():
    steps = [("Start", "/t/", False), ("Report", "/t/report", False), ("After the fix", "/t/after", True)]
    page = page_of(*known(), steps=steps)
    nav = page.one("nav", "steps")
    check("the path", [a.text for a in nav.all("a")] == ["Start", "Report", "After the fix"])
    check("and where the reader is", nav.one("a", aria_current="page").text == "After the fix")
    check("the app and the scenario are beside the name", page.one("span", "crumb").text == "com.example.app coldStart")
    check("a page on disk has no path", not page_of(*known()).all("nav", "steps"))
    check("the title names the scenario", page.one("title").text == "echolot comparison: coldStart")


def test_light_and_dark_and_a_phone():
    page = page_of(*known())
    css = page.one("style").text
    check("the viewport is the device's", page.one("meta", name="viewport").attrs["content"].startswith("width=device-width"))
    check("both schemes are announced", page.one("meta", name="color-scheme").attrs["content"] == "light dark")
    check("dark by the system's choice, and by an attribute",
          '@media (prefers-color-scheme: dark)' in css and ':root:not([data-theme="light"])' in css
          and ':root[data-theme="dark"]' in css)
    check("the table re-flows at the three widths of its container", css.count(".tb.moves {") >= 3
          and "@container (max-width: 899px)" in css and "@container (max-width: 559px)" in css)


def test_every_colour_is_a_token():
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    check("the page's own stylesheet names no colour of its own", not literal.search(kit.asset("compare.css")),
          literal.findall(kit.asset("compare.css")))
    icons = "".join(kit.move_icon(m) for m in kit.MOVES)
    check("nor does a movement's icon", not literal.search(icons), literal.findall(icons))
    check("and each is drawn in a token", all("var(--" in kit.move_icon(m) for m in kit.MOVES))


def test_the_page_is_made_of_the_shared_pieces_in_order():
    text = compare_page.render(comparison(*known()))
    at = [text.index(kit.asset(n)) for n in compare_page.CSS + compare_page.JS]
    check("each is written in whole, in the order named", at == sorted(at))
    check("every one of them is inside the package", all((PAGES / n).is_file() for n in compare_page.CSS + compare_page.JS))
    check("the base, the table and its own", compare_page.CSS[:2] == ("base.css", "table.css") and compare_page.CSS[-1] == "compare.css"
          and compare_page.JS == ("base.js", "table.js"))


def test_the_pieces_of_another_page_keep_their_bytes(demo_report):
    """The comparison's styles are its own: the report's page has none of them."""
    from echolot.pages import report_page
    text = report_page.render(demo_report)
    check("no rule of the comparison's is in the report's page", ".tb.moves" not in text and ".cr-p.two" not in text
          and ".notes" not in text)


def test_ids_are_unique_outside_the_templates(demo):
    for cmp in (demo, comparison(*every_movement())):
        page = read(compare_page.render(cmp))
        ids = [n.attrs["id"] for n in page.walk() if "id" in n.attrs and not n.inside("template")]
        check("no id twice", len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))
        opens = {b.attrs["data-open"] for b in page.all("button", "rowbtn")}
        check("every Details button has its panel", opens == {t.attrs["id"][5:] for t in page.all("template")})
        check("and every panel names its title for the dialog", all(t.all("h2", id="peek-title") for t in page.all("template")))


def test_a_check_with_a_name_that_is_no_identifier_still_has_a_row_of_its_own():
    odd = "my check/v2"
    page = page_of(report([entry(odd, [steady("a", 700)]), entry("gc_pressure")]),
                   report([entry(odd, [steady("a", 100)]), entry("gc_pressure")]))
    ids = [r.attrs["data-id"] for r in page.all("div", "tr")]
    check("its key is its place, and safe in a selector", ids == ["mv-check-0", "mv-gc_pressure"], ids)
    check("its title is its own name", page.all("div", "tr")[0].one("h3").text == odd)


def test_the_demo_pair_is_a_whole_page(demo):
    page = read(compare_page.render(demo))
    checks = demo["plain"]["checks"]
    check("a row and a panel per check", len(page.all("div", "tr")) == len(page.all("template")) == len(checks))
    check("the counts of the filter add up", sum(int(b.one("b").text) for b in page.all("button", "view")[1:]) == len(checks))
    check("neutral names only", "com.example.app" in page.one("span", "crumb").text)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not here to parse the scripts")
@pytest.mark.parametrize("name", compare_page.JS)
def test_the_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(PAGES / name)], capture_output=True, text=True)
    check(f"{name} is JavaScript", done.returncode == 0, done.stderr)


# --- the command ------------------------------------------------------------------------


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def pair(tmp_path, monkeypatch):
    before, after = every_movement()
    (tmp_path / "before.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(after), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_compare_html_writes_the_page_beside_the_comparison(pair):
    code, out, err = run("compare", "before.json", "after.json", "-o", "out", "--html")
    page = pair / "out" / "comparison.html"
    check("it wrote the three files", code == 0 and page.is_file() and (pair / "out" / "comparison.md").is_file()
          and (pair / "out" / "comparison.json").is_file(), err)
    check("and says where the page is",
          f"→ {Path('out') / 'comparison.html'}  (the same comparison as a page: open it in a browser)" in err, err)
    check("the table is still what it prints", out.startswith("# Comparison"))
    written = json.loads((pair / "out" / "comparison.json").read_text(encoding="utf-8"))
    check("the page is of the comparison beside it, reading and all",
          page.read_text(encoding="utf-8") == compare_page.render(written) and "plain" in written)
    check("written twice from the same two reports, the same bytes",
          (run("compare", "before.json", "after.json", "-o", "again", "--html")[0] == 0)
          and (pair / "again" / "comparison.html").read_bytes() == page.read_bytes())


def test_plain_and_html_go_together(pair):
    code, out, err = run("compare", "before.json", "after.json", "-o", "out", "--html", "--plain")
    check("the list is printed and the page is written", code == 0 and out.startswith("BETTER")
          and (pair / "out" / "comparison.html").is_file(), (out[:60], err))


def test_a_page_that_stands_there_is_written_again_and_none_is_written_unasked(pair):
    code, out, err = run("compare", "before.json", "after.json", "-o", "out")
    check("no flag, no page", code == 0 and not (pair / "out" / "comparison.html").exists() and "comparison.html" not in err, err)
    run("compare", "before.json", "after.json", "-o", "out", "--html")
    first = (pair / "out" / "comparison.html").read_text(encoding="utf-8")
    # The other way round: what was the fix is now the regression.
    code, out, err = run("compare", "after.json", "before.json", "-o", "out")
    again = (pair / "out" / "comparison.html").read_text(encoding="utf-8")
    check("a page of the comparison before would read as this one's: it is written again", code == 0 and again != first
          and "<h1 class=\"h1\">Slower by 0.7 s</h1>" in again, again[:200])
    check("and it says so", "it stood there from an earlier comparison: written again from this one" in err, err)
    check("with the reading beside it, as the page is of it",
          compare_page.render(json.loads((pair / "out" / "comparison.json").read_text(encoding="utf-8"))) == again)


def test_html_with_nowhere_to_write_says_so(pair):
    code, out, err = run("compare", "before.json", "after.json", "--html")
    check("the comparison is printed and nothing is written", code == 0 and out.startswith("# Comparison")
          and not list(pair.glob("**/comparison.html")), err)
    check("and it says what the flag needs", "--html writes the page beside comparison.json: name a folder with -o" in err, err)


def test_a_check_not_measured_alike_has_no_red_and_no_green():
    """#713: a threshold of 83.3 ms against 16 ms on the same recordings drew a red "Worse"."""
    before = report([entry("main_thread_block", [steady("inflate", 300)], params={"min_slice_ms": 83.3}),
                     entry("binder_txn", [steady("binder transaction", 600)])])
    after = report([entry("main_thread_block", [steady("inflate", 300), steady("bind", 600)],
                          params={"min_slice_ms": 16}),
                    entry("binder_txn", [steady("binder transaction", 300)])])
    page = page_of(before, after)
    rows = rows_of(page)
    pill = rows["mv-main_thread_block"].one("div", "c-l").one("span", "st")
    check("grey, and says it was not compared", pill.text == "Not compared"
          and pill.attrs["class"].split()[1] == "grey", pill.attrs)
    check("with the threshold in its sentence", "min_slice_ms from 83.3 to 16 ms"
          in rows["mv-main_thread_block"].one("p", "tr-f").text)
    check("the other check keeps its green", rows["mv-binder_txn"].one("div", "c-l").one("span", "st").text == "Better")
    views = [b.attrs["data-view"] for b in page.all("button", "view")]
    check("the filter has it, and every row is in a group the filter has", "uncompared" in views
          and {r.attrs["data-group"] for r in rows.values()} <= set(views), views)
    for rep in (before, after):
        rep["traces"] = [f"coldStart_iter00{i}.perfetto-trace" for i in range(3)]
        for d in rep["detectors"]:
            d["params"] = {}
    same = page_of(before, after)
    top = same.one("main").one("span", "lg")
    check("the same recordings: the verdict is grey and says so", top.text == "Not compared"
          and "grey" in top.attrs["class"], top.attrs)
    tints = {r.one("div", "c-l").one("span", "st").attrs["class"].split()[1] for r in rows_of(same).values()}
    check("and no check is red or green", tints == {"grey"}, tints)
