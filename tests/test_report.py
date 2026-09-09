#!/usr/bin/env python3
"""Merging repeats — what survives it, and what the median is allowed to eat.

Built by hand rather than from traces: `aggregate` is arithmetic over the
report structure and needs no trace_processor, so these run in milliseconds and
say exactly which shape produced which merge.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402


def one(rows: list[dict], det_id: str = "d",
        environment: dict | None = None) -> dict:
    return {
        "schema": 1, "generated_at": "2026-08-19T10:00:00+00:00",
        "trace": "t.perfetto-trace", "toolchain": {},
        "window": {"process": "com.example.app", "duration_ms": 1000.0},
        "environment": environment or {},
        "summary": {"detectors_run": 1, "detectors_fired": int(bool(rows)),
                    "fired_ids": [det_id] if rows else []},
        "detectors": [{"id": det_id, "title": det_id, "why": "", "params": {},
                       "params_source": "default", "error": None, "rows": rows}],
    }


def env(mhz: float, *, throttled: bool = False) -> dict:
    return {
        "cpu": {"mean_mhz": mhz, "min_mhz": 300.0, "max_mhz": 2400.0,
                "on_cpu_ms": 900.0, "measured_ms": 900.0},
        "thermal": {"max_celsius": 55.0 if not throttled else 78.0,
                    "hottest_zone": "cpu-therm", "throttled": throttled,
                    "throttle_device": "thermal-cpufreq-0" if throttled else None},
        "memory": None,
        "missing": ["memory"],
    }


def row(location: str, **cols) -> dict:
    return {"location": location, **cols}


def merged_rows(reports: list[dict]) -> dict[str, dict]:
    out = report_mod.aggregate(reports)
    return {r["location"]: r for r in out["detectors"][0]["rows"]}


def test_an_outlier_seen_in_one_run_of_three_survives_the_merge():
    """The question the working notes left open when the detector was sketched.

    The worry was that `aggregate` medians every numeric column, so a spike in
    one run of ten would be smoothed into nothing. It does not happen here, and
    the reason is structural rather than lucky: main_thread_outlier only emits
    a row for a run where something was actually out of line, so the median is
    taken over the runs that saw it — one value, which is that value. The
    `runs` column then says 1/3, which is the honest reading.

    Where the worry does hold is a detector whose row is present every run and
    only its max_ms spikes. That is main_thread_block, and it is exactly why
    `spread` keeps the per-run values.
    """
    quiet = one([])
    loud = one([row("inflate", count=1, total_ms=86.0, max_ms=86.0)])
    got = merged_rows([quiet, loud, quiet])

    check("the row survives three runs it appeared in once", "inflate" in got, got)
    check("its size is not smoothed away", got["inflate"]["max_ms"] == 86.0,
          got["inflate"])
    check("and the merge says how rare it was",
          got["inflate"]["runs"] == "1/3", got["inflate"])


def test_the_clock_is_merged_across_repeats_and_keeps_its_spread():
    """A median clock, and the range that says whether the median means anything."""
    got = report_mod.aggregate([one([], environment=env(mhz)) for mhz in
                                (900.0, 1800.0, 2000.0)])["environment"]

    check("the typical repeat, like every other number in a merged report",
          got["cpu"]["mean_mhz"] == 1800.0, got["cpu"])
    check("and the range, because a set spanning 900 to 2000 MHz is a set to "
          "throw away",
          (got["cpu"]["mean_mhz_min"], got["cpu"]["mean_mhz_max"]) == (900.0, 2000.0),
          got["cpu"])
    check("every repeat carried a clock", got["cpu"]["runs"] == "3/3", got["cpu"])


def test_one_throttled_repeat_is_not_outvoted_by_the_others():
    """Throttling does not take a median.

    Nine clean repeats and one throttled one is a fact about the set. A
    majority vote would round it away, and the reader would never learn that
    a tenth of the numbers were measured on a machine that had capacity taken
    from it.
    """
    got = report_mod.aggregate(
        [one([], environment=env(1800.0)) for _ in range(9)]
        + [one([], environment=env(1800.0, throttled=True))])["environment"]

    check("one throttled repeat carries", got["thermal"]["throttled"] is True,
          got["thermal"])
    check("and it says how many", got["thermal"]["throttled_runs"] == "1/10",
          got["thermal"])
    check("the peak temperature is the worst seen, not the typical one",
          got["thermal"]["max_celsius"] == 78.0, got["thermal"])


def test_repeats_without_platform_state_merge_to_not_recorded():
    got = report_mod.aggregate([one([]) for _ in range(3)])["environment"]
    check("nothing recorded stays nothing recorded",
          got["missing"] == ["cpu", "memory", "thermal"], got)
    check("and never becomes a zero", got["cpu"] is None, got)


def test_a_spike_inside_a_row_that_is_always_there_is_smoothed_and_kept():
    """The other half. A median does hide this one, and `spread` is why it is
    still findable."""
    runs = [one([row("draw", count=4, self_ms=120.0, max_ms=20.0)]),
            one([row("draw", count=4, self_ms=121.0, max_ms=890.0)]),
            one([row("draw", count=4, self_ms=119.0, max_ms=21.0)])]
    got = merged_rows(runs)["draw"]

    check("the median does smooth the spike out of the column",
          got["max_ms"] == 21.0, got)
    check("the runs column cannot help — the row is in every run",
          got["runs"] == "3/3", got)
    check("and the spike is still in the report, under spread",
          (got["spread"]["max_ms"]["max"]) == 890.0, got.get("spread"))
    check("with one value per run it was found in",
          got["spread"]["max_ms"]["values"] == [20.0, 890.0, 21.0],
          got["spread"]["max_ms"])


def test_a_single_report_is_returned_untouched():
    """One trace in, no `runs` column, no spread — there is nothing to merge."""
    only = one([row("draw", count=4, self_ms=120.0)])
    out = report_mod.aggregate([only])
    check("the same object comes back", out is only, out)
    only_row = out["detectors"][0]["rows"][0]
    check("and it carries no merge bookkeeping", "spread" not in only_row, only_row)


def test_evidence_comes_from_the_worst_repeat():
    """Two runs, two different owners. The detail that says most is the one to keep."""
    runs = [one([row("lock", count=1, total_ms=10.0, detail="owner tid 111")]),
            one([row("lock", count=1, total_ms=90.0, detail="owner tid 222")])]
    got = merged_rows(runs)["lock"]
    check("the heavier run's evidence is the one kept",
          got["detail"] == "owner tid 222", got)


# --- detectors a config left out --------------------------------------------

def test_a_report_names_the_detectors_the_config_left_out():
    """"3 of 6" reads as though six were all there is.

    A project config that names detectors enables only those. A detector
    shipped later never runs there, and until the report said so, nothing did:
    a real project was missing two of eight for months after they landed.
    """
    rep = report_mod.build(
        "t.perfetto-trace", {"process": "p", "duration_ms": 1.0},
        [{"id": "d", "title": "T", "why": "", "params": {},
          "params_source": "config", "error": None, "rows": []}],
        absent=["main_thread_outlier", "frame_jank"])

    check("the ids are in the summary, sorted",
          rep["summary"]["absent_ids"] == ["frame_jank", "main_thread_outlier"],
          rep["summary"])
    text = report_mod.to_markdown(rep)
    check("and the markdown says it above the config line",
          "did not run" in text and "`frame_jank`" in text, text[:400])
    check("with what to do about it",
          "--defaults" in text, text[:400])


def test_nothing_is_said_when_the_config_leaves_nothing_out():
    rep = report_mod.build(
        "t.perfetto-trace", {"process": "p", "duration_ms": 1.0},
        [{"id": "d", "title": "T", "why": "", "params": {},
          "params_source": "default", "error": None, "rows": []}])
    check("no ids", rep["summary"]["absent_ids"] == [], rep["summary"])
    check("and no warning", "did not run" not in report_mod.to_markdown(rep))


def test_the_absent_set_survives_merging_repeats():
    """One config produced every repeat, so it left the same ones out."""
    runs = [report_mod.build("a", {"process": "p", "duration_ms": 1.0},
                             [{"id": "d", "title": "T", "why": "", "params": {},
                               "params_source": "config", "error": None,
                               "rows": [row("x", self_ms=1.0)]}],
                             absent=["frame_jank"])
            for _ in range(3)]
    merged = report_mod.aggregate(runs)
    check("carried through the merge",
          merged["summary"]["absent_ids"] == ["frame_jank"], merged["summary"])


# --- the files behind "Runs: N" ---------------------------------------------

def _merged(*paths: str) -> dict:
    runs = []
    for p in paths:
        r = one([{"location": "x", "total_ms": 1.0}])
        r["trace"] = p
        runs.append(r)
    return report_mod.aggregate(runs)


def test_the_header_names_the_traces_it_merged():
    """A count alone hid a stray trace for a whole session.

    A probe capture left in `.echolot/traces/` was swept up by the documented
    `analyze .echolot/traces/*.perfetto-trace`. `Runs: 4` after a `collect`
    that recorded three read as ordinary; the medians spanned two sittings and
    one detector row came from the stray file alone.
    """
    text = report_mod.to_markdown(_merged(
        ".echolot/traces/coldStart_iter000.perfetto-trace",
        ".echolot/traces/coldStart_iter001.perfetto-trace",
        ".echolot/traces/coldStart_probe_2026-09-05.perfetto-trace"))

    check("the count is still there", "Runs: **3**" in text, text[:200])
    check("and every basename under it",
          all(f"`coldStart_{n}`" in text
              for n in ("iter000", "iter001", "probe_2026-09-05")), text[:300])
    check("without the directory or the suffix",
          ".echolot/traces" not in text and ".perfetto-trace" not in text,
          text[:300])


def test_a_long_set_of_traces_is_cut_rather_than_wrapped():
    """Twenty repeats is a legitimate round; twenty names is not a header."""
    text = report_mod.to_markdown(_merged(
        *[f"run_iter{i:03d}.perfetto-trace" for i in range(20)]))

    check("the first seven are named", "`run_iter006`" in text, text[:400])
    check("the rest are counted", "and 13 more" in text, text[:400])
    check("the count is the real one", "Runs: **20**" in text, text[:200])


def test_a_single_trace_gets_no_traces_line():
    """One trace is already named on the `Trace:` line above."""
    text = report_mod.to_markdown(one([{"location": "x", "total_ms": 1.0}]))
    check("no list", "Traces:" not in text, text[:200])


def budget(**kw) -> dict:
    base = {"on_cpu": 0.0, "waiting_for_cpu": 0.0, "in_kernel": 0.0,
            "sleeping": 0.0, "other": 0.0, "window_ms": 1000.0}
    base.update(kw)
    base["accounted_ms"] = round(sum(base[k] for k in (
        "on_cpu", "waiting_for_cpu", "in_kernel", "sleeping", "other")), 2)
    base["accounted_pct"] = round(base["accounted_ms"] / base["window_ms"] * 100, 1)
    return base


def with_budget(b: dict) -> dict:
    r = one([])
    r["window"] = {**r["window"], "main_thread": b}
    return r


def test_the_budget_is_merged_bucket_by_bucket():
    """A typical run's kernel time, not the kernel time of a typical run.

    Taken from the first report — which is what every key of `window` used to
    be — three repeats would be described by whichever one happened to be
    analysed first.
    """
    got = report_mod.aggregate([
        with_budget(budget(on_cpu=500.0, in_kernel=100.0, sleeping=400.0)),
        with_budget(budget(on_cpu=600.0, in_kernel=50.0, sleeping=350.0)),
        with_budget(budget(on_cpu=550.0, in_kernel=90.0, sleeping=360.0)),
    ])["window"]["main_thread"]

    check("each bucket takes its own median",
          (got["on_cpu"], got["in_kernel"]) == (550.0, 90.0), got)
    check("and the total is the sum of what is printed, not a median of totals",
          got["accounted_ms"] == round(550.0 + 90.0 + 360.0, 2), got)
    check("with the repeats it was built from", got["runs"] == "3/3", got)


def test_repeats_without_a_budget_merge_to_nothing():
    check("a report from before the budget existed stays that way",
          report_mod.aggregate([one([]) for _ in range(3)])["window"]
          .get("main_thread") is None)


def test_a_budget_short_of_the_window_is_called_out():
    """The main thread was not there for the whole scenario.

    Shares of a smaller total look exactly like shares of the window, so the
    shortfall has to be a sentence rather than arithmetic the reader is
    invited to do.
    """
    r = with_budget(budget(on_cpu=300.0, sleeping=200.0))
    lines = report_mod.to_markdown(r).splitlines()
    check("the shares are still printed",
          any(line.startswith("Main thread:") for line in lines), lines[:8])
    warned = [line for line in lines if "accounted for" in line]
    check("and the half nobody saw is named", warned, lines[:8])
    check("with the number in it", "50" in warned[0], warned[0])


def test_a_bucket_under_a_percent_stays_out_of_the_line():
    r = with_budget(budget(on_cpu=990.0, in_kernel=5.0, sleeping=5.0))
    line = next(x for x in report_mod.to_markdown(r).splitlines()
                if x.startswith("Main thread:"))
    check("a rounding-error bucket would end the line being read",
          "kernel" not in line and "sleeping" not in line, line)
    check("and the one that matters is there", "99% on a CPU" in line, line)
