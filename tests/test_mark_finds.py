"""`mark` on code it skipped, refused or misread: `Thread { }`, a labelled
return, a suffixed package, the notes on bindApplication, an abstract fun,
lambda frames, a pool given a factory, an annotated onCreate, a root under
`main`, a mistyped `--module`, a form feed, an annotation's parenthesis,
ForkJoin's names, and a hyphen in a Kotlin frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import mark, place  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_mark_edits import LAUNCHER, manifest, write  # noqa: E402

ACTIVITY = "app/src/main/kotlin/MainActivity.kt"


def _launcher(root: Path, body: str, module: str = "app", namespace: str | None = None) -> None:
    write(root, f"{module}/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(root, f"{module}/src/main/kotlin/MainActivity.kt", body)
    if namespace:
        write(root, f"{module}/build.gradle.kts",
              f'android {{\n  namespace = "{namespace}"\n}}\n')


def _rows(pl: mark.Plan) -> dict[str, mark.Proposal]:
    return {p.kind: p for p in pl.proposals}


def test_kotlin_s_thread_lambda_is_a_nameless_thread(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/kotlin/Work.kt",
          "fun go() {\n    Thread { work() }.start()\n}\n"
          "fun worker(): Thread {\n    return make()\n}\n")
    rows = mark.plan_pools(tmp_path).proposals
    check("one row, at the lambda", [(p.line, p.kind) for p in rows] == [(2, "thread_name")],
          [(p.line, p.what) for p in rows])


def test_a_return_that_leaves_a_lambda_does_not_refuse_oncreate(tmp_path: Path) -> None:
    _launcher(tmp_path, "class MainActivity : AppCompatActivity() {\n"
                        "    override fun onCreate(b: Bundle?) {\n"
                        "        super.onCreate(b)\n"
                        "        button.setOnClickListener {\n"
                        "            if (busy) return@setOnClickListener\n"
                        "            go()\n"
                        "        }\n"
                        "    }\n"
                        "}\n")
    check("applicable", _rows(mark.plan(tmp_path))["activity_oncreate"].applicable)
    body = "{\n    button.setOnClickListener(v -> {\n        if (busy) return;\n    });\n}"
    check("and in Java, a lambda's return is the lambda's", not mark.leaves(body, "onCreate", True))
    check("while a bare Kotlin return still leaves", mark.leaves("if (x) return", "onCreate"))


def test_the_installed_package_settles_the_module(tmp_path: Path) -> None:
    activity = ("class MainActivity : ComponentActivity() {\n"
                "    override fun onCreate(b: Bundle?) {\n        super.onCreate(b)\n    }\n}\n")
    _launcher(tmp_path, activity, "app", "com.example.app")
    _launcher(tmp_path, activity, "catalog", "com.example.catalog")
    pl = mark.plan(tmp_path, package="com.example.app.benchmark")
    check("the suffixed id is the app's", pl.module == ":app" and not pl.ambiguity,
          (pl.module, pl.ambiguity))
    pl = mark.plan(tmp_path, package="com.example.cat*")
    check("and a glob is matched as one", pl.module == ":catalog", (pl.module, pl.ambiguity))


def test_bind_application_is_never_said_to_be_empty(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/AndroidManifest.xml",
          manifest(LAUNCHER).replace(' android:name=".App"', ""))
    write(tmp_path, ACTIVITY, "class MainActivity : ComponentActivity()\n")
    notes = " ".join(mark.plan(tmp_path).notes)
    check("what runs there", "ContentProviders" in notes and "framework's alone" not in notes, notes)
    write(tmp_path, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER, application=".App"))
    write(tmp_path, "app/src/main/kotlin/App.kt", "@HiltAndroidApp\nclass App : Application()\n")
    notes = " ".join(mark.plan(tmp_path).notes)
    check("and with an Application that does not override onCreate",
          "its constructor, the ContentProviders" in notes
          and "nothing of yours" not in notes, notes)


def test_an_abstract_fun_does_not_take_the_next_body(tmp_path: Path) -> None:
    rel = "app/src/main/kotlin/BaseRepo.kt"
    write(tmp_path, rel, "abstract class BaseRepo {\n"
                         "    abstract fun key(): String\n"
                         "\n"
                         "    fun load() {\n"
                         "        val k = key()\n"
                         "        fetch(k)\n"
                         "    }\n"
                         "}\n")
    pl = mark.plan_from_anr(tmp_path, [("com.example.app.BaseRepo.load", rel, 6)])
    check("load, applicable, and no word of another build",
          pl.proposals[0].applicable and not pl.notes, (pl.proposals[0], pl.notes))


def test_a_lambda_frame_is_no_sign_of_another_build(tmp_path: Path) -> None:
    rel = "app/src/main/kotlin/Screen.kt"
    write(tmp_path, rel, "class Screen {\n"
                         "    fun load() {\n"
                         "        scope.launch {\n"
                         "            flow.collect {\n"
                         "                render(it)\n"
                         "            }\n"
                         "        }\n"
                         "    }\n"
                         "}\n")
    pl = mark.plan_from_anr(tmp_path, [("com.example.app.Screen$load$1$1.emit", rel, 5)])
    check("a reason of its own, and no note",
          "a lambda" in pl.proposals[0].reason and not pl.notes, (pl.proposals[0].reason, pl.notes))
    rel = "app/src/main/kotlin/Repo.kt"
    write(tmp_path, rel, "class Repo {\n"
                         "    suspend fun updateLocality() {\n"
                         "        val fresh = withContext(Dispatchers.IO) {\n"
                         "            fetch()\n"
                         "        }\n"
                         "        store(fresh)\n"
                         "    }\n"
                         "}\n")
    pl = mark.plan_from_anr(tmp_path, [("com.example.app.Repo$updateLocality$fresh$1.invokeSuspend",
                                        rel, 4)])
    check("a local variable's name is passed over for the function",
          pl.proposals[0].applicable, pl.proposals[0].reason)


def test_a_pool_given_a_factory_is_not_listed(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/kotlin/Pools.kt",
          "val db = Executors.newSingleThreadExecutor(NamedThreadFactory(\"db\"))\n"
          "val cpu = Executors.newFixedThreadPool(4, cpuFactory)\n"
          "val plain = Executors.newFixedThreadPool(4)\n")
    rows = mark.plan_pools(tmp_path).proposals
    check("only the plain one", [p.line for p in rows] == [3], [(p.line, p.what) for p in rows])


def test_an_annotated_java_on_create_is_found(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(tmp_path, "app/src/main/java/MainActivity.java",
          "public class MainActivity extends Activity {\n"
          "    @Override protected void onCreate(Bundle b) {\n"
          "        super.onCreate(b);\n"
          "        setContentView(R.layout.main);\n"
          "    }\n"
          "}\n")
    rows = _rows(mark.plan(tmp_path))
    check("the override", "activity_oncreate" in rows and rows["activity_oncreate"].applicable,
          list(rows))


def test_a_root_under_a_directory_named_main(tmp_path: Path) -> None:
    root = tmp_path / "main" / "proj"
    activity = "class MainActivity : ComponentActivity()\n"
    write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(root, "app/src/debug/AndroidManifest.xml", manifest(LAUNCHER))
    write(root, ACTIVITY, activity)
    write(root, "app/build.gradle.kts", "")
    pl = mark.plan(root, module=":app")
    check("one module, no ambiguity", pl.module == ":app" and not pl.ambiguity, pl.ambiguity)


def test_a_mistyped_module_names_the_ones_there(tmp_path: Path) -> None:
    _launcher(tmp_path, "class MainActivity : ComponentActivity()\n", namespace="com.example.app")
    pl = mark.plan(tmp_path, module=":application")
    check("matches none of", pl.ambiguity and "matches none of: :app" in pl.ambiguity[0],
          (pl.ambiguity, pl.notes))


def test_a_form_feed_is_no_line_break(tmp_path: Path) -> None:
    src = ("class A {\n    fun one() {\n        // section\x0c\n        a()\n    }\n\n"
           "    fun pad() {\n    }\n    fun two() {\n        b()\n    }\n}\n")
    found = mark.enclosing_block(src, ".kt", 10)
    check("two, at its own line", found is not None and found[:2] == ("two", 9), found)
    path = tmp_path / "A.kt"
    path.write_text(src, encoding="utf-8")
    check("declared_at agrees", place.declared_at(path, "two") == 9, place.declared_at(path, "two"))


def test_an_annotation_s_parenthesis_is_not_the_parameter_list() -> None:
    src = ('class A {\n    @Suppress("UNUSED_PARAMETER") fun load(limit: Int = 10) {\n'
           "        work()\n    }\n}\n")
    found = mark.enclosing_block(src, ".kt", 3)
    check("load", found is not None and found[0] == "load", found)


def test_fork_join_pools_are_named_as_fork_join_names_them(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/kotlin/Pools.kt",
          "val a = ForkJoinPool(4)\nval b = Executors.newWorkStealingPool()\n")
    rows = mark.plan_pools(tmp_path).proposals
    check("ForkJoinPool-N-worker-M", rows and all("ForkJoinPool-N-worker-M" in p.what for p in rows),
          [p.what for p in rows])


def test_a_hyphen_in_a_kotlin_frame_is_read() -> None:
    got = place.parse_frame("void com.example.app.Repo.update-Wv2hJ0w(long)(Repo.kt:30)")
    check("parsed", got is not None and got[1:] == ("Repo.kt", 30), got)
