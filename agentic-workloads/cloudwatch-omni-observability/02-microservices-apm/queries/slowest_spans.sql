-- The 20 slowest spans for one service in the last hour.
-- durationNano is string-typed nanoseconds: cast it, and exclude NULLs (they sort first under DESC).
-- Vars: service (frontend | orders | payments)
SELECT `@timestamp`, traceId, name,
       CAST(durationNano AS DOUBLE) / 1e6 AS duration_ms,
       attributes['http.route'] AS route,
       attributes['chaos.latency_ms'] AS chaos_latency_ms
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '1 HOUR' AND NOW()
  AND resource['attributes']['service.name'] = '{service}'
  AND durationNano IS NOT NULL
ORDER BY CAST(durationNano AS DOUBLE) DESC
LIMIT 20
