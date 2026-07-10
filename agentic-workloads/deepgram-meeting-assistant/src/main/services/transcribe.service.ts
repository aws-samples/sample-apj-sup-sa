import {
  TranscribeStreamingClient,
  StartStreamTranscriptionCommand,
  LanguageCode,
  PartialResultsStability,
} from '@aws-sdk/client-transcribe-streaming';
import { v4 as uuidv4 } from 'uuid';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { TranscriptionSegment } from '../../shared/types/transcription';
import { AUDIO_SAMPLE_RATE_HZ, AUDIO_BUFFER_POLL_INTERVAL_MS } from '../constants';
import { createLogger } from './logger.service';
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';

const log = createLogger('transcribe');

/**
 * AWS Transcribe의 ResultId는 단일 스트림 내에서만 고유하다. 일시정지/재개나
 * 재연결로 같은 meetingId에 대해 새 스트림이 열리면 동일한 ResultId가 다시 등장할 수
 * 있다. 스트림 세션 nonce를 접두사로 붙여 (meeting_id, result_id) 키가 스트림 간에
 * 충돌하지 않게 하고, upsert가 같은 스트림 내 진짜 중복 final만 병합하도록 한다.
 */
export function composeResultId(streamSessionId: string, awsResultId: string | undefined): string {
  return `${streamSessionId}:${awsResultId ?? uuidv4()}`;
}

export interface TranscribeServiceConfig {
  region: string;
  credentials: { accessKeyId: string; secretAccessKey: string };
  languageCode: TranscribeLanguage;
  stability: 'high' | 'medium' | 'low';
  enableStabilization: boolean;
  showSpeakerLabel: boolean;
  vocabularyName?: string; // AWS Transcribe Custom Vocabulary 이름
}

const RECONNECTABLE_ERROR_PATTERNS = [
  'no new audio was received',
  'timed out',
  'timeout',
  'connection reset',
  'socket hang up',
  'premature close',
];

const isReconnectableError = (error: unknown): boolean => {
  const message = formatTranscribeError(error).toLowerCase();
  return RECONNECTABLE_ERROR_PATTERNS.some((pattern) => message.includes(pattern));
};

const isPrematureCloseError = (error: unknown): boolean => {
  if (!(error instanceof Error)) {
    return false;
  }

  return (
    (error as NodeJS.ErrnoException).code === 'ERR_STREAM_PREMATURE_CLOSE' ||
    error.message.toLowerCase().includes('premature close')
  );
};

export class TranscribeService implements StreamingBackend {
  readonly kind: StreamingBackendKind = 'aws';
  private client: TranscribeStreamingClient | null = null;
  private audioBuffer: Buffer[] = [];
  private isStreaming = false;
  private config: TranscribeServiceConfig;
  private streamingPromise: Promise<void> | null = null;

  constructor(config: TranscribeServiceConfig) {
    this.config = config;
  }

  async startStreaming(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void,
    onError: (error: Error) => void
  ): Promise<void> {
    this.isStreaming = true;
    this.audioBuffer = [];
    this.streamingPromise = this.runStreamingLoop(meetingId, onPartialResult, onFinalResult, onError);
    try {
      await this.streamingPromise;
    } finally {
      this.streamingPromise = null;
    }
  }

  private async runStreamingLoop(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void,
    onError: (error: Error) => void
  ): Promise<void> {
    while (this.isStreaming) {
      try {
        await this.runSingleStream(meetingId, onPartialResult, onFinalResult);
        // Stream ended normally (isStreaming set to false)
        break;
      } catch (error) {
        if (!this.isStreaming) {
          // Intentionally stopped, don't report error
          break;
        }

        if (isReconnectableError(error)) {
          log.info('Transcribe stream disconnected, reconnecting');
          this.destroyClient();
          // Small delay before reconnecting
          await this.delay(500);
          continue;
        }

        // Non-reconnectable error
        onError(new Error(formatTranscribeError(error)));
        break;
      }
    }
  }

