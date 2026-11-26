-- @id: runnable_starvation
-- @title: CPU preemption (runnable but not running)
-- @why: the thread is ready to work but the scheduler will not let it in. The
--       slice is "long" not because the code is heavy but because something
--       else held the core — and for the main thread the row says what
-- @param: min_runnable_ms = 20 ms — the time a thread has to spend ready to
--       run and not running, in one state, summed, to be a row
-- @fires: total_ms >= {{min_runnable_ms}}
-- @calibrate: min_runnable_ms = top10(total_ms) * 1.5
-- @identity: location, state
-- @column: location — the thread's name; `main` for the main thread, told
--       apart by its tid
-- @column: threads — how many threads of that name the row adds up: the
--       kernel cuts a name to fifteen characters, and a pool's workers
--       share one. Their time together is not the window's
-- @column: count — the stretches it spent in that state in the window
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: state — `R`, preempted, or `R+`, woken and not yet run
-- @column: detail — on the main thread only: who held the core it was put
--       on next — the app's own threads, other processes, or nobody — with
--       the app's thread that held it longest, then how many of the other
--       cores stood idle meanwhile. Empty on other threads
-- @plain_title: Waiting for a free processor core
-- @plain_why: A thread that is ready to work but finds no free processor core
--       has to stand still. That wait comes from how busy the phone is, not
--       from the thread's own code.
-- @plain_clean: No thread had to wait long for a free processor core.
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
          FROM (SELECT h.state, h.dur,
                       COALESCE(f.family, h.holder_thread) AS holder_thread
                FROM _main_runnable_held h
                LEFT JOIN _name_family f ON f.name = h.holder_thread
                WHERE h.holder = 'app')
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
-- The main thread is `main`, by its tid and not by its name, and `threads`
-- says how many threads a row adds up: see `monitor_contention` (#511).
SELECT
    CASE WHEN s.tid = p.pid THEN 'main' ELSE COALESCE(f.family, s.thread_name) END
                                      AS location,
    COUNT(DISTINCT s.tid)             AS threads,
    COUNT(*)                          AS count,
    ROUND(SUM(s.dur) / 1e6, 2)        AS total_ms,
    ROUND(MAX(s.dur) / 1e6, 2)        AS max_ms,
    s.state                           AS state,
    -- The state is a column of its own; `detail` holds only what the
    -- state does not say, and nothing on a thread with nothing more (#515).
    CASE WHEN MAX(s.tid = p.pid) = 1 AND e.text IS NOT NULL
         THEN LTRIM(e.text, ' ·') END AS detail
FROM _tstate_win s
CROSS JOIN _proc p
LEFT JOIN evidence e ON e.state = s.state
-- A thread's name under the fold every detector groups by: a pool's
-- workers are one row, and `threads` counts them all (#831, #845).
LEFT JOIN _name_family f ON f.name = s.thread_name
WHERE s.state IN ('R', 'R+')
GROUP BY COALESCE(f.family, s.thread_name), s.tid = p.pid, s.state
ORDER BY total_ms DESC;

-- @intervals
--
-- The main thread's time ready and denied a CPU, in the states its rows name.
-- `_rows` carries the row's `state`: `detail` opened with it until #515, and
-- matched on that, the main thread's waits stopped counting in the window's
-- account when it no longer did.

SELECT t.ts, t.dur
FROM _tstate_win t
CROSS JOIN _proc p
JOIN _rows r ON r.location = 'main' AND r.state = t.state
WHERE t.tid = p.pid
  AND t.state IN ('R', 'R+');

-- @longest
--
-- The longest wait for a CPU of each row's threads in its state: `max_ms` is
-- the longest of them, within the window.

SELECT location, state, ts, dur, tid
FROM (
    SELECT r.location, r.state, t.ts, t.dur, t.tid,
           ROW_NUMBER() OVER (PARTITION BY r.location, r.state
                              ORDER BY t.dur DESC, t.ts) AS k
    FROM _tstate_win t
    CROSS JOIN _proc p
    LEFT JOIN _name_family f ON f.name = t.thread_name
    JOIN _rows r ON r.state = t.state
                AND r.location = CASE WHEN t.tid = p.pid THEN 'main'
                                      ELSE COALESCE(f.family, t.thread_name) END
    WHERE t.state IN ('R', 'R+') AND t.dur > 0
)
WHERE k = 1;
