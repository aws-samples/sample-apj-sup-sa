/**
 * Streaming Backend 공통 인터페이스
 *
 * AWS 직접 경로(TranscribeService)와 Pipecat 경로(PipecatBridgeService)를
 * 하나의 인터페이스 뒤에 둬서, 핸들러의 start/pause/resume/stop/cleanup이
 * 백엔드 종류를 몰라도 동작하게 한다.
 */
import type { TranscriptionSegment } from '../../shared/types/transcription';

export type StreamingBackendKind = 'aws' | 'pipecat';

export interface StreamingBackend {
  readonly kind: StreamingBackendKind;
  startStreaming(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void | Promise<void>,
    onError: (error: Error) => void
  ): Promise<void> | void;
  addAudioChunk(chunk: Buffer): void;
  stopStreaming(): Promise<void>;
}
