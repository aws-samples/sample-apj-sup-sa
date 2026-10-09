-- Token usage per model over the last 24h, from model-call (chat) spans only.
-- The agent-level span carries the same totals again, so including it would double-count.
-- Vars: service
SELECT attributes['gen_ai.request.model']                                AS model,
       COUNT(*)                                                         AS model_calls,
       SUM(TRY_CAST(attributes['gen_ai.usage.input_tokens']  AS BIGINT)) AS input_tokens,
       SUM(TRY_CAST(attributes['gen_ai.usage.output_tokens'] AS BIGINT)) AS output_tokens
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOURS' AND NOW()
  AND resource['attributes']['service.name'] = '{service}'
  AND attributes['gen_ai.operation.name'] = 'chat'
GROUP BY attributes['gen_ai.request.model']
