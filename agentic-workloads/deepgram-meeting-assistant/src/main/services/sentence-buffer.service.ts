import type { BufferedSegment, CompletedSentence } from '../../shared/types/transcription';
import type { TranscribeLanguage } from '../../shared/types/settings';
import { DEFAULT_SPEAKER } from '../constants';

const SENTENCE_ENDINGS = [
  // Common
  /[.!?]$/,
  // Korean
  /다\.?$/,
  /요\.?$/,
  /죠\.?$/,
  /니다\.?$/,
  /세요\.?$/,
  /까요?\??$/,
  /네요?\.?$/,
  // Japanese
  /です$/,
  /ます$/,
  /ました$/,
  /ません$/,
  /[。！？]$/,
  // Chinese
  /了$/,
  /的$/,
  /吗[？?]?$/,
  /呢[？?]?$/,
];

interface SentenceBufferConfig {
  minChars: number;
  minWords: number;
  maxChars: number;
  maxWords: number;
  maxDurationSeconds: number;
  silenceGapSeconds: number;
}

const ENGLISH_BUFFER_CONFIG: SentenceBufferConfig = {
  minChars: 40,
  minWords: 8,
  maxChars: 160,
  maxWords: 30,
  maxDurationSeconds: 12,
  silenceGapSeconds: 3.5,
};

// Japanese uses fewer characters per word, so lower thresholds
const JAPANESE_BUFFER_CONFIG: SentenceBufferConfig = {
  minChars: 30,
  minWords: 6,
  maxChars: 120,
  maxWords: 25,
  maxDurationSeconds: 12,
  silenceGapSeconds: 3.5,
};

// Chinese is logographic, even fewer characters needed
const CHINESE_BUFFER_CONFIG: SentenceBufferConfig = {
  minChars: 25,
  minWords: 5,
  maxChars: 100,
  maxWords: 20,
  maxDurationSeconds: 12,
  silenceGapSeconds: 3.5,
};

const DEFAULT_BUFFER_CONFIG: SentenceBufferConfig = {
  minChars: Number.POSITIVE_INFINITY,
  minWords: Number.POSITIVE_INFINITY,
  maxChars: Number.POSITIVE_INFINITY,
  maxWords: Number.POSITIVE_INFINITY,
  maxDurationSeconds: Number.POSITIVE_INFINITY,
  silenceGapSeconds: Number.POSITIVE_INFINITY,
};

export class SentenceBufferService {
  private buffers: Map<string, BufferedSegment[]> = new Map();
  private config: SentenceBufferConfig;

  constructor(language: TranscribeLanguage) {
    switch (language) {
      case 'en-US':
        this.config = ENGLISH_BUFFER_CONFIG;
        break;
      case 'ja-JP':
        this.config = JAPANESE_BUFFER_CONFIG;
        break;
      case 'zh-CN':
        this.config = CHINESE_BUFFER_CONFIG;
        break;
      default:
        this.config = DEFAULT_BUFFER_CONFIG;
    }
  }

  addSegment(segment: BufferedSegment): CompletedSentence[] {
    const speaker = segment.speakerLabel ?? DEFAULT_SPEAKER;
    const completed: CompletedSentence[] = [];

    if (!this.buffers.has(speaker)) {
      this.buffers.set(speaker, []);
    }
    let speakerBuffer = this.buffers.get(speaker)!;

    const lastSegment = speakerBuffer[speakerBuffer.length - 1];
    if (lastSegment) {
      const gapSeconds = segment.startTime - lastSegment.endTime;
      if (this.shouldFlushBySilenceGap(speakerBuffer, gapSeconds)) {
        completed.push(this.flushBuffer(speaker));
        speakerBuffer = this.buffers.get(speaker)!;
      }
    }

    speakerBuffer.push(segment);

    if (this.isSentenceComplete(segment.text)) {
      completed.push(this.flushBuffer(speaker));
      return completed;
    }

    if (this.shouldFlushByMaxThresholds(speakerBuffer)) {
      completed.push(this.flushBuffer(speaker));
    }

    return completed;
  }

  flushAll(): CompletedSentence[] {
    const results: CompletedSentence[] = [];

    for (const [speaker, segments] of this.buffers) {
      if (segments.length > 0) {
        results.push(this.flushBuffer(speaker));
      }
    }

    return results;
  }

  clear(): void {
    this.buffers.clear();
  }

  private isSentenceComplete(text: string): boolean {
    const trimmed = text.trim();
    return SENTENCE_ENDINGS.some((pattern) => pattern.test(trimmed));
  }

  private shouldFlushBySilenceGap(segments: BufferedSegment[], gapSeconds: number): boolean {
    if (!Number.isFinite(this.config.silenceGapSeconds)) {
      return false;
    }
    if (gapSeconds < this.config.silenceGapSeconds) {
      return false;
    }
    return this.meetsMinimumThresholds(segments);
  }

  private shouldFlushByMaxThresholds(segments: BufferedSegment[]): boolean {
    if (!this.meetsMinimumThresholds(segments)) {
      return false;
    }

    const text = segments.map((segment) => segment.text).join(' ').trim();
    const charCount = text.length;
    const wordCount = text ? text.split(/\s+/).filter(Boolean).length : 0;
    const durationSeconds = segments.length > 0
      ? segments[segments.length - 1].endTime - segments[0].startTime
      : 0;

    return (
      charCount >= this.config.maxChars ||
      wordCount >= this.config.maxWords ||
      durationSeconds >= this.config.maxDurationSeconds
    );
  }

  private meetsMinimumThresholds(segments: BufferedSegment[]): boolean {
    const text = segments.map((segment) => segment.text).join(' ').trim();
    const charCount = text.length;
    const wordCount = text ? text.split(/\s+/).filter(Boolean).length : 0;

    return charCount >= this.config.minChars || wordCount >= this.config.minWords;
  }

  private flushBuffer(speaker: string): CompletedSentence {
    const segments = this.buffers.get(speaker)!;

    const result: CompletedSentence = {
      originalText: segments.map((s) => s.text).join(' '),
      startTime: segments[0].startTime,
      endTime: segments[segments.length - 1].endTime,
      speakerLabel: speaker === DEFAULT_SPEAKER ? null : speaker,
      segmentIds: segments.map((segment) => segment.id),
    };

    this.buffers.set(speaker, []);

    return result;
  }
}
