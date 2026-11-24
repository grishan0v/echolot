#!/usr/bin/env python3
"""A detector measures every row, and its threshold only marks which rows to show (#832).

For a detector with `@fires`, the rows a report shows at thresholds T are
all the rows it measured, filtered by T: whatever T is, the measurements are
the same, and the predicate alone decides which of them are shown. Held per
detector on the fixture, at its shipped thresholds, at thresholds raised
the way `calibrate` raises them, and at zero.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest  # noqa: E402
from echolot.analyze import DETECTOR_DIR, analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.report import FLOOR_MS, UNDER_ROWS, metric_of  # noqa: E402
from echolot.tp import _PLACEHOLDER, load_detectors, sql_value  # noqa: E402
from tests.support import check  # noqa: E402

FIRES = [d for d in load_detectors(DETECTOR_DIR) if d.fires]


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fires") / "fixture.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def _thresholds(d) -> dict[str, dict]:
    """The predicate's parameters at three settings."""
    used = set(_PLACEHOLDER.findall(d.fires))
    return {
        "shipped": {},
        "raised": {p: d.params[p] * 3 for p in used},
        "zero": {p: 0 for p in used},
    }


def _measured(d, trace: Path, values: dict) -> tuple[list[dict], list[dict]]:
    """The rows shown, and the rows measured under the bar, of one run."""
    cfg = Config({**selftest.FIXTURE_CONFIG, "detectors": {d.id: values} if values else {}})
    entry = next(e for e in analyze_trace(trace, cfg)["detectors"] if e["id"] == d.id)
    check(f"{d.id} ran", entry["error"] is None, entry["error"])
    strip = ("stacks",)
    shown = [{k: v for k, v in r.items() if k not in strip} for r in entry["rows"]]
    return shown, list(entry.get(UNDER_ROWS) or [])


def _key(row: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in row.items()))


def _passes(d, rows: list[dict], values: dict) -> list[dict]:
    """`rows` filtered by the detector's predicate at `values`, in SQLite."""
    if not rows:
        return []
    resolved = {**d.params, **values}
    fires = _PLACEHOLDER.sub(lambda m: sql_value(resolved[m.group(1)]), d.fires)
    cols = sorted({k for r in rows for k in r if not isinstance(r[k], (dict, list))})
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE r (i, {', '.join(cols)})")
    for i, row in enumerate(rows):
        db.execute(f"INSERT INTO r VALUES ({', '.join('?' * (len(cols) + 1))})",
                   [i, *[row.get(c) for c in cols]])
    kept = {i for (i,) in db.execute(f"SELECT i FROM r WHERE {fires}")}
    return [r for i, r in enumerate(rows) if i in kept]


@pytest.mark.parametrize("d", FIRES, ids=[d.id for d in FIRES])
def test_the_rows_shown_are_every_row_filtered_by_the_thresholds(d, trace: Path) -> None:
    settings = _thresholds(d)
    runs = {name: _measured(d, trace, values) for name, values in settings.items()}
    every = {name: shown + under for name, (shown, under) in runs.items()}

    def over_floor(rows: list[dict]) -> set:
        return {_key(r) for r in rows if float(r.get(metric_of(r)) or 0) >= FLOOR_MS}

    base = over_floor(every["zero"])
    if not base:
        # `anr` and `anr_risk`: the fixture holds no ANR and no stall of
        # seconds, and their rows are held by tests of their own.
        pytest.skip(f"{d.id} measures nothing on the fixture")
    for name, rows in every.items():
        check(f"{d.id} at {name}: the same measurements, whatever the thresholds",
              over_floor(rows) == base, (name, rows, every["zero"]))
    for name, values in settings.items():
        shown = runs[name][0]
        expected = _passes(d, every["zero"], values)
        check(f"{d.id} at {name}: shown is every row the predicate passes",
              sorted(map(_key, shown)) == sorted(map(_key, expected)),
              (name, shown, expected))


def test_a_parameter_that_hides_rows_is_a_mask_a_shape_or_in_the_predicate() -> None:
    """A numeric parameter in the main query's WHERE or HAVING decides what
    is counted or what a row is, never whether it is shown: that is the
    predicate's alone. A string is a mask; a number must be a `@shape`."""
    for d in FIRES:
        body = "\n".join(ln for ln in d.sql.splitlines() if not ln.strip().startswith("--"))
        used = set(_PLACEHOLDER.findall(body))
        in_fires = set(_PLACEHOLDER.findall(d.fires))
        numeric = {p for p in used if isinstance(d.params.get(p), (int, float))}
        check(f"{d.id}: a threshold in the predicate is not in the query too",
              not (in_fires & used), sorted(in_fires & used))
        check(f"{d.id}: every number the query reads is a @shape",
              numeric <= set(d.shape), sorted(numeric - set(d.shape)))
        check(f"{d.id}: its query keeps every row it measured",
              not re.search(r"\bLIMIT\s+\d+\s*;?\s*$", body.strip(), re.I), d.id)
