#!/usr/bin/env python3
"""`echolot scan` — what the repository says about itself, read as text.

Every fact here is one an agent used to gather by reading build scripts
during setup, and one the tests pin to the shape a build script has: a
Groovy one and a Kotlin one, a publishing plugin with a `defaultConfig` of
its own, a benchmark module that declares its own variants.
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

GROOVY = """
plugins { id 'com.android.application' }

somePublishPlugin {
  defaultConfig {        // another plugin's, and first in the file
    compareOnAssemble = false
  }
}

android {
  namespace = 'com.example.app'
  defaultConfig {
    applicationId = "com.example.app"   // the one that matters
    versionName = "1.0"
  }
  flavorDimensions = ["default"]
  productFlavors {
    beta {
      dimension = "default"
      applicationIdSuffix = ".beta"
      manifestPlaceholders = [icon: "@mipmap/ic_beta"]
    }
    prod { dimension = "default" }
  }
  buildTypes {
    debug { debuggable = true; applicationIdSuffix = ".debug" }
    benchmark {
      initWith release
      debuggable = false
      profileable = true
      minifyEnabled = false
    }
    release { minifyEnabled = true }
  }
}
"""

KTS = """
android {
    namespace = "com.example.app"
    defaultConfig { applicationId = "com.example.app" }
    productFlavors {
        create("free") { dimension = "tier"; applicationIdSuffix = ".free" }
        create("paid") { dimension = "tier" }
    }
    buildTypes {
        getByName("debug") { isDebuggable = true }
        create("benchmark") { initWith(getByName("release")); isDebuggable = false; isProfileable = true }
    }
}
"""

MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:name=".App">
    <profileable android:shell="true" />
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
plugins { alias libs.plugins.android.test }
android {
  namespace = 'com.example.benchmark'
  defaultConfig {
    testInstrumentationRunnerArguments["androidx.benchmark.suppressErrors"] = "EMULATOR"
  }
  productFlavors { beta { dimension = "default" } }
  buildTypes { benchmark { initWith release } }
}
"""

BENCH = """
package com.example.benchmark

import androidx.benchmark.macro.junit4.MacrobenchmarkRule

private const val TARGET = "com.example.app.beta"

class StartupBenchmark {
    @get:Rule val rule = MacrobenchmarkRule()

    @Test
    fun startupToHome() = rule.measureRepeated(
        packageName = TARGET,
        metrics = listOf(StartupTimingMetric(), TraceSectionMetric("home_shown"), TraceSectionMetric(Marks.LOAD)),
        startupMode = StartupMode.COLD,
        iterations = 5,
    ) { startActivityAndWait() }

    @Test
    fun scroll() { }
}
"""


def repo(root: Path, script: str = GROOVY) -> Path:
    (root / "settings.gradle").write_text("", encoding="utf-8")
    (root / "build.gradle").write_text("", encoding="utf-8")
    app = root / "app"
    (app / "src/main/java/com/example/app").mkdir(parents=True)
    (app / "build.gradle").write_text(script, encoding="utf-8")
    (app / "src/main/AndroidManifest.xml").write_text(MANIFEST, encoding="utf-8")
    (app / "src/main/java/com/example/app/App.kt").write_text("package com.example.app\nclass App\n", encoding="utf-8")
    for module in ("feature/home", "feature/cart", "core"):
        (root / module / "src/main/java").mkdir(parents=True)
        (root / module / "build.gradle").write_text("", encoding="utf-8")
        (root / module / "src/main/java/A.kt").write_text("class A\n", encoding="utf-8")
    bench = root / "benchmark"
    (bench / "src/main/java/com/example/benchmark").mkdir(parents=True)
    (bench / "build.gradle").write_text(BENCH_SCRIPT, encoding="utf-8")
    (bench / "src/main/java/com/example/benchmark/StartupBenchmark.kt").write_text(BENCH, encoding="utf-8")
    return root


# --- the build script, read as text -------------------------------------------

def test_the_android_block_is_read_and_another_plugins_defaultconfig_is_not():
    s = scan.android_block(scan._strip(GROOVY))
    check("applicationId from android.defaultConfig, not the publish plugin's",
          scan._value(scan.block(s, "defaultConfig"), "applicationId") == "com.example.app", s[:200])


def test_flavours_and_build_types_in_both_dialects():
    for name, text in (("groovy", GROOVY), ("kts", KTS)):
        s = scan.android_block(scan._strip(text))
        flavors = {f["name"]: f for f in scan.flavors_of(s)}
        types = {b["name"]: b for b in scan.build_types_of(s)}
        check(f"{name}: two flavours", len(flavors) == 2, flavors)
        check(f"{name}: the suffix of the first", list(flavors.values())[0]["application_id_suffix"] in (".beta", ".free"), flavors)
        check(f"{name}: benchmark is not debuggable and profileable",
              types["benchmark"]["debuggable"] is False and types["benchmark"]["profileable"] is True, types)
        check(f"{name}: release exists whether or not the script names it", "release" in types, types)


