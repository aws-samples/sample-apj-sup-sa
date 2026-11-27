# Dashboard

The dashboard shows spend and usage per user, group, model and tag, and lets admins change budgets, rate limits and
model access without a Terraform run. It is a Lambda function (`lambda/dashboard.py`, `lambda/dashboard_api.py`)
serving a React app built with Cloudscape (`dashboard/ui-src`, built into `lambda/dashboard_ui`).

## Two ways to reach it

| Mode | When | How people sign in |
|---|---|---|
| Private (default) | `dashboard_hostname` is empty | Lambda Function URL with `AWS_IAM` auth. Run `python dashboard/proxy.py --profile <profile>` and open the `http://127.0.0.1:8090/?k=<key>` link it prints: the key is new on every run and is kept as an HttpOnly cookie, so another OS user on the same machine cannot read the dashboard through your proxy. Browsers send the key cookie to every port on 127.0.0.1, so a server another OS user runs on this machine could receive it if your browser visits it; on a shared machine use the team (ALB) mode, and stop the proxy when you are done. The proxy listens on 127.0.0.1 only, signs each request with your AWS credentials and forwards only `GET` requests, so it is read-only: the proxy signs an `x-ocgw-client: read-only-proxy` header with each request, so the Lambda reports the role as viewer whatever `dashboard_private_role` says and the edit controls are off, and the proxy answers any write with a JSON 405 |
| Team (optional) | `dashboard_hostname` and `dashboard_zone_name` are set | An internet-facing ALB with an ACM certificate, `authenticate-oidc` against your IdP, an IP allow-list (`dashboard_allowed_cidrs`) and an AWS WAF web ACL (`terraform/security.tf`). Admins and viewers sign in with the IdP |

For the team mode, create a second OIDC app in the IdP: a confidential web app with the redirect URI
`https://<dashboard_hostname>/oauth2/idpresponse` and client authentication `client_secret_post`. Put its client ID in
`dashboard_oidc_client_id` and its secret in the Secrets Manager secret named by `dashboard_oidc_secret_name`.

In the private mode the Lambda trusts AWS IAM: any principal allowed `lambda:InvokeFunctionUrl` on the dashboard
function is signed in, and `dashboard_private_role` sets its role for every client. The default, `viewer`, makes the
private mode read-only. Set it to `admin` only if the people with that permission administer the gateway and you want
them to write through a SigV4 client of their own; the local proxy still forwards `GET` only.

## Roles

| Role | Who | Can |
|---|---|---|
| admin | Members of `dashboard_admin_groups` (default `ocgw-admin`) | See everything and change budgets, per-user rpm, model access and boosts |
| viewer | Members of `dashboard_viewer_groups` | See everything, change nothing |

Behind the ALB the groups come from the IdP's userinfo, which the ALB signs and passes on. The dashboard reads them
from `dashboard_group_claim`, or `group_claim` when that is empty. Set `dashboard_group_claim` only when your IdP's
userinfo names the groups claim differently from its tokens.

Behind the ALB the Lambda checks the role on every request from the ALB's signed identity header (ES256, signer ARN,
expiry); in the private mode the role is `dashboard_private_role`. Every write also needs the `x-ocgw-csrf` header, a
matching `Origin` and a JSON body, and each change is written in one transaction with audit rows (who made it, before
and after values); each row is added under `attribute_not_exists(sk)`, so the dashboard never replaces one, though IAM
cannot enforce that (docs/security.md r192).

## Pages

| Page | What it shows | Admin actions |
|---|---|---|
| Overview | Month-to-date and today's spend, projected month-end spend, active users, daily spend by model, model mix, users close to a cap or with a usage record that cannot be read | Raise a cap or boost; repair an unreadable usage record |
| Usage explorer | Spend, tokens and requests by user, group, model or tag, per day or month, with filters | |
| Users | Every ledger user with month-to-date spend against their budget, rpm and group | Open a user |
| User detail | One user's spend by window and model, their effective limits and where each comes from (user, group or organization) | Set the user's monthly, daily and hourly caps and rpm; add a temporary boost or end it; repair an unreadable usage record |
| Groups | Spend and members per group | Open a group |
| Budgets & limits | Organization and group budgets and rpm | Edit organization and group budgets and rpm; clear a group's limits (its model access stays); end any active boost |
| Model access | Which groups may use which models | Allow or deny models per group |
| Rate limits | The interceptor's rpm per user and the gateway's native RPM and TPM limits (set in Terraform) | |
| Audit log | Every change, filterable by who and what | |
| Pricing | The per-token prices `usage_sync` uses | |

