import { describe, expect, it } from 'vitest';
import {
  buildConversationLines,
  buildFullScript,
  buildMergedTranscriptItems,
} from '../transcript-format';
import type { CorrectedSentence, TranscriptionSegment } from '@shared/types';

const createSegment = (overrides: Partial<TranscriptionSegment>): TranscriptionSegment => ({
  id: overrides.id ?? 'seg',
  meetingId: overrides.meetingId ?? 'meeting-1',
  resultId: overrides.resultId ?? 'result-1',
  text: overrides.text ?? '',
  startTime: overrides.startTime ?? 0,
  endTime: overrides.endTime ?? 0,
  speakerLabel: overrides.speakerLabel ?? null,
  createdAt: overrides.createdAt ?? new Date(),
});

const createCorrected = (overrides: Partial<CorrectedSentence>): CorrectedSentence => ({
  id: overrides.id ?? 'corr',
  meetingId: overrides.meetingId ?? 'meeting-1',
  originalText: overrides.originalText ?? '',
  correctedText: overrides.correctedText ?? '',
  translatedText: overrides.translatedText ?? null,
  segmentIds: overrides.segmentIds ?? [],
  startTime: overrides.startTime ?? 0,
  endTime: overrides.endTime ?? 0,
  speakerLabel: overrides.speakerLabel ?? null,
  modelId: overrides.modelId ?? 'model-1',
  correctedAt: overrides.correctedAt ?? new Date(),
});

describe('transcript-format', () => {
  it('returns raw segments when no corrected sentences exist', () => {
    const segments = [
      createSegment({ id: 's1', text: 'Hello', startTime: 0, endTime: 1 }),
      createSegment({ id: 's2', text: 'World', startTime: 1.2, endTime: 2 }),
    ];

    const items = buildMergedTranscriptItems(segments, []);
    expect(items).toHaveLength(2);
    expect(items.map((item) => item.id)).toEqual(['s1', 's2']);
    expect(items.every((item) => item.isCorrected === false)).toBe(true);
  });

  it('returns corrected sentences only when merging is not possible', () => {
    const segments = [
      createSegment({ id: 's1', text: 'Original', startTime: 0, endTime: 1 }),
    ];
    const corrected = [
      createCorrected({ id: 'c1', correctedText: 'Later', startTime: 2, endTime: 3, segmentIds: [] }),
      createCorrected({ id: 'c2', correctedText: 'Earlier', startTime: 1, endTime: 2, segmentIds: [] }),
    ];

    const items = buildMergedTranscriptItems(segments, corrected);
    expect(items.map((item) => item.id)).toEqual(['c2', 'c1']);
    expect(items.every((item) => item.isCorrected === true)).toBe(true);
  });

  it('merges corrected sentences with remaining segments when possible', () => {
    const segments = [
      createSegment({ id: 'seg-1', text: 'A', startTime: 0, endTime: 1 }),
      createSegment({ id: 'seg-2', text: 'B', startTime: 2, endTime: 3 }),
      createSegment({ id: 'seg-3', text: 'C', startTime: 4, endTime: 5 }),
    ];
    const corrected = [
      createCorrected({
        id: 'corr-1',
        correctedText: 'B corrected',
        startTime: 2,
        endTime: 3,
        segmentIds: ['seg-2'],
      }),
    ];

    const items = buildMergedTranscriptItems(segments, corrected);
    expect(items.map((item) => item.id)).toEqual(['seg-1', 'corr-1', 'seg-3']);
    expect(items.find((item) => item.id === 'corr-1')?.isCorrected).toBe(true);
  });

  it('formats conversation lines with speaker labels', () => {
    const segments = [
      createSegment({ id: 's1', text: 'Hello', speakerLabel: null, startTime: 0, endTime: 1 }),
      createSegment({ id: 's2', text: 'Hi', speakerLabel: 'Alice', startTime: 1.5, endTime: 2 }),
    ];

    expect(buildConversationLines(segments, [])).toEqual([
      '[Speaker] Hello',
      '[Alice] Hi',
    ]);
    expect(buildFullScript(segments, [])).toBe('[Speaker] Hello\n[Alice] Hi');
  });
});
