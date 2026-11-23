#!/usr/bin/env python3
"""Frames queued behind the one before are latency, not late frames (#537).

The doctor check pins `frame_jank`'s rows on the scene `fixture.build(
stuffed=True)` draws: a list that runs a frame behind, whose frames the
platform tags Buffer Stuffing, beside the frames the app really missed. The
late frames rank first, and the queued ones keep their row and count nothing
past a deadline. Here are the readers of those rows: the plain reading, on a
report written now and on one written before, where the queue's wait was the
row's total; `compare`, where that total was the table's largest move; and
the page.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, plain, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import compare, det, report  # noqa: E402
from tests.test_report_page import page_of, panel_of, rows_of  # noqa: E402

QUEUED = ("1254 of 1440 frames waited in a queue behind the frame before, in all 5 runs. "
          "They reached the screen without a stutter, only later than they could: the "
          "screen follows a touch a little late.")


def _row(location: str, values: list[float], count: float, detail: str,
         max_ms: float | None = None) -> dict:
    """A row as `analyze` merges it over as many runs as `values` holds."""
    out = {"location": location, "runs": f"{len(values)}/{len(values)}", "count": count,
           "total_ms": sorted(values)[len(values) // 2], "detail": detail,
           "spread": {"total_ms": {"min": min(values), "max": max(values), "values": values}}}
    if max_ms is not None:
        out["max_ms"] = max_ms
    return out


def _scroll(rows: list[dict]) -> dict:
    """Five recordings of a 40 s scroll that drew 1440 frames, the median."""
    return report([det("frame_jank", rows)], runs=5, window={
        "process": "com.example.app", "duration_ms": 40000.0, "frames": {"count": 1440}})


def _read(rep: dict) -> dict:
    return next(d for d in plain.annotate(rep)["detectors"] if d["id"] == "frame_jank")


def test_a_report_written_before_reads_the_queue_as_latency() -> None:
    """The rows `frame_jank` wrote on a real scroll before #537: the queue's
    wait summed into 16 s past the deadline, 41% of the run, and the first
    row. Read again, it sets no level, and the sentence leads with the
    frames that missed a deadline. Each count is the row's own, a median
    over the five runs, out of the window's frames: the evidence's are the
    worst run's, 13 and 1263 of 1438 and 1446."""
    rep = _scroll([
        _row("Buffer Stuffing", [16100.0, 16200.0, 16234.47, 16300.0, 16491.87], 1254,
             "Buffer Stuffing · 1263 of 1446 frames · longest 61.9 ms", 44.23),
        _row("App Deadline Missed, Buffer Stuffing", [250.0, 270.0, 274.91, 280.0, 290.0], 9,
             "Self Jank · 13 of 1438 frames · longest 56.0 ms", 48.69),
        _row("App Deadline Missed", [220.0, 230.0, 237.56, 240.0, 250.0], 19,
             "Self Jank · 21 of 1436 frames · longest 43.6 ms", 27.1),
    ])
    got = _read(rep)
    check("16 s of queued frames, 41% of the run, is worth a look, not attention",
          got["attention"] == plain.WORTH_A_LOOK, got["attention"])
    said = got["plain"]["found"]
    check("the sentence leads with the frames the app was late for, by the merged count",
          said.startswith("9 of 1440 frames came late because of the app, in all 5 runs: "
                          "0.3 s past their deadlines together, 49 ms at worst."), said)
    check("says that their overrun holds the wait, and that one more kind was late",
          "That counts the time they waited in a queue behind the frame before, too. "
          "1 more kind of late frame, too." in said, said)
    check("and the queued frames are a sentence of their own, after it",
          said.endswith(" " + QUEUED), said)
    check("which no share of the run is said of", said.count("% of the run") == 1, said)


def test_the_queue_alone_is_latency() -> None:
    """A list that runs a frame behind and missed nothing: the row as
    `frame_jank` writes it now."""
    got = _read(_scroll([_row(
        "Buffer Stuffing", [0.0] * 5, 1254,
        "latency, not jank · 1263 of 1446 frames queued behind the one before · up to "
        "44.2 ms late", 0.0)]))
    check("worth a look: the check found something, and it is not a late frame",
          got["attention"] == plain.WORTH_A_LOOK, got["attention"])
    check("the sentence says the frames were smooth and late, and nothing else",
          got["plain"]["found"] == QUEUED, got["plain"]["found"])


