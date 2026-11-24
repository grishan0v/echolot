"""`echolot scan` on a project that applies androidx.baselineprofile (#906).

The plugin adds build types no build script names: `benchmarkRelease`, the
build to measure on, release-like and profileable, and `nonMinifiedRelease`,
built without R8 to generate profiles. Read off the scripts alone they were
not there. On an open-source app `scan` recommended `release`, which shows no
application slices, said the app was not profileable, named release's mapping
and a task the benchmark module does not have, and could not say which
package the benchmark drives: its code takes it as an instrumentation
argument at run time. `mark --apply` then said to install `release`.

The fixture: an app module that declares only `debug` and `release` and
applies the plugin through a version catalog alias of its own choosing, with
no `<profileable>` in its manifest; a `com.android.test` module that applies
the plugin too, names the app with `targetProjectPath` and reads the target
package from the arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import scan  # noqa: E402
from tests.support import check  # noqa: E402

CATALOG = """
[versions]
benchmark = "1.5.0"

[plugins]
android-application = { id = "com.android.application", version = "9.0.0" }
android-test = { id = "com.android.test", version = "9.0.0" }
bp = { id = "androidx.baselineprofile", version.ref = "benchmark" }
"""

APP = """
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.bp)
}

android {
    namespace = "com.example.app"
    defaultConfig { applicationId = "com.example.app" }
    buildTypes {
        getByName("release") { isMinifyEnabled = true }
    }
}
"""

MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:name=".App">
    <activity android:name=".MainActivity">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
  </application>
</manifest>
"""

BENCH_SCRIPT = """
plugins {
    alias(libs.plugins.android.test)
    alias(libs.plugins.bp)
}

android {
    namespace = "com.example.baselineprofile"
    defaultConfig { minSdk = 28 }
    targetProjectPath = ":app"
}

androidComponents {
    onVariants { variant ->
        variant.instrumentationRunnerArguments.put("targetAppId", variant.testedApks.map { it.toString() })
    }
}
"""

