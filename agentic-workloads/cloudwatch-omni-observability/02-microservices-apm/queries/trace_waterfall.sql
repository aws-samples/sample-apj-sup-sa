-- Every span of one trace in start order, across all services.
-- In sample 03 this includes the agent spans as well.
-- Vars: trace_id
SELECT `@timestamp`,
       resource['attributes']['service.name'] AS service,
       name,
       kind,
       CAST(durationNano AS DOUBLE) / 1e6     AS duration_ms,
       status['code']                         AS status_code,
       spanId, parentSpanId
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '6 HOURS' AND NOW()
  AND traceId = '{trace_id}'
ORDER BY `@timestamp` ASC
