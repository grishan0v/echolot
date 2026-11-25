#!/usr/bin/env python3
"""What a report says about a window that is the launch (#446).

The line that says what bounds it, the share of it before the process
started, which is no hole in the recording, how repeats merge, and the
commands that print it. The window itself is in `test_launch_window.py`.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_launch_window import (  # noqa: E402
    APP, LAUNCH, MS, _fixture, _startup, _Startups, _two_launches)
from tests.test_report_page import read  # noqa: E402

from echolot import fixture, recorder, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.analyze import _startup_info, analyze_trace  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import report_page  # noqa: E402


def _cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --- the budget: the stretch before the process is no hole -------------------------

BUDGET = {"on_cpu": 400.0, "waiting_for_cpu": 50.0, "in_kernel": 20.0, "sleeping": 60.0,
          "other": 0.0, "accounted_ms": 530.0, "window_ms": 616.0, "accounted_pct": 86.0}


def test_the_launch_delay_is_a_share_of_the_window_and_not_a_warning() -> None:
    plain = render_mod._budget_lines(dict(BUDGET))
    check("86% accounted with nothing to explain it warns, as it always did",
          len(plain) == 2 and plain[1].startswith("> ⚠️ Only 86% of the window")
          and "the process started inside the window" in plain[1], plain)
    lines = render_mod._budget_lines({**BUDGET, "before_process_ms": 80.0})
    check("with the 80 ms before the process started, one line and no warning",
          lines == ["Main thread: 65% on a CPU · 8% waiting for a CPU · 3% blocked in the "
                    "kernel · 10% sleeping · 13% before the process started"], lines)


def test_a_hole_beyond_the_launch_delay_still_warns() -> None:
    lines = render_mod._budget_lines({**BUDGET, "accounted_ms": 430.0, "accounted_pct": 69.8,
                                      "on_cpu": 300.0, "before_process_ms": 80.0})
    check("70% and 13% leave 17% with no state: the recording has a hole",
          lines[-1].startswith("> ⚠️ Only 83% of the window is accounted for on the main "
                               "thread, the 80 ms before its process started included")
          and "the recording has a hole there" in lines[-1], lines)
    warm = render_mod._budget_lines({**BUDGET, "before_process_ms": 0.0})
    check("a process that was already there has no such share, and the old warning stands",
          warm[-1].startswith("> ⚠️ Only 86% of the window")
          and "before the process started" not in warm[0], warm)


# --- the text ------------------------------------------------------------------------

def test_the_report_says_what_bounds_a_launch_window(tmp_path: Path) -> None:
    said = {kind: report_mod.to_markdown(_fixture(tmp_path, fully_drawn=kind))
            for kind in (None, "app", "system_server", "early")}
    head = "Window: the launch as the platform records it, from the intent to "
    check("closed at the launch's end, with no call in the trace",
          head + "the launch's end, its first frame. The trace holds no `reportFullyDrawn` "
          "call after the launch began." in said[None], said[None][:600])
    check("closed on the app's call", head + "the end of the app's `reportFullyDrawn` call, "
          "which came after the first frame." in said["app"], said["app"][:600])
    check("closed on system_server's record of it", head + "`reportFullyDrawn` as system_server "
          "records it. The app's own slice of that call is not in the trace."
          in said["system_server"], said["system_server"][:600])
    check("a call before the first frame", "was called before that frame, so it closes nothing."
          in said["early"], said["early"][:600])
    check("and the startup line sets the launch against the window it built",
          "the startup began with the window, and ended 55 ms before it closed."
          in said["app"], said["app"][:900])
    view = render_mod.window_view(_fixture(tmp_path, fully_drawn="app"))
    check("`report --window` says it where it names anchors for the others",
          head in view and "Anchors:" not in view, view)


def test_a_quiet_launch_report_does_not_send_the_reader_to_anchors() -> None:
    quiet = {"trace": "t", "window": {"duration_ms": 600.0, "defined_by": "launch",
                                      "report_fully_drawn": None},
             "summary": {"detectors_run": 1, "detectors_fired": 0, "fired_ids": [],
                         "absent_ids": []},
             "detectors": [{"id": "anr", "title": "ANR", "rows": [], "error": None}]}
    text = report_mod.to_markdown(quiet)
    check("the process name is what could be wrong", "check that the process name matches the "
          "trace (`echolot probe`)" in text, text)
    anchored = report_mod.to_markdown({**quiet, "window": {"duration_ms": 600.0}})
    check("for anchors the sentence is what it was", "check that the process name and the "
          "scenario anchors match the trace (`echolot probe`)" in anchored, anchored)



def test_the_startup_line_says_it_is_the_launch_the_window_was_built_from() -> None:
    window = {"ts_start": 100 * MS, "ts_end": 1100 * MS}
    built = _startup_info(_Startups([_startup(1, 100, 400), _startup(2, 450, 1000)]),
                          window, APP, {"startup_id": 1})
    line = render_mod._startup_lines({**built, "reasons": {}}, True)[0]
    check("one of the two in the trace, and which",
          "(one of 2 in the trace: the launch the window is built from)" in line, line)
    by_overlap = render_mod._startup_lines({**built, "reasons": {}})[0]
    check("where an anchored window's says it was picked by overlap",
          "that shares the most with the window" in by_overlap, by_overlap)


# --- repeats -----------------------------------------------------------------------

def _report(defined: str | None, source: str | None = None, ms: float = 600.0) -> dict:
    window: dict = {"duration_ms": ms, "process": APP}
    if defined:
        window.update({"defined_by": defined, "report_fully_drawn": source})
    return {"trace": f"{defined}.perfetto-trace", "window": window, "detectors": [],
            "summary": {"absent_ids": []}}


def test_repeats_that_closed_the_same_way_merge_to_that_way() -> None:
    merged = report_mod.aggregate([_report("launch+reportFullyDrawn", "app")] * 3)["window"]
    check("one definition, and no count of several",
          merged["defined_by"] == "launch+reportFullyDrawn" and "defined_by_runs" not in merged,
          merged)
    check("read from the app in every one", merged["report_fully_drawn"] == "app", merged)
    anchored = report_mod.aggregate([_report(None)] * 2)["window"]
    check("repeats of an anchored scenario carry none of it", "defined_by" not in anchored
          and "report_fully_drawn" not in anchored, anchored)


def test_repeats_that_closed_differently_are_named_and_warned_about() -> None:
    reports = [_report("launch+reportFullyDrawn", "app")] * 4 + [_report("launch")]
    merged = report_mod.aggregate(reports)
    w = merged["window"]
    check("both definitions, joined, and how many repeats each",
          (w["defined_by"], w["defined_by_runs"]) == (
              "launch and launch+reportFullyDrawn",
              {"launch": "1/5", "launch+reportFullyDrawn": "4/5"}), w)
    check("the call's source is the least any repeat had: none",
          w["report_fully_drawn"] is None, w)
    text = report_mod.to_markdown(merged)
    check("the report warns, counting each",
          "> ⚠️ The launch window did not close the same way in every repeat: on "
          "`reportFullyDrawn` in 4 and at the launch's end in 1 of 5." in text, text)
    mixed_source = report_mod.aggregate([_report("launch+reportFullyDrawn", "app"),
                                         _report("launch+reportFullyDrawn", "system_server")])
    check("a call read from system_server in one repeat is what the set says",
          mixed_source["window"]["report_fully_drawn"] == "system_server"
          and "defined_by_runs" not in mixed_source["window"], mixed_source["window"])


def test_the_before_process_share_is_merged_by_median_like_the_buckets() -> None:
    def one(late: float) -> dict:
        rep = _report("launch")
        rep["window"]["main_thread"] = {**BUDGET, "before_process_ms": late}
        return rep
    merged = report_mod._merge_budget([one(60.0), one(80.0), one(100.0)])
    check("the median of the three", merged["before_process_ms"] == 80.0, merged)
    check("and it is not one of the thread's states",
          merged["accounted_ms"] == 530.0, merged)
    plain = report_mod._merge_budget([{"window": {"main_thread": dict(BUDGET)}}] * 2)
    check("an anchored set has no such key", "before_process_ms" not in plain, plain)


def test_a_launch_report_reads_the_same_merged_or_single(tmp_path: Path) -> None:
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        one = _fixture(tmp_path, fully_drawn="app")
    merged = report_mod.aggregate([one, one])
    check("two repeats of one trace keep the window's facts",
          (merged["window"]["defined_by"], merged["window"]["report_fully_drawn"],
           merged["window"]["main_thread"]["before_process_ms"])
          == ("launch+reportFullyDrawn", "app", 10.0), merged["window"])


# --- the other commands that cut a window ---------------------------------------------

def test_calibrate_and_analyze_take_the_launch_window_from_the_command_line(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "echolot.yml").write_text(yaml.safe_dump(LAUNCH), encoding="utf-8")
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(fixture.build(fully_drawn="app"))
    code, out, err = _cli("calibrate", str(trace))
    check("calibrate measures the same window", code == 0 and "Scenario window: 1120 ms" in out,
          (out[:300], err[-300:]))
    code, out, err = _cli("analyze", str(trace))
    check("analyze prints what bounds it", code == 0 and "Window: the launch as the platform "
          "records it" in out, (out[:500], err[-300:]))
    code, out, _ = _cli("report", "--window")
    check("and `report --window` reads it back", code == 0 and "Window: the launch" in out, out)
    none = _two_launches(tmp_path / "none.perfetto-trace", failed_only=True)
    code, _, err = _cli("analyze", str(none))
    check("a trace with no launch: exit 2 and a sentence, no traceback", code == 2
          and "error: no launch of com.example.app in trace none.perfetto-trace" in err
          and "Traceback" not in err, err[-500:])
    code, _, err = _cli("calibrate", str(none))
    check("the same from calibrate", code == 2 and "no launch of com.example.app" in err
          and "Traceback" not in err, err[-500:])


# --- the plain reading of it (#442) -----------------------------------------------------

def _plain(tmp_path: Path, config: dict, **build) -> dict:
    """The fixture's report with its plain reading in it, as `analyze` writes it."""
    with contextlib.redirect_stderr(io.StringIO()):
        return report_mod.aggregate([_fixture(tmp_path, config, **build)])


