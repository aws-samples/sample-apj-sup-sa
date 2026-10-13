# aws-diagram-design — Agent Plugin

[English](README.md) | **한국어**

**말로 설명하면, AWS 다이어그램을 그려줍니다.**

[Agent Plugins 1.0.0](https://agent-plugins.org/) 규격의 플러그인입니다. Kiro, Claude Code 등 `plugin.json` + `skills/`를 읽는 코딩 에이전트에 설치하면, [공식 AWS Architecture Icons](https://aws.amazon.com/ko/architecture/icons/)와 Amazon Ember 타이포그래피, VPC/서브넷/계정 컨테이너 규약이 적용된 에디토리얼 다이어그램을 자체 완결형 HTML로 생성하고 SVG/PNG로 내보냅니다.

> "ALB 뒤에 Fargate 서비스 두고, RDS Multi-AZ랑 ElastiCache 붙는 VPC 3-티어 구성도 그려줘"

![VPC Three-Tier Web Service](docs/samples/vpc-three-tier.svg)

샘플은 [`docs/samples/`](docs/samples/)에 HTML 원본과 함께 있습니다. GitHub은 SVG 미리보기에서 웹폰트를 차단하므로 시스템 폰트로 보입니다.

## 구성

| 컴포넌트 | 종류 | 역할 |
|---|---|---|
| `skills/aws-diagram-design` | 스킬 | 디자인 시스템 본체. 27개 시각 타입(아키텍처, IT 현황, 플로차트, 시퀀스, 상태, ER, 타임라인, 스윔레인, 간트, 데이터 플로, 메달리온 …), AWS 브랜드 스킨, 아이콘 812종, Amazon Ember 폰트, 자체 검증 스크립트. 한/영 다이어그램 요청에 자동 활성화. |
| `skills/import-drawio` | 스킬 | 기존 `.drawio` / `.drawio.png` / `.drawio.svg`를 지정한 크기·상세도·독자로 다시 그림. 구조만 추출하고 원본 레이아웃은 버림. |
| `skills/import-mermaid` | 스킬 | `.mmd` / `.mermaid` / Markdown 내 `mermaid` 블록 대상, 동일 방식. |
| `skills/export-diagram` | 스킬 | 생성된 HTML을 `.svg` / `.png`로 내보내기(Playwright, 투명 배경, 1×/2×/3×). |
| `skills/aws-live-architecture` | 스킬 · **신규** | 실제 계정/리전 또는 VPC 하나를 **읽기 전용**(Describe/List/Get만)으로 인벤토리해 as-is 구성도를 그림. 로컬 자격 증명 또는 AWS MCP Server 경유. |
| `mcp.json` | MCP 서버 · 선택 | [AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html) (streamable HTTP, OAuth). `aws-live-architecture`의 자격 증명 없는 실행 경로, 그리고 서비스명·리전 가용성 확인용. 나머지 네 스킬은 MCP가 없어도 동작. |

디렉터리 구조는 README(영문)의 트리를 참고하세요. 규격상 필수 위치는 루트의 `plugin.json`, `skills/`, `mcp.json` 세 곳이며 `.claude-plugin/`·`.mcp.json`은 Claude Code용 클라이언트 확장입니다.

## 설치

### Kiro (IDE · CLI) — Power로

Kiro Powers는 Agent Plugins 형식을 그대로 쓰므로 이 디렉터리가 곧 Power입니다.

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa && git sparse-checkout set ai-coding-assistants/agent-plugins/aws-diagram-design
```

Kiro IDE: **Powers** 패널 → **Add Custom Power** → **Import power from a folder** → `ai-coding-assistants/agent-plugins/aws-diagram-design` 선택.
Kiro CLI 2.11+: 스킬이 `/aws-diagram-design`, `/import-drawio`, `/import-mermaid`, `/export-diagram`, `/aws-live-architecture` 슬래시로 노출되고, 키워드(`구성도`, `아키텍처 다이어그램`, `drawio` …)로도 활성화됩니다.

MCP 없이 스킬만 쓰려면 `skills/*`를 `~/.kiro/skills/`에 복사하거나 심링크하세요.

### Claude Code — 플러그인으로

```
/plugin marketplace add /path/to/sample-apj-sup-sa/ai-coding-assistants/agent-plugins/aws-diagram-design
/plugin install aws-diagram-design@aws-diagram-design
/reload-plugins
```

스킬은 `/aws-diagram-design:export-diagram` 같은 형태로 노출됩니다. `.mcp.json`이 같은 `aws-mcp` 서버를 등록하며, 첫 사용 시 OAuth 로그인 창이 열립니다.

### 선택 사전 준비

| 기능 | 필요 사항 |
|---|---|
| PNG / SVG 내보내기 | `pip install playwright && playwright install chromium` (없으면 스킬이 안내만 하고 멈춤 — 자동 설치 안 함) |
| 로컬 브라우저에서 Amazon Ember 표시 | `skills/aws-diagram-design/scripts/install_fonts.sh` — 선택. 생성 HTML은 woff2를 자체 임베드 |
| `aws-live-architecture` 로컬 경로 | `boto3` + AWS 자격 증명(`aws login`, SSO, 프로파일). **읽기 전용 프로파일** 권장 |
| `aws-live-architecture` MCP 경로 | 클라이언트가 띄우는 AWS Sign-in에 로그인 (IAM 주체에 `AWSMCPSignInOAuthAccessPolicy` 필요). SigV4를 원하면 AWS MCP Server 가이드대로 `uvx mcp-proxy-for-aws …` stdio 서버로 교체 |

## 사용법

```
이 프로젝트 아키텍처 구성도 그려줘. EKS에 ArgoCD 배포, RDS는 Multi-AZ
Transit Gateway 허브-스포크 네트워크 구성도, 계정 3개짜리로
주문 처리 시퀀스 다이어그램 — API Gateway, Lambda, SQS, DynamoDB
이 온보딩 프로세스를 스윔레인으로 정리해줘
```

명시 호출:

```
/import-drawio legacy-arch.drawio --size=slide-16x9 --detail=simplified --audience=executive
/import-mermaid README.md --diagram=all
/export-diagram my-diagram.html --png-only --scale=3
/aws-live-architecture --region ap-northeast-2 --vpc vpc-0123456789abcdef0 --detail=faithful
```

임포트와 라이브 인벤토리는 네 가지 출력 다이얼 — **format**(`html`/`svg`/`png`/`html+png`), **size**(`doc-inline`, `slide-16x9`, `social-og` …), **detail**(`faithful`/`balanced`/`simplified`), **audience**(`engineer`/`mixed`/`executive`) — 를 공유하고, 병합·축약·생략된 요소(라이브의 경우 권한 부족으로 건너뛴 서비스 포함)를 *fidelity ledger*로 보고합니다.

### 라이브 인벤토리 스킬에 대해

`aws_inventory.py`는 VPC/서브넷(라우트 테이블로 퍼블릭·프라이빗 판정), IGW/NAT, EC2(`Name` 태그 기준 플릿 병합), ALB/NLB와 타깃, ECS/Fargate, EKS, Lambda(+이벤트 소스 매핑), RDS/Aurora, ElastiCache, DynamoDB, S3, SQS, SNS 구독, API Gateway(+Lambda 통합), CloudFront 오리진, Kinesis, OpenSearch를 다룹니다. 화살표는 **설정에서 추론**한 것이며 실제 트래픽이 아님을 범례와 ledger에 명시합니다. boto3만 쓰는 단일 파일이어서 로컬에서도, AWS MCP Server의 샌드박스 Python 도구 안에서도 그대로 실행됩니다.

## AWS MCP Server가 꼭 필요한가?

**그리는 데는 필요 없습니다.** 다섯 스킬 중 넷은 프롬프트 + 로컬 Python + 번들 에셋만으로 동작합니다. 그래도 선언해 둔 이유는 두 가지입니다.

1. `aws-live-architecture`를 로컬 자격 증명 없이 실행하는 경로(스크립트가 서버 샌드박스에서 로그인한 주체 권한으로 돌아감).
2. 그리는 도중 사실 확인 — 서비스 이름, `ap-northeast-2` 출시 여부, 아이콘 명칭 등을 기억이 아니라 AWS 문서에서 확인.

원치 않으면 `mcp.json`(과 `.mcp.json`)만 지우면 됩니다. 기존 `aws-api-mcp-server` / `aws-knowledge-mcp-server`와 함께 켜두지는 마세요 — AWS는 도구 중복을 피하기 위해 관리형 서버로 교체할 것을 권합니다.

## 라이선스

MIT — [LICENSE](LICENSE). [masangbeom/aws-diagram-design](https://github.com/masangbeom/aws-diagram-design)의 파생물이며, 그 원본은 [cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design)입니다. AWS Architecture Icons는 [AWS 아이콘 이용 약관](https://aws.amazon.com/ko/architecture/icons/)(변형·재채색 금지), Amazon Ember는 동봉된 라이선스 가이드라인을 따릅니다. 전체 고지는 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
