// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about an ended session (F3-003, F3-004, F3-039, F3-040, F3-041, F3-045). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from '@/components/AppShell';
import { NOT_SAVED_EXPIRED, useNotify } from '@/components/Notifications';
import { dirtyPages, useReportDirty } from '@/lib/dirty';
import { ADMIN, CONFIG, TestRouter } from '@/test/session';

type AuthState = {
  user?: { id_token: string; profile: { sub: string } };
  isAuthenticated: boolean;
  removeUser: () => Promise<void>;
  signinRedirect: (args?: unknown) => Promise<void>;
};
const store = vi.hoisted(() => {
  const redirect = { calls: [] as unknown[] };
  let state: AuthState = {
    isAuthenticated: false,
    removeUser: async () => undefined,
    signinRedirect: async (args?: unknown) => void redirect.calls.push(args),
  };
  const listeners = new Set<() => void>();
  return {
    redirect,
    get: () => state,
    subscribe: (l: () => void) => (listeners.add(l), () => void listeners.delete(l)),
    signIn: (token = 'id-1') => {
      state = { ...state, isAuthenticated: true, user: { id_token: token, profile: { sub: 'u-admin' } } };
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

import { ME_RETRY_MS, SessionProvider, useSession } from './session';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  act(() => dirtyPages.set('probe', false));
  store.redirect.calls.length = 0;
});

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
const shownModals = () =>
  createWrapper(document.body)
    .findAllModals()
    .filter((m) => m.isVisible())
    .map((m) => m.findHeader().getElement().textContent);
const modal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

/** A page with unsaved work that tries to save it; `rescue` is its download, if it has one. */
function Saver({ rescue, dirty = true }: { rescue?: () => void; dirty?: boolean }) {
  const { api, sessionEnded } = useSession();
  const { fail } = useNotify();
  useReportDirty(dirty, rescue ? { label: 'Download my edits', run: rescue } : undefined);
  return (
    <>
      <button onClick={() => void api.radars().catch(fail)}>save</button>
      <div>{sessionEnded ? 'polling stopped' : 'polling'}</div>
    </>
  );
}

function app(ui: React.ReactNode) {
  return (
    <SessionProvider config={CONFIG}>
      <TestRouter path="/">
        <AppShell>{ui}</AppShell>
      </TestRouter>
    </SessionProvider>
  );
}

describe('F3-004, F3-003: after "Not now" the rescue stays reachable and polling stays off', () => {
  it('keeps a banner with the download and Sign in again, and counts the session as ended', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') || url.endsWith('/api/config') ? json(url.endsWith('/api/me') ? ADMIN : CONFIG) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    const rescue = vi.fn();
    render(app(<Saver rescue={rescue} />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your session has expired']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
    // Before: sessionEnded went back to false, so a product page polled again and its first 401 replaced it.
    expect(screen.getByText('polling stopped')).toBeInTheDocument();
    const banner = createWrapper(document.body).findAllAlerts().find((a) => a.findHeader()?.getElement().textContent === 'Your session has expired')!;
    expect(banner).toBeTruthy();
    fireEvent.click(within(banner.getElement()).getByRole('button', { name: 'Download my edits' }));
    expect(rescue).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('save'));
    expect(await screen.findByText(NOT_SAVED_EXPIRED)).toBeInTheDocument();
    expect(NOT_SAVED_EXPIRED).toMatch(/download them from the banner at the top/);
    fireEvent.click(within(banner.getElement()).getByRole('button', { name: 'Sign in again and lose changes' }));
    expect(store.redirect.calls).toEqual([{ state: '/' }]);
  });

  it('ends "Not now" when a renewed token arrives', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    render(app(<Saver />));
    act(() => store.signIn('id-1'));
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your session has expired']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    expect(await screen.findByText('polling stopped')).toBeInTheDocument();
    const banners = () => createWrapper(document.body).findAllAlerts().filter((a) => a.findHeader()?.getElement().textContent === 'Your session has expired');
    expect(banners()).toHaveLength(1);
    act(() => store.signIn('id-2'));
    expect(await screen.findByText('polling')).toBeInTheDocument();
    expect(banners()).toHaveLength(0);
  });
});

describe('F3-004: the banner without unsaved changes', () => {
  it('offers plain Sign in again', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    render(app(<Saver dirty={false} />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your session has expired']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
    const banner = createWrapper(document.body).findAllAlerts().find((a) => a.findHeader()?.getElement().textContent === 'Your session has expired')!;
    fireEvent.click(within(banner.getElement()).getByRole('button', { name: 'Sign in again' }));
    expect(store.redirect.calls).toHaveLength(1);
  });
});

describe('F3-041: Sign out without a rescue to offer', () => {
  it('does not mention a download', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'account_disabled', message: 'disabled' }, 401)));
    render(app(<Saver />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your account is disabled']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(shownModals()).toEqual(['Sign out without saving?']));
    expect(within(modal().getElement()).getByText(/Choose Stay signed in to save them first\.$/)).toBeInTheDocument();
  });
});

