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
    ({"scenarios": {"cold": {"thresholds": {}}}}, "no key 'thresholds'"),
    ({"scenarios": {"cold": {"runner": "launch"}}}, "runner must be a mapping"),
    ({"scenarios": {"cold": {"detectors": ["frame_jank"]}}},
     "scenarios.cold.detectors must be a mapping of detector names"),
    ({"scenarios": {"cold": {"detectors": {"frame_jank": True}}}},
     r"scenarios\.cold\.detectors\.frame_jank: true is not a setting"),
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


# --- each scenario's report (#347) -------------------------------------------------

def analyzed(project: Path, trace: Path, monkeypatch, *picks: str | None) -> None:
    """`analyze` run in the project, once per scenario named (None: no flag)."""
    monkeypatch.chdir(project)
    for picked in picks:
        code, _, err = run("analyze", str(trace), *(["--scenario", picked] if picked else []))
        check(f"analyze {picked}: exit 0", code == 0, err[-600:])


def test_each_scenario_keeps_its_own_report_and_names_itself(tmp_path: Path, trace: Path,
                                                             monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, "first_frame", "loaded")
    out = tmp_path / ".echolot/out"
    check("a directory per scenario, and none shared",
          sorted(p.name for p in out.iterdir()) == ["first_frame", "loaded"],
          sorted(p.name for p in out.iterdir()))
    for name, end in (("first_frame", "Screen.firstFrame"), ("loaded", "Screen.loaded")):
        rep = json.loads((out / name / "report.json").read_text(encoding="utf-8"))
        check(f"{name}: the report names its scenario, and measured its window",
              rep["scenario"] == name and rep["window"]["end_anchor"]["glob"] == end, rep["scenario"])
        md = (out / name / "report.md").read_text(encoding="utf-8")
        check(f"{name}: so does its header", f"Scenario: `{name}`" in md, md[:300])


def test_one_scenario_writes_where_it_always_did(tmp_path: Path, trace: Path, monkeypatch) -> None:
    write(tmp_path, {"project": PROJECT,
                     "scenario": {"name": "fixture", "start": {"name": "AppStart"},
                                  "end": {"name": "Screen.firstFrame"}}})
    analyzed(tmp_path, trace, monkeypatch, None)
    rep = json.loads((tmp_path / ".echolot/out/report.json").read_text(encoding="utf-8"))
    check("in .echolot/out, named after its scenario", rep["scenario"] == "fixture", rep["scenario"])


def test_an_out_directory_given_is_where_it_goes(tmp_path: Path, trace: Path, monkeypatch) -> None:
    write(tmp_path, TWO)
    monkeypatch.chdir(tmp_path)
    code, _, err = run("analyze", str(trace), "--scenario", "loaded", "-o", "elsewhere")
    check("exit 0, and -o kept", code == 0 and (tmp_path / "elsewhere/report.json").is_file()
          and not (tmp_path / ".echolot/out").exists(), err[-400:])


def test_a_report_from_before_says_no_scenario() -> None:
    from echolot import report as report_mod
    text = report_mod.to_markdown(json.loads(json.dumps(OLD_REPORT)))
    check("no scenario line for a report that names none", "Scenario: `" not in text, text[:300])


OLD_REPORT = {
    "trace": "t.perfetto-trace", "window": {"process": "com.example.app"},
    "summary": {"detectors_run": 0, "detectors_fired": 0, "fired_ids": [], "absent_ids": []},
    "detectors": [],
}


def test_report_reads_the_scenario_it_is_asked_for(tmp_path: Path, trace: Path,
                                                    monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, "first_frame", "loaded")
    code, out, err = run("report", "--window")
    check("the first scenario's by default", code == 0 and "Screen.firstFrame" in out
          and "scenario `first_frame`" in out, (out[:400], err[-300:]))
    code, out, _ = run("report", "--window", "--scenario", "loaded")
    check("the one named with --scenario", code == 0 and "Screen.loaded" in out, out[:400])
    code, _, err = run("report", "--scenario", "scroll")
    check("one the config lacks: exit 2, in a sentence", code == 2
          and "no such scenario" in err and "Traceback" not in err, err[-300:])


