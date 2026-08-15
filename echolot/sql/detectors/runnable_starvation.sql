-- @id: runnable_starvation
-- @title: CPU preemption (runnable but not running)
-- @why: the thread is ready to work but the scheduler will not let it in. The
--       slice is "long" not because the code is heavy but because the device
--       has other plans.
-- @param: min_runnable_ms = 20
-- @calibrate: min_runnable_ms = top10(total_ms) * 1.5

SELECT
    thread_name                       AS location,
    COUNT(*)                          AS count,
    ROUND(SUM(dur) / 1e6, 2)          AS total_ms,
    ROUND(MAX(dur) / 1e6, 2)          AS max_ms,
    'state ' || state                 AS detail
FROM _tstate_win
WHERE state IN ('R', 'R+')
GROUP BY thread_name, state
HAVING SUM(dur) >= {{min_runnable_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;
