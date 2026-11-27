// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** The round-2 polish findings: wording, labels, states and small behaviours. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundaryForTest } from '@/App';
import { MAX_PAGES } from '@/api/client';
import AppShell from '@/components/AppShell';
import GettingStarted from '@/components/GettingStarted';
import { fieldLabels, versionNote } from '@/radar/RadarTabs';
import { module as module_, radarView, SCHEMA } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import AdminPage, { shiftDate, userStatus } from './AdminPage';
import HomePage from './HomePage';
import InsightsPage from './InsightsPage';
import RadarPage from './RadarPage';
import RadarsPage from './RadarsPage';

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear(); // the Getting started "Hide" choice is remembered per browser
});

const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);

describe('F2-045: version notes read as sentences', () => {
  it('maps the backend codes and leaves anything a person wrote alone', () => {
    expect(versionNote('saved', false)).toBe('Saved by hand');
    expect(versionNote('imported csv (merge)', false)).toBe('Imported a CSV file (merge)');
    expect(versionNote('imported json (replace)', false)).toBe('Imported a JSON file (replace)');
    expect(versionNote('schema changed (3 entries dropped)', true)).toBe('Schema changed (3 entries removed)');
    expect(versionNote('schema changed (1 entries dropped)', true)).toBe('Schema changed (1 entry removed)');
    expect(versionNote('rolled back to v12', false)).toBe('Restored version 12');
    expect(versionNote('schema changed', true)).toBe('Schema changed');
    expect(versionNote('', true)).toBe('Schema changed');
    expect(versionNote('', false)).toBe('-');
    expect(versionNote('Quarterly review', false)).toBe('Quarterly review');
  });
});

describe('F2-046: a diff names its fields in the radar\'s own words', () => {
  it('uses the headings, and falls back for anything unknown', () => {
    expect(fieldLabels(['sub_sector', 'link'], SCHEMA)).toBe('sub-category, link');
    expect(fieldLabels(['ring', 'marker', 'title'], SCHEMA)).toBe('position, momentum, title');
    expect(fieldLabels(['node_id', 'something_else'], SCHEMA)).toBe('linked record, something else');
  });
});

describe('F2-054: a viewer is told who reviews suggestions', () => {
  it('says editors do, instead of "until you accept"', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ can_edit: false, can_admin: false }),
      'GET /api/radars/rivals/suggestions': { items: [] },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions', me: VIEWER }));
    expect(await screen.findByText(/Editors of this radar accept or discard them/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing changes until you accept/)).toBeNull();
  });
});

