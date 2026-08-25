-- @id: repeated_work
-- @title: The same work reached from more than one place
-- @why: the other detectors ask how much a thing cost. This one asks whether
--       it needed doing: the same named work entered from two different
--       callers, costing about the same both times, is work somebody did
--       twice.
-- @param: min_total_ms = 40
-- @param: max_callers = 3
-- @param: max_spread = 1.5
-- @param: marker_prefix = AGENTTMP_
-- @calibrate: min_total_ms = top5(total_ms) * 1.5
-- @identity: location, detail
--
-- ## Why the set needed one of these
--
-- Every other detector gates on magnitude — `MAX(dur) >=`, `SUM(dur) >=`,
-- `COUNT(*) >=`, `running_ns >=`. That answers "how much", and a duplicate is
-- not a magnitude problem: on a real hunt the repeated half was 270 ms among
-- a dozen other three-hundred-millisecond things, and nothing about the
-- number was remarkable. What made it a bug is that the work had already been
-- done.
--
-- `main_thread_outlier` is the nearest relative — it also declines to gate on
-- an absolute number, and asks instead whether one occurrence is out of line
-- with the median for its own name. This asks a question of shape rather than
-- of size, which is the axis the set was missing.
--
-- ## What the shape actually is
--
-- Not "this name occurs more than once". Repetition is the normal state of a
-- trace: a frame callback repeats sixty times a second, a binder transaction
-- repeats by nature, and `count` has been in the report all along. Grouping
-- by name and parent and asking for two occurrences under one parent finds
-- loops and nothing else — tried, and it produced three incidental pairs on a
-- trace whose real duplicate it could not see.
--
-- The duplicate looks like this instead: one name, entered once from each of
-- two callers.
--
--     GT_deckTr_loop_x29_en   under GT_main_monster              103.7 ms
--     GT_deckTr_loop_x29_en   under GT_update6_fillDecks_again    85.9 ms
--
-- So the grouping is by name and thread, and the finding is that the callers
-- differ. `detail` names them, because which two places is the whole question
-- a reader has next.
--
-- ## The three gates, and which one is soft
--
-- `max_spread` carries the weight. Identical work costs an identical amount,
-- and that sameness is the signal: a loop's occurrences vary because what
-- they are given varies. On the trace above the pair was 103.7 against 85.9 —
-- a ratio of 1.21 — while the loop under it ran from 2.4 to 6.7.
--
-- `min_total_ms` keeps small change out, as everywhere else.
--
-- `max_callers` is the soft one, and it is worth saying so rather than
-- letting a reader discover it. A helper called from five places is a shared
-- utility; two callers is the original-and-repeat shape. That is a claim
-- about how code is usually written rather than about the trace, so the
-- fixture plants a four-caller helper to keep it honest.
--
-- ### Why neither of the two is calibrated, and what was done instead
--
-- `min_total_ms` is a size, so it calibrates like every other size in the
-- set. The other two are not sizes, and the machinery says so itself: a
-- calibrated threshold is opened by setting it to zero, which works for a
-- floor and shuts a ceiling completely. Both of these are ceilings with a
-- floor of their own — the caller count cannot go below the 2 the gate above
-- already demands, and a max/min ratio cannot go below 1 — so any statistic
-- divided by a safety factor lands beneath the gate and closes it for good.
-- That is the same reason `uninstrumented_cpu` leaves `max_covered_pct`
-- alone.
--
-- Numbers picked by looking at one app are worth no more for being left
-- alone, so they were measured against four more. One trace, five processes
-- — `system_server`, `com.instagram.android`, `ru.dodopizza.app.beta`,
-- surfaceflinger and the app itself — whole trace, shipped thresholds, 25
-- groups clearing the floor with two callers or more.
--
--     callers   2 ×6   3 ×3   4   9  10  16  17  19  26  46  51 …  221  934
--
-- Three sits in the gap. Below it, candidates; above it, helpers reached from
-- tens to hundreds of places, which is the claim `max_callers` was making all
-- along, now on five codebases rather than one.
--
--     spread of the 9 groups that clear the caller gate
--     12.2   57.9   64.4   181   365   408   932   1088   4793
--
-- Not one of them is near 1.5, and the closest is eight times past it. The
-- duplicates this detector was built for measure 1.07 and 1.21. An order of
-- magnitude separates the two populations, which is why the number does not
-- need to move per project — and why calibrating it would have been machinery
-- in search of a problem.
--
-- ## This detector works what the others left
--
-- Ten of the eleven detectors know what they are looking for and say so in a
-- `*name_glob*` param. This one does not: it asks a question about shape, so
-- it has to look at every name there is — which means it also looks at names
-- that belong to somebody else. Twice that produced a row already in the
-- report under its own heading.
--
--     Lock contention on a monitor lock (owner tid: 17403)   3 × 56.5 ms
--       under  monitor contention with owner … waiters=0 …
--       under  monitor contention with owner … waiters=1 …
--       under  monitor contention with owner … waiters=2 …
--
-- Every gate held. One name, three callers, all costing about the same — the
-- shape this is built for, and not three callers at all. ART writes a
-- contention as two nested slices and the outer one carries how many threads
-- are queued behind the lock, so one wait comes back under a different parent
-- each time the queue is a different length. `monitor_contention` had already
-- reported it with the owner and the blocking frame, which is the reading it
-- has.
--
-- That was first fixed by naming the family — `*contention*` — and the fix
-- lasted until the next trace. Run against four processes nobody had ever
-- tuned for, the same shape came back from a different family:
--
--     ReclaimPhase                                          2 × 344.0 ms
--       under  NativeAlloc concurrent copying GC
--       under  Background concurrent copying GC
--
-- Also already in the report: `gc_pressure` had both parents at 2423 and 977
-- ms. Naming families is a list somebody has to keep adding to, and the list
-- is always one app behind.
--
-- So the rule instead of the list: the set has already divided the platform's
-- vocabulary between its detectors, and this one works the leftovers. Anything
-- a `*name_glob*` speaks for is not its business, as the slice and as the
-- caller alike. `_claimed_name` is that set of names, built once per session
-- from the masks the detectors declare — including whatever the project
-- widened them to, so moving a mask in `echolot.yml` moves this boundary with
-- it.
--
-- What it still does not cover: a platform name no mask claims, `Compose:
-- recompose` or a Skia flush. For the near miss the prefix keeps those out;
-- for a finding they would have to cost the same from two callers, which is a
-- higher bar than it sounds.
--
-- ## The near miss, and why it only ever speaks about your own markers
--
-- A marker in the wrong place fails the spread gate rather than the caller
-- gate, so the detector's silence is the same silence whether the trace is
-- clean or the instrumentation is one level off. Three hunts on one app say
-- which of those it usually is.
--
-- All three bracketed the call site of the repeat instead of the work it
-- reached, and got one occurrence where the shape needs two. The third went
-- one level deeper still and wrapped the insert inside the loop, and
-- `AGENTTMP_insert_monster` came back under exactly two callers — 67
-- occurrences, 325.9 ms of them, every gate cleared but one. The occurrences
-- ran from 1.8 to 104.1 ms, a spread of 57.9, and nothing was said. One
-- marker moved inside the shared function instead would have priced the two
-- entries at 274 and 257 ms — medians over that round's five traces, a ratio
-- of 1.07, and a row.
--
-- So a group that clears every gate but the spread is reported too, and its
-- `detail` says which of the two it is. The row is not a finding — it is the
-- report saying that a name planted by this hunt is entered from two places
-- and its occurrences do not look alike, which is what a marker around a loop
-- looks like from here.
--
-- Two callers exactly, where a finding is allowed three. `max_callers` is
-- soft for a finding because the cost-equality carries the claim on its own:
-- three callers paying the same price is still a repeat worth a look. A near
-- miss has no equality to lean on, so the caller count is all it has, and
-- three of them is a shared helper. The first trace to produce one proved
-- that: `AGENTTMP_json_parse`, entered from all three seeding stages, 58
-- occurrences of it — a helper doing its job, and "wrap the unit instead"
-- is advice about a unit that does not exist.
--
-- Only names carrying the temporary prefix, and that restriction is what
-- makes it a hint rather than noise. A platform slice with two callers and a
-- wide spread is `Compose:recompose` or a Skia flush, and re-wrapping it is
-- not an option anyone has; on the trace above the prefix is the difference
-- between one row and three. It also keeps `calibrate` out of it: thresholds
-- are measured on healthy runs, and a healthy run is one nobody has
-- instrumented yet, so nothing here reaches the distribution `min_total_ms`
-- is derived from.
--
-- ## What it cannot tell you
--
-- That the work was redundant. Two locales inserting the same number of rows
-- from two callers look exactly like one thing done twice, and the trace
-- carries no argument either way. The row says where the same work was
-- entered from two places; whether the second one was needed is a question
-- for the code.
--
-- Named work only, so on a project with no instrumentation this is silent
-- until `mark` has been round once. That is the ordinary second lap of the
-- loop rather than a limitation of the query.

