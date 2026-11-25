"""What the other thread of a wait was doing meanwhile (#417, #418).

`monitor_contention` names the thread that waited for a lock and
`main_thread_waits` the thread that let the main thread go. Neither said
what the other side was doing, and that is what decides the fix: an owner
running holds the lock through a long section, an owner waiting for a CPU
holds it in the queue for a core, an owner in the kernel or asleep holds it
while it waits for something else. A waking thread on a CPU means the main
thread's way went through its code; anywhere else, it was held up too.

One start holds both. Two workers of one pool wait for StoreWriter at the
same moment, one in each of ART's shapes; StoreWriter is running, waiting
for a CPU, in the kernel and asleep over their waits. The main thread waits
for StoreWriter before the trace has any state of it, and sleeps inside
`loadConfig` until ConfigLoader, in each state in turn, lets it go. Lonely
waits for a thread that is not the process's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, place  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402

PID = 7400
APP = "com.example.app"
POOL_A, POOL_B, OWNER, LONELY, LOADER = 7401, 7402, 7403, 7404, 7405
THREADS = {PID: APP, POOL_A: "DefaultDispatch", POOL_B: "DefaultDispatch",
           OWNER: "StoreWriter", LONELY: "Lonely", LOADER: "ConfigLoader"}
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "start"}}

LONG = ("monitor contention with owner StoreWriter (7403) at void "
        "com.example.Store.update(java.lang.String)(Store.java:3) waiters=0 "
        "blocking from java.lang.Object com.example.Store.find()(Store.java:4)")


def _short(tid: int) -> str:
    return f"Lock contention on a monitor lock (owner tid: {tid})"


# What each thread wrote: (name, start ms, length ms, children).
SLICES = {
    PID: [("AppStart", 0, 400, [(_short(OWNER), 50, 20, []), ("loadConfig", 100, 100, [])])],
    POOL_A: [("fetch", 90, 170, [(LONG, 100, 100, [(_short(OWNER), 101, 98, [])])])],
    POOL_B: [("fetch", 120, 140, [(_short(OWNER), 130, 40, [])])],
    OWNER: [("writeAll", 40, 190, [])],
    LONELY: [("refresh", 300, 50, [(_short(9999), 310, 30, [])])],
}

# The scheduler: (cpu, ms, the thread it switches to, the one it switches
# from, the state that one is left in).
R, S, D = fixture.R, fixture.S, fixture.D
SCHED = [
    (0, 0, PID, 0, R), (0, 100, 0, PID, S), (0, 200, PID, 0, R), (0, 400, 0, PID, S),
    # StoreWriter: running from 60, waiting for a CPU 120..140, in the kernel
    # 145..155, asleep 160..175.
    (3, 60, OWNER, 0, R), (3, 120, 0, OWNER, R), (3, 140, OWNER, 0, R),
    (3, 145, 0, OWNER, D), (3, 155, OWNER, 0, R), (3, 160, 0, OWNER, S),
    (3, 175, OWNER, 0, R), (3, 220, 0, OWNER, S),
    # ConfigLoader: running 90..130, waiting for a CPU 130..150, in the kernel
    # 151..170, asleep 171..190, running until it wakes the main thread.
    (5, 90, LOADER, 0, R), (5, 130, 0, LOADER, R), (5, 150, LOADER, 0, R),
    (5, 151, 0, LOADER, D), (5, 170, LOADER, 0, R), (5, 171, 0, LOADER, S),
    (5, 190, LOADER, 0, R), (5, 200, 0, LOADER, S),
]


def _trace(path: Path, sched: list = SCHED) -> Path:
    names = {**THREADS, 0: "swapper"}
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [(PID, APP, THREADS)])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {}
    for cpu, at, tid_in, tid_out, left in sched:
        by_cpu.setdefault(cpu, []).append((fixture.ms(at), next(seq), "sched", names[tid_out],
                                           tid_out, left, names[tid_in], tid_in))
    by_cpu[5].append((fixture.ms(200) - 1, next(seq), "waking", LOADER, PID, APP, 0))
    for cpu, (tid, slices) in enumerate(SLICES.items(), start=6):
        for ts, order, kind, name in fixture._flatten(slices, [], seq):
            buf = f"B|{PID}|{name}\n" if kind == "B" else f"E|{PID}\n"
            by_cpu.setdefault(cpu, []).append((ts, order, "print", tid, buf))
    fixture.ftrace(builder, by_cpu)
    path.write_bytes(builder.serialize())
    return path


def _report(tmp_path: Path, sched: list = SCHED) -> dict:
    report = analyze_trace(_trace(tmp_path / "start.perfetto-trace", sched), Config(CONFIG))
    for d in report["detectors"]:
        if d["id"] in ("monitor_contention", "main_thread_waits"):
            check(f"{d['id']} ran", d["error"] is None, d["error"])
    return report


def _rows(report: dict, det_id: str) -> dict[str, dict]:
    det = next(d for d in report["detectors"] if d["id"] == det_id)
    return {r["location"]: r for r in det["rows"]}


# --- monitor_contention ------------------------------------------------------

OWNED = " · owner: running 46%, waiting for a CPU 21%, in the kernel 14%, asleep 18%"


def test_each_wait_is_split_by_the_owner_s_states_over_its_own_time(tmp_path: Path) -> None:
    """100..200: running 55, waiting 20, in the kernel 10, asleep 15. 130..170,
    at the same time: 10 of each. 140 ms in all. Joined against the owner's
    states in one partition, the second wait would lose what overlaps the
    first."""
    row = _rows(_report(tmp_path), "monitor_contention")["DefaultDispatch"]
    check("two waits of two workers, one row",
          (row["threads"], row["count"], row["total_ms"]) == (2, 2, 140.0), row)
    check("the long shape speaks, and the owner's shares follow it",
          row["detail"] == LONG + OWNED, row["detail"])


def test_both_frames_are_placed_with_the_shares_after_them(tmp_path: Path) -> None:
    report = _report(tmp_path)
    src = tmp_path / "checkout" / "com" / "example"
    src.mkdir(parents=True)
    (src / "Store.java").write_text(
        "package com.example;\npublic class Store {\n"
        "    public void update(String key) { }\n"
        "    public Object find() { return null; }\n}\n", encoding="utf-8")
    place.annotate(report, tmp_path / "checkout", None)
    row = _rows(report, "monitor_contention")["DefaultDispatch"]
    check("the owner's frame and the waiter's, not the waiter's with the shares in it",
          [(p["symbol"], p["line"]) for p in row.get("places") or []]
          == [("com.example.Store.update", 3), ("com.example.Store.find", 4)],
          row.get("places"))


def test_an_owner_the_trace_has_no_state_for_is_unknown(tmp_path: Path) -> None:
    rows = _rows(_report(tmp_path), "monitor_contention")
    check("before its first state, half of the main thread's wait",
          rows["main"]["detail"] == f"{_short(OWNER)} · owner StoreWriter in writeAll · "
                                    "waiting in AppStart · owner: running 50%, unknown 50%",
          rows["main"]["detail"])
    check("and an owner that is no thread of the process says nothing",
          rows["Lonely"]["detail"] == f"{_short(9999)} · waiting in refresh",
          rows["Lonely"]["detail"])


def test_a_view_that_cuts_the_evidence_keeps_the_shares(tmp_path: Path) -> None:
    view = render_mod.detector_view(_report(tmp_path), "monitor_contention")
    line = next(ln for ln in view.splitlines() if ln.startswith("| DefaultDispatch"))
    check("the slice's name gives way, the owner's state stays",
          line.endswith(f"| monitor contention with …{OWNED} |"), line)
    check("evidence with nothing at its end is cut as before",
          render_mod.clip_evidence("x" * 120, 100) == "x" * 99 + "…")
    check("and short evidence is left whole",
          render_mod.clip_evidence("blocked in a · its thread: running 100%", 100)
          == "blocked in a · its thread: running 100%")


def test_a_trace_without_the_scheduler_says_nothing_of_the_owner(tmp_path: Path) -> None:
    """The same slices, no `sched_switch`: the rows as before, no shares."""
    rows = _rows(_report(tmp_path, [(0, 0, PID, 0, R), (5, 190, LOADER, 0, R)]),
                 "monitor_contention")
    check("no row says what the owner did",
          all("owner:" not in r["detail"] for r in rows.values()), rows)
    check("and the rows are the same rows",
          sorted(rows) == ["DefaultDispatch", "Lonely", "main"], sorted(rows))


# --- main_thread_waits -------------------------------------------------------

def test_the_waking_thread_s_states_over_the_main_thread_s_sleep(tmp_path: Path) -> None:
    """100..200 asleep in `loadConfig`: ConfigLoader ran 30 + 1 + 1 + 10 ms,
    waited for a CPU 20, sat in the kernel 19 and asleep 19."""
    row = _rows(_report(tmp_path), "main_thread_waits")["ConfigLoader"]
    check("the wait, and what its thread did meanwhile",
          (row["count"], row["total_ms"], row["detail"])
          == (1, 100.0, "blocked in loadConfig · its thread: running 42%, "
                        "waiting for a CPU 20%, in the kernel 19%, asleep 19%"), row)
