#!/usr/bin/env python3
"""`main_thread_waits`: the main thread's sleeps, by the thread of the app
that ended each one (#128).

The fixture holds one row's worth, and the self-check pins it. Here every
rule gets a sleep of its own on one main thread: blocked inside its work
until SharedPreferences let it go, idle until Room's threads and a pool's
woke it, and the sleeps the detector leaves to others — a lock's wait, and
the ones `RenderThread`, a binder thread, another process or nobody ended.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402

PID = 7200
APP = "com.example.app"
THREADS = {PID: APP, 7201: "arch_disk_io_0", 7202: "arch_disk_io_1",
           7203: "SharedPreferenc", 7204: "RenderThread", 7205: f"binder:{PID}_2",
           7206: "pool-14-thread-"}
SYSTEM = 1000
SYSTEM_THREADS = {SYSTEM: "system_server", 1001: "android.display"}
CPU = {tid: i + 1 for i, tid in enumerate([*THREADS][1:] + [1001])}
# No anchors: the window is the process's slices, `AppStart` from 0 to 240,
# which spans it and so is no place to wait in.
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "waits"}}

SLICES = [("AppStart", 0, 240, [
    ("bindApplication", 10, 40, []),
    ("Lock contention on a monitor lock (owner tid: 7202)", 60, 10, []),
])]

# The main thread's sleeps: (from, to, who woke it, or None).
SLEEPS = [
    (20, 40, 7203),     # blocked in bindApplication until SharedPreferences loaded
    (50, 58, 7201),     # idle, until Room's thread posted
    (60, 70, 7202),     # in a lock's contention: monitor_contention's
    (100, 110, 7202),   # idle again, Room's other thread
    (120, 130, 7204),   # RenderThread: the frame pipeline
    (140, 150, 7205),   # a binder thread: a reply, binder_txn's
    (160, 170, 1001),   # another process: not the app's to change
    (180, 190, None),   # nobody recorded as waking it
    (200, 217, 7206),   # idle, a pool's thread with two digits in its name
]


def _trace(path: Path) -> Path:
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [(PID, APP, THREADS), (SYSTEM, "system_server", SYSTEM_THREADS)])
    names = {**THREADS, **SYSTEM_THREADS}
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {0: []}
    on = 0
    for start, end, waker in SLEEPS:
        # The main thread on CPU 0 until the sleep, then off it until woken.
        by_cpu[0].append((fixture.ms(on), next(seq), "sched", "swapper/0", 0, fixture.R,
                          APP, PID))
        by_cpu[0].append((fixture.ms(start), next(seq), "sched", APP, PID, fixture.S,
                          "swapper/0", 0))
        if waker is not None:
            # The waker runs for a millisecond and wakes it a nanosecond
            # before it is back.
            cpu = CPU[waker]
            by_cpu.setdefault(cpu, []).extend([
                (fixture.ms(end - 1), next(seq), "sched", f"swapper/{cpu}", 0, fixture.R,
                 names[waker], waker),
                (fixture.ms(end) - 1, next(seq), "waking", waker, PID, APP, 0),
                (fixture.ms(end), next(seq), "sched", names[waker], waker, fixture.S,
                 f"swapper/{cpu}", 0),
            ])
        on = end
    by_cpu[0].append((fixture.ms(on), next(seq), "sched", "swapper/0", 0, fixture.R, APP, PID))
    for ts, order, kind, name in fixture._flatten(SLICES, [], seq):
        buf = f"B|{PID}|{name}\n" if kind == "B" else f"E|{PID}\n"
        by_cpu[0].append((ts, order, "print", PID, buf))
    fixture.ftrace(builder, by_cpu)
    path.write_bytes(builder.serialize())
    return path


def _analyzed(tmp_path: Path, config: dict = CONFIG, **kwargs) -> dict:
    return analyze_trace(_trace(tmp_path / "waits.perfetto-trace"), Config(config), **kwargs)


def _rows(report: dict) -> list[dict]:
    det = next(d for d in report["detectors"] if d["id"] == "main_thread_waits")
    check("main_thread_waits ran without an error", det["error"] is None, det["error"])
    return det["rows"]


def test_each_sleep_goes_to_the_thread_of_the_app_that_ended_it(tmp_path: Path) -> None:
    rows = _rows(_analyzed(tmp_path))
    got = [(r["location"], r["count"], r["total_ms"], r["max_ms"], r["detail"]) for r in rows]
    check("blocked in its work until SharedPreferences let it go",
          got[0] == ("SharedPreferenc", 1, 20.0, 20.0, "blocked in bindApplication"),
          got)
    check("idle until Room's threads woke it, the two of them one row; the lock's wait "
          "that one of them ended is not among them",
          got[1] == ("arch_disk_io_#", 2, 18.0, 10.0, "idle"), got)
    check("two digits fold to one #",
          got[2] == ("pool-#-thread-", 1, 17.0, 17.0, "idle"), got)
    check("and nothing for RenderThread, a binder thread, another process or nobody",
          len(got) == 3, got)


def test_the_bar_is_the_total(tmp_path: Path) -> None:
    rows = _rows(_analyzed(tmp_path, cli_overrides={"main_thread_waits": {"min_total_ms": 19}}))
    check("only the thread that held the main thread 19 ms or more",
          [r["location"] for r in rows] == ["SharedPreferenc"], rows)


def test_the_sleeps_count_in_the_coverage(tmp_path: Path) -> None:
    """The idle sleeps are the main thread's time that no other row stands
    for; the blocked one lies inside `bindApplication`, which
    `main_thread_block` already counts, and is counted once."""
    on = _analyzed(tmp_path)["window"]["main_thread"]
    off = _analyzed(tmp_path, {**CONFIG, "detectors": {"main_thread_waits": False}})
    off = off["window"]["main_thread"]
    check("counted, not listed as uncounted",
          "main_thread_waits" not in (on.get("in_rows_uncounted") or []), on)
    check("8 + 10 + 17 ms of idle sleep more than without it",
          round(on["in_rows_ms"] - off["in_rows_ms"], 1) == 35.0, (on, off))


def test_what_other_detectors_explain_is_left_out_whatever_the_bar(tmp_path: Path) -> None:
    """At a bar of half a millisecond every sleep that is counted is a row,
    so a sleep counted by mistake shows."""
    rows = _rows(_analyzed(tmp_path, cli_overrides={"main_thread_waits": {"min_total_ms": 0.5}}))
    check("the app's own threads, and no RenderThread, binder thread, other process "
          "or unknown waker",
          sorted(r["location"] for r in rows)
          == ["SharedPreferenc", "arch_disk_io_#", "pool-#-thread-"], rows)
    check("and the lock's wait not in Room's row",
          next(r for r in rows if r["location"] == "arch_disk_io_#")["count"] == 2, rows)
