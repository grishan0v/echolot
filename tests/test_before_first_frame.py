#!/usr/bin/env python3
"""A window that closes before the app's first frame (#849).

`bindApplication` to `activityStart` is the app's start up to its first
Activity, and the phase `app_init` reads: nothing is drawn in it, because the
app has drawn nothing yet. `analyze` said of every repeat that the app drew no
frame and the scenario did not reach its screen, and `collect` stopped its
round on the same reading at the first repeat, so such a scenario could never
be recorded with more than one. The frame count was taken inside the window
alone (#536).

A window with no frame in it is now said only when the app had drawn before
it closed, or never drew: a frame there was none of yet says nothing. The
#536 case, a list behind an error screen, is a window opened after the app's
first frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import report as report_mod  # noqa: E402
from echolot.analyze import _drawn_info, analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _scenario_ran  # noqa: E402
from echolot.selftest import (  # noqa: E402
    BEFORE_FIRST_FRAME, BIND_TO_ACTIVITY, main_thread_trace)
from tests.support import check  # noqa: E402

LIST = {**BIND_TO_ACTIVITY, "scenario": {"name": "list", "start": {"name": "list_open"},
                                         "end": {"name": "list_shown"}}}


def test_a_window_that_closes_before_the_first_frame_says_nothing(tmp_path: Path) -> None:
    trace = main_thread_trace(tmp_path / "bind_iter000.perfetto-trace", BEFORE_FIRST_FRAME)
    rep = analyze_trace(trace, Config(BIND_TO_ACTIVITY))
    check("no count to read: nothing was drawn yet", rep["window"]["drawn"] is None,
          rep["window"])
    check("and no warning", "drew no frame" not in report_mod.to_markdown(rep))
    merged = report_mod.aggregate([rep, analyze_trace(trace, Config(BIND_TO_ACTIVITY))])
    check("over repeats neither", "drew no frame" not in report_mod.to_markdown(merged)
          and merged["window"].get("drawn") is None, merged["window"].get("drawn"))
    check("collect's first look lets the round go on",
          _scenario_ran(Config(BIND_TO_ACTIVITY), trace) == (None, None))


def test_a_window_opened_after_the_first_frame_that_drew_nothing_still_stops(
        tmp_path: Path) -> None:
    """#536: the app drew its screen, and then nothing while the window ran."""
    trace = main_thread_trace(tmp_path / "list_iter000.perfetto-trace", [
        *BEFORE_FIRST_FRAME, ("list_open", 400, 1), ("list_shown", 700, 1)])
    rep = analyze_trace(trace, Config(LIST))
    check("none drawn", rep["window"]["drawn"] == {"count": 0}, rep["window"])
    check("said", "The app drew no frame in its recording" in report_mod.to_markdown(rep))
    stop, _ = _scenario_ran(Config(LIST), trace)
    check("and collect stops", stop == "com.example.app drew no frame in the scenario's window",
          stop)


class _Tp:
    def __init__(self, row: dict):
        self.row = row

    def query(self, sql: str) -> list[dict]:
        return [self.row]


def test_what_the_count_reads() -> None:
    window = {"ts_start": 0, "ts_end": 10}
    check("drawn in the window", _drawn_info(_Tp({"seen": 9, "n": 3, "first": 5, "born": 1}),
                                             window) == {"count": 3})
    check("born in the trace, first frame after the window: nothing to say",
          _drawn_info(_Tp({"seen": 9, "n": 0, "first": 10, "born": 1}), window) is None)
    check("first frame before the window closed: none drawn in it",
          _drawn_info(_Tp({"seen": 9, "n": 0, "first": 4, "born": 1}), window)
          == {"count": 0})
    check("a process there before the trace began may have drawn before it: none drawn",
          _drawn_info(_Tp({"seen": 9, "n": 0, "first": 20, "born": None}), window)
          == {"count": 0})
    check("born, and never drew: none drawn",
          _drawn_info(_Tp({"seen": 9, "n": 0, "first": None, "born": 1}), window)
          == {"count": 0})
