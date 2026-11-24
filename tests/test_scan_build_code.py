#!/usr/bin/env python3
"""`echolot scan` and the project's own build code: the package each variant
installs as, where `buildSrc` or an included `build-logic` decides part of it
(#816), and the launcher the launch draft names (#930).

The doctor checks of the same claims build the two projects these tests start
from (`selftest._build_logic_repo`, `selftest._build_src_repo`); the tests
here take each shape apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import project_setup, runner, scan, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402


def _kotlin(tmp_path: Path) -> Path:
    selftest._build_logic_repo(tmp_path)
    return tmp_path


def _groovy(tmp_path: Path) -> Path:
    selftest._build_src_repo(tmp_path)
    return tmp_path


# --- reading the build code -----------------------------------------------------

ENUMS = """
enum class Tier { free, paid }
@Suppress("EnumEntryName")
enum class AppFlavor(val dimension: Tier, val applicationIdSuffix: String? = null, val label: String = "x") {
    demo(Tier.free, applicationIdSuffix = ".demo"),
    prod(Tier.paid, null, "store"),
    ;
    val shown get() = label
}
enum class Twice(val s: String) { A("a") }
"""


def test_an_enum_reads_by_position_by_name_and_by_default() -> None:
    enums = scan.enums_of({"a/AppFlavor.kt": scan._strip(ENUMS),
                           "b/Other.kt": 'enum class Twice(val s: String) { A("b") }'})
    flavor = enums["AppFlavor"]
    check("the parameters, in order", flavor["params"] == ["dimension", "applicationIdSuffix", "label"],
          flavor)
    check("demo: positional, named, default", flavor["entries"]["demo"] == {
        "dimension": ("expr", "Tier.free"), "applicationIdSuffix": ("text", ".demo"),
        "label": ("text", "x")}, flavor["entries"]["demo"])
    check("prod: null passed is null", flavor["entries"]["prod"]["applicationIdSuffix"] == ("null", None),
          flavor["entries"]["prod"])
    check("an enum without parameters lists its entries", list(enums["Tier"]["entries"]) == ["free", "paid"])
    check("two enums of one name that differ are neither", enums["Twice"] is None, enums["Twice"])


def test_an_expression_resolves_to_a_string_a_null_or_nothing() -> None:
    code = scan.BuildCode(texts={"logic/Config.kt": 'object Config { const val APP_ID = "com.example.app" }',
                                 "logic/AppFlavor.kt": scan._strip(ENUMS)})
    code.enums = scan.enums_of(code.texts)
    code.consts = scan.constants({Path(k): v for k, v in code.texts.items()})
    check("an enum entry's property", scan.resolve("AppFlavor.demo.applicationIdSuffix", code)
          == (".demo", "logic/AppFlavor.kt"))
    check("a qualified one, and `!!`", scan.resolve("com.example.AppFlavor.demo.applicationIdSuffix!!", code)
          == (".demo", "logic/AppFlavor.kt"))
    check("a null read is read", scan.resolve("AppFlavor.prod.applicationIdSuffix", code)
          == (None, "logic/AppFlavor.kt"))
    check("an entry's name", scan.resolve("Tier.paid.name", code) == ("paid", "logic/AppFlavor.kt"))
    check("a constant, with where it is declared", scan.resolve("Config.APP_ID", code)
          == ("com.example.app", "logic/Config.kt"))
    check("a val of the script", scan.resolve("suffix", code, 'val suffix = ".qa"') == (".qa", None))
    for unread in ("rootProject.ext.qaSuffix", '"." + name', "AppFlavor.demo.shown", "providers.gradleProperty(\"x\")"):
        check(f"{unread} is not read", scan.resolve(unread, code) is None, scan.resolve(unread, code))


@pytest.mark.parametrize("body,expected", [
    ("applicationIdSuffix = AppBuildType.DEBUG.applicationIdSuffix", "AppBuildType.DEBUG.applicationIdSuffix"),
    ("applicationIdSuffix rootProject.ext.qaSuffix", "rootProject.ext.qaSuffix"),
    ("applicationIdSuffix(Config.SUFFIX)\n", "Config.SUFFIX"),
    ("if (flavor.applicationIdSuffix != null) { x() }", None),
    ("y = flavor.applicationIdSuffix", None),
])
def test_what_a_block_sets_a_key_to(body: str, expected: str | None) -> None:
    check(body, scan._assigned(body, "applicationIdSuffix") == expected, scan._assigned(body, "applicationIdSuffix"))


# --- the Kotlin project: flavours from a convention plugin, suffixes from enums -----------

def test_the_flavours_come_from_the_convention_plugin_the_app_applies(tmp_path: Path) -> None:
    root = _kotlin(tmp_path)
    facts = scan.describe(root, devices=False)
    check("two flavours, each with the entry's suffix",
          [(f["name"], f["application_id_suffix"], f["dimension"]) for f in facts.flavors]
          == [("demo", ".demo", "contentType"), ("prod", None, "contentType")], facts.flavors)
    check("reached through the plugin the catalog alias names",
          facts.flavors[0]["via"] == "the plugin `example.android.application.flavors` app/build.gradle.kts applies",
          facts.flavors[0])
    debug = next(b for b in facts.build_types if b["name"] == "debug")
    check("debug's suffix, read off the enum", debug["application_id_suffix"] == ".debug"
          and "AppBuildType.kt" in debug["application_id_suffix_from"], debug)
    chosen = scan.preferred(facts.variants)
    check("the variant to start from, and what of its package was read",
          chosen["name"] == "demoRelease" and chosen["application_id"] == "com.example.app.demo"
          and chosen["id_read"] == ["`.demo` of the flavour demo, in build-logic/convention/src/main/kotlin/"
                                    "com/example/build/AppFlavor.kt"], chosen)
    check("its install task", scan.install_tasks(root, "com.example.app.demo") == [":app:installDemoRelease"])


def test_the_render_says_where_the_build_code_decided(tmp_path: Path) -> None:
    text = scan.render(scan.describe(_kotlin(tmp_path), devices=False))
    check("the flavours' sentence", "`demo`, `prod` are not in app/build.gradle.kts: the project's build code "
          "registers them, one for each entry of `AppFlavor`" in text, text)
    check("release's suffix is read as none", "`AppBuildType.RELEASE.applicationIdSuffix` in build-logic/"
          "convention/src/main/kotlin/com/example/build/AppBuildType.kt: null, so none" in text, text)
    check("the packages, all four", "the variants install as 4 packages in all, `com.example.app.demo.debug`, "
          "`com.example.app.debug`, `com.example.app.demo`, `com.example.app`" in text, text)
    check("derived, with the flavour's part", '  _evidence: "applicationId in app/build.gradle.kts, variant '
          "demoRelease; `.demo` of the flavour demo, in build-logic/" in text, text)


def test_a_plugin_the_app_does_not_apply_gives_it_nothing(tmp_path: Path) -> None:
    """A plugin's `apply` is Gradle's to call: taken by its name, the
    script's own `apply(plugin = …)` reached every plugin of the build."""
    root = _kotlin(tmp_path)
    (root / "build-logic/convention/src/main/kotlin/LibraryFlavorsPlugin.kt").write_text(
        "class LibraryFlavorsPlugin : Plugin<Project> {\n"
        "    override fun apply(target: Project) {\n"
        '        productFlavors { create("lib") { applicationIdSuffix = ".lib" } }\n    }\n}\n',
        encoding="utf-8")
    script = root / "app/build.gradle.kts"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "android {", 'apply(plugin = "kotlin-parcelize")\n\nandroid {'), encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("the app's flavours only", [f["name"] for f in facts.flavors] == ["demo", "prod"], facts.flavors)


