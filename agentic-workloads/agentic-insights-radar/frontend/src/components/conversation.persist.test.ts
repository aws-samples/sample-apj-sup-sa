// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { afterEach, describe, expect, it, vi } from 'vitest';

import { CHAT_STORAGE_KEY, ChatConversation, forgetChat, INTERRUPTED_NOTE } from './conversation';

class MemoryStore {
  items = new Map<string, string>();
  getItem = (k: string) => this.items.get(k) ?? null;
  setItem = (k: string, v: string) => void this.items.set(k, v);
}

afterEach(() => vi.useRealTimers());

describe('a conversation kept in the tab (CTR HU-031)', () => {
  it('comes back after a reload with the same session, and an answer cut off is marked interrupted', () => {
    const store = new MemoryStore();
    const first = new ChatConversation(store);
    const done = first.begin('Which rules changed?');
    done.update({ tool: 'search_graph' });
    done.update({ data: 'Two rules.' });
    done.done();
    const cut = first.begin('And in Japan?');
    cut.update({ tool: 'search_graph' }); // a tool frame applies at once
    const again = new ChatConversation(store);
    expect(again.sessionId).toBe(first.sessionId);
    const turns = again.snapshot().turns;
    expect(turns.map((t) => t.text)).toEqual(['Which rules changed?', 'Two rules.', 'And in Japan?', '']);
    expect(turns[3]).toMatchObject({ error: INTERRUPTED_NOTE, code: 'interrupted' });
    expect(again.snapshot().busy).toBe(false);
    // New turns continue the ids, so React keys stay unique.
    again.begin('Next');
    expect(new Set(again.snapshot().turns.map((t) => t.id)).size).toBe(6);
  });

  it('starts empty from a damaged, foreign or missing entry, and survives a store that refuses writes', () => {
    for (const raw of ['{not json', '"text"', JSON.stringify({ session: 'x', turns: [] }), JSON.stringify({ session: 'chat-1', turns: 'no' })]) {
      const store = new MemoryStore();
      store.items.set(CHAT_STORAGE_KEY, raw);
      expect(new ChatConversation(store).snapshot().turns).toEqual([]);
    }
    const store = new MemoryStore();
    const good = { id: 1, role: 'user', text: 'q', tools: [] };
    store.items.set(CHAT_STORAGE_KEY, JSON.stringify({ session: 'chat-1', turns: [good, { id: 'x' }, null], busy: false }));
    expect(new ChatConversation(store).snapshot().turns).toEqual([good]);
    const full = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); } };
    const c = new ChatConversation(full);
    c.begin('still works');
    expect(c.snapshot().turns).toHaveLength(2);
    expect(new ChatConversation().snapshot().turns).toEqual([]);
  });

  it('keeps a finished answer that ended in an error as it was', () => {
    const store = new MemoryStore();
    const c = new ChatConversation(store);
    const r = c.begin('q');
    r.update({ error: 'timeout', message: 'gone quiet' });
    // Saved while still busy, but the turn already carries its own error.
    expect(new ChatConversation(store).snapshot().turns[1]).toMatchObject({ code: 'timeout' });
  });
});

describe('streamed text is shown once per frame', () => {
  it('collects tokens until the next frame, and flushes them before a tool, the end, a stop or a new conversation', () => {
    vi.useFakeTimers();
    const raf = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((run) => setTimeout(() => run(0), 16) as unknown as number);
    const c = new ChatConversation();
    const r = c.begin('q');
    const renders = vi.fn();
    c.subscribe(renders);
    for (const t of ['a', 'b', 'c']) r.update({ data: t });
    expect(c.snapshot().turns[1]!.text).toBe('');
    expect(raf).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(16);
    expect(c.snapshot().turns[1]!.text).toBe('abc');
    expect(renders).toHaveBeenCalledTimes(1);
    r.update({ data: 'd' });
    r.update({ tool: 'get_node' });
    expect(c.snapshot().turns[1]).toMatchObject({ text: 'abcd', tools: ['get_node'] });
    r.update({ data: 'e' });
    r.done();
    expect(c.snapshot().turns[1]!.text).toBe('abcde');
    vi.advanceTimersByTime(16); // the frame scheduled for "e" finds nothing left to show
    expect(c.snapshot().turns[1]!.text).toBe('abcde');

    const s = c.begin('q2');
    s.update({ data: 'part' });
    c.stop();
    expect(c.snapshot().turns[3]).toMatchObject({ text: 'part', code: 'stopped' });

    const t = c.begin('q3');
    t.update({ data: 'dropped' });
    c.restart();
    vi.advanceTimersByTime(16);
    expect(c.snapshot().turns).toEqual([]);
    raf.mockRestore();
  });

  it('falls back to a timer where there is no animation frame', () => {
    vi.useFakeTimers();
    const saved = globalThis.requestAnimationFrame;
    // @ts-expect-error - a browser without requestAnimationFrame (a test runner, a worker)
    delete globalThis.requestAnimationFrame;
    const c = new ChatConversation();
    const r = c.begin('q');
    r.update({ data: 'x' });
    vi.advanceTimersByTime(16);
    expect(c.snapshot().turns[1]!.text).toBe('x');
    globalThis.requestAnimationFrame = saved;
  });
});

describe('a conversation belongs to who signed in', () => {
  it('is kept for its owner and replaced for anyone else, and is claimed by the first user', () => {
    const store = new MemoryStore();
    const mine = new ChatConversation(store);
    mine.claim('');  // not known yet: nothing changes
    mine.claim('user-a');
    mine.begin('My private question').done();
    const reload = new ChatConversation(store);
    reload.claim('user-a');
    expect(reload.snapshot().turns).toHaveLength(2);
    const other = new ChatConversation(store);
    other.claim('user-b');
    expect(other.snapshot().turns).toEqual([]);
    expect(new ChatConversation(store).snapshot().turns).toEqual([]); // and the stored copy is gone
  });

  it('keeps a question asked on this page before the user was known', () => {
    const c = new ChatConversation(new MemoryStore());
    c.begin('Asked while /api/me was loading').done();
    c.claim('user-a');
    expect(c.snapshot().turns).toHaveLength(2);
  });

  it('is replaced when it has turns but no owner (kept before owners were recorded)', () => {
    const store = new MemoryStore();
    store.items.set(CHAT_STORAGE_KEY, JSON.stringify({ session: 'chat-1', turns: [{ id: 1, role: 'user', text: 'q', tools: [] }] }));
    const c = new ChatConversation(store);
    c.claim('user-a');
    expect(c.snapshot().turns).toEqual([]);
  });

  it('is forgotten on sign-out, also where storage refuses access', () => {
    window.sessionStorage.setItem(CHAT_STORAGE_KEY, '{}');
    forgetChat();
    expect(window.sessionStorage.getItem(CHAT_STORAGE_KEY)).toBeNull();
    const refuse = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    expect(() => forgetChat()).not.toThrow();
    refuse.mockRestore();
  });
});
