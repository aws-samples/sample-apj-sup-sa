-- Tool health: calls, failures, and p95 latency per tool over the last hour.
-- Vars: service
SELECT attributes['gen_ai.tool.name'] AS tool,
       COUNT(*) AS calls,
       SUM(CASE WHEN upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
                THEN 1 ELSE 0 END) AS failed,
       approx_percentile_cont(CAST(durationNano AS DOUBLE) / 1e6, 0.95) AS p95_ms
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '1 HOUR' AND NOW()
  AND resource['attributes']['service.name'] = '{service}'
  AND attributes['gen_ai.operation.name'] = 'execute_tool'
  AND durationNano IS NOT NULL
GROUP BY attributes['gen_ai.tool.name']
ORDER BY failed DESC
