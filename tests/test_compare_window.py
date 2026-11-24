#!/usr/bin/env python3
"""`compare` tests the scenario's window, and the platform's startup, the way it
tests a row (#532).

The window was two medians and their difference: "5700 → 5310 ms (−390,
×0.93)" with no word on whether the runs' own scatter covers that. A merged
report now keeps each run's window and startup, and `compare` gives both the
row's `change`, `shift` and `holds`. A report written before it kept them
compares as it did, and says its move is untested.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report import one, row  # noqa: E402

# Eight cold starts of one build, as far apart as a quiet phone's are.
WINDOWS = (2010.0, 2135.0, 1960.0, 2080.0, 2045.0, 1925.0, 2110.0, 1990.0)


def runs(windows, plus: float = 0.0, *, startup: bool = True, at: int = 0) -> list[dict]:
    out = []
    for i, ms in enumerate(windows):
        r = one([row("work", count=1, self_ms=40.0, total_ms=40.0, max_ms=40.0)],
                det_id="main_thread_block")
        r["trace"] = f"t_iter{at + i:03d}.perfetto-trace"
        r["window"]["duration_ms"] = ms + plus
        if startup:
            r["window"]["startup"] = {
                "type": "cold", "dur_ms": ms + plus - 300.0,
                "reasons": {"bind_application": round(ms + plus - 1700.0, 2),
                            "io": 60.0 + i % 3, "binder": 20.0}}
        out.append(r)
    return out


def side(windows, plus: float = 0.0, **kw) -> dict:
    return report_mod.aggregate(runs(windows, plus, **kw))


def window_line(cmp: dict) -> str:
    return next(x for x in compare_mod.to_markdown(cmp).splitlines()
                if x.startswith("Scenario window"))


def compared(before: dict, after: dict) -> dict:
    return compare_mod.build(before, after, before_path="before.json", after_path="after.json")


def test_a_merged_report_keeps_each_runs_window_in_the_order_of_its_traces() -> None:
    merged = side(WINDOWS)
    check("every run's window, as recorded", merged["window"]["values"] == list(WINDOWS),
          merged["window"])
    s = merged["window"]["startup"]
    check("and its startup's", s["values"] == [ms - 300.0 for ms in WINDOWS], s)
    check("and each reason's, by run",
          s["reason_values"]["io"] == [60.0, 61.0, 62.0] * 2 + [60.0, 61.0], s["reason_values"])
    check("a single run keeps none: its window is its one value",
          "values" not in report_mod.aggregate(runs(WINDOWS[:1]))["window"], merged["window"])


def test_a_startup_reason_one_run_did_not_have_counts_as_nothing_in_it() -> None:
    reps = runs(WINDOWS[:3])
    del reps[1]["window"]["startup"]["reasons"]["binder"]
    s = report_mod.aggregate(reps)["window"]["startup"]
    check("as the median counts it", s["reason_values"]["binder"] == [20.0, 0.0, 20.0]
          and s["reasons"]["binder"] == 20.0, s)


def test_one_build_against_itself_does_not_hold() -> None:
    cmp = compared(side(WINDOWS[0::2]), side(WINDOWS[1::2], at=4))
    w = cmp["window"]
    check("the window's move has a range, and it runs through zero",
          w["shift"]["low_ms"] < 0 < w["shift"]["high_ms"] and w["holds"] is False, w)
    check("and so does the startup's", cmp["startup"]["holds"] is False, cmp["startup"])
    line = window_line(cmp)
    check("the line says so in the Holds column's words",
          line.startswith("Scenario window: **2027.5 → 2035.0 ms**  (+7.5, ×1.0) · holds: no, "),
          line)


def test_a_build_slower_than_its_runs_scatter_holds() -> None:
    cmp = compared(side(WINDOWS[0::2]), side(WINDOWS[1::2], 400.0, at=4))
    w = cmp["window"]
    check("the window grew, and the move holds clear of zero",
          w["change"] == "grew" and w["holds"] is True and w["shift"]["low_ms"] > 200.0, w)
    page = compare_mod.to_markdown(cmp)
    check("the window's line carries the range",
          "Scenario window: **2027.5 → 2435.0 ms**  (+407.5, ×1.2) · holds: yes, +" in page, page)
    st = cmp["startup"]
    check("the startup holds beside it", st["holds"] is True and st["delta_ms"] == 407.5, st)
    by = {r["reason"]: r for r in st["reasons"]}
    check("and its reasons are judged as rows: the one that grew holds, the rest are steady",
          by["bind_application"]["holds"] is True and by["io"]["change"] == "steady"
          and by["binder"]["change"] == "steady", by)
    check("the reason that moved is in a table of its own",
          "## Startup by reason" in page and "| `bind_application` |" in page
          and "| `io` |" not in page, page)
    check("and not among the detectors' rows",
          all(r["detector"] == "main_thread_block" for r in cmp["rows"]), cmp["rows"])


def test_a_report_without_per_run_windows_is_compared_as_before_and_said_untested() -> None:
    old = side(WINDOWS[0::2])
    del old["window"]["values"], old["window"]["startup"]["values"]
    cmp = compared(old, side(WINDOWS[1::2], 400.0, at=4))
    w = cmp["window"]
    check("the medians' difference, as ever", w["delta_ms"] == 407.5 and w["ratio"] == 1.2, w)
    check("no range, no verdict, and the side named",
          w["shift"] is None and w["holds"] is None and w["untested"] == ["before"], w)
    line = window_line(cmp)
    check("the line says why", line.endswith(
        "· untested: the before report keeps no per-run values; `analyze` the traces "
        "again to test the move"), line)


def test_a_startup_on_one_side_only_is_not_compared() -> None:
    cmp = compared(side(WINDOWS[0::2]), side(WINDOWS[1::2], at=4, startup=False))
    check("no startup block", cmp["startup"] is None, cmp["startup"])
    check("and no startup line", "Startup (platform)" not in compare_mod.to_markdown(cmp),
          compare_mod.to_markdown(cmp))


def test_two_kinds_of_startup_are_named_on_the_line() -> None:
    after = side(WINDOWS[1::2], at=4)
    after["window"]["startup"]["type"] = "warm"
    line = next(x for x in compare_mod.to_markdown(compared(side(WINDOWS[0::2]), after))
                .splitlines() if x.startswith("Startup"))
    check("cold against warm is said", "ms** (cold → warm)" in line, line)


def test_too_few_runs_leave_the_window_without_a_verdict() -> None:
    cmp = compared(side(WINDOWS[:3]), side(WINDOWS[3:6], 400.0, at=3))
    w = cmp["window"]
    check("three a side give no range", w["shift"] is None and w["holds"] is None
          and "untested" not in w, w)
    line = window_line(cmp)
    check("and the line ends at the move", line.endswith("(+435.0, ×1.22)"), line)


def test_two_apps_give_the_window_no_verdict() -> None:
    after = side(WINDOWS[1::2], 400.0, at=4)
    after["window"]["process"] = "com.example.other"
    cmp = compared(side(WINDOWS[0::2]), after)
    check("not comparable, and the window's move holds nothing",
          cmp["comparable"] is False and cmp["window"]["holds"] is None
          and cmp["startup"]["holds"] is None
          and all(r["holds"] is None for r in cmp["startup"]["reasons"]), cmp["window"])


def test_one_side_that_does_not_say_which_echolot_wrote_it_gives_the_window_no_verdict() -> None:
    before, after = side(WINDOWS[0::2]), side(WINDOWS[1::2], 400.0, at=4)
    for rep, version in ((before, "0.18.0"), (after, None)):
        rep["toolchain"] = {"echolot": version} if version else {}
    cmp = compared(before, after)
    check("the tool may account for the move, as for every row's",
          cmp["window"]["holds"] is None and cmp["window"]["delta_ms"] == 407.5, cmp["window"])


def test_the_window_is_listed_by_the_rows_floor_and_holds_against_zero() -> None:
    """A row's floor decides whether the window moved, and its range has to
    clear zero, not that floor: the window is one number tested once, and a
    tenth of a five-second launch is half a second no set of runs could
    clear."""
    before = side(WINDOWS[0::2])
    after = copy.deepcopy(before)
    after["traces"] = [f"other{i}" for i in range(4)]
    after["window"]["values"] = [v + 200.0 for v in before["window"]["values"]]
    after["window"]["duration_ms"] += 200.0
    w = compared(before, after)["window"]
    check("200 ms on a 2027.5 ms window is under the floor: steady",
          w["change"] == "steady" and w["delta_ms"] == 200.0, w)
    check("and every run after is past every run before: it holds",
          w["holds"] is True and w["shift"]["low_ms"] > 0, w)
