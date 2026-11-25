#!/usr/bin/env python3
"""Frames the app presents on a surface the frame timeline does not hold (#1011).

`frame_jank` reads the platform's frame timeline, which keeps the frames that
carry a Choreographer vsync id: those of the app's windows. A map, a game or a
video drawn by Vulkan or GL into a `SurfaceView` from a thread of the app's
own has none there. In a pan-and-zoom of a map, 1000 presents reached the
screen that way while the timeline held 321 frames of the window, and the
report said "Hardly any frame came late" of a map it never looked at.

Now the window lists such surfaces with what they presented, the thread and
the call, and every view of the report says that `frame_jank` did not judge
them.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, plain, selftest  # noqa: E402
from echolot import analyze as analyze_mod  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.pages import report_page  # noqa: E402
from tests.support import check  # noqa: E402

LISTED = {"surface": fixture.SURFACE, "thread": "Thread-7", "call": "QueuePresentKHR",
          "presents": 60, "judged": 0}


def _report(tmp_path: Path, surface: str | None) -> dict:
    trace = tmp_path / "s.perfetto-trace"
    trace.write_bytes(fixture.build(surface=surface))
    return analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))


def _jank(report: dict) -> dict:
    return next(d for d in report["detectors"] if d["id"] == "frame_jank")


def test_the_surface_is_listed_with_its_presents_thread_and_call(tmp_path: Path) -> None:
    frames = _report(tmp_path, "unjudged")["window"]["frames"]
    check("the timeline's frames are counted as before", frames["count"] == 24, frames)
    check("the surface: the 60 presents of the window, not the 3 after it, nor the "
          "window's own queue", frames.get("unjudged") == [LISTED], frames)


def test_a_surface_whose_frames_the_timeline_holds_is_not_listed(tmp_path: Path) -> None:
    frames = _report(tmp_path, "judged")["window"]["frames"]
    check("40 of its 60 in the timeline: judged", "unjudged" not in frames, frames)
    check("and counted with the window's", frames["count"] == 64, frames)


def test_every_view_says_what_frame_jank_did_not_judge(tmp_path: Path) -> None:
    got = _report(tmp_path, "unjudged")
    said = (f"`frame_jank` judged the 24 frames the frame timeline holds of the app. The "
            f"app also presented 60 frames on `{fixture.SURFACE}` with `QueuePresentKHR` "
            f"on `Thread-7`, and the timeline holds none of those")
    for name, text in (("report.md", report_mod.to_markdown(got)),
                       ("overview", render_mod.overview(got)),
                       ("--window", render_mod.window_view(got)),
                       ("--detector frame_jank", render_mod.detector_view(got, "frame_jank"))):
        check(f"said in {name}", said in text, text[:1500])
    check("not in another detector's view", said not in render_mod.detector_view(got, "anr"))
    also = _jank(plain.annotate(got))["plain"].get("also") or ""
    check("the plain reading says it too",
          also.startswith("This checked the 24 frames of the app's windows. The app drew 60 "
                          "more on a surface of its own"), also)


def test_a_fine_check_says_it_beside_its_clean_sentence(tmp_path: Path) -> None:
    got = _report(tmp_path, "unjudged")
    _jank(got)["rows"] = []
    det = _jank(plain.annotate(got))
    check("fine: the timeline's frames were on time", det["attention"] == plain.FINE,
          det["attention"])
    shown = next(c for c in report_page._checks(got) if c["id"] == "frame_jank")["said"]
    check("the page does not stop at fine",
          shown == f"{det['plain']['clean']} {det['plain']['also']}", shown)
    lines = plain.render(got, width=100)
    check("nor does report --plain", " ok Frames that came late" in lines, lines)


def test_repeats_keep_each_runs_presents(tmp_path: Path) -> None:
    one = _report(tmp_path, "unjudged")
    runs = [copy.deepcopy(one) for _ in range(3)]
    runs[1]["window"]["frames"]["unjudged"][0].update(presents=64, thread="Thread-8")
    del runs[2]["window"]["frames"]["unjudged"]
    listed = report_mod.aggregate(runs)["window"]["frames"]["unjudged"]
    check("the median, each run's, none where it was not listed, the first run's thread",
          listed == [{**LISTED, "values": [60, 64, 0], "runs": "2/3"}], listed)


class _Tp:
    """A trace_processor that answers by the start of the query."""

    def __init__(self, **answers):
        self.answers = answers

    def query(self, sql: str) -> list[dict]:
        for key, rows in self.answers.items():
            if key in sql:
                return rows
        raise AssertionError(sql)


def test_the_thread_that_presented_the_most_names_the_surface() -> None:
    queued = [
        {"queue": f"{fixture.SURFACE}#2", "thread": "m.example.app", "tid": 1, "n": 2, "last": 5},
        {"queue": f"{fixture.SURFACE}#2", "thread": None, "tid": 77, "n": 40, "last": 9},
        {"queue": "ViewRootImpl[MainActivity]#0", "thread": "RenderThread", "tid": 3, "n": 30,
         "last": 7},
    ]
    tp = _Tp(onFrameAvailable=queued, actual_frame_timeline_slice=[{"n": 0}],
             ancestor_slice=[{"name": "queueBuffer"}, {"name": "queueBuffer"}])
    got = analyze_mod._unjudged(tp, 0, 10)
    check("one surface, all 42 presents, named by the thread of 40 of them, by tid when "
          "it has no name; a window's queue of Android 12 is not listed",
          got == [{"surface": fixture.SURFACE, "thread": "tid 77", "call": "queueBuffer",
                   "presents": 42, "judged": 0}], got)


def test_the_call_is_what_holds_the_outermost_queue_buffer() -> None:
    def call(*names: str) -> str:
        return analyze_mod._present_call(
            _Tp(ancestor_slice=[{"name": n} for n in names]), 1)
    check("Vulkan inside the app's own slice",
          call("RenderFrame", "QueuePresentKHR", "queueBuffer", "queueBuffer")
          == "QueuePresentKHR")
    check("GL on a RenderThread", call("DrawFrames 1", "eglSwapBuffersWithDamageKHR",
                                       "queueBuffer") == "eglSwapBuffersWithDamageKHR")
    check("no queueBuffer above it: its parent", call("lockCanvas") == "lockCanvas")
    check("nothing around the queueing", call("queueBuffer") == "queueBuffer")
