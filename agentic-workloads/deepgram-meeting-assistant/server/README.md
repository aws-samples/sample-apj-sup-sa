# Pipecat Side-Server (agentic 모드)

Electron meeting-assistant의 **agentic** 모드용 Python 사이드 서버입니다.
Electron 메인 프로세스와 WebSocket으로 연결되어 PCM 오디오를 받고,
**STT**(기본 **Deepgram Nova-3**, `STT_BACKEND=aws`로 AWS Transcribe 전환 가능)
+ 선택적 **AWS Bedrock**(교정)을 실행한 뒤 전사/교정 결과를 JSON으로 돌려줍니다.

```
Electron main ──(WebSocket, JSON 프로토콜 v1)──► bot.py ──► Deepgram / AWS Transcribe + Bedrock
```

WebSocket JSON 프로토콜은 `src/shared/types/pipecat-protocol.ts`
(PROTOCOL_VERSION = 1)와 정확히 일치합니다. `bot.py` 상단 docstring에
메시지 매핑이 정리되어 있습니다.

## 설정 (Setup)

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env 를 열어 STT 백엔드/키(기본 Deepgram → DEEPGRAM_API_KEY), AWS 자격증명 /
# 리전 / Bedrock 모델 ID 를 채웁니다.
```

## 실행 (Run)

```bash
python bot.py
```

기본 바인딩은 `ws://localhost:9876` 입니다 (`.env`의 `PIPECAT_HOST` /
`PIPECAT_PORT`로 변경 가능). Electron 클라이언트는 이 주소로 접속합니다.
Electron 쪽 기본값은 `src/main/ipc/meeting.handlers.ts`의 `PIPECAT_SERVER_URL`이며,
`PIPECAT_SERVER_URL` 환경 변수로 덮어쓸 수 있습니다. 포트를 바꾸면 양쪽을 함께 맞추세요.

> 참고: 8765 포트는 Amazon Quick 등 일부 데스크톱 에이전트가 점유할 수 있어 기본값을
> 9876으로 둡니다(401/연결 거부 회피).

## 환경 변수 (.env)

| 변수 | 설명 |
| --- | --- |
| `STT_BACKEND` | STT 백엔드 선택: `deepgram`(기본) 또는 `aws` |
| `DEEPGRAM_API_KEY` | Deepgram API 키 (`STT_BACKEND=deepgram`일 때 필요) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS 자격증명 (미설정 시 boto3 기본 자격증명 체인 사용) |
| `AWS_SESSION_TOKEN` | (선택) 임시 자격증명용 세션 토큰 |
| `AWS_REGION` | AWS 리전 (기본 `us-east-1`) |
| `BEDROCK_MODEL_ID` | 교정에 쓸 Bedrock 모델 ID |
| `PIPECAT_HOST` / `PIPECAT_PORT` | 서버 바인딩 (기본 `localhost:9876`) |

## 동작 흐름

1. 클라이언트가 `start`를 보내면 STT 서비스와 파이프라인(`stt -> _ResultSink`)을
   초기화하고 `ready`를 회신합니다.
2. `audio` 메시지의 base64 PCM(16kHz mono int16)을 디코드해 파이프라인에
   `InputAudioRawFrame`으로 밀어 넣습니다.
3. STT가 내보내는 중간 결과는 `partial`, 확정 결과는 `final`로 변환합니다.
   각 `final`에는 세션 내에서 안정적이고 유일한 `resultId`
   (`{meetingId}:{index}`)가 붙습니다.
4. 교정이 켜져 있으면 각 `final`을 Bedrock으로 보내 `correction`을
   같은 `resultId`로 회신합니다(실패해도 스트림은 중단되지 않음).
5. `stop`을 받으면 파이프라인을 드레인해 진행 중인 final/교정을 마저
   내보낸 뒤 `stopped`를 회신하고 연결을 종료합니다.

연결당 하나의 미팅만 처리합니다(인증/멀티클라이언트 풀링 없음 — 로컬 데모용).

## 보안 주의사항

- **`.env`를 커밋하지 마세요.** (`.gitignore`에 포함되어 있습니다.)
- 이 서버는 **로컬 데모 전용**입니다. 기본값은 `localhost` 바인딩이며,
  외부 노출/인증이 없으므로 그대로 공개 네트워크에 띄우지 마세요.
- 장기 자격증명 대신 **단기 STS 자격증명**(`AWS_SESSION_TOKEN`) 사용을
  권장합니다.
