#!/usr/bin/env python3
"""Which echolot wrote a report, and how its traces were recorded (#507, #533).

Two reports of the very same fifteen traces, written by 0.7.0 and 0.17.0,
gave 11 rows that held: a detector reworked in between read the trace
differently, and nothing in either report said which echolot wrote it. A set
from a Macrobenchmark's own trace config against one `collect` recorded gave
44 rows that held, with nothing to say the recording changed. And the same
APK recorded on two days, once by a plain `collect` whose force-stop found the
app still running and once after a stop made earlier, came out 0.68 s apart
with 60 rows that held and no warning.

Each report now says which echolot wrote it and what each detector read the
trace with; carries how its traces were recorded, from the trace's own config
and from what `collect` keeps beside each trace; and `compare` says when any
of that differs, and withholds the verdict a difference in the tool leaves
unfounded.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import __version__, fixture, runner, selftest  # noqa: E402
from echolot import recording as recording_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.analyze import DETECTOR_DIR  # noqa: E402
from echolot.main import _report_text  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import PACKAGE, FakeAdb, kind  # noqa: E402
from tests.test_compare import compare, det, report, warned  # noqa: E402

BEFORE = [100.0, 102.0, 98.0, 101.0]
AFTER = [200.0, 204.0, 196.0, 202.0]


def _row(location: str, values: list[float]) -> dict:
    return {"location": location, "runs": "4/4", "count": 1,
            "self_ms": sorted(values)[len(values) // 2],
            "spread": {"self_ms": {"min": min(values), "max": max(values), "values": values}}}


def side(values: list[float], *, echolot: str | None = "1.0.0",
         shas: dict[str, str] | None = None, recording: dict | None = None,
         window: dict | None = None, environment: dict | None = None,
         detectors=("main_thread_block", "io_wait")) -> dict:
    """Four repeats a side, one row per detector, every row holding against the other side."""
    dets = []
    for det_id in detectors:
        d = det(det_id, [_row(f"{det_id} row", values)])
        if echolot:
            d["sha"] = (shas or {}).get(det_id, "a" * 12)
        dets.append(d)
    rep = report(dets, runs=4, window=window, environment=environment)
    rep["toolchain"] = {"echolot": echolot} if echolot else {}
    if recording is not None:
        rep["recording"] = recording
    return rep


def holds(cmp: dict) -> dict[str, bool | None]:
    return {r["detector"]: r["holds"] for r in cmp["rows"]}


def text(cmp: dict, warning: str) -> str:
    return " ".join(w["text"] for w in cmp["warnings"] if w["id"] == warning)


# --- which echolot (#507) ------------------------------------------------------

def test_the_report_names_its_echolot_and_what_each_detector_read_the_trace_with(marker_report):
    check("toolchain.echolot is this version",
          marker_report["toolchain"].get("echolot") == __version__, marker_report["toolchain"])
    shipped = {d.id: d.sha for d in load_detectors(DETECTOR_DIR)}
    check("each detector carries its sha, the shipped one",
          {d["id"]: d.get("sha") for d in marker_report["detectors"]}
          == {d["id"]: shipped[d["id"]] for d in marker_report["detectors"]},
          [(d["id"], d.get("sha")) for d in marker_report["detectors"]])


def test_a_comment_does_not_change_a_detectors_sha_and_its_query_does(tmp_path):
    src = next(DETECTOR_DIR.glob("main_thread_block.sql")).read_text(encoding="utf-8")
    worded = tmp_path / "worded.sql"
    worded.write_text(src.replace("-- @why:", "-- a sentence reworded\n-- @why:", 1),
                      encoding="utf-8")
    moved = tmp_path / "moved.sql"
    moved.write_text(src.replace("SELECT", "SELECT DISTINCT", 1), encoding="utf-8")
    shipped = load_detectors(DETECTOR_DIR)
    base = next(d for d in shipped if d.id == "main_thread_block").sha
    from echolot.tp import Detector
    check("a comment line leaves it", Detector.from_file(worded).sha == base)
    check("the query moves it", Detector.from_file(moved).sha != base)


def test_a_detector_whose_code_differs_keeps_its_rows_without_a_verdict():
    cmp = compare(side(BEFORE), side(AFTER, echolot="1.1.0",
                                     shas={"main_thread_block": "b" * 12}))
    check("only that detector's rows lose the verdict",
          holds(cmp) == {"main_thread_block": None, "io_wait": True}, holds(cmp))
    check("the versions are said", "1.0.0 before, 1.1.0 after" in text(cmp, "version"),
          cmp["warnings"])
    check("and the detector named", "`main_thread_block`" in text(cmp, "detector-changed"),
          cmp["warnings"])
    check("the rows are still in the table",
          {r["change"] for r in cmp["rows"]} == {"grew"}, cmp["rows"])


def test_versions_that_differ_and_detectors_that_do_not_keep_every_verdict():
    cmp = compare(side(BEFORE), side(AFTER, echolot="1.0.1"))
    check("every row holds", holds(cmp) == {"main_thread_block": True, "io_wait": True},
          holds(cmp))
    check("the versions are said, and that every detector read alike",
          "1.0.0 before, 1.0.1 after" in text(cmp, "version")
          and "read the trace the same way" in text(cmp, "version"), cmp["warnings"])
    check("no detector named", "detector-changed" not in warned(cmp), cmp["warnings"])


def test_the_same_version_with_one_detector_edited_names_it():
    """A checkout with a detector edited between two runs: one version, two codes."""
    cmp = compare(side(BEFORE), side(AFTER, shas={"io_wait": "c" * 12}))
    check("no version sentence", "version" not in warned(cmp), cmp["warnings"])
    check("the edited detector, without a verdict",
          holds(cmp) == {"main_thread_block": True, "io_wait": None}, holds(cmp))


@pytest.mark.parametrize("old_side", ["before", "after"])
def test_a_side_that_does_not_say_its_echolot_leaves_every_row_without_a_verdict(old_side):
    old, new = side(BEFORE, echolot=None), side(AFTER)
    cmp = compare(old, new) if old_side == "before" else compare(new, old)
    check("no row holds", set(holds(cmp).values()) == {None}, holds(cmp))
    said = text(cmp, "version")
    check("the side is named, and told to be analysed again",
          f"The {old_side} report does not say which echolot" in said
          and f"Analyze the {old_side} side's traces again" in said, said)


def test_two_reports_from_before_the_stamp_are_compared_as_they_always_were():
    cmp = compare(side(BEFORE, echolot=None), side(AFTER, echolot=None))
    check("verdicts as before",
          holds(cmp) == {"main_thread_block": True, "io_wait": True}, holds(cmp))
    check("and nothing about versions",
          not {"version", "detector-changed"} & warned(cmp), cmp["warnings"])


WINDOW = {"process": "com.example.app", "pid": 4242, "duration_ms": 5450.15,
          "duration_ms_min": 5071.24, "duration_ms_max": 5854.13}


def test_the_same_recordings_on_both_sides_are_said_and_get_no_verdict():
    """0.7.0 and 0.17.0 on the same fifteen traces: the window was +0.0, and
    11 rows held. No stamp is needed to see it is one set read twice."""
    cmp = compare(side(BEFORE, echolot=None, window=WINDOW),
                  side(AFTER, echolot=None, window=WINDOW))
    said = text(cmp, "same-traces")
    check("said, with what gave it away",
          "the same 4 trace names" in said and "pid 4242" in said
          and "median 5450.15 ms, 5071.24–5854.13" in said, said)
    check("and no row holds", set(holds(cmp).values()) == {None}, holds(cmp))


@pytest.mark.parametrize("change", [{"pid": 4343}, {"duration_ms_max": 5854.2}])
def test_another_pid_or_another_window_is_another_recording(change):
    cmp = compare(side(BEFORE, window=WINDOW), side(AFTER, window={**WINDOW, **change}))
    check("not said", "same-traces" not in warned(cmp), cmp["warnings"])
    check("verdicts stand", holds(cmp) == {"main_thread_block": True, "io_wait": True},
          holds(cmp))


# --- written by another echolot: status and report ---------------------------

def test_a_report_from_another_minor_version_says_so_and_a_patch_release_does_not():
    minor = __version__.rsplit(".", 1)[0]
    check("another minor version", "Written by echolot 0.7.0 (12 detectors)"
          in (render_mod.written_by("0.7.0", 12) or ""), render_mod.written_by("0.7.0", 12))
    check("no version at all", "from before reports named their version"
          in (render_mod.written_by(None, 12) or ""))
    check("a patch release of this one says nothing",
          render_mod.written_by(f"{minor}.99", 12) is None)


def test_status_and_the_report_view_say_it(tmp_path):
    rep = {"path": tmp_path / "report.json", "generated_at": "2026-09-09T10:00:00+00:00",
           "fired": 10, "run": 12, "runs": 15, "echolot": "0.7.0"}
    line = _report_text(rep, None, tmp_path)
    check("status", f"written by echolot 0.7.0, this is {__version__} — analyze again"
          in line, line)
    old = side(BEFORE, echolot="0.7.0")
    check("the report's header", f"⚠️ Written by echolot 0.7.0 (2 detectors); this is "
          f"{__version__}" in render_mod.overview(old), render_mod.overview(old)[:600])


# --- how the traces were recorded (#533) ----------------------------------------

def test_the_report_says_what_the_recording_asked_for(marker_report):
    rec = marker_report["recording"]
    check("data sources, from the trace's own config",
          rec["data_sources"] == ["android.surfaceflinger.frametimeline", "linux.ftrace",
                                  "linux.process_stats"], rec)
    check("atrace categories and ftrace events",
          rec["atrace_categories"] == sorted(fixture.RECORDED_CATEGORIES)
          and rec["ftrace_events"] == sorted([*fixture.SCHED_EVENTS, *fixture.STATE_EVENTS]),
          rec)
    check("and the runner unknown, for a trace collect did not record",
          rec["runner"] is None and rec["reset_policy"] is None and rec["app_at_stop"] is None,
          rec)


def test_a_trace_with_no_config_asks_for_nothing_anyone_can_tell():
    check("all three unknown", recording_mod.from_config(None) == {
        "data_sources": None, "atrace_categories": None, "ftrace_events": None})


def test_analyze_reads_what_collect_kept_beside_the_trace(tmp_path):
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(fixture.build())
    recording_mod.write(trace, {"runner": "launch", "reset_policy": "force-stop",
                                "app_at_stop": "running"})
    rep = selftest._analyze(trace, Config(selftest.FIXTURE_CONFIG))
    rec = rep["recording"]
    check("the runner's account in the report",
          (rec["runner"], rec["reset_policy"], rec["app_at_stop"])
          == ("launch", "force-stop", "running"), rec)


def blocks(**per_run) -> list[dict]:
    """One block per repeat; each keyword a list of values, one per repeat."""
    n = len(next(iter(per_run.values())))
    base = {"runner": "launch", "reset_policy": "force-stop", "app_at_stop": "stopped",
            "data_sources": ["linux.ftrace"], "atrace_categories": ["am"],
            "ftrace_events": ["sched/sched_switch"]}
    return [{**base, **{k: v[i] for k, v in per_run.items()}} for i in range(n)]


def test_a_set_takes_what_most_of_its_repeats_did_and_keeps_the_split():
    merged = recording_mod.merge(blocks(app_at_stop=["stopped", *["running"] * 14]))
    check("most of them", merged["app_at_stop"] == "running", merged)
    check("and the split", merged["mixed"] == {"app_at_stop": [
        {"value": "stopped", "runs": "1/15"}, {"value": "running", "runs": "14/15"}]}, merged)
    check("nothing to merge from reports without the block",
          recording_mod.merge([None, None]) is None)


def test_the_stop_that_found_the_app_running_against_one_that_did_not_is_said():
    """The two days: a plain collect, and a script that stopped the app first."""
    day1 = recording_mod.merge(blocks(app_at_stop=["stopped", *["running"] * 14]))
    day2 = recording_mod.merge(blocks(app_at_stop=["stopped"] * 15))
    cmp = compare(side(BEFORE, recording=day1), side(AFTER, recording=day2))
    said = text(cmp, "recording")
    check("both sides, with how many repeats",
          "before, the force-stop found the app still running in 14 of 15 repeats and "
          "killed it" in said
          and "after, the force-stop found the app already stopped in every repeat" in said,
          said)
    check("a plain collect's first repeat is no mixed set",
          "recording-mixed" not in warned(cmp), cmp["warnings"])
    check("the verdicts stand: the recording is said, not refused",
          holds(cmp) == {"main_thread_block": True, "io_wait": True}, holds(cmp))


def test_another_runner_is_said():
    gradle = recording_mod.merge(blocks(runner=["gradle"] * 4, reset_policy=[None] * 4,
                                        app_at_stop=[None] * 4))
    launch = recording_mod.merge(blocks(runner=["launch"] * 4))
    cmp = compare(side(BEFORE, recording=gradle), side(AFTER, recording=launch))
    said = text(cmp, "recording")
    check("the runner, both sides", "runner `gradle` before and `launch` after" in said, said)
    check("what follows from the runner is not called unknown",
          "recording-unknown" not in warned(cmp), cmp["warnings"])


def test_another_trace_config_names_what_each_side_had_and_who_reads_it():
    macro = recording_mod.merge(blocks(
        data_sources=[["android.power", "linux.ftrace", "track_event"]] * 4,
        atrace_categories=[["am", "dalvik", "disk", "sched"]] * 4,
        ftrace_events=[["sched/sched_switch"]] * 4))
    ours = recording_mod.merge(blocks(
        data_sources=[["android.surfaceflinger.frametimeline", "linux.ftrace"]] * 4,
        atrace_categories=[["am", "dalvik"]] * 4,
        ftrace_events=[["sched/sched_blocked_reason", "sched/sched_switch",
                        "thermal/thermal_temperature"]] * 4))
    cmp = compare(side(BEFORE, recording=macro, detectors=(
                      "io_wait", "frame_jank", "composables", "main_thread_block")),
                  side(AFTER, recording=ours, detectors=(
                      "io_wait", "frame_jank", "composables", "main_thread_block")))
    said = text(cmp, "recording")
    check("each list, by side",
          "data sources, only before `android.power`, `track_event` and only after "
          "`android.surfaceflinger.frametimeline`" in said
          and "atrace categories, only before `disk`, `sched`" in said
          and "ftrace events, only after `sched/sched_blocked_reason`, "
              "`thermal/thermal_temperature`" in said, said)
    check("the detectors that read what one side had alone",
          "`composables`, `frame_jank`, `io_wait` read what one side recorded" in said, said)


def test_a_side_recorded_two_ways_is_said():
    mixed = recording_mod.merge(blocks(runner=["launch", "launch", "command", "command"]))
    cmp = compare(side(BEFORE, recording=mixed),
                  side(AFTER, recording=recording_mod.merge(blocks(runner=["launch"] * 4))))
    check("the before side's split",
          "runner `launch` in 2 of 4, `command` in 2 of 4" in text(cmp, "recording-mixed"),
          cmp["warnings"])


def test_a_side_that_cannot_say_is_unknown_never_the_same():
    known = recording_mod.merge(blocks(runner=["launch"] * 4))
    cmp = compare(side(BEFORE), side(AFTER, recording=known))
    check("a report from before the block",
          "The before report was written before reports said how their traces were recorded"
          in text(cmp, "recording-unknown"), cmp["warnings"])
    old_traces = recording_mod.merge(blocks(runner=[None] * 4, reset_policy=[None] * 4,
                                            app_at_stop=[None] * 4))
    cmp = compare(side(BEFORE, recording=old_traces), side(AFTER, recording=known))
    check("traces recorded before collect kept its account",
          "The before side does not say how its repeats were driven"
          in text(cmp, "recording-unknown"), cmp["warnings"])
    check("and no difference claimed", "recording" not in warned(cmp), cmp["warnings"])
    cmp = compare(side(BEFORE), side(AFTER))
    check("two reports without the block: nothing",
          not {"recording", "recording-unknown", "recording-mixed"} & warned(cmp),
          cmp["warnings"])


# --- throttling seen on one side only (#533) -----------------------------------

HOT = {"cpu": None, "memory": None, "thermal": {
    "max_celsius": 82.0, "hottest_zone": "BIG", "throttled": True,
    "throttle_device": "thermal-cpufreq-1", "throttled_runs": "4/4", "runs": "4/4"}}
BLIND = {"cpu": None, "thermal": None, "memory": None, "missing": ["cpu", "thermal", "memory"]}


def test_throttling_on_the_one_side_that_measured_is_said_without_a_word_on_the_other():
    cmp = compare(side(BEFORE, environment=BLIND), side(AFTER, environment=HOT))
    said = text(cmp, "environment-thermal-one-side")
    check("the throttled side, with its repeats",
          "throttled the device during the after round in every repeat" in said, said)
    check("and the other neither cleared nor accused",
          "cannot be ruled out there" in said and "not the other" not in said, said)
    check("not the both-sides sentence", "environment-thermal" not in warned(cmp),
          cmp["warnings"])


def test_nobody_measured_says_nothing():
    cmp = compare(side(BEFORE, environment=BLIND), side(AFTER, environment=BLIND))
    check("no thermal sentence",
          not {"environment-thermal", "environment-thermal-one-side"} & warned(cmp),
          cmp["warnings"])


# --- what collect keeps beside each trace -------------------------------------

@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


def test_collect_keeps_the_runner_and_the_app_at_each_stop_beside_the_trace(tmp_path, adb):
    adb.script["force-stop"] = ["was:stopped\n", "was:running\n"]
    out = tmp_path / "traces"
    runner.collect(PACKAGE, out, 2, section={}, name="startup", log=lambda m: None)
    stops = [c for c in adb.calls if kind(c) == "force-stop"]
    check("the stop asks before HOME whether the app is running",
          stops[0][-1].index(f"pidof {PACKAGE} > /dev/null; then echo was:running")
          < stops[0][-1].index("input keyevent KEYCODE_HOME"), stops[0])
    kept = [json.loads((out / f"startup_iter00{i}.perfetto-trace.recording.json").read_text())
            for i in range(2)]
    check("each repeat's account",
          [(k["runner"], k["reset_policy"], k["app_at_stop"]) for k in kept]
          == [("launch", "force-stop", "stopped"), ("launch", "force-stop", "running")], kept)
    check("with the echolot that wrote it", kept[0]["echolot"] == __version__, kept[0])


def test_a_stop_that_does_not_say_leaves_the_app_unknown(tmp_path, adb):
    adb.script["force-stop"] = ["23628\n"]
    runner.collect(PACKAGE, tmp_path / "traces", 1, section={}, name="startup",
                   log=lambda m: None)
    kept = recording_mod.read(tmp_path / "traces" / "startup_iter000.perfetto-trace")
    check("unknown, and the pid still read as left over",
          "app_at_stop" not in kept and kept["runner"] == "launch", kept)


def test_no_stop_no_app_at_the_stop(tmp_path, adb):
    runner.collect(PACKAGE, tmp_path / "traces", 1, section={"reset_policy": "none"},
                   name="startup", log=lambda m: None)
    kept = recording_mod.read(tmp_path / "traces" / "startup_iter000.perfetto-trace")
    check("the policy, the device's settings, and nothing about a stop that was not made",
          kept == {"runner": "launch", "reset_policy": "none", "power": "charging",
                   **SHIPPED}, kept)


def test_the_account_moves_with_its_trace_when_a_set_is_set_aside(tmp_path):
    out = tmp_path / "traces"
    out.mkdir()
    trace = out / "startup_iter000.perfetto-trace"
    trace.write_bytes(b"the baseline")
    recording_mod.write(trace, {"runner": "launch"})
    aside = runner.set_aside(out, "startup", log=lambda m: None)
    check("both moved", sorted(p.name for p in aside.iterdir()) == [
        "startup_iter000.perfetto-trace", "startup_iter000.perfetto-trace.recording.json"],
        list(aside.iterdir()))
    check("nothing left behind", list(out.glob("*.recording.json")) == [])


def test_an_unreadable_account_is_no_account():
    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "t.perfetto-trace"
        recording_mod.sidecar(trace).write_text("[1, 2", encoding="utf-8")
        check("broken JSON", recording_mod.read(trace) == {})
        recording_mod.sidecar(trace).write_text('{"runner": 3}', encoding="utf-8")
        check("a field of the wrong kind", recording_mod.read(trace) == {})


# --- the device's settings: animation scales (#612), "Don't keep activities" (#616)

SHIPPED = {"animation_scales": {"window": 1.0, "transition": 1.0, "animator": 1.0},
           "always_finish_activities": False}
DUMP_HEADER = "ACTIVITY MANAGER RUNNING PROCESSES (dumpsys activity processes)\n"


def settings_answer(window="1.0", transition="1.0", animator="1.0", stored="0",
                    dump: bool | None = False) -> str:
    """What the round's settings read prints: `dump` None for a dump that did
    not answer, else whether ActivityManager has the option on."""
    out = (f"window_animation_scale={window}\ntransition_animation_scale={transition}\n"
           f"animator_duration_scale={animator}\nalways_finish_activities={stored}\n")
    if dump is not None:
        out += DUMP_HEADER + ("  mAlwaysFinishActivities=true\n" if dump else "")
    return out


def scales(n: float) -> dict:
    return {"window": n, "transition": n, "animator": n}


def test_the_settings_read_as_a_phone_comes():
    check("every scale 1, the option off",
          recording_mod.parse_settings(settings_answer()) == SHIPPED)
    check("a scale never set animates at the system's default, 1",
          recording_mod.parse_settings(settings_answer(animator="null"))["animation_scales"]
          == scales(1.0))


def test_dont_keep_activities_is_activity_managers_not_the_settings():
    """`settings put global always_finish_activities 0` on the phone of the
    test: the setting said 0, `dumpsys activity` still said
    `mAlwaysFinishActivities=true`, and starts stayed warm."""
    read = recording_mod.parse_settings
    check("on in ActivityManager, 0 in the setting: on",
          read(settings_answer(stored="0", dump=True))["always_finish_activities"] is True)
    check("1 in the setting, no line in the dump: off",
          read(settings_answer(stored="1", dump=False))["always_finish_activities"] is False)
    check("no dump: the setting stands in",
          read(settings_answer(stored="1", dump=None))["always_finish_activities"] is True
          and read(settings_answer(stored="null", dump=None))["always_finish_activities"]
          is False)


def test_a_device_that_does_not_say_leaves_its_settings_unknown():
    read = recording_mod.parse_settings
    check("nothing printed", read("") == {"animation_scales": None,
                                          "always_finish_activities": None})
    check("a scale that is not a number takes the three with it",
          read(settings_answer(window="fast"))["animation_scales"] is None)
    check("nor a negative one",
          read(settings_answer(transition="-1"))["animation_scales"] is None)


def test_collect_reads_the_settings_once_a_round_and_keeps_them_beside_every_trace(
        tmp_path, adb):
    adb.script["settings"] = [settings_answer("5", "5", "5", stored="0", dump=True)]
    out = tmp_path / "traces"
    runner.collect(PACKAGE, out, 3, section={}, name="startup", log=lambda m: None)
    check("one read for three repeats", adb.kinds().count("settings") == 1, adb.kinds())
    check("asked of the device it records on",
          next(c for c in adb.calls if kind(c) == "settings")[:4]
          == ["adb", "-s", adb.calls[1][2], "shell"], adb.calls)
    kept = [recording_mod.read(out / f"startup_iter00{i}.perfetto-trace") for i in range(3)]
    check("each trace's account has them",
          all(k["animation_scales"] == scales(5.0) and k["always_finish_activities"] is True
              for k in kept), kept)


def test_a_settings_read_that_fails_costs_no_repeat(tmp_path):
    fake = FakeAdb(fail="settings")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "_run", fake)
        mp.delenv("ANDROID_SERIAL", raising=False)
        results = runner.collect(PACKAGE, tmp_path / "traces", 2, section={},
                                 name="startup", log=lambda m: None)
    kept = recording_mod.read(tmp_path / "traces" / "startup_iter000.perfetto-trace")
    check("the set recorded", len(results) == 2, results)
    check("and the settings unknown, not guessed",
          not {"animation_scales", "always_finish_activities"} & set(kept), kept)


def test_an_account_from_before_the_settings_reads_without_them(tmp_path):
    trace = tmp_path / "startup_iter000.perfetto-trace"
    recording_mod.sidecar(trace).write_text(json.dumps(
        {"echolot": "0.17.0", "runner": "launch", "reset_policy": "force-stop",
         "app_at_stop": "running"}), encoding="utf-8")
    kept = recording_mod.read(trace)
    check("what it kept, and nothing more",
          kept == {"runner": "launch", "reset_policy": "force-stop", "app_at_stop": "running"},
          kept)
    recording_mod.sidecar(trace).write_text(json.dumps(
        {"runner": "launch", "animation_scales": {"window": "1"},
         "always_finish_activities": 1}), encoding="utf-8")
    check("settings of the wrong kind read as not kept",
          recording_mod.read(trace) == {"runner": "launch"}, recording_mod.read(trace))


def test_analyze_carries_the_settings_into_the_report_and_its_header(tmp_path):
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(fixture.build())
    recording_mod.write(trace, {"runner": "command", "reset_policy": "none",
                                "animation_scales": scales(5.0),
                                "always_finish_activities": True})
    rep = selftest._analyze(trace, Config(selftest.FIXTURE_CONFIG))
    rec = rep["recording"]
    check("in the recording block",
          rec["animation_scales"] == scales(5.0) and rec["always_finish_activities"] is True,
          rec)
    head = report_mod.to_markdown(rep)
    check("the header names the scales",
          "Animation scales: all three `5`, where a phone comes with 1" in head, head[:3000])
    check("and warns of the option, for a scenario that returns to the app",
          '> ⚠️ "Don\'t keep activities" was on' in head
          and "a warm start where a phone without it starts hot" in head, head[:3000])


def test_the_header_says_nothing_of_a_phone_as_it_comes_or_of_one_nobody_read():
    lines = recording_mod.header_lines
    check("defaults", lines({"runner": "launch", **SHIPPED}) == [])
    check("unknown", lines({"runner": "launch", "animation_scales": None,
                            "always_finish_activities": None}) == [])
    check("a report from before the block", lines(None) == [])


def test_the_header_words_the_option_by_what_the_set_measured():
    cold = recording_mod.header_lines({"runner": "launch", "reset_policy": "force-stop",
                                       "animation_scales": scales(1.0),
                                       "always_finish_activities": True})
    check("a cold start: said, not warned",
          len(cold) == 1 and not cold[0].startswith(">")
          and "A cold start creates its activity anyway" in cold[0], cold)
    off = recording_mod.header_lines({"runner": "launch", "animation_scales": scales(0.0),
                                      "always_finish_activities": False})
    check("animations off, by name", off == [
        "Animation scales: all three `0`, where a phone comes with 1: animations were off, "
        "so the frames a phone draws for them, and the work behind those frames, are not "
        "in these numbers. Developer options set them back."], off)
    one = recording_mod.header_lines({"animation_scales": {"window": 1.0, "transition": 1.0,
                                                           "animator": 0.5}})
    check("each scale named where they differ",
          one and one[0].startswith("Animation scales: window `1`, transition `1`, "
                                    "animator `0.5`,"), one)


def test_the_header_says_a_setting_only_some_repeats_had():
    """Two of five repeats with every scale at 5 and "Don't keep activities"
    on, three at the defaults: the header said nothing, since the set's value
    is the majority's (#850)."""
    split = recording_mod.merge(blocks(
        animation_scales=[scales(5.0)] * 2 + [scales(1.0)] * 3,
        always_finish_activities=[True] * 2 + [False] * 3,
        reset_policy=["none"] * 5))
    lines = recording_mod.header_lines(split)
    check("two lines", len(lines) == 2, lines)
    check("the scales, with how many repeats",
          lines[0].startswith("Animation scales: all three `5` in 2 of 5 repeats, where a phone "
                              "comes with 1"), lines)
    check("and the option, warned, with how many",
          lines[1].startswith('> ⚠️ "Don\'t keep activities" was on in 2 of 5 repeats:'), lines)
    two = recording_mod.header_lines(recording_mod.merge(blocks(
        animation_scales=[scales(0.0), scales(5.0), scales(1.0), scales(1.0)])))
    check("two values other than 1, each with its share",
          two and "all three `0` in 1 of 4 repeats and all three `5` in 1 of 4 repeats" in two[0],
          two)


