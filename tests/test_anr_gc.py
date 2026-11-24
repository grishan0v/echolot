"""`echolot anr` on a main thread stopped for a garbage collection (#591).

The runtime's frames on top of such a main thread name the collection; the
managed frame under them names where the thread ran into it. The report used
to print both and then say the freeze was inside something nobody here wrote,
"no line of yours to open" — while what made the collection necessary is how
much the app allocates and keeps, which is the app's own code. Every dump
here is synthetic, written in the shape each source prints.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import (  # noqa: E402
    DROPBOX, HEAD_LINES, IDLE_MAIN, MAIN_IN_JOBSCHEDULER_ACK, run,
)

LIBC = "/apex/com.android.runtime/lib64/bionic/libc.so"
ART = "/apex/com.android.art/lib64/libart.so"

# The shape a crash console gives: the runtime's native frames on top, the
# managed ones under them, and nothing of the app anywhere.
MAIN_WAITING_FOR_GC = HEAD_LINES + f"""\
main (native):tid=1 systid=4242
#00 pc 0x00000000000a1234 {LIBC} (syscall+28)
#01 pc 0x0000000000204394 {ART} (art::ConditionVariable::WaitHoldingLocks(art::Thread*)+140)
#02 pc 0x0000000000305678 {ART} (art::gc::Heap::WaitForGcToCompleteLocked(art::gc::GcCause, art::Thread*)+312)
#03 pc 0x0000000000306789 {ART} (art::gc::Heap::AllocateInternalWithGc(art::Thread*, art::gc::AllocatorType, bool, unsigned long, unsigned long*, unsigned long*, unsigned long*, art::ObjPtr<art::mirror::Class>*)+412)
       at android.view.Choreographer$FrameData.allocateFrameTimelines(Choreographer.java:1474)
       at android.view.Choreographer$FrameData.update(Choreographer.java:1487)
       at android.view.Choreographer.doFrame(Choreographer.java:1041)
       at android.view.Choreographer$FrameDisplayEventReceiver.run(Choreographer.java:1707)
       at android.os.Handler.handleCallback(Handler.java:959)
       at android.os.Handler.dispatchMessage(Handler.java:100)
       at android.os.Looper.loopOnce(Looper.java:257)
       at android.os.Looper.loop(Looper.java:342)
       at android.app.ActivityThread.main(ActivityThread.java:9634)
"""

# The device's own record of the same freeze: ART's state names the wait, the
# main thread wanted a collection of its own and waited for the running one,
# the collector's daemon is running it, a worker of the app was allocating
# into the same full heap, and the runtime's heap lines follow the threads.
RECORD = f"""\
----- pid 4300 at 2026-09-01 10:00:00.000000000+0200 -----
Cmd line: com.example.app

DALVIK THREADS (3):
"main" prio=5 tid=1 WaitingForGcToComplete
  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x71d88e28 self=0x7a375f7800
  | sysTid=4300 nice=-10 cgrp=top-app sched=0/0 handle=0x7a38cbe500
  native: #00 pc 000000000004df5c  {LIBC} (syscall+28)
  native: #01 pc 000000000047cc80  {ART} (art::ConditionVariable::WaitHoldingLocks(art::Thread*)+140)
  native: #02 pc 00000000002c8bb4  {ART} (art::gc::Heap::WaitForGcToCompleteLocked(art::gc::GcCause, art::Thread*)+324)
  native: #03 pc 00000000002c9a10  {ART} (art::gc::Heap::CollectGarbageInternal(art::gc::collector::GcType, art::gc::GcCause, bool, unsigned int)+512)
  native: #04 pc 00000000002ca120  {ART} (art::gc::Heap::AllocateInternalWithGc(art::Thread*, art::gc::AllocatorType, bool, unsigned long, unsigned long*, unsigned long*, unsigned long*, art::ObjPtr<art::mirror::Class>*)+380)
  native: #05 pc 0000000000610d4c  {ART} (artAllocArrayFromCodeResolvedRegionTLAB+412)
  at android.view.Choreographer$FrameData.allocateFrameTimelines(Choreographer.java:1474)
  at android.view.Choreographer.doFrame(Choreographer.java:1041)
  at android.os.Handler.handleCallback(Handler.java:959)
  at android.os.Looper.loop(Looper.java:342)
  at android.app.ActivityThread.main(ActivityThread.java:9634)

