#!/usr/bin/env python3
"""Generator for a synthetic Perfetto trace with known-in-advance answers.

It lives in echolot/ rather than tests/ on purpose: this is not test
scaffolding but a self-verification asset. `doctor` stands on it, proving that
the pipeline produces correct answers on THIS machine — and that check belongs
to the product, not only to whoever writes it.

The point: detector SQL cannot be verified by staring at it. What is needed is
a trace whose contents we know exactly. Then "the detector fired" turns from a
hope into a checkable fact, and the SQL edit cycle from ten minutes with a
device into one second.

The trace is real: Perfetto protobuf with ProcessTree (process names), ftrace
sched_switch (which yields thread_state) and atrace print (which yields
slices). The same format that arrives from a device.

One planted problem per detector plus negative controls — the things that must
NOT fire. The expected answers live in echolot/selftest.py.

    python -m echolot.fixture out.perfetto-trace
"""

from __future__ import annotations

import sys
from pathlib import Path

from perfetto.protos.perfetto.trace import perfetto_trace_pb2 as pb
from perfetto.trace_builder.proto_builder import TraceProtoBuilder

# Trace time is nanoseconds since boot. Start at 10 s so it is never confused
# with zero.
BASE_NS = 10_000_000_000


def ms(v: float) -> int:
    return BASE_NS + int(v * 1_000_000)


# --- the target process ----------------------------------------------------

APP_PID = 4100
APP_NAME = "com.example.app"

TID_MAIN = 4100  # tid == pid → main thread
TID_WORKER = 4201
TID_HEAP = 4202
TID_OKHTTP = 4203
TID_INSTR = 4204
TID_BLOCKED = 4205
TID_STUCK = 4206
TID_SEED = 4207
TID_LOCKED = 4208
TID_DISK = 4209
TID_DISK_BG = 4210

THREADS = {
    # The main thread's name cut from the front, the way a package name
    # longer than fifteen characters arrives: `com.example.myapp` comes as
    # `m.example.myapp`. `com.example.app` itself would arrive whole.
    TID_MAIN: "m.example.app",
    TID_WORKER: "DefaultDispatcher-worker-1",
    TID_HEAP: "HeapTaskDaemon",
    TID_OKHTTP: "OkHttp Dispatcher",
    TID_INSTR: "WellInstrumented",
    TID_BLOCKED: "BlockingIO-1",
    TID_STUCK: "StuckForever",
    TID_SEED: "SeedWorker",
    TID_LOCKED: "LockWaiter",
    TID_DISK: "DiskWaiter",
    TID_DISK_BG: "OtherBlocked",
}

# --- a foreign process: the process-isolation control ----------------------

OTHER_PID = 5000
OTHER_NAME = "com.other.app"
OTHER_THREADS = {OTHER_PID: "m.other.app"}

# --- surfaceflinger: the owner of display frames ---------------------------
# Present so that display frames sit where they really sit. They share a table
# with the app's surface frames, and the only thing keeping them out of the
# report is that they belong to another process.

SF_PID = 600
SF_NAME = "/system/bin/surfaceflinger"
SF_THREADS = {SF_PID: "surfaceflinger"}

# --- the frame timeline ----------------------------------------------------
# Shape: (token, start_ms, expected_ms, actual_ms, jank)
#
# Recorded by SurfaceFlinger since Android 12, not by the app: every frame
# carries the deadline it was given, what it actually took, and why it missed.
# These arrive as their own packets and land on their own track types
# (android_expected_frame_timeline / android_actual_frame_timeline), never on
# a thread track — so the slice-based detectors cannot see them and adding
# them here changes nothing about what those report. A check pins that.
#
# The window is [100, 1105]. Overrun is actual minus expected, and the frame
# rate is deliberately ignored: what matters is which frames the detector
# picks up, not that they form a plausible 60 Hz sequence.

FT = pb.FrameTimelineEvent
LAYER = "com.example.app/com.example.app.MainActivity#0"

APP_FRAMES = [
    # The finding: 5 frames the app itself was late for, 44 ms over each.
    *[(1 + i, 200 + i * 50, 16, 60, FT.JANK_APP_DEADLINE_MISSED) for i in range(5)],
    # Negative control: the same jank type, 2 ms over — below min_overrun_ms.
    # They must not swell the count of the row above, which is why the floor
    # is applied per frame and before the grouping.
    *[(10 + i, 450 + i * 20, 16, 18, FT.JANK_APP_DEADLINE_MISSED) for i in range(3)],
    # A second finding, and the one nobody should be sent into the code over:
    # the app finished on time and SurfaceFlinger did not. The platform tags
    # this 'Other Jank' by itself.
    *[(20 + i, 550 + i * 30, 16, 30, FT.JANK_SF_CPU_DEADLINE_MISSED) for i in range(4)],
    # Negative control: healthy frames. They fire nothing and still count in
    # the denominator — "5 of 24 frames" is the honest form of the finding.
    *[(30 + i, 700 + i * 5, 16, 14, FT.JANK_NONE) for i in range(10)],
    # Negative control: 34 ms over, but only twice — below min_frames. Two bad
    # frames are an anecdote.
    *[(50 + i, 800 + i * 20, 16, 50, FT.JANK_BUFFER_STUFFING) for i in range(2)],
    # Negative control: 184 ms over, outside the window. The worst frame in
    # the trace, and none of the detector's business.
    (60, 1200, 16, 200, FT.JANK_APP_DEADLINE_MISSED),
]

# Negative control: another app janking inside our window.
OTHER_LAYER = "com.other.app/com.other.app.Main#0"
OTHER_FRAMES = [
    (100 + i, 300 + i * 40, 16, 90, FT.JANK_APP_DEADLINE_MISSED) for i in range(4)
]

# Negative control: display frames. Same table, no surface token, and owned by
# surfaceflinger rather than by us.
SF_FRAMES = [
    (900 + i, 250 + i * 40, 16, 70, FT.JANK_SF_CPU_DEADLINE_MISSED) for i in range(4)
]

# --- slices ----------------------------------------------------------------
# Shape: (name, start_ms, dur_ms, [children])
# The scenario window is set by the anchors AppStart (start) and
# Screen.firstFrame (end), i.e. [100, 1105].

