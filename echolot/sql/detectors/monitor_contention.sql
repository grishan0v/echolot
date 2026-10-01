-- @id: monitor_contention
-- @title: Monitor contention
-- @why: ART writes a "Lock contention on ..." slice with the owner's tid —
--       ready-made evidence
-- @param: min_block_ms = 8
-- @param: max_total_ms = 50
-- @param: name_glob = Lock contention on a monitor lock*
-- @param: name_glob_alt = monitor contention with owner*
-- @calibrate: min_block_ms = top10(max_ms) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
--
-- Two conditions, like binder_txn and for the same reason. On the gameplay
-- scenario there were 200 blocks totalling 49.7 ms and 190 totalling 63.7 ms —
-- a quarter of a millisecond each, none clearing the 8 ms bar, and the
-- detector stayed silent. A thousand short waits stop a thread just as
-- effectively as one long one.
--
-- Masks narrowed against a live Android 14 trace. ART writes application-level
-- contention in exactly two shapes:
--   Lock contention on a monitor lock (owner tid: 13533)
--   monitor contention with owner main (13533) at void java.lang.Object.wait…
-- Everything else shaped like `Lock contention on <something> lock` is a
-- runtime-internal lock: ClassLinker classes, InternTable, linear alloc,
-- thread list, runtime shutdown. On one cold start ClassLinker classes alone
-- accounted for 129 of them. There is no application code behind those, and an
-- agent handed them as evidence goes hunting for something that isn't there.

--
-- One wait is often both shapes at once: ART opens `monitor contention with
-- owner …` and writes `Lock contention on a monitor lock …` inside it. On six
-- cold starts on an SM-A515F, 2,490 of 2,522 `Lock contention` slices sat
-- inside one, and summing both masks counted each of those waits twice, its
-- time with it. So a slice of the first mask inside one of the second is left
-- out: the outer one stays, because it names both frames — which `code`
-- places in the checkout — and the owner by name. One that stands alone is
-- a wait of its own, and counts.
SELECT
    s.thread_name                     AS location,
    COUNT(*)                          AS count,
    ROUND(SUM(s.dur) / 1e6, 2)        AS total_ms,
    ROUND(MAX(s.dur) / 1e6, 2)        AS max_ms,
    s.name                            AS detail
FROM _slice_win s
JOIN slice x ON x.id = s.slice_id
LEFT JOIN slice parent ON parent.id = x.parent_id
WHERE (s.name GLOB '{{name_glob}}' OR s.name GLOB '{{name_glob_alt}}')
  AND NOT (s.name GLOB '{{name_glob}}'
           AND COALESCE(parent.name GLOB '{{name_glob_alt}}', 0))
-- Grouped by thread, not by slice name. The owner's tid sits inside the name
-- ('owner tid: 13533'), so grouping by name shatters one finding into a dozen
-- rows, one per owner: on the gameplay scenario 190 blocks scattered so widely
-- that no group cleared the threshold. The detector's question is "who is
-- being blocked", not "by which particular lock".
--
-- detail carries the name of the LONGEST block: with a single min/max in the
-- query, SQLite takes the remaining columns from the record-holding row. That
-- keeps the owner's tid in the evidence, which is what the investigation
-- hooks onto.
GROUP BY s.thread_name
HAVING MAX(s.dur) >= {{min_block_ms}} * 1000000
    OR SUM(s.dur) >= {{max_total_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
--
-- The main thread's own waits for a monitor, when it is one of the rows.

SELECT s.ts, s.dur
FROM _slice_win s
WHERE s.is_main_thread = 1
  AND (s.name GLOB '{{name_glob}}' OR s.name GLOB '{{name_glob_alt}}')
  AND s.thread_name IN (SELECT location FROM _rows);
