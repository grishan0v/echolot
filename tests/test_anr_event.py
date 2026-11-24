"""A Crashlytics event as an agent's tool returns it, read by `echolot anr` (#527).

An agent reaches Crashlytics through a tool, and the tool gives an event, not
the console's export: the issue it was filed under, the build's revision,
whether the app was in the foreground, the keys the app set, and one thread —
the one Crashlytics blamed — with no state, no tid and no lock note, its
stack usually cut after eighteen or nineteen frames.

Every event below is synthetic, written in the shapes the tools and the API
give: YAML in which most fields are themselves small YAML documents in a
string, JSON with a list of frame objects, and the thread text alone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import DEADLOCK, DROPBOX, DUMP, run  # noqa: E402

REVISION = "0123456789abcdef0123456789abcdef01234567"


def _event(threads: str, *, subtitle: str = "Root cause for this ANR is unknown",
           revision: str = REVISION, state: str = "BACKGROUND") -> str:
    """One event as the Firebase tools print it: a list, each field a YAML
    document of its own inside a string."""
    body = "\n".join("    " + line for line in threads.strip().splitlines())
    stamp = (f'''  buildStamp: |
    |
      repositories {{
        system: GIT
        local_root_path: "$PROJECT_DIR"
        revision: "{revision}"
      }}
''' if revision else "")
    return f"""\
- name: |
    'projects/demo/apps/1:000000000000:android:0000000000000000/events/0000000000000001'
  platform: |
    ANDROID
  bundleOrPackage: |
    com.example.app
  eventId: |
    '0000000000000001'
  eventTime: |
    '2026-11-20T10:00:00Z'
  issue: |
    id: 0123456789abcdef0123456789abcdef
    title: com.example.app.data.Store.read
    subtitle: {subtitle}
    errorType: ANR
  version: |
    displayVersion: 1.2.3
    buildVersion: '45'
    displayName: 1.2.3 (45)
  customKeys: |
    screen: cart
    sessionToken: 5f1d0c9e
  threads: |
{body}
  processState: |
    {state}
{stamp}"""


# The main thread standing in the app's own code, as the three events filed
# under "a deadlock involving the main thread" arrived: neither the other
# thread nor a lock note.
MAIN_IN_THE_APP = """\
Thread: main
at com.example.app.data.Store.read (Store.kt:5)
at com.example.app.ui.CartViewModel.load (CartViewModel.kt:12)
at android.os.Handler.handleCallback (Handler.java:958)
at android.os.Handler.dispatchMessage (Handler.java:99)
... frames omitted ...
"""

# A worker Crashlytics blamed, and no main thread at all.
WORKER = """\
Thread: DefaultDispatcher-worker-3
at jdk.internal.misc.Unsafe.park (Native method)
at java.util.concurrent.locks.LockSupport.park (LockSupport.java:341)
at com.example.app.sync.Uploader.flush (Uploader.kt:40)
at kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run (CoroutineScheduler.kt:749)
"""


def test_an_event_is_told_from_every_dump():
    check("the tool's YAML is an event",
          anr.detect(_event(MAIN_IN_THE_APP)) is anr.EVENT)
    check("its thread text alone is one too",
          anr.detect(MAIN_IN_THE_APP) is anr.EVENT)
    for name, dump, source in (("an export", DUMP, anr.CRASHLYTICS),
                               ("a deadlock export", DEADLOCK, anr.CRASHLYTICS),
                               ("the device's record", DROPBOX, anr.DUMPSYS)):
        check(f"{name} is still read as {source.name}", anr.detect(dump) is source,
              anr.detect(dump))


def test_an_event_is_read_as_partial_and_says_what_it_cannot_show():
    report = anr.parse(_event(MAIN_IN_THE_APP,
                              subtitle="ANR triggered by a deadlock involving the main thread"))
    check("one thread, the main one", [t.name for t in report.threads] == ["main"],
          report.threads)
    main = report.main
    check("with no state made up for it", main is not None and main.state == "", main)
    check("its frames in a dump's shape",
          main.frames[0] == "com.example.app.data.Store.read(Store.kt:5)", main.frames)
    check("and the cut kept", main.cut == 4, main.cut)
    text = anr.render(report)
    check("the input is said to be partial before any section",
          text.index("Read from a Crashlytics event") < text.index("## The main thread"), text)
    check("no state is printed as an empty one", "State ``" not in text, text)
    check("the missing threads and locks are a gap of their own",
          "- The other threads, their states and their locks." in text, text)
    check("the deadlock Crashlytics named is said to need the full dump",
          "Crashlytics files this one as a deadlock" in text, text)
    check("and where the full dump is",
          "the console's export of this event" in text
          and "dumpsys dropbox --print data_app_anr" in text, text)
    check("the generic lock-note gap is not said twice",
          "- Who was holding what." not in text, text)
    check("Crashlytics' own reading is printed as its",
          "Crashlytics files it as: _ANR triggered by a deadlock" in text, text)


def test_what_the_event_adds_is_printed_and_the_app_data_is_not():
    report = anr.parse(_event(MAIN_IN_THE_APP))
    text = anr.render(report)
    check("the revision", f"revision `{REVISION[:12]}`" in text, text)
    check("the process state", "in the **background**" in text, text)
    check("the custom keys by name", "`screen`, `sessionToken`" in text, text)
    check("and never their values", "5f1d0c9e" not in text and "cart`" not in text, text)
    check("the version and the issue in the title",
          "**com.example.app 1.2.3 (45)** · 01234567" in text, text)
    said = anr.summary(report)
    check("the json carries the event",
          said["source"] == "crashlytics-event"
          and said["event"] == {"title": "com.example.app.data.Store.read",
                                "subtitle": "Root cause for this ANR is unknown",
                                "process_state": "BACKGROUND", "revision": REVISION,
                                "custom_keys": ["screen", "sessionToken"]}, said["event"])
    check("and no value of a key anywhere", "5f1d0c9e" not in anr.to_json(report))


def test_an_event_without_the_main_thread_says_so():
    report = anr.parse(_event(WORKER))
    check("no main thread", report.main is None, report.threads)
    text = anr.render(report)
    check("the worker is listed with no empty state",
          "- **DefaultDispatcher-worker-3** — `com.example.app.sync.Uploader.flush" in text, text)
    check("and the report says the main thread is not in the event",
          "What the main thread was doing. The event carries the thread "
          "Crashlytics blamed, and it is not the main thread" in text, text)


def test_a_main_thread_named_after_the_process_is_still_the_main_thread():
    # The shape two events on Android 16 and 17 had: a native unwinder's
    # stack, the thread named after the process, the looper's `nextLegacy`
    # under the native half of the poll (#587).
    report = anr.parse("""\
