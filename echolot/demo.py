"""A trace that reads like an app: the one the README's sample report is rendered from.

The self-check's fixture is a test, and reads as one: a problem planted for
every detector, under names that say so — `DiskWaiter`, `LockWaiter`,
`Input dispatching timed out (fixture)`. This is the other kind of synthetic
trace. It is the cold start of an app, a little over a second long, with the
handful of findings a real one has, under the names the README uses:
`com.example.app`, a `StoreRepository` whose lock the main thread waits on
while a coroutine worker holds it, burning CPU with no slices; a list whose
items take long to inflate, one of them far longer now and then; frames that
missed their deadline.

Five repeats, each a little different, the way a device gives them. A variant
with one change planted: the worker holds the lock longer, and the main thread
waits longer for it. And a small source tree, so that the rows that name code
find it.

    python -m echolot.demo <dir>             the traces, echolot.yml and the sources
    python -m echolot.demo <dir> --changed   the same, with the change planted

`docs/assets/render.py` renders the README's sample report from it, and
`tests/test_doc_samples.py` holds the sample to it, numbers and all. It is
built with the fixture's packet writers, and nothing in it is a self-check:
`doctor` runs the fixture only.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .fixture import FT, TraceProtoBuilder, _span, ftrace, ms, process_tree, sys_stats

APP_PID = 12903
APP_NAME = "com.example.app"
TID_MAIN = APP_PID
TID_RENDER = 12925
TID_WORKER = 12931
TID_DISK = 12940
THREADS = {
    TID_MAIN: APP_NAME,
    TID_RENDER: "RenderThread",
    # The kernel keeps fifteen characters of a thread's name, and every worker
    # of the coroutine pool arrives as this.
    TID_WORKER: "DefaultDispatch",
    TID_DISK: "arch_disk_io_1",
}
CPU = {TID_MAIN: 0, TID_RENDER: 1, TID_WORKER: 2, TID_DISK: 3}
LAYER = f"{APP_NAME}/{APP_NAME}.MainActivity#0"
SS_PID = 1495

RUNS = 5
TRACE = "coldStart_iter{:03d}.perfetto-trace"
# How each repeat differs from the first: how long the work on the main thread
# takes, and whether the list's one slow inflate happened. A device gives a
# spread like this, and the report is a median over it.
PACE = (1.0, 0.94, 1.07, 0.98, 1.12)
SLOW_INFLATE = (False, True, False, True, False)
# The clock the app ran on, by repeat: the Device line's spread.
KHZ = (1_478_400, 1_536_000, 1_401_600, 1_574_400, 1_324_800)

# The lock's two sides, as ART writes them into its contention slice: the
# worker updating the store, and the main thread reading it.
OWNER = "DefaultDispatcher-worker-3"
UPDATE = ("void com.example.app.data.StoreRepository.update(com.example.app.data.Item)"
          "(StoreRepository.kt:30)")
FIND = ("com.example.app.data.Item com.example.app.data.StoreRepository.find(long)"
        "(StoreRepository.kt:61)")

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app

scenario:
  name: coldStart
  start: {name: bindApplication}
  end: {name: collection_load}

domains:
  - slice: "collection_load"
    module: ":feature:collection"
  - slice: "collection_mapping"
    module: ":feature:collection"
    hint: "CollectionMapper.kt — entity to domain"
"""

# The checkout the rows name: the store, where the contention slice says the
# lock is taken and waited for, at the lines it says.
SOURCES = {
    "feature/collection/src/main/java/com/example/app/data/StoreRepository.kt": """\
package com.example.app.data

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * The collection, kept in memory and written through to the database.
 *
 * Every read and every write takes the same lock: [update] holds it while it
 * merges what the network sent, and [find] waits for it on the main thread.
 */
class StoreRepository(
    private val dao: ItemDao,
    private val scope: CoroutineScope,
) {
    private val items = LinkedHashMap<Long, Item>()

    fun refresh(fresh: List<Item>) {
        scope.launch(Dispatchers.Default) {
            fresh.forEach { update(it) }
        }
    }

    @Synchronized
    fun update(item: Item) {
        val merged = items[item.id]?.mergedWith(item) ?: item
        items[item.id] = merged
        dao.upsert(merged)
        recomputeIndexes(merged)
    }

    private fun recomputeIndexes(item: Item) {
        byCategory.getOrPut(item.category) { mutableListOf() }.add(item.id)
        bySeller.getOrPut(item.seller) { mutableListOf() }.add(item.id)
    }

    private val byCategory = HashMap<String, MutableList<Long>>()
    private val bySeller = HashMap<String, MutableList<Long>>()

    fun observeCount(): Int = items.size

    fun categories(): Set<String> = byCategory.keys

    fun sellers(): Set<String> = bySeller.keys

    fun clear() {
        items.clear()
        byCategory.clear()
        bySeller.clear()
    }

    fun size(): Int = items.size

    fun isEmpty(): Boolean = items.isEmpty()

    fun ids(): List<Long> = items.keys.toList()

    @Synchronized
    fun find(id: Long): Item? {
        return items[id] ?: dao.find(id)?.also { items[id] = it }
    }
}
""",
    "feature/collection/src/main/java/com/example/app/data/Item.kt": """\
package com.example.app.data

data class Item(val id: Long, val category: String, val seller: String, val title: String) {
    fun mergedWith(other: Item): Item = copy(title = other.title)
}
""",
}


