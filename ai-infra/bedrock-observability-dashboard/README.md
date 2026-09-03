# Bedrock LLM Dashboard

CloudWatch dashboard for Amazon Bedrock foundation-model users (`InvokeModel` / `Converse` / `ConverseStream` / `InvokeModelWithResponseStream`). Single multi-section dashboard covering FM app teams, SRE, and security/governance — built entirely from native AWS data sources (CloudWatch metrics, Bedrock Model Invocation Logs, CloudTrail).

## What it gives you

Every per-model widget uses CloudWatch `SEARCH` expressions on the `ModelId` dimension, so any model you invoke shows up automatically — no template edit needed when adding a new model.

- **Overview** — invocations, errors, total tokens (last hour)
- **Performance — by ModelId** — `Invocations`, `InvocationLatency` p50/p90/p99, `TimeToFirstToken` p50/p90, plus an avg-latency bar chart for at-a-glance per-model ranking
- **Errors / Throttles — by ModelId** — 4xx/5xx/throttle counts and throttle rate (%) per model with a shared 5% alarm-threshold annotation; legacy-model migration tracking
- **Tokens — by ModelId** — input vs output tokens per model, and cache read vs write tokens per model
- **Quota headroom** — `EstimatedTPMQuotaUsage` per model, derived RPM (with the documented "approximate" caveat)
- **Per-tenant attribution** (Tier 2) — Logs Insights widgets reading `requestMetadata.<TenantMetadataKey>` (default `team`) from invocation logs
- **IAM identity & cross-region routing** (Tier 3) — caller identity, error reasons, and routed region from CloudTrail management events

Plus saved Logs Insights queries (top callers, throttle reasons, cross-region routing, etc.) and CloudWatch alarms: throttle rate and 5xx errors always (Tier 1), plus a log-delivery-failure alarm once invocation logging is wired (Tier 2).

## Pick your tier

One template. You choose which parameters to pass; CloudFormation conditions decide which widgets, queries, and alarms are created. An empty parameter means the resource is **never created** — not created-and-blank. Tiers are additive and upgrade in place (re-deploy with one more parameter; same dashboard URL).

| Tier | What you get | What you set | Est. cost @ ~10K calls/day |
|---|---|---|---|
| **1** | Sections 1–5: invocations, latency, errors, tokens (per-model, region-agnostic). Throttle + 5xx alarms. | Nothing extra | <$5/mo (CW metrics) |
| **2** | Tier 1 + per-tenant attribution + 5 Logs Insights queries + log-delivery alarm | `ModelInvocationLogGroup=<your-log-group>` | + CW Logs ingest (~$0.50–$45/mo by volume) |
| **3** | Tier 2 + IAM identity + cross-region routing + error reasons (3 widgets, 3 queries) | `+ CloudTrailLogGroup=<your-trail-log-group>` | + CW Logs ingest of CloudTrail **management** events (default-on, free to capture) |

> **Tier 3 cost note:** standard runtime calls (`InvokeModel`/`Converse`/`ConverseStream`) are CloudTrail **management events** — captured by default at no per-event charge. You pay only to deliver them to CloudWatch Logs (ingest, comparable to Tier 2). This is *not* the costly "data events" path; data events apply only to async/bidirectional/Agent/KB operations, which this dashboard does not cover.

## Prerequisites (additive by tier)

**Tier 1:** an AWS account with Bedrock model access in your region, and IAM permission to deploy CloudFormation (CW dashboards, alarms, Logs query definitions). Verify model access: `aws bedrock list-foundation-models --region <REGION>`.

