"""What a merged report says about how its repeats were measured.

A merged report's medians stand on every repeat, so its checks have to as
well: an anchor that missed, a detector that failed, a device that recorded
its state in some repeats only. Each case below is a set where one repeat
differs from the rest, and the merged report has to say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_report import env, one, row  # noqa: E402


def _bare() -> dict:
    """A repeat recorded without the platform-state sources."""
    return {"cpu": None, "thermal": None, "memory": None,
            "missing": ["cpu", "memory", "thermal"]}


def _five(first: dict) -> list[dict]:
    """Five repeats: the first with `first` for its environment, four bare."""
    return [one([], environment=first)] + [one([], environment=_bare())
                                           for _ in range(4)]


def test_device_state_from_one_repeat_says_so() -> None:
    full = env(1224.0)
    full["memory"] = {"available_mb_min": 1536.0, "major_faults": 250}
    full["missing"] = []
    merged = report_mod.aggregate(_five(full))
    e = merged["environment"]
    check("the clock counts its repeats", e["cpu"]["runs"] == "1/5", e["cpu"])
    check("so does the temperature, and throttling counts out of those",
          e["thermal"]["runs"] == "1/5" and e["thermal"]["throttled_runs"] == "0/1",
          e["thermal"])
    md = report_mod.to_markdown(merged)
    check("report.md says the clock was read in one repeat",
          "read in 1 of 5 repeats" in md and "peak 55 °C in 1 of 5 repeats" in md,
          md[:1500])
    check("and warns that the device state is that repeat's",
          "The device state comes from 1 of 5 repeats" in md, md[:1500])

    steady = report_mod.aggregate([one([], environment=env(1224.0)) for _ in range(5)])
    warned = [w for w in compare_mod.build(merged, steady, before_path="b.json", after_path="a.json")["warnings"]
              if w["id"] == "environment"]
    check("compare does not call the before side checked and steady",
          len(warned) == 1 and "1 of 5 repeats" in warned[0]["text"], warned)


def test_the_missing_line_is_worded_by_what_is_missing() -> None:
    lines = report_mod._environment_lines({
        "cpu": {"mean_mhz": 1500.0, "on_cpu_ms": 900.0, "measured_ms": 900.0},
        "thermal": None, "memory": None, "missing": ["memory", "thermal"]})
    text = "\n".join(lines)
    check("the clock is there", "clock **1500 MHz**" in text, text)
    check("and the line names what is not, without saying compare cannot judge",
          "Device state not recorded: memory, thermal" in text
          and "cannot tell a slower machine" not in text
          and "whether the kernel throttled" in text, text)

    lines = report_mod._environment_lines({
        "cpu": None, "thermal": None, "memory": None,
        "missing": ["cpu", "memory", "thermal"]})
    check("with nothing recorded the old sentence stands",
          any("carries no platform-state sources" in line for line in lines), lines)


def _windowed(duration: float, *, start_matches: int = 1,
              process: str = "com.example.app",
              opened_inside: dict | None = None) -> dict:
    rep = one([])
    rep["window"] = {"process": process, "pid": 4100, "duration_ms": duration,
                     "start_anchor": {"glob": "AppStart", "matches": start_matches},
                     "end_anchor": {"glob": "Screen.firstFrame", "matches": 1},
                     "opened_inside": opened_inside}
    return rep


def test_an_anchor_that_missed_in_one_repeat_is_shouted() -> None:
    merged = report_mod.aggregate([_windowed(1000.0), _windowed(1050.0),
                                   _windowed(1900.0, start_matches=0)])
    a = merged["window"]["start_anchor"]
    check("the fewest matches, and the repeats that missed",
          a["matches"] == 0 and a["missed"] == "1/3", a)
    md = report_mod.to_markdown(merged)
    check("report.md warns about that repeat",
          "`AppStart` was not found in 1 of 3 repeats" in md, md[:2000])
    other = report_mod.aggregate([_windowed(1000.0) for _ in range(3)])
    warned = {w["id"] for w in compare_mod.build(merged, other, before_path="b.json", after_path="a.json")["warnings"]}
    check("and compare calls the before side's window into question",
          "anchor-before" in warned, warned)


def test_a_material_opened_inside_and_another_process_survive_the_merge() -> None:
    inside = {"state": "S", "total_ms": 300.0, "before_ms": 200.0, "material": True}
    merged = report_mod.aggregate([
        _windowed(1000.0),
        _windowed(1000.0, opened_inside=inside, process="com.example.app:sync"),
        _windowed(1000.0)])
    w = merged["window"]
    check("opened_inside from the repeat where it is material",
          w["opened_inside"]["before_ms"] == 200.0
          and w["opened_inside"]["runs"] == "1/3", w["opened_inside"])
    check("and the processes, since there were two",
          w["processes"] == ["com.example.app", "com.example.app:sync"], w)
    md = report_mod.to_markdown(merged)
    check("report.md says the repeats measured different processes",
          "measured different processes" in md, md[:2000])


def _with_binder(failed: bool) -> dict:
    rep = one([] if failed else [row("transact", count=1, total_ms=12.0,
                                     max_ms=12.0, detail="main")],
              det_id="binder_txn")
    if failed:
        rep["detectors"][0]["error"] = "simulated SQL error"
    return rep


def test_a_detector_failing_in_one_repeat_does_not_lower_runs() -> None:
    merged = report_mod.aggregate([_with_binder(i == 2) for i in range(5)])
    det = merged["detectors"][0]
    check("the row is counted against the repeats the detector ran in",
          det["rows"][0]["runs"] == "4/4", det["rows"])
    check("and the failures are counted apart",
          det["failed_runs"] == "1/5" and det["error"] == "simulated SQL error", det)
    md = report_mod.to_markdown(merged)
    check("report.md says it failed in one of five, with the error",
          "Failed in 1 of 5 repeats: simulated SQL error" in md, md)


def test_a_failed_detector_is_not_silent() -> None:
    from tests.test_report_view import sample
    md = report_mod.to_markdown(sample())
    check("it is named as failed, with its error",
          "> ⚠️ Failed: `frame_jank` — no such table: actual_frame_timeline_slice"
          in md, md)
    check("and kept out of Silent", "**Silent:** gc_pressure\n" in md + "\n", md)

    rep = one([], det_id="frame_jank")
    rep["detectors"][0]["error"] = "no such table: actual_frame_timeline_slice"
    md = report_mod.to_markdown(rep)
    check("a report where everything failed does not call the run clean",
          "No detector fired" not in md and "Failed: `frame_jank`" in md, md)