def test_a_package_folder_named_build_is_read(tmp_path: Path) -> None:
    """`com.example.build` is a package, not Gradle's output: the walk that
    skipped every `build` folder skipped the enums."""
    code = scan.build_code(_kotlin(tmp_path))
    check("the enums under com/example/build", {"AppFlavor", "AppBuildType"} <= set(code.enums), list(code.texts))


def test_a_benchmark_module_that_calls_the_flavour_function_has_the_flavours(tmp_path: Path) -> None:
    root = _kotlin(tmp_path)
    bench = root / "benchmark"
    (bench / "src/main/java").mkdir(parents=True)
    (bench / "build.gradle.kts").write_text(
        "import com.example.build.configureFlavors\n"
        "plugins { id(\"com.android.test\") }\n"
        "android {\n  configureFlavors(this) { flavor -> }\n  targetProjectPath = \":app\"\n"
        "  buildTypes { create(\"benchmark\") { } }\n}\n", encoding="utf-8")
    (bench / "src/main/java/StartupBenchmark.kt").write_text(
        "package com.example.benchmark\nimport androidx.benchmark.macro.junit4.MacrobenchmarkRule\n"
        "class StartupBenchmark {\n  @Test\n"
        "  fun startup() = rule.measureRepeated(packageName = TARGET, metrics = listOf(StartupTimingMetric())) {}\n}\n",
        encoding="utf-8")
    facts = scan.describe(root, devices=False)
    b = facts.benchmarks[0]
    check("the module's own flavours, from the call in its script", b["flavors"] == ["demo", "prod"], b)
    check("in its task names", scan.gradle_tasks(b, facts.variants)
          == [":benchmark:connectedDemoBenchmarkAndroidTest", ":benchmark:connectedProdBenchmarkAndroidTest"],
          scan.gradle_tasks(b, facts.variants))


