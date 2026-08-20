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


def one(rows: list[dict], det_id: str = "d") -> dict:
    return {
        "schema": 1, "generated_at": "2026-08-19T10:00:00+00:00",
        "trace": "t.perfetto-trace", "toolchain": {},
        "window": {"process": "com.example.app", "duration_ms": 1000.0},
        "summary": {"detectors_run": 1, "detectors_fired": int(bool(rows)),
                    "fired_ids": [det_id] if rows else []},
        "detectors": [{"id": det_id, "title": det_id, "why": "", "params": {},
                       "params_source": "default", "error": None, "rows": rows}],
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
