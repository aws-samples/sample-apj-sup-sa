-- Error and warning logs from the shop, with the trace they belong to.
-- Logs and spans share traceId because OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true.
-- Field names come from OTLP log records. Run EXPLAIN (ANALYZE_FIELDS) if anything comes back NULL.
-- Vars: log_group (SHOP_LOG_GROUP from .env, e.g. /omni-samples/shop)
SELECT `@timestamp`,
       resource['attributes']['service.name'] AS service,
       severityText,
       body,
       traceId
FROM logs.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '1 HOUR' AND NOW()
  AND `@logGroupName` = '{log_group}'
  AND upper(TRY_CAST(severityText AS VARCHAR)) IN ('ERROR', 'WARN', 'WARNING')
ORDER BY `@timestamp` DESC
LIMIT 100