def test_a_precompiled_script_plugin_declares_the_flavours(tmp_path: Path) -> None:
    (tmp_path / "buildSrc/src/main/kotlin").mkdir(parents=True)
    (tmp_path / "buildSrc/src/main/kotlin/example.app-flavors.gradle.kts").write_text(
        "android {\n  flavorDimensions += \"store\"\n  productFlavors {\n"
        "    create(\"play\") { dimension = \"store\" }\n"
        "    create(\"foss\") { dimension = \"store\"; applicationIdSuffix = \".foss\" }\n  }\n}\n",
        encoding="utf-8")
    app = tmp_path / "app"
    (app / "src/main").mkdir(parents=True)
    (app / "build.gradle.kts").write_text(
        'plugins { id("com.android.application"); id("example.app-flavors") }\n'
        'android { namespace = "com.example.app"\n  defaultConfig { applicationId = "com.example.app" } }\n',
        encoding="utf-8")
    (app / "src/main/AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application>'
        f'<activity android:name=".MainActivity">{selftest._LAUNCHER_FILTER}</activity></application></manifest>',
        encoding="utf-8")
    facts = scan.describe(tmp_path, devices=False)
    ids = {v["name"]: v["application_id"] for v in facts.variants}
    check("its flavours, and foss's suffix", ids["fossRelease"] == "com.example.app.foss"
          and ids["playRelease"] == "com.example.app", ids)


def test_a_loop_this_does_not_read_is_said_and_names_no_flavour(tmp_path: Path) -> None:
    root = _kotlin(tmp_path)
    flavor = root / "build-logic/convention/src/main/kotlin/com/example/build/AppFlavor.kt"
    flavor.write_text(flavor.read_text(encoding="utf-8").replace("register(flavor.name)", "register(\"x\" + flavor)"),
                      encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("no flavour named forEach, or any", facts.flavors == [], facts.flavors)
    check("and the note says where", any("productFlavors in build-logic/" in n and "is not read here" in n
                                         for n in facts.notes), facts.notes)


def test_a_loop_in_the_apps_own_script_is_read_too(tmp_path: Path) -> None:
    root = _kotlin(tmp_path)
    script = root / "app/build.gradle.kts"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "alias(libs.plugins.example.android.application.flavors)\n", "").replace(
        "    buildTypes {", "    productFlavors {\n        AppFlavor.values().forEach { register(it.name) {\n"
        "            dimension = it.dimension.name\n            applicationIdSuffix = it.applicationIdSuffix\n"
        "        } }\n    }\n    buildTypes {"), encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("the enum's entries, not a flavour called forEach", [f["name"] for f in facts.flavors] == ["demo", "prod"],
          facts.flavors)