def test_compare_says_when_the_device_animated_or_kept_activities_differently():
    """The list scroll of #612: all three scales at 5 on one side and at 1 on
    the other, rows that held, and no warning."""
    slow = recording_mod.merge(blocks(animation_scales=[scales(5.0)] * 4,
                                      always_finish_activities=[True] * 4))
    plain = recording_mod.merge(blocks(animation_scales=[scales(1.0)] * 4,
                                       always_finish_activities=[False] * 4))
    cmp = compare(side(BEFORE, recording=slow), side(AFTER, recording=plain))
    said = text(cmp, "recording")
    check("the scales, both sides",
          "the device's animation scales all three `5` before and all three `1` after"
          in said, said)
    check("and the option", '"Don\'t keep activities" on before and off after' in said, said)
    check("said, not refused: the verdicts stand",
          holds(cmp) == {"main_thread_block": True, "io_wait": True}, holds(cmp))
    cmp = compare(side(BEFORE, recording=plain), side(AFTER, recording=plain))
    check("the same settings: nothing",
          not {"recording", "recording-unknown", "recording-mixed"} & warned(cmp),
          cmp["warnings"])


def test_a_side_whose_repeats_ran_with_other_settings_is_said():
    split = recording_mod.merge(blocks(
        animation_scales=[scales(5.0), scales(5.0), scales(1.0), scales(1.0)],
        always_finish_activities=[False] * 4))
    plain = recording_mod.merge(blocks(animation_scales=[scales(1.0)] * 4,
                                       always_finish_activities=[False] * 4))
    cmp = compare(side(BEFORE, recording=split), side(AFTER, recording=plain))
    check("the before side's split, worded",
          "the device's animation scales all three `5` in 2 of 4, all three `1` in 2 of 4"
          in text(cmp, "recording-mixed"), cmp["warnings"])


