"""`echolot scan`, in the shapes where it read a fact wrong or wrote one it had
not read: a service's process, a profileable build type, two flavour
dimensions, a KDoc that says "class", `useLibrary`, an applicationId behind a
constant, benchmarks in two classes, many modules, `initWith(getByName(…))`, a
`%` pattern, build logic, gradle mode's iterations, and a minified build's
mapping.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import scan  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_scan import GROOVY, KTS, repo  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_service_s_process_is_not_the_app_s(tmp_path: Path) -> None:
    root = repo(tmp_path)
    _write(root, "app/src/main/AndroidManifest.xml",
           '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
           '  <application android:name=".App">\n'
           '    <activity android:name=".MainActivity" android:exported="true"><intent-filter>'
           '<action android:name="android.intent.action.MAIN" /><category '
           'android:name="android.intent.category.LAUNCHER" /></intent-filter></activity>\n'
           '    <service android:name=".SyncService" android:process=":sync" />\n'
           '  </application>\n</manifest>\n')
    check("no process", scan.describe(root, devices=False).app["process"] is None)


def test_a_profileable_build_type_is_said_so(tmp_path: Path) -> None:
    root = repo(tmp_path)
    manifest = root / "app/src/main/AndroidManifest.xml"
    manifest.write_text(re.sub(r"<profileable[^>]*/>", "", manifest.read_text(encoding="utf-8")),
                        encoding="utf-8")
    facts = scan.describe(root, devices=False)
    text = scan.render(facts)
    check("no note that the recommended build traces without the app's slices",
          not any("has no <profileable" in n for n in facts.notes), facts.notes)
    check("and the profileable line names the build type",
          "profileable: yes for `benchmark`" in text, text[:600])


def test_two_flavour_dimensions_make_one_variant_each(tmp_path: Path) -> None:
    script = GROOVY.replace('flavorDimensions = ["default"]', 'flavorDimensions "tier", "env"') \
        .replace('''    beta {
      dimension = "default"''', '''    free { dimension = "tier" }
    paid { dimension = "tier" }
    staging { dimension = "env" }
    beta {
      dimension = "env"''').replace('prod { dimension = "default" }', '')
    facts = scan.describe(repo(tmp_path, script), devices=False)
    names = {v["name"] for v in facts.variants}
    check("one flavour of each dimension",
          "freeStagingBenchmark" in names and "freeBenchmark" not in names
          and "stagingBenchmark" not in names, sorted(names))
    script = script.replace('flavorDimensions "tier", "env"', "")
    (tmp_path / "unread").mkdir()
    facts = scan.describe(repo(tmp_path / "unread", script), devices=False)
    check("and no variant at all when the order cannot be read",
          facts.variants == [] and any("several dimensions" in n for n in facts.notes),
          facts.notes)


def test_the_benchmark_class_is_read_from_the_code(tmp_path: Path) -> None:
    root = repo(tmp_path)
    path = root / "benchmark/src/main/java/com/example/benchmark/StartupBenchmark.kt"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "class StartupBenchmark {",
        "/**\n * This test class benchmarks the speed of app startup.\n */\nclass StartupBenchmark {"),
        encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("the class, not a word from its KDoc",
          facts.benchmarks[0]["classes"][0]["name"] == "com.example.benchmark.StartupBenchmark",
          facts.benchmarks[0]["classes"])


def test_use_library_is_not_the_test_plugin(tmp_path: Path) -> None:
    root = repo(tmp_path)
    script = root / "app/build.gradle"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "android {", 'android {\n  useLibrary("android.test.runner")'), encoding="utf-8")
    check("the app is no test module", "app" not in scan.test_modules(root))
    check("and stays allowed", "app/src/main" in scan.allowed_paths(root))


def test_an_application_id_not_read_is_no_derived_value(tmp_path: Path) -> None:
    script = GROOVY.replace('applicationId = "com.example.app"', "applicationId = AppConfig.ID")
    facts = scan.describe(repo(tmp_path, script), devices=False)
    text = "\n".join(scan.skeleton(facts))
    check("a placeholder marked default", "_source: default" in text.split("scenario:")[0], text)
    check("and said in the notes", any("applicationId could not be read" in n for n in facts.notes))


def test_the_skeleton_takes_one_benchmark_class(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "benchmark/src/main/java/com/example/benchmark/StartupBenchmark.kt").unlink()
    _write(root, "benchmark/src/main/java/com/example/benchmark/ScrollBench.kt",
           "package com.example.benchmark\nclass ScrollBench {\n"
           "  @get:Rule val rule = MacrobenchmarkRule()\n  @Test\n  fun scrollFeed() = "
           "rule.measureRepeated(metrics = listOf(FrameTimingMetric())) {}\n}\n")
    _write(root, "benchmark/src/main/java/com/example/benchmark/StartupBench.kt",
           "package com.example.benchmark\nclass StartupBench {\n"
           "  @get:Rule val rule = MacrobenchmarkRule()\n  @Test\n  fun coldStart() = "
           'rule.measureRepeated(metrics = listOf(TraceSectionMetric("HomeReady")), '
           "startupMode = StartupMode.COLD) {}\n}\n")
    text = "\n".join(scan.skeleton(scan.describe(root, devices=False)))
    check("the test, the anchor and the filter are one class's",
          "name: coldStart" in text and "StartupBench.kt" in text
          and "class=com.example.benchmark.StartupBench\"" in text and "ScrollBench" not in text,
          text)


def test_every_module_is_allowed_and_build_logic_is_not(tmp_path: Path) -> None:
    root = repo(tmp_path)
    for i in range(15):
        _write(root, f"lib{i:02d}/src/main/java/A.kt", "class A\n")
    _write(root, "buildSrc/src/main/kotlin/AppConfig.kt", "object AppConfig\n")
    _write(root, "build-logic/convention/build.gradle.kts", "plugins { `kotlin-dsl` }\n")
    _write(root, "build-logic/convention/src/main/kotlin/Conv.kt", "class Conv\n")
    facts = scan.describe(root, devices=False)
    text = "\n".join(scan.skeleton(facts))
    check("all of them in the skeleton", all(f'"lib{i:02d}/src/main"' in text for i in range(15)))
    check("and no build logic", "buildSrc" not in text and "build-logic" not in text, text)


def test_init_with_get_by_name_is_the_build_type() -> None:
    check("release", scan._init_with('initWith(getByName("release"))') == "release")
    check("and through buildTypes too",
          scan._init_with('initWith(buildTypes.getByName("release"))') == "release")
    check("Groovy unchanged", scan._init_with("initWith release") == "release")


def test_a_percent_pattern_becomes_a_glob() -> None:
    check("% is *", scan._as_glob("HomeScreen%") == "HomeScreen*")
    check("and a literal * stays literal", scan._as_glob("a*b%") == "a[*]b*")


def test_gradle_mode_writes_no_iterations(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path), devices=False)
    text = scan.render(facts)
    runner = "\n".join(scan.skeleton(facts)).split("runner:")[1].split("instrumentation:")[0]
    check("no iterations under the gradle runner", "iterations" not in runner, runner)
    check("and the probe line has no -n", "`echolot collect -c echolot.yml`" in text, text[-900:])


# --- a minified build, and its mapping (#209) ---------------------------------

def _minified_kts(benchmark_flag: str = "") -> str:
    """The Kotlin script with release minified, and benchmark built from it."""
    return KTS.replace(
        'getByName("debug") { isDebuggable = true }',
        'getByName("debug") { isDebuggable = true }\n'
        '        getByName("release") { isMinifyEnabled = true }').replace(
        "isProfileable = true }", f"isProfileable = true{benchmark_flag} }}")


def test_a_build_type_minified_through_init_with_names_its_mapping(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path, _minified_kts()), devices=False)
    by = {v["name"]: v for v in facts.variants}
    check("benchmark is minified as release is, which it starts from",
          (by["freeBenchmark"]["minified"], by["freeBenchmark"]["minified_by"])
          == (True, "release"), by["freeBenchmark"])
    check("where AGP will write its mapping",
          by["freeBenchmark"]["mapping"] == "app/build/outputs/mapping/freeBenchmark/mapping.txt",
          by["freeBenchmark"])
    check("debug is not", by["freeDebug"]["minified"] is None and by["freeDebug"]["mapping"] is None,
          by["freeDebug"])
    text = "\n".join(scan.skeleton(facts))
    check("the draft names it under project",
          "  mapping: app/build/outputs/mapping/freeBenchmark/mapping.txt" in text, text)
    check("and says where that was read",
          "the mapping, written when the variant is built: minifyEnabled in "
          "buildTypes.release of app/build.gradle, which benchmark starts from with initWith" in text, text)


def test_the_drafted_mapping_is_the_configs_once_it_is_built(tmp_path: Path) -> None:
    root = repo(tmp_path, _minified_kts())
    facts = scan.describe(root, devices=False)
    text = "\n".join(scan.skeleton(facts))
    (root / "echolot.yml").write_text(text, encoding="utf-8")
    config = Config.load(root / "echolot.yml")
    expected = root / "app/build/outputs/mapping/freeBenchmark/mapping.txt"
    check("not built: named, not read", config.mapping is None
          and config.missing_mapping == expected, config.missing_mapping)
    rendered = scan.render(facts)
    check("and scan says it is not there yet", "not there yet: AGP writes it when the "
          "variant is built" in rendered, rendered)
    expected.parent.mkdir(parents=True)
    expected.write_text("com.example.app.Store -> a.b:\n", encoding="utf-8")
    check("built: read", config.mapping == expected)
    check("and scan says it is there", "— there now." in scan.render(facts), scan.render(facts))


def test_a_build_type_that_turns_minify_off_is_not_minified(tmp_path: Path) -> None:
    # GROOVY's benchmark starts from a minified release and turns it off.
    facts = scan.describe(repo(tmp_path), devices=False)
    by = {v["name"]: v for v in facts.variants}
    check("benchmark says false itself", (by["betaBenchmark"]["minified"],
                                          by["betaBenchmark"]["minified_by"])
          == (False, "benchmark"), by["betaBenchmark"])
    check("release is minified", by["betaRelease"]["minified"] is True, by["betaRelease"])
    text = "\n".join(scan.skeleton(facts))
    check("and the draft names no mapping", "mapping:" not in text, text)


def test_a_build_type_minified_by_its_own_flag(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path, _minified_kts("; isMinifyEnabled = true")), devices=False)
    chosen = scan.preferred(facts.variants)
    check("its own flag decides", (chosen["minified"], chosen["minified_by"])
          == (True, "benchmark"), chosen)
    check("and the evidence names it alone", "minifyEnabled in buildTypes.benchmark of app/build.gradle\""
          in "\n".join(scan.skeleton(facts)), scan.skeleton(facts))


def test_a_mapping_path_from_a_module_and_from_the_root() -> None:
    check("under the module", scan.mapping_path({"dir": "app"}, "freeRelease")
          == "app/build/outputs/mapping/freeRelease/mapping.txt")
    check("an app module that is the root", scan.mapping_path({"dir": "."}, "release")
          == "build/outputs/mapping/release/mapping.txt")


def test_init_with_that_goes_round_in_a_circle_ends() -> None:
    types = [{"name": "a", "minify": None, "init_with": "b"},
             {"name": "b", "minify": None, "init_with": "a"}]
    check("unknown, and no hang", scan.minified(types[0], types) == (None, None))
