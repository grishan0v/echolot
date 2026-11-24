-- @id: main_thread_outlier
-- @title: Single occurrences far longer than the same work usually takes
-- @why: the pair to main_thread_block. That one asks where the main thread's
--       time went in total; this one asks which single occurrence was out of
--       line with its own history. A heavy tail hides from every sum.
-- @param: factor = 4 × — how many times its name's median an occurrence has
--       to take to be out of line
-- @param: min_abs_ms = 40 ms — and how long it has to take at least, so
--       that four times half a millisecond is not a finding
-- @param: min_occurrences = 5 slices — how many times a name has to occur
--       on the main thread in the window to have a median to measure against
-- @calibrate: min_abs_ms = top10(max_ms) * 1.5
-- @column: location — the slice name on the main thread, the numbers the
--       runtime chose folded: every frame is `Choreographer#doFrame #`
-- @column: count — its occurrences out of line, not all of its occurrences
-- @column: total_ms — their time, summed
-- @column: max_ms — the worst of them
-- @column: detail — the median it is measured against, over how many
--       occurrences, and how many times the median the worst one took
-- @plain_title: Single moments that took far too long
-- @plain_why: A step that usually takes a moment but once takes many times
--       longer is a hitch the user can see, even when the overall numbers look
--       fine.
-- @plain_clean: No step that repeats on the main thread took far longer than
--       it usually does.
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
-- name, rather than a total against a threshold. The name with the numbers
-- the runtime chose folded (`_name_family`, built in main.py): every frame is
-- `Choreographer#doFrame <its vsync id>`, so by the raw name each frame was
-- a group of one and no frame was ever an outlier (#728). Its row is named
-- `Choreographer#doFrame #`. Two floors under it, because
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

-- The occurrences this detector counts, past `factor` times the median for
-- their name and past the floor, are written down once as its own table:
-- both the rows below and the time they stand for (`@intervals`) are read
-- from it (#490).
CREATE OR REPLACE PERFETTO TABLE _det_main_thread_outlier AS
WITH ranked AS (
    SELECT
        name,
        ts,
        dur,
        ROW_NUMBER() OVER (PARTITION BY name ORDER BY dur) AS rk,
        COUNT(*)     OVER (PARTITION BY name)              AS n
    FROM (SELECT COALESCE(f.family, s.name) AS name, s.ts, s.dur
          FROM _slice_win s
          LEFT JOIN _name_family f ON f.name = s.name
          WHERE s.is_main_thread = 1
            AND s.dur > 0)
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
SELECT r.name, r.ts, r.dur, b.n, b.median_ns
FROM ranked r
JOIN baseline b ON b.name = r.name
WHERE b.n >= {{min_occurrences}}
  AND r.dur >= b.median_ns * {{factor}}
  AND r.dur >= {{min_abs_ms}} * 1000000;

SELECT
    name                                                AS location,
    COUNT(*)                                            AS count,
    ROUND(SUM(dur) / 1e6, 2)                            AS total_ms,
    ROUND(MAX(dur) / 1e6, 2)                            AS max_ms,
    'median ' || ROUND(median_ns / 1e6, 1) || ' ms of ' || n
        || ' · worst ' || ROUND(MAX(dur) * 1.0 / median_ns, 1) || '×'
                                                        AS detail
FROM _det_main_thread_outlier
GROUP BY name
ORDER BY max_ms DESC
LIMIT 20;

-- @intervals
--
-- The occurrences a row counted, whole, as the first query wrote them down:
-- the ones past `factor` times the median for their name, and past the
-- floor.

SELECT ts, dur
FROM _det_main_thread_outlier
WHERE name IN (SELECT location FROM _rows);
