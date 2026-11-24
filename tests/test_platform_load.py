"""The processes that draw the app are not other load (#914).

Every cold start of Wikipedia on an SM-F711B, a 287 ms window on 8 cores,
was told "Record again with nothing else running": other processes took 57%
of the CPUs, SurfaceFlinger 17%, system_server 12%, SystemUI 10%, on a phone
with nothing else running. `analyze.PLATFORM_PROCESSES` names the processes
that draw an app and record it; their share is `platform_pct`, apart from the
load the warning is about. Here the fixture's idle CPU time goes to
SurfaceFlinger and system_server, and then to another app as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import analyze, fixture, report, selftest  # noqa: E402
from echolot import compare as compare_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402

LOOPS = 9100     # another app's busy threads, one a CPU


@pytest.fixture(scope="module")
def reports(tmp_path_factory) -> tuple[dict, dict]:
    """The fixture with its idle cores drawing, and the same with another
    app's busy loops on two CPUs of every three first: the processes that
    draw keep the third."""
    root = tmp_path_factory.mktemp("drawing")
    drawn = root / "drawing.perfetto-trace"
    drawn.write_bytes(fixture.build(drawing=True))
    cpus = sorted({cpu for cpu, *_ in fixture.SCHED})
    loops = [(cpu, LOOPS + cpu, start, end, fixture.R)
             for cpu in cpus if cpu % 3
             for start, end in fixture.idle_stretches(fixture.SCHED, cpu)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fixture, "SCHED", [*fixture.SCHED, *loops])
        mp.setattr(fixture, "OTHER_THREADS",
                   {**fixture.OTHER_THREADS, **{tid: "sh" for _, tid, *_ in loops}})
        busy = root / "busy.perfetto-trace"
        busy.write_bytes(fixture.build(drawing=True))
    cfg = Config(selftest.FIXTURE_CONFIG)
    return selftest._analyze(drawn, cfg), selftest._analyze(busy, cfg)


def test_the_drawing_processes_are_counted_apart(reports) -> None:
    drawn = reports[0]["window"]["crowding"]["outside"]
    check("their share is platform_pct", drawn["platform_pct"] > 70, drawn)
    check("and the rest is what other processes took, as without them",
          drawn["pct"] == 5.5 and [p["name"] for p in drawn["top"]] == [fixture.OTHER_NAME],
          drawn)
    check("the header says nothing", "Other processes took" not in report.to_markdown(reports[0]))


def test_another_app_beside_them_is_still_a_busy_device(reports) -> None:
    busy = reports[1]["window"]["crowding"]["outside"]
    check("the loops are other load", busy["pct"] >= report.OUTSIDE_PCT, busy)
    check("the drawing processes are still apart", 10 < busy["platform_pct"] < 40, busy)
    text = report.to_markdown(reports[1])
    check("and the header warns, naming the app",
          "> ⚠️ Other processes took" in text and f"`{fixture.OTHER_NAME}`" in text, text[:2500])


@pytest.mark.parametrize("name", selftest.DRAWING_NAMES)
def test_names_vendors_give_the_drawing_processes(name: str) -> None:
    check(f"{name} draws the app", analyze.platform_process(name))


@pytest.mark.parametrize("name", selftest.OTHER_LOAD)
def test_names_of_other_load(name: str) -> None:
    check(f"{name} is other load", not analyze.platform_process(name))


def test_the_case_of_a_name_does_not_matter() -> None:
    check("a launcher in capitals", analyze.platform_process("com.transsion.XOSLauncher"))


def _repeat(pct: float, platform: float | None) -> dict:
    outside = {"pct": pct, "top": [{"name": "logd", "uid": 1036, "processes": 1, "pct": 2.0}]}
    if platform is not None:
        outside["platform_pct"] = platform
    return {"window": {"crowding": {"threads": 40, "cores": 8, "peak": 9,
                                    "above_cores_ms": 5.0, "outside": outside}}}


def test_repeats_keep_the_median_of_their_share() -> None:
    merged = report._merge_crowding([_repeat(10.0, 46.0), _repeat(9.0, 44.0),
                                     _repeat(12.0, 55.0)])["outside"]
    check("the median of each", (merged["pct"], merged["platform_pct"]) == (10.0, 46.0), merged)
    old = report._merge_crowding([_repeat(57.0, None)])["outside"]
    check("none from reports that never had it", "platform_pct" not in old, old)


def test_compare_does_not_set_an_old_report_against_a_new_one() -> None:
    def load(before: dict, after: dict) -> list[str]:
        return [w["id"] for w in compare_mod._outside_moved(before, after)]

    old, new, busy = _repeat(57.0, None), _repeat(10.0, 46.0), _repeat(70.0, 46.0)
    check("no warning across the change", load(old, new) == [] and load(new, old) == [])
    check("a busy round read the same way still warns",
          load(new, busy) == ["environment-load"])
    check("and two old reports as before", load(_repeat(10.0, None), _repeat(70.0, None))
          == ["environment-load"])