SELECT
    s.name                                                   AS location,
    COUNT(*)                                                 AS count,
    ROUND(SUM(MAX(s.dur, 0)) / 1e6, 2)                       AS total_ms,
    ROUND(MAX(MAX(s.dur, 0)) / 1e6, 2)                       AS max_ms,
    -- Two sentences, and which one a row gets is the spread gate. No number
    -- from the trace goes into either: `detail` is half of `@identity`, so a
    -- ratio that lands at 57.9 in one repeat and 61.2 in the next would make
    -- one row into five, each of them seen once. The size of the thing is in
    -- the columns, where merging repeats knows what to do with it.
    CASE WHEN (MAX(MAX(s.dur, 0)) * 1.0) / MIN(MAX(s.dur, 0))
              <= {{max_spread}}
    THEN 'entered from ' || COUNT(DISTINCT COALESCE(p.name, '(top level)'))
        || ': ' || GROUP_CONCAT(DISTINCT COALESCE(p.name, '(top level)'))
        || ' — on ' || s.thread_name
    ELSE 'near miss: entered from '
        || COUNT(DISTINCT COALESCE(p.name, '(top level)'))
        || ': ' || GROUP_CONCAT(DISTINCT COALESCE(p.name, '(top level)'))
        || ' — on ' || s.thread_name
        || '; occurrences too unlike to be one work. A marker inside a loop '
        || 'reads like this — wrap the unit instead.'
    END                                                      AS detail
