// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { radarView, SCHEMA } from '@/test/data';
import { CONFIG, fakeApi, wrap } from '@/test/session';

const auth = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('react-oidc-context', () => ({
  useAuth: () => auth.value,
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import App, { ErrorBoundaryForTest } from './App';
import { SessionProvider, useSession } from './auth/session';
import ChatPanel from './components/ChatPanel';
import AppShell from './components/AppShell';
import NodeLink from './components/NodeLink';
import EntryEditor, { validateEntries } from './radar/EntryEditor';
import { Legend, matchingKeys, RadarChart } from './radar/RadarChart';

afterEach(() => vi.restoreAllMocks());

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

describe('SessionProvider', () => {
  it('gives pages an authenticated API and signs out through Cognito', async () => {
    const removeUser = vi.fn(async () => undefined);
    auth.value = { user: { id_token: 'tok-1', profile: { sub: 'u1' } }, removeUser, isAuthenticated: true };
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ subject: 'u1', email: 'a@example.com', role: 'viewer', can: ['read'] })));
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign, origin: 'https://app.example.com' });
    const { result } = renderHook(() => useSession(), { wrapper: ({ children }) => <SessionProvider config={CONFIG}>{children}</SessionProvider> });
    await waitFor(() => expect(result.current.me?.email).toBe('a@example.com'));
    expect(result.current.idToken()).toBe('tok-1');
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok-1');
    await act(() => result.current.signOut());
    expect(removeUser).toHaveBeenCalled();
    expect(assign).toHaveBeenCalledWith(expect.stringContaining('/logout?client_id=client'));
    vi.unstubAllGlobals();
  });

  it('keeps one API client across token renewals and sends the renewed token', async () => {
    auth.value = { user: { id_token: 'tok-1', profile: { sub: 'u1' } }, removeUser: vi.fn() };
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ subject: 'u1', email: 'a@example.com', role: 'viewer', can: [] })));
    const { result, rerender } = renderHook(() => useSession(), { wrapper: ({ children }) => <SessionProvider config={CONFIG}>{children}</SessionProvider> });
    await waitFor(() => expect(result.current.me?.email).toBe('a@example.com'));
    const first = result.current.api;
    auth.value = { user: { id_token: 'tok-2', profile: { sub: 'u1' } }, removeUser: vi.fn() };
    rerender();
    expect(result.current.api).toBe(first); // pages keyed on [api] do not refetch on renewal
    expect(result.current.idToken()).toBe('tok-2');
    await act(() => result.current.api.nav());
    const init = fetcher.mock.calls.at(-1)?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok-2');
  });

  it('re-reads the configuration so the AI-paused banner follows the kill switch', async () => {
    auth.value = { user: { id_token: 'tok-1', profile: { sub: 'u1' } }, removeUser: vi.fn() };
    let genai = true;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/config') return new Response(JSON.stringify({ ...CONFIG, genai_enabled: genai }));
      if (path === '/api/nav') return new Response(JSON.stringify({ builtin: [], custom: [] }));
      if (path === '/api/me') return new Response(JSON.stringify({ subject: 'u1', email: 'a@example.com', role: 'admin', can: [] }));
      return new Response('{}', { status: 500 });
    });
    function Flip() {
      const { refreshConfig } = useSession();
      return <button type="button" onClick={refreshConfig}>refresh</button>;
    }
    render(<SessionProvider config={CONFIG}><MemoryRouter><AppShell><Flip /></AppShell></MemoryRouter></SessionProvider>);
    expect(screen.queryByText('AI features are paused')).toBeNull();
    genai = false;
    fireEvent.click(await screen.findByRole('button', { name: 'refresh' })); // the page waits for the profile
    expect(await screen.findByText('AI features are paused')).toBeInTheDocument();
    genai = true;
    fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
    await waitFor(() => expect(screen.queryByText('AI features are paused')).toBeNull());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 503 }));
    fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
    await act(async () => undefined); // a failed re-read keeps the configuration it has
    expect(screen.queryByText('AI features are paused')).toBeNull();
    createWrapper(document.body).findTopNavigation()!.findUtility(1)!.findButtonLinkType()!.click();
    expect(await screen.findByPlaceholderText('Ask a question')).toBeInTheDocument();
    createWrapper(document.body).findAppLayout()!.findToolsClose()!.click();
  });

  it('has no profile before sign-in and refuses use outside the provider', async () => {
    auth.value = { user: null, removeUser: vi.fn() };
    const { result } = renderHook(() => useSession(), { wrapper: ({ children }) => <SessionProvider config={CONFIG}>{children}</SessionProvider> });
    await waitFor(() => expect(result.current.me).toBeNull());
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => renderHook(() => useSession())).toThrow('useSession outside');
  });
});

