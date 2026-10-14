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
from tests import lockapp  # noqa: E402
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


# The same slice when ART did not know the owner's method:
# `Monitor::PrettyContentionInfo` then leaves out ` at <frame>`. About one
# contention slice in fifty on real cold starts has this shape (#269).
NO_OWNER_FRAME = ("monitor contention with owner DefaultDispatcher-worker-3 (28463) waiters=0 "
                  "blocking from void com.example.app.data.StoreRepository"
                  ".update(java.lang.String)(StoreRepository.kt:-1)")


def test_a_slice_without_the_owners_frame_still_gives_the_waiters():
    got = place.parse_contention(NO_OWNER_FRAME)
    check("it parses", got is not None, got)
    check("with no owner frame", got["at"] is None, got)
    check("the owner thread and the waiter count",
          (got["owner_thread"], got["owner_tid"], got["waiters"])
          == ("DefaultDispatcher-worker-3", 28463, 0), got)
    blocked = place.parse_frame(got["blocked"])
    check("and the waiter's frame",
          blocked == ("com.example.app.data.StoreRepository.update", "StoreRepository.kt", None),
          blocked)


def test_annotate_places_the_waiter_when_the_owner_frame_is_missing(tmp_path):
    root = checkout(tmp_path)
    rep = {"detectors": [{"id": "monitor_contention", "rows": [
        {"location": "DefaultDispatch", "total_ms": 30.0, "detail": NO_OWNER_FRAME}]}]}
    placed = place.annotate(rep, root)
    lock = rep["detectors"][0]["rows"][0]
    check("the row is placed", placed == 1, placed)
    check("by its waiter alone, with no owner role",
          [p["role"] for p in lock.get("places", [])] == ["blocked"], lock.get("places"))
    check("at the waiter's declaration",
          lock.get("code") == "blocked at StoreRepository.kt:6", lock.get("code"))


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


def test_the_frame_of_ours_the_samples_put_first_is_placed(tmp_path):
    root = checkout(tmp_path)
    (root / "domain/src/main/java/com/example/app/data/Sync.kt").write_text(
        "package com.example.app.data\n"
        "\n"
        "fun syncAll() {\n"
        "}\n", encoding="utf-8")
    # The frame of ours first in line was named back by the build's mapping as
    # two methods of one class; the first places it.
    ours = [{"frame": None, "samples": 9, "pct": 45.0},
            {"frame": "com.example.app.data.StoreRepository.update | "
                      "com.example.app.data.StoreRepository.updateAll", "samples": 6, "pct": 30.0},
            {"frame": "com.example.app.data.SyncKt.syncAll", "samples": 5, "pct": 25.0}]
    rep = {"detectors": [{"id": "uninstrumented_cpu", "rows": [
        {"location": "DefaultDispatch", "total_ms": 300.0,
         "detail": "100.0% of CPU outside slices · 20 stacks: …",
         "stacks": {"samples": 20, "with_stack": 20, "leaf": [], "ours": ours}},
        {"location": "Thread-9", "total_ms": 100.0,
         "detail": "100.0% of CPU outside slices · 4 stacks: …",
         "stacks": {"samples": 4, "with_stack": 4, "leaf": [],
                    "ours": [{"frame": "com.example.app.data.SyncKt.syncAll",
                              "samples": 4, "pct": 100.0}]}},
        {"location": "Thread-10", "total_ms": 90.0,
         "detail": "100.0% of CPU outside slices · 4 stacks: …",
         "stacks": {"samples": 4, "with_stack": 4, "leaf": [],
                    "ours": [{"frame": None, "samples": 4, "pct": 100.0}]}},
    ]}]}
    placed = place.annotate(rep, root)
    worker, thread, pooled = rep["detectors"][0]["rows"]
    check("two rows placed", placed == 2, placed)
    check("past `none` to the first frame with a name, at its declaration",
          worker["code"] == "sampled at StoreRepository.kt:6", worker)
    check("the package picks the module", worker["places"][0]["file"].startswith("domain/"),
          worker["places"])
    check("a Kotlin top-level function is in the file its class is named after",
          thread["code"] == "sampled at Sync.kt:3", thread)
    check("nothing of ours on the stack is no place", "places" not in pooled, pooled)


