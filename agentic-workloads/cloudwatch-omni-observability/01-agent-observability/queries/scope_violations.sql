-- Turns the ScopeAdherence evaluator scored as violations (higher is better, so low is bad),
-- with the judge's explanation. Use these trace IDs to build a regression dataset in Omni.
-- Vars: service, evaluator (the name the scores are filed under, e.g. ScopeAdherence)
SELECT `@timestamp`,
       traceId,
       attributes['session.id']                    AS session_id,
       attributes['gen_ai.evaluation.score.value'] AS score,
       attributes['gen_ai.evaluation.score.label'] AS label,
       attributes['gen_ai.evaluation.explanation'] AS explanation
FROM "logs.default"
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOURS' AND NOW()
  AND attributes['gen_ai.evaluation.name'] = '{evaluator}'
  AND attributes['error.type'] IS NULL
  AND CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE) < 0.5
  AND resource['attributes']['service.name'] IN ('{service}', '{service}.DEFAULT')
ORDER BY `@timestamp` DESC
LIMIT 50