# --- the Groovy project: buildSrc constants, and a suffix nothing reads -------------------

def test_constants_of_buildsrc_are_read_and_an_unread_suffix_is_said(tmp_path: Path) -> None:
    facts = scan.describe(_groovy(tmp_path), devices=False)
    by = {v["name"]: v for v in facts.variants}
    check("the applicationId, from AppConfig", facts.app["application_id"] == "com.example.app"
          and facts.app["application_id_from"] == "`AppConfig.APPLICATION_ID` in buildSrc/src/main/kotlin/AppConfig.kt",
          facts.app)
    check("free's suffix, from AppConfig", by["freeDebug"]["application_id"] == "com.example.app.free.debug", by)
    check("qa's suffix is unread, and its variants say so", by["freeQa"].get("unread") and by["paidQa"].get("unread"),
          by["paidQa"])
    check("in the table, + ?", "| paidQa | `com.example.app` + ? |" in scan.render(facts), scan.render(facts))


def test_an_unread_part_of_the_package_to_measure_is_not_derived(tmp_path: Path) -> None:
    root = _groovy(tmp_path)
    script = root / "app/build.gradle"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "qa { initWith debug;", "release { applicationIdSuffix rootProject.ext.storeSuffix }\n        qa { initWith debug;"),
        encoding="utf-8")
    facts = scan.describe(root, devices=False)
    drafted = project_setup.draft(facts)
    check("the page does not write it", not drafted["can"] and "could not read its name" in drafted["plain"], drafted)
    text = "\n".join(scan.skeleton(facts))
    check("default, and how to find it", "  _source: default" in text
          and "`adb shell pm list packages com.example.app.free` lists the installed ones" in text, text)


# --- the launcher the draft names --------------------------------------------------------

@pytest.mark.parametrize("manifest,namespace,expected", [
    (f'<activity android:name=".MainActivity">{selftest._LAUNCHER_FILTER}</activity>', "com.example.app",
     ["com.example.app.MainActivity"]),
    (f'<activity android:name="MainActivity">{selftest._LAUNCHER_FILTER}</activity>', "com.example.app",
     ["com.example.app.MainActivity"]),
    (f'<activity android:name="org.other.Main">{selftest._LAUNCHER_FILTER}</activity>', "com.example.app",
     ["org.other.Main"]),
    (f'<activity android:name=".MainActivity">{selftest._LAUNCHER_FILTER}</activity>', None, [None]),
    (f'<activity android:name="${{launcher}}">{selftest._LAUNCHER_FILTER}</activity>', "com.example.app", [None]),
])
def test_a_launcher_by_its_full_name(manifest: str, namespace: str | None, expected: list) -> None:
    found = scan.launcher_components(f"<manifest><application>{manifest}</application></manifest>", namespace)
    check(manifest, [c["name"] for c in found] == expected, found)


def test_the_launch_draft_names_the_enabled_alias_and_the_page_writes_it(tmp_path: Path) -> None:
    facts = scan.describe(_kotlin(tmp_path), devices=False)
    check("the alias that is on, not the trampoline or the one that is off",
          facts.app["activity"] == "com.example.app.Launcher", facts.app)
    drafted = project_setup.draft(facts)
    check("the page can write it", drafted["can"], drafted)
    check("with runner.activity", drafted["raw"]["runner"]["activity"] == "com.example.app.Launcher", drafted["raw"])
    (tmp_path / "echolot.yml").write_text(drafted["text"], encoding="utf-8")
    check("the loader reads it", runner.ACTIVITY.read(Config.load(tmp_path / "echolot.yml").runner)
          == "com.example.app.Launcher")


