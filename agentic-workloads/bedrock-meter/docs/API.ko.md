[English](API.md) | [한국어](API.ko.md)

# Bedrock Meter Core API 명세

API Gateway HTTP API 기반. 모든 요청은 **AWS SigV4** 서명이 필수이며, IAM `execute-api:Invoke` 권한이 있는 caller만 호출할 수 있다.

- **Endpoint**: `https://<api-id>.execute-api.<region>.amazonaws.com` (CDK output `CoreApiEndpoint` 참조)
- **Auth**: API Gateway IAM Authorizer (SigV4). Lambda 핸들러는 `requestContext.identity.userArn`을 caller로 인식
- **Region**: `ap-northeast-2`
- **Content-Type**: `application/json`
- **시간 정의**:
  - quota 일(day) = ConfigTable `default.quotaTimeZone`의 calendar day
  - quota 주(week) = 같은 timezone의 ISO 8601 week (Mon~Sun)
  - quota 월(month) = 같은 timezone의 calendar month
  - Athena history/model analytics = UTC event timestamp

---

## 0. 권한 모델

| 종류 | 설명 |
|---|---|
| Self-service | 누구든 본인 데이터만 조회 (`/usage/me`, `/usage/me/history`, `/limits/me`) |
| Admin | `ADMIN_USERNAMES` 환경변수에 등록된 IAM username만 가능 (그 외는 403) |

**현재 admin 등록 방법**: 배포 전 `BEDROCK_METER_ADMIN_USERNAMES=alice,bob` 설정 후 redeploy.

**Principal 제약**: path param `{username}`은 path-less IAM username만 허용한다. STS assumed-role
사용량은 stable IAM Role ARN으로 집계되지만 role은 username 기반 관리 API 대상이 아니다.

**디자인 노트 (하이브리드)**: API path와 admin allowlist는 username을 사용하지만, **내부 저장소(DDB UsageTable/ConfigTable, Bedrock 로그, Athena, SNS 메시지)는 모두 full ARN을 유지**한다. Lambda는 path username을 받아 `account_id`를 합쳐 ARN으로 reconstruct한다.

---

## 1. 공통 응답 형식

### 성공 응답

```json
{ "<route별 schema>": "..." }
```

HTTP 200, `content-type: application/json`, `cache-control: no-store`. user 식별자가 포함된 응답에는 `username`과 `user_arn`이 함께 들어 있다.

### 에러 응답

| HTTP | Body | 의미 |
|---|---|---|
| 400 | `{"error": "<reason>"}` | 입력 validation 실패 (잘못된 username, body 등) |
| 401 | `{"error": "unauthenticated"}` | caller ARN을 식별 못 함 (인증 미설정) |
| 403 (API GW) | `{"message": "Forbidden"}` | SigV4 미서명 또는 `execute-api:Invoke` 권한 없음 |
| 403 (Lambda) | `{"error": "admin only", "caller": "<arn>"}` | admin allowlist에 없는 caller가 admin route 호출 |
| 404 | `{"error": "not found", "method": "...", "path": "..."}` | route 매칭 실패 (예: 슬래시 포함 username) |
| 500 | `{"error": "<type>", "message": "..."}` | AWS API 오류 또는 unhandled exception |

---

## 2. Self-Service Endpoints

### `GET /usage/me`

caller 본인의 일/주/월 사용량과 차단 상태.

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

> **단위는 credits (비용 가중 토큰).** `day`/`week`/`month`의 `total_tokens`와 모든 한도(`limit`)는 raw 토큰 단순합이 아니라 **credits**로 계산된다: `credits = raw input×1 + cache read×0.1 + cache write×1.25 + output×5`. 1 credit = raw input 1 token. cache read는 0.1×(가장 쌈), output은 5×(가장 비쌈). 가중치는 `default` ConfigTable row의 `costWeightCacheRead`/`costWeightCacheWrite`/`costWeightOutput`로 재배포 없이 조정 가능. enforcement(차단)도 동일하게 credits 기준.

`day`/`week`/`month` 모두 DDB에서 조회된다 (`week`/`month`는 해당 기간의 DAY row를 범위 합산 — Athena 미사용). `as_of`는 **조회 시각**(매 호출마다 현재 시각), `last_activity`는 **마지막 토큰 누적 시각**(가장 최근 DAY row의 `lastUpdated` — 오늘 활동이 없으면 직전 활동일). 콘솔의 "Last updated"는 `last_activity`를 쓰며 이는 `/usage/aggregate`의 `last_updated`와 동일한 값. 활동 이력이 전혀 없으면 `last_activity`는 `null`.

`quota_policy`는 DDB enforcement에 적용되는 전역 calendar다. policy version 1은 legacy
`DAY#yyyy-mm-dd`, 이후 버전은 `DAY#V<version>#yyyy-mm-dd` namespace를 사용한다.

