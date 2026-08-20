-- @id: binder_txn
-- @title: Long binder transactions
-- @why: synchronous IPC into another process is a common hidden cost at startup
-- @param: min_txn_ms = 10
-- @param: max_total_ms = 50
-- @param: name_glob = binder transaction*
-- @param: skip_glob = *async*
-- @calibrate: min_txn_ms = top10(max_ms) * 1.5
-- @calibrate: max_total_ms = top10(total_ms) * 1.5
-- @identity: location, detail
--
-- Two conditions, not one. On a live cold start (772 ms window) the main
-- thread took 76 transactions totalling 65.9 ms, the longest 9.71 ms: not a
-- single one cleared the 10 ms bar, so the detector stayed silent while
-- synchronous IPC ate 8.5% of startup. Death by a thousand cuts is no less
-- real than one long call.
--
-- 50 ms is roughly three frames of accumulated blocking. The threshold is
-- absolute and therefore brittle: on a ten-second scenario it would fire every
-- time. The proper answer is `echolot calibrate`, which derives thresholds
-- from known-healthy runs.

SELECT
    thread_name                       AS location,
    COUNT(*)                          AS count,
    ROUND(SUM(dur) / 1e6, 2)          AS total_ms,
    ROUND(MAX(dur) / 1e6, 2)          AS max_ms,
    CASE WHEN is_main_thread = 1 THEN 'main thread' ELSE 'background' END AS detail
FROM _slice_win
-- Trace Processor emits four kinds of binder slice: 'binder transaction',
-- 'binder reply', 'binder transaction async', 'binder async rcv'. The mask
-- 'binder transaction*' also covers the async one — where the sender does not
-- block, so presenting it as the cost of synchronous IPC would be a lie.
WHERE name GLOB '{{name_glob}}'
  AND name NOT GLOB '{{skip_glob}}'
GROUP BY thread_name, is_main_thread
HAVING MAX(dur) >= {{min_txn_ms}} * 1000000
    OR SUM(dur) >= {{max_total_ms}} * 1000000
ORDER BY total_ms DESC
LIMIT 20;