def test_compare_refuses_two_scenarios(tmp_path: Path, trace: Path, monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, "first_frame", "loaded")
    out = tmp_path / ".echolot/out"
    code, _, err = run("compare", str(out / "first_frame/report.json"),
                       str(out / "loaded/report.json"))
    check("exit 2, naming both", code == 2 and "two scenarios" in err
          and "`first_frame`" in err and "`loaded`" in err, err[-400:])


def test_compare_against_the_latest_of_the_named_reports_scenario(tmp_path: Path, trace: Path,
                                                                   monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, "loaded", "first_frame")
    kept = tmp_path / "before.json"
    kept.write_bytes((tmp_path / ".echolot/out/loaded/report.json").read_bytes())
    code, out, err = run("compare", str(kept))
    check("the newer side is that scenario's latest, though the first ran last",
          code == 0 and "loaded/report.json" in out + err, (out[-600:], err[-400:]))
    check("and the comparison goes beside it",
          (tmp_path / ".echolot/out/loaded/comparison.md").is_file(),
          sorted(p.name for p in (tmp_path / ".echolot/out/loaded").iterdir()))


def test_compare_takes_a_report_from_before_as_it_is(tmp_path: Path, trace: Path,
                                                     monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, "loaded")
    latest = tmp_path / ".echolot/out/loaded/report.json"
    old = json.loads(latest.read_text(encoding="utf-8"))
    del old["scenario"]
    (tmp_path / "old.json").write_text(json.dumps(old), encoding="utf-8")
    code, _, err = run("compare", str(tmp_path / "old.json"), str(latest))
    check("no scenario to hold against: compared", code == 0, err[-400:])


def test_status_finds_the_report_where_analyze_put_it(tmp_path: Path, trace: Path,
                                                      monkeypatch) -> None:
    write(tmp_path, TWO)
    analyzed(tmp_path, trace, monkeypatch, None)
    code, out, err = run("status")
    check("the first scenario's report, by its path", code == 0
          and ".echolot/out/first_frame/report.json" in out, (out[-800:], err[-300:]))


# --- an investigation is about one scenario (#348) ----------------------------------

def traces_of(project: Path, *names: str) -> Path:
    """One old trace of each scenario named, where `collect` keeps them."""
    traces = project / ".echolot/traces"
    traces.mkdir(parents=True, exist_ok=True)
    for name in names:
        (traces / f"{name}_iter000.perfetto-trace").write_bytes(b"an earlier round")
    return traces


def test_hunt_opens_on_the_scenario_named_and_sets_aside_its_traces(tmp_path: Path,
                                                                    monkeypatch) -> None:
    from echolot import hunt as hunt_mod
    write(tmp_path, TWO)
    traces = traces_of(tmp_path, "first_frame", "loaded")
    monkeypatch.chdir(tmp_path)
    code, out, err = run("hunt", "--scenario", "loaded", "the feed got slower")
    check("opened", code == 0 and "opened #1" in out, err[-400:])
    check("on that scenario", hunt_mod.load(tmp_path)["scenario"] == "loaded",
          hunt_mod.load(tmp_path))
    left = sorted(p.name for p in traces.glob("*.perfetto-trace"))
    check("its previous traces set aside, the other scenario's left",
          left == ["first_frame_iter000.perfetto-trace"], left)
    check("and nothing said about a first scenario", "[i] scenario" not in err, err[-300:])


def test_hunt_without_a_scenario_takes_the_first_and_says_so(tmp_path: Path,
                                                             monkeypatch) -> None:
    from echolot import hunt as hunt_mod
    write(tmp_path, TWO)
    monkeypatch.chdir(tmp_path)
    code, _, err = run("hunt", "startup got slower")
    check("the first, said", code == 0 and hunt_mod.load(tmp_path)["scenario"] == "first_frame"
          and "[i] scenario first_frame, the first of 2" in err, err[-400:])


