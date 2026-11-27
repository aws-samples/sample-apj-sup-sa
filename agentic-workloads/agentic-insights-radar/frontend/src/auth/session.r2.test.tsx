// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 red-team findings about the real session provider (OIDC replaced by a store, as in session.provider.test). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { dirtyPages } from '@/lib/dirty';
import { ADMIN, CONFIG } from '@/test/session';

type AuthState = { user?: { id_token: string; profile: { sub: string } }; isAuthenticated: boolean; removeUser: () => void };
const store = vi.hoisted(() => {
  let state: AuthState = { isAuthenticated: false, removeUser: () => undefined };
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (l: () => void) => (listeners.add(l), () => void listeners.delete(l)),
    signIn: () => {
      state = { ...state, isAuthenticated: true, user: { id_token: 'id-token-1', profile: { sub: 'u-admin' } } };
      listeners.forEach((l) => l());
    },
  };
});
vi.mock('react-oidc-context', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    useAuth: () => useSyncExternalStore(store.subscribe, store.get),
  };
});

import { act } from 'react';

import { CONFIG_REFRESH_MS, SessionProvider, useSession } from './session';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** The headers of the dialogs that are actually on screen. */
const shownModals = () =>
  createWrapper(document.body)
    .findAllModals()
    .filter((m) => m.isVisible())
    .map((m) => m.findHeader().getElement().textContent);

/** Shows the kill-switch state the app is working from, and can make one failing request. */
function Banner() {
  const { config, api } = useSession();
  return (
    <>
      <div>{config.genai_enabled ? 'AI running' : 'AI paused'}</div>
      <button onClick={() => void api.search('x').catch(() => undefined)}>search</button>
    </>
  );
}

/** Answers /api/me, then /api/config with whatever `paused` says at the time of the call. */
function serve(paused: () => boolean, counts: { config: number }) {
  return (url: string) => {
    if (url.endsWith('/api/me')) return Promise.resolve(new Response(JSON.stringify(ADMIN)));
    if (url.endsWith('/api/config')) {
      counts.config += 1;
      return Promise.resolve(new Response(JSON.stringify({ ...CONFIG, genai_enabled: !paused() })));
    }
    return Promise.resolve(new Response(JSON.stringify({ code: 'genai_disabled', message: 'AI features are paused' }), { status: 503 }));
  };
}

function Expire() {
  const { api } = useSession();
  return <button onClick={() => void api.radars().catch(() => undefined)}>load</button>;
}

describe('F2-012: the expiry dialog is shown once, not once per request', () => {
  it('stays away after Not now until the token is renewed', async () => {
    let calls = 0;
    vi.stubGlobal('fetch', (url: string) => {
      if (url.endsWith('/api/me')) return Promise.resolve(new Response(JSON.stringify(ADMIN)));
      calls += 1;
      return Promise.resolve(new Response('{"code":"unauthorized","message":"Unauthorized"}', { status: 401 }));
    });
    const view = render(<SessionProvider config={CONFIG}><Expire /></SessionProvider>);
    act(() => store.signIn());
    screen.getByText('load').click();
    expect(await screen.findByText('Your session has expired')).toBeInTheDocument();
    screen.getByText('Not now').click();
    await waitFor(() => expect(shownModals()).toEqual([]));
    // A page that polls keeps asking: the dialog must not come back on its own.
    screen.getByText('load').click();
    screen.getByText('load').click();
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(3));
    expect(shownModals()).toEqual([]);
    view.unmount();
  });
});

describe('F2-009: signing in again warns about unsaved changes', () => {
  it('says the edits are lost while a page reports them, and not otherwise', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(url.endsWith('/api/me') ? new Response(JSON.stringify(ADMIN)) : new Response('{"code":"unauthorized"}', { status: 401 })),
    );
    const view = render(<SessionProvider config={CONFIG}><Expire /></SessionProvider>);
    act(() => store.signIn());
    screen.getByText('load').click();
    expect(await screen.findByText(/choose Not now first if you need to copy unsaved changes/)).toBeInTheDocument();
    act(() => dirtyPages.set('a-page', true));
    expect(await screen.findByText(/A page has unsaved changes/)).toBeInTheDocument();
    act(() => dirtyPages.set('a-page', false));
    view.unmount();
  });
});

describe('F2-005: the AI kill switch does not go stale in other tabs', () => {
  it('re-reads /api/config when the tab is focused again and when it becomes visible', async () => {
    let paused = false;
    const counts = { config: 0 };
    vi.stubGlobal('fetch', serve(() => paused, counts));
    render(<SessionProvider config={CONFIG}><Banner /></SessionProvider>);
    act(() => store.signIn());
    expect(await screen.findByText('AI running')).toBeInTheDocument();
    paused = true;
    window.dispatchEvent(new Event('focus'));
    expect(await screen.findByText('AI paused')).toBeInTheDocument();
    paused = false;
    document.dispatchEvent(new Event('visibilitychange'));
    expect(await screen.findByText('AI running')).toBeInTheDocument();
    expect(counts.config).toBe(2);
  });

  it('re-reads /api/config on a timer, but not while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let paused = true;
    const counts = { config: 0 };
    vi.stubGlobal('fetch', serve(() => paused, counts));
    render(<SessionProvider config={CONFIG}><Banner /></SessionProvider>);
    act(() => store.signIn());
    await act(async () => void vi.advanceTimersByTime(CONFIG_REFRESH_MS + 10));
    expect(await screen.findByText('AI paused')).toBeInTheDocument();
    expect(counts.config).toBe(1);
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    paused = false;
    await act(async () => void vi.advanceTimersByTime(3 * CONFIG_REFRESH_MS));
    window.dispatchEvent(new Event('focus'));
    expect(counts.config).toBe(1);
    hidden.mockRestore();
  });

  it('re-reads /api/config when a response says AI features are paused', async () => {
    let paused = false;
    const counts = { config: 0 };
    vi.stubGlobal('fetch', serve(() => paused, counts));
    render(<SessionProvider config={CONFIG}><Banner /></SessionProvider>);
    act(() => store.signIn());
    expect(await screen.findByText('AI running')).toBeInTheDocument();
    paused = true;
    screen.getByText('search').click();
    expect(await screen.findByText('AI paused')).toBeInTheDocument();
    await waitFor(() => expect(counts.config).toBe(1));
  });
});
