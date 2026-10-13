import { describe, it, expect } from 'vitest';
import { ServerMessageSchema, PROTOCOL_VERSION } from '../pipecat-protocol';

describe('pipecat protocol', () => {
  it('parses a valid final message', () => {
    const msg = {
      v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1',
      text: 'hello', startTime: 0, endTime: 1.2,
    };
    const parsed = ServerMessageSchema.parse(msg);
    expect(parsed.type).toBe('final');
  });

  it('rejects a final message without resultId', () => {
    const msg = { v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', text: 'x', startTime: 0, endTime: 1 };
    expect(() => ServerMessageSchema.parse(msg)).toThrow();
  });

  it('parses a stopped ack', () => {
    const parsed = ServerMessageSchema.parse({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' });
    expect(parsed.type).toBe('stopped');
  });
});
