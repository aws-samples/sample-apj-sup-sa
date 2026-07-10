[English](./README.md) | **한국어**

# rds-mysql-upgrade-agents

MySQL 업그레이드 준비 상태를 자동 분석하는 AWS Bedrock AgentCore 기반
multi-agent 패키지. 고객 AWS 계정에 CDK 로 바로 배포할 수 있는 자체 완결
구조입니다.

## 지원 버전

| 항목 | 지원 범위 |
| --- | --- |
| **MySQL (Source)** | 8.0.x |
| **MySQL (Target)** | 8.4.x |
| **AWS 서비스** | Amazon RDS for MySQL, Amazon Aurora MySQL-compatible |
| **리전** | `us-east-1` 중심 검증. 다른 리전은 Bedrock AgentCore 가용 여부 확인 필요 |
| **CDK** | AWS CDK v2 (Python) |
| **Runtime** | Bedrock AgentCore (Linux ARM64 컨테이너) |

> 위 범위 밖의 조합 (예: MySQL 5.7, MariaDB, MySQL → 9.x) 은 미검증이며 동작을
> 보장하지 않습니다.

## 구조

```
rds-mysql-upgrade-agents/
├── infra/                        # CDK (Python) 배포 코드 (필수)
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   ├── .env.example
│   ├── cdk_rds_mysql_upgrade/stack.py
│   └── lambda/agent_runtime_cr/handler.py
│
├── agents/                       # 에이전트 — 1폴더 1에이전트
│   ├── orchestrator/             #   전체 파이프라인을 조율
│   ├── variables-compare/        #   Blue/Green SHOW VARIABLES 비교
│   ├── error-log-analyzer/       #   CloudWatch RDS error log 분석
│   └── upgrade-readiness/        #   InnoDB status + Query optimizer 리스크 분석
│       ├── Dockerfile
│       ├── agent.py
│       └── requirements.txt
│
└── ui/streamlit/                 # (옵션) GUI — 로컬에서 실행
    ├── app.py
    ├── requirements.txt
    ├── .env.example
    └── README.md
```

각 에이전트 폴더는 자체완결 구조입니다 — `Dockerfile`, 진입점 `agent.py`,
의존성 `requirements.txt` 가 같이 있습니다. 에이전트 추가/제거/수정은 해당
폴더만 만지면 되고, `stack.py` 의 `agents` 매핑에 새 슬러그만 추가하면
CDK 가 자동으로 이미지 빌드 + AgentCore Runtime 을 만들어 줍니다.

## CDK가 만드는 리소스

- **S3 Bucket** — 분석 리포트 저장 (`REPORTS_BUCKET_NAME`)
- **IAM Role** — 4개 agent 가 공유하는 실행 역할
  (`bedrock-agentcore.amazonaws.com` trust, Bedrock / CWLogs / ECR / VPC ENI / S3 / AgentCore invoke)
- **ECR Images × 4** — orchestrator, variables-compare, error-log-analyzer, upgrade-readiness (ARM64)
- **AgentCore Runtime × 4** — 고객 VPC 내부에서 실행
- **Lambda** — AgentCore Runtime 을 만들/지우는 CustomResource 핸들러

VPC, Subnet, Security Group, RDS 는 **이미 존재한다고 가정**하며 새로 만들지 않습니다.

## 사전 요구사항

### 빌드 / 배포 도구

- AWS CLI 자격 증명
- Python 3.10+
- Node.js + AWS CDK v2 (`npm install -g aws-cdk`)
- Docker 또는 Finch (`linux/arm64` 빌드 가능해야 함)
- 대상 계정에 `cdk bootstrap` 완료

### MySQL 환경 (실행 시점에 필요)

이 패키지는 **활성 상태의 MySQL Blue-Green 배포**를 분석합니다 — 양쪽 인스턴스에
네트워크로 접속해 파라미터·상태·통계를 읽어옵니다. 다음 조건은 **orchestrator 를
실행하기 전에** 갖춰져 있어야 합니다 (배포 시점이 아니라 실행 시점 기준):

- **활성 Blue-Green 배포가 존재해야 함.** 첫 단계(`check_blue_green_deployment`)가
  Blue 와 Green **양쪽** 인스턴스에 접속하며, 한쪽이라도 실패하면 워크플로가
  중단됩니다. Green 이 없는 단일 인스턴스로는 동작하지 않습니다.
- **Blue = MySQL 8.0.x, Green = MySQL 8.4.x.** 분석이 이 업그레이드 조합을 전제로
  설계되어 있습니다.
