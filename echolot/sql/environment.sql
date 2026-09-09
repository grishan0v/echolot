-- Phase 3: what the platform was doing to the app while we measured it.
--
-- Runs after window.sql and, like it, receives ts_start/ts_end as plain
-- numbers. Nothing here is a detector: there is no finding, no threshold and
-- no judgement, only four measurements that every other number in the report
-- is conditional on. A slice is 40 ms partly because of the code in it and
-- partly because of the clock it ran on, and until the clock is written down
-- the report cannot tell those apart — neither can `compare`, which is what
-- these views exist for.
--
-- Every view here is empty when the trace was recorded without the matching
-- data source. That is a state the reader has to be able to see, so the CLI
-- asks each one separately and the report says "not recorded" rather than
-- printing a zero.

-- --- the clock -------------------------------------------------------------
--
-- `power/cpu_frequency` emits one sample per change per CPU, so a sample
-- holds until the next one on that CPU. LEAD gives that next one; the last
-- sample of a CPU holds to the end of the window.
--
-- The window is applied AFTER the interval is built, never before: the sample
-- that decides the frequency at ts_start is almost always the one emitted
-- before the window opened, and filtering by ts first would throw it away and
-- leave the beginning of every scenario unmeasured.
DROP VIEW IF EXISTS _cpu_freq;
CREATE VIEW _cpu_freq AS
SELECT cpu, ts, dur, khz FROM (
    SELECT
        t.cpu                                                     AS cpu,
        MAX(c.ts, {{ts_start}})                                   AS ts,
        MIN(LEAD(c.ts, 1, {{ts_end}})
                OVER (PARTITION BY t.cpu ORDER BY c.ts),
            {{ts_end}}) - MAX(c.ts, {{ts_start}})                 AS dur,
        CAST(c.value AS INT)                                      AS khz
    FROM counter c
    JOIN cpu_counter_track t ON c.track_id = t.id
    WHERE t.name = 'cpufreq'
)
WHERE dur > 0;

-- Where OUR threads were on a CPU. The clock of a core nobody of ours was
-- running on says nothing about our numbers, so the average below is weighted
-- by the time we actually spent there.
--
-- SPAN_JOIN needs intervals that do not overlap inside a partition, and here
-- the partition is the CPU: one core runs one thread at a time, so two
-- Running intervals of the same process cannot overlap on it. That is a
-- property of the scheduler rather than of this query, which is why it is
-- worth writing down.
DROP VIEW IF EXISTS _on_cpu;
CREATE VIEW _on_cpu AS
SELECT cpu, ts, dur
FROM _tstate_win
WHERE state = 'Running' AND cpu IS NOT NULL AND dur > 0;

DROP TABLE IF EXISTS _freq_on_cpu;
CREATE VIRTUAL TABLE _freq_on_cpu
USING SPAN_JOIN(_on_cpu PARTITIONED cpu, _cpu_freq PARTITIONED cpu);

-- --- temperature and throttling -------------------------------------------
--
-- Two different facts, and only the second one is evidence. A hot device is
-- not necessarily a slowed device; a cooling device above zero is the kernel
-- saying out loud that it took capacity away.
DROP VIEW IF EXISTS _thermal_win;
CREATE VIEW _thermal_win AS
-- trace_processor names the track after the zone and appends what kind of
-- track it is. The zone is the part a human recognises.
SELECT REPLACE(t.name, ' Temperature', '') AS zone, c.value / 1000.0 AS celsius
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.type = 'thermal_temperature'
  AND c.ts >= {{ts_start}} AND c.ts <= {{ts_end}};

DROP VIEW IF EXISTS _throttle_win;
CREATE VIEW _throttle_win AS
SELECT REPLACE(t.name, ' Cooling Device', '') AS device, c.value AS level
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.type = 'cooling_device_counter'
  AND c.ts >= {{ts_start}} AND c.ts <= {{ts_end}};

-- --- memory ----------------------------------------------------------------
--
-- Polled once a second, so a short window holds one sample or none. One
-- sample is still worth printing and zero samples must read as "not
-- recorded"; the CLI tells them apart by the count, which is why it is
-- selected alongside the value.
DROP VIEW IF EXISTS _meminfo_win;
CREATE VIEW _meminfo_win AS
SELECT t.name AS key, c.value AS value
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.type = 'meminfo'
  AND c.ts >= {{ts_start}} AND c.ts <= {{ts_end}};

-- Major faults are a running total, so the window's cost is the difference
-- across it and needs two samples to exist at all.
DROP VIEW IF EXISTS _vmstat_win;
CREATE VIEW _vmstat_win AS
SELECT t.name AS key, c.value AS value
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.type = 'vmstat'
  AND c.ts >= {{ts_start}} AND c.ts <= {{ts_end}};
