"""`mark --pools` on an app with a native core (#1010).

The hot `Thread-#` row of a map pan was eight native threads — a Vulkan
renderer and the core's workers — attached to the VM with
`AttachCurrentThread(&env, 0)`, which ART names `Thread-N`. `--pools` read
Kotlin and Java only, listed a vendored library's notifier and perf tests
first, and said a `ThreadPoolExecutor` made `Thread-N` threads. None of the
four rows named one of the eight.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from echolot import domains, mark, recorder
from echolot.main import main
from tests.support import check
from tests.test_mark_edits import write

CPP = "sdk/src/main/cpp"


def _rows(root: Path, allowed: list[str] | None = None) -> list[tuple[str, int, str]]:
    return [(p.file, p.line, p.source) for p in mark.plan_pools(root, allowed).proposals]


@pytest.mark.parametrize("call", [
    "vm->AttachCurrentThread(&env, nullptr);",
    "vm->AttachCurrentThread(&env, 0);",
    "vm->AttachCurrentThread(&env, NULL);",
    "vm->AttachCurrentThread(&env, (void*)0);",
    "vm->AttachCurrentThreadAsDaemon(&env, nullptr);",
    "(*vm)->AttachCurrentThread(vm, &env, NULL);",
    "jvm()->AttachCurrentThread(\n      &env,\n      nullptr);",
])
def test_an_attach_with_no_name_is_a_row(tmp_path: Path, call: str) -> None:
    write(tmp_path, f"{CPP}/attach.cpp", f"void Attach() {{\n  JNIEnv * env;\n  {call}\n}}\n")
    rows = mark.plan_pools(tmp_path).proposals
    check("one row, at the call", [(p.line, p.kind, p.source) for p in rows]
          == [(3, "thread_name", "jni")], [(p.line, p.what) for p in rows])
    check("it says what the trace calls the thread", "`Thread-N`" in rows[0].what, rows[0].what)
    check("and nothing claims it can be applied", not rows[0].applicable)


@pytest.mark.parametrize("text", [
    "JavaVMAttachArgs args{JNI_VERSION_1_6, \"render\", nullptr};\n"
    "  vm->AttachCurrentThread(&env, &args);",
    "vm->AttachCurrentThread(&env, attachArgs);",
    "// vm->AttachCurrentThread(&env, nullptr);",
    "const char * s = \"AttachCurrentThread(&env, nullptr)\";",
], ids=["named", "a-variable", "a-comment", "a-string"])
def test_a_named_attach_or_a_mention_is_not(tmp_path: Path, text: str) -> None:
    write(tmp_path, f"{CPP}/attach.cpp", f"void Attach() {{\n  {text}\n}}\n")
    check("no row", _rows(tmp_path) == [], _rows(tmp_path))


def test_native_code_is_read_wherever_it_lives_and_not_in_a_test_set(tmp_path: Path) -> None:
    body = "void A(JavaVM * vm) {\n  JNIEnv * env;\n  vm->AttachCurrentThread(&env, nullptr);\n}\n"
    write(tmp_path, "core/platform/thread.cc", body)
    write(tmp_path, "app/jni/attach.h", body)
    write(tmp_path, "sdk/src/test/cpp/attach_test.cpp", body)
    write(tmp_path, "build/intermediates/attach.cpp", body)
    files = sorted(f for f, _, _ in _rows(tmp_path))
    check("the core and jni/, not src/test or build/",
          files == ["app/jni/attach.h", "core/platform/thread.cc"], files)


def test_a_thread_pool_executor_makes_pool_threads(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/java/a/Pools.java",
          "class Pools {\n"
          "  ThreadPoolExecutor a = new ThreadPoolExecutor(1, 1, 5, SECONDS, q);\n"
          "  ScheduledThreadPoolExecutor b = new ScheduledThreadPoolExecutor(1);\n"
          "  void go() { new Thread(r).start(); }\n}\n")
    got = [(p.line, p.kind, p.what.split("`")[1]) for p in mark.plan_pools(tmp_path).proposals]
    check("the pools are pools, the thread a thread", got == [
        (2, "pool_name", "pool-N-thread-M"),
        (3, "pool_name", "pool-N-thread-M"),
        (4, "thread_name", "Thread-N"),
    ], got)


def test_the_attach_rows_come_first_and_allowed_before_outside(tmp_path: Path) -> None:
    thread = "class X {\n  void go() { new Thread(r).start(); }\n}\n"
    write(tmp_path, "aaa/src/main/java/a/A.java", thread)
    write(tmp_path, "sdk/src/main/java/a/B.java", thread)
    write(tmp_path, "zzz/src/main/cpp/attach.cpp",
          "void A() {\n  vm->AttachCurrentThread(&env, nullptr);\n}\n")
    write(tmp_path, f"{CPP}/attach.cpp",
          "void A() {\n  vm->AttachCurrentThread(&env, nullptr);\n}\n")
    got = [f for f, _, _ in _rows(tmp_path, ["sdk/src/main"])]
    check("allowed before outside, and the attach first in each", got == [
        f"{CPP}/attach.cpp", "sdk/src/main/java/a/B.java",
        "zzz/src/main/cpp/attach.cpp", "aaa/src/main/java/a/A.java",
    ], got)


def test_the_notes_say_what_a_native_thread_row_is(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/java/a/W.java", "class W {\n  Thread t = new Thread(r);\n}\n")
    notes = " ".join(mark.plan_pools(tmp_path).notes)
    check("with only Java threads: a native Thread-N is attached elsewhere",
          "native" in notes and "prebuilt library" in notes, notes)
    write(tmp_path, f"{CPP}/attach.cpp", "void A() {\n  vm->AttachCurrentThread(&env, 0);\n}\n")
    notes = " ".join(mark.plan_pools(tmp_path).notes)
    check("with an attach: how to name it", "JavaVMAttachArgs" in notes
          and "prebuilt library" not in notes, notes)


def test_an_empty_plan_says_neither_was_found(tmp_path: Path) -> None:
    write(tmp_path, "app/src/main/java/a/A.java", "class A {}\n")
    notes = " ".join(mark.plan_pools(tmp_path).notes)
    check("both kinds named", "JDK's default naming" in notes and "attached" in notes, notes)


def test_a_submodule_is_counted_not_listed(tmp_path: Path) -> None:
    write(tmp_path, ".gitmodules",
          '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://example.com/lib\n'
          '[submodule "vendor/demo"]\n\tpath = "vendor/demo"\n\turl = https://example.com/demo\n')
    thread = "class X {\n  void go() { new Thread(r).start(); }\n}\n"
    write(tmp_path, "vendor/lib/perf-tests/src/main/java/p/Perf.java", thread)
    write(tmp_path, "vendor/lib/core/src/main/java/p/Notifier.java", thread)
    write(tmp_path, "vendor/demo/main.cpp", "void A() {\n  vm->AttachCurrentThread(&env, nullptr);\n}\n")
    write(tmp_path, "vendor/libfoo/src/main/java/p/Copied.java", thread)
    write(tmp_path, "app/src/main/java/a/A.java", thread)
    pl = mark.plan_pools(tmp_path)
    files = [p.file for p in pl.proposals]
    check("the project's own, and a folder that only looks vendored",
          files == ["app/src/main/java/a/A.java", "vendor/libfoo/src/main/java/p/Copied.java"],
          files)
    check("the rest counted by submodule",
          any("3 more in git submodules" in n and "vendor/demo (1)" in n and "vendor/lib (2)" in n
              for n in pl.notes), pl.notes)


def test_submodules_are_read_from_the_repository_the_root_is_in(tmp_path: Path) -> None:
    write(tmp_path, ".gitmodules",
          '[submodule "a"]\n  path = android/libs/vendored\n  url = x\n'
          '[submodule "b"]\n  path = external/text\n  url = y\n')
    (tmp_path / ".git").mkdir()
    android = tmp_path / "android"
    (android / "libs/vendored").mkdir(parents=True)
    check("from the top: both", domains.submodules(tmp_path) == [
        (android / "libs/vendored").resolve(), (tmp_path / "external/text").resolve()],
        domains.submodules(tmp_path))
    check("from a folder below: the one under it",
          domains.submodules(android) == [(android / "libs/vendored").resolve()],
          domains.submodules(android))
    inside = tmp_path / "external/text"
    inside.mkdir(parents=True)
    (inside / ".git").write_text("gitdir: ../../.git/modules/external/text\n", encoding="utf-8")
    check("from inside a submodule: none of the parent's", domains.submodules(inside) == [],
          domains.submodules(inside))
    check("no .gitmodules, no submodules", domains.submodules(tmp_path / "nowhere") == [])


def test_the_command_prints_the_attach_row(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "_root", None)
    write(tmp_path, f"{CPP}/attach.cpp", "void A() {\n  vm->AttachCurrentThread(&env, 0);\n}\n")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["mark", "--pools", "--root", str(tmp_path)])
    text = out.getvalue()
    check("ran", code == 0, text + err.getvalue())
    check("the row, its source, and the advice", f"{CPP}/attach.cpp:2" in text and "[jni]" in text
          and "JavaVMAttachArgs" in text and "--apply" not in text, text)
