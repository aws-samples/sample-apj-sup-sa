/**
 * Settings Service
 * 
 * 앱 설정 관리를 담당하는 서비스입니다.
 * 암호화/복호화, 설정 저장/로드/검증 로직을 캡슐화합니다.
 * 
 * ORCH-014: main.ts Mixed Responsibilities → 설정 관리 모듈 분리
 */

import { safeStorage } from 'electron';
import Store from 'electron-store';
import { z } from 'zod';
import { createLogger } from './logger.service';
import type { AppSettings, BedrockSettings } from '../../shared/types/settings';
import { BEDROCK_MODEL_OPTIONS, DEFAULT_SETTINGS } from '../../shared/types/settings';

// ============================================================================
// Zod Schema for Settings Validation
// ============================================================================

export const AppSettingsSchema = z.object({
  aws: z.object({
    accessKeyId: z.string(),
    secretAccessKey: z.string(),
    region: z.string(),
  }),
  transcribe: z.object({
    languageCode: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']),
    translationTargetLanguage: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']),
    partialResultsStability: z.enum(['high', 'medium', 'low']),
    enablePartialResultsStabilization: z.boolean(),
    showSpeakerLabel: z.boolean(),
  }),
  bedrock: z.object({
    correctionModelId: z.string(),
    translationModelId: z.string(),
    summaryModelId: z.string(),
    maxTokens: z.number().min(1),
    temperature: z.number().min(0).max(1),
    enableCorrection: z.boolean(),
  }),
});

// ============================================================================
// Types
// ============================================================================

interface StoreSchema {
  settings: AppSettings;
}

export interface AWSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}

export interface SettingsResult {
  success: boolean;
  error?: string;
}

export interface LoadSettingsResult extends SettingsResult {
  settings?: AppSettings;
}

export interface CredentialsResult extends SettingsResult {
  credentials?: AWSCredentials | null;
  isConfigured?: boolean;
}

// ============================================================================
// Settings Service
// ============================================================================

/**
 * 앱 설정 관리 서비스
 */
class SettingsService {
  private store: Store<StoreSchema>;
  private log = createLogger('settings');

  constructor() {
    this.store = new Store<StoreSchema>({
      name: 'meeting-assistant-settings',
      defaults: {
        settings: DEFAULT_SETTINGS,
      },
    });
  }

  // ==================== Encryption ====================

  /**
   * 문자열을 암호화합니다.
   * P0-1: 암호화 불가 시 에러를 던져 평문 저장 방지
   */
  encryptValue(value: string): string {
    if (!value) return '';
    if (safeStorage.isEncryptionAvailable()) {
      return safeStorage.encryptString(value).toString('base64');
    }
    throw new Error('Encryption is not available on this system. Cannot save sensitive credentials safely.');
  }

  /**
   * 암호화된 문자열을 복호화합니다.
   * ORCH-004: 복호화 실패 시 로깅
   */
  decryptValue(encryptedValue: string): string {
    if (!encryptedValue) return '';
    if (safeStorage.isEncryptionAvailable()) {
      try {
        const buffer = Buffer.from(encryptedValue, 'base64');
        return safeStorage.decryptString(buffer);
      } catch (error) {
        this.log.error({ err: error }, 'Failed to decrypt value');
        return encryptedValue;
      }
    }
    return encryptedValue;
  }

  // ==================== Bedrock Settings ====================

  /**
   * Bedrock 모델 ID를 유효한 값으로 해석합니다.
   */
  private resolveModelId(value?: string): BedrockSettings['correctionModelId'] {
    // Legacy model ID migration
    if (value === 'apac.amazon.nova-2-lite-v1:0') {
      return 'global.amazon.nova-2-lite-v1:0';
    }
    const match = BEDROCK_MODEL_OPTIONS.find((model) => model.value === value);
    return match?.value ?? DEFAULT_SETTINGS.bedrock.correctionModelId;
  }

  /**
   * Bedrock 설정을 정규화합니다.
   */
  normalizeBedrockSettings(
    bedrock?: Partial<BedrockSettings> & { modelId?: string }
  ): BedrockSettings {
    const legacyModelId = this.resolveModelId(bedrock?.modelId);

    return {
      correctionModelId: this.resolveModelId(bedrock?.correctionModelId ?? legacyModelId),
      translationModelId: this.resolveModelId(bedrock?.translationModelId ?? legacyModelId),
      summaryModelId: this.resolveModelId(bedrock?.summaryModelId ?? legacyModelId),
      maxTokens: bedrock?.maxTokens ?? DEFAULT_SETTINGS.bedrock.maxTokens,
      temperature: bedrock?.temperature ?? DEFAULT_SETTINGS.bedrock.temperature,
      enableCorrection: bedrock?.enableCorrection ?? DEFAULT_SETTINGS.bedrock.enableCorrection,
    };
  }

