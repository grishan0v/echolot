-- @id: app_init
-- @title: What ran after the Application was created, before the first Activity
-- @why: every ContentProvider's onCreate and then Application.onCreate run
--       here, and the platform traces neither. What has a name is what
--       libraries write themselves; the rest is one row that says how much
--       nobody named.
-- @param: min_stretch_ms = 80
-- @identity: location
--
-- ## The stretch
--
-- `bindApplication` is the platform's section around the app's own start in
-- its process. Inside it, `ActivityThread.handleBindApplication` calls
-- `makeApplication` — the Application's constructor and `attachBaseContext`,
-- traced — then `installContentProviders(...)` and `callApplicationOnCreate`,
-- neither of them traced, on AOSP main as on the Android 13 devices this was
-- checked on. So there is no `ContentProvider.onCreate` slice to find, and
-- the stretch from the end of `makeApplication` to the end of
-- `bindApplication` is where the providers and `Application.onCreate` went.
-- Without a `makeApplication` in the trace the stretch is all of
-- `bindApplication`, and the last row says so.
--
-- What has a name in it, on real cold starts:
--
--   androidx.startup   `Startup`, with one child per Initializer
--   Firebase           `Firebase`, `fire-sessions`, `fire-perf-early`, …
--   ART                a section per class it initializes, `Lcom/…/Foo;`,
--                      or `Ll71;` once a release build is obfuscated
--   the platform       binder transactions, lock contention, resources
--
-- A row per name, with `Startup` opened into its Initializers, because the
-- Initializer is what somebody can move or defer and `Startup` is only the
-- library's own wrapper. ART's classes fold into one row, told by their
-- shape — `L`, no space, `;` — since an obfuscated name has no package to
-- go by. One cold start wrote 4,758 of them into this stretch, 331 ms
-- together, and a row each would bury everything else. A name another
-- detector speaks for — `binder transaction`, contention, GC: what
-- `_claimed_name` holds — stays with that detector, as it does for
-- `repeated_work`. Both rules apply under `Startup` too: ART initializes an
-- Initializer's class right there, beside the Initializer's own section.
--
-- ## The row nobody named
--
-- The stretch minus every section in it is the providers and the
-- `Application.onCreate` of code that writes nothing. It can be most of the
-- stretch: on the cold start above it took 2.8 s of a 3.7 s
-- `bindApplication`, and 2.1 s of that sat in no section at all. On another
-- app, whose libraries trace themselves, it was 11 ms.
-- `main_thread_block` sees it too, as part of `bindApplication`'s self time
-- mixed with everything before `makeApplication`; here it is on its own and
-- says where it went.
--
-- ## The threshold
--
-- On a healthy cold start the stretch took 49 to 53 ms, so the default sits
-- above that: a silent detector means the providers and `Application.onCreate`
-- together were short.

WITH bind AS (
    SELECT s.slice_id, s.ts, s.ts + s.dur AS te
    FROM _slice_win s
    WHERE s.is_main_thread = 1
      AND s.name = 'bindApplication'
      AND s.dur > 0
),
kid AS (
    SELECT b.slice_id AS bind_id, s.slice_id, s.name, s.ts, s.dur
    FROM _slice_win s
    JOIN slice raw ON raw.id = s.slice_id
    JOIN bind b    ON raw.parent_id = b.slice_id
),
stretch AS (
    SELECT b.slice_id AS bind_id,
           COALESCE((SELECT MAX(k.ts + k.dur) FROM kid k
                     WHERE k.bind_id = b.slice_id
                       AND k.name = 'makeApplication'), b.ts) AS ts,
           b.te,
           EXISTS (SELECT 1 FROM kid k
                   WHERE k.bind_id = b.slice_id
                     AND k.name = 'makeApplication') AS made
    FROM bind b
),
inside AS (
    SELECT k.*
    FROM kid k
    JOIN stretch t ON t.bind_id = k.bind_id
    WHERE k.ts >= t.ts
),
candidate AS (
    SELECT i.name, i.dur, 0 AS initializer
    FROM inside i
    WHERE i.name != 'Startup'

    UNION ALL

    SELECT s.name, s.dur, 1
    FROM _slice_win s
    JOIN slice raw ON raw.id = s.slice_id
    JOIN inside i  ON raw.parent_id = i.slice_id
    WHERE i.name = 'Startup'
),
named AS (
    SELECT CASE WHEN (c.name GLOB 'L*;' AND c.name NOT GLOB '* *')
                  OR c.name GLOB 'VerifyClass *'
                THEN 'class initialization (ART)'
                ELSE c.name
           END AS location,
           c.dur,
           CASE WHEN (c.name GLOB 'L*;' AND c.name NOT GLOB '* *')
                  OR c.name GLOB 'VerifyClass *'
                THEN 'ART writes a section for every class it initializes'
                WHEN c.initializer
                THEN 'an androidx.startup Initializer'
                ELSE 'a section under bindApplication, after makeApplication'
           END AS detail
    FROM candidate c
    WHERE c.name NOT IN (SELECT name FROM _claimed_name)

    UNION ALL

    SELECT '(no section)',
           (t.te - t.ts) - COALESCE((SELECT SUM(i.dur) FROM inside i
                                     WHERE i.bind_id = t.bind_id), 0),
           CASE WHEN t.made
                THEN 'no section holds it: ContentProviders and '
                     || 'Application.onCreate run here and write nothing. '
                     || 'echolot mark can name the app''s own'
                ELSE 'no section holds it, and the trace has no '
                     || 'makeApplication: the stretch is all of '
                     || 'bindApplication, the platform''s part included'
           END
    FROM stretch t
)
SELECT
    location,
    COUNT(*)                          AS count,
    ROUND(SUM(dur) / 1e6, 2)          AS total_ms,
    ROUND(MAX(dur) / 1e6, 2)          AS max_ms,
    MAX(detail)                       AS detail
