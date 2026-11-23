"""`monitor_contention`'s evidence names both methods when the row has a wait that does (#510).

ART writes a wait in two shapes: `monitor contention with owner <thread>
(<tid>) at <owner's method> … blocking from <waiter's method>`, usually with
`Lock contention on a monitor lock (owner tid: N)` inside it, and sometimes
the short one alone. On a real cold start a thread's longest wait, 890 ms,
was a short one alone, and the row's evidence, taken from the longest wait,
said `owner tid: 30788` and nothing else, though its other waits named both
methods. Here a thread waits twice: 40 ms in the long shape and 90 ms in the
short one alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, place  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402

PID = 7300
APP = "com.example.app"
WAITER, OWNER, OTHER = 7301, 7302, 7303
THREADS = {PID: APP, WAITER: "DefaultDispatch", OWNER: "DefaultDispatcher-worker-2",
           OTHER: "LoneWaiter"}
CONFIG = {"project": {"package": APP, "process": APP}, "scenario": {"name": "locks"}}

LONG = ("monitor contention with owner DefaultDispatcher-worker-2 (7302) at void "
        "com.example.Store.update(java.lang.String)(Store.java:3) waiters=0 "
        "blocking from java.lang.Object com.example.Store.find()(Store.java:4)")
SHORT = f"Lock contention on a monitor lock (owner tid: {OWNER})"

# What each thread wrote: (name, start ms, length ms, children).
SLICES = {
    PID: [("AppStart", 0, 400, [])],
    WAITER: [("loadFeed", 10, 300, [
        (LONG, 20, 40, [(SHORT, 21, 38, [])]),
        (SHORT, 100, 90, []),
    ])],
    # What the owner was doing while it held the lock.
    OWNER: [("Store.updateForRegion", 90, 180, [])],
    # A thread with the short shape only: its evidence is that, with what
    # the trace knows of the moment.
    OTHER: [("refresh", 200, 100, [(SHORT, 210, 30, [])])],
}


def _trace(path: Path) -> Path:
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [(PID, APP, THREADS)])
    seq = iter(range(10**6))
    by_cpu: dict[int, list] = {}
    for cpu, (tid, slices) in enumerate(SLICES.items()):
        for ts, order, kind, name in fixture._flatten(slices, [], seq):
            buf = f"B|{PID}|{name}\n" if kind == "B" else f"E|{PID}\n"
            by_cpu.setdefault(cpu, []).append((ts, order, "print", tid, buf))
    fixture.ftrace(builder, by_cpu)
    path.write_bytes(builder.serialize())
    return path


def _rows(tmp_path: Path) -> dict[str, dict]:
    rep = analyze_trace(_trace(tmp_path / "locks.perfetto-trace"), Config(CONFIG))
    det = next(d for d in rep["detectors"] if d["id"] == "monitor_contention")
    check("monitor_contention ran", det["error"] is None, det["error"])
    src = tmp_path / "checkout" / "com" / "example"
    src.mkdir(parents=True)
    (src / "Store.java").write_text(
        "package com.example;\n"
        "public class Store {\n"
        "    public void update(String key) { }\n"
        "    public Object find() { return null; }\n"
        "}\n", encoding="utf-8")
    place.annotate(rep, tmp_path / "checkout", None)
    return {r["location"]: r for r in det["rows"]}


def test_the_evidence_is_the_wait_that_names_both_methods(tmp_path: Path) -> None:
    row = _rows(tmp_path)["DefaultDispatch"]
    check("both waits, and the longest is the true one",
          (row["count"], row["max_ms"], row["total_ms"]) == (2, 90.0, 130.0), row)
    check("the evidence is the long shape", row["detail"] == LONG, row["detail"])
    check("which places both methods in the code",
          [p["symbol"] for p in row.get("places") or []]
          == ["com.example.Store.update", "com.example.Store.find"], row.get("places"))


def test_the_short_shape_alone_says_who_held_the_lock(tmp_path: Path) -> None:
    row = _rows(tmp_path)["LoneWaiter"]
    check("the owner's tid stays, and its thread, what it was in and where the "
          "waiter waited come after it",
          row["detail"] == f"{SHORT} · owner DefaultDispatcher-worker-2 in "
                           "Store.updateForRegion · waiting in refresh", row["detail"])
