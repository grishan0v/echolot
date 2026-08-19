#!/usr/bin/env python3
"""Self-check for `echolot compare` — the delta between two Marker Reports.

Comparison is arithmetic, and arithmetic over two reports is always willing to
produce a number. What has to be pinned is everything around the number: when a
difference is called real, when it is called noise, when two reports may not be
compared at all, and when a pair of rows is the same thing under a new name.

Most cases build reports by hand — compare reads the report structure and never
touches a trace, so the fast checks need no trace_processor. The last case runs
the real pipeline, because the one failure none of the others would catch is
`analyze` writing a shape `compare` no longer reads.

    python tests/check_compare.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402

RESULTS: list[tuple[str, str | None]] = []


def check(name: str, ok: bool, why: str = "") -> None:
    RESULTS.append((name, None if ok else (why or "failed")))


# --- building reports by hand ----------------------------------------------

def row(location: str, value: float, *, metric: str = "self_ms",
        values: list[float] | None = None, count: float = 1.0) -> dict:
    out: dict = {"location": location, "runs": "3/3", "count": count,
                 metric: value}
    if values:
        out["spread"] = {metric: {"min": min(values), "max": max(values),
                                  "values": values}}
    return out


def det(det_id: str, rows: list[dict], params: dict | None = None) -> dict:
    return {"id": det_id, "title": det_id, "why": "", "rows": rows,
            "params": params or {}, "params_source": "default", "error": None}


def report(detectors: list[dict], *, window: dict | None = None,
           config: dict | None = None, runs: int = 3) -> dict:
    fired = [d["id"] for d in detectors if d["rows"]]
    return {
        "schema": 1,
        "generated_at": "2026-08-19T10:00:00+00:00",
        "trace": "fixture.perfetto-trace",
        "traces": [f"run{i}.perfetto-trace" for i in range(runs)] if runs > 1 else None,
        "runs": runs,
        "toolchain": {},
        "window": window or {"process": "com.example.app", "duration_ms": 1000.0},
        "config": config or {"sha": "aaaa", "defaults": False},
        "summary": {"detectors_run": len(detectors),
                    "detectors_fired": len(fired), "fired_ids": fired},
        "detectors": detectors,
    }


def compare(before: dict, after: dict, **kw) -> dict:
    return compare_mod.build(before, after, before_path="before.json",
                             after_path="after.json", **kw)


def changes(cmp: dict) -> dict[str, str]:
    return {r["location"]: r["change"] for r in cmp["rows"]}


def warned(cmp: dict) -> set[str]:
    return {w["id"] for w in cmp["warnings"]}


# --- what counts as movement ------------------------------------------------

def case_grew_shrank_steady() -> None:
    """The three verdicts on a row that exists in both, and their order."""
    before = report([det("main_thread_block", [
        row("A", 100.0), row("B", 100.0), row("C", 100.0)])])
    after = report([det("main_thread_block", [
        row("A", 200.0), row("B", 50.0), row("C", 104.0)])])
    cmp = compare(before, after)

    check("grew / shrank / steady", changes(cmp) ==
          {"A": "grew", "B": "shrank", "C": "steady"}, str(changes(cmp)))
    check("summary counts the moves",
          (cmp["summary"]["moved"], cmp["summary"]["steady"]) == (2, 1),
          str(cmp["summary"]))

    first = cmp["rows"][0]
    check("the biggest mover is first", first["location"] == "A", first["location"])
    check("delta is after minus before", first["delta_ms"] == 100.0,
          str(first["delta_ms"]))
    check("ratio is after over before", first["ratio"] == 2.0, str(first["ratio"]))
    check("shrank keeps its sign",
          next(r for r in cmp["rows"] if r["location"] == "B")["delta_ms"] == -50.0)


def case_floor_has_two_halves() -> None:
    """Absolute floor and relative floor, each doing the job the other cannot."""
    before = report([det("d", [row("big", 900.0), row("small", 20.0)])])
    after = report([det("d", [row("big", 940.0), row("small", 26.0)])])
    cmp = compare(before, after)

    check("40 ms on 900 is within the relative floor",
          changes(cmp)["big"] == "steady", str(changes(cmp)))
    check("6 ms on 20 clears the absolute floor",
          changes(cmp)["small"] == "grew", str(changes(cmp)))


def case_floor_is_configurable() -> None:
    before = report([det("d", [row("A", 100.0)])])
    after = report([det("d", [row("A", 130.0)])])
    check("30% is a move by default", changes(compare(before, after))["A"] == "grew")
    check("and is not with the floor raised",
          changes(compare(before, after, floor_ratio=0.5))["A"] == "steady")


def case_appeared_and_vanished() -> None:
    before = report([det("d", [row("gone", 88.0)])])
    after = report([det("d", [row("new", 1402.0)])])
    cmp = compare(before, after)

    check("a row only in the later report appeared",
          changes(cmp)["new"] == "appeared", str(changes(cmp)))
    check("a row only in the earlier one is gone",
          changes(cmp)["gone"] == "vanished", str(changes(cmp)))
    check("appeared sorts by what it is worth now",
          cmp["rows"][0]["location"] == "new", cmp["rows"][0]["location"])
    check("appeared has no before side",
          cmp["rows"][0]["before"] is None)
    check("and no ratio, because there is nothing to divide by",
          cmp["rows"][0]["ratio"] is None)


# --- the same thing under a new name ----------------------------------------

def case_family_match() -> None:
    """One worker of a pool handing over to another is not a finding."""
    before = report([det("uninstrumented_cpu", [
        row("DefaultDispatcher-worker-2", 300.0, metric="total_ms")])])
    after = report([det("uninstrumented_cpu", [
        row("DefaultDispatcher-worker-5", 340.0, metric="total_ms")])])
    cmp = compare(before, after)

    check("one row, not one gone and one new", len(cmp["rows"]) == 1,
          str(len(cmp["rows"])))
    check("matched through the name family",
          cmp["rows"][0]["matched_by"] == "family", cmp["rows"][0]["matched_by"])
    check("and it is a plain move", cmp["rows"][0]["change"] == "grew",
          cmp["rows"][0]["change"])


def case_family_refuses_to_guess() -> None:
    """Two candidates on one side: there is no honest pairing, so make none."""
    before = report([det("d", [
        row("worker-2", 300.0, metric="total_ms"),
        row("worker-3", 200.0, metric="total_ms")])])
    after = report([det("d", [row("worker-5", 340.0, metric="total_ms")])])
    cmp = compare(before, after)

    check("ambiguous families are left unpaired",
          sorted(changes(cmp).values()) == ["appeared", "vanished", "vanished"],
          str(changes(cmp)))
    check("nothing is matched by family",
          all(r["matched_by"] == "exact" for r in cmp["rows"]))


# --- did the repeats move, or only the median -------------------------------

def case_ranges_apart() -> None:
    before = report([det("d", [row("A", 102.0, values=[100.0, 102.0, 104.0])])])
    after = report([det("d", [row("A", 205.0, values=[200.0, 205.0, 210.0])])])
    cmp = compare(before, after)
    check("ranges that do not touch are apart", cmp["rows"][0]["overlap"] is False,
          str(cmp["rows"][0]["overlap"]))


def case_ranges_overlap() -> None:
    """Medians moved, but every run after was inside what was already seen."""
    before = report([det("d", [row("A", 100.0, values=[10.0, 100.0, 300.0])])])
    after = report([det("d", [row("A", 150.0, values=[12.0, 150.0, 320.0])])])
    cmp = compare(before, after)
    check("overlapping ranges are called out", cmp["rows"][0]["overlap"] is True,
          str(cmp["rows"][0]["overlap"]))
    check("the move is still reported", cmp["rows"][0]["change"] == "grew")


def case_ranges_unknown() -> None:
    before = report([det("d", [row("A", 100.0)])], runs=1)
    after = report([det("d", [row("A", 200.0)])], runs=1)
    cmp = compare(before, after)
    check("no spread means no verdict", cmp["rows"][0]["overlap"] is None,
          str(cmp["rows"][0]["overlap"]))
    check("and a single trace is said out loud", "single" in warned(cmp),
          str(warned(cmp)))


# --- when two reports may not be compared -----------------------------------

def case_thresholds_moved() -> None:
    """The bar decides which rows exist, so a moved bar invents rows."""
    before = report([det("d", [row("A", 100.0)], params={"min_slice_ms": 16})])
    after = report([det("d", [row("A", 100.0), row("B", 20.0)],
                        params={"min_slice_ms": 5})])
    cmp = compare(before, after)

    check("a threshold change is a warning", "thresholds" in warned(cmp),
          str(warned(cmp)))
    text = next(w["text"] for w in cmp["warnings"] if w["id"] == "thresholds")
    check("naming the parameter and both values",
          "min_slice_ms" in text and "16" in text and "5" in text, text)


def case_process_differs() -> None:
    before = report([det("d", [row("A", 100.0)])],
                    window={"process": "com.example.app", "duration_ms": 1000.0})
    after = report([det("d", [row("A", 100.0)])],
                   window={"process": "com.other.app", "duration_ms": 1000.0})
    cmp = compare(before, after)
    check("two apps are not comparable", cmp["comparable"] is False)
    check("and the reason is named", "process" in warned(cmp), str(warned(cmp)))


def case_anchor_never_matched() -> None:
    before = report([det("d", [row("A", 100.0)])], window={
        "process": "com.example.app", "duration_ms": 1000.0,
        "start_anchor": {"glob": "AppStart", "matches": 0}})
    after = report([det("d", [row("A", 100.0)])])
    check("a window that is the whole trace is a warning",
          "anchor-before" in warned(compare(before, after)),
          str(warned(compare(before, after))))


def case_config_and_defaults() -> None:
    before = report([det("d", [row("A", 100.0)])],
                    config={"sha": "aaaa", "defaults": False})
    after = report([det("d", [row("A", 100.0)])],
                   config={"sha": "bbbb", "defaults": True})
    w = warned(compare(before, after))
    check("an edited config is a warning", "config" in w, str(w))
    check("--defaults on one side only is a warning", "defaults" in w, str(w))


def case_unequal_repeats() -> None:
    before = report([det("d", [row("A", 100.0)])], runs=3)
    after = report([det("d", [row("A", 100.0)])], runs=10)
    check("different repeat counts are said out loud",
          "runs" in warned(compare(before, after)),
          str(warned(compare(before, after))))


def case_detector_sets_differ() -> None:
    before = report([det("one", [row("A", 100.0)])])
    after = report([det("one", [row("A", 100.0)]), det("two", [row("B", 50.0)])])
    check("a detector that ran once is a warning",
          "detectors" in warned(compare(before, after)),
          str(warned(compare(before, after))))


# --- the rest of the report -------------------------------------------------

def case_planted_markers() -> None:
    """A marker the hunt added between rounds is not a new regression."""
    before = report([det("main_thread_block", [row("A", 100.0)])])
    after = report([det("main_thread_block", [
        row("A", 100.0), row("AGENTTMP_loadTeams", 400.0)])])

    plain = compare(before, after)
    check("without the prefix it is just a new row",
          "instrumentation" not in warned(plain), str(warned(plain)))

    told = compare(before, after, temp_prefix="AGENTTMP_")
    check("with it, the row is named as planted",
          "instrumentation" in warned(told), str(warned(told)))
    text = next(w["text"] for w in told["warnings"] if w["id"] == "instrumentation")
    check("and the warning names it", "AGENTTMP_loadTeams" in text, text)
    check("the row stays in the table",
          changes(told)["AGENTTMP_loadTeams"] == "appeared", str(changes(told)))


def case_state_changed() -> None:
    before = report([det("binder_txn", [])])
    after = report([det("binder_txn", [row("t", 214.0, metric="total_ms")])])
    cmp = compare(before, after)
    changed = cmp["summary"]["state_changed"]
    check("silence turning into rows is recorded", len(changed) == 1, str(changed))
    check("with both states", changed and changed[0]["before"] == "silent",
          str(changed))


def case_metric_falls_back_to_total() -> None:
    before = report([det("d", [row("A", 100.0, metric="total_ms")])])
    after = report([det("d", [row("A", 200.0, metric="total_ms")])])
    check("a detector without self time is judged by total",
          compare(before, after)["rows"][0]["metric"] == "total_ms")


def case_window_delta() -> None:
    before = report([det("d", [])], window={"process": "p", "duration_ms": 1184.0})
    after = report([det("d", [])], window={"process": "p", "duration_ms": 2960.0})
    w = compare(before, after)["window"]
    check("the window carries its own delta", w["delta_ms"] == 1776.0, str(w))
    check("and its ratio", w["ratio"] == 2.5, str(w))


def case_markdown() -> None:
    before = report([det("main_thread_block", [row("A", 100.0), row("C", 100.0)],
                         params={"min_slice_ms": 16})])
    after = report([det("main_thread_block", [row("A", 900.0), row("C", 101.0)],
                        params={"min_slice_ms": 5})])
    text = compare_mod.to_markdown(compare(before, after))

    check("the moved row is in the table", "| A |" in text, text[:200])
    check("the steady one is not", "| C |" not in text)
    check("but it is listed as steady", "## Steady" in text and "`C`" in text)
    check("the threshold warning is at the top",
          text.index("⚠️") < text.index("## What moved"))
    check("no python None reaches the page", "None" not in text)


def case_markdown_nothing_moved() -> None:
    before = report([det("d", [row("A", 100.0)])])
    after = report([det("d", [row("A", 101.0)])])
    text = compare_mod.to_markdown(compare(before, after))
    check("a comparison with no movement says so",
          "## Nothing moved" in text, text[:300])


# --- the shape analyze actually writes --------------------------------------

FIXTURE_CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""


def case_end_to_end(tmp: Path) -> None:
    """The real pipeline: analyze writes it, compare reads it.

    Everything above builds reports by hand, which pins the logic and nothing
    about the contract between the two commands. This is the case that fails
    when a column is renamed on one side only.
    """
    env = dict(os.environ, ECHOLOT_NO_RECORD="1")
    trace = tmp / "fixture.perfetto-trace"
    run = subprocess.run([sys.executable, "-m", "echolot.fixture", str(trace)],
                         capture_output=True, text=True, env=env)
    if run.returncode != 0:
        check("the fixture builds", False, run.stderr[-300:])
        return
    (tmp / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")

    def cli(*argv) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                              capture_output=True, text=True, cwd=tmp, env=env)

    done = cli("analyze", str(trace), str(trace), str(trace),
               "-c", "echolot.yml")
    if done.returncode != 0:
        check("analyze runs on the fixture", False, done.stderr[-400:])
        return

    written = json.loads((tmp / ".echolot/out/report.json").read_text())
    banded = [r for d in written["detectors"] for r in d["rows"] if r.get("spread")]
    check("analyze writes the spread compare reads", bool(banded))
    if banded:
        band = next(iter(banded[0]["spread"].values()))
        check("with min, max and the per-run values",
              set(band) == {"min", "max", "values"}, str(sorted(band)))
        check("one value per repeat the row was found in",
              len(band["values"]) == int(banded[0]["runs"].split("/")[0]),
              f"{band['values']} vs {banded[0]['runs']}")

    # The same report with one worker renamed: a real report shape going
    # through the family pass.
    before = tmp / "before.json"
    before.write_text(json.dumps(written), encoding="utf-8")
    after = json.loads(json.dumps(written))
    for d in after["detectors"]:
        for r in d["rows"]:
            r["location"] = r["location"].replace("DefaultDispatcher-worker-1",
                                                  "DefaultDispatcher-worker-3")
    (tmp / "after.json").write_text(json.dumps(after), encoding="utf-8")

    done = cli("compare", "before.json", "after.json", "-c", "echolot.yml")
    check("compare exits 0", done.returncode == 0, done.stderr[-400:])
    check("and writes both files",
          (tmp / ".echolot/out/comparison.json").exists()
          and (tmp / ".echolot/out/comparison.md").exists())

    if (tmp / ".echolot/out/comparison.json").exists():
        cmp = json.loads((tmp / ".echolot/out/comparison.json").read_text())
        worker = [r for r in cmp["rows"] if "DefaultDispatcher" in r["location"]]
        check("the renamed worker is one row, matched by family",
              len(worker) == 1 and worker[0]["matched_by"] == "family",
              str([(r["location"], r["matched_by"]) for r in worker]))
        check("and nothing is reported as moved",
              cmp["summary"]["moved"] == 0, str(cmp["summary"]))

    # A comparison is not a Marker Report, and feeding one back in is a mistake
    # worth naming rather than a stack trace.
    done = cli("compare", ".echolot/out/comparison.json", "before.json",
               "-c", "echolot.yml")
    check("a comparison is refused as input", done.returncode == 2,
          f"exit {done.returncode}")
    check("with a sentence that says why",
          "not a Marker Report" in done.stderr, done.stderr[-200:])


# --- which two reports, when nobody named them ------------------------------

def case_pair_resolution(tmp: Path) -> None:
    """The forms that take their arguments from the open investigation.

    This is the shape the loop uses: change something, record again, ask what
    that did. Getting the pair wrong here compares a round against itself and
    reports that nothing happened.
    """
    from echolot import hunt as hunt_mod

    (tmp / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    out = tmp / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    hunt_mod.open_new(tmp, "cold start 3s -> 7s", scenario="fixture")

    def round_of(value: float) -> None:
        """One analyze: the latest report, then a copy filed under the hunt."""
        body = report([det("d", [row("A", value)])])
        (out / "report.json").write_text(json.dumps(body), encoding="utf-8")
        (out / "report.md").write_text("#", encoding="utf-8")
        hunt_mod.record_report(tmp, out)

    for value in (100.0, 400.0, 900.0):
        round_of(value)

    env = dict(os.environ, ECHOLOT_NO_RECORD="1")

    def cli(*argv) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                              capture_output=True, text=True, cwd=tmp, env=env)

    def delta_of(done: subprocess.CompletedProcess) -> float | None:
        if done.returncode != 0:
            return None
        body = json.loads((out / "comparison.json").read_text())
        return body["rows"][0]["delta_ms"] if body["rows"] else None

    bare = cli("compare")
    check("bare compare takes the previous round against the latest",
          delta_of(bare) == 500.0, f"{delta_of(bare)} / {bare.stderr[-200:]}")

    whole = cli("compare", "--hunt", "1")
    check("--hunt takes the first round against the last",
          delta_of(whole) == 800.0, f"{delta_of(whole)} / {whole.stderr[-200:]}")

    one = cli("compare", ".echolot/hunts/1/reports/001.json")
    check("one path is that report against the latest",
          delta_of(one) == 800.0, f"{delta_of(one)} / {one.stderr[-200:]}")

    missing = cli("compare", "--hunt", "9")
    check("an investigation that does not exist is refused",
          missing.returncode == 2 and "hunt --list" in missing.stderr,
          missing.stderr[-200:])

    too_many = cli("compare", "a.json", "b.json", "c.json")
    check("three reports are refused",
          too_many.returncode == 2 and "at most two" in too_many.stderr,
          too_many.stderr[-200:])


def case_nothing_to_compare(tmp: Path) -> None:
    """No investigation, no arguments: say what to do rather than guess."""
    (tmp / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "echolot.main", "compare"],
                          capture_output=True, text=True, cwd=tmp,
                          env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("nothing to compare is refused", done.returncode == 2,
          f"exit {done.returncode}")
    check("and the message names the three forms",
          "--hunt" in done.stderr and "two reports" in done.stderr,
          done.stderr[-250:])


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main() -> int:
    os.environ["ECHOLOT_NO_RECORD"] = "1"
    for case in CASES:
        try:
            if case.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    case(Path(d))
            else:
                case()
        except Exception as e:                          # noqa: BLE001
            check(f"{case.__name__} raised", False, repr(e))

    failed = [(n, w) for n, w in RESULTS if w]
    for name, why in RESULTS:
        print(f"  ok    {name}" if not why else f"  FAILS {name}\n          {why}")
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
