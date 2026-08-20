#!/usr/bin/env python3
"""The ANR reader, against a dump whose contents are known exactly.

The same reasoning as the fixture trace: a reader cannot be verified by staring
at it, and the reports it was written against are a live application's — they
carry a package, versions and internal class names, and none of that belongs in
this repository.

So the dumps below are synthetic, one per source. Every shape in the
Crashlytics one was seen on a real export: a lock note on the signature and a
signature without one, java frames under native ones, two threads sharing a
name, an obfuscated class in the monitor next to the unminified one in the
frames, and a line the reader is meant to admit it could not read.

The device's record was made on purpose: an app that takes a monitor on a
worker thread and then blocks the main thread on it, frozen on an Android 13
phone and read back with `dumpsys dropbox --print data_app_anr`. Everything in
the second dump below is a shape that record actually had — the drop box's own
preamble before the entry, the CPU table, a thread still starting up whose
state carries a parenthetical, one the runtime never attached, `DumpLatencyMs`,
`(no managed stack frames)`, the runtime's statistics after the last thread,
and the kernel wait channels after that.

Written from a guess, five of those would have gone unread and two threads
would have vanished into the block above them.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from tests.support import check  # noqa: E402


def run(*argv: str) -> tuple[int, str, str]:
    """A command, with what it printed on each stream."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(list(argv))
    return code, out.getvalue(), err.getvalue()

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


def test_a_form_no_source_here_announces_threads_in_is_refused(tmp_path):
    """Silence with a reason, rather than a report built from zero threads."""
    strange = tmp_path / "strange.txt"
    strange.write_text("Thread 1 <main> RUNNING\n  frame: Foo.bar\n",
                       encoding="utf-8")
    code, _, err = run("anr", str(strange))
    check("it refuses", code == 2, code)
    check("and says which forms it does know", "Crashlytics" in err, err)


# --- the record the device keeps itself -------------------------------------
#
# The header below was read off a live device. The thread body is ART's own
# format and is written from it rather than sampled: the files under
# `/data/anr/` are mode 600 owned by `system`, and a device without root parts
# with them only inside a bugreport.

DROPBOX = """\
Drop box contents: 50 entries
Max entries: 1000
Searching for: data_app_anr

========================================
2026-08-20 21:22:45 data_app_anr (compressed text, 46727 bytes)
Process: com.example.app
PID: 4100
UID: 10214
Frozen: false
Flags: 0x30a8be46
Package: com.example.app v45
Foreground: Yes
Activity: com.example.app/.MainActivity
ErrorId: 9bd4a668-f993-4911-b244-b698efb7357d
Subject: Input dispatching timed out (com.example.app/.MainActivity is not responding. Waited 10000ms for MotionEvent)
Build: generic/vbox86p:13/TP1A/1234:user/release-keys
Dropped-Count: 0

CPU usage from 8ms to -8423ms ago (2026-08-20 21:22:37 to 2026-08-20 21:22:45):
  57% 5662/system_server: 31% user + 25% kernel / faults: 22124 minor
  38% 5339/surfaceflinger: 26% user + 12% kernel / faults: 1366 minor
 12% TOTAL: 6% user + 5% kernel
timestamp_ms: 1787253758398
window_ms: 300000

----- pid 4100 at 2026-08-20 21:22:39.180419343+0200 -----
Cmd line: com.example.app

DALVIK THREADS (4):
"main" prio=5 tid=1 Blocked
  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x71d88e28 self=0x7a375f7800
  | sysTid=4100 nice=-10 cgrp=default sched=0/0 handle=0x7a38cbe500
  | state=S schedstat=( 498411186 25640425 513 ) utm=36 stm=13 core=5 HZ=100
  | held mutexes=
  at com.example.app.data.StateStore.read(StateStore.kt:31)
  - waiting to lock <0x0a714d13> (a com.example.app.data.q) held by thread 12
  at com.example.app.ui.HomeViewModel.load(HomeViewModel.kt:88)
  at android.os.Looper.loop(Looper.java:392)
  DumpLatencyMs: 0.5

"Worker-2" daemon prio=5 tid=12 Waiting
  | sysTid=4112 nice=0 cgrp=default
  native: #00 pc 00000000000d9f5c  /apex/com.android.runtime/lib64/bionic/libc.so (syscall+28)
  at jdk.internal.misc.Unsafe.park(Native method)
  - locked <0x0a714d13> (a com.example.app.data.q)
  - waiting on <0x0b8f2a01> (a java.lang.Object)
  at com.google.android.gms.tasks.Tasks.await(Tasks.java:8)
  at com.example.app.data.StateStore.flush(StateStore.kt:52)
  at java.lang.Thread.run(Thread.java:1012)
  DumpLatencyMs: 0.4

"pool-1-thread-1" prio=5 tid=20 TimedWaiting
  | sysTid=4120 nice=0 cgrp=default
  at java.util.concurrent.LinkedBlockingQueue.take(LinkedBlockingQueue.java:435)
  at java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1026)

"perfetto_hprof_listener" prio=10 tid=3 Native (still starting up)
  | sysTid=4103 nice=0 cgrp=default
  native: #00 pc 0000000000119104  /apex/com.android.runtime/lib64/bionic/libc.so (read+4)
  (no managed stack frames)

"Binder:4100_1" sysTid=4123
  native: #00 pc 0000000000119748  /apex/com.android.runtime/lib64/bionic/libc.so (__ioctl+8)
  native: #01 pc 00000000000946c8  /system/lib64/libbinder.so (android::IPCThreadState::joinThreadPool+152)

"mali-cmar-backe" prio=7 (not attached)
  | sysTid=4125 nice=0 cgrp=default
  native: #00 pc 00000000000dc778  /apex/com.android.runtime/lib64/bionic/libc.so (__ppoll+8)
  (no managed stack frames)

Zygote loaded classes=18934 post zygote classes=68
Dumping registered class loaders
#0 dalvik.system.PathClassLoader: [], parent #1
Done dumping class loaders
Intern table: 35638 strong; 1170 weak
JNI: CheckJNI is on; globals=372 (plus 82 weak)
Heap: 63% free, 3019KB/8192KB

----- end 4100 -----

----- Waiting Channels: pid 4100 at 2026-08-20 21:22:39 -----
Cmd line: com.example.app

sysTid=4100      futex_wait_queue_me
sysTid=4112      poll_schedule_timeout

----- end 4100 -----

----- pid 5200 at 2026-08-20 21:22:39.180419343+0200 -----
Cmd line: com.other.app

DALVIK THREADS (1):
"main" prio=5 tid=1 Runnable
  | sysTid=5200 nice=0 cgrp=default
  at com.other.app.Thing.spin(Thing.kt:9)

----- end 5200 -----
"""


