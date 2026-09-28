#!/usr/bin/env python3
"""A frame from an ANR report, on the right function in the right file.

`anr --root` and `mark --from-anr` promise that a frame is followed to the file
and the line the compiler wrote into it, and to the function around that line
— and that a checkout which is the build that froze is not called another one.
Read against a checkout that was the right one, they called it another build
anyway — "the report is from another version", "probably not the build that
froze" — from three causes:

- the function a frame names was read off the first `$` segment, so a member
  of a companion or a nested class never agreed with the line it carried, and
  a lambda compiled by javac or with invokedynamic never did either;
- a Java method with no modifier was in no function at all;
- frames of libraries the list had never heard of — dagger, koin, sentry —
  were the project's code, missing from the checkout.

And a frame of one package was placed, as certain, in another whose directory
merely contained its name. Each of those is pinned here, with the wording that
came with them: the gap names the input that was read, a monitor that was never
obfuscated is named once, a trace with no header still names its process, and
the help and the refusal both name Play Console.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr, mark  # noqa: E402
from echolot.main import build_parser  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import DROPBOX, DUMP, PLAY, run  # noqa: E402


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def line_in(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    raise AssertionError(needle)


# --- the function a frame names -----------------------------------------------

@pytest.mark.parametrize("symbol, written", [
    # A member of a class nested in another is that member. The first `$`
    # segment is the class, and reading it as the function is what refused
    # these on a correct checkout.
    ("com.example.app.Adapter$ViewHolder.bind", "bind"),
    ("com.example.app.Repo$Companion.warm", "warm"),
    ("com.example.app.Registry.lookup", "lookup"),             # an `object`
    ("com.example.app.Outer$Registry.lookup", "lookup"),       # one nested
    # An anonymous class is entered through a method the source declares.
    ("com.example.app.MainActivity$1.onClick", "onClick"),
    ("com.example.app.Screen$load$1.onClick", "onClick"),
    # A lambda Kotlin compiled into a class of its own.
    ("com.example.app.Handler$updateLocality$2.invokeSuspend", "updateLocality"),
    ("com.example.app.Repo$Companion$warm$1.invoke", "warm"),
    ("com.example.app.Screen$load$1$1.invoke", "load"),
    ("com.example.app.Screen$Holder$bind$1.invokeSuspend", "bind"),
    # One Kotlin compiled with invokedynamic, the default since 2.0, and the
    # hyphen it used before 1.8.
    ("com.example.app.Repo.load$lambda$0", "load"),
    ("com.example.app.Repo.load$lambda$1$lambda$0", "load"),
    ("com.example.app.Repo.load$lambda-0", "load"),
    # javac's.
    ("com.example.app.Store.lambda$flush$0", "flush"),
    ("com.example.app.Store$Inner.lambda$drain$3", "drain"),
    # Names the compiler adds beside a function a person wrote.
    ("com.example.app.Repo.load$default", "load"),
    ("com.example.app.Repo.load$suspendImpl", "load"),
    # A class the toolchain made, not anyone here.
    ("com.example.app.Screen$load$1$invokeSuspend$$inlined$collect$1.emit", "load"),
])
def test_a_frame_names_the_function_a_person_wrote(symbol, written):
    got = mark.frame_function(symbol)
    check(f"`{symbol}` was written in `{written}`", got == written, got)


KOTLIN = """\
package com.example.app.data

import com.example.app.log.Log

class Repo {

  fun load() {
    scope.launch {
      fetch()
    }
    handler.post {
      render()
    }
  }

  class ViewHolder {
    fun bind() {
      draw()
      log()
    }
  }

  companion object {
    fun warm() {
      prime()
      log()
    }
  }
}
"""

JAVA = """\
package com.example.app.data;

class Store {
    private final Object lock = new Object();

