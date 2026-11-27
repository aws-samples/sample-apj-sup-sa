// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CONFIG, fakeApi, sessionValue, wrap } from '@/test/session';

import ChatPanel from './ChatPanel';

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

describe('ChatPanel', () => {
  it('streams an answer with tool activity', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ tool: 'search_graph' }, { data: 'Acme **leads**.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    const input = screen.getByPlaceholderText('Ask a question');
    fireEvent.change(input, { target: { value: 'Who leads?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('leads')).toBeInTheDocument());
    expect(screen.getByText('Searching the knowledge graph')).toBeInTheDocument();
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/runtimes/arn%3Aaws%3Abedrock-agentcore');
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer id-token');
  });
});

describe('ChatPanel with the global fetch', () => {
  it('uses window.fetch when no fetcher is given', async () => {
    const { api } = fakeApi({});
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => sse({ data: 'Hello there.' }));
    render(wrap(<ChatPanel />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('Hello there.')).toBeInTheDocument());
    spy.mockRestore();
  });
});

describe('ChatPanel scrolling', () => {
  it('survives scrollIntoView returning a promise (newer browsers)', async () => {
    const { api } = fakeApi({});
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as typeof original;
    const fetcher = vi.fn(async () => sse({ data: 'ok' }));
    const view = render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('ok')).toBeInTheDocument());
    view.unmount();
    Element.prototype.scrollIntoView = original;
  });
});

describe('ChatPanel edge cases', () => {
  it('sends nothing for a blank prompt or without a token', async () => {
    const fetcher = vi.fn(async () => sse({ data: 'never' }));
    const { api } = fakeApi({});
    const { unmount } = render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    unmount();
    const { TestSessionProvider } = await import('@/auth/session');
    const value = sessionValue({ api, me: null, idToken: () => undefined });
    render(<TestSessionProvider value={value}><ChatPanel fetcher={fetcher as unknown as typeof fetch} /></TestSessionProvider>);
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByText(/Ask about signals/)).toBeInTheDocument();
    // Without a token the session has expired: the user is asked to sign in, and the question is kept.
    expect(value.expire).toHaveBeenCalled();
    expect(screen.getByPlaceholderText('Ask a question')).toHaveValue('Hi');
  });

  it('is paused with the AI kill switch, and says so before anyone types', () => {
    const { api } = fakeApi({});
    render(wrap(<ChatPanel />, { api, config: { ...CONFIG, genai_enabled: false } }));
    expect(screen.getByText(/The assistant is paused by an administrator/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('The assistant is paused')).toBeDisabled();
  });

  it('offers sign-in when the chat says the session expired, and copies answers', async () => {
    const { api } = fakeApi({});
    const value = sessionValue({ api });
    const { TestSessionProvider } = await import('@/auth/session');
    const expired = vi.fn(async () => new Response('', { status: 401 }));
    render(<TestSessionProvider value={value}><ChatPanel fetcher={expired as unknown as typeof fetch} /></TestSessionProvider>);
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in again' }));
    expect(value.expire).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Copy the answer' })).toBeNull(); // nothing to copy
  });

  it('lets the user copy a finished answer and announces it once', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'Acme leads.' }));
    const { container } = render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Who?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('button', { name: 'Copy the answer' })).toBeInTheDocument();
    // F2-077: the announcement does not read the answer's Markdown out loud.
    expect(container.querySelector('[aria-live]')).toHaveTextContent('Answer ready.');
    expect(container.querySelector('[aria-live]')).not.toHaveTextContent('Acme leads.');
    expect(container.querySelector('.air-chat-log')).not.toHaveAttribute('aria-live');
  });

  it('shows unknown tools by name and an error code without a message', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ tool: 'future_tool' }, { error: 'throttled' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('throttled')).toBeInTheDocument();
    expect(screen.getByText('future_tool')).toBeInTheDocument();
  });
});