SLICES = {
    # A block that outlives the recording: opened 60 ms before the window
    # closes and never ended, which is what ART's contention slice looks like
    # when the lock is still held when tracing stops. Read as a slice of no
    # length — which is what a raw `dur = -1` becomes under MAX(dur, 0) — the
    # longest block in the trace disappears from every detector at once. On a
    # real freeze this was twenty seconds of it.
    #
    # On its own thread rather than on main: the point is how the pipeline
    # reads an open slice, and a window-long block on the main thread would
    # rewrite half the other findings to prove it.
    # --- work reached from two places, and three shapes that only look like it -
    #
    # The planted finding is `fill_presets`: the same named work entered once
    # from `stage_first` and once from `stage_again`, costing about the same
    # both times. That is what a migration ladder redoing a rung looks like in
    # a trace — and every other detector reads it as two ordinary slices,
    # because on the axis they all measure (how much) there is nothing wrong
    # with it.
    #
    # The three controls are the shapes that share one feature with it and
    # must stay silent, one per gate:
    #
    #   util_fn        four callers — a shared helper is not a repeat
    #   insert_row     one caller, ten times — a loop is repetition by design
    #   shared_helper  two callers, 5 ms against 80 ms — same name, different
    #                  work; identical work costs an identical amount, and
    #                  that sameness is the whole signal
    #
    # And one that must not stay silent: `AGENTTMP_insert_preset` is
    # `shared_helper`'s shape carrying the temporary prefix, which makes it
    # the hunt's own marker sitting one level too deep. That is a near miss
    # rather than a finding, and the report says so.
    TID_SEED: [
        ("call_a", 110, 16, [("util_fn", 111, 12, [])]),
        ("call_b", 130, 16, [("util_fn", 131, 12, [])]),
        ("call_c", 150, 16, [("util_fn", 151, 12, [])]),
        ("call_d", 170, 16, [("util_fn", 171, 12, [])]),
        ("batch_loop", 200, 120, [
            ("insert_row", 200 + i * 11, 8, []) for i in range(10)
        ]),
        # The near miss: a marker one level too deep. `AGENTTMP_insert_preset`
        # is a loop body reached from two callers, so it clears every gate
        # except the spread — a loop's occurrences vary by what they are
        # given. On a live hunt this was the duplicate seen from one level
        # down: the agent bracketed the insert instead of the unit around it,
        # and a detector built for that finding said nothing at all.
        #
        # `shared_helper` below is its control: two callers and a spread just
        # as wide, without the prefix. Nobody can re-wrap a slice they did not
        # plant, so that one stays silent and this one does not.
        # `AGENTTMP_parse_json` rides along in three of these as the near
        # miss's own control: the same shape from three callers instead of
        # two, which is a helper doing its job rather than a unit somebody
        # ran twice. A finding is allowed three callers because equal cost
        # carries the claim; a near miss has no equal cost to lean on.
        ("AGENTTMP_seed_first", 330, 60, [
            ("AGENTTMP_insert_preset", 331, 2, []),
            ("AGENTTMP_insert_preset", 334, 2, []),
            ("AGENTTMP_insert_preset", 337, 24, []),
            ("AGENTTMP_parse_json", 362, 24, []),
        ]),
        ("stage_first", 400, 200, [("fill_presets", 410, 90, [])]),
        ("AGENTTMP_seed_again", 610, 60, [
            ("AGENTTMP_insert_preset", 611, 2, []),
            ("AGENTTMP_insert_preset", 614, 2, []),
            ("AGENTTMP_insert_preset", 617, 20, []),
            ("AGENTTMP_parse_json", 640, 3, []),
        ]),
        ("stage_again", 700, 200, [("fill_presets", 710, 95, [])]),
        ("stage_light", 920, 30, [("shared_helper", 922, 5, [])]),
        ("stage_heavy", 960, 90, [("shared_helper", 962, 80, [])]),
        ("AGENTTMP_seed_third", 1052, 45, [
            ("AGENTTMP_parse_json", 1054, 20, []),
        ]),
    ],
    TID_STUCK: [
        ("Lock contention on a monitor lock (owner tid: 4444)", 1045, None, []),
    ],
    # ART writes a contention as two nested slices, and the outer one carries
    # the number of threads queued behind the lock. So one wait comes back
    # under a different parent every time the queue is a different length —
    # one name, two callers, the same price both times, which is the shape
    # `repeated_work` is built for and is not two callers at all. It cost that
    # detector its first two rows on a live trace.
    #
    # The wait itself is real and belongs to `monitor_contention`, which is
    # the point: a thread stopped working is not work done twice, and one
    # signal must not arrive in the report under two headings.
    TID_LOCKED: [
        # Two anchors a later scenario could legitimately use, both inside
        # the 800..860 stretch the main thread spends asleep, and they must
        # be read differently — which is the whole point of them.
        #
        # At 840 the main thread's `Steady_heavy` message (833..845) is still
        # open: it is asleep INSIDE a message, which is a blocking call, and
        # 40 of those 60 ms happened before the window. That is what
        # `window.opened_inside` is for.
        #
        # At 847 nothing is open below `AppStart`: `Steady_heavy` ended at 845
        # and `binder reply` does not start until 850. The looper had reached
        # the queue, the app is behaving correctly, and a rule reading the
        # sleep alone would call this a stall. On a real command-driven
        # scenario the main thread sat in exactly this state for 1615 ms
        # waiting for a finger.
        #
        # That 845..850 gap is the only clear moment in this sleep, so a
        # main-thread slice moved over it turns this control into a duplicate
        # of the one above.
        #
        # On this thread because its slices are all lock names, so an extra
        # name matches no detector's mask, and because its 10 ms of CPU is far
        # below anything that reads coverage.
        ("LateAnchor", 840, 5, []),
        ("IdleAnchor", 847, 2, []),
        ("monitor contention with owner Thread-3 (4455) at void "
         "com.example.Store.put(java.lang.String)(Store.java:41) waiters=0 "
         "blocking from java.lang.Object com.example.Store.get()(:-1)", 300, 26, [
             ("Lock contention on a monitor lock (owner tid: 4455)", 301, 24, []),
         ]),
        ("monitor contention with owner Thread-3 (4455) at void "
         "com.example.Store.put(java.lang.String)(Store.java:41) waiters=1 "
         "blocking from java.lang.Object com.example.Store.get()(:-1)", 400, 27, [
             ("Lock contention on a monitor lock (owner tid: 4455)", 401, 25, []),
         ]),
    ],
    TID_MAIN: [
        # BEFORE the window — main_thread_block must not see it
        ("Bootstrap_OUTSIDE", 0, 50, []),
        # The start anchor. Wraps the whole scenario, as on a real startup.
        ("AppStart", 100, 1006, [
            # app_init: the stretch a cold start spends between the
            # Application and the first Activity, shaped the way a real one
            # is. `makeApplication` is traced and ends at 108; what the
            # platform does not trace — ContentProviders, then
            # Application.onCreate — fills the 91 ms after it to 199. Inside:
            # androidx.startup's `Startup` with two Initializers, a library's
            # own section, classes ART initialized — one of them obfuscated,
            # one inside `Startup` beside the Initializer it belongs to — and
            # async binder transactions, whose name belongs to `binder_txn`
            # and stays there, even inside `Startup`. The 48 ms nobody named
            # is the row this detector exists for.
            ("bindApplication", 101, 98, [
                ("makeApplication", 102, 6, []),
                ("Startup", 110, 16, [
                    ("Landroidx/work/WorkManagerInitializer;", 110.2, 0.6, []),
                    ("WorkManagerInitializer", 111, 9, []),
                    ("binder transaction async", 120.2, 0.5, []),
                    ("ProfileInstallerInitializer", 121, 4, []),
                ]),
                ("Firebase", 128, 22, []),
                ("Lcom/example/app/Store;", 152, 1, []),
                ("Lq3;", 154, 1, []),
                ("binder transaction async", 156, 3, []),
            ]),
            # main_thread_block: 120 ms on main → fires (threshold 16)
            ("collection_mapping", 200, 120, []),
            # negative control: 5 ms < 16, must not fire
            ("quick_thing", 400, 5, []),
            # binder_txn: 25 ms → fires (threshold 10)
            ("binder transaction", 450, 25, []),
            # negative control: 3 ms < 10
            ("binder transaction", 480, 3, []),
            # negative control: an async transaction. Long and on main, but the
            # sender does not block on it, so it has no place in binder_txn.
            ("binder transaction async", 600, 40, []),
            # monitor_contention: 30 ms → fires (threshold 8)
            ("Lock contention on a monitor lock (owner tid: 4201)", 500, 30, []),
            # --- the naming zoo ---------------------------------------
            # The names below were confirmed against live Android 14 and 13
            # traces. They are kept in the fixture not to make tests green but
            # so that mask behaviour stays visible in `names` and in the
            # checks, instead of living as a comment in the README.

            # The other side of GC: allocations on main stalled waiting for
            # the collector. Three of them, 180 ms together, because that is
            # what it takes to clear gc_pressure's max_total_ms of 120 — with
            # one 60 ms stall the second mask was planted and never exercised,
            # and the check that said so had never once held.
            ("waitWhileAllocatingLocked", 700, 60, []),
            ("waitWhileAllocatingLocked", 900, 60, []),
            ("waitWhileAllocatingLocked", 980, 60, []),
            # A runtime-internal lock. The narrowed mask must NOT pick it up:
            # there is no application code behind it.
            ("Lock contention on GC lock (owner tid: 4202)", 800, 20, []),
            # The server side of a synchronous transaction. Does not match
            # 'binder transaction*' — whether it should is still open.
            ("binder reply", 850, 15, []),
            # A second block on the same thread with a DIFFERENT owner.
            # Grouping by slice name would shatter one finding into two rows:
            # the owner's tid sits right inside the name. On a live trace 190
            # blocks scattered exactly that way and none cleared the threshold.
            ("Lock contention on a monitor lock (owner tid: 4202)", 880, 12, []),

            # The Activity's start and resume, as ActivityThread traces them,
            # kept short and in gaps. `android_startups` counts the launch
            # below (LAUNCH) as the app's only where its main thread ran
            # these, and calls it cold for bindApplication with them.
            ("activityStart", 396.5, 3, []),
            ("activityResume", 866, 3, []),

            # --- main_thread_outlier -----------------------------------
            # A group with a history and one occurrence far outside it. Six
            # inflates of 4 ms and one of 44: 11x the median, and past the
            # 40 ms floor. Nothing about the group's SUM is remarkable, which
            # is the point — main_thread_block sees 64 ms and shrugs.
            *[("inflate", 322 + i * 6, 4, []) for i in range(5)],
            ("inflate", 352, 44, []),

            # Negative control: a group with no outlier in it. Even work stays
            # even work however often it repeats.
            *[("measure", 530 + i * 8, 6, []) for i in range(6)],

            # Negative control: three occurrences and a spike. A name seen
            # three times has no typical duration for anything to be an
            # outlier from, and min_occurrences is what says so.
            ("Rare_work", 641, 1, []),
            ("Rare_work", 643, 1, []),
            ("Rare_work", 645, 45, []),

            # Negative control: 20x the median, and 20 ms. A ratio without an
            # absolute floor under it turns every short repeated slice into a
            # finding.
            *[("Tiny_tick", 762 + i * 2, 1, []) for i in range(5)],
            ("Tiny_tick", 775, 20, []),

            # Negative control, and the one the other three do not cover:
            # work that is genuinely slow and consistently so. Five at 12 ms
            # and one at 44 — past the absolute floor, and only 3.7x the
            # median. Without the ratio gate this fires; with it, slow-but-
            # even work stays main_thread_block's business.
            *[("Steady_heavy", 405 + i * 13, 12, []) for i in range(3)],
            ("Steady_heavy", 820, 12, []),
            ("Steady_heavy", 833, 12, []),
            ("Steady_heavy", 1040, 44, []),

            # The end anchor: ts + dur = 1105
            ("Screen.firstFrame", 1100, 5, []),
        ]),
        # AFTER the window — 200 ms on main the detector must not see
        ("After_OUTSIDE", 1200, 200, []),
    ],
    # gc_pressure: 20 collection cycles → fires on count (threshold 15).
    # The structure is real, taken from a live Android 14 trace: the cycle sits
    # at depth 0 with its phases (CopyingPhase and friends) nested inside.
    # Adding them to the parent counts the same time twice; on the live trace
    # CopyingPhase reported 295 ms against 282 ms for the whole cycle.
    TID_HEAP: [
        ("Background young concurrent copying GC", 700 + i * 5, 4, [
            ("CopyingPhase", 700 + i * 5, 3, []),
        ])
        for i in range(20)
    ],
    # uninstrumented_cpu negative control: 180 ms of slices over 200 ms of
    # Running = 90% coverage, so the thread must NOT land in the blind spots.
    TID_INSTR: [
        ("work_a", 200, 90, []),
        ("work_b", 300, 90, []),
    ],
    # uninstrumented_cpu: a 300 ms slice by wall clock, but the thread sleeps
    # through most of it — only 60 ms on CPU. Plus 100 ms of Running with no
    # slices at all. Comparing slice duration against on-CPU time would give
    # "coverage" of 300 out of 160 ms = 188% and the blind spot would vanish.
    # The honest count is the intersection of Running with slices: 60 of 160.
    TID_BLOCKED: [("blocking_io_wait", 200, 300, [])],
    # TID_WORKER is deliberately empty: burns CPU, zero slices.
    TID_WORKER: [],
    # TID_OKHTTP is deliberately empty.
    TID_OKHTTP: [],
}

