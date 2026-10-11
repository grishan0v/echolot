#!/usr/bin/env python3
"""Several scenarios in one echolot.yml, and one picked per command (#346).

`scenarios:` maps a name to what `scenario:` holds, plus the runner keys the
scenario changes. Everything that reads the scenario goes through `Config`,
so these hold the reading, the refusals, and the three commands that take
`--scenario`: the window `analyze` and `calibrate` measure, and the traces
and the runner `collect` records with.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, runner  # noqa: E402
from echolot.config import NO_ANCHOR, Config, ConfigError  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import FakeAdb  # noqa: E402

PROJECT = {"package": "com.example.app", "process": "com.example.app"}
TWO = {
    "project": PROJECT,
    "scenarios": {
        "first_frame": {"start": {"name": "AppStart"}, "end": {"name": "Screen.firstFrame"}},
        "loaded": {"start": {"name": "AppStart"}, "end": {"name": "Screen.loaded"},
                   "budget_ms": 900,
                   "runner": {"mode": "command", "command": "echo scroll",
                              "gradle_args": ["-Pclass=Scroll"]}},
    },
    "runner": {"mode": "launch", "iterations": 3, "gradle_args": ["-Pclass=Startup"]},
}


def config(raw: dict, tmp_path: Path | None = None) -> Config:
    return Config(raw, tmp_path / "echolot.yml" if tmp_path else None)


def write(root: Path, raw: dict, name: str = "echolot.yml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --- the reading ----------------------------------------------------------------

def test_the_first_scenario_is_read_until_another_is_picked() -> None:
    cfg = config(TWO)
    check("the names, in the file's order", cfg.scenarios == ["first_frame", "loaded"],
          cfg.scenarios)
    check("the first by default: its name, its window",
          (cfg.scenario_name, cfg.scenario_start, cfg.scenario_end)
          == ("first_frame", "AppStart", "Screen.firstFrame"))
    check("and the config's runner as it is", cfg.runner == TWO["runner"], cfg.runner)
    cfg.select("loaded")
    check("picked: the other's name and window",
          (cfg.scenario_name, cfg.scenario_end) == ("loaded", "Screen.loaded"))
    check("its runner keys laid over the config's, a list replaced whole",
          cfg.runner == {"mode": "command", "iterations": 3, "command": "echo scroll",
                         "gradle_args": ["-Pclass=Scroll"]}, cfg.runner)
    cfg.select(None)
    check("and back to the first", cfg.scenario_name == "first_frame")


def test_one_scenario_and_none_read_as_they_did() -> None:
    one = config({"project": PROJECT, "scenario": {"name": "coldStart", "end": {"name": "X"}}})
    check("one scenario, named by its `name`", one.scenarios == ["coldStart"]
          and one.select("coldStart").scenario_end == "X", one.scenarios)
    with pytest.raises(ConfigError, match="has one scenario, `coldStart`"):
        one.select("scroll")
    bare = config({"project": PROJECT})
    check("none at all is the whole trace, named `run`",
          bare.scenarios == ["run"] and bare.scenario_start == NO_ANCHOR)


def test_a_scenario_left_empty_is_the_whole_trace() -> None:
    cfg = config({"project": PROJECT, "scenarios": {"everything": None}})
    check("no anchors", (cfg.scenario_name, cfg.scenario_start, cfg.scenario_end)
          == ("everything", NO_ANCHOR, NO_ANCHOR))


def test_provenance_rides_along_and_a_confirmed_end_is_found() -> None:
    raw = {"project": PROJECT, "scenarios": {"loaded": {
        "end": {"name": "Screen.loaded", "_source": "confirmed_by_user", "_evidence": "probe"},
        "_source": "derived"}}}
    cfg = config(raw)
    check("an entry may say where it came from", cfg.scenario_end == "Screen.loaded")
    check("a value a person confirmed is named by where it is",
          cfg.confirmed() == {"scenarios.loaded.end.name": "Screen.loaded"}, cfg.confirmed())


@pytest.mark.parametrize("raw, said", [
    ({"scenario": {"name": "a"}, "scenarios": {"b": {}}}, "two ways to write the scenarios"),
    ({"scenarios": ["cold", "warm"]}, "must map a name to a scenario"),
    ({"scenarios": {}}, "must map a name to a scenario"),
    ({"scenarios": {"cold start": {}}}, "cannot name a scenario"),
    ({"scenarios": {"a/b": {}}}, "cannot name a scenario"),
    ({"scenarios": {"cold": "AppStart"}}, "must be a mapping"),
    ({"scenarios": {"cold": {"name": "cold"}}}, "named by its key"),
    ({"scenarios": {"cold": {"detectors": {}}}}, "no key 'detectors'"),
    ({"scenarios": {"cold": {"runner": "launch"}}}, "runner must be a mapping"),
])
def test_a_scenarios_section_without_a_shape_is_refused_on_load(tmp_path: Path, raw: dict,
                                                                 said: str) -> None:
    path = write(tmp_path, {"project": PROJECT, **raw})
    with pytest.raises(ConfigError, match=said):
        Config.load(path)


def test_a_name_the_config_lacks_is_refused_with_the_ones_it_has(tmp_path: Path) -> None:
    path = write(tmp_path, TWO)
    with pytest.raises(ConfigError, match="no such scenario.*`first_frame`, `loaded`"):
        Config.load(path, scenario="scroll")
    check("one it has is read", Config.load(path, scenario="loaded").scenario_name == "loaded")


def test_local_yml_can_add_a_scenario(tmp_path: Path) -> None:
    path = write(tmp_path, TWO)
    write(tmp_path, {"scenarios": {"warm": {"end": {"name": "Screen.firstFrame"},
                                            "runner": {"reset_policy": "none"}}}}, "local.yml")
    cfg = Config.load(path, scenario="warm")
    check("the overlay's scenario joins the file's", cfg.scenarios
          == ["first_frame", "loaded", "warm"] and cfg.runner["reset_policy"] == "none",
          (cfg.scenarios, cfg.runner))


# --- the commands -----------------------------------------------------------------

@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("trace") / "fixture.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def test_analyze_measures_the_scenario_it_is_given(tmp_path: Path, trace: Path) -> None:
    path = write(tmp_path, TWO)
    windows = {}
    for picked in (None, "loaded"):
        out = tmp_path / f"out-{picked}"
        argv = ["analyze", str(trace), "-c", str(path), "-o", str(out)]
        code, _, err = run(*argv, *(["--scenario", picked] if picked else []))
        check(f"{picked}: exit 0", code == 0, err[-600:])
        window = json.loads((out / "report.json").read_text(encoding="utf-8"))["window"]
        windows[picked] = (window["end_anchor"]["glob"], err)
    check("without --scenario: the first, said with the others' names",
          windows[None][0] == "Screen.firstFrame"
          and "[i] scenario first_frame, the first of 2" in windows[None][1]
          and "(loaded)" in windows[None][1], windows[None])
    check("with it: that scenario's window, and nothing to say",
          windows["loaded"][0] == "Screen.loaded" and "[i] scenario" not in windows["loaded"][1],
          windows["loaded"])


def test_a_config_with_one_scenario_says_nothing_new(tmp_path: Path, trace: Path) -> None:
    path = write(tmp_path, {"project": PROJECT,
                            "scenario": {"name": "fixture", "start": {"name": "AppStart"},
                                         "end": {"name": "Screen.firstFrame"}}})
    code, _, err = run("analyze", str(trace), "-c", str(path), "-o", str(tmp_path / "out"))
    check("exit 0, and no scenario note", code == 0 and "[i] scenario" not in err, err[-400:])


def test_analyze_refuses_a_scenario_the_config_lacks(tmp_path: Path, trace: Path) -> None:
    path = write(tmp_path, TWO)
    code, _, err = run("analyze", str(trace), "-c", str(path), "--scenario", "scroll",
                       "-o", str(tmp_path / "out"))
    check("exit 2, in a sentence", code == 2 and "no such scenario" in err
          and "Traceback" not in err, err[-400:])


def test_calibrate_measures_the_scenario_and_says_which(tmp_path: Path, trace: Path) -> None:
    path = write(tmp_path, TWO)
    code, out, err = run("calibrate", str(trace), "-c", str(path), "--scenario", "loaded")
    check("exit 0", code == 0, err[-600:])
    check("the section says it came from that scenario's window",
          "# Scenario loaded, window: 900 ms" in out, out[:400])


def test_collect_records_the_scenario_with_its_runner(tmp_path: Path, monkeypatch) -> None:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    raw = {"project": {"package": "com.example.app"},
           "scenarios": {"cold": {}, "warm": {"runner": {"iterations": 2,
                                                         "reset_policy": "none"}}},
           "runner": {"iterations": 3}}
    write(tmp_path, raw)
    monkeypatch.chdir(tmp_path)
    code, _, err = run("collect", "--scenario", "warm")
    check("exit 0", code == 0, err[-600:])
    names = sorted(p.name for p in (tmp_path / ".echolot/traces").iterdir())
    check("the scenario's traces, as many as its runner asks",
          names == ["warm_iter000.perfetto-trace", "warm_iter001.perfetto-trace"], names)
    check("and its reset: nothing stopped between launches",
          "force-stop" not in fake.kinds(), fake.kinds())
