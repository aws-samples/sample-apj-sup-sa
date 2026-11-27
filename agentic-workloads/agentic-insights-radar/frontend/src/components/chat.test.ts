// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it, vi } from 'vitest';

import { CONFIG } from '@/test/session';

import { ask, ChatIdleTimeout, newSessionId, parseSse, runtimeUrl } from './chat';

describe('chat transport', () => {
  it('parses SSE frames across chunk boundaries', () => {
    const first = parseSse('data: {"data":"He"}\n\ndata: {"tool":"x"}\n\ndata: {"da');
    expect(first.frames).toEqual([{ data: 'He' }, { tool: 'x' }]);
    const second = parseSse(`${first.rest}ta":"llo"}\n\nevent: ping\ndata:\n\ndata: plain text\n\ndata: "str"\n\ndata: 3\n\n`);
    expect(second.frames).toEqual([{ data: 'llo' }, { data: 'plain text' }, { data: 'str' }]);
  });

  it('builds the runtime URL and session ids', () => {
    expect(runtimeUrl(CONFIG)).toBe(
      'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A111122223333%3Aruntime%2Fchat-1/invocations?qualifier=DEFAULT',
    );
    expect(newSessionId().length).toBeGreaterThan(33);
  });

  it('reports HTTP failures as frames', async () => {
    const frames: unknown[] = [];
    await ask(CONFIG, 't', 'q', 's', (f) => frames.push(f), vi.fn(async () => new Response('', { status: 403 })) as unknown as typeof fetch);
    await ask(CONFIG, 't', 'q', 's', (f) => frames.push(f), vi.fn(async () => new Response('', { status: 500 })) as unknown as typeof fetch);
    // F2-080: a 403 is not an expired session, and a 5xx says to try again.
    expect(frames).toEqual([
      { error: 'forbidden', message: 'The assistant would not accept this session, although the rest of the app does, so signing in again does not help. Try again in a moment; if it keeps happening, tell an administrator.' },
      { error: 'http', message: 'The assistant could not answer (500). Try again in a moment.' },
    ]);
  });

  it('flushes a final frame without a trailing blank line', async () => {
    const frames: unknown[] = [];
    const body = new ReadableStream({ start: (c) => { c.enqueue(new TextEncoder().encode('data: {"data":"end"}')); c.close(); } });
    await ask(CONFIG, 't', 'q', 's', (f) => frames.push(f), vi.fn(async () => new Response(body)) as unknown as typeof fetch);
    expect(frames).toEqual([{ data: 'end' }]);
  });

  it('stops on an aborted signal, before or during the request', async () => {
    const ok = vi.fn(async () => new Response('data: {"data":"x"}\n\n')) as unknown as typeof fetch;
    const before = new AbortController();
    before.abort(new Error('stopped first'));
    await expect(ask(CONFIG, 't', 'q', 's', vi.fn(), ok, before.signal)).rejects.toThrow('stopped first');
    const during = new AbortController();
    const frames: unknown[] = [];
    const body = new ReadableStream({ start: (c) => c.enqueue(new TextEncoder().encode('data: {"data":"part"}\n\n')) });
    const pending = ask(CONFIG, 't', 'q', 's', (f) => { frames.push(f); during.abort(new Error('stopped later')); }, vi.fn(async () => new Response(body)) as unknown as typeof fetch, during.signal, 1000);
    await expect(pending).rejects.toThrow('stopped later');
    expect(frames).toEqual([{ data: 'part' }]);
  });

  it('gives up when nothing arrives within the idle limit', async () => {
    const hanging = vi.fn((_: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    })) as unknown as typeof fetch;
    const err = await ask(CONFIG, 't', 'q', 's', vi.fn(), hanging, undefined, 10).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ChatIdleTimeout);
    expect((err as Error).message).toBe('The chat stopped responding.');
  });
});
