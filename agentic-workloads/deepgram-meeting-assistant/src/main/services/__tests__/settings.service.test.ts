/**
 * SettingsService Tests
 * 
 * Kent Beck 스타일:
 * - 경계 조건과 정상 동작 모두 테스트
 * - 에러 케이스도 명시적으로 테스트
 * - 테스트가 실패 원인을 명확히 보여주도록 작성
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { DEFAULT_SETTINGS } from '../../../shared/types';

// safeStorage mock
vi.mock('electron', () => ({
  safeStorage: {
    isEncryptionAvailable: vi.fn(() => true),
    encryptString: vi.fn((value: string) => Buffer.from(`encrypted:${value}`)),
    decryptString: vi.fn((buffer: Buffer) => {
      const str = buffer.toString();
      if (str.startsWith('encrypted:')) {
        return str.replace('encrypted:', '');
      }
      throw new Error('Invalid encrypted data');
    }),
  },
  app: {
    isPackaged: false,
    getVersion: vi.fn().mockReturnValue('1.0.0'),
  },
}));

// electron-store mock - 클래스 형태로 정의
vi.mock('electron-store', () => {
  return {
    default: class MockStore {
      private storage: Record<string, unknown>;
      constructor(options: { defaults: { settings: unknown } }) {
        this.storage = { settings: options.defaults.settings };
      }
      get(key: string) {
        return this.storage[key];
      }
      set(key: string, value: unknown) {
        this.storage[key] = value;
      }
    },
  };
});

// settingsService를 테스트마다 새로 import
describe('SettingsService', () => {
  let settingsService: typeof import('../settings.service').settingsService;
  let AppSettingsSchema: typeof import('../settings.service').AppSettingsSchema;

  beforeEach(async () => {
    vi.resetModules();
    const module = await import('../settings.service');
    settingsService = module.settingsService;
    AppSettingsSchema = module.AppSettingsSchema;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Bedrock 모델 ID 정규화', () => {
    it('유효한 모델 ID는 그대로 반환한다', () => {
      // Arrange
      const bedrock = {
        correctionModelId: 'global.anthropic.claude-haiku-4-5-20251001-v1:0' as const,
        translationModelId: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0' as const,
        summaryModelId: 'global.amazon.nova-2-lite-v1:0' as const,
        maxTokens: 1024,
        temperature: 0.7,
        enableCorrection: true,
      };

      // Act
      const normalized = settingsService.normalizeBedrockSettings(bedrock);

      // Assert
      expect(normalized.correctionModelId).toBe('global.anthropic.claude-haiku-4-5-20251001-v1:0');
      expect(normalized.translationModelId).toBe('global.anthropic.claude-sonnet-4-5-20250929-v1:0');
      expect(normalized.summaryModelId).toBe('global.amazon.nova-2-lite-v1:0');
    });

    it('레거시 APAC 모델 ID는 글로벌로 마이그레이션된다', () => {
      // Arrange - modelId 필드로 레거시 값 전달
      const bedrock = {
        modelId: 'apac.amazon.nova-2-lite-v1:0',
        maxTokens: 1024,
        temperature: 0.7,
        enableCorrection: true,
      };

      // Act
      const normalized = settingsService.normalizeBedrockSettings(bedrock);

      // Assert
      expect(normalized.correctionModelId).toBe('global.amazon.nova-2-lite-v1:0');
      expect(normalized.translationModelId).toBe('global.amazon.nova-2-lite-v1:0');
      expect(normalized.summaryModelId).toBe('global.amazon.nova-2-lite-v1:0');
    });

    it('알 수 없는 모델 ID는 기본값으로 대체된다', () => {
      // Arrange - modelId 필드로 알 수 없는 값 전달
      const bedrock = {
        modelId: 'unknown-model-id',
        maxTokens: 1024,
        temperature: 0.7,
        enableCorrection: true,
      };

      // Act
      const normalized = settingsService.normalizeBedrockSettings(bedrock);

      // Assert
      expect(normalized.correctionModelId).toBe(DEFAULT_SETTINGS.bedrock.correctionModelId);
      expect(normalized.translationModelId).toBe(DEFAULT_SETTINGS.bedrock.correctionModelId);
      expect(normalized.summaryModelId).toBe(DEFAULT_SETTINGS.bedrock.correctionModelId);
    });

    it('레거시 단일 modelId 필드가 있으면 모든 모델에 적용된다', () => {
      // Arrange - 이전 버전에서는 단일 modelId 필드만 있었음
      const bedrock = {
        modelId: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
        maxTokens: 2048,
        temperature: 0.5,
        enableCorrection: true,
      };

      // Act
      const normalized = settingsService.normalizeBedrockSettings(bedrock);

      // Assert
      expect(normalized.correctionModelId).toBe('global.anthropic.claude-haiku-4-5-20251001-v1:0');
      expect(normalized.translationModelId).toBe('global.anthropic.claude-haiku-4-5-20251001-v1:0');
      expect(normalized.summaryModelId).toBe('global.anthropic.claude-haiku-4-5-20251001-v1:0');
    });

    it('undefined 입력은 기본 설정을 반환한다', () => {
      // Act
      const normalized = settingsService.normalizeBedrockSettings(undefined);

      // Assert
      expect(normalized.maxTokens).toBe(DEFAULT_SETTINGS.bedrock.maxTokens);
      expect(normalized.temperature).toBe(DEFAULT_SETTINGS.bedrock.temperature);
      expect(normalized.enableCorrection).toBe(DEFAULT_SETTINGS.bedrock.enableCorrection);
    });
  });

  describe('설정 검증 스키마', () => {
    it('유효한 설정은 검증을 통과한다', () => {
      // Arrange
      const validSettings = {
        aws: {
          accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
          secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
          region: 'us-west-2',
        },
        transcribe: {
          languageCode: 'ko-KR',
          partialResultsStability: 'medium',
          enablePartialResultsStabilization: true,
          showSpeakerLabel: true,
        },
        bedrock: {
          correctionModelId: 'model-1',
          translationModelId: 'model-2',
          summaryModelId: 'model-3',
          maxTokens: 1024,
          temperature: 0.7,
          enableCorrection: true,
        },
      };

      // Act
      const result = AppSettingsSchema.safeParse(validSettings);

      // Assert
      expect(result.success).toBe(true);
    });

    it('잘못된 언어 코드는 검증 실패한다', () => {
      // Arrange
      const invalidSettings = {
        aws: {
          accessKeyId: 'test',
          secretAccessKey: 'test',
          region: 'us-west-2',
        },
        transcribe: {
          languageCode: 'invalid-language', // 잘못된 값
          partialResultsStability: 'medium',
          enablePartialResultsStabilization: true,
          showSpeakerLabel: true,
        },
        bedrock: {
          correctionModelId: 'model',
          translationModelId: 'model',
          summaryModelId: 'model',
          maxTokens: 1024,
          temperature: 0.7,
          enableCorrection: true,
        },
      };

      // Act
      const result = AppSettingsSchema.safeParse(invalidSettings);

      // Assert
      expect(result.success).toBe(false);
    });

    it('temperature 범위 초과는 검증 실패한다', () => {
      // Arrange
      const invalidSettings = {
        aws: {
          accessKeyId: 'test',
          secretAccessKey: 'test',
          region: 'us-west-2',
        },
        transcribe: {
          languageCode: 'ko-KR',
          partialResultsStability: 'medium',
          enablePartialResultsStabilization: true,
          showSpeakerLabel: true,
        },
        bedrock: {
          correctionModelId: 'model',
          translationModelId: 'model',
          summaryModelId: 'model',
          maxTokens: 1024,
          temperature: 1.5, // 0-1 범위 초과
          enableCorrection: true,
        },
      };

      // Act
      const result = AppSettingsSchema.safeParse(invalidSettings);

      // Assert
      expect(result.success).toBe(false);
    });

    it('maxTokens가 0 이하면 검증 실패한다', () => {
      // Arrange
      const invalidSettings = {
        aws: {
          accessKeyId: 'test',
          secretAccessKey: 'test',
          region: 'us-west-2',
        },
        transcribe: {
          languageCode: 'ko-KR',
          partialResultsStability: 'medium',
          enablePartialResultsStabilization: true,
          showSpeakerLabel: true,
        },
        bedrock: {
          correctionModelId: 'model',
          translationModelId: 'model',
          summaryModelId: 'model',
          maxTokens: 0, // 최소 1 이상이어야 함
          temperature: 0.7,
          enableCorrection: true,
        },
      };

      // Act
      const result = AppSettingsSchema.safeParse(invalidSettings);

      // Assert
      expect(result.success).toBe(false);
    });
  });

  describe('암호화/복호화', () => {
    it('빈 문자열은 그대로 반환한다', () => {
      // Act & Assert
      expect(settingsService.encryptValue('')).toBe('');
      expect(settingsService.decryptValue('')).toBe('');
    });

    it('암호화된 값을 복호화하면 원본을 얻는다', () => {
      // Arrange
      const original = 'my-secret-value';

      // Act
      const encrypted = settingsService.encryptValue(original);
      const decrypted = settingsService.decryptValue(encrypted);

      // Assert
      expect(decrypted).toBe(original);
    });
  });

  describe('설정 로드', () => {
    it('설정이 없으면 기본값을 반환한다', () => {
      // Act
      const result = settingsService.loadSettings();

      // Assert
      expect(result.success).toBe(true);
      expect(result.settings).toBeDefined();
    });
  });

  describe('AWS 자격 증명 조회', () => {
    it('자격 증명이 없으면 isConfigured가 false다', () => {
      // Act
      const result = settingsService.getAWSCredentials();

      // Assert
      expect(result.success).toBe(true);
      expect(result.isConfigured).toBe(false);
    });
  });
});
