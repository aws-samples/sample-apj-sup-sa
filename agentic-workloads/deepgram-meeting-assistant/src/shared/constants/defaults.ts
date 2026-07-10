import type { TranscribeLanguage, TranscribeStability } from '../types';

export const ENV_CONFIG = {
  TRANSCRIBE_STABILITY: process.env.TRANSCRIBE_STABILITY as TranscribeStability | undefined,
  TRANSCRIBE_LANGUAGE: process.env.TRANSCRIBE_LANGUAGE as TranscribeLanguage | undefined,
  BEDROCK_MODEL_ID: process.env.BEDROCK_MODEL_ID,
} as const;

export function getEffectiveConfig<T>(envValue: T | undefined, settingsValue: T): T {
  return envValue !== undefined ? envValue : settingsValue;
}
