export interface AWSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}

export type TranscribeLanguage = 'ko-KR' | 'en-US' | 'ja-JP' | 'zh-CN';

export type TranscribeStability = 'high' | 'medium' | 'low';

export interface TranscribeSettings {
  languageCode: TranscribeLanguage;
  translationTargetLanguage: TranscribeLanguage;
  partialResultsStability: TranscribeStability;
  enablePartialResultsStabilization: boolean;
  showSpeakerLabel: boolean;
}

export const BEDROCK_MODEL_OPTIONS = [
  {
    value: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
    label: 'Claude Haiku 4.5',
  },
  {
    value: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
    label: 'Claude Sonnet 4.5',
  },
  {
    value: 'global.anthropic.claude-opus-4-5-20251101-v1:0',
    label: 'Claude Opus 4.5',
  },
  {
    value: 'global.amazon.nova-2-lite-v1:0',
    label: 'Nova 2 Lite',
  },
] as const;

export type BedrockModelId = (typeof BEDROCK_MODEL_OPTIONS)[number]['value'];

export interface BedrockSettings {
  correctionModelId: BedrockModelId;
  translationModelId: BedrockModelId;
  summaryModelId: BedrockModelId;
  maxTokens: number;
  temperature: number;
  enableCorrection: boolean;
}

export interface AppSettings {
  aws: AWSCredentials;
  transcribe: TranscribeSettings;
  bedrock: BedrockSettings;
}

export const AWS_REGIONS = [
  { value: 'us-east-1', label: 'US East (N. Virginia)' },
  { value: 'us-west-2', label: 'US West (Oregon)' },
  { value: 'eu-west-1', label: 'Europe (Ireland)' },
  { value: 'eu-central-1', label: 'Europe (Frankfurt)' },
  { value: 'ap-northeast-1', label: 'Asia Pacific (Tokyo)' },
  { value: 'ap-northeast-2', label: 'Asia Pacific (Seoul)' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)' },
  { value: 'ap-southeast-2', label: 'Asia Pacific (Sydney)' },
] as const;

export type AWSRegion = (typeof AWS_REGIONS)[number]['value'];

export const SUPPORTED_LANGUAGES = [
  { value: 'ko-KR' as const, label: '한국어', icon: '🇰🇷' },
  { value: 'en-US' as const, label: 'English (US)', icon: '🇺🇸' },
  { value: 'ja-JP' as const, label: '日本語', icon: '🇯🇵' },
  { value: 'zh-CN' as const, label: '中文 (简体)', icon: '🇨🇳' },
] as const;

export const DEFAULT_SETTINGS: AppSettings = {
  aws: {
    accessKeyId: '',
    secretAccessKey: '',
    region: 'us-east-1',
  },
  transcribe: {
    languageCode: 'ko-KR',
    translationTargetLanguage: 'ko-KR',
    partialResultsStability: 'high',
    enablePartialResultsStabilization: true,
    showSpeakerLabel: true,
  },
  bedrock: {
    correctionModelId: BEDROCK_MODEL_OPTIONS[0].value,
    translationModelId: BEDROCK_MODEL_OPTIONS[0].value,
    summaryModelId: BEDROCK_MODEL_OPTIONS[0].value,
    maxTokens: 1000,
    temperature: 0.3,
    enableCorrection: true,
  },
};
