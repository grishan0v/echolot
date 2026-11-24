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
slices), and the recording's own config at its head. The same format that
arrives from a device.

One planted problem per detector plus negative controls — the things that must
NOT fire. The expected answers live in echolot/selftest.py.

    python -m echolot.fixture out.perfetto-trace
"""

from __future__ import annotations

import fnmatch
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
TID_ROOM_0 = 4211
TID_ROOM_1 = 4212
TID_JIT = 4213
TID_LOADER = 4215

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
    # Room's disk executor, whose threads wake the main thread below.
    TID_ROOM_0: "arch_disk_io_0",
    TID_ROOM_1: "arch_disk_io_1",
    # ART's compiler, whose methods compiled in the window `environment` counts.
    TID_JIT: "Jit thread pool",
    # Loads a native library while the main thread is inside `inflate`, and
    # is the thread that lets it out of the kernel (WAKINGS).
    TID_LOADER: "NativeLoader",
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

# A native daemon, which answers one of the main thread's binder calls
# (BINDER_CALLS) and has no other slice. Not surfaceflinger: its having no
# thread slice at all is what a check of `project.process` stands on.
KS_PID = 700
KS_NAME = "/system/bin/keystore2"
KS_BINDER_TID = 715
KS_THREADS = {KS_PID: "keystore2", KS_BINDER_TID: "binder:700_1"}

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

# Starting with Perfetto v58 (google/perfetto#6456), FrameTimelineEvent (field
# 76 on TracePacket) moved out of perfetto_trace.proto into an out-of-tree
# Android extension, so newer perfetto wheels omit pb.FrameTimelineEvent while
# trace_processor still decodes field 76 on the wire. Register it on the
# descriptor pool when the installed perfetto package no longer carries it.
_FT_EXT_NAME = "com.android.internal.frame_timeline_event"


def _frame_timeline_ext():
    from google.protobuf import descriptor_pb2

    pool = pb.DESCRIPTOR.pool
    try:
        return pool.FindExtensionByName(_FT_EXT_NAME)
    except KeyError:
        pass
    T = descriptor_pb2.FieldDescriptorProto
    fdp = descriptor_pb2.FileDescriptorProto(
        name="echolot_frame_timeline_ext.proto",
        package="com.android.internal",
        dependency=[pb.DESCRIPTOR.name],
    )
    msg = fdp.message_type.add()
    msg.name = "FrameTimelineEvent"
    for mname, fields in (
        ("ExpectedDisplayFrameStart", [
            ("cookie", 1, T.TYPE_INT64), ("token", 2, T.TYPE_INT64), ("pid", 3, T.TYPE_INT32)]),
        ("ActualDisplayFrameStart", [
            ("cookie", 1, T.TYPE_INT64), ("token", 2, T.TYPE_INT64), ("pid", 3, T.TYPE_INT32),
            ("present_type", 4, T.TYPE_INT32), ("on_time_finish", 5, T.TYPE_BOOL),
            ("gpu_composition", 6, T.TYPE_BOOL), ("jank_type", 7, T.TYPE_INT32),
            ("prediction_type", 8, T.TYPE_INT32)]),
        ("ExpectedSurfaceFrameStart", [
            ("cookie", 1, T.TYPE_INT64), ("token", 2, T.TYPE_INT64),
            ("display_frame_token", 3, T.TYPE_INT64), ("pid", 4, T.TYPE_INT32),
            ("layer_name", 5, T.TYPE_STRING)]),
        ("ActualSurfaceFrameStart", [
            ("cookie", 1, T.TYPE_INT64), ("token", 2, T.TYPE_INT64),
            ("display_frame_token", 3, T.TYPE_INT64), ("pid", 4, T.TYPE_INT32),
            ("layer_name", 5, T.TYPE_STRING), ("present_type", 6, T.TYPE_INT32),
            ("on_time_finish", 7, T.TYPE_BOOL), ("gpu_composition", 8, T.TYPE_BOOL),
            ("jank_type", 9, T.TYPE_INT32), ("prediction_type", 10, T.TYPE_INT32)]),
        ("FrameEnd", [("cookie", 1, T.TYPE_INT64)]),
    ):
        nested = msg.nested_type.add()
        nested.name = mname
        for fname, num, ftype in fields:
            f = nested.field.add()
            f.name, f.number, f.label, f.type = fname, num, T.LABEL_OPTIONAL, ftype
    for fname, num, tname in (
        ("expected_display_frame_start", 1, "ExpectedDisplayFrameStart"),
        ("actual_display_frame_start", 2, "ActualDisplayFrameStart"),
        ("expected_surface_frame_start", 3, "ExpectedSurfaceFrameStart"),
        ("actual_surface_frame_start", 4, "ActualSurfaceFrameStart"),
        ("frame_end", 5, "FrameEnd"),
    ):
        f = msg.field.add()
        f.name, f.number, f.label, f.type = fname, num, T.LABEL_OPTIONAL, T.TYPE_MESSAGE
        f.type_name = f".com.android.internal.FrameTimelineEvent.{tname}"
    ext = fdp.extension.add()
    ext.name, ext.number, ext.label, ext.type = (
        "frame_timeline_event", 76, T.LABEL_OPTIONAL, T.TYPE_MESSAGE)
    ext.type_name = ".com.android.internal.FrameTimelineEvent"
    ext.extendee = "." + pb.TracePacket.DESCRIPTOR.full_name
    pool.AddSerializedFile(fdp.SerializeToString())
    return pool.FindExtensionByName(_FT_EXT_NAME)


class _FrameTimelineConstants:
    JANK_NONE = 1
    JANK_SF_CPU_DEADLINE_MISSED = 16
    JANK_APP_DEADLINE_MISSED = 64
    JANK_BUFFER_STUFFING = 128
    PRESENT_ON_TIME = 1
    PRESENT_LATE = 2
    PREDICTION_VALID = 1


FT = getattr(pb, "FrameTimelineEvent", _FrameTimelineConstants)
_FT_EXT = (None if "frame_timeline_event" in pb.TracePacket.DESCRIPTOR.fields_by_name
           else _frame_timeline_ext())


def _frame_timeline(packet):
    return packet.frame_timeline_event if _FT_EXT is None else packet.Extensions[_FT_EXT]


LAYER = "com.example.app/com.example.app.MainActivity#0"

APP_FRAMES = [
    # The finding: 5 frames the app itself was late for, 44 ms over each. Each
    # starts 4 ms after its expected start and lasts 56 ms: the deadline is the
    # expected frame's end, so the late start is part of the overrun. Actual
    # minus expected duration would say 40, and on a real cold start every
    # frame of the app started late, by up to 212 ms.
    *[(1 + i, 200 + i * 50, 16, 56, FT.JANK_APP_DEADLINE_MISSED, 4) for i in range(5)],
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

# The app's frames of a list that runs a frame behind (#537), in place of the
# ones above with `build(stuffed=True)`. On a device where the render thread
# takes most of every frame the app queues each frame while the one before is
# still waiting to be shown: the platform tags those Buffer Stuffing, and they
# reach the screen late and smoothly. Here they are the most frames and the
# most time past a deadline, 12 of 25 frames 24 ms over each — 288 ms, 29% of
# the window — against the frames the app really missed: 4 of its own, 20 ms
# over each, and 3 that missed while queued, 18 ms over each. Summed as late
# frames, the queued ones were the first row and a level that needed
# attention.
STUFFED_FRAMES = [
    *[(1 + i, 150 + i * 40, 16, 40, FT.JANK_BUFFER_STUFFING) for i in range(12)],
    *[(20 + i, 650 + i * 40, 16, 36, FT.JANK_APP_DEADLINE_MISSED) for i in range(4)],
    *[(30 + i, 820 + i * 30, 16, 34,
       FT.JANK_APP_DEADLINE_MISSED | FT.JANK_BUFFER_STUFFING) for i in range(3)],
    *[(40 + i, 920 + i * 20, 16, 14, FT.JANK_NONE) for i in range(6)],
]

# The app's frames of a build that keeps up (#586), in place of the ones
# above with `build(smooth=True)`: a frame every 16 ms across the window, 60
# of them, 3 the app missed by 8 ms each, 5%. `APP_FRAMES` is the build that
# janks: 24 frames in the same window, 5 the app missed, a fifth. A drag
# recorded for a fixed time takes as long on either, and the share of frames
# the app made late is what tells the two apart.
SMOOTH_FRAMES = [
    *[(1 + i, 105 + i * 16, 16, 14, FT.JANK_NONE) for i in range(57)],
    *[(70 + i, 105 + (57 + i) * 16, 16, 24, FT.JANK_APP_DEADLINE_MISSED) for i in range(3)],
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
        # Before the window, as a process opens its dex at its start: the
        # compile state `environment.compile` reads off the whole trace. The
        # first is another package's file, verified only, and must not be
        # taken for the app's: the app's own is told by the path its parent
        # names, not by coming first.
        ("Bootstrap_OUTSIDE", 0, 50, [
            ("OpenDexFilesFromOat(/data/app/~~d2Vi==/com.google.android.webview-dmlldw==/base.apk)",
             5, 4, [("location=/data/app/~~d2Vi==/com.google.android.webview-dmlldw==/oat/arm64/"
                     "base.odex status=up-to-date filter=verify reason=vdex", 6, 1, [])]),
            ("OpenDexFilesFromOat(/data/app/~~Zm9v==/com.example.app-YmFy==/base.apk)",
             20, 4, [("location=/data/app/~~Zm9v==/com.example.app-YmFy==/oat/arm64/"
                      "base.odex status=up-to-date filter=speed-profile reason=install-dm", 21, 1, [])]),
        ]),
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
            # binder_txn: two synchronous transactions, 25 ms at 450 → fires
            # (threshold 10), and 3 ms at 480, a negative control alone. Not
            # here: they are the binder driver's events, not atrace's, so
            # that the trace ties each to the process that answered it
            # (BINDER_CALLS).
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
            # A group with a history and one occurrence far outside it. Five
            # inflates of 4 ms and one of 44, six in all: 11x the median, and
            # past the 40 ms floor. Nothing about the group's SUM is remarkable, which
            # is the point — main_thread_block sees 64 ms and shrugs.
            *[("inflate", 322 + i * 6, 4, []) for i in range(5)],
            ("inflate", 352, 44, []),

            # Negative control: a group with no outlier in it. Even work stays
            # even work however often it repeats.
            *[("measure", 530 + i * 8, 6, []) for i in range(6)],

            # --- composables ------------------------------------------
            # What `androidx.compose.runtime:runtime-tracing` writes: every
            # composable call a slice, `Name (File.kt:line)`, under the
            # `Compose:recompose` that composed it. Three passes, in gaps the
            # main thread spends running. First the screen's first
            # composition, in the traversal of the frame that attaches its
            # view: the app's `FeedScreen` with one `FeedItem`. Then in a
            # frame, `FeedScreen` again with two of its `FeedItem`s — one
            # under the library's `Column` — and a third party's `Chart` of
            # 9 ms; during measure, the library's `LazyColumn` composing two
            # more.
            #
            # `composables` ranks the app's own by their calls less the
            # app's own nested in them: `FeedScreen` 28 ms end to end over two
            # calls, 23 of them its own, and `FeedItem` 15 over five calls:
            # one in the first composition, two in a frame and two during
            # measure. `Chart` is above the bar and is no row: not androidx,
            # and not the app's either. No name here clears another
            # detector's bar — `main_thread_block` wants 16 ms of self time,
            # and the most any of them holds is `FeedItem`'s 15 — and
            # `Compose:recompose` has three callers and three costs.
            ("traversal", 484, 12, [
                ("Compose:recompose", 484.5, 11, [
                    ("com.example.app.ui.FeedScreen (FeedScreen.kt:24)", 485, 10, [
                        ("com.example.app.ui.FeedItem (FeedItem.kt:31)", 486, 1, []),
                    ]),
                ]),
            ]),
            ("Recomposer:recompose", 579, 20, [
                ("Compose:recompose", 579.5, 19, [
                    ("com.example.app.ui.FeedScreen (FeedScreen.kt:24)", 580, 18, [
                        ("androidx.compose.foundation.layout.Column (Column.kt:73)",
                         580.5, 4, [
                             ("com.example.app.ui.FeedItem (FeedItem.kt:31)", 581, 2, []),
                         ]),
                        ("com.example.app.ui.FeedItem (FeedItem.kt:31)", 585, 2, []),
                        ("com.vendor.chart.Chart (Chart.kt:12)", 588, 9, []),
                    ]),
                ]),
            ]),
            ("AndroidOwner:onMeasure", 962, 15, [
                ("Compose:recompose", 962.5, 14, [
                    ("androidx.compose.foundation.lazy.LazyColumn (LazyDsl.kt:378)",
                     963, 13, [
                         ("com.example.app.ui.FeedItem (FeedItem.kt:31)", 963.5, 5, []),
                         ("com.example.app.ui.FeedItem (FeedItem.kt:31)", 969, 5, []),
                     ]),
                ]),
            ]),

            # Negative control: three occurrences and a spike. A name seen
            # three times has no typical duration for anything to be an
            # outlier from, and min_occurrences is what says so.
            ("Rare_work", 641, 1, []),
            ("Rare_work", 643, 1, []),
            ("Rare_work", 645, 45, []),

            # --- main_thread_waits --------------------------------------
            # The main thread asleep in `loadConfig` from 692 to 698, until
            # Room's `arch_disk_io_1` woke it (WAKINGS below), and in
            # `readSession` from 1086 to 1098, until `arch_disk_io_0` did. At
            # 860 that thread ends a sleep that began inside the GC lock's
            # contention, which is a lock's wait and not counted. One row,
            # `arch_disk_io_#`: 2 waits, 18 ms, the longer one in
            # `readSession`. The two slices hold 9 and 14 ms of self time,
            # under `main_thread_block`'s bar. The looper's idle, no slice
            # open, is no wait (#926): `looper_waits` below has it.
            ("loadConfig", 690.5, 9, []),
            ("readSession", 1085, 14, []),

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
    # What each freed is in the heap's counter, HEAP_KB.
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
        # Classes ART verified at run time, as `environment.compile` counts
        # them: Store and Feed, 5 ms. FeedBase is verified inside Feed and is
        # Feed's time already; a class loader's check is not a class.
        ("work_a", 200, 90, [
            ("VerifyClass com.example.app.Store", 210, 3, []),
            ("VerifyClassLoaderContextMatch", 250, 1, []),
        ]),
        ("work_b", 300, 90, [
            ("VerifyClass com.example.app.Feed", 310, 2, [
                ("VerifyClass com.example.app.FeedBase", 310.5, 1, []),
            ]),
        ]),
    ],
    # Two methods compiled inside the window, 10 ms, and one after it closes,
    # which the window's count leaves out.
    TID_JIT: [
        ("JIT compiling void com.example.app.Store.load() (kind=Baseline) from "
         "/data/app/~~Zm9v==/com.example.app-YmFy==/base.apk", 300, 4, [
             ("Compiling baseline", 300.5, 3, [])]),
        ("JIT compiling void com.example.app.Feed.bind() (kind=Optimized) from "
         "/data/app/~~Zm9v==/com.example.app-YmFy==/base.apk", 600, 6, [
             ("Compiling optimized", 600.5, 5, [])]),
        ("JIT compiling void com.example.app.Late.run() (kind=Baseline) from "
         "/data/app/~~Zm9v==/com.example.app-YmFy==/base.apk", 1150, 2, []),
    ],
    # The library load the main thread waited on in the kernel, from 360 to
    # 380 ms, by NativeLoader's wakeup at its end.
    TID_LOADER: [("dlopen: libimage.so", 350, 40, [])],
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
    # main: six stretches on CPU 0, the first and the last deliberately
    # crossing the window bounds [100, 1105]. The first starts BEFORE the
    # window opens, the last ends AFTER it closes. This checks whether
    # context.sql clips intervals to the window or merely filters them by
    # their start point.
    #
    # The first two are 60 ms of io_wait apart, at 200: the main thread waits
    # for a block device, which is the headline case of the `io_wait`
    # detector and the one that costs a user a visibly frozen start. It stays
    # a single busy stretch for `anr_risk`, which counts D as busy —
    # correctly, since a thread parked in the kernel is not serving the looper
    # either.
    (0, TID_MAIN, 50, 200, D),
    # Held in the kernel for something other than the disk, twice: 20 ms
    # inside `inflate`, ended by NativeLoader while it was in `dlopen`, and
    # 2 ms more that another process ended. Only the first is the app's.
    (0, TID_MAIN, 260, 360, D),
    (0, TID_MAIN, 380, 384, D),
    (0, TID_MAIN, 386, 600, S),
    (0, TID_MAIN, 610, 692, S),
    # Asleep 692..698 inside `loadConfig`, until Room's thread wakes it.
    (0, TID_MAIN, 698, 800, S),
    # An idle moment inside the scenario: the main thread sleeps 800..860,
    # and only 845..850 of it has nothing open below the anchor — the rest is
    # covered by AppStart's children (the GC lock contention, Steady_heavy,
    # the binder reply). anr_risk must break its stretch there — the looper
    # reached the queue, and a pending event would have been served. The
    # anchor `AppStart` spans right across it at depth 0, which is why that
    # depth is not evidence of anything.
    (0, TID_MAIN, 860, 1086, S),
    # Asleep 1086..1098 inside `readSession`, until Room's thread wakes it
    # again.
    (0, TID_MAIN, 1098, 1300, S),

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
    #
    # On CPU 8, at the same 1 GHz as CPU 1: the worker holds CPU 1 over
    # 200..350 and 400..550, and one core runs one thread at a time.
    (8, TID_DISK, 150, 160, D),
    (8, TID_DISK, 310, 320, D),
    (8, TID_DISK, 410, 420, D),
    (8, TID_DISK, 480, 490, S),

    # Negative control: OtherBlocked spends 400 ms in the same D state, and
    # the kernel does NOT call it io_wait. Uninterruptible sleep is not the
    # signal — the disk is. A detector matching on the state alone would
    # report this thread as waiting for a disk it never touched.
    (2, TID_DISK_BG, 200, 210, D),
    (2, TID_DISK_BG, 610, 620, S),

    # Room's two disk threads, each on a CPU just long enough to wake the
    # main thread (WAKINGS).
    (8, TID_ROOM_1, 695, 698, S),
    (8, TID_ROOM_0, 857, 860, S),
    (8, TID_ROOM_0, 1094, 1098, S),

    # The JIT's thread, on a CPU only after the window: its slices need a
    # thread the trace knows, and its CPU time would move the window's clock.
    (7, TID_JIT, 1300, 1301, S),
    # NativeLoader the same way: on a CPU only after the window, so that its
    # slice and its wakeups need not move the window's clock.
    (7, TID_LOADER, 1302, 1303, S),

    # the foreign process
    (5, OTHER_PID, 200, 700, S),
]

# Who woke whom: (ms the wakee is back on its CPU, waker_tid, wakee_tid).
# `sched_waking`, emitted on the waker's CPU while it runs, a nanosecond before
# the wakee runs: trace_processor puts the waker on the state the woken
# thread enters, the one after its sleep, and a real wait for the CPU there
# would be `runnable_starvation`'s, which this fixture keeps for its own case.
WAKINGS = [
    (698, TID_ROOM_1, TID_MAIN),
    # The main thread out of the kernel: the disk (260) and the app's dlopen
    # (380) by NativeLoader, and the short one (386) by another process.
    (260, TID_LOADER, TID_MAIN),
    (380, TID_LOADER, TID_MAIN),
    (386, OTHER_PID, TID_MAIN),
    (860, TID_ROOM_0, TID_MAIN),
    (1098, TID_ROOM_0, TID_MAIN),
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
    (TID_MAIN, 360, 0),
    (TID_MAIN, 384, 0),
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
#     spends 875 of its ms in the window. The weighted mean has to move with it;
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


def _without_composables(slices):
    """The slice tree with every composable's call left out, and what is under it.

    The shape `runtime-tracing` writes — `Name (File.kt:line)`, under a
    `Compose:recompose` — which is the `composables` detector's own mask.
    What a recording without composable names looks like: the passes that
    composed them stay, and nothing names what they composed.
    """
    return [(name, start, dur, _without_composables(children))
            for name, start, dur, children in slices
            if name != "Compose:recompose"
            and not fnmatch.fnmatchcase(name, COMPOSABLE_MASK)]


# The mask `composables` reads a composable's call by.
COMPOSABLE_MASK = "* ([A-Za-z]*.kt:[0-9]*)"


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
    event = getattr(_frame_timeline(packet), kind)
    event.cookie = cookie
    for key, value in fields.items():
        setattr(event, key, value)

    packet = builder.add_packet()
    packet.timestamp = ms(start_ms + dur_ms)
    _frame_timeline(packet).frame_end.cookie = cookie


def _frames(builder, app_frames=APP_FRAMES) -> None:
    """The frame timeline: expected and actual, for three processes."""
    cookies = iter(range(10**5, 10**6))

    for frames, pid, layer in ((app_frames, APP_PID, LAYER),
                               (OTHER_FRAMES, OTHER_PID, OTHER_LAYER)):
        # An optional sixth field: how late the actual frame started after
        # the expected one.
        for token, start, expected_ms, actual_ms, jank, *late in frames:
            surface = dict(token=token, display_frame_token=token + 1000,
                           pid=pid, layer_name=layer)
            _span(builder, cookies, "expected_surface_frame_start",
                  start, expected_ms, **surface)
            _span(builder, cookies, "actual_surface_frame_start",
                  start + sum(late), actual_ms, **surface, jank_type=jank,
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
    """Callstack sampling: what the sampler wrote. The config that asked for it
    is `config`'s.

    `arrived=False` writes nothing — a recording that asked for samples on a
    device whose sampler never ran. `minified` writes the frames under the
    names R8 gave them, `MINIFIED`.
    """
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
# A binder thread, which answers one of the main thread's calls (BINDER_CALLS).
SS_BINDER_TID = 1215
SS_THREADS = {SS_PID: "system_server", SS_BINDER_TID: "binder:1200_2"}

# --- who answered the app's calls, and what the collector freed ---------------
#
# The main thread's synchronous transactions, as the binder driver writes them
# under `binder_driver`: the caller's `binder_transaction` and, on the thread
# that answers, its `binder_transaction_received`; then the answer's own pair
# the other way. trace_processor makes them the caller's `binder transaction`
# and the server's `binder reply`, tied by a flow, which `android.binder` reads
# the server off (#514). The first is answered by system_server, which names
# the AIDL method inside its reply as a recording with the `aidl` category
# has it; the second by keystore2, a native daemon named by its path, which
# names none. The 25 ms one is the row; the 3 ms one is in its sum, and alone
# would not be.
#
#   (caller tid, start ms, ms, server pid, server tid, the method's slice)
BINDER_CALLS = [
    (TID_MAIN, 450, 25, SS_PID, SS_BINDER_TID,
     "AIDL::java::IPackageManager::getPackageInfo::server"),
    (TID_MAIN, 480, 3, KS_PID, KS_BINDER_TID, None),
]

# ART's `Heap size (KB)`, an atrace counter of the app's process under
# `dalvik`, around the twenty cycles on HeapTaskDaemon (700 + 5i, 4 ms each):
# 30 MB before a cycle and 18 MB after it, which `android.garbage_collection`
# reads as 12 MB freed. The first cycle comes after a burst, at 64 MB, the
# shape a cold start's first collection has: so a row's heap is the largest
# before any cycle and its freed memory a cycle's average, 13.7 MB.
#
#   (at ms, KB)
HEAP_KB = [(650, 20_000)] + [
    point for i in range(20) for point in (
        (700 + i * 5 - 0.5, 64_000 if i == 0 else 30_000),
        (700 + i * 5 + 4.5, 18_000))
]

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

# --- `reportFullyDrawn()`, for a window that is the launch ----------------------
#
# `build(fully_drawn=...)` only: the default trace is a launch whose app never
# says when its content is there, and a launch window closes at the launch's
# end.
#
# What an Android 13 phone wrote for an app that calls it through androidx,
# after the first frame: two nested slices on the main thread, androidx's and
# the framework's inside it, and ActivityManager's own slice in system_server
# as it takes the call, a little later. `"app"` plants all three, as that
# phone did; `"system_server"` leaves out the app's two, for an Activity that
# writes none; `"early"` is the app's call made before the first frame, which
# must not close the window before the launch has ended. Both moments are
# ones where the main thread is on a CPU with nothing else open: 1099 inside
# `AppStart`, a millisecond before `Screen.firstFrame`, and 1160 between
# `AppStart` and `After_OUTSIDE`.
FULLY_DRAWN_AT_MS = 1160
FULLY_DRAWN_EARLY_MS = 1099
FULLY_DRAWN_MS = 0.4
FULLY_DRAWN_SEEN_AT_MS = 1162.5
FULLY_DRAWN_SEEN_MS = 0.3

# --- which activity the launch ended on, and a boot receiver ----------------------
#
# `build(activities=...)` and `build(boot=True)` only. An activity's creation
# as Android 12 and on trace it, `performCreate:<class>` on the main thread:
# the first inside `activityStart` at 396.5, the second inside
# `activityResume` at 866, both inside the launch (40–1105). A launch the app
# took on to another activity by itself creates both, the one started first,
# and ends on the second (#909). And `BOOT_COMPLETED`'s receiver, as
# ActivityThread names it, in a gap of the main thread inside `AppStart`
# after `quick_thing`: what Android 15 runs on the first start after a
# force-stop (#910). 4.5 ms, under every detector's threshold.
CREATED_AT_MS = (397.0, 866.5)
CREATED_MS = 1.0
BOOT_RECEIVER = "broadcastReceiveComp: android.intent.action.BOOT_COMPLETED"
BOOT_AT_MS = 410.0
BOOT_MS = 4.5


def _fully_drawn(kind: str) -> tuple[list, list]:
    """The slices of one `reportFullyDrawn()` call: the app's, and system_server's."""
    at = FULLY_DRAWN_EARLY_MS if kind == "early" else FULLY_DRAWN_AT_MS
    app = [] if kind == "system_server" else [
        ("reportFullyDrawn() for ComponentActivity", at, FULLY_DRAWN_MS, [
            (f"reportFullyDrawn() for {{{APP_NAME}/{APP_NAME}.MainActivity}}",
             at + 0.1, 0.2, []),
        ]),
    ]
    seen = [(f"ActivityManager:ReportingFullyDrawn {APP_NAME}",
             at + FULLY_DRAWN_SEEN_AT_MS - FULLY_DRAWN_AT_MS, FULLY_DRAWN_SEEN_MS, [])]
    return app, seen

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


# --- a crowded variant: who held the main thread's core ------------------------
#
# `build(crowded=True)` only; the default trace keeps the main thread off the
# CPU queue, and its budget's `waiting_for_cpu` is pinned at zero.
#
# The main thread is preempted at 400 ms and back on CPU 0 at 430. Meanwhile
# CPU 0 runs a thread of the app for 18 ms and another process's for 8, and
# stands idle for the last 4: `runnable_starvation`'s main-thread row says
# the app held the core 60%, other processes 27%, nobody 13%. Twelve more
# threads of the app queue at once from 880 ms on CPU 9 — more threads ready
# than the trace has cores — for `window.crowding`.
TID_CROWD = 4214
OTHER_WORKER = 5001
CROWD_TIDS = [4220 + i for i in range(12)]


def _crowded(sched: list, threads: dict, other_threads: dict) -> tuple[list, dict, dict]:
    sched = [row for row in sched if row != (0, TID_MAIN, 386, 600, S)]
    sched += [
        (0, TID_MAIN, 386, 400, R),
        (0, TID_CROWD, 400, 418, S),
        (0, OTHER_WORKER, 418, 426, S),
        (0, TID_MAIN, 430, 600, S),
    ]
    # Each runs a millisecond and is preempted, then runs again a millisecond
    # once all twelve have queued.
    for i, tid in enumerate(CROWD_TIDS):
        sched.append((9, tid, 880 + i, 881 + i, R))
    for i, tid in enumerate(CROWD_TIDS):
        sched.append((9, tid, 950 + i, 951 + i, S))
    threads = {**threads, TID_CROWD: "CrowdWorker",
               **{tid: f"Crowd-{i}" for i, tid in enumerate(CROWD_TIDS)}}
    other_threads = {**other_threads, OTHER_WORKER: "OtherWorker"}
    return sched, threads, other_threads


# --- the processes that draw the app, busy -------------------------------------
#
# `build(drawing=True)` only. On a quiet phone a cold start keeps SurfaceFlinger
# and system_server busy: 17.6% and 12.2% of the CPUs' time of Wikipedia's on
# an SM-F711B (#914). Here every stretch of the window [100, 1105] that a CPU
# stands idle in goes to a thread of one of them, SurfaceFlinger's on the even
# CPUs and system_server's on the odd: other processes take most of the CPUs,
# and none of it is load anybody could close.
DRAWING_SF_TID = 610     # + the CPU's number
DRAWING_SS_TID = 1230


def idle_stretches(sched: list, cpu: int) -> list[tuple[float, float]]:
    """The stretches of the window [100, 1105] in which `cpu` runs nothing,
    half a millisecond in from what runs on either side."""
    out, at = [], 100
    busy = sorted((start, end) for c, _, start, end, _ in sched if c == cpu)
    for start, end in [*busy, (1105, 1105)]:
        start = min(start, 1105)
        if start > at + 1:
            out.append((at + 0.5, start - 0.5))
        at = max(at, end)
        if at >= 1105:
            break
    return out


def _drawing(sched: list) -> tuple[list, dict, dict]:
    sf, ss = dict(SF_THREADS), dict(SS_THREADS)
    out = list(sched)
    for cpu in sorted({cpu for cpu, *_ in sched}):
        if cpu % 2 == 0:
            tid, sf[DRAWING_SF_TID + cpu] = DRAWING_SF_TID + cpu, "RenderEngine"
        else:
            tid, ss[DRAWING_SS_TID + cpu] = DRAWING_SS_TID + cpu, "android.anim"
        out += [(cpu, tid, start, end, S) for start, end in idle_stretches(sched, cpu)]
    return out, sf, ss


# --- a blind stretch on a well-marked main thread ------------------------------
#
# `blind_main()` only, a scene of its own: the default trace's main thread sits
# whole inside `AppStart`, a top-level slice, and can have no blind spot.
#
# The shape a large app's cold start had on a real project. The start anchor is
# `bindApplication`, which closes early, and the main thread's work from there
# to the end anchor is marked up at the top level, with 5 ms nobody names
# between one slice and the next: 25 ms in all, below `uninstrumented_cpu`'s
# bar. Then the fault planted there: a busy loop of 300 ms on the main thread,
# right before the last frame, with no section of its own. The main thread is
# on a CPU for 1520 ms and 1195 of them are inside slices, 79% covered — over
# the 50% that keeps a thread out of the rows — and the 325 ms outside them
# are the main thread's row. `loop_ms` sets the loop; 0 is the start without
# it.
#
# Two other threads, for the bar that still holds for them. A pool with no
# slices at all, 350 ms on a CPU: it ranks above the main thread on the time
# nobody named, though the main thread spent four times its CPU. And Room's
# thread, 400 ms on a CPU, 300 of them inside its queries: 100 ms outside,
# above the bar, and 75% covered. A thread that names three quarters of its
# work is not where the next marker goes; only the main thread is held to the
# time alone.
BLIND_POOL, BLIND_ROOM = 4301, 4302
# The main thread named as the kernel names it: the package's last fifteen
# characters, all of `com.example.app`.
BLIND_THREADS = {TID_MAIN: APP_NAME[-15:], BLIND_POOL: "DefaultDispatch",
                 BLIND_ROOM: "arch_disk_io_0"}
BLIND_LOOP_MS = 300


def blind_main(loop_ms: float = BLIND_LOOP_MS, *, run: int | None = None,
               install: str | None = None, jit: tuple[str, ...] = ()) -> bytes:
    """The scene above: `com.example.app`'s cold start from `bindApplication`
    to `Screen.firstFrame`, with `loop_ms` of busy loop on the main thread and
    no slice around it.

    `run`, `install` and `jit` draw the same start as one repeat of a build,
    for the names `compare` sets against each other (#610), and leave the
    scene above as it is when not given. `run` numbers what the runtime
    numbers anew every time: each frame's vsync, the HWC fence the render
    thread waits for, the number of a pool's thread. `jit` is the methods
    the JIT compiles on its thread, each `jit_name`d as from `install`,
    the build's install directory."""
    frame = "Choreographer#doFrame" if run is None else "Choreographer#doFrame {}"
    vsync = [] if run is None else [52_000 + 40 * run + i for i in range(3)]
    main = [
        ("bindApplication", 100, 300, []),
        ("activityStart", 405, 200, []),
        (frame.format(*vsync[0:1]), 610, 300, []),
        (frame.format(*vsync[1:2]), 915, 300, []),
        # The loop runs from 1215 on, after the gap.
        (frame.format(*vsync[2:3]), 1220 + loop_ms, 90, []),
        ("Screen.firstFrame", 1315 + loop_ms, 5, []),
    ]
    room = [("room_query", 200, 100, []), ("room_query", 350, 100, []),
            ("room_query", 500, 100, [])]
    sched = [(0, TID_MAIN, 100, 1320 + loop_ms, S),
             (1, BLIND_POOL, 300, 650, S),
             (2, BLIND_ROOM, 200, 600, S)]
    threads = dict(BLIND_THREADS)
    trees = [(0, TID_MAIN, main), (2, BLIND_ROOM, room)]
    if run is not None:
        threads[BLIND_RENDER] = "RenderThread"
        threads[BLIND_WORKER] = f"Thread-{30 + run}"
        trees += [(3, BLIND_RENDER, [
            (f"waiting for HWC release {300 + 7 * run + i}", 620 + 305 * i, 4, [])
            for i in range(3)]),
            (4, BLIND_WORKER, [("prefetch", 450, 30, [])])]
    if jit:
        threads[BLIND_JIT] = "Jit thread pool"
        trees.append((5, BLIND_JIT, [(jit_name(method, install or INSTALL[0]),
                                      700 + 20 * i, BLIND_JIT_MS, [])
                                     for i, method in enumerate(jit)]))

    builder = TraceProtoBuilder()
    process_tree(builder, [(APP_PID, APP_NAME, threads)])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {}
    for cpu, tid, start, end, end_state in sched:
        idle = f"swapper/{cpu}"
        by_cpu.setdefault(cpu, []).extend([
            (ms(start), next(seq), "sched", idle, 0, R, threads[tid], tid),
            (ms(end), next(seq), "sched", threads[tid], tid, end_state, idle, 0),
        ])
    for cpu, tid, tree in trees:
        for ts, order, kind, name in _flatten(tree, [], seq):
            buf = f"B|{APP_PID}|{name}\n" if kind == "B" else f"E|{APP_PID}\n"
            by_cpu.setdefault(cpu, []).append((ts, order, "print", tid, buf))
    ftrace(builder, by_cpu)
    return builder.serialize()


def nested_block() -> bytes:
    """A main thread whose longest piece of work is spread over small pieces
    (#912): a `clientTransactionExecuted` that usually takes 1 ms, once
    70 ms, holding an activity's start of 64 ms, holding a WebView's start of
    61 ms, made of ten pieces of under 6 ms with names of their own. Each
    piece is under main_thread_block's bar, and the WebView's own time is 2
    ms. What a 105 ms `performStart` of Wikipedia's article screen was, with
    Chromium's start of 59 ms in it.
    """
    pieces = [(f"WebViewChromium.step{i}", 203.5 + 6 * i, 5.9, []) for i in range(10)]
    main = [
        *[("clientTransactionExecuted", 100 + 10 * i, 1, []) for i in range(5)],
        ("clientTransactionExecuted", 200, 70, [
            ("performStart:com.example.app.PageActivity", 202, 64, [
                ("WebViewChromiumAwInit.startChromiumLockedSync", 203, 61, pieces)])]),
        ("Screen.firstFrame", 300, 5, []),
    ]
    builder = TraceProtoBuilder()
    process_tree(builder, [(APP_PID, APP_NAME, {TID_MAIN: APP_NAME[-15:]})])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {0: [
        (ms(100), next(seq), "sched", "swapper/0", 0, R, APP_NAME[-15:], TID_MAIN),
        (ms(305), next(seq), "sched", APP_NAME[-15:], TID_MAIN, S, "swapper/0", 0)]}
    for ts, order, kind, name in _flatten(main, [], seq):
        buf = f"B|{APP_PID}|{name}\n" if kind == "B" else f"E|{APP_PID}\n"
        by_cpu[0].append((ts, order, "print", TID_MAIN, buf))
    ftrace(builder, by_cpu)
    return builder.serialize()


# The threads the repeats of `blind_main` add, and how long a compilation takes.
BLIND_RENDER, BLIND_WORKER, BLIND_JIT = 4303, 4304, 4305
BLIND_JIT_MS = 6
# Two builds' install directories: Android picks both random parts anew at
# every install.
INSTALL = ("/data/app/~~kQ3v8Yw1Z0aB2c4d5e6f7g==/com.example.app-Hx2Lq9Rt4Vb7Nm1Pz3Kw5A==/",
           "/data/app/~~Tz8Wq1Ld3Ns5Ma7Kc9Je0Q==/com.example.app-Ub6Yo4Ip2Fr8Ge0Hd1Sv3B==/")
# A method both builds compile, and the one the build with the loop does: the
# loop made it hot.
JIT_BOTH = "void com.example.app.MainActivity.onCreate(android.os.Bundle)"
JIT_NEW = "void com.example.app.feature.ListViewModel.prepare()"


def jit_name(method: str, install: str) -> str:
    """What ART calls the slice of a method's JIT compilation."""
    return f"JIT compiling {method} (kind=Baseline) from {install}base.apk"


# --- the looper's idle is no wait -----------------------------------------------
#
# `looper_waits()` only, a scene of its own (#926): a start of an app that shows
# a WebView, in the shape such an app's recordings have. Between its messages
# the main thread's looper sleeps with no slice open, and Chromium's IO thread
# is what posts the next message, 210 ms of it here; the compositor's thread
# posts some too. Now and then the main thread waits for the IO thread inside a
# frame's `animation`, where WebView draws, 26 ms in three frames, and once for
# SharedPreferences inside `activityStart`, 20 ms. Only those are waits. Read
# as one, the IO thread's row was 236 ms, 89% of it idle, and ranked first.
LOOPER_IO, LOOPER_VIZ, LOOPER_PREFS = 4311, 4312, 4313
LOOPER_THREADS = {TID_MAIN: APP_NAME[-15:], LOOPER_IO: "Chrome_IOThread",
                  LOOPER_VIZ: "VizWebView", LOOPER_PREFS: "SharedPreferenc"}
LOOPER_MAIN = [
    ("bindApplication", 100, 30, []),
    ("activityStart", 140, 60, []),
    *[("Choreographer#doFrame", at, 30, [("animation", at + 2, 18, [])])
      for at in (300, 400, 500)],
    ("Screen.firstFrame", 600, 5, []),
]
# The main thread's sleeps: (from, to, the thread that woke it).
LOOPER_SLEEPS = [
    (150, 170, LOOPER_PREFS),   # inside activityStart: a wait
    (205, 295, LOOPER_IO),      # no slice open: the idle looper
    (305, 315, LOOPER_IO),      # inside animation: a wait
    (335, 395, LOOPER_VIZ),     # idle
    (405, 413, LOOPER_IO),      # a wait
    (435, 495, LOOPER_IO),      # idle
    (505, 513, LOOPER_IO),      # a wait
    (535, 595, LOOPER_IO),      # idle
]


def looper_waits() -> bytes:
    """The scene above: `com.example.app` from `bindApplication` to the end of
    `Screen.firstFrame`, 505 ms, the main thread on CPU 0 between its sleeps
    and each thread that wakes it on a CPU of its own for the millisecond
    before."""
    builder = TraceProtoBuilder()
    process_tree(builder, [(APP_PID, APP_NAME, LOOPER_THREADS)])
    seq = iter(range(10**6))
    main = LOOPER_THREADS[TID_MAIN]
    by_cpu: dict[int, list] = {0: []}
    on = 100
    for start, end, waker in LOOPER_SLEEPS:
        by_cpu[0] += [(ms(on), next(seq), "sched", "swapper/0", 0, R, main, TID_MAIN),
                      (ms(start), next(seq), "sched", main, TID_MAIN, S, "swapper/0", 0)]
        cpu, name = 1 + [*LOOPER_THREADS].index(waker), LOOPER_THREADS[waker]
        by_cpu.setdefault(cpu, []).extend([
            (ms(end - 1), next(seq), "sched", f"swapper/{cpu}", 0, R, name, waker),
            (ms(end) - 1, next(seq), "waking", waker, TID_MAIN, main, 0),
            (ms(end), next(seq), "sched", name, waker, S, f"swapper/{cpu}", 0),
        ])
        on = end
    by_cpu[0] += [(ms(on), next(seq), "sched", "swapper/0", 0, R, main, TID_MAIN),
                  (ms(610), next(seq), "sched", main, TID_MAIN, S, "swapper/0", 0)]
    for ts, order, kind, name in _flatten(LOOPER_MAIN, [], seq):
        buf = f"B|{APP_PID}|{name}\n" if kind == "B" else f"E|{APP_PID}\n"
        by_cpu[0].append((ts, order, "print", TID_MAIN, buf))
    ftrace(builder, by_cpu)
    return builder.serialize()


# --- a pool under one name -------------------------------------------------------
#
# `pool()` only, a scene of its own (#511). The kernel keeps fifteen characters
# of a thread's name, so three coroutine workers arrive as `DefaultDispatch`,
# and every detector that groups by thread name adds them up. Each waits 20 ms
# for a monitor, is preempted for 30 ms and runs 150 ms with no slice around
# it: one row per detector, of three threads, 60, 90 and 450 ms — the last
# longer than the 395 ms window it was measured in.
#
# The main thread waits 30 ms for the monitor and is preempted for 25, its
# work all inside `AppStart`. Beside it a native worker that never named
# itself, and so carries the process's name as the main thread does: it waits
# 25 ms for a monitor and runs 60 ms with no slice. By name the two were one
# row, and the worker's blind CPU was held to the main thread's bar and read
# as the main thread's.
POOL_TIDS = (4501, 4502, 4503)
POOL_NATIVE = 4510
POOL_THREADS = {TID_MAIN: APP_NAME[-15:], POOL_NATIVE: APP_NAME[-15:],
                **{tid: "DefaultDispatch" for tid in POOL_TIDS}}
POOL_WAIT = ("monitor contention with owner Thread-9 (4600) at void "
             "com.example.Cache.load()(Cache.java:20) waiters=2 blocking from "
             "void com.example.Cache.get()(Cache.java:9)")


def pool() -> bytes:
    """The scene above: `com.example.app`'s start from `AppStart` to the end
    of `Screen.firstFrame`, 395 ms, with three workers of one name, the main
    thread, and a worker that carries the main thread's name."""
    slices = {
        TID_MAIN: [("AppStart", 0, 400, [(POOL_WAIT, 100, 30, []),
                                         ("Screen.firstFrame", 390, 5, [])])],
        POOL_NATIVE: [("Lock contention on a monitor lock (owner tid: 4100)", 60, 25, [])],
        **{tid: [(POOL_WAIT, 50, 20, [])] for tid in POOL_TIDS},
    }
    sched = [(0, TID_MAIN, 0, 100, S), (0, TID_MAIN, 130, 200, R),
             (0, TID_MAIN, 225, 400, S),
             (4, POOL_NATIVE, 20, 60, S), (4, POOL_NATIVE, 85, 105, S)]
    for cpu, tid in enumerate(POOL_TIDS, start=1):
        sched += [(cpu, tid, 10, 50, S), (cpu, tid, 70, 150, R), (cpu, tid, 180, 210, S)]

    builder = TraceProtoBuilder()
    config(builder, frames=False)
    process_tree(builder, [(APP_PID, APP_NAME, POOL_THREADS)])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {}
    for cpu, tid, start, end, end_state in sched:
        idle = f"swapper/{cpu}"
        by_cpu.setdefault(cpu, []).extend([
            (ms(start), next(seq), "sched", idle, 0, R, POOL_THREADS[tid], tid),
            (ms(end), next(seq), "sched", POOL_THREADS[tid], tid, end_state, idle, 0),
        ])
    cpu_of = {tid: cpu for cpu, tid, *_ in sched}
    for tid, tree in slices.items():
        for ts, order, kind, name in _flatten(tree, [], seq):
            buf = f"B|{APP_PID}|{name}\n" if kind == "B" else f"E|{APP_PID}\n"
            by_cpu[cpu_of[tid]].append((ts, order, "print", tid, buf))
    ftrace(builder, by_cpu)
    return builder.serialize()


# --- a journey a command drives --------------------------------------------------
#
# `journey()` only, a scene of its own (#615). `com.example.app` is running
# when the recording starts, and a command taps an item that opens a screen.
# What the journey begins with is short: two input events, a few milliseconds
# each, then the platform's pause, start and resume of the activities. A
# worker's 1400 ms sync and the frames before the tap are the longest slices,
# and the start was in none of them.
#
# Android writes each input event twice: as an async span from the moment the
# window queued it, and as the main thread's section that delivers it. Both
# are named `deliverInputEvent …`, with the event's own numbers, and the span
# begins first.
#
# Beside the app, a macrobenchmark's runner, `com.example.app.benchmark`,
# which the mask `com.example.app*` matches as well: it binds an application
# and starts an activity of its own, before the app's first slice. Neither is
# the app's, and `analyze` never sees them (#727).
JOURNEY_RUNNER_PID, JOURNEY_RUNNER_TID = 5100, 5101
JOURNEY_RUNNER = "com.example.app.benchmark"
JOURNEY_WORKER = 4201
JOURNEY_THREADS = {TID_MAIN: APP_NAME[-15:], JOURNEY_WORKER: "DefaultDispatch"}
JOURNEY_FIRST_MS = 40          # the app's first slice: a window with no start opens here
JOURNEY_TAP_MS = 650           # the tap's input span is queued
JOURNEY_END = "Detail.shown"


def _input(at: float, event_id: int) -> str:
    """An input event's name as ViewRootImpl writes it: the touchscreen's
    source, the time the event was made, and its id."""
    return (f"deliverInputEvent src=0x1002 eventTimeNano={ms(at)} "
            f"id=0x{event_id:x}")


def journey() -> bytes:
    """The scene above: the tap at 650 ms opens a screen, shown at 905 ms."""
    down, up = _input(JOURNEY_TAP_MS, 0x5E1A2B), _input(720, 0x2C3D4E)
    main = [
        ("Choreographer#doFrame 7001", JOURNEY_FIRST_MS, 12, [("traversal", 42, 8, [])]),
        ("Choreographer#doFrame 7002", 200, 10, [("traversal", 201, 7, [])]),
        (down, JOURNEY_TAP_MS + 0.5, 3, []),
        (up, 720.5, 4, []),
        ("activityPause", 730, 6, []),
        ("activityStart", 745, 80, [("inflate", 750, 50, [])]),
        ("activityResume", 830, 5, []),
        ("Choreographer#doFrame 7003", 840, 40, [("traversal", 842, 35, [])]),
        (JOURNEY_END, 900, 5, []),
    ]
    worker = [("sync_settings", 60, 1400, [])]
    runner = [("bindApplication", 0, 25, []), ("activityStart", 30, 5, [])]

    builder = TraceProtoBuilder()
    config(builder, frames=False)
    process_tree(builder, [
        (APP_PID, APP_NAME, JOURNEY_THREADS),
        (JOURNEY_RUNNER_PID, JOURNEY_RUNNER,
         {JOURNEY_RUNNER_PID: JOURNEY_RUNNER[-15:], JOURNEY_RUNNER_TID: "roidJUnitRunner"}),
    ])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {}
    for cpu, pid, tid, tree in ((0, APP_PID, TID_MAIN, main), (1, APP_PID, JOURNEY_WORKER, worker),
                                (2, JOURNEY_RUNNER_PID, JOURNEY_RUNNER_TID, runner)):
        for ts, order, kind, name in _flatten(tree, [], seq):
            buf = f"B|{pid}|{name}\n" if kind == "B" else f"E|{pid}\n"
            by_cpu.setdefault(cpu, []).append((ts, order, "print", tid, buf))
    for cookie, (name, start, dur) in enumerate(((down, JOURNEY_TAP_MS, 4),
                                                 (up, 720, 5)), start=1):
        by_cpu[0] += [(ms(start), next(seq), "print", TID_MAIN, f"S|{APP_PID}|{name}|{cookie}\n"),
                      (ms(start + dur), next(seq), "print", TID_MAIN,
                       f"F|{APP_PID}|{name}|{cookie}\n")]
    ftrace(builder, by_cpu)
    return builder.serialize()


# --- the recording's own account of itself ---------------------------------------
#
# The tracing service writes the config a recording ran with at the head of
# its trace, and trace_processor prints it back as `trace_config_pbtxt`. It is
# the one place a trace says what it asked for: whether the atrace categories
# a detector reads were among them, whether composable names were, whether a
# sampler was. A trace with no such packet says nothing, and `needs` then
# takes nothing it cannot see for granted.

# What `collect` records by default, `DEFAULT_CATEGORIES` in runner.py.
RECORDED_CATEGORIES = ("am", "wm", "gfx", "view", "dalvik", "binder_driver",
                       "res", "database")
SCHED_EVENTS = ("sched/sched_switch", "sched/sched_waking",
                "sched/sched_process_exit", "sched/sched_process_free",
                "task/task_newtask", "task/task_rename")
# `ENVIRONMENT_EVENTS` in runner.py.
STATE_EVENTS = ("power/cpu_frequency", "sched/sched_blocked_reason",
                "thermal/thermal_temperature", "thermal/cdev_update")


def config(builder, *, categories=RECORDED_CATEGORIES, environment: bool = True,
           frames: bool = True, names: bool = False, sampling: bool = False,
           package: str = APP_NAME) -> None:
    """The config a recording like this one ran with, as one packet.

    One, because trace_processor keeps the last it meets: a second packet
    for the sampler, or for the track_event source, replaced the first and
    left the recording saying less than it asked for.
    """
    asked = builder.add_packet().trace_config
    ftrace_source = asked.data_sources.add().config
    ftrace_source.name = "linux.ftrace"
    ftrace_source.ftrace_config.ftrace_events.extend(
        [*SCHED_EVENTS, *(STATE_EVENTS if environment else ())])
    ftrace_source.ftrace_config.atrace_categories.extend(categories)
    ftrace_source.ftrace_config.atrace_apps.append(package)
    stats = asked.data_sources.add().config
    stats.name = "linux.process_stats"
    stats.process_stats_config.scan_all_processes_on_start = True
    if frames:
        asked.data_sources.add().config.name = "android.surfaceflinger.frametimeline"
    if names:
        source = asked.data_sources.add().config
        source.name = "track_event"
        source.track_event_config.disabled_categories.append("*")
        source.track_event_config.enabled_categories.append("rendering")
    if sampling:
        perf = asked.data_sources.add().config
        perf.name = "linux.perf"
        perf.target_buffer = 1
        perf.perf_event_config.timebase.frequency = SAMPLING_HZ
        perf.perf_event_config.callstack_sampling.kernel_frames = False


def build(frames: bool = True, environment: bool = True,
          sampling: str | None = None, names: bool = False,
          crowded: bool = False, categories=None, composables: bool = True,
          fully_drawn: str | None = None, stuffed: bool = False,
          smooth: bool = False, answered: bool = True, heap: bool = True,
          activities: tuple[str, ...] = (), boot: bool = False,
          drawing: bool = False, realtime_ns: int | None = None) -> bytes:
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
    recording. (The README's sample report comes from echolot/demo.py.)

    `names=True` writes the config `runner.compose_names` records with, a
    `track_event` source for Compose's category. The composables themselves
    are in the trace either way: they are slices like any other. Unless
    `composables=False`, a recording of an app that writes none, or whose
    names nobody switched on.

    `categories` is the atrace categories the recording's config asks for,
    `RECORDED_CATEGORIES` — what `collect` records — by default. Only the
    config changes: the slices a category writes stay, and what a detector
    needs of the trace is the config's word on it (see needs.py).

    `crowded=True` puts the main thread in the CPU queue behind the app's own
    thread and another process's, and more of the app's threads in the
    queue at once than there are cores (see `_crowded`).

    `fully_drawn` plants a `reportFullyDrawn()` call: `"app"`, `"system_server"`
    or `"early"` (see `_fully_drawn`). `None` by default: the app never calls
    it.

    `stuffed=True` draws the app's frames from `STUFFED_FRAMES`, a list that
    runs a frame behind, in place of `APP_FRAMES`; `smooth=True` from
    `SMOOTH_FRAMES`, a build that keeps up.

    `answered=False` writes the main thread's binder transactions as plain
    atrace slices, the same names and times with no server behind them, and
    `heap=False` leaves out the runtime's heap counter: what a trace from an
    older device, or from a tool that recorded less, gives the standard
    library to read (#514).

    `activities` plants a `performCreate:<class>` slice on the main thread for
    each class named, in order, the way Android 12 and on trace an activity's
    creation: a launch that ended on the last of them (#909). None by
    default, as on an older phone. `boot=True` plants the app's
    `BOOT_COMPLETED` receiver on the main thread inside the launch, as
    Android 15 runs it on the first start after a force-stop (#910).

    `drawing=True` gives every idle stretch of the window to SurfaceFlinger
    and system_server, the processes that draw a launch (see `_drawing`).

    `realtime_ns` is the wall clock at the trace's clock's zero, written as
    the clock snapshot a phone's tracing service writes at the head of every
    trace: what says when a recording was made (#848). None by default: no
    snapshot, as in a trace written by a tool that keeps none.
    """
    sched, threads, other_threads = list(SCHED), dict(THREADS), dict(OTHER_THREADS)
    if crowded:
        sched, threads, other_threads = _crowded(sched, threads, other_threads)
    sf_threads, ss_threads = SF_THREADS, SS_THREADS
    if drawing:
        sched, sf_threads, ss_threads = _drawing(sched)
    builder = TraceProtoBuilder()
    config(builder, categories=RECORDED_CATEGORIES if categories is None else categories,
           environment=environment, frames=frames, names=names, sampling=bool(sampling))
    if realtime_ns is not None:
        snapshot = builder.add_packet().clock_snapshot
        snapshot.clocks.add(clock_id=6, timestamp=0)  # BOOTTIME, the trace's clock
        snapshot.clocks.add(clock_id=1, timestamp=realtime_ns)  # REALTIME
    process_tree(builder, [(APP_PID, APP_NAME, threads),
                           (OTHER_PID, OTHER_NAME, other_threads),
                           (SF_PID, SF_NAME, sf_threads),
                           (SS_PID, SS_NAME, ss_threads),
                           (KS_PID, KS_NAME, KS_THREADS)])

    if frames:
        _frames(builder, STUFFED_FRAMES if stuffed else SMOOTH_FRAMES if smooth else APP_FRAMES)
    if sampling:
        _samples(builder, arrived=sampling in ("arrived", "minified"),
                 minified=sampling == "minified")

    # Collect ftrace events per CPU.
    by_cpu: dict[int, list] = {}
    tid_to_cpu = {tid: cpu for cpu, tid, *_ in sched}
    tid_to_tgid = {tid: APP_PID for tid in threads}
    tid_to_tgid.update({tid: OTHER_PID for tid in other_threads})
    names = dict(threads)
    names.update(other_threads)
    names.update(sf_threads)
    names.update(ss_threads)

    seq = iter(range(10**6))

    # sched_switch: entering and leaving Running.
    for cpu, tid, start, end, end_state in sched:
        idle = f"swapper/{cpu}"
        by_cpu.setdefault(cpu, []).append(
            (ms(start), next(seq), "sched", idle, 0, R, names[tid], tid)
        )
        by_cpu[cpu].append(
            (ms(end), next(seq), "sched", names[tid], tid, end_state, idle, 0)
        )

    # sched_waking: who ended a sleep.
    for at, waker, wakee in WAKINGS:
        by_cpu[tid_to_cpu[waker]].append(
            (ms(at) - 1, next(seq), "waking", waker, wakee, names[wakee], tid_to_cpu[wakee]))

    # atrace print: the slices.
    all_slices = dict(SLICES)
    if not composables:
        all_slices = {tid: _without_composables(tree) for tid, tree in all_slices.items()}
    all_slices.update(OTHER_SLICES)
    drawn_app, drawn_seen = _fully_drawn(fully_drawn) if fully_drawn else ([], [])
    created = [(f"performCreate:{cls}", at, CREATED_MS, [])
               for cls, at in zip(activities, CREATED_AT_MS, strict=False)]
    received = [(BOOT_RECEIVER, BOOT_AT_MS, BOOT_MS, [])] if boot else []
    for tid, tree_slices in all_slices.items():
        if tid == TID_MAIN:
            # After the thread's own: atrace nests by time, so an early call
            # lands inside `AppStart` wherever it is written here.
            tree_slices = [*tree_slices, *drawn_app, *created, *received]
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

    # The binder driver's events: a call, its arrival, the answer, and the
    # answer's arrival back at the caller. The server's side on CPU 0, as any
    # other process's here; the method it names is an atrace slice inside its
    # reply, as an AIDL server writes it.
    if answered:
        for n, (tid, start, dur, server_pid, server_tid, method) in enumerate(BINDER_CALLS):
            call, answer = 2 * n + 1, 2 * n + 2
            cpu = tid_to_cpu[tid]
            by_cpu.setdefault(cpu, []).append(
                (ms(start), next(seq), "binder", tid, call, server_pid, server_tid, False))
            by_cpu.setdefault(0, []).append(
                (ms(start + 0.1), next(seq), "received", server_tid, call))
            if method:
                by_cpu[0].append((ms(start + 0.2), next(seq), "print", server_tid,
                                  f"B|{server_pid}|{method}\n"))
                by_cpu[0].append((ms(start + dur - 0.2), next(seq), "print", server_tid,
                                  f"E|{server_pid}\n"))
            by_cpu[0].append(
                (ms(start + dur - 0.1), next(seq), "binder", server_tid, answer,
                 tid_to_tgid[tid], tid, True))
            by_cpu[cpu].append((ms(start + dur), next(seq), "received", tid, answer))
    else:
        # A recording whose binder slices came without the driver's events:
        # the same two names and times, as atrace writes any slice, and
        # nothing to say who answered.
        for tid, start, dur, *_ in BINDER_CALLS:
            for at, buf in ((start, f"B|{tid_to_tgid[tid]}|binder transaction\n"),
                            (start + dur, f"E|{tid_to_tgid[tid]}\n")):
                by_cpu.setdefault(tid_to_cpu[tid], []).append(
                    (ms(at), next(seq), "print", tid, buf))

    # The runtime's heap size, from the collector's thread.
    if heap:
        for at, kb in HEAP_KB:
            by_cpu.setdefault(tid_to_cpu[TID_HEAP], []).append(
                (ms(at), next(seq), "print", TID_HEAP, f"C|{APP_PID}|Heap size (KB)|{kb}\n"))

    # atrace counters: the ANR record, from system_server rather than from us.
    for at, name in ANR_COUNTERS:
        by_cpu.setdefault(0, []).append(
            (ms(at), next(seq), "print", SS_PID, f"C|{SS_PID}|{name}|1\n")
        )

    # system_server's record of `reportFullyDrawn()`, on its own thread.
    for at, order, kind, name in _flatten(drawn_seen, [], seq):
        buf = f"B|{SS_PID}|{name}\n" if kind == "B" else f"E|{SS_PID}\n"
        by_cpu.setdefault(0, []).append((at, order, "print", SS_PID, buf))

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
    # than of a core, so it goes on CPU 0 like any other global event. Why a
    # task blocked is one of the same events (`ENVIRONMENT_EVENTS` in
    # runner.py), so a recording with `runner.environment: false` has none of
    # it, and `io_wait` has no disk flag to read.
    if environment:
        for tid, at, io in BLOCKED_REASON:
            by_cpu.setdefault(tid_to_cpu[tid], []).append(
                (ms(at), next(seq), "blocked", tid, io))
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
    (prev_comm, prev_pid, prev_state, next_comm, next_pid), `waking` (waker
    tid, wakee tid, wakee's name, wakee's CPU), `blocked` (tid, io_wait),
    `freq` (cpu, khz), `temp` (zone, millidegrees), `cdev` (device, level),
    `binder` (tid, debug id, the process and thread it goes to, whether it is
    an answer), `received` (tid, debug id) and `print` (tid, atrace line).
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
            elif kind == "waking":
                _, _, _, waker, wakee, wakee_comm, wakee_cpu = item
                event.pid = waker
                wk = event.sched_waking
                wk.comm = wakee_comm
                wk.pid = wakee
                wk.prio = 120
                wk.success = 1
                wk.target_cpu = wakee_cpu
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
            elif kind == "binder":
                _, _, _, tid, debug_id, to_proc, to_thread, reply = item
                event.pid = tid
                txn = event.binder_transaction
                txn.debug_id = debug_id
                txn.to_proc = to_proc
                txn.to_thread = to_thread
                txn.reply = int(reply)
            elif kind == "received":
                _, _, _, tid, debug_id = item
                event.pid = tid
                event.binder_transaction_received.debug_id = debug_id
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
