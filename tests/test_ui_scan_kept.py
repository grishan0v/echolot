#!/usr/bin/env python3
"""`echolot ui` keeps what `scan` read of the repository between pages (#931).

On a repository of about a million lines `scan` took 9.6 s of CPU on an idle
machine and 88 s on a busy one, and the start page ran it at every view and
once more for every press: pages took up to two and a half minutes. What is
pinned here:

  - the repository is read once for many pages and presses, and the devices
    are asked at every page;
  - it is read again when a file `scan` reads changes: a build script, a
    manifest, a version catalog, the build code, a benchmark's source; and
    when a source comes or goes anywhere;
  - a source edited in any other module leaves the reading as it is;
  - Refresh forgets the reading, and the next page takes it anew;
  - every page gets the facts as they were read, not as the last page left
    them; a reading that fails is not kept;
  - the start page says when the repository was read.

adb is the double of `tests/test_ui.py`. Nothing needs a phone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import ready, scan_cache  # noqa: E402
from echolot import scan as scan_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report_page import read  # noqa: E402
from tests.test_ui import BENCHMARK, Adb, Project, get, site_of  # noqa: E402

BENCH = ("package com.example.benchmark\nimport androidx.benchmark.macro.junit4.MacrobenchmarkRule\n"
         "class StartupBenchmark {\n    @get:Rule val rule = MacrobenchmarkRule()\n    @Test\n"
         "    fun startup() = Unit\n}\n")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


@pytest.fixture
def adb(monkeypatch):
    return Adb(monkeypatch)


@pytest.fixture
def project(tmp_path, monkeypatch, adb) -> Project:
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def touched(path: Path, text: str | None = None) -> None:
    """A file written again: new contents where given, and a later time either way."""
    if text is not None:
        path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def source(root: Path, module: str, name: str, text: str = "class A\n") -> Path:
    folder = root / module / "src" / "main" / "java" / "com" / "example"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def pages(site, n: int = 1) -> None:
    for _ in range(n):
        reply = get(site)
        check("the start page is drawn", reply.status == 200, reply.body[:300])


def test_many_pages_and_a_press_read_the_repository_once_and_ask_for_the_phone_each_time(project, adb):
    site = site_of(project.ready())
    pages(site, 3)
    check("one reading for three pages", site.src.kept.reads == 1, site.src.kept.reads)
    asked = [a for a in adb.asked if a[:2] == ["adb", "devices"]]
    check("and the devices asked at every one", len(asked) >= 3, adb.asked)
    reply = get(site, "check", "POST", Origin=site.origin)
    check("a press asks whether a recording can start without reading the repository again",
          site.src.kept.reads == 1, (reply.status, site.src.kept.reads))


def test_a_phone_that_comes_shows_on_the_next_page_though_nothing_was_read_again(project, adb):
    adb.listing = ""
    site = site_of(project.ready())
    page = read(get(site).body)
    check("no phone first", "adb sees no devices" in page.one("ul", "ready").text, page.one("ul", "ready").text)
    adb.listing = "EXAMPLE0001          device product:shiba model:Pixel_8 device:shiba transport_id:1\n"
    page = read(get(site).body)
    check("then the phone", "Pixel_8" in page.one("ul", "ready").text, page.one("ul", "ready").text)
    check("with the repository read once", site.src.kept.reads == 1, site.src.kept.reads)


@pytest.mark.parametrize("what", ["build script", "manifest", "settings", "version catalog", "build code",
                                  "submodules"])
def test_a_file_scan_reads_by_name_read_again_when_it_changes(project, what):
    project.ready()
    root = project.root
    (root / "gradle").mkdir()
    (root / "gradle" / "libs.versions.toml").write_text("[plugins]\n", encoding="utf-8")
    source(root, "buildSrc", "Flavors.kt", "object Flavors\n")
    # Which modules are another repository's (#1003).
    (root / ".gitmodules").write_text("", encoding="utf-8")
    site = site_of(project)
    pages(site)
    path = {"build script": root / "app" / "build.gradle.kts",
            "manifest": root / "app" / "src" / "main" / "AndroidManifest.xml",
            "settings": root / "settings.gradle.kts",
            "version catalog": root / "gradle" / "libs.versions.toml",
            "build code": root / "buildSrc" / "src" / "main" / "java" / "com" / "example" / "Flavors.kt",
            "submodules": root / ".gitmodules"}[what]
    touched(path)
    pages(site, 2)
    check(f"a {what} changed: read once more, and only once", site.src.kept.reads == 2, site.src.kept.reads)


def test_a_source_that_comes_or_goes_reads_the_repository_again(project):
    site = site_of(project.ready())
    pages(site)
    added = source(project.root, "feature", "Screen.kt")
    pages(site)
    check("a source added", site.src.kept.reads == 2, site.src.kept.reads)
    added.unlink()
    pages(site)
    check("a source removed", site.src.kept.reads == 3, site.src.kept.reads)


def test_a_source_edited_in_an_ordinary_module_leaves_the_reading_as_it_is(project):
    project.ready()
    screen = source(project.root, "app", "Screen.kt")
    site = site_of(project)
    pages(site)
    touched(screen, "class Screen { val changed = 1 }\n")
    pages(site, 2)
    check("nothing scan says follows from it", site.src.kept.reads == 1, site.src.kept.reads)


def test_a_benchmarks_source_edited_reads_the_repository_again(project):
    bench = source(project.root, "benchmark", "StartupBenchmark.kt", BENCH)
    (project.root / "benchmark" / "build.gradle.kts").write_text('plugins { id("com.android.test") }\n',
                                                                 encoding="utf-8")
    project.app().config(BENCHMARK).agents().doctor()
    site = site_of(project)
    pages(site)
    check("the benchmark is read", site.src.kept.reads == 1, site.src.kept.reads)
    touched(bench, BENCH.replace("fun startup()", "fun startupCold()"))
    pages(site)
    check("its source counts by what it says", site.src.kept.reads == 2, site.src.kept.reads)


def test_refresh_forgets_the_reading_and_sends_the_browser_to_the_start_page(project):
    site = site_of(project.ready())
    pages(site)
    page = read(get(site).body)
    refresh = [a for a in page.all("a", "btn") if a.text == "Refresh"]
    check("Refresh leads to the address that reads anew", len(refresh) == 1
          and refresh[0].attrs["href"] == f"/{site.token}/anew", [a.attrs for a in refresh])
    reply = get(site, "anew")
    check("it sends the browser on to the start page",
          reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/", (reply.status, reply.headers))
    check("and writes nothing", not (project.root / ".echolot" / "log" / "check.json").exists())
    pages(site)
    check("the next page reads the repository anew", site.src.kept.reads == 2, site.src.kept.reads)


def test_refresh_where_nothing_is_kept_is_a_way_to_the_start_page(project):
    facts = scan_mod.Facts(root=str(project.root))
    site = site_of(project.ready(), ready.Sources(scan=lambda root: facts, pick_device=lambda serial: "EXAMPLE0001"))
    reply = get(site, "anew")
    check("a double that keeps nothing is not asked to forget",
          reply.status == 303 and dict(reply.headers)["Location"] == f"/{site.token}/", reply.status)


def test_every_page_gets_the_facts_as_they_were_read(project):
    kept = scan_cache.Kept(ask_devices=lambda facts: None)
    project.ready()
    first = kept(project.root)
    first.notes.append("a note a page added")
    first.variants.clear()
    second = kept(project.root)
    check("not as the last page left them", "a note a page added" not in second.notes and second.variants,
          (second.notes, second.variants))
    check("from one reading", kept.reads == 1, kept.reads)


def test_a_reading_that_fails_is_not_kept(project):
    calls = []

    def describe(root):
        calls.append(root)
        if len(calls) == 2:
            raise OSError("the disk said no")
        return scan_mod.Facts(root=str(root))
    kept = scan_cache.Kept(describe=describe, ask_devices=lambda facts: None)
    project.ready()
    kept(project.root)
    touched(project.root / "app" / "build.gradle.kts")
    with pytest.raises(OSError):
        kept(project.root)
    check("the reading before is not handed out after one that failed", kept.read_at is None)
    kept(project.root)
    check("the next page reads again", len(calls) == 3, len(calls))


def test_the_start_page_says_when_the_repository_was_read(project):
    site = site_of(project.ready())
    page = read(get(site).body)
    said = page.one("details", id="settings").text
    check("under All settings, with what reads it again",
          "echolot read the repository moments ago. It reads it again when a build script, a manifest or the "
          "build code changes, when a source file comes or goes, and at Refresh." in said, said)
    facts = scan_mod.Facts(root=str(project.root))
    page = read(get(site_of(project, ready.Sources(scan=lambda root: facts,
                                                   pick_device=lambda serial: "EXAMPLE0001"))).body)
    check("nothing is said where every page reads it",
          "echolot read the repository" not in page.one("details", id="settings").text)


def test_the_stamp_walks_what_scan_walks(project):
    project.ready()
    root = project.root
    source(root, "app", "Screen.kt")
    for skipped in ("build", ".gradle", "node_modules", ".echolot"):
        (root / skipped).mkdir(exist_ok=True)
        (root / skipped / "Generated.kt").write_text("class G\n", encoding="utf-8")
    seen = scan_cache.stamp(root, scan_cache.watched(root, None))
    check("the sources, the scripts and the manifest, by their path from the root",
          {"app/build.gradle.kts", "settings.gradle.kts", "app/src/main/AndroidManifest.xml",
           "app/src/main/java/com/example/Screen.kt"} <= set(seen), sorted(seen))
    check("nothing under a folder scan leaves out", not any(rel.endswith("Generated.kt") for rel in seen), sorted(seen))
    check("a source counts by being there, a build script by its time and size",
          seen["app/src/main/java/com/example/Screen.kt"] is None and seen["app/build.gradle.kts"] is not None)