-- `_slice_win` carries no parent, and the parent is what makes this a claim
-- about work rather than about a name: the same helper called from two places
-- is the finding, and the same helper called twice from one place is a loop.
FROM _slice_win s
JOIN slice raw    ON raw.id = s.slice_id
LEFT JOIN slice p ON p.id = raw.parent_id
-- The leftovers, and only the leftovers — see "This detector works what the
-- others left". Applied to the slice and to its caller alike: a name that
-- belongs to another detector is no more a place work is called from than it
-- is work.
WHERE s.name NOT IN (SELECT name FROM _claimed_name)
  AND COALESCE(p.name, '') NOT IN (SELECT name FROM _claimed_name)
GROUP BY s.name, s.thread_name
HAVING COUNT(DISTINCT COALESCE(p.name, '(top level)')) >= 2
   AND COUNT(DISTINCT COALESCE(p.name, '(top level)')) <= {{max_callers}}
   AND SUM(MAX(s.dur, 0)) >= {{min_total_ms}} * 1000000
   -- A slice still open when the trace stopped reads as zero here; dividing
   -- by it would be a ratio about nothing.
   AND MIN(MAX(s.dur, 0)) > 0
   -- Equal cost, or a marker this hunt planted itself from exactly two places
   -- — see "The near miss". `substr` rather than LIKE: `_` is a wildcard
   -- there, and every prefix anyone uses ends in one.
   AND ((MAX(MAX(s.dur, 0)) * 1.0) / MIN(MAX(s.dur, 0)) <= {{max_spread}}
        OR (SUBSTR(s.name, 1, LENGTH('{{marker_prefix}}')) = '{{marker_prefix}}'
            AND COUNT(DISTINCT COALESCE(p.name, '(top level)')) = 2))
ORDER BY total_ms DESC
LIMIT 20;
