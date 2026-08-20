-- @id: anr_risk
-- @title: Stretches where the main thread never got back to the message queue
-- @why: an ANR is not a slow method, it is a length of time in which nothing
--       was served. This measures that length directly, so it can name a
--       freeze the system has not declared yet — and it needs no ANR to have
--       happened, unlike the detector next to it.
-- @param: min_stall_ms = 5000
-- @param: max_gap_ms = 2
-- @identity: location
--
-- ## Why a sum cannot answer this
--
-- `main_thread_block` adds up the time under one slice name, and
-- `main_thread_outlier` compares one occurrence against the median for that
-- name. Both are about names. This is about a stretch: fifty different pieces
-- of work back to back are an ANR just as surely as one long one, and neither
-- of the other two would raise anything, because no single name accumulated
-- much. The question here is when the looper last went idle, not what it was
-- busy with.
--
-- ## What counts as "not making progress"
--
-- Two things, unioned, because either alone is blind in one direction.
--
-- Slices on the main thread say the looper is inside a message. A gap between
-- them is the looper idle and free to dispatch whatever is pending — an input
-- event, a broadcast — which is precisely what stops an ANR from firing.
--
-- Thread state covers the work no one instrumented. A main thread burning CPU
-- with no slice around it produces a gap in the first source while making no
-- progress at all. `Running` and the runnable states are progress-in-intent;
-- `D` is uninterruptible sleep, which is disk. Plain `S` is deliberately NOT
-- here: an idle looper sleeps in `S`, and so does a thread blocked on a
-- monitor, and only the slice around the second one tells them apart — which
-- the first source already does.
--
-- ## Depth zero is not evidence, and that is the whole trick
--
-- A scenario anchor is a marker opened at the start of a run and closed at
-- the end — `AppStart` around a cold start, and every `echolot mark` proposal
-- of that shape. It is a top-level slice covering the entire scenario,
-- including every idle moment in it, so counting depth 0 would report the
-- whole run as one unbroken stall on any project that has an anchor at all.
-- That is not a corner case; it is what this tool asks people to add.
--
-- So a slice counts from depth 1 down. What is left at depth 0 with nothing
-- under it is a marker holding a span open, and the thread states above
-- already say whether anything was happening inside it.
--
-- The cost is one blind spot, and it is small: an application with no
-- instrumentation whatsoever, blocked on a monitor, has ART's contention
-- slice at depth 0 and nothing under it. `monitor_contention` is the detector
-- for that, and it needs no instrumentation either.
--
-- ## The tolerance
--
-- `max_gap_ms` is small on purpose. If the looper reaches idle at all, a
-- pending event gets dispatched and the clock the system is watching resets.
-- Two milliseconds is enough to bridge the space between two messages without
-- bridging an actual idle moment.
--
-- ## The threshold is the platform's, and is not calibrated
--
-- Five seconds is the input dispatch timeout, ten and twenty are broadcasts,
-- twenty is a service. There is deliberately no `@calibrate` line: a bar
-- derived from healthy runs of this app would say what this app usually does,
-- and what matters is what the system will not tolerate. Lower it in the
-- config to hunt for stretches that are merely close.
--
-- On a cold-start scenario the window is a second or two, so nothing can be
-- five seconds long inside it and this stays silent by construction. It is
-- for the longer recording an ANR hunt collects.

WITH busy AS (
    -- Inside a message. Depth 1 and below; see above for why not 0.
    SELECT ts_win AS ts, ts_win + dur_win AS te
    FROM _slice_win
    WHERE is_main_thread = 1 AND depth >= 1 AND dur_win > 0

    UNION ALL

    -- On a CPU, waiting for one, or in the kernel. The state names are
    -- trace_processor's vocabulary rather than the kernel's.
    SELECT t.ts, t.ts + t.dur
    FROM _tstate_win t
    CROSS JOIN _proc p
    WHERE t.tid = p.pid
      AND t.state IN ('Running', 'R', 'R+', 'D', 'DK')
      AND t.dur > 0
),
-- The intervals overlap, so an island ends where the largest end seen so far
-- is left behind — comparing against the previous row's end alone would split
-- a run at every nested interval.
ordered AS (
    SELECT ts, te,
           MAX(te) OVER (ORDER BY ts, te
                         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS reached
    FROM busy
),
flagged AS (
    SELECT ts, te,
           CASE WHEN reached IS NULL OR ts - reached > {{max_gap_ms}} * 1000000
                THEN 1 ELSE 0 END AS opens
    FROM ordered
),
runs AS (
    SELECT ts, te,
           SUM(opens) OVER (ORDER BY ts, te ROWS UNBOUNDED PRECEDING) AS run
    FROM flagged
),
stalls AS (
    SELECT MIN(ts) AS ts_from, MAX(te) AS ts_to
    FROM runs
    GROUP BY run
    HAVING MAX(te) - MIN(ts) >= {{min_stall_ms}} * 1000000
),
described AS (
    SELECT
        s.ts_from,
        s.ts_to,
        s.ts_to - s.ts_from AS stall_ns,
        -- What to call it: the longest message inside the stretch. A stretch
        -- with no slices at all is uninstrumented work, and saying so is more
        -- use than leaving the row nameless.
        COALESCE((
            SELECT sl.name FROM _slice_win sl
            WHERE sl.is_main_thread = 1 AND sl.depth >= 1
              AND sl.ts_win < s.ts_to AND sl.ts_win + sl.dur_win > s.ts_from
            ORDER BY sl.dur_win DESC LIMIT 1
        ), 'uninstrumented work on the main thread') AS what,
        (
            SELECT COUNT(*) FROM _slice_win sl
            WHERE sl.is_main_thread = 1 AND sl.depth >= 1
              AND sl.ts_win < s.ts_to AND sl.ts_win + sl.dur_win > s.ts_from
        ) AS messages,
        (
            SELECT COALESCE(SUM(MIN(t.ts + t.dur, s.ts_to)
                                - MAX(t.ts, s.ts_from)), 0)
            FROM _tstate_win t CROSS JOIN _proc p
            WHERE t.tid = p.pid AND t.state = 'Running'
              AND t.ts < s.ts_to AND t.ts + t.dur > s.ts_from
        ) AS on_cpu_ns,
        (
            SELECT COALESCE(SUM(MIN(t.ts + t.dur, s.ts_to)
                                - MAX(t.ts, s.ts_from)), 0)
            FROM _tstate_win t CROSS JOIN _proc p
            WHERE t.tid = p.pid AND t.state IN ('R', 'R+')
              AND t.ts < s.ts_to AND t.ts + t.dur > s.ts_from
        ) AS runnable_ns
    FROM stalls s
)
SELECT
    what                                                  AS location,
    COUNT(*)                                              AS count,
    ROUND(SUM(stall_ns) / 1e6, 2)                         AS total_ms,
    ROUND(MAX(stall_ns) / 1e6, 2)                         AS max_ms,
    -- Taken from the longest stretch: with a single MAX in the query, SQLite
    -- reads the remaining columns off the record-holding row. The split says
    -- which of the other detectors to open next — time on CPU points at the
    -- code, time runnable at something else on the device, and the remainder
    -- at a lock or a disk.
    ROUND(on_cpu_ns / 1e6, 1) || ' ms on CPU · '
        || ROUND(runnable_ns / 1e6, 1) || ' ms waiting for one · '
        || ROUND((stall_ns - on_cpu_ns - runnable_ns) / 1e6, 1) || ' ms neither · '
        || messages || ' messages'                        AS detail
FROM described
GROUP BY what
ORDER BY total_ms DESC
LIMIT 20;
