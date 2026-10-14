-- @id: composables
-- @title: The app's composables, by the composition time they hold
-- @why: with runtime-tracing every composable call is a slice of its own, and
--       none reaches another detector: a composable's self time is a
--       millisecond, the rest being the library's composables under it. So it
--       is counted here with what it calls, down to the next composable of the
--       app's own
-- @param: min_self_ms = 8
-- @param: name_glob = * (*.kt:*)
-- @calibrate: min_self_ms = top10(self_ms) * 1.5
-- @identity: location
--
-- `androidx.compose.runtime:runtime-tracing` names every composable call, on
-- the main thread, `fully.qualified.Name (File.kt:line)`, nested under the
-- `Compose:recompose` that composed it. On a real cold start that was 922
-- calls of 177 names, 49 of them the app's own, and not one in the report:
-- summed by name, none had the 16 ms of self time a `main_thread_block` row
-- needs. The app's own had about a millisecond each. Their time was in the
-- library's composables under them — `material3`, `foundation` — which are
-- where the time goes and not where the project can change anything.
--
-- So a row is a composable of the app's own, and `self_ms` BENDS the
-- contract: it is the time of its calls less the calls of the app's own
-- composables nested in them, the library's staying in. On that start the
-- navigation host held 29 ms of its 42, an icon painter called seven times
-- 19 ms. Like self time, it adds up across the rows without counting
-- anything twice. `total_ms` is the calls end to end, the nested ones
-- included, and `max_ms` the longest call.
--
-- Which composables are the app's own is the checkout's to say, and the
-- trace's names do not: `_compose_own` is filled before the detectors run,
-- from the project's package and the files the config's directory holds
-- (`place.own_composables`). A trace without runtime-tracing has no
-- composable in it, and this detector says nothing.
--
-- `detail` says what composed the calls, read off the parent of the
-- `Compose:recompose` around each: `in a frame`, the frame's recomposition
-- (`Recomposer:recompose`); `during measure`, a lazy list or another
-- subcomposing layout composing its items as it measured
-- (`AndroidOwner:onMeasure`); `elsewhere`, the rest, most often a
-- `ComposeView`'s first composition. A count alone cannot tell recomposing
-- from composing many: seven calls of the icon painter were seven icons.

WITH calls AS MATERIALIZED (
    SELECT s.slice_id, s.name, s.dur
    FROM _slice_win s
    JOIN _compose_own o ON o.name = s.name
    WHERE s.is_main_thread = 1
      AND s.name GLOB '{{name_glob}}'
),
nest AS MATERIALIZED (
    SELECT c.slice_id,
           -- The nearest call of the app's own that this one runs inside:
           -- its time is that call's, less this one's.
           (SELECT a.id FROM ancestor_slice(c.slice_id) a
            JOIN calls p ON p.slice_id = a.id
            ORDER BY a.depth DESC LIMIT 1)                         AS outer_id,
           -- What composed it: whatever the nearest pass sits under.
           (SELECT pp.name FROM ancestor_slice(c.slice_id) a
            JOIN slice pp ON pp.id = a.parent_id
            WHERE a.name = 'Compose:recompose'
            ORDER BY a.depth DESC LIMIT 1)                         AS pass_parent
    FROM calls c
),
inner_time AS (
    SELECT n.outer_id AS slice_id, SUM(c.dur) AS ns
    FROM nest n
    JOIN calls c USING (slice_id)
    WHERE n.outer_id IS NOT NULL
    GROUP BY n.outer_id
),
grouped AS (
    SELECT c.name,
           COUNT(*)                                                AS count,
           SUM(c.dur)                                              AS total_ns,
           SUM(c.dur - COALESCE(i.ns, 0))                          AS self_ns,
           MAX(c.dur)                                              AS max_ns,
           SUM(n.pass_parent = 'Recomposer:recompose')             AS in_frame,
           SUM(n.pass_parent GLOB 'AndroidOwner:*')                AS in_measure,
           SUM(n.pass_parent IS NULL
               OR (n.pass_parent != 'Recomposer:recompose'
                   AND n.pass_parent NOT GLOB 'AndroidOwner:*'))   AS elsewhere
    FROM calls c
    JOIN nest n USING (slice_id)
    LEFT JOIN inner_time i USING (slice_id)
    GROUP BY c.name
)
SELECT
    name                                                     AS location,
    count,
    ROUND(total_ns / 1e6, 2)                                 AS total_ms,
    ROUND(self_ns / 1e6, 2)                                  AS self_ms,
    ROUND(max_ns / 1e6, 2)                                   AS max_ms,
    -- ' · ' joins what is there; the first three characters drop the lead.
    SUBSTR(CASE WHEN in_frame > 0 THEN ' · ' || in_frame || ' in a frame' ELSE '' END
        || CASE WHEN in_measure > 0 THEN ' · ' || in_measure || ' during measure' ELSE '' END
        || CASE WHEN elsewhere > 0 THEN ' · ' || elsewhere || ' elsewhere' ELSE '' END, 4)
                                                             AS detail
FROM grouped
WHERE self_ns >= {{min_self_ms}} * 1000000
ORDER BY self_ms DESC, location
LIMIT 20;

-- @intervals
--
-- A row stands for what `self_ms` counts: each of its calls, less the calls
-- of the app's own nested in it, which are their own rows' or under the bar.
-- The gaps before, between and after those, found by ordering them. They do
-- not overlap one another — one nested in another would have that one for
-- its nearest — so the gaps are the call's own time exactly.

WITH calls AS MATERIALIZED (
    SELECT s.slice_id, s.name, s.ts, s.dur
    FROM _slice_win s
    JOIN _compose_own o ON o.name = s.name
    WHERE s.is_main_thread = 1
      AND s.name GLOB '{{name_glob}}'
),
nest AS MATERIALIZED (
    SELECT c.slice_id, c.ts, c.dur,
           (SELECT a.id FROM ancestor_slice(c.slice_id) a
            JOIN calls p ON p.slice_id = a.id
            ORDER BY a.depth DESC LIMIT 1)                         AS outer_id
    FROM calls c
),
named AS MATERIALIZED (
    SELECT c.slice_id, c.ts, c.ts + c.dur AS te
    FROM calls c
    JOIN _rows r ON r.location = c.name
),
kids AS MATERIALIZED (
    SELECT n.outer_id AS slice_id, n.ts AS cs, n.ts + n.dur AS ce,
           LAG(n.ts + n.dur) OVER (PARTITION BY n.outer_id ORDER BY n.ts) AS prev_end
    FROM nest n
    JOIN named p ON p.slice_id = n.outer_id
)
SELECT COALESCE(k.prev_end, p.ts) AS ts, k.cs - COALESCE(k.prev_end, p.ts) AS dur
FROM kids k
JOIN named p USING (slice_id)
UNION ALL
SELECT COALESCE(MAX(k.ce), p.ts) AS ts, p.te - COALESCE(MAX(k.ce), p.ts) AS dur
FROM named p
LEFT JOIN kids k USING (slice_id)
GROUP BY p.slice_id;
