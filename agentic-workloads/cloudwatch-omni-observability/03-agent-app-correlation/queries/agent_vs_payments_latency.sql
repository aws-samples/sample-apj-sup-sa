-- Per scenario phase: agent turn latency, the slowest payments span in the same trace,
-- and how many turns hit a failed tool call. One SQL statement correlates agent behaviour
-- with downstream application health.
WITH agent_turns AS (
  SELECT traceId,
         attributes['app.scenario.phase'] AS phase,
         CAST(durationNano AS DOUBLE) / 1e6 AS agent_ms
  FROM traces.default
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '2 HOURS' AND NOW()
    AND resource['attributes']['service.name'] = 'support-agent'
    AND attributes['gen_ai.operation.name'] = 'invoke_agent'
    AND attributes['app.scenario.phase'] IN ('baseline', 'incident', 'recovery')
    AND durationNano IS NOT NULL
),
tool_failures AS (
  SELECT DISTINCT traceId
  FROM traces.default
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '2 HOURS' AND NOW()
    AND resource['attributes']['service.name'] = 'support-agent'
    AND attributes['gen_ai.operation.name'] = 'execute_tool'
    AND upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
),
payments AS (
  SELECT traceId, MAX(CAST(durationNano AS DOUBLE) / 1e6) AS payments_ms
  FROM traces.default
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '2 HOURS' AND NOW()
    AND resource['attributes']['service.name'] = 'payments'
    AND durationNano IS NOT NULL
  GROUP BY traceId
)
SELECT a.phase,
       COUNT(*)                                             AS turns,
       approx_percentile_cont(a.agent_ms, 0.5)              AS agent_p50_ms,
       AVG(p.payments_ms)                                   AS avg_payments_ms,
       SUM(CASE WHEN f.traceId IS NOT NULL THEN 1 ELSE 0 END) AS turns_with_tool_failure
FROM agent_turns a
LEFT JOIN payments p      ON a.traceId = p.traceId
LEFT JOIN tool_failures f ON a.traceId = f.traceId
GROUP BY a.phase
ORDER BY a.phase