def _with_budget(config: dict, ms: float) -> dict:
    return {**config, "scenario": {**config["scenario"], "budget_ms": ms}}


def test_the_plain_reading_calls_a_launch_window_a_launch(tmp_path: Path) -> None:
    rep = _plain(tmp_path, _with_budget(LAUNCH, 1000), fully_drawn="app")
    check("the verdict is the window against the limit, in the launch's words",
          rep["verdict"]["text"] == "The launch takes 1.1 s. You allowed 1.0 s, so it runs "
                                    "12% over.", rep["verdict"])
    check("the window being the 1120.4 ms from the intent to the app's call",
          (rep["window"]["duration_ms"], rep["verdict"]["ratio"]) == (1120.4, 1.12), rep["verdict"])
    said = [d["plain"]["found"] for d in rep["detectors"] if d["plain"]["found"]]
    check("every sentence that names a share names it of the launch",
          sum("of the launch" in s for s in said) >= 8
          and not [s for s in said if "of the run" in s or "the whole run" in s], said)
    plain_end = _plain(tmp_path, _with_budget(LAUNCH, 1000))
    check("a launch that closed at its end reads the same way",
          plain_end["verdict"]["text"] == "The launch takes 1.1 s. You allowed 1.0 s, so it "
                                          "runs 6% over.", plain_end["verdict"])


