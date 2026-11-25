[English](README.md) | [한국어](README.ko.md)

# Bedrock Meter Console

배포된 Bedrock Meter Core API를 위한 React/Next.js 콘솔입니다.

## 아키텍처

콘솔은 **공개 HTTP API 뒤의 단일 BFF Lambda**로 배포됩니다(`$default` route, authorizer 없음).
하나의 Lambda가 두 기능을 제공합니다.

- **정적 UI**: Next.js `output: 'export'` 결과를 `bff-lambda/dist/public`에 포함합니다.
- **BFF** (`/api/console/*`): 로컬 개발과 동일한 `src/lib/server/*` 코드를
  `bff-lambda/build.mjs`로 bundle하고 `bff-lambda/handler.ts`로 routing합니다.

공개 origin은 하나이며 CORS, public S3, CloudFront는 사용하지 않습니다.
**Lambda Function URL 대신 HTTP API를 사용하는 이유:** 이 계정의 SecurityHub 제어
`lambda-function-public-access-prohibited`는 `Principal: *`가 있는 Lambda resource policy를
edge에서 차단합니다. 공개 Function URL에는 이 정책이 필요하므로 함수 실행 전에 403이 반환됐습니다.
API Gateway는 `SourceArn`으로 범위를 제한한 `apigateway.amazonaws.com` service principal로
Lambda를 호출하므로 public principal 없이 이 제어를 충족합니다. HTTP API payload format 2.0은
Function URL과 event/response 구조가 같아 `handler.ts`를 변경할 필요가 없습니다.
Public S3도 계정의 Block Public Access로 차단되므로 CloudFront 없이 직접 공개하는 두 방식은
사용할 수 없습니다.

이 hosting 구조는 **접속량이 낮은 사내 운영 콘솔**을 대상으로 합니다. Next.js가 build 시
HTML, JavaScript, CSS를 생성하며 Lambda는 SSR이나 요청별 HTML 생성을 수행하지 않습니다.
생성된 `out/`을 `bff-lambda/dist/public/`에 복사하고 `dist/index.cjs`와 함께 패키징합니다.
Cache되지 않은 정적 요청마다 Lambda 파일시스템에서 파일을 읽습니다. 해시가 붙은
`/_next/static/*`는 1년 immutable browser cache를 사용하고 HTML은 `no-cache`를 사용합니다.

콘솔이 이 범위를 넘으면 정적 export를 다음 중 하나로 옮깁니다.

- **CloudFront + OAC를 사용하는 private S3**: `/api/console/*`를 기존 API Gateway origin으로
  routing하여 브라우저에서 하나의 origin을 유지하는 구성을 권장합니다.
- **AWS Amplify Hosting**: 독립적인 frontend CI/CD, preview, 관리형 custom domain이 중요할 때
  사용합니다. 별도 origin이면 CORS, CSP, 허용 origin, runtime API base를 명시적으로 설정해야 합니다.

어떤 구조에서도 Cognito token 검증과 Core API SigV4 서명은 BFF에 유지합니다.
AWS credential이나 Core API 직접 접근을 브라우저에 제공하지 마세요.

BFF는 **자신의 execution role 임시 credential**로 Core API를 SigV4 서명합니다.
Core는 `ADMIN_ROLE_NAMES` 환경변수에서 BFF의 고정 role 이름(`BedrockMeterConsoleBffRole`)을
admin **caller**로 허용합니다. Assumed-role session suffix가 달라지므로 role 이름으로 비교합니다.
IAM User는 기본 enforcement 대상입니다. STS session은 stable IAM Role로 정규화하며 role 전체
차단은 opt-in입니다. 장기 IAM User key나 SSM 서명 parameter는 사용하지 않습니다.

## 인증 (Cognito)

- 기본적으로 모든 데이터 API는 `console-admins` 또는 `console-viewers` 구성원만 사용할 수 있습니다.
  `/api/console/config`와 정적 UI asset만 공개됩니다.
- 브라우저는 **모든** 호출에 Cognito **ID token**을 `Bearer` header로 보냅니다.
  BFF(`src/lib/server/cognito-auth.ts`)는 `aws-jwt-verify`로 검증합니다.
  `console-admins`는 읽기/쓰기가 가능하고 `console-viewers`는 읽기 전용입니다.
  `CONSOLE_ALLOWED_USERS`는 기본적으로 비어 있는 비상 admin override입니다.
- Cognito `console-admins` group으로 **재배포 없이 콘솔 사용자를 추가·제거**합니다.

  ```bash
  POOL=<ConsoleUserPoolId>; GROUP=console-admins
  aws cognito-idp admin-create-user --user-pool-id $POOL --username alice \
    --user-attributes Name=email,Value=alice@amazon.com Name=email_verified,Value=true --message-action SUPPRESS
  aws cognito-idp admin-set-user-password --user-pool-id $POOL --username alice --password '<temp>' --permanent
  aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username alice --group-name $GROUP
  # 권한 회수(사용자는 유지하고 group만 제거): admin-remove-user-from-group
  # 즉시 session을 종료하려면 admin-user-global-sign-out도 실행
  ```

  Group 정보는 `cognito:groups`에 들어가므로 추가/제거는 **다음 token**부터 적용됩니다
  (재로그인 또는 최대 60분 만료). Group과 비상 allowlist가 모두 비어 있으면 접근을 거부합니다.
