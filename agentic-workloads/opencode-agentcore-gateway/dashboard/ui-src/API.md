# Dashboard API v2 contract

Served by the dashboard Lambda behind its IAM-authorized Function URL (default) or the optional ALB (same origin as the
UI). All responses JSON. Money in USD (float), tokens as integers, times ISO-8601 UTC. Errors: `{"error": "<message>", "request_id": "<id>"}` with 4xx/5xx.

Writes (PUT/POST/DELETE) must send `content-type: application/json` and `x-ocgw-csrf: 1`; the server also checks
`Origin`. Writes answer 403 for viewers. A write that changes a policy item takes `version` (the value read) and
answers 409 `{"error": "changed by someone else", "current": {...}}` when it no longer matches.

## Shared shapes

```ts
type Counters = { usd: number; input: number; output: number; requests: number };  // no cache split: Mantle counts cached input as input
type Period = "Monthly" | "Daily" | "Hourly";
type CapSource = "user" | `group:${string}` | "org" | "default" | null;
type Limit = { period: Period; cap: number | null /* enforced, boost included */; base: number | null /* before any boost */; source: CapSource; boost: number; spent: number | null;  // null when unreadable
               pct: number | null;            // spent / cap * 100, null when no cap
               state: "ok" | "warn" | "critical" | "blocked" | "none";  // warn >= warn_pct[0], critical >= warn_pct[1], blocked >= 100
               unreadable: boolean;           // the spend record cannot be read: "blocked" where capped (the gate refuses), else "warn"
               resets_at: string };
type Model = { id: string; display_name: string; description: string };
```

## Read (admin or viewer)

`GET /api/v2/me` → `{ email, sub, groups: string[], role: "admin" | "viewer", tag_dims: string[], sign_out: boolean }` (`tag_dims`: the tag dimensions this gateway rolls up, `[]` when tags are off; `sign_out`: true behind the ALB, where `/logout` ends the session; `email` is null for IAM callers)

`GET /api/v2/overview` → 
```ts
{ month: string /*YYYY-MM*/, today: string,
  kpis: { mtd_usd: number, today_usd: number, projected_usd: number, active_users_today: number,
          requests_mtd: number, org_budget_usd: number | null },
  daily: { date: string, by_model: Record<string, number /*usd*/> }[],     // this month, one row per day so far
  model_mix: { model: string, usd: number, requests: number }[],           // this month
  attention: { user: string, groups: string[], period: Period, spent: number | null, cap: number | null, pct: number | null, state: Limit["state"], unreadable: boolean }[],  // critical, blocked or unreadable; unreadable first
  generated_at: string }
```

`GET /api/v2/usage?from=YYYY-MM-DD&to=YYYY-MM-DD&granularity=day|month&group_by=model|user|group|tag&user=&group=&model=&tag=dim=value`
→ `{ tag_dims: string[], dim: string | null, series: { key: string, points: { t: string, usd: number, requests: number, input: number, output: number }[] }[],
     table: ({ key: string } & Counters)[], total: Counters, notes: string[] }`
