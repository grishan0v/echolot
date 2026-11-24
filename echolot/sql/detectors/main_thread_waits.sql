-- @id: main_thread_waits
-- @title: The main thread's waits on the app's own threads
-- @why: a main thread that waits on its own background work in the middle
--       of a piece of work spends that time asleep inside the slice, and no
--       slice says what it waited for. The kernel does: the thread that ended
--       each sleep is the one that let the main thread go
-- @param: min_total_ms = 16 ms — the time the main thread slept inside its
--       own work until one thread of the app let it go, summed, for that
--       thread to be a row
-- @param: lock_glob = *contention* — GLOB over slice names: a lock's
--       contention slice. A sleep inside one is `monitor_contention`'s, and
--       left out here
-- @fires: total_ms >= {{min_total_ms}}
-- @calibrate: min_total_ms = top10(total_ms) * 1.5
-- @identity: location
-- @column: location — the thread of the app that let the main thread go,
--       with the numbers the runtime chose folded: `arch_disk_io_#`,
--       `DefaultDispatch`
-- @column: count — the main thread's sleeps inside its own work that this
--       thread ended
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: detail — the slice the main thread waited longest in, then what
--       the thread that let it go was doing over its waits: `blocked in
--       animation · its thread: running 80%, waiting for a CPU 10%, in
--       the kernel 6%, asleep 4%`
-- @plain_title: The screen waits for background work
-- @plain_why: The main thread draws the screen and answers taps. While it
--       is held up waiting for another thread of the app, it can do neither.
-- @plain_clean: The main thread did not wait long for the app's other threads.
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
-- Only the large app's kind is a wait (#926). A sleep that began inside a
-- slice stopped the main thread in the middle of its work, and the work went
-- on when the other thread let it go. A sleep with no slice open is the
-- looper with nothing to do, asleep until a message comes, and the thread
-- that ended it is the one that posted the message: a delivery, not a wait.
-- The main thread could have drawn a frame or answered a tap the moment one
-- came. An app that shows a WebView has the shape at its plainest: one of
-- Chromium's threads wakes the idle looper hundreds of times in a run, and
-- added to the few waits inside the frame's `animation` that made a row of
-- seconds that ranked first and read as the main thread waiting all that
-- time. Idle time also runs the wrong way for a comparison: in a run whose
-- length the script fixes, a main thread that gets faster idles more.
--
-- So the idle sleeps are no row's, Room's above among them. The wakeups
-- alone cannot tell a launch that waited for background work with its main
-- thread idle from a looper that had nothing to do; the path the launch took
-- through the other threads can (#402). Until then the start's breakdown
-- shows the main thread sleeping, and `uninstrumented_cpu` the CPU of the
-- threads that worked.
--
-- A row is a thread of the app, named with the numbers the runtime chose
-- folded (`_name_family`, #831), so that `arch_disk_io_0` to `_3` are one
-- row, and `pool-14-thread-` is the same row in a repeat where it is
-- `pool-15-thread-`. The kernel keeps 15
-- characters of a thread's name, so every coroutine worker is
-- `DefaultDispatch` already. `detail` names the slice that was open when
-- the main thread went to sleep, the one it waited longest in:
-- `blocked in loadConfig`. A slice that spans the whole window is the
-- scenario's anchor and holds the idle moments too, so it is no place to
-- wait in, as for `main_thread_block`'s intervals: a sleep under it alone
-- is idle.
--
-- Other detectors keep the waits they already explain:
-- - a sleep inside a lock's contention slice (`lock_glob`) is
--   `monitor_contention`'s, which names both sides of the lock: on the
--   large app that was 84–95% of the main thread's waits on coroutine
--   workers;
-- - a sleep a binder thread ended is a binder reply, `binder_txn`'s;
-- - a sleep `RenderThread` ended is the frame pipeline;
-- - a sleep another process ended is not the app's to change.

-- The sleeps this detector counts, each under the row it belongs to, are
-- written down once as its own table: both the rows below and the time
-- they stand for (`@intervals`) are read from it (#490).
CREATE OR REPLACE PERFETTO TABLE _det_main_thread_waits AS
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
           w.utid                                                  AS utid,
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
)
-- The thread and the slice under the names the runtime's numbers are
-- folded out of, the same fold every detector groups by (#831). A sleep
-- with no slice open is the idle looper's, and no wait (#926).
-- `utid` is the thread that ended the sleep, the row's thread before the
-- fold, for the join below.
SELECT s.ts, s.dur, s.utid,
       COALESCE(fi.family, s.inside)                               AS inside,
       COALESCE(f.family, s.waker)                                 AS location
FROM sleeps s
LEFT JOIN _name_family f  ON f.name = s.waker
LEFT JOIN _name_family fi ON fi.name = s.inside
WHERE s.inside IS NOT NULL AND s.inside NOT GLOB '{{lock_glob}}';

-- What the thread that let the main thread go was doing while it slept
-- (#418). The row names that thread, and the fix depends on its state. On
-- a CPU, the main thread's way forward went through that thread's code:
-- make it cheaper, or stop waiting for it. Waiting for a CPU, in the kernel
-- or asleep, that thread was held up too, and its code is not the cost:
-- what held it up is, `runnable_starvation`, `io_wait` or
-- `monitor_contention` for that thread. Opening an article in the
-- Wikipedia app, the main thread waited ~0.4 s inside `animation` until
-- Chromium's IO thread let it go, and that thread slept through more than
-- half of it and ran less than a fifth: it passed on what another thread
-- had done, and its own code was not what the main thread waited for.
--
-- Each sleep is split by its own thread's states over its time, so a row
-- that folds a pool reads each worker for the sleeps it ended. The main
-- thread's sleeps do not overlap one another, and one thread's states do
-- not either: partitioned by the thread that woke it, SPAN_JOIN has the
-- input it needs. What no state covers is `unknown`: a thread the
-- scheduler had not run yet in the recording.
DROP TABLE IF EXISTS _det_main_thread_waits_by;
CREATE VIRTUAL TABLE _det_main_thread_waits_by
USING SPAN_JOIN(_det_main_thread_waits PARTITIONED utid, _tstate_win PARTITIONED utid);

WITH grouped AS (
    SELECT location,
           COUNT(*)                                                AS count,
           SUM(dur)                                                AS total_ns,
           MAX(dur)                                                AS max_ns,
           COUNT(DISTINCT utid)                                    AS threads
    FROM _det_main_thread_waits
    GROUP BY location
),
-- The waking threads' states over the row's waits.
woke AS (
    SELECT location,
           TOTAL(CASE WHEN state = 'Running' THEN dur END)         AS running,
           TOTAL(CASE WHEN state IN ('R', 'R+') THEN dur END)      AS queued,
           TOTAL(CASE WHEN state IN ('D', 'DK') THEN dur END)      AS kernel,
           TOTAL(CASE WHEN state = 'S' THEN dur END)               AS asleep
    FROM _det_main_thread_waits_by
    GROUP BY location
),
said AS (
    SELECT g.location,
           CASE WHEN g.threads > 1 THEN 'its threads: ' ELSE 'its thread: ' END
           || SUBSTR(
               CASE WHEN ROUND(100.0 * w.running / g.total_ns) >= 1
                    THEN ', running ' || CAST(ROUND(100.0 * w.running / g.total_ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * w.queued / g.total_ns) >= 1
                    THEN ', waiting for a CPU '
                         || CAST(ROUND(100.0 * w.queued / g.total_ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * w.kernel / g.total_ns) >= 1
                    THEN ', in the kernel ' || CAST(ROUND(100.0 * w.kernel / g.total_ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * w.asleep / g.total_ns) >= 1
                    THEN ', asleep ' || CAST(ROUND(100.0 * w.asleep / g.total_ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * (g.total_ns - w.running - w.queued - w.kernel - w.asleep)
                               / g.total_ns) >= 1
                    THEN ', unknown '
                         || CAST(ROUND(100.0 * (g.total_ns - w.running - w.queued - w.kernel
                                                - w.asleep) / g.total_ns) AS INT) || '%'
                    ELSE '' END, 3)                                AS woke
    FROM grouped g
    JOIN woke w USING (location)
    WHERE ROUND(100.0 * (w.running + w.queued + w.kernel + w.asleep) / g.total_ns) >= 1
),
-- The slice the main thread spent most of its waits in, per row.
blocked_in AS (
    SELECT location, inside
    FROM (SELECT location, inside,
                 ROW_NUMBER() OVER (PARTITION BY location
                                    ORDER BY SUM(dur) DESC, inside) AS k
          FROM _det_main_thread_waits
          GROUP BY location, inside)
    WHERE k = 1
)
SELECT
    g.location,
    g.count,
    ROUND(g.total_ns / 1e6, 2)                                     AS total_ms,
    ROUND(g.max_ns / 1e6, 2)                                       AS max_ms,
    'blocked in ' || b.inside
    || COALESCE(' · ' || w.woke, '')                               AS detail
FROM grouped g
JOIN blocked_in b USING (location)
LEFT JOIN said w USING (location)
ORDER BY total_ms DESC, location;

-- @intervals
--
-- A row stands for the waits its thread ended, as the first query wrote
-- them down: the main thread's own time, clipped to the window. Sleeps do
-- not overlap one another, and the idle ones are no row's (#926).

SELECT ts, dur
FROM _det_main_thread_waits
WHERE location IN (SELECT location FROM _rows);