OTHER_SLICES = {
    # A huge slice on the foreign process's main thread. If it surfaces in the
    # report, _proc is not isolating the process.
    OTHER_PID: [("other_app_huge_OUTSIDE", 200, 500, [])],
}

# --- async sections ----------------------------------------------------------
# Shape: (name, start_ms, dur_ms, cookie). What `Trace.beginAsyncSection`
# writes: an S/F pair on the process rather than a B/E pair on a thread, so
# the section lands on a track of its own and belongs to no thread. Every
# hand-written marker on a real project turned out to be this kind, and none
# of them was visible: the end anchor matched nothing and the window became
# the whole trace.
#
# `Screen.loaded` is what a project's own end marker looks like — a span across
# the load, closing at 1000, well inside the sync anchor's window. A config
# that ends the scenario on it must get a window of [100, 1000]: 900 ms, and
# `end_anchor.matches` of 1. `Async.mark` is the zero-length shape, a point
# in time written as begin-and-end in one go.
#
# Neither may reach a detector. An async section has no thread to be counted
# under and no depth in anyone's tree; a detector that shows one has started
# reading the process's tracks as if they were a thread's, and its self-time
# and coverage sums are wrong by that section's length.
#
# The cookie tells apart concurrent sections of the same name and must be
# unique per name; here every name has one section, and the numbers only
# have to differ.
ASYNC_SLICES = [
    ("Screen.loaded", 300, 700, 1),
    ("Async.mark", 350, 0, 2),
]

