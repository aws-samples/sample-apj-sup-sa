# Meeting Assistant

회의 녹음, 실시간 음성 인식, AI 요약을 제공하는 데스크톱 애플리케이션입니다.

## 주요 기능

- **실시간 음성 인식**: AWS Transcribe를 통한 스트리밍 음성-텍스트 변환
- **AI 문장 교정**: Amazon Bedrock을 통한 실시간 문장 교정
- **영어 회의 번역**: 영어 회의 시 한국어 번역 지원
- **회의 요약**: AI 기반 핵심 포인트, 액션 아이템, 결정 사항 추출
- **다양한 회의 유형**: Client, Quick, English, Interview
- **로컬 데이터 저장**: SQLite 기반 회의 및 트랜스크립트 저장
- **보안 자격 증명**: AWS 자격 증명 암호화 저장
- **MCP 연동**: Model Context Protocol을 통한 외부 도구 연동
- **미팅 준비**: Opportunity 검색 및 Task 연동 기능
- **구조화된 로깅**: Pino 기반 개발/프로덕션 로그 분리
- **API 속도 제한**: 요약/번역 API 호출 Rate Limiting

## 시스템 요구 사항

- Node.js 20+
- npm
- AWS 계정 (Transcribe, Bedrock 권한 필요)

## 설치 및 실행

```bash
# 의존성 설치
npm install

# 개발 모드 실행
npm start

# 렌더러만 브라우저에서 실행 (UI 개발용)
npm run dev:web
```

## 빌드 및 배포

### 빌드 명령어

```bash
# 현재 플랫폼용 패키징 (.app, .exe 등 생성)
npm run package

# macOS 전용 인스톨러 생성
npm run make:mac

# macOS 로컬 릴리즈 (서명/공증 검증 + checksum 생성)
npm run release:mac
```

### macOS 로컬 배포(권장)

1. `.env`에 Apple 서명/공증 정보를 설정합니다.
2. `npm run release:mac` 실행
3. 산출물(`out/release/...`)을 사내 스토리지 또는 수동 업로드 경로에 업로드

`.env` 예시:

```bash
APPLE_ID=your-apple-id@example.com
APPLE_APP_SPECIFIC_PASSWORD=xxxx-xxxx-xxxx-xxxx
APPLE_TEAM_ID=ABCD123456
APPLE_IDENTITY=Developer ID Application: Your Name (TEAM_ID)
NOTARIZE=true
```

`release:mac` 스크립트가 수행하는 작업:

- `npm run make:mac` 실행
- `.app` 코드 서명 검증 (`codesign`, `spctl`)
- 공증 사용 시 스테이플 검증 (`xcrun stapler validate`)
- `.dmg` Gatekeeper 검증 (`spctl --type open`)
- `.dmg`/`.zip` 복사 + `sha256` 파일 생성

### 코드 서명 (프로덕션 배포)

macOS 배포 시 Developer ID Application 인증서가 반드시 필요합니다.

사전 확인:

```bash
security find-identity -v -p codesigning
```

출력에 `APPLE_IDENTITY`와 동일한 인증서가 보이지 않으면 서명/설치가 실패합니다.

## AWS 설정

앱 실행 후 Settings 페이지에서 AWS 자격 증명을 입력하세요.

### 필요한 IAM 권한

```
transcribe:StartStreamTranscription
bedrock:InvokeModel
bedrock:InvokeModelWithResponseStream
```

### 지원 리전

- US East (N. Virginia) - us-east-1
- US West (Oregon) - us-west-2
- Europe (Ireland, Frankfurt)
- Asia Pacific (Tokyo, Seoul, Singapore, Sydney)

## 회의 유형

| 유형 | 설명 |
|------|------|
| Client Meeting | 고객 미팅, 액션 아이템 추적, MCP 연동 |
| Quick Meeting | 빠른 싱크, 태스크 추적 |
| English Meeting | 실시간 영어-한국어 번역 |
| Amazon Interview | 구조화된 Q&A, 후보자 평가 |

## 지원 언어

- 한국어 (ko-KR)
- English (en-US)

## Bedrock 모델

- Claude Haiku 4.5
- Claude Sonnet 4.5
- Claude Opus 4.5
- Nova 2 Lite

## 지원 플랫폼

- macOS (DMG, ZIP)
- Windows (Squirrel installer)
- Linux (DEB, RPM)

## 라이선스

ISC