"HeapTaskDaemon" daemon prio=5 tid=7 WaitingPerformingGc
  | sysTid=4310 nice=4 cgrp=background
  native: #00 pc 00000000002f1a20  {ART} (art::gc::collector::ConcurrentCopying::MarkingPhase()+1200)
  native: #01 pc 00000000002f0b10  {ART} (art::gc::collector::ConcurrentCopying::RunPhases()+1100)
  native: #02 pc 00000000002e0c30  {ART} (art::gc::collector::GarbageCollector::Run(art::gc::GcCause, bool)+300)
  native: #03 pc 00000000002c9a10  {ART} (art::gc::Heap::CollectGarbageInternal(art::gc::collector::GcType, art::gc::GcCause, bool, unsigned int)+600)
  native: #04 pc 00000000002cb200  {ART} (art::gc::Heap::ConcurrentGC(art::Thread*, art::gc::GcCause, bool, unsigned int)+120)
  native: #05 pc 00000000002d3300  {ART} (art::gc::TaskProcessor::RunAllTasks(art::Thread*)+64)
  at dalvik.system.VMRuntime.runHeapTasks(Native method)
  at java.lang.Daemons$HeapTaskDaemon.runInternal(Daemons.java:609)
  at java.lang.Daemons$Daemon.run(Daemons.java:140)
  at java.lang.Thread.run(Thread.java:1012)

"Worker-3" prio=5 tid=14 WaitingForGcToComplete
  | sysTid=4321 nice=0 cgrp=top-app
  native: #00 pc 000000000004df5c  {LIBC} (syscall+28)
  native: #01 pc 000000000047cc80  {ART} (art::ConditionVariable::WaitHoldingLocks(art::Thread*)+140)
  native: #02 pc 00000000002c8bb4  {ART} (art::gc::Heap::WaitForGcToCompleteLocked(art::gc::GcCause, art::Thread*)+324)
  native: #03 pc 00000000002ca120  {ART} (art::gc::Heap::AllocateInternalWithGc(art::Thread*, art::gc::AllocatorType, bool, unsigned long, unsigned long*, unsigned long*, unsigned long*, art::ObjPtr<art::mirror::Class>*)+380)
  at com.example.app.cache.Thumbnails.decode(Thumbnails.kt:57)
  at com.example.app.cache.Thumbnails.load(Thumbnails.kt:31)
  at java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1145)
  at java.lang.Thread.run(Thread.java:1012)

Heap: 2% free, 251MB/256MB; 3456789 objects
Total time waiting for GC to complete: 4.512s
Total blocking GC count: 17
Total blocking GC time: 3.104s
Free memory until OOME 4MB
Max memory 256MB

----- end 4300 -----
"""

# A collection the app asked for by name: the call is the line to open.
MAIN_IN_SYSTEM_GC = HEAD_LINES + f"""\
main (native):tid=1 systid=4242
#00 pc 0x00000000000a1234 {LIBC} (syscall+28)
#01 pc 0x0000000000204394 {ART} (art::ConditionVariable::WaitHoldingLocks(art::Thread*)+140)
#02 pc 0x0000000000305678 {ART} (art::gc::Heap::WaitForGcToCompleteLocked(art::gc::GcCause, art::Thread*)+312)
#03 pc 0x0000000000306000 {ART} (art::gc::Heap::CollectGarbageInternal(art::gc::collector::GcType, art::gc::GcCause, bool, unsigned int)+512)
       at java.lang.Runtime.nativeGc(Native method)
       at java.lang.Runtime.gc(Runtime.java:704)
       at java.lang.System.gc(System.java:1012)
       at com.example.app.ui.Gallery.onTrimMemory(Gallery.kt:88)
       at android.os.Looper.loop(Looper.java:342)
       at android.app.ActivityThread.main(ActivityThread.java:9634)