---

### `GET /usage/me/history`

caller의 사용량 history (일/시간 단위).

**Query**:

- `period`: `week` | `month` | `last30d` (default `month`) — `granularity=day`일 때만 적용
- `granularity`: `day` | `hour` (default `day`). `hour`는 **고정 last48h 윈도우**(`period` 무시), 버킷 키는 `rows[].day`에 `"2026-06-05T13:00"` 형식으로 들어옴

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

사용량이 없는 버킷은 `rows`에서 제외. `granularity=hour`면 `rows[].day`가 `"2026-05-02T14:00"`(UTC 시각) 형식이 되고, 윈도우는 호출 시점 기준 최근 48시간 고정. Athena 결과 cache TTL은 10초다.

---

### `GET /limits/me`

caller 본인의 effective config (default + per-user override merge 결과).

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

`exempt: true`인 사용자는 응답에 `"exempt": true` 포함.

---

## 3. Admin Read Endpoints

모든 admin 라우트는 caller username이 `ADMIN_USERNAMES`에 포함되어야 함.

### `GET /users`

CoreStack만 배포한 환경에서도 외부 클라이언트가 사용자 목록을 발견할 수 있도록 제공하는 known-user 목록.

**Query**:

- `limit`: 1~200 (default 100)

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

known-user는 UsageTable의 `DAY#...`/`STATE` row와 ConfigTable의 per-user override를 합쳐 만든다. `username`은 ARN이 IAM User 형태일 때만 포함된다. `effective_limits`는 default + per-user override merge 결과로, console grid에서 quota progress bar를 그릴 때 쓴다.

> **참고**: console처럼 모든 user의 day + week + month 합계를 한꺼번에 보고 싶다면 `/usage/aggregate`를 쓰는 편이 빠르다 (`/users`는 day/state row만 반환 — week/month total은 빈 값). `/usage/aggregate`는 동일 row 스키마에 `week_total_tokens` + `month_total_tokens`를 DAY row 범위 합산으로 채워서 추가한다 (둘 다 DDB scan, Athena 미사용).

### `GET /users/{username}`

`GET /usage/{username}`의 alias. 커스텀 클라이언트가 user resource 중심으로 구현할 때 사용.

### `GET /users/{username}/history`

`GET /usage/{username}/history`의 alias.

### `GET /usage/{username}`

임의 사용자의 사용량 snapshot. `{username}`은 path-less IAM username (URL-encoding 불필요).

```
GET /usage/alice
```

**Response**: `/usage/me`와 동일 schema, `username`/`user_arn`은 path 값 기반.

**400**: username 형식 위반 (특수문자 등). 슬래시 포함 시 라우트 매칭 실패로 404.

---

### `GET /usage/{username}/history`

**Query**: `period` (`week`|`month`|`last30d`, default `month`), `granularity` (`day`|`hour`, default `day`)

**Response**: `/usage/me/history`와 동일 schema. `granularity=hour`는 최근 48시간 시간 단위 버킷 (`period` 무시).

---

### `GET /usage/top`

사용량 상위 N명.

**Query**:

- `metric`: `credits` | `inputTokens` | `rawInputTokens` | `cacheReadTokens` | `cacheWriteTokens` | `outputTokens` | `totalTokens` | `invocations` (default `totalTokens`)
- `period`: `day` | `week` | `month` (default `month`)
- `limit`: 1~100 (default 5)

> `credits`는 한도/enforcement와 동일한 비용 가중 합계(raw×1 + cacheRead×0.1 + cacheWrite×1.25 + output×5). `inputTokens`는 raw + cache read + cache write 단순합, `rawInputTokens`는 raw input만 — 분석용 분해 metric이다.

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

- `username`은 ARN이 IAM User 형태일 때만 포함됨. SSO/assumed-role ARN이 로그에 들어왔다면 user_arn만 반환

**400**: `metric`/`period`/`limit` 값 부적절.

---

### `GET /usage/aggregate`

모든 사용자의 day + week + month 합계를 한 응답에 합쳐 반환 (전부 DDB). console처럼 사용자 목록 + 사용량 그리드를 그릴 때 `/usage/{username}` per-user fan-out을 피하기 위해 추가됨.

**Query**: `limit` (1~200, default 100). `period`는 고정으로 day + week + month. week/month는 UsageTable DAY row를 한 번 scan해 범위 합산으로 도출 (Athena 미사용).

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

