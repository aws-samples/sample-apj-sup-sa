import { describe, expect, it } from 'vitest';
import { SentenceBufferService } from '../sentence-buffer.service';
import type { BufferedSegment } from '@shared/types';

const createSegment = (overrides: Partial<BufferedSegment>): BufferedSegment => ({
  id: overrides.id ?? 'segment',
  text: overrides.text ?? '',
  startTime: overrides.startTime ?? 0,
  endTime: overrides.endTime ?? 0,
  speakerLabel: overrides.speakerLabel ?? null,
});

describe('SentenceBufferService', () => {
  it('flushes immediately when a sentence ends', () => {
    const service = new SentenceBufferService('en-US');
    const completed = service.addSegment(
      createSegment({
        id: 's1',
        text: 'Hello world.',
        startTime: 0,
        endTime: 1,
        speakerLabel: null,
      })
    );

    expect(completed).toHaveLength(1);
    expect(completed[0]).toMatchObject({
      originalText: 'Hello world.',
      startTime: 0,
      endTime: 1,
      speakerLabel: null,
      segmentIds: ['s1'],
    });
  });

  it('flushes by silence gap after meeting minimum thresholds', () => {
    const service = new SentenceBufferService('en-US');

    const first = service.addSegment(
      createSegment({
        id: 's1',
        text: 'one two three four five',
        startTime: 0,
        endTime: 1,
        speakerLabel: 'spk_1',
      })
    );
    const second = service.addSegment(
      createSegment({
        id: 's2',
        text: 'six seven eight',
        startTime: 1.2,
        endTime: 2,
        speakerLabel: 'spk_1',
      })
    );
    const third = service.addSegment(
      createSegment({
        id: 's3',
        text: 'nine ten',
        startTime: 6.2,
        endTime: 6.8,
        speakerLabel: 'spk_1',
      })
    );

    expect(first).toHaveLength(0);
    expect(second).toHaveLength(0);
    expect(third).toHaveLength(1);
    expect(third[0]).toMatchObject({
      originalText: 'one two three four five six seven eight',
      startTime: 0,
      endTime: 2,
      speakerLabel: 'spk_1',
      segmentIds: ['s1', 's2'],
    });

    const remaining = service.flushAll();
    expect(remaining).toHaveLength(1);
    expect(remaining[0]).toMatchObject({
      originalText: 'nine ten',
      segmentIds: ['s3'],
    });
  });

  it('flushes when max duration is exceeded after minimum thresholds', () => {
    const service = new SentenceBufferService('en-US');

    const first = service.addSegment(
      createSegment({
        id: 's1',
        text: 'one two three four five six seven eight',
        startTime: 0,
        endTime: 4,
        speakerLabel: 'spk_2',
      })
    );
    const second = service.addSegment(
      createSegment({
        id: 's2',
        text: 'nine',
        startTime: 4.5,
        endTime: 13.2,
        speakerLabel: 'spk_2',
      })
    );

    expect(first).toHaveLength(0);
    expect(second).toHaveLength(1);
    expect(second[0]).toMatchObject({
      originalText: 'one two three four five six seven eight nine',
      startTime: 0,
      endTime: 13.2,
      speakerLabel: 'spk_2',
      segmentIds: ['s1', 's2'],
    });
  });
});