    void flush() {
        executor.execute(() -> {
            write();
        });
        if (dirty)
        {
            log();
        }
        else if (stale)
        {
            reload();
        }
    }
}
"""


@pytest.fixture
def sources(tmp_path) -> Path:
    return write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/data/Repo.kt": KOTLIN,
        "app/src/main/java/com/example/app/data/Store.java": JAVA,
    })


@pytest.mark.parametrize("symbol, file, needle, function", [
    ("com.example.app.data.Repo$Companion.warm", "Repo.kt", "prime()", "warm"),
    ("com.example.app.data.Repo$ViewHolder.bind", "Repo.kt", "draw()", "bind"),
    ("com.example.app.data.Repo$load$1.invokeSuspend", "Repo.kt", "fetch()", "load"),
    ("com.example.app.data.Repo.load$lambda$0", "Repo.kt", "render()", "load"),
    ("com.example.app.data.Store.flush", "Store.java", "log();", "flush"),
    ("com.example.app.data.Store.lambda$flush$0", "Store.java", "write();", "flush"),
])
def test_a_frame_from_the_build_that_froze_can_take_a_pair(
        sources, symbol, file, needle, function):
    """The line and the name agree, so the pair goes in.

    Only the lambda compiled into a class of its own went in before. The rest
    were refused: the name came out as `Companion`, `ViewHolder`,
    `load$lambda$0` or `lambda$flush$0`, and a Java method with no modifier
    had no function around it at all.
    """
    rel = f"app/src/main/java/com/example/app/data/{file}"
    text = (sources / rel).read_text(encoding="utf-8")
    plan = mark.plan_from_anr(sources, [(symbol, rel, line_in(text, needle))])
    proposal = plan.proposals[0]
    check("applicable", proposal.applicable, proposal.reason)
    check(f"bracketing `{function}`",
          proposal.line == line_in(text, f"{function}()"), proposal)
    check("and nothing says the checkout is another build", not plan.notes,
          plan.notes)


def test_a_java_method_without_a_modifier_is_a_function():
    """Package-private is the absence of a modifier, and it is legal Java."""
    found = mark.enclosing_block(JAVA, ".java", line_in(JAVA, "log();"))
    check("found", found is not None and found[0] == "flush", found)


def test_a_statement_in_a_declarations_shape_is_not_one():
    """With the modifier optional, `else if (stale)` has a type, a name and a
    parenthesis — and a line under it would have been inside a function called
    `if`, which no frame names."""
    found = mark.enclosing_block(JAVA, ".java", line_in(JAVA, "reload();"))
    check("the method, not the branch", found is not None and found[0] == "flush",
          found)
    for statement in ("        return fetch(key);",
                      "        new Runnable() {",
                      "        else if (stale) {",
                      "        throw failure(key);"):
        check(f"`{statement.strip()}` is not a declaration",
              mark._JAVA_DECL.match(statement) is None, statement)


def test_a_collect_block_is_refused_rather_than_bracketed_around_its_function(
        tmp_path):
    """A lambda compiled into a class for an interface reads like an anonymous
    object: `Screen$load$1$1.emit`. Its line falls in `load` while the frame
    says `emit`, and it is refused. A pair around `load` would time the call
    that set the block up, not the block."""
    source = ("package com.example.app\n\nclass Screen {\n  fun load() {\n"
              "    scope.launch {\n      flow.collect {\n        render(it)\n"
              "      }\n    }\n  }\n}\n")
    rel = "app/src/main/java/com/example/app/Screen.kt"
    root = write(tmp_path / "checkout", {rel: source})
    plan = mark.plan_from_anr(root, [("com.example.app.Screen$load$1$1.emit", rel,
                                      line_in(source, "render(it)"))])
    proposal = plan.proposals[0]
    check("refused", not proposal.applicable, proposal)
    check("naming both sides", "`load`" in proposal.reason
          and "`emit`" in proposal.reason, proposal.reason)


# --- the project's frames and a library's -------------------------------------

# A freeze inside the dependency graph: the main thread in a dagger provider,
# through koin and sentry, with one frame of the app under all of it. The
# libraries are exactly the ones `anr.LIBRARIES` does not carry.
UNLISTED = """\
# Crashlytics -
# Application: com.example.app
# Platform: android
# Version: 1.2.3 (45)

main (blocked):tid=1 systid=1001 | waiting to lock <0x0a714d13> (dagger.internal.DoubleCheck) held by thread 12
       at dagger.internal.DoubleCheck.get(DoubleCheck.java:47)
       at org.koin.core.instance.SingleInstanceFactory.get(SingleInstanceFactory.kt:53)
       at org.koin.core.scope.Scope.resolveInstance(Scope.kt:245)
       at io.sentry.android.core.SentryAndroid.init(SentryAndroid.java:84)
       at com.example.app.App.onCreate(App.kt:6)
       at android.app.Instrumentation.callApplicationOnCreate(Instrumentation.java:1277)

Worker-2 (waiting):tid=12 systid=1012
       at jdk.internal.misc.Unsafe.park(Native method)
       at io.sentry.transport.QueuedThreadPoolExecutor.submit(QueuedThreadPoolExecutor.java:61)
       at org.koin.core.Koin.get(Koin.kt:100)
       at com.example.sdk.Payments.warm(Payments.kt:4)
       at dagger.internal.DoubleCheck.get(DoubleCheck.java:47)
       at java.lang.Thread.run(Thread.java:1012)
