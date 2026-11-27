// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 frontend review (CR2-F01): a failed background re-read of /api/me keeps the page and its draft. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from '@/components/AppShell';
import RadarPage from '@/pages/RadarPage';
import { NOT_SAVED_DISABLED, NOT_SAVED_EXPIRED, NotificationProvider, useNotify } from '@/components/Notifications';
import { dirtyPages, useReportDirty } from '@/lib/dirty';
import { radarView } from '@/test/data';
import { ADMIN, CONFIG, TestRouter } from '@/test/session';

type AuthState = {
  user?: { id_token: string; profile: { sub: string } };
  isAuthenticated: boolean;
  removeUser: () => Promise<void>;
};
const store = vi.hoisted(() => {
  let state: AuthState = { isAuthenticated: false, removeUser: async () => undefined };
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (l: () => void) => (listeners.add(l), () => void listeners.delete(l)),
    signIn: (sub = 'u-admin') => {
      state = { ...state, isAuthenticated: true, user: { id_token: `id-${sub}`, profile: { sub } } };
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
});

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

/** Serves the radar page; /api/me answers with whatever `me()` gives at the time of the call. */
function serve(me: () => Promise<Response>, counts: { me: number }) {
  return (url: string) => {
    if (url.endsWith('/api/me')) {
      counts.me += 1;
      return me();
    }
    if (url.endsWith('/api/config')) return json(CONFIG);
    if (url.endsWith('/api/radars/rivals')) return json(radarView());
    return json({ code: 'not_found', message: 'no route' }, 404);
  };
}

const shownModals = () =>
  createWrapper(document.body)
    .findAllModals()
    .filter((m) => m.isVisible())
    .map((m) => m.findHeader().getElement().textContent);

describe('CR2-F01: a failed profile re-read keeps the page', () => {
  it('keeps the radar page and its unsaved rows, warns without blocking, and tries again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fail = false;
    const counts = { me: 0 };
    vi.stubGlobal('fetch', serve(() => (fail ? Promise.reject(new TypeError('Failed to fetch')) : json(ADMIN)), counts));
    const { container } = render(
      <SessionProvider config={CONFIG}>
        <TestRouter path="/radars/rivals?tab=edit">
          <AppShell>
            <Routes>
              <Route path="/radars/:slug" element={<RadarPage />} />
            </Routes>
          </AppShell>
        </TestRouter>
      </SessionProvider>,
    );
    act(() => store.signIn());
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    expect(screen.getByText(/^1 unsaved change:/)).toBeInTheDocument();
    fail = true;
    await act(async () => void window.dispatchEvent(new Event('focus'))); // the laptop lid opens before Wi-Fi is back
    expect(await screen.findByText('Your profile could not be read again')).toBeInTheDocument();
    expect(screen.queryByText('Your profile could not be loaded')).toBeNull();
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument(); // the draft is still there
    expect(createWrapper(container).findTabs()).not.toBeNull();
    // It tries again by itself, and the warning goes once a re-read works.
    fail = false;
    const before = counts.me;
    await act(async () => void vi.advanceTimersByTime(ME_RETRY_MS + 10));
    await waitFor(() => expect(counts.me).toBeGreaterThan(before));
    await waitFor(() => expect(screen.queryByText('Your profile could not be read again')).toBeNull());
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
  });

  it('offers Try again on the warning', async () => {
    let fail = false;
    const counts = { me: 0 };
    vi.stubGlobal('fetch', serve(() => (fail ? json({ code: 'busy', message: 'try later' }, 429) : json(ADMIN)), counts));
    render(
      <SessionProvider config={CONFIG}>
        <TestRouter path="/">
          <AppShell>
            <div>the page</div>
          </AppShell>
        </TestRouter>
      </SessionProvider>,
    );
    act(() => store.signIn());
    expect(await screen.findByText('the page')).toBeInTheDocument();
    fail = true;
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')));
    expect(await screen.findByText(/try later/)).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.queryByText('Your profile could not be read again')).toBeNull());
    expect(screen.getByText('the page')).toBeInTheDocument();
  });

  it('still blocks when the profile was never read, and does not lend one user the profile of another', async () => {
    let meFor: () => Promise<Response> = () => json(ADMIN);
    vi.stubGlobal('fetch', serve(() => meFor(), { me: 0 }));
    function Who() {
      const { me, meError } = useSession();
      return <div>{meError ? `error ${me?.email ?? 'none'}` : (me?.email ?? 'loading')}</div>;
    }
    render(<SessionProvider config={CONFIG}><Who /></SessionProvider>);
    act(() => store.signIn('u-admin'));
    expect(await screen.findByText('admin@example.com')).toBeInTheDocument();
    meFor = () => Promise.reject(new TypeError('Failed to fetch'));
    act(() => store.signIn('u-other'));
    expect(await screen.findByText('error none')).toBeInTheDocument();
  });

  it('a disabled account with unsaved changes can wait before signing out', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'account_disabled', message: 'disabled' }, 401),
    );
    function Load() {
      const { api } = useSession();
      return <button onClick={() => void api.radars().catch(() => undefined)}>load</button>;
    }
    render(<SessionProvider config={CONFIG}><Load /></SessionProvider>);
    act(() => store.signIn());
    act(() => dirtyPages.set('probe', true));
    screen.getByText('load').click();
    expect(await screen.findByText(/can no longer be saved: choose Not now to copy them first/)).toBeInTheDocument();
    expect(shownModals()).toEqual(['Your account is disabled']);
    const modal = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    expect(modal.findFooter()!.getElement().textContent).toContain('Sign out and lose changes');
    fireEvent.click(within(modal.getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
  });
});

