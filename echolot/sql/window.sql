-- Phase 2: everything narrowed to the window. ts_start/ts_end arrive as plain
-- numbers — the CLI read them from _window after context.sql. That matters: a
-- view in their place would be recomputed on every reference to _slice_win.
--
-- And the same holds one level up. A view is a query with a name, not rows:
-- each reference to _slice_win ran the join under it again, over every slice
-- of the process, and every detector reads it, most of them more than once.
-- On trace_processor v56.1 `app_init`, which names it four times, took 25
-- of 48 seconds of five cold starts of a large app (#486). On v59.1, with
-- app_init walking the androidx.startup tree (#512) and every detector of a
-- set asked again with its thresholds open (#505), fifteen of them took 225 s
-- one at a time, and take 57 s with these built once and _slice_win indexed
-- by id. So the window's own sets — _slice in context.sql, _slice_win,
-- _child_sum, _aslice_win and _tstate_win — are PERFETTO TABLEs: built once,
-- when this script runs, and read as stored rows after that. The bounds are
-- constants by then, so nothing in them can go stale. The views below them
-- stay views: they are cheap, or read once.
--
-- Each is dropped as a table before it is made. trace_processor refuses
-- `DROP VIEW` on a table, and a second CREATE without a drop, so a session
-- that ran these scripts twice would have failed on the second pass.

-- The window is constants now. The view stays so a detector can refer to the
-- scenario duration without paying anything for it.
DROP VIEW IF EXISTS _window;
CREATE VIEW _window AS
SELECT {{ts_start}} AS ts_start, {{ts_end}} AS ts_end;

-- Slices that OVERLAP the window, not just those starting inside it.
--   dur      — the real slice duration, and that is what humans are shown:
--              a 200 ms block stays a 200 ms block even if the window cut it
--              in half;
--   dur_win  — only the part inside the window. For coverage and share math.
--
-- A slice still open when the trace stopped comes back with dur = -1, and
-- reading that as zero is how the worst case in the trace becomes invisible.
-- On a real ANR the main thread sat in ART's `monitor contention with owner
-- …` for twenty seconds. The lock was never released, so the slice never
-- closed, and every detector saw a slice of length zero. A block that outlives
-- the recording is not a block of no length — it is the longest one there is,
-- and the honest reading is that it ran to the end of the window.

DROP TABLE IF EXISTS _slice_win;
CREATE PERFETTO TABLE _slice_win AS
SELECT
    s.slice_id,
    s.ts,
    CASE WHEN s.dur < 0 THEN {{ts_end}} - s.ts ELSE s.dur END        AS dur,
    s.name,
    s.depth,
    s.utid,
    s.tid,
    s.thread_name,
    s.is_main_thread,
    -- Whether that duration is a measurement or a floor. A detector that
    -- wants to say "at least" has the fact, and nothing has to infer it from
    -- a number that happens to end exactly at the window.
    CASE WHEN s.dur < 0 THEN 1 ELSE 0 END                            AS unfinished,
    MAX(s.ts, {{ts_start}})                                          AS ts_win,
    MIN(s.ts + CASE WHEN s.dur < 0 THEN {{ts_end}} - s.ts
                    ELSE MAX(s.dur, 0) END, {{ts_end}})
        - MAX(s.ts, {{ts_start}})                                    AS dur_win
FROM _slice s
WHERE s.ts < {{ts_end}}
  AND s.ts + CASE WHEN s.dur < 0 THEN {{ts_end}} - s.ts
                  ELSE MAX(s.dur, 0) END > {{ts_start}};

-- Its rows found by id. A view over `slice` handed a join on `slice_id` down
-- to slice's own lookup by id; a table has none until it is given one. Without
-- it, app_init's walk down the androidx.startup tree, which joins _slice_win
-- on the id of every child at every step, read every slice of the process per
-- row. The table goes with its index when it is dropped, so the next pass
-- makes both again.
CREATE PERFETTO INDEX _slice_win_id ON _slice_win(slice_id);

-- How much of each slice its children account for, with an open child read
-- the way _slice_win reads an open slice: running to the end of the window.
-- Taken as zero long, its time landed in its own self time and again in each
-- open ancestor's — with B/E events the parent of an open slice is open too —
-- and self times added up to more than the window.
--
-- `ns` is all of the children, for a slice that closed. `ns_win` stops each
-- child at the end of the window, for a slice that did not close: its own
-- length stops there, and a child it opened after the window is not part of
-- it. One pass over the whole trace; a correlated subquery per slice would
-- cost an order of magnitude more.
DROP TABLE IF EXISTS _child_sum;
CREATE PERFETTO TABLE _child_sum AS
SELECT
    parent_id,
    SUM(CASE WHEN dur < 0 THEN MAX({{ts_end}} - ts, 0) ELSE dur END)    AS ns,
    SUM(MAX(MIN(CASE WHEN dur < 0 THEN {{ts_end}} ELSE ts + dur END,
                {{ts_end}}) - ts, 0))                                   AS ns_win
FROM slice
WHERE parent_id IS NOT NULL
GROUP BY parent_id;

-- The process's async sections that overlap the window, read the same way.
-- Not part of _slice_win, and for the same reason it is not part of _slice:
-- a section on no thread has no place in a per-thread sum. What reads this is
-- the markers table — the project's own names are usually async — and
-- nothing that attributes time to a thread.
DROP TABLE IF EXISTS _aslice_win;
CREATE PERFETTO TABLE _aslice_win AS
SELECT
    a.slice_id,
    a.ts,
    CASE WHEN a.dur < 0 THEN {{ts_end}} - a.ts ELSE a.dur END        AS dur,
    a.name,
    a.depth,
    CASE WHEN a.dur < 0 THEN 1 ELSE 0 END                            AS unfinished
FROM _aslice a
WHERE a.ts < {{ts_end}}
  AND a.ts + CASE WHEN a.dur < 0 THEN {{ts_end}} - a.ts
                  ELSE MAX(a.dur, 0) END > {{ts_start}};

-- Thread states, CLIPPED to the window.
-- Here dur is already the clipped value: the question is always "how long did
-- the thread spend in this state inside the scenario", never "how long was the
-- interval".
--
-- `cpu` is filled in only while the thread is Running — off a CPU there is no
-- CPU to name. `io_wait` and `blocked_function` come from
-- sched/sched_blocked_reason and say why a thread went to uninterruptible
-- sleep, but only the first of them survives a production device: the second
-- needs kernel symbols, and /proc/kallsyms is unreadable there, so on an
-- SM-A515F running Android 13 it was NULL for all 6683 D intervals in the
-- trace while `io_wait` was filled in for 6486 of them.
--
-- All three are carried rather than used here: environment.sql weighs the
-- clock by the cores we actually ran on, and none of them costs anything to a
-- detector that does not select it.
--
-- A thread's last state comes back with dur = -1 when the thread was still in
-- it as the recording stopped, and it runs to the end of the window, as an
-- open slice does above. Dropped, it took each thread's last stretch with it:
-- a main thread asleep from before the window to the end had no budget at
-- all, and the report put the gap down to a thread that was not there.
DROP TABLE IF EXISTS _tstate_win;
CREATE PERFETTO TABLE _tstate_win AS
SELECT
    ts.utid,
    th.tid,
    th.name AS thread_name,
    ts.state,
    ts.cpu,
    ts.io_wait,
    ts.blocked_function,
    MAX(ts.ts, {{ts_start}})                                        AS ts,
    MIN(CASE WHEN ts.dur < 0 THEN {{ts_end}} ELSE ts.ts + ts.dur END, {{ts_end}})
        - MAX(ts.ts, {{ts_start}})                                  AS dur
FROM thread_state ts
JOIN thread th ON ts.utid = th.utid
JOIN _proc p   ON th.upid = p.upid
WHERE ts.dur != 0
  AND ts.ts < {{ts_end}}
  AND CASE WHEN ts.dur < 0 THEN {{ts_end}} ELSE ts.ts + ts.dur END > {{ts_start}};

-- --- intersecting "thread on CPU" with "top-level slices" ------------------
--
-- Needed to answer honestly: what share of ON-CPU time ran inside instrumented
-- code. A naive interval-overlap JOIN is executed by SQLite as a nested loop:
-- 20k Running intervals against 50k slices is a billion comparisons, and the
-- run does not finish within ten minutes.
--
-- SPAN_JOIN is the trace_processor operator built for exactly this: merging
-- two sorted sets of non-overlapping intervals within a partition. On the same
-- trace it completes in 0.1 s.
--
-- Its requirements hold: within a single utid neither Running intervals nor
-- depth = 0 slices overlap, and both are sorted by ts.
--
-- Caveat: SPAN_JOIN is a Perfetto feature, not plain SQL. But it is core
-- trace_processor rather than a stdlib module, and our version is pinned.

DROP VIEW IF EXISTS _running_span;
CREATE VIEW _running_span AS
SELECT utid, ts, dur
FROM _tstate_win
WHERE state = 'Running';

DROP VIEW IF EXISTS _top_slice_span;
CREATE VIEW _top_slice_span AS
SELECT utid, ts_win AS ts, dur_win AS dur, 1 AS instrumented
FROM _slice_win
WHERE depth = 0 AND dur_win > 0;

DROP TABLE IF EXISTS _cpu_in_slice;
CREATE VIRTUAL TABLE _cpu_in_slice
USING SPAN_JOIN(_running_span PARTITIONED utid, _top_slice_span PARTITIONED utid);

-- The same split with nothing dropped: every stretch of on-CPU time, and
-- `instrumented` NULL where no top-level slice was open over it. That part is
-- what `uninstrumented_cpu` calls a blind spot, and its @intervals reads the
-- stretches from here. Computed only when something asks for it.
DROP TABLE IF EXISTS _cpu_by_slice;
CREATE VIRTUAL TABLE _cpu_by_slice
USING SPAN_LEFT_JOIN(_running_span PARTITIONED utid, _top_slice_span PARTITIONED utid);

-- --- who held the core the main thread waited for -------------------------
--
-- A thread in `R` is ready and not let onto a CPU, and on its own that says
-- nothing about whose fault it was. The trace knows: the scheduler's record
-- of every CPU (`sched`) says what ran on the core the main thread was put on
-- next, for as long as it waited. On a large app's launch on an SM-A515F that
-- was the app's own threads for half of the main thread's wait, which makes
-- "the device has other plans" the wrong reading.
--
-- Each wait of the main thread with the CPU it got next. The CPU is the one
-- of the `Running` state that follows, and a wait the window ends before it
-- is over has none.
DROP VIEW IF EXISTS _main_runnable;
CREATE VIEW _main_runnable AS
SELECT ts, dur, state, cpu FROM (
    SELECT t.ts, t.dur, t.state,
           LEAD(CASE WHEN t.state = 'Running' THEN t.cpu END)
               OVER (ORDER BY t.ts)                                 AS cpu
    FROM _tstate_win t
    CROSS JOIN _proc p
    WHERE t.tid = p.pid
)
WHERE state IN ('R', 'R+') AND cpu IS NOT NULL AND dur > 0;

-- What ran on each CPU inside the window, and whose it was: a thread of the
-- app, a thread of anything else, or nothing (the idle task, utid 0). A
-- stretch still running when the recording stopped runs to the window's end.
DROP VIEW IF EXISTS _sched_win;
CREATE VIEW _sched_win AS
SELECT s.cpu,
       MAX(s.ts, {{ts_start}})                                     AS ts,
       MIN(CASE WHEN s.dur < 0 THEN {{ts_end}} ELSE s.ts + s.dur END, {{ts_end}})
           - MAX(s.ts, {{ts_start}})                               AS dur,
       CASE WHEN s.utid = 0 THEN 'idle'
            WHEN t.upid = (SELECT upid FROM _proc) THEN 'app'
            ELSE 'other' END                                       AS holder,
       t.name                                                      AS holder_thread
FROM sched s
LEFT JOIN thread t ON t.utid = s.utid
WHERE s.dur != 0
  AND s.ts < {{ts_end}}
  AND CASE WHEN s.dur < 0 THEN {{ts_end}} ELSE s.ts + s.dur END > {{ts_start}};

-- The two side by side, per CPU. Neither overlaps itself inside a partition:
-- one thread waits once at a time, and one core runs one thing at a time.
DROP TABLE IF EXISTS _main_runnable_held;
CREATE VIRTUAL TABLE _main_runnable_held
USING SPAN_JOIN(_main_runnable PARTITIONED cpu, _sched_win PARTITIONED cpu);

-- And the other cores, idle while the main thread waited: a wait that
-- capacity stood by for is the scheduler's delay rather than a crowd.
DROP VIEW IF EXISTS _main_runnable_any;
CREATE VIEW _main_runnable_any AS
SELECT ts, dur, state, cpu AS next_cpu FROM _main_runnable;

DROP VIEW IF EXISTS _idle_win;
CREATE VIEW _idle_win AS
SELECT cpu, ts, dur FROM _sched_win WHERE holder = 'idle';

DROP TABLE IF EXISTS _main_runnable_idle;
CREATE VIRTUAL TABLE _main_runnable_idle
USING SPAN_JOIN(_main_runnable_any, _idle_win PARTITIONED cpu);