def test_hunt_refuses_a_scenario_it_cannot_measure(tmp_path: Path, monkeypatch) -> None:
    from echolot import hunt as hunt_mod
    write(tmp_path, TWO)
    traces = traces_of(tmp_path, "loaded")
    monkeypatch.chdir(tmp_path)
    code, _, err = run("hunt", "--scenario", "scroll", "the feed got slower")
    check("exit 2, in a sentence", code == 2 and "no such scenario" in err, err[-400:])
    check("nothing opened, nothing moved", hunt_mod.load(tmp_path) is None
          and (traces / "loaded_iter000.perfetto-trace").is_file())
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    code, _, err = run("hunt", "--scenario", "loaded", "a question")
    check("and with no config to name it in", code == 2 and "no echolot.yml" in err, err[-300:])


def test_inside_an_investigation_the_commands_take_its_scenario(tmp_path: Path, trace: Path,
                                                                monkeypatch) -> None:
    write(tmp_path, TWO)
    monkeypatch.chdir(tmp_path)
    run("hunt", "--scenario", "loaded", "the feed got slower")
    code, _, err = run("analyze", str(trace))
    rep = json.loads((tmp_path / ".echolot/out/loaded/report.json").read_text(encoding="utf-8"))
    check("analyze measured the investigation's scenario, and said so",
          code == 0 and rep["scenario"] == "loaded"
          and "[i] scenario loaded, the open investigation's" in err, err[-400:])
    code, out, _ = run("report", "--window")
    check("report reads its report", code == 0 and "Screen.loaded" in out, out[:400])
    code, _, err = run("analyze", str(trace), "--scenario", "first_frame")
    check("--scenario still wins", code == 0
          and (tmp_path / ".echolot/out/first_frame/report.json").is_file()
          and "the open investigation's" not in err, err[-400:])


def test_collect_inside_an_investigation_records_its_scenario(tmp_path: Path,
                                                              monkeypatch) -> None:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    write(tmp_path, {"project": {"package": "com.example.app"},
                     "scenarios": {"cold": {}, "warm": {"runner": {"iterations": 1}}},
                     "runner": {"iterations": 2}})
    monkeypatch.chdir(tmp_path)
    run("hunt", "--scenario", "warm", "warm start got slower")
    code, _, err = run("collect")
    names = sorted(p.name for p in (tmp_path / ".echolot/traces").glob("*.perfetto-trace"))
    check("the investigation's traces", code == 0
          and names == ["warm_iter000.perfetto-trace"], (names, err[-400:]))


def test_a_concluded_investigation_picks_no_scenario(tmp_path: Path, trace: Path,
                                                     monkeypatch) -> None:
    write(tmp_path, TWO)
    monkeypatch.chdir(tmp_path)
    run("hunt", "--scenario", "loaded", "the feed got slower")
    run("hunt", "--done", "a lock in the feed's repository")
    code, _, err = run("analyze", str(trace))
    check("the first again", code == 0 and "[i] scenario first_frame, the first of 2" in err
          and (tmp_path / ".echolot/out/first_frame/report.json").is_file(), err[-400:])


def test_drift_names_a_scenario_the_config_no_longer_has() -> None:
    from echolot import hunt as hunt_mod
    hunt = {"scenario": "feed_scroll", "status": "open"}
    said = hunt_mod.drift(hunt, {"config": {"scenario": "cold_start",
                                            "scenarios": ["cold_start", "checkout"]}})
    check("several: the one it measures is gone, and what there is", said
          == ["the config no longer has this investigation's scenario, feed_scroll: "
              "it has cold_start, checkout"], said)
    check("one of several that is still there is no drift", hunt_mod.drift(
        {"scenario": "checkout", "status": "open"},
        {"config": {"scenario": "cold_start", "scenarios": ["cold_start", "checkout"]}}) == [])
    said = hunt_mod.drift({"scenario": "coldStart", "status": "open"},
                          {"config": {"scenario": "firstLaunch", "scenarios": ["firstLaunch"]}})
    check("one scenario, renamed: said as before",
          said == ["the config's scenario changed: coldStart → firstLaunch"], said)


# --- status and the next step name the scenario (#349) ------------------------------

def a_report(project: Path, scenario: str) -> None:
    """A latest report of the scenario, where `analyze` would leave it."""
    out = project / ".echolot/out" / scenario
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({
        "generated_at": "2026-10-11T09:00:00+00:00", "scenario": scenario,
        "summary": {"detectors_fired": 4, "detectors_run": 13},
        "traces": ["a", "b", "c"], "config": {}}), encoding="utf-8")


