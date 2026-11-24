"""A window shorter than the sampling period still has the device's state (#617).

Temperature and free memory are sampled about once a second. A hot start or a
tap is shorter than that, and read only inside the window it held a sample
by chance: of six such windows, four said the state was "not recorded",
though the trace held it. Here no sample falls inside the fixture's window
[100, 1105] ms: one is 50 ms before it opens and one after it closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, report, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402


def _report(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(fixture, "THERMAL", [(50, "cpu-therm", 45_000),
                                             (1200, "cpu-therm", 80_000)])
    monkeypatch.setattr(fixture, "SYS_STATS", [(50, 2_000_000, 1_000),
                                               (1200, 100_000, 9_999)])
    path = tmp_path / "short.perfetto-trace"
    path.write_bytes(fixture.build())
    return analyze_trace(path, Config(selftest.FIXTURE_CONFIG))


def test_the_last_sample_before_the_window_counts_for_it(tmp_path: Path, monkeypatch) -> None:
    env = _report(tmp_path, monkeypatch)["environment"]
    check("nothing is called missing", env["missing"] == [], env["missing"])
    check("the temperature that held over the window, not the one after it",
          env["thermal"]["max_celsius"] == 45.0, env["thermal"])
    check("and how old the reading was",
          env["thermal"]["sampled_before_ms"] == 50.0, env["thermal"])
    check("free memory the same way",
          env["memory"]["available_mb_min"] == 2048.0
          and env["memory"]["sampled_before_ms"] == 50.0, env["memory"])


def test_the_header_does_not_say_not_recorded(tmp_path: Path, monkeypatch) -> None:
    text = report.to_markdown(_report(tmp_path, monkeypatch))
    check("no line sends the reader to record again",
          "Device state not recorded" not in text, text[:1500])


def test_a_sample_inside_the_window_has_no_age(tmp_path: Path) -> None:
    path = tmp_path / "f.perfetto-trace"
    path.write_bytes(fixture.build())
    env = analyze_trace(path, Config(selftest.FIXTURE_CONFIG))["environment"]
    check("the fixture's own samples fall inside, so no age is given",
          "sampled_before_ms" not in env["thermal"]
          and "sampled_before_ms" not in env["memory"], env)


# The views as #617 wrote them, kept to check the rewrite against: the same
# rows, asked once per track instead of once per counter sample (#706).
_BY_SUBQUERY = """
SELECT {key} AS key, c.value AS value, c.ts AS ts
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.type = '{kind}'
  AND c.ts <= {end}
  AND c.ts >= COALESCE((SELECT MAX(b.ts) FROM counter b
                        WHERE b.track_id = c.track_id AND b.ts <= {start}),
                       {start})
"""


def _rows(tp, sql: str) -> list[tuple]:
    return sorted((r["key"], r["value"], r["ts"]) for r in tp.query(sql))


def test_the_views_keep_their_rows_without_a_query_per_sample(
        tmp_path: Path, monkeypatch) -> None:
    from echolot.main import SQL_DIR, _resolve_process, _setup_context
    from echolot.tp import TraceSession

    # Samples before, at, inside and after the window [100, 1105], on two
    # zones, one of them with no sample before it opens, and a cpu clock of
    # thousands of samples beside them: none of those may reach either view.
    monkeypatch.setattr(fixture, "THERMAL", [
        (10, "cpu-therm", 40_000), (50, "cpu-therm", 45_000),
        (100, "cpu-therm", 46_000), (700, "cpu-therm", 61_500),
        (1200, "cpu-therm", 80_000), (300, "gpu-therm", 50_000)])
    monkeypatch.setattr(fixture, "SYS_STATS", [
        (20, 2_100_000, 900), (50, 2_000_000, 1_000),
        (900, 1_500_000, 1_250), (1200, 100_000, 9_999)])
    monkeypatch.setattr(fixture, "CPU_FREQ", fixture.CPU_FREQ + [
        (1, i * 0.25, 1_000_000 + i) for i in range(5_000)])
    path = tmp_path / "many.perfetto-trace"
    path.write_bytes(fixture.build())
    cfg = Config(selftest.FIXTURE_CONFIG)
    with TraceSession(path) as tp:
        bounds = _setup_context(tp, cfg, _resolve_process(tp, cfg.process)[0]["upid"])
        start, end = bounds["ts_start"], bounds["ts_end"]
        thermal = _rows(tp, "SELECT zone AS key, celsius * 1000 AS value, ts "
                            "FROM _thermal_win")
        memory = _rows(tp, "SELECT key, value, ts FROM _meminfo_win")
        check("the temperature rows are the ones the subquery kept",
              thermal == _rows(tp, _BY_SUBQUERY.format(
                  key="REPLACE(t.name, ' Temperature', '')", kind="thermal_temperature",
                  start=start, end=end)),
              thermal)
        check("and the memory rows",
              memory == _rows(tp, _BY_SUBQUERY.format(
                  key="t.name", kind="meminfo", start=start, end=end)),
              memory)
    check("the sample at the window's start counts for it, the older ones do not",
          [t for k, _, t in thermal if k == "cpu-therm"]
          == [fixture.ms(100), fixture.ms(700)], thermal)
    check("a zone first sampled inside the window keeps that sample",
          [t for k, _, t in thermal if k == "gpu-therm"] == [fixture.ms(300)], thermal)

    # The subquery per counter sample ran over every counter of the trace,
    # temperature or not: 80 s for this one view on a Macrobenchmark trace of
    # 180,000 counters with no temperature track (#706).
    text = (SQL_DIR / "environment.sql").read_text(encoding="utf-8")
    for view in ("_thermal_win", "_meminfo_win"):
        body = text.split(f"CREATE VIEW {view} AS", 1)[1].split(";", 1)[0]
        check(f"{view} asks no question per counter sample",
              "FROM counter b" not in body and "SELECT MAX(b.ts)" not in body, body)
