#!/usr/bin/env python3
"""A row summed over a pool says how many threads it adds up, and the main
thread's row is `main` (#511).

What `analyze` reads off a trace is held by the self-check on the fixture's
pool scene ("a row over a pool says how many threads it adds up …"). Here:
what the merge of repeats, the report's views, the plain reading and
`compare` make of the new column and the new name.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import plain  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report, row  # noqa: E402


def pooled(location: str, ms: float, threads: int | None) -> dict:
    r = row(location, ms, metric="total_ms", detail="monitor contention with owner Thread-9")
    if threads is not None:
        r["threads"] = threads
    return r


def contention(*rows: dict) -> dict:
    return report([det("monitor_contention", list(rows))],
                  window={"process": "com.example.app", "duration_ms": 400.0})


def test_the_merge_keeps_the_most_threads_any_repeat_found() -> None:
    merged = report_mod.merge_rows([[pooled("DefaultDispatch", 60.0, 3)],
                                    [pooled("DefaultDispatch", 50.0, 2)]],
                                   ("location",), 2)
    check("the pool is as large as the most threads a repeat found",
          merged[0]["threads"] == 3, merged)
    check("the time is the median, as before", merged[0]["total_ms"] == 55.0, merged)


def test_a_numbered_pool_is_one_row_of_all_its_threads(monkeypatch, tmp_path: Path) -> None:
    """#845: one recording said `Threads 1` for a pool whose row added up many
    workers, since the report folded the names after the query. The detector
    groups by the fold now, so the row counts every worker, and one recording
    says what a set of it says."""
    from echolot import fixture, selftest
    from echolot.analyze import analyze_trace
    from echolot.config import Config
    numbered = {tid: f"pool-1-thread-{n}" for n, tid in enumerate(fixture.POOL_TIDS, start=1)}
    monkeypatch.setattr(fixture, "POOL_THREADS", {**fixture.POOL_THREADS, **numbered})
    trace = tmp_path / "pool.perfetto-trace"
    trace.write_bytes(fixture.pool())
    analysed = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    rows = next(d for d in analysed["detectors"] if d["id"] == "uninstrumented_cpu")["rows"]
    pool = [r for r in rows if r["location"] == "pool-#-thread-#"]
    check("the pool is one row, of its three workers",
          len(pool) == 1 and pool[0]["threads"] == 3, rows)
    import copy
    one = report_mod.aggregate([copy.deepcopy(analysed)])
    three = report_mod.aggregate([copy.deepcopy(analysed) for _ in range(3)])

    def pool_of(rep: dict) -> dict:
        det = next(d for d in rep["detectors"] if d["id"] == "uninstrumented_cpu")
        return next(r for r in det["rows"] if r["location"] == "pool-#-thread-#")
    check("one recording and a set say the same threads",
          pool_of(one)["threads"] == pool_of(three)["threads"] == 3,
          (pool_of(one), pool_of(three)))


def test_a_row_from_before_the_column_merges_without_it() -> None:
    merged = report_mod.merge_rows([[pooled("DefaultDispatch", 60.0, None)]], ("location",), 1)
    check("no `threads` is invented for a report that did not count them",
          "threads" not in merged[0], merged)


def test_the_report_says_what_a_pool_row_adds_up() -> None:
    rep = contention(pooled("DefaultDispatch", 3800.0, 8), pooled("main", 120.0, 1))
    md = report_mod.to_markdown(rep)
    check("the table has the column", "| Where | Threads |" in md, md)
    check("and says what a row of several is",
          "_Threads: a row adds up every thread of its name — `DefaultDispatch` 8." in md, md)
    view = report_mod.detector_view(rep, "monitor_contention")
    check("so does the one detector's view", "`DefaultDispatch` 8" in view, view)
    over = report_mod.overview(rep)
    check("and the overview's top row", "DefaultDispatch (8 threads)" in over, over)


def test_no_note_where_every_row_is_one_thread() -> None:
    md = report_mod.to_markdown(contention(pooled("main", 120.0, 1),
                                           pooled("LockWaiter", 50.0, 1)))
    check("the column without the note", "| Threads |" in md and "_Threads:" not in md, md)


def test_the_main_thread_is_main_and_an_older_report_s_is_its_kernel_name() -> None:
    ctx = plain._Ctx(contention())
    check("`main` is the main thread", plain._on_main(
        "monitor_contention", pooled("main", 1.0, 1), ctx))
    check("a row from before #511 under the kernel's name is too", plain._on_main(
        "monitor_contention", pooled("com.example.app", 1.0, None), ctx))
    check("a worker that carries the process's name is not", not plain._on_main(
        "monitor_contention", pooled("com.example.app", 1.0, 1), ctx))


def test_the_plain_reading_counts_the_threads_of_a_pool() -> None:
    rep = contention(pooled("DefaultDispatch", 3800.0, 8), pooled("main", 120.0, 1))
    said = plain.render(rep, width=400)
    check("the pool's wait is eight threads' together",
          "`DefaultDispatch` (8 threads together) waited 3.8 s for a lock" in said, said)


def test_compare_pairs_an_older_main_row_with_main() -> None:
    before = contention(pooled("DefaultDispatch", 3800.0, None),
                        pooled("com.example.app", 120.0, None))
    after = contention(pooled("DefaultDispatch", 3800.0, 8), pooled("main", 120.0, 1))
    got = {r["location"]: r["change"] for r in compare(before, after)["rows"]}
    check("one row each, and neither gone nor new",
          got == {"DefaultDispatch": "steady", "main": "steady"}, got)


def test_a_newer_report_s_worker_under_the_process_name_stays_itself() -> None:
    both = contention(pooled("main", 120.0, 1), pooled("com.example.app", 25.0, 1))
    got = sorted(r["location"] for r in compare(both, both)["rows"])
    check("a report that counts threads is read as it is",
          got == ["com.example.app", "main"], got)


def test_the_page_says_a_bar_is_a_pool_s_time_together() -> None:
    from echolot.pages import report_page
    mark, _ = report_page.chart_row(pooled("DefaultDispatch", 3800.0, 8),
                                    plain._Ctx(contention()), [], False)
    check("under the bar, and in its tip",
          "8 threads together" in mark["notes"] and "threads added up: 8" in mark["tip"], mark)
    one, _ = report_page.chart_row(pooled("main", 120.0, 1), plain._Ctx(contention()), [], False)
    check("and nothing on one thread", not any("threads" in n for n in one["notes"]), one)


def test_an_older_main_row_under_the_bar_is_found_as_main() -> None:
    """A report written before #511 but after #715 keeps what it found under
    its bar in `below`, the main thread's row there under the kernel's name.
    The newer side's `main` row is looked up there, and found."""
    old = report([{**det("monitor_contention", []),
                   "below": {"floor_ms": 5.0,
                             "rows": [pooled("com.example.app", 6.0, None)]}}],
                 window={"process": "com.example.app", "duration_ms": 400.0})
    got = compare(old, contention(pooled("main", 120.0, 1)))["rows"]
    check("one row, from under the bar before",
          [(r["location"], r.get("under_bar")) for r in got] == [("main", "before")], got)
