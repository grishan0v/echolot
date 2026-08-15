-- Phase 1: the process, its slices, and the scenario window boundaries.
--
-- Only what is needed to COMPUTE the window lives here. The CLI then reads
-- ts_start/ts_end with a single query and runs window.sql, where they are
-- already plain numbers. The split is not cosmetic: while _window was a view,
-- every reference to _slice_win recomputed it — on a trace with 475k slices
-- that turned a run into tens of minutes.
--
-- Deliberately restricted to the ancient, stable Perfetto tables (process,
-- thread, slice, thread_track, thread_state), no stdlib modules.
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

-- The window start is kept separate: the end-anchor lookup refers back to it.
DROP VIEW IF EXISTS _win_start;
CREATE VIEW _win_start AS
SELECT COALESCE(
    (SELECT MIN(ts) FROM _slice WHERE name GLOB '{{scenario_start}}'),
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
        (SELECT s.ts + MAX(s.dur, 0) FROM _slice s
          WHERE s.name GLOB '{{scenario_end}}'
            AND s.ts >= (SELECT ts FROM _win_start)
          ORDER BY s.ts
          LIMIT 1),
        (SELECT MAX(ts + MAX(dur, 0)) FROM _slice)
    ) AS ts_end;