def test_the_source_is_decided_by_how_threads_are_announced():
    check("the device's record reads as itself",
          anr.detect(DROPBOX) is anr.DUMPSYS, anr.detect(DROPBOX))
    check("and the export as itself",
          anr.detect(DUMP) is anr.CRASHLYTICS, anr.detect(DUMP))


def test_the_record_carries_the_reason_the_export_does_not():
    rep = anr.parse(DROPBOX)
    check("the subject is read",
          rep.reason.startswith("Input dispatching timed out")
          and "Waited 10000ms for MotionEvent" in rep.reason, rep.reason)
    check("and it leads the report",
          "Fired for: **Input dispatching timed out" in anr.render(rep))
    check("so the gap is not claimed",
          "carries no reason" not in anr.render(rep))


def test_the_runtimes_state_names_land_in_the_same_vocabulary():
    by_tid = anr.parse(DROPBOX).by_tid()
    check("Blocked", by_tid["1"].state == "blocked", by_tid["1"].state)
    check("Waiting", by_tid["12"].state == "waiting", by_tid["12"].state)
    check("TimedWaiting is two words here",
          by_tid["20"].state == "timed waiting", by_tid["20"].state)


def test_only_the_process_that_hung_is_read():
    rep = anr.parse(DROPBOX)
    check("six threads from the app", len(rep.threads) == 6,
          [(t.name, t.process) for t in rep.threads])
    check("the other process is counted, not merged", rep.elsewhere == 1,
          rep.elsewhere)
    check("and the report says so",
          "from other processes" in anr.render(rep), anr.render(rep))


def test_the_lock_note_is_read_from_its_own_line():
    chain = anr.chains(anr.parse(DROPBOX))[0]
    check("the monitor is read past the article ART puts in it",
          chain.monitor == "com.example.app.data.q", chain.monitor)
    check("and named from the waiter's frame",
          chain.named == "com.example.app.data.StateStore", chain.named)
    check("the holder is the thread that says it locked it",
          chain.owner is not None and chain.owner.name == "Worker-2", chain.owner)
    check("main is behind it", chain.blocks_main)


def test_the_holder_is_found_by_what_it_locked_when_the_tid_is_absent():
    """ART prints what a thread holds; Crashlytics prints nothing of the kind.

    It is the second way to the same answer, and it survives a holder whose
    tid the dump names but does not carry.
    """
    orphan = DROPBOX.replace("held by thread 12", "held by thread 999")
    chain = anr.chains(anr.parse(orphan))[0]
    check("still resolved", chain.owner is not None and chain.owner.tid == "12",
          chain.owner)


