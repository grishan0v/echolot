#!/usr/bin/env python3
"""A set with a trace that gives no window (#466).

One trace of a startup macrobenchmark's fifteen carried the app's process
under a name cut to fifteen characters, and `analyze` stopped the whole set
on it with exit 2 and no report. Such a trace is left out and named while the
traces with a window are more than half of the set. The self-check holds the
rule on reports it makes up (`selftest`, "analyze leaves out of a set …");
here it is held on the fixture's traces, end to end: the launch window and an
anchored one, a mask with a wildcard and one without.
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

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, recorder  # noqa: E402
from echolot import launch as launch_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config, NoWindow  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import report_page  # noqa: E402

APP = "com.example.app"
EXACT = {"package": APP, "process": APP}
WILD = {"package": APP, "process": f"{APP}*"}
LAUNCH = {"name": "startup", "window": "launch"}
ANCHORED = {"name": "fixture", "start": {"name": "AppStart"}, "end": {"name": "Screen.firstFrame"}}


@pytest.fixture(scope="module")
def traces(tmp_path_factory) -> dict[str, Path]:
    """Three recordings with a window, each its own bytes (#565), and two
    whose app process came under a cut name beside the benchmark's own."""
    where = tmp_path_factory.mktemp("set")
    builds = {"startup_iter000": {}, "startup_iter001": {"composables": False},
              "startup_iter002": {"smooth": True},
              "startup_iter003": {"cut": True}, "startup_iter004": {"cut": True, "smooth": True}}
    out = {}
    for name, build in builds.items():
        out[name] = where / f"{name}.perfetto-trace"
        out[name].write_bytes(fixture.build(**build))
    return out


def _analyze(tmp_path: Path, project: dict, scenario: dict, *given: Path,
             tag: str = "out") -> tuple[int, str, dict | None, str]:
    # One file for one config: the report names the config it was read with.
    config = tmp_path / f"{'wild' if project is WILD else 'exact'}-{scenario['name']}.yml"
    config.write_text(yaml.safe_dump({"project": project, "scenario": scenario}), encoding="utf-8")
    out = tmp_path / tag
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(["analyze", *map(str, given), "-c", str(config), "-o", str(out)])
    if code:
        return code, err.getvalue(), None, ""
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    rep.pop("generated_at", None)
    return code, err.getvalue(), rep, (out / "report.md").read_text(encoding="utf-8")


def _names(entries: list[dict]) -> list[str]:
    return [Path(e["trace"]).name for e in entries]


# --- the launch window ------------------------------------------------------------

def test_the_launch_set_goes_on_without_the_trace_whose_process_is_not_the_apps(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    good = [traces["startup_iter000"], traces["startup_iter001"]]
    code, err, rep, md = _analyze(tmp_path, WILD, LAUNCH, *good, traces["startup_iter003"])
    check("the set is read", code == 0, err[-600:])
    check("over the other two", rep["runs"] == 2 and _names(rep["no_window"])
          == ["startup_iter003.perfetto-trace"], rep.get("no_window"))
    said = rep["no_window"][0]["said"]
    check("the sentence names the app, and the process the mask took in its place",
          said.startswith("no launch of com.example.app in trace startup_iter003.perfetto-trace: "
                          "`project.process` took com.example.app.benchmark in it, which is not "
                          "the app's own process, and com.example.app's process is not in this "
                          "trace under its name. The platform recorded com.example.app's launch"),
          said)
    check("on stderr", "[!] Left out: `startup_iter003.perfetto-trace`. It gives no window "
          "to read: no launch of com.example.app: `project.process` took" in err, err[-800:])
    head = md.split("\n## ", 1)[0]
    check("and in the header, under the runs", "Runs: **2**" in head
          and "> ⚠️ Left out: `startup_iter003.perfetto-trace`" in head, head)
    _, _, alone, alone_md = _analyze(tmp_path, WILD, LAUNCH, *good, tag="alone")
    check("the rest read as they read alone",
          {k: v for k, v in rep.items() if k != "no_window"} == alone,
          sorted(k for k in rep if rep.get(k) != alone.get(k)))
    check("to the byte, but for the line that says one was left out",
          "".join(ln for ln in md.splitlines(True) if "Left out" not in ln) == alone_md
          and "Left out" not in alone_md, md[:900])


def test_an_exact_mask_leaves_out_the_trace_with_no_process_of_its_name(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    """`scan` writes the exact name for a startup macrobenchmark whose own
    package begins with the app's (#467): the same trace then holds no
    process the mask matches, and that stopped the set before the launch
    was looked for."""
    code, err, rep, _ = _analyze(tmp_path, EXACT, LAUNCH, traces["startup_iter000"],
                                 traces["startup_iter001"], traces["startup_iter003"])
    check("left out", code == 0 and rep["runs"] == 2
          and _names(rep["no_window"]) == ["startup_iter003.perfetto-trace"], err[-600:])
    check("with what `_resolve_process` says of it, the cut name's mask included",
          rep["no_window"][0]["said"].startswith(
              "no process in trace startup_iter003.perfetto-trace matches project.process = "
              "'com.example.app'") and "'*com.example.app'" in rep["no_window"][0]["said"],
          rep["no_window"][0]["said"])


def test_a_launch_that_had_not_ended_and_a_trace_with_no_window_count_together(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    unfinished = tmp_path / "startup_iter005.perfetto-trace"
    unfinished.write_bytes(fixture.build(unfinished=True))
    code, err, rep, md = _analyze(tmp_path, WILD, LAUNCH, *(traces[f"startup_iter00{i}"]
                                                           for i in range(4)), unfinished)
    check("three of five: read", code == 0 and rep["runs"] == 3, err[-600:])
    check("each named under its own key", _names(rep["unfinished"]) == ["startup_iter005.perfetto-trace"]
          and _names(rep["no_window"]) == ["startup_iter003.perfetto-trace"], rep)
    check("each with its own line", "had not drawn its first frame" in md
          and "It gives no window to read" in md, md[:2000])
    code, err, _, _ = _analyze(tmp_path, WILD, LAUNCH, traces["startup_iter000"],
                               traces["startup_iter003"], unfinished, tag="few")
    check("one of three: refused, with the first one's reason",
          code == 2 and "error: 2 of the 3 traces give no window to read, and the one that does "
          "is not more than half of the set" in err
          and "The first: no launch of com.example.app in trace startup_iter003" in err
          and "To read the one with a window, name it alone." in err, err[-900:])


def test_too_few_left_is_refused_and_none_is_the_first_ones_sentence(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    code, err, _, _ = _analyze(tmp_path, WILD, LAUNCH, traces["startup_iter000"],
                               traces["startup_iter003"], tag="half")
    check("one of two: refused", code == 2 and "error: 1 of the 2 traces gives no window "
          "to read, and the one that does is not more than half of the set" in err, err[-600:])
    code, err, _, _ = _analyze(tmp_path, WILD, LAUNCH, traces["startup_iter003"],
                               traces["startup_iter004"], tag="none")
    code_one, err_one, _, _ = _analyze(tmp_path, WILD, LAUNCH, traces["startup_iter003"],
                                       tag="one")
    last = err.strip().splitlines()[-1]
    check("none of them: the first one's sentence, the same as given alone",
          code == code_one == 2 and last.startswith("error: no launch of com.example.app in trace "
                                                    "startup_iter003.perfetto-trace")
          and last == err_one.strip().splitlines()[-1], (err[-600:], err_one[-600:]))


def test_the_trace_alone_is_refused_by_analyze_trace(traces: dict[str, Path]) -> None:
    with pytest.raises(launch_mod.NoLaunch) as caught:
        analyze_trace(traces["startup_iter003"], Config({"project": WILD, "scenario": LAUNCH}))
    check("a NoWindow, which `analyze` leaves out of a set", isinstance(caught.value, NoWindow),
          type(caught.value))
    with pytest.raises(NoWindow):
        analyze_trace(traces["startup_iter003"], Config({"project": EXACT, "scenario": LAUNCH}))


# --- an anchored window ---------------------------------------------------------------

def test_an_anchored_set_leaves_out_the_trace_with_no_process_of_its_name(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    code, err, rep, _ = _analyze(tmp_path, EXACT, ANCHORED, traces["startup_iter000"],
                                 traces["startup_iter001"], traces["startup_iter003"])
    check("no process is no window, anchored or not", code == 0 and rep["runs"] == 2
          and _names(rep["no_window"]) == ["startup_iter003.perfetto-trace"], err[-600:])
    check("the anchors matched in both repeats read",
          rep["window"]["start_anchor"]["matches"] == 1 and "missed" not in rep["window"]["start_anchor"],
          rep["window"]["start_anchor"])


def test_an_anchored_set_keeps_the_trace_whose_anchors_missed(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    """With a wildcard the trace has a process, the benchmark's, and a window
    cut from its first and last slices: it stays in the set, and the report
    says the anchors missed in it and that the repeats measured two processes,
    as it did before. A missed anchor is the scenario's to explain, k of n,
    and not a recording that gave nothing to read."""
    code, err, rep, _ = _analyze(tmp_path, WILD, ANCHORED, traces["startup_iter000"],
                                 traces["startup_iter001"], traces["startup_iter003"])
    w = rep["window"] if rep else {}
    check("read, all three", code == 0 and rep["runs"] == 3 and "no_window" not in rep, err[-600:])
    check("the anchors said to have missed in one", (w["start_anchor"].get("missed"),
          w["end_anchor"].get("missed")) == ("1/3", "1/3"), w)
    check("and the two processes named", w.get("processes") == [APP, fixture.BENCHMARK_PROCESS], w)


# --- compare and the page -------------------------------------------------------------

def test_compare_and_the_page_say_the_set_is_smaller_than_recorded(
        tmp_path: Path, traces: dict[str, Path]) -> None:
    good = [traces["startup_iter000"], traces["startup_iter001"]]
    _, _, before, _ = _analyze(tmp_path, WILD, LAUNCH, *good, traces["startup_iter002"],
                               tag="before")
    _, _, after, _ = _analyze(tmp_path, WILD, LAUNCH, *good, traces["startup_iter003"],
                              traces["startup_iter004"], traces["startup_iter002"], tag="after")
    check("after: three of five", after["runs"] == 3 and len(after["no_window"]) == 2, after)
    warnings = {w["id"]: w["text"] for w in compare_mod.build(
        before, after, before_path="b.json", after_path="a.json")["warnings"]}
    check("compare names the two the after side left out",
          warnings.get("left-out-after") == (
              "The after report's medians are over 3 of the 5 recordings it was given. It left "
              "out `startup_iter003.perfetto-trace`, no window to read; "
              "`startup_iter004.perfetto-trace`, no window to read, and those are in neither "
              "its rows nor this comparison."), warnings)
    check("and nothing of the side that kept all", "left-out-before" not in warnings, warnings)
    notes = report_page.window_notes(after)
    check("the page says it beside the window's length",
          notes and notes[0].startswith("Left out: `startup_iter003.perfetto-trace`, "
                                        "`startup_iter004.perfetto-trace`. They give no window"),
          notes)