# --- the scheduler ---------------------------------------------------------
# (cpu, tid, start_ms, end_ms, state_after_being_switched_out)
#   0 → R (ready but preempted), 1 → S (sleeping), 2 → D (uninterruptible)
#
# One CPU per thread — simpler, and realistic for a multicore device.

# D is 2 in the same bitmask: uninterruptible sleep, which is where a thread
# goes while it waits for a block device. The kernel says which of those
# sleeps was disk in a separate event — see BLOCKED_REASON below.
R, S, D = 0, 1, 2

SCHED = [
    # main: two chunks, both deliberately crossing the window bounds
    # [100, 1105]. The first starts BEFORE the window opens, the second ends
    # AFTER it closes. This checks whether context.sql clips intervals to the
    # window or merely filters them by their start point.
    #
    # The first chunk is cut in two by 60 ms of io_wait at 200: the main
    # thread waits for a block device, which is the headline case of the
    # `io_wait` detector and the one that costs a user a visibly frozen start.
    # It stays a single busy stretch for `anr_risk`, which counts D as busy —
    # correctly, since a thread parked in the kernel is not serving the looper
    # either.
    (0, TID_MAIN, 50, 200, D),
    (0, TID_MAIN, 260, 600, S),
    (0, TID_MAIN, 610, 800, S),
    # An idle moment inside the scenario: 60 ms asleep with no slice open
    # below the anchor. anr_risk must break its stretch here — the looper
    # reached the queue, and a pending event would have been served. The
    # anchor `AppStart` spans right across it at depth 0, which is why that
    # depth is not evidence of anything.
    (0, TID_MAIN, 860, 1300, S),

    # StuckForever: 10 ms on a CPU and then blocked for good. Enough to give
    # the thread a place in the schedule and far too little to interest
    # uninstrumented_cpu.
    (5, TID_STUCK, 1040, 1050, S),

    # uninstrumented_cpu: 300 ms of Running, zero slices → fires
    (1, TID_WORKER, 200, 350, S),
    (1, TID_WORKER, 400, 550, S),

    # HeapTaskDaemon: 100 ms Running against 80 ms of slices = 80% coverage.
    # Must not land in the blind spots even though it clears the Running bar.
    (2, TID_HEAP, 700, 800, S),

    # runnable_starvation: Running, preempted for 50 ms (state R), Running again
    (3, TID_OKHTTP, 600, 620, R),
    (3, TID_OKHTTP, 670, 680, S),

    # WellInstrumented: 200 ms Running against 180 ms of slices
    (4, TID_INSTR, 200, 400, S),

    # BlockingIO-1: the blocking_io_wait slice spans 200..500, but the thread is
    # on CPU only from 200..260. The later 100 ms of Running is outside slices.
    (6, TID_BLOCKED, 200, 260, S),
    (6, TID_BLOCKED, 600, 700, S),

    # SeedWorker: 200 ms of Running sitting entirely inside `stage_first`,
    # which is a depth-0 slice — so its coverage is complete and
    # `uninstrumented_cpu` has nothing to say about it. The thread is here for
    # `repeated_work`, and a thread planted for one detector must not turn up
    # in another's answer.
    (7, TID_SEED, 400, 600, S),

    # LockWaiter: 10 ms on a CPU, enough to give the thread a place in the
    # schedule and far too little to interest uninstrumented_cpu. It spends
    # the scenario blocked, which is what its slices say.
    (8, TID_LOCKED, 200, 210, S),

    # io_wait: DiskWaiter runs briefly and then the kernel parks it three
    # times waiting for a block device — 150 + 90 + 60 = 300 ms of the window
    # in state D. Nothing else in the fixture can see this: there is no slice,
    # no CPU time, and `uninstrumented_cpu` is silent by construction because
    # a thread waiting on the disk burns nothing.
    (1, TID_DISK, 150, 160, D),
    (1, TID_DISK, 310, 320, D),
    (1, TID_DISK, 410, 420, D),
    (1, TID_DISK, 480, 490, S),

    # Negative control: OtherBlocked spends 400 ms in the same D state, and
    # the kernel does NOT call it io_wait. Uninterruptible sleep is not the
    # signal — the disk is. A detector matching on the state alone would
    # report this thread as waiting for a disk it never touched.
    (2, TID_DISK_BG, 200, 210, D),
    (2, TID_DISK_BG, 610, 620, S),

    # the foreign process
    (5, OTHER_PID, 200, 700, S),
]

# Which sleeps the kernel blamed on a block device: (tid, at_ms, io_wait).
#
# Emitted as `sched/sched_blocked_reason` at the moment the thread leaves the
# CPU, which is where a real kernel writes it. The `caller` address is
# deliberately absent: on a production build /proc/kallsyms cannot be read, so
# `blocked_function` is NULL for every interval — measured on an SM-A515F,
# 6683 of 6683 — and the fixture reproduces the phone rather than the ideal.
BLOCKED_REASON = [
    (TID_MAIN, 200, 1),
    (TID_DISK, 160, 1),
    (TID_DISK, 320, 1),
    (TID_DISK, 420, 1),
    (TID_DISK_BG, 210, 0),
]

# --- the platform state ----------------------------------------------------
#
# Not a detector's problem: nothing here fires anything. These are the numbers
# every duration in the report is conditional on, and the fixture plants them
# so that the arithmetic which weights them has a known answer.
#
# (cpu, at_ms, khz). Three things are planted deliberately:
#
#   * the opening samples sit at ms 0, BEFORE the window opens at 100. A
#     reader that filters samples by the window first would find no frequency
#     for the start of the scenario and report the whole run unmeasured;
#   * CPU 0 doubles at ms 600, mid-window, and CPU 0 is where the main thread
#     spends 935 of its ms. The weighted mean has to move with it;
#   * CPU 9 sits at 300 MHz and nothing of ours ever runs there. It is the
#     negative control for the weighting: a plain average across CPU tracks
#     would drag the answer down to 930 MHz and report a minimum of 300, and
#     neither number would be about this app.
CPU_FREQ = [
    *[(cpu, 0, 1_000_000) for cpu in range(9)],
    (0, 600, 2_000_000),
    (9, 0, 300_000),
]

