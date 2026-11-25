"""`mark` on a Views-only app whose code has a `setContent` of its own (#1005).

`setContent\\s*(…)?\\s*{` took a Java widget's
`public void setContent(int title, int subtitle)`, brace on the next line,
for Compose's `setContent(…) { … }`, and the app was told the Compose tracing
library was the first thing to add. Compose's `setContent` takes a composable
lambda, which only Kotlin writes; and a `fun` or a return type before the name
declares it rather than calls it.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from echolot import mark, recorder, selftest
from echolot.main import main
from tests.support import check

ROOT = "app/src/main/java/com/example/app"


def _note(root: Path) -> str | None:
    return next((n for n in mark.plan(root).notes if "runtime-tracing" in n), None)


def _app(root: Path, *extra: tuple[str, str]) -> Path:
    selftest._views_app(root, *extra)
    return root


def test_a_java_method_with_its_brace_on_the_next_line_is_not_compose(tmp_path: Path) -> None:
    check("no note", _note(_app(tmp_path)) is None, mark.plan(tmp_path).notes)


@pytest.mark.parametrize("rel, text", [
    (f"{ROOT}/Knr.java",
     "class Knr extends View {\n  void setContent(int x) {\n    a = x;\n  }\n}\n"),
    (f"{ROOT}/Generic.java",
     "class Generic {\n  public List<String> setContent(int x)\n  {\n    return null;\n  }\n}\n"),
    (f"{ROOT}/Banner.kt",
     "class Banner : View() {\n    fun setContent(title: Int) {\n        text = title\n    }\n}\n"),
    (f"{ROOT}/Ext.kt",
     "fun Banner.setContent(title: Int) {\n    text = title\n}\n"),
    (f"{ROOT}/Said.kt",
     "// setContent { AppRoot() } goes here once the screen is Compose\n"
     "/* @Composable */ fun later() = Unit\n"),
], ids=["java-knr", "java-generic", "kotlin-member", "kotlin-extension", "kotlin-comment"])
def test_a_declaration_or_a_comment_is_not_compose(tmp_path: Path, rel: str, text: str) -> None:
    check("no note", _note(_app(tmp_path, (rel, text))) is None, mark.plan(tmp_path).notes)


@pytest.mark.parametrize("text", [
    "fun show(v: ComposeView) {\n    v.setContent { AppRoot() }\n}\n",
    "fun show(v: ComposeView?) {\n    v?.setContent {\n        AppRoot()\n    }\n}\n",
    "class A : ComponentActivity() {\n    fun go(p: CompositionContext) {\n"
    "        setContent(p) { AppRoot() }\n    }\n}\n",
    "fun Banner.setContent(title: Int) {\n    text = title\n}\n"
    "fun show(v: ComposeView) {\n    v.setContent { AppRoot() }\n}\n",
    "@Composable\nfun Screen() {\n    Text(\"home\")\n}\n",
], ids=["receiver", "safe-call", "with-parent", "declaration-then-call", "composable"])
def test_compose_in_kotlin_still_gets_the_note(tmp_path: Path, text: str) -> None:
    root = _app(tmp_path, (f"{ROOT}/Home.kt", text))
    check("the note is there", _note(root) is not None, mark.plan(root).notes)


def test_a_java_launcher_with_its_own_setcontent_is_inflated_with_views(tmp_path: Path) -> None:
    """The launcher's `setContent(` is the Activity's own method: the row is
    `setContentView`, and nothing claims a Compose tree."""
    launcher = (f"{ROOT}/MainActivity.java",
                "package com.example.app;\n\npublic class MainActivity extends Activity\n{\n"
                "  @Override\n  protected void onCreate(Bundle b)\n  {\n"
                "    super.onCreate(b);\n    setContentView(R.layout.main);\n  }\n\n"
                "  void setContent(int which)\n  {\n    show(which);\n  }\n}\n")
    kinds = [p.kind for p in mark.plan(_app(tmp_path, launcher)).proposals]
    check("set_content_view, not set_content",
          "set_content_view" in kinds and "set_content" not in kinds, kinds)


@pytest.mark.parametrize("before, declares", [
    ("  public void ", True),
    ("    fun ", True),
    ("override fun ComposeView.", True),
    ("fun <T> ", True),
    ("  public List<String> ", True),
    ("    ", False),
    ("    view.", False),
    ("    return ", False),
    ("    else ", False),
    ("    if (a) ", False),
    ("    a -> ", False),
    ("    val x = ", False),
])
def test_what_stands_before_the_name_tells_a_declaration_from_a_call(before: str,
                                                                    declares: bool) -> None:
    clean = before + "setContent {\n}\n"
    check(repr(before), (mark.called(clean, mark._SET_CONTENT) is None) == declares)


def test_the_command_prints_no_compose_note_for_a_views_app(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "_root", None)
    root = _app(tmp_path / "views")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["mark", "--root", str(root)])
    check("ran", code == 0, out.getvalue() + err.getvalue())
    check("no runtime-tracing line", "runtime-tracing" not in out.getvalue(), out.getvalue())
