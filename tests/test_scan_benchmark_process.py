#!/usr/bin/env python3
"""`echolot scan` drafts a process that leaves the benchmark's own out (#467).

For a project with a macrobenchmark the draft said `process:
"com.example.app*"`. The benchmark module's own package usually begins with
the app's (`com.example.app.benchmark` is Android Studio's template), so the
mask matched two processes in every trace the benchmark wrote. `analyze`
took the larger, the app, and said so on stderr for every trace and in the
report's header: the first thing a newcomer read was a warning about a
process they had never heard of.

A GLOB cannot leave one name out and keep the rest. So where no manifest of
the project declares another process of the app, the draft names the app's
exactly; where one does, the mask is what reaches it, and stays, with its
`_evidence` saying both.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, scan  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_scan import repo  # noqa: E402
from tests.test_scan_benchmark_kinds import BENCH_DIR, LAUNCH  # noqa: E402
from tests.test_scan_startup_benchmark import PLAIN_APP  # noqa: E402

TEMPLATE = "plugins { id 'com.android.test' }\nandroid { namespace = 'com.example.app.benchmark' }\n"
EXACT = ("; the process by its exact name, not `com.example.app*`: the benchmark's own, "
         "`com.example.app.benchmark` (namespace in benchmark/build.gradle), begins with it, "
         "and no manifest here declares another process of the app")


def project(root: Path, bench_script: str = TEMPLATE) -> Path:
    """An app without flavours, installed as `com.example.app`, and a startup benchmark."""
    root.mkdir(parents=True, exist_ok=True)
    repo(root, PLAIN_APP)
    (root / "benchmark/build.gradle").write_text(bench_script, encoding="utf-8")
    (root / BENCH_DIR / "StartupBenchmark.kt").write_text(
        LAUNCH.replace("com.example.app.beta", "com.example.app"), encoding="utf-8")
    return root


def head(root: Path) -> list[str]:
    """The draft's `project:` block."""
    text = "\n".join(scan.skeleton(scan.describe(root, devices=False)))
    return text.split("\n\n")[0].splitlines()


def manifest(root: Path, where: str, body: str) -> None:
    path = root / where / "AndroidManifest.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
                    f"<application>{body}</application></manifest>\n", encoding="utf-8")


# --- the draft ----------------------------------------------------------------------

def test_the_templates_benchmark_leaves_the_app_named_exactly(tmp_path: Path) -> None:
    lines = head(project(tmp_path))
    check("the whole block: the app's process by its name, and why", lines == [
        "project:",
        "  package: com.example.app",
        '  process: "com.example.app"',
        "  _source: derived",
        '  _evidence: "applicationId in app/build.gradle, variant benchmark — the others: '
        f'debug, release{EXACT}"'], lines)


def test_the_benchmarks_application_id_comes_before_its_namespace(tmp_path: Path) -> None:
    lines = head(project(tmp_path, "plugins { id 'com.android.test' }\nandroid {\n"
                         "  namespace = 'com.example.bench'\n"
                         "  defaultConfig { applicationId = 'com.example.app.perf' }\n}\n"))
    check("the process it installs as, and where that was read",
          '  process: "com.example.app"' in lines
          and "the benchmark's own, `com.example.app.perf` (applicationId in "
              "benchmark/build.gradle), begins with it" in lines[-1], lines)


def test_another_process_of_the_app_keeps_the_mask(tmp_path: Path) -> None:
    root = project(tmp_path)
    manifest(root, "core/src/main",
             '<service android:name=".Sync" android:process=":remote" />')
    lines = head(root)
    check("the mask, which is what reaches the other process, and both said",
          '  process: "com.example.app*"' in lines
          and lines[-1].endswith(
              "; the mask matches the benchmark's own process too, `com.example.app.benchmark` "
              "(namespace in benchmark/build.gradle), and analyze takes the one with more "
              "slices: it stays a mask for the app's other processes, `:remote` in "
              'core/src/main/AndroidManifest.xml"'), lines)


