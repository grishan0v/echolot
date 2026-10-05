"""`mark --apply` and `--remove` in the shapes they broke: a `setContent` that
returns early, a body left by a `throw`, Windows paths, a name too long to
trace, a second run after the outer block was fixed, shared Kotlin source,
Java after an endless loop, sources outside `src/`, and a read-only file.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot import mark  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_mark_edits import LAUNCHER, manifest, write  # noqa: E402


def _tree(root: Path, activity: str) -> Path:
    write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    return write(root, "app/src/main/kotlin/MainActivity.kt", activity)


def _tagged(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if mark.TAG in ln]


def test_a_set_content_that_returns_early_is_refused(tmp_path: Path) -> None:
    _tree(tmp_path, "class MainActivity : ComponentActivity() {\n"
                    "    override fun onCreate(savedInstanceState: Bundle?) {\n"
                    "        super.onCreate(savedInstanceState)\n"
                    "        setContent {\n"
                    "            if (savedInstanceState != null) return@setContent\n"
                    "            AppScreen()\n"
                    "        }\n"
                    "    }\n"
                    "}\n")
    rows = {p.kind: p for p in mark.plan(tmp_path).proposals}
    check("the lambda is refused for its return",
          not rows["set_content"].applicable and "return" in rows["set_content"].reason,
          rows["set_content"])
    check("and onCreate, whose end goes in a finally, is not",
          rows["activity_oncreate"].applicable, rows["activity_oncreate"].reason)


def test_a_body_left_by_a_throw_still_closes(tmp_path: Path) -> None:
    rel = "app/src/main/kotlin/Repo.kt"
    path = write(tmp_path, rel, "class Repo {\n"
                                "    fun readConfig() {\n"
                                "        val f = File(\"config.json\")\n"
                                "        if (!f.exists()) throw FileNotFoundException(\"config.json\")\n"
                                "        parse(f)\n"
                                "    }\n"
                                "}\n")
    before = path.read_bytes()
    pl = mark.plan_from_anr(tmp_path, [("com.example.app.Repo.readConfig", rel, 4)])
    mark.apply(tmp_path, pl)
    check("the end is in a finally", _tagged(path) == [
        'android.os.Trace.beginSection("AGENTTMP_Repo_readConfig") // echolot:mark',
        "try { // echolot:mark",
        "} finally { android.os.Trace.endSection() } // echolot:mark"], _tagged(path))
    mark.remove(tmp_path)
    check("and --remove takes all three out", path.read_bytes() == before)


def test_paths_are_written_with_slashes() -> None:
    rel = mark._rel(PureWindowsPath("C:/r/app/src/main/java/A.kt"), PureWindowsPath("C:/r"))
    check("as posix", rel == "app/src/main/java/A.kt", rel)
    check("so the guard reads them", mark.under_allowed(rel, ["app/src/main"]))


def test_a_marker_fits_what_trace_takes(tmp_path: Path) -> None:
    long = ("com.example.app.ui.SubscriptionManagementViewModel$observeSubscriptionStatus"
            "ChangesForAccount$1$invokeSuspend$$inlined$flatMapLatest$1$2$1.emit")
    other = long.replace("emit", "collect")
    a, b = mark.marker_for(long, "AGENTTMP_"), mark.marker_for(other, "AGENTTMP_")
    check("within 127", len(a) <= mark.SECTION_NAME_MAX and len(b) <= mark.SECTION_NAME_MAX,
          (len(a), len(b)))
    check("and two cut names stay apart", a != b, (a, b))
    rel = "app/src/main/kotlin/Repo.kt"
    write(tmp_path, rel, "class Repo {\n    fun load() {\n        work()\n    }\n}\n")
    pl = mark.plan_from_anr(tmp_path, [("com.example.app.Repo.load", rel, 3)],
                            prefix="P" * 125)
    check("a prefix that leaves no room is refused", not pl.proposals[0].applicable
          and "127" in pl.proposals[0].reason, pl.proposals[0])


def test_a_second_apply_marks_the_outer_block_once_it_is_fixed(tmp_path: Path) -> None:
    path = _tree(tmp_path, "class MainActivity : ComponentActivity() {\n"
                           "    override fun onCreate(b: Bundle?) { super.onCreate(b)\n"
                           "        setContent {\n"
                           "            Home()\n"
                           "        }\n"
                           "    }\n"
                           "}\n")
    mark.apply(tmp_path, mark.plan(tmp_path))
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("{ super.onCreate(b)\n", "{\n        super.onCreate(b)\n"),
                    encoding="utf-8")
    applied = mark.apply(tmp_path, mark.plan(tmp_path))
    check("the outer one is marked now",
          applied[0] and applied[0][0][1] == ["AGENTTMP_activity_oncreate"], applied[0])
    check("and the inner one is named as marked already",
          [m for _, m in applied.already] == ["AGENTTMP_set_content"], applied.already)


def test_shared_kotlin_source_is_refused(tmp_path: Path) -> None:
    rel = "shared/src/commonMain/kotlin/com/example/shared/Parser.kt"
    write(tmp_path, rel, "class Parser {\n    fun parseAll(input: String) {\n"
                         "        split(input)\n        join(input)\n    }\n}\n")
    pl = mark.plan_from_anr(tmp_path, [("com.example.shared.Parser.parseAll", rel, 3)])
    check("commonMain", not pl.proposals[0].applicable
          and "commonMain" in pl.proposals[0].reason, pl.proposals[0])


def test_java_after_an_endless_loop_and_a_throw_compiles(tmp_path: Path) -> None:
    rel = "app/src/main/java/SyncWorker.java"
    path = write(tmp_path, rel, "class SyncWorker {\n"
                                "    public void run() {\n"
                                "        while (true) {\n"
                                "            work();\n"
                                "        }\n"
                                "    }\n"
                                "    void fail(String why) {\n"
                                "        log(why);\n"
                                "        throw new IllegalStateException(why);\n"
                                "    }\n"
                                "}\n")
    pl = mark.plan_from_anr(tmp_path, [("SyncWorker.run", rel, 4), ("SyncWorker.fail", rel, 8)])
    mark.apply(tmp_path, pl)
    tagged = _tagged(path)
    check("no bare end after either: each sits in a finally",
          "android.os.Trace.endSection(); // echolot:mark" not in tagged
          and tagged.count("} finally { android.os.Trace.endSection(); } // echolot:mark") == 2,
          tagged)


def test_remove_reaches_sources_outside_src(tmp_path: Path) -> None:
    path = write(tmp_path, "app/java/com/example/app/App.kt",
                 "class App {\n"
                 "    fun onCreate() {\n"
                 '        android.os.Trace.beginSection("AGENTTMP_App_onCreate") // echolot:mark\n'
                 "        work()\n"
                 "        android.os.Trace.endSection() // echolot:mark\n"
                 "    }\n"
                 "}\n")
    check("hunt counts them", hunt_mod.leftovers(tmp_path)["markers"] == 1)
    touched, _ = mark.remove(tmp_path)
    check("and --remove takes them out", touched and mark.TAG not in path.read_text())


def test_a_read_only_file_is_named_and_the_rest_go_on(tmp_path: Path) -> None:
    for name in ("A", "B"):
        write(tmp_path, f"app/src/main/kotlin/{name}.kt",
              f"class {name} {{\n    fun go() {{\n        work()\n    }}\n}}\n")
    locked = tmp_path / "app/src/main/kotlin/A.kt"
    locked.chmod(stat.S_IRUSR)
    try:
        pl = mark.plan_from_anr(tmp_path, [("A.go", "app/src/main/kotlin/A.kt", 3),
                                           ("B.go", "app/src/main/kotlin/B.kt", 3)])
        applied = mark.apply(tmp_path, pl)
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if os.access(locked, os.W_OK) and os.geteuid() == 0:
        return   # root writes anyway
    check("named", applied.unwritable == ["app/src/main/kotlin/A.kt"], applied.unwritable)
    check("and the other one marked", [rel for rel, _ in applied[0]] == ["app/src/main/kotlin/B.kt"])
