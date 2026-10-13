-- Per-evaluator rollup of stored evaluation scores over the last 24h.
-- Scores are LOG records in logs.default, not span attributes in traces.default.
-- Online-evaluation records use '<service>.DEFAULT' as service.name.
-- failed_to_run counts evaluation jobs that errored. It is not the same as a low score.
-- Vars: service
SELECT attributes['gen_ai.evaluation.name'] AS evaluator,
       AVG(CASE WHEN attributes['error.type'] IS NULL
                THEN CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE) END) AS avg_score,
       MIN(CASE WHEN attributes['error.type'] IS NULL
                THEN CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE) END) AS min_score,
       SUM(CASE WHEN attributes['error.type'] IS NULL     THEN 1 ELSE 0 END) AS scored,
       SUM(CASE WHEN attributes['error.type'] IS NOT NULL THEN 1 ELSE 0 END) AS failed_to_run
FROM "logs.default"
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOURS' AND NOW()
  AND attributes['gen_ai.evaluation.name'] IS NOT NULL
  AND resource['attributes']['service.name'] IN ('{service}', '{service}.DEFAULT')
GROUP BY attributes['gen_ai.evaluation.name']
