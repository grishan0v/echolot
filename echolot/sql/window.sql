-- Phase 2: everything narrowed to the window. ts_start/ts_end arrive as plain
-- numbers — the CLI read them from _window after context.sql. That matters: a
-- view in their place would be recomputed on every reference to _slice_win.

-- The window is constants now. The view stays so a detector can refer to the
-- scenario duration without paying anything for it.
DROP VIEW IF EXISTS _window;
CREATE VIEW _window AS
SELECT {{ts_start}} AS ts_start, {{ts_end}} AS ts_end;

-- Slices that OVERLAP the window, not just those starting inside it.
--   dur      — the real slice duration, and that is what humans are shown:
--              a 200 ms block stays a 200 ms block even if the window cut it
--              in half;
--   dur_win  — only the part inside the window. For coverage and share math.
DROP VIEW IF EXISTS _slice_win;
CREATE VIEW _slice_win AS
SELECT
    s.*,
    MAX(s.ts, {{ts_start}})                                          AS ts_win,
    MIN(s.ts + MAX(s.dur, 0), {{ts_end}}) - MAX(s.ts, {{ts_start}})  AS dur_win
FROM _slice s
WHERE s.ts < {{ts_end}}
  AND s.ts + MAX(s.dur, 0) > {{ts_start}};

-- Thread states, CLIPPED to the window.
-- Here dur is already the clipped value: the question is always "how long did
-- the thread spend in this state inside the scenario", never "how long was the
-- interval".
DROP VIEW IF EXISTS _tstate_win;
CREATE VIEW _tstate_win AS
SELECT
    ts.utid,
    th.tid,
    th.name AS thread_name,
    ts.state,
    MAX(ts.ts, {{ts_start}})                                        AS ts,
    MIN(ts.ts + ts.dur, {{ts_end}}) - MAX(ts.ts, {{ts_start}})      AS dur
FROM thread_state ts
JOIN thread th ON ts.utid = th.utid
JOIN _proc p   ON th.upid = p.upid
WHERE ts.dur > 0
  AND ts.ts < {{ts_end}}
  AND ts.ts + ts.dur > {{ts_start}};

-- --- intersecting "thread on CPU" with "top-level slices" ------------------
--
-- Needed to answer honestly: what share of ON-CPU time ran inside instrumented
-- code. A naive interval-overlap JOIN is executed by SQLite as a nested loop:
-- 20k Running intervals against 50k slices is a billion comparisons, and the
-- run does not finish within ten minutes.
--
-- SPAN_JOIN is the trace_processor operator built for exactly this: merging
-- two sorted sets of non-overlapping intervals within a partition. On the same
-- trace it completes in 0.1 s.
--
-- Its requirements hold: within a single utid neither Running intervals nor
-- depth = 0 slices overlap, and both are sorted by ts.
--
-- Caveat: SPAN_JOIN is a Perfetto feature, not plain SQL. But it is core
-- trace_processor rather than a stdlib module, and our version is pinned.

DROP VIEW IF EXISTS _running_span;
CREATE VIEW _running_span AS
SELECT utid, ts, dur
FROM _tstate_win
WHERE state = 'Running';

DROP VIEW IF EXISTS _top_slice_span;
CREATE VIEW _top_slice_span AS
SELECT utid, ts_win AS ts, dur_win AS dur, 1 AS instrumented
FROM _slice_win
WHERE depth = 0 AND dur_win > 0;

DROP TABLE IF EXISTS _cpu_in_slice;
CREATE VIRTUAL TABLE _cpu_in_slice
USING SPAN_JOIN(_running_span PARTITIONED utid, _top_slice_span PARTITIONED utid);
