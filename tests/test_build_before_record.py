"""Markers reach a trace only through a build (#390).

In launch and command modes `collect` records the build that is on the
phone, and `mark --apply` changes the code and nothing else. A hunt ran
`mark --apply` and then `collect -n 5` with no build in between, found none
of the markers in the new traces, put that down to the trace, and closed at
medium confidence without the breakdown the markers were placed for.
Nothing in echolot pointed at the cause.

So `mark --apply` ends with the build and install that come next, by gradle
task where the build script says which variant installs as the package, and
`analyze` says so when the code has temporary markers and none of them is in
the traces. Here: both of those, and what they read — the variants, the
names on the tagged lines, every prefixed name anywhere in a trace.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, mark, recorder, scan, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _unbuilt_markers, main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_mark_edits import LAUNCHER, manifest, write  # noqa: E402

ACTIVITY = ("class MainActivity : ComponentActivity() {\n"
            "    override fun onCreate(savedInstanceState: Bundle?) {\n"
            "        super.onCreate(savedInstanceState)\n"
            "    }\n"
            "}\n")
BENCHMARK = '  buildTypes { benchmark { initWith release; applicationIdSuffix ".benchmark" } }\n'
PAIR = ('    android.os.Trace.beginSection("{}") // echolot:mark\n'
        "    android.os.Trace.endSection() // echolot:mark\n")


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _app(root: Path, android: str = "", module: str = "app") -> Path:
    """An app module with a launcher whose onCreate `mark` can mark."""
    where = f"{module}/" if module else ""
    write(root, "build.gradle", "")
    write(root, f"{where}build.gradle",
          'android {\n  defaultConfig { applicationId = "com.example.app" }\n' + android + "}\n")
    write(root, f"{where}src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(root, f"{where}src/main/kotlin/MainActivity.kt", ACTIVITY)
    return root


# --- the variant the config measures, by the task that installs it -----------

def test_the_task_is_the_one_installing_as_the_package(tmp_path: Path) -> None:
    _app(tmp_path, BENCHMARK)
    check("one variant installs as the package: its task",
          scan.install_tasks(tmp_path, "com.example.app.benchmark") == [":app:installBenchmark"],
          scan.install_tasks(tmp_path, "com.example.app.benchmark"))
    check("debug installs as the bare id too, and is no build to measure on",
          scan.install_tasks(tmp_path, "com.example.app") == [":app:installRelease"],
          scan.install_tasks(tmp_path, "com.example.app"))
    check("no variant installs as another package", scan.install_tasks(tmp_path, "com.other") == [])
    check("and no package names nothing", scan.install_tasks(tmp_path, "") == [])


def test_every_variant_installing_as_the_package_is_named_the_one_to_measure_first(
        tmp_path: Path) -> None:
    """Which of them is on the phone is written nowhere a build script says."""
    _app(tmp_path, "  buildTypes { benchmark { initWith release } }\n")
    got = scan.install_tasks(tmp_path, "com.example.app")
    check("benchmark before release", got == [":app:installBenchmark", ":app:installRelease"], got)


def test_a_debuggable_variant_is_never_named(tmp_path: Path) -> None:
    """On a large Kotlin app the script showed only debug variants installing
    as the measured package, while the build on the phone was not
    debuggable: their tasks would have put a debug build in its place."""
    _app(tmp_path, '  buildTypes { debug { applicationIdSuffix ".debug" } }\n')
    got = scan.install_tasks(tmp_path, "com.example.app.debug")
    check("a debug build type is debuggable without saying so, and is not named", got == [], got)
    got = scan.install_tasks(tmp_path, "com.example.app")
    check("the release build beside it is", got == [":app:installRelease"], got)


def test_a_build_type_started_from_another_installs_and_debugs_as_that_one(
        tmp_path: Path) -> None:
    """`initWith` copies the suffix and the flag along with the rest, as the
    large Kotlin app's script relied on."""
    _app(tmp_path, '  buildTypes { debug { applicationIdSuffix ".beta" }\n'
                   "    regress { initWith debug; minifyEnabled true } }\n")
    variants = {v["name"]: v for v in scan.describe(tmp_path, devices=False).variants}
    check("it installs as the one it starts from",
          variants["regress"]["application_id"] == "com.example.app.beta", variants["regress"])
    check("and is debuggable as that one is, so no build to measure on",
          variants["regress"]["debuggable"] is True
          and variants["regress"]["measure"].startswith("no"), variants["regress"])
    got = scan.install_tasks(tmp_path, "com.example.app.beta")
    check("so neither is named", got == [], got)
    got = scan.install_tasks(tmp_path, "com.example.app")
    check("and the store's package is release's alone", got == [":app:installRelease"], got)