def status_of(project: Path, monkeypatch) -> tuple[str, str]:
    """`echolot status`, and the next step it would print past the layer."""
    from echolot import state
    monkeypatch.chdir(project)
    code, out, err = run("status")
    check("status: exit 0", code == 0, err[-400:])
    st = state.project_state(project)
    st["layer_verdict"] = "current"
    return out, state.next_step(st)


def test_status_lists_each_scenario_with_its_traces_and_report(tmp_path: Path,
                                                                monkeypatch) -> None:
    write(tmp_path, TWO)
    traces_of(tmp_path, "first_frame", "loaded")
    (tmp_path / ".echolot/traces/loaded_iter001.perfetto-trace").write_bytes(b"x")
    a_report(tmp_path, "first_frame")
    out, step = status_of(tmp_path, monkeypatch)
    check("the config counts its scenarios", "echolot.yml · 2 scenarios" in out, out)
    check("each scenario on a line, with its traces and its report",
          "scenario  first_frame · 1 trace(s) · .echolot/out/first_frame/report.json" in out
          and "3 run(s) · 4 of 13 detectors fired" in out
          and "scenario  loaded · 2 trace(s) · no report yet" in out, out)
    check("and no report line of one scenario standing for all", "\nreport " not in out, out)
    check("the next step is the first scenario's, by name",
          "--scenario first_frame" in step
          and ".echolot/traces/first_frame_iter000.perfetto-trace" in step
          and "loaded_iter" not in step, step)


def test_status_and_the_next_step_follow_the_open_investigation(tmp_path: Path,
                                                                monkeypatch) -> None:
    write(tmp_path, TWO)
    traces_of(tmp_path, "first_frame")
    monkeypatch.chdir(tmp_path)
    run("hunt", "--scenario", "loaded", "the feed got slower")
    traces_of(tmp_path, "loaded")
    out, step = status_of(tmp_path, monkeypatch)
    check("the investigation names its scenario", '"the feed got slower" · scenario loaded' in out,
          out)
    check("its scenario is marked", "loaded · 1 trace(s) · no report yet  ← the open investigation's"
          in out, out)
    check("the next step is its scenario's", "--scenario loaded" in step
          and ".echolot/traces/loaded_iter000.perfetto-trace" in step, step)


def test_the_next_step_with_no_traces_records_the_scenario(tmp_path: Path, monkeypatch) -> None:
    write(tmp_path, TWO)
    _, step = status_of(tmp_path, monkeypatch)
    check("collect, named", "echolot collect -c echolot.yml -n 5 --scenario first_frame" in step,
          step)


def test_one_scenario_status_reads_as_before(tmp_path: Path, monkeypatch) -> None:
    write(tmp_path, {"project": PROJECT, "scenario": {"name": "fixture"}})
    traces_of(tmp_path, "fixture")
    out, step = status_of(tmp_path, monkeypatch)
    check("the config names its scenario, and the report has its own line",
          "echolot.yml · scenario fixture" in out and "\nreport  none yet" in out
          and "\nscenario " not in out, out)
    check("and the next step names none", "--scenario" not in step, step)


def test_the_line_hunt_ends_with_names_the_scenario(tmp_path: Path, monkeypatch) -> None:
    write(tmp_path, TWO)
    monkeypatch.chdir(tmp_path)
    _, _, err = run("hunt", "--scenario", "loaded", "the feed got slower")
    check("both commands carry it", "echolot collect -c echolot.yml -n 5 --scenario loaded" in err
          and "-c echolot.yml --scenario loaded" in err, err[-600:])


# --- thresholds per scenario (#351) -----------------------------------------

def with_thresholds(top: dict, own: dict) -> dict:
    """TWO, with `detectors:` at the top and `loaded`'s own section."""
    raw = json.loads(json.dumps(TWO))
    raw["detectors"] = top
    raw["scenarios"]["loaded"]["detectors"] = own
    return raw


