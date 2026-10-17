-- @id: runnable_starvation
-- @title: CPU preemption (runnable but not running)
-- @why: the thread is ready to work but the scheduler will not let it in. The
--       slice is "long" not because the code is heavy but because something
--       else held the core — and for the main thread the row says what
-- @param: min_runnable_ms = 20
-- @calibrate: min_runnable_ms = top10(total_ms) * 1.5
-- @identity: location, state
--
-- One row per thread and state, `R` (preempted) apart from `R+` (woken and
-- not yet run). The state is a column of its own and part of the row's name;
-- `detail` is evidence.
--
-- For the main thread `detail` says who held the core it was put on next,
-- for as long as it waited: the app's own threads, other processes, or
-- nobody, with the app's thread that held it longest. On a large Kotlin
-- app's launch on an SM-A515F the app's own threads held it 45–48% of the
-- time, on cold launches and warm ones alike, while the app had 27–28 threads
-- runnable at once on 8 cores. "The device has other plans" is then the
-- wrong reading: the fix is in the app. Last comes how many of the other
-- cores stood idle meanwhile, on average — a wait that capacity stood by for
-- is the scheduler's delay rather than a crowd, and neither doing less nor
-- moving work fixes it.

WITH main_held AS (
    SELECT state,
           SUM(CASE WHEN holder = 'app'   THEN dur ELSE 0 END)       AS app_ns,
           SUM(CASE WHEN holder = 'other' THEN dur ELSE 0 END)       AS other_ns,
           SUM(CASE WHEN holder = 'idle'  THEN dur ELSE 0 END)       AS idle_ns
    FROM _main_runnable_held
    GROUP BY state
),
-- The app's thread that held the core longest, per state.
main_top AS (
    SELECT state, holder_thread
    FROM (SELECT state, holder_thread,
                 ROW_NUMBER() OVER (PARTITION BY state
                                    ORDER BY SUM(dur) DESC, holder_thread) AS k
          FROM _main_runnable_held
          WHERE holder = 'app'
          GROUP BY state, holder_thread)
    WHERE k = 1
),
main_idle AS (
    SELECT state, SUM(dur) AS ns
    FROM _main_runnable_idle
    WHERE cpu != next_cpu
    GROUP BY state
),
main_wait AS (
    SELECT state, SUM(dur) AS ns FROM _main_runnable GROUP BY state
),
-- Shares rather than milliseconds, as in `main_thread_waits`: merged,
-- `detail` comes from the worst repeat and `total_ms` is the median.
evidence AS (
    SELECT h.state,
           ' · its core held by the app '
           || CAST(ROUND(h.app_ns * 100.0 / (h.app_ns + h.other_ns + h.idle_ns)) AS INT) || '%'
           || COALESCE(' (' || t.holder_thread || ' most)', '')
           || ', other processes '
           || CAST(ROUND(h.other_ns * 100.0 / (h.app_ns + h.other_ns + h.idle_ns)) AS INT) || '%'
           || ', idle '
           || CAST(ROUND(h.idle_ns * 100.0 / (h.app_ns + h.other_ns + h.idle_ns)) AS INT) || '%'
           || ' · ' || printf('%.1f', COALESCE(i.ns, 0) * 1.0 / w.ns)
           || ' other cores idle on average'                       AS text
    FROM main_held h
    JOIN main_wait w USING (state)
    LEFT JOIN main_top t USING (state)
    LEFT JOIN main_idle i USING (state)
    WHERE h.app_ns + h.other_ns + h.idle_ns > 0
)
SELECT
    s.thread_name                     AS location,
    COUNT(*)                          AS count,
    ROUND(SUM(s.dur) / 1e6, 2)        AS total_ms,
    ROUND(MAX(s.dur) / 1e6, 2)        AS max_ms,
    s.state                           AS state,
    'state ' || s.state
        || CASE WHEN MAX(s.tid = p.pid) = 1 THEN COALESCE(e.text, '') ELSE '' END
                                      AS detail
FROM _tstate_win s
CROSS JOIN _proc p
LEFT JOIN evidence e ON e.state = s.state
WHERE s.state IN ('R', 'R+')
GROUP BY s.thread_name, s.state
HAVING SUM(s.dur) >= {{min_runnable_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
--
-- The main thread's time ready and denied a CPU, in the states its rows name.
-- `_rows` carries `location` and `detail`, and `detail` opens with the state.

SELECT t.ts, t.dur
FROM _tstate_win t
CROSS JOIN _proc p
JOIN _rows r ON r.location = t.thread_name
            AND (r.detail = 'state ' || t.state
                 OR r.detail LIKE 'state ' || t.state || ' · %')
WHERE t.tid = p.pid
  AND t.state IN ('R', 'R+');
