#!/usr/bin/env python3
"""`composables`' `detail`: what composed each call, read off the pass around it (#368).

The fixture holds three of the shapes under its `AppStart` anchor, and the
self-check pins them. A pass with nothing above it cannot sit under an anchor
at all, and it is how a large app's warm start composed its views for the
first time: each such pass followed `Compose:initializeView`, or the pass of
the view before it. So this trace is built here: one main thread, and one
call of the app's own in each shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402

PID = 7100
APP = "com.example.app"
# No anchors: the window is the whole trace.
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "passes"}}


def _pass(name: str, start: float) -> tuple:
    """A `Compose:recompose` of 10 ms, composing one call of the app's `name`."""
    return ("Compose:recompose", start, 10,
            [(f"{APP}.ui.{name} ({name}.kt:7)", start + 0.5, 9, [])])


SLICES = [
    # A view's first composition with nothing above it, after
    # `Compose:initializeView`, as on a large app's warm start.
    ("Compose:initializeView", 10, 1, []),
    _pass("Root", 12),
    # The first frame attaches a view, and its traversal composes it.
    ("Choreographer#doFrame 1", 30, 14, [
        ("traversal", 31, 12, [_pass("Screen", 32)])]),
    # State changed, and a frame recomposed the screen.
    ("Choreographer#doFrame 2", 50, 14, [
        ("animation", 51, 12, [
            ("Recomposer:recompose", 51.5, 11, [_pass("Screen", 52)])])]),
    # A lazy list composing an item as it measured, inside a traversal.
    ("Choreographer#doFrame 3", 70, 16, [
        ("traversal", 71, 14, [
            ("measure", 71.5, 13, [
                ("AndroidOwner:onMeasure", 72, 12, [_pass("Item", 72.5)])])])]),
    # A pass under anything else: a lazy list's prefetch.
    ("compose:lazy:prefetch:compose", 90, 12, [_pass("Item", 90.5)]),
    # A call with no pass around it, as in a recording that began inside one.
    (f"{APP}.ui.Orphan (Orphan.kt:7)", 110, 9, []),
]


def _trace(path: Path) -> Path:
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [(PID, APP, {PID: APP})])
    events = fixture._flatten(SLICES, [], iter(range(10**6)))
    fixture.ftrace(builder, {0: [
        (ts, order, "print", PID, f"B|{PID}|{name}\n" if kind == "B" else f"E|{PID}\n")
        for ts, order, kind, name in events]})
    path.write_bytes(builder.serialize())
    return path


def test_each_call_says_what_composed_it(tmp_path: Path) -> None:
    report = analyze_trace(_trace(tmp_path / "passes.perfetto-trace"), Config(CONFIG))
    det = next(d for d in report["detectors"] if d["id"] == "composables")
    check("composables ran without an error", det["error"] is None, det["error"])
    got = {r["location"].split(" (")[0].rsplit(".", 1)[1]: r["detail"] for r in det["rows"]}
    check("a pass with nothing above it is a first composition",
          got.get("Root") == "1 in a first composition", got)
    check("so is the pass a frame's traversal makes; a frame's recomposition is in a frame",
          got.get("Screen") == "1 in a first composition · 1 in a frame", got)
    check("under onMeasure is during measure, inside a traversal too; any other parent "
          "is elsewhere", got.get("Item") == "1 during measure · 1 elsewhere", got)
    check("a call with no pass around it is elsewhere, and no first composition",
          got.get("Orphan") == "1 elsewhere", got)