/** A page with unsaved changes that tries to save them, reporting failures as pages do. */
function Saver({ rescue }: { rescue: () => void }) {
  const { api } = useSession();
  const { fail } = useNotify();
  useReportDirty(true, { label: 'Download my edits', run: rescue });
  return <button onClick={() => void api.radars().catch(fail)}>save</button>;
}

describe('CR2-F03: after "Not now", a failed save says so', () => {
  it('offers the page download in the dialog, and a later 401 shows a message instead of nothing', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'unauthorized', message: 'Unauthorized' }, 401)));
    const rescue = vi.fn();
    render(
      <SessionProvider config={CONFIG}>
        <NotificationProvider>
          <Saver rescue={rescue} />
        </NotificationProvider>
      </SessionProvider>,
    );
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    expect(await screen.findByText(/They cannot be saved until you sign in again/)).toBeInTheDocument();
    const modal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    expect(within(modal().getElement()).getByRole('button', { name: 'Sign in again and lose changes' })).toBeInTheDocument();
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Download my edits' }));
    expect(rescue).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(NOT_SAVED_EXPIRED)).toBeNull(); // the dialog said it: no toast as well
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
    fireEvent.click(screen.getByText('save'));
    expect(await screen.findByText(NOT_SAVED_EXPIRED)).toBeInTheDocument();
    expect(shownModals()).toEqual([]);
  });

  it('says the account is disabled when that dialog was put off', async () => {
    vi.stubGlobal('fetch', (url: string) => (url.endsWith('/api/me') ? json(ADMIN) : json({ code: 'account_disabled', message: 'disabled' }, 401)));
    render(
      <SessionProvider config={CONFIG}>
        <NotificationProvider>
          <Saver rescue={vi.fn()} />
        </NotificationProvider>
      </SessionProvider>,
    );
    act(() => store.signIn());
    fireEvent.click(await screen.findByText('save'));
    await waitFor(() => expect(shownModals()).toEqual(['Your account is disabled']));
    const modal = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    expect(screen.queryByText(NOT_SAVED_DISABLED)).toBeNull();
    fireEvent.click(within(modal.getElement()).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(shownModals()).toEqual([]));
    fireEvent.click(screen.getByText('save'));
    expect(await screen.findByText(NOT_SAVED_DISABLED)).toBeInTheDocument();
    expect(shownModals()).toEqual([]);
  });
});
