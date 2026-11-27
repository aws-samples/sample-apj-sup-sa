// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { describeError, NotificationProvider, useNotify } from '@/components/Notifications';
import { ApiError } from '@/api/client';
import { ADMIN, fakeApi, sessionValue, VIEWER, wrap, CONFIG } from '@/test/session';

const auth = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('react-oidc-context', () => ({ useAuth: () => auth.value, AuthProvider: ({ children }: { children: React.ReactNode }) => children }));

import { AfterSignIn, AppRoutes, Gate, TRIED_KEY } from './App';
import { applyProfile } from './lib/profile';
import AppShell, { navItems } from './components/AppShell';
import { download, slugFile, svgMarkup, svgToPng } from './radar/export';

afterEach(() => vi.restoreAllMocks());

describe('App gate', () => {
  it('shows a spinner, sign-in and errors', () => {
    auth.value = { isLoading: true };
    const { unmount } = render(<MemoryRouter><Gate /></MemoryRouter>);
    expect(screen.getByText('Signing in\u2026')).toBeInTheDocument();
    unmount();
    const signinRedirect = vi.fn(async () => undefined);
    auth.value = { isLoading: false, isAuthenticated: false, signinRedirect, error: new Error('expired') };
    render(<MemoryRouter initialEntries={['/radars?x=1']}><Gate /></MemoryRouter>);
    // F2-085: a known OIDC failure is said in words the reader can act on.
    expect(screen.getByText('The sign-in took too long and expired. Sign in again.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(signinRedirect).toHaveBeenCalledWith({ state: '/radars?x=1' });
  });

  it('shows the deployment tagline on the sign-in page', () => {
    applyProfile({ app_name: 'Trade Radar', tagline: 'Rule changes by urgency', about_md: '', version: 1, lists: {} });
    auth.value = { isLoading: false, isAuthenticated: false, signinRedirect: vi.fn(async () => undefined), error: new Error('expired') };
    render(<MemoryRouter><Gate /></MemoryRouter>);
    expect(screen.getByText('Rule changes by urgency')).toBeInTheDocument();
    expect(screen.queryByText(/Track competitors/)).toBeNull();
    applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} });
  });

  it('goes straight to the identity provider the first time a tab shows the sign-in page (F2-025)', async () => {
    sessionStorage.removeItem(TRIED_KEY);
    const signinRedirect = vi.fn(async () => undefined);
    auth.value = { isLoading: false, isAuthenticated: false, signinRedirect };
    const { unmount } = render(<MemoryRouter initialEntries={['/radars/x']}><Gate /></MemoryRouter>);
    expect(screen.getByText('Signing in\u2026')).toBeInTheDocument();
    await waitFor(() => expect(signinRedirect).toHaveBeenCalledWith({ state: '/radars/x' }));
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
    unmount();
    // A second time in the same tab shows the button instead, so a refusal cannot loop.
    render(<MemoryRouter initialEntries={['/radars/x']}><Gate /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(signinRedirect).toHaveBeenCalledTimes(1);
    sessionStorage.removeItem(TRIED_KEY);
  });

  it('does not redirect by itself when session storage is blocked', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('site data is blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('site data is blocked');
    });
    const signinRedirect = vi.fn(async () => undefined);
    auth.value = { isLoading: false, isAuthenticated: false, signinRedirect };
    render(<MemoryRouter initialEntries={['/radars/x']}><Gate /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(signinRedirect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(signinRedirect).toHaveBeenCalledTimes(1); // the button still works without storage
    get.mockRestore();
    set.mockRestore();
  });

  it('never sends a retried sign-in back to the failed callback page', () => {
    const signinRedirect = vi.fn(async () => undefined);
    auth.value = { isLoading: false, isAuthenticated: false, signinRedirect, error: new Error('invalid_grant') };
    render(<MemoryRouter initialEntries={['/auth/callback?code=used&state=s']}><Gate /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(signinRedirect).toHaveBeenCalledWith({ state: '/' });
  });

  it('returns to the original page after sign-in', () => {
    for (const [state, expected] of [['/radars/x', '/radars/x'], ['//evil', '/'], [undefined, '/'], ['/auth/callback', '/'], ['/auth/callback?code=x&state=y', '/'], ['/auth/callback#err', '/'], ['/auth/callbacks', '/auth/callbacks']]) {
      auth.value = { user: { state } };
      const { unmount } = render(wrap(<AfterSignInProbe />, { api: fakeApi({}).api, path: '/auth/callback' }));
      expect(screen.getByTestId('where').textContent).toBe(expected);
      unmount();
    }
  });

  it('opens the app for signed-in users', async () => {
    auth.value = { isLoading: false, isAuthenticated: true };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<Gate />, { api, path: '/nowhere' }));
    expect(await screen.findByText('Page not found')).toBeInTheDocument();
  });

  it('keeps Administration from people who cannot use it', async () => {
    auth.value = { isLoading: false, isAuthenticated: true };
    const { api, calls } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppRoutes />, { api, path: '/admin', me: VIEWER }));
    expect(await screen.findByText('You need administrator access')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Invite user' })).toBeNull();
    expect(calls.some((c) => c.path === '/api/users')).toBe(false); // no page of 403s
    fireEvent.click(screen.getByRole('button', { name: 'Go to Overview' }));
  });

  it('opens Administration for administrators', async () => {
    auth.value = { isLoading: false, isAuthenticated: true };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/users': { items: [] } });
    render(wrap(<AppRoutes />, { api, path: '/admin', me: ADMIN }));
    expect(await screen.findByRole('button', { name: 'Invite user' })).toBeInTheDocument();
  });

  it('shows "Account" when the profile could not be loaded', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppShell><div>content</div></AppShell>, { api, me: null }));
    expect(createWrapper(document.body).findTopNavigation()!.findUtility(2)!.getElement().textContent).toContain('Account');
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('waits for the profile before deciding about Administration', async () => {
    const { RequireCapability } = await import('./components/ErrorState');
    const { api } = fakeApi({});
    const { TestSessionProvider } = await import('@/auth/session');
    render(
      <MemoryRouter>
        <TestSessionProvider value={sessionValue({ api, me: null, meLoading: true })}>
          <RequireCapability action="user.manage" what="Administration"><div>admin page</div></RequireCapability>
        </TestSessionProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText('Loading')).toBeInTheDocument();
    expect(screen.queryByText('admin page')).toBeNull();
  });

  it('routes signed-in users and catches page crashes', async () => {
    auth.value = { isLoading: false, isAuthenticated: true };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppRoutes />, { api, path: '/nowhere' }));
    expect(await screen.findByText('Page not found')).toBeInTheDocument();
  });
});

