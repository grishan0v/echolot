#!/usr/bin/env python3
"""The run log says how the window `analyze` measured was defined (#459).

A launch window (`scenario.window: launch`, #446) has no anchors, so the two
anchor counts in its line of `.echolot/log/runs.jsonl` are empty and nothing
told the line from one whose anchors were never looked up. `window_defined_by`
is the report's `window.defined_by`, kept where the next `analyze` does not
overwrite it. An anchored window's line has no such key.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_launch_window import LAUNCH  # noqa: E402

from echolot import fixture, recorder, selftest  # noqa: E402
from echolot.main import main  # noqa: E402


def _analyzed(project: Path, monkeypatch, config: dict, *builds: dict) -> tuple[dict, dict]:
    """`echolot analyze` over one trace per build, in a project of its own:
    the facts of the line it left in the run log, and the report's window."""
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    (project / "echolot.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    traces = []
    for i, build in enumerate(builds):
        trace = project / f"startup_iter{i:03d}.perfetto-trace"
        trace.write_bytes(fixture.build(**build))
        traces.append(str(trace))
    said = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        code = main(["analyze", *traces])
    runs = recorder.read(project / recorder.LOG_FILE)
    check("analyze ran and left its one line", code == 0 and len(runs) == 1
          and runs[0]["cmd"] == "analyze", (code, runs, said.getvalue()[-500:]))
    report = json.loads(next(project.rglob("report.json")).read_text(encoding="utf-8"))
    return runs[0]["facts"], report["window"]


def test_a_launch_windows_line_says_what_closed_it(tmp_path: Path, monkeypatch) -> None:
    facts, window = _analyzed(tmp_path / "first-frame", monkeypatch, LAUNCH, {})
    check("an app that never calls `reportFullyDrawn()`: the launch's own end",
          facts.get("window_defined_by") == "launch" == window["defined_by"], facts)
    check("beside the two anchor counts, empty because there were no anchors",
          (facts["start_anchor_matches"], facts["end_anchor_matches"]) == (None, None), facts)

    facts, window = _analyzed(tmp_path / "drawn", monkeypatch, LAUNCH, {"fully_drawn": "app"})
    check("an app that calls it: the launch, on to the call",
          facts.get("window_defined_by") == "launch+reportFullyDrawn" == window["defined_by"],
          facts)

    facts, window = _analyzed(tmp_path / "mixed", monkeypatch, LAUNCH,
                              {}, {"fully_drawn": "app"})
    check("repeats that closed differently: both, as the merged report names them",
          facts.get("window_defined_by") == "launch and launch+reportFullyDrawn"
          == window["defined_by"], facts)


def test_an_anchored_windows_line_is_what_it_was(tmp_path: Path, monkeypatch) -> None:
    anchored, window = _analyzed(tmp_path / "anchored", monkeypatch, selftest.FIXTURE_CONFIG,
                                 {"fully_drawn": "app"})
    check("the report has no `defined_by` to note", "defined_by" not in window, sorted(window))
    check("so the line has no such key, not even an empty one",
          "window_defined_by" not in anchored, anchored)
    check("and its anchors are counted, as before",
          (anchored["start_anchor_matches"], anchored["end_anchor_matches"]) == (1, 1), anchored)
    launch, _ = _analyzed(tmp_path / "launch", monkeypatch, LAUNCH, {"fully_drawn": "app"})
    check("the one key is all that a launch window's line adds, and nothing moved",
          list(anchored) == [k for k in launch if k != "window_defined_by"],
          (list(anchored), list(launch)))
