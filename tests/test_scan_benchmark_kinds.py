#!/usr/bin/env python3
"""`echolot scan`: which kind of benchmark each class is (#458).

`scan` called a class "startup" when its source had `StartupTimingMetric` or
`StartupMode`, and the two say different things. The metric says the launch
is what is measured. The mode says how each repeat begins: a scroll
benchmark that passes `startupMode = StartupMode.COLD` has it and measures
no launch.

A class that measures frames and not the launch is a kind of its own
(#517): a scroll, as a rule, whose `TraceSectionMetric` is a section inside
it, not where "ready" is.

Here: what is recorded for each class, and what the printed description
says of it. The draft each kind gets is in `test_scan_startup_benchmark.py`,
and the draft of several classes in `test_scan_benchmark_scenarios.py`.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import scan  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_scan import repo  # noqa: E402

BENCH_DIR = "benchmark/src/main/java/com/example/benchmark"


def klass(name: str, test: str, metrics: str, mode: bool = True, head: str = "") -> str:
    """A benchmark class as Android Studio's template writes one."""
    return ("package com.example.benchmark\n\n"
            "import androidx.benchmark.macro.junit4.MacrobenchmarkRule\n"
            f"{head}\n"
            f"class {name} {{\n"
            "    @get:Rule val rule = MacrobenchmarkRule()\n\n"
            "    @Test\n"
            f"    fun {test}() = rule.measureRepeated(\n"
            '        packageName = "com.example.app.beta",\n'
            f"        metrics = listOf({metrics}),\n"
            + ("        startupMode = StartupMode.COLD,\n" if mode else "")
            + "        iterations = 5,\n"
            "    ) { pressHome(); startActivityAndWait() }\n"
            "}\n")


# The four kinds, and a class that is none of them.
LAUNCH = klass("StartupBenchmark", "startup", "StartupTimingMetric()")
SECTION = klass("HomeBenchmark", "openHome",
                'StartupTimingMetric(), TraceSectionMetric("home_shown")')
# A scroll benchmark that starts each repeat cold, as its usual form is.
FRAMES = klass("ScrollBenchmark", "scrollFeed", "FrameTimingMetric()")
MEMORY = "MemoryUsageMetric(MemoryUsageMetric.Mode.Last)"
MODE = klass("MemoryBenchmark", "openFeed", MEMORY)
PLAIN = klass("ClickBenchmark", "openCart", MEMORY, mode=False)

EVERY = {"StartupBenchmark": LAUNCH, "HomeBenchmark": SECTION, "ScrollBenchmark": FRAMES,
         "MemoryBenchmark": MODE, "ClickBenchmark": PLAIN}

# A class whose file has two metrics' names, and constructs neither.
NAMED_ONLY = klass("MemoryBenchmark", "openFeed", MEMORY,
                   head="import androidx.benchmark.macro.StartupTimingMetric\n"
                        "// metrics = listOf(StartupTimingMetric(), FrameTimingMetric())\n"
                        "/* TraceSectionMetric(READY) */\n")

# A section whose name is a constant declared in another file.
UNREAD_SECTION = klass("StartupBenchmark", "startup",
                       "StartupTimingMetric(), TraceSectionMetric(Elsewhere.READY)")


def project(root: Path, **classes: str) -> Path:
    """The test repository, with these classes as its benchmark module's sources."""
    root.mkdir(parents=True, exist_ok=True)
    repo(root)
    bench = root / BENCH_DIR
    (bench / "StartupBenchmark.kt").unlink()
    for name, text in classes.items():
        (bench / f"{name}.kt").write_text(text, encoding="utf-8")
    return root


def by_name(root: Path, **classes: str) -> dict[str, dict]:
    facts = scan.describe(project(root, **classes), devices=False)
    return {c["name"].rsplit(".", 1)[-1]: c for c in facts.benchmarks[0]["classes"]}


# --- what is recorded for each class ----------------------------------------------

def test_each_class_says_whether_it_measures_the_launch(tmp_path: Path) -> None:
    got = by_name(tmp_path, **EVERY)
    check("StartupTimingMetric by itself, apart from StartupMode",
          {n: c["measures_launch"] for n, c in got.items()}
          == {"StartupBenchmark": True, "HomeBenchmark": True, "ScrollBenchmark": False,
              "MemoryBenchmark": False, "ClickBenchmark": False}, got)
    check("FrameTimingMetric by itself too",
          {n: c["measures_frames"] for n, c in got.items()}
          == {"StartupBenchmark": False, "HomeBenchmark": False, "ScrollBenchmark": True,
              "MemoryBenchmark": False, "ClickBenchmark": False}, got)
    check("and the kind of each: frames first, a section, the launch, the mode alone",
          {n: c["kind"] for n, c in got.items()}
          == {"StartupBenchmark": "launch", "HomeBenchmark": "section",
              "ScrollBenchmark": "frames", "MemoryBenchmark": "startup_mode",
              "ClickBenchmark": None}, got)
    check("`startup` is what it was: the metric or the mode",
          {n: c["startup"] for n, c in got.items()}
          == {"StartupBenchmark": True, "HomeBenchmark": True, "ScrollBenchmark": True,
              "MemoryBenchmark": True, "ClickBenchmark": False}, got)