def test_the_runtimes_bookkeeping_lines_are_not_counted_as_unreadable():
    rep = anr.parse(DROPBOX)
    stray = [line for t in rep.threads for line in t.unread]
    check("nothing inside a thread went unread", not stray, stray)
    check("and nothing outside one either", not rep.unread, rep.unread)


def test_a_thread_the_runtime_never_attached_has_no_tid_to_be_held_by():
    rep = anr.parse(DROPBOX)
    binder = next(t for t in rep.threads if t.name.startswith("Binder:"))
    check("it has a system id", binder.systid == "4123", binder.systid)
    check("and no runtime tid", binder.tid == "", binder.tid)
    check("so it cannot collide in the tid index",
          "" not in rep.by_tid(), sorted(rep.by_tid()))
    check("it is still struck out as an idle binder thread",
          anr.idle_reason(binder) == "binder pool waiting",
          anr.idle_reason(binder))


def test_a_second_entry_in_the_file_is_a_second_anr_and_is_not_merged():
    two = DROPBOX + "\n" + "=" * 40 + "\n" + DROPBOX
    rep = anr.parse(two)
    check("only the first is read", len(rep.threads) == 6,
          [t.name for t in rep.threads])
    check("and the reader says there are more", rep.entries == 2, rep.entries)
    check("in the report too",
          "holds more than one ANR" in anr.render(rep), anr.render(rep))


def test_nothing_in_a_real_records_shape_goes_unread():
    """The record carries far more than threads, and all of it was seen once."""
    rep = anr.parse(DROPBOX)
    check("nothing outside a thread", not rep.unread, rep.unread)
    check("nothing inside one",
          not [line for t in rep.threads for line in t.unread],
          [line for t in rep.threads for line in t.unread])
    check("and the rest is counted, not silent", rep.skipped > 0, rep.skipped)


def test_the_runtimes_statistics_do_not_become_fields_of_the_report():
    """`Intern table: …` and `Heap: …` are `Key: value` and are not the app."""
    head = anr.parse(DROPBOX).head
    for key in ("Intern table", "Heap", "JNI", "Drop box contents", "Searching for"):
        check(f"`{key}` stays out of the header", key not in head, sorted(head))
    check("while the record's own fields are in",
          head.get("Process") == "com.example.app", sorted(head))


def test_a_thread_still_starting_up_is_not_swallowed_by_the_one_above_it():
    """Its state carries a parenthetical, and without room for it the thread
    is not merely mis-stated — it vanishes from the dump."""
    rep = anr.parse(DROPBOX)
    starting = next((t for t in rep.threads
                     if t.name == "perfetto_hprof_listener"), None)
    check("it is there", starting is not None, [t.name for t in rep.threads])
    check("with the state it was given", starting.state == "native",
          starting.state)


def test_a_thread_the_runtime_never_attached_says_so():
    rep = anr.parse(DROPBOX)
    mali = next(t for t in rep.threads if t.name == "mali-cmar-backe")
    check("named rather than left blank", mali.state == "not attached",
          mali.state)
    check("and struck out as waiting on a descriptor",
          anr.idle_reason(mali) == "waiting on a descriptor",
          anr.idle_reason(mali))


# --- the verb ---------------------------------------------------------------

def test_the_verb_reads_a_report_and_writes_nothing(tmp_path):
    """Reconnaissance, not an investigation. That is why it is its own verb."""
    report_file = tmp_path / "export.txt"
    report_file.write_text(DUMP, encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    code, out, _ = run("anr", str(report_file))
    check("it exits 0", code == 0, code)
    check("and prints the lock section", "## What was holding the lock" in out, out)
    check("nothing landed beside the report",
          sorted(p.name for p in tmp_path.iterdir()) == before,
          sorted(p.name for p in tmp_path.iterdir()))


def test_the_verb_gives_an_agent_the_same_findings_as_a_person(tmp_path):
    report_file = tmp_path / "export.txt"
    report_file.write_text(DUMP, encoding="utf-8")

    found = json.loads(run("anr", str(report_file), "--json")[1])
    check("the source is named", found["source"] == "crashlytics", found["source"])
    check("one chain", len(found["chains"]) == 1, found["chains"])
    chain = found["chains"][0]
    check("with main behind it", chain["blocks_main"], chain)
    check("the monitor named as in the markdown",
          chain["named"] == "com.example.app.data.StateStore", chain)
    check("and the holder's stack carried",
          any("StateStore.flush" in frame for frame in chain["owner"]["stack"]),
          chain["owner"])
    check("the thread counts agree with the reader",
          found["threads"]["total"] == len(report().threads), found["threads"])


def test_the_verb_refuses_a_file_that_is_not_there(tmp_path):
    code, _, err = run("anr", str(tmp_path / "nothing.txt"))
    check("exit 2", code == 2, code)
    check("and says so", "no such file" in err, err)
