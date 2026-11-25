"""A temporary marker in C or C++ is in the code like one in Kotlin or Java (#1009).

A hunt in an app whose work sits behind a JNI call followed the time into
the native body and put its last marker there, with the project's own
tracing wrapper inside a block opened for its scope. Every reader of the
markers walked Kotlin and Java alone: `analyze` said the name in the traces
was "nowhere in the code" and asked for a build over a current one, `hunt`
counted the Java markers and not that one, `mark --remove` and the cleanup
grep of the agent texts found nothing, and `hunt --verdict` said the sources
were clean while it was compiled into the library.

These hold every reader to the C and C++ sources, and `--remove` to naming
what it does not delete.

    python -m pytest tests/test_native_markers.py
"""

from __future__ import annotations

from pathlib import Path

from echolot import domains
from echolot import hunt as hunt_mod
from echolot import mark as mark_mod
from echolot import recorder
from echolot import state
from echolot import uninit
from echolot import verdict as verdict_mod
from echolot.config import Config
from echolot.main import _markers_against_code
from echolot.mark import Markers
from echolot.reflect import facts
from echolot.reflect import signals
from tests.support import check
from tests.test_hunt_verdict import CONFIG, ROUND_1, ROUND_2, VERDICT, hunt_with, run, write

NATIVE = "app/src/main/cpp/native.cpp"
# The wrapper object in a block of its own, as the hunt wrote it: deleting
# the line with the name would leave the block, and the object has no end
# line to find it by.
SCOPED = ('#include "scoped_trace.h"\n\n'
          "void SetDir(const char* dir) {\n"
          "  {\n"
          '    ScopedTrace section("AGENTTMP_x");\n'
          "    Store(dir);\n"
          "  }\n"
          "}\n")
PLACE = f"{NATIVE}:5"


def native(root: Path, text: str = SCOPED, rel: str = NATIVE) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the walk -------------------------------------------------------------------

def test_the_marker_walk_reads_c_and_cpp_and_the_map_keeps_to_kotlin_and_java(tmp_path: Path) -> None:
    for rel in ("app/src/main/cpp/a.c", "app/src/main/cpp/b.cc", "app/src/main/cpp/c.cpp",
                "app/src/main/cpp/d.cxx", "app/src/main/cpp/e.h", "app/src/main/cpp/f.hh",
                "app/src/main/cpp/g.hpp", "app/src/main/cpp/h.hxx", "libs/core/i.cpp",
                "app/src/main/java/A.kt", "app/src/main/java/B.java"):
        native(tmp_path, "", rel)
    native(tmp_path, "", "app/build/intermediates/gen.cpp")
    native(tmp_path, "", "app/src/main/cpp/notes.txt")
    walked = [p.relative_to(tmp_path).as_posix() for p in mark_mod.marker_files(tmp_path)]
    check("every C and C++ suffix, outside src/ too, with Kotlin and Java", len(walked) == 11, walked)
    check("and nothing under build/", not any("/build/" in p for p in walked), walked)
    kotlin_java = [p.name for p in domains.source_files(tmp_path)]
    check("the map's walk is Kotlin and Java, as before", kotlin_java == ["A.kt", "B.java"], kotlin_java)


# --- analyze: not stale ---------------------------------------------------------

def against_code(root: Path, in_trace: list[str]) -> dict:
    (root / "echolot.yml").write_text(
        "project:\n  package: com.example.app\nrunner:\n  mode: gradle\n", encoding="utf-8")
    rep = {"markers": {"prefix": "AGENTTMP_", "in_trace": in_trace}}
    with recorder.isolated():
        _markers_against_code(rep, Config.load(root / "echolot.yml"))
    return rep["markers"]


def test_a_name_written_in_cpp_is_not_taken_out_of_the_code(tmp_path: Path) -> None:
    native(tmp_path, "class App\n", "app/src/main/java/com/example/app/App.kt")
    native(tmp_path)
    said = against_code(tmp_path, ["AGENTTMP_x"])
    check("the marker in the .cpp is in the code", "removed" not in said, said)
    native(tmp_path, "void f() {}\n")
    said = against_code(tmp_path, ["AGENTTMP_x"])
    check("and once it is taken out it is said, as before",
          said.get("removed", {}).get("names") == ["AGENTTMP_x"], said)


