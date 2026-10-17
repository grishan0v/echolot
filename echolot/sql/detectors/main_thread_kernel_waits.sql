-- @id: main_thread_kernel_waits
-- @title: The main thread held in the kernel by the app's own threads
-- @why: the main thread sleeps in the kernel for something other than the
--       disk, and a thread of the app is what lets it out. No slice names
--       that time, and `io_wait` takes only the disk
-- @param: min_total_ms = 16
-- @calibrate: min_total_ms = top10(total_ms) * 1.5
-- @identity: location
--
-- The main thread in `D` with `io_wait = 0`: uninterruptible, and the kernel
-- says it is not the disk. Each such sleep ends with a wakeup, and the thread
-- that sent it is the one that let the main thread go — read as in
-- `main_thread_waits`, with the waker of the state that follows.
--
-- On a large Kotlin app's launch on an SM-A515F the main thread spent ~106 ms
-- of a ~1.45 s cold launch this way (~300 ms of the warm launches recorded
-- before #404), 97–99% of it ended by the app's own threads, and where those
-- threads had a slice open it was work that maps memory: native library
-- loading, starting threads (ART's `InitStack`), creating the EGL context,
-- opening dex files. A thread taking the process's memory-map lock for
-- writing holds up every other thread's page fault. A test app on the same
-- phone put its main thread here for 2.3 s with three threads mapping memory
-- beside it, and for 5 ms without them. The report showed the time only as
-- "blocked in the kernel" in the budget and as self time of whatever slice
-- was open, where it reads as slow code.
--
-- What the trace cannot say on a production kernel is which lock it was:
-- `blocked_function` is empty there, and other kernel locks — the GPU
-- driver's, the file system's — leave the same signature. On a userdebug
-- kernel the function is named, and `detail` carries it.
--
-- A row is the thread of the app that ended the sleeps, its digits folded as
-- in `main_thread_waits`. `RenderThread` and binder threads count here: the
-- thread that released the lock is the finding, whatever its name. A sleep
-- another process ended, or that has no waker, stays out, and so does a
-- trace recorded without `sched_blocked_reason`, where `io_wait` is unknown.
--
-- `detail` says first what the waking thread was inside when it woke the
-- main thread — the slice that holds most of the row's time, with its share
-- — and then where the main thread was, the same way. Shares rather than
-- milliseconds, as in `main_thread_waits`.

WITH states AS MATERIALIZED (
    -- The main thread's states in order, each with the waker of the next.
    SELECT s.ts, s.dur, s.state, s.io_wait, s.blocked_function, s.utid,
           LEAD(s.waker_utid) OVER (ORDER BY s.ts)                 AS waker_utid
    FROM thread_state s
    WHERE s.utid = (SELECT t.utid FROM thread t CROSS JOIN _proc p
                    WHERE t.upid = p.upid AND t.tid = p.pid
                    ORDER BY t.utid DESC LIMIT 1)
),
-- The sleeps this detector counts, before anything is looked up for them.
candidates AS MATERIALIZED (
    SELECT s.ts AS raw_ts, s.dur AS raw_dur, s.utid, s.waker_utid,
           MAX(s.ts, wn.ts_start)                                  AS ts,
           MIN(s.ts + s.dur, wn.ts_end) - MAX(s.ts, wn.ts_start)    AS dur,
           w.name                                                  AS waker,
           NULLIF(s.blocked_function, '')                          AS fn
    FROM states s
    -- CROSS JOIN fixes the order: the sleeps outside, the thread looked up
    -- for each. Left to the planner, it put `thread` outside and took 13 s
    -- on a launch with 37,000 such sleeps; this way, 0.2.
    CROSS JOIN thread w ON w.utid = s.waker_utid
    CROSS JOIN _proc p
    CROSS JOIN _window wn
    WHERE s.state IN ('D', 'DK') AND s.io_wait = 0 AND s.dur > 0
      AND s.ts < wn.ts_end AND s.ts + s.dur > wn.ts_start
      AND w.upid = p.upid AND w.tid != p.pid
      AND w.name IS NOT NULL
),
-- The slices of the threads involved, once: a lookup per sleep in the
-- whole of `_slice_win` cost 5 s more on a launch with 37,000 sleeps.
lanes AS MATERIALIZED (
    SELECT x.utid, x.ts, x.ts + x.dur AS te, x.depth, x.name
    FROM _slice_win x
    CROSS JOIN _window wn
    WHERE x.utid IN (SELECT utid FROM candidates UNION SELECT waker_utid FROM candidates)
      -- A slice that spans the whole window is the scenario's anchor.
      AND NOT (x.ts <= wn.ts_start AND x.ts + x.dur >= wn.ts_end AND x.is_main_thread = 1)
),
sleeps AS MATERIALIZED (
    SELECT c.ts, c.dur, c.waker, c.fn,
           -- Where the main thread went to sleep: the deepest slice open then.
           (SELECT l.name FROM lanes l
            WHERE l.utid = c.utid AND l.ts <= c.raw_ts AND l.te > c.raw_ts
            ORDER BY l.depth DESC LIMIT 1)                         AS inside,
           -- What the waking thread was inside when it woke it.
           (SELECT l.name FROM lanes l
            WHERE l.utid = c.waker_utid
              AND l.ts <= c.raw_ts + c.raw_dur AND l.te >= c.raw_ts + c.raw_dur
            ORDER BY l.depth DESC LIMIT 1)                         AS waker_inside
    FROM candidates c
),
waits AS MATERIALIZED (
    SELECT ts, dur, inside, waker_inside, fn,
           -- Digits folded, a run of them to one `#`.
           REPLACE(REPLACE(REPLACE(
               REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                   waker, '0', '#'), '1', '#'), '2', '#'), '3', '#'), '4', '#'),
                   '5', '#'), '6', '#'), '7', '#'), '8', '#'), '9', '#'),
               '####', '#'), '##', '#'), '##', '#')                AS location
    FROM sleeps
),
grouped AS (
    SELECT location,
           COUNT(*)                                                AS count,
           SUM(dur)                                                AS total_ns,
           MAX(dur)                                                AS max_ns
    FROM waits
    GROUP BY location
),
-- The slice that holds most of each row's time: the waking thread's, the
-- main thread's, and the kernel function's where the kernel names one.
waker_top AS (
    SELECT location, waker_inside, ns
    FROM (SELECT location, waker_inside, SUM(dur) AS ns,
                 ROW_NUMBER() OVER (PARTITION BY location
                                    ORDER BY SUM(dur) DESC, waker_inside) AS k
          FROM waits GROUP BY location, waker_inside)
    WHERE k = 1
),
main_top AS (
    SELECT location, inside, ns
    FROM (SELECT location, inside, SUM(dur) AS ns,
                 ROW_NUMBER() OVER (PARTITION BY location
                                    ORDER BY SUM(dur) DESC, inside) AS k
          FROM waits GROUP BY location, inside)
    WHERE k = 1
),
fn_top AS (
    SELECT location, fn
    FROM (SELECT location, fn,
                 ROW_NUMBER() OVER (PARTITION BY location
                                    ORDER BY SUM(dur) DESC, fn) AS k
          FROM waits WHERE fn IS NOT NULL GROUP BY location, fn)
    WHERE k = 1
)
SELECT
    g.location,
    g.count,
    ROUND(g.total_ns / 1e6, 2)                                     AS total_ms,
    ROUND(g.max_ns / 1e6, 2)                                       AS max_ms,
    CASE WHEN w.waker_inside IS NULL THEN 'waking outside any slice '
         ELSE 'waking inside ' || w.waker_inside || ' ' END
        || CAST(ROUND(w.ns * 100.0 / g.total_ns) AS INT) || '%'
        || ' · main thread '
        || CASE WHEN m.inside IS NULL THEN 'outside any slice '
                ELSE 'inside ' || m.inside || ' ' END
        || CAST(ROUND(m.ns * 100.0 / g.total_ns) AS INT) || '%'
        || COALESCE(' · kernel: ' || f.fn, '')                     AS detail
