"""One set of recordings is one report, whatever order the paths come in (#581).

The header's trace, pid and window came from whichever file was named first,
and every `spread.values` list was in the order given. A shell glob, a
directory listing and an unpacked artifact each have an order of their own,
so `analyze` puts the recordings in one: by file name, numbers as numbers,
which is the order `collect` recorded them in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_compare import FIXTURE_CONFIG  # noqa: E402

from echolot import fixture  # noqa: E402
from echolot.main import run_order  # noqa: E402


def test_names_sort_with_their_numbers_as_numbers() -> None:
    names = ["s_iter010.perfetto-trace", "s_iter2.perfetto-trace",
             "b/s_iter001.perfetto-trace", "a/s_iter001.perfetto-trace"]
    check("by directory, then by name, run 2 before run 10",
          sorted(names, key=run_order) == [
              "s_iter2.perfetto-trace", "s_iter010.perfetto-trace",
              "a/s_iter001.perfetto-trace", "b/s_iter001.perfetto-trace"],
          sorted(names, key=run_order))


def test_a_set_of_several_rounds_keeps_each_round_together() -> None:
    """Every round names its repeats `_iter000` on: sorted by name first, the
    rounds were interleaved, and the drift test read the repeat's number as
    time (#848)."""
    paths = [f"{r}/s_iter{i:03d}.perfetto-trace" for i in range(3) for r in ("round2", "round1")]
    check("round1's three, then round2's", sorted(paths, key=run_order) == [
        f"{r}/s_iter{i:03d}.perfetto-trace" for r in ("round1", "round2") for i in range(3)],
          sorted(paths, key=run_order))


def test_a_set_is_read_in_the_order_it_was_recorded(tmp_path: Path) -> None:
    """The trace's own clock says when it was recorded: a round in a directory
    named after the later one goes first when it was recorded first."""
    env = dict(os.environ, ECHOLOT_NO_RECORD="1")
    start = 1_786_945_409_000_000_000
    made = []
    for n, (where, i) in enumerate([("b", 0), ("b", 1), ("b", 2), ("a", 0), ("a", 1), ("a", 2)]):
        trace = tmp_path / where / f"s_iter{i:03d}.perfetto-trace"
        trace.parent.mkdir(exist_ok=True)
        trace.write_bytes(fixture.build(realtime_ns=start + n * 60_000_000_000))
        made.append(f"{where}/{trace.name}")
    (tmp_path / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "analyze", *sorted(made), "-c", "echolot.yml",
         "-o", "out"], capture_output=True, text=True, cwd=tmp_path, env=env)
    check("analyze runs", done.returncode == 0, done.stderr[-600:])
    rep = json.loads((tmp_path / "out/report.json").read_text())
    check("b's round first, as recorded, each round in its own order",
          [str(Path(t).relative_to(tmp_path)) if Path(t).is_absolute() else t
           for t in rep["traces"]] == made, rep["traces"])
    check("and when each began, beside them", rep["recorded_at"] == sorted(rep["recorded_at"])
          and rep["recorded_at"][0] == "2026-08-17T05:43:39.000Z", rep.get("recorded_at"))


def test_the_same_recordings_in_another_order_give_the_same_report(tmp_path: Path) -> None:
    env = dict(os.environ, ECHOLOT_NO_RECORD="1")
    traces = []
    for i, build in enumerate(({}, {"composables": False}, {"crowded": True})):
        trace = tmp_path / f"cold_iter{i:03d}.perfetto-trace"
        trace.write_bytes(fixture.build(**build))
        traces.append(trace.name)
    (tmp_path / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")

    def analyzed(out: str, order: list[str]) -> tuple[dict, str]:
        done = subprocess.run(
            [sys.executable, "-m", "echolot.main", "analyze", *order,
             "-c", "echolot.yml", "-o", out],
            capture_output=True, text=True, cwd=tmp_path, env=env)
        check(f"analyze {order} runs", done.returncode == 0, done.stderr[-600:])
        rep = json.loads((tmp_path / out / "report.json").read_text())
        rep.pop("generated_at", None)
        md = (tmp_path / out / "report.md").read_text()
        return rep, md

    a, md_a = analyzed("a", traces)
    b, md_b = analyzed("b", list(reversed(traces)))
    check("the header names the first recording in name order",
          Path(a["trace"]).name == "cold_iter000.perfetto-trace"
          and [Path(t).name for t in a["traces"]] == traces, a["traces"])
    check("the two report.json are the same", a == b,
          [k for k in a if a.get(k) != b.get(k)])
    check("and so are the two report.md", md_a == md_b)
    check("the pid shown says whose it is",
          "the first of 3)" in md_a and "`cold_iter000`" in md_a, md_a[:800])
