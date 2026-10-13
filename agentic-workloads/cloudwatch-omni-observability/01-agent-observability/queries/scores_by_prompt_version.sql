-- Before/after: average evaluator score per prompt version.
-- Evaluation records don't carry app.prompt.version, so join them to the agent span on traceId.
-- The @timestamp bound is required on both sides of the join.
-- Vars: service
SELECT a.prompt_version,
       e.evaluator,
       AVG(e.score) AS avg_score,
       COUNT(*)     AS n
FROM (
  SELECT traceId,
         attributes['gen_ai.evaluation.name'] AS evaluator,
         CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE) AS score
  FROM "logs.default"
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOURS' AND NOW()
    AND attributes['gen_ai.evaluation.name'] IS NOT NULL
    AND attributes['error.type'] IS NULL
    AND resource['attributes']['service.name'] IN ('{service}', '{service}.DEFAULT')
) e
INNER JOIN (
  SELECT DISTINCT traceId, attributes['app.prompt.version'] AS prompt_version
  FROM traces.default
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOURS' AND NOW()
    AND resource['attributes']['service.name'] = '{service}'
    AND attributes['gen_ai.operation.name'] = 'invoke_agent'
) a ON e.traceId = a.traceId
GROUP BY a.prompt_version, e.evaluator
ORDER BY e.evaluator, a.prompt_version