FROM grouped g
JOIN waker_top w USING (location)
JOIN main_top m USING (location)
LEFT JOIN fn_top f USING (location)
WHERE g.total_ns >= {{min_total_ms}} * 1000000
ORDER BY total_ms DESC, location
LIMIT 20;

-- @intervals
--
-- A row stands for the sleeps its thread ended: the main thread's own time,
-- clipped to the window. Sleeps do not overlap one another.

WITH states AS MATERIALIZED (
    SELECT s.ts, s.dur, s.state, s.io_wait, s.utid,
           LEAD(s.waker_utid) OVER (ORDER BY s.ts)                 AS waker_utid
    FROM thread_state s
    WHERE s.utid = (SELECT t.utid FROM thread t CROSS JOIN _proc p
                    WHERE t.upid = p.upid AND t.tid = p.pid
                    ORDER BY t.utid DESC LIMIT 1)
),
sleeps AS MATERIALIZED (
    SELECT MAX(s.ts, wn.ts_start)                                  AS ts,
           MIN(s.ts + s.dur, wn.ts_end) - MAX(s.ts, wn.ts_start)    AS dur,
           w.name                                                  AS waker
    FROM states s
    CROSS JOIN thread w ON w.utid = s.waker_utid
    CROSS JOIN _proc p
    CROSS JOIN _window wn
    WHERE s.state IN ('D', 'DK') AND s.io_wait = 0 AND s.dur > 0
      AND s.ts < wn.ts_end AND s.ts + s.dur > wn.ts_start
      AND w.upid = p.upid AND w.tid != p.pid
      AND w.name IS NOT NULL
)
SELECT s.ts, s.dur
FROM sleeps s
JOIN _rows r ON r.location =
    REPLACE(REPLACE(REPLACE(
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            s.waker, '0', '#'), '1', '#'), '2', '#'), '3', '#'), '4', '#'),
            '5', '#'), '6', '#'), '7', '#'), '8', '#'), '9', '#'),
        '####', '#'), '##', '#'), '##', '#');