def _fixture_set(n: int = 3) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "stuffed.perfetto-trace"
        path.write_bytes(fixture.build(stuffed=True))
        one = analyze_trace(path, Config(selftest.FIXTURE_CONFIG))
    return report_mod.aggregate([one] * n)


def test_merged_the_late_frames_still_rank_first() -> None:
    """Three runs of the scene, merged the way `analyze` merges them: the
    merge ranks by the total too, and the queued frames have none."""
    rows = next(d for d in _fixture_set()["detectors"] if d["id"] == "frame_jank")["rows"]
    check("late frames first, the queued ones last with nothing past a deadline",
          [(r["location"], r["count"], r["total_ms"]) for r in rows] == [
              ("App Deadline Missed", 4, 80.0),
              ("App Deadline Missed, Buffer Stuffing", 3, 54.0),
              ("Buffer Stuffing", 12, 0.0)], rows)


def _side(frames: float, rows: list[dict]) -> dict:
    return report([det("frame_jank", rows)], runs=5, window={
        "process": "com.example.app", "duration_ms": 15000.0, "frames": {"count": frames}})


def test_compare_does_not_read_fewer_queued_frames_as_the_largest_move() -> None:
    """A known regression, as `compare` met it: the build that janks drew a
    fifth of the frames, and a tenth of them only queued. Summed as time past
    the deadline the queued frames fell by 5.8 s, the table's first row and
    an improvement that held, above the late frames that grew. They count
    nothing past a deadline now, so they did not move."""
    queued = "latency, not jank · {} of {} frames queued behind the one before · up to 16.4 ms late"
    before = _side(657, [
        _row("App Deadline Missed, Buffer Stuffing", [120.0, 122.0, 125.0, 128.0, 130.0], 4,
             "Self Jank · 4 of 657 frames · longest 40.0 ms"),
        _row("App Deadline Missed", [138.0, 140.0, 141.0, 145.0, 150.0], 6,
             "Self Jank · 6 of 657 frames · longest 38.0 ms"),
        _row("Buffer Stuffing", [0.0] * 5, 628, queued.format(628, 657)),
    ])
    after = _side(117.5, [
        _row("App Deadline Missed, Buffer Stuffing", [4480.0, 4500.0, 4546.5, 4600.0, 4650.0], 60,
             "Self Jank · 60 of 117 frames · longest 140.0 ms"),
        _row("App Deadline Missed", [830.0, 840.0, 847.0, 850.0, 860.0], 30,
             "Self Jank · 30 of 117 frames · longest 90.0 ms"),
        _row("Buffer Stuffing", [0.0] * 5, 63.5, queued.format(63, 117)),
    ])
    cmp = compare(before, after)
    by = {r["location"]: r for r in cmp["rows"]}
    check("the queued frames did not move", by["Buffer Stuffing"]["change"] == "steady"
          and by["Buffer Stuffing"]["delta_ms"] == 0.0, by["Buffer Stuffing"])
    check("the late frames lead the table, both grown and holding",
          [(r["location"], r["change"], r["holds"]) for r in cmp["rows"][:2]] == [
              ("App Deadline Missed, Buffer Stuffing", "grew", True),
              ("App Deadline Missed", "grew", True)], cmp["rows"])
    moved = compare_mod.to_markdown(cmp).split("## Steady")[0]
    check("and the table of what moved has no row of queued frames",
          "latency, not jank" not in moved, moved)


def test_the_page_says_the_same() -> None:
    """The scene through the page: the row of late frames is worth a look,
    and says what the plain reading says; the queued frames are a bar of
    nothing past a deadline, under the late ones, with their evidence."""
    page = page_of(_fixture_set())
    row = rows_of(page)["frame_jank"]
    check("yellow, not red", row.attrs["data-group"] == "yellow", row.attrs)
    said = row.one("p", "tr-f").text
    check("the app's late frames first, then the queue",
          said.startswith("4 of 25 frames came late because of the app, in all 3 launches")
          and "12 of 25 frames waited in a queue behind the frame before" in said, said)
    panel = panel_of(page, "frame_jank").text
    check("the panel draws the queued frames last, with their evidence",
          panel.index("App Deadline Missed") < panel.index("latency, not jank"), panel)
