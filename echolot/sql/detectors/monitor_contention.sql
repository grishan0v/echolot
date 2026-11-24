-- @id: monitor_contention
-- @title: Monitor contention
-- @why: ART writes a "Lock contention on ..." slice with the owner's tid —
--       ready-made evidence
-- @needs: atrace:dalvik
-- @param: min_block_ms = 8 ms — a thread with one wait this long is a row
-- @param: max_total_ms = 50 ms — a thread whose waits add up to this much is
--       a row too, with none of them long. A floor despite its name
-- @param: name_glob = Lock contention on a monitor lock* — GLOB over slice
--       names: ART's wait on a monitor, with the owner's tid
-- @param: name_glob_alt = monitor contention with owner* — GLOB over slice
--       names: the same wait with both methods and the owner by name; a
--       slice of the first mask inside one of these counts once, as this one
-- @fires: max_ms >= {{min_block_ms}} OR total_ms >= {{max_total_ms}}
-- @calibrate: min_block_ms = top10(max_ms) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @column: location — the thread that waited for the lock; `main` for the
--       main thread, told apart by its tid
-- @column: threads — how many threads of that name the row adds up: the
--       kernel cuts a name to fifteen characters, and a pool's workers
--       share one. Their time together is not the window's
-- @column: count — its waits in the window
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: detail — its longest wait that names both methods, as ART wrote
--       it; or else the owner's tid with its thread and the slice it was in,
--       and the slice the waiter was in
-- @plain_title: Threads waiting for each other
-- @plain_why: Threads protect what they share with a lock, so that only one
--       uses it at a time. A thread that needs it while another holds it
--       stands still until it is free.
-- @plain_clean: No thread waited long for another thread to let go of
--       something they share.
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
--
-- A row is a thread name, and the main thread is called `main` (#511). The
-- kernel cuts a name to fifteen characters, so every coroutine worker is
-- `DefaultDispatch` and its row adds up the pool: on a large app's cold start
-- 3.8 s of waiting in a 5.7 s window, read as time of the start until a
-- reader counted the threads. `threads` counts them. The main thread carries
-- the last fifteen characters of the process's name, which nobody reads as
-- the main thread — and a worker that never named itself carries them too, so
-- the main thread is told apart by its tid, the pid, and not by its name.
WITH waits AS (
    SELECT
        CASE WHEN s.is_main_thread = 1 THEN 'main'
             ELSE COALESCE(f.family, s.thread_name) END
                                          AS thread_name,
        s.tid,
        s.ts,
        s.dur,
        s.name,
        s.name GLOB '{{name_glob_alt}}'   AS named,
        parent.name                       AS parent_name
    FROM _slice_win s
    JOIN slice x ON x.id = s.slice_id
    LEFT JOIN slice parent ON parent.id = x.parent_id
    -- A thread's name under the fold every detector groups by: a pool's
    -- workers are one row, and `threads` counts them all (#831, #845).
    LEFT JOIN _name_family f ON f.name = s.thread_name
    WHERE (s.name GLOB '{{name_glob}}' OR s.name GLOB '{{name_glob_alt}}')
      AND NOT (s.name GLOB '{{name_glob}}'
               AND COALESCE(parent.name GLOB '{{name_glob_alt}}', 0))
),
-- The wait that speaks for the row (#510). The longest one used to, and
-- ART writes some waits in the short shape alone, owner tid and nothing
-- else: on a real cold start a thread's longest wait, 890 ms, was one,
-- while its other waits named the owner's method and its own. So the
-- longest of the long shape speaks when the row has one; `max_ms` stays
-- the longest wait of all.
evidence AS (
    SELECT thread_name, ts, name, parent_name,
           ROW_NUMBER() OVER (PARTITION BY thread_name
                              ORDER BY named DESC, dur DESC, ts) AS pick
    FROM waits
),
-- A row with the short shape alone still has what the trace knows about the
-- moment: the owner's tid is the app's thread whose name the trace has, the
-- slice open on that thread is what it was doing while it held the lock,
-- and the slice the wait sits in is where the waiter was.
told AS (
    SELECT e.thread_name, e.name, e.parent_name,
           CAST(SUBSTR(e.name, INSTR(e.name, 'owner tid: ') + 11) AS INT) AS owner_tid,
           e.ts
    FROM evidence e
    WHERE e.pick = 1
)
SELECT
    w.thread_name                     AS location,
    COUNT(DISTINCT w.tid)             AS threads,
    COUNT(*)                          AS count,
    ROUND(SUM(w.dur) / 1e6, 2)        AS total_ms,
    ROUND(MAX(w.dur) / 1e6, 2)        AS max_ms,
    CASE
        WHEN INSTR(t.name, 'owner tid: ') = 0
            THEN t.name
        ELSE t.name
             || COALESCE(' · owner ' || (
                    SELECT th.name FROM thread th JOIN _proc p ON th.upid = p.upid
                    WHERE th.tid = t.owner_tid LIMIT 1), '')
             || COALESCE(' in ' || (
                    SELECT o.name FROM _slice o
                    WHERE o.tid = t.owner_tid AND o.ts <= t.ts
                      AND (o.dur < 0 OR o.ts + o.dur > t.ts)
                    ORDER BY o.depth DESC LIMIT 1), '')
             || COALESCE(' · waiting in ' || t.parent_name, '')
    END                               AS detail
FROM waits w
JOIN told t ON t.thread_name = w.thread_name
-- Grouped by thread, not by slice name. The owner's tid sits inside the name
-- ('owner tid: 13533'), so grouping by name shatters one finding into a dozen
-- rows, one per owner: on the gameplay scenario 190 blocks scattered so widely
-- that no group cleared the threshold. The detector's question is "who is
-- being blocked", not "by which particular lock".
GROUP BY w.thread_name
ORDER BY total_ms DESC;

-- @intervals
--
-- The main thread's own waits for a monitor, when it is one of the rows.

SELECT s.ts, s.dur
FROM _slice_win s
WHERE s.is_main_thread = 1
  AND (s.name GLOB '{{name_glob}}' OR s.name GLOB '{{name_glob_alt}}')
  AND 'main' IN (SELECT location FROM _rows);