# (at_ms, zone, milli_celsius). The last one is outside the window [100, 1105]
# and must not reach the report: a device that got hot after the scenario says
# nothing about the scenario.
THERMAL = [
    (200, "cpu-therm", 45_000),
    (700, "cpu-therm", 61_500),
    (1200, "cpu-therm", 80_000),
]

# (at_ms, device, level). A cooling device that is present and idle is the
# point: `throttled` has to read false here. The kernel took nothing away
# during the window, and it took capacity away only afterwards.
COOLING = [
    (200, "thermal-cpufreq-0", 0),
    (900, "thermal-cpufreq-0", 0),
    (1200, "thermal-cpufreq-0", 3),
]

# (at_ms, MemAvailable_kb, pgmajfault_total). Polled once a second on a real
# device; here the samples are placed by hand so the window holds two of them
# and the third falls outside it. Major faults are a running total, so what
# the window costs is the difference across it: 250.
SYS_STATS = [
    (200, 2_000_000, 1_000),
    (900, 1_500_000, 1_250),
    (1200, 100_000, 9_999),
]

# --- callstack samples -----------------------------------------------------
#
# What `runner.sampling` records: Perfetto's linux.perf, the samples on a
# sequence of their own with their frames interned, and the recording's own
# config at the head of the trace, which is where the rate is read from.
#
# The report counts this process's samples inside the window and how many of
# them came with a stack: 46 and 44. `uninstrumented_cpu` reads them again, for
# what ran in its two blind spots. The rest is there to be left out:
#
#   * DefaultDispatcher-worker-1 is sampled at 100 Hz through both of its
#     stretches on a CPU, [200, 350] and [400, 550]: 30 samples. The first two
#     arrive without a stack, the way a new process's first samples do while
#     the sampler is still opening its memory. The other 28 are five kinds of
#     stack, WORKER_STACKS below;
#   * BlockingIO-1 is sampled through both of its stretches too: six in
#     [200, 260], inside `blocking_io_wait` and so no blind spot's, and ten in
#     [600, 700], after the slice closed;
#   * the main thread is sampled at 60 and 80 ms, before the window opens,
#     and at 1150 and 1200, after it closes;
#   * com.other.app is sampled inside the window, on the core nothing of ours
#     runs on, and without a stack: every app that is not profileable comes
#     back that way.
#
# (cpu, pid, tid, at_ms, stack) — the stack by its key in SAMPLE_STACKS, None
# for a sample that came without one.
SAMPLING_HZ = 100

# The files the frames come from. On a device the app's own code comes back
# by name, interpreted, JIT-compiled or ahead of time; the framework's,
# compiled into the boot image, comes back with no name at all, and the
# unwinder often stops in it. The app's path is the shape Android gives an
# install, and trace_processor reads the package out of it: that is how the
# build's mapping finds the frames it renames.
APP_CODE = ("/data/app/~~4P0Sh9WdPWBQZ7Lr3ihzDQ==/"
            "com.example.app-9QUkqHeqXmFKvBz0ZQ-Fhw==/oat/arm64/base.odex")
FRAMEWORK = "/system/framework/arm64/boot-framework.oat"
LIBC = "/apex/com.android.runtime/lib64/bionic/libc.so"
LIBART = "/apex/com.android.art/lib64/libart.so"
LIBZ = "/system/lib64/libz.so"

# A worker's stack from where its thread began, root first: bionic's thread
# start, Thread.run from the framework, then the pool's own loop, which the
# app ships in its own code.
_POOL = [("__start_thread", LIBC), (None, FRAMEWORK),
         ("kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run", APP_CODE),
         ("kotlinx.coroutines.DispatchedTask.run", APP_CODE)]

# Root first, as a callstack lists its frames: (function, file), and None for a
# function the unwinder could not name.
SAMPLE_STACKS = {
    # The project's own code saving through a gzip sink: zlib on top, the
    # sink's method the first one named, and Store.save the nearest of ours.
    "saved": [*_POOL, ("com.example.app.Store.save", APP_CODE),
              ("okio.GzipSink.write", APP_CODE), (None, FRAMEWORK), ("deflate", LIBZ)],
    # The same work with nothing of ours under it, down to the thread's start:
    # a task handed to the pool runs without its caller.
    "pooled": [*_POOL, ("okio.GzipSink.write", APP_CODE), (None, FRAMEWORK),
               ("deflate", LIBZ)],
    # The project's parser, interpreted: ART's helper on top, passed over for
    # the method it was running.
    "interpreted": [*_POOL, ("com.example.app.Store.load", APP_CODE),
                    ("com.example.app.Store.parse", APP_CODE), ("nterp_helper", LIBART)],
    # Cut short: the unwinder stopped in the framework's unnamed code, and
    # what is left is the collector's read barrier, mangled as C++ names come.
    "cut": [(None, FRAMEWORK), ("_ZN3art11ReadBarrier4MarkEPNS_6mirror6ObjectE", LIBART)],
    # Nothing named at all.
    "unnamed": [(None, FRAMEWORK)],
    # BlockingIO-1 inside its slice, and after it.
    "inside": [("__start_thread", LIBC), (None, FRAMEWORK),
               ("com.example.app.Disk.readSync", APP_CODE)],
    "after": [("__start_thread", LIBC), (None, FRAMEWORK),
              ("com.example.app.Disk.checksum", APP_CODE)],
    # The main thread, outside the window.
    "main": [(None, FRAMEWORK), ("com.example.app.Store.load", APP_CODE)],
}

# What the worker's 28 stacks are. What ran: GzipSink.write 15,
# ReadBarrier::Mark 6, Store.parse 5, the unnamed framework 2. Nearest of ours:
# Store.save 10, cut short 8, Store.parse 5, none on a whole stack 5.
WORKER_STACKS = (["saved"] * 10 + ["cut"] * 6 + ["pooled"] * 5
                 + ["interpreted"] * 5 + ["unnamed"] * 2)

# --- a minified build --------------------------------------------------------
#
# The same samples from a build R8 minified: the app's methods and those of
# the libraries it ships come back with the names R8 gave them. `MAPPING` is
# the build's `mapping.txt`, which names them back. Two methods of Disk got one
# minified name, as overloads do, and come back as both. `OTHER_MAPPING` is
# another build's: it renames the one frame it happens to match, wrongly, and
# misses the rest.
MINIFIED = {
    "com.example.app.Store.save": "a.b.c",
    "com.example.app.Store.load": "a.b.d",
    "com.example.app.Store.parse": "a.b.e",
    "com.example.app.Disk.checksum": "a.f.c",
    "com.example.app.Disk.readSync": "a.f.d",
    "okio.GzipSink.write": "b.c.a",
    "kotlinx.coroutines.DispatchedTask.run": "c.a.run",
    "kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run": "c.b$a.run",
}

