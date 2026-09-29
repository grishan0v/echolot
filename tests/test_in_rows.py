"""How much of the window the findings cover, each moment counted once.

The number a reader used to make by adding up the milliseconds down a report,
which counts a disk wait inside a slice twice and can pass 100% without a
single row being wrong. Each detector now says which stretches of the main
thread's time its rows stand for, in a second query after `-- @intervals`,
and `analyze` lays them on one timeline.

Three things are held here. The second query is parsed off the file without
disturbing the first. The arithmetic counts each moment once and nothing
outside the window. And every shipped detector's second query agrees with its
own rows: on the fixture, the stretches behind the rows about the main thread
add up to those rows' milliseconds — which is what keeps a query that was
written once from drifting away from the one above it.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, selftest  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import DETECTOR_DIR, _in_rows  # noqa: E402
from echolot.report import metric_of  # noqa: E402
from echolot.tp import _LIMIT_TAIL, Detector, load_detectors  # noqa: E402

MAIN = fixture.THREADS[fixture.TID_MAIN]


# --- the second query -----------------------------------------------------

DETECTOR = """\
-- @id: demo
-- @title: Demo
-- @param: min_ms = 16
-- @identity: location, detail

SELECT name AS location, thread_name AS detail, COUNT(*) AS count
FROM _slice_win
GROUP BY name, thread_name
HAVING SUM(dur) >= {{min_ms}} * 1000000
LIMIT 20;

-- @intervals
--
-- a comment of its own, with a semicolon; in it

SELECT ts, dur FROM _slice_win
WHERE dur >= {{min_ms}} * 1000000 AND name IN (SELECT location FROM _rows);
"""


def test_the_second_query_comes_off_the_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.sql"
    path.write_text(DETECTOR, encoding="utf-8")
    d = Detector.from_file(path)

    check("the first query ends where the marker begins",
          "@intervals" not in d.sql and "_rows" not in d.sql, d.sql)
    check("and still ends in its LIMIT, which calibrate strips",
          _LIMIT_TAIL.search(d.sql) is not None, d.sql[-80:])
    check("so the measuring pass has none",
          "LIMIT" not in d.render_open().upper(), d.render_open()[-80:])
    sql, params = d.render()
    check("the first renders as before", ">= 16 * 1000000" in sql, sql)
    second = d.render_intervals(params)
    check("the second takes the same thresholds",
          second is not None and "dur >= 16 * 1000000" in second, str(second))
    check("and a threshold moved for the first moves for the second",
          "dur >= 3 * 1000000" in (d.render_intervals(d.render({"min_ms": 3})[1])
                                    or ""))


def test_a_detector_without_one_says_so(tmp_path: Path) -> None:
    path = tmp_path / "demo.sql"
    path.write_text(DETECTOR.split("-- @intervals")[0], encoding="utf-8")
    d = Detector.from_file(path)
    check("no second query is None, not an empty result",
          d.render_intervals(d.render()[1]) is None)


def test_every_shipped_detector_has_one() -> None:
    """And none of them lost its LIMIT to the split."""
    for d in load_detectors(DETECTOR_DIR):
        check(f"{d.id}: a second query", d.intervals_sql.strip(), d.id)
        check(f"{d.id}: the first still ends in its LIMIT",
              _LIMIT_TAIL.search(d.sql) is not None, d.sql[-120:])


# --- counting each moment once ---------------------------------------------

def ms(value: float) -> int:
    return int(value * 1_000_000)


# Ten milliseconds, from one second into the trace.
WINDOW = {"ts_start": ms(1000), "ts_end": ms(1010)}


def test_each_moment_is_counted_once() -> None:
    spans = [
        (ms(1002), ms(3)),     # 1002..1005
        (ms(1004), ms(2)),     # 1004..1006, half inside the one above
        (ms(1004.5), ms(0.5)), # entirely inside both
        (ms(1008), ms(1)),     # 1008..1009, on its own
    ]
    got = _in_rows(WINDOW, spans, [])
    check("overlaps are counted once", got["in_rows_ms"] == 5.0, str(got))
    check("as a share of the window", got["in_rows_pct"] == 50.0, str(got))


def test_nothing_outside_the_window_counts() -> None:
    got = _in_rows(WINDOW, [(ms(999), ms(2)), (ms(1009.5), ms(5)),
                            (ms(1020), ms(1))], [])
    check("only the parts inside are counted: 1 ms at the start, 0.5 at the end",
          got["in_rows_ms"] == 1.5 and got["in_rows_pct"] == 15.0, str(got))


def test_no_findings_cover_nothing() -> None:
    got = _in_rows(WINDOW, [], ["custom_b", "custom_a"])
    check("an empty timeline is zero, not missing",
          got["in_rows_ms"] == 0.0 and got["in_rows_pct"] == 0.0, str(got))
    check("and what was not counted is named, in order",
          got["in_rows_uncounted"] == ["custom_a", "custom_b"], str(got))


# --- every detector against its own rows -----------------------------------

def _about_main(det_id: str, row: dict) -> bool:
    """Whether a row is about the main thread, by each detector's own naming."""
    loc, detail = str(row["location"]), str(row.get("detail") or "")
    if det_id == "binder_txn":
        return detail == "main thread"
    if det_id == "io_wait":
        return detail.startswith("main")
    if det_id in ("runnable_starvation", "monitor_contention", "uninstrumented_cpu"):
        return loc == MAIN
    if det_id in ("main_thread_block", "gc_pressure"):
        return detail == MAIN
    if det_id == "repeated_work":
        return detail.endswith("— on " + MAIN) or f"— on {MAIN};" in detail
    return det_id in ("main_thread_outlier", "anr_risk")