"""

APP = """\
package com.example.app

class App : Application() {

  override fun onCreate() {
    super.onCreate()
    startKoin()
    SentryAndroid.init(this)
  }
}
"""


@pytest.fixture
def app(tmp_path) -> Path:
    """The build that froze, with a file named like a library's class in it."""
    return write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/App.kt": APP,
        # `Koin.kt` is a natural name for the file that starts Koin. It is not
        # `org.koin.core.Koin`, and one file of that name must not make it so.
        "app/src/main/java/com/example/app/di/Koin.kt":
            "package com.example.app.di\n\nfun startKoin() {\n}\n",
    })


def test_libraries_the_list_never_heard_of_are_not_the_projects_code(app):
    report = anr.parse(UNLISTED)
    placed, missing = anr.locate(report, app)
    check("the app's own frame is placed",
          [(f.symbol, f.file, f.line) for f in placed]
          == [("com.example.app.App.onCreate",
               "app/src/main/java/com/example/app/App.kt", 6)], placed)
    check("and no library frame is counted as this checkout's missing code",
          missing == [], missing)
    text = anr.render(report, (placed, missing))
    check("the report does not call the checkout another version",
          "this checkout does not have" not in text, text)
    check("the library's `Koin.kt` is not the app's",
          "di/Koin.kt" not in text, text)


def test_the_stacks_draw_the_line_where_the_placement_did(app):
    """A stack that printed `DoubleCheck.get` as the app's own next to a
    placement that had set it aside would be two readings of one file."""
    report = anr.parse(UNLISTED)
    anr.locate(report, app)
    main = report.main
    check("the app's frame is the one nearest to it",
          main.nearest(report.ownership) == "com.example.app.App.onCreate(App.kt:6)",
          main.nearest(report.ownership))
    check("and the only one the project owns",
          main.own(report.ownership) == ["com.example.app.App.onCreate(App.kt:6)"],
          main.own(report.ownership))


def test_a_company_sdk_under_the_same_root_is_not_missing_from_the_checkout(app):
    """`com.example.sdk` shares the app's first two segments and is not here.

    Without a checkout that root is the best guess at the rest of the project.
    With one it is a guess the checkout has already answered, and kept, it
    counted the SDK as code the checkout had lost.
    """
    report = anr.parse(UNLISTED)
    worker = report.by_tid()["12"]
    check("without a checkout the root claims it",
          any("com.example.sdk" in f for f in worker.own(report.ownership)),
          worker.own(report.ownership))
    anr.locate(report, app)
    check("with one it does not",
          not any("com.example.sdk" in f for f in worker.own(report.ownership)),
          worker.own(report.ownership))


def test_marking_from_that_report_does_not_call_the_checkout_another_build(
        app, tmp_path):
    report_file = tmp_path / "export.txt"
    report_file.write_text(UNLISTED, encoding="utf-8")
    code, out, _ = run("mark", "--root", str(app), "--from-anr",
                       str(report_file), "--json")
    check("exit 0", code == 0, code)
    plan = json.loads(out)
    check("one proposal, for the app's own frame",
          [p["marker"] for p in plan["proposals"]] == ["AGENTTMP_App_onCreate"],
          plan["proposals"])
    check("and no note about the wrong build",
          not any("not the build that froze" in n for n in plan["notes"]),
          plan["notes"])


def test_the_checkout_decides_by_the_package_line_not_the_directory(tmp_path):
    """Kotlin lets a file sit in a directory that does not spell its package —
    and an editor can leave a byte-order mark in front of the line."""
    root = write(tmp_path / "checkout", {
        "app/src/main/java/legacy/Sync.kt":
            chr(0xFEFF) + "package com.example.work\n\nclass Sync {\n"
                          "  fun run() {\n  }\n}\n",
    })
    report = anr.parse(UNLISTED.replace(
        "com.example.app.App.onCreate(App.kt:6)",
        "com.example.work.Sync.run(Sync.kt:4)"))
    placed, missing = anr.locate(report, root)
    check("declared there, so the project's, and placed",
          [f.file for f in placed] == ["app/src/main/java/legacy/Sync.kt"], placed)
    check("with the libraries still set aside", missing == [], missing)


