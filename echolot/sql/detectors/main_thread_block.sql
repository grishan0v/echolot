-- @id: main_thread_block
-- @title: Where the main thread spent its time
-- @why: measured as SELF time, children subtracted — otherwise one event
--       lands in the report several times at different depths and the agent
--       counts it twice
-- @param: min_slice_ms = 16 ms — the self time a slice name has to add up
--       to on the main thread in the window to be a row. One frame at 60 Hz,
--       not calibrated
-- @bar: min_slice_ms
-- @identity: location, detail
-- @column: location — the slice name
-- @column: count — its slices on the main thread in the window
-- @column: total_ms — their time with their children, summed
-- @column: self_ms — their time less their children's, summed: what the
--       rows add up by, and what the bar is on
-- @column: max_ms — the longest slice, children included
-- @column: detail — the main thread's name, cut to 15 characters by the
--       kernel
-- @plain_title: Heavy work on the main thread
-- @plain_why: The main thread draws the screen and answers taps, so while it
--       is busy with one piece of work it can do neither. Work that does not
--       have to run there can run on another thread.
-- @plain_clean: No single piece of work held the main thread for long.
--
-- On a live cold start, sorting by total duration produced:
--   d0  355.4 ms  Choreographer#doFrame 55112
--   d1  354.6 ms  Choreographer#doFrame - resynced to 55120 in 21.1ms
--   d2  354.4 ms  traversal
-- Three rows, one event. Meanwhile the real time sink, draw with its own
-- 125 ms, sat fifth, and TextLayout:initLayout at depth 6 never made it into
-- the report at all.
--
-- ## The threshold is a frame, and is not calibrated
--
-- 16 ms is one frame at 60 Hz: work that holds the main thread longer than
-- that costs a frame wherever it runs. There is deliberately no `@calibrate`
-- line. This detector says where the main thread's time went, and a bar from
-- the tail of healthy runs moves with how busy the scenario is: on 15 healthy
-- cold starts it came out at 119.7 ms, and a 100 ms block stopped being a row
-- (#509). A slice that is long for this app is `main_thread_outlier`'s to
-- find, against its own median. `@bar` still opens it when repeats are
-- merged, so a block just under 16 ms in one repeat is not read as absent
-- there (#505).
--
-- Self times add up without overlap: their sum across a thread cannot exceed
-- the thread's own time. Total duration stays alongside — the "self / total"
-- pair shows at a glance whether a slice works or waits on its children.

-- The children come from `_child_sum` (window.sql), which reads an open child
-- the way `_slice_win` reads an open slice.
WITH self AS (
    SELECT s.name, s.thread_name, MAX(s.dur, 0) AS dur,
           MAX(s.dur, 0) - COALESCE(CASE WHEN s.unfinished = 1 THEN c.ns_win
                                         ELSE c.ns END, 0) AS self_ns
    FROM _slice_win s
    LEFT JOIN _child_sum c ON c.parent_id = s.slice_id
    WHERE s.is_main_thread = 1
)
SELECT
    name                                                     AS location,
    COUNT(*)                                                 AS count,
    ROUND(SUM(dur) / 1e6, 2)                                 AS total_ms,
    ROUND(SUM(self_ns) / 1e6, 2)                             AS self_ms,
    ROUND(MAX(dur) / 1e6, 2)                                 AS max_ms,
    thread_name                                              AS detail
FROM self
GROUP BY name, thread_name
HAVING SUM(self_ns) >= {{min_slice_ms}} * 1000000
ORDER BY self_ms DESC
LIMIT 20;

-- @intervals
--
-- A row is self time, so that is what it stands for: the stretches of each
-- slice it names during which none of the slice's children was open. The
-- gaps before, between and after the children, found by ordering them. A
-- child that never closed runs to the end of its parent, which never closed
-- either, and a child is cut at its parent's end, as the sum above cuts it.
--
-- Except a slice that spans the whole window. That is the scenario's anchor,
-- most often — `AppStart` around a cold start, the shape `echolot mark`
-- proposes — and its self time is the part of the window no child accounts
-- for: counted in, it would call the unexplained rest explained. The same
-- reason `anr_risk` does not take depth zero as evidence.

WITH named AS MATERIALIZED (
    SELECT s.slice_id, s.ts, s.ts + s.dur AS te
    FROM _slice_win s
    JOIN _rows r ON r.location = s.name AND r.detail = s.thread_name
    CROSS JOIN _window w
    WHERE s.is_main_thread = 1 AND s.dur > 0
      AND NOT (s.ts <= w.ts_start AND s.ts + s.dur >= w.ts_end)
),
kids AS MATERIALIZED (
    SELECT c.parent_id AS slice_id,
           c.ts AS cs,
           MIN(CASE WHEN c.dur < 0 THEN n.te ELSE c.ts + c.dur END, n.te) AS ce,
           LAG(MIN(CASE WHEN c.dur < 0 THEN n.te ELSE c.ts + c.dur END, n.te))
               OVER (PARTITION BY c.parent_id ORDER BY c.ts) AS prev_end
    FROM slice c
    JOIN named n ON n.slice_id = c.parent_id
    WHERE c.ts < n.te
)
SELECT COALESCE(k.prev_end, n.ts) AS ts,
       k.cs - COALESCE(k.prev_end, n.ts) AS dur
FROM kids k
JOIN named n ON n.slice_id = k.slice_id
UNION ALL
SELECT COALESCE(MAX(k.ce), n.ts) AS ts,
       n.te - COALESCE(MAX(k.ce), n.ts) AS dur
FROM named n
LEFT JOIN kids k ON k.slice_id = n.slice_id
GROUP BY n.slice_id;