def test_more_variants_than_anyone_picks_from_name_none(tmp_path: Path) -> None:
    four = "  productFlavors { one { }\n two { }\n three { }\n four { } }\n"
    _app(tmp_path / "four", four)
    got = scan.install_tasks(tmp_path / "four", "com.example.app")
    check(f"more than {scan.INSTALL_TASKS_SHOWN} is said in words", got == [], got)
    _app(tmp_path / "two", "  productFlavors { one { }\n two { } }\n")
    got = scan.install_tasks(tmp_path / "two", "com.example.app")
    check("two are both named", got == [":app:installOneRelease", ":app:installTwoRelease"], got)


def test_flavours_and_an_app_at_the_root(tmp_path: Path) -> None:
    flavours = '  productFlavors { free { }\n paid { applicationIdSuffix ".paid" } }\n'
    _app(tmp_path / "nested", flavours)
    got = scan.install_tasks(tmp_path / "nested", "com.example.app.paid")
    check("flavour and build type, as gradle names the variant",
          got == [":app:installPaidRelease"], got)
    _app(tmp_path / "flat", module="")
    got = scan.install_tasks(tmp_path / "flat", "com.example.app")
    check("a root module's task is the root project's", got == [":installRelease"], got)


# --- what `mark --apply` ends with -----------------------------------------------

LAUNCH = ("next: build and install the app with `./gradlew :app:installBenchmark`, then "
          "record with `echolot collect`. In launch mode `collect` records the build that "
          "is on the phone, and that build has none of these markers.")


def _apply(root: Path, config: str | None, *flags: str) -> tuple[int, str, str]:
    if config is not None:
        write(root, "echolot.yml", config)
    return _run("mark", "--root", str(root), "--apply", *flags)


def test_apply_in_launch_mode_ends_with_the_build_and_its_task(tmp_path: Path,
                                                               monkeypatch) -> None:
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    code, out, _ = _apply(tmp_path, "project:\n  package: com.example.app.benchmark\n"
                                    "runner:\n  mode: launch\n")
    lines = out.strip().splitlines()
    check("exit 0, applied", code == 0 and lines[-2].startswith("applied 1 marker(s)"), out)
    check("and the next step last, with the task", lines[-1] == LAUNCH, lines[-1])
    code, out, _ = _apply(tmp_path, None)
    check("applying again adds nothing, and names no build",
          code == 0 and "nothing applicable to apply" in out and "next:" not in out, out)


def test_apply_says_so_in_command_mode_and_without_a_task(tmp_path: Path, monkeypatch) -> None:
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    _, out, _ = _apply(tmp_path, "project:\n  package: com.example.nope\n"
                                 "runner:\n  mode: command\n  command: x\n")
    check("the variant by its package where no task installs as it",
          "next: build and install the variant that installs as com.example.nope, then "
          "record with `echolot collect`. In command mode `collect` records the build" in out,
          out)


