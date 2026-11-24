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
--       and the slice the waiter was in. Then what the owner was doing over
--       all of the row's waits: `owner: running 60%, waiting for a CPU 25%,
--       in the kernel 10%, asleep 5%`
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
--
-- What the owner was doing while the thread waited decides the fix, and the
-- rows did not say it (#417). Running, the critical section is long: make it
-- shorter, or move work out from under the lock. Waiting for a CPU, it held
-- the lock in the queue for a core, and fewer threads at once is the fix, not
-- a shorter section. In the kernel or asleep, it waited for something else
-- while it held this: the disk, another lock, a reply. So every wait of a
-- row is split by the owner's thread state over its time, and the shares go
-- after the evidence. A wait whose owner the trace has no state for — not a
-- thread of the process, a name the masks were widened to — is `unknown`,
-- and a row with nothing else says nothing about the owner.
--
-- The waits are written down once, with the owner's tid out of either shape:
-- `owner tid: N` in the short one, and in the long one the number in the
-- parentheses before ` at ` or ` waiters=`, where a thread's name with
-- parentheses of its own cannot move it.
CREATE OR REPLACE PERFETTO TABLE _det_monitor_contention AS
WITH waits AS (
    SELECT
        CASE WHEN s.is_main_thread = 1 THEN 'main'
             ELSE COALESCE(f.family, s.thread_name) END
                                          AS thread_name,
        s.is_main_thread,
        s.utid,
        s.tid,
        s.ts,
        s.dur,
        s.ts_win,
        s.dur_win,
        s.name,
        s.name GLOB '{{name_glob_alt}}'   AS named,
        parent.name                       AS parent_name,
        -- The long shape past `monitor contention with owner `.
        SUBSTR(s.name, 31)                AS rest
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
-- The long shape up to the tid's closing parenthesis: `Thread-3 (4455`.
owned AS (
    SELECT *,
           SUBSTR(rest, 1,
                  CASE WHEN INSTR(rest, ') at ') BETWEEN 1 AND INSTR(rest, ' waiters=')
                       THEN INSTR(rest, ') at ')
                       ELSE INSTR(rest, ') waiters=') END - 1) AS head
    FROM waits
)
SELECT thread_name, is_main_thread, utid, tid, ts, dur, ts_win, dur_win,
       name, named, parent_name,
       CASE
           WHEN INSTR(name, 'owner tid: ') > 0
               THEN CAST(SUBSTR(name, INSTR(name, 'owner tid: ') + 11) AS INT)
           WHEN name GLOB 'monitor contention with owner *'
               THEN CAST(NULLIF(SUBSTR(head, LENGTH(RTRIM(head, '0123456789')) + 1), '')
                         AS INT)
       END                                AS owner_tid
FROM owned;

-- The waits inside the window, one partition of the join below for each
-- thread that waited and thread that held the lock. Two threads wait for
-- one owner at the same moment as a matter of course (`waiters=1`), and
-- SPAN_JOIN needs no overlap inside a partition: fed overlap, it drops part
-- of the second wait without a word. One thread waits for one lock at a
-- time, and a pid fits in 22 bits, so the pair is one number.
CREATE OR REPLACE PERFETTO TABLE _det_monitor_contention_wait AS
SELECT utid * 4194304 + owner_tid        AS pair,
       owner_tid,
       ts_win                            AS ts,
       dur_win                           AS dur,
       thread_name                       AS location
FROM _det_monitor_contention
WHERE owner_tid IS NOT NULL AND dur_win > 0;

-- The owner's states over the stretch of each pair's waits. A tid the
-- system gave out again belongs to two threads one after the other, whose
-- states do not overlap either.
CREATE OR REPLACE PERFETTO TABLE _det_monitor_contention_owner AS
WITH pairs AS (
    SELECT pair, owner_tid, MIN(ts) AS since, MAX(ts + dur) AS until
    FROM _det_monitor_contention_wait
    GROUP BY pair
)
SELECT p.pair, t.ts, t.dur, t.state
FROM _tstate_win t
CROSS JOIN pairs p
WHERE t.tid = p.owner_tid AND t.dur > 0
  AND t.ts < p.until AND t.ts + t.dur > p.since;

DROP TABLE IF EXISTS _det_monitor_contention_held;
CREATE VIRTUAL TABLE _det_monitor_contention_held
USING SPAN_JOIN(_det_monitor_contention_wait PARTITIONED pair,
                _det_monitor_contention_owner PARTITIONED pair);

-- The wait that speaks for the row (#510). The longest one used to, and
-- ART writes some waits in the short shape alone, owner tid and nothing
-- else: on a real cold start a thread's longest wait, 890 ms, was one,
-- while its other waits named the owner's method and its own. So the
-- longest of the long shape speaks when the row has one; `max_ms` stays
-- the longest wait of all.
WITH evidence AS (
    SELECT thread_name, ts, name, parent_name, owner_tid,
           ROW_NUMBER() OVER (PARTITION BY thread_name
                              ORDER BY named DESC, dur DESC, ts) AS pick
    FROM _det_monitor_contention
),
-- A row with the short shape alone still has what the trace knows about the
-- moment: the owner's tid is the app's thread whose name the trace has, the
-- slice open on that thread is what it was doing while it held the lock,
-- and the slice the wait sits in is where the waiter was.
told AS (
    SELECT e.thread_name, e.name, e.parent_name, e.owner_tid, e.ts
    FROM evidence e
    WHERE e.pick = 1
),
-- The owner's states over the row's waits, against the time they waited
-- inside the window. Whatever no state covers is `unknown`.
held AS (
    SELECT w.thread_name                                           AS location,
           w.ns,
           TOTAL(CASE WHEN h.state = 'Running' THEN h.dur END)     AS running,
           TOTAL(CASE WHEN h.state IN ('R', 'R+') THEN h.dur END)  AS queued,
           TOTAL(CASE WHEN h.state IN ('D', 'DK') THEN h.dur END)  AS kernel,
           TOTAL(CASE WHEN h.state = 'S' THEN h.dur END)           AS asleep
    FROM (SELECT thread_name, SUM(dur_win) AS ns
          FROM _det_monitor_contention
          WHERE dur_win > 0
          GROUP BY thread_name) w
    LEFT JOIN _det_monitor_contention_held h ON h.location = w.thread_name
    GROUP BY w.thread_name
),
said AS (
    SELECT location,
           'owner: ' || SUBSTR(
               CASE WHEN ROUND(100.0 * running / ns) >= 1
                    THEN ', running ' || CAST(ROUND(100.0 * running / ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * queued / ns) >= 1
                    THEN ', waiting for a CPU ' || CAST(ROUND(100.0 * queued / ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * kernel / ns) >= 1
                    THEN ', in the kernel ' || CAST(ROUND(100.0 * kernel / ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * asleep / ns) >= 1
                    THEN ', asleep ' || CAST(ROUND(100.0 * asleep / ns) AS INT) || '%'
                    ELSE '' END
            || CASE WHEN ROUND(100.0 * (ns - running - queued - kernel - asleep) / ns) >= 1
                    THEN ', unknown '
                         || CAST(ROUND(100.0 * (ns - running - queued - kernel - asleep) / ns)
                                 AS INT) || '%'
                    ELSE '' END, 3)                                AS owner
    FROM held
    WHERE ROUND(100.0 * (running + queued + kernel + asleep) / ns) >= 1
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
    END
    || COALESCE(' · ' || o.owner, '') AS detail
FROM _det_monitor_contention w
JOIN told t ON t.thread_name = w.thread_name
LEFT JOIN said o ON o.location = w.thread_name
-- Grouped by thread, not by slice name. The owner's tid sits inside the name
-- ('owner tid: 13533'), so grouping by name shatters one finding into a dozen
-- rows, one per owner: on the gameplay scenario 190 blocks scattered so widely
-- that no group cleared the threshold. The detector's question is "who is
-- being blocked", not "by which particular lock".
GROUP BY w.thread_name
ORDER BY total_ms DESC;

-- @intervals
--
-- The main thread's own waits for a monitor, when it is one of the rows, as
-- the first query wrote them down: a wait ART writes as two nested slices
-- once, clipped to the window.

SELECT ts_win AS ts, dur_win AS dur
FROM _det_monitor_contention
WHERE is_main_thread = 1 AND dur_win > 0
  AND 'main' IN (SELECT location FROM _rows);
