-- @id: frame_jank
-- @title: Frames that missed their deadline
-- @why: the platform classifies every frame itself, and says whose fault it
--       was. The only detector that answers "which frames stuttered" rather
--       than "where did the total go" — and it needs no instrumentation in
--       the app at all.
-- @needs: frame_timeline
-- @param: min_frames = 3 frames — late frames of one verdict in the window,
--       Self Jank or Other Jank, all its types together, below which none of
--       that verdict's rows is reported
-- @param: min_overrun_ms = 4 ms — how far past its deadline a frame has to
--       finish to count as late at all
-- @fires: verdict_frames >= {{min_frames}}
-- @shape: min_overrun_ms
-- @calibrate: min_frames = top10(count) * 1.5
-- @column: location — why the frames missed, the platform's jank_type: a
--       comma-joined set, `App Deadline Missed, Buffer Stuffing` being one
-- @column: count — frames of that type in the window
-- @column: total_ms — bends the contract: their time past the deadline,
--       summed, which is what the jank cost; 0 on a Buffer Stuffing row
-- @column: max_ms — bends the contract: the worst frame's overrun past its
--       deadline, not its duration, which is `longest` in detail; 0 on a
--       Buffer Stuffing row
-- @column: detail — whose fault (`Self Jank` the app's, `Other Jank` the
--       system's), how many of all the app's frames, and the longest frame's
--       own duration — the number a benchmark's percentiles are quoted in. A
--       Buffer Stuffing row leads with `latency, not jank` instead
-- @column: verdict_frames — the late frames of its verdict in the window,
--       `Self Jank` or `Other Jank`, all its types together: what
--       `min_frames` is held against
-- @plain_title: Frames that came late
-- @plain_why: The screen is redrawn many times a second. A frame that is not
--       ready in time makes the picture stutter.
-- @plain_clean: Hardly any frame came late.
--
-- Most of the others aggregate by slice name and gate on accumulated sums. That
-- is the right shape for "cold start got slower" and the wrong one for a heavy
-- tail: one frame of 86 ms among thousands over 48 seconds disappears into
-- every sum there is. A benchmark reporting P50 14.7 ms and P99 86 ms was met
-- with silence from all six detectors of the time, and not because any of them
-- was broken.
--
-- SurfaceFlinger already knows. Since Android 12 the frame timeline records,
-- per frame, the deadline it was given and what it actually took, with a
-- classification of why it missed. That is a fact from the platform rather
-- than a threshold of ours, and it exists whether or not anyone wrote a
-- single trace{} call.
--
-- Requires `android.surfaceflinger.frametimeline` in the trace config —
-- `echolot collect` asks for it. Older traces and Android 11 and below leave
-- these tables empty, and an empty table is silence rather than an error.
--
-- The timeline keeps the frames that carry a Choreographer vsync id: the
-- app's windows, drawn by its RenderThread. A map, a game or a video a
-- thread of the app draws into a SurfaceView with Vulkan or GL carries none,
-- and none of its frames is here. In a pan-and-zoom of a map, 1000 presents
-- reached the screen that way beside the 321 frames this judged (#1011).
-- Judging those from their presents was looked at and left: nothing in the
-- trace gives them a deadline, and the app draws them only while something
-- moves, so the gap between two is as often a pause between gestures (190 ms
-- apart at rest, seconds between swipes) as a frame that came late. What
-- this cannot see, `analyze` counts beside it (`window.frames.unjudged`) and
-- the report says.
--
-- total_ms and max_ms do not mean what they mean elsewhere, as their
-- `@column` lines say: the loss is the time past the deadline, and that is
-- the actionable number. The frame's own duration is in `detail`, since a
-- benchmark's percentiles are quoted in it, and having both is what lets a
-- report be matched against a FrameTimingMetric run.
--
-- Rows are grouped by jank_type, which trace_processor renders as a
-- comma-joined set: 'App Deadline Missed, Buffer Stuffing' is one diagnosis
-- and gets one row, not two. `detail` leads with jank_tag, the platform's own
-- verdict on whose deadline was missed: 'Self Jank' is the app, 'Other Jank'
-- is the system and is not something to send anyone into the code over.
--
-- Buffer Stuffing is not a missed frame, and its row says so (#537). The
-- platform tags a frame that way, and with nothing else, when the app queued
-- it while the frame before was still waiting to be shown: it reaches the
-- screen a vsync or more after its deadline, smoothly, behind that frame.
-- Latency, not stutter. Summed with the rest, on a scroll where the render
-- thread took most of every frame, it was 1263 of 1446 frames and 16 s "past
-- the deadline" in 40 s: the first row, and a level that needed attention,
-- above the 28 frames the app really missed. So the row keeps its frames and
-- counts nothing past a deadline — total_ms and max_ms are 0, it ranks after
-- every late frame, `compare` has no summed delay to call a move — and its
-- evidence leads with `latency, not jank` and says how far behind they were.
-- A frame that also missed a deadline is tagged by that: 'App Deadline
-- Missed, Buffer Stuffing' is Self Jank and stays a late frame. Its overrun
-- includes the wait in the queue, which nothing in the timeline separates,
-- and its evidence says so.
--
-- `min_frames` is a number of late frames, and it is held against all the
-- frames of one verdict in the window, not against each jank_type: a type is
-- a set of reasons, and one bad frame often has a set of its own (#726).
-- Two frames late are still an anecdote; three the app was late for, each
-- for a different mix of reasons, are a finding, and each type keeps its
-- row.
--
-- jank_tag joins that GROUP BY and still does not belong in @identity: it is
-- a verdict *on* the jank_type, one per type, so the pair names no more rows
-- than the type alone. And `detail` carries per-frame numbers around it, so
-- naming rows by it would put a row's own measurements into its name and no
-- two repeats would ever be the same row.

WITH win AS (
    SELECT ts_start, ts_end FROM _window
),
frames AS (
    SELECT
        a.jank_type,
        a.jank_tag,
        a.dur           AS actual_ns,
        -- A frame whose expected slice is missing cannot be measured against
        -- a deadline it does not have. It still counts in the denominator
        -- below; it just cannot be the evidence for anything.
        --
        -- The deadline is where the expected frame ends, so a frame that
        -- started late carries its late start into the overrun. Actual minus
        -- expected duration lost it: totals came out low, and a frame the
        -- platform called late could fall under min_overrun_ms.
        (a.ts + a.dur) - (e.ts + e.dur)   AS over_ns
    FROM actual_frame_timeline_slice a
    JOIN _proc p ON p.upid = a.upid
    LEFT JOIN expected_frame_timeline_slice e
           ON e.surface_frame_token = a.surface_frame_token
          AND e.upid = a.upid
    CROSS JOIN win w
    -- Display frames live in the same table with a null surface token and
    -- belong to surfaceflinger. The join on _proc already excludes them; this
    -- says so out loud, so pointing the config at surfaceflinger does not
    -- quietly change what the detector counts.
    WHERE a.surface_frame_token IS NOT NULL
      AND a.dur > 0
      AND a.ts < w.ts_end
      AND a.ts + a.dur > w.ts_start
),
counted AS (
    SELECT COUNT(*) AS n FROM frames
),
kinds AS (
SELECT
    f.jank_type                                          AS location,
    COUNT(*)                                             AS count,
    CASE WHEN f.jank_tag = 'Buffer Stuffing' THEN 0.0
         ELSE ROUND(SUM(f.over_ns) / 1e6, 2) END         AS total_ms,
    CASE WHEN f.jank_tag = 'Buffer Stuffing' THEN 0.0
         ELSE ROUND(MAX(f.over_ns) / 1e6, 2) END         AS max_ms,
    CASE WHEN f.jank_tag = 'Buffer Stuffing' THEN
        'latency, not jank · ' || COUNT(*) || ' of ' || (SELECT n FROM counted)
        || ' frames queued behind the one before · up to '
        || ROUND(MAX(f.over_ns) / 1e6, 1) || ' ms late'
    ELSE
        f.jank_tag
        || ' · ' || COUNT(*) || ' of ' || (SELECT n FROM counted) || ' frames'
        || ' · longest ' || ROUND(MAX(f.actual_ns) / 1e6, 1) || ' ms'
        || CASE WHEN f.jank_type LIKE '%Buffer Stuffing%'
                THEN ' · the overrun includes the wait in the queue' ELSE '' END
    END                                                  AS detail,
    f.jank_tag = 'Buffer Stuffing'                       AS queued,
    -- The bar counts the late frames of one verdict together, whatever
    -- their reasons. Counted per jank_type, a cold start of three frames,
    -- all three the app's and hundreds of ms late, made three rows of one
    -- frame each, since 'App Deadline Missed', 'App Deadline Missed, Buffer
    -- Stuffing' and 'Display HAL, App Deadline Missed' are three types; and
    -- the detector said nothing (#726). Buffer Stuffing is its own verdict
    -- and stays apart, as #537 has it.
    SUM(COUNT(*)) OVER (PARTITION BY f.jank_tag)         AS verdict_frames
FROM frames f
WHERE f.jank_tag != 'No Jank'
  AND f.over_ns >= {{min_overrun_ms}} * 1000000
GROUP BY f.jank_type, f.jank_tag
)
SELECT location, count, total_ms, max_ms, detail, verdict_frames
FROM kinds
-- Late frames first, by what they cost; frames that only queued after them.
ORDER BY queued, total_ms DESC;

-- @intervals
--
-- None of the main thread's own. A frame's lateness is measured on the
-- display's timeline, and what the main thread did inside a late frame is
-- its slices, which `main_thread_block` stands for where they are large
-- enough to be rows.

SELECT NULL AS ts, NULL AS dur LIMIT 0;