def test_a_scenarios_thresholds_are_laid_over_the_configs() -> None:
    cfg = config(with_thresholds(
        {"main_thread_block": {"min_slice_ms": 30},
         "binder_txn": {"min_txn_ms": 12, "skip_glob": "*x*"}, "frame_jank": False},
        {"binder_txn": {"min_txn_ms": 7}, "gc_pressure": False,
         "frame_jank": {"min_frames": 2}, "main_thread_block": None}))
    check("the first scenario runs on the config's",
          cfg.detector_overrides == {"main_thread_block": {"min_slice_ms": 30},
                                     "binder_txn": {"min_txn_ms": 12, "skip_glob": "*x*"}}
          and cfg.disabled_detectors == {"frame_jank"} and cfg.scenario_overrides == {},
          (cfg.detector_overrides, cfg.disabled_detectors))
    cfg.select("loaded")
    over = cfg.detector_overrides
    check("a threshold at a time: the scenario's number, the config's mask",
          over["binder_txn"] == {"min_txn_ms": 7, "skip_glob": "*x*"}, over)
    check("an entry with nothing under it adds nothing",
          over["main_thread_block"] == {"min_slice_ms": 30}, over)
    check("thresholds turn on what the top turned off, and `false` turns off for it alone",
          over["frame_jank"] == {"min_frames": 2} and cfg.disabled_detectors == {"gc_pressure"},
          (over, cfg.disabled_detectors))
    check("and what it sets itself is known apart",
          cfg.scenario_overrides == {"binder_txn": {"min_txn_ms": 7},
                                     "frame_jank": {"min_frames": 2}}, cfg.scenario_overrides)


def test_one_scenario_keeps_its_thresholds_at_the_top(tmp_path: Path) -> None:
    raw = {"project": PROJECT, "detectors": {"main_thread_block": {"min_slice_ms": 30}},
           "scenario": {"name": "cold", "end": {"name": "Screen.loaded"}}}
    cfg = Config.load(write(tmp_path, raw))
    check("the top is its own", cfg.detector_overrides == {"main_thread_block": {"min_slice_ms": 30}}
          and cfg.scenario_overrides == {}, cfg.detector_overrides)
    raw["scenario"]["detectors"] = {"main_thread_block": {"min_slice_ms": 5}}
    with pytest.raises(ConfigError, match="keeps its thresholds in the top-level `detectors:`"):
        Config.load(write(tmp_path, raw))


def test_a_detector_no_scenario_has_is_refused_wherever_it_is_named() -> None:
    from echolot.main import plan_detectors
    cfg = config(with_thresholds({}, {"frame_jnk": False}))
    with pytest.raises(ConfigError, match=r"scenarios\.loaded\.detectors: no detector 'frame_jnk'"):
        plan_detectors(cfg)


def test_params_source_says_whose_the_thresholds_are() -> None:
    from echolot.main import plan_detectors
    cfg = config(with_thresholds(
        {"main_thread_block": {"min_slice_ms": 30}, "binder_txn": {"skip_glob": "*x*"}},
        {"binder_txn": {"min_txn_ms": 7}, "io_wait": {"min_io_wait_ms": 25}})).select("loaded")
    plan = {d.id: (ov, source) for d, ov, source in
            plan_detectors(cfg, cli_overrides={"io_wait": {"min_io_wait_ms": 5}})}
    check("the config's, the config's and the scenario's, the scenario's and --set's",
          (plan["main_thread_block"][1], plan["binder_txn"][1], plan["io_wait"][1])
          == ("config", "config+scenario", "scenario+cli"), plan)
    check("--set still wins", plan["io_wait"][0] == {"min_io_wait_ms": 5}, plan["io_wait"])
    check("and the first scenario's are the config's alone",
          {source for _, _, source in plan_detectors(cfg.select("first_frame"))}
          == {"config", "default"})

    bad = config(with_thresholds({}, {"binder_txn": {"min_txn_ms": "7ms"}})).select("loaded")
    with pytest.raises(ConfigError, match=r"from scenarios\.loaded\.detectors"):
        plan_detectors(bad)


