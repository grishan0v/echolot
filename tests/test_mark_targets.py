"""Which code `mark` brackets, in the shapes where it took the wrong one.

An `onCreate` that belongs to another class, a construct named in a comment,
a `suspend fun`, and a frame placed in a subpackage's file of the same name.
The lambda frames are in `test_anr_frames`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr, mark, place  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_mark_edits import LAUNCHER, manifest, write  # noqa: E402

PKG = "app/src/main/kotlin/com/example/app"


def _line(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def _project(root: Path, app: str, activity: str | None = None) -> None:
    write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER, application=".App"))
    write(root, f"{PKG}/App.kt", app)
    if activity is not None:
        write(root, f"{PKG}/MainActivity.kt", activity)


APP_WITH_ROOM = """\
class App : Application() {
    val db by lazy {
        Room.databaseBuilder(this, Db::class.java, "db")
            .addCallback(object : RoomDatabase.Callback() {
                override fun onCreate(db: SupportSQLiteDatabase) {
                    seed(db)
                }
            })
            .build()
    }

    override fun onCreate() {
        super.onCreate()
        warm()
    }
}
"""


def test_on_create_is_the_class_s_own(tmp_path: Path) -> None:
    _project(tmp_path, APP_WITH_ROOM,
             "class MainActivity : ComponentActivity() {\n"
             "    override fun onCreate(savedInstanceState: Bundle?) {\n"
             "        super.onCreate(savedInstanceState)\n"
             "    }\n"
             "}\n")
    app = next(p for p in mark.plan(tmp_path).proposals if p.kind == "app_oncreate")
    check("App.onCreate, not the Room callback's",
          app.line == _line(APP_WITH_ROOM, "override fun onCreate()"), app)

    both = ("class App : Application() {\n"
            "    override fun onCreate() {\n"
            "        super.onCreate()\n"
            "    }\n"
            "}\n"
            "\n"
            "class MainActivity : ComponentActivity() {\n"
            "    override fun onCreate(savedInstanceState: Bundle?) {\n"
            "        super.onCreate(savedInstanceState)\n"
            "    }\n"
            "}\n")
    write(tmp_path, f"{PKG}/App.kt", both)
    (tmp_path / f"{PKG}/MainActivity.kt").unlink()
    found = {p.kind: p.line for p in mark.plan(tmp_path).proposals}
    check("in one file, each class gets its own",
          found.get("app_oncreate") == 2 and found.get("activity_oncreate") == 8, found)


def test_a_construct_in_a_comment_is_not_the_construct(tmp_path: Path) -> None:
    activity = ("/**\n"
                " * The entry point. Compose starts in setContent { } below.\n"
                " */\n"
                "class MainActivity : ComponentActivity() {\n"
                "    /*\n"
                "    override fun onCreate(b: Bundle?) {\n"
                "        old()\n"
                "    }\n"
                "    */\n"
                "    private fun helper() {\n"
                "        work()\n"
                "    }\n"
                "\n"
                "    override fun onCreate(savedInstanceState: Bundle?) {\n"
                "        super.onCreate(savedInstanceState)\n"
                "        setContent {\n"
                "            Home()\n"
                "        }\n"
                "    }\n"
                "}\n")
    _project(tmp_path, "class App : Application()\n", activity)
    found = {p.kind: p.line for p in mark.plan(tmp_path).proposals}
    check("the real onCreate", found.get("activity_oncreate")
          == _line(activity, "override fun onCreate(savedInstanceState"), found)
    check("and the real setContent", found.get("set_content")
          == _line(activity, "        setContent {"), found)


def test_a_suspend_function_is_not_bracketed(tmp_path: Path) -> None:
    repo = ("package com.example.app\n"
            "\n"
            "class Repo {\n"
            "    suspend fun updateLocality(id: Long) {\n"
            "        val fresh = withContext(Dispatchers.IO) {\n"
            "            api.fetch(id)\n"
            "        }\n"
            "        store.save(fresh)\n"
            "    }\n"
            "}\n")
    write(tmp_path, f"{PKG}/Repo.kt", repo)
    proposal = mark.plan_from_anr(
        tmp_path, [("com.example.app.Repo.updateLocality", f"{PKG}/Repo.kt",
                    _line(repo, "store.save"))]).proposals[0]
    check("refused, for the thread it may resume on",
          not proposal.applicable and proposal.reason.startswith("a suspend function"),
          proposal)


def test_a_subpackage_s_file_of_the_same_name_is_not_the_frame_s(tmp_path: Path) -> None:
    write(tmp_path, "core/src/main/java/com/example/feature/Mapper.kt",
          "package com.example.feature\nfun map() = 1\n")
    write(tmp_path, "data/src/main/java/com/example/Mapper.kt",
          "package com.example\nfun map() = 2\n")
    idx = anr.source_index(tmp_path)
    found = place.locate("com.example.Mapper.map", "Mapper.kt", 2, idx, tmp_path, "owner")
    check("the package's own file, for certain",
          found is not None and found.file == "data/src/main/java/com/example/Mapper.kt"
          and found.exact, found)


def test_java_names_for_kotlin_classes_are_not_another_build(tmp_path: Path) -> None:
    """On the build that froze, frames naming `Prefs.java` for a Kotlin class
    made 'probably not the build that froze' (#590)."""
    from tests.test_anr_frames import JAVA_NAMES_ONLY, KOTLIN_NAMED_JAVA_SOURCES, SYNC

    for rel, body in KOTLIN_NAMED_JAVA_SOURCES.items():
        write(tmp_path, rel, body)
    rep = anr.parse(JAVA_NAMES_ONLY)
    placed, missing = anr.locate(rep, tmp_path)
    pl = mark.plan_from_anr(tmp_path, [(f.symbol, f.file, f.line) for f in placed if f.exact],
                            unplaced=len(missing), version="1.2.0 (120)")
    check("every frame placed", len(placed) == 3 and not missing, (placed, missing))
    check("each takes a pair", all(p.applicable for p in pl.proposals),
          [(p.marker, p.reason) for p in pl.proposals])
    check("and no word of another build", not pl.notes, pl.notes)

    prefs = KOTLIN_NAMED_JAVA_SOURCES[SYNC + "Prefs.kt"].replace(
        "    fun write(value: String) {\n        store(value)\n        sync.push(value)\n",
        "    fun write(value: String) = store(value)\n"
        "    fun flush() {\n        sync.push(value)\n")
    write(tmp_path, SYNC + "Prefs.kt", prefs)
    placed, missing = anr.locate(anr.parse(JAVA_NAMES_ONLY), tmp_path)
    pl = mark.plan_from_anr(tmp_path, [(f.symbol, f.file, f.line) for f in placed if f.exact],
                            unplaced=len(missing))
    moved = next(p for p in pl.proposals if p.marker.endswith("Prefs_write"))
    check("a call moved into another function is still said to be",
          not moved.applicable and "`flush`" in moved.reason, moved.reason)


def test_lines_inside_other_functions_say_the_checkout_is_not_the_build(tmp_path: Path) -> None:
    """Counted when nothing was retraced; after the mapping, R8 moves lines on
    the right build too, and the per-frame refusal is all there is (#525)."""
    from tests.test_anr_frames import AWAITING, FOO

    write(tmp_path, "app/src/main/java/com/example/app/Foo.kt", FOO)
    placed, missing = anr.locate(anr.parse(AWAITING), tmp_path)
    frames = [(f.symbol, f.file, f.line) for f in placed if f.exact]
    pl = mark.plan_from_anr(tmp_path, frames, unplaced=len(missing), version="1.2.0 (120)")
    check("the refusal is worded neutrally",
          any("another build, an inlined function, or the compiler" in p.reason
              for p in pl.proposals), [p.reason for p in pl.proposals])
    check("and, not minified, most of them is a note",
          any("probably not the build that froze (1.2.0 (120))" in n for n in pl.notes), pl.notes)
    pl = mark.plan_from_anr(tmp_path, frames, unplaced=len(missing), retraced=True)
    check("retraced with the mapping: no such note",
          not any("not the build" in n for n in pl.notes), pl.notes)