/** A response whose stream stays open and silent until the request is aborted. */
function stalled(): { fetcher: typeof fetch; signals: AbortSignal[] } {
  const signals: AbortSignal[] = [];
  const fetcher = vi.fn(async (_: string, init?: RequestInit) => {
    signals.push(init!.signal!);
    return new Response(new ReadableStream({ start() {} }), { status: 200 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, signals };
}

describe('ChatPanel stalled streams', () => {
  const ask = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  };

  it('gives up on a silent stream and unlocks the panel', async () => {
    const { api } = fakeApi({});
    const { fetcher, signals } = stalled();
    render(wrap(<ChatPanel fetcher={fetcher} idleMs={30} />, { api }));
    ask('Hi');
    expect(await screen.findByText(/The answer stopped arriving/)).toBeInTheDocument();
    expect(signals[0]!.aborted).toBe(true);
    expect(screen.getByPlaceholderText('Ask a question')).not.toBeDisabled();
  });

  it('starts a new conversation while an answer is still running', async () => {
    const { api } = fakeApi({});
    const { fetcher, signals } = stalled();
    render(wrap(<ChatPanel fetcher={fetcher} />, { api }));
    ask('First question');
    expect(await screen.findByText('Thinking\u2026')).toBeInTheDocument();
    const restart = screen.getByRole('button', { name: 'New conversation' });
    expect(restart).not.toBeDisabled();
    fireEvent.click(restart);
    expect(screen.getByText(/still arriving is stopped/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start new' }));
    expect(signals[0]!.aborted).toBe(true);
    expect(screen.queryByText('First question')).toBeNull();
    expect(screen.getByText(/Ask about signals/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 10)); // the aborted request settles without touching the new conversation
    expect(screen.queryByText(/could not be reached/)).toBeNull();
    expect(screen.getByPlaceholderText('Ask a question')).not.toBeDisabled();
    ask('Second question');
    expect(await screen.findByText('Second question')).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('aborts the running request on unmount', async () => {
    const { api } = fakeApi({});
    const { fetcher, signals } = stalled();
    const { unmount } = render(wrap(<ChatPanel fetcher={fetcher} />, { api }));
    ask('Hi');
    await waitFor(() => expect(signals).toHaveLength(1));
    unmount();
    expect(signals[0]!.aborted).toBe(true);
  });

  it('starts a new conversation after a finished answer', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ data: 'Done.' }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeDisabled();
    ask('Hi');
    expect(await screen.findByText('Done.')).toBeInTheDocument();
    // A mis-click does not wipe the thread: it asks first.
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep this one' }));
    expect(screen.getByText('Done.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    createWrapper(document.body).findModal()!.findDismissButton().click();
    expect(screen.getByText('Done.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(screen.getByText(/cannot be brought back/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start new' }));
    expect(screen.queryByText('Done.')).toBeNull();
  });

  it('stops a running answer and keeps the conversation', async () => {
    const { api } = fakeApi({});
    const { fetcher, signals } = stalled();
    render(wrap(<ChatPanel fetcher={fetcher} />, { api }));
    ask('Long question');
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    expect(signals[0]!.aborted).toBe(true);
    expect(screen.getByText('Long question')).toBeInTheDocument();
    // F2-081: the note says the runtime may have finished it anyway, and remembers it.
    expect(screen.getByText(/^Stopped\. The assistant may still have finished this answer/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    expect(screen.getByPlaceholderText('Ask a question')).not.toBeDisabled();
  });

  it('ignores a stop with nothing running', async () => {
    const { ChatConversation } = await import('./conversation');
    const c = new ChatConversation();
    c.stop();
    expect(c.snapshot().turns).toEqual([]);
  });
});

describe('ChatPanel intro', () => {
  it('names the lists this deployment shows', async () => {
    const { applyProfile } = await import('@/lib/profile');
    applyProfile({
      app_name: 'Trade Radar', tagline: '', about_md: '', version: 1,
      lists: {
        signals: { label: 'Regulatory changes', item: 'Regulatory change', help: '', visible: true, order: 1, set: ['label', 'item'] },
        gaps: { label: 'Gaps', item: 'Gap', help: '', visible: false, order: 3, set: ['visible'] },
      },
    });
    const { api } = fakeApi({});
    render(wrap(<ChatPanel fetcher={vi.fn() as unknown as typeof fetch} />, { api }));
    expect(screen.getByText('Ask about regulatory changes, trends, radars and the knowledge graph. Answers cite the entries they use.')).toBeInTheDocument();
    applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} });
  });
});
