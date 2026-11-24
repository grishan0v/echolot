"""The main thread's own move above the sums over a pool it set off (#595).

Faults injected into one app, 5 cold starts against 18: the cause on the
main thread, +419 ms and +328 ms, came ninth and thirteenth, under
`monitor_contention` summed over the coroutine workers, −4.0 s and −4.3 s,
which held — the workers contended less while the main thread was held — and
under pool rows that did not hold.
`compare` now lists first a move of the main thread's own time (or the
frames') that holds and is a twentieth of the window or more, and last the
moves that do not hold. The plain reading judges a check whose rows on the
main thread moved one way and on a pool the other by the main thread's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import moves  # noqa: E402
from echolot.selftest import _cold_starts  # noqa: E402
from tests.support import check  # noqa: E402

MAIN = "com.example.app"
POOL = "DefaultDispatch"


def build(before: dict, after: dict) -> dict:
    return compare_mod.build(_cold_starts(before), _cold_starts(after),
                             before_path="a.json", after_path="b.json")


def order(cmp: dict) -> list[tuple[str, str]]:
    return [(r["detector"], r["location"]) for r in cmp["rows"] if r["change"] != "steady"]


def four(ms: float, step: float = 2.0) -> list[float]:
    return [ms, ms + step, ms - step, ms + step / 2]


WAIT_BEFORE = {("monitor_contention", MAIN): four(20.0), ("monitor_contention", POOL): four(8600.0, 80.0)}
WAIT_AFTER = {("monitor_contention", MAIN): four(350.0), ("monitor_contention", POOL): four(4300.0, 40.0)}


def test_each_row_says_whether_it_is_the_main_threads():
    cmp = build(WAIT_BEFORE, WAIT_AFTER)
    check("by the plain reading's rule", {r["location"]: r["on_main"] for r in cmp["rows"]}
          == {MAIN: True, POOL: False}, cmp["rows"])


def test_the_main_threads_move_that_holds_leads():
    check("above the pool's larger one", order(build(WAIT_BEFORE, WAIT_AFTER))[0] == ("monitor_contention", MAIN))


def test_with_too_few_runs_for_holds_the_order_is_by_size():
    before, after = (_three(_cold_starts({k: v[:3] for k, v in side.items()}))
                     for side in (WAIT_BEFORE, WAIT_AFTER))
    cmp = compare_mod.build(before, after, before_path="a.json", after_path="b.json")
    check("nothing holds at three a side, so the pool's 4.3 s is first",
          order(cmp)[0] == ("monitor_contention", POOL) and all(r["holds"] is None for r in cmp["rows"]),
          cmp["rows"])


def _three(side: dict) -> dict:
    side["runs"], side["traces"] = 3, side["traces"][:3]
    for det in side["detectors"]:
        for row in det["rows"]:
            row["runs"] = "3/3"
    return side


def test_a_small_main_thread_knock_on_does_not_bury_a_cause_off_it():
    cmp = build({("uninstrumented_cpu", "Thread-25"): four(10.0), ("runnable_starvation", MAIN): four(60.0)},
                {("uninstrumented_cpu", "Thread-25"): four(850.0), ("runnable_starvation", MAIN): four(100.0)})
    main = next(r for r in cmp["rows"] if r["location"] == MAIN)
    check("40 ms that holds, under a twentieth of 5.5 s", main["holds"] is True and main["delta_ms"] < 274)
    check("so the burning pool stays first", order(cmp)[0] == ("uninstrumented_cpu", "Thread-25"), order(cmp))


def test_a_move_that_does_not_hold_goes_after_the_rest():
    cmp = build({("runnable_starvation", POOL): [900.0, 1500.0, 600.0, 1300.0],
                 ("io_wait", "worker"): four(100.0)},
                {("runnable_starvation", POOL): [700.0, 400.0, 900.0, 600.0],
                 ("io_wait", "worker"): four(300.0)})
    check("the 200 ms that holds above the 600 ms that does not",
          [(r["location"], r["holds"]) for r in cmp["rows"]] == [("worker", True), (POOL, False)], cmp["rows"])


def test_the_frames_still_split_the_table_first():
    before = _cold_starts({("main_thread_block", "postAndWait"): four(890.0),
                           ("frame_jank", "App Deadline Missed"): four(130.0)})
    after = _cold_starts({("main_thread_block", "postAndWait"): four(400.0),
                          ("frame_jank", "App Deadline Missed"): four(4500.0, 40.0)})
    before["window"]["frames"], after["window"]["frames"] = {"count": 657}, {"count": 117}
    for side, n in ((before, (670, 5)), (after, (161, 13))):
        for det in side["detectors"]:
            det["rows"][0]["count"] = n[0] if det["id"] == "main_thread_block" else n[1]
    cmp = compare_mod.build(before, after, before_path="a.json", after_path="b.json")
    check("the late frames, against the frames, lead; the per-frame work follows",
          [r["location"] for r in cmp["rows"]] == ["App Deadline Missed", "postAndWait"], cmp["rows"])


# --- the plain reading -----------------------------------------------------------------


def test_a_check_whose_main_thread_rows_moved_against_its_pool_is_judged_by_them():
    cmp = build(WAIT_BEFORE, WAIT_AFTER)
    before, after = _cold_starts(WAIT_BEFORE), _cold_starts(WAIT_AFTER)
    item = next(c for c in moves.read(before, after, cmp)["checks"] if c["id"] == "monitor_contention")
    check("worse, by the main thread", item["movement"] == moves.WORSE
          and item["main"] == {"before": 20.5, "after": 350.5}, item)
    check("and the sentence says both", item["text"]
          == "On the main thread, what this check found went from 20 ms to 0.4 s. On the other threads, added "
             "up, it went from 8.6 s to 4.3 s.", item["text"])


def test_a_check_whose_rows_all_moved_one_way_reads_as_before():
    after = {("monitor_contention", MAIN): four(350.0), ("monitor_contention", POOL): four(11000.0, 80.0)}
    cmp = build(WAIT_BEFORE, after)
    item = next(c for c in moves.read(_cold_starts(WAIT_BEFORE), _cold_starts(after), cmp)["checks"]
                if c["id"] == "monitor_contention")
    check("worse, added up", item["movement"] == moves.WORSE and "main" not in item
          and item["text"].startswith("Added up, what this check found went from"), item)
