#!/usr/bin/env python3
"""The ANR reader, against a dump whose contents are known exactly.

The same reasoning as the fixture trace: a reader cannot be verified by staring
at it, and the reports it was written against are a live application's — they
carry a package, versions and internal class names, and none of that belongs in
this repository.

So the dump below is synthetic, and every shape in it was seen on a real
export: a lock note on the signature and a signature without one, java frames
under native ones, two threads sharing a name, an obfuscated class in the
monitor next to the unminified one in the frames, and a line the reader is
meant to admit it could not read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from tests.support import check  # noqa: E402

PACKAGE = "com.example.app"

# One lock, two threads denied it, and the holder parked inside a blocking
# call. That is the shape both chains in the sample reports had.
DUMP = """\
# Crashlytics -
# Application: com.example.app
# Platform: android
# Version: 1.2.3 (45)
# Issue: 0123456789abcdef0123456789abcdef
# Session: SESSION_DNE_0_v2
# Date: Thu Aug 20 2026 19:12:08 GMT+0200 (Central European Summer Time)

main (blocked):tid=1 systid=1001 | waiting to lock <0x0a714d13> (com.example.app.data.q) held by thread 12
       at com.example.app.data.StateStore.read(StateStore.kt:31)
       at com.example.app.ui.HomeViewModel.load(HomeViewModel.kt:88)
       at android.os.Looper.loop(Looper.java:392)

Worker-1 (blocked):tid=7 systid=1007 | waiting to lock <0x0a714d13> (com.example.app.data.q) held by thread 12
       at com.example.app.data.StateStore.write(StateStore.kt:47)
       at java.lang.Thread.run(Thread.java:1572)

Worker-2 (waiting):tid=12 systid=1012
#00 pc 0xd9f5c libc.so (syscall + 28) (BuildId: aa)
#01 pc 0x20008c libart.so (art::ConditionVariable::WaitHoldingLocks + 140) (BuildId: bb)
       at jdk.internal.misc.Unsafe.park(Native method)
       at com.google.android.gms.tasks.Tasks.await(Tasks.java:8)
       at com.example.app.data.StateStore.flush(StateStore.kt:52)
       at java.lang.Thread.run(Thread.java:1572)
       | a line no rule here reads

pool-1-thread-1 (waiting):tid=20 systid=1020
       at jdk.internal.misc.Unsafe.park(Native method)
       at java.util.concurrent.LinkedBlockingQueue.take(LinkedBlockingQueue.java:435)
       at java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1026)

DefaultDispatcher-worker-1 (timed waiting):tid=21 systid=1021
       at jdk.internal.misc.Unsafe.park(Native method)
       at kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.tryPark(CoroutineScheduler.kt:781)

FinalizerDaemon (waiting):tid=22 systid=1022
       at java.lang.Object.wait(Native method)
       at java.lang.Daemons$FinalizerDaemon.runInternal(Daemons.java:350)

binder:1000_1 (native):tid=23 systid=1023
#00 pc 0x119748 libc.so (__ioctl + 8) (BuildId: cc)
#01 pc 0x946c8 libbinder.so (android::IPCThreadState::joinThreadPool + 152) (BuildId: dd)

GoogleApiHandler (native):tid=24 systid=1024
       at android.os.MessageQueue.nativePollOnce(Native method)
       at android.os.Looper.loop(Looper.java:392)

GoogleApiHandler (native):tid=25 systid=1025
       at android.os.MessageQueue.nativePollOnce(Native method)
       at android.os.Looper.loop(Looper.java:392)
"""

# The main thread doing nothing at the moment the dump was taken. Two of ten
# real reports looked like this, and reading it as "the top frame is to blame"
# sends an investigation into Android's message queue.
IDLE_MAIN = """\
# Application: com.example.app

main (native):tid=1 systid=1001
       at android.os.MessageQueue.nativePollOnce(Native method)
       at android.os.Looper.loop(Looper.java:392)
       at android.app.ActivityThread.main(ActivityThread.java:10346)

