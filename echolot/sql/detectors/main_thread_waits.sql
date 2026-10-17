-- @id: main_thread_waits
-- @title: The main thread's waits on the app's own threads
-- @why: a start that waits on its own background work spends that time
--       asleep, and no slice says what it waited for. The kernel does: the
--       thread that ended each sleep is the one that woke the main thread
-- @param: min_total_ms = 16
-- @param: lock_glob = *contention*
-- @calibrate: min_total_ms = top10(total_ms) * 1.5
-- @identity: location
--
-- Every sleep of the main thread ends with a wakeup, and `sched_waking`
-- names the thread that sent it. trace_processor keeps it as `waker_utid`
-- on the state the thread was woken into, the one after the sleep, so each
-- sleep is read with the waker of the state that follows it. `collect`
-- records the event; a macrobenchmark's trace carries it too.
--
-- On a Compose app's first launch on an SM-A515F the main thread was idle
-- for 0.7 s of 2.1 s, and Room's `arch_disk_io_*` threads woke it about 260
-- times while the database filled. The report had those threads' CPU under
-- `uninstrumented_cpu` and "45% sleeping" in the start's breakdown, and
-- nothing connected the two. On a large Kotlin app the main thread blocked
-- for 19–36 ms a trace inside its own work until another thread of the app
-- let it go: WebView's initialization, SharedPreferences, coroutine workers.
-- `main_thread_block` counted that time as the self time of the slice that
-- waited. Those traces caught the end of a start the system had begun on its
-- own, right after a force-stop, and the launch that followed was warm.
--
-- A row is a thread of the app, named with its digits folded, so that
-- `arch_disk_io_0` to `_3` are one row, and `pool-14-thread-` is the same
-- row in a repeat where it is `pool-15-thread-`. The kernel keeps 15
-- characters of a thread's name, so every coroutine worker is
-- `DefaultDispatch` already. `detail` says where the main thread was:
-- idle, with no slice open, or blocked inside the slice that was open when
-- it went to sleep, the one it waited longest in, and with both the share of
-- each: `67% idle · 33% blocked in loadConfig`. A slice that spans the
-- whole window is the scenario's anchor and holds the idle moments too, so
-- it is no place to wait in, as for `main_thread_block`'s intervals.
--
-- Other detectors keep the waits they already explain:
-- - a sleep inside a lock's contention slice (`lock_glob`) is
--   `monitor_contention`'s, which names both sides of the lock: on the
--   large app that was 84–95% of the main thread's waits on coroutine
--   workers;
-- - a sleep a binder thread ended is a binder reply, `binder_txn`'s;
-- - a sleep `RenderThread` ended is the frame pipeline;
-- - a sleep another process ended is not the app's to change.