def test_variants_install_as_and_the_preferred_one_is_the_benchmark_build():
    s = scan.android_block(scan._strip(GROOVY))
    variants = scan.variants_of({"application_id": "com.example.app"}, scan.flavors_of(s), scan.build_types_of(s))
    by = {v["name"]: v for v in variants}
    check("six variants", len(variants) == 6, sorted(by))
    check("suffixes of flavour and build type both", by["betaDebug"]["application_id"] == "com.example.app.beta.debug", by)
    check("the debuggable one is not for measuring", by["betaDebug"]["measure"].startswith("no"), by["betaDebug"])
    check("benchmark over release over debug, first flavour", scan.preferred(variants)["name"] == "betaBenchmark", scan.preferred(variants))


def test_a_string_with_a_brace_does_not_unbalance_the_block():
    text = 'android { buildTypes { debug { manifestPlaceholders = [x: "}"] } } }'
    s = scan.android_block(text)
    check("the block closes where it should", [n for n, _ in scan.entries(scan.block(s, "buildTypes"))] == ["debug"], s)


# --- the tree -----------------------------------------------------------------

def test_describe_finds_the_app_the_benchmark_and_what_to_allow(tmp_path):
    facts = scan.describe(repo(tmp_path), devices=False)
    app = facts.app
    check("the app module by its launcher", app["module"] == ":app" and app["launcher"] == ".MainActivity", app)
    check("its applicationId", app["application_id"] == "com.example.app", app)
    check("profileable seen", app["profileable"] is True, app)
    check("no profileable note", not any("profileable" in n for n in facts.notes), facts.notes)
    b = facts.benchmarks[0]
    check("the benchmark module", b["module"] == ":benchmark", b)
    check("its class and tests", b["classes"][0]["name"] == "com.example.benchmark.StartupBenchmark"
          and b["classes"][0]["tests"] == ["startupToHome", "scroll"], b["classes"])
    check("who it drives, through a constant", b["package_name"] == "com.example.app.beta", b)
    check("what it measures, literal and constant", b["metrics"] == ["home_shown"], b["metrics"])
    check("a cold start", b["startup"], b)
    check("its runner arguments", b["runner_args"] == {"androidx.benchmark.suppressErrors": "EMULATOR"}, b)
    tasks = scan.gradle_tasks(b, facts.variants)
    check("the task from the module's own variants", tasks == [":benchmark:connectedBetaBenchmarkAndroidTest"], tasks)
    check("allowed: the app and the trees, not the test module",
          facts.allowed == ["app/src/main", "core/src/main", "feature/*/src/main"], facts.allowed)
    check("devices not asked", facts.devices is None, facts.devices)


def test_the_skeleton_names_its_sources(tmp_path):
    facts = scan.describe(repo(tmp_path), devices=False)
    text = "\n".join(scan.skeleton(facts))
    check("the package of the preferred variant", "package: com.example.app.beta" in text, text)
    check("the process as a glob", 'process: "com.example.app.beta*"' in text, text)
    check("the evidence names the script and the variant", "app/build.gradle, variant betaBenchmark" in text, text)
    check("the scenario after the test", "name: startupToHome" in text, text)
    check("the end anchor from the metric", 'end: {name: "home_shown"' in text, text)
    check("the gradle task and the class", ':benchmark:connectedBetaBenchmarkAndroidTest' in text
          and "class=com.example.benchmark.StartupBenchmark" in text, text)
    check("allowed as globs", '"feature/*/src/main"' in text, text)


def test_a_tree_with_no_benchmark_gets_a_launch_runner(tmp_path):
    root = repo(tmp_path)
    (root / "benchmark/src/main/java/com/example/benchmark/StartupBenchmark.kt").unlink()
    facts = scan.describe(root, devices=False)
    check("no benchmark", facts.benchmarks == [], facts.benchmarks)
    text = "\n".join(scan.skeleton(facts))
    check("launch mode", "mode: launch" in text, text)
    rendered = scan.render(facts)
    check("said in words", "no MacrobenchmarkRule" in rendered, rendered)


# --- the command --------------------------------------------------------------

def test_the_command_prints_the_facts_and_the_json(tmp_path):
    repo(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(["scan", "--root", str(tmp_path), "--no-devices"])
    check("exit 0", code == 0, code)
    text = out.getvalue()
    check("the variants table", "| betaBenchmark |" in text, text[:600])
    check("the config to start from", "```yaml" in text and "gradle_task" in text, text[-800:])
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        main(["scan", "--root", str(tmp_path), "--no-devices", "--json"])
    got = json.loads(out.getvalue())
    check("json with the same facts", got["app"]["application_id"] == "com.example.app"
          and got["benchmarks"][0]["module"] == ":benchmark", list(got))
