/**
 * Pipecat Bridge Service
 *
 * 로컬 Pipecat 서버(WebSocket)에 연결해 STT+LLM 파이프라인을 구동하는 StreamingBackend.
 * AWS SDK 직접 호출 대신, 오디오를 서버로 보내고 전사/교정 결과를 받아 콜백으로 전달한다.
 */
import WebSocket from 'ws';
import { v4 as uuidv4 } from 'uuid';
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';
import type { TranscriptionSegment } from '../../shared/types/transcription';
import {
  PROTOCOL_VERSION,
  ServerMessageSchema,
  type ServerMessage,
  type AssistantEvent,
} from '../../shared/types/pipecat-protocol';
import { createLogger } from './logger.service';

export type { AssistantEvent };

const log = createLogger('pipecat-bridge');

const READY_TIMEOUT_MS = 10000;
const STOP_DRAIN_TIMEOUT_MS = 3000;

const ORPHAN_CORRECTION_TIMEOUT_MS = 5000;

export interface PipecatBridgeConfig {
  url: string; // ws://localhost:9876
  language: string;
  targetLanguage?: string;
  vocabularyName?: string;
  enableCorrection: boolean;
  // correction을 DB에 반영하는 핸들러(Bridge는 DB를 모름). resultId로 main이 segment row를 찾아 저장.
  onCorrection?: (resultId: string, original: string, corrected: string) => void | Promise<void>;
  // 음성 어시스턴트(wake word → LLM → TTS) 이벤트를 renderer로 전달하는 핸들러.
  onAssistant?: (event: AssistantEvent) => void;
}

export class PipecatBridgeService implements StreamingBackend {
  readonly kind: StreamingBackendKind = 'pipecat';

  private ws: WebSocket | null = null;
  private config: PipecatBridgeConfig;
  private meetingId = '';
  private seq = 0;
  private accepting = false; // 로컬 audio 수용 게이트(pause/stop 즉시 차단용)
  private stopping = false; // stop 진행 중 플래그(idempotency + unexpected-close 구분용)
  private stopPromise: Promise<void> | null = null; // 진행 중 stop을 공유(멱등)
  private stopAcked = false; // 서버 'stopped' ack를 실제로 받았는지(ack 없는 close와 구분)
  private persistenceFailed = false; // 영속 작업(onFinal/onCorrection) 실패 여부
  private seenResultIds = new Set<string>();
  private onPartial: (text: string, speaker: string | null) => void = () => {};
  private onFinal: (s: TranscriptionSegment) => void | Promise<void> = () => {};
  private onError: (e: Error) => void = () => {};
  private stoppedResolve: (() => void) | null = null;

  // in-flight 영속 작업 추적(stop drain barrier용)
  private inflight = new Set<Promise<void>>();
  // resultId별 final 처리 promise(correction 순서 보장용)
  private finalPromiseByResult = new Map<string, Promise<void>>();
  // final보다 먼저 도착한 correction 보류 버퍼
  private pendingCorrections = new Map<string, { original: string; corrected: string; timer: ReturnType<typeof setTimeout> }>();

  constructor(config: PipecatBridgeConfig) {
    this.config = config;
  }

  private track(p: Promise<void>): Promise<void> {
    // 영속 작업 실패는 삼키지 않는다. 로그 + onError로 표면화하고 실패 플래그를 세워
    // stopStreaming이 깨끗한 완료로 보고하지 못하게 한다(조용한 tail 유실 방지).
    const wrapped = Promise.resolve(p).catch((err) => {
      this.persistenceFailed = true;
      log.error({ err: String(err) }, 'in-flight persistence task failed');
      this.onError(new Error(`전사/교정 저장에 실패했습니다: ${String(err)}`));
    });
    this.inflight.add(wrapped);
    void wrapped.finally(() => this.inflight.delete(wrapped));
    return wrapped;
  }

