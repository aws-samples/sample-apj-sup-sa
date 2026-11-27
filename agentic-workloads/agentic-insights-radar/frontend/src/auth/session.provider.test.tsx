// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CONFIG, VIEWER } from '@/test/session';

// The real provider, with OIDC replaced by a store whose user arrives after the first render, as in a
// browser: oidc-client loads the user asynchronously, and the app's pages mount only once it has.
type AuthState = { user?: { id_token: string; refresh_token?: string; profile: { sub: string } }; isAuthenticated: boolean; removeUser: () => void };
const store = vi.hoisted(() => {
  let state: AuthState = { isAuthenticated: false, removeUser: () => undefined };
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (l: () => void) => (listeners.add(l), () => void listeners.delete(l)),
    signIn: (refresh?: string) => {
      state = { ...state, isAuthenticated: true, user: { id_token: 'id-token-1', refresh_token: refresh, profile: { sub: 'u1' } } };
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
import { useAuth } from 'react-oidc-context';

import { SessionProvider, useSession } from './session';

function Gate({ children }: { children: React.ReactNode }) {
  return useAuth().isAuthenticated ? <>{children}</> : null;  // like App: pages only when signed in
}

function FirstLoad() {
  const { api } = useSession();
  const [nav, setNav] = React.useState('');
  React.useEffect(() => {
    void api.request<{ items: unknown[] }>('GET', '/api/nav').then(() => setNav('loaded'));
  }, [api]);
  return <div>{nav}</div>;
}

afterEach(() => vi.unstubAllGlobals());

describe('SessionProvider', () => {
  it('sends the ID token on the very first request a page makes', async () => {
    const seen: (string | undefined)[] = [];
    vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
      seen.push((init.headers as Record<string, string>).authorization);
      const body = url.endsWith('/api/me') ? VIEWER : { items: [] };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    render(
      <SessionProvider config={CONFIG}>
        <Gate>
          <FirstLoad />
        </Gate>
      </SessionProvider>,
    );
    act(() => store.signIn());
    await waitFor(() => expect(screen.getByText('loaded')).toBeInTheDocument());
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((h) => h === 'Bearer id-token-1')).toBe(true);
  });
});

function SignOut() {
  const { signOut } = useSession();
  return <button onClick={() => void signOut()}>out</button>;
}

function Expire() {
  const { api, expire } = useSession();
  return (
    <>
      <button onClick={() => void api.request('GET', '/api/radars').catch(() => undefined)}>load</button>
      <button onClick={expire}>expire</button>
    </>
  );
}

describe('an expired session', () => {
  it('asks the user to sign in again after a 401, once, instead of a toast per request', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(url.endsWith('/api/me') ? new Response(JSON.stringify(VIEWER)) : new Response('{"code":"unauthorized","message":"Unauthorized"}', { status: 401 })),
    );
    const signinRedirect = vi.fn(async () => undefined);
    render(<SessionProvider config={CONFIG}><Expire /></SessionProvider>);
    act(() => store.signIn());
    Object.assign(store.get(), { signinRedirect });
    window.history.pushState({}, '', '/radars/x?q=1');
    screen.getByText('load').click();
    expect(await screen.findByText('Your session has expired')).toBeInTheDocument();
    screen.getByText('Not now').click();
    expect(signinRedirect).not.toHaveBeenCalled();
    screen.getByText('expire').click();
    createWrapper(document.body).findModal()!.findDismissButton().click();
    expect(signinRedirect).not.toHaveBeenCalled();
    screen.getByText('expire').click();
    (await screen.findByText('Sign in again')).click();
    expect(signinRedirect).toHaveBeenCalledWith({ state: '/radars/x?q=1' });
  });
});

describe('a disabled account (CR-13)', () => {
  it('says the account is disabled instead of asking to sign in again, and offers to sign out', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(
        url.endsWith('/api/me')
          ? new Response(JSON.stringify(VIEWER))
          : new Response('{"code":"account_disabled","message":"this account is disabled"}', { status: 401 }),
      ),
    );
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, origin: 'https://app.example.com', assign });
    render(<SessionProvider config={CONFIG}><Expire /></SessionProvider>);
    act(() => store.signIn());
    const removeUser = vi.fn(async () => undefined);
    Object.assign(store.get(), { removeUser });
    window.sessionStorage.setItem('air.chat.v1', '{"session":"chat-1","turns":[]}');
    screen.getByText('load').click();
    expect(await screen.findByText('Your account is disabled')).toBeInTheDocument();
    const shown = createWrapper(document.body).findAllModals().filter((m) => m.isVisible());
    expect(shown.map((m) => m.findHeader().getElement().textContent)).toEqual(['Your account is disabled']);
    (await screen.findByText('Sign out')).click();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(removeUser).toHaveBeenCalled();
    expect(window.sessionStorage.getItem('air.chat.v1')).toBeNull(); // the chat goes with the session
  });

  it('cannot be dismissed into an app where nothing works (F2-084)', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(url.endsWith('/api/me') ? new Response(JSON.stringify(VIEWER)) : new Response('{"code":"account_disabled"}', { status: 401 })),
    );
    render(<SessionProvider config={CONFIG}><Expire /></SessionProvider>);
    act(() => store.signIn());
    screen.getByText('load').click();
    expect(await screen.findByText('Your account is disabled')).toBeInTheDocument();
    const dialog = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    dialog.findDismissButton().click(); // Sign out is the only way on: the dialog stays
    await new Promise((r) => setTimeout(r, 20));
    expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(true);
    expect(screen.getByText('Your account is disabled')).toBeInTheDocument();
  });
});

describe('signing out', () => {
  it.each([['rt-9', 1], [undefined, 0]])('revokes the refresh token (%s) before leaving', async (refresh, revokes) => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', (url: string) => {
      urls.push(url);
      return Promise.resolve(new Response(JSON.stringify(VIEWER), { status: 200 }));
    });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, origin: 'https://app.example.com', assign });
    render(<SessionProvider config={CONFIG}><SignOut /></SessionProvider>);
    act(() => store.signIn(refresh));
    screen.getByText('out').click();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(urls.filter((u) => u.endsWith('/oauth2/revoke'))).toHaveLength(revokes);
  });
});
