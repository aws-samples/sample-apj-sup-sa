// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about the assistant (F3-032, F3-070, F3-071, F3-082..F3-085, F3-104). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fakeApi, sessionValue, TestRouter, wrap } from '@/test/session';
import { TestSessionProvider } from '@/auth/session';

import AppShell from './AppShell';
import ChatPanel, { absoluteLinks } from './ChatPanel';
import { ChatConversation } from './conversation';

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
  fireEvent.change(screen.getByPlaceholderText(/Ask a question|budget/), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('F3-032: a crashed answer has a way out without reloading the page', () => {
  it('offers the answers as plain text, or a new conversation', async () => {
    const { api } = fakeApi({});
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const conversation = new ChatConversation();
    const request = conversation.begin('Who leads?');
    request.update({ data: '**bold** answer' });
    request.done();
    const markdown = await import('./Markdown');
    vi.spyOn(markdown, 'default').mockImplementation(() => {
      throw new Error('a Markdown edge case');
    });
    render(wrap(<ChatPanel conversation={conversation} />, { api }));
    expect(screen.getByText('The assistant stopped working because of a problem showing its answer.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show the answers as plain text' }));
    expect(screen.getByText('**bold** answer')).toBeInTheDocument(); // as text: the renderer is not used again
    error.mockRestore();
    expect(conversation.snapshot().turns).toHaveLength(2);
  });

  it('starts a new conversation from the fallback', async () => {
    const { api } = fakeApi({});
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const conversation = new ChatConversation();
    const request = conversation.begin('Who leads?');
    request.update({ data: 'answer' });
    request.done();
    const markdown = await import('./Markdown');
    const spy = vi.spyOn(markdown, 'default').mockImplementation(() => {
      throw new Error('a Markdown edge case');
    });
    render(wrap(<ChatPanel conversation={conversation} />, { api }));
    spy.mockRestore();
    fireEvent.click(screen.getByRole('button', { name: 'Start a new conversation' }));
    error.mockRestore();
    expect(conversation.snapshot().turns).toEqual([]);
    expect(await screen.findByPlaceholderText('Ask a question')).toBeInTheDocument();
  });
});

describe('F3-070: opening the assistant focuses the prompt', () => {
  it('puts the keyboard in the prompt', () => {
    const { api } = fakeApi({});
    render(wrap(<ChatPanel />, { api }));
    expect(screen.getByPlaceholderText('Ask a question')).toHaveFocus();
  });
});

describe('F3-082: a 403 from the runtime does not offer the expiry dialog', () => {
  it('says signing in does not help, with no Sign in again button', async () => {
    const { api } = fakeApi({});
    const expire = vi.fn();
    const fetcher = vi.fn(async () => new Response('', { status: 403 }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api, expire }));
    ask('Who leads?');
    expect(await screen.findByText(/signing in again does not help/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in again' })).toBeNull();
  });
});

describe('F3-083: a used-up budget closes the prompt for the day', () => {
  it('disables the prompt with the reason after a budget_exhausted frame', async () => {
    const { api } = fakeApi({});
    const fetcher = vi.fn(async () => sse({ error: 'budget_exhausted', message: "Today's chat budget is used up. Try again tomorrow." }));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('Who leads?');
    expect(await screen.findByText(/The assistant takes questions again after midnight UTC/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('The chat budget is used up for today')).toBeDisabled();
  });
});

describe('F3-084, F3-085: a question typed during an answer, and Stop', () => {
  it('says why Send waits, and does not put the stopped question back', async () => {
    const { api } = fakeApi({});
    let abortSeen: AbortSignal | undefined;
    const fetcher = vi.fn((_url: string, init?: RequestInit) => {
      abortSeen = init?.signal ?? undefined;
      return new Promise<Response>((_, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
    });
    const { container } = render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    ask('First question');
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'Follow-up' } });
    expect(screen.getByText('Wait for the answer, or Stop it, to send this question.')).toBeInTheDocument();
    expect(createWrapper(container).findPromptInput()!.findActionButton().getElement()).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(abortSeen?.aborted).toBe(true));
    await act(async () => new Promise((r) => setTimeout(r, 20)));
    expect(screen.getByPlaceholderText('Ask a question')).toHaveValue(''); // it used to come back after Stop
  });
});

describe('F3-104: a copied answer keeps working links', () => {
  it('makes in-app links absolute', () => {
    expect(absoluteLinks('See [Acme](/insights/nodes/signal/1) and <a href="/radars/x">x</a> and [web](https://a.example/b).', 'https://app.example.com')).toBe(
      'See [Acme](https://app.example.com/insights/nodes/signal/1) and <a href="https://app.example.com/radars/x">x</a> and [web](https://a.example/b).',
    );
  });
});

describe('F3-071: an in-app link in the assistant closes it on a phone', () => {
  it('closes the drawer on navigation below the mobile width, and keeps it open on a desktop', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    let go: (to: string) => unknown = () => undefined;
    function Pages() {
      const navigate = useNavigate();
      go = (to) => navigate(to);
      return (
        <Routes>
          <Route path="/" element={<div>home</div>} />
          <Route path="/radars" element={<div>radars</div>} />
          <Route path="/graph" element={<div>graph</div>} />
        </Routes>
      );
    }
    const width = vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1200);
    render(
      <TestSessionProvider value={sessionValue({ api })}>
        <TestRouter path="/">
          <AppShell>
            <Pages />
          </AppShell>
        </TestRouter>
      </TestSessionProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Open the assistant' }));
    expect(await screen.findByPlaceholderText('Ask a question')).toBeInTheDocument();
    await act(async () => void (await go('/radars')));
    expect(await screen.findByText('radars')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide the assistant' })).toBeInTheDocument(); // still open on a desktop
    width.mockReturnValue(390);
    await act(async () => void (await go('/graph')));
    expect(await screen.findByText('graph')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open the assistant' })).toBeInTheDocument());
  });
});