def test_apply_in_gradle_mode_says_the_macrobenchmark_builds_it(tmp_path: Path,
                                                                monkeypatch) -> None:
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    _, out, _ = _apply(tmp_path, "project:\n  package: com.example.app.benchmark\n"
                                 "runner:\n  mode: gradle\n  gradle_task: x\n")
    check("no build to make",
          out.strip().splitlines()[-1] == "next: record with `echolot collect`: in gradle mode "
          "the macrobenchmark builds the app with these markers and installs it first.", out)


def test_a_scenario_recording_the_phone_s_build_gets_the_build(tmp_path: Path,
                                                              monkeypatch) -> None:
    """One scenario in gradle mode and one in command mode: the second needs it."""
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    config = {"project": {"package": "com.example.app.benchmark"},
              "runner": {"mode": "gradle", "gradle_task": "x"},
              "scenarios": {"coldStart": {"start": "AppStart", "end": "Screen.firstFrame"},
                            "feed": {"start": "AppStart", "end": "Feed.loaded",
                                     "runner": {"mode": "command", "command": "x"}}}}
    _, out, _ = _apply(tmp_path, yaml.safe_dump(config))
    check("the build, said for the mode that needs it",
          "`./gradlew :app:installBenchmark`" in out and "In command mode `collect`" in out, out)


def test_apply_without_a_config_says_both(tmp_path: Path, monkeypatch) -> None:
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    _, out, _ = _apply(tmp_path, None)
    check("launch and command modes build nothing, gradle mode does",
          out.strip().splitlines()[-1] == (
              "next: build and install the app, then record with `echolot collect`. In launch "
              "and command modes `collect` records the build that is on the phone, and that "
              "build has none of these markers; in gradle mode the macrobenchmark builds it."),
          out)


def test_apply_json_carries_the_next_step(tmp_path: Path, monkeypatch) -> None:
    _app(tmp_path, BENCHMARK)
    monkeypatch.chdir(tmp_path)
    code, out, _ = _apply(tmp_path, "project:\n  package: com.example.app.benchmark\n",
                          "--json")
    data = json.loads(out)
    check("one document, with the line in it", code == 0 and data["next"] == LAUNCH, data)


# --- the markers in the code -------------------------------------------------------

def test_the_markers_in_the_code_are_the_names_on_its_tagged_lines(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/kotlin/A.kt", (
        "class A {\n"
        "  fun go() {\n"
        '    android.os.Trace.beginSection("AGENTTMP_go") // echolot:mark\n'
        "    try { // echolot:mark\n"
        '      trace("AGENTTMP_untagged") { }\n'
        "    } finally { android.os.Trace.endSection() } // echolot:mark\n"
        '    android.os.Trace.beginSection("PERF_go") // echolot:mark\n'
        "  }\n"
        "}\n"))
    write(tmp_path, "app/src/main/java/B.java", (
        '    android.os.Trace.beginSection("AGENTTMP_go"); // echolot:mark\n'
        '    android.os.Trace.beginAsyncSection("AGENTTMP_load", 0); // echolot:mark\n'))
    write(tmp_path, "app/src/main/kotlin/C.kt",
          b'\xff android.os.Trace.beginSection("AGENTTMP_c") // echolot:mark\n')
    got = mark.planted(tmp_path)
    check("each name on a tagged line, with every file it is in; an untagged one is "
          "not the tool's, an end names nothing, and a file not in UTF-8 is read anyway",
          got == {"AGENTTMP_go": ["app/src/main/java/B.java", "app/src/main/kotlin/A.kt"],
                  "AGENTTMP_load": ["app/src/main/java/B.java"],
                  "AGENTTMP_c": ["app/src/main/kotlin/C.kt"]}, got)
    check("by the prefix asked for", mark.planted(tmp_path, "PERF_") == {
        "PERF_go": ["app/src/main/kotlin/A.kt"]}, mark.planted(tmp_path, "PERF_"))


# --- what the traces hold, and what the report says --------------------------------