def test_the_platform_stays_the_platforms_whatever_a_stub_declares(tmp_path):
    """A unit-test stub of `android.util.Log` declares `package android.util`.
    The frame is the platform's all the same, and is not placed on the stub."""
    root = write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/App.kt": APP,
        "app/src/test/java/android/util/Log.java":
            "package android.util;\n\npublic class Log {\n}\n",
    })
    report = anr.parse(UNLISTED.replace(
        "at dagger.internal.DoubleCheck.get(DoubleCheck.java:47)\n       at org",
        "at android.util.Log.d(Log.java:10)\n       at org", 1))
    placed, _ = anr.locate(report, root)
    check("the stub is not where the frame is",
          all(not f.file.endswith("Log.java") for f in placed), placed)


def test_a_package_is_the_projects_up_to_the_dot():
    """`com.example.app` is not the root of `com.example.application`."""
    ours = anr.Ownership("com.example.app", frozenset({"com.example.app"}))
    check("its own package", ours.claims("com.example.app.App.onCreate(App.kt:6)"))
    check("and one under it", ours.claims("com.example.app.data.Repo.load(Repo.kt:1)"))
    check("not a longer name that begins the same",
          not ours.claims("com.example.application.Foo.bar(Foo.kt:1)"))


def test_without_a_checkout_the_list_still_decides(tmp_path):
    """No sources to ask — the report is still read, and the app's frame is
    still on the stack it was on."""
    report = anr.parse(UNLISTED)
    text = anr.render(report)
    check("the main thread's stack still reaches the app",
          "com.example.app.App.onCreate(App.kt:6)" in text, text)
    empty = tmp_path / "empty"
    empty.mkdir()
    report_file = tmp_path / "export.txt"
    report_file.write_text(UNLISTED, encoding="utf-8")
    code, out, _ = run("anr", str(report_file), "--root", str(empty))
    check("and a root with nothing in it changes none of that",
          code == 0 and "com.example.app.App.onCreate(App.kt:6)" in out, out)


def test_a_library_is_a_lead_whether_the_list_knows_it_or_not(app):
    """With nothing of the app anywhere, the frame nearest to it is a
    library's — and `sentry` is one the list has never carried. Asking the
    list as well would have dropped the only lead the file had."""
    report = anr.parse(UNLISTED.replace(
        "       at com.example.app.App.onCreate(App.kt:6)\n", ""))
    placed, missing = anr.locate(report, app)
    text = anr.render(report, (placed, missing))
    check("nothing here is the project's",
          "Every frame here belongs to the platform or a library" in text, text)
    check("and sentry is named as where to read next",
          "The frames nearest to the app:" in text
          and "`io.sentry.transport.QueuedThreadPoolExecutor.submit"
              "(QueuedThreadPoolExecutor.java:61)` on **Worker-2**" in text, text)


# --- the package, whole -------------------------------------------------------

def test_a_package_is_matched_whole_not_inside_a_longer_one(tmp_path):
    """`com/example/a` is a substring of `com/example/app`. As one it placed a
    frame of `com.example.a`, as certain, in the other package's file — the
    first in sorted order — while its own sat in the next module."""
    root = write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/Mapper.kt": "package com.example.app\n",
        "lib/src/main/java/com/example/a/Mapper.kt": "package com.example.a\n",
    })
    found = anr.place("com.example.a.Mapper.map(Mapper.kt:5)",
                      anr.source_index(root), root)
    check("its own package's file",
          found is not None and found.file == "lib/src/main/java/com/example/a/Mapper.kt",
          found)
    check("and certain", found.exact, found)


def test_a_file_in_a_package_below_is_not_the_packages(tmp_path):
    """The directory ends in the package; containing it is a package below."""
    root = write(tmp_path / "checkout", {
        "core/src/main/java/com/example/a/sub/Mapper.kt": "package com.example.a.sub\n",
        "feature/src/main/java/com/example/a/Mapper.kt": "package com.example.a\n",
    })
    found = anr.place("com.example.a.Mapper.map(Mapper.kt:5)",
                      anr.source_index(root), root)
    check("the package's own directory",
          found is not None and found.file.startswith("feature/"), found)


def test_two_candidates_neither_in_the_package_is_still_a_guess(tmp_path):
    root = write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/Mapper.kt": "package com.example.app\n",
        "lib/src/main/java/com/other/Mapper.kt": "package com.other\n",
    })
    found = anr.place("com.example.a.Mapper.map(Mapper.kt:5)",
                      anr.source_index(root), root)
    check("placed, and said to be one of several",
          found is not None and not found.exact, found)


