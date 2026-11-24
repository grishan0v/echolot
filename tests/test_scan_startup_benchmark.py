#!/usr/bin/env python3
"""`echolot scan` drafts the launch scenario for a startup macrobenchmark (#458).

`scan` drafted `scenario.window: launch` only for a project with no
macrobenchmark (#446). The project best prepared for that scenario was left
with a question mark for an end: its benchmark already drives the launch and
writes one trace for each repeat, and the launch window reads such traces.

Here: the draft a class that measures the launch gets, that it loads, which
class is taken among several, and that the drafted config records in
`gradle` mode and analyses under the launch window. Every draft of one class
that is not that class's is held byte for byte to what it was before. Which
kind a class is, and what the description says of it, is in
`test_scan_benchmark_kinds.py`; several classes are a scenario each, in
`test_scan_benchmark_scenarios.py` (#517).
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
    EVERY,
    LAUNCH,
    MODE,
    NAMED_ONLY,
    PLAIN,
    SECTION,
    UNREAD_SECTION,
    project,
)


def draft(root: Path, **classes: str) -> str:
    return "\n".join(scan.skeleton(scan.describe(project(root, **classes), devices=False)))


# --- the draft, whole -------------------------------------------------------------

HEAD = """\
project:
  package: com.example.app.beta
  process: "com.example.app.beta*"
  _source: derived
  _evidence: "applicationId in app/build.gradle, variant betaBenchmark — the others: betaDebug, prodDebug, prodBenchmark, betaRelease, prodRelease"

scenario:
"""

TAIL = """\

instrumentation:
  allowed:
    - "app/src/main"
    - "core/src/main"
    - "feature/*/src/main"
  temp_prefix: AGENTTMP_
  cleanup: always"""

QUESTION = """\
  start: {name: "bindApplication", _source: default}
  end: {name: "?", _source: default, _evidence: "pick from `echolot probe` — the longest slice that means ready"}
"""


def _gradle(cls: str) -> str:
    """The runner block of a benchmark's draft, the same for every kind."""
    return ("\nrunner:\n"
            "  mode: gradle\n"
            '  gradle_task: ":benchmark:connectedBetaBenchmarkAndroidTest"\n'
            "  gradle_args:\n"
            f'    - "-Pandroid.testInstrumentationRunnerArguments.class=com.example.benchmark.{cls}"\n'
            f'  _evidence: "MacrobenchmarkRule in {BENCH_DIR}/{cls}.kt; '
            'the module declares its own variants"\n')


LAUNCH_WINDOW = (
    '  window: {name: launch, _source: derived, _evidence: "StartupTimingMetric in '
    f"{BENCH_DIR}/StartupBenchmark.kt: the benchmark measures the launch, and the runner "
    "below runs it; the trace decides the end: the launch's end as the platform records it, "
    "or the app's reportFullyDrawn when that comes later, and the report says which "
    '(window.defined_by)"}\n')

LAUNCH_DRAFT = HEAD + "  name: startup\n" + LAUNCH_WINDOW + _gradle("StartupBenchmark") + TAIL


def test_a_startup_benchmark_gets_the_launch_window(tmp_path: Path) -> None:
    text = draft(tmp_path, StartupBenchmark=LAUNCH)
    check("the whole draft: the launch for a window, and the gradle runner it had",
          text == LAUNCH_DRAFT, text)
    lines = text.splitlines()
    check("no anchor beside the window, and no question mark",
          not [line for line in lines if line.startswith(("  start:", "  end:"))]
          and '"?"' not in text, text)
    window = next(line for line in lines if line.startswith("  window:"))
    check("the evidence names the file and the metric, as a derived value",
          "_source: derived" in window and "StartupTimingMetric in " in window
          and f"{BENCH_DIR}/StartupBenchmark.kt" in window, window)


def test_the_drafted_config_loads(tmp_path: Path) -> None:
    path = tmp_path / "echolot.yml"
    path.write_text(draft(tmp_path / "project", StartupBenchmark=LAUNCH) + "\n", encoding="utf-8")
    cfg = Config.load(path)
    check("the scenario, its window and its runner",
          (cfg.scenario_name, cfg.scenario_window, cfg.runner["mode"], cfg.runner["gradle_task"])
          == ("startup", "launch", "gradle", ":benchmark:connectedBetaBenchmarkAndroidTest"),
          cfg.raw)
    check("with the class the window was read from as the filter",
          cfg.runner["gradle_args"]
          == ["-Pandroid.testInstrumentationRunnerArguments.class="
              "com.example.benchmark.StartupBenchmark"], cfg.runner)


# What each of these was before #458, byte for byte: the scenario and runner
# blocks between the same head and tail.
SECTION_DRAFT = (
    "  name: openHome\n"
    '  start: {name: "bindApplication", _source: default}\n'
    '  end: {name: "home_shown", _source: derived, _evidence: "TraceSectionMetric in '
    f'{BENCH_DIR}/HomeBenchmark.kt; confirm against a probe"}}\n' + _gradle("HomeBenchmark"))

