-- @id: gc_pressure
-- @title: Garbage collector pressure
-- @why: frequent GC means many intermediate allocations; GC itself is the
--       symptom, not the cause
-- @needs: atrace:dalvik
-- @module: android.garbage_collection
-- @param: max_events = 15 slices — a name on a thread seen this many times
--       in the window is a row. A floor despite its name
-- @param: max_total_ms = 120 ms — a name on a thread whose slices add up to
--       this much is a row too. A floor despite its name
-- @param: name_glob = *GC — GLOB over slice names: ART's collection cycles,
--       every one of which ends in GC
-- @param: name_glob_alt = waitWhileAllocating* — GLOB over slice names: a
--       thread of the app stopped in an allocation until the collector is done
-- @fires: count >= {{max_events}} OR total_ms >= {{max_total_ms}}
-- @calibrate: max_events = top10(count) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @identity: location, thread
-- @column: location — the slice name: the kind of collection, or the
--       allocation wait
-- @column: count — its slices on that thread in the window
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: thread — the thread the slices are on: the collector's own,
--       `HeapTaskDaemon`, for a background cycle; a thread of the app for an
--       allocation wait or a collection it asked for
-- @column: detail — for a collection, what a cycle freed on average and how
--       large the heap grew before one; for an allocation wait, the
--       collections it waited for, with their share of its time. Empty on a
--       trace without the runtime's heap-size counter
-- @plain_title: Memory clean-up
-- @plain_why: When the app creates many short-lived objects, the system has to
--       clean them up often. That takes processor time and can stop the app
--       for a moment.
-- @plain_clean: Memory clean-up ran rarely and did not take long.
--
-- Masks verified against a live Android 14 trace (ART, concurrent copying).
--
-- '*GC' matches on the END of the name. ART names collection cycles like this:
--   Background young concurrent copying GC
--   Background concurrent copying GC
--   Explicit concurrent copying GC        (from System.gc())
-- All of them end in GC, so a suffix mask catches them precisely.
--
-- What is deliberately NOT here:
--   * thread_name GLOB 'HeapTaskDaemon*' — the old thread mask dragged in
--     everything: TrimMaps, TrimIndirectReferenceTables and 305 instances of
--     LocalReferenceTable::Trim. That is not GC pressure, that is whatever
--     else happens to share the thread.
--   * the phases CopyingPhase / MarkingPhase / ReclaimPhase / FlipThreadRoots
--     — they live INSIDE the GC slice (depth 1 under the cycle's depth 0).
--     Counting them separately counts the same time twice: on the live trace
--     CopyingPhase reported 295 ms against 282 ms for the entire cycle.
--   * GarbageCollectCache / Code cache collection on Jit thread pool — that is
--     JIT cache collection, a different phenomenon with nothing to do with
--     application allocations.
--
-- The second mask is the other side of the coin: waitWhileAllocatingLocked
-- appears on application threads when an allocation stalls waiting for the
-- collector. The price of GC is visible not only on HeapTaskDaemon but also
-- wherever the app stands still because of it.
--
-- What a cycle did is not in its name. The standard library's
-- `android.garbage_collection` reads it off the runtime's `Heap size (KB)`
-- counter, which ART writes under the same `dalvik` category as the cycles:
-- the heap's size when the cycle began and after it ended, so what it freed
-- (#514). Its `gc_type` and `is_mark_compact` are not used: both are read
-- off the same name, which is already the row's `location`. On a trace
-- without the counter the module holds no cycle at all, and the rows are
-- what they were, with no evidence.
--
-- The thread is a column of its own, `thread`, and names the row with
-- `location`; `detail` is evidence. A report written before had the thread
-- in `detail`, and `compare` reads it from there.
--
-- A cold start's first two seconds are blind here (#609). After the fork
-- from zygote ART holds the heap's target at its growth limit for
-- `kPostForkMaxHeapDurationMS`, 2000 ms (runtime/gc/heap.cc), so that much
-- allocation runs no cycle at all: measured on SM-A515F, Android 13, a
-- planted 300 MB of short-lived arrays in the first half second took the
-- heap to 379 MB with no collection, and the first one came at 1.96 s on
-- both sides, with the same five cycles in the run. Silence over that
-- stretch says nothing about allocations. The heap's size does: a row's
-- evidence carries the largest it reached before a cycle, and the counter
-- is in the trace whenever `dalvik` was recorded.

-- The slices this detector counts, each with what the module knows of it,
-- written down once: the rows and the time they stand for (`@intervals`)
-- are both read from here (#490).
CREATE OR REPLACE PERFETTO TABLE _det_gc_pressure AS
SELECT
    s.ts,
    s.dur,
    -- The slice's and the thread's names under the fold every detector
    -- groups by (#831): a collection's numbers and a pool's are the
    -- runtime's.
    COALESCE(fn.family, s.name)                                    AS name,
    COALESCE(ft.family, s.thread_name)                             AS thread_name,
    s.is_main_thread,
    g.reclaimed_mb,
    g.max_heap_mb,
    -- An allocation wait waits for the collection that was running when it
    -- began, or that began while it waited: the first one over it.
    CASE WHEN (s.name = '{{name_glob_alt}}' OR s.name GLOB '{{name_glob_alt}}') THEN (
        SELECT COALESCE(fc.family, c.name)
        FROM android_garbage_collection_events e
        JOIN slice c ON c.id = e.gc_id
        LEFT JOIN _name_family fc ON fc.name = c.name
        WHERE e.upid = (SELECT upid FROM _proc)
          AND e.gc_ts < s.ts + s.dur
          AND e.gc_ts + e.gc_dur > s.ts
        ORDER BY e.gc_ts, e.gc_id
        LIMIT 1
    ) END                                                          AS during
FROM _slice_win s
LEFT JOIN android_garbage_collection_events g ON g.gc_id = s.slice_id
LEFT JOIN _name_family fn ON fn.name = s.name
LEFT JOIN _name_family ft ON ft.name = s.thread_name
WHERE (s.name = '{{name_glob}}' OR s.name GLOB '{{name_glob}}')
   OR (s.name = '{{name_glob_alt}}' OR s.name GLOB '{{name_glob_alt}}');

WITH grouped AS (
    SELECT name,
           thread_name,
           COUNT(*)                                                AS count,
           SUM(dur)                                                AS total_ns,
           MAX(dur)                                                AS max_ns,
           -- A cycle's average rather than the sum: merged, `detail` comes
           -- from the worst repeat and `count` is the median, and a sum of
           -- one beside the other would not add up.
           AVG(reclaimed_mb)                                       AS freed_mb,
           MAX(max_heap_mb)                                        AS heap_mb
    FROM _det_gc_pressure
    GROUP BY name, thread_name
),
waited AS (
    SELECT name,
           thread_name,
           during,
           SUM(dur)                                                AS ns,
           ROW_NUMBER() OVER (PARTITION BY name, thread_name
                              ORDER BY SUM(dur) DESC, during)      AS k
    FROM _det_gc_pressure
    WHERE during IS NOT NULL
    GROUP BY name, thread_name, during
),
shares AS (
    SELECT w.name,
           w.thread_name,
           w.k,
           w.during || ' ' || CASE
               WHEN ROUND(w.ns * 100.0 / g.total_ns) < 1 THEN '<1'
               ELSE CAST(ROUND(w.ns * 100.0 / g.total_ns) AS INT)
           END || '%'                                              AS said
    FROM waited w
    JOIN grouped g ON g.name = w.name AND g.thread_name IS w.thread_name
),
named AS (
    SELECT name,
           thread_name,
           MAX(CASE WHEN k = 1 THEN said END)
           || COALESCE(', ' || MAX(CASE WHEN k = 2 THEN said END), '')
           || CASE WHEN MAX(k) > 2 THEN ', ' || (MAX(k) - 2) || ' more' ELSE '' END
                                                                   AS collections
    FROM shares
    GROUP BY name, thread_name
)
SELECT
    g.name                                                         AS location,
    g.count,
    ROUND(g.total_ns / 1e6, 2)                                     AS total_ms,
    ROUND(g.max_ns / 1e6, 2)                                       AS max_ms,
    g.thread_name                                                  AS thread,
    CASE WHEN g.freed_mb IS NOT NULL
         THEN printf('%.1f MB freed a cycle, the heap up to %.1f MB',
                     g.freed_mb, g.heap_mb)
         ELSE 'during ' || n.collections
    END                                                            AS detail
FROM grouped g
LEFT JOIN named n ON n.name = g.name AND n.thread_name IS g.thread_name
ORDER BY total_ms DESC;

-- @intervals
--
-- The collector's slices a row counted, where they ran on the main thread:
-- `waitWhileAllocating` there is the app standing still for the collector.

SELECT s.ts, s.dur
FROM _det_gc_pressure s
JOIN _rows r ON r.location = s.name AND r.thread IS s.thread_name
WHERE s.is_main_thread = 1;

-- @longest
--
-- The longest collection, or wait for one, of each row on its thread:
-- `max_ms` is the longest of them. The detector's table keeps no thread id,
-- and the slice it came from gives it back.

SELECT location, thread, ts, dur,
       (SELECT w.tid FROM _slice_win w
        WHERE w.ts = l.ts AND w.dur = l.dur
        ORDER BY w.is_main_thread DESC LIMIT 1) AS tid
FROM (
    SELECT r.location, r.thread, g.ts, g.dur,
           ROW_NUMBER() OVER (PARTITION BY r.location, r.thread
                              ORDER BY g.dur DESC, g.ts) AS k
    FROM _det_gc_pressure g
    JOIN _rows r ON r.location = g.name AND r.thread IS g.thread_name
    WHERE g.dur > 0
) l
WHERE k = 1;
