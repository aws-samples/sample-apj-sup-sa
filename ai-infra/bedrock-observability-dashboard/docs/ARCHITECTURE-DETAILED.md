# Architecture — Detailed

End-to-end architecture of the Bedrock FM observability dashboard: data sources, tier model, CloudTrail enablement, tenant attribution, test infrastructure, and the data generator.

---

## Contents

1. [What it is](#1-what-it-is)
2. [The three data sources](#2-the-three-data-sources)
3. [The tier model (parameter-gated, one template)](#3-the-tier-model)
4. [Dashboard sections (Tier 3 = full)](#4-dashboard-sections)
5. [CloudTrail prerequisite (optional Tier-3 enabler)](#5-cloudtrail-prerequisite)
6. [Tenant attribution architecture](#6-tenant-attribution-architecture)
7. [Test & CI architecture](#7-test--ci-architecture)
8. [Data generator architecture](#8-data-generator-architecture)
9. [File organization (responsibility map)](#9-file-organization)
10. [Key design decisions & why](#10-key-design-decisions--why)
11. [What it does NOT cover](#11-what-it-does-not-cover)

---

## 1. What it is

A **single CloudFormation template** (`cloudformation/dashboard.yaml`) that builds a multi-section CloudWatch dashboard for Amazon Bedrock **runtime** workloads (`InvokeModel`/`Converse`/`ConverseStream`/`InvokeModelWithResponseStream`). Targets multiple audiences in one pane: app teams, SRE, security/governance. Built entirely on **native AWS data** — no Athena, no CUR, no third-party SaaS.

---

## 2. The three data sources

```
┌───────────────────────────────────────────────────────────────────────────┐
│                   Bedrock InvokeModel / Converse calls                     │
└──────────────────────────┬────────────────────────────────────────────────┘
                           │
          ┌────────────────┼─────────────────────────────┐
          ▼                ▼                             ▼
┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────────────┐
│ CW Metrics      │  │ Model Invocation     │  │ CloudTrail management    │
│ (AWS/Bedrock)   │  │ Logs (CW Logs)       │  │ events (default-on)      │
│                 │  │                      │  │                          │
│ Always on, free │  │ Must enable per-     │  │ Need a trail → CW Logs   │
│ ~1 min latency  │  │ region; CW Logs dest │  │ delivery (~5-15 min lag) │
│                 │  │ ~30s latency         │  │                          │
│ Dimensions:     │  │ Fields: modelId,     │  │ Fields: userIdentity,    │
│   ModelId only  │  │ operation,           │  │ requestParameters.       │
│                 │  │ requestMetadata.*,   │  │   modelId,               │
│ Metrics:        │  │ input/output token   │  │ additionalEventData.     │
│ Invocations,    │  │ counts, full         │  │   inferenceRegion,       │
│ Latency, TTFT,  │  │ request/response     │  │ errorCode/Message,       │
│ Errors, Tokens, │  │ body, stopReason,    │  │ sourceIPAddress           │
│ Cache, Quota    │  │ guardrailAction      │  │                          │
└────────┬────────┘  └───────────┬──────────┘  └─────────────┬────────────┘
         │                       │                            │
         │ Tier 1                │ Tier 2                     │ Tier 3
         └───────────────────────┴────────────────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  CloudWatch Dashboard    │
                    │  (this repo's CFN)       │
                    │                          │
                    │  Sections 1-5 (always)   │
                    │  Section 6 (if Tier ≥ 2) │
                    │  Section 7 (if Tier 3)   │
                    └──────────────────────────┘
```

| Source | What it answers | Cost | Dashboard sections |
|---|---|---|---|
| **CW Metrics** (`AWS/Bedrock`) | How fast? How many? How many tokens? Throttled? | Free (first 10 metrics, then ~$0.30/metric) | 1–5 (always) |
| **Model Invocation Logs** | What did each tenant ask? Per-team chargeback. Guardrail interventions. | CW Logs ingest (~$0.50/GB) | 6 (Tier 2) |
| **CloudTrail** (management events) | Who called what? From where? Routed to which region? Why did it error? | Free capture; CW Logs ingest for query delivery | 7 (Tier 3) |

---

## 3. The tier model

```
                    ┌─────────────────────────────────────────────────┐
                    │      cloudformation/dashboard.yaml               │
                    │                                                   │
                    │  Parameters:                                      │
                    │    ModelInvocationLogGroup = '' ──► HasInvocationLogs
                    │    CloudTrailLogGroup      = '' ──► HasCloudTrail │
                    │                                                   │
                    │  Conditions decide which resources exist:         │
                    │                                                   │
                    │  ┌─────────────────────────────────────────┐     │
                    │  │ ALWAYS: Dashboard (Sections 1-5)        │     │
                    │  │         AlarmThrottleRate                │     │
                    │  │         AlarmServerErrors                │     │
                    │  └─────────────────────────────────────────┘     │
                    │                                                   │
                    │  ┌─────────────────────────────────────────┐     │
                    │  │ HasInvocationLogs:                       │     │
                    │  │   Dashboard Section 6 (5 widgets)        │     │
                    │  │   AlarmLogDeliveryFailures               │     │
                    │  │   QueryDefinitions 01-05                 │     │
                    │  └─────────────────────────────────────────┘     │
                    │                                                   │
                    │  ┌─────────────────────────────────────────┐     │
                    │  │ HasCloudTrail:                           │     │
                    │  │   Dashboard Section 7 (4 widgets)        │     │
                    │  │   QueryDefinitions 06-08                 │     │
                    │  └─────────────────────────────────────────┘     │
                    └─────────────────────────────────────────────────┘
```

The gating mechanism: the dashboard JSON body is composed via `Fn::Sub` with two injected variables (`${Section6}`, `${Section7}`) that resolve to either a comma-prefixed JSON fragment (when the condition is true) or an empty string (when false). This means the rendered dashboard **never has empty widgets** — sections are literally absent from the JSON.

**Upgrade path:** re-deploy with one more parameter, same stack name, same URL. CFN adds the new section in place.

---

## 4. Dashboard sections

```
y=0   ┌──────────────────── Title + metadata ────────────────────┐  always
y=2   ├──────────── § 1. Overview (last hour) ───────────────────┤  always
      │  Invocations │ Errors │ Total tokens                     │
y=7   ├──────────── § 2. Performance — by ModelId ───────────────┤  always
      │  Invocations │ Latency p50/p90/p99 │ TTFT │ Avg bar      │
y=20  ├──────────── § 3. Errors & Throttles (SRE) ──────────────┤  always
      │  4xx/5xx/Throttle │ Throttle rate (%) │ Legacy tracking   │
y=33  ├──────────── § 4. Tokens ──────────────────────────────────┤  always
      │  Input/Output │ Cache read/write                          │
y=46  ├──────────── § 5. Quota Headroom ─────────────────────────┤  always
      │  EstimatedTPMQuotaUsage │ Derived RPM                     │
y=55  ├──────────── § 6. Per-tenant attribution ─────────────────┤  Tier ≥ 2
      │  Top teams │ Tokens by model+op │ Delivery health │ Guard │
y=68  ├──────────── § 7. Identity & routing (CloudTrail) ────────┤  Tier 3
      │  Top callers │ Error reasons │ Cross-region routing       │
      └─────────────────────────────────────────────────────────────┘
```

**Widget count by tier:** Tier 1 = 23 · Tier 2 = 28 (+5) · Tier 3 = 32 (+4)

---

## 5. CloudTrail prerequisite

```
┌──────────────────────────────────────────────────────────────────────┐
│  cloudformation/cloudtrail-logging.yaml                              │
│                                                                      │
│  4 modes (parameter-gated):                                          │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ Greenfield      │  │ BYO Bucket      │  │ BYO Trail       │     │
│  │ (default)       │  │                 │  │ (CreateTrail=   │     │
│  │                 │  │                 │  │  false)          │     │
│  │ Trail ✓         │  │ Trail ✓         │  │ Trail ✗         │     │
│  │ S3 Bucket ✓     │  │ S3 Bucket ✗     │  │ S3 Bucket ✗     │     │
│  │ Bucket Policy ✓ │  │ (customer's)    │  │                 │     │
│  │ Log Group ✓     │  │ Log Group ✓     │  │ Log Group ✓     │     │
│  │ IAM Role ✓      │  │ IAM Role ✓      │  │ IAM Role ✓      │     │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘     │
│                                                                      │
│  + Optional KMS encryption across all modes                          │
│                                                                      │
│  Output: CloudTrailLogGroup ──► pass to dashboard stack              │
└──────────────────────────────────────────────────────────────────────┘
```

**Key facts:**
- Bedrock runtime calls are CloudTrail **management events** (`eventSource = bedrock.amazonaws.com`, `managementEvent: true`). Captured by default, no per-event charge.
- Data events ($0.10/100K) apply only to `InvokeModelWithBidirectionalStream`, `GetAsyncInvoke`, `StartAsyncInvoke`, Agents, KBs, Flows, Guardrails — out of scope.
- The trail is **multi-region**: deploy it **once** per account (IAM role name is global, would collide on a second regional deploy). One trail captures Bedrock calls across all regions into one log group.
- First delivery to a brand-new trail takes ~10–15 minutes.

---

## 6. Tenant attribution architecture

```
Caller application                   Dashboard Section 6
─────────────────                    ─────────────────────

client.converse(                     Logs Insights query:
  modelId=...,                       ───────────────────
  messages=[...],                    SOURCE '<LogGroup>'
  requestMetadata={                  | fields requestMetadata.${TenantMetadataKey}
    "<TenantMetadataKey>": "team-x", │   as tenant, ...
    "app": "chatbot",               | filter ispresent(tenant)
    "env": "prod"                    | stats ... by tenant
  }                                  | sort total_tokens desc
)
         │                                      ▲
         ▼                                      │
  Bedrock Model Invocation Log              Logs Insights
  ─────────────────────────────             reads from
  { "requestMetadata": {                    the log group
      "team": "team-x", ... },
    "input": { "inputTokenCount": 450 },
    "output": { "outputTokenCount": 120 }
  }
```

The `TenantMetadataKey` parameter (default `team`) flows into:
- The 2 Section-6 widget queries (via the Section6 `Fn::Sub` fragment's inner var map)
- The 3 saved `QueryDefinitions` 01/03/04 (via `!Sub` in their `QueryString`)

**Log schema key fields (verified against AWS docs):**
- `requestMetadata` — arbitrary string keys (present only when the caller tags). Up to 16 keys, 256 chars each.
- `input.inputTokenCount` / `output.outputTokenCount` — per-call token counts.
- `output.outputBodyJson` — model-specific. `stopReason` and `amazon-bedrock-guardrailAction` live here (Anthropic-shaped; may differ for Nova/Llama/Titan).
- Cache-token counts are **CloudWatch metrics only**, not in the log schema.

---

## 7. Test & CI architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  scripts/render_dashboard.py                                     │
│  ─────────────────────────────                                   │
│  Local CFN intrinsic resolver (SafeLoader-based).                │
│  Resolves: Fn::Sub, Fn::If, Fn::FindInMap, Fn::Equals,         │
│            Fn::Not, Fn::Join, Ref, AWS::NoValue (drops it).     │
│  Input: tier (1/2/3) + param overrides                           │
│  Output: the rendered dashboard JSON body                        │
│                                                                  │
│  Used by pytest to validate per-tier structure WITHOUT deploying. │
└──────────────────────────────┬──────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────────┐
            ▼                  ▼                      ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────┐
│ test_tier_gating │ │ test_dashboard_  │ │ conftest             │
│                  │ │ json             │ │                      │
│ Section presence │ │ No widget overlap│ │ Shared fixtures      │
│ Widget count     │ │ Grid bounds      │ │ Render helpers       │
│ Condition gates  │ │ CT-only layout   │ │ Per-tier params      │
│ Query names      │ │                  │ │                      │
└──────────────────┘ └──────────────────┘ └──────────────────────┘
            │                  │                      │
            └──────────────────┴──────────────────────┘
                               │
            ┌──────────────────┼──────────────────────┐
            ▼                  ▼                      ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────┐
│ test_template_   │ │ test_query_sync  │ │ test_metric_names    │
│ structure        │ │                  │ │                      │
│                  │ │ .cwli ↔ QueryDef │ │ Metric spelling vs   │
│ Params, alarms   │ │ NN-paired exact  │ │ template + live acct │
│ Tenant token     │ │ equality         │ │                      │
│ Alarm wiring     │ │ (3-place rule)   │ │                      │
└──────────────────┘ └──────────────────┘ └──────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  scripts/check_query_sync.py                                         │
│  ─────────────────────────────                                       │
│  NN-paired EXACT normalized equality between .cwli files and         │
│  QueryDefinition resources. Normalizes: whitespace collapse +         │
│  ${TenantMetadataKey} → <param default>. Handles QueryString as      │
│  plain str or Fn::Sub dict, Name as str or Fn::Sub.                  │
└──────────────────────────────────────────────────────────────────────┘

CI (configure in your platform's pipeline):
  lint:     cfn-lint dashboard.yaml
  test:     pytest -m "not sandbox" + render all 3 tiers + sync check
  security: secret-detection scan
```

**27 tests** cover: tier gating correctness, widget-overlap prevention (4 layout variants), SEARCH-fragment region-agnosticism, eventSource consistency across widgets+queries, tenant-key substitution, three-place query sync, metric-name spelling, and parameter structure.

---

## 8. Data generator architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  data-generator/generate.py  (async Python, single-process)          │
│                                                                      │
│  ┌────────────┐    ┌─────────────────────────────────────────────┐  │
│  │ SCHEDULE   │    │ Per iteration:                               │  │
│  │ (diurnal)  │    │  pick model (weighted) → pick team/app →    │  │
│  │ 2 cpm night│    │  50% stream (ConverseStream)                │  │
│  │ 10 steady  │───▶│  40% cache (system prompt > 1024 tok)       │  │
│  │ 60 burst   │    │  5% intentional 4xx (bad model id)          │  │
│  └────────────┘    │  varied maxTokens (50-1500)                 │  │
│                    │  tag requestMetadata with chosen team        │  │
│                    └─────────────────────────────────────────────┘  │
│                                                                      │
│  Env overrides (no source edit needed):                              │
│    AWS_REGION ─── target region (default us-east-1)                  │
│    BEDROCK_MODELS ─── semicolon-separated catalog override           │
│    TENANT_METADATA_KEY ─── match dashboard's TenantMetadataKey       │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼  drives real Bedrock API calls
┌──────────────────────────────────────────────────────────────────────┐
│  Result: every widget populates within ~10 min:                      │
│  ✓ Per-model latency          ✓ Per-team attribution                │
│  ✓ TimeToFirstToken (stream)  ✓ Error widgets (4xx)                 │
│  ✓ Cache read/write           ✓ Stop-reason mix                     │
│  ✓ Diurnal traffic shape      ✗ CloudTrail identity (uses own IAM)  │
└──────────────────────────────────────────────────────────────────────┘
```

**Cost:** ~$15–25 over 24h at us-east-1 list prices (dominated by Sonnet/Opus calls).

---

## 9. File organization

```
bedrock-llm-dashboard/
├── cloudformation/
│   ├── dashboard.yaml              ← THE product: dashboard + alarms + queries
│   └── cloudtrail-logging.yaml     ← Optional Tier-3 enabler (4 modes)
├── queries/logs-insights/
│   └── 01..08-*.cwli              ← Standalone query copies (3-place sync)
├── data-generator/
│   ├── generate.py                ← Synthetic traffic for demos
│   ├── README.md + OPERATIONS.md  ← Usage + lifecycle
│   └── requirements.txt           ← boto3
├── scripts/
│   ├── render_dashboard.py        ← Local CFN resolver (test infra)
│   ├── check_query_sync.py        ← 3-place sync enforcer (test infra)
│   └── deploy_and_verify.sh       ← Sandbox deploy-assert-teardown
├── tests/
│   ├── conftest.py                ← Session fixtures (template, rendered tiers)
│   ├── test_tier_gating.py        ← Section presence/absence, conditions
│   ├── test_dashboard_json.py     ← Overlap, grid bounds, CT-only layout
│   ├── test_template_structure.py ← Params, tenant token, alarm wiring
│   ├── test_query_sync.py         ← .cwli ↔ QueryDef exact equality
│   ├── test_metric_names.py       ← Referenced metrics exist in template + live
│   └── fixtures/bedrock_metrics.json ← Pinned live metric-name snapshot
├── docs/
│   ├── DEPLOYMENT.md              ← End-to-end deployment guide
│   ├── ARCHITECTURE-DETAILED.md   ← This file
│   ├── setup.md                   ← Prereqs + per-tier enable + all 4 CT modes
│   ├── architecture.md            ← Panel + metric + log-field reference
│   ├── data-sources-comparison.md ← CW Metrics vs Logs vs CloudTrail trade-offs
│   └── iam-policy.md              ← Least-privilege deploy/view policies
└── README.md                      ← Tier table, deploy commands, operate table
```

---

## 10. Key design decisions & why

| Decision | Why |
|---|---|
| **YAML CloudFormation, not CDK/Terraform** | Lowest barrier for any customer: one `aws cloudformation deploy` command. Editable in console for one-off tweaks. |
| **One template, parameter-gated tiers** (not separate templates) | Upgrade = re-deploy with one more param; same stack, same URL, no teardown. No "which template am I on?" confusion. |
| **SEARCH expressions** (not hardcoded model IDs in metric widgets) | Any new model auto-appears in latency/errors/tokens widgets — no template edit. |
| **Three-place query sync** | The same Logs Insights query lives in the widget, the saved QueryDefinition, and the `.cwli` file. Drift = silent widget/query mismatch. A test enforces exact equality. |
| **`TenantMetadataKey` parameter** | Different orgs use different `requestMetadata` keys for chargeback. One param, not a fork. |
| **CloudTrail = management events** (not data events) | Runtime calls are default-on/free-capture management events (`eventSource = bedrock.amazonaws.com`). Data events ($0.10/100K) only apply to async/agent ops (out of scope). Saves customers ~$1,100/mo vs the wrong guidance. |
| **Separate CloudTrail prerequisite template** | Decouples "I need a trail" from "I need the dashboard." 4 modes (greenfield/BYO-bucket/BYO-trail/KMS) handle any customer without touching existing resources. |
| **Local renderer + 27 tests** | JSON-in-Fn::Sub is fragile; cfn-lint can't validate it. The renderer + structural tests catch every layout/gating/comma/Y-coordinate regression without deploying. |
| **Explicit `DependsOn: TrailBucketPolicy`** | CloudTrail validates bucket write access at create time; without explicit ordering, the trail can be created before the bucket policy and fail with AccessDenied. |
| **No `s3:x-amz-acl` condition on bucket policy** | Bucket uses `BucketOwnerEnforced` (ACLs disabled), so CloudTrail never sends the `x-amz-acl` header. Requiring it = every PutObject denied. |
| **IAM log-stream resource from NAME, not GetAtt Arn** | `AWS::Logs::LogGroup.Arn` ends in `:*`; appending `:log-stream:...` to it yields a malformed ARN that CloudTrail's `CreateLogStream`/`PutLogEvents` is denied against. Build the resource from the log-group name string instead. |

---

## 11. What it does NOT cover

- Bedrock **Agents** (`AWS/Bedrock/Agents` namespace)
- Bedrock **Guardrails standalone** metrics (`AWS/Bedrock/Guardrails`)
- **Knowledge Bases** (ingestion-job events)
- **Provisioned Throughput** utilization (no native metric)
- Athena/CUR-based cost analysis
- Multi-account aggregation (org trails)
- `InvokeModelWithBidirectionalStream` / async invocations (data events, out of scope)

Each warrants its own dashboard — they have different audiences, dimensions, and data shapes.
