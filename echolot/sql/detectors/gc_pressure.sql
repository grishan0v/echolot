-- @id: gc_pressure
-- @title: Garbage collector pressure
-- @why: frequent GC means many intermediate allocations; GC itself is the
--       symptom, not the cause
-- @param: max_events = 15
-- @param: max_total_ms = 120
-- @param: name_glob = *GC
-- @param: name_glob_alt = waitWhileAllocating*
-- @calibrate: max_events = top10(count) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @identity: location, detail
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
