#!/usr/bin/env python3
"""`main_thread_waits`: the main thread's waits, by the thread of the app
that let it go (#128).

The fixture holds one row's worth, and the self-check pins it. Here every
rule gets a sleep of its own on one main thread: blocked inside its work
until SharedPreferences, Room's threads and a pool's let it go; idle, with no
slice open, until Room's thread or WebView's IO thread posted its next
message, which is no wait (#926); and the sleeps the detector leaves to
others — a lock's wait, and the ones `RenderThread`, a binder thread, another
process or nobody ended.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402

PID = 7200
APP = "com.example.app"
THREADS = {PID: APP, 7201: "arch_disk_io_0", 7202: "arch_disk_io_1",
           7203: "SharedPreferenc", 7204: "RenderThread", 7205: f"binder:{PID}_2",
           7206: "pool-14-thread-", 7207: "Chrome_IOThread"}
SYSTEM = 1000
SYSTEM_THREADS = {SYSTEM: "system_server", 1001: "android.display"}
CPU = {tid: i + 1 for i, tid in enumerate([*THREADS][1:] + [1001])}
# No anchors: the window is the process's slices, `AppStart` from 0 to 300,
# which spans it and so is no place to wait in.
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "waits"}}

# `openDatabase`, `decodeImage` and `bindRow` are under `main_thread_block`'s
# bar, so no other row stands for the waits inside them.
SLICES = [("AppStart", 0, 300, [
    ("bindApplication", 10, 40, []),
    ("Lock contention on a monitor lock (owner tid: 7202)", 60, 10, []),
    ("openDatabase", 98, 14, []),
    ("decodeImage", 198, 12, []),
    ("bindRow", 228, 12, []),
])]

# The main thread's sleeps: (from, to, who woke it, or None).
SLEEPS = [
    (20, 40, 7203),     # blocked in bindApplication until SharedPreferences loaded
    (50, 58, 7201),     # idle: Room's thread posted the next message, no wait
    (60, 70, 7202),     # in a lock's contention: monitor_contention's
    (100, 110, 7202),   # in openDatabase until Room's other thread let it go
    (120, 130, 7204),   # RenderThread: the frame pipeline
    (140, 150, 7205),   # a binder thread: a reply, binder_txn's
    (160, 170, 1001),   # another process: not the app's to change
    (180, 190, None),   # nobody recorded as waking it
    (200, 209, 7206),   # in decodeImage, a pool's thread with two digits in its name
    (212, 226, 7207),   # idle: WebView's IO thread posted, no wait however long
    (230, 239, 7206),   # in bindRow, the pool's thread again
    (245, 290, 7207),   # idle again
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


def test_each_wait_goes_to_the_thread_of_the_app_that_ended_it(tmp_path: Path) -> None:
    rows = _rows(_analyzed(tmp_path))
    # Where it waited; what the waking thread did meanwhile is
    # test_other_thread_state's.
    got = [(r["location"], r["count"], r["total_ms"], r["max_ms"], r["detail"].split(" · ")[0])
           for r in rows]
    check("blocked in its work until SharedPreferences let it go",
          got[0] == ("SharedPreferenc", 1, 20.0, 20.0, "blocked in bindApplication"),
          got)
    check("two digits fold to one #, and the slice it waited in is named, the first "
          "by name of two it waited in as long",
          got[1] == ("pool-#-thread-", 2, 18.0, 9.0, "blocked in bindRow"), got)
    check("and nothing for Room's 10 ms under the bar, or for the threads that only woke "
          "the idle looper, or for RenderThread, a binder thread, another process or nobody",
          len(got) == 2, got)


def test_the_idle_looper_is_no_wait(tmp_path: Path) -> None:
    """WebView's IO thread ended 59 ms of the main thread's sleeps, more than
    any thread let it go, and none of them began inside a slice: it posted
    the looper's next message each time. Before #926 it was the first row."""
    rows = _rows(_analyzed(tmp_path, cli_overrides={"main_thread_waits": {"min_total_ms": 0.5}}))
    check("no row for the IO thread at any bar",
          all(r["location"] != "Chrome_IOThread" for r in rows), rows)
    room = next(r for r in rows if r["location"] == "arch_disk_io_#")
    check("and Room's row holds its wait in openDatabase, not the idle sleep it ended",
          (room["count"], room["total_ms"], room["detail"].split(" · ")[0])
          == (1, 10.0, "blocked in openDatabase"),
          room)


def test_the_bar_is_the_total(tmp_path: Path) -> None:
    rows = _rows(_analyzed(tmp_path, cli_overrides={"main_thread_waits": {"min_total_ms": 19}}))
    check("only the thread that held the main thread 19 ms or more",
          [r["location"] for r in rows] == ["SharedPreferenc"], rows)


def test_the_waits_count_in_the_coverage(tmp_path: Path) -> None:
    """The pool's waits lie in slices no other row stands for, and count
    here. SharedPreferences' lies inside `bindApplication`, which
    `main_thread_block` already counts, and is counted once. The idle sleeps
    are no row's time."""
    on = _analyzed(tmp_path)["window"]["main_thread"]
    off = _analyzed(tmp_path, {**CONFIG, "detectors": {"main_thread_waits": False}})
    off = off["window"]["main_thread"]
    check("counted, not listed as uncounted",
          "main_thread_waits" not in (on.get("in_rows_uncounted") or []), on)
    check("9 + 9 ms of the pool's waits more than without it",
          round(on["in_rows_ms"] - off["in_rows_ms"], 1) == 18.0, (on, off))


def test_what_other_detectors_explain_is_left_out_whatever_the_bar(tmp_path: Path) -> None:
    """At a bar of half a millisecond every sleep that is counted is a row,
    so a sleep counted by mistake shows."""
    rows = _rows(_analyzed(tmp_path, cli_overrides={"main_thread_waits": {"min_total_ms": 0.5}}))
    check("the app's own threads that let the main thread go, and no RenderThread, binder "
          "thread, other process, unknown waker or idle looper",
          sorted(r["location"] for r in rows)
          == ["SharedPreferenc", "arch_disk_io_#", "pool-#-thread-"], rows)
    check("and the lock's wait not in Room's row",
          next(r for r in rows if r["location"] == "arch_disk_io_#")["count"] == 1, rows)


def test_the_fixture_s_webview_start_is_read_by_its_waits(tmp_path: Path) -> None:
    """The doctor check on `fixture.looper_waits`, here too: Chromium's IO
    thread posts 210 ms to the idle looper and lets the main thread go for
    26 ms inside `animation`; the compositor's thread only posts."""
    path = tmp_path / "looper.perfetto-trace"
    path.write_bytes(fixture.looper_waits())
    cfg = Config({**CONFIG, "scenario": {"name": "webview", "start": {"name": "bindApplication"},
                                         "end": {"name": "Screen.firstFrame"}}})
    rows = _rows(analyze_trace(path, cfg))
    got = [(r["location"], r["count"], r["total_ms"], r["max_ms"], r["detail"].split(" · ")[0])
           for r in rows]
    check("the IO thread's waits in animation, then SharedPreferences', and no VizWebView",
          got == [("Chrome_IOThread", 3, 26.0, 10.0, "blocked in animation"),
                  ("SharedPreferenc", 1, 20.0, 20.0, "blocked in activityStart")], got)
