-- @id: binder_txn
-- @title: Long binder transactions
-- @why: synchronous IPC into another process is a common hidden cost at startup
-- @needs: atrace:binder_driver
-- @module: android.binder
-- @param: min_txn_ms = 10 ms — a thread with one transaction this long is a
--       row
-- @param: max_total_ms = 50 ms — a thread whose transactions add up to this
--       much is a row too, with none of them long. A floor despite its name
-- @param: name_glob = binder transaction* — GLOB over slice names: the
--       transactions counted
-- @param: skip_glob = *async* — GLOB over slice names: those of the above
--       left out. An async transaction does not block the thread that sent it
-- @fires: max_ms >= {{min_txn_ms}} OR total_ms >= {{max_total_ms}}
-- @calibrate: min_txn_ms = top10(max_ms) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @identity: location, thread
-- @column: location — the thread that sent the transactions
-- @column: threads — how many threads of that name the row adds up: the
--       kernel cuts a name to fifteen characters, and a pool's workers
--       share one. Their time together is not the window's
-- @column: count — its synchronous transactions in the window
-- @column: total_ms — the time it waited on them, summed
-- @column: max_ms — the longest one
-- @column: thread — `main thread` or `background`
-- @column: detail — `main thread` or `background` again, then where the
--       calls went: the processes that answered them, up to three, each with
--       its share of the row's time and the AIDL method it was asked for,
--       where the recording names one. The thread alone when the trace does
--       not say who answered
-- @column: phase — on the main thread's row, where its calls were made,
--       by share of the row's time: `before the app's code` (the platform's
--       own start), `Application`, `activity`, `frames`, `other`. Empty on a
--       background row
-- @plain_title: Calls to the system or to other apps
-- @plain_why: When the app asks the system or another app for something, the
--       thread that asked waits until the answer comes back.
-- @plain_clean: No call to the system or to another app took long.
--
-- Two conditions, not one. On a live cold start (772 ms window) the main
-- thread took 76 transactions totalling 65.9 ms, the longest 9.71 ms: not a
-- single one cleared the 10 ms bar, so the detector stayed silent while
-- synchronous IPC ate 8.5% of startup. Death by a thousand cuts is no less
-- real than one long call.
--
-- 50 ms is roughly three frames of accumulated blocking. The threshold is
-- absolute and therefore brittle: on a ten-second scenario it would fire every
-- time. The proper answer is `echolot calibrate`, which derives thresholds
-- from known-healthy runs.
--
-- Who answered is not in the slice's name: a row used to say which thread of
-- the app waited, and not on what — "something did binder", where the reader
-- needs "the main thread asks system_server". trace_processor ties the two
-- ends of a transaction together with a flow, the caller's `binder
-- transaction` to the server's `binder reply`, and the standard library's
-- `android.binder` reads the server's process off it, and the AIDL method
-- off the slice the server opens inside its reply
-- (`AIDL::java::IPackageManager::getPackageInfo::server`). The server writes
-- that slice only when the recording asked for the `aidl` atrace category,
-- which `collect` does not by default: on such a trace a row names the
-- processes, and no method.
--
-- The module only adds to the evidence. Which transactions count, and the
-- numbers, come from the slices as they always did: a call nobody answered
-- within the trace — its reply fell past the end, or the slices were written
-- by something other than the binder driver's events — is counted the same
-- and names nobody.
--
-- The thread is a column of its own, `thread`, and names the row with
-- `location`; `detail` is evidence, which changes from one repeat to the
-- next. A report written before had the thread in `detail`, and `compare`
-- reads it from there (#514).
--
-- ## Where in the launch
--
-- Who answered does not say whose the call was. On eleven cold starts of
-- Wikipedia's app the main thread's row was 70 ms, 94% of it to
-- system_server, and an agent sent to place it spent a round of markers on
-- it and came back with "I could not place it in app code". About a third
-- of it was the platform's own handshake: `attachApplication`, which
-- `ActivityThread.main` calls inside `ActivityThreadMain` before a single
-- class of the app is loaded — 8.6 to 31.4 ms a start, the median 20.9.
-- Every app pays it, and nothing in the app moves it (#911).
--
-- So the main thread's row says where its calls were made, in `phase`, by
-- the outermost of the platform's sections around each one:
--
--   before the app's code  `ActivityThreadMain`; and `bindApplication` up to
--                          its `makeApplication`, the platform setting the
--                          process up. The platform's own
--   Application            `bindApplication` from `makeApplication` on: the
--                          Application's constructor, the ContentProviders,
--                          `Application.onCreate`. All of `bindApplication`
--                          where the trace has no `makeApplication`: the
--                          platform's part is said only where it is known
--   activity               `activityStart`, `activityResume` and the rest of
--                          an activity's lifecycle, and the
--                          `clientTransactionExecuted` a newer release opens
--                          around them
--   frames                 `Choreographer#doFrame`, where the first frame's
--                          traversal asks for its window (`relayoutWindow`)
--   other                  any other message the looper ran, or none
--
-- The outermost, because the section the looper's message opened says which
-- part of the launch it was, and nothing opened inside it moves the call to
-- another: not an Initializer, not an app's own section that happens to be
-- called `activitySomething`. A section of the app's around them all, as the
-- fixture's `AppStart` is, is none of them, and is looked through. On eight
-- cold starts of the same app the row said: before the app's code 13 to 29
-- ms of 53 to 68, Application 11 to 18, activity 11 to 18, frames 7 to 12,
-- other 2 to 3.
--
-- Only the first is outside the app's reach. The others hold the app's code
-- and the platform's together, and a share there says where to look, not
-- whose the time was. Shares rather than milliseconds, for the reason the
-- callees have them. A column of its own rather than more of `detail`: the
-- report cuts `detail` at 100 characters, and who answered fills most of
-- them. A window that starts later says less: one cut from `bindApplication`
-- has no `ActivityThreadMain` in it, and a scroll's calls are frames and
-- other.

