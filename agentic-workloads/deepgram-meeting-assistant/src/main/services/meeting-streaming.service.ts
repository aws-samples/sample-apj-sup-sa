/**
 * Meeting Streaming Service
 * 
 * 회의 스트리밍 초기화 및 서비스 생성을 담당하는 서비스입니다.
 * TranscribeService, BedrockService 인스턴스 생성 책임을 분리합니다.
 * 
 * ORCH-015: Complex startStreaming → 초기화 로직 분리
 */

import type { MeetingType } from '../../shared/types/meeting';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { TranscriptionSegment } from '../../shared/types/transcription';
// 순환 의존성 방지: barrel(index.ts) 대신 직접 모듈에서 import
import { TranscribeService } from './transcribe.service';
import type { TranscribeServiceConfig } from './transcribe.service';
import { BedrockService } from './bedrock.service';
import type { BedrockServiceConfig } from './bedrock.service';
import { SentenceBufferService } from './sentence-buffer.service';
import { sessionManager, type MeetingSessionState } from './session-manager.service';
import { ServiceFactory } from '../factories/service.factory';

/**
 * AWS 자격 증명
 */
export interface AWSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}

/**
 * Transcribe 설정
 */
export interface TranscribeSettings {
  languageCode: TranscribeLanguage;
  translationTargetLanguage?: TranscribeLanguage;  // Target language for translation
  partialResultsStability: 'high' | 'medium' | 'low';
  enablePartialResultsStabilization: boolean;
  showSpeakerLabel: boolean;
  vocabularyName?: string; // AWS Transcribe Custom Vocabulary 이름
}

/**
 * Bedrock 설정
 */
export interface BedrockSettings {
  correctionModelId: string;
  translationModelId: string;
  summaryModelId: string;
  maxTokens: number;
  temperature: number;
  enableCorrection: boolean;
}

/**
 * 스트리밍 시작을 위한 설정
 */
export interface StreamingConfig {
  meetingId: string;
  meetingType: MeetingType;
  credentials: AWSCredentials;
  transcribeSettings: TranscribeSettings;
  bedrockSettings: BedrockSettings;
  languageOverride?: TranscribeLanguage;
}

/**
 * 스트리밍 콜백 함수 타입
 */
export interface StreamingCallbacks {
  onPartialResult: (text: string, speakerLabel: string | null) => void;
  onFinalResult: (segment: TranscriptionSegment) => Promise<void>;
  onError: (error: Error) => void;
}

/**
 * 생성된 서비스 인스턴스들
 */
export interface CreatedServices {
  transcribeService: TranscribeService;
  correctionService: BedrockService | null;
  translationService: BedrockService | null;
  sentenceBuffer: SentenceBufferService;
}

/**
 * 회의 스트리밍 서비스
 */
