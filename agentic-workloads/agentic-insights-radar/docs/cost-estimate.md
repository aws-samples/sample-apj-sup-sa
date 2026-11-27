# Cost estimate: Agentic Insights Radar on AWS

You are responsible for the cost of the AWS services used while running this sample. As of
October 2026, the cost for running this sample with the default settings in the US East
(N. Virginia) Region is approximately USD 300 per month for the
[typical deployment](#the-typical-deployment) below: 10 users and the six scheduled built-in
modules turned on. About USD 16 of that is fixed; the rest scales with use and is capped by the
token budgets. To check the estimate for your own workload, enter the dimensions in the tables
below into the [AWS Pricing Calculator](https://calculator.aws/#/); model and AgentCore usage
depend on your sources and questions more than on any setting.

Prices below are AWS public list prices for US East (N. Virginia) `us-east-1`, read from the AWS Price List
API in October 2026, before any Free Tier, credits or discounts. They change over time; check each
service's pricing page.

## Summary

| Scenario | What it assumes | Monthly cost (USD) |
|---|---|---|
| Idle | Deployed, no modules enabled, nobody signed in | about 16 |
| Light | Six scheduled modules at 10% of their budgets, 300 chat turns, 3 web searches per run | about 130 |
| **Typical** | Six scheduled modules at 25% of their budgets, 1,100 chat turns, 10 drafts, 10 web searches per run | **about 300** |
| At the default caps | Every module, drafts and chat spend their full daily budgets every day, 20 web searches per run | about 1,100 |

Amazon Bedrock model tokens are most of the bill. They are capped by the per-module and chat
budgets; see [Controlling cost](#controlling-cost).

## The typical deployment

- Region `us-east-1`, default deployment options.
- 10 users; each asks the chat assistant 5 questions per working day (22 days): 1,100 turns.
- The six scheduled built-in modules (Scout, Signals, Trends, Gaps, Content ideas, Radar curator)
  are turned on with their defaults: every 6 hours (4 runs per day each, 720 runs per month),
  Claude Sonnet 5.5, a 2,000,000 weighted-token daily budget each.
- Content drafts runs on demand: 10 drafts per month.
- 1 product profile with about 200 public documentation pages, re-crawled weekly, and a few
  uploaded documents (under 50 MB in total).
- A 30-day month.

## Monthly cost by service (typical)

### Fixed costs (paid even when idle)

| Service | Dimension | Price | Monthly (USD) |
|---|---|---|---|
| AWS WAF | 1 web ACL | $5.00 per web ACL per month | 5.00 |
| AWS WAF | 4 rules: the rate-based rule and 3 AWS managed rule groups (each counts as one rule) | $1.00 per rule per month | 4.00 |
| AWS KMS | 2 customer managed keys (data key, run-token HMAC key) | $1.00 per key per month | 2.00 |
| Amazon CloudWatch | 44 standard-resolution alarms (2 for each of 9 functions, 8 for runs, queues, the indexer, the API and the workspace rate ceiling, 4 for the two agent tool gates' function URLs, 10 for the two runtimes, 1 for the account's AgentCore sessions, 1 for the web-search gateway, 2 for the edge in a `us-east-1` deployment) reading 48 metrics: the four rate alarms (the HTTP API's and the two tool gates' 4xx rates, CloudFront's 5xx rate) each read two; and 1 dashboard | $0.10 per alarm metric per month; the first 3 dashboards in an account are free | 4.80 |
| AWS Lambda | `Sweep` every 5 minutes: 8,640 invocations at 1 GB for about 2 s | $0.0000133334 per GB-second (arm64), $0.20 per million requests | 0.25 |
| Amazon DynamoDB, CloudWatch Logs | The sweep's reads and log lines | see below | 0.05 |
| **Fixed total** | | | **about 16** |

Amazon Cognito, AgentCore, Step Functions, API Gateway and S3 Vectors have no charge while idle.
Outside `us-east-1` the edge stack adds one more KMS key for its WAF log ($1.00 a month).

### Usage costs

| Service | Assumption | Price | Monthly (USD) |
|---|---|---|---|
| Amazon Bedrock, modules | 6 modules x 500,000 weighted tokens per day (25% of 2,000,000) x 30 days = 90 M weighted tokens | Claude Sonnet 5.5 global: $2.00 input, $10.00 output, $2.50 cache write, $0.20 cache read per million tokens, which is exactly $2.00 per million weighted tokens | 180.00 |
| Amazon Bedrock, chat | 1,100 turns x 25,000 weighted tokens = 27.5 M | as above | 55.00 |
| Amazon Bedrock, drafts | 10 drafts x 200,000 weighted tokens = 2 M | as above | 4.00 |
| Amazon Bedrock, embeddings | About 15 M tokens: 200 pages re-crawled 4 times, uploads, graph node changes, search queries | Titan Text Embeddings V2: $0.02 per million tokens | 0.30 |
| Amazon Bedrock Guardrails, content filters | About 40,000 text units (a text unit is up to 1,000 characters): the newest user message and each model response | Content filters (including prompt attack): $0.15 per 1,000 text units | 6.00 |
| Amazon Bedrock Guardrails, sensitive information filter | About 20,000 text units: the guardrail also masks AWS keys and card numbers in model output, which is billed separately from content filters | Sensitive information filter: $0.10 per 1,000 text units | 2.00 |
| AgentCore Runtime, modules | 730 runs; each billed for about 25 minutes at 2 GB, with 1 vCPU active 10% of that time | $0.0895 to $0.1276 per vCPU-hour and $0.00945 to $0.0169 per GB-hour (the Price List shows two rate cards) | 8.50 to 14.20 (11 used) |
| AgentCore Runtime, chat | 220 sessions of about 10 minutes at 2 GB (a session ends after 5 idle minutes); 30 s of active CPU per turn | as above | 1.10 to 1.85 (2 used) |
| AgentCore Gateway web search | 2 modules with the `web_search` grant (Scout, Signals) x 120 runs x 10 queries = 2,400 queries (a run may make at most 60) | $0.007 per query | 16.80 |
| AgentCore Gateway | 2,400 tool invocations | $0.000005 per invocation | 0.01 |
| AgentCore Memory | 1,100 turns x 2 short-term events | $0.00025 per event | 0.55 |
| AgentCore Browser | Scout: 120 runs x 5 minutes, 1 vCPU active half the time, 2 GB | $0.0895 per vCPU-hour, $0.00945 per GB-hour | 0.65 |
| AgentCore Code Interpreter | Nothing: no built-in module holds the `code_interpreter` grant. A custom module that does is billed here, per minute of session | $0.0895 per vCPU-hour, $0.00945 per GB-hour | 0.00 |
| AWS WAF requests | About 200,000 requests | $0.60 per million | 0.12 |
| AWS KMS requests | About 200,000 (run-token MAC signing and verification on every agent tool call, data key use) | $0.03 per 10,000 | 0.60 |
| AWS Lambda | API: 150,000 requests at 1.5 GB for 0.3 s; indexer, crawler, invoke and finalize | $0.0000133334 per GB-second, $0.20 per million requests | 1.20 |
| Amazon API Gateway | 150,000 HTTP API requests (users, MCP, chat reads); agent tool calls go to Lambda function URLs, which have no request charge | $1.00 per million | 0.15 |
| AWS Step Functions | 730 runs x 5 state transitions | $0.000025 per transition | 0.09 |
| Amazon DynamoDB | 300,000 write and 3 million read request units; about 1 GB stored with point-in-time recovery | $0.625 per million writes, $0.125 per million reads, $0.20 per GB-month for PITR; first 25 GB of storage free | 0.80 |
| Amazon S3 | About 2 GB (documents, crawled pages, versions, site, access logs); 50,000 PUT/LIST and 200,000 GET requests | $0.023 per GB-month, $0.005 per 1,000 PUT/LIST, $0.0004 per 1,000 GET | 0.40 |
| Amazon S3 Vectors | About 0.3 GB stored, 0.2 GB written, 30,000 queries | $0.06 per GB-month, $0.20 per GB written, $0.0000025 per query plus processed bytes | 0.15 |
| Amazon CloudWatch Logs | About 2.3 GB ingested (including about 0.3 GB of WAF request logs), 10 GB stored | $0.50 per GB ingested, $0.03 per GB-month | 1.45 |
| AWS X-Ray | About 160,000 traces, first 100,000 free | $5.00 per million traces stored | 0.30 |
| Amazon Cognito | 10 monthly active users, Essentials plan | Free for the first 10,000 MAUs; then $0.015 per MAU | 0.00 |
| Amazon CloudFront | 10 users of a single-page app | Within the always-free allowance (1 TB out and 10 million requests per month); then from $0.0075 per 10,000 requests | 0.00 |
| Amazon SNS, Amazon SQS, Amazon EventBridge | Alarm notifications, dead-letter queues, one schedule rule | First 1 million SNS requests free; SQS $0.40 per million | 0.01 |

Rounded by group, the typical month is:

| Group | Monthly (USD) |
|---|---|
| Amazon Bedrock models and embeddings | 239 |
| Amazon Bedrock AgentCore (Runtime, web search, Gateway, Memory, Browser, Code Interpreter) | 31 |
| AWS WAF | 9 |
| Amazon Bedrock Guardrails | 8 |
| Everything else (KMS, alarms, Lambda, API Gateway, Step Functions, DynamoDB, S3, S3 Vectors, logs, X-Ray, Cognito, CloudFront, SNS, SQS) | 12 |
| **Total** | **about 300** |

## Assumptions and uncertainty

- **Weighted tokens.** Budgets count `input + 5 x output + 1.25 x cache write + 0.1 x cache read`
  (`backend/src/insights_radar/core/tokens.py`). For Claude Sonnet 5.5 and Claude Haiku 4.5 through
  their `global.` profiles these weights match the list prices exactly, so cost is weighted tokens
  times the input price ($2.00 per million for Sonnet 5.5, $1.00 for Haiku 4.5). For Claude Opus
  5.5 and Claude Fable 5.1 cache reads cost less than the weight assumes, so the same formula is an
  upper bound. Amazon Nova 2 Lite (`global.` profile, Price List API, October 2026: $0.30 input,
  $2.50 output, $0.075 cache read per million tokens, cache writes free) does not follow these
  weights: per million weighted tokens, input costs $0.30, output $0.50 and cache reads $0.75,
  so a 2,000,000 daily budget costs at most $1.50 a day.
- **Budget use.** How much of its budget a module spends depends on how much new material your
  sources produce. A module with nothing new to process exits early; a busy Scout can use its full
  budget. 25% is an assumption; the light and cap scenarios bracket it.
- **Chat turns.** 25,000 weighted tokens per turn assumes a few tool calls with cached context.
  Long, multi-step questions cost more; each turn is capped at 12 model calls and by the budgets.
- **AgentCore Runtime.** Runtime bills active CPU and session memory. The session length, memory
  footprint and CPU share above are assumptions, and the Price List API returned two rate cards
  for Runtime consumption, so the estimate gives a range.
- **Web search.** Two built-in modules hold the `web_search` grant (Scout and Signals);
  Trends, Gaps, Content ideas, Content drafts and the Radar curator do not. The number of searches per run
  is up to the agent, at most 60 per run (the runtime refuses more); the estimate uses 10 (3 light,
  20 at the caps). Without the web-search connector this line is zero. Browser, Code Interpreter,
  attached MCP servers and `http_fetch` are capped at 120, 30, 200 and 120 calls per run.
- **Tool caps are per run, not per day.** Nothing limits a module's tool spend over a day, and no
  alarm watches it: the token budgets meter model calls only. A module on the 15-minute minimum
  schedule makes 96 runs a day, so 60 web searches a run is up to 5,760 queries -- about USD 40 a
  day, USD 1,200 a month, from one module, multiplied by its `max_concurrency` (1 to 10) and by
  every other module with the grant. Keep the schedule at hours rather than minutes for modules with
  `web_search`, `browser`, `code_interpreter` or `mcp`, set `AIR_MONTHLY_BUDGET_USD` so the account
  budget (`<prefix>-insights-radar-<region>-account`, the one that sees model and tool spend) alerts, and watch the **AgentCore web search** line in Cost Explorer with the
  `deployment` cost allocation tag. There is no per-day tool ceiling to configure.
- **Bedrock Knowledge Bases.** The estimate assumes no separate charge for `Retrieve` and ingestion
  on a knowledge base with your own S3 Vectors store, so you pay for embeddings and the vector
  store. Verify this in the Pricing Calculator.
- **Free Tier.** Line items do not deduct the Lambda, DynamoDB, CloudWatch, SNS or other Free Tier
  allowances your account may have, except where noted (Cognito, CloudFront, X-Ray).

## Scenario details

| Group | Light | Typical | At the default caps |
|---|---|---|---|
| Bedrock models: modules | 72 (10% of budgets) | 180 | 720 (6 x 2 M x 30 days x $2 per million) |
| Bedrock models: chat | 15 (300 turns) | 55 | 120 (2 M shared per day x 30) |
| Bedrock models: drafts | 1 | 4 | 120 (2 M per day x 30) |
| AgentCore web search | 5 (3 per run) | 17 | 34 (20 per run) |
| AgentCore Runtime, Memory, Gateway, tools | 9 | 14 | 30 |
| Guardrails (content and sensitive information filters) | 4 | 8 | 20 |
| Fixed costs | 16 | 16 | 16 |
| Other usage | 5 | 7 | 16 |
| **Total (rounded)** | **about 130** | **about 300** | **about 1,100** |

## Controlling cost

Model tokens dominate, and every model call in this sample goes through a budget. The budgets
count model tokens only: web search (per query), Browser and Code Interpreter (per minute) and
AgentCore Runtime session time are billed outside them, capped by the per-run call limits above and
by how often modules run. Search and product questions embed or retrieve on every request outside
any budget; their API routes are throttled at 10 requests per second.

1. **Keep modules off until you need them.** All modules ship turned off. Turn on only the ones you
   use; each enabled module can spend up to its daily budget every day.
2. **Set each module's daily budget** on its page (weighted tokens, 0 to 100,000,000; default
   2,000,000). At Claude Sonnet 5.5 prices, 2,000,000 weighted tokens is at most $4.00 a day, or
   about $120 a month, per module. A run stops when the budget is used up; budgets reset at
   00:00 UTC. Size it as runs per day x the most one run uses (a module every 6 hours with a
   400,000-token worst run needs 1,600,000), since a run holds what is left of the day when it starts.
3. **Set the chat limits** in **Administration > AI controls**: a shared daily limit (default
   2,000,000) and a per-user limit (default 300,000).
4. **Run less often.** A 12-hour or 24-hour schedule halves or quarters the number of runs. Use
   on demand (`0`) for modules you trigger by hand.
5. **Choose a cheaper model.** Claude Haiku 4.5 costs half as much per weighted token as Claude
   Sonnet 5.5; Claude Opus 5.5 costs about twice as much and Claude Fable 5.1 about five times.
6. **Limit tool grants.** Web search is charged per query; remove the `web_search` grant from
   modules that do not need it, or deploy with `AIR_WEB_SEARCH=false`. A grant removed from a
   **built-in** module comes back at the next release, because `Seed` resets every built-in's
   grants to the template (it records the change as a `module.migrate` audit row and nothing else
   tells you). `AIR_WEB_SEARCH=false` and a custom module with the grants you want are the two
   durable ways; see [Upgrading](implementation-guide.md#upgrading).
7. **Watch tool spend separately.** The per-run caps above are the only limit on web search,
   Browser, Code Interpreter and MCP calls; there is no per-day ceiling and no alarm on them. The
   levers are the schedule, `max_concurrency` and which modules hold the grant.
8. **Use the AI kill switch** in **Administration > AI controls** to stop every model call: new runs
   and chat turns at once, a run already in flight within a minute.
9. **Set an AWS Budget** with an alert on your expected monthly spend: deploy with
   `AIR_MONTHLY_BUDGET_USD=<amount>` and the stack creates two that notify the alarm topic at 80%
   of actual and 100% of forecast spend: one for the whole account, which sees the model spend
   (model invocations through the `global.` inference profiles carry no tag), and one filtered to
   this deployment's `deployment=<prefix>` tag, which sees only its tagged resources (activate the
   tag as a cost allocation tag first). AWS Budgets charges nothing for the first two budgets in
   an account. Review the **Administration > Health** page, which shows each
   module's budget use for the day. A module budget can be up to 100,000,000 weighted tokens a day,
   about $200 a day on Claude Sonnet 5.5 or $1,000 on Claude Fable 5.1: check for a stray zero.

The ceiling on model tokens with the default budgets is the sum of all budgets: with six scheduled
modules, drafts and chat at their defaults on Claude Sonnet 5.5, about $960 a month. Tool and
runtime charges come on top.
Raising budgets or adding custom modules raises the ceiling by about $120 a month for every
additional 2,000,000 weighted tokens per day.

## Optional features

| Option | What it adds | Monthly (USD) |
|---|---|---|
| `AIR_BACKUP=true` | AWS Backup: daily recovery points of both tables and the assets bucket, kept 120 days | about 1 for the typical data (DynamoDB backup $0.10 per GB-month, S3 backup $0.05 per GB-month) |
| `AIR_MONTHLY_BUDGET_USD` | Two AWS Budgets (account-wide and deployment-tagged) with two alerts each | 0 for the first two budgets in the account |
| `AIR_PRICE_CLASS=200` or `All` | More CloudFront edge locations | Data transfer is billed at the price of the edge that serves it |
| `AIR_RESERVED_CONCURRENCY=true` | Reserved Lambda concurrency for the run bookkeeping functions | 0 |
