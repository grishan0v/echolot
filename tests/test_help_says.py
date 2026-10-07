#!/usr/bin/env python3
"""What the help and the notes under a table say, held to what the code does.

Each of these was a sentence that stayed while the code under it moved: a
list of three detectors that had become the wrong three, three lines where
Codex adds a fourth, two floors joined by "and" where the larger one wins,
one column named where two are cut (#264).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.main import _detector_masks, _names_text, build_parser  # noqa: E402
from tests.support import check  # noqa: E402


def _help(verb: str) -> str:
    sub = next(a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    return " ".join(sub.choices[verb].format_help().split())


def test_names_lists_the_detectors_that_have_a_mask_and_calls_the_rest_structural(capsys):
    _names_text(SimpleNamespace(top=5, grep=None), [], None, 0, [], [], False)
    said = " ".join(capsys.readouterr().out.split())
    masked = sorted({det for det, kind, _ in _detector_masks() if kind == "name"})
    check("there are detectors that search by name", masked, masked)
    check("each of them is named", all(f"`{d}`" in said for d in masked), said)
    check("and every other one is structural",
          "Every other detector is structural" in said, said)
    check("no structural detector is listed as the only structural ones",
          "`main_thread_block`, `runnable_starvation` and" not in said, said)


def test_doctor_quiet_says_four_lines_where_codex_is_used():
    text = _help("doctor")
    check("three lines, four where Codex is used",
          "three lines (four where Codex is used)" in text, text)


def test_compare_says_the_larger_floor_applies_and_a_small_move_is_steady():
    text = _help("compare")
    check("a move under the floor is steady", "is steady" in text, text)
    check("the larger of the two floors applies", "the larger floor applies" in text, text)
    check("no longer 'not a row'", "is not a row" not in text, text)


def test_report_wide_names_both_columns_it_keeps_whole():
    text = _help("report")
    check("--wide keeps the location and the evidence",
          "do not cut the location and evidence columns" in text, text)