- `username`은 ARN이 IAM User 형태일 때만 포함
- **`today_total_tokens`/`today_invocation_count`는 오늘(UTC) DAY row 기준** — 콘솔 day 카드가 쓰는 값이며 `/usage/{username}`의 `day.total_tokens`와 일치. 오늘 활동이 없으면 0
- **`latest_day`/`latest_day_total_tokens`는 마지막 활동일** (오늘일 수도, 그 이전일 수도). `last_updated`(콘솔 "Last updated") 표시·발견용이며 day 사용량이 아님 — 자정 직후 today=0인데 latest_day_total>0인 게 정상
- `week_total_tokens`/`month_total_tokens`는 DAY row 범위 합산 결과 (DDB, credit 가중치는 읽을 때 적용)
- 호출은 admin 전용. 전부 DDB scan이라 Athena 지연 없음. day/today는 ConsistentRead 아님 (수초 차이 가능)

---

### `GET /usage/by-model`

모델별 사용량 합계.

**Query**: `period` (default `month`)

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

`input_tokens`는 raw input만, cache read/write는 별도 필드로 분리. `total_tokens`는 enforcement에서 쓰는 합계와 동일.

---

### `GET /usage/by-model/trend`

모델별 **시계열** (일/시간 단위). `/usage/by-model`이 기간 합계 한 줄을 주는 반면, 이쪽은 bucket × model로 쪼갠 long-format을 반환해 콘솔에서 모델별 추세 라인 차트를 그릴 수 있다.

**Query**:

- `period`: `week` | `month` | `last30d` (default `month`) — `granularity=day`일 때만 적용
- `granularity`: `day` | `hour` (default `day`). `hour`는 **고정 last48h 윈도우**(`period` 무시), `rows[].day`가 `"2026-06-05T13:00"` 형식
- `metric`: `totalTokens` | `invocations` (default `totalTokens`) — **top-N 선정 기준만** 결정. row에는 항상 full breakdown이 들어가므로 클라이언트가 토글해도 재쿼리 불필요
- `top`: 1~20 (default 10) — 기간 합계 상위 N개 모델만 개별 노출, 나머지는 `model_id="other"`로 합산

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

- `models`는 기간 합계 내림차순으로 정렬된 top-N 모델 id 목록 (top-N 밖이 있으면 끝에 `"other"` 포함). 콘솔은 이 순서로 차트 시리즈를 그린다
- `rows`는 `(bucket, model_id)` 단위 long-format. `day` 필드는 day granularity면 `"2026-06-01"`, hour면 `"2026-06-01T14:00"`(UTC). top-N 밖 모델은 같은 bucket의 `model_id="other"` row로 합산됨
- Athena 1쿼리(bucket × model GROUP BY) + 10초 TTLCache. top/metric 후처리는 Lambda 메모리에서 수행되므로 (period, granularity)가 같으면 캐시를 공유한다. hour는 day와 별도 캐시

**400**: `period`/`metric`/`granularity` 값 부적절, `top`이 1~20 밖이거나 정수 아님.

---

### `GET /limits/{username}`

임의 사용자의 effective config. Response schema는 `/limits/me`와 동일.

---

### `GET /admin/blocked`

현재 차단(`blocked=true`)된 모든 사용자.

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

빈 결과면 `rows: []`. `username`은 ARN이 IAM User 형태일 때만 포함.

---

## 4. Admin Write Endpoints

### `GET /admin/settings/quota`

현재 전역 quota calendar 설정.

```json
{
  "mode": "CALENDAR",
  "quotaTimeZone": "Asia/Seoul",
  "quotaTimeZoneVersion": 2
}
```

### `PUT /admin/settings/quota`

전역 quota timezone 변경. IANA timezone 이름과 감사 사유가 필요하다.

```json
{
  "quotaTimeZone": "Asia/Seoul",
  "reason": "Korean operating calendar"
}
```

timezone이 실제로 바뀌면 `quotaTimeZoneVersion`이 증가하고 active DAY/WEEK/MONTH 카운터가 새
namespace에서 시작한다. 이전 row를 새 timezone으로 재해석하지 않는다. 응답의
`countersReset=true`가 이 전환을 나타내며 `QUOTA_TIMEZONE_CHANGED` audit event가 발행된다.

같은 timezone을 다시 요청하면 `updated=false`, `countersReset=false`이고 version은 바뀌지 않는다.
잘못된 timezone, 누락된 reason, 동시 변경 충돌은 400이다.

---

### `PUT /limits/{username}`

per-user override upsert. Body의 키 중 일부만 보내도 되며, 누락된 키는 default fallback.

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

**허용 키**: `dailyTokenLimit`, `weeklyTokenLimit`, `monthlyTokenLimit`, `warningThresholdPct`, `exempt` 만. 그 외 키는 400.

**Validation**:

