[English](API.md) | [한국어](API.ko.md)

# Bedrock Meter Core API Reference

Built on API Gateway HTTP API. All requests require **AWS SigV4** signing, and callers must
have IAM `execute-api:Invoke` permission.

- **Endpoint**: `https://<api-id>.execute-api.<region>.amazonaws.com` (see CDK output `CoreApiEndpoint`)
- **Auth**: API Gateway IAM Authorizer (SigV4). The Lambda handler identifies the caller from `requestContext.identity.userArn`.
- **Region**: `ap-northeast-2`
- **Content-Type**: `application/json`
- **Time definitions**:
  - Quota day: calendar day in ConfigTable `default.quotaTimeZone`.
  - Quota week: ISO 8601 week (Monday–Sunday) in the same timezone.
  - Quota month: calendar month in the same timezone.
  - Athena history/model analytics: UTC event timestamps.

---

## 0. Authorization model

| Type | Description |
|---|---|
| Self-service | Callers can read only their own data (`/usage/me`, `/usage/me/history`, `/limits/me`) |
| Admin | IAM usernames registered in `ADMIN_USERNAMES` only; otherwise 403 |

**Registering admins**: Set `BEDROCK_METER_ADMIN_USERNAMES=alice,bob` before deployment and redeploy.

**Principal constraints**: The `{username}` path parameter accepts only path-less IAM usernames.
STS assumed-role usage is aggregated under a stable IAM Role ARN, but roles are not targets of
the username-based management API.

**Hybrid design note**: API paths and the admin allowlist use usernames, but **internal storage
(DDB UsageTable/ConfigTable, Bedrock logs, Athena, and SNS messages) retains full ARNs**.
Lambda reconstructs the ARN from the path username and `account_id`.

---

## 1. Common response format

### Successful responses

```json
{ "<route-specific schema>": "..." }
```

HTTP 200, `content-type: application/json`, `cache-control: no-store`. Responses with user
identifiers include both `username` and `user_arn`.

### Error responses

| HTTP | Body | Meaning |
|---|---|---|
| 400 | `{"error": "<reason>"}` | Input validation failed, such as an invalid username or body |
| 401 | `{"error": "unauthenticated"}` | Caller ARN could not be identified; authentication is not configured |
| 403 (API GW) | `{"message": "Forbidden"}` | Missing SigV4 signature or `execute-api:Invoke` permission |
| 403 (Lambda) | `{"error": "admin only", "caller": "<arn>"}` | Caller outside the admin allowlist invoked an admin route |
| 404 | `{"error": "not found", "method": "...", "path": "..."}` | No matching route, such as a username containing a slash |
| 500 | `{"error": "<type>", "message": "..."}` | AWS API error or unhandled exception |

---

## 2. Self-Service Endpoints

### `GET /usage/me`

The caller's daily, weekly, and monthly usage and block state.

**Response 200**:

```json
{
  "username": "alice",
  "user_arn": "arn:aws:iam::123456789012:user/alice",
  "as_of": "2026-05-27T01:00:00.000000+00:00",
  "last_activity": "2026-05-27T00:55:12.000000+00:00",
  "quota_policy": {
    "mode": "CALENDAR",
    "quotaTimeZone": "Asia/Seoul",
    "quotaTimeZoneVersion": 2
  },
  "day": {
    "date": "2026-05-27",
    "input_tokens": 12345,
    "cache_read_tokens": 800,
    "cache_write_tokens": 200,
    "output_tokens": 6789,
    "total_tokens": 20134,
    "invocation_count": 42,
    "limit": 3000000
  },
  "week": {
    "iso_week": "2026-W22",
    "start": "2026-05-25",
    "end": "2026-05-31",
    "total_tokens": 89000,
    "limit": 15000000
  },
  "month": {
    "period": "2026-05",
    "start": "2026-05-01",
    "end": "2026-05-31",
    "total_tokens": 230000,
    "limit": 45000000
  },
  "state": {
    "blocked": false,
    "blocked_scope": null,
    "blocked_reason": null,
    "blocked_at": null
  },
  "config": {
    "exempt": false,
    "warning_threshold_pct": 80
  }
}
```

