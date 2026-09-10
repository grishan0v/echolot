#!/usr/bin/env python3
"""From a row of the report to a line in the checkout — `echolot/place.py`.

Pure functions over strings the runtime writes and a directory tree, so
every case here is a fact about a shape ART produces or a repository has,
and none needs a trace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import place  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402

# The slice as it came off a Galaxy A51 on Android 13, release build: the
# runtime had no line for either side.
REAL = ("monitor contention with owner DefaultDispatcher-worker-3 (28463) at void "
        "jdk.internal.misc.Unsafe.park(boolean, long)(Unsafe.java:-2) waiters=0 "
        "blocking from void com.example.app.data.StoreRepository"
        ".update(java.lang.String)(StoreRepository.kt:-1)")
# The fixture's, with a line on the owner and nothing at all on the waiter.
FIXTURE = ("monitor contention with owner Thread-3 (4455) at void "
           "com.example.Store.put(java.lang.String)(Store.java:41) waiters=0 "
           "blocking from java.lang.Object com.example.Store.get()(:-1)")


def checkout(root: Path) -> Path:
    """Two modules, a name that lives in both, and a release-build target."""
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    for module, pkg in (("domain", "com/example/app/data"),
                        ("data", "com/example/app/cache")):
        src = root / module / "src/main/java" / pkg
        src.mkdir(parents=True)
        (root / module / "build.gradle.kts").write_text("", encoding="utf-8")
    (root / "domain/src/main/java/com/example/app/data/StoreRepository.kt").write_text(
        "package com.example.app.data\n"
        "\n"
        "class StoreRepository {\n"
        "  private val cache by lazy { Cache() }\n"
        "  @Synchronized\n"
        "  fun update(key: String) {\n"
        "    if (update(key)) return\n"
        "  }\n"
        "  fun <T> findSelected(): T = TODO()\n"
        "}\n", encoding="utf-8")
    # The same file name in another module — the package tells them apart.
    (root / "data/src/main/java/com/example/app/cache/StoreRepository.kt").write_text(
        "package com.example.app.cache\nclass StoreRepository\n", encoding="utf-8")
    java = root / "data/src/main/java/com/example"
    java.mkdir(parents=True, exist_ok=True)
    (java / "Store.java").write_text(
        "package com.example;\n"
        "public class Store {\n"
        "    public void put(String key) {\n"
        "        if (get()) {\n"            # a call, not the declaration
        "        }\n"
        "    }\n"
        "    public Object get() {\n"
        "        return null;\n"
        "    }\n"
        "}\n", encoding="utf-8")
    return root


# --- the strings the runtime writes ------------------------------------------

def test_both_frames_come_off_a_contention_slice():
    got = place.parse_contention(REAL)
    check("the owner thread", got["owner_thread"] == "DefaultDispatcher-worker-3", got)
    check("the waiter count", got["waiters"] == 0, got)
    owner = place.parse_frame(got["at"])
    check("the owner's frame, return type dropped",
          owner == ("jdk.internal.misc.Unsafe.park", "Unsafe.java", None), owner)
    blocked = place.parse_frame(got["blocked"])
    check("the waiter's frame, with the file the runtime named",
          blocked == ("com.example.app.data.StoreRepository.update",
                      "StoreRepository.kt", None), blocked)
    check("a line of -1 is no line", blocked[2] is None, blocked)


def test_the_other_shape_and_an_empty_file_part_parse_to_nothing_or_no_file():
    check("the tid-only shape names nobody",
          place.parse_contention("Lock contention on a monitor lock (owner tid: 13533)") is None, "")
    frame = place.parse_frame("java.lang.Object com.example.Store.get()(:-1)")
    check("an empty file part keeps the symbol and drops the file",
          frame == ("com.example.Store.get", None, None), frame)
    dynamite = place.parse_frame(
        "m140.gac m140.fyy.a()(:com.google.android.gms.policy_maps_core_dynamite@2608@2608.95:847)")
    check("a dependency coordinate is not a file",
          dynamite == ("m140.fyy.a", None, None), dynamite)
    lined = place.parse_frame("void okhttp3.internal.concurrent.TaskRunner$runnable$1.run()(TaskRunner.kt:365)")
    check("a real line comes through", lined == ("okhttp3.internal.concurrent.TaskRunner$runnable$1.run",
                                                 "TaskRunner.kt", 365), lined)


# --- the checkout -------------------------------------------------------------

def test_the_package_picks_between_two_files_of_one_name(tmp_path):
    root = checkout(tmp_path)
    idx = place.index(root)
    check("two candidates for the name", len(idx["StoreRepository.kt"]) == 2, idx)
    got = place.locate("com.example.app.data.StoreRepository.update",
                       "StoreRepository.kt", None, idx, root, "blocked")
    check("the domain module's, by package",
          got.file == "domain/src/main/java/com/example/app/data/StoreRepository.kt", got)
    check("and it is exact", got.exact, got)
    check("with no line from the runtime, the declaration's line",
          got.line == 6, got)


def test_a_release_build_frame_lands_on_the_declaration_not_a_call(tmp_path):
    root = checkout(tmp_path)
    idx = place.index(root)
    got = place.locate("com.example.Store.get", None, None, idx, root, "blocked")
    check("the file comes from the class when the runtime named none",
          got.file == "data/src/main/java/com/example/Store.java", got)
    check("the declaration on line 7, not the call on line 4", got.line == 7, got)
    generic = place.locate("com.example.app.data.StoreRepository.findSelected",
                           "StoreRepository.kt", None, idx, root, "owner")
    check("a generic Kotlin function is still found", generic.line == 9, generic)
    lazy = place.locate("com.example.app.data.StoreRepository.cache_delegate$lambda$0",
                        "StoreRepository.kt", None, idx, root, "owner")
    check("a lazy delegate's synthetic name lands on the val", lazy.line == 4, lazy)


def test_a_symbol_not_in_the_checkout_keeps_its_name_and_no_file(tmp_path):
    root = checkout(tmp_path)
    got = place.locate("jdk.internal.misc.Unsafe.park", "Unsafe.java", None, place.index(root), root, "owner")
    check("no file", got.file is None and got.line is None, got)
    check("the symbol stays — an owner parked in the JDK is a fact about the lock",
          got.symbol == "jdk.internal.misc.Unsafe.park", got)


# --- the report ---------------------------------------------------------------

def test_annotate_places_both_sides_and_a_class_location(tmp_path):
    root = checkout(tmp_path)
    rep = {"detectors": [
        {"id": "monitor_contention", "rows": [
            {"location": "DefaultDispatch", "total_ms": 6862.0, "detail": REAL},
            {"location": "Firebase Backgr", "total_ms": 70.0,
             "detail": "Lock contention on a monitor lock (owner tid: 1)"},
        ]},
        {"id": "main_thread_block", "rows": [
            {"location": "com.example.Store", "self_ms": 400.0, "detail": "main"},
            {"location": "android.view.View", "self_ms": 10.0, "detail": "main"},
            {"location": "inflate", "self_ms": 5.0, "detail": "main"},
        ]},
    ]}
    placed = place.annotate(rep, root)
    check("two rows placed", placed == 2, placed)
    lock = rep["detectors"][0]["rows"][0]
    roles = {p["role"]: p for p in lock["places"]}
    check("the owner is kept without a file",
          roles["owner"]["file"] is None and roles["owner"]["symbol"].endswith("Unsafe.park"), roles)
    check("the waiter is placed at its declaration",
          roles["blocked"]["file"].endswith("data/StoreRepository.kt") and roles["blocked"]["line"] == 6, roles)
    check("the markdown cell names the side and the file",
          lock["code"] == "blocked at StoreRepository.kt:6", lock["code"])
    check("the tid-only row is left alone", "places" not in rep["detectors"][0]["rows"][1], rep)
    block = rep["detectors"][1]["rows"]
    check("a class location is placed", block[0]["code"] == "Store.java", block[0])
    check("a platform class placed nowhere says nothing", "places" not in block[1], block[1])
    check("a slice name is not a class", "places" not in block[2], block[2])

    # And the markdown shows the column, before the evidence.
    text = report_mod._table(rep["detectors"][0]["rows"])
    head = text.splitlines()[0]
    check("the column is there", "In the code" in head, head)
    check("before the evidence", head.index("In the code") < head.index("Evidence"), head)
    check("json bookkeeping is not a column", "places" not in head, head)


def test_annotate_walks_nothing_when_there_is_nothing_to_place(tmp_path):
    rep = {"detectors": [{"id": "gc_pressure", "rows": [
        {"location": "HeapTaskDaemon", "total_ms": 1.0, "detail": "GC"}]}]}
    placed = place.annotate(rep, tmp_path / "nowhere")
    check("nothing placed, no error on a missing root", placed == 0, placed)
    check("the row is untouched", "places" not in rep["detectors"][0]["rows"][0], rep)