# --- laying a thread's time out -----------------------------------------------
#
# A thread's time is a list, laid out end to end. A slice is (name, ms,
# [children]) and runs on a CPU, or (name, ms, [children], "S") for one the
# thread sleeps through; ("~S", ms), ("~D", ms) and ("~R", ms) are time off a
# CPU with no slice — asleep, blocked in the kernel, waiting for a CPU — and
# (".", ms) is time on a CPU with no slice. A slice's children are laid out
# from its start, and the slice lasts at least as long as they do.

def _lay(items, at: float, slices: list, off: list) -> float:
    for item in items:
        name, dur = item[0], item[1]
        if name.startswith("~"):
            off.append((at, dur, name[1:]))
            at += dur
        elif name == ".":
            at += dur
        else:
            children = item[2] if len(item) > 2 else []
            kids: list = []
            end = _lay(children, at, kids, off)
            dur = max(dur, end - at)
            if len(item) > 3:
                off.append((at, dur, item[3]))
            slices.append((name, at, dur, kids))
            at += dur
    return at


def _sched(tid: int, start: float, end: float, off: list) -> list:
    """The thread on its CPU from `start` to `end`, except where it was off it.

    A thread leaves one state for another only by running: from waiting for a
    CPU it cannot fall asleep without one. So two stretches off a CPU that
    meet get a moment on it between them, of no length, or the first one's
    state would run on through both.
    """
    out, at = [], start
    for s, dur, state in sorted(off):
        if s >= at:
            out.append((CPU[tid], tid, at, s, {"S": 1, "D": 2, "R": 0}[state]))
        at = max(at, s + dur)
    if end > at:
        out.append((CPU[tid], tid, at, end, 1))
    return out


def _contention(wait: float) -> tuple:
    """The main thread blocked on the store's lock, asleep for all of it."""
    return (f"monitor contention with owner {OWNER} ({TID_WORKER}) at {UPDATE} "
            f"waiters=0 blocking from {FIND}", 0,
            [(f"Lock contention on a monitor lock (owner tid: {TID_WORKER})", wait, [], "S")])


def _frame(vsync: int, measure: list, layout: list, draw: float) -> tuple:
    """One frame: measure, layout and draw, as ViewRootImpl traces them; a
    phase with nothing in it is not traced at all."""
    phases = [(name, 0, kids) for name, kids in (("measure", measure), ("layout", layout)) if kids]
    return (f"Choreographer#doFrame {vsync}", 0, [
        ("traversal", 0, [*phases, ("draw", draw, [])]),
    ])


def _text(n: int, each: float) -> list:
    return [("TextLayout:initLayout", each, []) for _ in range(n)]


# --- the main thread -------------------------------------------------------------