"""

# The main thread running the collection itself, for its own allocation.
MAIN_RUNNING_GC = HEAD_LINES + f"""\
main (native):tid=1 systid=4242
#00 pc 0x00000000002f1a20 {ART} (art::gc::collector::ConcurrentCopying::CopyingPhase()+2048)
#01 pc 0x00000000002f0b10 {ART} (art::gc::collector::ConcurrentCopying::RunPhases()+1100)
#02 pc 0x00000000002e0c30 {ART} (art::gc::collector::GarbageCollector::Run(art::gc::GcCause, bool)+300)
#03 pc 0x00000000002c9a10 {ART} (art::gc::Heap::CollectGarbageInternal(art::gc::collector::GcType, art::gc::GcCause, bool, unsigned int)+600)
#04 pc 0x00000000002ca120 {ART} (art::gc::Heap::AllocateInternalWithGc(art::Thread*, art::gc::AllocatorType, bool, unsigned long, unsigned long*, unsigned long*, unsigned long*, art::ObjPtr<art::mirror::Class>*)+380)
       at android.view.Choreographer$FrameData.allocateFrameTimelines(Choreographer.java:1474)
       at android.view.Choreographer.doFrame(Choreographer.java:1041)
       at android.os.Looper.loop(Looper.java:342)
       at android.app.ActivityThread.main(ActivityThread.java:9634)
"""

# Stopped on the runtime's condition variable for something else: a suspend
# check. The same futex and the same `WaitHoldingLocks`, and no collection.
MAIN_IN_A_SUSPEND_CHECK = HEAD_LINES + f"""\
main (native):tid=1 systid=4242
#00 pc 0x00000000000a1234 {LIBC} (syscall+28)
#01 pc 0x0000000000204394 {ART} (art::ConditionVariable::WaitHoldingLocks(art::Thread*)+140)
#02 pc 0x0000000000207777 {ART} (art::Thread::FullSuspendCheck(bool)+176)
       at android.view.Choreographer.doFrame(Choreographer.java:1041)
       at android.os.Looper.loop(Looper.java:342)
       at android.app.ActivityThread.main(ActivityThread.java:9634)
"""

# Asleep in the app's own code: a wait, and not the collector's.
MAIN_ASLEEP_IN_ITS_CODE = HEAD_LINES + """\
main (sleeping):tid=1 systid=4242
       at java.lang.Thread.sleep(Native method)
       at java.lang.Thread.sleep(Thread.java:450)
       at com.example.app.Splash.onCreate(Splash.kt:12)
       at android.os.Looper.loop(Looper.java:342)
       at android.app.ActivityThread.main(ActivityThread.java:9634)