def _report(in_trace: list[str] | None, rows: list[dict] | None = None) -> dict:
    markers = {"prefix": "AGENTTMP_", "globs": ["AGENTTMP_*"], "rows": rows or [], "absent": []}
    if in_trace is not None:
        markers["in_trace"] = in_trace
    return report_mod.build("t", {"process": "p", "duration_ms": 1.0}, [], markers=markers)


def test_the_names_the_traces_hold_are_merged_across_repeats() -> None:
    merged = report_mod.aggregate([_report(["AGENTTMP_b"]), _report(["AGENTTMP_a"])])
    check("every name any repeat held", merged["markers"]["in_trace"] == ["AGENTTMP_a", "AGENTTMP_b"],
          merged["markers"])
    older = report_mod.aggregate([_report(["AGENTTMP_a"]), _report(None)])
    check("and none when a repeat cannot say", "in_trace" not in older["markers"], older["markers"])


def _project(tmp_path: Path, runner: str = "launch", names: tuple[str, ...] = ("AGENTTMP_a",)
             ) -> Config:
    _app(tmp_path, BENCHMARK)
    write(tmp_path, "echolot.yml", "project:\n  package: com.example.app.benchmark\n"
                                   f"runner:\n  mode: {runner}\n  gradle_task: x\n")
    write(tmp_path, "app/src/main/kotlin/App.kt",
          "class App {\n" + "".join(PAIR.format(n) for n in names) + "}\n")
    return Config.load(tmp_path / "echolot.yml")


def test_markers_in_the_code_and_in_no_trace_are_put_in_the_report(tmp_path: Path) -> None:
    cfg = _project(tmp_path, names=("AGENTTMP_a", "AGENTTMP_b"))
    rep = _report([])
    _unbuilt_markers(rep, cfg)
    check("their names, files, the mode, and the task",
          rep["markers"].get("unbuilt") == {
              "names": ["AGENTTMP_a", "AGENTTMP_b"], "files": ["app/src/main/kotlin/App.kt"],
              "mode": "launch", "install": [":app:installBenchmark"]}, rep["markers"])
    text = report_mod.to_markdown(rep)
    check("said at the top of the report, before the detectors",
          "> ⚠️ 2 temporary markers are in the code and none is in these traces: the app on "
          "the phone was built before them. Build and install the app with `./gradlew "
          ":app:installBenchmark`, then record again. If they are still missing after that, "
          "the scenario does not run the code they wrap." in text
          and text.index("temporary markers") < text.index("Detectors fired"), text)


def test_one_marker_the_traces_hold_says_the_build_has_them(tmp_path: Path) -> None:
    """The others wrap code the scenario does not run."""
    cfg = _project(tmp_path, names=("AGENTTMP_a", "AGENTTMP_b"))
    rep = _report(["AGENTTMP_b", "AGENTTMP_old"])
    _unbuilt_markers(rep, cfg)
    check("nothing said", "unbuilt" not in rep["markers"], rep["markers"])


def test_nothing_is_said_without_a_checkout_or_without_what_the_traces_hold(
        tmp_path: Path) -> None:
    cfg = _project(tmp_path)
    rep = _report(None)
    _unbuilt_markers(rep, cfg)
    check("a report that cannot say what the traces hold", "unbuilt" not in rep["markers"])
    rep = _report([])
    _unbuilt_markers(rep, Config(cfg.raw))
    check("a config that lives in memory has no code to read", "unbuilt" not in rep["markers"])
    write(tmp_path, "app/src/main/kotlin/App.kt", "class App\n")
    _unbuilt_markers(rep, cfg)
    check("and code with no markers has none to miss", "unbuilt" not in rep["markers"])