def test_a_benchmark_draft_names_no_activity(tmp_path: Path) -> None:
    """Gradle mode: the benchmark starts the app, and the key is launch mode's."""
    root = tmp_path
    selftest._android_repo(root)
    text = "\n".join(scan.skeleton(scan.describe(root, devices=False)))
    check("no activity line", "activity:" not in text, text)


# --- collect and several launchers -------------------------------------------------------

CHOOSER = ("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n"
           "android/com.android.internal.app.ResolverActivity\n")
BRIEF = ("2 activities found:\n  Activity #0:\n    priority=0 preferredOrder=0 match=0x108000\n"
         "    com.example.app.demo.debug/com.example.app.Launcher\n  Activity #1:\n"
         "    priority=0 preferredOrder=0 match=0x108000\n"
         "    com.example.app.demo.debug/leakcanary.internal.activity.LeakLauncherActivity\n")


def _adb(monkeypatch, query: str | Exception) -> list[list[str]]:
    asked: list[list[str]] = []

    def run(args, **kw):
        asked.append(args)
        if "resolve-activity" in args:
            return CHOOSER
        if isinstance(query, Exception):
            raise query
        return query
    monkeypatch.setattr(runner, "_run", run)
    return asked


def test_the_launchers_are_listed_and_the_projects_own_named(tmp_path: Path, monkeypatch) -> None:
    root = _kotlin(tmp_path)
    asked = _adb(monkeypatch, BRIEF)
    with pytest.raises(runner.RunnerError) as e:
        runner.resolve_activity("SERIAL", "com.example.app.demo.debug", root)
    said = str(e.value)
    check("both, as components", "instead of one of them: com.example.app.demo.debug/com.example.app.Launcher, "
          "com.example.app.demo.debug/leakcanary.internal.activity.LeakLauncherActivity" in said, said)
    check("the project's own, as the line to write",
          'declares com.example.app.Launcher, so `activity: "com.example.app.Launcher"` under `runner:`' in said, said)
    check("asked for MAIN and LAUNCHER in the package", "query-activities" in asked[-1] and asked[-1][-5:] == [
        "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "com.example.app.demo.debug"],
        asked)


def test_without_the_list_the_error_still_stands(monkeypatch) -> None:
    _adb(monkeypatch, runner.RunnerError("Unknown command: query-activities"))
    with pytest.raises(runner.RunnerError, match="more than one launcher activity") as e:
        runner.resolve_activity("SERIAL", "com.example.app")
    check("no list, no suggestion", "instead of one of them" not in str(e.value)
          and "declares" not in str(e.value) and "Set runner.activity" in str(e.value), str(e.value))


def test_the_components_flag_form_is_read_too(monkeypatch) -> None:
    _adb(monkeypatch, "com.example.app/.MainActivity\ncom.example.app/leakcanary.Leak\n")
    check("short form expanded", runner.launchers_on("SERIAL", "com.example.app")
          == ["com.example.app/com.example.app.MainActivity", "com.example.app/leakcanary.Leak"])


@pytest.mark.parametrize("written,started", [
    ("com.example.app.Launcher", "com.example.app.demo/com.example.app.Launcher"),
    ("com.example.app.demo/.Launcher", "com.example.app.demo/.Launcher"),
    ("  com.example.app.Launcher ", "com.example.app.demo/com.example.app.Launcher"),
])
def test_runner_activity_starts_a_class_in_the_package(written: str, started: str) -> None:
    check(written, runner.started(written, "com.example.app.demo") == started)


def test_a_name_under_the_namespace_is_refused() -> None:
    with pytest.raises(runner.RunnerError, match="full name"):
        runner.started(".Launcher", "com.example.app.demo")