- 브라우저는 Amplify/SDK 없이 Cognito `USER_PASSWORD_AUTH`를 직접 사용합니다(`src/lib/auth.tsx`).
  Cognito **region과 app client ID는 build가 아닌 runtime config**에서 가져옵니다.
  로그인 전에는 실패할 query를 보내는 대신 앱 shell에 "Sign in required" 안내를 표시합니다.
- 브라우저가 *실제 IAM User*로 직접 인증할 수는 없습니다. 장기 key를 브라우저에 배포할 수 없고,
  Cognito Identity Pool → STS는 Core가 거부하는 assumed-role ARN을 반환합니다.
  지원하는 운영자 identity 모델은 Cognito + Cognito group입니다.

모든 BFF 응답에는 CSP, HSTS, frame 차단, referrer, MIME-sniffing 방지, permissions header가 포함됩니다.

## Runtime Config (환경 독립 build)

정적 build에는 Console URL이나 Cognito client ID 등 환경값을 넣지 않습니다.
최초 로드 시 브라우저가 `GET /api/console/config`를 조회합니다.

```jsonc
{ "apiBase": "/api/console",
  "auth": { "provider": "cognito", "region": "...", "userPoolId": "...", "appClientId": "..." } }
```

`region`/`userPoolId`/`appClientId`는 공개 정보이며 secret이 아닙니다. BFF는 Core API endpoint를
반환하지 않습니다. `/config`는 인증 **전에** 제공하므로 identity와 관계없이 콘솔이 시작되고
설정 오류 상태도 표시할 수 있습니다. `apiBase`는 same-origin `/api/console`으로 고정됩니다.
처음 `/config` 호출에 이미 이 경로를 사용하므로 응답에서 다시 읽지는 않습니다.

## 배포

프로젝트 루트에서 실행합니다. Node.js 22.12 이상(24 권장)과 AWS CLI v2, `jq`가 필요합니다.

```bash
./scripts/deploy-all.sh --bootstrap  # 최초 계정/리전 준비를 포함한 배포
./scripts/deploy-all.sh              # 이후 재배포
```

계정·리전은 터미널의 AWS 자격증명과 환경변수/profile에서 가져옵니다. `.env.deploy.local`에는
프로젝트 옵션만 둡니다. 스크립트가 정적 UI, BFF, 배포 package를 빌드한 뒤 CDK를 실행합니다.
최초 콘솔 사용자는 같은 AWS 대상에서 `scripts/add-console-admin.sh <username> <email>`로
생성합니다. 읽기 전용 사용자는 `add-console-viewer.sh`를 사용합니다.

CDK는 `index.cjs`와 `public/index.html`이 없으면 synth 단계에서 중단합니다. 직접 CDK 명령을
사용하는 개발자는 먼저 `npm run build:console-artifact`를 실행해야 합니다.

## 로컬 실행

정적 export와 호환되지 않아 Next route handler는 제거했습니다. 로컬 UI 개발 시 정적 앱이 배포된
BFF를 사용하도록 설정합니다. Cognito client ID는 해당 BFF의 `/api/console/config`에서 runtime에
가져오므로 build 시 설정하지 않습니다.

```bash
npm install
NEXT_PUBLIC_CONSOLE_API_BASE="<ConsoleUrl>/api/console" npm run dev
```

`NEXT_PUBLIC_CONSOLE_API_BASE`는 개발 전용 override이며 production은 same-origin
`/api/console`을 사용합니다. AWS credential은 브라우저에 노출되지 않으며 모든 SigV4 서명은
배포된 BFF Lambda에서 수행합니다.

## Audit Log

`Audit` 페이지는 영속적인 DynamoDB **`BedrockMeterAuditTable`**을 사용합니다.
Core stack의 `audit-writer` Lambda가 SNS alerts topic을 구독해 `BLOCKED`, `WARNING`, `RESOLVED`
event를 기록합니다. BFF를 통한 admin 변경(`LIMIT_CHANGED`, `LIMIT_REMOVED`, `MANUAL_UNBLOCK`)은
`[by <email>]` actor suffix와 함께 기록됩니다. 페이지는 cursor pagination을 지원하는
Core API `GET /admin/audit`로 조회하므로 재시작 후에도 유지되고 여러 instance가 공유합니다.

## 사용자 탐색

Core API의 `GET /users`로 사용자를 찾을 수 있습니다. 사용량이 발생하기 전에 표시해야 하는
사용자에는 `BEDROCK_METER_EXTRA_USERS`가 여전히 유용합니다.

사용량이 없어도 콘솔에 표시할 사용자는 `BEDROCK_METER_EXTRA_USERS`에 지정하세요.

설치 방법과 전체 문서 목록은 [프로젝트 README](../../README.ko.md)를 참고하세요.
