-- @id: main_thread_outlier
-- @title: Single occurrences far longer than the same work usually takes
-- @why: the pair to main_thread_block. That one asks where the main thread's
--       time went in total; this one asks which single occurrence was out of
--       line with its own history. A heavy tail hides from every sum.
-- @param: factor = 4
-- @param: min_abs_ms = 40
-- @param: min_occurrences = 5
-- @calibrate: min_abs_ms = top10(max_ms) * 1.5
--
-- Found on a live hunt and not by design. ManageTeamBenchmark on an SM-A515F,
-- ten iterations over a 48-second window, reported P50 14.7 ms and P99 86 ms.
-- All six detectors of the time stayed silent, and none of them was broken:
-- every one aggregates by name and gates on an accumulated sum, and one 86 ms
-- frame among thousands dissolves into any sum you care to take. The group
-- `Choreographer#doFrame` collects its three seconds and fires — on the sum,
-- with the spike visible only in max_ms, if anyone thinks to look.
--
-- So the gate here is a single occurrence against the median for that same
-- name, rather than a total against a threshold. Two floors under it, because
-- a ratio on its own is worthless at small sizes:
--
--   min_abs_ms       "four times longer than 0.5 ms" is not a finding;
--   min_occurrences  a name seen twice has no typical duration to be an
--                    outlier from, and calling the longer of two an outlier
--                    is arithmetic rather than evidence.
--
-- The median is computed with window functions — one sort, no correlated
-- subquery per group. The working notes assumed SQLite had no median and
-- weighed three workarounds; the bundled SQLite is 3.50 and has had the
-- window functions since 3.25.
--
-- Main thread only, matching main_thread_block, so the two read as a pair over
-- one scope. An outlier on a background thread is a different question, and
-- uninstrumented_cpu and runnable_starvation already answer it structurally.

WITH ranked AS (
    SELECT
        name,
        dur,
        ROW_NUMBER() OVER (PARTITION BY name ORDER BY dur) AS rk,
        COUNT(*)     OVER (PARTITION BY name)              AS n
    FROM _slice_win
    WHERE is_main_thread = 1
      AND dur > 0
),
baseline AS (
    -- The lower of the two middles on an even count, rather than their mean:
    -- the base is meant to be a typical occurrence, and a real observation is
    -- a better one than a number that never happened. Integer division does
    -- it — n = 5 gives rank 3, n = 6 gives rank 3, n = 8 gives rank 4.
    SELECT name, n, dur AS median_ns
    FROM ranked
    WHERE rk = (n + 1) / 2
)
SELECT
    r.name                                              AS location,
    COUNT(*)                                            AS count,
    ROUND(SUM(r.dur) / 1e6, 2)                          AS total_ms,
    ROUND(MAX(r.dur) / 1e6, 2)                          AS max_ms,
    'median ' || ROUND(b.median_ns / 1e6, 1) || ' ms of ' || b.n
        || ' · worst ' || ROUND(MAX(r.dur) * 1.0 / b.median_ns, 1) || '×'
                                                        AS detail
FROM ranked r
JOIN baseline b ON b.name = r.name
WHERE b.n >= {{min_occurrences}}
  AND r.dur >= b.median_ns * {{factor}}
  AND r.dur >= {{min_abs_ms}} * 1000000
GROUP BY r.name
ORDER BY max_ms DESC
LIMIT 20;