> **Units are credits (cost-weighted tokens).** `total_tokens` in `day`/`week`/`month` and all
> `limit` values use **credits**, not a raw token sum:
> `credits = raw input×1 + cache read×0.1 + cache write×1.25 + output×5`.
> One credit equals one raw input token. Cache reads have the lowest weight (0.1×), and output
> the highest (5×). Change `costWeightCacheRead`/`costWeightCacheWrite`/`costWeightOutput` in the
> ConfigTable `default` row without redeploying. Enforcement also uses credits.

All `day`/`week`/`month` values come from DDB; week/month sum DAY rows over their periods without
Athena. `as_of` is the **query time**, refreshed on every call. `last_activity` is the **last token
accumulation time**, taken from the latest DAY row's `lastUpdated`, including a previous day if
there is no activity today. The console's "Last updated" uses `last_activity`, which matches
`last_updated` in `/usage/aggregate`. It is `null` if there has never been any activity.

`quota_policy` is the global calendar used for DDB enforcement. Policy version 1 uses the legacy
`DAY#yyyy-mm-dd` namespace; later versions use `DAY#V<version>#yyyy-mm-dd`.

---

### `GET /usage/me/history`

The caller's daily/hourly usage history.

**Query**:

- `period`: `week` | `month` | `last30d` (default `month`); applies only to `granularity=day`.
- `granularity`: `day` | `hour` (default `day`). `hour` uses a **fixed last-48-hours window**,
  ignores `period`, and returns bucket keys such as `"2026-06-05T13:00"` in `rows[].day`.

**Response 200**:

```json
{
  "username": "alice",
  "user_arn": "arn:aws:iam::123456789012:user/alice",
  "period": "month",
  "granularity": "day",
  "rows": [
    {
      "day": "2026-05-01",
      "input_tokens": 1234,
      "cache_read_tokens": 200,
      "cache_write_tokens": 50,
      "output_tokens": 567,
      "total_tokens": 2051,
      "invocations": 8
    },
    {
      "day": "2026-05-02",
      "input_tokens": 4321,
      "cache_read_tokens": 0,
      "cache_write_tokens": 100,
      "output_tokens": 800,
      "total_tokens": 5221,
      "invocations": 12
    }
  ]
}
```

Buckets without usage are omitted from `rows`. With `granularity=hour`, `rows[].day` uses UTC
values such as `"2026-05-02T14:00"`, and the window is the 48 hours preceding the call.
Athena results have a 10-second cache TTL.

---

### `GET /limits/me`

The caller's effective configuration: defaults merged with per-user overrides.

**Response 200**:

```json
{
  "username": "alice",
  "user_arn": "arn:aws:iam::123456789012:user/alice",
  "effective": {
    "dailyTokenLimit": 3000000,
    "weeklyTokenLimit": 15000000,
    "monthlyTokenLimit": 45000000,
    "warningThresholdPct": 80
  }
}
```

For exempt users, the response includes `"exempt": true`.

---

## 3. Admin Read Endpoints

All admin routes require the caller's username to be in `ADMIN_USERNAMES`.

### `GET /users`

Lists known users so external clients can discover them even when only CoreStack is deployed.

**Query**:

- `limit`: 1–200 (default 100).

**Response 200**:

```json
{
  "limit": 100,
  "rows": [
    {
      "user_arn": "arn:aws:iam::...:user/alice",
      "username": "alice",
      "display_name": "alice",
      "status": "normal",
      "latest_day": "2026-05-27",
      "latest_day_total_tokens": 20134,
      "latest_day_invocation_count": 42,
      "last_updated": "2026-05-27T01:00:00.000000+00:00",
      "state": { "blocked": false },
      "config": {
        "exempt": false,
        "warning_threshold_pct": 80,
        "has_override": false
      },
      "effective_limits": {
        "daily": 3000000,
        "weekly": 15000000,
        "monthly": 45000000
      }
    }
  ]
}
```

