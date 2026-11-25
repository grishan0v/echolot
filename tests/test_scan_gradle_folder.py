"""`echolot scan` on a repository whose Gradle build is in a folder of its own.

Three readings that went wrong together on one open-source app: a git
submodule's example app sorted before the app (#1003), the applicationId was
kept in the root build script's `ext` (#1004), and the build type to measure
on was made profileable by its own source set's manifest, of the flavour
`isDefault` marks (#1006). The fixture is the doctor's
(`selftest._gradle_in_a_folder_repo`); the cases around it are here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import scan, selftest  # noqa: E402
from tests.support import check  # noqa: E402

MANIFEST = ('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application>'
            f'<activity android:name=".MainActivity">{selftest._LAUNCHER_FILTER}</activity>'
            '</application></manifest>\n')


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    selftest._gradle_in_a_folder_repo(tmp_path)
    return tmp_path


def _app(root: Path, rel: str, script: str = "android { namespace 'com.example.app' }\n") -> None:
    _write(root, f"{rel}/build.gradle", script)
    _write(root, f"{rel}/src/main/AndroidManifest.xml", MANIFEST)


# --- #1003: which module is the app ------------------------------------------------------

def test_the_app_of_the_project_s_own_build_comes_before_a_submodule_s_example(tmp_path: Path) -> None:
    facts = scan.describe(_repo(tmp_path), devices=False)
    draft = "\n".join(scan.skeleton(facts))
    text = scan.render(facts)
    check("android/app, by its own build's module path", facts.app["dir"] == "android/app"
          and facts.app["module"] == ":app" and facts.app["gradle_root"] == "android", facts.app)
    check("the module line says where the build is", "App module `:app` (android/app) · Gradle build in `android/`"
          in text, text[:400])
    check("the submodule's example is named as such",
          any("(vendor/lib/examples/demo/android/app, in the git submodule vendor/lib)" in n for n in facts.notes),
          facts.notes)
    check("and the note says where ./gradlew runs",
          any(n.startswith("the Gradle build is in android/ (android/settings.gradle): `./gradlew` runs there")
              for n in facts.notes), facts.notes)
    check("the draft's paths are from the root", "android/app/build/outputs/mapping/bProfileable/mapping.txt" in draft
          and '    - "android/app/src/main"' in draft, draft)
    check("and nothing of the example's", "org.example.demo" not in draft and "vendor/" not in draft, draft)


def test_read_from_its_own_folder_the_build_is_the_root(tmp_path: Path) -> None:
    facts = scan.describe(_repo(tmp_path) / "android", devices=False)
    check("no folder to name", facts.app["dir"] == "app" and "gradle_root" not in facts.app, facts.app)
    check("no note about one", not any("Gradle build is in" in n for n in facts.notes), facts.notes)


def test_a_folder_named_third_party_is_another_project_s(tmp_path: Path) -> None:
    _app(tmp_path, "third_party/sdk/sample")
    _app(tmp_path, "mobile")
    facts = scan.describe(tmp_path, devices=False)
    check("mobile, not the third-party sample", facts.app["dir"] == "mobile", facts.app)
    check("the sample said to be another project's",
          any("third_party/sdk/sample, under third_party/, another project's code" in n for n in facts.notes),
          facts.notes)


def test_a_module_the_settings_leave_out_comes_after_one_they_include(tmp_path: Path) -> None:
    _write(tmp_path, "settings.gradle.kts", 'include(\n    ":mobile",\n    ":lib"\n)\n')
    _app(tmp_path, "demo")
    _app(tmp_path, "mobile")
    facts = scan.describe(tmp_path, devices=False)
    check("mobile, which settings include", facts.app["dir"] == "mobile", facts.app)
    check("demo, which they do not", any(":demo (demo, which settings.gradle.kts does not include)" in n
                                         for n in facts.notes), facts.notes)


def test_of_two_included_the_shallower_then_the_one_named_app(tmp_path: Path) -> None:
    _write(tmp_path, "settings.gradle", "include ':wear', ':app', ':sdk:widgets:demo'\n")
    for rel in ("sdk/widgets/demo", "wear", "app"):
        _app(tmp_path, rel)
    check("app", scan.describe(tmp_path, devices=False).app["module"] == ":app")


def test_a_project_dir_the_settings_move_is_included(tmp_path: Path) -> None:
    _write(tmp_path, "settings.gradle", "include ':demo'\ninclude ':app'\n"
                                        "project(':app').projectDir = file('apps/main')\n")
    _app(tmp_path, "apps/main")
    _app(tmp_path, "demo")
    check("included", scan.included(tmp_path / "settings.gradle") == {"demo", "apps/main"},
          scan.included(tmp_path / "settings.gradle"))


def test_only_a_submodule_s_app_is_read_and_said_to_be_one(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for path in sorted((root / "android").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    facts = scan.describe(root, devices=False)
    check("the example, since nothing else is there", facts.app["dir"].startswith("vendor/lib/"), facts.app)
    check("said so, with --root", any("is in the git submodule vendor/lib: no other module" in n
                                      and "--root" in n for n in facts.notes), facts.notes)


@pytest.mark.parametrize("text,expected", [
    ("include ':app'", {"app"}),
    ("include ':app', ':feature:home'", {"app", "feature/home"}),
    ('include(":app")\ninclude(":core")', {"app", "core"}),
    ("include 'app'", {"app"}),
    ('includeBuild("build-logic")', None),
    ("listOf(\":a\").forEach { include(it) }", None),
])
def test_what_a_settings_file_includes(tmp_path: Path, text: str, expected: set[str] | None) -> None:
    _write(tmp_path, "settings.gradle", text + "\n")
    check(text, scan.included(tmp_path / "settings.gradle") == expected, scan.included(tmp_path / "settings.gradle"))


# --- #1004: an applicationId in the root build script's ext ------------------------------

def test_the_application_id_kept_in_the_root_script_s_ext_is_read(tmp_path: Path) -> None:
    facts = scan.describe(_repo(tmp_path), devices=False)
    text = scan.render(facts)
    check("read, with where", facts.app["application_id"] == "com.example.app"
          and facts.app["application_id_from"] == "`rootProject.ext.appId` in android/build.gradle", facts.app)
    check("every variant's package whole", all(v["application_id"] and not v.get("unread") for v in facts.variants),
          facts.variants)
    check("no placeholder", "com.example.app.profileable" in "\n".join(scan.skeleton(facts)))
    check("said under the table", "applicationId of defaultConfig is `rootProject.ext.appId` in android/build.gradle: "
          "`com.example.app`" in text, text)


@pytest.mark.parametrize("root_script,read", [
    ("ext { appId = 'com.example.app' }", "rootProject.ext.appId"),
    ("ext.appId = 'com.example.app'", "rootProject.appId"),
    ("rootProject.ext.set('appId', 'com.example.app')", "appId"),
    ("project.ext { appId = \"com.example.app\" }", "project.appId"),
    ('extra["appId"] = "com.example.app"', 'rootProject.extra["appId"] as String'),
    ('val appId by extra("com.example.app")', 'rootProject.extra.get("appId").toString()'),
])
def test_each_way_to_write_it_and_read_it_back(tmp_path: Path, root_script: str, read: str) -> None:
    root = _repo(tmp_path)
    (root / "android/build.gradle").write_text(root_script + "\n", encoding="utf-8")
    script = root / "android/app/build.gradle"
    script.write_text(script.read_text(encoding="utf-8").replace("rootProject.ext.appId", read), encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check(read, facts.app["application_id"] == "com.example.app", facts.app)


def test_the_module_s_own_ext_is_its_own(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    script = root / "android/app/build.gradle"
    script.write_text("ext { appId = 'com.example.own' }\n" + script.read_text(encoding="utf-8").replace(
        "rootProject.ext.appId", "ext.appId"), encoding="utf-8")
    check("the module's", scan.describe(root, devices=False).app["application_id"] == "com.example.own")


@pytest.mark.parametrize("root_script", [
    "ext { appId = baseId + '.app' }",
    "ext { appId = 'com.example.app' }\next.appId = 'com.example.other'",
    "ext { def appId = 'com.example.app' }",
])
def test_a_value_that_is_not_one_string_is_not_read(tmp_path: Path, root_script: str) -> None:
    root = _repo(tmp_path)
    (root / "android/build.gradle").write_text(root_script + "\n", encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("unread", facts.app["application_id"] is None, facts.app)
    check("and quoted as the script writes it",
          any("`applicationId rootProject.ext.appId` in defaultConfig of android/app/build.gradle" in n
              for n in facts.notes), facts.notes)


# --- #1006: a source set's manifest, and the default flavour -----------------------------

def test_a_build_type_its_own_manifest_makes_profileable_is_measured_on(tmp_path: Path) -> None:
    facts = scan.describe(_repo(tmp_path), devices=False)
    by = {v["name"]: v for v in facts.variants}
    check("bProfileable first", scan.preferred(facts.variants)["name"] == "bProfileable", facts.variants)
    check("profileable by its manifest", by["aProfileable"]["profileable_manifest"]
          == "android/app/src/profileable/AndroidManifest.xml", by["aProfileable"])
    check("release still says the manifest decides", by["bRelease"]["measure"].startswith("release"), by["bRelease"])
    check("minified through initWith, its mapping named", by["bProfileable"]["mapping"]
          == "android/app/build/outputs/mapping/bProfileable/mapping.txt", by["bProfileable"])
    check("no note of missing slices", not any("has no <profileable" in n for n in facts.notes), facts.notes)
    check("the default flavour said", "`b` is the flavour `isDefault = true` marks" in scan.render(facts))


def test_a_flavour_s_own_manifest_makes_its_variants_alone_profileable(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    tagged = root / "android/app/src/profileable/AndroidManifest.xml"
    _write(root, "android/app/src/aRelease/AndroidManifest.xml", tagged.read_text(encoding="utf-8"))
    tagged.unlink()
    facts = scan.describe(root, devices=False)
    check("aRelease, the one variant with the tag", facts.app["profileable_by"] == ["aRelease"], facts.app)
    check("and the one to measure on, over the default flavour's release",
          scan.preferred(facts.variants)["name"] == "aRelease", facts.variants)


@pytest.mark.parametrize("tag", [
    '<profileable android:shell="false" />',
    '<profileable android:shell="true" android:enabled="false" />',
    '<!-- <profileable android:shell="true" /> -->',
])
def test_a_tag_that_does_not_make_it_profileable(tmp_path: Path, tag: str) -> None:
    root = _repo(tmp_path)
    _write(root, "android/app/src/profileable/AndroidManifest.xml", f"<manifest><application>{tag}</application></manifest>")
    facts = scan.describe(root, devices=False)
    check(tag, scan.preferred(facts.variants)["name"] == "bRelease", facts.variants)


def test_a_test_source_set_is_not_the_app(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    tag = (root / "android/app/src/profileable/AndroidManifest.xml").read_text(encoding="utf-8")
    (root / "android/app/src/profileable/AndroidManifest.xml").unlink()
    _write(root, "android/app/src/androidTestProfileable/AndroidManifest.xml", tag)
    check("none", scan.profileable_source_sets(root, scan.describe(root, devices=False).app) == {})


@pytest.mark.parametrize("body,expected", [
    ("isDefault = true", True),
    ("isDefault true", True),
    ("setDefault(true)", True),
    ("isDefault = false", False),
    ("dimension 'store'", False),
])
def test_what_marks_a_flavour_default(body: str, expected: bool) -> None:
    check(body, scan._is_default(body) is expected)


def test_without_is_default_the_flavour_is_taken_by_name(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    script = root / "android/app/build.gradle"
    script.write_text(script.read_text(encoding="utf-8").replace("isDefault = true", ""), encoding="utf-8")
    facts = scan.describe(root, devices=False)
    check("aProfileable", scan.preferred(facts.variants)["name"] == "aProfileable", facts.variants)
    check("and nothing said of a default", "isDefault" not in scan.render(facts))
