"""Which code `mark` brackets, in the shapes where it took the wrong one.

An `onCreate` that belongs to another class, a construct named in a comment,
a `suspend fun`, and a frame placed in a subpackage's file of the same name.
The lambda frames are in `test_anr_frames`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr, mark, place  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import run  # noqa: E402
from tests.test_anr_frames import INLINED, filler_checkout  # noqa: E402
from tests.test_anr_frames import write as write_files  # noqa: E402
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


# --- a line past the end of its file: Kotlin's inlined code (#725) -----------


@pytest.fixture
def fillers(tmp_path) -> Path:
    return filler_checkout(tmp_path)


def test_mark_proposes_the_function_an_inlined_frame_names(fillers, tmp_path):
    report_file = tmp_path / "export.txt"
    report_file.write_text(INLINED, encoding="utf-8")
    code, out, _ = run("mark", "--root", str(fillers), "--from-anr",
                       str(report_file), "--json")
    check("exit 0", code == 0, code)
    plan = json.loads(out)
    by_marker = {p["marker"]: p for p in plan["proposals"]}
    fill = by_marker.get("AGENTTMP_ScenarioFiller_fill")
    check("a proposal at the declaration of `fill`",
          fill is not None and fill["line"] == 7 and fill["applicable"], plan["proposals"])
    check("which says the line was inlined code",
          "past the end of the file: inlined code" in fill["what"], fill)
    check("and no note that this is another build",
          not any("land nowhere" in n for n in plan["notes"]), plan["notes"])


def test_mark_refuses_an_inlined_frame_whose_function_is_overloaded(tmp_path):
    root = write_files(tmp_path / "checkout", {
        "app/src/main/kotlin/com/example/app/filler/ScenarioFiller.kt":
            "package com.example.app.filler\n\nclass ScenarioFiller {\n"
            "    fun fill() {\n        go()\n    }\n"
            "    fun fill(n: Int) {\n        go(n)\n    }\n}\n"})
    plan = mark.plan_from_anr(root, [(
        "com.example.app.filler.ScenarioFiller.fill",
        "app/src/main/kotlin/com/example/app/filler/ScenarioFiller.kt", 30)])
    (p,) = plan.proposals
    check("refused, saying which", not p.applicable
          and p.reason == "line 30 is past the end of the file: inlined code, and "
                          "`fill` is declared 2 times here — mark by hand", p)
    check("and not counted as another build", not plan.notes, plan.notes)


# --- a dump of a minified build, with no mapping (#730) ------------------------

MINIFIED = """\
# Crashlytics -
# Application: com.example.app
# Platform: android
# Version: 1.2.3 (45)

main (blocked):tid=1 systid=1001 | waiting to lock <0x0a714d13> (java.lang.Object) held by thread 12
       at cy.c(SourceFile:117)
       at y71.m(SourceFile:10)
       at android.app.Instrumentation.callApplicationOnCreate(Instrumentation.java:1277)

Worker-2 (waiting):tid=12 systid=1012
       at jdk.internal.misc.Unsafe.park(Native method)
       at cy.b(SourceFile:102)
       at java.lang.Thread.run(Thread.java:1012)