BENCH = """
package com.example.baselineprofile

import androidx.benchmark.macro.StartupMode
import androidx.benchmark.macro.StartupTimingMetric
import androidx.benchmark.macro.junit4.MacrobenchmarkRule
import androidx.test.platform.app.InstrumentationRegistry

val targetPackageName: String
    get() = InstrumentationRegistry.getArguments().getString("targetAppId")!!

class StartupBenchmarks {
    @get:Rule val rule = MacrobenchmarkRule()

    @Test
    fun startup() = rule.measureRepeated(
        packageName = targetPackageName,
        metrics = listOf(StartupTimingMetric()),
        startupMode = StartupMode.COLD,
        iterations = 10,
    ) { startActivityAndWait() }
}
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repo(root: Path, app: str = APP, bench_script: str = BENCH_SCRIPT) -> Path:
    _write(root, "settings.gradle.kts", 'include(":app", ":baselineprofile")\n')
    _write(root, "build.gradle.kts", "plugins { alias(libs.plugins.bp) apply false }\n")
    _write(root, "gradle/libs.versions.toml", CATALOG)
    _write(root, "app/build.gradle.kts", app)
    _write(root, "app/src/main/AndroidManifest.xml", MANIFEST)
    _write(root, "app/src/main/java/com/example/app/App.kt", "package com.example.app\nclass App\n")
    _write(root, "baselineprofile/build.gradle.kts", bench_script)
    _write(root, "baselineprofile/src/main/java/com/example/baselineprofile/StartupBenchmarks.kt", BENCH)
    return root


def test_the_plugin_s_build_types_are_variants(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path), devices=False)
    names = {v["name"]: v for v in facts.variants}
    check("the two the plugin adds beside debug and release",
          sorted(names) == ["benchmarkRelease", "debug", "nonMinifiedRelease", "release"], sorted(names))
    bench, profiles = names["benchmarkRelease"], names["nonMinifiedRelease"]
    check("the benchmark one is not debuggable and is measured on",
          bench["debuggable"] is False and bench["measure"].startswith("yes"), bench)
    check("minified as release is, with its own mapping",
          bench["minified"] is True
          and bench["mapping"] == "app/build/outputs/mapping/benchmarkRelease/mapping.txt", bench)
    check("installs as release does", bench["application_id"] == "com.example.app", bench)
    check("the one for profiles is not minified and not measured on",
          profiles["minified"] is False and profiles["measure"].startswith("no")
          and "benchmarkRelease" in profiles["measure"], profiles)


def test_it_is_the_one_to_start_from_and_the_app_is_profileable(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path), devices=False)
    check("benchmarkRelease before release", scan.preferred(facts.variants)["name"] == "benchmarkRelease",
          [v["name"] for v in facts.variants])
    check("no note that a release-like build traces without the app's slices",
          not any("has no <profileable" in n for n in facts.notes), facts.notes)
    text = scan.render(facts)
    check("the profileable line says who makes it so",
          "profileable: yes for `benchmarkRelease`, `nonMinifiedRelease`, which "
          "androidx.baselineprofile makes profileable" in text, text[:800])
    check("and the variants say where the build types come from",
          "`benchmarkRelease`, `nonMinifiedRelease` are not in app/build.gradle.kts: "
          "androidx.baselineprofile, applied there" in text, text)


def test_the_benchmark_module_drives_the_app_it_targets(tmp_path: Path) -> None:
    facts = scan.describe(repo(tmp_path), devices=False)
    b = facts.benchmarks[0]
    check("the module applies the plugin", b["baseline_profile"] is True, b)
    check("the target package, by targetProjectPath",
          b["package_name"] == "com.example.app"
          and b["package_by"] == {"target": ":app", "variant": "benchmarkRelease"}, b)
    check("its tasks are the plugin's build types'",
          scan.gradle_tasks(b, facts.variants) == [":baselineprofile:connectedBenchmarkReleaseAndroidTest",
                                                   ":baselineprofile:connectedNonMinifiedReleaseAndroidTest"],
          scan.gradle_tasks(b, facts.variants))
    text = scan.render(facts)
    check("said instead of `?`", "`:baselineprofile` — drives `com.example.app`" in text
          and "no string in its code names the package" in text, text)


def test_the_draft_measures_the_benchmark_build(tmp_path: Path) -> None:
    text = "\n".join(scan.skeleton(scan.describe(repo(tmp_path), devices=False)))
    check("its package", "  package: com.example.app\n" in text, text)
    check("its mapping, not release's",
          "  mapping: app/build/outputs/mapping/benchmarkRelease/mapping.txt" in text, text)
    check("the evidence says the plugin adds the build type",
          "variant benchmarkRelease, whose benchmarkRelease androidx.baselineprofile adds" in text, text)
    check("the task that runs it", 'gradle_task: ":baselineprofile:connectedBenchmarkReleaseAndroidTest"' in text,
          text)


def test_missing_dimension_strategy_picks_the_flavour(tmp_path: Path) -> None:
    """A benchmark module that takes the app's only dimension with
    `missingDimensionStrategy` has no flavour of its own: its task carries
    none, and it runs against that flavour, which is then the one to start
    from although another comes first by name."""
    app = APP.replace('    buildTypes {', '''    flavorDimensions += "tier"
    productFlavors {
        create("free") { dimension = "tier"; applicationIdSuffix = ".free" }
        create("paid") { dimension = "tier" }
    }
    buildTypes {''')
    bench = BENCH_SCRIPT.replace('defaultConfig { minSdk = 28 }',
                                 'defaultConfig { minSdk = 28; missingDimensionStrategy("tier", "paid") }')
    facts = scan.describe(repo(tmp_path, app, bench), devices=False)
    chosen = scan.preferred(facts.variants)
    check("paid, the flavour the benchmark runs against", chosen["name"] == "paidBenchmarkRelease", chosen)
    b = facts.benchmarks[0]
    check("drives its package", b["package_name"] == "com.example.app", b)
    check("a task with no flavour in it",
          scan.measuring_task(b, facts.variants, chosen) == ":baselineprofile:connectedBenchmarkReleaseAndroidTest",
          scan.gradle_tasks(b, facts.variants))
    text = "\n".join(scan.skeleton(facts))
    check("the draft's mapping is paid's",
          "mapping: app/build/outputs/mapping/paidBenchmarkRelease/mapping.txt" in text, text)
    check("and its evidence says why", "missingDimensionStrategy runs it against paid" in text, text)


def test_install_tasks_start_with_the_benchmark_build(tmp_path: Path) -> None:
    """`mark --apply` said `installRelease`; the build measured is the
    plugin's, and the one for profiles is never named."""
    got = scan.install_tasks(repo(tmp_path / "one"), "com.example.app")
    check("benchmarkRelease first, nonMinifiedRelease not at all",
          got == [":app:installBenchmarkRelease", ":app:installRelease"], got)
    # Three flavours that install as one package: six builds once the plugin
    # adds its own, and more than anyone picks from. The three to measure on
    # are named, as their release builds were before the plugin.
    app = APP.replace('    buildTypes {', '''    productFlavors {
        create("one") { }
        create("two") { }
        create("three") { }
    }
    buildTypes {''')
    got = scan.install_tasks(repo(tmp_path / "three", app), "com.example.app")
    check("the benchmark builds of each flavour",
          got == [":app:installOneBenchmarkRelease", ":app:installThreeBenchmarkRelease",
                  ":app:installTwoBenchmarkRelease"], got)