**Tier 2 adds:** Bedrock Model Invocation Logging enabled, delivering to a **CloudWatch Logs** group (S3-only delivery can't be queried by Logs Insights). One-time setup in `docs/setup.md`. Optionally, have callers tag `requestMetadata` with your chargeback key.

**Tier 3 adds:** a CloudTrail trail delivering **management events** to a CloudWatch Logs group. No data-event selector is required for this dashboard. If you don't already have such a trail, deploy `cloudformation/cloudtrail-logging.yaml` first — it provisions the log group (and optionally the trail) and works in four modes: greenfield (new trail + bucket), bring-your-own S3 bucket, bring-your-own trail (log group + delivery role only), and optional KMS. Pass its `CloudTrailLogGroup` output to the dashboard. See `docs/setup.md`.

## Deploy

```bash
# Tier 1 — CloudWatch metrics only
aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides DashboardName=Bedrock-FM-Dashboard

# Tier 2 — add Model Invocation Logs (per-tenant attribution)
aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides DashboardName=Bedrock-FM-Dashboard \
                        ModelInvocationLogGroup=<your-invocation-log-group> \
                        TenantMetadataKey=team

# Tier 3 — add CloudTrail (IAM identity, routing, error reasons)
aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides DashboardName=Bedrock-FM-Dashboard \
                        ModelInvocationLogGroup=<your-invocation-log-group> \
                        CloudTrailLogGroup=<your-cloudtrail-log-group>
```

The `DashboardURL` output deep-links to the dashboard; `DeployedTier` confirms which tier (1/2/3) materialised.

## Operate

| To do this | Action |
|---|---|
| Move Tier 1 → 2 | Add `ModelInvocationLogGroup`, re-deploy |
| Move Tier 2 → 3 | Add `CloudTrailLogGroup`, re-deploy |
| Change the chargeback key | Set `TenantMetadataKey=<your-key>`, re-deploy |
| Multi-region | Deploy the same template once per region |
| Tear down | `aws cloudformation delete-stack` (one stack, all tiers) |

Full setup (including how to enable Model Invocation Logging from scratch) → `docs/setup.md`.

## Architecture

```
                ┌───────────────────────────────┐
                │  Bedrock InvokeModel/Converse │
                └───────────────┬───────────────┘
                                │
        ┌───────────────────────┼─────────────────────────┐
        ▼                       ▼                         ▼
 ┌──────────────┐      ┌──────────────────┐     ┌──────────────────┐
 │ CW Metrics   │      │ Model Invocation │     │ CloudTrail mgmt  │
 │ AWS/Bedrock  │      │ Logs (CW Logs)   │     │ events (free)    │
 │ ModelId only │      │ requestMetadata  │     │ identity, errors │
 └──────┬───────┘      └────────┬─────────┘     └────────┬─────────┘
        │                       │                        │
        └────────────► CloudWatch Dashboard ◄────────────┘
                       (this repo's CFN)
```

See `docs/architecture.md` for full panel and metric reference.

## Repository layout

```
cloudformation/dashboard.yaml             CloudFormation template (dashboard + alarms + saved queries)
cloudformation/cloudtrail-logging.yaml    Optional Tier-3 prerequisite: CloudTrail → CloudWatch Logs (greenfield / BYO-bucket / BYO-trail / KMS)
queries/logs-insights/*.cwli              Standalone Logs Insights queries
data-generator/                           24h synthetic workload generator (multi-model, multi-tenant)
  ├── generate.py                         async loop driving Converse / ConverseStream
  ├── README.md                           what it does, tunables, IAM, caveats
  └── OPERATIONS.md                       start / monitor / stop / restart / troubleshooting
docs/architecture.md                      Panel + metric + log-field reference
docs/setup.md                             Deploy + enable Model Invocation Logging from scratch
docs/iam-policy.md                        IAM permissions for deploy / view
docs/data-sources-comparison.md           CW Metrics vs Invocation Logs vs CloudTrail (management events)
```

## Configuration

All knobs are CloudFormation parameters. See `cloudformation/dashboard.yaml` `Parameters` block. Highlights:

| Parameter | Purpose |
|---|---|
| `ModelInvocationLogGroup` | CW Log Group with Bedrock invocation logs — empty = Tier 1; set it to reach Tier 2 (Logs Insights widgets) |
| `CloudTrailLogGroup` | Optional — set it to reach Tier 3 (throttle-reason, identity, cross-region routing widgets) |
| `TenantMetadataKey` | `requestMetadata` key used for per-tenant attribution (default `team`; e.g. `tenant`, `cost_center`, `business_unit`) |
| `PrimaryModelId` | Inference profile / model id; used only in the Section-1 header title |
| `ThrottleRateAlarmThreshold` | % over 5 min that triggers the throttle alarm |
| `ServerErrorsAlarmThreshold` | 5xx (`InvocationServerErrors`) Sum over 5 min that triggers the alarm |
| `AlarmSnsTopicArn` | Optional SNS topic for alarm notifications |

**Region-agnostic by design:** every per-model widget matches models by name fragment via CloudWatch `SEARCH`, so `us.`/`eu.`/`apac.` cross-region inference prefixes work with no template edits, and any model you invoke shows up automatically.

## Out of scope

This dashboard is for **runtime InvokeModel/Converse only**. Other Bedrock features need their own dashboards:

- Bedrock Agents → `AWS/Bedrock/Agents` namespace (per-alias, ModelLatency, agent traces)
- Bedrock Guardrails → `AWS/Bedrock/Guardrails` namespace (policy-type breakdown, automated reasoning)
- Knowledge Bases → vended logs only, ingestion-job events
- Provisioned Throughput → no native utilization metric

## Disclaimer

This project is provided as a sample for educational and reference purposes. It is not an official AWS product or service and is provided "as is" without warranty of any kind. Review and test thoroughly before any production use, and verify any AWS charges (CloudWatch metrics, dashboards, and Logs ingest) against current AWS pricing for your accounts and regions. Licensed MIT-0 — see the [repository root `LICENSE`](../../LICENSE) for terms.