- **양쪽 인스턴스 endpoint 가 3306 포트로 접근 가능해야 함** — AgentCore 에 지정한
  Subnet / Security Group (`SUBNET_IDS` / `SECURITY_GROUP_IDS`) 기준.
- **DB 사용자가 진단 정보를 읽을 수 있어야 함.** 시크릿의 사용자(또는 `DB_USER`)에
  `SHOW VARIABLES`, `SHOW ENGINE INNODB STATUS`, `performance_schema` SELECT
  권한이 필요합니다.
- **DB 자격증명이 담긴 Secrets Manager 시크릿이 미리 존재해야 함.** 배포 **전에**
  JSON 형식(`{"username": "...", "password": "..."}` — RDS 관리형 마스터 시크릿과
  동일 형식)으로 만들고, 그 이름/ARN 을 `DB_SECRET_ID` 에 지정하세요. Blue/Green 은
  시크릿 하나를 공유합니다. 비밀번호는 `.env` 에 저장하지 않습니다.
- **Green 인스턴스의 error log 가 CloudWatch Logs 로 export 되어 있어야 함**
  (아래 `GREEN_LOG_GROUP` 참고). 없으면 Error Log Analyzer 가 읽을 대상이 없습니다.

## 배포 순서

```bash
cd infra

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env 를 열어 VPC/Subnet/SG/S3/DB 값 채우기

cdk bootstrap   # 해당 account/region 에 처음이라면 1회
cdk synth       # 템플릿 검증
cdk deploy
```

## 채워야 하는 환경변수 (`infra/.env`)

| 변수 | 설명 |
| --- | --- |
| `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` | 배포 대상 계정 / 리전 |
| `VPC_ID` | 기존 VPC ID |
| `SUBNET_IDS` | AgentCore 에서 쓸 Private Subnet (쉼표로 구분, 2개+ 권장) |
| `SECURITY_GROUP_IDS` | RDS 로 3306 outbound 허용된 SG |
| `REPORTS_BUCKET_NAME` | 리포트용 S3 버킷 이름 prefix. 실제 버킷명은 `<prefix>-<DEPLOYMENT_SUFFIX>` 로 자동 생성됨 (예: `rds-mysql-upgrade-reports-d2be5a`). 소문자/숫자/`-` 만 허용 |
| `REPORT_LANGUAGE` | 리포트 / 요약 / UI 출력 언어 (`ko` = 한국어, `en` = English). 선택, 기본값 `ko` |
| `BEDROCK_MODEL_ID` | 모든 에이전트가 사용하는 Bedrock 모델 ID (env 로 주입됨). 선택, 기본값 `us.anthropic.claude-sonnet-4-6`. `us-east-1` 은 in-region endpoint 가 없으므로 geo inference profile prefix (`us.` / `eu.` / …) 사용 |
| `BLUE_HOST` / `GREEN_HOST` | Blue(MySQL 8.0) / Green(MySQL 8.4) 인스턴스 호스트 |
| `DB_SECRET_ID` | DB 자격증명이 담긴 AWS Secrets Manager 시크릿 **이름 또는 ARN**. 배포 시 에이전트 runtime role 에 이 시크릿에 대한 `secretsmanager:GetSecretValue` 권한이 부여되고, 에이전트가 실행 시점에 비밀번호를 조회 |
| `DB_USER` | 선택 — 시크릿의 `username` 을 덮어쓸 때만 지정 (비우면 시크릿 값 사용) |
| `GREEN_LOG_GROUP` | Error Log Analyzer 가 읽을 CloudWatch Log Group |

> **Secrets Manager 로 자격증명 관리.** DB 비밀번호는 `.env` 에 저장하거나
> payload 로 전달하지 **않습니다.** Secrets Manager 시크릿에 저장하고
> (`{"username": "...", "password": "..."}` 형태 JSON — RDS 관리형 마스터
> 시크릿과 동일한 형식) `DB_SECRET_ID` 로 지정하세요. Blue/Green 은 복제본이라
> 시크릿 하나로 양쪽 인증이 됩니다. 연결은 에이전트 이미지에 포함된 RDS CA
> 번들로 TLS 검증(`ssl_verify_cert`)을 수행합니다.
>
> 최소 권한을 위해 마스터 `admin` 대신 읽기 전용 모니터링 유저 + reader
> endpoint 사용을 권장합니다.

## 리포트 언어

모든 출력 — 마크다운 리포트, LLM 요약, Streamlit UI 텍스트, 실시간 진행 로그 —
이 하나의 설정을 따릅니다:

