"""What `compare` and `report` print, in the six places it was off: the clock
warning for two blank sides, a move of exactly the floor, the floor's
percentage, "Silent in both", hex in a family name, and `--top 0`.
"""

from __future__ import annotations

import contextlib
import copy
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import recorder  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402


def _build(before: dict, after: dict, **kw) -> dict:
    return compare_mod.build(before, after, before_path="b.json", after_path="a.json", **kw)


def test_two_blank_sides_are_plural(demo_report) -> None:
    a, b = copy.deepcopy(demo_report), copy.deepcopy(demo_report)
    for rep in (a, b):
        rep["environment"] = {**(rep.get("environment") or {}), "cpu": None,
                              "thermal": {"max_c": 40.0, "zones": 1}}
    text = " ".join(w["text"] for w in _build(a, b)["warnings"])
    check("sides carry", "the before and after sides carry no CPU frequency" in text, text)


def test_a_move_of_exactly_the_floor_is_steady() -> None:
    before = {"detectors": [{"id": "d", "rows": [{"location": "x", "self_ms": 50.0}]}]}
    after = {"detectors": [{"id": "d", "rows": [{"location": "x", "self_ms": 55.0}]}]}
    rows = _build(before, after)["rows"]
    check("steady at +5 on 50", rows and rows[0]["change"] == "steady", rows)


def test_the_floor_s_percentage_is_rounded(demo_report) -> None:
    text = compare_mod.to_markdown(_build(demo_report, demo_report, floor_ratio=0.29))
    check("29%", "29%" in text and "28%" not in text, text[:1500])


def test_silent_in_both_names_the_quiet_detectors(demo_report) -> None:
    cmp = _build(demo_report, demo_report)
    quiet = [d["id"] for d in demo_report["detectors"] if not d.get("rows") and not d.get("error")]
    check("in the json", quiet and cmp["summary"]["silent_both"] == sorted(quiet),
          cmp["summary"]["silent_both"])
    check("and printed", "**Silent in both:**" in compare_mod.to_markdown(cmp))


def test_hex_folds_to_0x() -> None:
    got = report_mod.family("Lock contention on 0x7f3a2b (owner tid: 1234)")
    check("0x#", got == "Lock contention on 0x# (owner tid: #)", got)


def test_top_below_one_is_refused(tmp_path: Path) -> None:
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        try:
            main(["report", str(tmp_path / "r.json"), "--top", "0"])
            code = 0
        except SystemExit as e:
            code = e.code
    check("an argument error", code == 2 and "one or more" in err.getvalue(), err.getvalue())
