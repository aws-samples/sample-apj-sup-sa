-- Failed spans across the shop in the last hour. Failure comes from status.code; the HTTP status corroborates it.
-- Python's FastAPI/httpx instrumentation still emits the older http.status_code key, so check both.
SELECT `@timestamp`, traceId,
       resource['attributes']['service.name']     AS service,
       name,
       COALESCE(attributes['http.response.status_code'], attributes['http.status_code']) AS http_status,
       status['message']                          AS status_message
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '1 HOUR' AND NOW()
  AND resource['attributes']['service.namespace'] = 'shop'
  AND upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
ORDER BY `@timestamp` DESC
LIMIT 100