describe('App', () => {
  it('renders the router and recovers from page crashes', () => {
    auth.value = { isLoading: true };
    render(<App />);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function Boom(): React.ReactElement {
      throw new Error('kaput');
    }
    auth.value = { isLoading: false, isAuthenticated: true };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    render(wrap(<ErrorBoundaryForTest><Boom /></ErrorBoundaryForTest>, { api }));
    // Friendly text first; the raw message only under Technical details, with a way out.
    expect(screen.getByText(/stopped working because of a problem/)).toBeInTheDocument();
    expect(screen.getByText('kaput')).toBeInTheDocument();
    expect(screen.getByText('Technical details')).toBeInTheDocument();
    // F2-087: a button that navigates inside the app, not a link that reloads and loses the conversation.
    expect(screen.getByRole('button', { name: 'Go to Overview' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe('navigation', () => {
  it('follows side navigation and the logo inside the app', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppShell><Routes><Route path="*" element={<Where />} /></Routes></AppShell>, { api, path: '/radars' }));
    const nav = createWrapper(document.body).findSideNavigation()!;
    nav.findLinkByHref('/graph')!.click();
    expect(screen.getByTestId('where').textContent).toBe('/graph');
    createWrapper(document.body).findTopNavigation()!.findIdentityLink()!.click();
    expect(screen.getByTestId('where').textContent).toBe('/');
    createWrapper(document.body).findAppLayout()!.findNavigationClose()!.click();
  });

  it('links nodes into the app', () => {
    render(<MemoryRouter><Routes><Route path="/" element={<NodeLink node={{ id: 'gap/1', title: 'Gap one', evidence_url: 'javascript:x' }} />} /><Route path="*" element={<Where />} /></Routes></MemoryRouter>);
    expect(screen.queryByText('source')).toBeNull();
    fireEvent.click(screen.getByText('Gap one'));
    expect(screen.getByTestId('where').textContent).toBe('/insights/nodes/gap/1');
  });
});

/** EntryEditor with its draft rows held in state, as RadarPage holds them. */
function Editor(props: Omit<React.ComponentProps<typeof EntryEditor>, 'rows' | 'onRowsChange'>) {
  const [rows, setRows] = React.useState(props.entries);
  return <EntryEditor {...props} rows={rows} onRowsChange={setRows} />;
}

describe('EntryEditor', () => {
  it('validates, edits selects, removes and discards', () => {
    const onSave = vi.fn();
    const entries = radarView().entries;
    const { container } = render(<Editor schema={SCHEMA} entries={entries} saving={false} onSave={onSave} />);
    const w = createWrapper(container);
    const selects = w.findAllSelects();
    selects[0]!.openDropdown();
    selects[0]!.selectOptionByValue('Adjacent', { expandToViewport: true });
    selects[1]!.openDropdown();
    selects[1]!.selectOptionByValue('Challenger', { expandToViewport: true });
    selects[2]!.openDropdown();
    selects[2]!.selectOptionByValue('', { expandToViewport: true });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(onSave.mock.calls[0]![0][0]).toMatchObject({ sector: 'Adjacent', ring: 'Challenger', marker: undefined });
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    w.findTable()!.findRowSelectionArea(1)!.click();
    // F2-032: the button says how many rows it would take.
    fireEvent.click(screen.getByRole('button', { name: 'Remove 1' }));
    expect(screen.getByText(/No entries/)).toBeInTheDocument();
  });

  it('reports every kind of problem', () => {
    const problems = validateEntries(SCHEMA, [
      { key: 'a', title: '', sector: 'Nope', ring: 'Nope', marker: 'Nope', description: '' },
    ]);
    // F2-031: a problem names its entry; an untitled row keeps its position.
    expect(problems).toEqual([
      'Row 1 (no title yet): a title is required',
      'Row 1 (no title yet): choose a category',
      'Row 1 (no title yet): choose a position',
      'Row 1 (no title yet): unknown momentum',
    ]);
  });

  it('adds blank entries to a radar with no sectors or rings yet', () => {
    const { container } = render(<Editor schema={{ ...SCHEMA, sectors: [], rings: [] }} entries={[]} saving={false} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    const [sector, ring] = createWrapper(container).findAllSelects();
    // Nothing is chosen for the user: the cells ask, and are marked until a value is picked.
    expect(sector!.findTrigger().getElement()).toHaveTextContent('Choose');
    expect(ring!.findTrigger().getElement()).toHaveTextContent('Choose');
    expect(screen.getByRole('button', { name: 'Save version' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('renders schemas without markers', () => {
    render(<Editor schema={{ ...SCHEMA, markers: [] }} entries={[]} saving onSave={vi.fn()} />);
    expect(screen.queryByText('Momentum')).toBeNull();
  });
});

describe('tolerant schema matching', () => {
  const schema = { ...SCHEMA, sectors: [{ name: 'Builders\u2019 tools' }], rings: [{ name: 'Leader' }], markers: [{ name: 'Gaining' }] };
  const entries = [{ key: 'a', title: 'Acme', sector: "builders' tools ", ring: '\uff2ceader', marker: 'gaining', description: '' }];

  it('lists in the legend every entry the chart draws', () => {
    const { container } = render(<RadarChart schema={schema} entries={entries} />);
    expect(container.querySelectorAll('g[role="button"]')).toHaveLength(1);
    const legend = render(<Legend schema={schema} entries={entries} />).container;
    expect(legend.querySelectorAll('.air-legend-item')).toHaveLength(1);
    expect(legend.textContent).not.toContain('Not shown');
  });

  it('accepts in the editor what the chart accepts', () => {
    expect(validateEntries(schema, entries)).toEqual([]);
    expect(validateEntries(schema, [...entries, { ...entries[0]!, key: 'b', title: 'ACME ' }])).toEqual(['ACME: "ACME " appears twice']);
  });
});

describe('RadarChart', () => {
  it('leaves room beside the circle for sector names at the diagonals', () => {
    const { container } = render(<RadarChart schema={SCHEMA} entries={[]} size={640} />);
    const [x, y, w, h] = container.querySelector('svg')!.getAttribute('viewBox')!.split(' ').map(Number);
    // The circle has radius 280; a 22-character name at 13 px reaches about 70 px past it at the diagonal.
    expect([x, y, h]).toEqual([-420, -320, 640]);
    expect(w).toBe(840);
  });

  it('wraps long segment names onto lines and keeps the full name as a tooltip', () => {
    const schema = { ...SCHEMA, sectors: [{ name: 'Materials and components for desks' }, { name: 'Short' }] };
    const { container } = render(<RadarChart schema={schema} entries={[]} />);
    const labels = [...container.querySelectorAll('text.air-radar-segment-label')];
    expect(labels.map((t) => [...t.querySelectorAll('tspan')].map((s) => s.textContent))).toEqual([
      ['Materials and', 'components for', 'desks'],
      ['Short'],
    ]);
    expect(labels[0]!.querySelector('title')!.textContent).toBe('Materials and components for desks');
  });

  it('draws an empty radar with no rings at the default radius', () => {
    const { container } = render(<RadarChart schema={{ ...SCHEMA, rings: [] }} entries={[]} />);
    expect(Number(container.querySelector('circle.air-radar-disc')!.getAttribute('r'))).toBeGreaterThan(0);
  });

  it('draws marker arcs, dims entries outside the filter and picks a readable number colour', () => {
    const entries = [
      { key: 'a', title: 'A', sector: 'Direct', ring: 'Leader', marker: 'Gaining', description: '' },
      { key: 'b', title: 'B', sector: 'Direct', ring: 'Leader', description: '' },
    ];
    const schema = { ...SCHEMA, markers: [{ name: 'Gaining' }, { name: 'Losing' }], sectors: [{ name: 'Direct', color: '#f5f5f5' }] };
    const { container } = render(<RadarChart schema={schema} entries={entries} visible={new Set(['a'])} />);
    const blips = [...container.querySelectorAll('svg > g[opacity]')];
    expect(blips.map((g) => g.getAttribute('opacity'))).toEqual(['1', '0.2']);
    expect(blips.map((g) => g.getAttribute('role'))).toEqual(['button', null]); // a filtered-out blip leaves the tab order
    expect(blips[1]).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelectorAll('path.air-blip-marker')).toHaveLength(1);
    expect(blips[0]!.querySelector('text')!.getAttribute('fill')).toBe('#000000'); // dark number on a light disc
    expect(container.querySelectorAll('line.air-radar-line')).toHaveLength(0); // one segment: no dividers
  });

  it('selects with the keyboard and shortens long tooltips', () => {
    const onSelect = vi.fn();
    const entries = [
      { key: 'a', title: 'A very long entry title that needs shortening', sector: 'Direct', ring: 'Leader', description: '' },
      { key: 'b', title: 'B', sector: 'Adjacent', ring: 'Challenger', marker: 'Gaining', description: '' },
    ];
    const schema = { ...SCHEMA, markers: [{ name: 'Gaining', glyph: 'ring' }], sectors: [{ name: 'Direct' }, { name: 'Adjacent' }, { name: 'Third' }] };
    const { container } = render(<RadarChart schema={schema} entries={entries} onSelect={onSelect} selected="a" />);
    const blips = container.querySelectorAll('g[role="button"]');
    fireEvent.focus(blips[0]!);
    expect(container.textContent).toContain('A very long entry title t');
    fireEvent.blur(blips[0]!);
    fireEvent.keyDown(blips[1]!, { key: ' ' });
    fireEvent.keyDown(blips[1]!, { key: 'x' });
    fireEvent.click(container.querySelector('svg')!);
    expect(onSelect.mock.calls.map((c) => c[0]?.key ?? null)).toEqual(['b', null]);
  });
});

describe('ChatPanel failures', () => {
  it('shows runtime errors and network failures, and starts over', async () => {
    const { api } = fakeApi({});
    const errorStream = vi.fn(async () => {
      const body = new ReadableStream({ start: (c) => { c.enqueue(new TextEncoder().encode('data: {"error":"budget_exhausted","message":"Budget used up"}\n\n')); c.close(); } });
      return new Response(body);
    });
    const { unmount } = render(wrap(<ChatPanel fetcher={errorStream as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Budget used up')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start new' }));
    expect(screen.queryByText('Budget used up')).toBeNull();
    unmount();
    const offline = vi.fn(async () => { throw new TypeError('Failed to fetch'); });
    render(wrap(<ChatPanel fetcher={offline as unknown as typeof fetch} />, { api }));
    fireEvent.change(screen.getByPlaceholderText('Ask a question'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText(/could not be reached/)).toBeInTheDocument();
  });
});

describe('sub-sectors', () => {
  const entries = [
    { key: 'a', title: 'Zeta', sector: 'Direct', ring: 'Leader', description: '' },
    { key: 'b', title: 'Alpha', sector: 'Direct', ring: 'Leader', description: '', sub_sector: 'Suites' },
    { key: 'c', title: 'Beta', sector: 'Direct', ring: 'Challenger', description: '', sub_sector: 'suites' },
  ];

  it('groups the legend by sub-sector and numbers entries in that order', () => {
    const { container } = render(<Legend schema={SCHEMA} entries={entries} />);
    expect(container.querySelector('h3.air-legend-sub')).toHaveTextContent('Suites');
    expect(screen.getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['1. Zeta, Leader', '2. Alpha, Leader', '3. Beta, Challenger']);
    expect(container.querySelectorAll('h3.air-legend-sub')).toHaveLength(1); // 'Suites' and 'suites' are one group
    expect(matchingKeys(entries, 'SUITES')).toEqual(new Set(['b', 'c']));
  });

  it('shows the sub-sector in the tooltip', () => {
    const { container } = render(<RadarChart schema={SCHEMA} entries={entries} />);
    fireEvent.mouseEnter(container.querySelector('g[aria-label^="2. Alpha"]')!);
    expect(container).toHaveTextContent('Direct / Suites / Leader');
  });

  it('edits the sub-sector in the editor and clears it', () => {
    const onSave = vi.fn();
    const { container } = render(<Editor schema={SCHEMA} entries={radarView().entries} saving={false} onSave={onSave} />);
    const input = createWrapper(container).findAllInputs().find((i) => i.findNativeInput().getElement().getAttribute('aria-label') ?.startsWith('Sub-category of ') ?? false)!;
    input.setInputValue('Suites');
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(onSave.mock.calls[0]![0][0]).toMatchObject({ sub_sector: 'Suites' });
    input.setInputValue('');
    expect(screen.getByRole('button', { name: 'Save version' })).toHaveAttribute('aria-disabled', 'true'); // cleared = unchanged again
  });
});

describe('entry links', () => {
  it('edits an entry link and asks for a full https address', () => {
    const onSave = vi.fn();
    const { container } = render(<Editor schema={SCHEMA} entries={radarView().entries} saving={false} onSave={onSave} />);
    const link = createWrapper(container).findAllInputs().find((i) => i.findNativeInput().getElement().getAttribute('aria-label') ?.startsWith('Link of ') ?? false)!;
    link.setInputValue('acme.example.com');
    expect(screen.getByText('Acme: the link must be a full https:// web address')).toBeInTheDocument();
    link.setInputValue('https://acme.example.com/about');
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(onSave.mock.calls[0]![0][0]).toMatchObject({ link: 'https://acme.example.com/about' });
    link.setInputValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(onSave.mock.calls[1]![0][0].link).toBeUndefined();
  });
});