def test_an_application_with_a_process_of_its_own_keeps_the_mask(tmp_path: Path) -> None:
    root = project(tmp_path)
    app = root / "app/src/main/AndroidManifest.xml"
    app.write_text(app.read_text(encoding="utf-8").replace(
        '<application android:name=".App">',
        '<application android:name=".App" android:process=":ui">'), encoding="utf-8")
    lines = head(root)
    # Its main process is not called by the package at all: the exact name
    # would match nothing.
    check("the mask", '  process: "com.example.app*"' in lines
          and "`:ui` in app/src/main/AndroidManifest.xml" in lines[-1], lines)


def test_a_process_no_build_of_the_app_declares_is_not_counted(tmp_path: Path) -> None:
    root = project(tmp_path)
    manifest(root, "app/src/androidTest", '<service android:name=".Fake" android:process=":t" />')
    manifest(root, "benchmark/src/main", '<service android:name=".Own" android:process=":b" />')
    manifest(root, "core/src/main", '<!-- <service android:name=".Old" android:process=":old" /> -->')
    lines = head(root)
    check("a test's, the benchmark module's, and one commented out: the exact name",
          '  process: "com.example.app"' in lines and lines[-1].endswith(EXACT + '"'), lines)


def test_a_benchmark_of_another_package_leaves_the_mask_alone(tmp_path: Path) -> None:
    lines = head(project(tmp_path, TEMPLATE.replace("com.example.app.benchmark",
                                                    "com.example.benchmark")))
    check("the mask, and nothing added to its evidence", lines[2:] == [
        '  process: "com.example.app*"',
        "  _source: derived",
        '  _evidence: "applicationId in app/build.gradle, variant benchmark — the others: '
        'debug, release"'], lines)


def test_a_benchmark_whose_package_is_not_read_leaves_the_mask_alone(tmp_path: Path) -> None:
    lines = head(project(tmp_path, "plugins { id 'com.android.test' }\n"
                                   "android { namespace = Ids.BENCHMARK }\n"))
    check("the mask, as before", '  process: "com.example.app*"' in lines
          and "benchmark's own" not in lines[-1], lines)


def test_the_facts_carry_the_benchmarks_package_and_the_apps_processes(tmp_path: Path) -> None:
    root = project(tmp_path)
    manifest(root, "core/src/main", '<service android:name=".Sync" android:process=":remote" />')
    facts = scan.describe(root, devices=False)
    bench = facts.benchmarks[0]
    check("the benchmark's own package and where it was read",
          (bench["own_package"], bench["own_package_from"])
          == ("com.example.app.benchmark", "namespace in benchmark/build.gradle"), bench)
    check("the processes the project's manifests declare",
          facts.app["processes"]
          == [{"process": ":remote", "manifest": "core/src/main/AndroidManifest.xml"}],
          facts.app)


# --- what analyze makes of it ---------------------------------------------------------

def _analyze(tmp_path: Path, name: str, process: str, capsys) -> tuple[dict, str]:
    config = tmp_path / f"{name}.yml"
    config.write_text(f'project:\n  package: com.example.app\n  process: "{process}"\n'
                      "scenario:\n  name: journey\n  window: whole\n", encoding="utf-8")
    trace = tmp_path / "journey.perfetto-trace"
    if not trace.exists():
        trace.write_bytes(fixture.journey())
    out = tmp_path / name
    code = main(["analyze", "-c", str(config), "-o", str(out), str(trace)])
    _, err = capsys.readouterr()
    check(f"{name}: exit 0", code == 0, err[-800:])
    return json.loads((out / "report.json").read_text(encoding="utf-8")), err


def test_the_exact_name_takes_the_app_and_says_nothing_of_the_benchmark(
        tmp_path: Path, capsys) -> None:
    """A trace with the app and a macrobenchmark's runner beside it."""
    masked, said = _analyze(tmp_path, "mask", "com.example.app*", capsys)
    check("the mask matched both, took the app, and said so",
          masked["window"]["process"] == "com.example.app"
          and [p["name"] for p in masked["window"]["process_alternatives"]]
          == ["com.example.app.benchmark"] and "matched 2 processes" in said,
          (masked["window"], said[-600:]))
    exact, said = _analyze(tmp_path, "exact", "com.example.app", capsys)
    check("the exact name took the same process, and had nothing to say",
          exact["window"]["process"] == "com.example.app"
          and "process_alternatives" not in exact["window"]
          and "matched" not in said, (exact["window"], said[-600:]))