def test_a_metric_imported_or_commented_out_measures_nothing(tmp_path: Path) -> None:
    got = by_name(tmp_path, MemoryBenchmark=NAMED_ONLY)["MemoryBenchmark"]
    check("no launch measured, no frames, and no section named",
          (got["measures_launch"], got["measures_frames"], got["kind"])
          == (False, False, "startup_mode"), got)


def test_java_constructs_the_metric_with_new(tmp_path: Path) -> None:
    java = ("package com.example.benchmark;\n\n"
            "public class StartupBenchmark {\n"
            "    @Rule public MacrobenchmarkRule rule = new MacrobenchmarkRule();\n\n"
            "    @Test\n"
            "    public void startup() {\n"
            '        rule.measureRepeated("com.example.app.beta",\n'
            "            Collections.singletonList(new StartupTimingMetric ()), 5, scope -> {});\n"
            "    }\n"
            "}\n")
    root = project(tmp_path)
    (root / BENCH_DIR / "StartupBenchmark.java").write_text(java, encoding="utf-8")
    cls = scan.describe(root, devices=False).benchmarks[0]["classes"][0]
    check("read the same way", (cls["measures_launch"], cls["kind"]) == (True, "launch"), cls)


def test_a_section_whose_name_was_not_read_is_still_a_section(tmp_path: Path) -> None:
    got = by_name(tmp_path, StartupBenchmark=UNREAD_SECTION)["StartupBenchmark"]
    check("a section is named, though its name is declared elsewhere",
          (got["kind"], got["metrics"], got["measures_launch"]) == ("section", [], True), got)


# --- a class that measures frames (#517) ------------------------------------------

# A scroll as a team writes one: it starts cold, names a section of the app's
# and marks its phases in the test itself.
SCROLL = ("package com.example.benchmark\n\n"
          "import androidx.tracing.trace\n\n"
          'private const val PHASE_SCROLL = "phase_scroll"\n\n'
          "class ListScrollBenchmark {\n"
          "    @get:Rule val rule = MacrobenchmarkRule()\n\n"
          "    @Test\n"
          "    fun scroll() = rule.measureRepeated(\n"
          '        packageName = "com.example.app.beta",\n'
          "        metrics = listOf(FrameTimingMetric(), TraceSectionMetric(Rows.BIND)),\n"
          "        startupMode = StartupMode.COLD,\n"
          "        iterations = 5,\n"
          "        setupBlock = { startActivityAndWait() },\n"
          "    ) {\n"
          "        traced(PHASE_SCROLL) { list.fling(Direction.DOWN) }\n"
          '        trace(label = "settle") { device.waitForIdle() }\n'
          "        traced(Phases.FLING_BACK) { list.fling(Direction.UP) }\n"
          "    }\n\n"
          "    private fun traced(name: String, block: () -> Unit) = trace(name, block)\n"
          "}\n")


def test_a_scroll_that_starts_cold_and_names_a_section_measures_frames(tmp_path: Path) -> None:
    got = by_name(tmp_path, ListScrollBenchmark=SCROLL)["ListScrollBenchmark"]
    # It was a `section`, with `startup` set, and the draft took it for the
    # cold start: its section became the end of a window from bindApplication.
    check("frames, not a section and not a cold start",
          (got["kind"], got["measures_frames"], got["measures_launch"], got["startup"])
          == ("frames", True, False, True), got)
    check("the sections the test writes, a constant by its value or as written, and no "
          "parameter", got["traced"] == ["phase_scroll", "settle", "Phases.FLING_BACK"], got)


def test_frames_beside_the_launch_are_a_startup_benchmark(tmp_path: Path) -> None:
    both = klass("StartupBenchmark", "startup", "StartupTimingMetric(), FrameTimingMetric()")
    got = by_name(tmp_path, StartupBenchmark=both)["StartupBenchmark"]
    check("the launch is what it measures", (got["kind"], got["measures_frames"])
          == ("launch", True), got)


