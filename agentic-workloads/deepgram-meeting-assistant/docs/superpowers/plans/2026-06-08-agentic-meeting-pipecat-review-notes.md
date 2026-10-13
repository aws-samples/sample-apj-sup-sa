# Adversarial Review Notes (non-blocking medium/low)

기록만 하고 진행을 막지 않은 항목들 (사용자 지시: medium/low는 기록만).

## Task 2 (DB)
- [medium] saveSegment ON CONFLICT는 production 경로에서 발동하지 않음(AWS one-final-per-ResultId + IsPartial guard, Pipecat seenResultIds dedup). 최후 안전망으로만 존재. id=excluded.id 승격으로 downstream id correlation 일관성은 확보.

## Task 7 (PipecatBridge)
- [low] concurrent overlapping startStreaming 호출 시나리오: 실제 배선(Task 8)은 resume마다 new 인스턴스를 만들고 동시 start가 발생하지 않음. per-connection ownership 가드(ws===this.ws)로 stale socket 이벤트는 이미 차단.

## Task 8 (wiring)
- [medium] MeetingView.tsx:117-123 device-restart effect: stop 진행 중 입력 디바이스를 변경하면 in-flight startCapture가 stop 이후 완료되어 마이크가 재활성화될 수 있음. 단 이 코드는 이번 작업이 작성/수정한 것이 아닌 기존 코드이며, AWS 경로에도 동일하게 존재했음(이번 작업이 도입한 회귀 아님). stop 중 디바이스 변경이라는 드문 동시 조작에서만 발생. 추후 generation-token 기반 취소 가드로 개선 가능.

## Task 9/11 (Python server) — 의도된 설계 범위
- [medium] server/bot.py는 localhost WebSocket에 인증/오리진 체크 없이 연결을 수락하고 AWS(Transcribe/Bedrock) 클라이언트를 환경 자격증명으로 구동한다. 같은 머신의 다른 프로세스가 ws://localhost:8765에 접속해 전사/교정을 구동할 수 있음(ambient AWS 사용/과금 경로). → 단, 계획/스펙이 명시적으로 "로컬 데모 전용, 사용자가 직접 실행, localhost-only 바인딩, .env는 단기 STS 토큰 권장"으로 설계한 의도된 범위. 인증 핸드셰이크(per-launch 토큰/private socket)는 프로덕션 배포 시 별도 기능으로 추가 권장. README의 보안 주의에 명시됨.
