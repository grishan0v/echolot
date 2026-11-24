"""A detector's own tables: its pieces written down once, read twice (#490).

A first query that finds its pieces in steps writes them into
`_det_<id>`, and its `@intervals` reads that table rather than taking the
steps again. Two copies of one logic used to agree only as long as the tests
said so, and the work was done twice on every trace.

Held here: what the file is allowed to write and read, and that the time
behind a run's rows is read from that run's pieces — not from the ones the
merge's second run, with its thresholds open, leaves in the table after it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot.analyze import DETECTOR_DIR  # noqa: E402
from echolot.tp import Detector, load_detectors  # noqa: E402

HEAD = "-- @id: own\n-- @title: Own\n-- @param: min_ms = 30\n\n"
ROWS = "SELECT name AS location, COUNT(*) AS count FROM {t} GROUP BY name LIMIT 20;\n"


def _read(tmp_path: Path, body: str) -> Detector:
    path = tmp_path / "own.sql"
    path.write_text(HEAD + body, encoding="utf-8")
    return Detector.from_file(path)


# --- what the file may write and read --------------------------------------

def test_a_table_of_its_own_is_kept(tmp_path: Path) -> None:
    d = _read(tmp_path, "CREATE OR REPLACE PERFETTO TABLE _det_own_part AS\n"
                        "SELECT name, ts, dur FROM _slice_win;\n"
                        "CREATE OR REPLACE PERFETTO TABLE _det_own AS\n"
                        "SELECT * FROM _det_own_part;\n" + ROWS.format(t="_det_own")
              + "\n-- @intervals\nSELECT ts, dur FROM _det_own;\n")
    check("the tables, in the order it writes them",
          d.tables == ("_det_own_part", "_det_own"), d.tables)
    check("the first query keeps them, and still ends in its LIMIT",
          "_det_own_part" in d.sql and d.sql.rstrip().endswith("LIMIT 20;"), d.sql)


def test_a_detector_without_any_has_none(tmp_path: Path) -> None:
    d = _read(tmp_path, ROWS.format(t="_slice_win"))
    check("no tables", d.tables == (), d.tables)


@pytest.mark.parametrize("body, said", [
    # The window's own: every detector after it would read what this one left.
    ("CREATE OR REPLACE PERFETTO TABLE _slice_win AS SELECT 1 AS x;\n"
     + ROWS.format(t="_slice_win"), "named after it: `_det_own`"),
    # Another detector's name.
    ("CREATE OR REPLACE PERFETTO TABLE _det_other AS SELECT 'a' AS name;\n"
     + ROWS.format(t="_det_other"), "named after it"),
    # Written once only: the merge's second run in the same session fails.
    ("CREATE PERFETTO TABLE _det_own AS SELECT 'a' AS name;\n" + ROWS.format(t="_det_own"),
     "CREATE OR REPLACE"),
    # Read where nothing of its own wrote it.
    ("CREATE OR REPLACE PERFETTO TABLE _det_own AS SELECT 'a' AS name;\n"
     + ROWS.format(t="_det_own") + "\n-- @intervals\nSELECT ts, dur FROM _det_other;\n",
     "`_det_other`, which its first query does not write"),
    ("CREATE OR REPLACE PERFETTO TABLE _det_own AS SELECT 'a' AS name;\n"
     + ROWS.format(t="_det_own")
     + "\n-- @samples\nSELECT location, NULL AS callsite_id FROM _det_own_more;\n",
     "`_det_own_more`, which its first query does not write"),
])
def test_what_is_refused_as_the_file_is_read(tmp_path: Path, body: str, said: str) -> None:
    with pytest.raises(ValueError) as e:
        _read(tmp_path, body)
    check("refused, with the file's name and the reason",
          "own.sql" in str(e.value) and said in str(e.value), str(e.value))


def test_a_sentence_about_a_table_is_not_one(tmp_path: Path) -> None:
    d = _read(tmp_path, "-- unlike CREATE PERFETTO TABLE _slice_win, or _det_other\n"
                        "CREATE OR REPLACE PERFETTO TABLE _det_own AS\n"
                        "SELECT name FROM _slice_win;  -- not _det_another\n"
              + ROWS.format(t="_det_own"))
    check("comments are left out", d.tables == ("_det_own",), d.tables)


def test_two_detectors_cannot_share_a_table(tmp_path: Path) -> None:
    """`anr` may write `_det_anr_risk` by its own rule; not beside `anr_risk`."""
    table = "CREATE OR REPLACE PERFETTO TABLE _det_anr_risk AS SELECT 'a' AS name;\n"
    (tmp_path / "anr.sql").write_text("-- @id: anr\n-- @title: A\n\n" + table
                                      + ROWS.format(t="_det_anr_risk"), encoding="utf-8")
    check("alone, it is its own", load_detectors(tmp_path)[0].tables == ("_det_anr_risk",))
    (tmp_path / "anr_risk.sql").write_text("-- @id: anr_risk\n-- @title: B\n\n" + table
                                           + ROWS.format(t="_det_anr_risk"), encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_detectors(tmp_path)
    check("beside the detector it is named after, refused",
          "`_det_anr_risk` is written by `anr` too" in str(e.value), str(e.value))


def test_every_shipped_writer_reads_its_tables_for_the_time() -> None:
    writers = [d for d in load_detectors(DETECTOR_DIR) if d.tables]
    check("some shipped detectors write their pieces down", writers)
    for d in writers:
        check(f"{d.id}: its @intervals reads what its first query wrote",
              any(t in d.intervals_sql for t in d.tables), d.tables)
