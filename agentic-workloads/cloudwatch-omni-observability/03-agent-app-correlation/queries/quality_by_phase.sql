-- Did downstream latency change how users experienced the agent? Average evaluator score per phase.
-- Needs online evaluation from sample 01 to be ENABLED before the scenario ran.
-- Scores are log records in logs.default; join them to the agent span on traceId for the phase.
SELECT a.phase,
       e.evaluator,
       AVG(e.score) AS avg_score,
       COUNT(*)     AS scored_turns
FROM (
  SELECT traceId,
         attributes['gen_ai.evaluation.name'] AS evaluator,
         CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE) AS score
  FROM "logs.default"
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '3 HOURS' AND NOW()
    AND attributes['gen_ai.evaluation.name'] IS NOT NULL
    AND attributes['error.type'] IS NULL
    AND resource['attributes']['service.name'] IN ('support-agent', 'support-agent.DEFAULT')
) e
INNER JOIN (
  SELECT DISTINCT traceId, attributes['app.scenario.phase'] AS phase
  FROM traces.default
  WHERE `@timestamp` BETWEEN NOW() - INTERVAL '3 HOURS' AND NOW()
    AND resource['attributes']['service.name'] = 'support-agent'
    AND attributes['gen_ai.operation.name'] = 'invoke_agent'
    AND attributes['app.scenario.phase'] IN ('baseline', 'incident', 'recovery')
) a ON e.traceId = a.traceId
GROUP BY a.phase, e.evaluator
ORDER BY e.evaluator, a.phase
