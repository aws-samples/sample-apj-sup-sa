import type { CorrectedSentence, TranscriptionSegment } from '@shared/types';

export const formatSpeakerLine = (speakerLabel: string | null | undefined, text: string): string => {
  const label = speakerLabel || 'Speaker';
  return `[${label}] ${text}`;
};

export interface MergedTranscriptItem {
  id: string;
  speakerLabel: string | null;
  text: string;
  translatedText?: string | null;
  startTime: number;
  isCorrected: boolean;
}

const sortTranscriptItems = (items: MergedTranscriptItem[]): MergedTranscriptItem[] => {
  return [...items].sort((a, b) => {
    const timeDiff = a.startTime - b.startTime;
    if (timeDiff !== 0) {
      return timeDiff;
    }
    return a.id.localeCompare(b.id);
  });
};

export const buildMergedTranscriptItems = (
  segments: TranscriptionSegment[],
  correctedSentences: CorrectedSentence[]
): MergedTranscriptItem[] => {
  if (correctedSentences.length === 0) {
    return segments.map((segment) => ({
      id: segment.id,
      speakerLabel: segment.speakerLabel,
      text: segment.text,
      startTime: segment.startTime,
      isCorrected: false,
    }));
  }

  const canMerge = correctedSentences.every((sentence) => sentence.segmentIds.length > 0);
  if (!canMerge) {
    return sortTranscriptItems(correctedSentences.map((sentence) => ({
      id: sentence.id,
      speakerLabel: sentence.speakerLabel,
      text: sentence.correctedText,
      translatedText: sentence.translatedText ?? null,
      startTime: sentence.startTime,
      isCorrected: true,
    })));
  }

  const correctedSegmentIds = new Set(
    correctedSentences.flatMap((sentence) => sentence.segmentIds)
  );
  const remainingSegments = segments.filter((segment) => !correctedSegmentIds.has(segment.id));

  const items: MergedTranscriptItem[] = [
    ...correctedSentences.map((sentence) => ({
      id: sentence.id,
      speakerLabel: sentence.speakerLabel,
      text: sentence.correctedText,
      translatedText: sentence.translatedText ?? null,
      startTime: sentence.startTime,
      isCorrected: true,
    })),
    ...remainingSegments.map((segment) => ({
      id: segment.id,
      speakerLabel: segment.speakerLabel,
      text: segment.text,
      startTime: segment.startTime,
      isCorrected: false,
    })),
  ];

  return sortTranscriptItems(items);
};

export const buildConversationLines = (
  segments: TranscriptionSegment[],
  correctedSentences: CorrectedSentence[]
): string[] => {
  return buildMergedTranscriptItems(segments, correctedSentences)
    .map((item) => formatSpeakerLine(item.speakerLabel, item.text));
};

export const buildFullScript = (
  segments: TranscriptionSegment[],
  correctedSentences: CorrectedSentence[]
): string => {
  const lines = buildMergedTranscriptItems(segments, correctedSentences)
    .map((item) => formatSpeakerLine(item.speakerLabel, item.text));
  return lines.join('\n');
};