MAPPING = """\
# compiler: R8
# compiler_version: 8.5.35
# min_api: 24
# pg_map_id: 3f9a2c1
com.example.app.Store -> a.b:
# {"id":"sourceFile","fileName":"Store.kt"}
    1:6:void save(java.lang.String):22:27 -> c
    7:9:void save(java.lang.String):30:32 -> c
    1:4:java.util.List load():40:43 -> d
    1:8:com.example.app.Model parse(java.lang.String):60:67 -> e
    java.lang.String path -> f
com.example.app.Disk -> a.f:
# {"id":"sourceFile","fileName":"Disk.kt"}
    1:3:long checksum(java.io.File):12:14 -> c
    4:4:long checksumLegacy(java.io.File):30:30 -> c
    1:2:byte[] readSync(java.io.File):20:21 -> d
okio.GzipSink -> b.c:
    1:20:void write(okio.Buffer,long):70:89 -> a
kotlinx.coroutines.DispatchedTask -> c.a:
    1:30:void run():90:119 -> run
kotlinx.coroutines.scheduling.CoroutineScheduler$Worker -> c.b$a:
    1:40:void run():700:739 -> run
"""

OTHER_MAPPING = """\
# compiler: R8
# pg_map_id: 77d01e5
com.example.app.Cache -> a.b:
    1:5:void evict(int):12:16 -> c
com.example.app.Store -> a.g:
    1:6:void save(java.lang.String):22:27 -> a
"""

SAMPLES = [
    *[(1, APP_PID, TID_WORKER, at, None) for at in (205, 215)],
    *[(1, APP_PID, TID_WORKER, at, stack) for at, stack in zip(
        [*range(225, 350, 10), *range(405, 550, 10)], WORKER_STACKS, strict=True)],
    *[(6, APP_PID, TID_BLOCKED, at, "inside") for at in range(205, 260, 10)],
    *[(6, APP_PID, TID_BLOCKED, at, "after") for at in range(605, 700, 10)],
    *[(0, APP_PID, TID_MAIN, at, "main") for at in (60, 80, 1150, 1200)],
    *[(9, OTHER_PID, OTHER_PID, at, None) for at in (300, 500, 700)],
]


def _flatten(slices, out, seq):
    """Unrolls the slice tree into B/E events with correct nesting.

    A duration of None means the slice never ends: the begin event is written
    and no end event follows, which is what a trace looks like when the thing
    was still happening when recording stopped. trace_processor gives such a
    slice `dur = -1`, and reading that as zero is how the longest block in a
    trace becomes invisible.
    """
    for name, start, dur, children in slices:
        out.append((ms(start), next(seq), "B", name))
        _flatten(children, out, seq)
        if dur is not None:
            out.append((ms(start + dur), next(seq), "E", name))
    return out


def _span(builder, cookies, kind, start_ms, dur_ms, **fields) -> None:
    """One timeline entry: a start event, then FrameEnd on the same cookie.

    Duration is not a field. The parser derives it from the gap between the
    start packet and the FrameEnd carrying the same cookie, which is why every
    frame costs two packets per timeline and why cookies must be unique across
    the whole trace.
    """
    cookie = next(cookies)
    packet = builder.add_packet()
    packet.timestamp = ms(start_ms)
    event = getattr(packet.frame_timeline_event, kind)
    event.cookie = cookie
    for key, value in fields.items():
        setattr(event, key, value)

    packet = builder.add_packet()
    packet.timestamp = ms(start_ms + dur_ms)
    packet.frame_timeline_event.frame_end.cookie = cookie


def _frames(builder) -> None:
    """The frame timeline: expected and actual, for three processes."""
    cookies = iter(range(10**5, 10**6))

    for frames, pid, layer in ((APP_FRAMES, APP_PID, LAYER),
                               (OTHER_FRAMES, OTHER_PID, OTHER_LAYER)):
        for token, start, expected_ms, actual_ms, jank in frames:
            surface = dict(token=token, display_frame_token=token + 1000,
                           pid=pid, layer_name=layer)
            _span(builder, cookies, "expected_surface_frame_start",
                  start, expected_ms, **surface)
            _span(builder, cookies, "actual_surface_frame_start",
                  start, actual_ms, **surface, jank_type=jank,
                  present_type=(FT.PRESENT_ON_TIME if jank == FT.JANK_NONE
                                else FT.PRESENT_LATE),
                  on_time_finish=(jank == FT.JANK_NONE),
                  gpu_composition=False, prediction_type=FT.PREDICTION_VALID)

    for token, start, expected_ms, actual_ms, jank in SF_FRAMES:
        _span(builder, cookies, "expected_display_frame_start",
              start, expected_ms, token=token, pid=SF_PID)
        _span(builder, cookies, "actual_display_frame_start",
              start, actual_ms, token=token, pid=SF_PID, jank_type=jank,
              present_type=FT.PRESENT_LATE, on_time_finish=False,
              gpu_composition=False, prediction_type=FT.PREDICTION_VALID)


def _samples(builder, arrived: bool = True, minified: bool = False) -> None:
    """Callstack sampling: the config that asked for it, then what the sampler wrote.

    `arrived=False` keeps the config and drops the rest — a recording that
    asked for samples on a device whose sampler never ran. `minified` writes
    the frames under the names R8 gave them, `MINIFIED`.
    """
    packet = builder.add_packet()
    perf = packet.trace_config.data_sources.add().config
    perf.name = "linux.perf"
    perf.target_buffer = 1
    perf.perf_event_config.timebase.frequency = SAMPLING_HZ
    perf.perf_event_config.callstack_sampling.kernel_frames = False
    if not arrived:
        return

    # The sequence opens the way traced_perf opens it: state cleared, the
    # defaults it samples with, and the files, names, frames and stacks every
    # sample below refers to.
    packet = builder.add_packet()
    packet.trusted_packet_sequence_id = 3000
    packet.sequence_flags = pb.TracePacket.SEQ_INCREMENTAL_STATE_CLEARED
    packet.trace_packet_defaults.perf_sample_defaults.timebase.frequency = SAMPLING_HZ
    interned = packet.interned_data

    files = {path: iid for iid, path in enumerate(dict.fromkeys(
        path for stack in SAMPLE_STACKS.values() for _, path in stack), start=1)}
    for path, iid in files.items():
        string = interned.mapping_paths.add()
        string.iid, string.str = iid, path.encode()
        mapping = interned.mappings.add()
        mapping.iid = iid
        mapping.start, mapping.end = iid * 0x100000, (iid + 1) * 0x100000
        mapping.path_string_ids.append(iid)

    # A frame with no name is written without one, which is how the sampler
    # writes a function it could not symbolize.
    frames: dict[tuple[str | None, str], int] = {}
    names: dict[str, int] = {}
    for stack in SAMPLE_STACKS.values():
        for name, path in stack:
            if (name, path) in frames:
                continue
            iid = frames[(name, path)] = len(frames) + 1
            frame = interned.frames.add()
            frame.iid, frame.mapping_id, frame.rel_pc = iid, files[path], 0x100 * iid
            if name is not None:
                if name not in names:
                    names[name] = len(names) + 1
                    function = interned.function_names.add()
                    written = MINIFIED.get(name, name) if minified else name
                    function.iid, function.str = names[name], written.encode()
                frame.function_name_id = names[name]

    callstacks: dict[str, int] = {}
    for iid, (key, stack) in enumerate(SAMPLE_STACKS.items(), start=1):
        callstack = interned.callstacks.add()
        callstack.iid = callstacks[key] = iid
        callstack.frame_ids.extend(frames[frame] for frame in stack)

    for cpu, pid, tid, at, stack in SAMPLES:
        packet = builder.add_packet()
        packet.trusted_packet_sequence_id = 3000
        packet.timestamp = ms(at)
        sample = packet.perf_sample
        sample.cpu, sample.pid, sample.tid = cpu, pid, tid
        sample.cpu_mode = pb.Profiling.MODE_USER
        if stack is not None:
            sample.callstack_iid = callstacks[stack]
        else:
            # What traced_perf writes for a sample it could not unwind: the
            # sample, without its stack, and why.
            sample.sample_skipped_reason = pb.PerfSample.PROFILER_SKIP_READ_STAGE



