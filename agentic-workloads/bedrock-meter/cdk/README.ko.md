[English](README.md) | [한국어](README.ko.md)

# Bedrock Meter CDK

이 CDK 앱은 Bedrock invocation logging, 실시간·분석 경로의 사용량 집계, 한도 적용, 알림,
SigV4로 보호된 Core API를 포함하는 Bedrock Meter core stack을 배포합니다.

`cdk.json`은 CDK Toolkit이 앱을 실행하는 방법을 지정합니다.

## 주요 명령어

- `npm run build`: TypeScript를 JavaScript로 컴파일합니다.
- `npm run watch`: 변경을 감지해 컴파일합니다.
- `npm run test`: Jest 단위 테스트를 실행합니다.
- `npx cdk deploy BedrockMeterCoreStack`: 기본 AWS 계정/리전에 core stack을 배포합니다.
- `npx cdk diff`: 배포된 stack과 현재 상태를 비교합니다.
- `npx cdk synth`: 합성된 CloudFormation 템플릿을 출력합니다.

## 배포 환경

Node.js 24를 사용합니다. 최초 설치는 프로젝트 루트에서 `./scripts/deploy-all.sh --bootstrap`,
이후 재배포는 `./scripts/deploy-all.sh`로 실행합니다. 스크립트는 콘솔을 먼저 빌드하므로
bootstrap/synth의 산출물 누락 오류를 방지합니다.

AWS 계정은 현재 자격증명의 STS 결과, 리전은 `AWS_REGION` → `AWS_DEFAULT_REGION` →
선택한 AWS profile에서 결정합니다. 리전 미설정 시 중단합니다. `.env.deploy.local`은 관리자
username·알림 이메일·timezone 같은 프로젝트 설정만 저장합니다. 계정·리전을 입력하지 않습니다.
`CDK_DEFAULT_*`는 스크립트가 확인한 대상을 CDK에 전달하는 내부 값입니다.

직접 `npx cdk` 명령을 사용할 때는 먼저 콘솔 의존성을 설치하고 `build:console-artifact`를
실행해야 합니다. 직접 명령은 `.env.deploy.local`을 읽지 않으므로 필요한 프로젝트 옵션은
터미널에서 export하세요. 위의 주요 명령은 이 준비가 끝난 개발·검토용 명령입니다.

새 ConfigTable에서는 `BEDROCK_METER_QUOTA_TIME_ZONE`이 전역 quota calendar의 초기값이 됩니다.
생략하면 `deploy-all.sh`가 배포 환경의 IANA 타임존을 감지해 `.env.deploy.local`에 저장합니다.
CDK seed 변경은 기존 runtime 설정을 덮어쓰지 않습니다.

Bedrock invocation logging은 계정/리전별 singleton입니다. 기존 설정이 충돌하면 명시적으로
overwrite를 활성화하지 않는 한 배포가 실패합니다. Stack 삭제 시에는 기본적으로 설정을 유지합니다.

설치 방법과 전체 문서 목록은 [프로젝트 README](../README.ko.md)를 참고하세요.
