-- @id: io_wait
-- @title: Threads the kernel put to sleep waiting for the disk
-- @why: the thread is neither running nor runnable and is not waiting on a
--       lock of ours — the kernel parked it until a block device answered.
--       No amount of reading the code shows this: there is no slice, no CPU
--       time and nothing to profile, which is why a cold start can spend
--       hundreds of milliseconds here with every other detector silent.
-- @needs: platform_state
-- @param: min_io_wait_ms = 20 ms — the time a thread has to spend asleep on
--       a block device in the window, summed, to be a row
-- @calibrate: min_io_wait_ms = top10(total_ms) * 1.5
-- @identity: location, thread
-- @column: location — the thread's name
-- @column: threads — how many threads of that name the row adds up: the
--       kernel cuts a name to fifteen characters, and a pool's workers
--       share one. Their time together is not the window's
-- @column: count — its sleeps on the disk in the window
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: thread — `main` or `background`: a worker can carry the process's
--       name, so whether it is the main thread is said apart
-- @column: detail — `main` or `background` again, then the kernel functions
--       it stopped in, when a userdebug kernel names them
-- @plain_title: Waiting for the disk
-- @plain_why: Reading or writing a file can put a thread to sleep until the
--       phone's storage answers.
-- @plain_clean: No thread slept long waiting for the disk.
--
-- The state is `D` — uninterruptible sleep — and `io_wait` is the kernel
-- saying that this particular sleep was block I/O rather than one of the
-- other things that park a task uninterruptibly. The state comes from
-- `sched/sched_switch`; `io_wait` comes from `sched/sched_blocked_reason`,
-- which `collect` records unless `runner.environment: false`.
--
-- Why this is not `uninstrumented_cpu` under another name: that detector
-- looks for threads burning CPU with nothing instrumented around it, and is
-- silent here by construction. A thread waiting on the disk burns no CPU at
-- all. It is not `runnable_starvation` either — that is state `R`, ready and
-- held off by the scheduler, which is the opposite problem.
--
-- What it finds on a real cold start, before anyone has instrumented
-- anything. Fifteen macrobenchmark iterations of a freshly installed app on
-- an SM-A515F, window 541 ms:
--
--     55.02 ms  n=108  15/15  <main>            main
--     29.47 ms  n=23    1/15  arch_disk_io_0    background
--     28.66 ms  n=29    2/15  RenderThread      background
--
-- A tenth of the cold start, on the main thread, in every single run, spent
-- waiting for a block device. Nothing else in the report mentions it.
--
-- What this detector cannot tell you, on a phone anybody actually owns: WHICH
-- kernel function it stopped in. `sched_blocked_reason` carries the caller's
-- address, and turning an address into a name needs /proc/kallsyms, which is
-- unreadable on a production build. Measured on an SM-A515F running
-- Android 13: `blocked_function` was NULL for all 6683 uninterruptible-sleep
-- intervals in the trace, with `symbolize_ksyms` on and off, while `io_wait`
-- was filled in for 6486 of them. So the name rides in `detail` when a
-- userdebug kernel supplies it and is simply absent otherwise — and the row
-- is worth acting on either way, because the thread and the milliseconds are
-- the finding.

--
-- A row is named by its thread and by whether that is the main one, which
-- has a column of its own: a worker can carry the process's name. The kernel
-- functions stay in `detail` and out of the name. Which of them a thread
-- stopped in, and in what order they come, changes from one repeat to the
-- next, and a name built from them made one thread a different row in each
-- repeat: merged, a row per variant, each seen once; compared, one row gone
-- and another new. `threads` says how many threads of that name the row
-- adds up (#511).
SELECT
    COALESCE(f.family, t.thread_name)               AS location,
    COUNT(DISTINCT t.tid)                           AS threads,
    COUNT(*)                                        AS count,
    ROUND(SUM(t.dur) / 1e6, 2)                      AS total_ms,
    ROUND(MAX(t.dur) / 1e6, 2)                      AS max_ms,
    CASE WHEN t.tid = p.pid THEN 'main' ELSE 'background' END
                                                    AS thread,
    CASE WHEN t.tid = p.pid THEN 'main' ELSE 'background' END
        || COALESCE(' · ' || GROUP_CONCAT(DISTINCT t.blocked_function), '')
                                                    AS detail
FROM _tstate_win t
CROSS JOIN _proc p
-- A thread's name under the fold every detector groups by: a pool's
-- workers are one row, and `threads` counts them all (#831, #845).
LEFT JOIN _name_family f ON f.name = t.thread_name
WHERE t.state IN ('D', 'DK')
  AND t.io_wait = 1
GROUP BY COALESCE(f.family, t.thread_name), t.tid = p.pid
HAVING SUM(t.dur) >= {{min_io_wait_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
--
-- The main thread's waits for the disk, when its row is in the report. The
-- same states the sum above counted, already clipped to the window.

SELECT t.ts, t.dur
FROM _tstate_win t
CROSS JOIN _proc p
LEFT JOIN _name_family f ON f.name = t.thread_name
WHERE t.tid = p.pid
  AND t.state IN ('D', 'DK')
  AND t.io_wait = 1
  AND EXISTS (SELECT 1 FROM _rows r
              WHERE r.location = COALESCE(f.family, t.thread_name)
                AND r.detail LIKE 'main%');