Thread: com.example.app
at __epoll_pwait
at android::Looper::pollOnce(int, int*, int*, void**)
at android::android_os_MessageQueue_nativePollOnce(_JNIEnv*, _jobject*, long, int)
at art_jni_trampoline
at android.os.MessageQueue.nextLegacy
at android.os.Looper.loopOnce (Looper.java:211)
at android.os.Looper.loop (Looper.java:317)
at android.app.ActivityThread.main (ActivityThread.java:8705)
""")
    check("it is the main thread", report.main is not None, report.threads)
    check("a managed frame without a place reads as ART writes it",
          report.main.frames[0] == "android.os.MessageQueue.nextLegacy(Unknown Source)",
          report.main.frames)
    check("the native frames sit above it", report.main.above == 4, report.main.native)
    check("and it was idle at its looper",
          anr.idle_reason(report.main, report.ownership) == "looper idle")


def test_a_main_thread_stopped_for_a_collection_reads_as_one():
    # The garbage-collection event: the native frames say it (#591).
    report = anr.parse(_event("""\
Thread: main
at syscall
at art::ConditionVariable::WaitHoldingLocks
at art::gc::Heap::WaitForGcToCompleteLocked
at art::gc::Heap::AllocateInternalWithGc
at android.view.Choreographer$FrameData.allocateFrameTimelines (Choreographer.java:1102)
at android.view.Choreographer.doFrame (Choreographer.java:870)
""", subtitle="ANR occurred during garbage collection"))
    said = anr.summary(report)["main"]
    check("waiting for a collection", said["waiting_for"] == "gc", said)
    check("an allocation's", said["gc"]["cause"] == "allocation", said)
    text = anr.render(report)
    check("said once, with the event's gap beside it",
          "waiting for a garbage collection" in text
          and "This file holds the main thread alone" in text, text)


def test_the_api_shape_is_read_the_same():
    event = {
        "bundleOrPackage": "com.example.app",
        "issue": {"id": "0123456789abcdef", "title": "t",
                  "subtitle": "ANR triggered by thread waiting for a binder transaction"},
        "version": {"displayVersion": "1.2.3", "buildVersion": "45"},
        "processState": "FOREGROUND",
        "buildStamp": json.dumps({"repositories": [{"revision": REVISION}]}),
        "threads": [{
            "name": "main",
            "frames": [
                {"symbol": "android::IPCThreadState::transact", "library": "libbinder.so"},
                {"symbol": "android.os.BinderProxy.transactNative", "file": "Native method"},
                {"symbol": "android.os.BinderProxy.transact", "file": "BinderProxy.java",
                 "line": 584},
                {"symbol": "android.app.job.IJobCallback$Stub$Proxy.acknowledgeStopMessage",
                 "file": "IJobCallback.java", "line": 449},
                {"symbol": "android.app.job.JobServiceEngine$JobHandler.ackStopMessage",
                 "file": "JobServiceEngine.java", "line": 267},
            ],
        }],
    }
    for name, text in (("an event", json.dumps(event)),
                       ("a list of them", json.dumps([event])),
                       ("events under a key", json.dumps({"events": [event]}))):
        report = anr.parse(text)
        check(f"{name}: read as an event", report.source == "crashlytics-event", report.source)
        main = report.main
        check(f"{name}: frames in a dump's shape", main is not None and main.frames[:2] == [
            "android.os.BinderProxy.transactNative(Native method)",
            "android.os.BinderProxy.transact(BinderProxy.java:584)"], main)
        check(f"{name}: the native frame above them", main.native == [
            "android::IPCThreadState::transact"] and main.above == 1, main)
    check("the version from its two halves", report.head["Version"] == "1.2.3 (45)", report.head)
    check("the revision out of the stamp's object", report.event.revision == REVISION)
    text = anr.render(report)
    check("the job-service lead found on it, as on a dump (#588)",
          "acknowledgeStopMessage" in text and "JobScheduler job had stopped" in text, text)


def test_a_batch_of_events_reads_the_first_and_counts_the_rest():
    report = anr.parse(_event(MAIN_IN_THE_APP) + _event(WORKER))
    check("two events counted", report.entries == 2, report.entries)
    check("the first read", report.main is not None, report.threads)
    check("and said", "This file holds 2 events" in anr.render(report), anr.render(report))


def test_the_thread_text_alone_is_read_with_nothing_made_up():
    report = anr.parse(MAIN_IN_THE_APP)
    check("one thread", len(report.threads) == 1, report.threads)
    check("an event with nothing else in it",
          report.event == anr.Event() and report.head == {}, (report.event, report.head))
    text = anr.render(report)
    check("still said to be partial",
          "- The other threads, their states and their locks." in text, text)
    check("and no revision invented", "revision" not in text.split("##")[0], text)


def _checkout(root: Path) -> str:
    source = root / "app/src/main/java/com/example/app/data/Store.kt"
    source.parent.mkdir(parents=True)
    source.write_text("package com.example.app.data\n\nclass Store {\n"
                      "    fun read(): String {\n        return \"x\"\n    }\n}\n",
                      encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def test_the_revision_is_held_against_the_checkout(tmp_path):
    root = tmp_path / "app"
    head = _checkout(root)

    report = anr.parse(_event(MAIN_IN_THE_APP, revision=head))
    code = anr.locate(report, root)
    text = anr.render(report, code)
    check("the frame placed", code[0] and code[0][0].file.endswith("Store.kt"), code)
    check("the checkout said to be the build",
          f"This checkout is at the build's own revision, `{head[:12]}`" in text, text)
    check("and in json", anr.summary(report, code)["code"]["is_the_build"] is True)

    report = anr.parse(_event(MAIN_IN_THE_APP))
    code = anr.locate(report, root)
    text = anr.render(report, code)
    check("another revision is said, with the command that checks it out",
          f"this checkout is at `{head[:12]}`" in text
          and f"git worktree add --detach <dir> {REVISION[:12]}" in text, text)
    said = anr.summary(report, code)["code"]
    check("and in json", said["is_the_build"] is False and said["checkout"] == head, said)


def test_an_event_or_export_with_no_thread_is_refused_as_what_it_is(tmp_path):
    empty = tmp_path / "event.yaml"
    empty.write_text(_event("").replace("  threads: |\n\n", ""), encoding="utf-8")
    code, _, err = run("anr", str(empty))
    check("an event with no thread exits 2", code == 2, (code, err))
    check("and says it is an event with none", "Crashlytics event with no thread" in err, err)

    export = tmp_path / "export.txt"
    export.write_text("# Crashlytics - \n# Application: com.example.app\n"
                      "# Version: 1.2.3 (45)\n", encoding="utf-8")
    code, _, err = run("anr", str(export))
    check("an export with no thread exits 2", code == 2, (code, err))
    check("and is named as one", "Crashlytics export with no thread in it" in err, err)
