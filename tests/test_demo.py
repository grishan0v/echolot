#!/usr/bin/env python3
"""The demo app — `echolot/demo.py`: a cold start that reads like an app.

Its report is the README's sample, held to the last digit by
`tests/test_doc_samples.py`. Here: that the traces are the same every time
and differ from repeat to repeat, that the report tells the story it was
built to tell, and that the planted change is what `compare` puts on top.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare, demo  # noqa: E402
from tests.support import check  # noqa: E402


def _rows(report: dict, det_id: str) -> list[dict]:
    return next(d for d in report["detectors"] if d["id"] == det_id)["rows"]


def test_the_traces_are_the_same_every_time_and_differ_by_repeat() -> None:
    first = [demo.build(run) for run in range(demo.RUNS)]
    check("built twice, the same bytes", first == [demo.build(run) for run in range(demo.RUNS)])
    check("and no two repeats alike", len(set(first)) == demo.RUNS)
    check("the change is a different trace", demo.build(0, changed=True) != first[0])


def test_what_is_written_is_what_analyze_is_pointed_at(tmp_path: Path) -> None:
    traces = demo.write(tmp_path)
    check("five traces, named the way collect names them",
          [t.name for t in traces] == [f"coldStart_iter00{i}.perfetto-trace" for i in range(5)],
          traces)
    check("the config beside them", (tmp_path / "echolot.yml").read_text() == demo.CONFIG)
    check("and the sources the rows name",
          all((tmp_path / path).is_file() for path in demo.SOURCES), sorted(demo.SOURCES))


def test_the_report_tells_the_story_it_was_built_for(demo_report) -> None:
    fired = demo_report["summary"]["fired_ids"]
    check("five findings, the ones a cold start like this one has",
          fired == ["frame_jank", "main_thread_block", "main_thread_outlier",
                    "monitor_contention", "uninstrumented_cpu"], fired)
    outlier = _rows(demo_report, "main_thread_outlier")
    check("the slow inflate is an outlier, in two repeats of five",
          [(r["location"], r["runs"]) for r in outlier] == [("inflate", "2/5")], outlier)
    lock = _rows(demo_report, "monitor_contention")[0]
    check("the lock's two sides are placed in the checkout",
          lock["code"] == "owner at StoreRepository.kt:30 · blocked at StoreRepository.kt:61",
          lock)
    blind = _rows(demo_report, "uninstrumented_cpu")
    check("the worker holding it runs with nothing traced",
          [r["location"] for r in blind] == ["DefaultDispatch"], blind)
    markers = [r["location"] for r in demo_report["markers"]["rows"]]
    check("the project's own markers are measured",
          markers == ["collection_load", "collection_mapping"], markers)


def test_the_planted_change_is_what_compare_puts_on_top(demo_report, demo_changed_report) -> None:
    cmp = compare.build(demo_report, demo_changed_report,
                        before_path="before/report.json", after_path="after/report.json")
    # Sorted: the two rows are one wait seen twice, they move by the same
    # amount, and which of them compare lists first is not the point.
    moved = sorted((r["detector"], r["location"], r["change"], r["holds"])
                   for r in cmp["rows"] if r["change"] != compare.STEADY)
    check("the worker holds the lock longer, the main thread waits longer for it, "
          "and nothing else moved",
          moved == [("main_thread_block",
                     f"Lock contention on a monitor lock (owner tid: {demo.TID_WORKER})",
                     compare.GREW, True),
                    ("monitor_contention", demo.APP_NAME, compare.GREW, True)], moved)
    check("and the two rounds are comparable", cmp["comparable"] is True, cmp["warnings"])


def test_the_module_writes_a_directory_and_says_how_to_call_it(tmp_path: Path) -> None:
    check("a directory", demo.main([str(tmp_path / "app")]) == 0
          and (tmp_path / "app" / "echolot.yml").is_file())
    check("the change, on request", demo.main([str(tmp_path / "changed"), "--changed"]) == 0)
    check("and without a directory, the usage", demo.main([]) == 2)
