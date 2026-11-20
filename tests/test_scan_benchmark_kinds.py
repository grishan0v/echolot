#!/usr/bin/env python3
"""`echolot scan`: which kind of benchmark each class is (#458).

`scan` called a class "startup" when its source had `StartupTimingMetric` or
`StartupMode`, and the two say different things. The metric says the launch
is what is measured. The mode says how each repeat begins: a scroll
benchmark that passes `startupMode = StartupMode.COLD` has it and measures
no launch.

Here: what is recorded for each class, and what the printed description
says of it. The draft each kind gets is in `test_scan_startup_benchmark.py`.
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


# The three kinds, and a class that is none of them.
LAUNCH = klass("StartupBenchmark", "startup", "StartupTimingMetric()")
SECTION = klass("HomeBenchmark", "openHome",
                'StartupTimingMetric(), TraceSectionMetric("home_shown")')
MODE = klass("ScrollBenchmark", "scrollFeed", "FrameTimingMetric()")
PLAIN = klass("ClickBenchmark", "openCart", "FrameTimingMetric()", mode=False)

EVERY = {"StartupBenchmark": LAUNCH, "HomeBenchmark": SECTION,
         "ScrollBenchmark": MODE, "ClickBenchmark": PLAIN}

# A scroll benchmark whose file has both metrics' names, and constructs neither.
NAMED_ONLY = klass("ScrollBenchmark", "scrollFeed", "FrameTimingMetric()",
                   head="import androidx.benchmark.macro.StartupTimingMetric\n"
                        "// metrics = listOf(StartupTimingMetric())\n"
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
          == {"StartupBenchmark": True, "HomeBenchmark": True,
              "ScrollBenchmark": False, "ClickBenchmark": False}, got)
    check("and the kind of each: a section first, then the launch, then the mode alone",
          {n: c["kind"] for n, c in got.items()}
          == {"StartupBenchmark": "launch", "HomeBenchmark": "section",
              "ScrollBenchmark": "startup_mode", "ClickBenchmark": None}, got)
    check("`startup` is what it was: the metric or the mode",
          {n: c["startup"] for n, c in got.items()}
          == {"StartupBenchmark": True, "HomeBenchmark": True,
              "ScrollBenchmark": True, "ClickBenchmark": False}, got)


def test_a_metric_imported_or_commented_out_measures_nothing(tmp_path: Path) -> None:
    got = by_name(tmp_path, ScrollBenchmark=NAMED_ONLY)["ScrollBenchmark"]
    check("no launch measured, and no section named", (got["measures_launch"], got["kind"])
          == (False, "startup_mode"), got)


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
    check("StartupMode alone", lines["ScrollBenchmark"].endswith(
        "ScrollBenchmark.kt): scrollFeed — sets `StartupMode` and names no "
        "`StartupTimingMetric`: it may measure no launch, so its window needs an end picked "
        "from a probe"), lines["ScrollBenchmark"])
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
          "and was not read\n" in text, text)


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
                               classes["ScrollBenchmark"]["measures_launch"])
          == ("launch", True, "startup_mode", False), classes)