def test_annotate_walks_nothing_when_there_is_nothing_to_place(tmp_path):
    rep = {"detectors": [{"id": "gc_pressure", "rows": [
        {"location": "HeapTaskDaemon", "total_ms": 1.0, "detail": "GC"}]}]}
    placed = place.annotate(rep, tmp_path / "nowhere")
    check("nothing placed, no error on a missing root", placed == 0, placed)
    check("the row is untouched", "places" not in rep["detectors"][0]["rows"][0], rep)


# --- a minified build -----------------------------------------------------------

def lock_report(detail: str) -> dict:
    return {"detectors": [{"id": "monitor_contention", "rows": [
        {"location": "main", "total_ms": 8500.0, "detail": detail}]}]}


def mapping_file(tmp_path: Path, text: str = lockapp.MAPPING) -> Path:
    path = tmp_path / "mapping.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_minified_lock_is_placed_where_the_mapping_says(tmp_path):
    root = lockapp.checkout(tmp_path / "app")
    for build, text in (("lines", lockapp.MAPPING), ("default", lockapp.DEFAULT)):
        rep = lock_report(lockapp.SLICE[build])
        place.annotate(rep, root, mapping_file(tmp_path, text))
        row = rep["detectors"][0]["rows"][0]
        roles = {p["role"]: p for p in row["places"]}
        check(f"{build}: the worker inside Store.hold, at the loop it spun in",
              (roles["owner"]["symbol"], roles["owner"]["line"])
              == ("com.example.locks.Store.hold", 10), roles)
        check(f"{build}: the main thread at the `synchronized` it could not enter, "
              f"inlined into MainActivity.freeze as it was",
              (roles["blocked"]["symbol"], roles["blocked"]["line"])
              == ("com.example.locks.Store.read", 17), roles)
        check(f"{build}: the file is the checkout's",
              roles["blocked"]["file"] == "app/src/main/java/com/example/locks/Store.java",
              roles)
        check(f"{build}: both say the mapping named them",
              all(p.get("retraced") is True for p in row["places"]), row["places"])
        check(f"{build}: the code column", row["code"]
              == "owner at Store.java:10 · blocked at Store.java:17", row["code"])
        check(f"{build}: the evidence stays as the runtime wrote it",
              row["detail"] == lockapp.SLICE[build], row["detail"])
        check(f"{build}: the report counts them", rep["retrace"]
              == {"frames": 2, "outside": 0}, rep.get("retrace"))


def test_a_lock_slice_with_no_line_is_placed_at_the_methods_r8_kept(tmp_path):
    # A build R8 left without a line table writes `-1` into the lock slice.
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(lockapp.SLICE["offsets"])
    place.annotate(rep, root, mapping_file(tmp_path, lockapp.OFFSETS))
    row = rep["detectors"][0]["rows"][0]
    check("at their declarations, as a frame of an unminified release build is",
          row["code"] == "owner at Holder.java:17 · blocked at MainActivity.java:20", row)


def test_without_the_mapping_a_minified_lock_is_nowhere(tmp_path):
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(lockapp.SLICE["lines"])
    placed = place.annotate(rep, root)
    row = rep["detectors"][0]["rows"][0]
    check("both frames kept, in no file", placed == 0
          and [(p["symbol"], p["file"]) for p in row["places"]]
          == [("a.a.run", None), ("a.b.run", None)], row)
    check("no `retraced` on a place that was not", all("retraced" not in p
                                                       for p in row["places"]), row)
    check("and no count", "retrace" not in rep, rep)


def test_a_frame_of_the_platform_is_not_asked_of_the_mapping(tmp_path):
    (tmp_path / "app").mkdir()
    root = checkout(tmp_path / "app")
    rep = lock_report(REAL)
    place.annotate(rep, root, mapping_file(tmp_path))
    row = rep["detectors"][0]["rows"][0]
    check("placed as it always was",
          row["code"] == "blocked at StoreRepository.kt:6"
          and not any(p.get("retraced") for p in row["places"]), row)
    check("nothing to count", "retrace" not in rep, rep)


