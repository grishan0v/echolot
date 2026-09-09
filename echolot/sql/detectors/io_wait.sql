-- @id: io_wait
-- @title: Threads the kernel put to sleep waiting for the disk
-- @why: the thread is neither running nor runnable and is not waiting on a
--       lock of ours — the kernel parked it until a block device answered.
--       No amount of reading the code shows this: there is no slice, no CPU
--       time and nothing to profile, which is why a cold start can spend
--       hundreds of milliseconds here with every other detector silent.
-- @param: min_io_wait_ms = 20
-- @calibrate: min_io_wait_ms = top10(total_ms) * 1.5
-- @identity: location, detail
--
-- The state is `D` — uninterruptible sleep — and `io_wait` is the kernel
-- saying that this particular sleep was block I/O rather than one of the
-- other things that park a task uninterruptibly. Both come from
-- `sched/sched_blocked_reason`, which `collect` records by default.
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

SELECT
    t.thread_name                                   AS location,
    COUNT(*)                                        AS count,
    ROUND(SUM(t.dur) / 1e6, 2)                      AS total_ms,
    ROUND(MAX(t.dur) / 1e6, 2)                      AS max_ms,
    CASE WHEN t.tid = p.pid THEN 'main' ELSE 'background' END
        || COALESCE(' · ' || GROUP_CONCAT(DISTINCT t.blocked_function), '')
                                                    AS detail
FROM _tstate_win t
CROSS JOIN _proc p
WHERE t.state IN ('D', 'DK')
  AND t.io_wait = 1
GROUP BY t.thread_name, t.tid = p.pid
HAVING SUM(t.dur) >= {{min_io_wait_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;
