-- @id: uninstrumented_cpu
-- @title: Blind spots: threads burning CPU with no instrumentation
-- @why: the ONLY detector that finds a problem inside uninstrumented code.
--       The agent does not guess — it is handed the fact "thread T ran for
--       340 ms, zero slices". That is exactly where adding trace{} pays off.
-- @param: min_running_ms = 50
-- @param: max_covered_pct = 50
-- @calibrate: min_running_ms = top10(total_ms) * 1.5
--
-- Coverage is the INTERSECTION of on-CPU time with slices, not slice duration.
-- Those are different quantities: a thread can sleep inside a slice, and then
-- wall-clock "coverage" easily passes 100% (a live cold start showed 217%).
-- Inflated coverage hides blind spots, and a false negative is the most
-- expensive answer this detector can give.
--
-- Only top-level slices count (depth = 0): nested ones sit inside their
-- parent, so summing them counts the same time twice. Depth = 0 slices do not
-- overlap each other, so there is no double counting among them.

WITH running AS (
    SELECT utid, thread_name, SUM(dur) AS running_ns
    FROM _tstate_win
    WHERE state = 'Running'
    GROUP BY utid, thread_name
),
covered AS (
    -- _cpu_in_slice is prepared by the context: a SPAN_JOIN of on-CPU time
    -- with top-level slices. Doing that interval-overlap join by hand makes
    -- SQLite run a nested loop that does not finish within ten minutes on a
    -- live trace.
    SELECT utid, SUM(dur) AS sliced_ns
    FROM _cpu_in_slice
    GROUP BY utid
),
counted AS (
    SELECT utid, COUNT(*) AS slice_count FROM _slice_win GROUP BY utid
)
SELECT
    r.thread_name                                          AS location,
    COALESCE(n.slice_count, 0)                             AS count,
    ROUND(r.running_ns / 1e6, 2)                           AS total_ms,
    NULL                                                   AS max_ms,
    ROUND(COALESCE(c.sliced_ns, 0) / 1e6, 2)               AS covered_ms,
    ROUND(
        100.0 * (r.running_ns - COALESCE(c.sliced_ns, 0)) / r.running_ns
    ) || '% of CPU outside slices'                         AS detail
FROM running r
LEFT JOIN covered c ON r.utid = c.utid
LEFT JOIN counted n ON r.utid = n.utid
WHERE r.running_ns >= {{min_running_ms}} * 1000000
  AND COALESCE(c.sliced_ns, 0) < r.running_ns * {{max_covered_pct}} / 100.0
ORDER BY r.running_ns DESC
LIMIT 20;
