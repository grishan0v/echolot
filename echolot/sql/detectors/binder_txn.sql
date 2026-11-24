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

-- The transactions this detector counts, each with who answered it, written
-- down once: the rows and the time they stand for (`@intervals`) are both
-- read from here (#490).
CREATE OR REPLACE PERFETTO TABLE _det_binder_txn AS
SELECT
    s.ts,
    s.dur,
    s.tid,
    COALESCE(f.family, s.thread_name)                              AS thread_name,
    s.is_main_thread,
    b.callee
FROM _slice_win s
-- A thread's name under the fold every detector groups by: a pool's
-- workers are one row, and `threads` counts them all (#831, #845).
LEFT JOIN _name_family f ON f.name = s.thread_name
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
) b ON b.binder_txn_id = s.slice_id
-- Trace Processor emits four kinds of binder slice: 'binder transaction',
-- 'binder reply', 'binder transaction async', 'binder async rcv'. The mask
-- 'binder transaction*' also covers the async one — where the sender does not
-- block, so presenting it as the cost of synchronous IPC would be a lie.
WHERE s.name GLOB '{{name_glob}}'
  AND s.name NOT GLOB '{{skip_glob}}';

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
    || COALESCE(' · to ' || n.callees, '')                         AS detail
FROM grouped g
LEFT JOIN named n ON n.thread_name IS g.thread_name
                 AND n.is_main_thread = g.is_main_thread
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
