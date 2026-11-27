// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { AppConfig } from '@/api/types';

export interface ChatFrame {
  data?: string;
  tool?: string;
  error?: string;
  message?: string;
}

export function newSessionId(): string {
  // AgentCore requires at least 33 characters; two UUIDs keep it unguessable.
  return `chat-${crypto.randomUUID()}-${crypto.randomUUID()}`;
}

export function runtimeUrl(config: AppConfig): string {
  const arn = encodeURIComponent(config.chat_runtime_arn);
  return `https://bedrock-agentcore.${config.region}.amazonaws.com/runtimes/${arn}/invocations?qualifier=DEFAULT`;
}

/** Parse Server-Sent Events text into frames; returns the frames and the unconsumed tail. */
export function parseSse(buffer: string): { frames: ChatFrame[]; rest: string } {
  const frames: ChatFrame[] = [];
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop() as string; // split always yields at least one part
  for (const part of parts) {
    for (const line of part.split(/\r?\n/)) {
      if (!line.startsWith('data:')) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        const value: unknown = JSON.parse(raw);
        if (value && typeof value === 'object') frames.push(value as ChatFrame);
        else if (typeof value === 'string') frames.push({ data: value });
      } catch {
        frames.push({ data: raw });
      }
    }
  }
  return { frames, rest };
}

/**
 * What an HTTP failure from the runtime means to the reader. A 403 is not the same as a 401: the
 * token may be perfectly good and the runtime's authorizer may simply not accept it, and "(429)"
 * told a throttled user nothing about waiting.
 */
export function httpFrame(status: number): ChatFrame {
  if (status === 401) return { error: 'auth', message: 'Your session has expired. Sign in again to keep asking.' };
  if (status === 403) {
    return { error: 'forbidden', message: 'The assistant would not accept this session, although the rest of the app does, so signing in again does not help. Try again in a moment; if it keeps happening, tell an administrator.' };
  }
  if (status === 429) return { error: 'busy', message: 'The assistant is busy. Wait a moment and ask again.' };
  if (status >= 500) return { error: 'http', message: `The assistant could not answer (${status}). Try again in a moment.` };
  return { error: 'http', message: `The assistant refused the question (${status}).` };
}

/** The stream sent nothing for the idle limit; `ask` aborted it. */
export class ChatIdleTimeout extends Error {
  constructor() {
    super('The chat stopped responding.');
    this.name = 'ChatIdleTimeout';
  }
}

/**
 * Ask the chat runtime; calls `onFrame` as frames stream in. With `idleMs`, a request that gets no
 * bytes for that long (before the response or between chunks) is aborted and rejects with
 * ChatIdleTimeout. Aborting `signal` stops the request and rejects with the abort reason.
 */
export async function ask(
  config: AppConfig,
  idToken: string,
  prompt: string,
  sessionId: string,
  onFrame: (f: ChatFrame) => void,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
  idleMs?: number,
): Promise<void> {
  const ctrl = new AbortController();
  let idle = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const arm = () => {
    clearTimeout(timer);
    if (idleMs) timer = setTimeout(() => { idle = true; ctrl.abort(); }, idleMs);
  };
  const outer = signal ?? new AbortController().signal;
  const forward = () => ctrl.abort(outer.reason);
  if (outer.aborted) forward();
  outer.addEventListener('abort', forward);
  try {
    arm();
    const res = await fetcher(runtimeUrl(config), {
      method: 'POST',
      headers: {
        authorization: `Bearer ${idToken}`,
        'content-type': 'application/json',
        accept: 'text/event-stream',
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
      },
      body: JSON.stringify({ prompt }),
      signal: ctrl.signal,
    });
    ctrl.signal.throwIfAborted();
    if (!res.ok || !res.body) {
      onFrame(httpFrame(res.status));
      return;
    }
    const reader = res.body.getReader();
    // Ends a pending read even when the fetcher does not honour the signal.
    ctrl.signal.addEventListener('abort', () => void Promise.allSettled([reader.cancel()]));
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      arm();
      const { value, done } = await reader.read();
      ctrl.signal.throwIfAborted();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = parseSse(buffer);
      buffer = rest;
      frames.forEach(onFrame);
    }
    const { frames } = parseSse(`${buffer}\n\n`);
    frames.forEach(onFrame);
  } catch (err) {
    throw idle ? new ChatIdleTimeout() : err;
  } finally {
    clearTimeout(timer);
    outer.removeEventListener('abort', forward);
  }
}