  // ==================== Settings Management ====================

  /**
   * 저장된 설정을 복호화하여 반환합니다.
   */
  decryptSettings(encryptedSettings?: StoreSchema['settings']): AppSettings {
    if (!encryptedSettings?.aws) {
      return DEFAULT_SETTINGS;
    }

    return {
      aws: {
        accessKeyId: this.decryptValue(encryptedSettings.aws.accessKeyId ?? ''),
        secretAccessKey: this.decryptValue(encryptedSettings.aws.secretAccessKey ?? ''),
        region: encryptedSettings.aws.region ?? DEFAULT_SETTINGS.aws.region,
      },
      transcribe: encryptedSettings.transcribe ?? DEFAULT_SETTINGS.transcribe,
      bedrock: this.normalizeBedrockSettings(encryptedSettings.bedrock),
    };
  }

  /**
   * 설정을 저장합니다.
   */
  saveSettings(settings: unknown): SettingsResult {
    try {
      const validated = AppSettingsSchema.safeParse(settings);
      if (!validated.success) {
        this.log.warn({ error: validated.error.message }, 'Invalid settings format');
        return { success: false, error: `Invalid settings format: ${validated.error.message}` };
      }

      const data = validated.data;
      const encryptedSettings: AppSettings = {
        aws: {
          accessKeyId: this.encryptValue(data.aws.accessKeyId),
          secretAccessKey: this.encryptValue(data.aws.secretAccessKey),
          region: data.aws.region,
        },
        transcribe: data.transcribe,
        bedrock: data.bedrock as BedrockSettings,
      };
      this.store.set('settings', encryptedSettings);
      this.log.info({ region: data.aws.region }, 'Settings saved');
      return { success: true };
    } catch (error) {
      this.log.error({ err: error }, 'Failed to save settings');
      return { success: false, error: String(error) };
    }
  }

  /**
   * 설정을 로드합니다.
   */
  loadSettings(): LoadSettingsResult {
    try {
      const encryptedSettings = this.store.get('settings');
      const settings = this.decryptSettings(encryptedSettings);
      this.log.debug('Settings loaded');
      return { success: true, settings };
    } catch (error) {
      this.log.error({ err: error }, 'Failed to load settings');
      return { success: false, settings: DEFAULT_SETTINGS };
    }
  }

  /**
   * 설정을 초기화합니다.
   */
  clearSettings(): SettingsResult {
    try {
      this.store.set('settings', DEFAULT_SETTINGS);
      this.log.info('Settings cleared');
      return { success: true };
    } catch (error) {
      this.log.error({ err: error }, 'Failed to clear settings');
      return { success: false, error: String(error) };
    }
  }

  /**
   * AWS 자격 증명을 가져옵니다.
   */
  getAWSCredentials(): CredentialsResult {
    try {
      const encryptedSettings = this.store.get('settings');
      const settings = this.decryptSettings(encryptedSettings);
      const credentials = settings.aws;
      const isConfigured = !!(credentials.accessKeyId && credentials.secretAccessKey);
      return { success: true, credentials, isConfigured };
    } catch (error) {
      this.log.error({ err: error }, 'Failed to get AWS credentials');
      return { success: false, credentials: null, isConfigured: false };
    }
  }

  /**
   * meeting.handlers에서 사용할 자격 증명 getter
   */
  async getCredentials(): Promise<AWSCredentials | null> {
    const encryptedSettings = this.store.get('settings');
    const settings = this.decryptSettings(encryptedSettings);
    if (!settings.aws.accessKeyId || !settings.aws.secretAccessKey) {
      return null;
    }
    return settings.aws;
  }

  /**
   * meeting.handlers에서 사용할 설정 getter
   */
  async getSettings(): Promise<{
    transcribe: AppSettings['transcribe'];
    bedrock: AppSettings['bedrock'];
  }> {
    const encryptedSettings = this.store.get('settings');
    const settings = this.decryptSettings(encryptedSettings);
    return {
      transcribe: settings.transcribe,
      bedrock: settings.bedrock,
    };
  }
}

// 싱글톤 인스턴스 export
export const settingsService = new SettingsService();
