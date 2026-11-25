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
from echolot import names as names_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot import render as render_mod  # noqa: E402
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
    lines = render_mod._environment_lines({
        "cpu": {"mean_mhz": 1500.0, "on_cpu_ms": 900.0, "measured_ms": 900.0},
        "thermal": None, "memory": None, "missing": ["memory", "thermal"]})
    text = "\n".join(lines)
    check("the clock is there", "clock **1500 MHz**" in text, text)
    check("and the line names what is not, without saying compare cannot judge",
          "Device state not recorded: memory, thermal" in text
          and "cannot tell a slower machine" not in text
          and "whether the kernel throttled" in text, text)

    lines = render_mod._environment_lines({
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


def test_an_end_that_never_closed_in_one_repeat_is_said() -> None:
    """The merged anchor is the repeat with the fewest matches; an end left
    open in another repeat ran that window to the end of its trace."""
    reps = [_windowed(1000.0), _windowed(1050.0), _windowed(1900.0)]
    reps[2]["window"]["end_anchor"] = {**reps[2]["window"]["end_anchor"], "unfinished": True,
                                       "matches": 5}
    merged = report_mod.aggregate(reps)
    check("kept", merged["window"]["end_anchor"].get("unfinished") is True,
          merged["window"]["end_anchor"])
    check("and said", "never closed" in report_mod.to_markdown(merged))


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


LOCK = "Lock contention on a monitor lock (owner tid: #)"


def _lock(tid: int, self_ms: float) -> dict:
    """A lock wait as a detector returns it since #831: under the folded
    name, with the name the trace had in `variants`."""
    return row(LOCK, count=1, self_ms=self_ms, total_ms=self_ms, max_ms=self_ms,
               detail="main", variants={"location": [
                   f"Lock contention on a monitor lock (owner tid: {tid})"]})


def test_one_wait_owned_by_another_tid_each_run_is_one_row() -> None:
    """#504: the owner's tid is the runtime's choice, and a wait that happens
    in every run is one row, 3/3, with the tids kept beside it. The detector
    folds the name (#831); the merge keeps every repeat's variants."""
    reps = [one([_lock(tid, ms)], det_id="main_thread_block")
            for tid, ms in ((15427, 110.0), (10359, 120.0), (9001, 115.0))]
    for rep in reps:
        rep["detectors"][0]["identity"] = ["location", "detail"]
    rows = report_mod.aggregate(reps)["detectors"][0]["rows"]
    check("one row", len(rows) == 1, rows)
    got = rows[0]
    check("named without the tid",
          got["location"] == "Lock contention on a monitor lock (owner tid: #)", got)
    check("seen in every run, with the median of the three",
          got["runs"] == "3/3" and got["self_ms"] == 115.0, got)
    check("the tids as the trace had them are kept",
          got["variants"]["location"] == sorted(
              f"Lock contention on a monitor lock (owner tid: {t})"
              for t in (15427, 10359, 9001)), got)


def test_the_merge_folds_nothing_itself() -> None:
    """#831: a name is folded once, by the detector. Rows that reach the merge
    under two names are two rows: the merge has no rule of its own to make
    them one, which is how one recording and a set came to disagree."""
    def cpu(name: str, ms: float) -> dict:
        return row(name, count=0, total_ms=ms, covered_ms=0.0,
                   detail="100% of CPU outside slices")
    reps = [one([cpu(f"Thread-{n}", 250.0 + n)], det_id="uninstrumented_cpu")
            for n in (31, 34, 39)]
    rows = report_mod.aggregate(reps)["detectors"][0]["rows"]
    check("three names, three rows",
          sorted(r["location"] for r in rows) == ["Thread-31", "Thread-34", "Thread-39"], rows)


def test_numbers_that_say_where_or_what_are_kept() -> None:
    fold = names_mod.runtime_name
    check("a frame's vsync id goes", fold("Choreographer#doFrame 55112")
          == "Choreographer#doFrame #")
    check("pool and binder threads lose their numbers",
          [fold(n) for n in ("pool-3-thread-2", "binder:1234_5", "AsyncTask #2",
                             "DefaultDispatcher-worker-3")]
          == ["pool-#-thread-#", "binder:#_#", "AsyncTask #", "DefaultDispatcher-worker-#"])
    note = ("monitor contention with owner Thread-3 (4455) at void "
            "com.example.Store.put(java.lang.String)(Store.java:41) waiters=1 "
            "blocking from java.lang.Object com.example.Store.get()(:-1)")
    check("a lock note keeps its lines and loses the owner's numbers",
          fold(note) == note.replace("Thread-3 (4455)", "Thread-# (#)")
          .replace("waiters=1", "waiters=#"), fold(note))
    check("a marker keeps its digits, by prefix or by name",
          fold("AGENTTMP_fill_v4", keep="AGENTTMP_") == "AGENTTMP_fill_v4"
          and fold("step_2", names={"step_2"}) == "step_2"
          and fold("AGENTTMP_step_2", keep="AGENTTMP_") == "AGENTTMP_step_2")


def test_a_tid_after_an_equals_sign_is_folded_too() -> None:
    """#847: ART writes `… for tid=23762`, and the fold took `tid: N` and
    `tid N` only, so the section was another row in every launch."""
    fold = names_mod.runtime_name
    name = "SuspendPlatformOrVirtualThread suspended Firebase Blocking Thread #0 for tid=23762"
    check("the tid goes, as the thread's number does",
          fold(name) == "SuspendPlatformOrVirtualThread suspended Firebase Blocking Thread # for tid=#",
          fold(name))
    check("and the two spellings it took before still fold",
          fold("(owner tid: 13533)") == "(owner tid: #)" and fold("tid 4202") == "tid #")


def test_a_frame_that_is_another_vsync_each_run_is_one_row_in_compare() -> None:
    """#504's comment: ten `Choreographer#doFrame <N>`, one per run, were
    twenty rows without a verdict in compare."""
    def side(base: float) -> dict:
        return report_mod.aggregate([
            one([row(names_mod.runtime_name(f"Choreographer#doFrame {55000 + i * 7}"),
                     count=1, self_ms=base + i, total_ms=base + i, max_ms=base + i,
                     detail="main")], det_id="main_thread_block")
            for i in range(5)])
    cmp = compare_mod.build(side(4200.0), side(4200.0),
                            before_path="b.json", after_path="a.json")
    frames = [r for r in cmp["rows"] if "doFrame" in r["location"]]
    check("one row on both sides, no vanished or appeared",
          len(frames) == 1 and frames[0]["change"] not in ("vanished", "appeared"),
          frames)


def _slice(ms: float) -> dict:
    return row("draw", count=1, self_ms=ms, total_ms=ms, max_ms=ms, detail="main")


def _below(rows: list[dict], measured: list[dict] | None) -> dict:
    """One repeat of a detector with `@fires`: the rows it shows, and the
    rows it measured, shown or not. None for a repeat that handed nothing
    under the bar."""
    rep = one(rows, det_id="main_thread_block")
    rep["detectors"][0]["identity"] = ["location", "detail"]
    if measured is not None:
        shown = [tuple(sorted(r.items())) for r in rows]
        rep["detectors"][0][report_mod.UNDER_ROWS] = [
            r for r in measured if tuple(sorted(r.items())) not in shown]
    return rep


def test_a_row_below_the_bar_in_two_repeats_is_present_in_all_three() -> None:
    """#505: 20 ms in one repeat, 12 ms in two, threshold 16."""
    reps = [_below([_slice(20.0)], [_slice(20.0)]),
            _below([], [_slice(12.0)]),
            _below([], [_slice(12.0), row("other", self_ms=1.0, detail="main")])]
    merged = report_mod.aggregate(reps)
    got = merged["detectors"][0]["rows"]
    check("one row: the one that fired, not every name it measured",
          [r["location"] for r in got] == ["draw"], got)
    check("present in all three, over the bar in one",
          got[0]["runs"] == "3/3" and got[0]["fired"] == "1/3", got[0])
    check("the median is over every repeat",
          got[0]["self_ms"] == 12.0
          and got[0]["spread"]["self_ms"]["values"] == [20.0, 12.0, 12.0], got[0])
    check("what it measured under the bar is not written beside the rows",
          all(report_mod.UNDER_ROWS not in d for d in merged["detectors"]), merged)
    md = report_mod.to_markdown(merged)
    check("report.md says both counts", "3/3, over the bar in 1" in md, md)


def test_a_row_the_open_query_did_not_find_either_is_absent() -> None:
    reps = [_below([_slice(20.0)], [_slice(20.0)]), _below([], []),
            _below([], None)]
    got = report_mod.aggregate(reps)["detectors"][0]["rows"][0]
    check("absent where it was not there, and where nobody looked",
          got["runs"] == "1/3" and "fired" not in got, got)


def test_compare_takes_the_values_under_the_bar_and_no_zeros() -> None:
    """#505: a row reported in 7 of 7 against 3 of 8, present in all 15, is
    judged on its 15 values, not on 5 zeros."""
    def side(reported: list[bool]) -> dict:
        return report_mod.aggregate([
            _below([_slice(20.0)] if fired else [], [_slice(20.0 if fired else 19.5)])
            for fired in reported])
    before, after = side([True] * 7), side([True] * 3 + [False] * 5)
    row_after = after["detectors"][0]["rows"][0]
    check("the after side counts every run it happened in",
          row_after["runs"] == "8/8" and row_after["fired"] == "3/8", row_after)
    cmp = compare_mod.build(before, after, before_path="b.json", after_path="a.json")
    draw = next(r for r in cmp["rows"] if r["location"] == "draw")
    check("no shrink made of zeros", draw["change"] == compare_mod.STEADY
          and not draw.get("shares"), draw)


def test_analyze_keeps_the_rows_below_the_bar_for_the_merge(tmp_path) -> None:
    from echolot import fixture, selftest
    from echolot.config import Config
    from echolot.analyze import analyze_trace

    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    cfg = Config(selftest.FIXTURE_CONFIG)
    # `collection_mapping` takes 120 ms of the main thread: over a bar of 100,
    # under one of 200.
    reps = [analyze_trace(trace, cfg,
                          cli_overrides={"main_thread_block": {"min_slice_ms": bar}})
            for bar in (100, 200, 200)]
    merged = report_mod.aggregate(reps)
    det = next(d for d in merged["detectors"] if d["id"] == "main_thread_block")
    got = next(r for r in det["rows"] if r["location"] == "collection_mapping")
    check("found in all three, reported in one",
          got["runs"] == "3/3" and got["fired"] == "1/3", got)
    single = analyze_trace(trace, cfg)
    check("a report of one recording carries none of it once merged",
          report_mod.UNDER_ROWS not in next(
              d for d in report_mod.aggregate([single])["detectors"]
              if d["id"] == "main_thread_block"))


def test_analyze_keeps_what_it_found_under_the_bar_in_no_repeat(tmp_path) -> None:
    """#715: what a report leaves out under its threshold is kept as `below`,
    for `compare` to look a row the other side reports up in."""
    from echolot import fixture, selftest
    from echolot.config import Config
    from echolot.analyze import analyze_trace

    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    cfg = Config(selftest.FIXTURE_CONFIG)
    for n in (1, 2):
        reps = [analyze_trace(trace, cfg,
                              cli_overrides={"main_thread_block": {"min_slice_ms": 200}})
                for _ in range(n)]
        det = next(d for d in report_mod.aggregate(reps)["detectors"] if d["id"] == "main_thread_block")
        under = {r["location"]: r for r in det["below"]["rows"]}
        reported = {r["location"] for r in det["rows"]}
        check(f"{n} recording(s): `collection_mapping`, 120 ms under a bar of 200, is kept under it",
              "collection_mapping" in under and under["collection_mapping"]["runs"] == f"{n}/{n}"
              and "collection_mapping" not in reported, det["below"])
        check(f"{n} recording(s): nothing reported is kept twice, nothing under the floor, no evidence",
              not reported & set(under) and det["below"]["floor_ms"] == report_mod.FLOOR_MS
              and all(max(r["spread"]["self_ms"]["values"] if "spread" in r else [r["self_ms"]])
                      >= report_mod.FLOOR_MS for r in under.values())
              and not any("stacks" in r for r in under.values()), det["below"])


def _marker_runs(totals: list[float], open_at: set[int]) -> list[dict]:
    reps = []
    for i, ms in enumerate(totals):
        rows = [{"location": "collection_load", "count": 1, "self_ms": ms,
                 "total_ms": ms, "max_ms": ms, "detail": "(async)",
                 **({"unfinished": True} if i in open_at else {})}]
        reps.append(report_mod.build(
            f"t{i}", {"process": "p", "duration_ms": 1.0}, [],
            markers={"prefix": "AGENTTMP_", "globs": ["AGENTTMP_*"], "rows": rows,
                     "absent": []}))
    return reps


def test_a_marker_open_in_one_repeat_of_three_leaves_its_median_a_number() -> None:
    """#513: the open repeat is the longest, so the median does not move
    whatever it really took. The longest does: it is that repeat's."""
    merged = report_mod.aggregate(_marker_runs([1680.0, 1500.0, 6100.0], {2}))
    got = merged["markers"]["rows"][0]
    check("the median is a number, and the open repeat is counted",
          got["total_ms"] == 1680.0 and "total_ms" not in got.get("floors", [])
          and "self_ms" not in got.get("floors", [])
          and got["unfinished_runs"] == "1/3", got)
    check("the longest is the open repeat's, so it is a floor (#512)",
          got["max_ms"] == 6100.0 and got["floors"] == ["max_ms"], got)
    md = report_mod.to_markdown(merged)
    check("report.md prints no ≥ on the median, and one on the longest",
          "≥ 1680" not in md and "≥ 6100.0" in md, md)
    check("and says it did not close in one repeat",
          "`collection_load` did not close in 1 of 3 repeats (there: ≥ 6100.0 ms). "
          "Only the numbers marked ≥ depend on those repeats." in md, md)


def test_a_marker_open_in_two_repeats_of_three_is_a_floor() -> None:
    got = report_mod.aggregate(_marker_runs([1680.0, 5900.0, 6100.0], {1, 2}))
    row_ = got["markers"]["rows"][0]
    check("the median is a floor", row_.get("unfinished") is True
          and row_["unfinished_runs"] == "2/3" and "total_ms" in row_["floors"], row_)
    md = report_mod.to_markdown(got)
    check("and printed as one", f"≥ {row_['total_ms']}" in md, md)


def test_a_marker_cut_short_in_one_repeat_holds_the_median_down() -> None:
    """Open below the middle: the real median could be higher, so a floor."""
    got = report_mod.aggregate(_marker_runs([1680.0, 1700.0, 400.0], {2}))
    row_ = got["markers"]["rows"][0]
    check("a floor", row_.get("unfinished") is True and row_["total_ms"] == 1680.0, row_)


def test_a_bar_that_is_not_calibrated_still_opens_for_the_merge(tmp_path) -> None:
    """#666 took `@calibrate` off `main_thread_block`, and its bar stopped
    opening: a row under 16 ms in one repeat read as absent again (#505).
    The bar is the predicate now, and no query filters by it (#832)."""
    from echolot.analyze import DETECTOR_DIR
    from echolot.tp import Detector
    det = Detector.from_file(DETECTOR_DIR / "main_thread_block.sql")
    check("min_slice_ms is in the predicate and not a calibration",
          "{{min_slice_ms}}" in det.fires and not det.calibrations, (det.fires, det.calibrations))
    code = "\n".join(ln for ln in det.sql.splitlines() if not ln.strip().startswith("--"))
    check("and in no query", "{{min_slice_ms}}" not in code, code[-400:])


def test_one_recording_names_rows_as_a_set_does() -> None:
    """#714: a report of one recording names its rows as the merge of several
    does. Both take the name the detector folded (#831), and keep what the
    trace had beside it."""
    lock = report_mod.aggregate([one([_lock(15427, 110.0)], det_id="main_thread_block")])
    got = lock["detectors"][0]["rows"][0]
    check("the owner's tid is `#`", got["location"] == LOCK, got)
    check("as the trace had it, beside it",
          got["variants"] == {"location": [
              "Lock contention on a monitor lock (owner tid: 15427)"]}, got)
    check("and no column a merge adds", "runs" not in got and "spread" not in got, got)
    two = report_mod.aggregate([one([_lock(15427, 110.0)], det_id="main_thread_block"),
                                one([_lock(10359, 120.0)], det_id="main_thread_block")])
    check("the name the merge of two gives",
          two["detectors"][0]["rows"][0]["location"] == got["location"], two)
