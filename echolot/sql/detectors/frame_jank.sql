-- @id: frame_jank
-- @title: Frames that missed their deadline
-- @why: the platform classifies every frame itself, and says whose fault it
--       was. The only detector that answers "which frames stuttered" rather
--       than "where did the total go" — and it needs no instrumentation in
--       the app at all.
-- @param: min_frames = 3
-- @param: min_overrun_ms = 4
-- @calibrate: min_frames = top10(count) * 1.5
--
-- The other six aggregate by slice name and gate on accumulated sums. That is
-- the right shape for "cold start got slower" and the wrong one for a heavy
-- tail: one frame of 86 ms among thousands over 48 seconds disappears into
-- every sum there is. A benchmark reporting P50 14.7 ms and P99 86 ms was met
-- with silence from all six, and not because any of them was broken.
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
        a.dur - e.dur   AS over_ns
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
    ROUND(SUM(f.over_ns) / 1e6, 2)                       AS total_ms,
    ROUND(MAX(f.over_ns) / 1e6, 2)                       AS max_ms,
    f.jank_tag
        || ' · ' || COUNT(*) || ' of ' || (SELECT n FROM counted) || ' frames'
        || ' · longest ' || ROUND(MAX(f.actual_ns) / 1e6, 1) || ' ms'
                                                         AS detail
FROM frames f
WHERE f.jank_tag != 'No Jank'
  AND f.over_ns >= {{min_overrun_ms}} * 1000000
GROUP BY f.jank_type, f.jank_tag
HAVING COUNT(*) >= {{min_frames}}
ORDER BY total_ms DESC
LIMIT 20;