"""


def test_an_empty_plan_from_a_minified_dump_says_a_mapping_is_needed(tmp_path):
    root = write_files(tmp_path / "checkout", {
        f"{PKG}/A.kt": "package com.example.app\n\nclass A {\n    fun a() {\n    }\n}\n"})
    report = write_files(tmp_path, {"anr.txt": MINIFIED}) / "anr.txt"
    code, out, _ = run("mark", "--root", str(root), "--from-anr", str(report))
    check("exit 0", code == 0, code)
    check("it says the frames are R8's and what names them back",
          "nothing to mark from this report. What 3 frames are: they read as R8 "
          "named them, first `cy.c(SourceFile:117)`" in out and "`--mapping`" in out, out)
    check("and not the manifest's sentence", "platform's vocabulary" not in out, out)
    code, out, _ = run("mark", "--root", str(root), "--from-anr", str(report), "--json")
    check("--json says it too", "R8 named them" in json.loads(out)["empty"], out)


def test_mark_takes_the_mapping_as_anr_does(tmp_path):
    from tests import lockapp

    record = write_files(tmp_path, {"anr.txt": lockapp.record()}) / "anr.txt"
    root = lockapp.checkout(tmp_path / "app")
    mapping = write_files(tmp_path / "out", {"mapping.txt": lockapp.MAPPING}) / "mapping.txt"
    code, out, err = run("mark", "--root", str(root), "--from-anr", str(record),
                         "--mapping", str(mapping), "--json")
    check("exit 0", code == 0, (code, err))
    plan = json.loads(out)
    files = {p["file"].rsplit("/", 1)[-1] for p in plan["proposals"]}
    check("the frames are placed by their names",
          {"Store.java", "MainActivity.java"} <= files, plan["proposals"])
    check("and nothing says a mapping is needed", not plan["empty"]
          and not any("R8 named" in n for n in plan["notes"]), plan)


def test_a_mapping_flag_without_a_report_or_a_file_stops_mark(tmp_path):
    code, _, err = run("mark", "--root", str(tmp_path), "--mapping", str(tmp_path / "m.txt"))
    check("--mapping alone is refused", code == 2 and "--from-anr" in err, (code, err))
    report = write_files(tmp_path, {"anr.txt": MINIFIED}) / "anr.txt"
    code, _, err = run("mark", "--root", str(tmp_path), "--from-anr", str(report),
                       "--mapping", str(tmp_path / "nope.txt"))
    check("a mapping that is not there is said", code == 2 and "no such file" in err,
          (code, err))


BASE_APP = """\
abstract class BaseApplication : Application() {
    final override fun onCreate() {
        super.onCreate()
        if (isMainProcess()) onMainProcessCreate()
    }

    abstract fun onMainProcessCreate()
}
"""


def test_an_on_create_inherited_from_the_app_s_own_base_is_proposed(tmp_path: Path) -> None:
    """#924: the Application extends a base of this project's own that
    overrides onCreate; that override is what runs at bindApplication."""
    _project(tmp_path, "class App : MiddleApplication() {\n"
                       "    override fun onMainProcessCreate() {\n"
                       "        warm()\n"
                       "    }\n"
                       "}\n")
    write(tmp_path, f"{PKG}/MiddleApplication.kt",
          "abstract class MiddleApplication : BaseApplication()\n")
    write(tmp_path, "core/base/src/main/kotlin/com/example/app/BaseApplication.kt", BASE_APP)
    pl = mark.plan(tmp_path)
    app = next((p for p in pl.proposals if p.kind == "app_oncreate"), None)
    check("the base's onCreate, two classes up",
          app is not None
          and app.file == "core/base/src/main/kotlin/com/example/app/BaseApplication.kt"
          and app.line == _line(BASE_APP, "final override fun onCreate()")
          and app.applicable and "which App inherits" in app.what,
          (app, pl.notes))
    check("and no note that it is not overridden",
          not any("does not override onCreate" in n for n in pl.notes), pl.notes)
    mark.apply(tmp_path, pl)
    base = (tmp_path / app.file).read_text(encoding="utf-8")
    check("and --apply brackets it", "AGENTTMP_app_oncreate" in base, base)


def test_a_base_of_the_app_s_own_without_on_create_is_named(tmp_path: Path) -> None:
    _project(tmp_path, "class App : BaseApplication()\n")
    write(tmp_path, f"{PKG}/BaseApplication.kt",
          "abstract class BaseApplication : MultiDexApplication()\n")
    pl = mark.plan(tmp_path)
    check("nothing proposed", not any(p.kind == "app_oncreate" for p in pl.proposals),
          pl.proposals)
    check("the base looked in is named",
          any("does not override onCreate (nor does BaseApplication, which it inherits from)"
              in n for n in pl.notes), pl.notes)


def test_a_cycle_of_bases_ends(tmp_path: Path) -> None:
    _project(tmp_path, "class App : A()\n")
    write(tmp_path, f"{PKG}/A.kt", "open class A : B()\nopen class B : A()\n")
    notes = mark.plan(tmp_path).notes
    check("said, not looped", any("does not override onCreate" in n for n in notes), notes)