Worker-1 (runnable):tid=9 systid=1009
       at com.example.app.work.Sync.run(Sync.kt:19)
"""

# Two threads each holding what the other wants.
DEADLOCK = """\
# Application: com.example.app

A (blocked):tid=1 systid=1001 | waiting to lock <0x1> (com.example.app.X) held by thread 2
       at com.example.app.X.one(X.kt:1)

B (blocked):tid=2 systid=1002 | waiting to lock <0x2> (com.example.app.Y) held by thread 1
       at com.example.app.Y.two(Y.kt:2)
"""


def report() -> anr.Report:
    return anr.parse(DUMP)


# --- the format -------------------------------------------------------------

def test_header_gives_the_application_and_the_version():
    head = report().head
    check("the package is read", head.get("Application") == PACKAGE, head)
    check("the version is read", head.get("Version") == "1.2.3 (45)", head)
    check("`# Crashlytics -` is decoration, not a field",
          "Crashlytics -" not in head, sorted(head))


def test_every_thread_in_the_dump_is_read():
    threads = report().threads
    check("nine threads", len(threads) == 9, [t.name for t in threads])
    check("no thread came out without frames",
          all(t.frames or t.native for t in threads),
          [t.name for t in threads if not (t.frames or t.native)])


def test_a_thread_is_named_by_its_name_and_its_tid():
    """Two threads shared a name in three of the ten sample reports."""
    threads = [t for t in report().threads if t.name == "GoogleApiHandler"]
    check("both survive the parse", len(threads) == 2, threads)
    check("they differ by tid", {t.tid for t in threads} == {"24", "25"},
          [t.tid for t in threads])


def test_native_frames_sit_beside_the_java_ones_on_one_thread():
    holder = report().by_tid()["12"]
    check("native frames read", len(holder.native) == 2, holder.native)
    check("java frames read", len(holder.frames) == 4, holder.frames)
    check("java is what the thread is said to stand on",
          holder.top.startswith("jdk.internal.misc.Unsafe.park"), holder.top)


def test_a_signature_survives_the_trailing_space_the_exports_carry():
    """Every real signature line ends in one, and no editor should decide that."""
    spaced = DUMP.replace("main (blocked):tid=1 systid=1001 |",
                          "main (blocked):tid=1 systid=1001  |")
    check("still nine threads", len(anr.parse(spaced).threads) == 9)


def test_a_line_nothing_reads_is_kept_and_confessed():
    rep = report()
    stray = [line for t in rep.threads for line in t.unread]
    check("the stray line is kept", len(stray) == 1, stray)
    check("and it reaches the report",
          "neither frames nor a lock note" in anr.render(rep), anr.render(rep))


# --- striking out what was doing nothing ------------------------------------

def test_each_idle_family_is_struck_out():
    by_tid = report().by_tid()
    expected = {
        "20": "pool waiting for work",
        "21": "coroutine worker parked",
        "22": "runtime daemon",
        "23": "binder pool waiting",
        "24": "looper idle",
    }
    for tid, reason in expected.items():
        got = anr.idle_reason(by_tid[tid])
        check(f"tid {tid} reads as `{reason}`", got == reason,
              f"{by_tid[tid].name}: {got}")


def test_a_blocked_thread_is_never_idle_whatever_its_stack_says():
    """It was denied a monitor. That is the thing this module exists to find."""
    for tid in ("1", "7"):
        thread = report().by_tid()[tid]
        check(f"{thread.name} counts as working",
              anr.idle_reason(thread) is None, anr.idle_reason(thread))


def test_a_parked_thread_is_not_idle_by_itself():
    """The holder of the lock in both sample chains was parked.

    A mask on `Unsafe.park` would have struck out the answer.
    """
    holder = report().by_tid()["12"]
    check("the lock holder survives", anr.idle_reason(holder) is None,
          anr.idle_reason(holder))


def test_only_the_threads_that_were_doing_something_are_left():
    left = {t.name for t in anr.working(report())}
    check("three of nine", left == {"main", "Worker-1", "Worker-2"}, left)


# --- the lock chain ---------------------------------------------------------

def test_one_monitor_with_two_waiters_is_one_finding():
    found = anr.chains(report())
    check("a single chain", len(found) == 1, found)
    check("both waiters under it", len(found[0].waiters) == 2,
          [t.name for t in found[0].waiters])
    check("the holder is resolved by tid",
          found[0].owner is not None and found[0].owner.tid == "12", found[0].owner)


def test_the_chain_says_whether_the_main_thread_is_in_it():
    check("main is behind this lock", anr.chains(report())[0].blocks_main)


def test_the_obfuscated_monitor_is_named_from_a_waiters_own_frame():
    """Crashlytics unminifies frames and leaves the monitor as R8 wrote it."""
    chain = anr.chains(report())[0]
    check("the raw name is kept", chain.monitor == "com.example.app.data.q",
          chain.monitor)
    check("and resolved to the class",
          chain.named == "com.example.app.data.StateStore", chain.named)


def test_a_monitor_from_another_package_is_not_renamed():
    """The inference holds only while the waiter stands in the same package."""
    other = DUMP.replace("(com.example.app.data.q)", "(com.other.lib.q)")
    check("no name is invented", anr.chains(anr.parse(other))[0].named is None)


def test_two_threads_holding_what_the_other_wants_is_called_a_deadlock():
    found = anr.chains(anr.parse(DEADLOCK))
    check("both chains found", len(found) == 2, found)
    check("and both say so", all(c.cycle for c in found),
          [(c.monitor, c.cycle) for c in found])
    check("the report says the word", "deadlock" in anr.render(anr.parse(DEADLOCK)))


# --- what the report says ---------------------------------------------------

def test_the_report_leads_with_the_lock_and_the_holders_own_frames():
    text = anr.render(report())
    check("the lock section is there", "## What was holding the lock" in text, text)
    check("the holder's app frames are printed",
          "com.example.app.data.StateStore.flush(StateStore.kt:52)" in text, text)
    check("the blocking call it was standing on is printed",
          "Tasks.await" in text, text)


def test_a_long_stack_keeps_the_method_that_took_the_lock():
    """On a real chain the holder ran through eight of its own interceptors.

    Printing the top of that and stopping leaves out the coroutine that took
    the lock, which is the line the investigation opens.
    """
    deep = "\n".join(
        [f"       at com.example.app.net.Interceptor{i}.intercept(I{i}.kt:1)"
         for i in range(10)]
        + ["       at com.example.app.startup.Boot.load(Boot.kt:126)"]
    )
    text = anr.render(anr.parse(
        DUMP.replace("       at com.example.app.data.StateStore.flush(StateStore.kt:52)",
                     deep + "\n       at com.example.app.data.StateStore.flush(StateStore.kt:52)")))
    check("the near end is printed", "Interceptor0.intercept" in text, text)
    check("the far end is printed too",
          "com.example.app.data.StateStore.flush(StateStore.kt:52)" in text, text)
    check("and the gap is marked", "…" in text, text)


def test_an_idle_main_thread_is_named_as_idle():
    text = anr.render(anr.parse(IDLE_MAIN))
    check("it says the main thread was idle", "It was **idle**" in text, text)
    check("and the working thread is still listed",
          "com.example.app.work.Sync.run(Sync.kt:19)" in text, text)


def test_the_report_names_the_questions_it_cannot_answer():
    text = anr.render(report())
    check("the missing ANR reason is named",
          "carries no reason, component or intent" in text, text)
    check("and that a dump has no durations",
          "durations come from a trace" in text, text)


def test_a_dump_in_a_signature_form_this_reader_does_not_know_reads_as_nothing(tmp_path):
    """Silence with a reason, rather than a report built from zero threads."""
    art = tmp_path / "dumpsys.txt"
    art.write_text('"main" prio=5 tid=1 Blocked\n  | group="main"\n', encoding="utf-8")
    check("it refuses", anr.main([str(art)]) == 1)
