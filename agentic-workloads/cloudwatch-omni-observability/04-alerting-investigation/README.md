# 04 — Alerting and AI-assisted investigation

A single alerting system for applications **and** agents. The alerts below read the same telemetry as samples 01–03. They notify through SNS or Slack, and when one fires you hand it to **AWS DevOps Agent** for a root-cause investigation.

| Alert | Language | Signal | Fires when (demo default) |
|---|---|---|---|
| `omni-samples.payments-slow-requests` | SQL | `payments` **server** spans slower than 1 s, last 5 min | > **5** for 2 min |
| `omni-samples.shop-error-spans` | SQL | ERROR **server** spans in `service.namespace=shop`, last 5 min | > **10** |
| `omni-samples.agent-tool-failures` | SQL | failed `execute_tool` spans for `support-agent`, last 5 min | > **3** |
| `omni-samples.agent-quality` | SQL | avg `Builtin.Helpfulness` score from online evaluation, last 30 min | < **0.6** for 5 min |

> These thresholds are **demo values** sized to the faults `chaos.sh` injects. For real services, measure a baseline first (`--ground`) and set thresholds from it. All of them are flags on `create_alerts.py`.

Omni alerts are separate from CloudWatch alarms. Existing alarms keep working, and the two systems don't share configuration.

## 1. Notification target (optional)

> **Organization domain?** Run `source common/assume-member-role.sh` from the repo root in this shell first, so everything below acts in the member account that owns the space (see the main README).

Step 0 already created `omni-samples-alerts`, with the publish policy for `cloudwatch.amazonaws.com`. `write_env.sh` put its ARN in `.env` as `ALERTS_TOPIC_ARN`, and `create_alerts.py` uses it by default. Deploy step 0 with `-c alertEmail=you@example.com` to get emails.

**Slack** instead of, or as well as, SNS: connect Slack in Omni (**Settings › Integrations**), then get the integration ARN with `aws cloudwatchomni list-integrations`. In Slack, `/invite` the CloudWatch Omni app into the channel. The access profile you use must allow `cloudwatch:InvokeIntegration` on that integration.

## 2. Create the alerts

An alert evaluates its query **as an access profile**, and a new space has none. A profile is an empty container until two grants exist: a **permission grant** (the profile gets `READ`, which includes `StartTelemetryQuery`/`GetTelemetryQueryResults`) and a **trust grant** (`ALERT` / `ALL` may assume it, as a `CUSTOM` grant with `cloudwatch:AssumeAccessProfile` scoped to the profile ARN). `setup_alert_profile.py` creates all three pieces, and re-running it reuses them:

```bash
pip install "boto3>=1.43"

python setup_alert_profile.py --apply        # profile "omni-samples-alerts" + both grants
sleep 180                                    # new grants take effect after an auth-cache refresh (~3 min)

python create_alerts.py --ground             # dry run: shows each SQL query's current value and the requests
python create_alerts.py --apply              # uses the omni-samples-alerts profile and ALERTS_TOPIC_ARN from .env
```

If `--apply` fails with `The alert cannot assume access profile … Grant the profile a trust grant…` right after setup, the grants haven't propagated yet. Wait a few minutes and re-run. Re-running `create_alerts.py --apply` updates existing alerts in place, so it's safe to repeat after changing thresholds.

Why the queries look the way they do:
* Every query carries a **relative** window (`NOW() - INTERVAL '5 MINUTES'`, or `[5m]` in PromQL), because an alert has no window of its own.
* SQL `FIELD_VALUE` alerts project a named number (`error_spans`, `avg_score`) that becomes `thresholdField`, with `ORDER BY … DESC LIMIT 1`.
* **Server spans only** (`kind` = SERVER). OpenTelemetry marks an HTTP *client* span as ERROR on any 4xx. Counting all spans made `shop-error-spans` fire forever, because the load generator deliberately looks up a missing order (about 30 client 404s per 5 minutes).
* **Latency is alerted as a count of slow requests**, not a percentile. Two percentile shapes failed in testing. A PromQL `histogram_quantile(0.99, sum(rate(traces.span.metrics.duration[5m])))` alert fired but stayed CRITICAL more than 10 minutes after the fault ended (the SQL p99 was 5 ms). A SQL `approx_percentile_cont(...) AS p99_seconds` alert never fired, although the same query returned about 2.9 s when run ad hoc. `COUNT(*)` thresholds evaluate reliably. Keep percentiles on dashboards ([`../02-microservices-apm/queries/promql.md`](../02-microservices-apm/queries/promql.md)).
* No threshold is baked into a query. It lives in `condition`, so you can retune it without touching the query.
* `noData`: every alert here treats no data as `OK`. The three `COUNT(*)` queries return a row (`0`) even when nothing arrived, so they never reach `NODATA` in the first place; the quality query can return no rows, and `OK` is the right reading, because an unscored window says nothing about quality. The consequence: **none of these alerts detects a silent service.** If `payments` stops serving traffic altogether, `payments-slow-requests` counts zero slow requests and stays `OK`. To catch that, alert on request *volume* — server spans per 5 minutes below a floor you set from your own baseline — rather than relying on `NODATA`.
* The quality alert's query ends with `HAVING COUNT(*) > 0`. That's a no-data guard, not a second threshold. Without it, an `AVG` over zero scores still returns one row with an empty value, and the alert read that as below threshold (it fired CRITICAL before any scores existed).