def test_how_the_plugin_is_applied(tmp_path: Path) -> None:
    def types(app: str, name: str) -> list[str]:
        facts = scan.describe(repo(tmp_path / name, app), devices=False)
        return [b["name"] for b in facts.build_types]

    plain = APP.replace("    alias(libs.plugins.bp)\n", "")
    check("without it, no such build type",
          types(plain, "none") == ["release", "debug"], types(plain, "none"))
    check("by its id", "benchmarkRelease" in types(
        plain.replace("plugins {", 'plugins {\n    id("androidx.baselineprofile")'), "id"))
    check("an alias the catalog does not give it is not it", "benchmarkRelease" not in types(
        plain.replace("plugins {", "plugins {\n    alias(libs.plugins.other)"), "other"))
    check("nor one in a comment", "benchmarkRelease" not in types(
        plain.replace("plugins {", "plugins {\n    // alias(libs.plugins.bp)"), "comment"))
    check("nor the consumer part alone, which adds none", "benchmarkRelease" not in types(
        plain.replace("plugins {", 'plugins {\n    id("androidx.baselineprofile.consumer")'), "consumer"))
    staged = APP.replace('getByName("release") { isMinifyEnabled = true }',
                         'getByName("release") { isMinifyEnabled = true }\n'
                         '        create("staging") { initWith(getByName("release")) }\n'
                         '        create("qa") { initWith(getByName("debug")) }')
    got = types(staged, "staged")
    check("one for each build type that is not debuggable",
          "benchmarkStaging" in got and "nonMinifiedStaging" in got
          and "benchmarkQa" not in got and "benchmarkDebug" not in got, got)


def test_a_declared_benchmark_build_type_still_comes_first(tmp_path: Path) -> None:
    """A `benchmark` build type the project wrote itself is what its own
    macrobenchmark was set up against; the plugin makes none of it."""
    app = APP.replace('getByName("release") { isMinifyEnabled = true }',
                      'getByName("release") { isMinifyEnabled = true }\n'
                      '        create("benchmark") { initWith(getByName("release")); isProfileable = true }')
    facts = scan.describe(repo(tmp_path, app), devices=False)
    names = [b["name"] for b in facts.build_types]
    check("the plugin skips it", "benchmarkBenchmark" not in names and "benchmarkRelease" in names, names)
    check("and it is the one to start from", scan.preferred(facts.variants)["name"] == "benchmark",
          [v["name"] for v in facts.variants])