def test_the_description_says_frames_and_where_the_tests_sections_are(tmp_path: Path) -> None:
    root = project(tmp_path, ListScrollBenchmark=SCROLL, StartupBenchmark=LAUNCH)
    script = root / "benchmark/build.gradle"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "com.example.benchmark", "com.example.app.beta.benchmark"), encoding="utf-8")
    text = scan.render(scan.describe(root, devices=False))
    check("the module names each kind, the cold start first, and its own process",
          "`:benchmark` — drives `com.example.app.beta`, a cold start and frame timing; its "
          "own process is `com.example.app.beta.benchmark`\n" in text, text)
    check("the scroll's line: the whole trace, and the test's own sections",
          "scroll — measures frames (`FrameTimingMetric`) and not the launch: its window is "
          "the whole trace, one repeat of its measure block; `phase_scroll`, `settle`, "
          "`Phases.FLING_BACK`, written by the test itself, are in the benchmark's own process "
          "`com.example.app.beta.benchmark`, not the app's, and no window's end\n" in text, text)


def test_a_module_of_one_scroll_is_frame_timing_and_no_cold_start(tmp_path: Path) -> None:
    text = scan.render(scan.describe(project(tmp_path, ScrollBenchmark=FRAMES), devices=False))
    check("frame timing alone, though the class starts cold",
          "`:benchmark` — drives `com.example.app.beta`, frame timing; " in text, text)


# --- the description --------------------------------------------------------------

def test_the_description_says_which_kind_each_class_is(tmp_path: Path) -> None:
    text = scan.render(scan.describe(project(tmp_path, **EVERY), devices=False))
    lines = {line.split("`")[1].rsplit(".", 1)[-1]: line
             for line in text.splitlines() if line.startswith("- `com.example.benchmark.")}
    check("one line for each class", sorted(lines) == sorted(EVERY), lines)
    check("the launch", lines["StartupBenchmark"].endswith(
        "StartupBenchmark.kt): startup — measures the launch (`StartupTimingMetric`): "
        "its window can be the launch, with no end to pick"), lines["StartupBenchmark"])
    check("a section, and that it comes before the launch the class measures too",
          lines["HomeBenchmark"].endswith(
              "HomeBenchmark.kt): openHome — measures a section (`TraceSectionMetric`): "
              "`home_shown`, where its window can end; it measures the launch too "
              "(`StartupTimingMetric`), and the section comes first"), lines["HomeBenchmark"])
    check("frames, though it starts cold", lines["ScrollBenchmark"].endswith(
        "ScrollBenchmark.kt): scrollFeed — measures frames (`FrameTimingMetric`) and not the "
        "launch: its window is the whole trace, one repeat of its measure block"),
        lines["ScrollBenchmark"])
    check("StartupMode alone", lines["MemoryBenchmark"].endswith(
        "MemoryBenchmark.kt): openFeed — sets `StartupMode` and names no "
        "`StartupTimingMetric`: it may measure no launch, so its window needs an end picked "
        "from a probe"), lines["MemoryBenchmark"])
    check("none of the three, which may be a shared helper's doing",
          lines["ClickBenchmark"].endswith(
              "ClickBenchmark.kt): openCart — names no `StartupTimingMetric`, "
              "`TraceSectionMetric` or `StartupMode` in its own file: its window needs an end "
              "picked from a probe"), lines["ClickBenchmark"])


def test_a_section_alone_and_one_not_read_are_said_too(tmp_path: Path) -> None:
    alone = klass("HomeBenchmark", "openHome", 'TraceSectionMetric("home_shown")')
    unread = klass("CartBenchmark", "openCart", "TraceSectionMetric(Elsewhere.READY)")
    text = scan.render(scan.describe(
        project(tmp_path, HomeBenchmark=alone, CartBenchmark=unread), devices=False))
    check("a section, and nothing about a launch it does not measure",
          "openHome — measures a section (`TraceSectionMetric`): `home_shown`, where its "
          "window can end\n" in text, text)
    check("a section whose name is a constant declared elsewhere",
          "openCart — measures a section (`TraceSectionMetric`): its name is set elsewhere "
          "and was not read, by `Elsewhere.READY`: a constant whose value was not found in "
          "the project\n" in text, text)


def test_the_command_prints_the_kind_and_the_json_carries_it(tmp_path: Path) -> None:
    project(tmp_path, **EVERY)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(["scan", "--root", str(tmp_path), "--no-devices"])
    check("exit 0", code == 0, code)
    check("the kind in the printed description",
          "startup — measures the launch (`StartupTimingMetric`)" in out.getvalue(),
          out.getvalue())
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        main(["scan", "--root", str(tmp_path), "--no-devices", "--json"])
    classes = {c["name"].rsplit(".", 1)[-1]: c
               for c in json.loads(out.getvalue())["benchmarks"][0]["classes"]}
    check("and in the facts", (classes["StartupBenchmark"]["kind"],
                               classes["StartupBenchmark"]["measures_launch"],
                               classes["ScrollBenchmark"]["kind"],
                               classes["ScrollBenchmark"]["measures_launch"],
                               classes["MemoryBenchmark"]["kind"])
          == ("launch", True, "frames", False, "startup_mode"), classes)