Known users combine UsageTable `DAY#...`/`STATE` rows with per-user overrides in ConfigTable.
`username` is included only for IAM User ARNs. `effective_limits` merges defaults and per-user
overrides and supplies the console grid's quota progress bars.

> **Note**: Use `/usage/aggregate` to retrieve all users' day/week/month totals together, as the
> console does. `/users` returns day/state rows without week/month totals. `/usage/aggregate`
> extends the same row schema with `week_total_tokens` and `month_total_tokens`, computed by
> summing DAY rows over each period. Both use DDB scans, not Athena.

### `GET /users/{username}`

Alias for `GET /usage/{username}`, useful for clients organized around user resources.

### `GET /users/{username}/history`

Alias for `GET /usage/{username}/history`.

### `GET /usage/{username}`

Usage snapshot for a specified user. `{username}` is a path-less IAM username; no URL encoding is needed.

```
GET /usage/alice
```

**Response**: Same schema as `/usage/me`, with `username`/`user_arn` derived from the path.

**400**: Invalid username format, such as unsupported special characters. A slash prevents route
matching and produces 404.

---

### `GET /usage/{username}/history`

**Query**: `period` (`week`|`month`|`last30d`, default `month`), `granularity` (`day`|`hour`, default `day`).

**Response**: Same schema as `/usage/me/history`. `granularity=hour` returns hourly buckets for
the last 48 hours and ignores `period`.

---

### `GET /usage/top`

Top N users by usage.

**Query**:

- `metric`: `credits` | `inputTokens` | `rawInputTokens` | `cacheReadTokens` | `cacheWriteTokens` | `outputTokens` | `totalTokens` | `invocations` (default `totalTokens`).
- `period`: `day` | `week` | `month` (default `month`).
- `limit`: 1–100 (default 5).

> `credits` is the same cost-weighted sum used by limits/enforcement
> (raw×1 + cacheRead×0.1 + cacheWrite×1.25 + output×5). `inputTokens` is the simple sum of raw,
> cache-read, and cache-write tokens. `rawInputTokens` includes only raw input; these are
> breakdown metrics for analytics.

**Response 200**:

```json
{
  "period": "month",
  "metric": "outputTokens",
  "limit": 5,
  "rows": [
    { "user_arn": "arn:aws:iam::...:user/heavy",  "username": "heavy",  "value": 2300000 },
    { "user_arn": "arn:aws:iam::...:user/medium", "username": "medium", "value":  850000 }
  ]
}
```

- `username` is included only for IAM User ARNs. Logs with SSO/assumed-role ARNs return only `user_arn`.

**400**: Invalid `metric`/`period`/`limit`.

---

### `GET /usage/aggregate`

Combines all users' day/week/month totals in one response, entirely from DDB. This avoids
per-user `/usage/{username}` fan-out when rendering a user list and usage grid such as the console.

**Query**: `limit` (1–200, default 100). The period is fixed to day + week + month. Week/month
are computed by scanning UsageTable DAY rows once and summing the relevant ranges, without Athena.

**Response 200**:

```json
{
  "limit": 100,
  "as_of": "2026-06-05T01:00:00.000000+00:00",
  "rows": [
    {
      "user_arn": "arn:aws:iam::...:user/alice",
      "username": "alice",
      "display_name": "alice",
      "status": "warning",
      "today_total_tokens": 120000,
      "today_invocation_count": 8,
      "latest_day": "2026-06-05",
      "latest_day_total_tokens": 824000,
      "latest_day_invocation_count": 32,
      "last_updated": "2026-06-05T00:55:00+00:00",
      "state": { "blocked": false },
      "config": { "exempt": false, "warning_threshold_pct": 80, "has_override": false },
      "effective_limits": { "daily": 3000000, "weekly": 15000000, "monthly": 45000000 },
      "week_total_tokens": 3500000,
      "month_total_tokens": 11800000
    }
  ]
}
```