The ad hoc query API rejects metric queries (`Metrics queries are not supported`), so `--ground` can't check PromQL. That's why every sample alert is SQL.

## 3. Trigger an incident

With sample 02 running (and sample 01's online evaluation enabled, for the quality alert):

```bash
../02-microservices-apm/chaos.sh both 2500 0.3     # payments latency and errors: fires the two shop alerts
# wait about 5 minutes for payments-slow-requests and shop-error-spans, then:
../03-agent-app-correlation/run_scenario.sh 20       # agent traffic through a slow payments: fires agent-tool-failures
```

Run these one after the other, not together: `run_scenario.sh` manages the fault itself (off → 4 s latency → off), so it clears the `both 2500 0.3` fault when it starts and leaves payments healthy when it ends.

Timings measured in a verified run (fault `both 2500 0.3`): `shop-error-spans` went CRITICAL after about **3 minutes** and `payments-slow-requests` after about **4 minutes**. Both returned to OK about **5 minutes** after `chaos.sh off`: the 5-minute window has to clear, then the 2-minute recovery period. `agent-tool-failures` only fires while agent traffic runs through the broken path (`run_scenario.sh`). `agent-quality` lags, because it averages 30 minutes of scores. All of them appear on **Alerts** in Omni, and SNS or Slack receives the CRITICAL transitions.

![Alerts page with payments-slow-requests and shop-error-spans CRITICAL during the injected fault](../docs/screenshots/04-alerts-critical.png)

*Alerts about 5 minutes after `chaos.sh both 2500 0.3`: the two shop alerts are CRITICAL, and the agent alerts stay OK until agent traffic goes through the broken path.*

Check the states from the CLI:

```bash
aws cloudwatchomni list-alerts --space-id $OMNI_SPACE_ID --region $AWS_REGION --query 'items[].name'
aws cloudwatchomni get-alert --space-id $OMNI_SPACE_ID --alert-id <alertId> --region $AWS_REGION --query 'alert.state'
```

## 4. Investigate with AWS DevOps Agent

One-time setup (space administrator):
1. Create a DevOps Agent space in the same account. If Omni uses IAM Identity Center, the agent space must use it too.
2. Attach the `AIDevOpsAgentFullAccess` managed policy to your space's operator role. For a space created by step 0, that's the stack's `SpaceAccessRoleArn` output; a console-created space uses `CloudWatchOmniOperatorRole`. The policy is broad, so detach it when you're done (see Cleanup).
   ```bash
   ROLE=$(aws cloudformation describe-stacks --stack-name OmniSamplesPrereqs --region $AWS_REGION \
            --query "Stacks[0].Outputs[?OutputKey=='SpaceAccessRoleArn'].OutputValue" --output text)
   aws iam attach-role-policy --role-name "${ROLE##*/}" --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentFullAccess
   ```
3. In Omni: **Settings › Integrations › AWS DevOps Agent › Select Space**, then choose the Region and agent space and **Connect**.

Start an investigation in any of these ways. Investigations are always manual; a firing alert never starts one by itself.
* **From the alert:** open `omni-samples.payments-slow-requests` and choose **Investigate**.
* **From a thread:** `/investigation new checkout and support-agent degraded since <time>; payments requests slower than 1s`
* **By asking the Omni agent:** *"Why are support-agent tool calls failing?"* It hands root-cause questions to DevOps Agent.

A good investigation should follow this path: the frontend and orders errors are 502/504 responses from `payments` → payments' slow `card_processor.authorize` span with `chaos.latency_ms` set → the "chaos enabled" warning log in `/omni-samples/shop` → the agent's `lookup_order` timeouts are downstream of the same cause.

Recover:

```bash
../02-microservices-apm/chaos.sh off
```

The alerts return to OK after their recovery duration.

## Cleanup

```bash
aws cloudwatchomni list-alerts --space-id <space-id> --region $AWS_REGION \
  --query "items[?starts_with(name,'omni-samples.')].[alertId,name]" --output text
aws cloudwatchomni delete-alert --space-id <space-id> --alert-id <alertId> --region $AWS_REGION   # per alert
aws cloudwatchomni list-access-profiles --space-id <space-id> --region $AWS_REGION                  # then, once no alert uses it:
aws cloudwatchomni delete-access-profile --space-id <space-id> --profile-id <profileId> --region $AWS_REGION
# The ALERT/ALL trust grant survives the profile:
aws cloudwatchomni list-access-grants --space-id <space-id> --region $AWS_REGION \
  --query "items[?principal.principalType=='ALERT'].grantId" --output text
aws cloudwatchomni delete-access-grant --grant-id <grantId> --region $AWS_REGION
# If you connected DevOps Agent, detach the policy from the space role:
#   aws iam detach-role-policy --role-name "${ROLE##*/}" --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentFullAccess
# The SNS topic belongs to step 0 and is removed by its cdk destroy.
```