def test_c_literals_the_compiler_joins_are_a_fixed_start(tmp_path: Path) -> None:
    native(tmp_path, '#define MARK(x) ATrace_beginSection("AGENTTMP_" #x)\n'
                     '#define SEED(x) ATrace_beginSection("AGENTTMP_seed_" #x)\n'
                     'void f() { ATRACE_NAME("AGENTTMP_load_" "tiles"); }\n'
                     'void g() { ATrace_beginSection("AGENTTMP_step_" STEP_NAME); }\n',
           "app/src/main/cpp/t.h")
    code = mark_mod.named(tmp_path, "AGENTTMP_")
    check("the prefix alone makes every name", code.makes("AGENTTMP_anything"), code)
    check("a literal joined to the next, to a stringized argument or to a macro is the start of the names",
          {"AGENTTMP_load_", "AGENTTMP_seed_", "AGENTTMP_step_"} <= code.heads and not code.exact, code)


def test_a_literal_followed_by_anything_else_is_a_whole_name(tmp_path: Path) -> None:
    native(tmp_path, 'ATrace_beginSection("AGENTTMP_a"); f("b");\n')
    native(tmp_path, 'val pair = mapOf("AGENTTMP_k" to 1, "AGENTTMP_j" "x")\n', "app/src/main/java/A.kt")
    code = mark_mod.named(tmp_path, "AGENTTMP_")
    check("in C++", "AGENTTMP_a" in code.exact and code.where["AGENTTMP_a"] == f"{NATIVE}:1", code)
    check("and C's joining stays out of Kotlin", {"AGENTTMP_k", "AGENTTMP_j"} <= code.exact
          and not code.heads, code)


def test_a_tagged_marker_in_cpp_is_planted_and_its_absence_is_said(tmp_path: Path) -> None:
    native(tmp_path, "class App\n", "app/src/main/java/com/example/app/App.kt")
    native(tmp_path, 'ATrace_beginSection("AGENTTMP_y"); // echolot:mark\n'
                     "ATrace_endSection(); // echolot:mark\n")
    check("its name is among the planted ones",
          mark_mod.planted(tmp_path, "AGENTTMP_") == {"AGENTTMP_y": [NATIVE]})
    said = against_code(tmp_path, ["AGENTTMP_other"])
    check("and a round without it says the build is older", said.get("unbuilt", {}).get("names") == ["AGENTTMP_y"],
          said)


# --- leftovers, hunt, --remove --------------------------------------------------

def test_a_marker_added_by_hand_in_cpp_is_a_leftover_by_its_place(tmp_path: Path) -> None:
    native(tmp_path)
    left = Markers(tmp_path).leftovers()
    check("counted, none of it for --remove",
          (left["markers"], left["removable"], left["lines"]) == (1, 0, 0), left)
    check("named by its place", left["hand"] == [PLACE] and left["places"] == [NATIVE], left)
    check("and the agent's 'removed' does not agree", verdict_mod.cleanup_agrees("removed", left) is False)
    check("a tree with only Kotlin hand markers names them the same way", Markers(
        native(tmp_path / "k", 'fun a() {\n    trace("AGENTTMP_k") { }\n}\n',
               "app/src/main/java/A.kt").parents[4]).leftovers()["hand"] == ["app/src/main/java/A.kt:2"])


def test_status_names_the_marker_in_cpp(tmp_path: Path) -> None:
    native(tmp_path)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    said = hunt_mod.recap({"n": 1, "status": "open", "question": "q"}, state.project_state(tmp_path), tmp_path)
    check("the marker, how it goes, and where",
          "  ! 1 AGENTTMP_ marker(s) still in 1 file(s) — `echolot mark --remove` takes out 0, 1 were added "
          f"by hand and need removing by hand: {PLACE}" in said, said)


def test_mark_remove_names_it_and_leaves_the_file_as_it_was(tmp_path: Path) -> None:
    path = native(tmp_path)
    native(tmp_path, "fun a() {\n"
                     '    android.os.Trace.beginSection("AGENTTMP_a") // echolot:mark\n'
                     "    work()\n"
                     "    android.os.Trace.endSection() // echolot:mark\n"
                     '    trace("AGENTTMP_k") { }\n'
                     "}\n", "app/src/main/java/A.kt")
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, out, err = run(tmp_path, "mark", "--remove")
    check("it runs", code == 0, err)
    check("it takes out the two lines --apply wrote", "  - app/src/main/java/A.kt: 2 line(s)" in out, out)
    check("and names each marker added by hand, the Kotlin one at its line once those are gone",
          f'  ! {PLACE}: ScopedTrace section("AGENTTMP_x");' in out
          and '  ! app/src/main/java/A.kt:3: trace("AGENTTMP_k") { }' in out, out)
    check("saying they go by hand, with what came with them",
          "left 2 AGENTTMP_ marker(s) added by hand — --remove deletes only the lines --apply writes, so take "
          "each out by hand, with what came with it: its end, the block opened for its scope, an include" in out,
          out)
    check("the native file is as it was", path.read_text(encoding="utf-8") == SCOPED)