```ini
# infra/.env
REPORT_LANGUAGE=ko   # 한국어 (기본값)
REPORT_LANGUAGE=en   # English
```

언어를 바꾸는 방법:

- **Streamlit UI** — `infra/.env` 의 `REPORT_LANGUAGE` 를 바꾼 뒤 앱을 재시작
  (`streamlit run app.py`). **재배포 불필요** — 언어는 실행할 때마다 payload 로
  에이전트에 전달됩니다.
- **boto3 직접 호출** — orchestrator payload 에 `"language": "ko"` 또는
  `"language": "en"` 을 지정 (아래 예시 참고). 생략하면 `ko` 가 기본값입니다.

언어 값이 요청 payload 로 전달되므로, 언어만 바꿀 때는 `cdk deploy` 가 필요
없습니다. `cdk deploy` 는 에이전트 코드가 바뀔 때만 필요합니다.

## 배포 산출물 (CloudFormation Outputs)

- `OrchestratorArn` — 애플리케이션에서 호출할 메인 ARN
- `VariablesCompareArn` / `ErrorLogAnalyzerArn` / `UpgradeReadinessArn`
- `ReportsBucketName`
- `RuntimeRoleArn`

## Orchestrator 호출 예시

아래의 `<...Arn>` 과 `<ReportsBucketName>` 자리표시자는 `cdk deploy` 가 끝날 때
출력되는 **CloudFormation Outputs** 값입니다 (바로 위 섹션에도 정리되어 있음).
각 자리표시자를 해당 Output 값으로 바꿔 넣으세요.

```python
import boto3, json

client = boto3.client("bedrock-agentcore", region_name="us-east-1")

payload = {
    "blue_host":  "blue.xxxx.us-east-1.rds.amazonaws.com",
    "green_host": "green.xxxx.us-east-1.rds.amazonaws.com",
    "db_secret_id": "<Secrets Manager 시크릿 이름 또는 ARN>",  # 비밀번호는 실행 시점에 조회
    "s3_bucket":  "<ReportsBucketName>",  # CfnOutput 으로 노출됨
    "language":   "ko",                   # 리포트 언어: "ko" 또는 "en" (선택, 기본 "ko")
    "green_log_group":  "/aws/rds/instance/<green>/error",
    "variables_compare_arn":           "<VariablesCompareArn>",
    "error_log_analyzer_arn":          "<ErrorLogAnalyzerArn>",
    "upgrade_readiness_analyzer_arn":  "<UpgradeReadinessArn>",
}

resp = client.invoke_agent_runtime(
    agentRuntimeArn="<OrchestratorArn>",
    runtimeSessionId="customer-run-" + "x" * 20,   # 33자 이상 필수
    payload=json.dumps(payload).encode(),
)
print(resp["response"].read().decode())
```

## 업데이트 / 제거

```bash
# agent 코드 수정 후 이미지 재빌드 + runtime 갱신
cdk deploy

# 전체 제거 (S3 는 RETAIN 이므로 남음)
cdk destroy
```

## (옵션) Streamlit UI

boto3 로 직접 호출하는 대신 GUI 로 실행하고 싶다면 `ui/streamlit/` 을
참조하세요. 로컬에서 `streamlit run app.py` 로 띄우는 간단한 앱입니다
(CDK 로 배포되지 않음 — 사용자 PC 에서만 실행).

UI 는 `infra/.env` 를 **자동으로 공유** 하므로 VPC/DB/호스트 등을 다시 적지
않아도 됩니다. `ui/.env` 에는 `cdk deploy` 결과로 나온 **4개 Agent ARN** 만
붙여넣으면 됩니다.

```bash
cd ui/streamlit
pip install -r requirements.txt
cp .env.example .env        # ARN 4개만 입력
streamlit run app.py
```

자세한 내용은 [ui/streamlit/README.ko.md](./ui/streamlit/README.ko.md).

## 트러블슈팅

| 증상 | 조치 |
| --- | --- |
| Docker 빌드 실패 | Docker Desktop 실행 확인, macOS 는 ARM64 네이티브라 OK |
| `Cannot connect to the Docker daemon` (Finch 사용 시) | `finch vm start` 후 `export CDK_DOCKER=finch` |
| `CREATE_FAILED` (AgentRuntime) | CloudWatch Logs `/aws/lambda/*-AgentRuntimeCrHandler-*` 확인 |
| Agent 가 RDS 에 못 붙음 | `SECURITY_GROUP_IDS` 의 3306 outbound + RDS SG inbound 확인 |
| `cdk bootstrap` 필요 오류 | 해당 account/region 에서 1회 실행 |
