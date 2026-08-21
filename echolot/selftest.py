"""The self-check: proof that the pipeline computes correctly on THIS machine.

The list of checks below is the fixture's specification in executable form:
what the detectors must find and, just as importantly, what they must NOT. A
false positive costs the agent more than a miss — it will go off investigating
a problem that does not exist and burn its context window.

Checking that dependencies are installed is deliberately absent — pip fails
loudly without us. The value is elsewhere: showing that the environment gives
CORRECT answers on a trace whose contents are known.

Run through `echolot doctor`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import fixture
from . import recorder
from .config import Config, ConfigError

# The config is kept in memory rather than in a file: the self-check must not
# depend on what happens to lie on disk nearby. The detectors section is
# deliberately absent, so every detector is enabled with the defaults from the
# .sql files themselves. That is exactly how the defaults a user gets without
# configuring anything are exercised.
FIXTURE_CONFIG = {
    "project": {"package": "com.example.app", "process": "com.example.app"},
    "scenario": {
        "name": "fixture",
        "start": {"name": "AppStart"},
        "end": {"name": "Screen.firstFrame"},
    },
}

# A detector the fixture cannot make fire, with the reason it cannot.
#
# Written down rather than counted, and named rather than numbered: whoever
# adds a detector still has to plant a problem for it or say here why the
# fixture is the wrong shape to hold one. `anr_risk` is the wrong shape by
# construction — its bar is the platform's five seconds, and this fixture is a
# cold start a second long. A window that could hold a six-second freeze would
# be a different fixture, and every other check reads this one.
#
# It is not left untested for that: its own checks below drive it against this
# fixture with the bar lowered, where the arithmetic and the splitting are the
# same code, and one of them holds it to silence at the shipped bar.
SILENT_ON_FIXTURE = {
    "anr_risk": "its bar is five seconds and this fixture is a one-second "
                "cold start",
}

CHECKS: list[tuple[str, object]] = []


def _shipped() -> int:
    """How many detectors this package ships."""
    from .main import DETECTOR_DIR
    from .tp import load_detectors
    return len(load_detectors(DETECTOR_DIR))


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --- helpers ---------------------------------------------------------------

def rows(report, detector_id):
    for d in report["detectors"]:
        if d["id"] == detector_id:
            if d["error"]:
                raise AssertionError(f"the detector failed: {d['error']}")
            return d["rows"]
    raise AssertionError(f"detector {detector_id} is not in the report")


def locations(report, detector_id):
    return [r["location"] for r in rows(report, detector_id)]


def only_row(report, detector_id):
    got = rows(report, detector_id)
    assert len(got) == 1, f"expected one row, got {len(got)}: {got}"
    return got[0]


def no_slice_named(report, needle):
    """No detector may show this slice."""
    for d in report["detectors"]:
        for r in d["rows"]:
            blob = f"{r.get('location')} {r.get('detail')}"
            assert needle not in blob, \
                f"{needle} surfaced in detector {d['id']}: {r}"


# --- the window and the process --------------------------------------------

@check("scenario window built from the anchors: 1005 ms")
def _(report):
    assert report["window"]["duration_ms"] == 1005.0, report["window"]


@check("the right process is picked, the foreign one is dropped")
def _(report):
    assert report["window"]["process"] == "com.example.app"
    no_slice_named(report, "other_app_huge_OUTSIDE")


@check("both anchors actually matched in the trace")
def _(report):
    w = report["window"]
    assert w["start_anchor"]["matches"] == 1, w["start_anchor"]
    assert w["end_anchor"]["matches"] == 1, w["end_anchor"]


@check("every shipped detector ran, and every one fired")
def _(report):
    # Counted rather than written down. The fixture's promise is that it plants
    # a problem for each detector, and a detector added to sql/detectors/ needs
    # no registration in code — a number here would quietly turn both of those
    # into "for the six that existed when this line was written".
    s = report["summary"]
    assert s["detectors_run"] == _shipped(), s
    expected = _shipped() - len(SILENT_ON_FIXTURE)
    assert s["detectors_fired"] == expected, (s["fired_ids"], SILENT_ON_FIXTURE)
    for detector, why in SILENT_ON_FIXTURE.items():
        assert detector not in s["fired_ids"], (
            f"{detector} fired after all — it is excused because {why}, and "
            f"that reason no longer holds")


@check("slices outside the window stayed out of the report")
def _(report):
    no_slice_named(report, "Bootstrap_OUTSIDE")   # 50 ms before the window
    no_slice_named(report, "After_OUTSIDE")       # 200 ms after the window


# --- detectors -------------------------------------------------------------

@check("main_thread_block: found the 120 ms, skipped the 5 ms")
def _(report):
    loc = locations(report, "main_thread_block")
    assert "collection_mapping" in loc, loc
    assert "quick_thing" not in loc, "a 5 ms slice must not clear a 16 ms bar"
    row = next(r for r in rows(report, "main_thread_block")
               if r["location"] == "collection_mapping")
    assert row["total_ms"] == 120.0, row
    assert row["self_ms"] == 120.0, f"no children — self equals total: {row}"


@check("main_thread_block: a wrapper's self time is below its total")
def _(report):
    # AppStart runs for 1006 ms but holds every other slice of the window
    # inside it. It used to arrive in the report with its full 1006 ms
    # alongside its children, and the same time was counted twice. On a live
    # trace that duplicated a 355 ms frame three ways: doFrame →
    # doFrame-resynced → traversal, three rows about one event.
    row = next(r for r in rows(report, "main_thread_block")
               if r["location"] == "AppStart")
    assert row["total_ms"] == 1006.0, row
    assert row["self_ms"] < row["total_ms"], f"children not subtracted: {row}"
    assert row["self_ms"] == 275.0, f"1006 minus 731 ms of children: {row}"


@check("main_thread_block: self time never exceeds the window")
def _(report):
    # The defining property of self time: the terms do not overlap. If the sum
    # exceeds the window, double counting has crept back in somewhere.
    total_self = sum(r["self_ms"] for r in rows(report, "main_thread_block"))
    window = report["window"]["duration_ms"]
    assert total_self <= window, (
        f"self time sums to {total_self} ms against a {window} ms window"
    )


@check("binder_txn: fired on the long transaction, async does not count")
def _(report):
    row = only_row(report, "binder_txn")
    # The group fires because of the 25 ms one, but both synchronous
    # transactions go into the sum: 25 + 3. Showing "25 ms total" would
    # misstate the cost.
    assert row["max_ms"] == 25.0, row
    assert row["count"] == 2, f"both sync transactions belong to the group: {row}"
    assert row["total_ms"] == 28.0, row
    assert row["detail"] == "main thread", row


@check("the async transaction is still visible as a long slice on main")
def _(report):
    # Dropping it from binder_txn does not mean hiding it: the user still sees
    # 40 ms on the main thread, and main_thread_block must say so.
    assert "binder transaction async" in locations(report, "main_thread_block")


@check("monitor_contention: a thread's blocks collapse into one row")
def _(report):
    # main has two blocks with DIFFERENT owners (tid 4201 and 4202). The tid
    # sits inside the slice name, so grouping by name would give two rows of 30
    # and 12 ms instead of one finding of 42. On a live trace 190 blocks
    # scattered exactly that way and the detector stayed silent.
    got = [r for r in rows(report, "monitor_contention")
           if r["location"] == "m.example.app"]
    assert len(got) == 1, f"the finding shattered across owners: {got}"
    assert got[0]["count"] == 2, got[0]
    assert got[0]["total_ms"] == 42.0, got[0]
    assert got[0]["max_ms"] == 30.0, got[0]
    # The evidence carries the name of the LONGEST block, with its owner's tid.
    assert "owner tid: 4201" in got[0]["detail"], got[0]


@check("monitor_contention: runtime-internal locks are filtered out")
def _(report):
    # 'Lock contention on GC lock' is an ART-internal lock with no application
    # code behind it. The old '*ock contention*' mask dragged it in; after the
    # narrowing against a live trace (see the detector header) it must not.
    details = [r["detail"] for r in rows(report, "monitor_contention")]
    assert not any("GC lock" in d for d in details), (
        f"over-matching on runtime locks is back: {details}"
    )


@check("gc_pressure: 20 collection cycles totalling 80 ms")
def _(report):
    got = [r for r in rows(report, "gc_pressure")
           if r["location"] == "Background young concurrent copying GC"]
    assert len(got) == 1, rows(report, "gc_pressure")
    assert got[0]["count"] == 20, got[0]
    assert got[0]["total_ms"] == 80.0, got[0]


@check("gc_pressure: phases inside a cycle are not a separate finding")
def _(report):
    # CopyingPhase lives INSIDE the GC slice. The old thread mask
    # (HeapTaskDaemon*) pulled the phases in alongside the cycle — the same
    # time twice. On a live trace that gave CopyingPhase 295 ms against 282 ms
    # for the whole cycle.
    assert "CopyingPhase" not in locations(report, "gc_pressure"), (
        f"double counting of phases is back: {locations(report, 'gc_pressure')}"
    )


@check("gc_pressure: sees the other side too — waiting on an allocation")
def _(report):
    # waitWhileAllocatingLocked appears on application threads when an
    # allocation stalls waiting for the collector. A real name from Android 14.
    assert "waitWhileAllocatingLocked" in locations(report, "gc_pressure")


@check("main_thread_outlier: one occurrence far outside its own history")
def _(report):
    # Six inflates of 4 ms and one of 44. The sum, 68 ms, is unremarkable and
    # main_thread_block reports it without comment; the single occurrence is
    # the finding, and it is the one a benchmark's P99 was made of.
    row = only_row(report, "main_thread_outlier")
    assert row["location"] == "inflate", row
    assert row["count"] == 1, f"one occurrence was out of line, not the group: {row}"
    assert row["max_ms"] == 44.0, row
    assert "median 4.0 ms of 6" in row["detail"], row["detail"]
    assert "11.0×" in row["detail"], row["detail"]


@check("main_thread_outlier: an even group is not an outlier from itself")
def _(report):
    # Six occurrences of 6 ms. Repetition is not a finding, however much of it
    # there is — that question belongs to main_thread_block and its sums.
    assert "measure" not in locations(report, "main_thread_outlier"), \
        "an even group fired"


@check("main_thread_outlier: three occurrences are not a history")
def _(report):
    # 1, 1, 45 ms. Forty-five times the median of a name seen three times is
    # arithmetic, not evidence, and min_occurrences is what says so.
    assert "Rare_work" not in locations(report, "main_thread_outlier"), \
        "a group below min_occurrences fired"


@check("main_thread_outlier: a large ratio on a small slice is not a finding")
def _(report):
    # Twenty times the median, and twenty milliseconds. Without the absolute
    # floor every short repeated slice in a trace becomes a row.
    assert "Tiny_tick" not in locations(report, "main_thread_outlier"), \
        "a 20x ratio under the absolute floor fired"


@check("main_thread_outlier: slow but even work is not an outlier")
def _(report):
    # Five at 12 ms and one at 44: past the absolute floor, and 3.7x the
    # median. The absolute floor alone would let this through, which is why
    # there is a ratio gate as well — and why this control exists, because
    # without it removing that gate changed nothing in the fixture.
    assert "Steady_heavy" not in locations(report, "main_thread_outlier"), \
        "work that is consistently slow fired as an outlier"


@check("main_thread_outlier: the pair with main_thread_block, not a copy")
def _(report):
    # The same name in both, saying different things: 64 ms of main-thread time
    # in total there, one occurrence of 44 against a median of 4 here. If this
    # detector ever reduces to "the sums again", that is what it will lose.
    block = next(r for r in rows(report, "main_thread_block")
                 if r["location"] == "inflate")
    assert block["count"] == 6 and block["self_ms"] == 64.0, block
    outlier = only_row(report, "main_thread_outlier")
    assert outlier["count"] == 1 and outlier["max_ms"] == 44.0, outlier


@check("runnable_starvation: 50 ms in state R")
def _(report):
    row = only_row(report, "runnable_starvation")
    assert row["location"] == "OkHttp Dispatcher", row
    assert row["total_ms"] == 50.0, row


@check("uninstrumented_cpu: found the thread without a single slice")
def _(report):
    got = [r for r in rows(report, "uninstrumented_cpu")
           if r["location"] == "DefaultDispatcher-worker-1"]
    assert len(got) == 1, rows(report, "uninstrumented_cpu")
    assert got[0]["total_ms"] == 300.0, f"Running inside the window: {got[0]}"
    assert got[0]["count"] == 0, got[0]
    assert got[0]["covered_ms"] == 0.0, got[0]
    # WellInstrumented: 180 ms of slices over 200 ms Running → not blind
    # HeapTaskDaemon:    80 ms of slices over 100 ms Running → not blind


@check("uninstrumented_cpu: coverage is measured on CPU time, not wall clock")
def _(report):
    # BlockingIO-1 sleeps inside its slice: the slice runs 300 ms while the
    # thread is on CPU for only 60 of them, plus 100 ms of Running with no
    # slices at all. Comparing slice duration against on-CPU time would give
    # 300 of 160 = 188% and hide the blind spot. A live cold start showed
    # exactly that: 217% on the main thread.
    got = [r for r in rows(report, "uninstrumented_cpu")
           if r["location"] == "BlockingIO-1"]
    assert len(got) == 1, (
        "a thread sleeping inside a slice must still count as blind: "
        f"{rows(report, 'uninstrumented_cpu')}"
    )
    assert got[0]["total_ms"] == 160.0, got[0]
    assert got[0]["covered_ms"] == 60.0, (
        f"expected the Running/slice intersection, not the slice length: {got[0]}"
    )


@check("uninstrumented_cpu: coverage counts nested slices only once")
def _(report):
    # main: 995 ms of Running inside the window, with the AppStart slice
    # covering the whole window on top. Adding parent to children would exceed
    # everything the thread ever did. The thread is not blind either way, but
    # the arithmetic has to be honest or on a real trace it will mask a genuine
    # blind spot.
    assert "m.example.app" not in locations(report, "uninstrumented_cpu")


# --- calibration -----------------------------------------------------------

@check("calibrate: the measuring pass opens thresholds and strips LIMIT")
def _(report):
    from .main import DETECTOR_DIR
    from .tp import load_detectors
    d = next(x for x in load_detectors(DETECTOR_DIR)
             if x.id == "main_thread_block")
    sql = d.render_open()
    # A statistic over a truncated top twenty would be a statistic over the tail.
    assert "LIMIT" not in sql.upper(), "LIMIT must be stripped while measuring"
    # The threshold is zeroed so HAVING lets the whole distribution through.
    assert ">= 0 * 1000000" in sql, sql[-300:]


@check("calibrate: topN takes the Nth largest, not a percentile")
def _(report):
    from .tp import Calibration
    values = [10.0, 50.0, 30.0, 20.0, 40.0]
    assert Calibration("x", "top", 1, "c", 1.0).value(values) == 50.0
    assert Calibration("x", "top", 3, "c", 1.0).value(values) == 30.0
    # A rank, not a share: the same value holds on a twice-larger sample.
    assert Calibration("x", "top", 3, "c", 1.0).value(values * 2) == 40.0


@check("calibrate: every directive points at a live param and column")
def _(report):
    from .main import DETECTOR_DIR
    from .tp import load_detectors
    from .report import COLUMNS
    for d in load_detectors(DETECTOR_DIR):
        for c in d.calibrations:
            # parse_meta already validates the param; here it is the column —
            # a typo there would silently yield an empty sample and a "kept the
            # default".
            assert c.column in COLUMNS, (
                f"{d.id}: {c.expr} refers to a column outside the report "
                f"contract"
            )


@check("collect: a previous set of traces is set aside, never overwritten")
def _(report):
    from .runner import set_aside
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "traces"
        assert set_aside(out, "run", log=lambda s: None) is None, "nothing to move yet"
        out.mkdir()
        for i in range(3):
            (out / f"run_iter{i:03d}.perfetto-trace").write_bytes(b"x")
        (out / "other_iter000.perfetto-trace").write_bytes(b"y")
        said = []
        aside = set_aside(out, "run", log=said.append)
        assert aside and aside.parent == out and aside.name.startswith("run-"), aside
        assert sorted(p.name for p in aside.iterdir()) == [
            f"run_iter{i:03d}.perfetto-trace" for i in range(3)], list(aside.iterdir())
        assert not list(out.glob("run_iter*.perfetto-trace")), "the old names are free again"
        assert (out / "other_iter000.perfetto-trace").exists(), "another scenario is not touched"
        assert said and "set aside" in said[0], said


@check("collect: a scenario that overruns is an error, and it stops")
def _(report):
    """Two halves of one failure, and neither used to hold.

    `subprocess.TimeoutExpired` went straight past `collect`'s
    `except RunnerError` and out of the CLI as a traceback, which `reflect`
    then files under "a traceback is a bug in echolot".

    And killing a `shell=True` child kills `sh -c` and nothing it started. The
    scenario carried on into the next iteration, driving the app while the
    next trace was being recorded — the one failure mode a repeat cannot
    survive.
    """
    import time
    from . import runner

    with tempfile.TemporaryDirectory() as tmp:
        sentinel = Path(tmp) / "still-running"
        # A grandchild that outlives the shell, and says so if it does.
        started = time.monotonic()
        try:
            runner.run_command(
                f"(sleep 1; touch {sentinel}) & sleep 30", timeout=0.4,
                knob="runner.duration_ms")
        except runner.RunnerError as e:
            assert "still running after 0.4s" in str(e), e
            assert "runner.duration_ms" in str(e), e
        else:
            raise AssertionError("an overrunning scenario must be a RunnerError")
        assert time.monotonic() - started < 5, "the timeout did not stop it"
        time.sleep(1.4)
        assert not sentinel.exists(), (
            "the shell was killed and its scenario kept going — it would drive "
            "the app through the next iteration's recording")

    # A command that merely fails is the other error, and still says so.
    try:
        runner.run_command("echo boom >&2; exit 3", timeout=30)
    except runner.RunnerError as e:
        assert "returned 3" in str(e) and "boom" in str(e), e
    else:
        raise AssertionError("a non-zero scenario must be a RunnerError")


@check("thresholds: --set is typed and refuses what does not exist")
def _(report):
    from .main import DETECTOR_DIR, parse_set
    from .tp import load_detectors
    dets = load_detectors(DETECTOR_DIR)
    got = parse_set(["main_thread_block.min_slice_ms=16",
                     "binder_txn.name_glob=binder*",
                     "gc_pressure.max_total_ms=4.5"], dets)
    assert got == {"main_thread_block": {"min_slice_ms": 16},
                   "binder_txn": {"name_glob": "binder*"},
                   "gc_pressure": {"max_total_ms": 4.5}}, got
    for bad in ("nope.min_slice_ms=1", "main_thread_block.nope=1", "garbage"):
        try:
            parse_set([bad], dets)
        except ConfigError:
            continue
        raise AssertionError(f"--set accepted '{bad}'")


@check("thresholds: the report says where each detector's numbers came from")
def _(report):
    from .main import plan_detectors
    # every detector in the fixture run uses the shipped defaults
    assert all(d["params_source"] == "default" for d in report["detectors"]), \
        [(d["id"], d["params_source"]) for d in report["detectors"]]
    assert not any("defaults" in d for d in report["detectors"])

    # The rules, without a trace: which detectors, which overrides, from
    # where. Four analyze runs here once cost doctor four seconds.
    cfg = Config({**FIXTURE_CONFIG,
                  "detectors": {"main_thread_block": {"min_slice_ms": 40}}})
    plan = plan_detectors(cfg)
    # the config lists one detector: only it runs, from the config
    assert [d.id for d, _, _ in plan] == ["main_thread_block"], plan
    d, over, src = plan[0]
    assert src == "config" and over == {"min_slice_ms": 40} and d.params["min_slice_ms"] == 16
    # --set on a detector the config leaves out brings it in, marked cli
    plan = plan_detectors(cfg, cli_overrides={"gc_pressure": {"max_events": 1}})
    by_id = {d.id: (over, src) for d, over, src in plan}
    assert set(by_id) == {"main_thread_block", "gc_pressure"}, set(by_id)
    assert by_id["gc_pressure"] == ({"max_events": 1}, "cli"), by_id
    # --set on top of the config: cli wins, both are named
    plan = plan_detectors(cfg, cli_overrides={"main_thread_block": {"min_slice_ms": 5}})
    _, over, src = plan[0]
    assert src == "config+cli" and over["min_slice_ms"] == 5, (over, src)
    # --defaults: every detector, shipped numbers, the config's list ignored
    plan = plan_detectors(cfg, use_defaults=True)
    assert len(plan) == _shipped(), plan
    assert all(src == "default" and not over for _, over, src in plan), plan


@check("thresholds: a value the parameter cannot mean is refused, not rendered")
def _(report):
    """The quiet one, which is why this is a check and not a comment.

    Thresholds go into the query body unquoted — `>= {{min_slice_ms}} *
    1000000` — so whatever the config holds lands in the arithmetic as itself.
    `16ms` is a SQL error, reported against the detector, which is at least
    visible. `16 OR 1=1` is valid SQL that means something else: the HAVING
    clause stops filtering, the detector reports the whole trace, and the run
    finishes green with a report nobody can trust.

    `@param` already says which kind each one is, by what its default is.
    Nothing was reading it.
    """
    from .main import plan_detectors

    for value in ("16 OR 1=1", "16ms", None, True, [16]):
        cfg = Config({**FIXTURE_CONFIG,
                      "detectors": {"main_thread_block": {"min_slice_ms": value}}})
        try:
            plan_detectors(cfg)
        except ConfigError as e:
            assert "min_slice_ms" in str(e) and "number" in str(e), str(e)
            assert "from the config" in str(e), \
                f"the message does not say where the value came from: {e}"
            continue
        raise AssertionError(f"a threshold of {value!r} was accepted")

    # And from the flag, named as the flag.
    cfg = Config({**FIXTURE_CONFIG, "detectors": {"main_thread_block": {}}})
    try:
        plan_detectors(cfg, cli_overrides={"main_thread_block": {"min_slice_ms": "x"}})
    except ConfigError as e:
        assert "from --set" in str(e), str(e)
    else:
        raise AssertionError("--set accepted a threshold that is not a number")

    # A mask is a string, and a number is not one of those either.
    cfg = Config({**FIXTURE_CONFIG,
                  "detectors": {"binder_txn": {"name_glob": 16}}})
    try:
        plan_detectors(cfg)
    except ConfigError as e:
        assert "string" in str(e), str(e)
    else:
        raise AssertionError("a mask of 16 was accepted")

    # What must keep working: `calibrate` derives 41.2 for a default of 16,
    # and refusing that would break the feature this guards.
    cfg = Config({**FIXTURE_CONFIG,
                  "detectors": {"main_thread_block": {"min_slice_ms": 41.2}}})
    _, over, _src = plan_detectors(cfg)[0]
    assert over == {"min_slice_ms": 41.2}, over


# --- frame_jank ------------------------------------------------------------
#
# The fixture plants 24 frames inside the window: 5 the app was 44 ms late for,
# 3 more of the same kind but only 2 ms late, 4 that SurfaceFlinger was late
# for, 10 healthy ones and 2 stuffed. Outside the window sits the worst frame
# in the trace, and in other processes sit eight more.

@check("frame_jank: found the app's late frames, at the right size")
def _(report):
    row = next(r for r in rows(report, "frame_jank")
               if r["location"] == "App Deadline Missed")
    assert row["count"] == 5, f"5 frames cleared the overrun floor: {row}"
    assert row["total_ms"] == 220.0, f"5 frames, 44 ms over each: {row}"
    assert row["max_ms"] == 44.0, f"the worst single overrun: {row}"


@check("frame_jank: the floor is applied per frame, before the grouping")
def _(report):
    # Three more frames of the same jank type missed by 2 ms. Filtering after
    # the GROUP BY would leave the row at 8 frames and add 6 ms of overrun to
    # a finding that is supposed to be about the 44 ms ones.
    row = next(r for r in rows(report, "frame_jank")
               if r["location"] == "App Deadline Missed")
    assert row["count"] == 5, f"the 2 ms frames must not swell the row: {row}"


@check("frame_jank: two bad frames are an anecdote, not a row")
def _(report):
    loc = locations(report, "frame_jank")
    assert "Buffer Stuffing" not in loc, \
        f"2 frames must not clear a floor of 3: {loc}"


@check("frame_jank: healthy frames count in the denominator and nowhere else")
def _(report):
    loc = locations(report, "frame_jank")
    assert "None" not in loc, f"'No Jank' is not a finding: {loc}"
    row = next(r for r in rows(report, "frame_jank")
               if r["location"] == "App Deadline Missed")
    # "5 of 24 frames" — the share is what makes the number mean anything.
    # Against a denominator of only the janky ones it would read 5 of 11.
    assert "5 of 24 frames" in row["detail"], row["detail"]


@check("frame_jank: the platform's verdict on whose deadline it was")
def _(report):
    by_loc = {r["location"]: r for r in rows(report, "frame_jank")}
    assert by_loc["App Deadline Missed"]["detail"].startswith("Self Jank"), \
        by_loc["App Deadline Missed"]["detail"]
    # The app finished on time and the compositor did not. Same table, same
    # window, and nobody should be sent into the app's code over it.
    other = by_loc["SurfaceFlinger CPU Deadline Missed"]
    assert other["detail"].startswith("Other Jank"), other["detail"]
    assert other["count"] == 4 and other["total_ms"] == 56.0, other


@check("frame_jank: the worst frame in the trace is outside the window")
def _(report):
    # 200 ms, 184 over, at ts 1200 — after Screen.firstFrame ends the window.
    # It would be the top row of the report if the clip were missing.
    for row in rows(report, "frame_jank"):
        assert row["max_ms"] < 184.0, f"a frame outside the window leaked: {row}"
        assert "of 25 frames" not in row["detail"], \
            f"it must not reach the denominator either: {row}"


@check("frame_jank: other processes and display frames stay out")
def _(report):
    # com.other.app janks four times inside our window, 74 ms over each, and
    # surfaceflinger's own display frames sit in the same table with no
    # surface token. Neither is ours.
    for row in rows(report, "frame_jank"):
        assert row["max_ms"] != 74.0, f"a foreign process leaked in: {row}"
        assert row["max_ms"] != 54.0, f"a display frame leaked in: {row}"
    row = next(r for r in rows(report, "frame_jank")
               if r["location"] == "SurfaceFlinger CPU Deadline Missed")
    assert row["count"] == 4, f"ours are the four surface frames: {row}"


@check("frame_jank: frames never reach the slice-based detectors")
def _(report):
    # The frame timeline lands in the `slice` table too, named by the frame
    # token — but on its own track types rather than a thread track, so _slice
    # cannot see it. If that ever changes, main_thread_block starts reporting
    # rows called "1", "2", "3".
    for det in ("main_thread_block", "gc_pressure", "monitor_contention",
                "binder_txn", "uninstrumented_cpu"):
        for row in rows(report, det):
            assert not str(row["location"]).isdigit(), \
                f"a frame token surfaced in {det}: {row}"


@check("frame_jank: a trace without a frame timeline is silence, not an error")
def _(report):
    # Android 11 and below, and anything recorded without the frametimeline
    # data source, have no frame timeline. The two tables still exist in
    # trace_processor and are simply empty — but "still exist" is an
    # assumption, and if it ever stops holding, every such trace comes back
    # with an error in detectors[].error instead of a clean silent detector.
    from .main import analyze_trace
    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "no-frames.perfetto-trace"
        trace.write_bytes(fixture.build(frames=False))
        plain = analyze_trace(trace, Config(FIXTURE_CONFIG))
    det = next(d for d in plain["detectors"] if d["id"] == "frame_jank")
    assert det["error"] is None, f"empty tables must not be an error: {det['error']}"
    assert det["rows"] == [], det["rows"]
    # And the rest of the report is exactly what it was before frames existed.
    assert plain["summary"]["detectors_fired"] \
        == _shipped() - 1 - len(SILENT_ON_FIXTURE), plain["summary"]


# --- anr, anr_risk ---------------------------------------------------------

def _lowered_bar():
    """The fixture analysed with anr_risk's bar dropped to a fixture's size.

    The bar itself is the platform's and is checked at its shipped value below.
    What is checked here is everything else the detector does — merging,
    splitting, naming, the breakdown — on the only trace whose contents are
    known exactly. Costs one more pass over the fixture, which is the price of
    testing a five-second rule on a one-second scenario.
    """
    from .main import analyze_trace
    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "lowered.perfetto-trace"
        trace.write_bytes(fixture.build())
        return analyze_trace(trace, Config({
            **FIXTURE_CONFIG,
            "detectors": {"anr_risk": {"min_stall_ms": 100}},
        }))


@check("a block still running when the trace stopped is the longest one, not a zero")
def _(report):
    # trace_processor gives an unfinished slice dur = -1, and the window view
    # used to fold that to zero with MAX(dur, 0). The effect was not a rounding
    # error: on a real freeze the main thread sat in ART's contention slice for
    # twenty seconds, the lock was never released, the slice never closed, and
    # every detector in the report saw nothing at all. The worst thing in the
    # trace was the one thing invisible in it.
    #
    # The fixture's `StuckForever` opens such a slice 60 ms before the window
    # closes and never ends it. Sixty is the whole of what is left of the
    # window, which is the reading being pinned: an open slice runs to the
    # edge.
    row = next(r for r in rows(report, "monitor_contention")
               if r["location"] == "StuckForever")
    assert row["max_ms"] == 60.0, row
    assert row["count"] == 1, row


@check("a finished block beside it keeps the length it actually had")
def _(report):
    # The other half of the same claim. Reading an open slice to the window's
    # edge must not stretch the ones that ended on their own.
    row = next(r for r in rows(report, "monitor_contention")
               if r["location"] == "m.example.app")
    assert row["max_ms"] == 30.0, row
    assert row["total_ms"] == 42.0, row


@check("anr: the record the system wrote, with its own subject")
def _(report):
    found = rows(report, "anr")
    assert len(found) == 1, found
    row = found[0]
    assert row["location"] == fixture.ANR_SUBJECT, row
    # The platform's own id, so this report and a `dumpsys dropbox` entry off
    # the device can be matched to each other by hand.
    assert fixture.ANR_UUID in row["detail"], row["detail"]


@check("anr: a record whose counter carries no pid is still ours")
def _(report):
    # The counter is named either `ErrorId:<process> <pid>#<uuid>` or, on
    # older platforms, `ErrorId:<process>#<uuid>` — and the stdlib can only
    # read a pid out of the first. The fixture writes the second, which is
    # what an Android 13 phone produced: no pid, no upid, and a detector
    # joining on either finds nothing while the record sits in plain sight.
    found = rows(report, "anr")
    assert len(found) == 1, found
    assert found[0]["location"] == fixture.ANR_SUBJECT, found[0]


@check("anr: another application's freeze is not ours")
def _(report):
    for row in rows(report, "anr"):
        assert "other app" not in row["location"], row
        assert fixture.OTHER_UUID not in row["detail"], row


@check("anr: a record outside the scenario window is still a record")
def _(report):
    # The one decision this detector makes differently from every other: it
    # reads the whole trace. An ANR fires five seconds after the event that
    # could not be served, and a cold start's window closes at the first
    # frame — clip this to the window and it is absent from nearly every trace
    # that contains one, which reads as "no freeze".
    row = rows(report, "anr")[0]
    assert "after the window" in row["detail"], row["detail"]
    window_end_ms = 1105
    expected = fixture.ANR_AT_MS - window_end_ms
    assert f"{expected}" in row["detail"], (row["detail"], expected)


@check("anr_risk: silent at the bar the platform sets")
def _(report):
    # Five seconds cannot happen inside a one-second scenario, and a detector
    # that found one anyway would be measuring something other than what it
    # says. The rest of its behaviour is checked below with the bar lowered.
    assert rows(report, "anr_risk") == [], rows(report, "anr_risk")


@check("anr_risk: an idle moment ends the stretch, and the anchor does not span it")
def _(report):
    # The fixture's main thread sleeps for 60 ms with no slice open below
    # `AppStart`. The looper reached the queue there: a pending event would
    # have been served, and no ANR would have fired.
    #
    # `AppStart` runs straight across that moment at depth 0. Counting depth 0
    # would join the two halves into one 1005 ms stretch — the whole scenario,
    # on any project that has an anchor, which is every project this tool asks
    # to add one. Two rows is the proof that depth 0 is not evidence.
    found = _lowered_bar()
    got = rows(found, "anr_risk")
    assert len(got) == 2, [(r["location"], r["max_ms"]) for r in got]
    longest = max(r["max_ms"] for r in got)
    assert longest < 1000, (longest, "the anchor was counted as a stretch")


@check("anr_risk: the breakdown adds up to the stretch it describes")
def _(report):
    # `detail` splits the longest stretch into time on a CPU, time waiting for
    # one, and the rest. A reader picks the next detector from that split, so
    # the three parts have to be the stretch and not merely near it.
    row = max(rows(_lowered_bar(), "anr_risk"), key=lambda r: r["max_ms"])
    parts = [float(p.strip().split(" ")[0]) for p in row["detail"].split("·")[:3]]
    assert abs(sum(parts) - row["max_ms"]) <= 0.3, (parts, row["max_ms"])


@check("the trace config asks for the frame timeline, and parses")
def _(report):
    # frame_jank has exactly one source, and it is off unless the config asks.
    # A detector nobody can ever trigger is worse than no detector.
    from .runner import trace_config
    text = trace_config("com.example.app", 12000, ["view", "gfx"], 65536)
    assert "android.surfaceflinger.frametimeline" in text, text

    # Perfetto reads this as protobuf text format, where a comment is `#`.
    # A `--` in here parses as SQL nowhere and as a syntax error on the
    # device — every capture fails, and the message points at the config
    # rather than at whoever wrote the sentence.
    assert "--" not in text, "a SQL comment in a protobuf text config"
    opens = text.count("{") - text.count("{{")
    assert opens == text.count("}") - text.count("}}"), "unbalanced braces"
    assert "{{" not in text and "}}" not in text, \
        "a doubled brace survived str.format"


@check("a pipe in a cell does not shift the table")
def _(report):
    # A slice name is whatever someone passed to trace{}, and one pipe in it
    # used to move every column of that row one to the right — in a table an
    # agent reads as data. Two of the five renderers this replaced escaped it
    # and three did not, which is what having five of them costs.
    from . import table
    rows = [{"location": "a|b", "count": 1}]
    line = table.render(rows).splitlines()[-1]
    assert line.count("|") == 3 + 1, f"the pipe was not escaped: {line}"
    assert "a\\|b" in line, line

    # And the same through the Marker Report, which is where it would show up.
    from .report import to_markdown
    text = to_markdown({
        "window": {"duration_ms": 1.0}, "trace": "t",
        "summary": {"detectors_run": 1, "detectors_fired": 1, "fired_ids": ["d"]},
        "detectors": [{"id": "d", "title": "T", "why": "", "params": {},
                       "params_source": "default", "error": None,
                       "rows": [{"location": "a|b", "self_ms": 1.0}]}],
    })
    assert "a\\|b" in text, text


@check("doctor: a check that breaks fails itself, and says which kind of not")
def _(report):
    """`run` caught AssertionError and nothing else.

    A check is ordinary Python over a report — it indexes dicts, reads files,
    builds temp trees. Any of that can raise something that is not an
    assertion, and that exception went all the way up to `cmd_doctor`, which
    prints "could not run" and "The environment is broken. Until this is
    fixed, no report from it can be trusted" — on a machine where every other
    check would have passed, and without naming the one that did not.

    Each check fails on its own now, and the two kinds of failure are told
    apart: the claim not holding is the pipeline's problem, the check raising
    is the check's, and they are fixed in different places.
    """
    from . import selftest as st

    def passes(_report):
        pass

    def claims(_report):
        assert 1 == 2, "120 ms became 240 ms"

    def bare(_report):
        assert "x" in []

    def breaks(_report):
        {"a": 1}["b"]

    out = dict(st.run_checks({}, [("passes", passes), ("claims", claims),
                                  ("bare", bare), ("breaks", breaks)]))
    assert out["passes"] is None, out["passes"]
    assert out["claims"] == "120 ms became 240 ms", out["claims"]
    # A bare assert carries no message; an empty string reads as "passed".
    assert out["bare"] and "assertion failed at selftest.py:" in out["bare"], out["bare"]
    assert out["breaks"] and out["breaks"].startswith(
        "the check itself raised KeyError at selftest.py:"), out["breaks"]
    # And the run went on: one broken check is one failure, not none of them.
    assert sum(1 for why in out.values() if why) == 3, out


@check("reflect: a word after `echolot` is not a subcommand")
def _(report):
    """The readers and the facts disagreed on what counts as a call.

    `facts` filters the match against the verbs the parser registers, and says
    at length why: a real report once counted `ran`, `call`, `calls` and
    `without` as subcommands, and its "By subcommand" line read like a
    sentence. The Claude Code reader had a second regex beside it with no such
    filter, so `echo "echolot never ran here"` put `never` in the count a
    session is listed by — and made a session that never touched the tool the
    newest candidate to reflect on.
    """
    from .reflect import claude_code
    from .reflect import facts as facts_mod
    from .reflect.model import Call, Session

    assert facts_mod.subcommands('echo "echolot never ran here" && ls') == []
    assert facts_mod.subcommands(
        "cd x && echolot doctor -q && echolot analyze t.perfetto-trace") == \
        ["doctor", "analyze"]
    assert facts_mod.subcommands("echolot --help") == ["help"]
    # A heredoc body is the config being written, not calibrate being run.
    assert facts_mod.subcommands(
        "cat > echolot.yml <<EOF\necholot calibrate a b\nEOF") == []

    def session(*commands):
        s = Session(id="s", agent="claude-code")
        s.calls = [Call(id=str(i), ts="", tool="Bash", input={}, command=c)
                   for i, c in enumerate(commands)]
        return s

    prose = session('echo "echolot was never run in this session"')
    assert claude_code.echolot_subcommands(prose) == [], \
        claude_code.echolot_subcommands(prose)
    assert not claude_code.involves_echolot(prose), \
        "a session that only talked about the tool is not one to reflect on"
    real = session("echolot doctor", "echolot analyze x.perfetto-trace")
    assert claude_code.echolot_subcommands(real) == ["doctor", "analyze"]
    assert claude_code.involves_echolot(real)


@check("compare: a pipe does not shift either of its tables")
def _(report):
    """`compare` printed the sixth hand-rolled markdown table.

    `table` exists because there were five, three column orders and two of
    them not escaping `|`. This file assembled its own header, its own
    separator and its own `_escape` — and the second table, the one listing
    detectors that changed state, escaped nothing at all.
    """
    from . import compare as cmp_mod

    def side(moved_ms, quiet_rows):
        return {"schema": 1, "trace": "t", "window": {"duration_ms": 100.0},
                "summary": {"detectors_run": 2, "detectors_fired": 1,
                            "fired_ids": ["d|x"]},
                "detectors": [
                    {"id": "d|x", "title": "T", "why": "", "params": {},
                     "params_source": "default", "error": None,
                     "rows": [{"location": "a|b", "total_ms": moved_ms}]},
                    {"id": "q|t", "title": "Q", "why": "", "params": {},
                     "params_source": "default", "error": None,
                     "rows": quiet_rows},
                ]}

    text = cmp_mod.to_markdown(cmp_mod.build(
        side(10.0, []), side(300.0, [{"location": "z", "total_ms": 9.0}]),
        before_path="before.json", after_path="after.json"))
    # the moved table: the location and the detector id
    assert "a\\|b" in text, text
    assert "d\\|x" in text, text
    # and the one below it, which never escaped anything
    assert "## Detectors that changed state" in text, text
    assert "q\\|t" in text, text
    # An escaped pipe is not a cell boundary: every body row of both tables
    # has to come out with the column count its header declared.
    for line in text.splitlines():
        if line.startswith("|") and set(line) - set("|- "):
            assert line.replace("\\|", "").count("|") in (4, 8), line


@check("one way to say how long ago, on every screen that says it")
def _(report):
    """`status` and `hunt --show` print this one under the other.

    They were two copies of the same eleven lines — the same three thresholds,
    the same four spellings — in two files that cannot import each other,
    because `state` already imports `hunt`. Nothing compared them, so editing
    one would have made the two lines disagree in silence.
    """
    import time
    from . import hunt as hunt_mod
    from . import when

    now = time.time()
    assert when.ago(None) == "never"
    assert when.ago(now - 30) == "30s ago"
    assert when.ago(now - 3600) == "60m ago"
    assert when.ago(now - 86400) == "24h ago"
    assert when.ago(now - 86400 * 3) == "3d ago"
    assert when.iso_epoch(None) is None and when.iso_epoch("not a date") is None
    assert when.iso_epoch("2020-01-01T00:00:00Z") == \
        when.iso_epoch("2020-01-01T00:00:00+00:00")
    # One definition, reached from both, rather than two that happen to agree.
    assert hunt_mod._ago is when.ago, "hunt has grown its own copy again"
    assert hunt_mod._epoch is when.iso_epoch, "and its own parse"


@check("a table keeps the columns it knows in order, and shows the rest")
def _(report):
    from . import table
    # Registered columns first, in the order the report declares them; a
    # column a detector invented follows rather than disappearing.
    rows = [{"detail": "d", "invented": 7, "location": "x"}]
    cols = table.columns(rows, order=["location", "detail"])
    assert cols == ["location", "detail", "invented"], cols
    assert table.columns(rows, order=["location"], skip=["invented"]) \
        == ["location", "detail"], "skip drops a column entirely"
    assert table.render([]) == "_empty_"


@check("analyze: a relative -o is taken from the config's directory")
def _(report):
    from .main import _out_dir
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "echolot.yml").write_text("project:\n  process: app\n", encoding="utf-8")
        cfg = Config.load(root / "echolot.yml")
        assert _out_dir(".echolot/out", cfg) == root / ".echolot" / "out"
        assert _out_dir("/abs/elsewhere", cfg) == Path("/abs/elsewhere")
        # an in-memory config has no directory: cwd it is, as before
        assert _out_dir(".echolot/out", Config({})) == Path(".echolot/out")


# --- local.yml -------------------------------------------------------------

def _write_pair(root: Path, project: str, local: str | None) -> Path:
    path = root / "echolot.yml"
    path.write_text(project, encoding="utf-8")
    if local is not None:
        (root / "local.yml").write_text(local, encoding="utf-8")
    return path


@check("local.yml: overrides precisely, without wiping its section neighbours")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_pair(
            Path(tmp),
            "project:\n  process: com.example.app\n"
            "runner:\n  iterations: 5\n  duration_ms: 12000\n",
            "runner:\n  device: ABC123\n  duration_ms: 9000\n")
        cfg = Config.load(path)

    assert cfg.local_path is not None, "the adjacent local.yml was not picked up"
    assert cfg.runner["duration_ms"] == 9000, "the local value did not override"
    assert cfg.runner["device"] == "ABC123", "the local value was not added"
    # The merge is recursive: a whole section is never swapped out.
    assert cfg.runner["iterations"] == 5, "a section neighbour was wiped"
    assert cfg.process == "com.example.app", "another section was wiped"


@check("local.yml: its absence alongside is normal, not an error")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config.load(_write_pair(
            Path(tmp), "project:\n  process: com.example.app\n", None))
    assert cfg.local_path is None
    assert cfg.process == "com.example.app"


@check("local.yml: named explicitly and missing is an error, not silence")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_pair(Path(tmp), "project:\n  process: x\n", None)
        try:
            Config.load(path, Path(tmp) / "no-such-file.yml")
        except ConfigError:
            return
    raise AssertionError("stayed quiet about a missing file named explicitly")


# --- the run log ------------------------------------------------------------

@check("the run log goes to the project, not to wherever the command was typed")
def _(report):
    """Everything a run leaves behind follows the config. This did not.

    `analyze` is called from wherever the traces are — inside a
    macrobenchmark's output directory, named after a build variant and a
    device model. The report follows the config, and so does the open
    investigation, both deliberately. The run log was resolved against the
    working directory while every reader looks under the project, so the two
    could not meet: `status` said "doctor: never run here" on a project where
    doctor had just passed, and `reflect` saw only the runs that happened to
    be typed in the right place.
    """
    import json

    from .main import main

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project, elsewhere = root / "proj", root / "builddir"
        project.mkdir()
        elsewhere.mkdir()
        config = _write_pair(project, "project:\n  process: com.example.app\n"
                                      "scenario:\n  name: firstLaunch\n", None)

        here = Path.cwd()
        # The recorder is what is under test, so it has to be on. pytest turns
        # it off for every test in the suite — no test may append to whatever
        # run log it happens to run beside — and this one writes into a
        # temporary directory that is about to go away.
        quiet = os.environ.pop("ECHOLOT_NO_RECORD", None)
        try:
            os.chdir(elsewhere)
            # Its own scope: this runs inside the doctor run that hosts it,
            # and neither the facts nor the project may leak into that line.
            with recorder.isolated():
                assert main(["status", "-c", str(config)]) == 0
        finally:
            os.chdir(here)
            if quiet is not None:
                os.environ["ECHOLOT_NO_RECORD"] = quiet

        stray = list(elsewhere.rglob("runs.jsonl"))
        assert not stray, f"the run log was left in the working directory: {stray}"

        landed = project / recorder.LOG_FILE
        assert landed.exists(), (
            "nothing was written under the project the config names — "
            "`status` there would report the run as never having happened")

        entry = json.loads(landed.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["cmd"] == "status", entry
        # Where the command ran is still a fact, and a useful one. It is a
        # different question from where the log lives, and the two had one
        # answer between them.
        assert Path(entry["cwd"]).resolve() == elsewhere.resolve(), (
            f"the working directory stopped being recorded: {entry['cwd']}")


# --- the domains map -------------------------------------------------------

def _sample_repo(root: Path) -> None:
    """A mini repo: two modules, three forms of instrumentation, one trap."""
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    for module in ("app", "feature/collection"):
        (root / module).mkdir(parents=True, exist_ok=True)
        (root / module / "build.gradle.kts").write_text("", encoding="utf-8")

    src = root / "feature/collection/src/main/kotlin"
    src.mkdir(parents=True)
    (src / "Mapper.kt").write_text(
        "package feature.collection\n"
        "import androidx.tracing.trace\n"
        "\n"
        "class CollectionMapper {\n"
        "    fun mapEntities(raw: List<Raw>) = trace(\"collection_mapping\") {\n"
        "        raw.map { it.toDomain() }\n"
        "    }\n"
        "}\n", encoding="utf-8")

    java = root / "app/src/main/java"
    java.mkdir(parents=True)
    (java / "Startup.java").write_text(
        "package app;\n"
        "import android.os.Trace;\n"
        "public class Startup {\n"
        "    public void initGraph() {\n"
        "        Trace.beginSection(\"di_graph_init\");\n"
        "    }\n"
        "    public void dynamic(String tag) {\n"
        "        Trace.beginSection(tag);\n"
        "    }\n"
        "}\n", encoding="utf-8")

    # The trap: a logging function with the same name but no tracing import.
    (java / "Noise.kt").write_text(
        "package app\n"
        "fun handle() { trace(\"this is a log line, not instrumentation\") }\n",
        encoding="utf-8")

    # The build directory must not be scanned.
    build = root / "app/build/generated"
    build.mkdir(parents=True)
    (build / "Gen.kt").write_text(
        "import androidx.tracing.trace\n"
        "fun g() { trace(\"generated_noise\") }\n", encoding="utf-8")


@check("domains: names, modules and hints are gathered from the sources")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        sites, _ = dm.scan(root)
        found = {s.name: s for s in sites}

    assert set(found) == {"collection_mapping", "di_graph_init"}, sorted(found)
    assert found["collection_mapping"].module == ":feature:collection"
    assert found["di_graph_init"].module == ":app"
    # The hint must lead into the method, not the class: otherwise it is
    # useless in a file with two dozen methods.
    assert found["di_graph_init"].symbol == "method initGraph", found
    assert found["collection_mapping"].symbol == "fun mapEntities", found


@check("domains: a logger named trace is not mistaken for instrumentation")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        names = {s.name for s in dm.scan(root)[0]}
    # Without an androidx.tracing import, a bare trace(...) is someone else's
    # function.
    assert "this is a log line, not instrumentation" not in names, names
    # And generated code is no place for hypotheses.
    assert "generated_noise" not in names, names


@check("domains: non-literal names are counted, not lost")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        _, stats = dm.scan(root)
    # Trace.beginSection(tag) is visible in the trace but will never reach the
    # map — that has to be said out loud, or the gap looks like its absence.
    assert sum(s.dynamic for s in stats.values()) == 1, stats


@check("process mask: a wide match names a few alternatives, not hundreds")
def _(report):
    import contextlib
    import io
    from .main import _OTHERS_SHOWN, _resolve_process

    class _FakeTP:
        # 641 processes, as `*` gives on a real device: the chosen one plus
        # six hundred others that must not become a fifteen-kilobyte line.
        def query(self, sql):
            return [{"upid": i, "pid": 1000 + i, "name": f"proc{i}",
                     "slices": 1000 - i} for i in range(641)]

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        procs = _resolve_process(_FakeTP(), "*")
    assert len(procs) == 641, "every candidate is still returned to the caller"
    line = err.getvalue()
    assert "matched 641 processes" in line, line
    assert f"and {641 - 1 - _OTHERS_SHOWN} more" in line, line
    assert len(line) < 400, f"the warning is {len(line)} chars long"


# --- mark ------------------------------------------------------------------

def _mark_repo(root: Path, *, app="GloomApp", act="MainActivity", theme="AppTheme",
               nav="AppNavHost", pkg="com.example.app", app_return=False,
               launcher=True, second_app=False, one_line=False,
               unclosed=False) -> None:
    """A Kotlin + Compose app: manifest, Application, launcher Activity with
    setContent, a theme and a nav host in two modules, Room in a core module.

    Every name is a parameter so the same tree can be built with nonsense
    names — `mark` must give the same skeleton, source for source.
    """
    (root / "settings.gradle.kts").write_text("", encoding="utf-8")
    for module in ("app", "feature/home", "core/database", "core/ui"):
        (root / module).mkdir(parents=True, exist_ok=True)
        (root / module / "build.gradle.kts").write_text(
            f'android {{ namespace = "{pkg}{"" if module == "app" else "." + module.replace("/", ".")}" }}\n',
            encoding="utf-8")
    main = root / "app/src/main"
    (main / "kotlin/x").mkdir(parents=True)
    filt = ("<intent-filter><action android:name=\"android.intent.action.MAIN\" />"
            "<category android:name=\"android.intent.category.LAUNCHER\" /></intent-filter>"
            if launcher else "")
    (main / "AndroidManifest.xml").write_text(
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        f'  <application android:name=".{app}">\n'
        f'    <activity android:name=".ui.{act}" android:exported="true">{filt}</activity>\n'
        f'    <activity android:name=".ui.Other" />\n'
        f'  </application>\n</manifest>\n', encoding="utf-8")
    ret = "        if (BuildConfig.DEBUG) return\n" if app_return else ""
    (main / f"kotlin/x/{app}.kt").write_text(
        f"package {pkg}\n\nclass {app} : Application() {{\n"
        f"    override fun onCreate() {{\n        super.onCreate()\n{ret}"
        f"        // a string with a brace: \"}}\" and a comment }} here\n"
        f"        init()\n    }}\n}}\n", encoding="utf-8")
    if unclosed:
        # A `setContent {` whose brace is never closed — what a file caught
        # mid-edit looks like. Deliberately one `{` short of balancing to the
        # end of the file, so `match_brace` walks off it and returns None.
        body = (
            f"    override fun onCreate(savedInstanceState: Bundle?) {{\n"
            f"        super.onCreate(savedInstanceState)\n"
            f"        setContent {{\n            {theme} {{\n"
            f"                {nav}(start = true)\n")
    body = body if unclosed else (
        f"    override fun onCreate(b: Bundle?) {{ super.onCreate(b); "
        f"setContent {{ {theme} {{ {nav}(start = true) }} }} }}\n"
        if one_line else
        f"    override fun onCreate(savedInstanceState: Bundle?) {{\n"
        f"        super.onCreate(savedInstanceState)\n"
        f"        setContent {{\n            {theme} {{\n                Surface(modifier = Modifier) {{\n"
        f"                    {nav}(start = true)\n                }}\n            }}\n        }}\n"
        f"    }}\n")
    (main / f"kotlin/x/{act}.kt").write_text(
        f"package {pkg}.ui\n\nclass {act} : ComponentActivity() {{\n"
        + body + "}\n", encoding="utf-8")
    ui = root / "core/ui/src/main/kotlin"
    ui.mkdir(parents=True)
    (ui / "Theme.kt").write_text(
        f"package {pkg}.core.ui\n\n@Composable\nfun {theme}(content: @Composable () -> Unit) {{\n"
        f"    MaterialTheme(content = content)\n}}\n", encoding="utf-8")
    home = root / "feature/home/src/main/kotlin"
    home.mkdir(parents=True)
    (home / "Nav.kt").write_text(
        f"package {pkg}.feature.home\n\n@Composable\nfun {nav}(start: Boolean) {{\n"
        f"    NavHost(startDestination = \"home\") {{ }}\n}}\n", encoding="utf-8")
    db = root / "core/database/src/main/kotlin"
    db.mkdir(parents=True)
    (db / "Db.kt").write_text(
        f"package {pkg}.core.database\n\nfun open(ctx: Context) =\n"
        f"    Room.databaseBuilder(ctx, AppDb::class.java, \"app.db\").build()\n",
        encoding="utf-8")
    if second_app:
        wear = root / "wear/src/main"
        (wear / "kotlin").mkdir(parents=True)
        (root / "wear/build.gradle.kts").write_text(
            f'android {{ namespace = "{pkg}.wear" }}\n', encoding="utf-8")
        (wear / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <application><activity android:name=".WearActivity">'
            '<intent-filter><action android:name="android.intent.action.MAIN" />'
            '<category android:name="android.intent.category.LAUNCHER" /></intent-filter>'
            '</activity></application>\n</manifest>\n', encoding="utf-8")
        # A block body over several lines: this tree is about which module
        # `--module` picks, and a one-line body is refused for its own
        # reasons two checks below.
        (wear / "kotlin/WearActivity.kt").write_text(
            f"package {pkg}.wear\nclass WearActivity : ComponentActivity() {{\n"
            f"    override fun onCreate(b: Bundle?) {{\n"
            f"        super.onCreate(b)\n    }}\n}}\n",
            encoding="utf-8")


def _shape(pl) -> list[tuple[str, str, bool]]:
    return [(p.kind, p.source, p.applicable) for p in pl.proposals]


@check("mark: the skeleton comes from the platform's vocabulary, not the project's names")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        pl = mk.plan(root, package="com.example.app",
                     allowed=["app/src/main", "feature/home/src/main"])
        assert pl.module == ":app" and not pl.ambiguity, (pl.module, pl.ambiguity)
        shape = _shape(pl)
        assert shape == [
            ("app_oncreate", "manifest+lifecycle", True),
            ("activity_oncreate", "manifest+lifecycle", True),
            ("set_content", "api", True),
            # composables in call order: theme first, nav host inside it —
            # the theme sits in core/ui, outside allowed
            ("compose_root", "call-from-setContent", False),
            ("compose_root", "call-from-setContent", False),
            ("room_open", "api", False),
        ], shape
    by = {p.marker: p for p in pl.proposals}
    assert by["AGENTTMP_compose_AppTheme"].file == "core/ui/src/main/kotlin/Theme.kt"
    assert "outside instrumentation.allowed" in by["AGENTTMP_compose_AppTheme"].reason
    assert by["AGENTTMP_compose_AppNavHost"].file == "feature/home/src/main/kotlin/Nav.kt"
    assert by["AGENTTMP_room_open"].file == "core/database/src/main/kotlin/Db.kt"
    # the string "}" and the comment "}" in onCreate did not confuse the brace match
    assert by["AGENTTMP_app_oncreate"].applicable and by["AGENTTMP_app_oncreate"].close_at
    assert any("runtime-tracing" in n for n in pl.notes), pl.notes

    # the same tree, every name nonsense: same skeleton, source for source
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, app="Zq1", act="Yx2", theme="Wv3", nav="Ut4", pkg="a.b.c")
        pl2 = mk.plan(root, package="a.b.c", allowed=["app/src/main", "feature/home/src/main"])
        assert _shape(pl2) == shape, (_shape(pl2), shape)
        # and it says so in the markers, with the project's own words
        assert {p.marker for p in pl2.proposals} >= {"AGENTTMP_compose_Wv3", "AGENTTMP_compose_Ut4"}


@check("mark: what it cannot see it says — no launcher, two launchers, an early return")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, launcher=False)
        pl = mk.plan(root)
        assert not pl.proposals and any("no launcher Activity" in n for n in pl.notes), pl.notes
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, second_app=True)
        pl = mk.plan(root)   # no package to break the tie
        assert pl.ambiguity and not pl.proposals, (pl.ambiguity, pl.proposals)
        pl = mk.plan(root, package="com.example.app")
        assert pl.module == ":app" and not pl.ambiguity, (pl.module, pl.ambiguity)
        pl = mk.plan(root, module=":wear")
        assert pl.module == ":wear" and _shape(pl)[:1] == [("activity_oncreate", "manifest+lifecycle", True)], _shape(pl)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, app_return=True)
        pl = mk.plan(root, package="com.example.app")
        app = next(p for p in pl.proposals if p.kind == "app_oncreate")
        assert not app.applicable and "return" in app.reason, app
    # Every refusal carries its reason. A row printed with `·` and nothing
    # after it reads as the tool declining without saying why, and a reader
    # cannot tell that from a bug. An unclosed brace was the one shape that
    # produced it: `find_lambda` finds the `{`, `match_brace` returns None,
    # and the proposal came out not applicable with an empty reason.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, unclosed=True)
        pl = mk.plan(root, package="com.example.app")
        for p in pl.proposals:
            assert p.applicable or p.reason, \
                f"refused without saying why: {p.kind} in {p.file}"
        sc = next((p for p in pl.proposals if p.kind == "set_content"), None)
        assert sc is not None and not sc.applicable, _shape(pl)
        assert "closing brace" in sc.reason, sc.reason

    # the same tree twice: byte-identical
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        a = "\n".join(mk.render(mk.plan(root, package="com.example.app")))
        b = "\n".join(mk.render(mk.plan(root, package="com.example.app")))
        assert a == b


@check("mark: --apply inserts tagged pairs, --remove restores the bytes")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        before = {p: p.read_bytes() for p in mk.source_files(root)}
        pl = mk.plan(root, package="com.example.app", allowed=["app/src/main"])
        done, _ = mk.apply(root, pl)
        files = {rel for rel, _ in done}
        assert files == {"app/src/main/kotlin/x/GloomApp.kt", "app/src/main/kotlin/x/MainActivity.kt"}, files
        act = (root / "app/src/main/kotlin/x/MainActivity.kt").read_text(encoding="utf-8")
        tagged = [ln for ln in act.splitlines() if mk.TAG in ln]
        assert len(tagged) == 4, tagged      # onCreate pair + setContent pair
        assert tagged[0].strip().startswith('android.os.Trace.beginSection("AGENTTMP_activity_oncreate")'), tagged
        # order inside the file: begin(onCreate) … begin(setContent) … end … end,
        # the inner pair indented one level deeper
        assert ["begin" in t for t in tagged] == [True, True, False, False], tagged
        assert "AGENTTMP_set_content" in tagged[1], tagged
        indent = [len(t) - len(t.lstrip()) for t in tagged]
        assert indent[0] == indent[3] < indent[1] == indent[2], indent
        # applying twice does not double the markers
        again, _ = mk.apply(root, mk.plan(root, package="com.example.app",
                                          allowed=["app/src/main"]))
        assert again == [], again
        touched = mk.remove(root)
        assert {rel for rel, _ in touched} == files and all(n == 2 or n == 4 for _, n in touched), touched
        after = {p: p.read_bytes() for p in mk.source_files(root)}
        assert after == before, "remove must restore every file byte for byte"


@check("mark: a block written on one line is refused, never mangled")
def _(report):
    """`setContent { AppRoot() }` — half of Compose is written this way.

    apply puts the begin line after the `{` and the end line at the start of
    the `}`'s line; on one line those two inserts cross. The end marker came
    out above the block, the body ended up inside the begin line's comment,
    and `--remove` — which deletes tagged lines whole — then took the body
    with it. The file could not be got back.
    """
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, one_line=True)
        before = {p: p.read_bytes() for p in mk.source_files(root)}
        pl = mk.plan(root, package="com.example.app", allowed=["app/src/main"])
        flat = [p for p in pl.proposals
                if p.kind in ("activity_oncreate", "set_content")]
        assert len(flat) == 2, _shape(pl)
        for p in flat:
            assert not p.applicable, f"a one-line block is not applicable: {p}"
            assert "one line" in p.reason, p.reason
        done, _ = mk.apply(root, pl)
        # The Application, whose onCreate spans lines, is still marked: the
        # refusal is about the block, not about the file or the run.
        assert [rel for rel, _ in done] == ["app/src/main/kotlin/x/GloomApp.kt"], done
        act = root / "app/src/main/kotlin/x/MainActivity.kt"
        assert act.read_bytes() == before[act], \
            "a one-line block must come out of --apply untouched"
        mk.remove(root)
        after = {p: p.read_bytes() for p in mk.source_files(root)}
        assert after == before, "remove must restore every file byte for byte"


@check("the investigation keeps every round, even after one is deleted by hand")
def _(report):
    """`.echolot/out/report.json` is overwritten by every `analyze`.

    The copies under the investigation are the only record of what it
    concluded at each step, and their number came from counting the files
    rather than from the highest name in use. Delete one from the middle —
    they are just files in a directory — and the next `analyze` wrote over
    the round before it, silently, in the one place a round is kept.
    """
    import json

    from . import hunt as hunt_mod

    with tempfile.TemporaryDirectory() as tmp:
        project, out = Path(tmp), Path(tmp) / "out"
        out.mkdir()
        hunt_mod.save(project, {"n": 1, "status": "open", "question": "why"})

        kept = []
        for i in range(3):
            (out / "report.json").write_text(json.dumps({"round": i}),
                                             encoding="utf-8")
            kept.append(hunt_mod.record_report(project, out))
        assert [p.name for p in kept] == ["001.json", "002.json", "003.json"], kept

        kept[1].unlink()
        (out / "report.json").write_text(json.dumps({"round": 3}), encoding="utf-8")
        fourth = hunt_mod.record_report(project, out)

        assert fourth.name == "004.json", f"the number was reused: {fourth.name}"
        assert json.loads(kept[2].read_text(encoding="utf-8"))["round"] == 2, \
            "the previous round was written over"


@check("repeats: the merged report describes the run, not the repeat that failed")
def _(report):
    """`params` says which thresholds a detector actually used.

    A detector that failed cannot say: `analyze_trace` has no resolved values
    to report when rendering is what raised, so it falls back to the shipped
    defaults. Merging took everything about the detector from repeat one, so
    a run whose first trace happened to trip a SQL error described its
    thresholds as the built-in ones — and `compare` read that against the
    next report and announced a threshold change nobody had made.
    """
    from . import report as rep

    def one(error, params):
        return {"window": {"duration_ms": 10.0}, "trace": "t",
                "summary": {"fired_ids": [], "absent_ids": []},
                "detectors": [{"id": "d", "title": "d", "why": "",
                               "identity": ["location"], "params": params,
                               "params_source": "config", "rows": [],
                               "error": error}]}

    merged = rep.aggregate([one("boom", {"min_slice_ms": 16}),
                            one(None, {"min_slice_ms": 40}),
                            one(None, {"min_slice_ms": 40})])
    only = merged["detectors"][0]
    assert only["params"] == {"min_slice_ms": 40}, only["params"]
    # The failure is still reported. Taking the description from a repeat
    # that ran must not turn into hiding that another one did not.
    assert only["error"] == "boom", only["error"]


@check("reflect: a signal that broke is not checked, and does not read as checked")
def _(report):
    """`skip` and not `info`, for the reason `skip` exists at all.

    A signal that raised examined nothing. Filed as `info` it sat among the
    friction hints, where a reader looking for what the session did wrong
    would take it for one more observation about the session rather than a
    hole in the report.
    """
    from .reflect import signals as sig_mod
    from .reflect.model import EVERYTHING, Session

    def broken(session, facts, cfg):
        raise RuntimeError("nope")

    saved = sig_mod.SIGNALS[:]
    sig_mod.SIGNALS[:] = [broken]
    try:
        got = sig_mod.run(Session(id="s", agent="x", carries=list(EVERYTHING)),
                          sig_mod.Facts(), None)
    finally:
        sig_mod.SIGNALS[:] = saved

    assert len(got) == 1 and got[0].severity == "skip", got
    assert "RuntimeError" in got[0].why, got[0].why


@check("CLI: what the user typed wrong is a sentence and an exit code, never a traceback")
def _(report):
    """Four ways the tool used to end in a Python traceback.

    A traceback out of the CLI is a bug in echolot, and it is not a private
    matter: `reflect` reads the run log, sees one, and files it as exactly
    that. Three of these were not bugs in echolot at all — a path that is not
    there, a `--since` nobody spelled right, a source file that is not UTF-8 —
    and the fourth is a race with gradle tidying up its own output directory.
    """
    from . import mark as mk
    from .main import main
    from .tp import TraceSession

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "echolot.yml").write_text(
            "project:\n  process: com.example.app\n", encoding="utf-8")

        # A trace that is not there, from every verb that opens one. A glob
        # that matched nothing arrives here as itself, which is how an empty
        # .echolot/traces produced a bare FileNotFoundError.
        try:
            TraceSession(root / "nosuch.perfetto-trace")
        except ConfigError as e:
            assert "no such trace" in str(e), str(e)
        else:
            raise AssertionError("a missing trace opened a session")

        here = Path.cwd()
        try:
            os.chdir(root)
            with recorder.isolated():
                for argv in (["analyze", "nosuch.perfetto-trace"],
                             ["probe", "nosuch.perfetto-trace"],
                             ["names", "nosuch.perfetto-trace"],
                             ["calibrate", "nosuch.perfetto-trace"],
                             ["reflect", "--since", "2weeks"]):
                    assert main(argv) == 2, f"{argv} did not exit 2"
        finally:
            os.chdir(here)

    # A source file that is not UTF-8. `plan` reads it leniently and computes
    # offsets on what that produced; `apply` cannot read it the same way
    # without those offsets shifting, so it skips the file and says which.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rel = "app/src/main/kotlin/Foo.kt"
        path = root / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(
            b"package a\n\n// caf\xe9\nclass Foo {\n"
            b"    fun onCreate() {\n        bar()\n    }\n}\n")
        was = path.read_bytes()

        pl = mk.plan_from_anr(root, [("a.Foo.onCreate", rel, 6)])
        assert pl.proposals and pl.proposals[0].applicable, _shape(pl)
        done, unreadable = mk.apply(root, pl)
        assert done == [] and unreadable == [rel], (done, unreadable)
        assert path.read_bytes() == was, "a file that could not be read was written"


@check("mark: --remove deletes its own lines and nothing that carries code")
def _(report):
    """The other half of the same promise.

    `remove` used to delete any line holding the tag. That is safe only while
    every such line is one apply wrote — and the moment one was not, the code
    sharing the line went too. Now the shape is the predicate, and a tag
    someone typed onto a line of their own code is left where it is.
    """
    from . import mark as mk
    assert mk.is_applied_line(
        f'    android.os.Trace.beginSection("AGENTTMP_x") {mk.TAG}')
    assert mk.is_applied_line(f'  android.os.Trace.endSection(); {mk.TAG}')  # Java
    assert not mk.is_applied_line(f'    doWork() {mk.TAG}')
    assert not mk.is_applied_line(f'    // TODO {mk.TAG} clean this up')


@check("domains: no instrumentation is a report, not an empty output")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "build.gradle.kts").write_text("", encoding="utf-8")
        src = root / "app/src/main/kotlin"
        src.mkdir(parents=True)
        (root / "app/build.gradle.kts").write_text("", encoding="utf-8")
        (src / "Big.kt").write_text("fun a() {}\n" * 500, encoding="utf-8")
        sites, stats = dm.scan(root)
        text = "\n".join(dm.render(sites, stats, root))
    assert not sites
    assert "domains: []" in text
    assert ":app" in text and "500 lines" in text, text


# --- merging repeats -------------------------------------------------------

@check("merging repeats: median, not mean and not maximum")
def _(report):
    import copy
    from .report import aggregate
    first, second = copy.deepcopy(report), copy.deepcopy(report)
    second["trace"] = "second"
    for d in second["detectors"]:
        if d["id"] != "main_thread_block":
            continue
        for r in d["rows"]:
            if r["location"] == "collection_mapping":
                r["self_ms"] = 240.0        # twice as costly in the second run
        d["rows"].append({"location": "flaky_once", "count": 1,
                          "self_ms": 999.0, "total_ms": 999.0, "detail": "—"})

    merged = aggregate([first, second])
    rows = {r["location"]: r for r in next(
        d for d in merged["detectors"]
        if d["id"] == "main_thread_block")["rows"]}

    assert merged["runs"] == 2, merged["runs"]
    assert rows["collection_mapping"]["self_ms"] == 180.0, (
        f"the median of 120 and 240: {rows['collection_mapping']}"
    )


@check("merging repeats: a one-off finding is marked as one-off")
def _(report):
    import copy
    from .report import aggregate
    first, second = copy.deepcopy(report), copy.deepcopy(report)
    second["trace"] = "second"
    for d in second["detectors"]:
        if d["id"] == "main_thread_block":
            d["rows"].append({"location": "flaky_once", "count": 1,
                              "self_ms": 999.0, "total_ms": 999.0,
                              "detail": "—"})
    merged = aggregate([first, second])
    rows = {r["location"]: r for r in next(
        d for d in merged["detectors"]
        if d["id"] == "main_thread_block")["rows"]}
    # This is what repeats are for: 999 ms in one run out of two is not a
    # finding but an accident, and the report must tell it from a reproducible
    # one.
    assert rows["flaky_once"]["runs"] == "1/2", rows["flaky_once"]
    assert rows["collection_mapping"]["runs"] == "2/2", rows["collection_mapping"]


@check("merging repeats: rows a detector kept apart stay apart")
def _(report):
    """Two rows, one location, one run — and the merge used to eat one.

    `runnable_starvation` groups by thread and state, so a thread that was
    both preempted (R) and runnable-on-another-cpu (R+) is two rows carrying
    one thread name. Merged on the name alone they became a single row whose
    median was taken over both: `runs 6/3` for three repeats, `N 2.5`, and a
    spread reading 20→110 where each phenomenon had been steady. The fixture
    plants only state R, so nothing here could ever have caught it.
    """
    import copy
    from .report import aggregate

    def run(r_ms, rplus_ms):
        return {"schema": 1, "trace": f"t{r_ms}", "window": {"duration_ms": 1000.0},
                "summary": {"detectors_run": 1, "detectors_fired": 1,
                            "fired_ids": ["runnable_starvation"]},
                "detectors": [{
                    "id": "runnable_starvation", "title": "t", "why": "",
                    "params": {}, "params_source": "default", "error": None,
                    "identity": ["location", "detail"],
                    "rows": [
                        {"location": "RenderThread", "count": 3,
                         "total_ms": r_ms, "max_ms": r_ms, "detail": "state R"},
                        {"location": "RenderThread", "count": 2,
                         "total_ms": rplus_ms, "max_ms": rplus_ms, "detail": "state R+"},
                    ]}]}

    merged = aggregate([run(100.0, 20.0), run(110.0, 22.0), run(105.0, 21.0)])
    rows = {r["detail"]: r for r in merged["detectors"][0]["rows"]}
    assert set(rows) == {"state R", "state R+"}, merged["detectors"][0]["rows"]
    assert rows["state R"]["runs"] == "3/3", rows["state R"]
    assert rows["state R"]["total_ms"] == 105.0, rows["state R"]
    assert rows["state R+"]["total_ms"] == 21.0, rows["state R+"]
    # Each phenomenon was steady; the merged spread has to say so.
    assert rows["state R"]["spread"]["total_ms"] == {
        "min": 100.0, "max": 110.0, "values": [100.0, 110.0, 105.0]}, rows["state R"]

    # A report from before the field existed keeps the old behaviour rather
    # than failing: location alone, exactly as it was merged then.
    old = copy.deepcopy([run(100.0, 20.0), run(110.0, 22.0)])
    for r in old:
        del r["detectors"][0]["identity"]
    assert len(aggregate(old)["detectors"][0]["rows"]) == 1


@check("every detector names one row per run, by its own @identity")
def _(report):
    """The declaration and the query's GROUP BY, checked against each other.

    @identity is written by hand in the .sql header and nothing in SQLite can
    be asked whether it is right. What can be checked is the consequence: if
    the declared columns leave two rows of one run indistinguishable, merging
    repeats will fold them together and the declaration is short a column.
    """
    from .report import identity_of
    for d in report["detectors"]:
        identity = identity_of(d)
        assert "location" in identity, (d["id"], identity)
        keys = [tuple(r.get(c) for c in identity) for r in d["rows"]]
        assert len(set(keys)) == len(keys), (
            f"{d['id']} declares @identity {', '.join(identity)} and produced "
            f"two rows that share it: {[k for k in keys if keys.count(k) > 1]}"
        )


@check("merging repeats: a single trace stays itself")
def _(report):
    from .report import aggregate
    assert aggregate([report]) is report, "a needless wrapper on a single run"


# --- the .claude/ layer ----------------------------------------------------

@check(".claude/ layer: every part of the template is present")
def _(report):
    import json as _json
    from .layer import CLAUDE_DIR
    required = [
        "skills/echolot/SKILL.md",
        "agents/perf-hunter.md",
        "commands/echolot-setup.md",
        "commands/echolot-hunt.md",
        "settings.json",
    ]
    for rel in required:
        assert (CLAUDE_DIR / rel).exists(), f"the template has no {rel}"
    # A broken settings.json quietly removes the permissions, and the agent
    # starts asking for confirmation on every call.
    _json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))


@check(".claude/ layer: doctor tells stale from customised from current")
def _(report):
    import argparse
    import contextlib
    import io
    from .main import cmd_init
    from .layer import LAYER_MANIFEST, audit, write_manifest
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        assert audit(project) is None, "no layer yet must be None"
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        assert (project / ".claude" / LAYER_MANIFEST).exists(), "init writes the manifest"
        st = audit(project)
        assert st and st["manifest"], st
        assert {r["state"] for r in st["rows"]} == {"current"}, st["rows"]

        skill = project / ".claude" / "skills" / "echolot" / "SKILL.md"
        agent = project / ".claude" / "agents" / "perf-hunter.md"
        # the project edits one file: customised, not stale
        skill.write_text(skill.read_text(encoding="utf-8") + "\n# ours\n", encoding="utf-8")
        # the template moves on for another. Stale means: the file equals
        # what init wrote and the template no longer does — emulated by
        # changing the installed copy and recording that as what init wrote.
        from .layer import sha
        agent.write_text(agent.read_text(encoding="utf-8") + "\n# older\n", encoding="utf-8")
        write_manifest(project / ".claude", {"agents/perf-hunter.md": sha(agent)})
        # and one file goes missing
        (project / ".claude" / "commands" / "echolot-hunt.md").unlink()

        by = {r["file"]: r["state"] for r in audit(project)["rows"]}
        assert by["skills/echolot/SKILL.md"] == "customised", by
        assert by["agents/perf-hunter.md"] == "stale", by
        assert by["commands/echolot-hunt.md"] == "missing", by
        assert by["settings.json"] == "current", by

        # init without --force keeps both edited files, restores the missing one
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        assert "≠ .claude/skills/echolot/SKILL.md (already there and customised" in out.getvalue(), out.getvalue()
        assert "+ .claude/commands/echolot-hunt.md" in out.getvalue(), out.getvalue()
        assert "# ours" in skill.read_text(encoding="utf-8")
        # --force overwrites and says which one had local edits
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_init(argparse.Namespace(into=str(project), force=True, no_doctor=True))
        assert "! .claude/skills/echolot/SKILL.md" in out.getvalue(), out.getvalue()
        assert {r["state"] for r in audit(project)["rows"]} == {"current"}

        # a layer installed before the manifest existed: differs, not stale
        (project / ".claude" / LAYER_MANIFEST).unlink()
        skill.write_text("edited", encoding="utf-8")
        st = audit(project)
        assert not st["manifest"]
        by = {r["file"]: r["state"] for r in st["rows"]}
        assert by["skills/echolot/SKILL.md"] == "differs", by


@check("status: the next step follows the project's state, first visit to return")
def _(report):
    import argparse
    import contextlib
    import io
    from .main import cmd_init
    from .state import NEXT_KINDS, next_kind, next_step, project_state
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        # nothing here yet: install the layer
        st = project_state(project)
        assert next_kind(st) == "init" and next_step(st).startswith("echolot init"), next_step(st)
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        # layer, no config: build the config — /echolot does that
        st = project_state(project)
        assert next_kind(st) == "setup" and "/echolot" in next_step(st), next_step(st)
        (project / "echolot.yml").write_text(
            "project:\n  process: app\nscenario:\n  name: checkout\n", encoding="utf-8")
        # config, no traces: hunt (it collects), or collect by hand
        st = project_state(project)
        assert st["config"]["scenario"] == "checkout" and st["config"]["thresholds"] == "built-in defaults", st["config"]
        assert next_kind(st) == "hunt" and "collect" in next_step(st), next_step(st)
        # traces present: hunt or analyze
        (project / ".echolot" / "traces").mkdir(parents=True)
        (project / ".echolot" / "traces" / "checkout_iter000.perfetto-trace").write_bytes(b"x")
        st = project_state(project)
        assert st["traces"]["count"] == 1 and "analyze" in next_step(st), next_step(st)
        # a config that does not load is said, not swallowed
        (project / "echolot.yml").write_text("project: [\n", encoding="utf-8")
        st = project_state(project)
        assert st["config"] and st["config"].get("error"), st["config"]
        assert next_kind(st) == "fix-config" and next_step(st).startswith("fix echolot.yml"), next_step(st)
        # an open investigation nobody has touched for a while, with history
        # on disk: the human is asked rather than attached to it silently
        from . import hunt as hunt_mod
        (project / "echolot.yml").write_text(
            "project:\n  process: app\nscenario:\n  name: checkout\n", encoding="utf-8")
        hunt_mod.open_new(project, "checkout got slower", scenario="checkout")
        st = project_state(project)
        assert next_kind(st) == "hunt", "just opened — the same sitting, do not ask"
        aged = hunt_mod.load(project)
        aged["touched_at"] = "2020-01-01T00:00:00+00:00"
        hunt_mod.save(project, aged)
        st = project_state(project)
        assert next_kind(st) == "resume-or-new", next_kind(st)
        assert "carry on" in next_step(st), next_step(st)
        # and the loop is never asked: its own work keeps the hunt current
        hunt_mod.touch(project, analyze=True)
        assert next_kind(project_state(project)) == "hunt", \
            "the loop must never be interrupted by the choice"
        # every kind the skill switches on is one the decision can produce
        assert set(NEXT_KINDS) >= {"init", "init-force", "doctor", "setup",
                                   "fix-config", "resume-or-new", "hunt"}


@check("guide: printed knowledge covers every `next` the tool can produce")
def _(report):
    """The guide is what an agent outside Claude Code has to work from.

    `.claude/` is a Claude Code mechanism; in Cursor or Codex it is an
    invisible directory, and the reported symptom was a tool that "sometimes
    follows the instructions" — the model found SKILL.md by chance or it did
    not. `echolot guide` replaces chance with a command, which only works if
    the guide actually answers every state the tool can report.
    """
    from .state import NEXT_KINDS
    from .layer import GUIDE_DIR

    overview = (GUIDE_DIR / "overview.md").read_text(encoding="utf-8")
    missing = [k for k in NEXT_KINDS if f"`{k}`" not in overview]
    assert not missing, f"`next` words with no branch in the guide: {missing}"
    for topic in ("setup", "hunt"):
        assert (GUIDE_DIR / f"{topic}.md").exists(), f"guide {topic} is missing"
        assert f"guide {topic}" in overview, \
            f"the overview never sends anyone to `guide {topic}`"
    # The rule the whole design rests on, in the words an agent will act on.
    assert "Never open the trace yourself" in overview


@check("guide: a client pointer sends the agent to the guide, not to a copy")
def _(report):
    """Stubs must stay stubs.

    One knowledge file per client is how four copies drift apart. Each pointer
    is a few lines naming the command; the text lives in the package, so it
    cannot go stale inside somebody's repository the way a committed copy
    does. A pointer that grew into a copy would bring the drift back.
    """
    from . import hosts as hosts_mod

    for host in hosts_mod.HOSTS:
        if host.key == "claude":
            continue
        text = host.render()
        assert "echolot guide" in text, f"{host.key} never names the guide"
        assert hosts_mod.MARKER in text, f"{host.key} carries no marker to find it by"
        assert hosts_mod.END_MARKER in text, \
            f"{host.key} has no end marker, so nothing can say where it stops"
        assert len(text.splitlines()) < 40, \
            f"{host.key} is turning into a copy of the guide ({len(text.splitlines())} lines)"

    # A file the project wrote itself is never rewritten.
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        own = project / "AGENTS.md"
        own.write_text("# our own rules\n", encoding="utf-8")
        what, _ = hosts_mod.write_stub(project, hosts_mod.BY_KEY["agents"])
        assert what == "exists-without-ours", what
        assert own.read_text(encoding="utf-8") == "# our own rules\n", \
            "a project's own AGENTS.md was edited"


@check("init: run again, the pointer is updated and the project's own text is not")
def _(report):
    """The file these stubs go in belongs to the project, not to echolot.

    `AGENTS.md` is where a team keeps its instructions for its own agents, and
    `init` is meant to be run again — it says so, and it is the one command a
    person has to know. So the ordinary sequence is: install, write your own
    rules underneath, install again.

    That sequence used to delete the rules. The guard asked only whether our
    marker was in the file; when it was, and the content differed, the whole
    file was rewritten and reported as `updated`. Nothing said what had gone.
    """
    from . import hosts as hosts_mod

    agents = hosts_mod.BY_KEY["agents"]
    theirs = "\n## Our build rules\n\nRun ./gradlew spotlessApply first.\n"

    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        assert hosts_mod.write_stub(project, agents)[0] == "written"
        own = project / "AGENTS.md"

        # Nothing to do the second time round.
        assert hosts_mod.write_stub(project, agents)[0] == "current", \
            "a freshly written stub reads as out of date"

        own.write_text(own.read_text(encoding="utf-8") + theirs, encoding="utf-8")
        what, _ = hosts_mod.write_stub(project, agents)
        after = own.read_text(encoding="utf-8")
        assert theirs in after, (
            f"`init` deleted the project's own section from AGENTS.md "
            f"(reported {what!r})")
        assert hosts_mod.MARKER in after and hosts_mod.END_MARKER in after, \
            "the pointer went missing while the project's text was kept"

        # And a stale section really is brought up to date, in place.
        stale = after.replace("echolot guide", "echolot gide")
        own.write_text(stale, encoding="utf-8")
        assert hosts_mod.write_stub(project, agents)[0] == "updated"
        fixed = own.read_text(encoding="utf-8")
        assert "echolot gide" not in fixed, "a stale pointer was left stale"
        assert theirs in fixed, "updating the pointer took the project's text"

    # A file from before the section had an end, plus something written after
    # it: where ours stops cannot be known, so nothing is written at all.
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        own = project / "AGENTS.md"
        legacy = hosts_mod._without_an_end(agents.render()) + theirs
        own.write_text(legacy, encoding="utf-8")
        what, _ = hosts_mod.write_stub(project, agents)
        assert what == "ours-without-an-end", what
        assert own.read_text(encoding="utf-8") == legacy, \
            "a file with no end marker was rewritten on a guess"

    # The same file untouched is exactly what an earlier echolot wrote, so it
    # is ours alone and this run gives it the end marker it lacks.
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        own = project / "AGENTS.md"
        own.write_text(hosts_mod._without_an_end(agents.render()), encoding="utf-8")
        assert hosts_mod.write_stub(project, agents)[0] == "updated"
        assert hosts_mod.END_MARKER in own.read_text(encoding="utf-8"), \
            "an older install was not migrated to the marked form"


@check("init: the picker can never hang an agent or the self-check")
def _(report):
    """The one way this feature could be worse than not having it.

    `echolot init` is run by agents, and by this very self-check five times
    over. A prompt that appears there is a hang, not a question. Two gates
    have to hold: only the CLI parser turns prompting on, so a direct call
    with a bare Namespace is silent whatever the terminal is doing; and even
    with the flag there must be a terminal on both ends.
    """
    import argparse

    from . import hosts as hosts_mod
    from .main import cmd_init

    # A bare Namespace is what the self-check itself passes.
    bare = argparse.Namespace(into=".", force=False, no_doctor=True)
    assert not getattr(bare, "interactive", False), \
        "a direct call would prompt — the self-check would hang"

    class Tty:
        def isatty(self): return True
        def write(self, *a): pass
        def flush(self): pass

    saved = os.environ.get("CI")
    try:
        os.environ["CI"] = "1"
        assert not hosts_mod.interactive(Tty()), "CI is not a place to ask questions"
        os.environ.pop("CI")
        os.environ["ECHOLOT_NO_INPUT"] = "1"
        assert not hosts_mod.interactive(Tty()), "ECHOLOT_NO_INPUT was ignored"
    finally:
        os.environ.pop("ECHOLOT_NO_INPUT", None)
        if saved is not None:
            os.environ["CI"] = saved
        else:
            os.environ.pop("CI", None)

    class NotTty(Tty):
        def isatty(self): return False
    assert not hosts_mod.interactive(NotTty()), "asked without a terminal"

    # And it really does install, silently, when called the bare way.
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        with recorder.isolated():
            cmd_init(argparse.Namespace(into=str(project), force=False,
                                        no_doctor=True))
        assert (project / ".claude").is_dir(), "the bare call installed nothing"


@check("init: declining Claude Code is remembered, not asked again forever")
def _(report):
    """Opting out must not become a loop.

    `.claude/` absent normally means "run init". On a project that chose
    Cursor only, that same absence would have `next` demand `echolot init`
    every time — on a project that had just declined it.
    """
    import argparse

    from . import hosts as hosts_mod
    from .main import cmd_init
    from .state import next_kind, project_state

    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        (project / "echolot.yml").write_text(
            "project:\n  process: com.example.app\n", encoding="utf-8")
        with recorder.isolated():
            cmd_init(argparse.Namespace(into=str(project), force=False,
                                        no_doctor=True, for_hosts="cursor"))
        assert not (project / ".claude").exists(), \
            "the layer went in despite not being chosen"
        assert (project / ".cursor" / "rules" / "echolot.mdc").exists()
        assert hosts_mod.load_choice(project) == ["cursor"], \
            hosts_mod.load_choice(project)

        st = project_state(project, "echolot.yml")
        assert st["layer_verdict"] == "opted-out", st["layer_verdict"]
        assert next_kind(st) != "init", "a project that declined is still asked to init"


@check("CLI: every verb is grouped by audience and shown in --help")
def _(report):
    """The header of `--help` is generated, so nothing can quietly fall out.

    Three audiences share this CLI. While the split lived in prose, `--help`
    showed a flat list of equals and argparse printed a second copy below it.
    Both the grouping and the reading order now come from the registration —
    and a verb added without a place in either would silently vanish from the
    only list a person reads.
    """
    from .main import ORDER, build_parser

    parser = build_parser()
    verbs = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            verbs = set(action.choices)
            break
    assert verbs, "no subcommands are registered"
    assert verbs <= set(ORDER), f"no place in the reading order: {sorted(verbs - set(ORDER))}"

    help_text = parser.format_help()
    missing = [v for v in verbs if f"  {v} " not in help_text
               and f"  {v}\n" not in help_text]
    assert not missing, f"registered but absent from --help: {sorted(missing)}"
    # The second, ungrouped copy argparse prints when a subparser carries
    # help= — and the literal it prints when that help is SUPPRESS.
    assert "==SUPPRESS==" not in help_text, "a subparser was given help=SUPPRESS"
    for title in ("Yours:", "The pipeline:", "The agent's:"):
        assert title in help_text, f"the {title} group is missing from --help"


@check("CLI: status reports and does not mutate")
def _(report):
    """`status` grew four hidden flags that opened and closed investigations.

    That was deliberate and temporary — the verbs were due a rethink and a
    published name would have had to be carried. The rethink happened: the
    state lives in `hunt`. A reporting command must not change what it reports
    on, or `echolot` in a loop stops being safe to run.
    """
    from .main import build_parser

    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            flags = {s for a in action.choices["status"]._actions for s in a.option_strings}
            assert not any("hunt" in f for f in flags), \
                f"status can still mutate the investigation: {sorted(flags)}"
            assert "--resume" in {s for a in action.choices["hunt"]._actions
                                  for s in a.option_strings}, \
                "hunt has no --resume"
            return
    raise AssertionError("no subcommands are registered")


@check(".claude/ layer: the skill knows every `next` the tool can produce")
def _(report):
    """A word `status --next` prints that SKILL.md does not list is a dead end.

    The skill dispatches on that one word. Adding a state to `next_kind` and
    forgetting the row leaves the agent holding a value it has no branch for,
    and what it does then is anyone's guess.
    """
    from .state import NEXT_KINDS
    from .layer import CLAUDE_DIR
    skill = (CLAUDE_DIR / "skills" / "echolot" / "SKILL.md").read_text(encoding="utf-8")
    missing = [k for k in NEXT_KINDS if f"`{k}`" not in skill]
    assert not missing, f"SKILL.md has no branch for: {missing}"


@check(".claude/ layer: the skill and the agent have frontmatter")
def _(report):
    from .layer import CLAUDE_DIR
    for rel in ("skills/echolot/SKILL.md", "agents/perf-hunter.md"):
        text = (CLAUDE_DIR / rel).read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{rel}: no frontmatter"
        head = text.split("---", 2)[1]
        assert "description:" in head, \
            f"{rel}: no description — it will never trigger"


@check(".claude/ layer: reference links are not broken")
def _(report):
    import re as _re
    from .layer import CLAUDE_DIR
    root = CLAUDE_DIR / "skills" / "echolot"
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(_re.findall(r"references/([\w-]+\.md)", skill))
    assert mentioned, "the skill references no document at all"
    for name in mentioned:
        assert (root / "references" / name).exists(), (
            f"SKILL.md points at references/{name}, but the file is missing"
        )
    on_disk = {p.name for p in (root / "references").glob("*.md")}
    assert on_disk <= mentioned, (
        f"a reference exists but the skill does not know about it: "
        f"{on_disk - mentioned}"
    )


@check("docs: every document under docs/ is reachable, and no link dangles")
def _(report):
    """Reachable from the README or from the index the README points at.

    Two link forms have to count. The README is also the PyPI long
    description, where a relative path resolves against pypi.org, so its links
    are absolute; docs/README.md is only ever read on GitHub, so its links are
    relative. An earlier version of this check knew only the relative form and
    passed vacuously — it went green on a README that linked nothing at all.
    """
    import re as _re
    from .layer import CLAUDE_DIR
    # The repo root, when we are running from a source checkout. An installed
    # package has neither README nor docs/ — nothing to verify there, so the
    # check passes trivially rather than failing for every user.
    root = CLAUDE_DIR.parent.parent
    readme = root / "README.md"
    index = root / "docs" / "README.md"
    if not readme.exists() or not (root / "docs").is_dir():
        return

    # (docs/name.md) and (https://github.com/…/docs/name.md) alike, plus the
    # index's own (name.md) written from inside docs/.
    def links(path, bare=False):
        if not path.exists():
            return set()
        text = path.read_text(encoding="utf-8")
        found = set(_re.findall(r"\((?:[^)\s]*/)?docs/([\w-]+\.md)\)", text))
        if bare:
            found |= set(_re.findall(r"\(([\w-]+\.md)\)", text))
        return found

    from_readme = links(readme)
    linked = from_readme | links(index, bare=True)
    assert from_readme or linked, "nothing under docs/ is linked from anywhere"
    for name in sorted(linked):
        assert (root / "docs" / name).exists(), (
            f"a link points at docs/{name}, but the file is missing"
        )
    # The index is the way in, not a document to be indexed by itself.
    on_disk = {p.name for p in (root / "docs").glob("*.md")} - {"README.md"}
    assert on_disk <= linked, (
        f"a document exists under docs/ but nothing links it: "
        f"{sorted(on_disk - linked)}"
    )
    if index.exists():
        assert on_disk <= links(index, bare=True), (
            f"the index does not list: {sorted(on_disk - links(index, bare=True))}"
        )


# --- the run ---------------------------------------------------------------

def build_report(tp_binary: str | None = None) -> dict:
    """Builds the fixture into a temp file and runs the detectors over it."""
    from .main import analyze_trace# late import: main imports us

    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "fixture.perfetto-trace"
        trace.write_bytes(fixture.build())
        return analyze_trace(trace, Config(FIXTURE_CONFIG), tp_binary)


def _where(e: BaseException) -> str:
    """`at selftest.py:412` — the line the check gave up on."""
    import traceback
    frames = traceback.extract_tb(e.__traceback__)
    if not frames:
        return ""
    last = frames[-1]
    return f" at {Path(last.filename).name}:{last.lineno}"


def _why(e: BaseException) -> str:
    """Why a check did not pass, in a sentence that says which kind of not.

    An AssertionError is the claim failing: the pipeline computed something
    other than what the fixture plants, which is the whole point of the
    check. Anything else is the check itself breaking — a KeyError over a
    renamed field, an OSError on a temp directory — and that is a different
    problem with a different fix, so it does not get to wear the first one's
    words.
    """
    if isinstance(e, AssertionError):
        # A bare `assert x in y` raises with no message at all, and `str(e)`
        # is then the empty string — which the caller's `if why` reads as
        # "this one passed". doctor reported 65 of 65 while a check was
        # failing, for as long as that stood.
        return str(e) or f"assertion failed{_where(e)}"
    return f"the check itself raised {type(e).__name__}{_where(e)}: {e}"


def run_checks(report: dict, checks) -> list[tuple[str, str | None]]:
    """[(check name, None if it passed else why)] — the judging half.

    Separated from `run` so that what happens to a check that breaks can be
    checked over made-up checks, rather than by breaking a real one.
    """
    from . import recorder
    out = []
    # Checks call commands of their own (`init` into a temp dir); their notes
    # must not land in the log line of the doctor run that hosts them.
    with recorder.isolated():
        for name, fn in checks:
            try:
                fn(report)
                out.append((name, None))
            except Exception as e:
                # Every check fails on its own. Letting anything but an
                # AssertionError out of here took the whole run with it:
                # doctor caught it far above, printed "could not run" and
                # declared the environment broken — on a machine where 78 of
                # the 79 checks would have passed, and without naming the one
                # that did not.
                out.append((name, _why(e)))
    return out


def run(tp_binary: str | None = None) -> list[tuple[str, str | None]]:
    """[(check name, None if it passed else the mismatch text)]."""
    return run_checks(build_report(tp_binary), CHECKS)