def test_another_builds_mapping_is_told_by_the_frames_it_has_no_place_for(tmp_path):
    # The worker's class is there under another build's numbering: its `run`
    # has no line 6. The main thread's class is not there at all.
    other = ("com.example.locks.Holder -> a.a:\n"
             "    1:2:void run():19:20 -> run\n")
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(lockapp.SLICE["lines"])
    place.annotate(rep, root, mapping_file(tmp_path, other))
    row = rep["detectors"][0]["rows"][0]
    owner = row["places"][0]
    check("the class comes back, the method keeps R8's name, and no line",
          (owner["symbol"], owner["line"], owner.get("retraced"))
          == ("com.example.locks.Holder.run", None, True), owner)
    check("counted", rep["retrace"] == {"frames": 1, "outside": 1}, rep.get("retrace"))
    text = "\n".join(report_mod._retrace_lines(rep["retrace"]))
    check("and the header says check the mapping", text.startswith("> ⚠️")
          and "1 of the 1 frames" in text and "another build" in text, text)


def test_the_header_says_what_the_mapping_made_of_the_lock_frames():
    check("nothing without a mapping, or with nothing R8 wrote",
          report_mod._retrace_lines(None) == []
          and report_mod._retrace_lines({"frames": 0, "outside": 0}) == [])
    lines = report_mod._retrace_lines({"frames": 4, "outside": 0})
    check("one plain line: why `code` and the evidence name it differently",
          len(lines) == 1 and not lines[0].startswith(">")
          and "named back the 4 frames" in lines[0] and "as recorded" in lines[0], lines)
    lines = report_mod._retrace_lines({"frames": 40, "outside": 1})
    check("a frame or two out of many is said, not warned about",
          len(lines) == 1 and not lines[0].startswith(">") and "1 had no place" in lines[0],
          lines)
    check("a tenth is a warning",
          report_mod._retrace_lines({"frames": 10, "outside": 1})[0].startswith("> ⚠️"))


# --- a mapping and a build that is not its own (#342) --------------------------

# The lock as a build that kept its names writes it: MainActivity is a class
# R8 kept, and line 3 of its `onCreate` happens to be in one of R8's ranges.
UNMINIFIED = ("monitor contention with owner Holder (21322) at void "
              "com.example.locks.Store.hold(long)(Store.java:10) waiters=0 blocking from "
              "void com.example.locks.MainActivity.onCreate(android.os.Bundle)(MainActivity.java:3)")


def test_a_build_that_kept_its_names_is_not_retraced_by_its_lines(tmp_path):
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(UNMINIFIED)
    place.annotate(rep, root, mapping_file(tmp_path))
    row = rep["detectors"][0]["rows"][0]
    check("both placed as written, the runtime's lines", row["code"]
          == "owner at Store.java:10 · blocked at MainActivity.java:3", row)
    check("nothing called retraced", not any(p.get("retraced") for p in row["places"])
          and "retrace" not in rep, (row["places"], rep.get("retrace")))


def test_beside_a_frame_that_is_r8s_the_line_is_r8s(tmp_path):
    # The same kept frame, in a report whose other lock names a class R8
    # renamed: the build is minified, and line 3 is R8's.
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(UNMINIFIED.replace("com.example.locks.Store.hold(long)(Store.java:10)",
                                         "a.a.run()(Store.java:6)"))
    place.annotate(rep, root, mapping_file(tmp_path))
    blocked = rep["detectors"][0]["rows"][0]["places"][1]
    check("named back through what R8 inlined there", (blocked["symbol"], blocked["line"])
          == ("com.example.locks.Holder.start", 13), blocked)


def test_samples_the_mapping_named_back_say_the_build_is_minified(tmp_path):
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(UNMINIFIED)
    rep["environment"] = {"sampling": {"names": {"methods": 8, "minified": 0, "renamed": 8}}}
    place.annotate(rep, root, mapping_file(tmp_path))
    blocked = rep["detectors"][0]["rows"][0]["places"][1]
    check("the kept frame is R8's", blocked.get("retraced") is True
          and blocked["symbol"] == "com.example.locks.Holder.start", blocked)