NO_BENCHMARK_DRAFT = (
    "  name: coldStart\n"
    '  window: {name: launch, _source: derived, _evidence: "the launcher Activity '
    ".MainActivity in app/src/main/AndroidManifest.xml, started by the runner below; the "
    "trace decides the end: the launch's end as the platform records it, or the app's "
    'reportFullyDrawn when that comes later, and the report says which (window.defined_by)"}\n'
    "\nrunner:\n"
    "  mode: launch\n"
    # The launcher by name, so a debug build's second one does not make the
    # device answer with its chooser (#930).
    '  activity: "com.example.app.MainActivity"\n'
    "  iterations: 5\n"
    "  duration_ms: 20000\n"
    '  _evidence: "no MacrobenchmarkRule in the tree; force-stop + am start of the launcher in '
    "app/src/main/AndroidManifest.xml, by name: a build with a second launcher, a leak detector's "
    "in a debug build, would otherwise start the system's chooser\"\n")

UNCHANGED = {
    # A section is the developer's own statement of where "ready" is, so it
    # wins over the launch window in a class that measures the launch too.
    "a section, in a class that measures the launch too":
        ({"HomeBenchmark": SECTION}, SECTION_DRAFT),
    "StartupMode alone":
        ({"MemoryBenchmark": MODE},
         "  name: openFeed\n" + QUESTION + _gradle("MemoryBenchmark")),
    # Its file has the metrics' names in an import and in a comment.
    "StartupMode alone, with the metric named and not constructed":
        ({"MemoryBenchmark": NAMED_ONLY},
         "  name: openFeed\n" + QUESTION + _gradle("MemoryBenchmark")),
    "none of the three":
        ({"ClickBenchmark": PLAIN},
         "  name: openCart\n" + QUESTION + _gradle("ClickBenchmark")),
    # The developer named a section, so the launch window does not take its
    # place, and the end is asked for, naming the constant to look up (#516).
    "a section whose name was not read, beside the launch":
        ({"StartupBenchmark": UNREAD_SECTION},
         "  name: startup\n"
         '  start: {name: "bindApplication", _source: default}\n'
         '  end: {name: "?", _source: default, _evidence: "TraceSectionMetric(Elsewhere.READY) '
         f'in {BENCH_DIR}/StartupBenchmark.kt: its value was not read here. Put the string it '
         'holds, or pick from `echolot probe`"}\n' + _gradle("StartupBenchmark")),
    "no benchmark at all": ({}, NO_BENCHMARK_DRAFT),
}


@pytest.mark.parametrize("case", list(UNCHANGED))
def test_every_other_draft_is_byte_for_byte_what_it_was(tmp_path: Path, case: str) -> None:
    classes, expected = UNCHANGED[case]
    text = draft(tmp_path, **classes)
    check(case, text == HEAD + expected + TAIL, text)


def test_the_repository_of_the_other_scan_tests_drafts_what_it_did(tmp_path: Path) -> None:
    text = "\n".join(scan.skeleton(scan.describe(repo(tmp_path), devices=False)))
    expected = (
        "  name: startupToHome\n"
        '  start: {name: "bindApplication", _source: default}\n'
        '  end: {name: "home_shown", _source: derived, _evidence: "TraceSectionMetric in '
        f'{BENCH_DIR}/StartupBenchmark.kt; confirm against a probe"}}\n'
        + _gradle("StartupBenchmark"))
    check("a literal, a constant and StartupTimingMetric in one class: the section's draft",
          text == HEAD + expected + TAIL, text)


# --- which class first, among several ---------------------------------------------
#
# Several classes are a scenario each (#517), and the first is the one
# `collect` and `analyze` take when none is named: the order the one class
# used to be picked in.

def _first(text: str) -> str:
    """The first scenario under `scenarios:`, by its name."""
    lines = text.splitlines()
    return lines[lines.index("scenarios:") + 1].strip().rstrip(":")


def test_a_class_that_measures_the_launch_comes_before_startup_mode_alone(tmp_path: Path) -> None:
    # MemoryBenchmark.kt is read first.
    text = draft(tmp_path, MemoryBenchmark=MODE, StartupBenchmark=LAUNCH)
    check("the startup benchmark first, with its window", _first(text) == "StartupBenchmark"
          and LAUNCH_WINDOW.replace("  window:", "    window:") in text, text)


def test_and_before_a_class_that_names_none(tmp_path: Path) -> None:
    text = draft(tmp_path, ClickBenchmark=PLAIN, StartupBenchmark=LAUNCH)
    check("the startup benchmark first", _first(text) == "StartupBenchmark", text)


