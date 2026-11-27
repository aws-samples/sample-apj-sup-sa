// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 red-team findings about the assistant panel. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { fakeApi, wrap } from '@/test/session';

import ChatPanel, { CHAT_COUNT_FROM, CHAT_MAX_CHARS } from './ChatPanel';

function sse(...frames: object[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream({
    start(c) {
      for (const f of frames) c.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

const ask = (text: string) => {
  fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
};

describe('F2-016: the runtime\'s own unauthorized frame offers Sign in again', () => {
  it('shows the button for "unauthorized" as it does for "auth"', async () => {
    const { api } = fakeApi({});
    const expire = vi.fn();
    const fetcher = vi.fn(async () => sse({ error: 'unauthorized', message: 'Sign in again.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api, expire }));
    ask('Who leads?');
    expect(await screen.findByText('Sign in again.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }));
    expect(expire).toHaveBeenCalled();
  });

  it('does not offer it for an ordinary failure', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ error: 'http', message: 'Chat is unavailable (500).' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    expect(await screen.findByText('Chat is unavailable (500).')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in again' })).toBeNull();
  });
});

describe('F2-017: a question is not lost', () => {
  it('checks the length before sending, and says how long it is', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'ok' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    const long = 'x'.repeat(CHAT_MAX_CHARS + 1);
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: long } });
    expect(screen.getByText(`${CHAT_MAX_CHARS + 1} characters: shorten the question to ${CHAT_MAX_CHARS} before sending it.`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText('Ask a question')).toHaveValue(long);
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'x'.repeat(CHAT_COUNT_FROM) } });
    expect(screen.getByText(`${CHAT_COUNT_FROM} of ${CHAT_MAX_CHARS} characters.`)).toBeInTheDocument();
  });

  it('puts the question back when the turn fails with no answer', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ error: 'http', message: 'Chat is unavailable (429).' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads the mid-market?');
    expect(await screen.findByText('Chat is unavailable (429).')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question')).toHaveValue('Who leads the mid-market?');
  });

  it('keeps the input clear when an answer arrived before the error', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'Acme leads' }, { error: 'http', message: 'The answer stopped.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    expect(await screen.findByText('The answer stopped.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question')).toHaveValue('');
  });

  it('offers a copy action on the question', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'ok' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    expect(await screen.findByText('ok')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy the question' })).toBeInTheDocument();
  });
});

describe('F2-018: the input keeps the keyboard', () => {
  it('stays enabled while the answer streams, and refuses a second submit', async () => {
    const { api } = fakeApi({});
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    const fetcher = vi.fn(async () => {
      await held;
      return sse({ data: 'Acme leads.' });
    });
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument());
    const box = screen.getByPlaceholderText('Ask a question');
    expect(box).not.toBeDisabled();
    // A follow-up can be typed, and pressing Send while the answer runs sends nothing.
    fireEvent.change(box, { target: { value: 'And who is second?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    release();
    expect(await screen.findByText('Acme leads.')).toBeInTheDocument();
    expect(box).toHaveValue('And who is second?');
  });

  it('brings focus back to the input when an answer ends with focus nowhere', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'ok' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    (document.activeElement as HTMLElement | null)?.blur();
    expect(await screen.findByText('ok')).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByPlaceholderText('Ask a question')));
  });
});

describe('F2-005: a paused chat frame refreshes the config', () => {
  it('re-reads /api/config when the runtime says AI features are paused', async () => {
    const { api } = fakeApi({});
    const refreshConfig = vi.fn();
    const fetcher = vi.fn(async () => sse({ error: 'genai_disabled', message: 'AI features are paused by an administrator.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api, refreshConfig }));
    ask('Who leads?');
    expect(await screen.findByText('AI features are paused by an administrator.')).toBeInTheDocument();
    await waitFor(() => expect(refreshConfig).toHaveBeenCalled());
  });

  it('leaves the config alone for an ordinary answer', async () => {
    const { api } = fakeApi({});
    const refreshConfig = vi.fn();
    const fetcher = vi.fn(async () => sse({ data: 'Acme leads.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api, refreshConfig }));
    ask('Who leads?');
    expect(await screen.findByText('Acme leads.')).toBeInTheDocument();
    expect(refreshConfig).not.toHaveBeenCalled();
  });
});
