"""A start the app hands over after its first frame (#1008).

A splash that draws one frame, then starts the app's main activity by itself
and finishes, is one launch for a person and two for the platform: the second
`completed-same-process`. `window: launch` closed on the first. On an
open-source app that was 181 ms of the 690 a person waited for the map, with
181 ms of the app's native start on the main thread after the splash's frame,
outside the window, and the page called the work nobody names fine.

The window runs on through each launch the app went on to by itself: of the
same app, begun after the one before ended, with nothing touched between, and
with the screen before finished. It says where the first frame was, in the
report, in collect's first look and on the page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, plain  # noqa: E402
from echolot import launch as launch_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _scenario_ran  # noqa: E402
from echolot.render import overview, to_markdown  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = {"project": {"package": fixture.APP_NAME, "process": fixture.APP_NAME},
          "scenario": {"name": "startup", "window": "launch"}}


def _trace(tmp_path: Path, **build) -> Path:
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(fixture.handover(**build))
    return trace


def _bounds(rep: dict) -> tuple[float, float]:
    w = rep["window"]
    return ((w["ts_start"] - fixture.BASE_NS) / 1e6, (w["ts_end"] - fixture.BASE_NS) / 1e6)


@pytest.fixture(scope="module")
def handed(tmp_path_factory) -> tuple[dict, Path]:
    trace = _trace(tmp_path_factory.mktemp("handover"))
    return analyze_trace(str(trace), Config(CONFIG)), trace


def test_the_window_runs_to_the_end_of_the_launch_the_app_went_on_to(handed):
    rep, _ = handed
    w = rep["window"]
    check("from the intent to the main activity's first frame",
          _bounds(rep) == (fixture.HANDOVER_LAUNCH_MS[0], fixture.HANDOVER_NEXT_MS[1]),
          _bounds(rep))
    check("closed by the hand-over", w["defined_by"] == launch_mod.HANDOVER, w["defined_by"])
    check("which says where the first frame was, and who went on to whom",
          w["handover"] == {"from": fixture.HANDOVER_SPLASH, "to": fixture.HANDOVER_MAIN,
                            "first_frame_ms": 260.0, "after_ms": 255.0, "launches": 1},
          w["handover"])
    check("the launch ended on the main activity", w["activity"] == fixture.HANDOVER_MAIN)


def test_the_app_s_start_after_the_splash_s_frame_is_named_as_nobody_s(handed):
    rep, _ = handed
    blind = next(d for d in rep["detectors"] if d["id"] == "uninstrumented_cpu")
    init = fixture.HANDOVER_INIT_MS[1] - fixture.HANDOVER_INIT_MS[0]
    check("the main thread's 180 ms outside every slice is a row",
          [(r["location"], r["total_ms"]) for r in blind["rows"]] == [("main", init)],
          blind["rows"])


def test_the_report_and_the_overview_say_it(handed):
    rep, _ = handed
    said = (f"`{fixture.HANDOVER_SPLASH}` drew the launch's first frame at 260 ms. 255 ms "
            f"later, with nothing touched, the app went on by itself to "
            f"`{fixture.HANDOVER_MAIN}` and finished the one before: the start was not "
            f"over at that frame, so the window runs on.")
    text = to_markdown(rep)
    check("in the window's line", said in text, text[:1500])
    check("which says what closed it",
          "from the intent to the end of the launch the app handed its start over to" in text)
    check("and in the overview", said in overview(rep))


def test_the_page_says_the_first_frame_beside_the_launch(handed):
    rep, _ = handed
    text = plain.verdict(rep)["text"]
    check("the launch's length, and the splash's frame said apart from it",
          text.startswith("The launch takes 0.9 s. It runs past SplashActivity's first frame "
                          "at 0.3 s: the app went on to MainActivity by itself, and the start "
                          "was not over."), text)


def test_collect_s_first_look_says_it(handed):
    _, trace = handed
    stop, note = _scenario_ran(Config(CONFIG), trace, argparse.Namespace(tp_binary=None))
    check("the round goes on", stop is None, stop)
    check("and repeat 1 says it handed over, and where the window ends",
          note == "repeat 1 handed its start over: `SplashActivity` drew the launch's first "
                  "frame at 260 ms, and 255 ms later the app went on by itself to "
                  "`MainActivity`. `window: launch` runs through that launch, 860 ms in all, "
                  "and analyze says so", note)


@pytest.mark.parametrize("build, why", [
    ({"touched": True}, "a tap between the two launches: the second is a person's doing"),
    ({"kept": True}, "the first screen stays beneath the second: the start was over"),
])
def test_a_launch_that_is_not_the_start_s_is_not_followed(tmp_path, build, why):
    rep = analyze_trace(str(_trace(tmp_path, **build)), Config(CONFIG))
    w = rep["window"]
    check(why, _bounds(rep) == fixture.HANDOVER_LAUNCH_MS and w["defined_by"] == "launch"
          and "handover" not in w, (_bounds(rep), w.get("defined_by")))
    check("and nothing is said of it", "handed" not in to_markdown(rep))


# --- over a set, and against another ------------------------------------------------

def _window(first: float, after: float, to: str = fixture.HANDOVER_MAIN) -> dict:
    return {"defined_by": launch_mod.HANDOVER, "report_fully_drawn": None,
            "handover": {"from": fixture.HANDOVER_SPLASH, "to": to,
                         "first_frame_ms": first, "after_ms": after, "launches": 1}}


def test_a_set_says_the_medians_and_how_many_handed_over():
    got = launch_mod.merged([_window(170, 250), _window(180, 260), _window(200, 270)], 3)
    check("medians, the activity most named, and every repeat",
          got["handover"] == {"from": fixture.HANDOVER_SPLASH, "to": fixture.HANDOVER_MAIN,
                              "first_frame_ms": 180, "after_ms": 260, "launches": 1,
                              "runs": "3/3"}, got)


def test_a_set_that_handed_over_in_some_repeats_mixes_two_windows():
    alone = {"defined_by": launch_mod.LAUNCH, "report_fully_drawn": None}
    got = launch_mod.merged([_window(170, 250), alone, _window(200, 270)], 3)
    check("two definitions, counted", got["defined_by_runs"] == {
        launch_mod.HANDOVER: "2/3", launch_mod.LAUNCH: "1/3"}, got)
    said = " ".join(launch_mod.lines(got))
    check("and said with what to look for",
          "at the end of the launch the app handed over to in 2 and at the launch's end in "
          "1 of 3" in said and "by itself after its first frame in some repeats and not in "
          "others" in said, said)


def test_compare_refuses_a_handed_over_window_against_one_closed_at_the_first_frame():
    said = launch_mod.differ({"window": {"defined_by": launch_mod.LAUNCH}},
                             {"window": {"defined_by": launch_mod.HANDOVER}})
    check("two stretches of the start",
          said == "the windows were defined differently: as the launch, closed at its end "
                  "before, as the launch, run on through the launches the app handed over "
                  "to after", said)
    check("and one against its own kind is fine", launch_mod.differ(
        {"window": {"defined_by": launch_mod.HANDOVER}},
        {"window": {"defined_by": launch_mod.HANDOVER}}) is None)