def test_a_set_recorded_before_the_settings_were_read_is_unknown_never_the_same():
    """The orchestrated case of #612: a baseline from yesterday's `collect`
    against a round recorded today."""
    old = recording_mod.merge(blocks(runner=["launch"] * 4))
    new = recording_mod.merge(blocks(animation_scales=[scales(1.0)] * 4,
                                     always_finish_activities=[False] * 4))
    cmp = compare(side(BEFORE, recording=old), side(AFTER, recording=new))
    said = text(cmp, "recording-unknown")
    check("could not be checked, said",
          "The before side does not say the device's animation scales nor whether "
          '"Don\'t keep activities" was on' in said
          and "cannot be checked" in said, cmp["warnings"])
    check("no difference claimed", "recording" not in warned(cmp), cmp["warnings"])
    cmp = compare(side(BEFORE, recording=old), side(AFTER, recording=old))
    check("two sets from before: nothing",
          not {"recording", "recording-unknown", "recording-mixed"} & warned(cmp),
          cmp["warnings"])
    gradle = recording_mod.merge(blocks(runner=["gradle"] * 4, reset_policy=[None] * 4,
                                        app_at_stop=[None] * 4))
    cmp = compare(side(BEFORE, recording=gradle), side(AFTER, recording=new))
    check("a Macrobenchmark's round, which never reads them, is not called unknown",
          "recording-unknown" not in warned(cmp), cmp["warnings"])
