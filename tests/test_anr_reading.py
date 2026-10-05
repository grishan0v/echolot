"""What `echolot anr` reads off a dump, in the shapes it used to misread.

A lock note ART could not finish, a working thread above an idle frame, a
monitor that kept its name, a class in two source sets, a drop box entry cut
at its size limit, and a deadlock: each case is a few lines of the dump it
came from, and the answer the report owes for them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import DEADLOCK, DROPBOX  # noqa: E402


def _main_blocked(note_tail: str) -> str:
    return f'''\
"main" prio=5 tid=1 Blocked
  at com.example.app.data.Store.read(Store.kt:31)
  - waiting to lock <0x0a714d13> (a com.example.app.data.Store){note_tail}
"Worker-2" prio=5 tid=12 Waiting
  at jdk.internal.misc.Unsafe.park(Native method)
  - locked <0x0a714d13> (a com.example.app.data.Store)
'''


def test_a_lock_note_without_its_owner_finds_the_holder_by_address() -> None:
    for tail in ("", " held by thread 12"):
        report = anr.parse(_main_blocked(tail))
        found = anr.chains(report)
        check(f"the holder is found with the tail {tail!r}",
              len(found) == 1 and found[0].owner is not None
              and found[0].owner.name == "Worker-2" and not found[0].inferred, found)
        text = anr.render(report)
        check("and named", "Held by **Worker-2**" in text, text)
        check("the source is not said to record no owner",
              "does not record who holds" not in text, text)


def test_work_above_an_idle_frame_is_work() -> None:
    dump = '''\
"main" prio=5 tid=1 Waiting
  at java.util.concurrent.LinkedBlockingQueue.take(LinkedBlockingQueue.java:435)
  at com.example.app.auth.TokenStore.awaitToken(TokenStore.kt:40)
  at com.example.app.MainActivity.onCreate(MainActivity.kt:21)
"Timer-0" prio=5 tid=13 Runnable
  at com.example.app.sync.SyncTask.run(SyncTask.kt:77)
  at java.util.TimerThread.mainLoop(Timer.java:562)
"FinalizerDaemon" daemon prio=5 tid=4 Waiting
  at com.example.app.io.Handle.finalize(Handle.kt:12)
  at java.lang.Daemons$FinalizerDaemon.runInternal(Daemons.java:292)
"Binder:4100_2" prio=5 tid=14 Waiting
  at com.example.app.data.Provider.query(Provider.kt:40)
  at android.os.Binder.execTransact(Binder.java:1339)
  native: #00 pc 0000000000048c5c  /system/lib64/libbinder.so (android::IPCThreadState::joinThreadPool(bool)+60)
"pool-1-thread-1" prio=5 tid=20 Waiting
  at java.util.concurrent.LinkedBlockingQueue.take(LinkedBlockingQueue.java:435)
  at java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1026)
'''
    report = anr.parse(dump)
    busy = {t.name for t in anr.working(report)}
    check("the main thread, the timer, the finalizer and the binder thread worked",
          busy == {"main", "Timer-0", "FinalizerDaemon", "Binder:4100_2"}, busy)
    check("and the pool waiting for work is idle",
          anr.idle_reason(next(t for t in report.threads
                               if t.name == "pool-1-thread-1")) == "pool waiting for work")
    check("the main thread is idle only at its looper",
          anr.summary(report)["main"]["idle"] is None, anr.summary(report)["main"])


def test_a_monitor_that_kept_its_name_is_not_renamed_after_the_waiter() -> None:
    dump = '''\
"main" prio=5 tid=1 Blocked
  at com.example.app.data.Repository.get(Repository.kt:31)
  - waiting to lock <0x0a714d13> (a com.example.app.data.Cache) held by thread 12
"Worker-2" prio=5 tid=12 Waiting
  at jdk.internal.misc.Unsafe.park(Native method)
  - locked <0x0a714d13> (a com.example.app.data.Cache)
'''
    chain = anr.chains(anr.parse(dump))[0]
    check("the Cache stays a Cache", chain.named == "com.example.app.data.Cache", chain)
    obfuscated = dump.replace("data.Cache)", "data.q)")
    chain = anr.chains(anr.parse(obfuscated))[0]
    check("while an R8 name is resolved from the waiter",
          chain.named == "com.example.app.data.Repository", chain)


def test_a_class_in_two_source_sets_is_not_placed_for_certain(tmp_path: Path) -> None:
    for build in ("debug", "release"):
        f = tmp_path / f"app/src/{build}/java/com/example/app/di/NetModule.kt"
        f.parent.mkdir(parents=True)
        f.write_text("package com.example.app.di\n\nobject NetModule {\n  fun client() = 1\n}\n",
                     encoding="utf-8")
    found = anr.place("com.example.app.di.NetModule.client(NetModule.kt:4)",
                      anr.source_index(tmp_path), tmp_path)
    check("a guess, with the other file named",
          found is not None and not found.exact
          and found.others == ("app/src/release/java/com/example/app/di/NetModule.kt",),
          found)


def test_a_truncated_entry_says_what_it_is_missing() -> None:
    cut = DROPBOX[:DROPBOX.index('"Worker-2" daemon prio=5') + 20] + "\n\n[[TRUNCATED]]\n"
    report = anr.parse(cut)
    check("the cut is read", report.truncated and report.cut == 3,
          (report.truncated, report.listed, len(report.threads)))
    text = anr.render(report)
    check("and said", "The drop box cut this entry" in text
          and "3 of the threads ART listed are not in it" in text, text)
    check("--json carries it", anr.summary(report)["cut"] == 3)
    check("a whole entry says nothing of the kind",
          anr.parse(DROPBOX).cut is None and "cut this entry" not in anr.render(anr.parse(DROPBOX)))


def test_a_deadlock_names_no_root() -> None:
    found = anr.chains(anr.parse(DEADLOCK))
    check("each chain's waiter is in its loop, and no thread is its root",
          all(c.cycle and c.loop and c.root is None for c in found), found)
    text = anr.render(anr.parse(DEADLOCK))
    check("the report does not call a waiter the one standing on its own",
          "Only the last is standing" not in text and "this is a deadlock" in text, text)

    queued = '''\
main (blocked):tid=1 systid=1001 | waiting to lock <0x3> (com.example.app.X) held by thread 2
       at com.example.app.X.one(X.kt:1)
A (blocked):tid=2 systid=1002 | waiting to lock <0x1> (com.example.app.Y) held by thread 3
       at com.example.app.Y.two(Y.kt:2)
B (blocked):tid=3 systid=1003 | waiting to lock <0x2> (com.example.app.Z) held by thread 2
       at com.example.app.Z.three(Z.kt:3)
'''
    report = anr.parse(queued)
    mains = [c for c in anr.chains(report) if c.blocks_main]
    check("main's chain: the holders block each other, main is not in the loop",
          len(mains) == 1 and not mains[0].cycle
          and [t.name for t in mains[0].loop] == ["A", "B"] and mains[0].root is None,
          mains)
    text = anr.render(report)
    check("and the report says so",
          "The holders block each other" in text, text)
