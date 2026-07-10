export const WINDOW_DEFAULT_WIDTH = 1200;
export const WINDOW_DEFAULT_HEIGHT = 800;
export const WINDOW_MIN_WIDTH = 800;
export const WINDOW_MIN_HEIGHT = 600;

// AWS Transcribe requires 16kHz sample rate
export const AUDIO_SAMPLE_RATE_HZ = 16000;
export const AUDIO_BUFFER_POLL_INTERVAL_MS = 10;

// 16-bit signed integer range for PCM conversion (32768 / 32767)
export const INT16_MIN_ABS = 0x8000;
export const INT16_MAX = 0x7fff;

export const ANTHROPIC_API_VERSION = 'bedrock-2023-05-31';
export const SUMMARY_MAX_TOKENS = 4000;
// 트랜스크립트 최대 길이 (문자 수) - 약 200,000 토큰에 해당
export const MAX_TRANSCRIPT_LENGTH = 80000;
// 요약을 생성하기 위한 최소 트랜스크립트 길이
export const MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY = 50;

export const DEFAULT_SPEAKER = 'unknown';
