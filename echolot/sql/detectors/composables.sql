-- @id: composables
-- @title: The app's composables, by the composition time they hold
-- @why: with runtime-tracing every composable call is a slice of its own, and
--       none reaches another detector: a composable's self time is a
--       millisecond, the rest being the library's composables under it. So it
--       is counted here with what it calls, down to the next composable of the
--       app's own
-- @needs: composable_names
-- @param: min_self_ms = 8 ms — the composition time a composable of the
--       app's own has to hold, as self_ms below, to be a row
-- @param: name_glob = * ([A-Za-z]*.kt:[0-9]*) — GLOB over slice names: a
--       composable call as runtime-tracing names it, `Name (File.kt:line)`
-- @fires: self_ms >= {{min_self_ms}}
-- @calibrate: min_self_ms = top10(self_ms) * 1.5
-- @identity: location
-- @column: location — the composable, as runtime-tracing names it: its
--       qualified name and the file and line it is declared at
-- @column: count — its calls in the window; not recompositions, since seven
--       calls can be seven icons
-- @column: total_ms — its calls end to end, the app's own composables nested
--       in them included
-- @column: self_ms — bends the contract: its calls less the app's own
--       composables nested in them, the library's composables under it
--       counted in. Adds up across the rows without counting a moment twice
-- @column: max_ms — its longest call
-- @column: detail — what composed the calls, with how many each: `in a first
--       composition`, `in a frame` (a recomposition), `during measure` (a
--       lazy list composing its items), `elsewhere`
-- @plain_title: Parts of the screen that take long to build
-- @plain_why: When a screen is built, some of its parts can take much longer
--       than the rest. This shows which of the app's own parts take the time.
-- @plain_clean: None of the app's own screen parts took long to build.
--
-- `androidx.compose.runtime:runtime-tracing` names every composable call, on
-- the main thread, `fully.qualified.Name (File.kt:line)`. The mask wants a
-- letter after ` (`: ART's contention slice also ends in `(File.kt:61)`, and
-- what follows its ` (` is a thread id. The calls are nested under the
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
-- anything twice. The other columns keep their meaning; the `@column` lines
-- above say each.
--
-- Which composables are the app's own is the checkout's to say, and the
-- trace's names do not: `_compose_own` is filled before the detectors run,
-- from the project's package and the files the config's directory holds
-- (`place.own_composables`). A trace without runtime-tracing has no
-- composable in it, and this detector says nothing.
--
-- `detail` says what composed the calls, read off the parent of the
-- `Compose:recompose` around each:
-- - `in a first composition`, a view composed for the first time. Under
--   `traversal` when the frame that attaches the view composes it: on a
--   Compose app's cold starts that one pass took 42 to 44 ms, more than
--   any other, and composed the navigation host. Or under nothing: on a
--   large app's warm start each such pass followed
--   `Compose:initializeView`, or the pass of the view before it;
-- - `in a frame`, the frame's recomposition (`Recomposer:recompose`), where
--   state changed;
-- - `during measure`, a lazy list or another subcomposing layout composing
--   its items as it measured (`AndroidOwner:onMeasure`);
-- - `elsewhere`, the rest.
-- A count alone cannot tell recomposing from composing many: seven calls of
-- the icon painter were seven icons.

-- The calls, each with the nearest call of the app's own around it and the
-- nearest pass, are written down once as this detector's own table: both
-- the rows below and the time they stand for (`@intervals`) are read from
-- it (#490).
CREATE OR REPLACE PERFETTO TABLE _det_composables AS
WITH calls AS MATERIALIZED (
    SELECT s.slice_id, s.name, s.ts, s.dur
    FROM _slice_win s
    JOIN _compose_own o ON o.name = s.name
    WHERE s.is_main_thread = 1
      AND s.name GLOB '{{name_glob}}'
)
-- The name under the fold every detector groups by (#831).
SELECT c.slice_id, COALESCE(f.family, c.name) AS name, c.ts, c.dur,
       -- The nearest call of the app's own that this one runs inside:
       -- its time is that call's, less this one's.
       (SELECT a.id FROM ancestor_slice(c.slice_id) a
        JOIN calls p ON p.slice_id = a.id
        ORDER BY a.depth DESC LIMIT 1)                         AS outer_id,
       -- The nearest pass around it: what composed it is what that
       -- pass sits under.
       (SELECT a.id FROM ancestor_slice(c.slice_id) a
        WHERE a.name = 'Compose:recompose'
        ORDER BY a.depth DESC LIMIT 1)                         AS pass_id
FROM calls c
LEFT JOIN _name_family f ON f.name = c.name;

WITH composed AS (
    SELECT n.slice_id,
           CASE
               WHEN pp.name = 'Recomposer:recompose' THEN 'frame'
               WHEN pp.name GLOB 'AndroidOwner:*' THEN 'measure'
               -- A pass with nothing above it has a parent id of NULL; a
               -- call with no pass around it has no pass at all.
               WHEN pp.name = 'traversal'
                    OR (p.id IS NOT NULL AND p.parent_id IS NULL) THEN 'first'
               ELSE 'elsewhere'
           END                                                     AS how
    FROM _det_composables n
    LEFT JOIN slice p ON p.id = n.pass_id
    LEFT JOIN slice pp ON pp.id = p.parent_id
),
inner_time AS (
    SELECT n.outer_id AS slice_id, SUM(n.dur) AS ns
    FROM _det_composables n
    WHERE n.outer_id IS NOT NULL
    GROUP BY n.outer_id
),
grouped AS (
    SELECT c.name,
           COUNT(*)                                                AS count,
           SUM(c.dur)                                              AS total_ns,
           SUM(c.dur - COALESCE(i.ns, 0))                          AS self_ns,
           MAX(c.dur)                                              AS max_ns,
           SUM(w.how = 'first')                                    AS in_first,
           SUM(w.how = 'frame')                                    AS in_frame,
           SUM(w.how = 'measure')                                  AS in_measure,
           SUM(w.how = 'elsewhere')                                AS elsewhere
    FROM _det_composables c
    JOIN composed w USING (slice_id)
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
    SUBSTR(CASE WHEN in_first > 0 THEN ' · ' || in_first || ' in a first composition' ELSE '' END
        || CASE WHEN in_frame > 0 THEN ' · ' || in_frame || ' in a frame' ELSE '' END
        || CASE WHEN in_measure > 0 THEN ' · ' || in_measure || ' during measure' ELSE '' END
        || CASE WHEN elsewhere > 0 THEN ' · ' || elsewhere || ' elsewhere' ELSE '' END, 4)
                                                             AS detail
FROM grouped
ORDER BY self_ms DESC, location;

-- @intervals
--
-- A row stands for what `self_ms` counts, read off the calls the first
-- query wrote down: each of its calls, less the calls of the app's own
-- nested in it, which are their own rows' or under the bar. The gaps before, between and after those, found by ordering them. They do
-- not overlap one another — one nested in another would have that one for
-- its nearest — so the gaps are the call's own time exactly.

WITH named AS MATERIALIZED (
    SELECT c.slice_id, c.ts, c.ts + c.dur AS te
    FROM _det_composables c
    WHERE c.name IN (SELECT location FROM _rows)
),
kids AS MATERIALIZED (
    SELECT n.outer_id AS slice_id, n.ts AS cs, n.ts + n.dur AS ce,
           LAG(n.ts + n.dur) OVER (PARTITION BY n.outer_id ORDER BY n.ts) AS prev_end
    FROM _det_composables n
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
