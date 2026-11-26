[English](OperationalGuide.md) | [한국어](OperationalGuide.ko.md)

# Bedrock Meter 운영 가이드

이 문서는 Bedrock Meter를 일상적으로 운영하는 사람을 위한 가이드입니다. 한도와 예외 설정,
warning 확인, 차단 해제, 콘솔 계정 관리, 장애 대응을 다룹니다.

설치와 배포는 [README.ko.md](../README.ko.md), 코드와 AWS 구조는
[DeveloperGuide.ko.md](DeveloperGuide.ko.md), API 전체 명세는 [API.ko.md](API.ko.md)를 참고하세요.

## 1. 운영자가 알아야 할 핵심

1. Bedrock Meter는 Proxy가 아니라 **Bedrock invocation log 기반**입니다.
2. 호출이 실행된 뒤 로그를 받아 사용량을 더하고 IAM deny를 부착합니다.
3. 일 차단은 보통 5~30초, 주/월 차단은 최대 약 5분 뒤 적용됩니다.
4. IAM 반영에도 수 초가 걸릴 수 있어 한도를 정확히 0만큼만 초과하도록 보장하지 않습니다.
5. 한도는 token raw 합이 아니라 상대 비용을 반영한 `credits`입니다.
6. 운영 알림은 사용자 개인이 아니라 설정된 SNS 운영 이메일로 갑니다.
7. 수동으로 IAM deny만 삭제하지 말고 반드시 unblock API를 사용해야 합니다.
8. `exempt` 사용량은 계속 보이지만 warning과 block은 발생하지 않습니다.

## 2. 운영 접근 준비

배포 output을 환경변수로 설정하면 CLI 작업이 편합니다.

```bash
export AWS_REGION=ap-northeast-2
export BEDROCK_METER_API_ENDPOINT="$(
  jq -r '.BedrockMeterCoreStack.CoreApiEndpoint' cdk-outputs.json
)"
export BEDROCK_METER_CONSOLE_URL="$(
  jq -r '.BedrockMeterCoreStack.ConsoleUrl' cdk-outputs.json
)"
export BEDROCK_METER_USER_POOL_ID="$(
  jq -r '.BedrockMeterCoreStack.ConsoleUserPoolId' cdk-outputs.json
)"
```

Core API CLI 의존성:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install boto3 requests requests-aws4auth
```

현재 AWS identity 확인:

```bash
aws sts get-caller-identity
```

Core API admin route를 사용하려면 다음 중 하나여야 합니다.

- 배포 설정의 `BEDROCK_METER_ADMIN_USERNAMES`에 포함된 IAM User
- 배포된 Console BFF execution role

## 3. 콘솔 권한

| Cognito group | 권한 |
|---|---|
| `console-admins` | 조회, 한도 변경, exempt, unblock |
| `console-viewers` | 조회 전용 |

콘솔은 username/password 인증을 사용합니다.

- self-signup: 비활성
- MFA: 비활성
- 비밀번호: 7자 이상, 숫자 1개 이상
- session token: browser session storage

### Admin 생성 또는 비밀번호 재설정

같은 명령을 다시 실행하면 기존 사용자의 비밀번호와 group을 갱신합니다.

```bash
scripts/add-console-admin.sh <iam-username> <email>
```

### Viewer 생성 또는 강등

```bash
scripts/add-console-viewer.sh <iam-username> <email>
```

이 스크립트는 반대 group에서 사용자를 제거한 뒤 대상 group에 넣습니다. 따라서 admin을 viewer로
바꾸는 데도 사용할 수 있습니다.

명령은 전체 group 목록을 조회해 대상 group이 있고 반대 group이 없는 것을 확인한 경우에만
성공합니다. 제거가 실패하면 대상 group을 추가하기 전에 중단합니다. 추가 또는 최종 조회·검증이
실패해도 0이 아닌 종료 코드를 반환하며, 변경이 일부 적용된 상태일 수 있습니다. 재시도 전에
현재 group을 확인하세요. 이전 admin group을 자동으로 복원하지 않습니다.

이 검증은 Cognito group 변경의 확인이며 기존 콘솔 session의 즉시 회수를 보장하지 않습니다.
현재 BFF는 이미 발급된 token의 group을 만료까지 신뢰하고, `CONSOLE_ALLOWED_USERS`도 별도로
admin 권한을 부여할 수 있습니다. 계정·session 확인과 이 권한 예외의 제거는 별도 변경입니다.

### 사용자 비활성화

```bash
aws cognito-idp admin-user-global-sign-out \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"

aws cognito-idp admin-disable-user \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"
```

재활성화:

```bash
aws cognito-idp admin-enable-user \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"
```

### Cognito와 IAM username 규칙

Cognito 계정과 IAM User는 실제로 연결된 계정이 아닙니다. Console BFF는 IAM User가 아니라 자기
Lambda role로 Core API를 호출합니다.

Console admin은 자신을 포함한 IAM User의 한도와 `exempt` 설정을 변경할 수 있습니다. Cognito
username과 IAM username의 일치는 요구하지 않으며, 변경한 Console operator identity는 Audit에
기록됩니다.

## 4. Credits와 기간

기본 계산:

```text
credits =
  raw input × 1
  + cache read × 0.1
  + cache write × 1.25
  + output × 5
