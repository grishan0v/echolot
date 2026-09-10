-- Phase 1: the process, its slices, and the scenario window boundaries.
--
-- Only what is needed to COMPUTE the window lives here. The CLI then reads
-- ts_start/ts_end with a single query and runs window.sql, where they are
-- already plain numbers. The split is not cosmetic: while _window was a view,
-- every reference to _slice_win recomputed it — on a trace with 475k slices
-- that turned a run into tens of minutes.
--
-- Deliberately restricted to the ancient, stable Perfetto tables (process,
-- thread, slice, thread_track, process_track, thread_state), no stdlib
-- modules.
--
-- {{upid}} is substituted by the CLI: the process is picked in Python before
-- rendering, so we neither pay for a join over every slice in each view nor
-- stay silent when the GLOB matched several processes.

DROP VIEW IF EXISTS _proc;
CREATE VIEW _proc AS
SELECT upid, pid, name
FROM process
WHERE upid = {{upid}};

-- Every slice of our process, tied to its thread.
DROP VIEW IF EXISTS _slice;
CREATE VIEW _slice AS
SELECT
    s.id            AS slice_id,
    s.ts            AS ts,
    s.dur           AS dur,
    s.name          AS name,
    s.depth         AS depth,
    t.utid          AS utid,
    t.tid           AS tid,
    t.name          AS thread_name,
    CASE WHEN t.tid = p.pid THEN 1 ELSE 0 END AS is_main_thread
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t        ON tt.utid = t.utid
JOIN _proc p         ON t.upid = p.upid;

-- The application's own async sections. `Trace.beginAsyncSection` — what
-- androidx.tracing writes for work that starts on one thread and ends on
-- another, and what a hand-rolled wrapper over it writes for everything —
-- lands on a track owned by the process, not by a thread, and the join
-- above never sees it. On a real project every named marker was that
-- kind: the end anchor matched nothing, the window
-- quietly became the whole trace, and ten detectors fired on a scenario
-- that had not been cut out.
--
-- Kept apart from _slice rather than folded into it. A detector that reads
-- thread slices asks "what was this thread doing", and an async section has
-- no thread to answer for: it would be counted under no thread, at a depth
-- of its own, and every self-time and coverage sum would be wrong by its
-- length. What it is good for is the two things that only need a name and a
-- time — the scenario anchors, and the inventory `names` and `probe` print.
--
-- `atrace_async_slice` is how trace_processor labels the tracks the S/F
-- atrace events go to. The other process-owned tracks are not the app's
-- sections: the frame timeline lives there, named by vsync number, and so
-- do the low-memory-killer events.
DROP VIEW IF EXISTS _aslice;
CREATE VIEW _aslice AS
SELECT
    s.id            AS slice_id,
    s.ts            AS ts,
    s.dur           AS dur,
    s.name          AS name,
    s.depth         AS depth
FROM slice s
JOIN process_track pt ON s.track_id = pt.id
JOIN _proc p          ON pt.upid = p.upid
WHERE pt.type = 'atrace_async_slice';

-- What an anchor may match: a thread's section or an async one. The
-- macrobenchmark's own end marker is usually the second kind — a
-- `beginAsyncSection` from wherever the screen was first drawn, which is
-- rarely the thread that started the scenario.
DROP VIEW IF EXISTS _anchor;
CREATE VIEW _anchor AS
SELECT ts, dur, name FROM _slice
UNION ALL
SELECT ts, dur, name FROM _aslice;

-- The window start is kept separate: the end-anchor lookup refers back to it.
DROP VIEW IF EXISTS _win_start;
CREATE VIEW _win_start AS
SELECT COALESCE(
    (SELECT MIN(ts) FROM _anchor WHERE name GLOB '{{scenario_start}}'),
    (SELECT MIN(ts) FROM _slice)
) AS ts;

-- Scenario boundaries. Start is the first occurrence of the start anchor.
-- End is where the FIRST anchor starting after that ends — first by ts, not
-- smallest by ts+dur: a short but later slice must not cut the window early.
-- With no anchors configured or none matching, the whole trace is the window.
DROP VIEW IF EXISTS _window;
CREATE VIEW _window AS
SELECT
    (SELECT ts FROM _win_start) AS ts_start,
    COALESCE(
        (SELECT a.ts + MAX(a.dur, 0) FROM _anchor a
          WHERE a.name GLOB '{{scenario_end}}'
            AND a.ts >= (SELECT ts FROM _win_start)
          ORDER BY a.ts
          LIMIT 1),
        (SELECT MAX(ts + MAX(dur, 0)) FROM _slice)
    ) AS ts_end;