FROM named
WHERE dur > 0
  AND (SELECT MAX(te - ts) FROM stretch) >= {{min_stretch_ms}} * 1000000
GROUP BY location
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
--
-- The rows' own time: the Initializers, the sections and ART's classes
-- whole, and for the row nobody named, the gaps between the sections in the
-- stretch. The steps that find the stretch are the ones above, repeated.

WITH bind AS (
    SELECT s.slice_id, s.ts, s.ts + s.dur AS te
    FROM _slice_win s
    WHERE s.is_main_thread = 1
      AND s.name = 'bindApplication'
      AND s.dur > 0
),
kid AS (
    SELECT b.slice_id AS bind_id, s.slice_id, s.name, s.ts, s.dur
    FROM _slice_win s
    JOIN slice raw ON raw.id = s.slice_id
    JOIN bind b    ON raw.parent_id = b.slice_id
),
stretch AS (
    SELECT b.slice_id AS bind_id,
           COALESCE((SELECT MAX(k.ts + k.dur) FROM kid k
                     WHERE k.bind_id = b.slice_id
                       AND k.name = 'makeApplication'), b.ts) AS ts,
           b.te
    FROM bind b
),
inside AS (
    SELECT k.*,
           LAG(k.ts + k.dur) OVER (PARTITION BY k.bind_id ORDER BY k.ts) AS prev_end
    FROM kid k
    JOIN stretch t ON t.bind_id = k.bind_id
    WHERE k.ts >= t.ts
),
candidate AS (
    SELECT i.name, i.ts, i.dur
    FROM inside i
    WHERE i.name != 'Startup'

    UNION ALL

    SELECT s.name, s.ts, s.dur
    FROM _slice_win s
    JOIN slice raw ON raw.id = s.slice_id
    JOIN inside i  ON raw.parent_id = i.slice_id
    WHERE i.name = 'Startup'
),
pieces AS (
    SELECT CASE WHEN (c.name GLOB 'L*;' AND c.name NOT GLOB '* *')
                  OR c.name GLOB 'VerifyClass *'
                THEN 'class initialization (ART)'
                ELSE c.name
           END AS location,
           c.ts, c.dur
    FROM candidate c
    WHERE c.name NOT IN (SELECT name FROM _claimed_name)

    UNION ALL

    SELECT '(no section)', COALESCE(i.prev_end, t.ts),
           i.ts - COALESCE(i.prev_end, t.ts)
    FROM inside i
    JOIN stretch t ON t.bind_id = i.bind_id

    UNION ALL

    SELECT '(no section)', COALESCE(MAX(i.ts + i.dur), t.ts),
           t.te - COALESCE(MAX(i.ts + i.dur), t.ts)
    FROM stretch t
    LEFT JOIN inside i ON i.bind_id = t.bind_id
    GROUP BY t.bind_id
)
SELECT p.ts, p.dur
FROM pieces p
WHERE p.location IN (SELECT location FROM _rows);