```

| 기간 | 정의 | 평가 |
|---|---|---|
| DAY | 전역 quota timezone의 00:00~23:59 | invocation log 수신 직후 |
| WEEK | 같은 timezone의 ISO week, 월~일 | 5분마다 |
| MONTH | 같은 timezone의 calendar month | 5분마다 |

현재 timezone은 Overview 카드와 Limits의 **Global Quota Calendar**에서 확인합니다. Statistics와
User Detail history의 Athena 시간축은 계속 UTC이므로 quota DAY와 그래프의 일 경계가 다를 수 있습니다.

기본 한도:

| Scope | Limit | Warning |
|---|---:|---:|
| DAY | 3,000,000 | 2,400,000 |
| WEEK | 15,000,000 | 12,000,000 |
| MONTH | 45,000,000 | 36,000,000 |

## 5. 사용량 확인

### Console

- Overview: 전체 사용량, warning/blocked 수, 최근 event
- Users: 사용자별 day/week/month pressure
- User Detail: token 종류, trend, effective limit, 상태
- Statistics: 모델별 trend와 invocation
- Audit: warning, block, limit change, unblock

| 화면 | 데이터 소스 | 표시 내용 |
|---|---|---|
| Overview | DynamoDB | 전체 day/week/month, warning/blocked 수, 상위 사용자, 최근 30일 전체 추세, audit |
| Users | DynamoDB | 사용자별 현재 day/week/month와 상태 |
| User Detail | DynamoDB + Athena | 현재 snapshot·한도·상태는 DynamoDB, 사용자 일/시간 history는 Athena |
| Statistics | Athena | 모델별 합계와 일/시간 추세 |
| Limits | DynamoDB | 전역 quota timezone, effective limit과 사용자 override |
| Blocked | DynamoDB | STATE 기준 차단 사용자와 현재 사용량 |
| Audit | DynamoDB | warning, block, resolved, limit 변경과 강제 해제 기록 |

DynamoDB를 사용하는 API:

- 현재/전체 사용량: `/usage/me`, `/usage/{username}`, `/users`,
  `/users/{username}`, `/usage/aggregate`, `/usage/history/all`
- 한도와 운영: `/limits/me`, `/limits/{username}`, `/admin/blocked`,
  `/admin/unblock/{username}`, `/admin/audit`, `/admin/settings/quota`

Athena를 사용하는 API:

- 사용자 이력: `/usage/me/history`, `/usage/{username}/history`,
  `/users/{username}/history`
- 분석: `/usage/top`, `/usage/by-model`, `/usage/by-model/trend`

DynamoDB 기반 화면은 invocation log가 수집된 뒤 보통 5~30초 안에 갱신됩니다. Athena 기반
Statistics와 사용자 history는 S3 전달 때문에 보통 5~10분 늦을 수 있고, 연속 재조회 시 최대
10초 동안 warm Lambda의 query cache 결과가 반환될 수 있습니다. Athena 지연은 한도 판정이나
차단 지연을 뜻하지 않습니다. 일·주·월 한도는 모두 DynamoDB DAY row로 평가됩니다.

### CLI

본인:

```bash
scripts/api-call.py GET /usage/me
scripts/api-call.py GET '/usage/me/history?period=month'
```

Admin:

```bash
scripts/api-call.py GET '/usage/aggregate?limit=100'
scripts/api-call.py GET '/usage/alice'
scripts/api-call.py GET '/usage/alice/history?period=last30d'
scripts/api-call.py GET '/usage/by-model/trend?period=month&top=10'
```

## 6. 한도 관리

### 전역 quota timezone

Console의 **Limits → Global Quota Calendar**에서 IANA timezone을 선택합니다. 변경에는 admin 권한과
감사 사유가 필요합니다.

CLI 예:

```bash
scripts/api-call.py GET '/admin/settings/quota'
scripts/api-call.py PUT '/admin/settings/quota' \
  '{"quotaTimeZone":"Asia/Seoul","reason":"Korean operating calendar"}'
```

타임존을 변경하면 policy version이 증가하며 현재 DAY/WEEK/MONTH 카운터는 새 namespace에서 0부터
시작합니다. 이는 기존 UTC row와 로컬 날짜 row가 섞이는 것을 방지하기 위한 동작입니다. 기존 row는
90일 TTL까지 보존됩니다. 차단 상태는 다음 5분 periodic check에서 새 카운터로 재평가됩니다.

일반 운영 중 반복적으로 변경하지 마세요. 변경 전 현재 차단 사용자와 한도 사용률을 확인하고,
가능하면 조직의 새 quota period 시작 시점에 수행하세요.
ConfigTable에서 `quotaTimeZone`만 직접 수정하면 policy version이 증가하지 않아 서로 다른 기간의
카운터가 섞일 수 있으므로 반드시 Console 또는 Core API를 사용하세요.

### 사용자별 한도 설정

Console의 Limits 화면을 권장합니다.

CLI 예:

```bash
scripts/api-call.py PUT '/limits/alice' \
  '{"dailyTokenLimit":2000000,"weeklyTokenLimit":8000000,"monthlyTokenLimit":25000000}'