"""


def _render(text: str) -> tuple[str, dict]:
    rep = anr.parse(text)
    return anr.render(rep), anr.summary(rep)


def test_the_main_thread_waiting_for_a_collection_is_said_in_words() -> None:
    out, data = _render(MAIN_WAITING_FOR_GC)
    main = out.split("## The main thread", 1)[1].split("##", 1)[0]
    check("the main thread names the wait", "**waiting for a garbage collection**" in main
          and "WaitForGcToCompleteLocked" in main, main)
    check("and the allocation it ran into it in", "while allocating" in main, main)
    check("and says the frame below is not why",
          "not why one was needed" in main and "allocates and keeps" in main, main)
    check("the frames are still printed",
          "Choreographer$FrameData.allocateFrameTimelines" in main, main)


def test_no_line_of_yours_is_not_said_of_a_collection() -> None:
    out, _ = _render(MAIN_WAITING_FOR_GC)
    check("the old verdict is gone", "no line of yours to open" not in out, out)
    verdict = out.split("## Every frame here belongs to the platform or a library", 1)
    check("the heading stays: it is still true of the frames", len(verdict) == 2, out)
    body = verdict[1].split("##", 1)[0]
    check("the verdict sends the reader to the app's allocations",
          "what the app allocates" in body and "`gc_pressure`" in body
          and "heap dump" in body, body)


def test_the_gaps_say_where_the_allocations_are_read() -> None:
    out, _ = _render(MAIN_WAITING_FOR_GC)
    gaps = out.split("## What this report does not say", 1)[1]
    check("whose allocations, with the trace and the heap dump",
          "- Whose allocations made the collection necessary." in gaps
          and "`gc_pressure`" in gaps and "`atrace:dalvik`" in gaps
          and "heap dump" in gaps, gaps)
    check("a file of the main thread alone says the others are in the event",
          "main thread alone" in gaps, gaps)
    check("and no heap line was in it",
          "- How full the heap was." in gaps and "dumpsys dropbox" in gaps, gaps)


def test_json_routes_on_the_wait() -> None:
    _, data = _render(MAIN_WAITING_FOR_GC)
    main = data["main"]
    check("not idle", main["idle"] is None, main)
    check("waiting for a collection", main["waiting_for"] == "gc", main)
    check("an allocation's, waited for rather than run",
          main["gc"] == {"frame": "art::gc::Heap::WaitForGcToCompleteLocked",
                         "running": False, "cause": "allocation"}, main["gc"])
    check("no heap lines in a console export", data["heap"] == {}, data["heap"])


def test_the_device_s_record_names_the_others_and_quotes_the_heap() -> None:
    out, data = _render(RECORD)
    check("read as the device's record", data["source"] == "dumpsys", data["source"])
    check("its state is ART's own word for it",
          data["main"]["state"] == "waiting for gc to complete", data["main"]["state"])
    check("waited for the running one before its own",
          data["main"]["gc"]["running"] is False
          and data["main"]["gc"]["cause"] == "allocation", data["main"]["gc"])
    main = out.split("## The main thread", 1)[1].split("##", 1)[0]
    check("the worker allocating into the same heap is named",
          "1 other thread(s) here stopped for a collection too — `Worker-3`" in main, main)
    check("the collector's own daemon is not one of them",
          "HeapTaskDaemon" not in main, main)
    check("the runtime's heap line is quoted",
          "`Heap: 2% free, 251MB/256MB; 3456789 objects`" in main
          and "`Max memory: 256MB`" in main
          and "`Total blocking GC count: 17`" in main, main)
    working = out.split("## Threads that were doing something", 1)[1].split("##", 1)[0]
    check("the worker is listed by the app's frame, marked",
          "Thumbnails.decode" in working
          and "stopped for a garbage collection" in working, working)
    check("json: the worker waits too",
          [(t["name"], t["waiting_for"]) for t in data["working"]] == [("Worker-3", "gc")],
          data["working"])
    check("json: the heap lines by name, as written",
          data["heap"] == {"Heap": "2% free, 251MB/256MB; 3456789 objects",
                           "Total time waiting for GC to complete": "4.512s",
                           "Total blocking GC count": "17",
                           "Total blocking GC time": "3.104s",
                           "Free memory until OOME": "4MB",
                           "Max memory": "256MB"}, data["heap"])
    check("read by name, so not among the lines passed over",
          data["unread"]["after_the_threads"] == 0, data["unread"])
    gaps = out.split("## What this report does not say", 1)[1]
    check("no gap for a heap the record shows", "How full the heap was" not in gaps, gaps)
    check("the allocations still are one", "Whose allocations" in gaps, gaps)


def test_an_older_art_s_heap_line_before_the_threads_is_read_too() -> None:
    older = RECORD.replace("Cmd line: com.example.app\n",
                           "Cmd line: com.example.app\nHeap: 9% free, 40MB/44MB\n")
    older = older.replace("Heap: 2% free, 251MB/256MB; 3456789 objects\n", "")
    check("the first heap line of the block",
          anr.parse(older).heap.get("Heap") == "9% free, 40MB/44MB",
          anr.parse(older).heap)


def test_another_process_s_heap_is_not_this_app_s() -> None:
    other = RECORD + RECORD.replace("pid 4300", "pid 5200").replace(
        "com.example.app\n", "com.other.app\n", 1).replace("2% free", "70% free")
    check("the app's heap line, not the neighbour's",
          anr.parse(other).heap["Heap"].startswith("2% free"), anr.parse(other).heap)


def test_a_collection_the_app_asked_for_by_name_is_that_call() -> None:
    out, data = _render(MAIN_IN_SYSTEM_GC)
    check("explicit", data["main"]["gc"]["cause"] == "explicit", data["main"]["gc"])
    check("still a wait for a collection", data["main"]["waiting_for"] == "gc", data["main"])
    main = out.split("## The main thread", 1)[1].split("##", 1)[0]
    check("the call is the line to open", "a call asked for" in main
          and "That call is the line to open" in main, main)
    check("not the heap's allocations", "Whose allocations" not in out
          and "allocates and keeps" not in out, out)


def test_a_main_thread_running_the_collection_itself() -> None:
    out, data = _render(MAIN_RUNNING_GC)
    check("ran it", data["main"]["gc"] == {"frame": "art::gc::Heap::CollectGarbageInternal",
                                           "running": True, "cause": "allocation"},
          data["main"]["gc"])
    check("said so", "**running a garbage collection** itself" in out, out)
    check("and the verdict is the heap's", "no line of yours to open" not in out, out)


def test_the_state_alone_says_it() -> None:
    thread = anr.Thread(name="main", state="waiting for gc to complete",
                        frames=["android.view.Choreographer.doFrame(Choreographer.java:1041)"])
    got = anr.gc_wait(thread)
    check("a wait with no frame to name", got == anr.GcWait("", False, None), got)
    check("and a running one", anr.gc_wait(anr.Thread(
        name="main", state="waiting performing gc")).running is True)


def test_what_is_not_a_collection_reads_as_before() -> None:
    for name, text in (("a suspend check", MAIN_IN_A_SUSPEND_CHECK),
                       ("asleep in its own code", MAIN_ASLEEP_IN_ITS_CODE),
                       ("idle in its looper", IDLE_MAIN),
                       ("denied a monitor", DROPBOX),
                       ("in a platform binder call", MAIN_IN_JOBSCHEDULER_ACK)):
        out, data = _render(text)
        check(f"{name}: no wait", data["main"]["waiting_for"] is None
              and data["main"]["gc"] is None, data["main"])
        check(f"{name}: no word of it", "garbage collection" not in out
              and "Whose allocations" not in out, out)
    out, _ = _render(MAIN_IN_JOBSCHEDULER_ACK)
    check("a platform-only stack keeps its verdict", "no line of yours to open" in out, out)
    _, data = _render(IDLE_MAIN)
    check("the idle main thread is still idle",
          data["main"]["idle"] == "looper idle", data["main"])


def test_the_command_prints_it(tmp_path: Path) -> None:
    path = tmp_path / "anr.txt"
    path.write_text(RECORD, encoding="utf-8")
    code, out, _ = run("anr", str(path), "--json")
    data = json.loads(out)
    check("json from the command", code == 0 and data["main"]["waiting_for"] == "gc"
          and data["heap"]["Max memory"] == "256MB", out[:600])
    code, out, _ = run("anr", str(path))
    check("and the markdown", code == 0 and "**waiting for a garbage collection**" in out,
          out[:600])