class MeetingStreamingService {
  /**
   * TranscribeService 설정을 생성합니다.
   */
  createTranscribeConfig(
    credentials: AWSCredentials,
    settings: TranscribeSettings,
    languageOverride?: TranscribeLanguage
  ): TranscribeServiceConfig {
    const languageCode = languageOverride ?? settings.languageCode;
    return {
      region: credentials.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
      },
      languageCode,
      stability: settings.partialResultsStability,
      enableStabilization: settings.enablePartialResultsStabilization,
      showSpeakerLabel: settings.showSpeakerLabel,
      vocabularyName: settings.vocabularyName,
    };
  }

  /**
   * BedrockService 설정을 생성합니다.
   */
  createBedrockConfig(
    credentials: AWSCredentials,
    modelId: string,
    maxTokens: number,
    temperature: number
  ): BedrockServiceConfig {
    return {
      region: credentials.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
      },
      modelId,
      maxTokens,
      temperature,
    };
  }

  /**
   * 스트리밍에 필요한 모든 서비스 인스턴스를 생성합니다.
   */
  createServices(config: StreamingConfig): CreatedServices {
    const transcribeLanguage = config.languageOverride ?? config.transcribeSettings.languageCode;
    const targetLanguage = config.transcribeSettings.translationTargetLanguage ?? 'ko-KR';
    const needsTranslation = transcribeLanguage !== targetLanguage;

    // TranscribeService 생성
    const transcribeConfig = this.createTranscribeConfig(
      config.credentials,
      config.transcribeSettings,
      config.languageOverride
    );
    const transcribeService = ServiceFactory.createTranscribeService(transcribeConfig);

    // SentenceBuffer 생성 또는 기존 것 재사용
    const existingSession = sessionManager.getSession();
    const sentenceBuffer = existingSession?.sentenceBuffer ?? ServiceFactory.createSentenceBufferService(transcribeLanguage);

    // BedrockService 생성 (교정) - always create when correction is enabled or translation is needed
    let correctionService: BedrockService | null = null;
    if (config.bedrockSettings.enableCorrection || needsTranslation) {
      const correctionConfig = this.createBedrockConfig(
        config.credentials,
        config.bedrockSettings.correctionModelId,
        config.bedrockSettings.maxTokens,
        config.bedrockSettings.temperature
      );
      correctionService = ServiceFactory.createBedrockService(correctionConfig);
    }

    // BedrockService 생성 (번역) - only when source !== target
    let translationService: BedrockService | null = null;
    if (needsTranslation) {
      const translationConfig = this.createBedrockConfig(
        config.credentials,
        config.bedrockSettings.translationModelId,
        config.bedrockSettings.maxTokens,
        config.bedrockSettings.temperature
      );
      translationService = ServiceFactory.createBedrockService(translationConfig);
    }

    return {
      transcribeService,
      correctionService,
      translationService,
      sentenceBuffer,
    };
  }

  /**
   * 스트리밍을 시작합니다.
   * 세션을 생성/업데이트하고 TranscribeService 스트리밍을 시작합니다.
   */
  startStreaming(
    config: StreamingConfig,
    callbacks: StreamingCallbacks
  ): MeetingSessionState {
    const services = this.createServices(config);
    const transcribeLanguage = config.languageOverride ?? config.transcribeSettings.languageCode;
    const targetLanguage = config.transcribeSettings.translationTargetLanguage ?? 'ko-KR';
    const existingSession = sessionManager.getSession();

    // 세션 생성 또는 업데이트
    const session = sessionManager.createSession({
      meetingId: config.meetingId,
      meetingType: config.meetingType,
      language: transcribeLanguage,
      targetLanguage,
      transcribeService: services.transcribeService,
      correctionService: services.correctionService,
      translationService: services.translationService,
      sentenceBuffer: services.sentenceBuffer,
    });

    // 기존 세션 상태 복원 (일시 정지 후 재개 시)
    if (existingSession) {
      session.recentSentences = existingSession.recentSentences;
      session.correctedCount = existingSession.correctedCount;
      session.titleGenerated = existingSession.titleGenerated;
      session.prepData = existingSession.prepData;
      session.transcribeTimeOffsetSec = existingSession.transcribeTimeOffsetSec;
      session.lastSegmentEndTimeSec = existingSession.lastSegmentEndTimeSec;
      session.targetLanguage = existingSession.targetLanguage;  // Preserve target language
    }

    // 스트리밍 시작
    services.transcribeService.startStreaming(
      config.meetingId,
      callbacks.onPartialResult,
      callbacks.onFinalResult,
      callbacks.onError
    );

    return session;
  }

  /**
   * 스트리밍을 중지합니다.
   */
  async stopStreaming(): Promise<void> {
    const session = sessionManager.getSession();
    if (session?.transcribeService) {
      await session.transcribeService.stopStreaming();
      sessionManager.setTranscribeService(null);
    }
  }

  /**
   * 요약/번역/제안을 위한 BedrockService를 생성합니다.
   */
  createBedrockService(
    credentials: AWSCredentials,
    modelId: string,
    maxTokens: number,
    temperature: number
  ): BedrockService {
    const config = this.createBedrockConfig(credentials, modelId, maxTokens, temperature);
    return ServiceFactory.createBedrockService(config);
  }
}

// 싱글톤 인스턴스 export
export const meetingStreamingService = new MeetingStreamingService();