# --- what the report says about the file it read ------------------------------

NO_SUBJECT = DROPBOX.replace(
    "Subject: Input dispatching timed out (com.example.app/.MainActivity is not "
    "responding. Waited 10000ms for MotionEvent)\n", "")


@pytest.mark.parametrize("dump, names, others", [
    (DUMP, "A Crashlytics export carries no reason", ("Play Console",)),
    (PLAY, "A Play Console export carries no reason", ("Crashlytics",)),
    (NO_SUBJECT, "ART's dump without the drop box's header",
     ("Crashlytics", "Play Console")),
], ids=["crashlytics", "play", "dumpsys"])
def test_the_missing_reason_is_said_of_the_input_that_was_read(dump, names, others):
    gap = next((line for line in anr.render(anr.parse(dump)).splitlines()
                if line.startswith("- Why the system fired")), "")
    check("the input is named as what it is", names in gap, gap)
    check("and no other kind of input is", not any(o in gap for o in others), gap)
    check("the reader is sent to the one record that has a reason",
          "dumpsys dropbox --print data_app_anr" in gap, gap)


def test_a_record_that_has_its_reason_claims_no_gap():
    check("no gap", "- Why the system fired" not in anr.render(anr.parse(DROPBOX)))


def test_the_device_record_is_not_sent_to_itself_for_lock_notes():
    """ART writes the note on every thread that waits for a monitor, so its
    own record without one says nothing was — and pointing it at `dumpsys
    dropbox` for the notes it lacks was pointing it at itself."""
    quiet = NO_SUBJECT.replace(
        '"main" prio=5 tid=1 Blocked', '"main" prio=5 tid=1 Runnable').replace(
        "  - waiting to lock <0x0a714d13> (a com.example.app.data.q) held by thread 12\n",
        "").replace("  - locked <0x0a714d13> (a com.example.app.data.q)\n", "")
    report = anr.parse(quiet)
    check("no note left in it, and nothing blocked",
          not report.lock_notes and not anr.chains(report), report.threads)
    check("and no gap claimed for it",
          "Who was holding what" not in anr.render(report), anr.render(report))
    check("while an export without notes still says it cannot tell",
          "Who was holding what" in anr.render(anr.parse(PLAY)))


def test_a_monitor_that_was_never_obfuscated_is_named_once():
    plain = DUMP.replace("(com.example.app.data.q)", "(com.example.app.data.StateStore)")
    chain = anr.chains(anr.parse(plain))[0]
    check("it resolves to itself", chain.named == chain.monitor, chain)
    line = next(line for line in anr.render(anr.parse(plain)).splitlines()
                if "denied" in line)
    check("and is not printed again as the dump's", "in the dump" not in line, line)
    obfuscated = next(line for line in anr.render(anr.parse(DUMP)).splitlines()
                      if "denied" in line)
    check("where R8 renamed it, the dump's name stays beside",
          "(`com.example.app.data.q` in the dump)" in obfuscated, obfuscated)


def test_a_trace_with_no_header_is_titled_by_its_cmd_line():
    """A trace pulled from `/data/anr/` is the process block alone."""
    start = DROPBOX.index("----- pid 4100")
    end = DROPBOX.index("----- Waiting Channels")
    trace = DROPBOX[start:end]
    report = anr.parse(trace)
    check("read as ART's", report.source == anr.DUMPSYS.name, report.source)
    check("the process is known", report.package == "com.example.app", report.head)
    text = anr.render(report)
    check("and the report is titled by it", "**com.example.app**" in text, text)
    check("not as an unknown one", "unknown application" not in text, text)


# --- the verb, and what it says it reads --------------------------------------

def test_the_refusal_names_every_form_the_reader_knows(tmp_path):
    strange = tmp_path / "strange.txt"
    strange.write_text("Thread 1 <main> RUNNING\n  frame: Foo.bar\n", encoding="utf-8")
    code, _, err = run("anr", str(strange))
    check("refused", code == 2, code)
    for form in ("Crashlytics", "Play Console", "dumpsys dropbox"):
        check(f"naming {form}", form in err, err)


def test_the_help_names_play_console_and_what_the_verb_writes():
    parser = build_parser()
    sub = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    text = " ".join(sub.choices["anr"].format_help().split())
    check("Play Console is one of the inputs", "Play Console" in text, text)
    check("the run log is what it writes", ".echolot/log/runs.jsonl" in text, text)
    check("not that it writes nothing", "writes nothing" not in text, text)