- `username` is included only for IAM User ARNs.
- **`today_total_tokens`/`today_invocation_count` use today's (UTC) DAY row**. The console's day
  cards use these values, matching `day.total_tokens` from `/usage/{username}`. They are zero
  when there is no activity today.
- **`latest_day`/`latest_day_total_tokens` describe the last active day**, which may be today or
  an earlier date. They support discovery and `last_updated` (the console's "Last updated"), not
  today's usage. Immediately after midnight, today=0 with latest_day_total>0 is normal.
- `week_total_tokens`/`month_total_tokens` sum DAY rows over their ranges in DDB, applying credit
  weights at read time.
- Admin only. DDB scans avoid Athena latency. Day/today reads are not ConsistentRead, so a few
  seconds of discrepancy are possible.

---

### `GET /usage/by-model`

Usage totals by model.

**Query**: `period` (default `month`).

**Response 200**:

```json
{
  "period": "month",
  "rows": [
    {
      "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
      "input_tokens": 12300000,
      "cache_read_tokens": 1200000,
      "cache_write_tokens": 300000,
      "output_tokens": 4500000,
      "total_tokens": 18300000,
      "invocations": 18234
    }
  ]
}
```

`input_tokens` contains raw input only; cache reads/writes are separate fields. `total_tokens`
uses the same total as enforcement.

---

### `GET /usage/by-model/trend`

Per-model **time series** with daily/hourly buckets. Unlike `/usage/by-model`, which returns a
period total for each model, this endpoint returns long-format bucket × model rows for trend charts.

**Query**:

- `period`: `week` | `month` | `last30d` (default `month`); applies only to `granularity=day`.
- `granularity`: `day` | `hour` (default `day`). `hour` uses a **fixed last-48-hours window**,
  ignores `period`, and formats `rows[].day` as `"2026-06-05T13:00"`.
- `metric`: `totalTokens` | `invocations` (default `totalTokens`); controls **only top-N selection**.
  Rows always contain the full breakdown, so client-side metric toggles need no new query.
- `top`: 1–20 (default 10). Returns the top N models by period total individually and combines
  the remainder as `model_id="other"`.

**Response 200**:

```json
{
  "period": "month",
  "granularity": "day",
  "metric": "totalTokens",
  "top": 10,
  "models": [
    "global.anthropic.claude-opus-4-8",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "other"
  ],
  "rows": [
    {
      "day": "2026-06-01",
      "model_id": "global.anthropic.claude-opus-4-8",
      "input_tokens": 120000,
      "cache_read_tokens": 8000,
      "cache_write_tokens": 2000,
      "output_tokens": 45000,
      "total_tokens": 175000,
      "invocations": 320
    }
  ]
}
```

- `models` lists the top-N model IDs in descending order of period total, followed by `"other"`
  if additional models exist. The console uses this order for chart series.
- `rows` uses long format at `(bucket, model_id)` granularity. `day` is `"2026-06-01"` for daily
  buckets or `"2026-06-01T14:00"` (UTC) for hourly buckets. Models outside the top N are summed
  into the same bucket's `model_id="other"` row.
- One Athena query groups by bucket × model, with a 10-second TTLCache. Top/metric post-processing
  runs in Lambda memory, so requests with the same `(period, granularity)` share a cache.
  Hourly and daily queries use separate caches.

**400**: Invalid `period`/`metric`/`granularity`, or `top` is not an integer from 1–20.

---

### `GET /limits/{username}`

A specified user's effective configuration. Same response schema as `/limits/me`.

---

### `GET /admin/blocked`

All currently blocked users (`blocked=true`).

**Response 200**:

```json
{
  "rows": [
    {
      "user_arn": "arn:aws:iam::...:user/over",
      "username": "over",
      "blocked_scope": "WEEK",
      "blocked_reason": "WEEK limit exceeded: 15100000/15000000 credits",
      "blocked_at": "2026-05-26T11:30:21.724770+00:00"
    }
  ]
}
```

An empty result returns `rows: []`. `username` is included only for IAM User ARNs.

---

## 4. Admin Write Endpoints

### `GET /admin/settings/quota`

Current global quota calendar configuration.

```json
{
  "mode": "CALENDAR",
  "quotaTimeZone": "Asia/Seoul",
  "quotaTimeZoneVersion": 2
}
```

### `PUT /admin/settings/quota`

Changes the global quota timezone. Requires an IANA timezone name and an audit reason.

```json
{
  "quotaTimeZone": "Asia/Seoul",
  "reason": "Korean operating calendar"
}
```

An actual timezone change increments `quotaTimeZoneVersion` and starts active DAY/WEEK/MONTH
counters in a new namespace. Existing rows are not reinterpreted in the new timezone.
`countersReset=true` in the response indicates this transition, and a `QUOTA_TIMEZONE_CHANGED`
audit event is published.

Requesting the same timezone returns `updated=false`, `countersReset=false`, and leaves the
version unchanged. An invalid timezone, missing reason, or concurrent-update conflict returns 400.

---

### `PUT /limits/{username}`

Upserts a per-user override. A subset of keys may be supplied; omitted keys fall back to defaults.

**Request Body**:

```json
{
  "dailyTokenLimit": 2000000,
  "weeklyTokenLimit": 10000000,
  "monthlyTokenLimit": 30000000,
  "warningThresholdPct": 90,
  "exempt": false
}
```

**Allowed keys**: Only `dailyTokenLimit`, `weeklyTokenLimit`, `monthlyTokenLimit`,
`warningThresholdPct`, and `exempt`. Other keys return 400.

**Validation**:

- Token limits: integers with 1 ≤ value ≤ 10^12; `0` is not allowed.
- `warningThresholdPct`: integer from 1–100.
- `exempt`: boolean, `true` or `false` only.
- When supplied together, `monthly ≥ weekly ≥ daily` is required.
- **Self-lockout protection**: Token-limit keys are rejected when the caller ARN matches the
  target ARN reconstructed from the path username; `warningThresholdPct` is allowed.
- **Self-exemption protection**: `exempt` is rejected under the same target == caller ARN condition.

**Response 200**:

```json
{
  "updated": true,
  "username": "alice",
  "user_arn": "arn:aws:iam::...:user/alice",
  "changes": { "dailyTokenLimit": 2000000 }
}
```

**Side effects**: Publishes an SNS audit event (`event=LIMIT_CHANGED`, `severity=INFO`, `scope=ADMIN`).

**Errors**:

- 400 `unknown keys: [...]`
- 400 `exempt must be boolean`
- 400 `<key> out of range (0..10^12)`
- 400 `weeklyTokenLimit must be >= dailyTokenLimit`, and similar ordering errors

---

### `DELETE /limits/{username}`

Removes the per-user override. Default configuration applies afterward.

**Response 200**:

```json
{ "deleted": true, "username": "alice", "user_arn": "arn:..." }
```

**Side effects**: Publishes an SNS audit event (`event=LIMIT_REMOVED`).

---

### `POST /admin/unblock/{username}`

Force-unblocks a user: removes the IAM deny policy and clears the `STATE` row's block state,
regardless of whether usage exceeds the limit.

**Body**: None; ignored if supplied.

**Response 200**:

```json
{
  "unblocked": true,
  "username": "alice",
  "user_arn": "arn:...",
  "policy_removed": true
}
```

- `policy_removed: false` means IAM already had no deny policy; the operation is idempotent.

**Side effects**: Publishes an SNS audit event (`event=MANUAL_UNBLOCK`).

---

## 5. SNS Audit/Alert Message Format

All messages published by Bedrock Meter core to its SNS topic use this JSON structure:

```json
{
  "event": "BLOCKED" | "WARNING" | "RESOLVED" | "MANUAL_UNBLOCK" | "LIMIT_CHANGED" | "LIMIT_REMOVED",
  "severity": "CRITICAL" | "WARN" | "INFO",
  "scope": "DAY" | "WEEK" | "MONTH" | "ADMIN" | "MANUAL" | "RESET",
  "user_arn": "arn:aws:iam::...:user/alice",
  "username": "alice",
  "timestamp": "2026-05-27T01:00:00+00:00",
  "total": 3300000,
  "limit": 3000000,
  "percentage": 110,
  "caller_arn": "arn:aws:iam::...:user/admin",
  "caller_username": "admin",
  "changes": {"dailyTokenLimit": 2000000}
}
```

Only fields relevant to the event type are included. SNS `MessageAttributes` separately include
`severity`, `scope`, and `event`, allowing subscription filtering.

---

## 6. SigV4 Examples

### Bash + scripts/api-call.py

```bash
pip install boto3 requests requests-aws4auth   # one-time setup

export BEDROCK_METER_API_ENDPOINT="https://abc.execute-api.ap-northeast-2.amazonaws.com"
export AWS_REGION=ap-northeast-2

# Own usage
scripts/api-call.py GET /usage/me

# Top 5 (admin)
scripts/api-call.py GET '/usage/top?metric=outputTokens&period=month&limit=5'

# Change limits (admin) - no URL encoding required
scripts/api-call.py PUT '/limits/alice' '{"dailyTokenLimit":2000000}'

# Force-unblock
scripts/api-call.py POST '/admin/unblock/alice'
```

### Python (raw)

```python
import requests, boto3
from requests_aws4auth import AWS4Auth

creds = boto3.Session().get_credentials().get_frozen_credentials()
auth = AWS4Auth(creds.access_key, creds.secret_key, 'ap-northeast-2', 'execute-api', session_token=creds.token)
r = requests.get('https://abc.execute-api.ap-northeast-2.amazonaws.com/usage/alice', auth=auth)
r.raise_for_status()
print(r.json())
```

> ⚠️ The `urllib + boto3.SigV4Auth` combination can cause API Gateway to return 403 because
> differences in `Host`/`Content-Length` handling prevent caller identification.
> **Use `requests-aws4auth` instead.**

---

## 7. Caller IAM Permissions

Minimum permissions to attach to API callers:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "execute-api:Invoke",
    "Resource": "arn:aws:execute-api:ap-northeast-2:<account>:<api-id>/*"
  }]
}
```

Extract `<api-id>` from the host in the `CoreApiEndpoint` stack output. Admin authorization is
controlled by the Lambda allowlist (`ADMIN_USERNAMES`), rather than IAM alone.

---

## 8. Throttling

The default API Gateway stage uses `throttlingRateLimit=20 RPS` and `throttlingBurstLimit=40`.
Exceeding these limits returns 429.

---

## 9. Operational Notes

- After `force_unblock`, the hot/cold path can block the user again at the next evaluation
  unless the limit is adjusted as well.
- `PUT /limits/{username}` is admin-only. Admins can change limits and `exempt` for IAM Users,
  including their own user.
- Athena query routes use a 10-second TTLCache with a 128-entry in-memory LRU.
  `/usage/by-model/trend` shares cached queries for the same period despite different top/metric
  values. DDB routes are independent of the Athena cache.
- **Athena data (model statistics, trends, history) is delayed by S3 delivery by 5 seconds to
  10 minutes**. Very recent usage is accurate only through DDB routes such as `/usage/me` and
  `/usage/aggregate`. Daily/weekly/monthly enforcement and aggregation use DDB and avoid this delay.
- **IAM path users are unsupported**: The path regex rejects users with IAM paths such as
  `/team/alice`. Username-based APIs cannot manage these users.