  startStreaming(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void | Promise<void>,
    onError: (error: Error) => void
  ): Promise<void> {
    this.meetingId = meetingId;
    this.onPartial = onPartialResult;
    this.onFinal = onFinalResult;
    this.onError = onError;

    // 인스턴스 재사용(두 번째 start) 시 이전 스트림의 상태가 새 세션을 오염시키지 않도록
    // per-stream 상태를 초기값으로 리셋한다. 새 WebSocket을 만들기 전에 수행.
    this.seq = 0;
    this.accepting = false;
    this.stopping = false;
    this.stopPromise = null;
    this.stopAcked = false;
    this.persistenceFailed = false;
    this.stoppedResolve = null;
    this.seenResultIds.clear();
    this.finalPromiseByResult.clear();
    this.pendingCorrections.forEach((p) => clearTimeout(p.timer));
    this.pendingCorrections.clear();
    this.inflight.clear();

    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(this.config.url);
      this.ws = ws;

      const readyTimer = setTimeout(() => {
        if (!settled) {
          settled = true;
          // ready 전 타임아웃: 이 소켓을 detach+close 해 orphan listener를 남기지 않는다.
          try { ws.removeAllListeners(); ws.close(); } catch { /* noop */ }
          if (this.ws === ws) this.ws = null;
          reject(new Error('Pipecat 서버 ready 응답 시간 초과'));
        }
      }, READY_TIMEOUT_MS);

      ws.on('open', () => {
        // stale/superseded 소켓의 이벤트는 무시. 이 가드를 통과하면 this.ws === ws 이므로
        // 아래 this.send(...)는 올바른(현재) 소켓으로 전송된다(send 리팩터 불필요).
        if (ws !== this.ws) return;
        this.send({
          v: PROTOCOL_VERSION,
          type: 'start',
          meetingId,
          language: this.config.language,
          targetLanguage: this.config.targetLanguage,
          vocabularyName: this.config.vocabularyName,
          enableCorrection: this.config.enableCorrection,
        });
      });

      ws.on('message', (raw: WebSocket.RawData) => {
        if (ws !== this.ws) return; // stale 소켓 이벤트 무시
        const msg = this.parse(raw);
        if (!msg) return;
        // 다른 세션/유령 프레임 차단: meetingId가 현재 세션과 다르면 폐기.
        // error는 meetingId가 없을 수 있으므로(서버 레벨), 있을 때만 일치 검사.
        // ready 해석/handleMessage(stop ack/dedupe/persistence)보다 먼저 early-return.
        if ('meetingId' in msg && msg.meetingId !== undefined && msg.meetingId !== this.meetingId) {
          log.warn({ got: msg.meetingId, expected: this.meetingId, type: msg.type }, 'Dropping message for non-active meeting');
          return;
        }
        if (msg.type === 'ready' && !settled) {
          settled = true;
          clearTimeout(readyTimer);
          this.accepting = true; // ready 이후에만 audio 전송 허용
          resolve();
          return;
        }
        this.handleMessage(msg);
      });

      ws.on('error', (err: Error) => {
        if (ws !== this.ws) return; // stale 소켓 이벤트 무시
        const friendly = new Error(
          `Pipecat 서버에 연결할 수 없습니다. 'cd server && python bot.py'로 서버를 먼저 실행하세요. (${err.message})`
        );
        if (!settled) {
          settled = true;
          clearTimeout(readyTimer);
          // ready 전 error: 이 소켓을 detach+close 해 orphan listener를 남기지 않는다.
          try { ws.removeAllListeners(); ws.close(); } catch { /* noop */ }
          if (this.ws === ws) this.ws = null;
          reject(friendly);
        } else {
          this.onError(friendly);
        }
      });

      ws.on('close', () => {
        if (ws !== this.ws) return; // stale 소켓 이벤트 무시
        if (this.stopping) {
          // stop 진행 중 close: drain 대기 해제(정상/비정상 구분은 stopAcked로 runStop이 판단)
          if (this.stoppedResolve) {
            this.stoppedResolve();
            this.stoppedResolve = null;
          }
          return;
        }
        // 예기치 않은 close(서버 재시작/크래시 등): audio를 조용히 버리지 않도록 fail-fast.
        this.accepting = false;
        if (settled) {
          this.onError(new Error('Pipecat 서버 연결이 끊겼습니다. 서버 상태를 확인하고 회의를 다시 시작하세요.'));
        }
        // settled=false(=ready 전 close)는 위 'error'/timeout 경로가 reject를 처리한다.
      });
    });
  }

  addAudioChunk(chunk: Buffer): void {
    // 로컬 게이트: pause/stop 이후 도착하는 청크는 즉시 무시(drain 윈도우 동안 발화 유입 방지).
    if (!this.accepting) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({
        v: PROTOCOL_VERSION,
        type: 'audio',
        meetingId: this.meetingId,
        seq: this.seq++,
        data: chunk.toString('base64'),
      });
    }
  }

  stopStreaming(): Promise<void> {
    // 멱등: 이미 stop이 진행 중이면 같은 promise를 반환(동시 호출자가 drain 결과를 공유).
    if (this.stopPromise) return this.stopPromise;
    // 가장 먼저 로컬 audio 수용을 차단(AWS TranscribeService가 isStreaming=false로 즉시 막는 것과 동일 취지).
    this.accepting = false;
    this.stopping = true;
    this.stopPromise = this.runStop();
    return this.stopPromise;
  }

  private async runStop(): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.ws = null;
      // 소켓이 이미 닫혀 있으면 서버 drain을 보장할 수 없다 → degraded.
      throw new Error('Pipecat 연결이 이미 닫혀 정상 종료(drain)를 보장할 수 없습니다.');
    }
    this.send({ v: PROTOCOL_VERSION, type: 'stop', meetingId: this.meetingId });

    // 1단계: stopped ack(또는 socket close, 또는 타임아웃) 대기 — 서버가 tail 프레임을 다 보냄
    let timedOut = false;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        timedOut = true;
        this.stoppedResolve = null;
        resolve();
      }, STOP_DRAIN_TIMEOUT_MS);
      // 'stopped' ack 또는 'close' 이벤트가 오면 호출됨 (handleMessage / ws.on('close'))
      this.stoppedResolve = () => {
        clearTimeout(timer);
        resolve();
      };
    });

    // 2단계: 수신한 final/correction의 비동기 영속 작업이 모두 끝날 때까지 대기(tail 유실 방지)
    await Promise.allSettled(Array.from(this.inflight));

    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;

    // 3단계: 종료 품질 판정. ack 없는 close/타임아웃 또는 영속 실패는 degraded로 보고(throw)
    // → 핸들러가 회의를 '깨끗한 완료'로 처리하지 않도록 한다.
    if (!this.stopAcked) {
      throw new Error('Pipecat 서버 종료 ack(stopped)를 받지 못해 일부 전사가 유실됐을 수 있습니다.');
    }
    if (timedOut) {
      throw new Error('Pipecat 종료 drain 시간 초과로 일부 전사가 유실됐을 수 있습니다.');
    }
    if (this.persistenceFailed) {
      throw new Error('일부 전사/교정 저장에 실패했습니다.');
    }
  }

  private handleMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case 'partial':
        this.onPartial(msg.text, msg.speakerLabel ?? null);
        break;
      case 'final': {
        if (this.seenResultIds.has(msg.resultId)) return; // 중복 억제(보조)
        this.seenResultIds.add(msg.resultId);
        const segment: TranscriptionSegment = {
          id: uuidv4(),
          meetingId: msg.meetingId,
          resultId: msg.resultId,
          text: msg.text,
          startTime: msg.startTime,
          endTime: msg.endTime,
          speakerLabel: msg.speakerLabel ?? null,
          confidence: msg.confidence ?? undefined,
          createdAt: new Date(),
        };
        // final 영속 promise를 추적하고 resultId에 매핑(correction 순서 보장용)
        const finalP = this.track(Promise.resolve(this.onFinal(segment)));
        this.finalPromiseByResult.set(msg.resultId, finalP);
        // 이미 보류 중인 correction이 있으면 final 완료 후 처리
        const pending = this.pendingCorrections.get(msg.resultId);
        if (pending) {
          clearTimeout(pending.timer);
          this.pendingCorrections.delete(msg.resultId);
          this.runCorrectionAfterFinal(msg.resultId, pending.original, pending.corrected);
        }
        break;
      }
      case 'correction': {
        const finalP = this.finalPromiseByResult.get(msg.resultId);
        if (finalP) {
          // 매칭 final이 이미 도착 → 그 영속 완료 후 correction 실행(순서 보장)
          this.runCorrectionAfterFinal(msg.resultId, msg.original, msg.corrected);
        } else {
          // final보다 먼저 도착 → 보류 버퍼에 넣고 타임아웃 시 폐기(고아 방지).
          // 같은 resultId의 재시도/중복 correction이 오면 최신 payload로 갱신하되,
          // 반드시 기존 타이머를 먼저 clear한다(오래된 타이머가 새 엔트리를 지우는 race 방지).
          const original = msg.original;
          const corrected = msg.corrected;
          const resultId = msg.resultId;
          const existing = this.pendingCorrections.get(resultId);
          if (existing) clearTimeout(existing.timer);
          const timer = setTimeout(() => {
            this.pendingCorrections.delete(resultId);
            log.warn({ resultId }, 'Orphan correction timed out, dropping');
          }, ORPHAN_CORRECTION_TIMEOUT_MS);
          this.pendingCorrections.set(resultId, { original, corrected, timer });
        }
        break;
      }
      case 'stopped':
        this.stopAcked = true;
        if (this.stoppedResolve) { this.stoppedResolve(); this.stoppedResolve = null; }
        break;
      case 'error':
        this.onError(new Error(msg.message));
        break;
      case 'assistant_start':
        this.config.onAssistant?.({ kind: 'start', query: msg.query });
        break;
      case 'assistant_text':
        this.config.onAssistant?.({ kind: 'text', text: msg.text, done: msg.done });
        break;
      case 'assistant_audio':
        this.config.onAssistant?.({ kind: 'audio', data: msg.data, sampleRate: msg.sampleRate });
        break;
      case 'assistant_end':
        this.config.onAssistant?.({ kind: 'end' });
        break;
    }
  }

  private runCorrectionAfterFinal(resultId: string, original: string, corrected: string): void {
    if (!this.config.onCorrection) return;
    const finalP = this.finalPromiseByResult.get(resultId) ?? Promise.resolve();
    const cb = this.config.onCorrection;
    this.track(finalP.then(() => Promise.resolve(cb(resultId, original, corrected))));
  }

  private parse(raw: WebSocket.RawData): ServerMessage | null {
    try {
      const json = JSON.parse(raw.toString());
      const result = ServerMessageSchema.safeParse(json);
      if (!result.success) {
        log.warn({ err: result.error.message }, 'Invalid server message');
        return null;
      }
      return result.data;
    } catch {
      return null;
    }
  }

  private send(msg: unknown): void {
    this.ws?.send(JSON.stringify(msg));
  }
}