WITH states AS MATERIALIZED (
    -- The main thread's states in order, each with the waker of the next.
    SELECT s.ts, s.dur, s.state, s.utid,
           LEAD(s.waker_utid) OVER (ORDER BY s.ts)                 AS waker_utid
    FROM thread_state s
    WHERE s.utid = (SELECT t.utid FROM thread t CROSS JOIN _proc p
                    WHERE t.upid = p.upid AND t.tid = p.pid
                    ORDER BY t.utid DESC LIMIT 1)
),
sleeps AS MATERIALIZED (
    SELECT MAX(s.ts, wn.ts_start)                                  AS ts,
           MIN(s.ts + s.dur, wn.ts_end) - MAX(s.ts, wn.ts_start)    AS dur,
           w.name                                                  AS waker,
           -- Where the main thread went to sleep: the deepest slice open
           -- then, the anchor that spans the window left out.
           (SELECT x.name FROM _slice_win x
            WHERE x.utid = s.utid AND x.ts <= s.ts AND x.ts + x.dur > s.ts
              AND NOT (x.ts <= wn.ts_start AND x.ts + x.dur >= wn.ts_end)
            ORDER BY x.depth DESC LIMIT 1)                         AS inside
    FROM states s
    JOIN thread w ON w.utid = s.waker_utid
    CROSS JOIN _proc p
    CROSS JOIN _window wn
    WHERE s.state = 'S' AND s.dur > 0
      AND s.ts < wn.ts_end AND s.ts + s.dur > wn.ts_start
      AND w.upid = p.upid AND w.tid != p.pid
      AND w.name IS NOT NULL
      AND w.name != 'RenderThread'
      AND w.name NOT GLOB 'binder:*'
),
waits AS MATERIALIZED (
    SELECT ts, dur, inside,
           -- Digits folded, a run of them to one `#`.
           REPLACE(REPLACE(REPLACE(
               REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                   waker, '0', '#'), '1', '#'), '2', '#'), '3', '#'), '4', '#'),
                   '5', '#'), '6', '#'), '7', '#'), '8', '#'), '9', '#'),
               '####', '#'), '##', '#'), '##', '#')                AS location
    FROM sleeps
    WHERE inside IS NULL OR inside NOT GLOB '{{lock_glob}}'
),
grouped AS (
    SELECT location,
           COUNT(*)                                                AS count,
           SUM(dur)                                                AS total_ns,
           MAX(dur)                                                AS max_ns,
           SUM(CASE WHEN inside IS NULL THEN dur ELSE 0 END)       AS idle_ns
    FROM waits
    GROUP BY location
),
-- The slice the main thread spent most of its blocked time in, per row.
blocked_in AS (
    SELECT location, inside
    FROM (SELECT location, inside,
                 ROW_NUMBER() OVER (PARTITION BY location
                                    ORDER BY SUM(dur) DESC, inside) AS k
          FROM waits
          WHERE inside IS NOT NULL
          GROUP BY location, inside)
    WHERE k = 1
)
SELECT
    g.location,
    g.count,
    ROUND(g.total_ns / 1e6, 2)                                     AS total_ms,
    ROUND(g.max_ns / 1e6, 2)                                       AS max_ms,
    -- Shares rather than milliseconds: merged, `detail` comes from the
    -- worst repeat and `total_ms` is the median, and milliseconds of one
    -- beside the other read as a sum that does not add up.
    CASE WHEN b.inside IS NULL THEN 'idle'
         WHEN g.idle_ns = 0 THEN 'blocked in ' || b.inside
         ELSE CAST(ROUND(g.idle_ns * 100.0 / g.total_ns) AS INT) || '% idle · '
              || (100 - CAST(ROUND(g.idle_ns * 100.0 / g.total_ns) AS INT))
              || '% blocked in ' || b.inside
    END                                                            AS detail
FROM grouped g
LEFT JOIN blocked_in b USING (location)
WHERE g.total_ns >= {{min_total_ms}} * 1000000
ORDER BY total_ms DESC, location
LIMIT 20;

-- @intervals
--
-- A row stands for the sleeps its thread ended: the main thread's own time,
-- clipped to the window. Sleeps do not overlap one another.

WITH states AS MATERIALIZED (
    SELECT s.ts, s.dur, s.state, s.utid,
           LEAD(s.waker_utid) OVER (ORDER BY s.ts)                 AS waker_utid
    FROM thread_state s
    WHERE s.utid = (SELECT t.utid FROM thread t CROSS JOIN _proc p
                    WHERE t.upid = p.upid AND t.tid = p.pid
                    ORDER BY t.utid DESC LIMIT 1)
),
sleeps AS MATERIALIZED (
    SELECT MAX(s.ts, wn.ts_start)                                  AS ts,
           MIN(s.ts + s.dur, wn.ts_end) - MAX(s.ts, wn.ts_start)    AS dur,
           w.name                                                  AS waker,
           (SELECT x.name FROM _slice_win x
            WHERE x.utid = s.utid AND x.ts <= s.ts AND x.ts + x.dur > s.ts
              AND NOT (x.ts <= wn.ts_start AND x.ts + x.dur >= wn.ts_end)
            ORDER BY x.depth DESC LIMIT 1)                         AS inside
    FROM states s
    JOIN thread w ON w.utid = s.waker_utid
    CROSS JOIN _proc p
    CROSS JOIN _window wn
    WHERE s.state = 'S' AND s.dur > 0
      AND s.ts < wn.ts_end AND s.ts + s.dur > wn.ts_start
      AND w.upid = p.upid AND w.tid != p.pid
      AND w.name IS NOT NULL
      AND w.name != 'RenderThread'
      AND w.name NOT GLOB 'binder:*'
)
SELECT s.ts, s.dur
FROM sleeps s
JOIN _rows r ON r.location =
    REPLACE(REPLACE(REPLACE(
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            s.waker, '0', '#'), '1', '#'), '2', '#'), '3', '#'), '4', '#'),
            '5', '#'), '6', '#'), '7', '#'), '8', '#'), '9', '#'),
        '####', '#'), '##', '#'), '##', '#')
WHERE s.inside IS NULL OR s.inside NOT GLOB '{{lock_glob}}';