def _main(pace: float, slow: bool, changed: bool) -> list:
    """bindApplication, the Activity, the list's first frames, the wait for the collection."""
    def p(v: float) -> float:
        return round(v * pace, 2)

    def lock(v: float) -> tuple:
        return _contention(round(v * pace * (1.4 if changed else 1.0), 2))

    # The list's items, three a frame for the first four frames. One of them,
    # now and then, reads its layout from disk on the main thread: the
    # outlier.
    items = [("inflate", p(12.9), []) for _ in range(12)]
    if slow:
        items[4] = ("inflate", 86.2, [(".", 30.0), ("~D", 31.0), (".", 25.2)])
    return [
        ("bindApplication", 0, [
            ("makeApplication", p(3.1), []),
            ("AppStartup", 0, [(".", p(5)), ("~D", p(4)), (".", p(3))]),
            ("Firebase", p(9), []),
            (".", p(2)),
        ]),
        ("~S", p(3)),
        ("activityStart", 0, [
            ("inflate", 0, [
                (".", p(5)), ("~D", p(14)),
                ("inflate", p(8.5), []), (".", p(2)), ("inflate", p(8.2), []),
                (".", p(3)), ("~R", p(3.3)),
            ]),
            (".", p(4)),
        ]),
        ("~S", p(2)),
        ("activityResume", p(11), []),
        ("~S", p(6)),
        _frame(9985934, _text(12, p(1.45)), [
            ("RV CreateView", 0, items[0:3]),
            ("RV OnBindView", 0, [(".", p(2)), lock(22.4), (".", p(2))]),
        ], p(34.0)),
        ("~S", p(4)),
        _frame(9985941, _text(10, p(1.45)), [
            ("RV CreateView", 0, items[3:6]),
            ("RV OnBindView", 0, [lock(8.1), (".", p(1)), lock(6.2)]),
        ], p(27.0)),
        ("~R", p(5)),
        (".", p(1)),
        ("~S", p(12)),
        _frame(9985950, _text(10, p(1.45)), [
            ("RV CreateView", 0, items[6:9]),
            ("RV OnBindView", 0, [lock(5.3), (".", p(1)), lock(4.9)]),
        ], p(24.0)),
        ("~S", p(15)),
        _frame(9985957, _text(10, p(1.45)), [
            ("RV CreateView", 0, items[9:12]),
            ("RV OnBindView", 0, [lock(4.6), (".", p(1)), lock(3.8), (".", p(1)), lock(3.4)]),
        ], p(22.0)),
        ("~D", p(18)),
        (".", p(1)),
        ("~S", p(40)),
        _frame(9985972, _text(3, p(1.45)), [
            ("RV OnBindView", 0, [lock(2.9)]),
        ], p(5.0)),
        ("~D", p(25)),
        (".", p(1)),
        ("~S", p(150)),
        _frame(9985990, _text(3, p(1.45)), [], p(4.6)),
        ("~R", p(5)),
        (".", p(1)),
        ("~D", p(30)),
        (".", p(1)),
        ("~S", p(260)),
        _frame(9986021, _text(3, p(1.45)), [], p(4.2)),
    ]


# --- one repeat ------------------------------------------------------------------

def build(run: int = 0, changed: bool = False) -> bytes:
    """One repeat of the cold start: `run` from 0 to RUNS - 1, `changed` with the change planted."""
    pace, slow = PACE[run], SLOW_INFLATE[run]
    start = 100.0
    main: list = []
    off: list = []
    end = _lay(_main(pace, slow, changed), start, main, off)
    frames = [s for s in main if s[0].startswith("Choreographer#doFrame")]
    locks = sorted(k for s in main for k in _walk(s) if k[0].startswith("monitor contention"))

    # The worker holds the lock through every wait the main thread has on it,
    # merging what the network sent; nothing on it is traced.
    worker_from = locks[0][1] - 40.0 * pace
    worker_to = locks[-1][1] + locks[-1][2] + 2.0
    # The render thread draws each frame after the main thread hands it over.
    render: list = []
    for name, at, dur, _ in frames:
        vsync = name.rsplit(" ", 1)[1]
        render.append((f"DrawFrames {vsync}", at + dur, round(dur * 0.12 + 1.5, 2), []))
    # The collection: loaded from the second frame on, mapped on Room's thread,
    # and shown with the last frame, which is where the scenario ends.
    load_from = frames[1][1]
    mapping_at = frames[1][1] + 150.0 * pace
    mapping = ("collection_mapping", mapping_at, round(118.3 * pace, 2),
               [("collection_query", mapping_at, round(22.3 * pace, 2), [])])

    builder = TraceProtoBuilder()
    process_tree(builder, [(APP_PID, APP_NAME, THREADS),
                           (SS_PID, "system_server", {SS_PID: "system_server"})])

    by_cpu: dict[int, list] = {}
    seq = iter(range(10**6))
    sched = (_sched(TID_MAIN, start, end, off)
             + [(CPU[TID_WORKER], TID_WORKER, worker_from, worker_to, 1)]
             + [(CPU[TID_RENDER], TID_RENDER, at, at + dur, 1) for _, at, dur, _ in render]
             + [(CPU[TID_DISK], TID_DISK, mapping[1], mapping[1] + mapping[2], 1)])
    for cpu, tid, a, b, state in sched:
        idle = f"swapper/{cpu}"
        by_cpu.setdefault(cpu, []).append((ms(a), next(seq), "sched", idle, 0, 0, THREADS[tid], tid))
        by_cpu[cpu].append((ms(b), next(seq), "sched", THREADS[tid], tid, state, idle, 0))
    for tid, slices in ((TID_MAIN, main), (TID_RENDER, render), (TID_DISK, [mapping])):
        for ts, order, kind, name in _events(slices, seq):
            buf = f"B|{APP_PID}|{name}\n" if kind == "B" else f"E|{APP_PID}\n"
            by_cpu.setdefault(CPU[tid], []).append((ts, order, "print", tid, buf))
    for a, b, buf in ((load_from, end, "collection_load|1"),):
        by_cpu[CPU[TID_MAIN]].append((ms(a), next(seq), "print", TID_MAIN, f"S|{APP_PID}|{buf}\n"))
        by_cpu[CPU[TID_MAIN]].append((ms(b), next(seq), "print", TID_MAIN, f"F|{APP_PID}|{buf}\n"))
    # The launch as system_server writes it on Android 13: from the intent,
    # 38 ms before bindApplication, to the frame that shows the collection.
    launch = "launchingActivity#7"
    for at, buf in ((start - 38.0, f"S|{SS_PID}|{launch}|7"), (end, f"F|{SS_PID}|{launch}|7"),
                    (end, f"B|{SS_PID}|{launch}:completed:{APP_NAME}"), (end, f"E|{SS_PID}")):
        by_cpu.setdefault(4, []).append((ms(at), next(seq), "print", SS_PID, buf + "\n"))
    # The device: its clock on the app's cores, its temperature, its memory.
    for cpu in CPU.values():
        by_cpu.setdefault(cpu, []).append((ms(50), next(seq), "freq", cpu, KHZ[run]))
    for at, milli_c in ((60, 47_000 + 1_000 * run), (end - 20, 52_000 + 600 * run)):
        by_cpu.setdefault(4, []).append((ms(at), next(seq), "temp", "cpu-therm", milli_c))
    ftrace(builder, by_cpu)
    sys_stats(builder, [(60, 1_572_000 - 9_000 * run, 41_000), (end - 10, 1_514_000 - 9_000 * run, 41_520)])

    # The frame timeline: each frame's deadline and what it took, the main
    # thread's part and the render thread's together. Between them, while the
    # main thread waits for the collection, the render thread animates the
    # progress indicator on its own, a frame every 33 ms and every one on time.
    cookies = iter(range(10**5, 10**6))
    timeline = [(at, round(r_at + r_dur - at, 2)) for (_, at, _, _), (_, r_at, r_dur, _)
                in zip(frames, render, strict=True)]
    busy = [(at, at + dur) for at, dur in timeline]
    spinner = frames[4][1] + frames[4][2] + 40.0
    while spinner < end - 40.0:
        if not any(a - 20 < spinner < b + 20 for a, b in busy):
            timeline.append((round(spinner, 2), 6.4))
        spinner += 33.3
    for token, (at, actual) in enumerate(sorted(timeline), start=1):
        jank = FT.JANK_APP_DEADLINE_MISSED if actual > 16.7 + 4 else FT.JANK_NONE
        surface = dict(token=token, display_frame_token=token + 1000, pid=APP_PID, layer_name=LAYER)
        _span(builder, cookies, "expected_surface_frame_start", at, 16.7, **surface)
        _span(builder, cookies, "actual_surface_frame_start", at, actual, **surface,
              jank_type=jank,
              present_type=FT.PRESENT_ON_TIME if jank == FT.JANK_NONE else FT.PRESENT_LATE,
              on_time_finish=jank == FT.JANK_NONE, gpu_composition=False,
              prediction_type=FT.PREDICTION_VALID)
    return builder.serialize()


