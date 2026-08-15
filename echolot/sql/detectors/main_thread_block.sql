-- @id: main_thread_block
-- @title: Where the main thread spent its time
-- @why: measured as SELF time, children subtracted — otherwise one event
--       lands in the report several times at different depths and the agent
--       counts it twice
-- @param: min_slice_ms = 16
-- @calibrate: min_slice_ms = top10(self_ms) * 1.5
--
-- On a live cold start, sorting by total duration produced:
--   d0  355.4 ms  Choreographer#doFrame 55112
--   d1  354.6 ms  Choreographer#doFrame - resynced to 55120 in 21.1ms
--   d2  354.4 ms  traversal
-- Three rows, one event. Meanwhile the real time sink, draw with its own
-- 125 ms, sat fifth, and TextLayout:initLayout at depth 6 never made it into
-- the report at all.
--
-- Self times add up without overlap: their sum across a thread cannot exceed
-- the thread's own time. Total duration stays alongside — the "self / total"
-- pair shows at a glance whether a slice works or waits on its children.

WITH child_sum AS (
    -- One pass over the whole trace: the sum of children per parent.
    -- A correlated subquery per row would cost an order of magnitude more.
    SELECT parent_id, SUM(MAX(dur, 0)) AS ns
    FROM slice
    WHERE parent_id IS NOT NULL
    GROUP BY parent_id
)
SELECT
    s.name                                                   AS location,
    COUNT(*)                                                 AS count,
    ROUND(SUM(MAX(s.dur, 0)) / 1e6, 2)                       AS total_ms,
    ROUND(SUM(MAX(s.dur, 0) - COALESCE(c.ns, 0)) / 1e6, 2)   AS self_ms,
    ROUND(MAX(MAX(s.dur, 0)) / 1e6, 2)                       AS max_ms,
    s.thread_name                                            AS detail
FROM _slice_win s
LEFT JOIN child_sum c ON c.parent_id = s.slice_id
WHERE s.is_main_thread = 1
GROUP BY s.name, s.thread_name
HAVING SUM(MAX(s.dur, 0) - COALESCE(c.ns, 0)) >= {{min_slice_ms}} * 1000000
ORDER BY self_ms DESC
LIMIT 20;
