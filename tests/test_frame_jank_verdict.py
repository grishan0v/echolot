#!/usr/bin/env python3
"""A cold start whose few frames were all late, each for its own mix of
reasons, is a finding (#726).

`frame_jank` groups the late frames by `jank_type`, a set of reasons joined
with commas, and one bad frame often has a set of its own. The bar
`min_frames` is a number of late frames, so it is held against all the
frames of one verdict in the window: three frames the app was late for, by
three different mixes of reasons, made three groups of one frame and no row
at all, and the plain reading said hardly any frame came late.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, plain, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402

FT = fixture.FT
APP = FT.JANK_APP_DEADLINE_MISSED
HAL = getattr(FT, "JANK_DISPLAY_HAL", 8)

# What the first repeat of the issue's cold start held, scaled into the
# fixture's window: three frames, the app's, late by hundreds of ms.
COLD_START = [
    (1, 200, 16, 300, APP),
    (2, 520, 16, 120, APP | FT.JANK_BUFFER_STUFFING),
    (3, 700, 16, 60, HAL | APP),
]


def _report(tmp_path: Path, frames: list) -> dict:
    trace = tmp_path / "f.perfetto-trace"
    old = fixture.APP_FRAMES
    fixture.APP_FRAMES = frames
    try:
        trace.write_bytes(fixture.build())
    finally:
        fixture.APP_FRAMES = old
    return analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))


def _jank(report: dict) -> dict:
    return next(d for d in report["detectors"] if d["id"] == "frame_jank")


def test_three_late_frames_of_three_types_are_a_finding(tmp_path: Path) -> None:
    report = _report(tmp_path, COLD_START)
    rows = _jank(report)["rows"]
    check("a row for each type the late frames had", len(rows) == 3,
          [(r["location"], r["count"]) for r in rows])
    check("each the app's own", all(r["detail"].startswith("Self Jank") for r in rows),
          [r["detail"] for r in rows])
    check("the worst first", rows[0]["max_ms"] == max(r["max_ms"] for r in rows), rows)
    check("frame_jank fired", "frame_jank" in report["summary"]["fired_ids"],
          report["summary"]["fired_ids"])
    text = plain.render(report)
    check("the plain reading does not call it clean",
          "Hardly any frame came late" not in text, text)


def test_two_late_frames_are_still_an_anecdote(tmp_path: Path) -> None:
    rows = _jank(_report(tmp_path, COLD_START[:2]))["rows"]
    check("two late frames, whatever their types, make no row", rows == [], rows)


def test_the_verdicts_are_counted_apart(tmp_path: Path) -> None:
    """Two of the app's late frames and two queued ones are four late frames
    of two verdicts, two of each: no row, as #537 keeps Buffer Stuffing
    apart from the frames the app missed."""
    rows = _jank(_report(tmp_path, [
        (1, 200, 16, 300, APP),
        (2, 520, 16, 120, HAL | APP),
        (3, 700, 16, 40, FT.JANK_BUFFER_STUFFING),
        (4, 760, 16, 40, FT.JANK_BUFFER_STUFFING),
    ]))["rows"]
    check("no verdict reaches three frames", rows == [], rows)
