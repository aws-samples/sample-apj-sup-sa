-- RED per service in the 'shop' application over the last 15 minutes, from SERVER spans.
-- Omni's service views show the same signals from span metrics. This SQL version lets you slice further.
-- Span kind and status can be ingested as enums or names, so normalise both.
SELECT resource['attributes']['service.name'] AS service,
       COUNT(*) AS requests,
       SUM(CASE WHEN upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
                THEN 1 ELSE 0 END) AS errors,
       approx_percentile_cont(CAST(durationNano AS DOUBLE) / 1e6, 0.50) AS p50_ms,
       approx_percentile_cont(CAST(durationNano AS DOUBLE) / 1e6, 0.99) AS p99_ms
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '15 MINUTES' AND NOW()
  AND resource['attributes']['service.namespace'] = 'shop'
  AND upper(TRY_CAST(kind AS VARCHAR)) IN ('2', 'SERVER', 'SPAN_KIND_SERVER')
  AND durationNano IS NOT NULL
GROUP BY resource['attributes']['service.name']
ORDER BY p99_ms DESC