```

일부 필드만 보내면 나머지는 default에서 상속합니다.

검증 규칙:

- 각 limit은 0~10^12 정수
- `monthly >= weekly >= daily`
- `warningThresholdPct`는 1~100
- `exempt`는 JSON boolean
- `0` 이하 limit은 해당 scope를 enforcement하지 않는 값으로 처리

현재 설정 확인:

```bash
scripts/api-call.py GET '/limits/alice'
```

### Override 제거

```bash
scripts/api-call.py DELETE '/limits/alice'
```

제거 후 즉시 default 정책을 상속합니다. 사용자가 이미 default 한도를 넘었다면 다음 평가에서 차단될
수 있으므로 먼저 사용량을 확인하세요.

### 전역 default 변경

현재 Core API에는 default row 전용 endpoint가 없습니다. 변경 창을 잡고 ConfigTable을 직접
갱신해야 합니다.

```bash
aws dynamodb update-item \
  --table-name BedrockMeterConfigTable \
  --region "$AWS_REGION" \
  --key '{"ConfigKey":{"S":"default"}}' \
  --update-expression \
    'SET dailyTokenLimit=:d, weeklyTokenLimit=:w, monthlyTokenLimit=:m, warningThresholdPct=:p' \
  --expression-attribute-values \
    '{":d":{"N":"3000000"},":w":{"N":"15000000"},":m":{"N":"45000000"},":p":{"N":"80"}}' \
  --return-values ALL_NEW
```

직접 DynamoDB 변경은 API validation과 SNS audit를 우회합니다.

변경 전 확인 사항:

1. `monthly >= weekly >= daily`
2. 현재 전체 사용자 중 새 한도를 넘은 사용자
3. 운영자 principal의 exempt 또는 충분한 한도
4. 변경 명령과 사유를 별도 change record에 기록

CDK seed 값을 바꾸고 redeploy하는 것으로는 기존 default row가 바뀌지 않습니다.

## 7. Warning

`warningThresholdPct`에 도달하면 SNS가 `WARNING` event를 발행합니다.

- DAY: 하루에 한 번
- WEEK: ISO week에 한 번
- MONTH: calendar month에 한 번
- `exempt`: 발행하지 않음
- 한 번에 limit을 넘으면 warning 없이 block event만 발생할 수 있음

이메일은 ConfigTable 사용자별 email로 가지 않습니다. 배포 설정
`BEDROCK_METER_ADMIN_EMAILS`에 등록하고 SNS 구독을 확인한 운영자에게 갑니다.

현재 구독 확인:

```bash
TOPIC_ARN="$(
  jq -r '.BedrockMeterCoreStack.SnsTopicArn' cdk-outputs.json
)"

aws sns list-subscriptions-by-topic \
  --topic-arn "$TOPIC_ARN" \
  --region "$AWS_REGION"
```

`SubscriptionArn`이 `PendingConfirmation`이면 수신함에서 구독 확인 링크를 눌러야 합니다.

email subscription에는 기본 filter가 없으므로 warning/block 외에도 resolved, limit 변경 audit,
CloudWatch alarm이 전달됩니다.

## 8. Exempt

`exempt: true`의 의미:

- 사용량과 history는 계속 기록
- warning 없음
- 자동 block 없음
- 이미 blocked인 상태를 자동으로 즉시 해제하는 명령은 아님

설정:

```bash
scripts/api-call.py PUT '/limits/service-admin' '{"exempt":true}'
```

해제:

```bash
scripts/api-call.py PUT '/limits/service-admin' '{"exempt":false}'
```

Console admin은 자신의 계정을 포함해 `exempt`를 설정할 수 있습니다. `exempt`는 warning과
자동 block을 모두 건너뛰므로, 적용 전 권한과 사용 목적을 확인하고 변경 사유를 남기세요.

## 9. Block

한도를 넘으면 대상 IAM entity에 다음 inline policy가 붙습니다.

```text
AutoBlocked-BedrockMeter-QuotaExceeded
```

이 정책은 Bedrock `Invoke*`, `Converse*`, `RetrieveAndGenerate*`, `InvokeAgent*`를 explicit deny합니다.
다른 AWS 서비스 접근이나 콘솔 로그인 전체를 막는 정책은 아닙니다.

차단 목록:

```bash
scripts/api-call.py GET /admin/blocked
```

IAM policy 확인:

```bash
aws iam get-user-policy \
  --user-name alice \
  --policy-name AutoBlocked-BedrockMeter-QuotaExceeded
