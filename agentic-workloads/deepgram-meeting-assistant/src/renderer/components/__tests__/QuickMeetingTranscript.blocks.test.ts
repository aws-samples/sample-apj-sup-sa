import { describe, it, expect } from 'vitest';
import {
  buildBlocks,
  BLOCK_GAP_SEC,
  computePendingLineId,
  type TranscriptItem,
} from '../QuickMeetingTranscript';

const item = (overrides: Partial<TranscriptItem>): TranscriptItem => ({
  id: overrides.id ?? 'x',
  speakerLabel: overrides.speakerLabel ?? null,
  text: overrides.text ?? 't',
  translatedText: overrides.translatedText,
  startTime: overrides.startTime ?? 0,
  isCorrected: overrides.isCorrected ?? false,
});

describe('buildBlocks', () => {
  it('splits into separate blocks when the time gap exceeds BLOCK_GAP_SEC (no speaker labels)', () => {
    // agentic 모드 재현: speakerLabel이 전부 null이어도 시간 간격으로 블록이 갈려야 한다.
    const items: TranscriptItem[] = [
      item({ id: 'a', startTime: 0 }),
      item({ id: 'b', startTime: BLOCK_GAP_SEC + 1 }), // 큰 간격 → 새 블록
      item({ id: 'c', startTime: BLOCK_GAP_SEC + 2 }), // 직전과 가까움 → 같은 블록
    ];

    const blocks = buildBlocks(items);

    expect(blocks).toHaveLength(2);
    expect(blocks[0].startTime).toBe(0);
    expect(blocks[0].lines.map((l) => l.id)).toEqual(['a']);
    expect(blocks[1].startTime).toBe(BLOCK_GAP_SEC + 1);
    expect(blocks[1].lines.map((l) => l.id)).toEqual(['b', 'c']);
  });

  it('keeps close, same-speaker items in one block (timestamp stays the block start)', () => {
    const items: TranscriptItem[] = [
      item({ id: 'a', startTime: 0 }),
      item({ id: 'b', startTime: 1 }),
      item({ id: 'c', startTime: 2 }),
    ];

    const blocks = buildBlocks(items);

    expect(blocks).toHaveLength(1);
    expect(blocks[0].startTime).toBe(0);
    expect(blocks[0].lines).toHaveLength(3);
  });

  it('still splits on speaker change even within the time gap', () => {
    const items: TranscriptItem[] = [
      item({ id: 'a', speakerLabel: 'spk_0', startTime: 0 }),
      item({ id: 'b', speakerLabel: 'spk_1', startTime: 1 }), // 가깝지만 화자 바뀜 → 새 블록
    ];

    const blocks = buildBlocks(items);

    expect(blocks).toHaveLength(2);
    expect(blocks[0].speakerLabel).toBe('spk_0');
    expect(blocks[1].speakerLabel).toBe('spk_1');
  });

  it('measures the gap from the block start, so a long run re-splits periodically', () => {
    // 블록 시작(0)에서 BLOCK_GAP_SEC 넘어가는 지점부터 새 블록이 시작된다.
    const items: TranscriptItem[] = [
      item({ id: 'a', startTime: 0 }),
      item({ id: 'b', startTime: 3 }),
      item({ id: 'c', startTime: 6 }),
      item({ id: 'd', startTime: BLOCK_GAP_SEC }), // 0 기준 간격 도달 → 새 블록
    ];

    const blocks = buildBlocks(items);

    expect(blocks).toHaveLength(2);
    expect(blocks[0].lines.map((l) => l.id)).toEqual(['a', 'b', 'c']);
    expect(blocks[1].lines.map((l) => l.id)).toEqual(['d']);
  });
});

describe('computePendingLineId', () => {
  const items: TranscriptItem[] = [
    item({ id: 'a', startTime: 0 }),
    item({ id: 'b', startTime: 1 }),
    item({ id: 'c', startTime: 2 }),
  ];

  it('returns the most recent (last) item id when there is no partial text', () => {
    // 말이 끝난 직후: 마지막 라인만 진행중(italic)
    expect(computePendingLineId(items, '')).toBe('c');
  });

  it('returns null while partial text is present (speaking in progress)', () => {
    // 말하는 중: 확정 라인은 모두 non-italic, partial 블록이 진행중 표시
    expect(computePendingLineId(items, '말하는 중...')).toBeNull();
  });

  it('returns null when there are no finalized items', () => {
    expect(computePendingLineId([], '')).toBeNull();
  });
});