def test_two_startup_benchmarks_keep_the_order_they_are_read_in(tmp_path: Path) -> None:
    text = draft(tmp_path, StartupBenchmark=LAUNCH,
                 ColdStartBenchmark=LAUNCH.replace("StartupBenchmark", "ColdStartBenchmark"))
    check("the first file's class first", _first(text) == "ColdStartBenchmark"
          and text.index(f"StartupTimingMetric in {BENCH_DIR}/ColdStartBenchmark.kt")
          < text.index(f"StartupTimingMetric in {BENCH_DIR}/StartupBenchmark.kt"), text)


def test_every_kind_in_one_module_puts_the_section_first(tmp_path: Path) -> None:
    text = draft(tmp_path, **EVERY)
    check("the class with a section and a cold start, as the one class taken was",
          _first(text) == "HomeBenchmark", text)


# --- recorded by the benchmark, analysed under the launch window ---------------------

# An app with no flavours, so the draft's package is the fixture trace's
# process, and a benchmark module that declares no variants of its own.
PLAIN_APP = """
android {
  namespace = 'com.example.app'
  defaultConfig { applicationId = "com.example.app" }
  buildTypes {
    benchmark { initWith release; profileable = true }
  }
}
"""


@pytest.mark.usefixtures("no_adb")
def test_a_drafted_startup_benchmark_records_and_analyses(tmp_path: Path, capsys) -> None:
    """`collect` in gradle mode, then `analyze`, on the config as `scan` drafted it.

    Nothing between the two reads the window: gradle mode gathers what the
    benchmark wrote, and `analyze` takes the bounds of each trace from the
    launch in it. The benchmark here is a stand-in for `./gradlew` that
    writes the fixture trace, whose app is launched inside the recording, as
    a startup macrobenchmark's is.
    """
    root = tmp_path / "project"
    root.mkdir()
    repo(root, PLAIN_APP)
    (root / "benchmark/build.gradle").write_text(
        "plugins { id 'com.android.test' }\nandroid { namespace = 'com.example.benchmark' }\n",
        encoding="utf-8")
    (root / BENCH_DIR / "StartupBenchmark.kt").write_text(
        LAUNCH.replace("com.example.app.beta", "com.example.app"), encoding="utf-8")
    text = "\n".join(scan.skeleton(scan.describe(root, devices=False)))
    check("the draft is the launch window under a gradle runner",
          "package: com.example.app\n" in text and "  window: {name: launch" in text
          and "mode: gradle" in text
          and 'gradle_task: ":benchmark:connectedBenchmarkAndroidTest"' in text, text)
    config = root / "echolot.yml"
    config.write_text(text + "\n", encoding="utf-8")

    # What the benchmark would write: one trace for each repeat, under the
    # module's build directory.
    # Two recordings, not one copied twice: `analyze` counts one recording
    # once, however many paths it arrives under (#565).
    written = tmp_path / "recorded.bin"
    written.write_bytes(fixture.build(fully_drawn="app"))
    second = tmp_path / "recorded2.bin"
    second.write_bytes(fixture.build(fully_drawn="app", composables=False))
    wrapper = root / "gradlew"
    wrapper.write_text(
        "#!/bin/sh\n"
        'd="benchmark/build/outputs/connected_android_test_additional_output/benchmark/connected/Pixel"\n'
        'mkdir -p "$d"\n'
        f'cp "{written}" "$d/StartupBenchmark_startup_iter000.perfetto-trace"\n'
        f'cp "{second}" "$d/StartupBenchmark_startup_iter001.perfetto-trace"\n',
        encoding="utf-8")
    wrapper.chmod(0o755)

    with recorder.isolated():
        code = main(["collect", "-c", str(config)])
        _, err = capsys.readouterr()
        check("collect: exit 0", code == 0, err[-800:])
        traces = sorted((root / ".echolot" / "traces").glob("*.perfetto-trace"))
        check("both repeats gathered under the scenario's name",
              [p.name for p in traces] == ["startup_iter000.perfetto-trace",
                                           "startup_iter001.perfetto-trace"], traces)
        code = main(["analyze", "-c", str(config), "-o", str(tmp_path / "out"),
                     *map(str, traces)])
        _, err = capsys.readouterr()
        check("analyze: exit 0", code == 0, err[-800:])
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    window = report["window"]
    check("two runs of the scenario the draft named",
          (report["runs"], report["scenario"]) == (2, "startup"),
          {k: report.get(k) for k in ("runs", "scenario")})
    check("the window is the launch, closed on the app's reportFullyDrawn",
          (window["defined_by"], window["report_fully_drawn"], window["start_anchor"],
           window["end_anchor"]) == ("launch+reportFullyDrawn", "app", None, None), window)
    check("and no detector failed on it",
          [d["id"] for d in report["detectors"] if d.get("error")] == [])