def test_the_plain_readings_shares_are_of_the_launch_window(tmp_path: Path) -> None:
    """The same 0.2 s of late frames and the same 0.2 s of work, against three
    windows of one trace: the anchored 1005 ms, the launch's 1065, and the
    1120.4 that runs on to the app's call. The stretch before the process
    started and the one after the first frame are part of what is divided."""
    found = {}
    for name, config, build in (("anchors", selftest.FIXTURE_CONFIG, {}),
                                ("launch", LAUNCH, {}),
                                ("call", LAUNCH, {"fully_drawn": "app"})):
        rep = _plain(tmp_path, config, **build)
        by_id = {d["id"]: d for d in rep["detectors"]}
        found[name] = (by_id["frame_jank"]["plain"]["found"],
                       by_id["main_thread_block"]["plain"]["found"],
                       by_id["frame_jank"]["attention"], by_id["main_thread_block"]["attention"])
    check("late frames: 21%, 20% and 19% of the launch",
          ["Together that is 21% of the launch" in found["anchors"][0],
           "Together that is 20% of the launch" in found["launch"][0],
           "Together that is 19% of the launch" in found["call"][0]] == [True] * 3, found)
    check("the heaviest piece of work: 17%, then 16% and 16%",
          ["That is 17% of the launch" in found["anchors"][1],
           "That is 16% of the launch" in found["launch"][1],
           "That is 16% of the launch" in found["call"][1]] == [True] * 3, found)
    check("and both still need attention: over 15% and over 0.15 s in each window",
          all(levels == ("needs_attention", "needs_attention")
              for levels in (f[2:] for f in found.values())), found)