def _walk(s: tuple):
    yield s
    for kid in s[3]:
        yield from _walk(kid)


def _events(slices: list, seq) -> list:
    """B and E events for a laid-out slice tree, a child's inside its parent's."""
    out = []
    for name, at, dur, kids in slices:
        out.append((ms(at), next(seq), "B", name))
        out.extend(_events(kids, seq))
        out.append((ms(at + dur), next(seq), "E", name))
    return out


# --- on disk -------------------------------------------------------------------

def write(root: Path, changed: bool = False) -> list[Path]:
    """The repeats as traces in `root`, with echolot.yml and the sources beside them."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    for path, text in SOURCES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    traces = []
    for run in range(RUNS):
        trace = root / TRACE.format(run)
        trace.write_bytes(build(run, changed))
        traces.append(trace)
    return traces


def report(changed: bool = False, tp_binary: str | None = None) -> dict:
    """The repeats analysed and merged the way `analyze` does, places included."""
    # Late imports: `python -m echolot.demo <dir>` only writes traces, which
    # needs the fixture alone, and need not load the CLI to do it.
    from . import place
    from . import report as report_mod
    from .config import Config
    from .main import analyze_trace

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        traces = write(root, changed)
        cfg = Config.load(root / "echolot.yml")
        merged = report_mod.aggregate([analyze_trace(t, cfg, tp_binary) for t in traces])
        place.annotate(merged, root)
        merged["traces"] = [Path(t).name for t in merged.get("traces") or []]
        merged["trace"] = Path(merged["trace"]).name
        merged["config"] = {"path": "/home/you/my-app/echolot.yml", "sha": cfg.sha,
                            "local": None, "defaults": False, "set": None}
        return merged


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    changed = "--changed" in args
    args = [a for a in args if a != "--changed"]
    if len(args) != 1:
        print("usage: python -m echolot.demo <dir> [--changed]", file=sys.stderr)
        return 2
    for trace in write(Path(args[0]), changed):
        print(f"→ {trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
