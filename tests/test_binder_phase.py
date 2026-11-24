#!/usr/bin/env python3
"""Where in the launch the main thread's binder calls were made (#911).

On eleven cold starts of Wikipedia's app `binder_txn`'s top row was the main
thread's 70 ms, 94% of it answered by system_server, and it did not say where
in the launch the calls were made. An agent spent a round of markers looking
for them in the app's code; a third of them were `attachApplication`, inside
`ActivityThreadMain`, before any of the app's code had loaded. The row's
`phase` now says it, by the outermost of the platform's sections around each
call. The doctor checks say the same on the same trace; this is the pytest
side, with what the report prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402

SPLIT = "before the app's code 30%, Application 30%, activity 20%, frames 15%, other 5%"


def _report(tmp_path: Path, made: bool = True) -> dict:
    trace = tmp_path / f"launch-{made}.perfetto-trace"
    trace.write_bytes(fixture.launch_binder(made=made))
    return analyze_trace(trace, Config(selftest.LAUNCH_BINDER))


def _rows(report: dict) -> dict[str, dict]:
    det = next(d for d in report["detectors"] if d["id"] == "binder_txn")
    assert not det.get("error"), det
    return {r["thread"]: r for r in det["rows"]}


def test_the_main_threads_calls_are_split_by_where_in_the_launch(tmp_path: Path) -> None:
    rows = _rows(_report(tmp_path))
    main = rows["main thread"]
    check("the split, in launch order, by share of the row's time",
          main["phase"] == SPLIT, main)
    check("the numbers are the slices' as before",
          (main["count"], main["total_ms"], main["max_ms"]) == (6, 80.0, 24.0), main)
    check("a background thread's calls are not asked where",
          rows["background"]["phase"] is None, rows["background"])


def test_without_make_application_the_platform_has_only_its_own_section(tmp_path: Path) -> None:
    main = _rows(_report(tmp_path, made=False))["main thread"]
    check("the 4 ms in bindApplication go to the Application, not the platform",
          main["phase"] == "before the app's code 25%, Application 35%, activity 20%, "
                           "frames 15%, other 5%", main)


def test_a_section_of_the_apps_around_the_calls_is_looked_through(marker_report) -> None:
    # The fixture's two calls sit inside `AppStart`, a section of the app's
    # that wraps the whole scenario, and inside none of the platform's.
    row = selftest.only_row(marker_report, "binder_txn")
    check("in none of the launch's sections", row["phase"] == "other 100%", row)


def test_the_report_prints_the_split_in_full(tmp_path: Path) -> None:
    # `detail` is cut at 100 characters in the report; the split is a column
    # of its own, and is not.
    md = report_mod.to_markdown(_report(tmp_path))
    head = next(line for line in md.splitlines()
                if line.startswith("| Where | Threads |") and "| phase |" in line)
    check("the table has a phase column", head.endswith("| thread | phase |"), head)
    check("and the main thread's row has the whole split", f"| {SPLIT} |" in md, md[:2000])
