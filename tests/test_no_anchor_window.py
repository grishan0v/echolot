"""A scenario with no anchors says where its window was taken from (#614).

`null` is what setup writes for an anchor it could not find. The window then
runs from the process's first slice to where its last one ends, and on
three `command` scenarios of one app that was 3 to 6 times the path from the
tap to the settled screen. The report never said there were no anchors, and
setup and the warnings spoke of "the whole trace", which is not what the code
takes either.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, report  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402

PROJECT = {"package": "com.example.app", "process": "com.example.app"}


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("noanchor") / "f.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def _header(trace: Path, scenario: dict) -> str:
    rep = analyze_trace(trace, Config({"project": PROJECT, "scenario": scenario}))
    return report.to_markdown(rep)


def test_no_anchors_is_one_line_that_says_where_the_window_came_from(trace: Path) -> None:
    text = _header(trace, {"name": "tap"})
    line = next((x for x in text.splitlines() if x.startswith("> No anchors")), "")
    check("the line is there", line, text[:1500])
    check("and says what the window runs over",
          "the process's first slice to where its last one ends" in line, line)
    check("nothing calls it the whole trace", "whole trace" not in text, text[:1500])


def test_a_missing_end_alone_says_so(trace: Path) -> None:
    text = _header(trace, {"name": "tap", "start": {"name": "AppStart"},
                           "end": {"name": None}})
    check("the end only",
          "> No end anchor in the scenario: the window runs to where the "
          "process's last slice ends" in text, text[:1500])
    check("and not the start", "No start anchor" not in text, text[:1500])


def test_both_anchors_named_says_nothing(trace: Path) -> None:
    text = _header(trace, {"name": "fixture", "start": {"name": "AppStart"},
                           "end": {"name": "Screen.firstFrame"}})
    check("no line", "anchor in the scenario" not in text
          and "No anchors" not in text, text[:1500])


def test_an_anchor_that_found_nothing_says_which_end_fell_back(trace: Path) -> None:
    text = _header(trace, {"name": "tap", "start": {"name": "AppStart"},
                           "end": {"name": "no_such_marker"}})
    check("the end ran to the process's last slice",
          "End anchor `no_such_marker` was not found in the trace — the window ran "
          "to where the process's last slice ends" in text, text[:1500])
