#!/usr/bin/env python3
"""A frame is one occurrence of the frames' work, whatever its vsync id (#728).

Since Android 12 every frame's slice is `Choreographer#doFrame <vsync id>`,
a name of its own. `main_thread_outlier` took its median per raw name, so
every frame was a group of one, `min_occurrences` was never reached, and a
215 ms frame among frames of 0.3 ms was never flagged, though it is the case
the detector was written for. The names are now grouped with the numbers the
runtime chose folded, the way the merge of repeats folds them.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import (  # noqa: E402
    DETECTOR_DIR,
    _resolve_process,
    _setup_context,
    _spans_behind,
    analyze_trace,
)
from echolot.tp import Detector, TraceSession  # noqa: E402
from tests.support import check  # noqa: E402

# Ten frames of 1 ms and one of 80, each with its own vsync id, in place of
# everything the main thread does inside `AppStart` but the end anchor.
FRAMES = [(f"Choreographer#doFrame {55100 + i}", 200 + i * 5, 1, []) for i in range(10)]
SLOW = ("Choreographer#doFrame 55110", 300, 80, [])


def _trace(tmp_path: Path, frames: list) -> Path:
    slices = copy.deepcopy(fixture.SLICES)
    main = slices[fixture.TID_MAIN]
    for i, (name, start, dur, kids) in enumerate(main):
        if name == "AppStart":
            end = [k for k in kids if k[0] == "Screen.firstFrame"]
            main[i] = (name, start, dur, frames + end)
    old = fixture.SLICES
    fixture.SLICES = slices
    try:
        path = tmp_path / "frames.perfetto-trace"
        path.write_bytes(fixture.build())
    finally:
        fixture.SLICES = old
    return path


def _outlier(report: dict) -> list[dict]:
    det = next(d for d in report["detectors"] if d["id"] == "main_thread_outlier")
    check("main_thread_outlier ran", det["error"] is None, det["error"])
    return det["rows"]


def test_a_slow_frame_among_frames_of_their_own_names_is_an_outlier(tmp_path: Path) -> None:
    cfg = Config(selftest.FIXTURE_CONFIG)
    trace = _trace(tmp_path, FRAMES + [SLOW])
    rows = _outlier(analyze_trace(trace, cfg))
    check("one row, for the frames' family",
          [r["location"] for r in rows] == ["Choreographer#doFrame #"], rows)
    row = rows[0]
    check("the slow frame alone is the outlier",
          row["count"] == 1 and row["max_ms"] == 80.0, row)
    check("measured against all eleven frames",
          "median 1.0 ms of 11" in row["detail"], row["detail"])

    detector = Detector.from_file(DETECTOR_DIR / "main_thread_outlier.sql")
    with TraceSession(trace) as tp:
        _setup_context(tp, cfg, _resolve_process(tp, cfg.process)[0]["upid"])
        spans = _spans_behind(tp, detector, dict(detector.params), rows)
    check("its interval is the slow frame", spans == [(fixture.ms(300), 80_000_000)],
          spans)


def test_frames_that_all_take_as_long_are_not_outliers(tmp_path: Path) -> None:
    even = FRAMES + [("Choreographer#doFrame 55110", 300, 1, [])]
    check("no row", _outlier(analyze_trace(_trace(tmp_path, even),
                                           Config(selftest.FIXTURE_CONFIG))) == [])


def test_numbers_someone_wrote_still_tell_work_apart(tmp_path: Path) -> None:
    """`Load step0`, `Load step1` and `Load step7` are names somebody chose,
    not one name the runtime numbered: a slow `Load step7` seen once has no
    history of its own to be an outlier from."""
    work = ([(f"Load step{i % 2}", 200 + i * 5, 1, []) for i in range(10)]
            + [("Load step7", 300, 80, [])])
    rows = _outlier(analyze_trace(_trace(tmp_path, work), Config(selftest.FIXTURE_CONFIG)))
    check("no row: the slow one is a name of its own", rows == [], rows)
