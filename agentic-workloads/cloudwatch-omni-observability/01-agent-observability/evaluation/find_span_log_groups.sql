-- Which log group(s) actually hold this agent's spans. Online evaluation reads from these.
-- Never take the group from resource.attributes['aws.log.group.names']. A config pointed at the
-- wrong group goes ACTIVE/ENABLED and silently scores nothing.
-- If this returns no rows, drop the aws.service.type line and widen the window.
-- Vars: service
SELECT `@logGroupName` AS log_group, COUNT(*) AS spans
FROM "traces.default"
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '24 HOUR' AND NOW()
  AND resource['attributes']['service.name'] = '{service}'
  AND resource['attributes']['aws.service.type'] = 'gen_ai_agent'
GROUP BY `@logGroupName`
ORDER BY spans DESC
LIMIT 10
