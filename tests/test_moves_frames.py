"""The plain verdict of a window that runs to the end of the recording (#586).

A scripted drag recorded for 15 s took 14.9 s on every build, and five pairs
of sets of it — a build that janked against the build before and the fix
after — all read "No clear change", while the share of frames the app itself
made late went 3% → 21% → 10%. Such a window is judged by that share, run by
run, with the window's own rule; a window that ends where the scenario does
keeps the verdict by its length.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import moves  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_moves import reading, report  # noqa: E402

GESTURE = (14900.0, 14905.0, 14910.0)


def drag(frames, late, launches=GESTURE, window=None):
    """A set of a drag with no anchors: each run's frames, and how many of
    them the app made late. `window` puts its keys over the window's."""
    rep = report([], launches=launches, startup=False)
    rep["window"].update({"start_anchor": None, "end_anchor": None})
    if frames is not None:
        merged = report_mod._merge_frames([{"window": {"frames": {"count": n, "late": k}}}
                                           for n, k in zip(frames, late, strict=True)])
        rep["window"]["frames"] = merged
    rep["window"].update(window or {})
    return rep


def verdict(before, after):
    return reading(before, after)["verdict"]


GOOD = drag((640, 650, 660), (20, 21, 22))
BAD = drag((600, 612, 620), (125, 128, 132))
FIXED = drag((660, 671, 680), (68, 70, 72))


def test_a_regression_by_the_frames_reads_as_worse_and_its_fix_as_better():
    v = verdict(GOOD, BAD)
    check("worse, by the frames", (v["movement"], v["by"], v["headline"])
          == (moves.WORSE, "frames", "More frames came late"), v)
    check("in one sentence with both shares and both counts, then why the window says nothing", v["text"]
          == "The app made 3.2% of its frames late before and 21% after: 21 of 650 frames in the middle run, then "
             "128 of 612. "
             "The run took 14.9 s before and 14.9 s after: the window runs to the end of the recording, so its "
             "length is the recording's and not the app's. Measured on 3 runs before and 3 after.", v["text"])
    check("with each side's share kept", v["frames"]["before"]["share"] == 0.0323
          and v["frames"]["after"]["low"] == 0.2083, v["frames"])
    v = verdict(BAD, FIXED)
    check("the fix, better", (v["movement"], v["headline"]) == (moves.BETTER, "Fewer frames came late"), v)


FIVE = tuple(14900.0 + i for i in range(5))


def test_the_counts_beside_a_share_are_of_the_run_it_is_of():
    """#846: late 4, 3, 4, 3, 3 of 16, 15, 15, 16, 16 frames. Each run's share
    is 25, 20, 26.7, 18.8 and 18.8%: the median is 20%, the 3 of 15 of the
    second run. The median late frames and the median frames drawn are 3 and
    16, two other runs, and "20% … 3 of 16" was 19%."""
    side = drag((16, 15, 15, 16, 16), (4, 3, 4, 3, 3), launches=FIVE)
    other = drag((16, 15, 15, 16, 17), (3, 4, 3, 3, 3), launches=FIVE)
    v = verdict(side, other)
    check("the share and the counts of the middle run, on each side",
          v["text"].startswith("The app made 20% of its frames late before and 19% after: 3 of 15 frames in the "
                               "middle run, then 3 of 16."), v["text"])
    check("the medians the report keeps stay, beside the run they differ from",
          (v["frames"]["before"]["late"], v["frames"]["before"]["count"], v["frames"]["before"]["middle"])
          == (3, 16, [{"late": 3, "count": 15}]), v["frames"]["before"])


def test_an_even_number_of_runs_says_the_two_its_share_lies_between():
    four = drag((16, 15, 15, 16), (3, 4, 3, 4), launches=FIVE[:4])
    v = verdict(four, drag((16, 15, 15, 16, 16), (4, 3, 4, 3, 3), launches=FIVE))
    check("18.8, 20, 25 and 26.7%: halfway between 20 and 25, and each side says which runs",
          v["text"].startswith("The app made 22% of its frames late before and 20% after: 3 of 15 and 4 of 16 "
                               "frames in the two middle runs, then 3 of 15 in the middle run."), v["text"])
    check("the share the verdict reads is that halfway", v["frames"]["before"]["share"] == 0.225, v["frames"])


