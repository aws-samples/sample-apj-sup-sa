# opencode on Amazon Bedrock AgentCore Gateway

Run the [opencode](https://opencode.ai) coding agent against open-weight models on Amazon Bedrock, through an
[Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) that
your organization controls. Developers sign in with your identity provider (Okta, JumpCloud, or a Cognito stand-in);
the gateway decides which models each group may use, enforces per-user rate limits and dollar budgets, falls back to
another model when one is throttled or unavailable, and attributes every token to the person who used it.

> **Status:** sample code, not production-ready as is. Review the [Security](#security) and
> [Limitations](#limitations) sections and run your own security review before you deploy it for real users.
> This is sample code, for non-production usage. You should work with your security and legal teams to meet your
> organizational security, regulatory and compliance requirements before deployment.

## Why

opencode can call Amazon Bedrock directly with each developer's AWS credentials
([AWS blog, 23 Sep 2026](https://aws.amazon.com/blogs/machine-learning/use-open-weight-models-as-your-ai-coding-agent-with-amazon-bedrock/)).
That works for one person. For a team it leaves gaps: every laptop needs AWS credentials, IAM can allow or deny a model
but cannot cap a person's spend, and there is no per-user rate limit, fallback or usage view. opencode's own
Bedrock provider has also had model-ID and tool-calling problems with some open-weight models (see
[docs/background.md](docs/background.md)).

This sample puts a managed gateway in between:

| Need | How this sample meets it | Built on |
|---|---|---|
| Sign in with the company IdP, no AWS credentials on laptops | OIDC sign-in (PKCE) with `ocgw`; the gateway validates the JWT | AgentCore Gateway JWT authorizer |
| Which models each team may use | Group allow-list checked on every call, `/v1/models` lists only allowed models | REQUEST interceptor (Lambda) |
| Requests and tokens per minute per user | Gateway rate limits keyed on the user claim | AgentCore Gateway rate limits (native) |
| Dollar budgets per user (monthly, daily, hourly), checked before every call; spend arrives minutes late (Limitations, Budget lag) | Spend ledger in DynamoDB, checked before every call | Interceptor + usage sync (custom) |
| Keep working when a model is throttled or down | Fallback chain, for example GLM 5.3 to Kimi K3 | AgentCore Gateway model fallback (native) |
| Who used what, and what it cost | Per-user Mantle projects, priced every 5 minutes, rolled up by user, group, model and tag | Mantle metrics + usage sync |
| Web search for open-weight models | AgentCore Web Search as an MCP server opencode can call, gated and metered | AgentCore Gateway MCP target |
| Admin view | Dashboard: spend, users, budgets, rate limits, model access, audit log | Lambda + React (Cloudscape), optional ALB sign-in |
| Fleet rollout | macOS package and managed opencode config (MDM) | `mdm/` |

## Architecture

```
 developer laptop                                  AWS account (us-east-1)
 +-----------------------+         HTTPS          +---------------------------------------------------------+
 | opencode              |  Bearer <IdP token>    | AgentCore Gateway  (CUSTOM_JWT: Okta / JumpCloud / Cognito)|
 |  provider "ocgw"      | ---------------------> |   REQUEST interceptor (Lambda)                            |
 |  baseURL 127.0.0.1    |                        |     identity, group allow-list, $ budgets, rpm,           |
 |        |              |                        |     ow/<alias> -> Mantle model, per-user OpenAI-Project   |
 |  ocgw proxy           |                        |   rate limits (RPM / TPM per user)                        |
 |   adds a fresh token  |                        |   fallback rule (GLM 5.3 -> Kimi K3)                      |
 |  ocgw login (PKCE) ---+--> IdP                 |   target "ow": bedrock-mantle connector  --> Amazon Bedrock|
 +-----------------------+                        |                                             (Mantle)      |
                                                  | Web search gateway (MCP) -> AgentCore Web Search          |
                                                  | usage_sync (every 5 min): Mantle metrics -> DynamoDB      |
                                                  | dashboard (Lambda + React), optional ALB + IdP sign-in    |
                                                  +---------------------------------------------------------+
```

1. `ocgw login` signs the developer in with authorization code + PKCE and keeps the tokens in a 0600 file.
2. opencode talks to `ocgw proxy` on 127.0.0.1 with a per-machine key; the proxy adds a fresh IdP token to each
   request, so a long session never sends an expired one.
3. The gateway validates the token. The interceptor checks the group allow-list, the budgets and the per-user rpm,
   then rewrites `ow/kimi-k3` to the Mantle model and adds the user's Mantle project.
4. The gateway applies its rate limits and fallback rule and calls Amazon Bedrock (Mantle) with its own IAM role.
5. Every 5 minutes `usage_sync` reads Mantle's per-project token metrics, prices them, and updates the ledger the
   budget checks and the dashboard read.

## Prerequisites

- An AWS account with access to Amazon Bedrock open-weight models in us-east-1 (Kimi K3, GLM 5.3, DeepSeek V3.2, GLM 5)
  and Amazon Bedrock AgentCore.
- Terraform 1.9 or later, AWS CLI v2, Python 3.10+ with `boto3` 1.43.118 or later (the deploy helpers use it), and
  [uv](https://docs.astral.sh/uv/): `terraform plan` uses it to build the dashboard's boto3 layer, and the tests use
  it. The deploy helpers run through `terraform/helpers/python.sh`, which uses the repo's `.venv` when it has boto3
  1.43.118 or later and `requests`, else `python3` when that has them, else uv. The helpers also check boto3's
  version themselves, so a `python` or `rule_python` you set by hand stops with a message when it is too old.
- opencode 1.18 or later on the developer machine.
- Optional: an Okta or JumpCloud tenant. Without one, use the Cognito stand-in.

## Deploy

```bash
cd terraform
cp cognito.tfvars.example terraform.tfvars      # or okta.tfvars.example / jumpcloud.tfvars.example
terraform init
terraform apply
```

For Okta or JumpCloud, follow [docs/okta.md](docs/okta.md) or [docs/jumpcloud.md](docs/jumpcloud.md) first: create a
native (public, PKCE) OIDC app, add a `groups` claim, and create the `ocgw-team-a` and `ocgw-team-b` groups.
List each person in `users` (ledger name and group) and map their IdP user ID (`sub`) to that name in
`identity_map`; anyone not in the map is refused.

## Set up opencode

```bash
# once per machine (MDM can install the config file instead)
mkdir -p -m 700 ~/.config/ocgw
# Cognito stand-in: Terraform writes the whole file
terraform -chdir=terraform output -raw ocgw_config > ~/.config/ocgw/config.json
# Okta / JumpCloud: your app's issuer and client ID
cat > ~/.config/ocgw/config.json <<EOF
{"issuer": "<issuer URL>", "client_id": "<client ID>", "gateway_url": "$(terraform -chdir=terraform output -raw gateway_base_url)", "websearch_url": "$(terraform -chdir=terraform output -raw websearch_mcp_url)"}
EOF
python3 client/ocgw.py login            # browser sign-in
python3 client/ocgw.py opencode-config  # adds the "ocgw" provider to ~/.config/opencode/opencode.json
python3 client/ocgw.py proxy &          # keep running while you use opencode
opencode                                # /models -> pick an ocgw model, for example ow/kimi-k3
```

## Features in detail

- **Model allow-list.** `group_models` in Terraform sets the default per group; admins can change it in the dashboard.
  A user in several groups gets the union. A model a group may not use is refused with 403 and is not listed.
- **Budgets.** Monthly (default $50, `default_budget_usd`), daily and hourly caps per user (`default_daily_usd`, `default_hourly_usd`, off by default), with group and organization defaults. A call
  over budget is refused with `budget_exceeded` and a message saying when the window resets.
- **Rate limits.** The gateway's own rate limits (default 60 requests and 2,000,000 tokens per minute per user) plus a
  per-user requests-per-minute cap in the interceptor that admins can set from the dashboard.
- **Fallback.** `fallback_rules` defines chains. Terraform refuses a chain whose fallback model is not allowed for every
  group that may use the primary model, so a fallback can never serve a model the caller was not allowed.
- **Web search.** opencode can add the web search gateway as a remote MCP server. Each search needs model access,
  counts against the caller's rpm and budget, and is charged at $0.007.
- **Attribution.** Spend and tokens per user, group, model and directory tag (team, project, environment).
- **Dashboard.** See [docs/dashboard.md](docs/dashboard.md).

## Costs

You pay for Amazon Bedrock tokens at the model's on-demand price, AgentCore Gateway requests, AgentCore Web Search
queries, Lambda, DynamoDB on-demand, CloudWatch Logs and, if enabled, the ALB and AWS WAF in front of the dashboard. See
[docs/costs.md](docs/costs.md) for a worked estimate.

## Security

See [SECURITY.md](SECURITY.md) and [docs/security.md](docs/security.md) for the threat model, the controls and the
scan results. Key points: the interceptor fails closed, client-sent identity and project headers are dropped, the
local proxy binds 127.0.0.1 only and needs a per-machine key, Lambda logs and the ledger are encrypted with a
customer managed KMS key, and each Lambda role may write only its own ledger partitions (on `USER#` items, only the
attributes named for it) and its own log group. Wider grants: table reads cover the whole ledger, the gateway role
may call every Mantle project in the account, the web search role may read any configuration bundle in the account,
and the usage sync's CloudWatch `ListMetrics` and `GetMetricData`, the X-Ray daemon writes
(`AWSXRayDaemonWriteAccess`) and the gateway role's Marketplace subscribe (only when Mantle makes the call) are on
`Resource "*"`; see [docs/security.md](docs/security.md).

Before you put real users on the stack:

- **Use a dedicated AWS account**, or one without production workloads, and for the ALB dashboard set
  `dashboard_allowed_cidrs` to your offices' and VPN's ranges only.
- **Keep Terraform state somewhere safe.** The repository configures no backend, so state is a local file. It holds
  the Cognito stand-in's passwords and, in ALB mode, the dashboard's OIDC client secret. Use an encrypted remote
  backend (for example Amazon S3 with a KMS key and access limited to the operators), and treat saved plan files the
  same way.
- **Set hourly caps and a per-user rpm** if budgets must hold within minutes (see Budget lag below).
- **Alarm on the usage sync.** If it fails, spend stops reaching the ledger and caps stop tripping. Add a CloudWatch
  alarm on the `${name}-usage-sync` function's Errors metric with your own notification target.
- **Keep opencode's permissions.** The managed opencode configuration (`mdm/opencode.managed.json.tmpl`) asks before
  edits, shell commands and fetches; `ocgw opencode-config` adds only the provider, so set a `permission` policy
  yourself on machines set up without MDM. Web search results and repository content are untrusted input to the model. opencode also loads a
  repository's own `opencode.json`, which can add MCP servers: a local command, or a remote server sent the contents
  of files such as `~/.config/ocgw/tokens.json` through `{file:...}`. Review that file before opening an untrusted
  repository in opencode.
- **Narrow the KMS key policy** in `terraform/security.tf` to named roles; it delegates to the account, as the
  default key policy does.
- **Set a per-user rpm if web search must be rate-limited:** the web search gateway has no gateway rate limit of its
  own, so between budget checks only the interceptor's rpm bounds it.
- **Deploy one stack per account and Region.** If two must share one, do not name one stack with the other's name,
  a hyphen and more (the gateway role trusts `gateway/<name>-gw-*`).
- **Keep `dashboard_private_role` at `viewer`** unless every IAM principal that may invoke the dashboard's Function
  URL should change budgets; use the ALB mode for named-person admin access.

## Limitations

- **A model reply can arrive empty.** Now and then (in about one test round in three, always in the same read-then-edit
  session) Kimi on Bedrock streams a reply whose every chunk has an empty delta: the usage counts about 70 output tokens and the stream
  ends with `finish_reason: stop`, but no text and no tool call arrive, so opencode ends the turn with nothing done.
  The gateway passes model replies through unchanged; asking again works. The test suite records such a reply and
  runs the session once more (see [docs/testing.md](docs/testing.md)).
- **Budget lag.** Model spend reaches the ledger through Mantle's CloudWatch metrics and the 5-minute usage sync,
  about 1 to 6 minutes after a call in all. A user can therefore go over a budget by a few minutes of traffic before calls are
  refused. The first call to a model in a project can take up to about 15 minutes to show, because CloudWatch
  `ListMetrics` lists a new metric late; a short hourly cap can be passed by more in that window.
- **Models removed mid-month.** A model taken out of `openweight_models` has no price any more, so its spend so far
  in the current windows stops counting toward caps. Remove a model at the start of a month, or keep it (and its
  price) until then.
- **Users removed mid-month.** Group, model and tag views are rebuilt each run from the users in `var.users`. A user
  removed from it keeps their own month figures (and the overview total), but drops out of that month's group, model
  and tag rows; the overview's model mix shows that spend as "(unattributed)". Re-added in the same month, they get a new Mantle project: the month, day and hour add its usage
  to the old project's, so the caps keep counting what they spent before. Usage in the old project's last few minutes (CloudWatch delay) is
  not collected after the switch, since the usage sync reads only current projects. Removing a user also deletes
  their BUDGET item, so the limits, $0 block and boost the dashboard set are gone; a re-added user starts from their
  group's and the organization's limits (the audit log shows the old ones). Renaming a user makes a new ledger user:
  their month-to-date spend starts at $0.
- **No cache split.** Mantle does not report cached input separately, so all input is priced at the full input rate
  (or at a model's `cache_write` price in `openweight_models`, when that is set and higher). Reported spend is an
  upper bound only for models whose cache writes cost no more than that rate. Kimi K3 and GLM 5.3 have cache-write
  usage types whose rates this sample has not checked; until you set `cache_write` for them from the Price List, their
  reported spend can be below the bill when prompt caching is used.
- **Client clock.** `ocgw` compares token expiry with the laptop's clock. A clock that is ahead by more than the
  token's lifetime (normally an hour) makes every request refresh the token; keep the system clock synced (NTP).
- **Web search is charged up front.** The $0.007 and an rpm slot are taken when the search is allowed, before it runs;
  the gateway's request interceptor never sees the result, so a search that then fails is not refunded.
- **`terraform plan` creates Mantle projects.** The AWS provider has no Mantle project resource yet, so a data source
  (`helpers/acgw.py projects-ensure`, one process for all users) creates a missing one while Terraform reads data,
  at plan time too. A user added
  and then dropped before any apply keeps an unused, free project named `<name>-<user>`; archive it in the Bedrock
  console. Mantle has no idempotent create: after a create with an unclear outcome (a 5xx or a lost reply) the helper
  checks the listing for about a minute before sending it again, but two checkouts planning at once, or a listing that
  lags longer, can still leave two projects with one name. Every run then uses the oldest and logs the others' ids;
  destroying a user archives all of them. Changing `name` after a deploy leaves the old `<name>-<user>` projects live
  (Terraform updates the archive step in place, and a replace would archive every user's current project): archive
  them in the Bedrock console. If an apply ever used the newer one before the older appeared in Mantle's
  listing, that user's few minutes of spend in it are not collected.
- **Gateway request limit.** In live testing the gateway's own requests-per-minute limit bit only on bursts well past
  it: six calls in one minute passed a limit of 2 (12 Nov 2026), while bursts of 12 and 16 drew 429s (16 Nov 2026).
  The tokens-per-minute limit on the same rule was enforced. AgentCore rate limits also fail open by design. The
  interceptor's per-user rpm is what caps requests exactly; it is off unless `default_rpm` or a group or user rpm is
  set, so set it, and treat the gateway limits as a second layer.
- **Region.** Bedrock Mantle and AgentCore Gateway inference targets are used in us-east-1.

## Clean up

The ledger table and the dashboard ALB have deletion protection on. Destroy deletes the ledger, and with it the
audit log, and the ALB access-log bucket (`force_destroy`); export the `AUDIT` partition first if you need the record
of changes. Turn deletion protection off first:

```bash
cd terraform
terraform apply -var ledger_deletion_protection=false -var dashboard_deletion_protection=false
terraform destroy -var ledger_deletion_protection=false -var dashboard_deletion_protection=false
```

## Contributing and license

See [CONTRIBUTING.md](../../CONTRIBUTING.md). This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
The dashboard build in `lambda/dashboard_ui/assets` bundles third-party packages under their own licenses (MIT,
Apache-2.0, BSD-3-Clause, 0BSD); see [THIRD-PARTY-LICENSES](THIRD-PARTY-LICENSES).
