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

THREADS = {
    TID_MAIN: "m.example.app",  # Linux truncates comm to 15 characters
    TID_WORKER: "DefaultDispatcher-worker-1",
    TID_HEAP: "HeapTaskDaemon",
    TID_OKHTTP: "OkHttp Dispatcher",
    TID_INSTR: "WellInstrumented",
    TID_BLOCKED: "BlockingIO-1",
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
    TID_MAIN: [
        # BEFORE the window — main_thread_block must not see it
        ("Bootstrap_OUTSIDE", 0, 50, []),
        # The start anchor. Wraps the whole scenario, as on a real startup.
        ("AppStart", 100, 1006, [
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

# --- the scheduler ---------------------------------------------------------
# (cpu, tid, start_ms, end_ms, state_after_being_switched_out)
#   0 → R (ready but preempted), 1 → S (sleeping)
#
# One CPU per thread — simpler, and realistic for a multicore device.

R, S = 0, 1

SCHED = [
    # main: two chunks, both deliberately crossing the window bounds
    # [100, 1105]. The first starts BEFORE the window opens, the second ends
    # AFTER it closes. This checks whether context.sql clips intervals to the
    # window or merely filters them by their start point.
    (0, TID_MAIN, 50, 600, S),
    (0, TID_MAIN, 610, 1300, S),

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

    # the foreign process
    (5, OTHER_PID, 200, 700, S),
]


def _flatten(slices, out, seq):
    """Unrolls the slice tree into B/E events with correct nesting."""
    for name, start, dur, children in slices:
        out.append((ms(start), next(seq), "B", name))
        _flatten(children, out, seq)
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


def build(frames: bool = True) -> bytes:
    """The fixture trace. `frames=False` leaves out the frame timeline.

    Android 11 and below, and any trace recorded without the
    android.surfaceflinger.frametimeline data source, have no frame timeline
    at all. That is the common case for a while yet, and frame_jank has to
    meet it with silence rather than an error.
    """
    builder = TraceProtoBuilder()

    # ProcessTree is the only source of process names.
    packet = builder.add_packet()
    packet.timestamp = BASE_NS
    tree = packet.process_tree
    for pid, name, threads in (
        (APP_PID, APP_NAME, THREADS),
        (OTHER_PID, OTHER_NAME, OTHER_THREADS),
        (SF_PID, SF_NAME, SF_THREADS),
    ):
        proc = tree.processes.add()
        proc.pid = pid
        proc.ppid = 1
        proc.cmdline.append(name)
        for tid, tname in threads.items():
            thread = tree.threads.add()
            thread.tid = tid
            thread.tgid = pid
            thread.name = tname

    if frames:
        _frames(builder)

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
            else:
                _, _, _, tid, buf = item
                event.pid = tid
                event.print.buf = buf

    return builder.serialize()


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    out = Path(argv[0]) if argv else Path("fixture.perfetto-trace")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build())
    print(f"→ {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