# --- the ANR the system recorded -------------------------------------------
#
# Not written by the app. When ActivityManager declares an ANR it writes two
# atrace counters from system_server under the ActivityManager tag — the `am`
# category, which `echolot collect` asks for — and the stdlib module reads the
# ANR out of those. So the fixture has to carry a system_server, and the shape
# of the two names is the whole contract:
#
#   ErrorId:<process> <pid>#<uuid>
#   Subject(for ErrorId <uuid>):<subject>
#
# Placed AFTER the window on purpose. That is where a real one lands: the
# system waits five seconds before declaring anything, and a cold start's
# window closes at the first frame. A detector clipped to the window would
# find nothing in almost every trace that contains an ANR, which is why this
# one is not, and this is the case that proves it.

SS_PID = 1200
SS_NAME = "system_server"
SS_THREADS = {SS_PID: "system_server"}

# --- the launch, as system_server records it ----------------------------------
#
# What Perfetto's `android_startups` reads on Android 13, as a phone wrote it:
# an async section `launchingActivity#<id>` from the intent to the first frame,
# and an instant `launchingActivity#<id>:completed:<package>` when the frame is
# drawn. The startup's type is the module's own inference: cold, for a main
# thread that ran bindApplication, activityStart and activityResume inside it.
#
# It begins at 40, 60 ms before the scenario's window opens, and ends with the
# window at 1105. The startup is the platform's measure and the window is the
# config's, and the report has to say which is which.
LAUNCH_ID = 1
LAUNCH_AT_MS = 40
LAUNCH_DRAWN_MS = 1105

ANR_UUID = "0123abcd-1111-2222-3333-444455556666"
ANR_SUBJECT = "Input dispatching timed out (fixture)"
ANR_AT_MS = 1300

# Negative control: another application froze too. It is not our process and
# has no business in our report.
OTHER_UUID = "9999ffff-8888-7777-6666-555544443333"

# The counter's name comes in two shapes and both are here, because the pid is
# in only one of them:
#
#   ErrorId:<process> <pid>#<uuid>     the stdlib reads the pid out of this
#   ErrorId:<process>#<uuid>           and has nothing to read in this
#
# Ours is written in the second, which is what an Android 13 phone actually
# produced. On it the stdlib returns no pid and no upid, so a detector joining
# on either matches nothing — a real ANR sat in a real trace, parsed correctly,
# and went unreported. The process name is what identifies it, and this is the
# fixture that says so.
ANR_COUNTERS = [
    (ANR_AT_MS, f"ErrorId:{APP_NAME}#{ANR_UUID}"),
    (ANR_AT_MS, f"Subject(for ErrorId {ANR_UUID}):{ANR_SUBJECT}"),
    # The negative control is written in the other shape, so the pid path is
    # exercised too — and has to exclude it just the same.
    (ANR_AT_MS + 40, f"ErrorId:{OTHER_NAME} {OTHER_PID}#{OTHER_UUID}"),
    (ANR_AT_MS + 40,
     f"Subject(for ErrorId {OTHER_UUID}):Input dispatching timed out (other app)"),
]


