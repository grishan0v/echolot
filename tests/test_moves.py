#!/usr/bin/env python3
"""The plain reading of a comparison: a verdict, its notes, a movement per check.

`comparison.md` is a table for an agent. `echolot/moves.py` says the same
comparison to someone who has just changed their code, and a model writes
none of it, so what is pinned here is words and the edges of one rule:

  - a move is called with three launches a side, a change of a fifth, and
    launches of the two sides that do not overlap;
  - a check is judged by the sum of its rows, each as a typical launch has it;
  - a movement is never a level: the two words stay apart in the keys;
  - nothing `compare` computes, prints or writes changes without the flag.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import moves, plain, recorder  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

APP = "com.example.app"


def row(location, *values, of=None, **kw):
    """A merged row: its value in each launch it was found in, out of `of`."""
    values = [float(v) for v in values]
    n = of or len(values)
    mid = sorted(values)[len(values) // 2] if len(values) % 2 else sum(sorted(values)[len(values) // 2 - 1:][:2]) / 2
    out = {"location": location, "runs": f"{len(values)}/{n}", "count": 1, "total_ms": round(mid, 2),
           "spread": {"total_ms": {"min": min(values), "max": max(values), "values": values}}}
    out.update(kw)
    return out


def entry(did, rows=(), **extra):
    """An entry as `analyze` writes it: `checked` is always there."""
    return {"id": did, "title": did, "why": "", "params": {}, "params_source": "default",
            "identity": ["location"], "rows": list(rows), "error": None, "checked": True, **extra}


def report(detectors, launches=(2000.0, 2010.0, 2020.0), budget=None, process=APP, startup=True):
    launches = [float(v) for v in launches]
    window = {"process": process, "pid": 4100, "duration_ms": sorted(launches)[len(launches) // 2]}
    if len(launches) > 1:
        window["duration_ms_min"], window["duration_ms_max"] = min(launches), max(launches)
    if startup:
        window["startup"] = {"type": "cold", "dur_ms": window["duration_ms"]}
    rep = {"schema": 1, "scenario": "coldStart", "budget_ms": budget, "window": window,
           "runs": len(launches), "summary": {"absent_ids": [], "fired_ids": []},
           "detectors": [copy.deepcopy(d) for d in detectors]}
    return plain.annotate(rep)


def reading(before, after):
    cmp = compare_mod.build(before, after, before_path="before.json", after_path="after.json")
    return moves.read(before, after, cmp)


def one(before_rows, after_rows, did="main_thread_block", **kw):
    """The item of one check, with these rows before and after."""
    got = reading(report([entry(did, before_rows)], **kw), report([entry(did, after_rows)], **kw))
    return got["checks"][0]


def steady(location, ms, n=3, step=4.0, **kw):
    """A row found in every launch, a few milliseconds apart."""
    return row(location, *[ms + step * i for i in range(n)], **kw)


# --- a movement per check ---------------------------------------------------------------


def test_less_added_up_with_every_launch_under_every_launch_before_is_better():
    item = one([steady("inflate", 600)], [steady("inflate", 300)])
    check("it got better", item["movement"] == moves.BETTER, item)
    check("and says from what to what", item["text"]
          == "Added up, what this check found went from 0.6 s to 0.3 s.", item["text"])
    check("each side keeps the report's own level, under its own word",
          item["before"]["attention"] in plain.LEVELS and item["after"]["attention"] in plain.LEVELS
          and "level" not in item and "attention" not in item, item)


def test_more_added_up_is_worse():
    item = one([steady("inflate", 300)], [steady("inflate", 600)])
    check("it got worse", item["movement"] == moves.WORSE and item["text"]
          == "Added up, what this check found went from 0.3 s to 0.6 s.", item)


def test_findings_before_and_none_now_is_gone_in_the_reports_own_words():
    before = report([entry("main_thread_waits", [steady("worker_pool_#", 700, detail="blocked in loadConfig")])])
    after = report([entry("main_thread_waits")])
    item = reading(before, after)["checks"][0]
    found = before["detectors"][0]["plain"]["found"]
    check("gone", item["movement"] == moves.GONE, item)
    check("with what the report said of it before", item["text"]
          == f"This check finds nothing now. Before: {found}" and found.startswith("The main thread waited 0.7 s"),
          item["text"])
    check("nothing is a side with no figure", item["after"]["ms"] is None and item["before"]["ms"] == 704.0, item)


def test_nothing_before_and_findings_now_is_new():
    before = report([entry("main_thread_waits")])
    after = report([entry("main_thread_waits", [steady("worker_pool_#", 700)])])
    item = reading(before, after)["checks"][0]
    check("new", item["movement"] == moves.NEW and item["text"].startswith(
        "This check found nothing before. Now: The main thread waited 0.7 s for `worker_pool_#`"), item)


def test_nothing_both_times_is_fine_both_times_in_the_checks_clean_words():
    item = one([], [], did="main_thread_waits")
    check("fine both times", item["movement"] == moves.FINE_BOTH, item)
    check("said as the report says a clean result",
          item["text"] == plain.fixed_texts("main_thread_waits")[2] and item["text"], item["text"])
    check("with no figure on either side", item["before"]["ms"] is None and item["after"]["ms"] is None)


def test_a_small_change_is_no_change_and_says_how_much():
    item = one([steady("inflate", 600)], [steady("inflate", 520)])
    check("under a fifth is no change", item["movement"] == moves.NO_CHANGE, item)
    check("in words", item["text"] == "Added up, what this check found was 0.6 s before and 0.5 s after. "
          "A change under 20% is not counted.", item["text"])
    same = one([steady("inflate", 600)], [steady("inflate", 601)])
    check("two equal figures need no reason", same["text"]
          == "Added up, what this check found was 0.6 s before and 0.6 s after.", same["text"])


def test_the_floor_is_a_fifth_of_the_larger_sum():
    def moved(before_ms, after_ms):
        return one([row("inflate", before_ms, before_ms, before_ms)],
                   [row("inflate", after_ms, after_ms, after_ms)])["movement"]
    check("19% less is no change", moved(1000, 810) == moves.NO_CHANGE)
    check("20% less is better", moved(1000, 800) == moves.BETTER)
    check("from the smaller side up it is the larger one that counts",
          moved(810, 1000) == moves.NO_CHANGE and moved(800, 1000) == moves.WORSE)
    check("the constants are the ones the docs name",
          (moves.SHARE, moves.LAUNCH_SHARE, moves.LEAST_RUNS) == (0.20, 0.05, 3))


def test_a_change_under_a_twentieth_of_the_launch_is_no_news():
    item = one([steady("inflate", 60, step=1)], [steady("inflate", 20, step=1)])
    check("40 ms of a two-second launch is no change", item["movement"] == moves.NO_CHANGE, item)
    check("and says why", item["text"] == "Added up, what this check found was 61 ms before and 21 ms after. "
          "That is under 5% of the launch, which is not counted.", item["text"])
    short = one([steady("inflate", 60, step=1)], [steady("inflate", 20, step=1)], launches=(500.0, 505.0, 510.0))
    check("of a half-second launch it is a change", short["movement"] == moves.BETTER, short)


def test_launches_that_overlap_are_no_change_however_far_the_medians_are():
    item = one([row("inflate", 300, 600, 900)], [row("inflate", 200, 310, 320)])
    check("one launch before was as low as the launches after", item["movement"] == moves.NO_CHANGE, item)
    check("in words", item["text"] == "Added up, what this check found was 0.6 s before and 0.3 s after. "
          "Single launches of the two sides overlap, so it is not counted as a change.", item["text"])


def test_fewer_than_three_launches_a_side_call_nothing():
    two = dict(launches=(2000.0, 2010.0))
    item = one([row("inflate", 600, 610)], [row("inflate", 100, 110)], **two)
    check("two a side is no change", item["movement"] == moves.NO_CHANGE, item)
    check("and says it is the launches", item["text"] == "Added up, what this check found was 0.6 s before and "
          "0.1 s after. 2 launches before and 2 after are too few to call that a change.", item["text"])
    gone = one([row("inflate", 600, 610)], [], **two)
    check("nor is anything gone", gone["movement"] == moves.NO_CHANGE and gone["text"]
          == "This check finds nothing now. Before it found 0.6 s. 2 launches before and 2 after are too few "
          "to call that a change.", gone)


def test_a_row_seen_in_one_launch_of_three_adds_nothing():
    """A typical launch does not have it: one slow launch must not read as a move."""
    odd = row("worker_pool_#", 456.0, of=3)
    item = one([odd], [], did="main_thread_waits")
    check("it is not called gone", item["movement"] == moves.NO_CHANGE, item)
    check("its sum is nothing, with a finding", item["before"] == {**item["before"], "ms": 0.0, "findings": 1})
    check("and the sentence says why", item["text"] == "This check finds nothing now. Before it found something, "
          "but not in every launch, so that is not counted as a change.", item["text"])
    back = one([], [odd], did="main_thread_waits")
    check("nor new", back["movement"] == moves.NO_CHANGE and back["text"]
          == "This check found nothing before. Now it finds something, but not in every launch, so that is "
          "not counted as a change.", back)


def test_a_small_steady_finding_that_went_is_no_change():
    item = one([steady("inflate", 60, step=1)], [])
    check("60 ms of two seconds", item["movement"] == moves.NO_CHANGE and item["text"]
          == "This check finds nothing now. Before it found 61 ms. That is too small, or in too few of the "
          "launches, to call a change.", item)


def test_a_check_is_judged_by_the_sum_of_its_rows_not_by_its_largest():
    """The real case: a fix put five rows under one section. The largest row
    grew from 0.4 s to 0.7 s, and the check as a whole shrank."""
    before = [steady("dao.insertTranslations", 373), steady("json.decode", 173), steady("dao.insertItems", 172),
              steady("dao.insertAllStats", 113), steady("binder transaction", 111), steady("main.perk", 94),
              steady("Compose:recompose", 87)]
    after = [steady("store.fill", 500), steady("Compose:recompose", 91), steady("binder transaction", 88)]
    item = one(before, after)
    check("the largest row grew", max(r["total_ms"] for r in after) > max(r["total_ms"] for r in before))
    check("and the check got better", item["movement"] == moves.BETTER, item)
    check("with the rows that went and came counted", item["findings"] == {"gone": 5, "new": 1, "kept": 2}
          and item["text"] == "Added up, what this check found went from 1.2 s to 0.7 s. "
          "5 of its findings are gone, 1 is new.", item)


def test_findings_that_are_left_in_a_minority_of_launches_are_said_so():
    item = one([steady("worker_pool_0", 400), steady("worker_pool_1", 380)],
               [row("worker_pool_2", 90.0, of=3)], did="uninstrumented_cpu")
    check("better, down to next to nothing", item["movement"] == moves.BETTER and item["text"]
          == "Added up, what this check found went from 0.8 s to next to nothing. 2 of its findings are gone, "
          "1 is new. What it finds now is not in every launch.", item)


def test_a_check_one_side_could_not_look_for_is_not_checked():
    why = "The recording does not carry the phone's own state, so there was nothing to read."
    looked = entry("io_wait", [steady("disk_io_0", 600)])
    blind = entry("io_wait", checked=False, reason=why)
    after = reading(report([looked]), report([blind]))["checks"][0]
    check("not checked after, with the reason", after["movement"] == moves.NOT_CHECKED and after["text"]
          == f"Not checked after, so there is nothing to compare. {why}", after)
    before = reading(report([blind]), report([looked]))["checks"][0]
    check("not checked before", before["text"] == f"Not checked before, so there is nothing to compare. {why}")
    both = reading(report([blind]), report([blind]))["checks"][0]
    check("not checked either time", both["text"] == f"Not checked either time, so there is nothing to compare. {why}")


def test_a_check_that_failed_or_ran_on_one_side_only_is_not_checked():
    looked = entry("io_wait", [steady("disk_io_0", 600)])
    failed = entry("io_wait", error="no such table: thread_state")
    item = reading(report([looked]), report([failed]))["checks"][0]
    check("a failure did not look", item["movement"] == moves.NOT_CHECKED and "It failed to run" in item["text"], item)
    only = reading(report([looked, entry("gc_pressure")]), report([entry("gc_pressure")]))["checks"]
    lone = next(c for c in only if c["id"] == "io_wait")
    check("a check one report does not have is not gone", lone["movement"] == moves.NOT_CHECKED
          and lone["text"] == "Not checked after, so there is nothing to compare. It did not run on this side.", lone)


def test_rows_from_some_repeats_of_a_check_that_could_not_run_in_the_rest_are_left_out():
    """As `compare` leaves them out of its table."""
    why = "Two of three recordings do not carry the phone's own state."
    partly = entry("io_wait", [steady("disk_io_0", 600)], checked=False, reason=why)
    item = reading(report([partly]), report([entry("io_wait")]))["checks"][0]
    check("not called gone", item["movement"] == moves.NOT_CHECKED and item["text"].endswith(why), item)


def test_reports_from_before_checked_existed_cannot_say_fine_or_gone():
    def old(rows=()):
        out = entry("main_thread_waits", rows)
        del out["checked"]
        return out
    item = reading(report([old([steady("worker_pool_#", 700)])]), report([old()]))["checks"][0]
    check("an empty side of an old report did not say it looked", item["movement"] == moves.NOT_CHECKED, item)
    check("and says what to do", item["text"] == "Not checked after, so there is nothing to compare. "
          "This report was written before echolot recorded whether a check could run. "
          "Analyse the recordings again to find out.", item["text"])


def test_reports_from_before_the_plain_keys_read_the_same():
    before = report([entry("main_thread_waits", [steady("worker_pool_#", 700)]), entry("gc_pressure")], budget=500.0)
    after = report([entry("main_thread_waits"), entry("gc_pressure")], launches=(1400.0, 1410.0, 1420.0), budget=500.0)
    want = reading(before, after)

    def bare(rep):
        rep = copy.deepcopy(rep)
        del rep["verdict"]
        for det in rep["detectors"]:
            del det["plain"], det["attention"]
        return rep
    got = reading(bare(before), bare(after))
    check("the keys are worked out on a copy", got == want, got)
    check("and the reports are not touched", "verdict" not in bare(before))


def test_the_checks_come_in_the_order_a_reader_needs_them():
    def pair(did, b, a):
        return entry(did, b), entry(did, a)
    pairs = [pair("gc_pressure", [], []),
             pair("main_thread_waits", [steady("worker_pool_#", 700)], []),
             pair("main_thread_block", [steady("inflate", 300)], [steady("inflate", 600)]),
             pair("binder_txn", [steady("main", 200)], [steady("main", 205)]),
             pair("uninstrumented_cpu", [steady("pool", 900)], [steady("pool", 300)]),
             pair("io_wait", [], [])]
    pairs[5] = (entry("io_wait", checked=False, reason="No state."), entry("io_wait", checked=False, reason="No state."))
    got = reading(report([b for b, _ in pairs]), report([a for _, a in pairs]))
    check("worse first, then what got better by how much, no change, fine, not checked",
          [(c["id"], c["movement"]) for c in got["checks"]] == [
              ("main_thread_block", "worse"), ("main_thread_waits", "gone"), ("uninstrumented_cpu", "better"),
              ("binder_txn", "no_change"), ("gc_pressure", "fine_both"), ("io_wait", "not_checked")],
          [(c["id"], c["movement"]) for c in got["checks"]])
    check("every movement is one of the seven", {c["movement"] for c in got["checks"]} <= set(moves.MOVEMENTS))


# --- the verdict ------------------------------------------------------------------------


def verdict(before_launches, after_launches, **kw):
    return reading(report([], launches=before_launches, **kw), report([], launches=after_launches, **kw))["verdict"]


def test_a_launch_a_third_shorter_reads_as_better_in_one_sentence():
    v = verdict((1968.0, 2118.0, 2166.0), (1418.0, 1421.0, 1477.0))
    check("better", v["movement"] == moves.BETTER, v)
    check("the headline is the gain", v["headline"] == "Faster by 0.7 s", v["headline"])
    check("the sentence has the two times and the launches a side", v["text"]
          == "The launch went from 2.1 s to 1.4 s, 33% less. Measured on 3 launches before and 3 after.", v["text"])
    check("with the figures as numbers", (v["before_ms"], v["after_ms"]) == (2118.0, 1421.0))


def test_a_launch_that_grew_reads_as_worse():
    v = verdict((1418.0, 1421.0, 1477.0), (1968.0, 2118.0, 2166.0))
    check("worse", v["movement"] == moves.WORSE and v["headline"] == "Slower by 0.7 s" and v["text"]
          == "The launch went from 1.4 s to 2.1 s, 49% more. Measured on 3 launches before and 3 after.", v)


def test_what_two_rounds_of_the_same_app_differ_by_is_no_change():
    """The same build recorded on two days: 15% apart, every launch slower."""
    v = verdict((2035.0, 2041.0, 2079.0), (2378.0, 2388.0, 2596.0))
    check("no clear change", v["movement"] == moves.NO_CHANGE and v["headline"] == "No clear change", v)
    check("and why", v["text"] == "The launch took 2.0 s before and 2.4 s after, 17% more. The same app "
          "differs by that much between two rounds of recordings, so a change under 20% is not counted. "
          "Measured on 3 launches before and 3 after.", v["text"])


def test_two_times_that_read_alike_are_said_without_a_percentage():
    v = verdict((1070.0, 1080.0, 1090.0), (1110.0, 1120.0, 1130.0))
    check("1.1 s and 1.1 s, with no 4% between them", v["movement"] == moves.NO_CHANGE and v["text"]
          == "The launch took 1.1 s before and 1.1 s after. Measured on 3 launches before and 3 after.", v["text"])


def test_launches_that_overlap_are_no_change():
    v = verdict((1000.0, 2000.0, 2100.0), (1100.0, 1200.0, 1300.0))
    check("the fastest launch before beats half of the launches after", v["movement"] == moves.NO_CHANGE
          and "That is more than 20%, but single launches of the two sides overlap" in v["text"], v)
    v = verdict((1900.0, 2000.0, 2100.0), (1100.0, 1200.0, 3000.0))
    check("one slow launch after does not hide a change", v["movement"] == moves.BETTER, v)


def kept(before_launches, after_launches, **kw):
    """The verdict of two reports that keep each launch's window, as a merged
    report does (#532): `compare` gives the window's move a range."""
    reps = []
    for launches in (before_launches, after_launches):
        rep = report([], launches=launches, **kw)
        rep["window"]["values"] = [float(v) for v in launches]
        reps.append(rep)
    return reading(*reps)["verdict"]


def test_a_window_whose_move_does_not_hold_is_no_change_however_far_the_medians_are():
    """Four of five launches after beat every launch before, and the fifth is
    as slow as any of them: by the medians alone that read as better. The
    window's range, 95% sure, runs through zero, as the Holds beside it in
    the table says, and the verdict does not say otherwise (#532)."""
    before, after = (1900.0, 1920.0, 1940.0, 1960.0, 1980.0), (1100.0, 1120.0, 1140.0, 1160.0, 3000.0)
    check("by the medians alone, better", verdict(before, after)["movement"] == moves.BETTER)
    v = kept(before, after)
    check("by the window's range, no change", v["movement"] == moves.NO_CHANGE
          and v["headline"] == "No clear change", v)
    check("and why, with the range and the launches that would settle it", v["text"]
          == "The launch took 1.9 s before and 1.1 s after, 41% less. That is more than 20%, but the "
             "launches of each side scatter too much to be 95% sure of it: the move lies anywhere from "
             "-0.9 s to +1.1 s, so it is not counted as a change. More launches a side would settle it. "
             "Measured on 5 launches before and 5 after.", v["text"])
    v = kept((*before, 2000.0, 2020.0), (*after[:-1], 1180.0, 1200.0, 3000.0))
    check("from seven a side one slow launch no longer hides it", v["movement"] == moves.BETTER, v)


def test_a_window_whose_move_holds_is_judged_by_the_fifth_as_before():
    v = kept((1418.0, 1421.0, 1477.0, 1450.0), (1968.0, 2118.0, 2166.0, 2050.0))
    check("worse, in the same sentence", v["movement"] == moves.WORSE and v["headline"] == "Slower by 0.7 s", v)
    v = kept((2035.0, 2041.0, 2079.0, 2060.0), (2378.0, 2388.0, 2596.0, 2400.0))
    check("a move that holds and is under a fifth is still no change",
          v["movement"] == moves.NO_CHANGE and "a change under 20% is not counted" in v["text"], v)


def test_fewer_than_three_launches_a_side_give_no_verdict():
    v = verdict((2118.0,), (1421.0,))
    check("one a side", v["movement"] == moves.NO_CHANGE and v["text"]
          == "The launch took 2.1 s before and 1.4 s after, 33% less. One launch a side cannot tell a change "
          "from a hiccup: it takes 3 a side.", v)
    v = verdict((2100.0, 2118.0, 2130.0), (1421.0, 1430.0))
    check("three against two", v["movement"] == moves.NO_CHANGE and "3 launches before and 2 after are too "
          "few to tell a change from a hiccup: it takes 3 a side." in v["text"], v)


def test_a_stretch_that_is_not_a_launch_is_a_run():
    v = verdict((1968.0, 2118.0, 2166.0), (1418.0, 1421.0, 1477.0), startup=False)
    check("a run", v["text"] == "The run went from 2.1 s to 1.4 s, 33% less. Measured on 3 runs before and 3 after.", v)


def test_two_apps_are_not_compared_and_no_check_is_said_to_move():
    before = report([entry("main_thread_waits", [steady("worker_pool_#", 700)])])
    after = report([entry("main_thread_waits")], process="com.example.other")
    got = reading(before, after)
    v = got["verdict"]
    check("the verdict says so", v["movement"] == moves.NOT_COMPARED and v["headline"]
          == "These two cannot be compared" and v["text"] == "The first report is of `com.example.app` and the "
          "second of `com.example.other`: two apps, so nothing here is a change in one.", v)
    check("and there is no check to call gone", got["checks"] == [], got["checks"])
    check("nor a note that the two were recorded unlike", [n["id"] for n in got["notes"]] == ["few_runs"])


def test_a_report_with_no_window_has_no_two_times():
    before = report([])
    after = report([])
    after["window"]["duration_ms"] = None
    v = reading(before, after)["verdict"]
    check("nothing to hold against each other", v["movement"] == moves.NOT_COMPARED
          and v["headline"] == "No time to compare", v)


def test_what_compare_found_under_the_bar_counts_in_the_sum_of_its_side():
    """#715: 111 ms of calls before, the same calls at 83 ms after under the
    bar, read as "went from 0.2 s to 49 ms", the 111 ms all saved."""
    other = steady("com.example.app:bg", 49)
    before = report([entry("binder_txn", [steady("com.example.app", 110), other])])
    after = report([entry("binder_txn", [other],
                          below={"floor_ms": 5.0, "rows": [steady("com.example.app", 83)]})])
    item = reading(before, after)["checks"][0]
    check("the sum after is what was under the bar plus the rest", item["after"]["ms"] == 87.0 + 53.0
          and item["before"]["ms"] == 114.0 + 53.0, item)
    check("and that is not a change", item["movement"] == moves.NO_CHANGE, item)
    check("the row under the bar is no finding of that side", item["after"]["findings"] == 1
          and item["findings"] == {"gone": 0, "new": 0, "kept": 2}, item)


# --- the notes --------------------------------------------------------------------------


def notes(before_launches, after_launches, budget=None):
    got = reading(report([], launches=before_launches, budget=budget),
                  report([], launches=after_launches, budget=budget))
    return {n["id"]: n for n in got["notes"]}


def test_still_over_the_limit_in_the_reports_own_sentence():
    n = notes((1968.0, 2118.0, 2166.0), (1418.0, 1421.0, 1477.0), budget=520.0)["budget"]
    check("still over", n["title"] == "Still over the limit." and n["text"]
          == "The launch takes 1.4 s. You allowed 0.5 s, so it takes 2.7 times as long.", n)
    check("at the level the report gives the launch", n["attention"] == plain.NEEDS_ATTENTION, n)


def test_over_the_limit_now_within_it_now_and_no_limit_at_all():
    n = notes((400.0, 410.0, 420.0), (700.0, 710.0, 720.0), budget=520.0)["budget"]
    check("it was within and is over", n["title"] == "Over the limit.", n)
    n = notes((700.0, 710.0, 720.0), (400.0, 410.0, 420.0), budget=520.0)["budget"]
    check("it is within", n["title"] == "Within the limit." and n["attention"] == plain.FINE and n["text"]
          == "The launch takes 0.4 s. You allowed 0.5 s, so it is within the limit.", n)
    check("with no limit there is no such note", "budget" not in notes((700.0, 710.0, 720.0), (400.0, 410.0, 420.0)))


def test_too_few_repeats_to_say_which_single_changes_hold():
    three = notes((2000.0, 2010.0, 2020.0), (2000.0, 2010.0, 2020.0))["few_runs"]
    check("three a side", three["title"] == "Not certain check by check yet." and three["text"]
          == "3 launches before and 3 after are too few to say which single changes hold. It takes four a side, "
          "or three against five.", three)
    single = notes((2000.0,), (2010.0,))["few_runs"]
    check("one a side is too few for anything", single["title"] == "Too few launches to tell." and single["text"]
          == "One launch before and one after cannot tell a change from a hiccup, so nothing here is called one. "
          "Record 3 launches or more a side for that, and four a side, or three against five, to be sure which "
          "single changes hold.", single)
    four = (2000.0, 2010.0, 2020.0, 2030.0)
    check("four a side is enough, as it is for `compare`", "few_runs" not in notes(four, four))
    check("and so is three against five", "few_runs" not in notes(four[:3], (*four, 2040.0)))


def test_launches_too_few_for_any_move_are_said_before_anything_else():
    """The same code, the button pressed twice, three launches and then two:
    rows come and go by the dozen, and nothing may read as better."""
    odd = [row("main", 448.0, 430.0, 470.0), row("worker", 90.0, of=3)]
    now = [row("main", 270.0, 276.0)]
    before = report([entry("binder_txn", odd), entry("main_thread_block", [steady("inflate", 498)])],
                    launches=(877.0, 938.0, 1076.0), budget=520.0)
    after = report([entry("binder_txn", now), entry("main_thread_block", [row("inflate", 470.0, 484.0)])],
                   launches=(945.0, 1005.0), budget=520.0)
    got = reading(before, after)
    check("the verdict is no change, and says the launches are too few", got["verdict"]["movement"] == moves.NO_CHANGE
          and "3 launches before and 2 after are too few to tell a change from a hiccup" in got["verdict"]["text"],
          got["verdict"])
    check("the first note is that one, before the limit", [n["id"] for n in got["notes"]] == ["few_runs", "budget"]
          and got["notes"][0]["title"] == "Too few launches to tell." and got["notes"][0]["text"].startswith(
              "3 launches before and 2 after cannot tell a change from a hiccup, so nothing here is called one."),
          got["notes"])
    check("no check got better, however far its sum went", [c["movement"] for c in got["checks"]] == [moves.NO_CHANGE] * 2
          and got["checks"][0]["before"]["ms"] == 448.0 and got["checks"][0]["after"]["ms"] == 273.0, got["checks"])
    check("and each says why", all(c["text"].endswith("3 launches before and 2 after are too few to call that a change.")
                                   for c in got["checks"]), [c["text"] for c in got["checks"]])
    three = reading(report([], budget=520.0), report([], budget=520.0))
    check("with three a side the limit comes first, as before", [n["id"] for n in three["notes"]] == ["budget", "few_runs"]
          and three["notes"][1]["title"] == "Not certain check by check yet.")


def test_the_two_were_not_recorded_alike():
    before = report([entry("gc_pressure")])
    after = report([entry("gc_pressure")])
    before["environment"] = {"compile": {"filter": "speed-profile", "reason": "install-dm"}}
    after["environment"] = {"compile": {"filter": "verify", "reason": "install"}}
    before["config"], after["config"] = {"sha": "aaa"}, {"sha": "bbb"}
    got = {n["id"]: n for n in reading(before, after)["notes"]}
    check("said once, in a person's words", got["unlike"]["title"] == "Not recorded alike." and got["unlike"]["text"]
          == "Besides the app, 2 things differed between the two: the config changed; the app was compiled "
          "differently. A time can move for that reason alone. The technical comparison says more.", got["unlike"])
    check("with the warnings it says, by id, for a reader that is not a person",
          got["unlike"]["warnings"] == ["config", "compile"], got["unlike"])
    alike = {n["id"] for n in reading(report([]), report([]))["notes"]}
    check("and not at all for two that were", "unlike" not in alike, alike)


# --- the reading as a whole -------------------------------------------------------------


def known():
    before = report([
        entry("main_thread_waits", [steady("worker_pool_#", 700, detail="blocked in loadConfig")]),
        entry("main_thread_block", [steady("inflate", 300), steady("bind", 200)]),
        entry("binder_txn", [row("main", 40, 200, 230)]),
        entry("gc_pressure"),
        entry("io_wait", checked=False, reason="The recording does not carry the phone's own state."),
    ], launches=(1968.0, 2118.0, 2166.0), budget=520.0)
    after = report([
        entry("main_thread_waits"),
        entry("main_thread_block", [steady("inflate", 310), steady("bind", 195)]),
        entry("binder_txn", [row("main", 45, 50, 52)]),
        entry("gc_pressure"),
        entry("io_wait", checked=False, reason="The recording does not carry the phone's own state."),
    ], launches=(1418.0, 1421.0, 1477.0), budget=520.0)
    return before, after


KNOWN_VIEW = """\
BETTER        Faster by 0.7 s. The launch went from 2.1 s to 1.4 s, 33% less. Measured on 3
              launches before and 3 after.

 !  Still over the limit. The launch takes 1.4 s. You allowed 0.5 s, so it takes 2.7 times as
    long.
 ~  Not certain check by check yet. 3 launches before and 3 after are too few to say which
    single changes hold. It takes four a side, or three against five.

 GONE       The screen waits for background work
            This check finds nothing now. Before: The main thread waited 0.7 s for
            `worker_pool_#`, in all 3 launches. That is 33% of the launch. It was inside
            `loadConfig` at the time.
 NO CHANGE  Calls to the system or to other apps
            Added up, what this check found was 0.2 s before and 50 ms after. Single launches of
            the two sides overlap, so it is not counted as a change.
 NO CHANGE  Heavy work on the main thread
            Added up, what this check found was 0.5 s before and 0.5 s after.

 ok 1 check is fine both times     -  1 could not be checked on one side or both"""


def test_the_terminal_view_of_a_known_pair():
    got = moves.render(reading(*known()), width=96)
    check("the list", got == KNOWN_VIEW, got)


def test_the_same_two_reports_give_the_same_reading():
    before, after = known()
    first = json.dumps(reading(before, after), sort_keys=True)
    again = json.dumps(reading(copy.deepcopy(before), copy.deepcopy(after)), sort_keys=True)
    check("word for word", first == again)
    check("and nothing in it is a time of day", "generated_at" not in first)


def test_a_movement_and_a_level_are_two_words_in_the_keys():
    got = reading(*known())

    def keys(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield f"{path}/{k}"
                yield from keys(v, f"{path}/{k}")
        elif isinstance(node, list):
            for v in node:
                yield from keys(v, path)
    found = set(keys(got))
    check("no key calls a movement a level", not any(k.endswith("/level") for k in found), sorted(found))
    check("a check has a movement and no attention of its own",
          "/checks/movement" in found and "/checks/attention" not in found)
    check("what each report said stays with its side", "/checks/before/attention" in found
          and "/checks/after/attention" in found)
    check("the verdict has a movement", got["verdict"]["movement"] in (*moves.MOVEMENTS, moves.NOT_COMPARED))
    check("a note says how much attention it needs", all(n["attention"] in plain.LEVELS for n in got["notes"]))


def test_the_demo_pair_reads_without_a_claim_it_cannot_back(demo_report, demo_changed_report):
    got = reading(demo_report, demo_changed_report)
    check("every check of the two reports has an item",
          {c["id"] for c in got["checks"]} == {d["id"] for d in demo_report["detectors"]}
          | {d["id"] for d in demo_changed_report["detectors"]})
    check("the verdict is a sentence", got["verdict"]["text"].endswith("Measured on 5 launches before and 5 after."),
          got["verdict"])
    same = reading(demo_report, demo_report)
    check("a report against itself is the same recordings, and moves nothing (#713)",
          same["verdict"]["movement"] == moves.NOT_COMPARED
          and not {c["movement"] for c in same["checks"]} & {moves.BETTER, moves.WORSE, moves.GONE, moves.NEW}, same)


# --- through the command ----------------------------------------------------------------


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def pair_on_disk(tmp_path, monkeypatch):
    before, after = known()
    (tmp_path / "before.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(after), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "96")
    return tmp_path


def test_the_command_prints_the_reading_and_keeps_it_beside_the_comparison(pair_on_disk):
    code, out, err = run("compare", "before.json", "after.json", "-o", "out", "--plain")
    check("it prints the list in place of the table", code == 0 and out == KNOWN_VIEW + "\n", out)
    written = json.loads((pair_on_disk / "out" / "comparison.json").read_text(encoding="utf-8"))
    before, after = known()
    check("comparison.json carries the same reading under `plain`", written["plain"] == reading(before, after))
    check("after everything it held before", list(written)[-1] == "plain" and written["kind"] == "comparison")
    table = (pair_on_disk / "out" / "comparison.md").read_text(encoding="utf-8")
    check("comparison.md is still the table", table.startswith("# Comparison") and "Faster by" not in table, table[:80])


def test_without_the_flag_compare_computes_prints_and_writes_what_it_did(pair_on_disk, monkeypatch):
    class Fixed:
        @staticmethod
        def now(tz=None):
            from datetime import datetime
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)
    monkeypatch.setattr(compare_mod, "datetime", Fixed)
    before, after = known()
    want = compare_mod.build(before, after, before_path="before.json", after_path="after.json",
                             temp_prefix="AGENTTMP_")
    code, out, err = run("compare", "before.json", "after.json", "-o", "out")
    check("the table, byte for byte", code == 0 and out == compare_mod.to_markdown(want) + "\n", out)
    check("comparison.md, byte for byte", (pair_on_disk / "out" / "comparison.md").read_text(encoding="utf-8")
          == compare_mod.to_markdown(want))
    check("comparison.json, byte for byte, with no reading in it",
          (pair_on_disk / "out" / "comparison.json").read_text(encoding="utf-8") == report_mod.to_json(want)
          and "plain" not in want)
    check("and the two lines that say where they went", err.count("→") == 2, err)


def test_the_flag_changes_nothing_the_comparison_already_held(pair_on_disk):
    run("compare", "before.json", "after.json", "-o", "plainly", "--plain")
    run("compare", "before.json", "after.json", "-o", "as-before")
    with_flag = json.loads((pair_on_disk / "plainly" / "comparison.json").read_text(encoding="utf-8"))
    without = json.loads((pair_on_disk / "as-before" / "comparison.json").read_text(encoding="utf-8"))
    del with_flag["plain"]
    with_flag["generated_at"] = without["generated_at"]
    check("every key it had is as it was", with_flag == without)
    check("and so is the table", (pair_on_disk / "plainly" / "comparison.md").read_text(encoding="utf-8").split("\n", 4)[4]
          == (pair_on_disk / "as-before" / "comparison.md").read_text(encoding="utf-8").split("\n", 4)[4])


def test_no_exit_code_depends_on_a_movement(pair_on_disk):
    before, after = known()
    (pair_on_disk / "before.json").write_text(json.dumps(after), encoding="utf-8")
    (pair_on_disk / "after.json").write_text(json.dumps(before), encoding="utf-8")
    code, out, err = run("compare", "before.json", "after.json", "--plain")
    check("worse, far over the limit, and still 0", code == 0 and out.startswith("WORSE"), (code, out[:80]))


def test_a_reading_that_fails_leaves_the_table(pair_on_disk, monkeypatch):
    def broken(*args):
        raise KeyError("window")
    monkeypatch.setattr(moves, "read", broken)
    code, out, err = run("compare", "before.json", "after.json", "-o", "out", "--plain")
    check("the comparison is printed all the same", code == 0 and out.startswith("# Comparison"), out[:80])
    check("and it says what happened", "the plain reading could not be worked out" in err, err)
    check("with nothing half-written", "plain" not in json.loads(
        (pair_on_disk / "out" / "comparison.json").read_text(encoding="utf-8")))


# --- not measured alike (#713) ----------------------------------------------------------


def same_recordings(*reps):
    """The same three recordings read twice: names, pid and window alike."""
    for rep in reps:
        rep["traces"] = [f"traces/coldStart_iter00{i}.perfetto-trace" for i in range(3)]
    return reps


def test_the_same_recordings_on_both_sides_are_not_compared_and_no_check_moves():
    """#713: the same three recordings read with two sets of thresholds read
    "WORSE Heavy work on the main thread", 1.1 s to 1.6 s."""
    before, after = same_recordings(
        report([entry("main_thread_block", [steady("inflate", 300)]), entry("gc_pressure")]),
        report([entry("main_thread_block", [steady("inflate", 300), steady("bind", 600)]),
                entry("gc_pressure")]))
    got = reading(before, after)
    v = got["verdict"]
    check("the verdict says what the two are first", v["movement"] == moves.NOT_COMPARED
          and v["headline"] == "The same recordings on both sides"
          and v["text"].startswith("Both sides are the same recordings: the same 3 trace names, the same "
                                   "process (pid 4100)") and "no check below is called better or worse" in v["text"], v)
    block = next(c for c in got["checks"] if c["id"] == "main_thread_block")
    check("the check that found more is not called worse", block["movement"] == moves.NOT_COMPARED
          and block["text"] == "Both sides are the same recordings, so whatever moved here is the tool's or the "
                               "config's, not the app's. Added up, what it found was 0.3 s before and 0.9 s after.",
          block)
    check("no check is better, worse, new or gone",
          not {c["movement"] for c in got["checks"]} & {moves.BETTER, moves.WORSE, moves.NEW, moves.GONE},
          [c["movement"] for c in got["checks"]])
    check("one that found nothing either time is still fine both times",
          next(c for c in got["checks"] if c["id"] == "gc_pressure")["movement"] == moves.FINE_BOTH)
    text = moves.render(got, width=100)
    check("the terminal says it at the top, and names the check as not compared",
          text.startswith("NOT COMPARED  The same recordings on both sides.")
          and " NOT COMPARED Heavy work on the main thread" in text and "WORSE" not in text, text)


def test_a_check_whose_threshold_changed_is_not_compared_and_the_others_keep_their_verdict():
    before = report([entry("main_thread_block", [steady("inflate", 300)], params={"min_slice_ms": 83.3}),
                     entry("binder_txn", [steady("binder transaction", 600)], params={"min_txn_ms": 14.4})])
    after = report([entry("main_thread_block", [steady("inflate", 300), steady("bind", 600)],
                          params={"min_slice_ms": 16}),
                    entry("binder_txn", [steady("binder transaction", 300)], params={"min_txn_ms": 14.4})])
    got = reading(before, after)
    block = next(c for c in got["checks"] if c["id"] == "main_thread_block")
    check("the check whose threshold moved is not compared, and says from what to what",
          block["movement"] == moves.NOT_COMPARED and block["text"]
          == "Its thresholds changed between the two (min_slice_ms from 83.3 to 16 ms), so it did not count by "
             "the same measure on both sides. Added up, what it found was 0.3 s before and 0.9 s after.", block)
    binder = next(c for c in got["checks"] if c["id"] == "binder_txn")
    check("a check with the same thresholds keeps its verdict", binder["movement"] == moves.BETTER, binder)
    check("the verdict is still the window's", got["verdict"]["movement"] == moves.NO_CHANGE, got["verdict"])
    check("and the note that the two were read unlike stays",
          any(n["id"] == "unlike" for n in got["notes"]), got["notes"])


def test_a_check_another_echolot_read_differently_is_not_compared():
    before = report([entry("main_thread_block", [steady("inflate", 300)], sha="aaa")])
    after = report([entry("main_thread_block", [steady("inflate", 900)], sha="bbb")])
    for rep, version in ((before, "0.17.0"), (after, "0.18.0")):
        rep["toolchain"] = {"echolot": version}
    item = reading(before, after)["checks"][0]
    check("not compared, and why", item["movement"] == moves.NOT_COMPARED
          and item["text"].startswith("The two versions of echolot that wrote the reports read this check "
                                      "differently"), item)