def test_mark_remove_says_nothing_is_left_only_when_nothing_is(tmp_path: Path) -> None:
    native(tmp_path, "void f() {}\n")
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    _, out, _ = run(tmp_path, "mark", "--remove")
    check("a clean tree", "no temporary markers and no `echolot:mark` lines found under this root" in out, out)
    native(tmp_path)
    _, out, _ = run(tmp_path, "mark", "--remove")
    check("and never over a marker in C++", "found under this root" not in out and PLACE in out, out)


def test_a_tagged_line_in_cpp_is_one_remove_leaves_and_names(tmp_path: Path) -> None:
    """`ATrace_beginSection(…); // echolot:mark` is not a shape `--apply` writes."""
    native(tmp_path, 'void f() {\n  ATrace_beginSection("AGENTTMP_t"); // echolot:mark\n  g();\n'
                     "  ATrace_endSection(); // echolot:mark\n}\n")
    left = Markers(tmp_path).leftovers()
    check("both lines go by hand, by their places", left["kept"] == [f"{NATIVE}:2", f"{NATIVE}:4"], left)
    check("and the begin is not named twice", left["hand"] == [], left)
    hand: list[tuple[str, int, str]] = []
    touched, kept = Markers(tmp_path).remove(hand=hand)
    check("--remove deletes neither and names both once",
          touched == [] and [n for _, n, _ in kept] == [2, 4] and hand == [], (touched, kept, hand))


def test_a_new_investigation_names_the_marker_in_cpp(tmp_path: Path) -> None:
    native(tmp_path)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, _, err = run(tmp_path, "hunt", "cold start 2.1 s")
    check("it opens", code == 0, err)
    check("and says what the last one left, and where",
          "[!] 1 AGENTTMP_ marker(s) left in 1 file(s) by the previous investigation." in err
          and f"the other 1 were added by hand and go by hand: {PLACE}." in err, err)


def test_the_verdict_check_does_not_call_the_sources_clean(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    native(p)
    code, out, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("the check accepts, as it does for a Kotlin marker", code == 0, err)
    check("and never says the sources are clean", "no temporary markers in the sources" not in out, out)
    check("it names the marker and its place", f"by hand and need removing by hand: {PLACE}" in err, err)
    check("and holds it against the agent's word", "the verdict says removed; closing records both" in err, err)

    code, _, err = run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    cleanup = hunt_mod.load(p)["verdict_check"]["cleanup"]
    check("closing records it by its place", code == 0 and cleanup["found"]["hand"] == [PLACE], cleanup)
    check("and that the agent's word disagrees", cleanup["agrees"] is False, cleanup)
    code, out, _ = run(p, "hunt", "--show", "1")
    check("looking back names it",
          "cleanup: removed — echolot found 1 AGENTTMP_ marker(s) in 1 file(s) still in the sources, added by "
          f"hand at {PLACE}" in out, out)


# --- reflect, uninit and the agent texts ----------------------------------------

def test_reflect_reads_the_tree_and_the_edits_in_cpp(tmp_path: Path) -> None:
    native(tmp_path)
    tree = facts.tree_check(tmp_path, "AGENTTMP_", ["app/src/main/java"])
    check("the tree carries the marker, outside allowed", tree["files"] == [NATIVE]
          and tree["outside"] == [NATIVE], tree)
    check("an edit to a .cpp file is an edit to the sources", facts._is_source(NATIVE))
    check("ATrace_beginSection is a tracing call", facts._TRACE_CALL.search('ATrace_beginSection("x");'))
    check("and so is the macro over it", facts._TRACE_CALL.search("ATRACE_CALL();"))
    grep = signals._cleanup_grep("AGENTTMP_")
    check("the cleanup it suggests greps C and C++",
          all(f"--include='*{ext}'" in grep for ext in (".kt", ".java", ".c", ".cpp", ".h", ".hpp")), grep)


def test_uninit_counts_the_tagged_lines_in_cpp(tmp_path: Path) -> None:
    native(tmp_path, '  ATrace_beginSection("AGENTTMP_t"); // echolot:mark\n')
    plan = uninit.Plan(tmp_path)
    uninit._markers(plan, tmp_path)
    hand = [s for s in plan.steps if s.sign == uninit.HAND]
    check("the line is named for removal by hand", len(hand) == 1
          and any(NATIVE in d for d in hand[0].detail), plan.steps)