# --- the page (#443): its chart of the main thread adds up to a launch window ---------------

CHART = "Where the main thread was during the window"


def _main_thread_chart(report: dict) -> tuple[list, str]:
    """The rows of the page's main-thread chart, and the page itself."""
    page = report_page.render(report)
    fold = [d for d in read(page).all("details")
            if d.one("summary").text == "The full technical report"][0]
    chart = [c for c in fold.all("div", "chart") if c.one("p", "chart-h").text == CHART][0]
    return [r for r in chart.all("div", "cr") if "ax" not in r.attrs["class"].split()], page


def _tip_ms(row) -> float:
    """A row's exact milliseconds, from its tooltip."""
    return float(row.attrs["data-tip"].split("\n")[1].removesuffix(" ms").replace(",", ""))


def test_the_pages_chart_of_a_launch_window_adds_up_to_the_window(tmp_path: Path) -> None:
    """A process started 30 ms into a launch of 200: the main thread has a
    state for 170 of them. With only the states drawn, the chart stopped at
    85% of the window."""
    with contextlib.redirect_stderr(io.StringIO()):
        rep = report_mod.aggregate([analyze_trace(
            _two_launches(tmp_path / "two.perfetto-trace"), Config(LAUNCH))])
    rows, page = _main_thread_chart(rep)
    names = [r.one("div", "cr-n").all("span")[0].text for r in rows]
    check("the thread's states, then the stretch before its process, in report.md's words",
          names == ["on a CPU", "before the process started"]
          and "15% before the process started" in report_mod.to_markdown(rep), names)
    check("the bars add up to the window", sum(_tip_ms(r) for r in rows)
          == rep["window"]["duration_ms"] == 200.0, [r.attrs["data-tip"] for r in rows])
    late = rows[-1]
    check("with its own figures", late.attrs["data-tip"].split("\n")
          == ["before the process started", "30.00 ms", "15.0% of 200.00 ms"]
          and late.one("div", "cr-v").text == "30 ms15% of the window", late.attrs["data-tip"])
    check("it says that it is no state of the main thread",
          late.one("span", "cr-s").text == "no state of the main thread: its process did not "
                                           "exist yet", late.one("div", "cr-n").text)
    styles = [r.one("i", "bar").attrs["style"] for r in rows]
    check("and is drawn in the chart's quiet neutral, the states in the data colour",
          styles == ["width:85.00%", "width:15.00%;background:var(--data-soft)"]
          and page.count("background:var(--data-soft)") == 1, styles)


def test_a_stretch_under_a_percent_is_drawn_in_neither_the_chart_nor_the_line(
        tmp_path: Path) -> None:
    rep = _plain(tmp_path, LAUNCH, fully_drawn="app")
    rows, page = _main_thread_chart(rep)
    check("the fixture's 10 ms of 1120: under a percent, as in the budget line",
          rep["window"]["main_thread"]["before_process_ms"] == 10.0
          and "before the process started" not in page
          and len(rows) == 3, [r.one("div", "cr-n").text for r in rows])


def test_the_page_of_an_anchored_report_has_no_such_bar(tmp_path: Path) -> None:
    """An anchored window carries no `before_process_ms`, and its page is
    what it was: the same rows, and no bar in the soft colour. (Held byte for
    byte against the page before this bar by hand; here, by its parts.)"""
    rep = _plain(tmp_path, selftest.FIXTURE_CONFIG)
    rows, page = _main_thread_chart(rep)
    check("the three states the fixture's main thread was in, as before",
          [r.one("div", "cr-n").text for r in rows]
          == ["on a CPU", "blocked in the kernel", "sleeping"],
          [r.one("div", "cr-n").text for r in rows])
    check("each bar a width and nothing else",
          all(re.fullmatch(r"width:[\d.]+%", r.one("i", "bar").attrs["style"]) for r in rows),
          [r.one("i", "bar").attrs["style"] for r in rows])
    check("and the stretch is nowhere on the page", "before the process started" not in page
          and "background:var(--data-soft)" not in page)
    # A report by hand whose budget names the stretch as nothing draws none.
    bare = {"detectors": [], "window": {"duration_ms": 100.0, "main_thread": {
        "on_cpu": 100.0, "window_ms": 100.0, "before_process_ms": None}}}
    check("nor does a budget that holds the key with nothing in it",
          "before the process started" not in report_page.render(bare))
