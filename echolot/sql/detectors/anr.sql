-- @id: anr
-- @title: ANRs the system recorded during this trace
-- @why: the platform decides what an ANR is and writes down that it happened,
--       with its reason. Nothing here is inferred — this is the record, and
--       it is the only detector that can say a freeze actually fired rather
--       than that one was close.
-- @module: android.anrs
-- @identity: location, detail
--
-- Written by system_server, not by the app: when ActivityManager declares an
-- ANR it emits counter tracks named `ErrorId:<process> <pid>#<uuid>` and
-- `Subject(for ErrorId <uuid>):<subject>` under the ActivityManager trace tag.
-- That tag is the `am` atrace category, which `echolot collect` asks for by
-- default, so a trace recorded by this tool already carries them — if a freeze
-- fired while it was recording.
--
-- The stdlib module reads those counters and the ANR timer's `expired(...)`
-- slices. On a platform without that timer `anr_type` and `anr_dur_ms` come
-- back empty; an empty column is a column with nothing in it, not a failure.
-- On a trace with no ANR at all the table is empty and this detector is
-- silent, which is the same shape `frame_jank` has on Android 11.
--
-- ## Why this one ignores the scenario window
--
-- Every other detector is clipped to it, and this one must not be. An ANR
-- fires five seconds after the event that could not be served, and a cold
-- start's window closes at the first frame — usually a second or two in. A
-- record clipped to that window would be absent from almost every trace that
-- contains one, and absence here reads as "no freeze", which is the one thing
-- this detector exists to contradict.
--
-- So it looks at the whole trace and says in `detail` where the record fell
-- relative to the window. A reader who needs "was it inside the scenario" has
-- the number; a reader who needs "did it happen at all" is not lied to.
--
-- ## The process
--
-- `_proc` is the target process, and the module attributes each ANR to the
-- process that hung. `upid` is the reliable join when the trace saw that
-- process; `pid` covers a record whose process the trace never resolved into
-- a upid, which happens when the app died before process_stats ran.

WITH win AS (
    SELECT ts_start, ts_end FROM _window
),
ours AS (
    SELECT
        a.error_id,
        a.ts,
        COALESCE(NULLIF(a.subject, ''), a.anr_type, 'ANR')  AS subject,
        a.anr_dur_ms
    FROM android_anrs a
    CROSS JOIN _proc p
    WHERE a.upid = p.upid OR a.pid = p.pid
)
SELECT
    o.subject                                            AS location,
    COUNT(*)                                             AS count,
    ROUND(MAX(COALESCE(o.anr_dur_ms, 0)), 2)             AS total_ms,
    ROUND(MAX(COALESCE(o.anr_dur_ms, 0)), 2)             AS max_ms,
    -- Where it fell against the scenario, and the id the platform gave it —
    -- the same string the device's own record carries, so a report and a
    -- `dumpsys dropbox` entry can be matched to each other by hand.
    CASE
        WHEN o.ts < (SELECT ts_start FROM win) THEN
            'before the window by ' ||
            ROUND(((SELECT ts_start FROM win) - o.ts) / 1e6, 1) || ' ms'
        WHEN o.ts > (SELECT ts_end FROM win) THEN
            'after the window by ' ||
            ROUND((o.ts - (SELECT ts_end FROM win)) / 1e6, 1) || ' ms'
        ELSE
            'inside the window at +' ||
            ROUND((o.ts - (SELECT ts_start FROM win)) / 1e6, 1) || ' ms'
    END || ' · ' || o.error_id                            AS detail
FROM ours o
GROUP BY o.subject, detail
ORDER BY o.ts
LIMIT 20;
