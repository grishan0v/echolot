#!/usr/bin/env python3
"""The plain reading of a report: fixed texts, a sentence per detector, a level.

A report row is written for an agent that knows the tool. `echolot/plain.py`
says the same report to someone who has never opened a trace, and a model
writes none of it, so what is pinned here is words: the same report gives the
same ones. Three things hold it together:

  - every shipped detector has its three texts in its .sql header and a
    template in `plain.FOUND`, and this fails for a new detector without them;
  - the level rule is one sentence per level, and its edges are the places
    where a number moves a row from one to the next;
  - a sentence never claims more than its rows do.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import plain, recorder, tp  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402
from echolot.analyze import DETECTOR_DIR  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

SHIPPED = tp.load_detectors(DETECTOR_DIR)
MAIN = "com.example.app"


def row(location, total, runs="3/3", **kw):
    out = {"location": location, "runs": runs, "count": kw.pop("count", 1), "total_ms": total}
    out.update(kw)
    return out


def entry(did, rows, **extra):
    """An entry as `analyze` writes it: `checked` is always there."""
    return {"id": did, "title": did, "why": "", "params": {}, "params_source": "default",
            "identity": ["location"], "rows": rows, "error": None, "checked": True, **extra}


def old_entry(did, rows, **extra):
    """An entry of a report written before `checked` existed (#441): no such key."""
    out = entry(did, rows, **extra)
    del out["checked"]
    return out


OLD_REPORT = ("This report was written before echolot recorded whether a check could run. "
              "Analyse the recordings again to find out.")


def make(detectors, duration=2000.0, budget=None, runs=3, startup=True, process=MAIN):
    window = {"process": process, "pid": 4100, "duration_ms": duration}
    if startup:
        window["startup"] = {"type": "cold", "dur_ms": duration}
    rep = {"schema": 1, "scenario": "coldStart", "budget_ms": budget, "window": window,
           "summary": {"absent_ids": []}, "detectors": detectors}
    if runs:
        rep["runs"] = runs
    return rep


def level(did, rows, duration=2000.0, **kw):
    rep = make([entry(did, rows, **kw)], duration=duration)
    return plain.attention(rep["detectors"][0], rep)


def says(did, rows, duration=2000.0, **kw):
    rep = make([entry(did, rows, **kw)], duration=duration)
    return plain.found(rep["detectors"][0], rep)


# Rows shaped like each detector's own, in a 2000 ms window, with the level
# and the sentence they come to. Every shipped detector has to be here.
SAMPLES = {
    "main_thread_waits": (
        [row("worker_pool_#", 700.0, count=259, max_ms=15.2, detail="blocked in loadConfig")],
        "needs_attention",
        "The main thread waited 0.7 s for `worker_pool_#`, in all 3 launches. That is 35% of the "
        "launch. It was inside `loadConfig` at the time."),
    "uninstrumented_cpu": (
        [row("worker_pool_0", 480.0, count=6, covered_ms=1.0, detail="99% of CPU outside slices"),
         row("worker_pool_1", 300.0, count=4, covered_ms=2.0, detail="98% of CPU outside slices")],
        "worth_a_look",
        "2 threads or pools of threads spent 0.8 s of processor time between them on work that "
        "nothing in the recording names. The largest part is on `worker_pool_0`: 0.5 s, in all "
        "3 launches."),
    "main_thread_outlier": (
        [row("bindView", 280.0, count=2, max_ms=170.0, detail="median 0.3 ms of 80 · worst 566.7×")],
        "worth_a_look",
        "`bindView` took far longer than usual 2 times, in all 3 launches: 0.3 s together, 0.2 s "
        "at worst. It usually takes 0.3 ms. Together that is 14% of the launch."),
    "main_thread_block": (
        [row("Screen.render", 420.0, count=3, self_ms=380.0, max_ms=200.0, detail=MAIN),
         row("Adapter.bind", 90.0, count=3, self_ms=60.0, max_ms=40.0, detail=MAIN)],
        "needs_attention",
        "The heaviest piece is `Screen.render`: 0.4 s of its own work, in all 3 launches. That is "
        "19% of the launch. 1 more piece took long, too."),
    "binder_txn": (
        [row(MAIN, 310.0, count=87, max_ms=17.5, detail="main thread")],
        "needs_attention",
        "The main thread waited 0.3 s for calls to the system or to other apps, in all 3 "
        "launches. The longest took 18 ms. That is 15% of the launch."),
    "monitor_contention": (
        [row("Sync Worker", 40.0, runs="2/3", count=4, max_ms=12.0,
             detail="monitor contention with owner Sync Worker 2 (4455)")],
        "worth_a_look",
        "`Sync Worker` waited 40 ms for a lock that another thread held, in 2 of 3 launches. The "
        "longest wait was 12 ms."),
    "repeated_work": (
        [row("Store.load", 200.0, count=2, max_ms=100.0,
             detail="entered from 2: Home.open,Home.refresh — on com.example.app")],
        "worth_a_look",
        "`Store.load` is reached from 2 places and costs 0.2 s together, in all 3 launches. Each "
        "place costs about the same, so the same work may be done more than once. That is 10% of "
        "the launch."),
    "app_init": (
        [row("(no section)", 120.0, max_ms=120.0,
             detail="no section holds it: ContentProviders and Application.onCreate run here "
                    "and write nothing."),
         row("Analytics", 30.0, runs="2/3", max_ms=30.0,
             detail="a section under bindApplication, after makeApplication")],
        "worth_a_look",
        "The setup before the first screen was created took long, in all 3 launches. The largest "
        "of its 2 pieces is setup code that nothing in the recording names: 0.1 s. That is 6% of "
        "the launch."),
    "gc_pressure": (
        [row("concurrent mark compact GC", 140.0, count=22, max_ms=12.0, detail="HeapTaskDaemon")],
        "worth_a_look",
        "Memory clean-up ran 22 times on `HeapTaskDaemon`, in all 3 launches: 0.1 s together, "
        "the longest 12 ms."),
    "runnable_starvation": (
        [row(MAIN, 90.0, count=12, max_ms=30.0, state="R", detail="state R")],
        "worth_a_look",
        "The main thread was ready to work but had no free processor core for 90 ms, in all 3 "
        "launches. The longest wait was 30 ms. That is 4% of the launch."),
    "io_wait": (
        [row(MAIN, 260.0, count=9, max_ms=80.0, thread="main", detail="main · wait_on_page")],
        "worth_a_look",
        "The main thread slept 0.3 s waiting for the disk, in all 3 launches. The longest wait was "
        "80 ms. That is 13% of the launch."),
    "main_thread_kernel_waits": (
        [row("worker_pool_#", 130.0, count=14, max_ms=40.0, detail="waking outside any slice 90%")],
        "worth_a_look",
        "The main thread was held up inside the system for 0.1 s, until `worker_pool_#` let it go, "
        "in all 3 launches. That is 6% of the launch."),
    "frame_jank": (
        [row("App Deadline Missed", 160.0, count=6, max_ms=48.0,
             detail="Self Jank · 6 of 41 frames · longest 80.0 ms")],
        "worth_a_look",
        "6 of 41 frames came late because of the app, in all 3 launches: 0.2 s past their "
        "deadlines together, 48 ms at worst. Together that is 8% of the launch."),
    "anr": (
        [row("Input dispatching timed out", 5000.0, max_ms=5000.0,
             detail="inside the window at +120.0 ms · abc-123")],
        "needs_attention",
        "The system recorded a freeze during the launch: `Input dispatching timed out`, in all 3 "
        "launches."),
    "anr_risk": (
        [row("Parser.run", 1500.0, max_ms=1500.0,
             detail="1.4 s on CPU · 0 ms waiting for one · 0 ms neither · 3 slices")],
        "needs_attention",
        "The main thread went 1.5 s without answering, while `Parser.run` was open, in all 3 "
        "launches. That is 75% of the launch."),
    "composables": (
        [row("HomeScreen", 210.0, count=5, self_ms=190.0, max_ms=90.0,
             detail="2 in a first composition")],
        "worth_a_look",
        "`HomeScreen` spent 0.2 s of its own time building the screen, in all 3 launches. That is "
        "9% of the launch."),
}

JARGON = ("slice", "binder", "kernel", "perfetto", "trace", "sql", "runnable", "gc ", "ipc",
          "anr", "jank", "dofram", "uninterrupt", "instrument", "callstack", "`", "@")


def sentences(text):
    return re.findall(r"[^.!?]+[.!?]", text)


# --- every shipped detector speaks, and the test fails for one that does not ------

@pytest.mark.parametrize("det", SHIPPED, ids=lambda d: d.id)
def test_every_shipped_detector_has_its_three_texts(det):
    """What was checked, how it touches the app, what a clean result says."""
    for name in ("plain_title", "plain_why", "plain_clean"):
        text = getattr(det, name)
        check(f"{det.id} has @{name}", text.strip(),
              "add the three `@plain_*` lines after the last key of its header")
        check(f"{det.id}: @{name} is plain words", not any(j in text.lower() for j in JARGON),
              text)
        check(f"{det.id}: @{name} starts a sentence", text[0].isupper(), text)
    check(f"{det.id}: the title is a heading, not a sentence", not det.plain_title.endswith("."),
          det.plain_title)
    for name in ("plain_why", "plain_clean"):
        text = getattr(det, name)
        check(f"{det.id}: @{name} is one or two sentences, whole",
              text.endswith(".") and 1 <= len(sentences(text)) <= 2, text)
    check(f"{det.id}: the three are not the detector's own words",
          det.plain_title != det.title and det.plain_why != det.why)


@pytest.mark.parametrize("det", SHIPPED, ids=lambda d: d.id)
def test_every_shipped_detector_has_a_template_and_a_sample(det):
    check(f"{det.id} has a template in plain.FOUND", det.id in plain.FOUND,
          "add one, and say what its rows say and no more")
    check(f"{det.id} has sample rows in this test", det.id in SAMPLES,
          "add its rows and the sentence they come to to SAMPLES")


def test_the_fixed_texts_come_from_the_headers():
    for det in SHIPPED:
        check(det.id, plain.fixed_texts(det.id) == (det.plain_title, det.plain_why, det.plain_clean))


def test_a_detector_this_version_does_not_know_still_reads():
    """A report from a newer echolot: its own title, no second text, a generic third."""
    title, why, clean = plain.fixed_texts("from_the_future", "Something new")
    check("the title is the detector's own", title == "Something new", title)
    check("there is no second text to make up", why == "", why)
    check("and the third claims nothing", clean == "It found nothing above its limit.", clean)
    said = says("from_the_future", [row("Thing.run", 300.0, count=2)])
    check("the sentence is the generic one", said == "1 result. The largest is `Thing.run`: "
          "0.3 s, in all 3 launches.", said)


# --- the sentence per detector ----------------------------------------------------

@pytest.mark.parametrize("did", sorted(SAMPLES))
def test_the_sentence_and_the_level_of_every_detector(did):
    rows, want_level, want_text = SAMPLES[did]
    check(f"{did}: the level", level(did, rows) == want_level, level(did, rows))
    check(f"{did}: the words", says(did, rows) == want_text, says(did, rows))


def test_the_same_rows_give_the_same_words_in_any_order():
    rows = SAMPLES["uninstrumented_cpu"][0]
    check("the order of the rows is not what the sentence is made of",
          says("uninstrumented_cpu", rows) == says("uninstrumented_cpu", rows[::-1]))


def test_a_silent_detector_says_nothing_about_what_it_found():
    rep = make([entry("main_thread_block", [])])
    det = rep["detectors"][0]
    check("no sentence", plain.found(det, rep) is None)
    plain.annotate(rep)
    check("fine", det["attention"] == "fine", det["attention"])
    check("and its clean text is the one from the header",
          det["plain"]["clean"] == "No single piece of work held the main thread for long.",
          det["plain"]["clean"])
    check("found is null, and so is the reason", det["plain"]["found"] is None
          and det["plain"]["reason"] is None)


def test_a_report_from_one_recording_does_not_count_launches():
    rows = [dict(r) for r in SAMPLES["main_thread_waits"][0]]
    for r in rows:
        del r["runs"]
    said = says("main_thread_waits", rows, runs=None)
    check("no 'in all 3 launches' for a report that has no runs",
          said.startswith("The main thread waited 0.7 s for `worker_pool_#`. That is 35%"), said)
    check("and it still counts as seen every time",
          level("main_thread_waits", rows) == "needs_attention")


def test_a_stretch_that_is_not_a_launch_is_a_run():
    rep = make([entry("main_thread_waits", SAMPLES["main_thread_waits"][0])], startup=False)
    said = plain.found(rep["detectors"][0], rep)
    check("runs, not launches", "in all 3 runs" in said and "of the run" in said, said)
    check("the verdict too", plain.verdict(rep)["text"].startswith("The run takes 2.0 s."),
          plain.verdict(rep)["text"])


def test_what_a_row_does_not_say_a_sentence_does_not_say():
    """Three places where a template could claim more than the row holds."""
    inside = row("worker_pool_#", 700.0, count=2, detail="33% blocked in loadConfig")
    check("inside a slice for a third of it is not 'at the time'",
          "at the time" not in says("main_thread_waits", [inside]),
          says("main_thread_waits", [inside]))
    near = row("AGENTTMP_x", 90.0, count=2,
               detail="near miss: entered from 2: A,B — on com.example.app; occurrences too unlike")
    said = says("repeated_work", [near])
    check("a near miss is not called the same work twice",
          "may be done more than once" not in said and "differs too much" in said, said)
    one = row("bindView", 90.0, max_ms=90.0, count=1, detail="median 2.0 ms of 5 · worst 45.0×")
    check("one occurrence is 'One', and says no 'together'",
          says("main_thread_outlier", [one]).startswith("One `bindView` took 90 ms")
          and "together" not in says("main_thread_outlier", [one]), says("main_thread_outlier", [one]))
    background = row("Sync Worker", 600.0, detail="background", max_ms=20.0)
    said = says("binder_txn", [background])
    check("a background thread's calls are not the main thread's wait",
          said.startswith("`Sync Worker`, a background thread, spent 0.6 s") and "of the launch" not in said,
          said)


def test_a_share_is_rounded_down_so_that_15_percent_holds_15_percent():
    row_ = row("bindView", 299.0, max_ms=299.0, detail="median 2.0 ms of 5 · worst 100.0×")
    said = says("main_thread_outlier", [row_])
    check("14.95% reads 14%, and is worth a look",
          "That is 14% of the launch." in said and level("main_thread_outlier", [row_]) == "worth_a_look",
          said)
    exact = row("bindView", 300.0, max_ms=300.0, detail="median 2.0 ms of 5 · worst 100.0×")
    check("15% reads 15%, and needs attention",
          "That is 15% of the launch." in says("main_thread_outlier", [exact])
          and level("main_thread_outlier", [exact]) == "needs_attention")
    tiny = row("bindView", 3.0, max_ms=3.0)
    check("under a percent says so", "less than 1% of the launch" in says("main_thread_outlier", [tiny]))
    whole = row("Parser.run", 2500.0, max_ms=2500.0, detail="x")
    check("longer than the window is the whole of it",
          "as long as the whole launch" in says("anr_risk", [whole]), says("anr_risk", [whole]))


@pytest.mark.parametrize("ms, want", [(0.3, "0.3 ms"), (12.0, "12 ms"), (99.4, "99 ms"),
                                      (100.0, "0.1 s"), (696.5, "0.7 s"), (1918.65, "1.9 s"),
                                      (12400.0, "12.4 s")])
def test_milliseconds_the_way_a_person_says_them(ms, want):
    check(str(ms), plain.hum(ms) == want, plain.hum(ms))


# --- an idle main thread (#554) ---------------------------------------------------

def scroll(rows, duration=2000.0, **window):
    """A report of a window without anchors, whose length the script fixes."""
    rep = make([entry("main_thread_waits", rows)], duration=duration, startup=False)
    rep["window"].update({"start_anchor": None, "end_anchor": None, **window})
    return rep


def closed_launch(rows, duration=2000.0):
    """A launch whose end anchor matched in every repeat."""
    rep = make([entry("main_thread_waits", rows)], duration=duration)
    rep["window"].update({"start_anchor": {"glob": "bindApplication", "matches": 1},
                          "end_anchor": {"glob": "app_ready", "matches": 1}})
    return rep


def read(rep):
    det = rep["detectors"][0]
    return plain.attention(det, rep), plain.found(det, rep)


def test_idle_time_in_a_window_of_fixed_length_needs_no_attention():
    level_, said = read(scroll([row("DefaultExecutor", 400.0, detail="idle")]))
    check("20% of a scroll, all idle, is worth a look", level_ == "worth_a_look", level_)
    check("and the sentence says it had nothing to do, not that it waited",
          "nothing to do for 0.4 s" in said and "waited" not in said
          and "could still draw and answer taps" in said, said)


def test_a_blocked_wait_in_a_window_of_fixed_length_still_needs_attention():
    level_, said = read(scroll([row("worker_pool_#", 400.0, detail="blocked in loadConfig")]))
    check("needs attention, as before", level_ == "needs_attention", level_)
    check("and says it waited inside the slice",
          said.startswith("The main thread waited 0.4 s") and "inside `loadConfig`" in said, said)


def test_idle_time_before_a_launch_ends_keeps_its_weight():
    """The detector's own case: a first launch sat idle while Room filled its database."""
    level_, said = read(closed_launch([row("arch_disk_io_0", 660.0, detail="idle")]))
    check("33% of a launch whose end matched, all idle, needs attention",
          level_ == "needs_attention", level_)
    check("with the idle sentence, and that the launch may have waited",
          "nothing to do for 0.7 s" in said and "launch may have been waiting" in said, said)


def test_only_the_blocked_part_of_a_mixed_row_counts_in_a_fixed_window():
    rows = [row("worker_pool_#", 800.0, detail="67% idle · 33% blocked in loadConfig")]
    level_, said = read(scroll(rows))
    check("40% of the run weighs as 13.2%, worth a look", level_ == "worth_a_look", level_)
    check("the sentence gives the idle share", "For 67% of that time it had nothing to do." in said, said)
    check("the same row in a launch that ended where it should needs attention",
          read(closed_launch(rows))[0] == "needs_attention")


def test_idle_time_in_a_window_whose_anchor_missed_needs_no_attention():
    rep = closed_launch([row("DefaultExecutor", 660.0, detail="idle")])
    rep["window"]["end_anchor"] = {"glob": "app_ready", "matches": 0, "missed": "3/3"}
    level_, said = read(rep)
    check("a missed end anchor is a window of the recording's length", level_ == "worth_a_look", level_)
    check("so the idle sentence does not say the launch waited",
          "may have been waiting" not in said and "could still draw" in said, said)


def test_a_launch_the_platform_closed_is_a_window_that_ends_with_the_scenario():
    rep = make([entry("main_thread_waits", [row("arch_disk_io_0", 660.0, detail="idle")])])
    rep["window"].update({"start_anchor": None, "end_anchor": None, "defined_by": "launch"})
    check("idle time keeps its weight", read(rep)[0] == "needs_attention", read(rep))


# --- the level: one sentence each, and where a number moves a row -----------------

def test_the_rule_is_15_percent_and_150_ms_in_every_run_on_the_main_thread():
    def waits(ms, runs="3/3"):
        return [row("worker_pool_#", ms, runs=runs)]

    check("exactly 15% and 150 ms needs attention",
          level("main_thread_waits", waits(150.0), duration=1000.0) == "needs_attention")
    check("just under 15% of the launch is worth a look",
          level("main_thread_waits", waits(149.0), duration=1000.0) == "worth_a_look")
    check("15% of a short launch is under the floor, and is worth a look",
          level("main_thread_waits", waits(60.0), duration=400.0) == "worth_a_look")
    check("150 ms of a long launch is under 15%, and is worth a look",
          level("main_thread_waits", waits(150.0), duration=1500.0) == "worth_a_look")
    check("a row seen in two launches of three is worth a look, however big",
          level("main_thread_waits", waits(900.0, "2/3")) == "worth_a_look")
    check("a window of no length judges nothing, and claims nothing",
          level("main_thread_waits", waits(900.0), duration=0.0) == "worth_a_look")


def test_any_row_of_a_detector_can_carry_it_not_only_the_biggest():
    """The prototype read the biggest row, which may be seen in one launch only."""
    rows = [row("seen once", 900.0, runs="1/3", count=1), row("seen always", 400.0, count=1)]
    check("the steady row is the one that counts",
          level("main_thread_waits", rows) == "needs_attention")


def test_only_the_main_threads_time_can_need_attention_by_its_size():
    def big(did, loc="worker_pool_0", **kw):
        return level(did, [row(loc, 900.0, **kw)])

    check("the CPU time of another thread is a yardstick", big("uninstrumented_cpu") == "worth_a_look")
    check("the same of the main thread is a blind spot",
          big("uninstrumented_cpu", loc=MAIN) == "needs_attention")
    check("a background binder call",
          big("binder_txn", loc="Sync Worker", detail="background") == "worth_a_look")
    check("the main thread's own", big("binder_txn", loc=MAIN, detail="main thread") == "needs_attention")
    check("a background thread's disk wait",
          big("io_wait", thread="background", detail="background") == "worth_a_look")
    check("the main thread's", big("io_wait", loc=MAIN, thread="main", detail="main") == "needs_attention")
    check("clean-up on the GC's own thread", big("gc_pressure", detail="HeapTaskDaemon") == "worth_a_look")
    check("clean-up on the main thread", big("gc_pressure", detail=MAIN) == "needs_attention")
    check("a lock another thread waited for", big("monitor_contention") == "worth_a_look")
    check("one the main thread waited for", big("monitor_contention", loc=MAIN) == "needs_attention")
    check("a core the main thread waited for",
          big("runnable_starvation", loc=MAIN, state="R") == "needs_attention")
    check("work done twice on another thread",
          big("repeated_work", detail="entered from 2: A,B — on worker_pool_0") == "worth_a_look")
    check("on the main thread",
          big("repeated_work", detail=f"entered from 2: A,B — on {MAIN}") == "needs_attention")
    check("a near miss is a hint, never a finding",
          big("repeated_work", detail=f"near miss: entered from 2: A,B — on {MAIN}; x") == "move_marker")
    check("a detector this version does not know asks less", big("from_the_future") == "worth_a_look")


def test_a_process_with_a_long_name_has_a_main_thread_named_by_its_last_15_characters():
    process = "com.example.somelongproductname.app"
    rep = make([entry("uninstrumented_cpu", [row(process[-15:], 900.0)])], process=process)
    check("the kernel keeps the last 15 characters of the process's name for the main thread",
          plain.attention(rep["detectors"][0], rep) == "needs_attention")


def test_a_freeze_the_system_recorded_is_always_the_top_level():
    rows = [row("Input dispatching timed out", 0.0, runs="1/3", max_ms=0.0,
                detail="before the window by 4000.0 ms · abc")]
    check("whatever its size, whichever launch", level("anr", rows) == "needs_attention")
    said = says("anr", rows)
    check("and it says where it fell", "before the launch began" in said, said)
    twice = rows + [row("Broadcast of Intent", 0.0, detail="after the window by 10.0 ms · def")]
    check("two of them are counted", says("anr", twice).startswith("The system recorded 2 freezes."),
          says("anr", twice))


def test_silent_failed_and_not_checked():
    check("silent is fine", level("main_thread_block", []) == "fine")
    check("a detector that failed did not look: not checked, never fine",
          level("main_thread_block", [], error="no such table") == "not_checked")
    check("but one that failed and found rows reads its rows",
          level("main_thread_block", SAMPLES["main_thread_block"][0], error="1 run failed")
          == "needs_attention")
    check("an entry that says it was not checked is not checked", level(
        "io_wait", [], checked=False, reason="Record with the phone's state switched on.")
        == "not_checked")
    check("`checked: true` and nothing found is fine", level("io_wait", [], checked=True) == "fine")
    old = old_entry("io_wait", [])
    check("an entry with no `checked` at all is from before the key: not checked, never fine",
          plain.attention(old, make([old])) == "not_checked")
    failed_and_true = entry("io_wait", [], error="boom")
    check("`checked: true` over an error is still a check that did not look",
          plain.attention(failed_and_true, make([failed_and_true])) == "not_checked")


def test_a_report_from_before_the_key_says_so_for_every_silent_check():
    """`analyze` writes `checked` always, so an entry without it is older than #441."""
    rep = make([old_entry("io_wait", []), old_entry("gc_pressure", []),
                old_entry("main_thread_waits", SAMPLES["main_thread_waits"][0])])
    plain.annotate(rep)
    io_wait, gc, waits = rep["detectors"]
    check("the silent ones are not checked", io_wait["attention"] == gc["attention"] == "not_checked",
          (io_wait["attention"], gc["attention"]))
    check("and say that the report is too old to tell",
          io_wait["plain"]["reason"] == OLD_REPORT and gc["plain"]["reason"] == OLD_REPORT,
          (io_wait["plain"], gc["plain"]))
    check("one with rows reads its rows", waits["attention"] == "needs_attention", waits["attention"])
    check("says what it found, and has no reason to give",
          waits["plain"]["found"].startswith("The main thread waited 0.7 s")
          and waits["plain"]["reason"] is None, waits["plain"])


def test_rows_are_read_whatever_checked_says():
    """Some of a set's recordings may have lacked what the check reads; the rest have rows."""
    rows = SAMPLES["main_thread_waits"][0]
    rep = make([entry("main_thread_waits", rows, checked=False,
                      reason="The recording did not carry it (1 of the 3 recordings).")])
    plain.annotate(rep)
    det = rep["detectors"][0]
    check("the rows set the level", det["attention"] == "needs_attention", det["attention"])
    check("the sentence is there", det["plain"]["found"] == SAMPLES["main_thread_waits"][2],
          det["plain"])
    check("and the reason is only for a check that shows nothing", det["plain"]["reason"] is None)


def test_a_check_that_did_not_look_always_has_a_reason():
    gives = lambda det: plain.annotate(make([det]))["detectors"][0]["plain"]["reason"]  # noqa: E731
    check("its own, when it has one",
          gives(entry("io_wait", [], checked=False, reason="Because.")) == "Because.")
    check("that it could not run, when it says so and no more",
          gives(entry("io_wait", [], checked=False)) == "It could not run on these recordings.")
    check("that it failed, when there is an error",
          gives(entry("io_wait", [], checked=False, error="boom"))
          == "It failed to run on these recordings.")
    check("that it failed, in a report that has no `checked`",
          gives(old_entry("io_wait", [], error="boom")) == "It failed to run on these recordings.")


def test_not_checked_says_why_and_is_never_a_finding():
    rep = make([entry("io_wait", [], checked=False, reason="Record with the phone's state on."),
                entry("composables", [], error="boom")])
    plain.annotate(rep)
    io_wait, comp = rep["detectors"]
    check("the entry's own reason is passed on", io_wait["plain"]["reason"]
          == "Record with the phone's state on.", io_wait["plain"])
    check("a failure has a reason too", comp["plain"]["reason"] == "It failed to run on these recordings.",
          comp["plain"])
    check("neither has a sentence about what it found",
          io_wait["plain"]["found"] is None and comp["plain"]["found"] is None)


# --- the verdict ----------------------------------------------------------------

def verdict_of(duration, budget):
    return plain.verdict(make([], duration=duration, budget=budget))


@pytest.mark.parametrize("duration, budget, want_level, text", [
    (400.0, 500.0, "fine", "The launch takes 0.4 s. You allowed 0.5 s, so it is within the limit."),
    (500.0, 500.0, "fine", "The launch takes 0.5 s. You allowed 0.5 s, so it is within the limit."),
    (501.0, 500.0, "worth_a_look", "The launch takes 501 ms. You allowed 500 ms, so it is just over the limit."),
    (478.51, 460.0, "worth_a_look", "The launch takes 479 ms. You allowed 460 ms, so it runs 4% over."),
    (460.1, 460.0, "worth_a_look",
     "The launch takes 460.1 ms. You allowed 460.0 ms, so it is just over the limit."),
    (1219.0, 1240.0, "fine", "The launch takes 1.22 s. You allowed 1.24 s, so it is within the limit."),
    (560.0, 500.0, "worth_a_look", "The launch takes 0.6 s. You allowed 0.5 s, so it runs 12% over."),
    (625.0, 500.0, "worth_a_look", "The launch takes 0.6 s. You allowed 0.5 s, so it runs 25% over."),
    (626.0, 500.0, "needs_attention", "The launch takes 0.6 s. You allowed 0.5 s, so it runs 25% over."),
    (990.0, 500.0, "needs_attention", "The launch takes 1.0 s. You allowed 0.5 s, so it runs 98% over."),
    (1920.0, 500.0, "needs_attention",
     "The launch takes 1.9 s. You allowed 0.5 s, so it takes 3.8 times as long."),
    (1919.0, 520.0, "needs_attention",
     "The launch takes 1.9 s. You allowed 0.5 s, so it takes 3.7 times as long."),
    (625.2, 500.0, "worth_a_look", "The launch takes 0.6 s. You allowed 0.5 s, so it runs 25% over."),
])
def test_the_verdict_is_the_launch_against_the_budget(duration, budget, want_level, text):
    got = verdict_of(duration, budget)
    check("level", got["level"] == want_level, got)
    check("words", got["text"] == text, got["text"])
    check("budget and ratio are in it", got["budget_ms"] == budget
          and got["ratio"] == round(duration / budget, 3), got)


@pytest.mark.parametrize("a, b, want", [
    (478.51, 460.0, ("479 ms", "460 ms")), (400.0, 500.0, ("0.4 s", "0.5 s")),
    (500.0, 500.0, ("0.5 s", "0.5 s")), (1219.0, 1240.0, ("1.22 s", "1.24 s")),
    (995.0, 1004.0, ("0.99 s", "1.00 s")), (47.6, 48.4, ("47.6 ms", "48.4 ms")),
    (1200.1, 1200.4, ("1200.10 ms", "1200.40 ms"))])
def test_two_different_times_never_read_alike(a, b, want):
    check(f"{a} and {b}", plain.hum_apart(a, b) == want, plain.hum_apart(a, b))


def test_two_times_in_the_verdict_differ_when_the_launch_is_not_on_the_limit():
    """#709: 478.51 ms against 460 ms was "takes 0.5 s, you allowed 0.5 s, 4% over"."""
    for window in [w / 10 for w in range(4000, 6000, 7)]:
        for budget in (460.0, 500.0, 520.0):
            got = verdict_of(window, budget)
            took, said = re.match(r"The launch takes (.+?)\. You allowed (.+?), so", got["text"]).groups()
            check(f"{window} against {budget}: the two times", (took == said) == (window == budget),
                  got["text"])
            check(f"{window} against {budget}: within only when it is",
                  ("within the limit" in got["text"]) == (window <= budget), got["text"])


def test_the_ratio_in_the_sentence_is_the_key_rounded_to_one_decimal():
    """1919 ms against 520 ms is 3.7. The rounded 1.9 over the rounded 0.5 would say 3.8."""
    got = verdict_of(1919.0, 520.0)
    check("the key", got["ratio"] == 3.69, got)
    check("the sentence says that key to one decimal, and not the other quotient",
          "so it takes 3.7 times as long." in got["text"] and "3.8" not in got["text"], got["text"])
    check("beside the rounded numbers it shows",
          got["text"].startswith("The launch takes 1.9 s. You allowed 0.5 s, so"), got["text"])
    for window in range(900, 9000, 41):
        for budget in (300.0, 500.0, 520.0, 1234.0):
            got = verdict_of(float(window), budget)
            if got["ratio"] >= 2:
                check(f"{window} ms against {budget}", f"it takes {got['ratio']:.1f} times as long."
                      in got["text"], got)


@pytest.mark.parametrize("budget", [None, 0, 0.0, -5, True, "2500", "fast", float("nan"), float("inf")])
def test_with_no_budget_the_verdict_says_so_instead_of_calling_the_launch_fine(budget):
    got = verdict_of(1920.0, budget)
    check("no time limit, not fine and not 'not checked'", got["level"] == "no_limit", got)
    check("it still says how long the launch takes, and that nothing was compared",
          got["text"] == "The launch takes 1.9 s. No time limit is set, so there is nothing to "
                         "compare it with.", got["text"])
    check("nothing to report of the budget", got["budget_ms"] is None and got["ratio"] is None, got)


def test_a_report_with_no_window_has_nothing_to_judge():
    got = plain.verdict({"window": {}, "budget_ms": 500, "detectors": []})
    check("not checked", got["level"] == "not_checked", got)
    check("said so", got["text"] == "This recording has no measured run to judge.", got["text"])


# --- into the report ---------------------------------------------------------------

def known():
    """A launch of 1.9 s against 0.5 s: one red, two yellow, two fine, one blind, one off."""
    return make([
        entry("main_thread_waits", SAMPLES["main_thread_waits"][0]),
        entry("uninstrumented_cpu", SAMPLES["uninstrumented_cpu"][0]),
        entry("binder_txn", [row("Sync Worker", 41.0, count=15, max_ms=18.0, detail="background")]),
        entry("gc_pressure", []),
        entry("monitor_contention", []),
        entry("io_wait", [], checked=False,
              reason="These recordings do not carry the phone's state. Record with it on."),
    ], duration=1920.0, budget=500.0) | {"summary": {"absent_ids": ["frame_jank"]}}


KNOWN_VIEW = """\
NEEDS ATTENTION   The launch takes 1.9 s. You allowed 0.5 s, so it takes 3.8 times as long.

 !  The screen waits for background work
    The main thread waited 0.7 s for `worker_pool_#`, in all 3 launches. That is 36% of the launch.
    It was inside `loadConfig` at the time.
 ~  Work nobody can name
    2 threads or pools of threads spent 0.8 s of processor time between them on work that nothing in
    the recording names. The largest part is on `worker_pool_0`: 0.5 s, in all 3 launches.
 ~  Calls to the system or to other apps
    `Sync Worker`, a background thread, spent 41 ms on calls to the system or to other apps, in all
    3 launches. The longest took 18 ms.

 ok 2 checks are fine     -  1 could not be checked
    Waiting for the disk: These recordings do not carry the phone's state. Record with it on.

 1 more check is switched off in the config."""


def test_annotate_writes_the_reading_into_the_report():
    rep = plain.annotate(known())
    check("the verdict is at the top", rep["verdict"]["level"] == "needs_attention"
          and rep["verdict"]["ratio"] == 3.84, rep["verdict"])
    levels = {d["id"]: d["attention"] for d in rep["detectors"]}
    check("a level per detector", levels == {
        "main_thread_waits": "needs_attention", "uninstrumented_cpu": "worth_a_look",
        "binder_txn": "worth_a_look", "gc_pressure": "fine", "monitor_contention": "fine",
        "io_wait": "not_checked"}, levels)
    waits = rep["detectors"][0]["plain"]
    check("and the plain block", set(waits) == {"title", "why", "clean", "found", "reason"}, waits)
    check("it carries the three fixed texts", (waits["title"], waits["why"], waits["clean"])
          == plain.fixed_texts("main_thread_waits"), waits)
    check("the report keeps its own keys", rep["budget_ms"] == 500.0 and rep["scenario"] == "coldStart")
    json.dumps(rep)  # all of it is json


def test_a_template_that_fails_does_not_fail_the_analysis(monkeypatch, capsys):
    def broken(rows, ctx):
        raise KeyError("location")

    monkeypatch.setitem(plain.FOUND, "main_thread_waits", broken)
    rep = plain.annotate(known())
    waits = rep["detectors"][0]
    check("the generic sentence stands in", waits["plain"]["found"]
          == "1 result. The largest is `worker_pool_#`: 0.7 s, in all 3 launches.", waits["plain"])
    check("the level does not depend on the sentence", waits["attention"] == "needs_attention")
    check("and it is said on stderr, as a failed detector is",
          "[!] plain reading of main_thread_waits" in capsys.readouterr().err)


def test_a_row_with_a_number_that_is_no_number_counts_for_nothing():
    rep = make([entry("main_thread_block", [row("Screen.render", "n/a", self_ms="n/a")])])
    check("it is no weight, not a crash", plain.attention(rep["detectors"][0], rep) == "worth_a_look")


def test_annotating_twice_is_annotating_once():
    once = plain.annotate(known())
    twice = plain.annotate(copy.deepcopy(once))
    check("the same words again", once == twice)


def test_annotate_runs_again_after_something_else_marks_an_entry_not_checked():
    rep = plain.annotate(known())
    check("the disk wait was looked at", rep["detectors"][-1]["attention"] == "not_checked")
    rep["detectors"][3]["checked"] = False
    rep["detectors"][3]["reason"] = "The recordings carry no allocations."
    plain.annotate(rep)
    check("and now so is the clean-up", rep["detectors"][3]["attention"] == "not_checked"
          and rep["detectors"][3]["plain"]["reason"] == "The recordings carry no allocations.")


def test_the_terminal_view_of_a_known_report():
    check("the list", plain.render(known(), width=100) == KNOWN_VIEW, plain.render(known(), width=100))


def test_an_older_report_is_read_the_same_way_as_a_new_one():
    """No `plain`, `attention` or `verdict` in it: the same words, worked out."""
    check("same", plain.render(known(), width=100) == plain.render(plain.annotate(known()), width=100))


def test_a_report_is_read_as_it_says_when_it_carries_the_reading():
    rep = plain.annotate(known())
    rep["verdict"]["text"] = "Written by another version."
    check("its words, not worked out again",
          plain.render(rep, width=100).startswith("NEEDS ATTENTION   Written by another version."))


def test_a_report_from_before_the_key_says_why_once_for_all_the_silent_checks():
    rep = make([old_entry("io_wait", []), old_entry("gc_pressure", []),
                old_entry("main_thread_waits", SAMPLES["main_thread_waits"][0])], budget=500.0)
    text = " ".join(plain.render(rep, width=100).split())
    check("the count", "- 2 could not be checked" in text, text)
    check("the reason once, with the titles of both",
          text.count("This report was written before echolot recorded whether a check could run.") == 1
          and "Waiting for the disk, Memory clean-up: This report was written before" in text, text)
    check("and the finding is still there", "! The screen waits for background work" in text, text)


def test_the_terminal_view_wraps_to_the_width_it_is_given():
    text = plain.render(known(), width=60)
    check("no line is wider", all(len(line) <= 60 for line in text.split("\n")), text)
    check("and nothing is cut", "It was inside `loadConfig` at the time." in " ".join(text.split()), text)


def test_nothing_to_look_at_reads_as_a_verdict_and_a_count():
    rep = make([entry("gc_pressure", []), entry("anr", [])], duration=400.0, budget=500.0)
    check("fine, and the count", plain.render(rep, width=100) == (
        "FINE              The launch takes 0.4 s. You allowed 0.5 s, so it is within the limit.\n"
        "\n ok 2 checks are fine"), plain.render(rep, width=100))


def test_one_fine_check_is_singular():
    rep = make([entry("gc_pressure", [])], budget=None)
    check("is, not are", "ok 1 check is fine" in plain.render(rep, width=100))


# --- through the pipeline -------------------------------------------------------------

def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_build_carries_the_budget_and_aggregate_the_reading():
    def one(window_ms):
        """What `analyze_trace` hands over for one recording: rows have no `runs` yet."""
        waits = {"location": "worker_pool_#", "count": 259, "total_ms": 700.0, "max_ms": 15.2,
                 "detail": "blocked in loadConfig"}
        return report_mod.build(
            "t.perfetto-trace",
            {"process": MAIN, "pid": 1, "duration_ms": window_ms,
             "startup": {"type": "cold", "dur_ms": window_ms, "reasons": {}}},
            [entry("main_thread_waits", [waits])], scenario="coldStart", budget_ms=500.0)

    reports = [one(ms) for ms in (1900.0, 2000.0, 2100.0)]
    check("a report from build has the budget", reports[0]["budget_ms"] == 500.0)
    merged = report_mod.aggregate(reports)
    check("the merged report is about the median window", merged["verdict"]["text"]
          == "The launch takes 2.0 s. You allowed 0.5 s, so it takes 4.0 times as long.",
          merged["verdict"])
    check("the budget survives the merge", merged["budget_ms"] == 500.0)
    det = merged["detectors"][0]
    check("the level is worked out on the merged rows, seen in every recording",
          det["rows"][0]["runs"] == "3/3" and det["attention"] == "needs_attention", det)
    check("and its sentence says so", det["plain"]["found"].startswith(
        "The main thread waited 0.7 s for `worker_pool_#`, in all 3 launches. That is 35%"),
        det["plain"]["found"])
    single = one(1900.0)
    check("one recording is the report itself, read", report_mod.aggregate([single]) is single
          and "verdict" in single and "attention" in single["detectors"][0])


def test_no_budget_in_the_config_is_none_in_the_report():
    rep = report_mod.build("t.perfetto-trace", {"duration_ms": 900.0}, [])
    check("the key is there, empty", "budget_ms" in rep and rep["budget_ms"] is None, list(rep))


def test_the_command_prints_the_list(tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(known()), encoding="utf-8")
    code, out, err = run("report", str(path), "--plain")
    check("it prints and says nothing else", code == 0 and err == "", (code, err))
    check("the list", out == KNOWN_VIEW + "\n", out)


def test_the_command_takes_no_other_view_beside_it(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(known()), encoding="utf-8")
    code, _, err = run("report", str(path), "--plain", "--json", "--top", "3")
    check("refused", code == 2, (code, err))
    check("and says which", "--plain is a view of its own" in err and "--top, --json" in err, err)


# --- the budget in the config -----------------------------------------------------

def test_the_scenarios_budget_is_read_from_where_the_scenario_is_written():
    one = Config({"scenario": {"name": "run", "budget_ms": 2500}})
    check("a single scenario", one.scenario_budget_ms == 2500.0, one.scenario_budget_ms)
    check("a whole number is a float", isinstance(one.scenario_budget_ms, float))
    many = {"scenarios": {"a": {"start": {"name": "s"}, "end": {"name": "e"}, "budget_ms": 900},
                          "b": {"start": {"name": "s"}, "end": {"name": "e"}}}}
    cfg = Config(many)
    check("the first of several", cfg.scenario_budget_ms == 900.0)
    check("one that sets none is None", cfg.select("b").scenario_budget_ms is None)
    check("none at all is None", Config({}).scenario_budget_ms is None)
    check("`null` is none too", Config({"scenario": {"budget_ms": None}}).scenario_budget_ms is None)


@pytest.mark.parametrize("value", ["2500", "2.5s", 0, -1, True, [2500], float("nan"), float("inf")])
def test_a_budget_that_is_no_positive_number_is_refused_in_a_sentence(value):
    with pytest.raises(ConfigError) as err:
        _ = Config({"scenario": {"budget_ms": value}}).scenario_budget_ms
    check("it names the key and says what goes there",
          "scenario.budget_ms must be a number of milliseconds above zero" in str(err.value),
          str(err.value))


# --- the header keys ---------------------------------------------------------------

def test_the_three_keys_are_read_from_a_header_with_continuation_lines(tmp_path):
    path = tmp_path / "thing.sql"
    path.write_text(
        "-- @id: thing\n-- @title: Thing\n-- @why: for an agent\n-- @param: min_ms = 5\n"
        "-- @plain_title: A thing happened\n"
        "-- @plain_why: Something long enough\n--       to need a second line.\n"
        "-- @plain_clean: Nothing happened.\n--\n-- Prose about the query.\nSELECT 1 AS location;\n",
        encoding="utf-8")
    det = tp.Detector.from_file(path)
    check("title", det.plain_title == "A thing happened", det.plain_title)
    check("a value may run on", det.plain_why == "Something long enough to need a second line.",
          det.plain_why)
    check("the last one stops at the blank comment line", det.plain_clean == "Nothing happened.",
          det.plain_clean)
    check("nothing else moved", (det.id, det.title, det.why, det.params)
          == ("thing", "Thing", "for an agent", {"min_ms": 5}))


def test_a_detector_without_them_reads_empty_and_the_test_above_catches_it(tmp_path):
    path = tmp_path / "bare.sql"
    path.write_text("-- @id: bare\n-- @title: Bare\n\nSELECT 1 AS location;\n", encoding="utf-8")
    det = tp.Detector.from_file(path)
    check("empty", (det.plain_title, det.plain_why, det.plain_clean) == ("", "", ""))


def test_the_three_keys_come_after_the_last_key_and_before_the_prose():
    """Next to `@why` a change to either would meet the other; and a line of
    prose straight after the last key would be read as part of it."""
    for det in SHIPPED:
        lines = det.path.read_text(encoding="utf-8").split("\n")
        at = next(i for i, line in enumerate(lines) if line.startswith("-- @plain_title:"))
        # The last key's words may go on below it, a `@column`'s most often (#494).
        key = at - 1
        while re.match(r"--\s{2,}\S", lines[key]):
            key -= 1
        check(f"{det.id}: right after the last of the other keys",
              re.match(r"-- @(param|calibrate|identity|module|column):", lines[key]), lines[key])
        after = at + 1
        while lines[after].startswith("-- @plain_") or re.match(r"--\s{2,}\S", lines[after]):
            after += 1
        check(f"{det.id}: and a blank comment line ends them", lines[after] == "--", lines[after])


# --- a window that is not the scenario's (#557) -----------------------------------

def missed(budget=None, missed_="3/3", startup_ms=1258.0):
    rep = make([entry("main_thread_waits", [row("worker_pool_#", 700.0, detail="blocked in loadConfig")])],
               duration=18786.61, budget=budget)
    rep["window"]["startup"] = {"type": "cold", "dur_ms": startup_ms}
    rep["window"].update({"start_anchor": {"glob": "bindApplication", "matches": 1},
                          "end_anchor": {"glob": "app_marker", "matches": 0, "missed": missed_}})
    return rep


@pytest.mark.parametrize("budget", [None, 2000.0])
def test_a_window_whose_end_was_not_found_is_not_judged(budget):
    v = plain.verdict(missed(budget))
    check("not checked, with or without a limit", v["level"] == "not_checked" and v["ratio"] is None, v)
    check("no 'The launch takes'", "The launch takes" not in v["text"]
          and v["text"].startswith("There is no measured launch to judge."), v["text"])
    check("the platform's start is said as the platform's", "The platform measured the start at 1.3 s." in v["text"],
          v["text"])
    check("and the warning says what was not found, in how many recordings",
          v["warning"] == "echolot did not find where this scenario ends (`app_marker`) in 3 of 3 recordings, so "
                          "the numbers below are about everything the app did in the recording, not about the "
                          "scenario.", v["warning"])
    if budget:
        check("the limit is named as not applied", "not held against the time limit of 2.0 s" in v["text"], v["text"])


def test_an_end_missed_in_one_recording_of_three_is_said_as_that():
    v = plain.verdict(missed(missed_="1/3"))
    check("1 of 3, and those are in the numbers", v["warning"].startswith(
        "echolot did not find where this scenario ends (`app_marker`) in 1 of 3 recordings. In those")
        and v["level"] == "not_checked", v)


def test_shares_of_a_missed_window_are_of_the_recording():
    rep = missed()
    said = plain.found(rep["detectors"][0], rep)
    check("not 'of the launch'", "of the launch" not in said and "of the recording" in said, said)


def test_a_window_whose_anchors_matched_has_no_warning():
    rep = missed(budget=2000.0)
    rep["window"]["end_anchor"] = {"glob": "app_marker", "matches": 1}
    rep["window"]["duration_ms"] = 1900.0
    v = plain.verdict(rep)
    check("judged as before", v["warning"] is None and v["text"].startswith("The launch takes 1.9 s."), v)


def test_the_list_says_the_window_first():
    out = plain.render(missed())
    check("the warning is the first line", out.startswith("WINDOW            echolot did not find"), out)


def test_a_reading_from_before_the_warning_is_read_again():
    rep = plain.annotate(missed())
    del rep["verdict"]["warning"]
    rep["verdict"]["text"] = "The launch takes 18.8 s."
    check("is_read says no", not plain.is_read(rep))
    check("and the list works it out anew", "The launch takes" not in plain.render(rep), plain.render(rep))


# --- the small things of #556 ------------------------------------------------------

def test_a_check_whose_only_row_is_a_near_miss_is_a_marker_to_move():
    near = row("AGENTTMP_app_marker", 2442.48, runs="15/15", count=2,
               detail="near miss: entered from 2: (top level),load_state — on DefaultDispatch")
    check("its own level", level("repeated_work", [near], duration=5700.0) == "move_marker")
    real = row("loadConfig", 400.0, detail="entered from 2: A,B — on worker_pool_0")
    check("a near miss beside a real row is read as the real row",
          level("repeated_work", [near, real], duration=5700.0) == "worth_a_look")
    out = plain.render(make([entry("repeated_work", [near])], duration=5700.0))
    check("the list leaves it out of what needs a look", "AGENTTMP" not in out, out)


def _spread(totals: list[float], maxes: list[float]) -> dict:
    return {"total_ms": {"values": totals}, "max_ms": {"values": maxes}}


def test_the_longest_from_another_launch_says_so():
    """#712: merged, Total is the median and Max the longest any launch saw.
    Totals [10, 30] and longest [8, 25]: the 25 ms is one launch's, beside a
    median total of 20, and the sentence says so."""
    merged = row("Sync Worker", 20.0, runs="2/2", count=2, max_ms=25.0, detail="background",
                 spread=_spread([10.0, 30.0], [8.0, 25.0]))
    said = says("binder_txn", [merged])
    check("the longest is from one launch", "The longest took 25 ms in one launch." in said,
          said)
    same = row("Sync Worker", 20.0, runs="2/2", count=2, max_ms=8.0, detail="background",
               spread=_spread([10.0, 30.0], [8.0, 8.0]))
    check("where every launch's longest is the same, nothing to say",
          "in one launch" not in says("binder_txn", [same]), says("binder_txn", [same]))
    single = row("Sync Worker", 20.0, runs=None, count=2, max_ms=8.0, detail="background")
    check("one recording, nothing to say", "in one launch" not in says("binder_txn", [single]))


def test_one_occurrence_a_launch_reads_its_median_then_the_longest():
    """One `activityStart` a launch: 65.86 ms the median, 99.59 ms in one."""
    merged = row("activityStart", 65.86, runs="5/5", count=1, max_ms=99.59,
                 detail="median 1.0 ms of 40 · worst 99.6×",
                 spread=_spread([30.0, 50.0, 65.86, 70.0, 99.59],
                                [30.0, 50.0, 65.86, 70.0, 99.59]))
    said = says("main_thread_outlier", [merged])
    check("the one occurrence at its median, the longest apart",
          "took 66 ms, in all 5 launches. In one launch it took 100 ms." in said, said)
    late = says("frame_jank", [row("App Deadline Missed", 300.0, runs="2/2", count=2,
                                   max_ms=400.0, detail="Self Jank · 2 of 12 frames",
                                   spread=_spread([200.0, 400.0], [150.0, 400.0]))])
    check("frames: the worst in one launch", "0.4 s at worst in one launch" in late, late)


def test_a_merged_table_heads_max_as_any_runs():
    """#712: in a report of several launches, a row with one call a launch
    read "Total 65.86, Max 99.59"; the header says the two are different
    launches'."""
    merged = report_mod._table([row("activityStart", 65.86, runs="5/5", max_ms=99.59)])
    check("merged: Max of any run", "Max of any run, ms" in merged, merged)
    one = report_mod._table([{"location": "activityStart", "count": 1, "total_ms": 65.86,
                              "max_ms": 65.86}])
    check("one recording: Max", "| Max, ms |" in one and "any run" not in one, one)