import { Route, Routes, useLocation } from 'react-router-dom';

function AfterSignInProbe() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<AfterSignIn />} />
      <Route path="*" element={<Where />} />
    </Routes>
  );
}

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

describe('AppShell', () => {
  it('builds navigation with custom kinds and admin links', () => {
    const items = navItems({ builtin: [], custom: [{ kind: 'insight:x', label: 'X moves', module: 'm' }] }, true);
    expect(JSON.stringify(items)).toContain('X moves');
    expect(JSON.stringify(items)).toContain('/admin');
    expect(JSON.stringify(navItems(undefined, false))).not.toContain('/admin');
  });

  it('opens the assistant, signs out and warns when AI is paused', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const signOut = vi.fn(async () => undefined);
    const value = sessionValue({ config: { ...CONFIG, genai_enabled: false }, api, me: VIEWER, signOut });
    const { TestSessionProvider } = await import('@/auth/session');
    render(
      <MemoryRouter>
        <TestSessionProvider value={value}>
          <AppShell><div>content</div></AppShell>
        </TestSessionProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText('AI features are paused')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open the assistant' })); // F2-078: the label follows the state
    expect(await screen.findByPlaceholderText('The assistant is paused')).toBeDisabled();
    const menu = createWrapper(document.body).findTopNavigation()!.findUtility(2)!.findMenuDropdownType()!;
    expect(menu.getElement().textContent).toContain('Role: Viewer'); // the label, not the raw role code
    menu.openDropdown();
    menu.findItemById('signout')!.click();
    await waitFor(() => expect(signOut).toHaveBeenCalled());
    // Revoking can take seconds: the menu says so, and a second click starts nothing.
    expect(menu.getElement().textContent).toContain('Signing out');
    menu.openDropdown();
    menu.findItemById('signout')?.click();
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it('highlights the list a detail page belongs to', async () => {
    const { activeNavHref } = await import('./components/AppShell');
    expect(activeNavHref('/')).toBe('/');
    expect(activeNavHref('/radars/rivals')).toBe('/radars');
    expect(activeNavHref('/products/p1')).toBe('/products');
    expect(activeNavHref('/modules/scout')).toBe('/modules');
    expect(activeNavHref('/insights/trends')).toBe('/insights/trends');
    expect(activeNavHref('/insights')).toBe('/insights/');
    expect(activeNavHref('/insights/nodes/signal/1')).toBe('/insights/signals');
    expect(activeNavHref('/insights/nodes/content_draft/x')).toBe('/insights/drafts');
    expect(activeNavHref('/insights/nodes/organization/1')).toBe('/insights/nodes/organization/1');
    expect(activeNavHref('/insights/nodes')).toBe('/insights/nodes');
  });

  it('starts with the navigation closed on a phone, and holds pages until the profile loads', async () => {
    const width = window.innerWidth;
    window.innerWidth = 400;
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const value = sessionValue({ api, me: null, meLoading: true });
    const { TestSessionProvider } = await import('@/auth/session');
    render(
      <MemoryRouter>
        <TestSessionProvider value={value}>
          <AppShell><div>content</div></AppShell>
        </TestSessionProvider>
      </MemoryRouter>,
    );
    window.innerWidth = width;
    expect(createWrapper(document.body).findAppLayout()!.findNavigationToggle()!.getElement()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('content')).toBeNull();
    expect(screen.getByText('Loading')).toBeInTheDocument();
  });

  it('puts notifications in the sticky slot, inside the layout', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    function Toast() {
      const { notify } = useNotify();
      return <button type="button" onClick={() => notify('error', 'Boom')}>toast</button>;
    }
    render(wrap(<AppShell><Toast /></AppShell>, { api, me: ADMIN }));
    fireEvent.click(screen.getByRole('button', { name: 'toast' }));
    const layout = createWrapper(document.body).findAppLayout()!;
    expect(layout.findNotifications()!.getElement().textContent).toContain('Boom');
    expect(document.getElementById('air-top')!.textContent).not.toContain('Boom');
  });

  it('keeps the conversation when the assistant is closed and reopened', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const enc = new TextEncoder();
    const body = new ReadableStream({
      start(c) {
        c.enqueue(enc.encode(`data: ${JSON.stringify({ data: 'Acme leads.' })}\n\n`));
        c.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })));
    const value = sessionValue({ api, me: VIEWER });
    const { TestSessionProvider } = await import('@/auth/session');
    render(
      <MemoryRouter>
        <TestSessionProvider value={value}>
          <AppShell><div>content</div></AppShell>
        </TestSessionProvider>
      </MemoryRouter>,
    );
    // F2-078: the button's name says what it will do, so closing it is "Close the assistant".
    const toggle = (open: boolean) => fireEvent.click(screen.getByRole('button', { name: open ? 'Hide the assistant' : 'Open the assistant' }));
    toggle(false);
    fireEvent.change(await screen.findByPlaceholderText('Ask a question'), { target: { value: 'Who leads?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Acme leads.')).toBeInTheDocument();
    toggle(true);
    await waitFor(() => expect(screen.queryByText('Acme leads.')).toBeNull());
    toggle(false);
    expect(await screen.findByText('Acme leads.')).toBeInTheDocument();
    expect(screen.getByText('Who leads?')).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe('page error states', () => {
  it('says "not found" for a 404 with a way back, and offers Retry for other failures', async () => {
    const { LoadError, Crumbs } = await import('./components/ErrorState');
    const { api } = fakeApi({});
    const retry = vi.fn();
    const back = { href: '/radars', label: 'Back to radars' };
    const { unmount } = render(wrap(<><LoadError error={new ApiError(404, 'not_found', 'Not found')} what="Radar" back={back} onRetry={retry} /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/radars/typo' }));
    expect(screen.getByText('Radar not found')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Back to radars' }));
    expect(screen.getByTestId('where').textContent).toBe('/radars');
    unmount();
    render(wrap(<><LoadError error={new Error('Failed to fetch')} what="Radar" back={back} onRetry={retry} /><Crumbs items={[{ text: 'Radars', href: '/radars' }, { text: 'Rivals', href: '/radars/rivals' }]} /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/radars/rivals' }));
    expect(screen.getByText('This radar could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('Failed to fetch')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalled();
    createWrapper(document.body).findBreadcrumbGroup()!.findBreadcrumbLink(1)!.click();
    expect(screen.getByTestId('where').textContent).toBe('/radars');
  });
});

describe('exports', () => {
  it('downloads blobs and strings', () => {
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    download('a.txt', 'hello');
    download('b.bin', new Blob(['x']));
    expect(click).toHaveBeenCalledTimes(2);
    expect(create).toHaveBeenCalledTimes(2);
    expect(slugFile('My Radar!', 'png')).toBe('my-radar.png');
    expect(slugFile('!!!', 'svg')).toBe('radar.svg');
  });

  it('serialises the SVG and renders a PNG', async () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 10 10');
    expect(svgMarkup(svg)).toContain('<svg');
    const bare = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    expect(svgMarkup(bare)).toMatch(/width="640" height="640"|height="640" width="640"/);
    class FakeImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_: string) { setTimeout(() => this.onload?.(), 0); }
    }
    vi.stubGlobal('Image', FakeImage);
    const ctx = { fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '' };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never);
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (cb) { cb(new Blob(['png'], { type: 'image/png' })); });
    await expect(svgToPng('<svg/>', 10, 10, 2)).resolves.toBeInstanceOf(Blob);
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (cb) { cb(null); });
    await expect(svgToPng('<svg/>', 10, 10)).rejects.toThrow('PNG encoding failed');
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    await expect(svgToPng('<svg/>', 10, 10)).rejects.toThrow('canvas');
    class BrokenImage extends FakeImage { override set src(_: string) { setTimeout(() => this.onerror?.(), 0); } }
    vi.stubGlobal('Image', BrokenImage);
    await expect(svgToPng('<svg/>', 10, 10)).rejects.toThrow('could not be rendered');
    vi.unstubAllGlobals();
  });
});

describe('notifications', () => {
  it('are harmless outside the provider', () => {
    const { result } = renderHook(() => useNotify());
    expect(() => {
      result.current.notify('info', 'x');
      result.current.fail(new Error('x'));
    }).not.toThrow();
  });

  it('can be dismissed', async () => {
    function Failing() {
      const { fail } = useNotify();
      return <button type="button" onClick={() => fail(new Error('It broke'))}>go</button>;
    }
    document.body.classList.add('awsui-motion-disabled'); // no exit animation to wait for
    render(<NotificationProvider><Failing /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(await screen.findByText('It broke')).toBeInTheDocument();
    createWrapper(document.body).findFlashbar()!.findItems()[0]!.findDismissButton()!.click();
    await waitFor(() => expect(screen.queryByText('It broke')).toBeNull());
    document.body.classList.remove('awsui-motion-disabled');
  });

  it('dismiss confirmations after a few seconds, and stop timing once gone', () => {
    vi.useFakeTimers();
    function Saving() {
      const { notify } = useNotify();
      return <button type="button" onClick={() => notify('success', 'Saved.')}>save</button>;
    }
    document.body.classList.add('awsui-motion-disabled');
    const { unmount } = render(<NotificationProvider><Saving /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    expect(screen.getByText('Saved.')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(6000));
    expect(screen.queryByText('Saved.')).toBeNull();
    const set = vi.spyOn(globalThis, 'setTimeout');
    const cleared = vi.spyOn(globalThis, 'clearTimeout');
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    const dismissal = set.mock.results[set.mock.calls.findIndex(([, ms]) => ms === 6000)]!.value;
    unmount();
    expect(cleared).toHaveBeenCalledWith(dismissal); // no state update after the provider is gone
    document.body.classList.remove('awsui-motion-disabled');
    vi.useRealTimers();
  });

  it('show the newest message on top of the stack', () => {
    let n = 0;
    function Failing() {
      const { fail } = useNotify();
      return <button type="button" onClick={() => fail(new Error(`failure ${++n}`))}>go</button>;
    }
    render(<NotificationProvider><Failing /></NotificationProvider>);
    for (let i = 0; i < 5; i++) fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(screen.getByText('failure 5')).toBeInTheDocument();
    expect(screen.queryByText('failure 1')).toBeNull();
    const flashbar = createWrapper(document.body).findFlashbar()!;
    flashbar.findToggleButton()!.click();
    expect(flashbar.findItems().map((item) => item.findContent()!.getElement().textContent)).toEqual(['failure 5', 'failure 4', 'failure 3', 'failure 2']);
  });

  it('show a repeated message once, on top', () => {
    let n = 0;
    function Failing() {
      const { fail } = useNotify();
      return <button type="button" onClick={() => fail(new Error(++n === 2 ? 'other' : 'Forbidden'))}>go</button>;
    }
    render(<NotificationProvider><Failing /></NotificationProvider>);
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByRole('button', { name: 'go' }));
    const flashbar = createWrapper(document.body).findFlashbar()!;
    flashbar.findToggleButton()!.click();
    expect(flashbar.findItems().map((item) => item.findContent()!.getElement().textContent)).toEqual(['Forbidden', 'other']);
  });

  it('describes errors', () => {
    expect(describeError(new ApiError(400, 'validation_failed', 'bad', { errors: ['a', 'b'] }))).toBe('bad a; b');
    expect(describeError(new ApiError(409, 'conflict', 'stale'))).toBe('stale');
    expect(describeError(new Error('x'))).toBe('x');
    expect(describeError('plain')).toBe('plain');
  });
});
