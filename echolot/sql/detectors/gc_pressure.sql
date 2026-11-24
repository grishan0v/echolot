-- @id: gc_pressure
-- @title: Garbage collector pressure
-- @why: frequent GC means many intermediate allocations; GC itself is the
--       symptom, not the cause
-- @needs: atrace:dalvik
-- @param: max_events = 15 slices — a name on a thread seen this many times
--       in the window is a row. A floor despite its name
-- @param: max_total_ms = 120 ms — a name on a thread whose slices add up to
--       this much is a row too. A floor despite its name
-- @param: name_glob = *GC — GLOB over slice names: ART's collection cycles,
--       every one of which ends in GC
-- @param: name_glob_alt = waitWhileAllocating* — GLOB over slice names: a
--       thread of the app stopped in an allocation until the collector is done
-- @calibrate: max_events = top10(count) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @identity: location, detail
-- @column: location — the slice name: the kind of collection, or the
--       allocation wait
-- @column: count — its slices on that thread in the window
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: detail — the thread the slices are on: the collector's own,
--       `HeapTaskDaemon`, for a background cycle; a thread of the app for an
--       allocation wait or a collection it asked for
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

SELECT
    name                              AS location,
    COUNT(*)                          AS count,
    ROUND(SUM(dur) / 1e6, 2)          AS total_ms,
    ROUND(MAX(dur) / 1e6, 2)          AS max_ms,
    thread_name                       AS detail
FROM _slice_win
WHERE name GLOB '{{name_glob}}'
   OR name GLOB '{{name_glob_alt}}'
GROUP BY name, thread_name
HAVING COUNT(*) >= {{max_events}}
    OR SUM(dur) / 1e6 >= {{max_total_ms}}
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
--
-- The collector's slices a row counted, where they ran on the main thread:
-- `waitWhileAllocating` there is the app standing still for the collector.

SELECT s.ts, s.dur
FROM _slice_win s
JOIN _rows r ON r.location = s.name AND r.detail = s.thread_name
WHERE s.is_main_thread = 1;
