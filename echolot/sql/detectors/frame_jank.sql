-- @id: frame_jank
-- @title: Frames that missed their deadline
-- @why: the platform classifies every frame itself, and says whose fault it
--       was. The only detector that answers "which frames stuttered" rather
--       than "where did the total go" — and it needs no instrumentation in
--       the app at all.
-- @needs: frame_timeline
-- @param: min_frames = 3
-- @param: min_overrun_ms = 4
-- @calibrate: min_frames = top10(count) * 1.5
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
-- Two columns that do not mean what they mean elsewhere, and the reason:
--   total_ms  the time past the deadline, summed. What the jank cost.
--   max_ms    the worst single frame's overrun.
-- Frame duration itself is in `detail` — that is the number a benchmark's
-- percentiles are quoted in, and having both is what lets a report be matched
-- against a FrameTimingMetric run.
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
)
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
    END                                                  AS detail
FROM frames f
WHERE f.jank_tag != 'No Jank'
  AND f.over_ns >= {{min_overrun_ms}} * 1000000
GROUP BY f.jank_type, f.jank_tag
HAVING COUNT(*) >= {{min_frames}}
-- Late frames first, by what they cost; frames that only queued after them.
ORDER BY f.jank_tag = 'Buffer Stuffing', total_ms DESC
LIMIT 20;

-- @intervals
--
-- None of the main thread's own. A frame's lateness is measured on the
-- display's timeline, and what the main thread did inside a late frame is
-- its slices, which `main_thread_block` stands for where they are large
-- enough to be rows.

SELECT NULL AS ts, NULL AS dur LIMIT 0;
