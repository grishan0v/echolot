#!/usr/bin/env python3
"""`echolot report` — the views of a report that is already on disk.

On a real hunt the agent cut report.json up sixteen times with jq and
python one-liners: the keys, the window, which detectors fired with which
thresholds, the top rows of one detector with the evidence shortened. Each
of those is a view here, and each is pinned to what the one-liner was after.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

LONG = "monitor contention with owner Thread-3 (4455) at void com.example.Store.put(java.lang.String)(Store.java:41) waiters=0 blocking from java.lang.Object com.example.Store.get()(:-1)"


def sample() -> dict:
    """Three detectors: one with many rows, one silent, one that failed."""
    rows = [{"location": f"slice_{i:02d}", "runs": "3/3", "count": 1,
             "total_ms": float(100 - i), "max_ms": float(100 - i),
             "detail": LONG} for i in range(8)]
    rows[0]["places"] = [{"role": "owner", "symbol": "com.example.Store.put",
                          "file": "app/src/main/java/com/example/Store.java",
                          "line": 41, "exact": True}]
    rows[0]["code"] = "owner at Store.java:41"
    rep = report_mod.build(
        "t.perfetto-trace",
        {"process": "com.example.app", "pid": 4100, "duration_ms": 1005.0,
         "start_anchor": {"glob": "AppStart", "matches": 1},
         "end_anchor": {"glob": "Screen.firstFrame", "matches": 0}},
        [
            {"id": "monitor_contention", "title": "Monitor contention", "why": "locks",
             "params": {"min_block_ms": 8}, "params_source": "config",
             "defaults": {"min_block_ms": 8}, "identity": ["location"],
             "rows": rows, "error": None},
            {"id": "gc_pressure", "title": "GC", "why": "", "params": {},
             "params_source": "default", "identity": ["location"], "rows": [], "error": None},
            {"id": "main_thread_block", "title": "Main thread", "why": "",
             "params": {"min_slice_ms": 16}, "params_source": "default",
             "identity": ["location", "detail"],
             "rows": [{"location": "inflate", "self_ms": 40.0, "total_ms": 41.0,
                       "max_ms": 20.0, "detail": "m.example.app"}], "error": None},
            {"id": "frame_jank", "title": "Frames", "why": "", "params": {},
             "params_source": "default", "identity": ["location"], "rows": [],
             "error": "no such table: actual_frame_timeline_slice"},
        ],
        absent=["io_wait"],
        markers={"prefix": "AGENTTMP_", "globs": ["AGENTTMP_*", "gone"],
                 "rows": [{"location": "AGENTTMP_x", "count": 1, "self_ms": 8.0,
                           "total_ms": 60.0, "max_ms": 60.0, "detail": "Seed"}],
                 "absent": ["gone"]},
    )
    return rep


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --- the views, as functions --------------------------------------------------

def test_the_overview_names_every_detector_with_what_it_found():
    text = report_mod.overview(sample())
    check("the window is on the first lines", "window **1005.0 ms**" in text, text[:300])
    check("a missed anchor is shouted", "⚠️ 0 matches" in text, text[:400])
    check("a detector with rows shows its count and its top row",
          "| monitor_contention | 8 | config | slice_00 | total 100.0 ms |" in text, text)
    check("a silent one says so", "| gc_pressure | — | default | silent |" in text, text)
    check("a failed one says that", "| frame_jank | — | default | failed |" in text, text)
    check("the turned-off detector is named", "Turned off in this config: `io_wait`" in text, text)
    check("the markers are counted", "Markers: 1 measured, 1 listed in `domains`" in text, text)


def test_a_detector_view_cuts_the_rows_and_the_evidence_and_says_so():
    text = report_mod.detector_view(sample(), "monitor_contention", top=3)
    check("the title and the params", "Monitor contention" in text and "min_block_ms" in text, text)
    check("three rows", text.count("| slice_") == 3, text)
    check("the evidence is cut", "…" in text and "Store.get()(:-1)" not in text, text)
    check("and the cut is announced", "_Showing 3 of 8; `--top 8` for all._" in text, text)
    check("places are pointed at", "`--json` carries `places`" in text, text)
    wide = report_mod.detector_view(sample(), "monitor_contention", top=1, wide=True)
    check("--wide keeps the evidence whole", "Store.get()(:-1)" in wide, wide)


def test_the_evidence_column_of_main_thread_block_is_explained():
    """`m.example.app` in a column called Evidence reads as a lost name."""
    text = report_mod.detector_view(sample(), "main_thread_block")
    check("the legend is under the table",
          "Evidence: the thread's comm" in text and "15 characters" in text, text)


def test_a_silent_detector_and_an_unknown_one_read_differently():
    check("silent", "_silent" in report_mod.detector_view(sample(), "gc_pressure"), "")
    text = report_mod.detector_view(sample(), "nothing")
    check("unknown names what there is",
          "no detector `nothing`" in text and "monitor_contention" in text, text)


def test_select_returns_only_what_was_asked_for_and_cuts_the_rows():
    rep = sample()
    summary = report_mod.select(rep, [], None, False, False)
    check("no selection is a summary, rows counted not carried",
          summary["detectors"][0] == {"id": "monitor_contention", "rows": 8,
                                      "params_source": "config", "error": None}, summary)
    one = report_mod.select(rep, ["monitor_contention"], 2, False, False)
    check("one detector, two rows, places intact",
          len(one["detectors"][0]["rows"]) == 2 and one["detectors"][0]["rows"][0]["places"], one)
    check("nothing else came along", set(one) == {"detectors"}, one)
    both = report_mod.select(rep, [], 1, True, True)
    check("window and markers", set(both) == {"window", "environment", "markers"}, both)
    check("markers cut to top", len(both["markers"]["rows"]) == 1, both)


# --- the command --------------------------------------------------------------

def test_the_command_reads_a_path_and_prints_the_view(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(report_mod.to_json(sample()), encoding="utf-8")
    code, out, _ = run("report", str(path))
    check("exit 0", code == 0, code)
    check("the overview", "# Report" in out and "| monitor_contention |" in out, out[:400])
    code, out, _ = run("report", str(path), "-d", "monitor_contention", "--top", "2", "--json")
    got = json.loads(out)
    check("json, two rows", len(got["detectors"][0]["rows"]) == 2, out[:300])
    code, out, _ = run("report", str(path), "--window", "--markers")
    check("both views in order", out.index("## Window") < out.index("## Markers"), out)


def test_the_command_refuses_what_it_cannot_read(tmp_path):
    code, _, err = run("report", str(tmp_path / "missing.json"))
    check("no file is exit 2", code == 2 and "report not found" in err, err)
    path = tmp_path / "report.json"
    path.write_text(report_mod.to_json(sample()), encoding="utf-8")
    code, _, err = run("report", str(path), "-d", "nope")
    check("an unknown detector is exit 2 and lists the known ones",
          code == 2 and "monitor_contention" in err, err)


def test_without_a_path_the_last_report_next_to_the_config_is_read(tmp_path, monkeypatch):
    out_dir = tmp_path / ".echolot" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "report.json").write_text(report_mod.to_json(sample()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, _ = run("report")
    check("found without being told where", code == 0 and "# Report" in out, out[:200])