-- The transactions this detector counts, each with who answered it and, on
-- the main thread, where in the launch it was made, written down once: the
-- rows and the time they stand for (`@intervals`) are both read from here
-- (#490).
CREATE OR REPLACE PERFETTO TABLE _det_binder_txn AS
WITH txn AS (
    SELECT s.slice_id,
           s.ts,
           s.dur,
           s.tid,
           -- A thread's name under the fold every detector groups by: a
           -- pool's workers are one row, and `threads` counts them all
           -- (#831, #845).
           COALESCE(f.family, s.thread_name)                       AS thread_name,
           s.is_main_thread
    FROM _slice_win s
    LEFT JOIN _name_family f ON f.name = s.thread_name
    -- Trace Processor emits four kinds of binder slice: 'binder transaction',
    -- 'binder reply', 'binder transaction async', 'binder async rcv'. The
    -- mask 'binder transaction*' also covers the async one — where the
    -- sender does not block, so presenting it as the cost of synchronous IPC
    -- would be a lie.
    WHERE (s.name = '{{name_glob}}' OR s.name GLOB '{{name_glob}}')
      AND NOT (s.name = '{{skip_glob}}' OR s.name GLOB '{{skip_glob}}')
),
-- The outermost of the platform's sections a main-thread transaction was
-- made in, if any.
around AS (
    SELECT t.slice_id,
           (SELECT a.id
            FROM ancestor_slice(t.slice_id) a
            WHERE a.name IN ('ActivityThreadMain', 'bindApplication',
                             'clientTransactionExecuted')
               OR a.name GLOB 'activity[A-Z]*'
               OR a.name GLOB 'Choreographer#doFrame*'
            ORDER BY a.depth
            LIMIT 1)                                               AS section_id
    FROM txn t
    WHERE t.is_main_thread = 1
),
-- Where the app's own code starts inside a `bindApplication`.
made AS (
    SELECT parent_id                                               AS bind_id,
           MIN(ts)                                                 AS ts
    FROM slice
    WHERE name = 'makeApplication'
    GROUP BY parent_id
)
SELECT
    t.ts,
    t.dur,
    t.tid,
    t.thread_name,
    t.is_main_thread,
    b.callee,
    -- Where in the launch, as a number in launch order: the order `phase`
    -- says them in.
    CASE WHEN t.is_main_thread = 0 THEN NULL
         WHEN a.name = 'ActivityThreadMain' THEN 1
         WHEN a.name = 'bindApplication' AND t.ts < m.ts THEN 1
         WHEN a.name = 'bindApplication' THEN 2
         WHEN a.name GLOB 'Choreographer#doFrame*' THEN 4
         WHEN a.name IS NOT NULL THEN 3
         ELSE 5
    END                                                            AS step
FROM txn t
LEFT JOIN around r ON r.slice_id = t.slice_id
LEFT JOIN slice a  ON a.id = r.section_id
LEFT JOIN made m   ON m.bind_id = a.id
LEFT JOIN (
    -- One line per transaction, whatever the module holds: a duplicate
    -- here would be counted twice in the rows.
    SELECT binder_txn_id,
           MIN(
               -- The process without its directory: `/system/bin/surfaceflinger`
               -- is surfaceflinger. A process with no name, by its pid.
               COALESCE(substr(server_process,
                               length(rtrim(server_process,
                                            replace(server_process, '/', ''))) + 1),
                        'pid ' || server_pid)
               || COALESCE(' ' || interface || '.' || method_name, '')
           )                                                       AS callee
    FROM android_binder_txns
    GROUP BY binder_txn_id
) b ON b.binder_txn_id = t.slice_id;

WITH grouped AS (
    SELECT thread_name,
           is_main_thread,
           -- How many threads of that name the row adds up (#511). The main
           -- thread is one; a background row can be a whole pool.
           COUNT(DISTINCT tid)                                     AS threads,
           COUNT(*)                                                AS count,
           SUM(dur)                                                AS total_ns,
           MAX(dur)                                                AS max_ns
    FROM _det_binder_txn
    GROUP BY thread_name, is_main_thread
),
-- Who answered a row's calls, by the time the thread waited on each.
callees AS (
    SELECT thread_name,
           is_main_thread,
           callee,
           SUM(dur)                                                AS ns,
           ROW_NUMBER() OVER (PARTITION BY thread_name, is_main_thread
                              ORDER BY SUM(dur) DESC, callee)      AS k
    FROM _det_binder_txn
    WHERE callee IS NOT NULL
    GROUP BY thread_name, is_main_thread, callee
),
-- Shares rather than milliseconds: merged, `detail` comes from the worst
-- repeat and `total_ms` is the median, and milliseconds of one beside the
-- other read as a sum that does not add up.
shares AS (
    SELECT c.thread_name,
           c.is_main_thread,
           c.k,
           c.callee || ' ' || CASE
               WHEN ROUND(c.ns * 100.0 / g.total_ns) < 1 THEN '<1'
               ELSE CAST(ROUND(c.ns * 100.0 / g.total_ns) AS INT)
           END || '%'                                              AS said
    FROM callees c
    JOIN grouped g ON g.thread_name IS c.thread_name
                  AND g.is_main_thread = c.is_main_thread
),
named AS (
    SELECT thread_name,
           is_main_thread,
           MAX(CASE WHEN k = 1 THEN said END)
           || COALESCE(', ' || MAX(CASE WHEN k = 2 THEN said END), '')
           || COALESCE(', ' || MAX(CASE WHEN k = 3 THEN said END), '')
           || CASE WHEN MAX(k) > 3 THEN ', ' || (MAX(k) - 3) || ' more' ELSE '' END
                                                                   AS callees
    FROM shares
    GROUP BY thread_name, is_main_thread
),
-- Where in the launch the main thread made its calls, by the same shares,
-- in launch order. A phase with no call in it is left out.
phases AS (
    SELECT p.thread_name,
           p.step,
           CASE p.step WHEN 1 THEN 'before the app''s code'
                        WHEN 2 THEN 'Application'
                        WHEN 3 THEN 'activity'
                        WHEN 4 THEN 'frames'
                        ELSE 'other'
           END || ' ' || CASE
               WHEN ROUND(SUM(p.dur) * 100.0 / g.total_ns) < 1 THEN '<1'
               ELSE CAST(ROUND(SUM(p.dur) * 100.0 / g.total_ns) AS INT)
           END || '%'                                              AS said
    FROM _det_binder_txn p
    JOIN grouped g ON g.thread_name IS p.thread_name
                  AND g.is_main_thread = 1
    WHERE p.is_main_thread = 1
    GROUP BY p.thread_name, p.step
),
placed AS (
    SELECT thread_name,
           SUBSTR(COALESCE(', ' || MAX(CASE WHEN step = 1 THEN said END), '')
                  || COALESCE(', ' || MAX(CASE WHEN step = 2 THEN said END), '')
                  || COALESCE(', ' || MAX(CASE WHEN step = 3 THEN said END), '')
                  || COALESCE(', ' || MAX(CASE WHEN step = 4 THEN said END), '')
                  || COALESCE(', ' || MAX(CASE WHEN step = 5 THEN said END), ''),
                  3)                                               AS phase
    FROM phases
    GROUP BY thread_name
)
SELECT
    g.thread_name                                                  AS location,
    g.threads,
    g.count,
    ROUND(g.total_ns / 1e6, 2)                                     AS total_ms,
    ROUND(g.max_ns / 1e6, 2)                                       AS max_ms,
    CASE WHEN g.is_main_thread = 1 THEN 'main thread' ELSE 'background' END
                                                                   AS thread,
    CASE WHEN g.is_main_thread = 1 THEN 'main thread' ELSE 'background' END
    || COALESCE(' · to ' || n.callees, '')                         AS detail,
    w.phase
FROM grouped g
LEFT JOIN named n ON n.thread_name IS g.thread_name
                 AND n.is_main_thread = g.is_main_thread
LEFT JOIN placed w ON w.thread_name IS g.thread_name
                  AND g.is_main_thread = 1
ORDER BY total_ms DESC;

-- @intervals
--
-- The main thread's row stands for its transactions, end to end; the
-- background rows are other threads' time and stand for none of the window.

SELECT t.ts, t.dur
FROM _det_binder_txn t
WHERE t.is_main_thread = 1
  AND EXISTS (SELECT 1 FROM _rows r
              WHERE r.location = t.thread_name AND r.thread = 'main thread');
