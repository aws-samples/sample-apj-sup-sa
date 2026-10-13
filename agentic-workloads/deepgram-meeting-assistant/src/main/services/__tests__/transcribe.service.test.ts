import { describe, it, expect, vi } from 'vitest';

// electron을 모킹 (logger.service가 app.isPackaged를 읽음).
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/tmp'),
    getVersion: vi.fn().mockReturnValue('1.0.0'),
    isPackaged: false,
  },
}));

import { composeResultId } from '../transcribe.service';

describe('composeResultId', () => {
  it('prefixes the AWS ResultId with the stream session id', () => {
    expect(composeResultId('sess1', 'r1')).toBe('sess1:r1');
  });

  it('produces different composite ids across stream sessions for the same AWS ResultId', () => {
    expect(composeResultId('sess1', 'r1')).not.toBe(composeResultId('sess2', 'r1'));
  });

  it('is stable for the same session id + AWS ResultId', () => {
    expect(composeResultId('sess1', 'r1')).toBe(composeResultId('sess1', 'r1'));
  });

  it('still returns a session-prefixed string when the AWS ResultId is undefined', () => {
    const composed = composeResultId('sess1', undefined);
    expect(composed.startsWith('sess1:')).toBe(true);
    expect(composed.length).toBeGreaterThan('sess1:'.length);
  });
});
