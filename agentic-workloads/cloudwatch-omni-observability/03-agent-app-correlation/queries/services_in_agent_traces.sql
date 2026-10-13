-- Which services appear in traces that START in the agent? This proves context propagated
-- agent -> orders -> payments.
-- Uses IN (subquery) rather than a JOIN: bracket access through a table alias
-- (t.resource['attributes'][...]) comes back NULL. The @timestamp bound is required in both queries.
SELECT resource['attributes']['service.name'] AS service,
       COUNT(DISTINCT traceId) AS traces,
       COUNT(*)                AS spans
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '2 HOURS' AND NOW()
  AND traceId IN (
    SELECT DISTINCT traceId
    FROM traces.default
    WHERE `@timestamp` BETWEEN NOW() - INTERVAL '2 HOURS' AND NOW()
      AND resource['attributes']['service.name'] = 'support-agent'
      AND attributes['app.tools.mode'] = 'live'
  )
GROUP BY resource['attributes']['service.name']
ORDER BY spans DESC
