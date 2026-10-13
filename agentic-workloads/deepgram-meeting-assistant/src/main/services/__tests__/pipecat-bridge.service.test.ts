import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'events';
import { PROTOCOL_VERSION } from '../../../shared/types/pipecat-protocol';

// ws mock: 생성된 인스턴스를 테스트에서 잡을 수 있게 전역에 보관
const sockets: any[] = [];
vi.mock('ws', () => {
  // Use a class EXPRESSION (not a class declaration) so vitest's mock-hoister
  // rewrites the `EventEmitter` superclass reference in place rather than
  // hoisting a TDZ-prone `const EventEmitter = __vi_import_N__...` above the
  // deferred import. This localizes the fix to this factory.
  const FakeWS = class extends EventEmitter {
    static OPEN = 1;
    readyState = 1;
    sent: string[] = [];
    constructor() { super(); sockets.push(this); }
    send(data: string) { this.sent.push(data); }
    close() { this.emit('close'); }
  };
  return { default: FakeWS, WebSocket: FakeWS };
});

import { PipecatBridgeService } from '../pipecat-bridge.service';

function lastSocket() { return sockets[sockets.length - 1]; }

describe('PipecatBridgeService', () => {
  beforeEach(() => { sockets.length = 0; });

  it('has kind "pipecat"', () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    expect(bridge.kind).toBe('pipecat');
  });

  it('sends start message and resolves startStreaming after ready', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onPartial = vi.fn(), onFinal = vi.fn(), onError = vi.fn();
    const p = bridge.startStreaming('m1', onPartial, onFinal, onError);
    const ws = lastSocket();
    ws.emit('open');
    const startMsg = JSON.parse(ws.sent[0]);
    expect(startMsg.type).toBe('start');
    expect(startMsg.meetingId).toBe('m1');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
  });

  it('maps a final message to a TranscriptionSegment via onFinalResult', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onFinal = vi.fn();
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({
      v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1',
      text: 'hello', startTime: 0, endTime: 1,
    }));
    expect(onFinal).toHaveBeenCalledOnce();
    const seg = onFinal.mock.calls[0][0];
    expect(seg.resultId).toBe('r1');
    expect(seg.meetingId).toBe('m1');
    expect(typeof seg.id).toBe('string');
  });

  it('drops duplicate finals with the same resultId', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onFinal = vi.fn();
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const final = { v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 };
    ws.emit('message', JSON.stringify(final));
    ws.emit('message', JSON.stringify(final));
    expect(onFinal).toHaveBeenCalledOnce();
  });

  it('waits for stopped ack before closing on stopStreaming', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const closeSpy = vi.spyOn(ws, 'close');
    const stopP = bridge.stopStreaming();
    expect(JSON.parse(ws.sent[ws.sent.length - 1]).type).toBe('stop');
    expect(closeSpy).not.toHaveBeenCalled();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await stopP;
    expect(closeSpy).toHaveBeenCalled();
  });

  it('runs correction AFTER the matching final persistence resolves (ordering)', async () => {
    const order: string[] = [];
    let resolveFinal: () => void = () => {};
    const onFinal = vi.fn().mockImplementation(() =>
      new Promise<void>((res) => { resolveFinal = () => { order.push('final'); res(); }; })
    );
    const onCorrection = vi.fn().mockImplementation(() => { order.push('correction'); });
    const bridge = new PipecatBridgeService({
      url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true, onCorrection,
    });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'X' }));
    expect(onCorrection).not.toHaveBeenCalled();
    resolveFinal();
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual(['final', 'correction']);
  });

  it('stopStreaming waits for in-flight final/correction persistence before resolving', async () => {
    let resolveFinal: () => void = () => {};
    let finalDone = false;
    const onFinal = vi.fn().mockImplementation(() =>
      new Promise<void>((res) => { resolveFinal = () => { finalDone = true; res(); }; })
    );
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    const stopP = bridge.stopStreaming();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    let stopResolved = false;
    void stopP.then(() => { stopResolved = true; });
    await new Promise((r) => setTimeout(r, 0));
    expect(stopResolved).toBe(false);
    expect(finalDone).toBe(false);
    resolveFinal();
    await stopP;
    expect(finalDone).toBe(true);
  });

  it('drops audio chunks sent after stopStreaming begins (pause/stop gate)', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    bridge.addAudioChunk(Buffer.from([1, 2, 3]));
    const sentBefore = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(sentBefore).toBe(1);
    const stopP = bridge.stopStreaming();
    bridge.addAudioChunk(Buffer.from([4, 5, 6]));
    const sentAfter = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(sentAfter).toBe(1);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await stopP;
  });

  it('keeps the latest correction when duplicates arrive before final (no timer leak)', async () => {
    vi.useFakeTimers();
    const order: string[] = [];
    const onCorrection = vi.fn().mockImplementation((_rid: string, _o: string, corrected: string) => { order.push(corrected); });
    const onFinal = vi.fn().mockResolvedValue(undefined);
    const bridge = new PipecatBridgeService({
      url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true, onCorrection,
    });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'V1' }));
    vi.advanceTimersByTime(1000);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'V2' }));
    vi.advanceTimersByTime(4500);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    await vi.runAllTimersAsync();
    expect(order).toEqual(['V2']);
    vi.useRealTimers();
  });

  it('surfaces onError and stops accepting audio on unexpected close (not during stop)', async () => {
    const onError = vi.fn();
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), onError);
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('close');
    expect(onError).toHaveBeenCalledOnce();
    const audioBefore = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    bridge.addAudioChunk(Buffer.from([1, 2, 3]));
    const audioAfter = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(audioAfter).toBe(audioBefore);
  });

  it('stopStreaming is idempotent: concurrent calls share one drain and send stop once', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const stopP1 = bridge.stopStreaming();
    const stopP2 = bridge.stopStreaming();
    expect(stopP1).toBe(stopP2);
    const stopMsgs = ws.sent.filter((s: string) => JSON.parse(s).type === 'stop').length;
    expect(stopMsgs).toBe(1);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await Promise.all([stopP1, stopP2]);
  });

  it('stopStreaming rejects (degraded) when socket closes before stopped ack', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const stopP = bridge.stopStreaming();
    ws.emit('close');
    await expect(stopP).rejects.toThrow(/stopped|유실|drain/);
  });

  it('stopStreaming rejects (degraded) when a final persistence fails', async () => {
    const onFinal = vi.fn().mockRejectedValue(new Error('db write failed'));
    const onError = vi.fn();
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, onError);
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    const stopP = bridge.stopStreaming();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await expect(stopP).rejects.toThrow(/저장|실패|persist/i);
    expect(onError).toHaveBeenCalled();
  });

  it('ignores server messages for a different meetingId', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onFinal = vi.fn();
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'WRONG', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    expect(onFinal).not.toHaveBeenCalled();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    expect(onFinal).toHaveBeenCalledOnce();
  });

  it('does not resolve startStreaming on ready for a different meetingId', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    let resolved = false;
    void p.then(() => { resolved = true; });
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'other' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(resolved).toBe(false);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    expect(resolved).toBe(true);
  });

  it('supports a second start/stop cycle on the same instance (state reset)', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    // first cycle
    const p1 = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws1 = lastSocket();
    ws1.emit('open');
    ws1.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p1;
    // use resultId 'r1' in cycle 1 to poison dedupe set
    ws1.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    const stop1 = bridge.stopStreaming();
    ws1.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await stop1;
    // second cycle on the SAME instance, new meeting
    const onFinal2 = vi.fn();
    const p2 = bridge.startStreaming('m2', vi.fn(), onFinal2, vi.fn());
    const ws2 = lastSocket();
    expect(ws2).not.toBe(ws1); // new socket
    ws2.emit('open');
    ws2.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm2' }));
    await p2; // must resolve (not poisoned by stopAcked/stopPromise from cycle 1)
    // dedupe reset: a final with the same resultId 'r1' is NOT suppressed
    ws2.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm2', resultId: 'r1', text: 'y', startTime: 0, endTime: 1 }));
    expect(onFinal2).toHaveBeenCalledOnce();
    const stopMsgsOn2Before = ws2.sent.filter((s: string) => JSON.parse(s).type === 'stop').length;
    const stop2 = bridge.stopStreaming();
    const stopMsgsOn2After = ws2.sent.filter((s: string) => JSON.parse(s).type === 'stop').length;
    expect(stopMsgsOn2After).toBe(stopMsgsOn2Before + 1); // a FRESH stop was sent on the new socket
    ws2.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm2' }));
    await stop2;
  });

  it('ignores events from a superseded (stale) socket', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onErr1 = vi.fn();
    const p1 = bridge.startStreaming('m1', vi.fn(), vi.fn(), onErr1);
    const ws1 = lastSocket();
    ws1.emit('open');
    ws1.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p1;
    // new session on same instance
    const onErr2 = vi.fn();
    const p2 = bridge.startStreaming('m2', vi.fn(), vi.fn(), onErr2);
    const ws2 = lastSocket();
    expect(ws2).not.toBe(ws1);
    ws2.emit('open');
    ws2.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm2' }));
    await p2;
    // stale ws1 fires close/stopped — must be ignored for m2
    ws1.emit('close');
    ws1.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    expect(onErr2).not.toHaveBeenCalled();
    // m2 stop still needs ws2's own ack
    const stop2 = bridge.stopStreaming();
    ws2.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm2' }));
    await stop2; // resolves cleanly via ws2
  });
});