def test_frames_named_after_another_mapping_stay_as_recorded(tmp_path):
    # The build with no `-keepattributes` names its files after its own
    # mapping; given the other build's, nothing is named back.
    root = lockapp.checkout(tmp_path / "app")
    rep = lock_report(lockapp.SLICE["default"])
    place.annotate(rep, root, mapping_file(tmp_path, lockapp.MAPPING))
    row = rep["detectors"][0]["rows"][0]
    check("both frames as written, in no file",
          [(p["symbol"], p["file"]) for p in row["places"]]
          == [("a.a.run", None), ("a.b.run", None)], row["places"])
    check("the report says which ids", rep["retrace"] == {
        "frames": 0, "outside": 0,
        "map_id": {"frames": lockapp.MAP_ID.removeprefix("r8-map-id-"),
                   "mapping": "c2d66c23dd18be785acf86ecf50d8d7c1abe10b6de3a101cc4f3f25f7dff18b6"}},
          rep["retrace"])
    text = "\n".join(report_mod._retrace_lines(rep["retrace"]))
    check("and the header warns, with ids a reader can compare",
          text.startswith("> ⚠️") and "`4393c57b0c71…`" in text and "`c2d66c23dd18…`" in text, text)


# --- composables ----------------------------------------------------------

def test_a_composables_name_carries_its_declaration() -> None:
    check("runtime-tracing's shape, lambdas included",
          place.parse_composable("com.example.app.ui.Feed.<anonymous> (Feed.kt:42)")
          == ("com.example.app.ui.Feed.<anonymous>", "Feed.kt", 42))
    for name in ("Compose:recompose", "void com.example.Store.put(java.lang.String)(Store.java:41)",
                 "com.example.app.ui.Feed (Feed.java:42)", "Feed (Feed.kt:)"):
        check(f"not a composable: {name}", place.parse_composable(name) is None)


def _feed_checkout(root: Path, package_dir: str = "com/example/app/ui") -> Path:
    src = root / "feature/feed/src/main/kotlin" / package_dir
    src.mkdir(parents=True)
    for name in ("FeedScreen", "FeedItem"):
        (src / f"{name}.kt").write_text("package x\n", encoding="utf-8")
    return root


def test_the_apps_own_composables_by_package_or_by_checkout(tmp_path: Path) -> None:
    names = ["com.example.app.ui.FeedScreen (FeedScreen.kt:24)",
             "com.example.app.ui.FeedItem.<anonymous> (FeedItem.kt:31)",
             "androidx.compose.material3.Text (Text.kt:153)",
             "com.vendor.chart.Chart (Chart.kt:12)",
             "Compose:recompose"]
    check("by the project's package alone, with no checkout",
          place.own_composables(names, None, "com.example.app") == names[:2])
    root = _feed_checkout(tmp_path / "repo")
    check("by the checkout, when the package is a build's and not the code's",
          place.own_composables(names, root, "com.example.app.beta") == names[:2])
    other = _feed_checkout(tmp_path / "other", "com/elsewhere/ui")
    check("a file of that name where another package lives is not the app's",
          place.own_composables(names, other, None) == [])


def test_a_top_level_composables_package_leaves_out_the_function(tmp_path: Path) -> None:
    src = tmp_path / "app/src/main/kotlin/com/example/app/ui"
    src.mkdir(parents=True)
    (src / "Items.kt").write_text("package com.example.app.ui\n", encoding="utf-8")
    check("`com.example.app.ui.feedItem` is a function in com/example/app/ui",
          place.own_composables(["com.example.app.ui.feedItem (Items.kt:9)"], tmp_path, None)
          == ["com.example.app.ui.feedItem (Items.kt:9)"])


def test_no_composables_no_walk(tmp_path: Path, monkeypatch) -> None:
    """The checkout is walked for a trace that names composables, and only
    then: a big checkout costs its walk on every analyze otherwise."""
    def refuse(root):
        raise AssertionError("walked the checkout for nothing")
    monkeypatch.setattr(place, "index", refuse)
    check("nothing to own", place.own_composables(["Compose:recompose", "inflate"],
                                                    tmp_path, None) == [])


def test_annotate_places_a_composable_at_its_declaration(tmp_path: Path) -> None:
    root = _feed_checkout(tmp_path)
    report = {"detectors": [{"id": "composables", "rows": [
        {"location": "com.example.app.ui.FeedScreen (FeedScreen.kt:24)"},
        {"location": "androidx.compose.material3.Text (Text.kt:153)"}]}]}
    check("one placed", place.annotate(report, root) == 1)
    placed, library = report["detectors"][0]["rows"]
    check("at the line the name gives", placed["code"] == "FeedScreen.kt:24"
          and placed["places"][0]["file"]
          == "feature/feed/src/main/kotlin/com/example/app/ui/FeedScreen.kt", placed)
    check("and the library's is not in this checkout", "code" not in library, library)
