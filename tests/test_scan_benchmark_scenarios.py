#!/usr/bin/env python3
"""`echolot scan` drafts a scenario for each benchmark class (#517).

A benchmark module with a startup class and a scroll class was summed up as
"drives com.example.app, a cold start", and the draft had one scenario, the
start: the scroll was listed as a test and left. Worse, a scroll that passes
`startupMode = StartupMode.COLD` and names a section was taken for the cold
start, its section the end of a window from bindApplication. A team whose
question was scroll jank then recorded its traces with its own scripts,
inside an investigation whose scenario was the start.

Here: several classes are one scenario each under `scenarios:`, named after
the class, each with its own class filter laid over one gradle runner; a
class that measures frames gets the whole trace for a window, and its draft
says that the sections the test writes are the benchmark's process, not the
app's; and the drafted config records one scenario in gradle mode and
analyses it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, scan  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import no_adb  # noqa: E402,F401
from tests.test_scan import repo  # noqa: E402
from tests.test_scan_benchmark_kinds import (  # noqa: E402
    BENCH_DIR,
    FRAMES,
    LAUNCH,
    SCROLL,
    project,
)
from tests.test_scan_startup_benchmark import HEAD, LAUNCH_WINDOW, PLAIN_APP, TAIL  # noqa: E402


def draft(root: Path, **classes: str) -> str:
    return "\n".join(scan.skeleton(scan.describe(project(root, **classes), devices=False)))


ARG = "-Pandroid.testInstrumentationRunnerArguments.class=com.example.benchmark."

SCROLL_WINDOW = (
    '    window: {name: whole, _source: derived, _evidence: "FrameTimingMetric in '
    f"{BENCH_DIR}/ListScrollBenchmark.kt, and no StartupTimingMetric: the benchmark measures "
    "the frames of its measure block, not the launch, and each trace it writes holds one "
    "repeat of that block, so the window is the whole trace; `phase_scroll`, `settle`, "
    "`Phases.FLING_BACK`, which the test writes itself, are slices of the benchmark's own "
    "process, `com.example.benchmark`, not of the app's, which a window is read from\"}\n")

TWO = (HEAD.replace("scenario:\n", "scenarios:\n")
       + "  StartupBenchmark:\n"
       + LAUNCH_WINDOW.replace("  window:", "    window:")
       + "    runner:\n      gradle_args:\n"
       + f'        - "{ARG}StartupBenchmark"\n'
       + "  ListScrollBenchmark:\n"
       + SCROLL_WINDOW
       + "    runner:\n      gradle_args:\n"
       + f'        - "{ARG}ListScrollBenchmark"\n'
       + "\nrunner:\n"
       + "  mode: gradle\n"
       + '  gradle_task: ":benchmark:connectedBetaBenchmarkAndroidTest"\n'
       + '  _evidence: "MacrobenchmarkRule in 2 classes of :benchmark: each scenario above '
         'runs its own; the module declares its own variants"\n'
       + TAIL)


# --- the draft ----------------------------------------------------------------------

def test_a_startup_class_and_a_scroll_class_are_two_scenarios(tmp_path: Path) -> None:
    # ListScrollBenchmark.kt is read first, and it starts cold and names a
    # section: the one class the draft used to take.
    text = draft(tmp_path, ListScrollBenchmark=SCROLL, StartupBenchmark=LAUNCH)
    check("the whole draft: the start first with the launch, then the scroll with the whole "
          "trace, each with its own filter, under one runner", text == TWO, text)
    check("the scroll's section is no end of any window",
          'end: {name: "' not in text and "bindApplication" not in text, text)


def test_the_drafted_scenarios_load_and_each_runs_its_own_class(tmp_path: Path) -> None:
    path = tmp_path / "echolot.yml"
    path.write_text(draft(tmp_path / "project", ListScrollBenchmark=SCROLL,
                          StartupBenchmark=LAUNCH) + "\n", encoding="utf-8")
    cfg = Config.load(path)
    check("two scenarios, the start the one taken by default",
          (cfg.scenarios, cfg.scenario_name) == (["StartupBenchmark", "ListScrollBenchmark"],
                                                 "StartupBenchmark"), cfg.raw)
    for name, window in (("StartupBenchmark", "launch"), ("ListScrollBenchmark", "whole")):
        one = Config.load(path, scenario=name)
        check(f"{name}: its window, the module's task, and its own class alone",
              (one.scenario_window, one.runner["mode"], one.runner["gradle_task"],
               one.runner["gradle_args"])
              == (window, "gradle", ":benchmark:connectedBetaBenchmarkAndroidTest",
                  [ARG + name]), one.runner)


def test_one_scroll_class_is_one_scenario_with_the_whole_trace(tmp_path: Path) -> None:
    text = draft(tmp_path, ScrollBenchmark=FRAMES)
    check("`scenario:` as for any one class, with no end to pick",
          "\nscenario:\n  name: scrollFeed\n  window: {name: whole, _source: derived" in text
          and '"?"' not in text and "scenarios:" not in text, text)
    check("and its filter at the top, as before",
          f'  gradle_args:\n    - "{ARG}ScrollBenchmark"' in text, text)


def test_two_classes_of_one_name_get_two_names(tmp_path: Path) -> None:
    root = project(tmp_path, StartupBenchmark=LAUNCH)
    other = root / "benchmark/src/main/java/com/example/benchmark/cold/StartupBenchmark.kt"
    other.parent.mkdir()
    other.write_text(LAUNCH.replace("package com.example.benchmark",
                                    "package com.example.benchmark.cold"), encoding="utf-8")
    text = "\n".join(scan.skeleton(scan.describe(root, devices=False)))
    check("the second numbered, each with its own class",
          "\n  StartupBenchmark:\n" in text and "\n  StartupBenchmark_2:\n" in text
          and f'"{ARG}cold.StartupBenchmark"' in text and f'"{ARG}StartupBenchmark"' in text,
          text)
    path = tmp_path / "echolot.yml"
    path.write_text(text + "\n", encoding="utf-8")
    check("and the config loads", Config.load(path).scenarios
          == ["StartupBenchmark", "StartupBenchmark_2"])


# --- recorded by the benchmark, one scenario at a time -----------------------------------

@pytest.mark.usefixtures("no_adb")
def test_the_scroll_scenario_records_its_class_and_analyses_the_whole_trace(
        tmp_path: Path, capsys) -> None:
    """`collect --scenario` in gradle mode, then `analyze`, on the draft as `scan` wrote it.

    The benchmark here is a stand-in for `./gradlew` that keeps the class
    filter it was given and writes a trace of the app scrolling, beside a
    benchmark's runner, as a frame-timing macrobenchmark's is.
    """
    root = tmp_path / "project"
    root.mkdir()
    repo(root, PLAIN_APP)
    (root / "benchmark/build.gradle").write_text(
        "plugins { id 'com.android.test' }\nandroid { namespace = 'com.example.app.benchmark' }\n",
        encoding="utf-8")
    bench = root / BENCH_DIR
    (bench / "StartupBenchmark.kt").write_text(
        LAUNCH.replace("com.example.app.beta", "com.example.app"), encoding="utf-8")
    (bench / "ListScrollBenchmark.kt").write_text(
        SCROLL.replace("com.example.app.beta", "com.example.app"), encoding="utf-8")
    config = root / "echolot.yml"
    config.write_text("\n".join(scan.skeleton(scan.describe(root, devices=False))) + "\n",
                      encoding="utf-8")

    written = tmp_path / "recorded.bin"
    written.write_bytes(fixture.journey())
    asked = tmp_path / "asked.txt"
    wrapper = root / "gradlew"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{asked}"\n'
        'd="benchmark/build/outputs/connected_android_test_additional_output/benchmark/connected/Pixel"\n'
        'mkdir -p "$d"\n'
        f'cp "{written}" "$d/ListScrollBenchmark_scroll_iter000.perfetto-trace"\n',
        encoding="utf-8")
    wrapper.chmod(0o755)

    with recorder.isolated():
        code = main(["collect", "-c", str(config), "--scenario", "ListScrollBenchmark"])
        _, err = capsys.readouterr()
        check("collect: exit 0", code == 0, err[-800:])
        args = asked.read_text(encoding="utf-8").split()
        check("gradle ran the scroll class alone",
              f"{ARG}ListScrollBenchmark" in args and f"{ARG}StartupBenchmark" not in args, args)
        traces = sorted((root / ".echolot" / "traces").glob("*.perfetto-trace"))
        check("its trace gathered under the scenario's name",
              [p.name for p in traces] == ["ListScrollBenchmark_iter000.perfetto-trace"], traces)
        code = main(["analyze", "-c", str(config), "--scenario", "ListScrollBenchmark",
                     "-o", str(tmp_path / "out"), *map(str, traces)])
        _, err = capsys.readouterr()
        check("analyze: exit 0", code == 0, err[-800:])
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    window = report["window"]
    check("the scenario the draft named, over the whole trace",
          (report["scenario"], window["defined_by"]) == ("ListScrollBenchmark", "whole"),
          {k: report.get(k) for k in ("scenario", "window")})
    # The benchmark's own process begins with the app's package, and no
    # manifest declares another: the draft named the app's exactly (#467).
    check("of the app's process, with nothing else matched and nothing said of it",
          (window["process"], window.get("process_alternatives")) == ("com.example.app", None)
          and "matched 2 processes" not in err, (window, err[-800:]))
