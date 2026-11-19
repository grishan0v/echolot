#!/usr/bin/env python3
"""`scenario.window`: how a config says its window is the launch (#446).

A scenario says where it starts and ends with two anchors, or names the one
window that needs none. Here: the two ways to write it, what is refused, and
a scenario under `scenarios:`. What the window is on a trace is in
`test_launch_window.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import selftest  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402

PROJECT = {"package": "com.example.app", "process": "com.example.app"}
LAUNCH = {"project": PROJECT, "scenario": {"name": "startup", "window": "launch"}}


def _window(scenario: dict) -> str | None:
    return Config({"project": PROJECT, "scenario": scenario}).scenario_window


def test_the_window_is_one_word_or_a_block_like_an_anchor() -> None:
    check("the word", Config(LAUNCH).scenario_window == "launch")
    block = Config({"project": PROJECT, "scenario": {"name": "startup", "window": {
        "name": "launch", "_source": "derived", "_evidence": "the launcher activity"}}})
    check("a block with its provenance, as `start` and `end` take one",
          block.scenario_window == "launch")
    check("and such a scenario has no anchors",
          (block.scenario_start, block.scenario_end)
          == ("__echolot_no_anchor__", "__echolot_no_anchor__"))
    check("a scenario with anchors has no window, as before",
          Config(selftest.FIXTURE_CONFIG).scenario_window is None)
    check("nor one with nothing under it", _window({"name": "s", "window": None}) is None)


def test_a_window_beside_an_anchor_is_refused() -> None:
    for key in ("start", "end"):
        with pytest.raises(ConfigError) as e:
            _window({"window": "launch", key: "Screen.loaded"})
        check(f"`window` beside `{key}`: two answers to one question",
              f"`{key}`" in str(e.value) and "keep one" in str(e.value), e.value)
    check("an anchor written as `name: null` is no anchor, and is not in the way",
          _window({"window": "launch", "end": {"name": None, "_source": "default"}}) == "launch")


def test_a_window_that_is_no_window_is_refused_in_a_sentence() -> None:
    for value, said in (("startup", "'startup' is no window"), (True, "True is no window")):
        with pytest.raises(ConfigError) as e:
            _window({"window": value})
        check(f"`window: {value}` names what it takes", said in str(e.value)
              and "`launch`" in str(e.value), e.value)
    with pytest.raises(ConfigError) as e:
        _window({"window": {"_source": "derived"}})
    check("a block without `name`", "has no `name`" in str(e.value), e.value)


def test_a_scenario_under_scenarios_takes_a_window_too(tmp_path: Path) -> None:
    many = {"project": PROJECT, "scenarios": {
        "startup": {"window": "launch"},
        "feed": {"start": "Feed.open", "end": "Feed.shown"}}}
    cfg = Config(many)
    check("the first reads the launch", cfg.scenario_window == "launch")
    check("and the second its anchors", cfg.select("feed").scenario_window is None
          and cfg.scenario_end == "Feed.shown")
    path = tmp_path / "echolot.yml"
    path.write_text("project: {package: com.example.app}\n"
                    "scenarios:\n  feed: {start: a, end: b}\n"
                    "  startup: {window: launch, end: Screen.loaded}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        Config.load(path)
    check("a window no scenario can mean is refused on load, whichever is selected",
          "scenarios.startup" in str(e.value) and "keep one" in str(e.value), e.value)
    path.write_text("project: {package: com.example.app}\n"
                    "scenario: {name: s, window: first_frame}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        Config.load(path)
    check("and so is a single scenario's", "scenario.window" in str(e.value), e.value)
