#!/usr/bin/env python3
"""A name that differs only by the runtime's numbers is folded once (#831).

The detectors group by `_name_family`, built once per trace with
`names.runtime_name`, and the merge folds nothing. So one recording and a
set of copies of it say the same of every row, and the numbers somebody
wrote — a marker's, a line in a lock note — are kept everywhere.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_outlier_families import FRAMES, _trace  # noqa: E402

# What a set has and one recording does not: how many repeats found a row,
# and each repeat's number.
OF_A_SET = ("runs", "spread")


def _rows(rep: dict) -> dict[str, list[dict]]:
    return {d["id"]: [{k: v for k, v in r.items() if k not in OF_A_SET} for r in d["rows"]]
            for d in rep["detectors"]}


def _strings(rep: dict) -> list[str]:
    """Every name a report's rows carry: their columns and their variants."""
    out: list[str] = []
    for d in rep["detectors"]:
        for r in d["rows"] + list((d.get("below") or {}).get("rows") or []):
            out += [v for v in r.values() if isinstance(v, str)]
            for names in (r.get("variants") or {}).values():
                out += names
    return out


def test_one_recording_and_five_copies_of_it_have_the_same_rows(tmp_path: Path) -> None:
    trace = tmp_path / "fixture.perfetto-trace"
    trace.write_bytes(fixture.build())
    analysed = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    one = report_mod.aggregate([copy.deepcopy(analysed)])
    five = report_mod.aggregate([copy.deepcopy(analysed) for _ in range(5)])
    rows_one, rows_five = _rows(one), _rows(five)
    check("the same detectors", rows_one.keys() == rows_five.keys(),
          (sorted(rows_one), sorted(rows_five)))
    for det_id in rows_one:
        check(f"{det_id}: the same rows, but for `runs` and `spread`",
              rows_one[det_id] == rows_five[det_id], (rows_one[det_id], rows_five[det_id]))
    check("and there are rows to compare", sum(map(len, rows_one.values())) >= 10, rows_one)


def test_a_name_the_project_listed_keeps_its_digits_everywhere(tmp_path: Path) -> None:
    listed = "Choreographer#doFrame 55103"
    raw = {**selftest.FIXTURE_CONFIG, "domains": [{"slice": listed, "code": "Frames"}]}
    analysed = analyze_trace(_trace(tmp_path, FRAMES), Config(raw))
    merged = report_mod.aggregate([copy.deepcopy(analysed) for _ in range(2)])
    for name, rep in (("one recording", analysed), ("the merge", merged)):
        folded_into = [r for d in rep["detectors"] for r in d["rows"]
                       if listed in sum(((r.get("variants") or {}).values()), [])]
        check(f"{name}: no row folds it into a family", folded_into == [], folded_into)
    markers = [r["location"] for r in merged["markers"]["rows"]]
    check("the markers table has it as written", listed in markers, markers)


def test_a_line_number_in_a_lock_note_is_kept_by_every_detector(tmp_path: Path) -> None:
    trace = tmp_path / "fixture.perfetto-trace"
    trace.write_bytes(fixture.build())
    analysed = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))
    merged = report_mod.aggregate([copy.deepcopy(analysed) for _ in range(2)])
    for name, rep in (("one recording", analysed), ("the merge", merged)):
        said = [s for s in _strings(rep) if "Store.java:" in s]
        check(f"{name}: the lock note is somewhere in the rows", said != [], _strings(rep))
        check(f"{name}: and keeps its line", all("Store.java:41" in s for s in said), said)