describe('F3-039: the dialog offers a download only where a page has one', () => {
  it('does not point at a download button that is not there', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    render(app(<Saver />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    expect(await screen.findByText(/Choose Not now to copy them out first/)).toBeInTheDocument();
    expect(screen.queryByText(/Download them first/)).toBeNull();
  });
});

describe('F3-040: Esc on the expiry dialog is "Not now"', () => {
  it('does not bring the dialog back at the next 401', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    render(app(<Saver rescue={vi.fn()} />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your session has expired']));
    modal().findDismissButton().click();
    await waitFor(() => expect(shownModals()).toEqual([]));
    fireEvent.click(screen.getByText('save'));
    expect(await screen.findByText(NOT_SAVED_EXPIRED)).toBeInTheDocument();
    expect(shownModals()).toEqual([]);
  });
});

describe('F3-041: Sign out offers the same rescue', () => {
  it('shows the download in "Sign out without saving?" from the disabled-account banner', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'account_disabled', message: 'disabled' }, 401)));
    const rescue = vi.fn();
    render(app(<Saver rescue={rescue} />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your account is disabled']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
    expect(screen.getByText(/Nothing can be saved any more/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(shownModals()).toEqual(['Sign out without saving?']));
    expect(within(modal().getElement()).getByText(/or download them\./)).toBeInTheDocument();
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Download my edits' }));
    expect(rescue).toHaveBeenCalledTimes(1);
  });
});

describe('F3-045: the profile warning while the session is over, and its retries', () => {
  it('is hidden while the session is over', async () => {
    let meStatus = 200;
    vi.stubGlobal('fetch', (url: string) =>
      url.endsWith('/api/me') ? (meStatus === 200 ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)) : url.endsWith('/api/config') ? json(CONFIG) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401),
    );
    render(app(<Saver />));
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your session has expired']));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    meStatus = 401;
    await act(async () => void window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(screen.getByText('polling stopped')).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('Your profile could not be read again')).toBeNull();
  });

  it('tries again after every failure, waiting longer each time', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fail = false;
    let reads = 0;
    vi.stubGlobal('fetch', (url: string) => {
      if (url.endsWith('/api/config')) return json(CONFIG);
      reads += 1;
      return fail ? json({ code: 'busy', message: 'try later' }, 429) : json(ADMIN);
    });
    render(app(<div>the page</div>));
    act(() => store.signIn());
    expect(await screen.findByText('the page')).toBeInTheDocument();
    fail = true;
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')));
    expect(await screen.findByText(/try later/)).toBeInTheDocument();
    const first = reads;
    await act(async () => void vi.advanceTimersByTime(ME_RETRY_MS + 10));
    await waitFor(() => expect(reads).toBe(first + 1));
    // The second failure is tried again too (it used to fire once), after twice the wait.
    await act(async () => void vi.advanceTimersByTime(ME_RETRY_MS + 10));
    expect(reads).toBe(first + 1);
    await act(async () => void vi.advanceTimersByTime(ME_RETRY_MS + 10));
    await waitFor(() => expect(reads).toBe(first + 2));
    fail = false;
    await act(async () => void vi.advanceTimersByTime(4 * ME_RETRY_MS + 10));
    await waitFor(() => expect(screen.queryByText('Your profile could not be read again')).toBeNull());
  });
});