  private async runSingleStream(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void
  ): Promise<void> {
    // 스트림마다 새 nonce를 생성한다. runSingleStream은 재연결/재개 루프에서 매번
    // 다시 호출되므로, 이 위치가 곧 "새 스트림 = 새 세션 id"를 보장한다.
    const streamSessionId = uuidv4();

    this.client = new TranscribeStreamingClient({
      region: this.config.region,
      credentials: this.config.credentials,
    });

    await this.waitForFirstChunk();

    const command = new StartStreamTranscriptionCommand({
      LanguageCode: this.config.languageCode as LanguageCode,
      MediaEncoding: 'pcm',
      MediaSampleRateHertz: AUDIO_SAMPLE_RATE_HZ,
      EnablePartialResultsStabilization: this.config.enableStabilization,
      PartialResultsStability: this.config.stability as PartialResultsStability,
      ShowSpeakerLabel: this.config.showSpeakerLabel,
      VocabularyName: this.config.vocabularyName, // Custom Vocabulary
      AudioStream: this.createAudioStream(),
    });

    const response = await this.client.send(command);

    if (!response.TranscriptResultStream) {
      throw new Error('No transcript stream in response');
    }

    try {
      for await (const event of response.TranscriptResultStream) {
        if (!this.isStreaming) break;

        if (event.TranscriptEvent?.Transcript?.Results) {
          for (const result of event.TranscriptEvent.Transcript.Results) {
            if (!result.Alternatives?.[0]) continue;

            const alternative = result.Alternatives[0];
            const text = alternative.Transcript ?? '';
            const speakerLabel = alternative.Items?.[0]?.Speaker ?? null;

            if (result.IsPartial) {
              onPartialResult(text, speakerLabel);
            } else {
              const segment: TranscriptionSegment = {
                id: uuidv4(),
                meetingId,
                resultId: composeResultId(streamSessionId, result.ResultId),
                text,
                startTime: result.StartTime || 0,
                endTime: result.EndTime || 0,
                speakerLabel,
                confidence: alternative.Items?.[0]?.Confidence,
                createdAt: new Date(),
              };

              onFinalResult(segment);
            }
          }
        }
      }
    } catch (error) {
      if (!this.isStreaming && isPrematureCloseError(error)) {
        log.info('Transcribe stream closed during shutdown');
        return;
      }
      throw error;
    }
  }

  addAudioChunk(chunk: Buffer): void {
    if (this.isStreaming) {
      this.audioBuffer.push(chunk);
    }
  }

  async stopStreaming(): Promise<void> {
    this.isStreaming = false;
    this.audioBuffer = [];

    if (this.streamingPromise) {
      try {
        await Promise.race([this.streamingPromise, this.delay(3000)]);
      } catch {
        // Ignore stop-time errors while shutting down.
      }
    }

    this.destroyClient();
  }

  private destroyClient(): void {
    if (!this.client) {
      return;
    }

    try {
      this.client.destroy();
    } catch {
      // Ignore client destroy errors during cleanup.
    } finally {
      this.client = null;
    }
  }

  private async waitForFirstChunk(timeoutMs = 10000): Promise<void> {
    const startTime = Date.now();
    while (this.isStreaming && this.audioBuffer.length === 0) {
      if (Date.now() - startTime > timeoutMs) {
        throw new Error('Timeout waiting for audio stream');
      }
      await this.delay(100);
    }
  }

  private async *createAudioStream(): AsyncGenerator<
    { AudioEvent: { AudioChunk: Uint8Array } },
    void,
    unknown
  > {
    while (this.isStreaming || this.audioBuffer.length > 0) {
      if (this.audioBuffer.length > 0) {
        const chunk = this.audioBuffer.shift()!;
        yield { AudioEvent: { AudioChunk: new Uint8Array(chunk) } };
      } else {
        await this.delay(AUDIO_BUFFER_POLL_INTERVAL_MS);
      }
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

const formatTranscribeError = (error: unknown): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }

  if (error && typeof error === 'object') {
    const maybeMessage = (error as { message?: unknown }).message;
    const maybeName = (error as { name?: unknown }).name;
    const metadata = (error as { $metadata?: { httpStatusCode?: number; requestId?: string } }).$metadata;
    const parts = [
      typeof maybeName === 'string' ? maybeName : null,
      typeof maybeMessage === 'string' ? maybeMessage : null,
      metadata?.httpStatusCode ? `HTTP ${metadata.httpStatusCode}` : null,
      metadata?.requestId ? `Request ${metadata.requestId}` : null,
    ].filter(Boolean);

    if (parts.length > 0) {
      return parts.join(' - ');
    }

    try {
      const serialized = JSON.stringify(error);
      if (serialized && serialized !== '{}') {
        return serialized;
      }
    } catch {
      // Fall through to default string conversion.
    }
  }

  return String(error) || 'Unknown transcription error';
};
