/**
 * MeetingStreamingService Tests
 * 
 * Kent Beck 스타일:
 * - 단위 테스트는 외부 의존성을 격리
 * - 서비스 생성 로직의 정확성 검증
 * - 설정에 따른 조건부 동작 테스트
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { meetingStreamingService } from '../meeting-streaming.service';
import { sessionManager } from '../session-manager.service';

// TranscribeService와 BedrockService mock - 클래스 형태로 정의
vi.mock('../transcribe.service', () => {
  return {
    TranscribeService: class MockTranscribeService {
      config: unknown;
      constructor(config: unknown) {
        this.config = config;
      }
      startStreaming = vi.fn();
      stopStreaming = vi.fn();
      addAudioChunk = vi.fn();
    },
  };
});

vi.mock('../bedrock.service', () => {
  return {
    BedrockService: class MockBedrockService {
      config: { modelId: string };
      constructor(config: { modelId: string }) {
        this.config = config;
      }
      getModelId = vi.fn(function (this: { config: { modelId: string } }) {
        return this.config.modelId;
      });
    },
  };
});

vi.mock('../sentence-buffer.service', () => {
  return {
    SentenceBufferService: class MockSentenceBufferService {
      language: string;
      constructor(language: string) {
        this.language = language;
      }
      addSegment = vi.fn(() => []);
      flushAll = vi.fn(() => []);
    },
  };
});

describe('MeetingStreamingService', () => {
  const mockCredentials = {
    accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
    secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    region: 'us-west-2',
  };

  const mockTranscribeSettings = {
    languageCode: 'ko-KR' as const,
    partialResultsStability: 'medium' as const,
    enablePartialResultsStabilization: true,
    showSpeakerLabel: true,
  };

  const mockBedrockSettings = {
    correctionModelId: 'claude-model',
    translationModelId: 'translation-model',
    summaryModelId: 'summary-model',
    maxTokens: 1024,
    temperature: 0.7,
    enableCorrection: true,
  };

  beforeEach(() => {
    sessionManager.resetSession();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('TranscribeConfig 생성', () => {
    it('자격 증명과 설정을 올바르게 매핑한다', () => {
      // Act
      const config = meetingStreamingService.createTranscribeConfig(
        mockCredentials,
        mockTranscribeSettings
      );

      // Assert
      expect(config.region).toBe('us-west-2');
      expect(config.credentials.accessKeyId).toBe('AKIAIOSFODNN7EXAMPLE');
      expect(config.languageCode).toBe('ko-KR');
      expect(config.stability).toBe('medium');
      expect(config.enableStabilization).toBe(true);
      expect(config.showSpeakerLabel).toBe(true);
    });

    it('languageOverride가 있으면 해당 언어를 사용한다', () => {
      // Act
      const config = meetingStreamingService.createTranscribeConfig(
        mockCredentials,
        mockTranscribeSettings,
        'en-US'
      );

      // Assert
      expect(config.languageCode).toBe('en-US');
    });
  });

  describe('BedrockConfig 생성', () => {
    it('자격 증명과 모델 설정을 올바르게 매핑한다', () => {
      // Act
      const config = meetingStreamingService.createBedrockConfig(
        mockCredentials,
        'test-model-id',
        2048,
        0.5
      );

      // Assert
      expect(config.region).toBe('us-west-2');
      expect(config.credentials.accessKeyId).toBe('AKIAIOSFODNN7EXAMPLE');
      expect(config.modelId).toBe('test-model-id');
      expect(config.maxTokens).toBe(2048);
      expect(config.temperature).toBe(0.5);
    });
  });

  describe('서비스 생성', () => {
    it('한국어 회의에서 correction이 활성화되면 correctionService만 생성한다', () => {
      // Arrange
      const config = {
        meetingId: 'meeting-123',
        meetingType: 'client' as const,
        credentials: mockCredentials,
        transcribeSettings: mockTranscribeSettings,
        bedrockSettings: mockBedrockSettings,
      };

      // Act
      const services = meetingStreamingService.createServices(config);

      // Assert
      expect(services.transcribeService).toBeDefined();
      expect(services.correctionService).toBeDefined();
      expect(services.translationService).toBeNull(); // 한국어라서 번역 불필요
      expect(services.sentenceBuffer).toBeDefined();
    });

    it('영어 회의에서는 correctionService와 translationService 모두 생성한다', () => {
      // Arrange
      const config = {
        meetingId: 'meeting-456',
        meetingType: 'english' as const,
        credentials: mockCredentials,
        transcribeSettings: mockTranscribeSettings,
        bedrockSettings: mockBedrockSettings,
        languageOverride: 'en-US' as const,
      };

      // Act
      const services = meetingStreamingService.createServices(config);

      // Assert
      expect(services.transcribeService).toBeDefined();
      expect(services.correctionService).toBeDefined();
      expect(services.translationService).toBeDefined();
    });

    it('correction이 비활성화되고 한국어면 BedrockService가 생성되지 않는다', () => {
      // Arrange
      const config = {
        meetingId: 'meeting-789',
        meetingType: 'weekly' as const,
        credentials: mockCredentials,
        transcribeSettings: mockTranscribeSettings,
        bedrockSettings: {
          ...mockBedrockSettings,
          enableCorrection: false,
        },
      };

      // Act
      const services = meetingStreamingService.createServices(config);

      // Assert
      expect(services.transcribeService).toBeDefined();
      expect(services.correctionService).toBeNull();
      expect(services.translationService).toBeNull();
    });

    it('영어 회의에서는 correction 비활성화와 관계없이 서비스가 생성된다', () => {
      // Arrange - 영어 회의는 correction 설정과 관계없이 번역/교정 필요
      const config = {
        meetingId: 'meeting-abc',
        meetingType: 'english' as const,
        credentials: mockCredentials,
        transcribeSettings: mockTranscribeSettings,
        bedrockSettings: {
          ...mockBedrockSettings,
          enableCorrection: false, // 비활성화해도
        },
        languageOverride: 'en-US' as const,
      };

      // Act
      const services = meetingStreamingService.createServices(config);

      // Assert - 영어면 서비스 생성됨
      expect(services.correctionService).toBeDefined();
      expect(services.translationService).toBeDefined();
    });
  });

  describe('BedrockService 단독 생성', () => {
    it('요약/번역/제안용 BedrockService를 생성할 수 있다', () => {
      // Act
      const service = meetingStreamingService.createBedrockService(
        mockCredentials,
        'summary-model',
        8000,
        0.3
      );

      // Assert
      expect(service).toBeDefined();
      // BedrockService가 정상적으로 생성되었는지 확인
      expect(service.getModelId()).toBe('summary-model');
      // @ts-ignore - 테스트를 위해 private config 접근
      expect(service.config.maxTokens).toBe(8000);
      // @ts-ignore
      expect(service.config.temperature).toBe(0.3);
    });
  });
});