def build(frames: bool = True, environment: bool = True,
          sampling: str | None = None) -> bytes:
    """The fixture trace. `frames=False` leaves out the frame timeline.

    Android 11 and below, and any trace recorded without the
    android.surfaceflinger.frametimeline data source, have no frame timeline
    at all. That is the common case for a while yet, and frame_jank has to
    meet it with silence rather than an error.

    `environment=False` leaves out the platform state the same way: every
    trace recorded before echolot asked for those sources, and every one
    recorded with `runner.environment: false`, arrives without them. The
    report has to say "not recorded" for those and never "the device held
    steady", which is a different sentence and the one a comparison would act
    on.

    `sampling` is what became of callstack sampling: `"arrived"`, `"asked"`
    for a config that asked and a sampler that never ran, `"minified"` for
    samples that arrived from a build R8 minified, or `None` for a recording
    that never asked. `None` by default, because that is the default
    recording, and the sample report in the README is this one.
    """
    builder = TraceProtoBuilder()
    process_tree(builder, [(APP_PID, APP_NAME, THREADS),
                           (OTHER_PID, OTHER_NAME, OTHER_THREADS),
                           (SF_PID, SF_NAME, SF_THREADS),
                           (SS_PID, SS_NAME, SS_THREADS)])

    if frames:
        _frames(builder)
    if sampling:
        _samples(builder, arrived=sampling in ("arrived", "minified"),
                 minified=sampling == "minified")

    # Collect ftrace events per CPU.
    by_cpu: dict[int, list] = {}
    tid_to_cpu = {tid: cpu for cpu, tid, *_ in SCHED}
    tid_to_tgid = {tid: APP_PID for tid in THREADS}
    tid_to_tgid.update({tid: OTHER_PID for tid in OTHER_THREADS})
    names = dict(THREADS)
    names.update(OTHER_THREADS)

    seq = iter(range(10**6))

    # sched_switch: entering and leaving Running.
    for cpu, tid, start, end, end_state in SCHED:
        idle = f"swapper/{cpu}"
        by_cpu.setdefault(cpu, []).append(
            (ms(start), next(seq), "sched", idle, 0, R, names[tid], tid)
        )
        by_cpu[cpu].append(
            (ms(end), next(seq), "sched", names[tid], tid, end_state, idle, 0)
        )

    # atrace print: the slices.
    all_slices = dict(SLICES)
    all_slices.update(OTHER_SLICES)
    for tid, tree_slices in all_slices.items():
        events = _flatten(tree_slices, [], seq)
        cpu = tid_to_cpu[tid]
        tgid = tid_to_tgid[tid]
        for ts, order, kind, name in events:
            buf = f"B|{tgid}|{name}\n" if kind == "B" else f"E|{tgid}\n"
            by_cpu.setdefault(cpu, []).append(
                (ts, order, "print", tid, buf)
            )

    # atrace async sections: S and F carry the name and a cookie, and the
    # process rather than the thread. Emitted from the main thread's CPU
    # because an ftrace event needs a CPU to sit on; the parser ties the
    # section to the pid in the buffer, not to the thread that wrote it.
    for name, start, dur, cookie in ASYNC_SLICES:
        cpu = tid_to_cpu[TID_MAIN]
        by_cpu.setdefault(cpu, []).append(
            (ms(start), next(seq), "print", TID_MAIN, f"S|{APP_PID}|{name}|{cookie}\n"))
        by_cpu[cpu].append(
            (ms(start + dur), next(seq), "print", TID_MAIN, f"F|{APP_PID}|{name}|{cookie}\n"))

    # atrace counters: the ANR record, from system_server rather than from us.
    for at, name in ANR_COUNTERS:
        by_cpu.setdefault(0, []).append(
            (ms(at), next(seq), "print", SS_PID, f"C|{SS_PID}|{name}|1\n")
        )

    # The launch, from system_server too. The completion is an instant: a
    # begin and an end at one timestamp, which is a slice of no length.
    launch = f"launchingActivity#{LAUNCH_ID}"
    for at, buf in ((LAUNCH_AT_MS, f"S|{SS_PID}|{launch}|{LAUNCH_ID}"),
                    (LAUNCH_DRAWN_MS, f"F|{SS_PID}|{launch}|{LAUNCH_ID}"),
                    (LAUNCH_DRAWN_MS, f"B|{SS_PID}|{launch}:completed:{APP_NAME}"),
                    (LAUNCH_DRAWN_MS, f"E|{SS_PID}")):
        by_cpu.setdefault(0, []).append((ms(at), next(seq), "print", SS_PID, buf + "\n"))

    # The platform state. Frequency belongs to its own CPU's bundle — that is
    # where a real kernel writes it. Thermal is a property of the device rather
    # than of a core, so it goes on CPU 0 like any other global event.
    for tid, at, io in BLOCKED_REASON:
        by_cpu.setdefault(tid_to_cpu[tid], []).append(
            (ms(at), next(seq), "blocked", tid, io))

    if environment:
        for cpu, at, khz in CPU_FREQ:
            by_cpu.setdefault(cpu, []).append((ms(at), next(seq), "freq", cpu, khz))
        for at, zone, milli_c in THERMAL:
            by_cpu.setdefault(0, []).append((ms(at), next(seq), "temp", zone, milli_c))
        for at, device, level in COOLING:
            by_cpu.setdefault(0, []).append((ms(at), next(seq), "cdev", device, level))

    ftrace(builder, by_cpu)
    sys_stats(builder, SYS_STATS if environment else [])

    return builder.serialize()


# --- the packets, for any scene ------------------------------------------------
#
# The trace's packets are written the same way whatever the scene: the demo
# (demo.py) builds an app of its own with these.

def process_tree(builder, processes) -> None:
    """ProcessTree, the only source of process and thread names: (pid, name, {tid: name})."""
    packet = builder.add_packet()
    packet.timestamp = BASE_NS
    tree = packet.process_tree
    for pid, name, threads in processes:
        proc = tree.processes.add()
        proc.pid = pid
        proc.ppid = 1
        proc.cmdline.append(name)
        for tid, tname in threads.items():
            thread = tree.threads.add()
            thread.tid = tid
            thread.tgid = pid
            thread.name = tname


def ftrace(builder, by_cpu: dict[int, list]) -> None:
    """One ftrace bundle per CPU, its events in time order.

    An event is a tuple: its timestamp, a sequence number that orders events
    of one timestamp, its kind, and the fields the kind needs — `sched`
    (prev_comm, prev_pid, prev_state, next_comm, next_pid), `blocked` (tid,
    io_wait), `freq` (cpu, khz), `temp` (zone, millidegrees), `cdev` (device,
    level), and `print` (tid, atrace line).
    """
    for cpu in sorted(by_cpu):
        packet = builder.add_packet()
        packet.trusted_packet_sequence_id = 1000 + cpu
        bundle = packet.ftrace_events
        bundle.cpu = cpu
        for item in sorted(by_cpu[cpu], key=lambda e: (e[0], e[1])):
            ts, _order, kind = item[0], item[1], item[2]
            event = bundle.event.add()
            event.timestamp = ts
            if kind == "sched":
                _, _, _, prev_comm, prev_pid, prev_state, next_comm, next_pid = item
                event.pid = prev_pid
                sw = event.sched_switch
                sw.prev_comm = prev_comm
                sw.prev_pid = prev_pid
                sw.prev_prio = 120
                sw.prev_state = prev_state
                sw.next_comm = next_comm
                sw.next_pid = next_pid
                sw.next_prio = 120
            elif kind == "blocked":
                _, _, _, tid, io = item
                event.pid = tid
                event.sched_blocked_reason.pid = tid
                event.sched_blocked_reason.io_wait = io
            elif kind == "freq":
                _, _, _, freq_cpu, khz = item
                event.pid = 0
                event.cpu_frequency.cpu_id = freq_cpu
                event.cpu_frequency.state = khz
            elif kind == "temp":
                _, _, _, zone, milli_c = item
                event.pid = 0
                event.thermal_temperature.thermal_zone = zone
                event.thermal_temperature.temp = milli_c
            elif kind == "cdev":
                _, _, _, device, level = item
                event.pid = 0
                event.cdev_update.type = device
                event.cdev_update.target = level
            else:
                _, _, _, tid, buf = item
                event.pid = tid
                event.print.buf = buf


def sys_stats(builder, rows) -> None:
    """Memory, as its own packets rather than ftrace events: on a device this
    is linux.sys_stats polling /proc, and it arrives the same way here.
    (at_ms, MemAvailable in kB, major faults so far)."""
    for at, avail_kb, faults in rows:
        packet = builder.add_packet()
        packet.timestamp = ms(at)
        packet.trusted_packet_sequence_id = 2000
        meminfo = packet.sys_stats.meminfo.add()
        meminfo.key = pb.MEMINFO_MEM_AVAILABLE
        meminfo.value = avail_kb
        vmstat = packet.sys_stats.vmstat.add()
        vmstat.key = pb.VMSTAT_PGMAJFAULT
        vmstat.value = faults


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    out = Path(argv[0]) if argv else Path("fixture.perfetto-trace")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build())
    print(f"→ {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
