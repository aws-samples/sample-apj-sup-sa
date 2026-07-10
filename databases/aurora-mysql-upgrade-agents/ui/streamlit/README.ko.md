[English](./README.md) | **한국어**

# Aurora MySQL Upgrade Readiness Checker — Streamlit UI (옵셔널)

배포된 오케스트레이터를 GUI 로 호출할 수 있는 간단한 Streamlit 앱입니다.
**로컬에서 실행**되며, 사용자 PC 의 AWS 자격 증명으로 AgentCore 를 호출합니다.
(앱 자체를 배포할 필요 없음 — CDK 스택과 독립적)

## 사전 요구사항

- CDK 스택 (`infra/`) 이 이미 배포되어 있고 Outputs 를 알고 있음
- 에이전트가 접속할 수 있는 **활성 Aurora MySQL Blue-Green 배포** (Blue 3.04 /
  Green 3.10) — 실행 시점에 필요한 전체 조건은 [루트 README](../../README.ko.md#사전-요구사항)
  의 "Aurora MySQL 환경" 항목 참고
- Python 3.10+
- AWS 자격 증명이 `aws configure` 로 설정되어 있고 `bedrock-agentcore:InvokeAgentRuntime` 권한이 있음

## 실행

```bash
cd ui/streamlit

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env 에 CDK Outputs (ARN 6개) 만 입력

streamlit run app.py
```

브라우저가 자동으로 `http://localhost:8501` 을 엽니다.

## `.env` 채우는 법

UI 는 **두 개의 `.env`** 를 순서대로 읽습니다:

1. `../../infra/.env` — VPC / DB / 호스트 / 리전 / 버킷 등 CDK 배포 시 이미 채운 값들
2. `./.env` — 이 UI 전용 값 (주로 CDK 가 만들어낸 6개 ARN)

즉 **`infra/.env` 에 써둔 값은 복사할 필요 없습니다.** UI 가 자동으로 가져갑니다.

`cdk deploy` 가 끝나면 터미널에 CloudFormation Outputs 가 출력됩니다:

```
Outputs:
AuroraUpgradeAgentStack.OrchestratorArn = arn:aws:bedrock-agentcore:...
AuroraUpgradeAgentStack.VariablesCompareArn = arn:aws:...
AuroraUpgradeAgentStack.ErrorLogAnalyzerArn = arn:aws:...
AuroraUpgradeAgentStack.UpgradeReadinessArn = arn:aws:...
AuroraUpgradeAgentStack.QueryRiskScorerArn = arn:aws:...
AuroraUpgradeAgentStack.PlanDiffArn = arn:aws:...
```

이 6개만 `./.env` 에 복사하면 끝입니다.

### 값을 덮어쓰고 싶다면

UI 의 `.env` 는 `infra/.env` 를 **override** 합니다. 예를 들어 다른 리전에서
테스트하고 싶다면 `./.env` 에 `CDK_DEFAULT_REGION=ap-northeast-2` 만 추가하면
UI 만 그 리전으로 호출합니다.

### 표시 언어

UI 의 표시 언어와 리포트 언어는 `infra/.env` 의 `REPORT_LANGUAGE` 값을 함께
따릅니다 (`ko` = 한국어, `en` = English, 기본값 `ko`). `./.env` 에
`REPORT_LANGUAGE=en` 을 추가하면 UI 와 리포트가 모두 영어로 표시됩니다.

## 화면 구성

- **상단 배지** — `.env` 값이 모두 채워졌는지 실시간 체크
- **진행 상황** — 각 step 을 체크리스트로 표시 (실행 중 ⏳ / 완료 ✅)
- **상세 로그** (접힘) — 터미널 스트리밍 출력
- **사이드바 리포트** — 분석 완료 후 `.md` 리포트 5종 다운로드

## 트러블슈팅

| 증상 | 조치 |
| --- | --- |
| `AccessDeniedException` | AWS 자격 증명에 `bedrock-agentcore:InvokeAgentRuntime` 권한 있는지 확인 |
| `.env` 배지가 계속 ❌ | `cd ui/streamlit && cp .env.example .env` 후 값 입력 확인 |
| 리포트 다운로드 실패 | presigned URL 의 만료 시간 지났을 수 있음. 다시 분석 실행 |
| 스트림이 중간에 끊김 | AgentCore timeout (기본 15분) 확인. 재실행 |
