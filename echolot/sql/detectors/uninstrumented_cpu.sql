-- @id: uninstrumented_cpu
-- @title: Blind spots: threads burning CPU with no instrumentation
-- @why: names a thread that burns CPU with no slice around it.
--       The agent does not guess — it is handed the fact "thread T ran for
--       340 ms, zero slices". That is exactly where adding trace{} pays off.
-- @param: min_running_ms = 50 ms — a thread's time on a CPU outside every
--       top-level slice, which has to reach this to be a row
-- @param: max_covered_pct = 50 % — and, on any thread but the main one, the
--       share of its CPU time inside slices it may have at most. Not
--       calibrated: a share means the same on any device
-- @calibrate: min_running_ms = top10(total_ms) * 1.5
-- @column: location — the thread's name; a pool's threads are one row, the
--       kernel having cut them to one name
-- @column: count — slices on threads of that name in the window, at any
--       depth: 0 is a thread nobody instrumented
-- @column: total_ms — bends the contract: its time on a CPU outside every
--       top-level slice, the blind spot itself
-- @column: max_ms — empty: a blind spot has no single occurrence
-- @column: covered_ms — its time on a CPU inside a top-level slice; with
--       total_ms, all of its time on a CPU
-- @column: detail — the share of its CPU time outside slices; in a sampled
--       recording, then what the samples there named, `ours:` the nearest
--       method of the app's own
-- @plain_title: Work nobody can name
-- @plain_why: Some threads keep the processor busy, but nothing in the
--       recording says what for. When the app is slow because of them, the
--       recording cannot tell why.
-- @plain_clean: No thread spent long on the processor doing work that nothing
--       in the recording names.
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
-- A row measures the blind spot itself, `total_ms` and `covered_ms` as their
-- `@column` lines say, and the bar is on the first. Ranked and
-- compared on the whole of a thread's CPU, a stretch nobody named weighs no
-- more than the work around it that somebody did: a 200 ms busy loop on a
-- main thread with 2.3 s on a CPU would move its row by less than
-- `compare`'s floor, and the plain verdict would call all 2.3 s work nothing
-- names.
--
-- The share is a bar for the other threads only. On them it keeps the slivers
-- between well-marked work out — a pool that names 90% of what it does is not
-- where the next marker goes. The main thread is the window's own time, and
-- its CPU outside every slice is where a regression in the app's own code
-- hides first. On a large app's cold start its main thread was 72–80%
-- covered in every run, so a 200 ms loop planted there with no section
-- around it, 476 ms outside slices growing to 678, gave no row on either
-- side: the better a thread is marked up, the longer a stretch the share let
-- it hide. Its row stands on the time alone.

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
    -- `main` marks the name the process's main thread goes by: the thread
    -- whose tid is the pid.
    SELECT w.thread_name, SUM(w.dur) AS running_ns,
           MAX(w.tid = p.pid)                              AS main
    FROM _tstate_win w
    CROSS JOIN _proc p
    WHERE w.state = 'Running'
    GROUP BY w.thread_name
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
),
blind AS (
    SELECT r.thread_name, r.running_ns, r.main,
           COALESCE(c.sliced_ns, 0)                        AS sliced_ns,
           r.running_ns - COALESCE(c.sliced_ns, 0)         AS outside_ns
    FROM running r
    LEFT JOIN covered c ON c.thread_name IS r.thread_name
)
SELECT
    b.thread_name                                          AS location,
    COALESCE(n.slice_count, 0)                             AS count,
    ROUND(b.outside_ns / 1e6, 2)                           AS total_ms,
    NULL                                                   AS max_ms,
    ROUND(b.sliced_ns / 1e6, 2)                            AS covered_ms,
    ROUND(100.0 * b.outside_ns / b.running_ns)
        || '% of CPU outside slices'                       AS detail
FROM blind b
LEFT JOIN counted n ON n.thread_name IS b.thread_name
WHERE b.outside_ns >= {{min_running_ms}} * 1000000
  AND (b.main OR b.sliced_ns < b.running_ns * {{max_covered_pct}} / 100.0)
ORDER BY b.outside_ns DESC
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