def test_analyze_says_the_thresholds_were_the_scenarios(tmp_path: Path, trace: Path) -> None:
    path = write(tmp_path, with_thresholds({}, {"main_thread_block": {"min_slice_ms": 5}}))
    out = tmp_path / "out"
    code, _, err = run("analyze", str(trace), "-c", str(path), "--scenario", "loaded",
                       "-o", str(out))
    check("exit 0", code == 0, err[-600:])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    block = next(d for d in report["detectors"] if d["id"] == "main_thread_block")
    check("the detector's thresholds are the scenario's, with the shipped ones beside them",
          block["params_source"] == "scenario" and block["params"]["min_slice_ms"] == 5
          and block["defaults"] == {"min_slice_ms": 16}, block)
    header = (out / "report.md").read_text(encoding="utf-8")
    check("and the header names the scenario", "thresholds: scenario `loaded`'s own for 1 of"
          in header, header[:1200])


def test_calibrate_prints_the_scenarios_own_section(tmp_path: Path, trace: Path) -> None:
    """The section goes under the scenario. A number at the top that its runs
    gave nothing to set against does not reach it: the cold start of #351's
    spike would have run on the first launch's 2.8 instead of the default 40."""
    raw = with_thresholds(
        {"main_thread_outlier": {"min_abs_ms": 2.8}, "frame_jank": False,
         "binder_txn": {"skip_glob": "*async*x"}},
        {"binder_txn": {"min_txn_ms": 7}, "gc_pressure": False,
         "repeated_work": {"max_callers": 5}})
    path = write(tmp_path, raw)
    code, out, err = run("calibrate", str(trace), "-c", str(path), "--scenario", "loaded")
    check("exit 0", code == 0, err[-600:])
    check("under the scenario's name", "scenarios:\n  loaded:\n    detectors:\n" in out, out)
    check("a number the runs could not give is the default, over the top's, said so",
          "        min_abs_ms: 40    # kept the default over the 2.8 in detectors:" in out, out)
    check("the scenario's own value is kept, its own settings carried",
          "        min_txn_ms: 7    # kept the scenario's value" in out
          and "        max_callers: 5    # from the scenario" in out, out)
    check("its `false` stands, the top's is said and not measured",
          "      gc_pressure: false" in out
          and "      # frame_jank: turned off in detectors:, so not measured" in out, out)
    check("and the top's mask stays at the top", "*async*x" not in out, out)

    pasted = yaml.safe_load(out)["scenarios"]["loaded"]["detectors"]
    raw["scenarios"]["loaded"]["detectors"] = pasted
    cfg = config(raw)
    check("pasted, the first scenario keeps the top's number",
          cfg.detector_overrides["main_thread_outlier"] == {"min_abs_ms": 2.8},
          cfg.detector_overrides)
    cfg.select("loaded")
    check("and the scenario runs on its own",
          cfg.detector_overrides["main_thread_outlier"] == {"min_abs_ms": 40}
          and cfg.detector_overrides["binder_txn"] == {"skip_glob": "*async*x", "min_txn_ms": 7}
          and cfg.disabled_detectors == {"frame_jank", "gc_pressure"}, cfg.detector_overrides)


def test_status_says_which_scenario_has_thresholds_of_its_own(tmp_path: Path,
                                                               monkeypatch) -> None:
    write(tmp_path, with_thresholds({}, {"main_thread_block": {"min_slice_ms": 5}}))
    out, _ = status_of(tmp_path, monkeypatch)
    check("the config's own are none", "thresholds built-in defaults" in out, out)
    check("the scenario's line says it has its own",
          "scenario  loaded · own thresholds · 0 trace(s)" in out
          and "scenario  first_frame · 0 trace(s)" in out, out)


def test_reflect_knows_the_scenarios_and_their_thresholds() -> None:
    from echolot.reflect import facts, render
    cfg = config(with_thresholds({"main_thread_block": {"min_slice_ms": 30}},
                                 {"main_thread_block": {"min_slice_ms": 5}}))
    snap = facts.config_snapshot(cfg)
    check("the names, the top's thresholds, and the ones each scenario sets itself",
          snap["scenarios"] == ["first_frame", "loaded"]
          and snap["detector_overrides"] == {"main_thread_block": {"min_slice_ms": 30}}
          and snap["scenario_overrides"] == {"loaded": {"main_thread_block": {"min_slice_ms": 5}}},
          snap)
    check("and its config line names them",
          "scenarios `first_frame`, `loaded`" in render._config_line(snap),
          render._config_line(snap))