(`granularity=day` covers a range of at most 40 days, `month` at most 13 months; a longer range is a 400, never cut.
Filters combine with AND. `notes` explains data limits, for example "Tag usage is rolled up per month, so this view is
shown by month.")

`GET /api/v2/users` → `{ users: { user: string, groups: string[], mtd: Counters, today_usd: number, hour_usd: number,
  limits: Limit[], rpm: { value: number | null, source: CapSource }, last_seen: string | null,
  state: "ok" | "warn" | "critical" | "blocked" /* worst of limits */,
  removed: boolean /* no BUDGET item: removed from var.users, cannot call or be edited */ }[] }`

`GET /api/v2/users/{user}` → `{ user, groups, limits: Limit[], rpm, boost: { usd, period, until, reason, in_effect } | null, boost_state: "live" | "expired" | "unreadable" | null /* what the item holds of a boost; End boost clears any of it */,
  override: { monthly_usd?, daily_usd?, hourly_usd?, rpm? }, models: string[] /* effective patterns */,
  by_model_mtd: ({ model: string } & Counters)[], daily: { date, usd, requests }[] /* last 30 days */,
  audit: AuditRow[] /* last 50 about this user */, version: string /* send back with limits, boost and end-boost writes */,
  removed: boolean }`

`GET /api/v2/groups` → `{ groups: { group: string, members_seen: string[], mtd: Counters,
  policy: { monthly_usd?, daily_usd?, hourly_usd?, rpm?, models?: string[] }, models_effective: string[],
  models_source: "dynamodb" | "env", version: string | null,
  editable: boolean /* false for "(no group)": users without groups get the org policy */ }[] }`

`GET /api/v2/policy` → `{ org: { monthly_usd?, daily_usd?, hourly_usd?, rpm?, warn_pct: [number, number], block_message?: string, version: string | null },
  env_defaults: { monthly_usd: number | null, daily_usd: number | null, hourly_usd: number | null, rpm: number | null },
  boosts: { user, usd, period, until, reason, in_effect, version }[] }` (`version`: the user's limits version, sent back to end that boost)

`in_effect` is false when the boost's period has no cap any more (removed after the grant) or a $0 cap (a block,
which a boost never lifts): the boost then adds nothing, and that period's `Limit.boost` is 0. It applies again if a
cap above 0 is set before the boost ends.

`GET /api/v2/models` → `{ models: (Model & { price: { input: number, output: number /* USD per 1M */ } })[],
  aliases: Record<string, string> }`

`GET /api/v2/ratelimits` → `{ interceptor: { default: number | null, org: number | null, groups: Record<string, number>, users: Record<string, number> },
  gateway: { id: string, description: string, dimension: string, value: string, user: string | null,
             requests_per_minute?: number, tokens_per_minute?: number, status: string }[] }` (`user`: the user the entry
  applies to, from `identity_map`; null for the `*` default)

`GET /api/v2/audit?from=&to=&actor=&target=` → `{ rows: AuditRow[], complete: boolean }`. `complete` is false when a filtered search stopped after reading 20,000 rows (newest first): narrow it with `from`/`to` to look further back. A `target` that contains `:` or is `org` is taken as a full target (`user:alice`, `group:eng`, `org`) and matches exactly, so `user:al` finds nothing; text without a `:` (`alice`, `eng`) is a substring search. Each call returns at most the newest 500 matching rows in the `from`/`to` window (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`; a bad value or `from` after `to` is a 400 naming the field); `actor` and `target` filter before that cap, so an older match is still returned. Here
`AuditRow = { at: string, actor: string, action: string, target: string, before: object | null, after: object | null, reason: string | null }`
`actor` is `<email> (<sub>)` for an ALB sign-in whose claims carry both (from r194), else the email or the sub; in Function URL mode the caller's IAM principal ARN. The `actor` filter is a substring match, so either part finds the row.

## Write (admin only)

| Method and path | Body | Effect |
|---|---|---|
| `PUT /api/v2/policy/org` | `{ monthly_usd?, daily_usd?, hourly_usd?, rpm?, warn_pct?, block_message?, version, reason? }` | set org policy (null clears a field; a body naming no field is a 400, as for user limits) |
| `PUT /api/v2/policy/groups/{group}` | `{ monthly_usd?, daily_usd?, hourly_usd?, rpm?, models?, version, reason? }` | set group policy (a body naming no field is a 400) |
| `DELETE /api/v2/policy/groups/{group}` | `{ version, reason? }` | remove the whole group policy, limits and model allow-list (env defaults apply again); to clear only the limits, `PUT` them as `null` |
| `PUT /api/v2/users/{user}/limits` | `{ monthly_usd?, daily_usd?, hourly_usd?, rpm?, version?, reason? }` | user override (null clears); `version` from the user page makes a save over another admin's change a 409 |
| `POST /api/v2/users/{user}/boost` | `{ usd, period, hours, reason, version? }` | temporary boost, `hours` 1..720 (`version` as for limits); 400 with `field: "period"` when the user has no cap for that period (the boost would change nothing) or a $0 cap (a block: raise the cap instead) |
| `POST /api/v2/users/{user}/repair` | `{ period, reason }` | remove the current `period` window's spend values the gateway cannot read (409 when it can read them all); `item: { window, removed }` |
| `DELETE /api/v2/users/{user}/boost` | `{ version?, reason? }` | end a boost now (`version` from the user page or the policy's boosts list: a boost changed since is a 409) |

The gateway's own rate limits are set in Terraform (`rpm_default`, `tpm_default`, `rpm_overrides`): AgentCore allows
one limit per set of dimension keys, and Terraform owns it. `GET /api/v2/ratelimits` lists one row per entry with the
`user` it applies to (null for `*`). Per-user request caps an admin can change are the interceptor's `rpm` (user, group
or organization limits).

Every write answers `{ ok: true, item: <new state>, audit: AuditRow }`. After a save the UI reloads the page; the user page and the policy items are read strongly consistent, while the Budgets page's boost list comes from an eventually consistent index, so a version from it can be stale for a moment (a 409 and a reload, never a lost write). Validation errors answer 400 with
`{"error": "...", "field": "<name>"}`. Numbers: caps 0 or 0.01..100000, rpm 1..100000, boost `usd` 0.01..100000 and `hours` 1..720 (whole hours), `block_message` text up to 500 characters without control or direction characters, or null, warn_pct ascending in 1..100.
User names: `^[A-Za-z0-9._@+-]{1,128}$`. Group names are IdP group names: 1-256 characters, no control characters, `/`, `\` or `,` (spaces are fine, for example `former eng`). Models must be catalog IDs or `ow/<prefix>*` (a pattern without `ow/` would match nothing). `reason` required (non-empty) when a
cap is set to 0 and for boosts.
