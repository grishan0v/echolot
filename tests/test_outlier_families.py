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
from echolot.analyze import (  # noqa: E402
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
        # The second query reads the occurrences the first wrote down (#490).
        sql, params = detector.render()
        tp.query(sql)
        spans = _spans_behind(tp, detector, params, rows)
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


def test_every_name_of_the_window_has_its_family(tmp_path: Path) -> None:
    """#831: the table is built once per trace, for slice and thread names
    alike, and not only for the main thread's slices. A name the config's
    `domains` lists keeps its digits, as the merge keeps them."""
    raw = {**selftest.FIXTURE_CONFIG,
           "domains": [{"slice": "Choreographer#doFrame 55103", "code": "Frames"}]}
    cfg = Config(raw)
    with TraceSession(_trace(tmp_path, FRAMES)) as tp:
        _setup_context(tp, cfg, _resolve_process(tp, cfg.process)[0]["upid"])
        family = {r["name"]: r["family"]
                  for r in tp.query("SELECT name, family FROM _name_family")}
    check("a frame's vsync id is folded",
          family.get("Choreographer#doFrame 55101") == "Choreographer#doFrame #", family)
    check("a worker thread's number is folded",
          family.get("DefaultDispatcher-worker-1") == "DefaultDispatcher-worker-#", family)
    check("a lock note keeps its line and loses its owner's numbers",
          any("Store.java:41" in f and "Thread-# (#)" in f for f in family.values()), family)
    check("a name the project listed is not folded",
          "Choreographer#doFrame 55103" not in family, family)


# --- where the worst occurrence went (#912) --------------------------------------

def test_an_outlier_names_the_slice_that_held_most_of_it(tmp_path: Path) -> None:
    """A 61 ms WebView start in ten pieces of under 6 ms, inside an
    activity's start inside a `clientTransactionExecuted`: every piece under
    main_thread_block's bar, and the outlier row named only the outermost
    slice. It now names the way down to the slice that held most of it."""
    from echolot import plain
    trace = tmp_path / "nested.perfetto-trace"
    trace.write_bytes(fixture.nested_block())
    cfg = Config({"project": {"package": "com.example.app", "process": "com.example.app"},
                  "scenario": {"name": "s", "start": {"name": "clientTransactionExecuted"},
                               "end": {"name": "Screen.firstFrame"}}})
    rep = analyze_trace(trace, cfg)
    rows = {d["id"]: d["rows"] for d in rep["detectors"]}
    block = [r["location"] for r in rows["main_thread_block"]]
    check("no block row names the WebView start", not any("WebView" in str(n) for n in block),
          block)
    row = rows["main_thread_outlier"][0]
    check("the outlier row names the way, the deepest last, with its time",
          row["location"] == "clientTransactionExecuted" and row["detail"].endswith(
              " · mostly in performStart:com.example.app.PageActivity › "
              "WebViewChromiumAwInit.startChromiumLockedSync (61.0 ms)"), row)
    said = plain.found(next(d for d in rep["detectors"] if d["id"] == "main_thread_outlier"),
                       rep, plain._Ctx(rep))
    check("and the plain sentence names it",
          "Most of the longest was `WebViewChromiumAwInit.startChromiumLockedSync`, 61 ms." in said,
          said)
    check("a piece no larger than half its parent stops the way",
          "step" not in row["detail"])
