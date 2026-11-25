#!/usr/bin/env python3
"""The whole trace as the window, and a check that could not look at it (#519).

A recording made outside the config's scenarios — a scroll recorded by hand,
a teammate's trace — was measured by letting the scenario's anchors miss.
The report opened with a warning that its numbers were not about the
scenario, under the scenario's name, and `app_init` read "silent" on a trace
with no launch in it. `analyze --whole-trace` and `window: whole` take the
trace itself as the window and say so; a detector whose window holds nothing
it reads says why it did not look. The rule on the fixture is the
self-check's; here: the config, the command, the run log, `compare`, and the
words.
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

from tests.support import check  # noqa: E402

from echolot import fixture, needs, plain, recorder, selftest  # noqa: E402
from echolot import compare as compare_mod  # noqa: E402
from echolot import launch as launch_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.config import NO_ANCHOR, Config, ConfigError  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import report_page  # noqa: E402

PROJECT = {"package": "com.example.app", "process": "com.example.app"}


def _cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --- the config ---------------------------------------------------------------------

def _window(scenario: dict) -> str | None:
    return Config({"project": PROJECT, "scenario": scenario}).scenario_window


def test_window_whole_is_a_window_with_no_anchor() -> None:
    cfg = Config({"project": PROJECT, "scenario": {"name": "adhoc", "window": "whole"}})
    check("the word", cfg.scenario_window == "whole")
    check("and no anchors", (cfg.scenario_start, cfg.scenario_end) == (NO_ANCHOR, NO_ANCHOR))
    check("under its own name", cfg.scenario_name == "adhoc")


def test_window_whole_beside_an_anchor_or_a_limit_is_refused() -> None:
    with pytest.raises(ConfigError) as e:
        _window({"window": "whole", "end": "Screen.loaded"})
    check("an anchor: two answers to one question", "keep one" in str(e.value)
          and "where the recording does" in str(e.value), e.value)
    with pytest.raises(ConfigError) as e:
        _window({"window": "whole", "budget_ms": 800})
    check("a time limit on the recording's length says nothing about the app",
          "`budget_ms`" in str(e.value) and "name `start` and `end`" in str(e.value), e.value)
    with pytest.raises(ConfigError) as e:
        _window({"window": "trace"})
    check("a window that is none names both it takes",
          "`launch`" in str(e.value) and "`whole`" in str(e.value), e.value)


@pytest.mark.parametrize("config", [
    {**selftest.FIXTURE_CONFIG, "detectors": {"main_thread_block": {"min_slice_ms": 30}}},
    {"project": PROJECT, "detectors": {"main_thread_block": {"min_slice_ms": 30}},
     "scenarios": {"cold": {"start": "AppStart", "end": "Screen.firstFrame", "budget_ms": 900,
                            "detectors": {"main_thread_block": {"min_slice_ms": 5}}},
                   "feed": {"start": "Feed.open", "end": "Feed.shown"}}},
])
def test_whole_trace_is_a_scenario_of_its_own(config: dict) -> None:
    cfg = Config(config)
    cfg.whole_trace = True
    check("called `whole-trace`", cfg.scenario_name == "whole-trace")
    check("its window the whole trace, with no anchor to look for",
          (cfg.scenario_window, cfg.scenario_start, cfg.scenario_end)
          == ("whole", NO_ANCHOR, NO_ANCHOR))
    check("no limit", cfg.scenario_budget_ms is None)
    check("the config's top-level thresholds, none of a scenario's",
          cfg.detector_overrides == {"main_thread_block": {"min_slice_ms": 30}},
          cfg.detector_overrides)
    check("and a directory no scenario's latest report is in",
          cfg.report_dir == ".echolot/out/whole-trace", cfg.report_dir)


# --- the command --------------------------------------------------------------------

def _project(tmp_path: Path, monkeypatch, config: dict, build=fixture.build) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    (tmp_path / "echolot.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    trace = tmp_path / "scroll.perfetto-trace"
    trace.write_bytes(build())
    return trace


def test_analyze_whole_trace_measures_the_trace_and_says_so(tmp_path: Path, monkeypatch) -> None:
    # A config whose scenario is a cold start, and a recording of no launch.
    config = {**selftest.FIXTURE_CONFIG,
              "scenario": {"name": "coldStart", "start": "bindApplication", "end": "Screen.loaded"}}
    trace = _project(tmp_path, monkeypatch, config, fixture.pool)
    code, out, err = _cli("analyze", str(trace), "--whole-trace", "--full")
    check("exit 0", code == 0, err[-800:])
    check("nothing is said about an anchor", "anchor" not in (out + err).lower(), (out + err)[:1500])
    report = json.loads((tmp_path / ".echolot/out/whole-trace/report.json").read_text())
    check("its own directory, and the scenario's latest report is not touched",
          not (tmp_path / ".echolot/out/report.json").exists())
    check("named `whole-trace`, its window the whole trace",
          (report["scenario"], report["window"]["defined_by"]) == ("whole-trace", "whole"),
          report["window"])
    check("the header says what the window is",
          "Scenario: `whole-trace`" in out and "The window is the whole trace" in out
          and "Scenario window" not in out, out[:1200])
    app_init = next(d for d in report["detectors"] if d["id"] == "app_init")
    check("`app_init` did not look, and says why",
          app_init["checked"] is False and "no start of the app's process" in app_init["reason"],
          app_init)
    check("and is not called silent",
          "app_init" not in next(ln for ln in out.splitlines() if ln.startswith("**Silent:**")))
    runs = recorder.read(tmp_path / recorder.LOG_FILE)
    facts = runs[-1]["facts"]
    check("the run log keeps how the window was defined, and the scenario's name",
          (facts.get("window_defined_by"), facts.get("scenario")) == ("whole", "whole-trace"), facts)


def test_whole_trace_and_a_scenario_are_two_answers(tmp_path: Path, monkeypatch) -> None:
    trace = _project(tmp_path, monkeypatch, selftest.FIXTURE_CONFIG)
    code, _, err = _cli("analyze", str(trace), "--whole-trace", "--scenario", "fixture")
    check("refused in a sentence, before any trace is read",
          code == 2 and "--whole-trace and --scenario fixture" in err
          and "keep one" in err and "Traceback" not in err, err[-600:])
    check("and nothing written", not (tmp_path / ".echolot/out").exists())


def test_compare_refuses_the_whole_trace_against_a_scenarios_window(tmp_path: Path,
                                                                     monkeypatch) -> None:
    trace = _project(tmp_path, monkeypatch, selftest.FIXTURE_CONFIG)
    for argv in (("-o", "anchored"), ("--whole-trace", "-o", "whole"),
                 ("--whole-trace", "-o", "again")):
        code, _, err = _cli("analyze", str(trace), *argv)
        check(f"analyze {' '.join(argv)}: exit 0", code == 0, err[-600:])
    code, out, err = _cli("compare", "anchored/report.json", "whole/report.json")
    check("exit 2, the window said first and how to make the two alike",
          code == 2 and "the windows were defined differently" in err
          and "as the whole trace" in err and "both with `--whole-trace`" in err
          and "two scenarios" not in err, err[-700:])
    check("and no table", "| " not in out, out[:300])
    code, out, err = _cli("compare", "whole/report.json", "again/report.json")
    check("two whole traces are compared", code == 0 and "Traceback" not in err,
          (out[-300:], err[-500:]))


# --- what is said -------------------------------------------------------------------

def _det(did: str, reason: str) -> dict:
    return {"id": did, "rows": [], "error": None, "checked": False, "reason": reason}


def test_a_window_reason_is_said_apart_from_a_recordings() -> None:
    disk = needs.SOURCES["platform_state"].missing(needs.Probe(None))
    line = needs.line([_det("io_wait", disk), _det("app_init", needs.NO_PROCESS_START)])
    first, second = line.split("\n")
    check("the recording's is a warning: another recording lets it look",
          first.startswith("> ⚠️ 1 detector(s) could not run") and "`io_wait`" in first
          and "app_init" not in first, first)
    check("the window's is not: no recording of this window would",
          second.startswith("> 1 detector(s) did not look, because what they read is not "
                            "in this window") and "`app_init`" in second, second)
    alone = needs.line([_det("app_init", needs.NO_PROCESS_START)])
    check("alone, it is the one line", alone == second, alone)
    merged = needs.merged([{"checked": False, "reason": needs.NO_PROCESS_START},
                           {"checked": True}, {"checked": True}])
    check("a set's reason, with how many recordings, is still the window's",
          needs.of_window(merged["reason"]) and "(1 of the 3 recordings)" in merged["reason"],
          merged)
    both = needs.verdict(("atrace:am", "process_start"),
                         {"atrace:am": "The recording did not ask for it.",
                          "process_start": needs.NO_PROCESS_START})
    check("a recording without `am` says that first, and is the recording's",
          both["reason"].startswith("The recording did not ask")
          and not needs.of_window(both["reason"]), both)


def _whole_report(ms: float = 15000.0) -> dict:
    return {"scenario": "whole-trace", "trace": f"{ms}.perfetto-trace", "detectors": [],
            "summary": {"absent_ids": []},
            "window": {"duration_ms": ms, "process": PROJECT["process"], "defined_by": "whole",
                       "start_anchor": None, "end_anchor": None}}


def test_the_whole_trace_is_the_recording_in_words() -> None:
    rep = _whole_report()
    v = plain.verdict(rep)
    check("its length is the recording's, and nothing to judge it against",
          v["text"].startswith("The recording takes 15.0 s.") and v["warning"] is None, v)
    check("it does not end where a scenario does", plain._closed(rep["window"]) is False)
    check("the page asks about the trace, not about a scenario's time",
          report_page.question(rep).startswith("The whole trace runs 15,000 ms, and no scenario "
                                                "cuts it."), report_page.question(rep))
    head = "\n".join(render_mod._header_lines(rep))
    check("the overview calls it the whole trace, once",
          "window **15000.0 ms**" in head and head.count("whole trace") == 1, head[:800])
    merged = report_mod.aggregate([_whole_report(), _whole_report(16000.0)])["window"]
    check("a set of them is one definition, with nothing of a launch",
          merged["defined_by"] == "whole" and "report_fully_drawn" not in merged, merged)
    check("and against the launch it is not one window",
          "as the whole trace" in (launch_mod.differ(
              {"window": {"defined_by": "launch"}}, rep) or ""))


def test_a_window_whose_anchors_missed_is_not_called_the_whole_trace() -> None:
    w = {"duration_ms": 900.0, "end_anchor": {"glob": "Screen.loaded", "matches": 0}}
    from echolot import state
    said = state.window_warning(w)
    check("status says what it runs over instead", "whole trace" not in said
          and "everything the process did" in said, said)
    cmp = compare_mod._anchor_warning("before", w)[0]
    check("and so does compare", "whole trace" not in cmp["text"], cmp)


def test_analyze_whole_trace_from_a_subfolder_works_on_the_project(tmp_path: Path,
                                                                  monkeypatch) -> None:
    """From the build folder, `--whole-trace` reads the project's config above
    it and writes into that project, as any `analyze` does (#830)."""
    config = {**selftest.FIXTURE_CONFIG,
              "scenario": {"name": "coldStart", "start": "bindApplication", "end": "Screen.loaded"}}
    (tmp_path / ".git").mkdir()
    trace = _project(tmp_path, monkeypatch, config, fixture.pool)
    (tmp_path / "build/out").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "build/out")
    code, _, err = _cli("analyze", str(trace), "--whole-trace")
    check("exit 0, with the config above", code == 0
          and f"config: {(tmp_path / 'echolot.yml').resolve()}" in err, err[-800:])
    check("the report in the project",
          (tmp_path / ".echolot/out/whole-trace/report.json").is_file()
          and not (tmp_path / "build/out/.echolot").exists())
    runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("and its line in the project's log",
          runs and runs[-1]["facts"].get("scenario") == "whole-trace", runs[-1:])
