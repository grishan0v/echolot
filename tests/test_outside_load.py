"""Another process taking the CPU is named in the report and in `compare` (#596).

Eight busy loops of the shell user took 68% of all eight cores of an
SM-A515F during a round. The header put the app's waits for a CPU down to
"the app's own work", nothing warned, and `compare` against a quiet round
spoke only of a lower clock. Here the fixture's idle CPU time inside the
window goes to threads of another process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, report, selftest  # noqa: E402
from echolot import compare as compare_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402

WINDOW = (100, 1105)
LOOPS = 9100     # the first busy thread's tid; one per CPU


def _loaded(sched: list) -> tuple[list, dict]:
    """The fixture's sched, with every idle stretch of the window on every CPU
    given to a busy thread of `com.other.app`."""
    cpus = sorted({cpu for cpu, *_ in sched} | set(range(9)))
    out, threads = list(sched), {}
    for i, cpu in enumerate(cpus):
        tid = LOOPS + i
        threads[tid] = f"sh{i}"
        busy = sorted((s, e) for c, _, s, e, _ in sched if c == cpu)
        at = WINDOW[0]
        for start, end in [*busy, (WINDOW[1], WINDOW[1])]:
            if start > at + 1:
                out.append((cpu, tid, at + 0.5, start - 0.5, fixture.R))
            at = max(at, end)
    return out, threads


@pytest.fixture(scope="module")
def reports(tmp_path_factory) -> tuple[dict, dict]:
    root = tmp_path_factory.mktemp("load")
    quiet = root / "quiet.perfetto-trace"
    quiet.write_bytes(fixture.build())
    sched, threads = _loaded(fixture.SCHED)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fixture, "SCHED", sched)
        mp.setattr(fixture, "OTHER_THREADS", {**fixture.OTHER_THREADS, **threads})
        busy = root / "busy.perfetto-trace"
        busy.write_bytes(fixture.build())
    cfg = Config(selftest.FIXTURE_CONFIG)
    return analyze_trace(quiet, cfg), analyze_trace(busy, cfg)


def test_the_share_other_processes_took_is_measured(reports) -> None:
    quiet, busy = (r["window"]["crowding"]["outside"] for r in reports)
    check("little on the quiet fixture", quiet["pct"] < 10, quiet)
    check("most of the CPUs on the busy one", busy["pct"] > 60, busy)
    check("named by process", busy["top"][0]["name"] == fixture.OTHER_NAME, busy["top"])


def test_the_header_warns_and_names_the_load(reports) -> None:
    quiet, busy = (report.to_markdown(r) for r in reports)
    check("the busy report warns",
          "> ⚠️ Other processes took" in busy and f"`{fixture.OTHER_NAME}`" in busy, busy[:2500])
    check("the quiet one does not", "Other processes took" not in quiet, quiet[:2500])


def test_the_crowding_line_puts_the_wait_behind_them_too() -> None:
    crowd = {"threads": 80, "cores": 8, "peak": 83, "above_cores_ms": 5844.0,
             "outside": {"pct": 80.0, "top": [{"name": "sh", "uid": 2000,
                                               "processes": 8, "pct": 68.0}]}}
    line = report._crowding_lines(crowd)[0]
    check("the waits are behind other processes too",
          "other processes', which took 80% of the CPUs' time" in line, line)
    quiet = dict(crowd, outside={"pct": 12.0, "top": []})
    check("and only the app's own work on a quiet device",
          "waits behind the app's own work, and" in report._crowding_lines(quiet)[0])
    check("the warning names eight sh of the shell user as one load",
          "`sh` (8 processes, uid 2000, 68%)" in report._outside_lines(crowd)[0],
          report._outside_lines(crowd))


def test_repeats_carry_the_median_and_the_worst_names(reports) -> None:
    quiet, busy = reports
    merged = report.aggregate([quiet, busy, busy])["window"]["crowding"]["outside"]
    check("the median, the worst and how many repeats had it",
          merged["pct"] == busy["window"]["crowding"]["outside"]["pct"]
          and merged["pct_max"] == merged["pct"] and merged["runs"] == "3/3"
          and merged["top"][0]["name"] == fixture.OTHER_NAME, merged)


def test_repeats_name_the_process_that_took_most_across_them() -> None:
    """#710: `system_server` first in two repeats, another app first in the
    third, the busiest. The warning names the process of the median, with
    its median share, and not the busiest repeat's."""
    def repeat(pct: float, top: list[tuple[str, int, float]]) -> dict:
        return {"window": {"crowding": {
            "threads": 40, "cores": 8, "peak": 9, "above_cores_ms": 5.0,
            "outside": {"pct": pct, "top": [
                {"name": n, "uid": uid, "processes": 1, "pct": share}
                for n, uid, share in top]}}}}

    merged = report._merge_crowding([
        repeat(50.0, [("system_server", 1000, 20.0), ("com.other.app", 10283, 4.0)]),
        repeat(55.0, [("system_server", 1000, 18.0), ("surfaceflinger", 1000, 6.0)]),
        repeat(61.0, [("com.other.app", 10283, 16.0), ("system_server", 1000, 9.0)]),
    ])
    top = merged["outside"]["top"]
    check("the process of the median leads", top[0]["name"] == "system_server", top)
    check("with its median share over the three", top[0]["pct"] == 18.0, top)
    check("a process missing from a repeat counts zero there",
          next(p for p in top if p["name"] == "com.other.app")["pct"] == 4.0, top)
    check("the worst repeat's share is still kept", merged["outside"]["pct_max"] == 61.0,
          merged["outside"])
    line = report._outside_lines(merged)[0]
    check("the sentence names it, with that share",
          "most of it `system_server` (uid 1000, 18%)" in line, line)


def compare(before: dict, after: dict) -> dict:
    return compare_mod.build(before, after, before_path="a.json", after_path="b.json")


def test_compare_warns_when_one_side_was_busy(reports) -> None:
    quiet, busy = reports
    warned = [w for w in compare(quiet, busy)["warnings"] if w["id"] == "environment-load"]
    check("one warning, naming the busy side and the load",
          len(warned) == 1 and "the after round's window" in warned[0]["text"]
          and fixture.OTHER_NAME in warned[0]["text"], warned)
    check("none between two quiet rounds",
          not [w for w in compare(quiet, quiet)["warnings"] if w["id"] == "environment-load"])