def test_in_gradle_mode_the_traces_are_older_than_the_markers(tmp_path: Path) -> None:
    cfg = _project(tmp_path, runner="gradle")
    rep = _report([])
    _unbuilt_markers(rep, cfg)
    check("no task to name", rep["markers"]["unbuilt"]["install"] == [], rep["markers"])
    check("collect builds it", "> ⚠️ The temporary marker `AGENTTMP_a` is in the code and not "
          "in these traces: the traces were recorded before it went in. In gradle mode "
          "`echolot collect` builds the app with it, so record again. If it is still missing "
          "after that, the scenario does not run the code it wraps."
          in report_mod.to_markdown(rep), report_mod.to_markdown(rep))


def test_the_views_say_it_where_the_markers_are_looked_for() -> None:
    rep = _report([])
    rep["markers"]["unbuilt"] = {"names": ["AGENTTMP_a"], "files": ["A.kt"], "mode": "command",
                                 "install": [":app:installBenchmark", ":app:installRelease"]}
    view = report_mod.markers_view(rep)
    check("`report --markers`, above the line that says there are none",
          view.index("Build and install the app with `./gradlew :app:installBenchmark` "
                     "(or `:app:installRelease`, whichever is on the phone)")
          < view.index("_no markers"), view)
    check("`report`, in its header", "is in the code and not in these traces"
          in report_mod.overview(rep), report_mod.overview(rep))
    check("and `report --json`", report_mod.select(rep, [], None, False, False)["markers"][
        "unbuilt"]["names"] == ["AGENTTMP_a"])
    rep["markers"]["unbuilt"]["install"] = []
    check("no task, the words alone",
          "Build and install the app, then record again." in report_mod.markers_view(rep),
          report_mod.markers_view(rep))


def test_analyze_holds_the_code_against_the_whole_trace(tmp_path: Path, monkeypatch) -> None:
    """A marker that ran outside the window is in the build all the same."""
    slices = copy.deepcopy(fixture.SLICES)
    slices[fixture.TID_WORKER] = [("AGENTTMP_late", 1200, 10, [])]
    monkeypatch.setattr(fixture, "SLICES", slices)
    trace = tmp_path / "t.perfetto-trace"
    trace.write_bytes(fixture.build())
    _app(tmp_path, BENCHMARK)
    config = {**selftest.FIXTURE_CONFIG, "runner": {"mode": "launch"},
              "project": {"package": "com.example.app.benchmark",
                          "process": "com.example.app"}}
    write(tmp_path, "echolot.yml", yaml.safe_dump(config))
    source = tmp_path / "app/src/main/kotlin/App.kt"
    write(tmp_path, "app/src/main/kotlin/App.kt", "class App {\n" + PAIR.format("AGENTTMP_late") + "}\n")

    code, out, _ = _run("analyze", str(trace), "-c", str(tmp_path / "echolot.yml"))
    rep = json.loads((tmp_path / ".echolot/out/report.json").read_text(encoding="utf-8"))
    check("exit 0", code == 0, out[-2000:])
    check("the marker after the window is in the trace, and not in the table",
          "AGENTTMP_late" in rep["markers"]["in_trace"]
          and all(r["location"] != "AGENTTMP_late" for r in rep["markers"]["rows"]), rep["markers"])
    check("so the build has it, and nothing is said",
          "unbuilt" not in rep["markers"] and "temporary marker" not in out, out[:3000])

    source.write_text("class App {\n" + PAIR.format("AGENTTMP_new") + "}\n", encoding="utf-8")
    code, out, _ = _run("analyze", str(trace), "-c", str(tmp_path / "echolot.yml"))
    rep = json.loads((tmp_path / ".echolot/out/report.json").read_text(encoding="utf-8"))
    check("a marker the trace lacks is in report.json",
          rep["markers"]["unbuilt"]["names"] == ["AGENTTMP_new"], rep["markers"])
    check("and in what analyze printed, with the task",
          "The temporary marker `AGENTTMP_new` is in the code and not in these traces" in out
          and "`./gradlew :app:installBenchmark`" in out, out[:3000])
