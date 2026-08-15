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

            # The other side of GC: an allocation on main stalled waiting for
            # the collector.
            ("waitWhileAllocatingLocked", 700, 60, []),
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


def build() -> bytes:
    builder = TraceProtoBuilder()

    # ProcessTree is the only source of process names.
    packet = builder.add_packet()
    packet.timestamp = BASE_NS
    tree = packet.process_tree
    for pid, name, threads in (
        (APP_PID, APP_NAME, THREADS),
        (OTHER_PID, OTHER_NAME, OTHER_THREADS),
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
