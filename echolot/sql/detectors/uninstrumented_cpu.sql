-- @id: uninstrumented_cpu
-- @title: Blind spots: threads burning CPU with no instrumentation
-- @why: names a thread that burns CPU with no slice around it.
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

--
-- One row per thread name, not per thread. The kernel cuts a name to fifteen
-- characters, so a pool's threads arrive under one name — every
-- `DefaultDispatcher-worker-N` is `DefaultDispatch` — and a row per thread
-- gave a report three rows of one name. Merging repeats tells rows apart by
-- name, so it took a median over a mix of threads; `compare` paired them by
-- report order, and each got the samples of all of them. The pool is the
-- unit a reader can act on, and `@intervals` and `@samples` already pick
-- threads by name.

WITH running AS (
    SELECT thread_name, SUM(dur) AS running_ns
    FROM _tstate_win
    WHERE state = 'Running'
    GROUP BY thread_name
),
covered AS (
    -- _cpu_in_slice is prepared by the context: a SPAN_JOIN of on-CPU time
    -- with top-level slices. Doing that interval-overlap join by hand makes
    -- SQLite run a nested loop that does not finish within ten minutes on a
    -- live trace.
    SELECT t.name AS thread_name, SUM(c.dur) AS sliced_ns
    FROM _cpu_in_slice c
    JOIN thread t ON t.utid = c.utid
    GROUP BY t.name
),
counted AS (
    SELECT thread_name, COUNT(*) AS slice_count
    FROM _slice_win
    GROUP BY thread_name
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
LEFT JOIN covered c ON c.thread_name IS r.thread_name
LEFT JOIN counted n ON n.thread_name IS r.thread_name
WHERE r.running_ns >= {{min_running_ms}} * 1000000
  AND COALESCE(c.sliced_ns, 0) < r.running_ns * {{max_covered_pct}} / 100.0
ORDER BY r.running_ns DESC
LIMIT 20;

-- @intervals
--
-- The blind spot itself: the main thread on a CPU with no top-level slice
-- open, when the main thread is one of the rows. Most rows here are other
-- threads, and their time is none of the window's.

SELECT c.ts, c.dur
FROM _cpu_by_slice c
JOIN thread t ON t.utid = c.utid
CROSS JOIN _proc p
WHERE t.upid = p.upid
  AND t.tid = p.pid
  AND c.instrumented IS NULL
  AND t.name IN (SELECT location FROM _rows);

-- @samples
--
-- What ran in the blind spot: the row's thread's callstack samples in the
-- same stretches the query above calls uninstrumented, on a CPU with no
-- top-level slice open. A sample that came without a stack stays, with a NULL
-- callsite: sampled and not unwound is a different answer from not sampled.
-- `_samples_win` is already this process's alone.

SELECT t.name AS location, s.callsite_id
FROM _samples_by_slice s
JOIN thread t ON t.utid = s.utid
WHERE s.instrumented IS NULL
  AND t.name IN (SELECT location FROM _rows);