def test_every_share_said_is_the_one_its_counts_give():
    """Whatever the runs, the percentage a side says is its counts, rounded
    the way the sentence rounds them."""
    sets = [((16, 15, 15, 16, 16), (4, 3, 4, 3, 3)), ((16, 15, 15, 16, 17), (3, 4, 3, 3, 3)),
            ((640, 650, 660, 700, 655), (20, 21, 22, 9, 40)), ((7, 9, 8), (1, 2, 1)),
            ((12, 13, 14, 15, 16, 17), (1, 2, 3, 3, 2, 1))]
    for frames, late in sets:
        side = moves._late_side(drag(frames, late, launches=tuple(14900.0 + i for i in range(len(frames)))))
        middle = side["middle"]
        own = sum(m["late"] / m["count"] for m in middle) / len(middle)
        check(f"{late} of {frames}: the share is of the runs said", moves._late_share(side)
              == moves._share_of(own) and round(own, 4) == side["share"], side)


def test_the_same_build_twice_is_no_change():
    v = verdict(BAD, drag((605, 610, 618), (120, 130, 126)))
    check("no change, and why", v["movement"] == moves.NO_CHANGE and v["by"] == "frames" and
          "A change under 20%, or under one frame in a hundred, is not counted." in v["text"], v)


def test_runs_that_overlap_are_no_change():
    v = verdict(drag((600, 600, 600), (60, 105, 108)), drag((600, 600, 600), (100, 150, 160)))
    check("a fifth apart, but the worse side's best run is under the better side's typical one",
          v["movement"] == moves.NO_CHANGE and "single runs of the two sides overlap" in v["text"], v)


def test_under_one_frame_in_a_hundred_is_not_news():
    v = verdict(drag((700, 700, 700), (3, 3, 3)), drag((700, 700, 700), (4, 4, 4)))
    check("0.4% against 0.6%: a third apart, and one frame in seven hundred", v["movement"] == moves.NO_CHANGE, v)


def test_too_few_runs_give_no_verdict():
    v = verdict(drag((650, 640), (20, 21), launches=(14900.0, 14905.0)),
                drag((612, 600), (128, 125), launches=(14900.0, 14905.0)))
    check("two a side", v["movement"] == moves.NO_CHANGE and "2 runs before and 2 after are too few to tell a "
          "change from a hiccup: it takes 3 a side." in v["text"] and "Measured" not in v["text"], v)


def test_a_window_that_ends_where_the_scenario_does_is_judged_by_its_length():
    closed = {"end_anchor": {"glob": "Screen.loaded", "matches": 1}}
    v = verdict(drag((640, 650, 660), (20, 21, 22), window=closed),
                drag((600, 612, 620), (125, 128, 132), window=closed))
    check("the window's verdict, and no frames in it", v["movement"] == moves.NO_CHANGE and v["by"] == "window"
          and "frames" not in v and v["text"] == "The run took 14.9 s before and 14.9 s after. Measured on 3 runs "
          "before and 3 after.", v)
    unsaid = report([], launches=GESTURE, startup=False)
    check("nor is a window whose report names no anchors at all", not moves._open(unsaid["window"]))


def test_without_late_frames_the_window_says_why_it_says_nothing():
    v = verdict(drag(None, None, window={"frames": None}), drag(None, None, window={"frames": None}))
    check("no frame timeline", v["movement"] == moves.NO_CHANGE and v["text"]
          == "The run took 14.9 s before and 14.9 s after, but the window runs to the end of the recording, so "
             "its length may be the recording's and not the app's. There is no frame timeline on either side, so "
             "no late frames to judge it by: read the checks below. Measured on 3 runs before and 3 after.", v)
    old = drag(None, None, window={"frames": {"count": 612, "values": [600, 612, 620], "runs": "3/3"}})
    v = verdict(old, old)
    check("a report written before they were counted", "analyse the recordings again" in v["text"], v["text"])


def test_a_window_that_moved_keeps_its_verdict_and_says_the_frames_beside_it():
    slower = (19900.0, 19905.0, 19910.0)
    v = verdict(GOOD, drag((600, 612, 620), (125, 128, 132), launches=slower))
    check("slower, with more frames late", v["movement"] == moves.WORSE and v["headline"] == "Slower by 5.0 s"
          and "The app made 3.2% of its frames late before and 21% after" in v["text"], v)
    v = verdict(BAD, drag((660, 671, 680), (68, 70, 72), launches=slower))
    check("slower, with fewer late: neither", v["movement"] == moves.NO_CHANGE and v["headline"]
          == "Slower, but fewer frames came late" and "neither is called a change" in v["text"], v)


def test_the_merge_keeps_each_runs_late_frames_beside_its_frames():
    reps = [{"window": {"frames": {"count": n, "late": k}}} for n, k in ((644, 20), (657, 31), (770, 12))]
    check("median and values, in the order of the frames", report_mod._merge_frames(reps)
          == {"count": 657, "values": [644, 657, 770], "runs": "3/3", "late": 20, "late_values": [20, 31, 12]})
    reps[1]["window"]["frames"].pop("late")
    check("a repeat that does not say leaves them out", "late" not in report_mod._merge_frames(reps))
