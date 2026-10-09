-- Recent agent turns: one row per invoke_agent span, newest first.
-- Vars: service
SELECT `@timestamp`,
       traceId,
       attributes['session.id']          AS session_id,
       attributes['app.prompt.version']  AS prompt_version,
       CAST(durationNano AS DOUBLE) / 1e6 AS duration_ms,
       status['code']                    AS status_code
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '1 HOUR' AND NOW()
  AND resource['attributes']['service.name'] = '{service}'
  AND attributes['gen_ai.operation.name'] = 'invoke_agent'
ORDER BY `@timestamp` DESC
LIMIT 50