```

Role enforcement가 켜진 환경은 `get-role-policy`를 사용합니다.

### 자동 해제

5분 주기 재평가에서 DAY/WEEK/MONTH가 모두 한도 미만이면 자동 해제되고 `RESOLVED`가 발행됩니다.

- DAY block: 설정된 quota timezone의 날짜가 바뀐 뒤 다른 scope도 미만이면 해제
- WEEK block: 다음 ISO week에 다른 scope도 미만이면 해제
- MONTH block: 다음 달에 다른 scope도 미만이면 해제

### 강제 해제

Console의 Blocked 화면 또는 다음 명령을 사용합니다.

```bash
scripts/manual-unblock.sh alice "approved temporary recovery"
```

또는:

```bash
scripts/api-call.py POST '/admin/unblock/alice'
```

강제 해제는 limit을 올리거나 사용량을 지우지 않습니다. 여전히 한도 초과이면 다음 invocation 또는
5분 평가에서 다시 차단됩니다. 필요하면 먼저 한도/exempt 정책을 변경하고 해제하세요.

### 하지 말아야 할 작업

```text
IAM Console에서 inline deny만 수동 삭제
DynamoDB STATE만 수동 삭제
UsageTable DAY counter 임의 감소
```

이 작업은 IAM과 STATE 정합성을 깨뜨립니다. PeriodicCheck가 deny를 다시 붙이거나 audit가 누락될 수
있습니다.

## 10. IAM User와 Role 정책

### IAM User

- full IAM User ARN으로 집계
- 기본 warning/block 대상
- 관리 API의 username target 지원

### STS assumed-role

- session ARN을 stable IAM Role ARN으로 합쳐 집계
- 같은 role을 쓰는 모든 session 사용량이 합산
- 기본값에서는 DAY 80~100% 구간 warning이 발생할 수 있지만 role block은 비활성
- 기본값에서 PeriodicCheck의 WEEK/MONTH warning과 block 평가는 role을 건너뜀
- `roleEnforcementEnabled=true`이면 공유 role 전체 session을 차단

SSO 사용자를 개인 단위로 구분하거나 차단하지 않습니다. SSO permission-set role을 차단하면 조직의
여러 사용자가 동시에 영향을 받을 수 있으므로 role enforcement는 별도 승인 없이 켜지 마세요.

## 11. Audit

주요 event:

| Event | 의미 |
|---|---|
| `WARNING` | scope warning threshold 도달 |
| `BLOCKED` | IAM deny 부착 |
| `RESOLVED` | 자동 재평가 해제 |
| `MANUAL_UNBLOCK` | 운영자 강제 해제 |
| `LIMIT_CHANGED` | per-user override 변경 |
| `LIMIT_REMOVED` | override 삭제 |

Console Audit 화면 또는 API:

```bash
scripts/api-call.py GET '/admin/audit?limit=100'
```

AuditTable 기본 TTL은 730일(2년)입니다. SNS redelivery는 message ID로 멱등 저장됩니다.
`scripts/deploy-all.sh`는 아직 남아 있는 기존 record의 TTL도 730일 기준으로 연장합니다.
이미 TTL 삭제된 record는 이 과정으로 복구되지 않습니다.

## 12. 정기 점검

### 매일

- Console Overview에서 warning/blocked 사용자 확인
- Audit에서 예상하지 못한 limit/exempt 변경 확인
- CloudWatch alarm email 확인
- UsageAggregator와 PeriodicCheck error alarm이 없는지 확인

### 매주

- SNS email subscription 상태
- Bedrock CloudWatch/S3 delivery failure metric
- Athena scan 비용과 query failure
- IAM policy와 blocked STATE drift
- S3 archive object가 계속 생성되는지 확인

### 배포 후

- CloudFormation `UPDATE_COMPLETE`
- Bedrock text logging `true`
- Core/Console URL HTTP 상태
- Cognito admin/viewer group
- test invocation의 DAY row 증가
- warning/block/unblock smoke test

### Console hosting 확장성 점검

현재 콘솔은 회사 내부의 소수 운영자를 위한 구조입니다. 정적 HTML/JS/CSS가 BFF Lambda code
package에 포함되고, API Gateway가 정적 요청과 `/api/console/*` 요청을 모두 같은 Lambda로
전달합니다. Lambda는 HTML을 생성하지 않고 build 시 만들어진 파일을 반환합니다.

이 구조에서는 browser cache에 없는 정적 요청도 Lambda invocation이 됩니다. 해시가 붙은
`/_next/static/*`는 1년간 cache되지만 HTML은 `no-cache`입니다. 다음 신호가 여러 주 지속되면
CloudFront + private S3(OAC) 또는 Amplify Hosting으로 정적 UI 분리를 검토합니다.

| 확인 항목 | 호스팅 분리를 검토할 신호 |
|---|---|
| 이용 범위 | 회사 외부 사용자 또는 다수의 상시 사용자가 접속 |
| 요청량 | 정적 요청 때문에 API Gateway/Lambda 요청 비용이 의미 있게 증가 |
| 동시성 | BFF Lambda `ConcurrentExecutions` 또는 `Throttles` 증가 |
| 지연 | 원거리 사용자의 HTML/asset 다운로드 지연이 반복 |
| 정적 용량 | image/font 등 artifact 크기와 전송량이 지속 증가 |
| 배포 | UI만 독립 배포·preview·rollback해야 하는 요구가 반복 |
| Edge 보안 | custom domain, WAF, bot control, edge access 정책 필요 |

CloudFront를 사용하면 S3 bucket은 public으로 열지 말고 OAC로만 접근시킵니다. `/api/console/*`는
기존 API Gateway/BFF origin으로 routing해 same-origin을 유지하는 방식을 우선합니다. 이 API
behavior는 cache를 끄고 `Authorization`과 query string을 origin에 전달해야 합니다. 정적 export의
`/users.html` 같은 파일이 `/users`로 열리도록 rewrite도 구성합니다. Amplify Hosting을 별도
origin으로 사용하면 CORS, CSP, 허용 origin과 runtime API base를 함께 변경해야 합니다. 어떤
hosting을 사용해도 Cognito token 검증과 Core API SigV4 서명은 BFF에 남겨야 하며 AWS credential을
웹 bundle에 넣으면 안 됩니다.

### Athena 확장성 점검

현재 raw JSON + Athena 구조는 일반적인 초기 운영에 적합합니다. 다음 값은 즉시 장애를 의미하는
제한이 아니라 Parquet 사전 집계의 비용 대비 효과를 검토하기 위한 신호입니다.

| 확인 항목 | 검토를 시작할 신호 |
|---|---|
| 조회 지연 | Statistics/history가 반복적으로 5~10초 이상 |
| Query scan | 자주 사용하는 30일 query가 수백 MB~1GB 이상 |
| CloudWatch alarm | `Athena ProcessedBytes > 2GB/hour`가 여러 날 반복 |
| S3 증가량 | invocation archive가 하루 수 GB 이상 |
| S3 object 수 | 작은 JSON object가 하루 수천 개 이상 |
| 조회 빈도 | 여러 운영자/dashboard가 시간당 수십 회 이상 조회 |
| 장기 분석 | 6~12개월 이상 기간을 반복 조회 |
| 비용 | Athena 비용이 월 운영비에서 별도 최적화가 필요한 수준 |

한 항목의 일시적인 spike만으로 구조를 바꾸지 않습니다. 최소 일주일간 query 실행 시간, scan bytes,
S3 object 수, 조회 빈도와 비용을 함께 관찰하고 둘 이상의 문제가 지속될 때 검토합니다.

도입할 때의 운영 원칙:

1. 원본 S3 JSON은 감사와 재처리를 위해 그대로 보존합니다.
2. 일별 또는 시간별 Parquet에는 principal·model별 raw/cache/output token과 invocation count를
   저장합니다.
3. 과거 Statistics/history만 Parquet으로 전환하고, 당일 freshness가 필요하면 raw log와 합칩니다.
4. 재처리와 backfill이 중복 집계를 만들지 않는지 확인합니다.
5. 원본과 Parquet의 일별 token 합계를 정기적으로 대조합니다.
6. DynamoDB 기반 한도 판정, warning, block, unblock 경로는 변경하지 않습니다.

Parquet 도입은 분석 성능·비용 최적화 작업입니다. quota 정확도 개선이나 차단 지연 해결책으로
취급해서는 안 됩니다.

## 13. 장애 대응 Runbook

### 13.1 사용량이 전혀 증가하지 않는다

1. 호출 리전과 stack 리전이 같은지 확인합니다.
2. Bedrock logging 설정을 확인합니다.

```bash
aws bedrock get-model-invocation-logging-configuration \
  --region "$AWS_REGION"
```

확인할 값:

- `cloudWatchConfig.logGroupName`
- `s3Config.bucketName`
- `textDataDeliveryEnabled=true`

3. Subscription Filter를 확인합니다.

```bash
aws logs describe-subscription-filters \
  --log-group-name /aws/bedrock/modelinvocations \
  --region "$AWS_REGION"
```

4. UsageAggregator error를 확인합니다.

```bash
AGGREGATOR_LOG_GROUP="$(
  aws cloudformation list-stack-resources \
    --stack-name BedrockMeterCoreStack \
    --region "$AWS_REGION" \
    --query \
      "StackResourceSummaries[?ResourceType=='AWS::Logs::LogGroup' && contains(LogicalResourceId, 'UsageAggregatorLogGroup')].PhysicalResourceId | [0]" \
    --output text
)"

aws logs tail "$AGGREGATOR_LOG_GROUP" --since 30m --region "$AWS_REGION"
```

`TransactionConflict` warning은 같은 principal의 DAY row를 여러 invocation이 동시에 갱신할 때
발생할 수 있는 일시 충돌입니다. 현재 함수는 50ms 기반 full-jitter exponential backoff로 최대
5회 시도하므로 warning 뒤 성공했다면 조치가 필요 없습니다. `TransactionCanceledException`이
`ERROR`로 끝나거나 error alarm이 반복되면 재시도 예산을 소진한 것입니다. 동일 principal에
집중된 호출량과 DynamoDB throttle을 함께 확인하고, 필요하면 CDK의
`DDB_TRANSACTION_MAX_ATTEMPTS`, `DDB_TRANSACTION_BASE_BACKOFF_MS`,
`DDB_TRANSACTION_MAX_BACKOFF_MS`를 조정한 뒤 재배포합니다.

5. 호출 log의 `identity.arn`과 token field가 존재하는지 확인합니다.

잘못된 inference profile 또는 다른 source region에서 호출되면 현재 stack이 log를 받지 못할 수 있습니다.

### 13.2 Day는 보이는데 Statistics/history가 비어 있다

Hot path는 정상이고 S3/Athena path만 지연되었을 가능성이 큽니다.

1. 최대 10분 기다립니다.
2. S3 archive에 새 object가 있는지 확인합니다.
3. Bedrock S3 delivery failure alarm을 확인합니다.
4. Athena workgroup이 `ENABLED`인지 확인합니다.
5. query 기간 partition이 호출 리전/date와 맞는지 확인합니다.

### 13.3 Warning 이메일이 오지 않는다

1. 사용자가 `exempt`인지 확인합니다.
2. 실제 total이 warning threshold를 넘었는지 확인합니다.
3. 같은 기간에 이미 warning이 발송됐는지 Audit에서 확인합니다.
4. 한 batch로 바로 100%를 넘어 `BLOCKED`만 발송된 것은 아닌지 확인합니다.
5. SNS subscription이 confirmed인지 확인합니다.
6. spam/quarantine 정책을 확인합니다.

### 13.4 사용자가 계속 blocked 상태다

1. day/week/month 중 어떤 scope가 아직 초과인지 확인합니다.
2. effective limit과 override를 확인합니다.
3. IAM inline policy와 UsageTable STATE를 함께 확인합니다.
4. PeriodicCheck Lambda error를 확인합니다.
5. 즉시 복구가 필요하면 limit/exempt를 먼저 변경하고 API로 force unblock합니다.

### 13.5 STATE는 blocked인데 IAM policy가 없다

수동 삭제 또는 중간 실패로 생긴 drift입니다. PeriodicCheck는 초과 상태라면 policy를 복원합니다.
정상 해제가 목적이면 unblock API를 호출하세요.

### 13.6 IAM policy는 있는데 STATE가 blocked가 아니다

API unblock을 먼저 시도합니다. 계속 남아 있으면 Lambda/IAM 오류를 확인한 뒤 운영 승인하에 policy와
STATE를 reconciliation합니다. 임의 삭제 전에 Audit와 CloudTrail을 보존하세요.

### 13.7 운영자 자신이 차단됐다

Bedrock deny는 Console/API 접근 자체를 막지 않으므로 다른 console admin으로 다음 순서를 수행합니다.

1. 운영자 한도 상향 또는 `exempt=true`
2. force unblock
3. IAM consistency를 고려해 약 10초 후 Bedrock 재시도

다른 admin이 없으면 배포 권한이 있는 break-glass operator가 ConfigTable과 Core API를 사용합니다.

### 13.8 Console 로그인이 안 된다

1. Cognito user가 `Enabled=true`, `CONFIRMED`인지 확인합니다.
2. `console-admins` 또는 `console-viewers` group에 속하는지 확인합니다.
3. 비밀번호를 `add-console-admin.sh` 또는 `add-console-viewer.sh`로 재설정합니다.
4. 브라우저 session storage를 지우고 다시 로그인합니다.
5. BFF Lambda error와 Console API access log를 확인합니다.

### 13.9 배포가 Bedrock logging owner 충돌로 실패한다

현재 destination을 먼저 확인합니다.

```bash
aws bedrock get-model-invocation-logging-configuration \
  --region "$AWS_REGION"
```

다른 시스템 소유라면 자동 overwrite하지 않습니다. 같은 stack의 Log Group/S3/role인지 검토한 뒤에만
일회성 overwrite 옵션을 사용합니다.

## 14. 변경에 재배포가 필요한가

| 작업 | 재배포 |
|---|---|
| per-user limit/warning/exempt | 불필요 |
| force unblock | 불필요 |
| Cognito admin/viewer 추가·비밀번호 재설정 | 불필요 |
| global default ConfigTable 변경 | 불필요, 직접 변경 |
| SNS email 목록 변경 | 필요 |
| Core API IAM admin 목록 변경 | 필요 |
| role enforcement 활성화 | 필요 |
| retention, resource name, schedule 변경 | 필요 |
| Console 코드 변경 | 필요 |
| Lambda/CDK 코드 변경 | 필요 |

현재 구조에서는 Console UI가 BFF Lambda package에 포함되므로 CSS, 화면, image 같은 정적 파일만
바꿔도 Lambda code asset 재배포가 필요합니다.

재배포:

```bash
./scripts/deploy-all.sh
```

신규 설치에서 `BEDROCK_METER_QUOTA_TIME_ZONE`을 생략하면 스크립트가 실행 환경의 IANA timezone을
감지해 `.env.deploy.local`에 저장합니다. CI, CloudShell, CodeBuild, 컨테이너에서는 UTC로 감지될 수
있으므로 다음처럼 명시하는 것이 안전합니다.

```bash
BEDROCK_METER_QUOTA_TIME_ZONE=Asia/Seoul ./scripts/deploy-all.sh
```

기존 설치는 재배포만으로 timezone이 바뀌지 않습니다. 배포 후 Limits 화면에서 변경합니다.

## 15. 데이터 보존과 삭제

- UsageTable, ConfigTable, AuditTable: PITR + RETAIN
- Invocation Log Group: RETAIN
- S3 archive/result bucket: RETAIN
- Cognito User Pool: RETAIN
- DedupTable: DESTROY
- Lambda/HTTP API와 운영 log groups: stack 관리
- Bedrock logging configuration: stack 삭제 시 기본적으로 유지

일반 stack 삭제는 보존 데이터를 남깁니다. 아래 삭제 스크립트에서 보존 또는 전체 삭제를 한 번 선택할 수 있습니다.

### 삭제

배포에 사용한 같은 AWS 터미널/profile과 리전으로 프로젝트 디렉터리에서 실행합니다.

```bash
bash scripts/destroy-all.sh
```

계정·리전은 스크립트가 자동으로 읽어 표시하므로 다시 입력하지 않습니다.
실제 `BedrockMeterCoreStack`을 조회하여 보존 리소스 이름을 표시하며,
`cdk-outputs.json`, 빌드 산출물, 새 설정 파일에 의존하지 않습니다. 질문은 아래 영문 선택 한 번입니다.

```text
How would you like to uninstall Bedrock Meter?

1. Remove the service, keep stored data
   Preserve Meter usage, settings, audit records, and console users.

2. Remove the service and Meter data
   Permanently delete the listed data resources.

Select [1/2, Enter to cancel]:
```

선택 자체가 해당 삭제의 최종 확인이며 추가로 묻지 않습니다. 데이터 보존 모드는 배포된
템플릿의 보존 정책을 따릅니다. 이후 새 배포가 이 리소스를 자동으로 재사용하지는 않습니다.
전체 삭제 모드는 stack 삭제 후 목록에 표시된 Meter 데이터도 정리하며, Meter 조회 결과 bucket의
객체 버전·delete marker·미완료 multipart upload를 포함합니다. Object Lock, 리소스 삭제 보호,
예상하지 못한 IAM 역할 연결은 강제로 해제하지 않습니다.

**Bedrock 호출 로그는 두 모드 모두 제외합니다.** Bedrock 로깅 설정을 변경하거나 중단하지 않고,
템플릿에서 호출 로그용으로 선언한 저장소·역할 및 현재 로깅 대상과 관련 전달 권한을 보존합니다.
템플릿에 명시하지 않은 `videoDataDeliveryEnabled` 같은 옵션 때문에 삭제가 중단되지 않습니다.
로깅을 유지하는 데 필요한 역할 권한과 bucket policy도 보존하므로, 로그 저장소만 남기고 로그
전달 권한을 삭제하는 동작을 방지합니다.

이 보존 정책이 없는 기존 stack은 삭제 전에 해당 리소스의 보존 정책만 갱신합니다. 애플리케이션
코드를 빌드하거나 변경하지 않습니다. 큰 템플릿은 검증된 Meter 조회 결과 bucket에 잠시 저장하고
갱신 후 해당 객체 버전을 제거합니다. Bedrock 호출 로그용 bucket은 staging에도 사용하지 않습니다.
보존 정책 갱신·확인에 실패하면 stack 삭제를 시작하지 않습니다.

Stack 삭제 후 진행 중이던 metering 요청이 끝나도록 기다린 뒤, 사용량 테이블에 기록된 IAM
주체에서 Meter의 정확한 quota deny 정책만 제거합니다. 외부 IAM 사용자·역할 자체는 삭제하지
않습니다. 현재 stack이 관리하지 않는 리소스를 이름만으로 찾아 삭제하지 않습니다.

Stack 조회·갱신·삭제 권한과 선택한 Meter 데이터 삭제 권한이 필요합니다. 기존 stack의 큰
템플릿 갱신에는 Meter 조회 결과 bucket의 객체 업로드·삭제 권한도 필요합니다. Quota 정리에는
사용량 테이블 Scan, IAM inline policy 조회·삭제 권한이 필요합니다. AWS CLI v2와 `jq`만 사용합니다.

실패 시 nonzero로 종료하고 stack ARN과 삭제가 확인되지 않은 리소스를 표시합니다. 이미
stack이 삭제됐다면 이 목록으로 수동 정리합니다. 스크립트 재실행이 이름만 보고 과거 리소스를
찾아 삭제하지는 않습니다. Stack이 없으면 아무것도 변경하지 않습니다. 삭제 중에는 해당
설치의 리소스를 다른 작업에서 변경하지 마세요.

“전체 삭제”의 범위는 목록에 표시된 stack 소유 리소스입니다. PITR이 활성화된 DynamoDB
테이블 삭제 후 AWS가 system recovery backup을 보존할 수 있습니다. 이런 AWS 복구 사본,
독립적인 AWS Backup 복구 지점, CloudTrail 기록, export 사본은 삭제 범위 밖이며,
공유 CDK bootstrap 리소스도 유지합니다.

### 수동 보존 기간 연장

배포는 기존 DAY/audit row의 만료 시각을 연장하지 않습니다. 과거 데이터의 보존 기간을 늘리려는
경우에만 실행합니다(Python 3.12 이상과 AWS CLI v2 필요, 추가 Python package 불필요).
현재 터미널 자격증명과 `AWS_REGION` → `AWS_DEFAULT_REGION` → profile 리전을 사용하고
계정·리전·대상 테이블을 출력합니다. 리전이 없으면 중단합니다.

먼저 AWS 계정과 `cdk-outputs.json`이 이번 설치의 출력인지 확인합니다.

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name BedrockMeterCoreStack \
  --query 'Stacks[0].Outputs' --output table
```

대상 확인 후, 사용량 180일·감사 730일로 연장하는 예시:

```bash
python3 scripts/backfill-retention.py \
  --usage-table "$(jq -er '.BedrockMeterCoreStack.UsageTableName' cdk-outputs.json)" \
  --audit-table "$(jq -er '.BedrockMeterCoreStack.AuditTableName' cdk-outputs.json)" \
  --usage-days 180 --audit-days 730
```

선택한 테이블을 scan하며 만료 시각을 늘리기만 하고 줄이지 않습니다. 일부 row 갱신 후 실패할 수
있으므로 원인을 확인·해결한 뒤 재실행합니다. 별도 배포는 필요하지 않습니다. 이 명령의 값은
새 데이터의 보존 정책을 바꾸지 않으므로 향후 데이터 정책은 CDK에서 별도로 설정합니다.

### 설치 실패와 수동 복구

설치에 사용한 같은 터미널 자격증명·리전을 사용합니다. 재시도 전에 실제 대상과 실패 이벤트를 확인합니다.

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name BedrockMeterCoreStack \
  --query 'Stacks[0].StackStatus' --output text
aws cloudformation describe-stack-events --stack-name BedrockMeterCoreStack \
  --query 'StackEvents[?ResourceStatusReason!=null].[LogicalResourceId,ResourceStatus,ResourceStatusReason]' \
  --output table
```

- **도구/빌드:** Node.js 22.12 이상(24 권장), AWS CLI v2, `jq`를 사용합니다. 원래 npm/빌드 오류를 해결하고
  재실행합니다. 빌드 실패 시 stack 변경은 시도하지 않습니다. 프로젝트 설정 파일에서 `AWS_*`를
  변경하지 말고 기존 `CDK_DEFAULT_*` 계정·리전 항목을 제거합니다.
- **자격증명/리전:** 선택한 AWS 로그인을 갱신하고 `AWS_REGION` 또는 profile 리전을 설정합니다.
  `CDK_DEFAULT_REGION`, `REGION`은 스크립트의 대상 선택 입력이 아닙니다.
- **Bootstrap/권한:** 표시된 권한 오류를 해결한 뒤 `./scripts/deploy-all.sh --bootstrap`을 실행합니다.
  Bootstrap 실패 시 애플리케이션 배포는 실행하지 않습니다. 자동 재시도나 권한 확대는 하지 않습니다.
- **진행 중인 stack:** 실제 작업이 끝날 때까지 기다리고 결과를 확인합니다.
- **`ROLLBACK_COMPLETE`:** 실패 리소스와 원인을 확인·해결합니다.
  `aws cloudformation list-stack-resources --stack-name BedrockMeterCoreStack`으로 리소스를
  확인하고 보존할 대상을 결정합니다. 검토 후에만
  `aws cloudformation delete-stack --stack-name BedrockMeterCoreStack`으로 실패 stack을 수동 삭제하고
  `aws cloudformation wait stack-delete-complete --stack-name BedrockMeterCoreStack`으로 기다립니다.
  다른 실패 상태에는 다른 복구가 필요할 수 있으므로 이 삭제 절차를 무조건 적용하지 않습니다.
- **이름/로깅 충돌:** Stack 삭제 후에도 테이블·invocation 로그 그룹·bucket·로깅 역할·사용자 풀이
  남을 수 있습니다. 잔존 리소스와 `aws bedrock get-model-invocation-logging-configuration`을
  확인합니다. 충돌 대상을 고아 리소스로 단정하지 않습니다. 운영자가 불필요한 잔재임을 확인한
  경우에만 수동 변경·삭제합니다. 데이터나 공유 로깅을 유지해야 하면 정리를 중단하고 재사용·이전을
  별도로 계획합니다.
- **관리자 생성:** 출력된 AWS 계정·리전과 실제 stack의 `ConsoleUserPoolId`를 확인합니다.
  조회 또는 Cognito 오류를 해결한 뒤 사용자 명령을 재실행합니다. 오래된 로컬 출력 파일로 자동
  전환하지 않습니다. 명령 재실행은 해당 사용자의 비밀번호와 권한 등급을 다시 설정합니다.

수동 복구 후 배포 명령을 재실행합니다. 배포 스크립트는 보존 리소스를 자동 삭제하거나 bucket을
비우거나 로깅 overwrite를 활성화하지 않습니다. 비어 있지 않은 bucket은 내용을 보존할지
운영자가 먼저 결정해야 합니다. Uninstall은 stack 삭제 전에 리소스를 확인하고, 남은 데이터·
계측 대상 IAM 주체의 차단 정책·계정 단위 Bedrock 로깅을 별도로 검토합니다. 새 설치를 위해
공유 리소스나 필요한 데이터를 삭제하지 않습니다.

## 16. 보안 운영 주의

- invocation log에는 prompt/response가 포함될 수 있습니다.
- archive bucket, CloudWatch Logs, Athena query 권한을 최소화합니다.
- Console endpoint는 internet-reachable이며 데이터 인증은 Cognito/BFF가 담당합니다.
- MFA는 현재 비활성입니다. 비밀번호 공유를 금지하고 퇴사·역할 변경 시 즉시 disable/sign-out합니다.
- emergency allowlist는 일시 복구용으로만 사용합니다.
- 운영자 IAM User를 test block 대상으로 사용하지 않습니다.
- role enforcement 변경은 공유 role blast radius를 승인받은 뒤 수행합니다.
- 직접 DynamoDB/IAM 조작은 마지막 수단이며 변경 사유와 복구 결과를 남깁니다.