- 토큰 limit: 1 ≤ value ≤ 10^12 정수 (`0`은 허용하지 않음)
- `warningThresholdPct`: 1~100 정수
- `exempt`: boolean (`true`/`false`만)
- 같이 오는 경우 `monthly ≥ weekly ≥ daily` 강제
- **Self-lockout 방지**: caller ARN이 path username으로 reconstruct한 target ARN과 같으면 토큰 limit 키 거부 (warningThresholdPct는 OK)
- **Self-exempt 방지**: 같은 조건(target == caller ARN)에서 `exempt` 키 거부

**Response 200**:

```json
{
  "updated": true,
  "username": "alice",
  "user_arn": "arn:aws:iam::...:user/alice",
  "changes": { "dailyTokenLimit": 2000000 }
}
```

**Side effects**: SNS audit publish (`event=LIMIT_CHANGED`, `severity=INFO`, `scope=ADMIN`).

**Errors**:

- 400 `unknown keys: [...]`
- 400 `exempt must be boolean`
- 400 `<key> out of range (0..10^12)`
- 400 `weeklyTokenLimit must be >= dailyTokenLimit` 등

---

### `DELETE /limits/{username}`

per-user override 제거. 이후 default config 적용.

**Response 200**:

```json
{ "deleted": true, "username": "alice", "user_arn": "arn:..." }
```

**Side effects**: SNS audit publish (`event=LIMIT_REMOVED`).

---

### `POST /admin/unblock/{username}`

강제 차단 해제 — 한도 초과 여부와 무관하게 IAM Deny 정책 제거 + `STATE` row clear.

**Body**: 없음 (있어도 무시).

**Response 200**:

```json
{
  "unblocked": true,
  "username": "alice",
  "user_arn": "arn:...",
  "policy_removed": true
}
```

- `policy_removed: false` → 이미 IAM에 deny 정책이 없었음 (idempotent)

**Side effects**: SNS audit publish (`event=MANUAL_UNBLOCK`).

---

## 5. SNS Audit/Alert 메시지 형식

Bedrock Meter core가 SNS topic으로 발행하는 모든 메시지는 JSON 구조:

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

필드는 event 종류에 따라 일부만 포함됨. SNS `MessageAttributes`에 `severity`, `scope`, `event`가 별도로 들어 있어 subscription filter로 구독 필터 가능.

---

## 6. SigV4 호출 예제

### Bash + scripts/api-call.py

```bash
pip install boto3 requests requests-aws4auth   # 1회

export BEDROCK_METER_API_ENDPOINT="https://abc.execute-api.ap-northeast-2.amazonaws.com"
export AWS_REGION=ap-northeast-2

# 본인 사용량
scripts/api-call.py GET /usage/me

# Top 5 (admin)
scripts/api-call.py GET '/usage/top?metric=outputTokens&period=month&limit=5'

# 한도 변경 (admin) - URL-encoding 불필요
scripts/api-call.py PUT '/limits/alice' '{"dailyTokenLimit":2000000}'

# 강제 unblock
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

> ⚠️ `urllib + boto3.SigV4Auth` 조합은 `Host`/`Content-Length` 헤더 처리 차이로 API GW가 caller를 식별하지 못해 403을 반환하는 경우가 있음. **`requests-aws4auth` 사용을 권장**한다.

---

## 7. Caller 측 IAM 권한

API 호출자에게 attach 필요한 최소 권한:

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

`<api-id>`는 stack output `CoreApiEndpoint`의 host에서 추출. Admin 권한은 IAM이 아닌 Lambda 측 allowlist(`ADMIN_USERNAMES` 환경변수)로 통제.

---

## 8. Throttling

API GW 기본 stage에 `throttlingRateLimit=20 RPS`, `throttlingBurstLimit=40` 적용. 초과 시 429.

---

## 9. 운영 주의사항

- `force_unblock`은 hot/cold path가 다음 평가에서 다시 차단할 수 있음 — 한도 자체를 같이 조정하지 않으면 재차단됨
- `PUT /limits/{username}`은 admin 전용이며, admin은 자신의 IAM User를 포함해 limit과 `exempt`를 변경할 수 있음
- Athena 쿼리 route는 10초 TTLCache 적용 (메모리 LRU 128 entries). `/usage/by-model/trend`는 period가 같으면 top/metric이 달라도 같은 캐시를 공유한다. DDB route는 Athena 캐시와 무관
- **Athena 데이터(모델별 통계·추세·history)는 S3 delivery에 5초~10분 지연** — 매우 최근 사용량은 DDB 기반(`/usage/me`·`/usage/aggregate`)에서만 정확. 단 day/week/month enforcement·집계는 DDB라 지연 없음
- **IAM path 사용자 미지원**: `/team/alice` 같은 IAM path가 있는 user는 path 정규식이 거부함. 조직에서 IAM path를 쓰면 username 기반 API는 사용 불가