def _analysed(monkeypatch, tmp_path: Path,
              overrides: dict | None = None) -> tuple[dict, dict]:
    """The fixture analysed, with each detector's stretches kept aside."""
    seen: dict[str, list[tuple[int, int]]] = {}
    real = main_mod._spans_behind

    def keep(tp, d, params, rows):
        out = real(tp, d, params, rows)
        seen[d.id] = out or []
        return out

    monkeypatch.setattr(main_mod, "_spans_behind", keep)
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    report = main_mod.analyze_trace(trace, Config(selftest.FIXTURE_CONFIG),
                                    cli_overrides=overrides)
    return report, seen


def _hold_to_rows(report: dict, seen: dict, skip_anchor: bool) -> None:
    fired = [d for d in report["detectors"] if d["rows"]]
    check("the fixture fires detectors to hold", len(fired) >= 10, len(fired))
    for d in fired:
        mine = [r for r in d["rows"] if _about_main(d["id"], r)]
        if d["id"] == "uninstrumented_cpu":
            want = sum(r["total_ms"] - (r.get("covered_ms") or 0) for r in mine)
        else:
            want = sum(r.get(metric_of(r)) or 0 for r in mine)
        if d["id"] == "main_thread_block" and skip_anchor:
            # `AppStart` spans the fixture's whole window: its self time is
            # the part of the window nothing else accounts for, and is left
            # out on purpose.
            want -= sum(r["self_ms"] for r in mine if r["location"] == "AppStart")
        got = sum(dur for _, dur in seen.get(d["id"], [])) / 1e6
        check(f"{d['id']}: the stretches add up to its rows about the main thread",
              abs(got - want) < 0.01, f"stretches {got} ms, rows {want} ms")


def test_every_second_query_agrees_with_its_rows(monkeypatch, tmp_path) -> None:
    report, seen = _analysed(monkeypatch, tmp_path)
    _hold_to_rows(report, seen, skip_anchor=True)

    budget = report["window"]["main_thread"]
    check("nothing is left uncounted", budget["in_rows_uncounted"] == [],
          str(budget))
    check("the share is of the window",
          0 < budget["in_rows_ms"] <= budget["window_ms"], str(budget))
    naive = sum(r.get(metric_of(r)) or 0 for d in report["detectors"]
                for r in d["rows"] if _about_main(d["id"], r))
    check("and smaller than adding the rows up, which counts moments twice",
          budget["in_rows_ms"] < naive, f"{budget['in_rows_ms']} vs {naive}")


def test_anr_risk_stretches_are_its_rows(monkeypatch, tmp_path) -> None:
    """Its second query repeats the steps of the first, so it is held to them.

    The fixture holds no five-second stall; with the bar lowered it holds two
    stretches, and they cannot overlap, so their length is the rows' total.
    """
    report, seen = _analysed(monkeypatch, tmp_path,
                             {"anr_risk": {"min_stall_ms": 50}})
    rows = next(d["rows"] for d in report["detectors"] if d["id"] == "anr_risk")
    check("anr_risk fires with the bar lowered", rows, rows)
    _hold_to_rows(report, seen, skip_anchor=True)


def test_a_detector_that_cannot_say_is_named(monkeypatch, capsys,
                                            tmp_path) -> None:
    """Not counted is said, rather than counted as nothing."""
    real = main_mod._spans_behind

    def partly(tp, d, params, rows):
        if d.id == "io_wait":
            return None                      # as if it had no second query
        if d.id == "binder_txn":
            raise RuntimeError("no such column: nope")
        return real(tp, d, params, rows)

    monkeypatch.setattr(main_mod, "_spans_behind", partly)
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    report = main_mod.analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    budget = report["window"]["main_thread"]
    check("both are named", budget["in_rows_uncounted"] == ["binder_txn", "io_wait"],
          str(budget))
    check("the failure is said where failures are",
          "binder_txn @intervals" in capsys.readouterr().err)
    line = next((x for x in report_mod.to_markdown(report).splitlines()
                 if x.startswith("Covered by the findings below")), "")
    check("and the header names them", "`binder_txn`, `io_wait`" in line, line)


# --- across repeats, and on the page ---------------------------------------

def test_merged_share_is_the_median_run(marker_report) -> None:
    runs = []
    for pct, ms, left_out in ((40.0, 400.0, []), (60.0, 600.0, ["custom"]),
                              (50.0, 500.0, [])):
        one = copy.deepcopy(marker_report)
        one["window"]["main_thread"].update(
            in_rows_pct=pct, in_rows_ms=ms, in_rows_uncounted=left_out)
        runs.append(one)
    merged = report_mod.aggregate(runs)["window"]["main_thread"]
    check("the share is the median of the runs'", merged["in_rows_pct"] == 50.0,
          str(merged))
    check("and so are the milliseconds", merged["in_rows_ms"] == 500.0,
          str(merged))
    check("a detector left out of any run is named",
          merged["in_rows_uncounted"] == ["custom"], str(merged))


def test_the_header_says_it_under_the_budget(marker_report) -> None:
    text = report_mod.to_markdown(marker_report)
    lines = text.splitlines()
    at = next(i for i, x in enumerate(lines) if x.startswith("Main thread:"))
    check("the line straight under the budget",
          lines[at + 1].startswith("Covered by the findings below: **"),
          lines[at:at + 2])
    check("naming the thread and how it counts",
          "of the main thread's window, each moment counted once" in lines[at + 1],
          lines[at + 1])

    old = copy.deepcopy(marker_report)
    for key in ("in_rows_ms", "in_rows_pct", "in_rows_uncounted"):
        old["window"]["main_thread"].pop(key, None)
    check("a report from before the field says nothing about it",
          "Covered by the findings" not in report_mod.to_markdown(old))
