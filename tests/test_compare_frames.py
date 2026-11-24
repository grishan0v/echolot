"""One side drew far fewer frames in the same window (#566).

In a frame-paced scenario the window is the gesture's length, and a build
that janks draws fewer frames in it. Work done once a frame shrinks with the
frames, and the table read as a list of improvements that held, while each
occurrence cost more. `analyze` now keeps the frames drawn in the window;
`compare` warns, gives such rows per occurrence without a verdict on the
total, and lists first the rows that moved against the frames.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report  # noqa: E402


def _row(location: str, values: list[float], count: float, metric: str) -> dict:
    return {"location": location, "runs": f"{len(values)}/{len(values)}", "count": count,
            metric: sorted(values)[len(values) // 2],
            "spread": {metric: {"min": min(values), "max": max(values), "values": values}}}


def _side(frames: int | None, post: tuple[list[float], float],
          late: tuple[list[float], float]) -> dict:
    rep = report([
        det("main_thread_block", [_row("postAndWait", *post, "self_ms")]),
        det("frame_jank", [_row("App Deadline Missed", *late, "total_ms")]),
    ], runs=5, window={"process": "com.example.app", "duration_ms": 14900.0,
                       **({"frames": {"count": frames}} if frames else {})})
    return rep


SMOOTH = (657, ([890.0, 894.0, 900.0, 880.0, 896.0], 670),
          ([120.0, 128.9, 130.0, 125.0, 140.0], 5))
# A fifth of the frames, each occurrence about twice the cost, and more frames
# the app itself made late.
JANKY = (117, ([396.0, 400.3, 410.0, 390.0, 405.0], 161.5),
         ([4400.0, 4550.4, 4600.0, 4500.0, 4700.0], 13))


def _by(cmp: dict, location: str) -> dict:
    return next(r for r in cmp["rows"] if r["location"] == location)


def test_fewer_frames_are_said_first_and_per_frame_work_is_no_improvement() -> None:
    cmp = compare(_side(*SMOOTH), _side(*JANKY))
    text = next((w["text"] for w in cmp["warnings"] if w["id"] == "frames"), "")
    check("the warning, with both counts", "after side drew 5.6× fewer frames" in text
          and "657 before, 117 after" in text, cmp["warnings"])
    post = _by(cmp, "postAndWait")
    check("postAndWait gets no verdict on its total",
          post["change"] == "shrank" and post["holds"] is None and post["shift"] is None, post)
    check("and per occurrence it grew",
          post["per_occurrence"] == {"before": 1.33, "after": 2.48}, post)
    late = _by(cmp, "App Deadline Missed")
    check("the app's own late frames grew against the frames, and hold",
          late["holds"] is True and "per_occurrence" not in late, late)
    check("so they lead the table", cmp["rows"][0]["location"] == "App Deadline Missed",
          [r["location"] for r in cmp["rows"]])
    md = compare_mod.to_markdown(cmp)
    check("the N column says the cost of each", "1.33 → 2.48 ms each" in md, md)


def test_the_fix_the_other_way_round_is_not_a_list_of_regressions() -> None:
    cmp = compare(_side(*JANKY), _side(*SMOOTH))
    check("the before side drew fewer",
          any(w["id"] == "frames" and "before side drew 5.6× fewer" in w["text"]
              for w in cmp["warnings"]), cmp["warnings"])
    post = _by(cmp, "postAndWait")
    check("postAndWait grew in total, gets no verdict, and got cheaper each time",
          post["change"] == "grew" and post["holds"] is None
          and post["per_occurrence"]["after"] < post["per_occurrence"]["before"], post)
    check("the late frames that went away lead",
          cmp["rows"][0]["location"] == "App Deadline Missed", cmp["rows"])


def test_the_same_frames_change_nothing() -> None:
    near = (640,) + SMOOTH[1:]
    cmp = compare(_side(*SMOOTH), _side(*near))
    check("no warning, no per-occurrence", "frames" not in {w["id"] for w in cmp["warnings"]}
          and all("per_occurrence" not in r for r in cmp["rows"]))
    without = compare(_side(None, *SMOOTH[1:]), _side(None, *JANKY[1:]))
    check("a side without a frame timeline: the table as it was",
          "frames" not in {w["id"] for w in without["warnings"]}
          and _by(without, "postAndWait")["holds"] is True)


def test_the_merge_keeps_each_runs_frames() -> None:
    reps = [{"window": {"frames": {"count": n}}} for n in (644, 657, 770)]
    check("median and values", report_mod._merge_frames(reps)
          == {"count": 657, "values": [644, 657, 770], "runs": "3/3"})
    check("no timeline, nothing", report_mod._merge_frames([{"window": {}}]) is None)


def test_analyze_counts_the_frames_in_the_window(marker_report) -> None:
    check("the fixture's frames, and the 5 the app made late by frame_jank's own floors",
          marker_report["window"]["frames"] == {"count": 24, "late": 5},
          marker_report["window"].get("frames"))


def test_a_trace_without_a_frame_timeline_has_no_count(tmp_path: Path) -> None:
    from echolot import fixture, recorder, selftest
    from echolot.config import Config
    from echolot.main import analyze_trace

    trace = tmp_path / "no-frames.perfetto-trace"
    trace.write_bytes(fixture.build(frames=False))
    with recorder.isolated():
        rep = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    check("None rather than zero frames", rep["window"]["frames"] is None,
          rep["window"].get("frames"))