## How changes take effect

- **Budgets, rpm and model access** are stored as policy items in the ledger table. The interceptor caches them for
  60 seconds, so a change reaches every call within a minute.
- **Action with an API but no UI control yet:** the block message (`block_message` in `PUT /api/v2/policy/org`).
  Ending a boost early has a control (User detail, Budgets & limits).
- **A boost needs a cap to raise.** The Boost dialog does not offer a period with no cap (the boost would change
  nothing) or with a $0 cap: a $0 cap is a block set with a typed confirmation, and only raising the cap lifts it.
  The gateway applies the same rule, so a boost already running when its period's cap is set to $0 adds nothing; the
  user page shows it as not in effect until the cap is raised or the boost ends. The rule is the same whether the $0
  cap is the user's own, a group's or the organization's: the boost is held back, applies again if a cap above $0 is
  set before it ends, and End boost removes it. The Block confirmation says so when the user has a boost there.
- **A hand-edited boost** (boost attributes the dashboard never writes: an amount or end that is not a number, a
  negative or zero amount, an end past the year 3000, a period that is not Monthly, Daily or Hourly, a reason that is not text, or a boost that is
  not whole: an amount without an end, an end without an amount, or a period or reason with neither) lifts no cap at the gateway. User detail says the item holds boost values that cannot be read, and
  End boost clears them.
- **An unreadable usage record** (a hand edit left a spend value that is not a number, or is negative) has no
  amount, so its row shows "Usage record cannot be read" instead of a figure. Where the window has a cap the gateway
  refuses the user's calls ("calls refused"): neither a higher cap nor a boost can help, because there is no spend
  to compare. **Repair record** removes only the bad values, with a reason in the audit log; web search spend in
  that window restarts at $0 and usage_sync sets model spend again within 5 minutes.
- **Gateway rate limits** are set in Terraform (`rpm_default`, `tpm_default`, `rpm_overrides`) and shown read-only on
  the Rate limits page. AgentCore allows one rate limit per set of dimension keys, so the gateway's per-user limits are
  entries of the one limit Terraform manages. A per-user request cap an admin can change from the dashboard is the
  interceptor's `rpm` (user, group or organization), which the interceptor enforces exactly.
  After a change to the gateway's limit, AgentCore can let every request through for several minutes, even once the
  limit reads ACTIVE (seen in testing: up to three minutes and more). Plan gateway limit changes for quiet times, and
  rely on the interceptor's `rpm` when a cap must hold the moment it is set.
- **Which limit applies.** A user's own limit wins; otherwise the most generous of their groups that set that limit;
  otherwise the organization policy; otherwise the deployment default. A group that sets a limit replaces the default
  for its members, so a user in a capped group and a group that sets nothing gets the capped group's figure. Set the
  limit on each group (or on the user) where members should keep a higher figure. The user detail page shows where
  each limit comes from.
- **Two admins editing at the same time.** Every policy item carries a version. A save made against an older
  version is refused with "changed by someone else", and the page reloads the current values.
- **Fallback chains** still apply. If an admin removes a fallback model from a group, members of that group are
  refused the primary model with a message naming the fallback, rather than being served a model they may not use.

## Numbers you see

Spend is the gateway's own estimate: Mantle token counts priced with the table on the Pricing page, plus $0.007 per
web search. It reaches the ledger through `usage_sync` every 5 minutes, a few minutes after each call, so the latest
minutes can read low. Cached input is priced at the full input rate (see [costs.md](costs.md)). For billed amounts,
use AWS Cost Explorer.

The model mix and the usage explorer can show an **(unattributed)** slice: spend in a user's totals with no model or
group row behind it. That is web search charges, any model spend the usage sync could not price, and the month's spend
of users removed from `users` in Terraform. Group, model and tag rows are rebuilt each run from the listed users, so a
removed user's spend leaves those rows but stays in their own total and in the month-to-date figure.

When a fallback answers, the request is counted under the model that was asked for (the interceptor runs before the
gateway picks a hop), while the tokens and dollars land on the model that answered (Mantle meters the served model).
So on a day with fallbacks, a model's request count and its spend describe slightly different sets of calls. The
gateway's application logs (`/aws/vendedlogs/bedrock-agentcore/gateway/<id>`) record `servedViaFallbackHop` for
each call that a fallback served.