describe('F2-055, F2-056, F2-057, F2-060: the Settings sections', () => {
  it('refuses a schema save that would change nothing', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    const rings = await waitFor(() => screen.getByLabelText('Position'));
    fireEvent.change(rings, { target: { value: 'Leader\nChallenger\n\n  ' } }); // trailing blanks only
    const save = screen.getByRole('button', { name: 'Save schema' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(rings, { target: { value: 'Leader\nChallenger\nNiche' } });
    expect(screen.getByRole('button', { name: 'Save schema' })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('counts the name and purpose against the limits the API enforces', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    const purpose = await waitFor(() => screen.getByDisplayValue('Who competes with us'));
    expect(screen.getByText('20 of 300 characters.')).toBeInTheDocument();
    fireEvent.change(purpose, { target: { value: 'x'.repeat(301) } });
    expect(screen.getByText('At most 300 characters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save general' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(purpose, { target: { value: 'Shorter' } });
    fireEvent.change(screen.getByDisplayValue('Rivals'), { target: { value: 'y'.repeat(121) } });
    expect(screen.getByText('At most 120 characters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save general' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('counts an editor typed but not added as unsaved', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    const email = await waitFor(() => screen.getByLabelText('Editor email'));
    fireEvent.change(email, { target: { value: 'pat@example.com' } });
    const warn = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(warn);
    expect(warn.defaultPrevented).toBe(true);
  });

  it('holds the other Save buttons while one section is saving', async () => {
    const { api, fetcher } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PATCH /api/radars/rivals': radarView().radar,
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    const name = await waitFor(() => screen.getByDisplayValue('Rivals'));
    fireEvent.change(name, { target: { value: 'Competitors' } });
    fireEvent.change(screen.getByLabelText('Editor email'), { target: { value: 'pat@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await held;
      return real(...args);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save access' })).toHaveAttribute('aria-disabled', 'true'));
    release();
  });
});

describe('F2-061 and F2-093: the Radars list', () => {
  it('says an autopilot radar is held, and wraps a long purpose', async () => {
    const long = 'A long purpose that would once have been cut off mid-sentence with no way to read the rest of it at all.';
    const held = { ...radarView().radar, can_edit: true, mode: 'autopilot' as const, auto_apply_agent: false, purpose: long };
    const armed = { ...held, slug: 'armed', name: 'Armed', auto_apply_agent: true };
    const { api } = fakeApi({ 'GET /api/radars': { items: [held, armed] } });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars' }));
    expect(await screen.findByText('Autopilot (held for review)')).toBeInTheDocument();
    expect(screen.getByText('Autopilot')).toBeInTheDocument();
    expect(screen.getAllByText(long)).toHaveLength(2); // wrapped, not clipped
  });
});

describe('F2-088: the account menu says which build this is', () => {
  it('shows the version', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppShell><div>page</div></AppShell>, { api }));
    const menu = createWrapper(document.body).findTopNavigation()!.findUtility(2)!.findMenuDropdownType()!;
    menu.openDropdown();
    expect(await screen.findByText('Version 1.0.0')).toBeInTheDocument();
  });
});

describe('F2-095, F2-096, F2-102: the Users and Audit tabs', () => {
  it('words every Cognito status', () => {
    expect(userStatus('CONFIRMED')).toBe('Active');
    expect(userStatus('RESET_REQUIRED')).toBe('Password reset required');
    expect(userStatus('SOMETHING_NEW')).toBe('Something new');
  });

  it('steps a day at a time and never into the future', async () => {
    expect(shiftDate('2026-03-02', -1)).toBe('2026-03-01');
    expect(shiftDate('2026-03-01', -1)).toBe('2026-02-28');
    expect(shiftDate('2026-12-31', 1)).toBe('2027-01-01');
    const { api, calls } = fakeApi({ 'GET /api/audit': { items: [] }, 'GET /api/users': { items: [] } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=audit' }));
    expect(await screen.findByText('No activity that day.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'The day after' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'The day before' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'The day after' })).not.toBeDisabled());
    expect(calls.filter((c) => c.path === '/api/audit').length).toBeGreaterThan(1);
    fireEvent.click(screen.getByRole('button', { name: 'The day after' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'The day after' })).toBeDisabled());
    // The calendar itself offers no day after today either.
    createWrapper(document.body).findDatePicker()!.findOpenCalendarButton().click();
    const grid = await waitFor(() => createWrapper(document.body).findDatePicker()!.findCalendar()!.getElement());
    expect(grid.querySelectorAll('[aria-disabled="true"]').length).toBeGreaterThan(0);
  });

  it('shows one Enable at a time and can be tried again after a failure', async () => {
    const users = {
      items: [
        { username: 'u-admin', email: 'admin@example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '' },
        { username: 'u2', email: 'two@example.com', role: 'viewer', enabled: false, status: 'CONFIRMED', created_at: '' },
      ],
    };
    let hits = 0;
    const { api, calls } = fakeApi({
      'GET /api/users': () => {
        hits += 1;
        if (hits === 1) throw { status: 500, message: 'user store broken' };
        return users;
      },
      'PATCH /api/users/u2': users.items[1],
    });
    render(wrap(<AdminPage />, { api, path: '/admin' }));
    expect(await screen.findByText(/The users could not be loaded: user store broken/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Enable' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
  });
});

describe('F2-103, F2-104, F2-105, F2-106: Getting started', () => {
  const setup = (over: Record<string, unknown> = {}) =>
    fakeApi({
      'GET /api/products': { items: [{ id: 'p1', name: 'DeskPro' }] },
      'GET /api/modules': { items: [module_({ slug: 'scout', enabled: true })] },
      'GET /api/radars': { items: [radarView().radar] },
      'GET /api/graph/explore': { nodes: [{ id: 'signal/1' }], edges: [] },
      'GET /api/modules/scout/sources': { items: [{ id: 's1', name: 'Blog', type: 'rss', enabled: true }] },
      ...over,
    });

  it('ticks the module step only when Scout has a source', async () => {
    const { api } = setup({ 'GET /api/modules/scout/sources': { items: [] } });
    render(wrap(<GettingStarted />, { api }));
    expect(await screen.findByText(/3 of 4 done\./)).toBeInTheDocument();
  });

  it('collapses to one line when everything is done', async () => {
    const { api } = setup();
    render(wrap(<GettingStarted />, { api }));
    expect(await screen.findByText('Setup is done.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide getting started' }));
    expect(await screen.findByRole('button', { name: 'Show getting started' })).toBeInTheDocument();
  });

  it('says a check could not be made, rather than calling it not done', async () => {
    const { api } = setup({
      'GET /api/products': () => {
        throw { status: 500, message: 'products broken' };
      },
    });
    render(wrap(<GettingStarted />, { api }));
    expect(await screen.findByText('This step could not be checked, so it may already be done.')).toBeInTheDocument();
    expect(screen.queryByText(/of 4 done/)).toBeNull(); // the count is left out while a check failed
  });

  it('survives blocked site data', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('site data is blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('site data is blocked');
    });
    const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('site data is blocked');
    });
    const { api } = setup({ 'GET /api/modules/scout/sources': { items: [] } });
    render(wrap(<GettingStarted />, { api }));
    expect(await screen.findByText('Getting started')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    expect(await screen.findByRole('button', { name: 'Show getting started' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show getting started' }));
    expect(await screen.findByText('Getting started')).toBeInTheDocument();
    get.mockRestore();
    set.mockRestore();
    remove.mockRestore();
  });

  it('asks for nothing when the reader cannot follow the steps', async () => {
    const { api, calls } = setup();
    render(wrap(<GettingStarted />, { api, me: VIEWER }));
    expect(await screen.findByText(/are what the AI modules found/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/api/products')).toBe(false);
  });
});

describe('F2-087: the error boundary stays in the app', () => {
  it('goes to the Overview without reloading, which would lose the conversation', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(wrap(<ErrorBoundaryForTest><Boom /></ErrorBoundaryForTest>, { api, path: '/radars/x' }));
    error.mockRestore();
    fireEvent.click(screen.getByRole('button', { name: 'Go to Overview' }));
    expect(reload).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

function Boom(): never {
  throw new Error('kaput');
}

describe('F2-044: unmatched import rows keep the API field name when the radar has no heading for it', () => {
  it('falls back to the field the API named', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': { rows: 1, unmatched: [{ row: 1, field: 'title', value: '', suggestion: null }], duplicate_rows: [], accepted: false },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
    const upload = createWrapper(container).findFileUpload()!;
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['a'], 'r.csv')] } });
    expect(await screen.findByText('Row 1: "" is not a title')).toBeInTheDocument();
  });
});

describe('F2-119: feedback in one place', () => {
  const vote = (over: Record<string, unknown> = {}) => ({
    node_id: 'signal/1',
    vote: 'down' as const,
    comment: 'The source is a press release',
    at: '2026-10-02T10:00:00Z',
    by: 'pat@example.com',
    ...over,
  });

  it('lists the votes and comments for a kind, and follows the entry', async () => {
    const { api, calls } = fakeApi({
      'GET /api/feedback': (_: unknown, url: URL) =>
        url.searchParams.get('type') === 'gap'
          ? { items: [vote({ node_id: 'gap/9', vote: 'up', comment: '' })], cursor: null }
          : { items: [vote(), vote({ by: 'sam@example.com', vote: 'up', comment: '' })], cursor: null },
    });
    render(
      wrap(
        <Routes>
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/insights/nodes/:type/:uid" element={<div>the entry</div>} />
        </Routes>,
        { api, path: '/admin?tab=feedback' },
      ),
    );
    expect(await screen.findByText('The source is a press release')).toBeInTheDocument();
    expect(screen.getByText('Not useful')).toBeInTheDocument();
    expect(screen.getByText(/1 vote of these is "Not useful"/)).toBeInTheDocument();
    const kind = createWrapper(document.body).findAllSelects().pop()!;
    kind.openDropdown();
    kind.selectOptionByValue('gap', { expandToViewport: true });
    await waitFor(() => expect(screen.getByText('gap/9')).toBeInTheDocument());
    expect(calls.filter((c) => c.path === '/api/feedback')).toHaveLength(2);
    fireEvent.click(screen.getByRole('link', { name: 'gap/9' }));
    expect(await screen.findByText('the entry')).toBeInTheDocument();
  });

  it('pages a long list, and says "are" for more than one', async () => {
    const many = Array.from({ length: 30 }, (_, i) => vote({ node_id: `signal/${i}`, by: `p${i}@example.com` }));
    const { api } = fakeApi({ 'GET /api/feedback': { items: many, cursor: null } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=feedback' }));
    expect(await screen.findByText(/30 votes of these are "Not useful"/)).toBeInTheDocument();
    expect(screen.getByText('signal/0')).toBeInTheDocument();
    expect(screen.queryByText('signal/26')).toBeNull();
    createWrapper(document.body).findPagination()!.findNextPageButton().click();
    await waitFor(() => expect(screen.getByText('signal/26')).toBeInTheDocument());
  });

  it('marks a capped list', async () => {
    let n = 0;
    const { api } = fakeApi({ 'GET /api/feedback': () => ({ items: [vote({ node_id: `signal/${n++}` })], cursor: 'more' }) });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=feedback' }));
    expect(await screen.findByText(`(${MAX_PAGES}+)`)).toBeInTheDocument();
  });

  it('says so when there is none, and offers Retry when it fails', async () => {
    const empty = fakeApi({ 'GET /api/feedback': { items: [], cursor: null } });
    render(wrap(<AdminPage />, { api: empty.api, path: '/admin?tab=feedback' }));
    expect(await screen.findByText('No votes on this kind of entry yet.')).toBeInTheDocument();
    let hits = 0;
    const broken = fakeApi({
      'GET /api/feedback': () => {
        hits += 1;
        if (hits === 1) throw { status: 500, message: 'feedback store broken' };
        return { items: [vote()], cursor: null };
      },
    });
    render(wrap(<AdminPage />, { api: broken.api, path: '/admin?tab=feedback' }));
    expect(await screen.findByText(/The feedback could not be loaded: feedback store broken/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('The source is a press release')).toBeInTheDocument();
  });
});

describe('the round-2 fixes whose only sign is in the markup', () => {
  it('F2-086: /insights without a kind opens the signals list', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': { items: [] } });
    render(
      wrap(
        <Routes>
          <Route path="/insights" element={<Navigate to="/insights/signals" replace />} />
          <Route path="/insights/:kind" element={<InsightsPage />} />
        </Routes>,
        { api, path: '/insights' },
      ),
    );
    expect(await screen.findByRole('heading', { level: 1, name: /Signals/ })).toBeInTheDocument();
  });

  it('F2-091: the address can be cleared and retyped without the name snapping back', async () => {
    const { api } = fakeApi({
      'GET /api/radars': { items: [] },
      'GET /api/radar-templates': { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }] },
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars?create=1' }));
    fireEvent.change(await waitFor(() => screen.getByLabelText('Name')), { target: { value: 'Mid-market CRM' } });
    const address = screen.getByLabelText('Address');
    expect(address).toHaveValue('mid-market-crm');
    fireEvent.change(address, { target: { value: '' } });
    expect(address).toHaveValue(''); // it used to snap straight back to the name
    fireEvent.change(address, { target: { value: 'crm' } });
    expect(address).toHaveValue('crm');
  });

  it('F2-094: Create radar defaults to a template the API offers', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars': { items: [] },
      // The backend renamed competitor_landscape away: the first one offered is the default.
      'GET /api/radar-templates': { items: [{ key: 'rivals_2026', purpose: 'Rivals', schema: SCHEMA }] },
      'POST /api/radars': (b: unknown) => ({ ...radarView().radar, ...(b as object) }),
    });
    render(
      wrap(
        <Routes>
          <Route path="/radars" element={<RadarsPage />} />
          <Route path="/radars/:slug" element={<div>opened</div>} />
        </Routes>,
        { api, path: '/radars?create=1' },
      ),
    );
    fireEvent.change(await waitFor(() => screen.getByLabelText('Name')), { target: { value: 'Rivals' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create' })).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ template: 'rivals_2026' }));
  });

  it('F2-098 and F2-101: a capped audit day says so, and identical rows get distinct keys', async () => {
    const at = '2026-10-01T10:00:00Z';
    const row = { at, actor: 'module:signal', email: '', action: 'node.create', target: 'signal/1' };
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { api } = fakeApi({ 'GET /api/audit': () => ({ items: [row, row], cursor: 'more' }), 'GET /api/users': { items: [] } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=audit' }));
    expect(await screen.findByText(`(${MAX_PAGES * 2}+)`)).toBeInTheDocument();
    expect(screen.getByText(/the filter searches only these, and older rows are not loaded/)).toBeInTheDocument();
    expect(error.mock.calls.flat().join(' ')).not.toContain('same key');
    error.mockRestore();
  });

  it('F2-107: the Overview counts the modules\' tokens, and says so', async () => {
    const { api } = fakeApi({
      'GET /api/health': {
        genai_enabled: true,
        budgets: [{ module: 'scout', enabled: true, consumed: 1200, limit: 10_000, state: 'open' }],
        recent_failures: [],
        runs: {},
      },
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': { items: [] },
      'GET /api/insights/trends': { items: [] },
      'GET /api/insights/gaps': { items: [] },
      'GET /api/insights/content': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules': { items: [] },
      'GET /api/radars': { items: [] },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    render(wrap(<HomePage />, { api }));
    expect(await screen.findByText('Module tokens today')).toBeInTheDocument();
    expect(screen.getByText('1.2k')).toBeInTheDocument();
    expect(screen.queryByText('Tokens used today')).toBeNull();
  });
});
