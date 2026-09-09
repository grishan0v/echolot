#!/usr/bin/env python3
"""The claims `docs/detectors.md` makes about trace_processor, checked against it.

Prose about a dependency goes stale the same way a tally does, and worse: a
number that drifts looks wrong to a careful reader, while a behaviour that
changed leaves the sentence reading perfectly and meaning the opposite.

The one that matters here is `SPAN_JOIN` on overlapping input. The working
note said it fails. It does not — it double-counts, silently — and every
detector that sums durations inherits that. If a future trace_processor starts
raising instead, the guidance stops being true and this says so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture  # noqa: E402
from echolot.tp import TraceSession  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# Function-scoped on purpose, at the cost of a trace_processor start per test.
# A session held across the module leaves its thread alive, and the next file
# along calls `forkpty()` — which warns, correctly, that doing so from a
# multi-threaded process can deadlock the child. Paying a second here keeps
# that hazard out of a suite that had none.
@pytest.fixture
def tp(tmp_path):
    trace = tmp_path / "t.perfetto-trace"
    trace.write_bytes(fixture.build())
    with TraceSession(str(trace)) as session:
        yield session


def test_span_join_double_counts_overlapping_input(tp):
    """The documented worked example, run.

    Two intervals of one partition that overlap by 50, joined against one that
    spans both. A detector summing the result gets 200 where the union is 150.
    """
    tp.exec_script(
        "DROP VIEW IF EXISTS _overlap_a; CREATE VIEW _overlap_a AS "
        "SELECT 1 AS utid, 0 AS ts, 100 AS dur, 'first' AS tag "
        "UNION ALL SELECT 1, 50, 100, 'second';")
    tp.exec_script(
        "DROP VIEW IF EXISTS _overlap_b; CREATE VIEW _overlap_b AS "
        "SELECT 1 AS utid, 0 AS ts, 200 AS dur;")
    tp.exec_script(
        "DROP TABLE IF EXISTS _overlap_join; CREATE VIRTUAL TABLE _overlap_join "
        "USING SPAN_JOIN(_overlap_a PARTITIONED utid, _overlap_b PARTITIONED utid);")

    rows = tp.query("SELECT ts, dur, tag FROM _overlap_join ORDER BY ts")
    assert [(r["ts"], r["dur"]) for r in rows] == [(0, 100), (50, 100)], rows
    assert sum(r["dur"] for r in rows) == 200, (
        "the documented example says overlapping input sums to 200 against a "
        "true union of 150; if this now raises or de-duplicates, the guidance "
        "in docs/detectors.md is no longer true")


def test_a_view_cannot_be_created_twice_but_a_perfetto_table_can(tp):
    """Why the context files open with DROP, and what replaces it."""
    tp.exec_script("DROP VIEW IF EXISTS _twice; CREATE VIEW _twice AS SELECT 1 AS x;")
    # The message is trace_processor's and not worth pinning; that it refuses
    # at all is the whole reason every context view opens with a DROP.
    try:
        tp.exec_script("CREATE VIEW _twice AS SELECT 1 AS x;")
    except Exception:
        pass
    else:
        raise AssertionError(
            "a second CREATE VIEW succeeded, so the DROP in front of every "
            "view in the context files is guarding against nothing")

    for _ in range(2):
        tp.exec_script(
            "CREATE OR REPLACE PERFETTO TABLE _twice_pt AS SELECT 1 AS x;")


def test_limit_zero_hands_back_no_column_names(tp):
    """Why the guidance says LIMIT 1 rather than the tidier-looking LIMIT 0.

    Columns arrive as the keys of the rows, so a query with no rows has no
    columns to read — the trick that works in the trace_processor shell does
    not survive the trip through here.
    """
    assert tp.query("SELECT * FROM thread_state LIMIT 0") == []
    assert "blocked_function" in tp.query("SELECT * FROM thread_state LIMIT 1")[0]


def test_the_guidance_is_where_the_test_says_it_is():
    """A section renamed away leaves these tests guarding nothing."""
    text = (ROOT / "docs/detectors.md").read_text(encoding="utf-8")
    for claim in ("double-counts", "PARTITIONED utid", "LIMIT 1",
                  "CREATE OR REPLACE PERFETTO"):
        assert claim in text, f"docs/detectors.md no longer states: {claim}"
