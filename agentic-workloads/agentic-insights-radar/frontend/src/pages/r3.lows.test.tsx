// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team low findings: titles, lists, votes, drafts, limits and smaller fixes. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiClient } from '@/api/client';
import { ErrorBoundaryForTest } from '@/App';
import AppShell, { pageReload } from '@/components/AppShell';
import { NotFound } from '@/components/ErrorState';
import { entriesToCsv } from '@/radar/export';
import { pageTitle } from '@/lib/pageTitle';
import { listError } from '@/pages/RadarsPage';
import { detail, node, radarView } from '@/test/data';
import { ADMIN, CONFIG, fakeApi, sessionValue, TestRouter, wrap } from '@/test/session';
import { TestSessionProvider } from '@/auth/session';

import AdminPage from './AdminPage';
import HomePage from './HomePage';
import InsightDetailPage, { BRIEF_MAX, DRAFT_POLL_MS } from './InsightDetailPage';
import InsightsPage, { forgetLoadedPages } from './InsightsPage';
import RadarPage from './RadarPage';
import RadarsPage from './RadarsPage';

const EDITOR = { ...ADMIN, subject: 'u-ed', role: 'editor' as const, can: ['read', 'radar.create', 'radar.edit', 'feedback.give', 'content.draft', 'working.mark'] };

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  forgetLoadedPages();
});

describe('F3-046: pages with no data still name themselves', () => {
  it('titles the not-found page and the crash page', () => {
    const { api } = fakeApi({});
    const { unmount } = render(wrap(<NotFound />, { api }));
    expect(document.title).toBe('Page not found - Agentic Insights Radar');
    unmount();
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function Boom(): never {
      throw new Error('boom');
    }
    render(wrap(<ErrorBoundaryForTest><Boom /></ErrorBoundaryForTest>, { api }));
    error.mockRestore();
    expect(document.title).toBe('Something went wrong - Agentic Insights Radar');
  });
});

describe('F3-047, F3-049, F3-103: a custom list, Load more, Back and focus', () => {
  it('waits for the list name, keeps loaded pages for Back, and focuses the first new card', async () => {
    const page = (items: ReturnType<typeof node>[], cursor?: string) => ({ items, cursor: cursor ?? null });
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [{ kind: 'insight:pm', label: 'Pricing moves', module: 'pm' }] },
      'GET /api/insights/insight:pm': (_b: unknown, url: URL) =>
        url.searchParams.get('cursor') ? page([node({ id: 'insight/2', title: 'Second' })]) : page([node({ id: 'insight/1', title: 'First' })], 'c1'),
    });
    const routes = (
      <Routes>
        <Route path="/insights/:kind" element={<InsightsPage />} />
      </Routes>
    );
    const { unmount } = render(wrap(routes, { api, path: '/insights/insight:pm' }));
    expect(pageTitle.state().ready).toBe(false); // not announced as "insight:pm" while the name loads
    expect(await screen.findByText('First', {}, { timeout: 5000 })).toBeInTheDocument(); // the names, then the list
    await waitFor(() => expect(document.title).toBe('Pricing moves - Insights - Agentic Insights Radar'));
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Second')).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement?.textContent).toBe('Second'));
    expect(screen.getByText('1 more card loaded.')).toBeInTheDocument();
    unmount();
    // Back to the list: both pages are still there.
    render(wrap(routes, { api, path: '/insights/insight:pm' }));
    expect(await screen.findByText('First', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('Second')).toBeInTheDocument();
  });
});

describe('F3-050, F3-060, F3-061: the Radars list', () => {
  const row = (slug: string, owner: string, canEdit: boolean, entries = 1) => ({ ...radarView().radar, slug, name: slug.toUpperCase(), owner, entry_count: entries, can_edit: canEdit });
  it('keeps the filter and sort in the address, marks what I can edit, and blocks Create at the owner cap', async () => {
    const items = [...Array.from({ length: 25 }, (_, i) => row(`mine${i}`, 'u-ed', true)), row('other', 'u-x', false, 9)];
    const { api } = fakeApi({ 'GET /api/radars': { items } });
    const { container } = render(wrap(<RadarsPage />, { api, me: EDITOR, path: '/radars?q=other&sort=entries&dir=asc' }));
    expect(await screen.findByText('OTHER')).toBeInTheDocument();
    expect(screen.queryByText('MINE0')).toBeNull();
    expect(createWrapper(container).findTextFilter()!.findInput().getInputValue()).toBe('other');
    expect(screen.getByRole('button', { name: 'Create radar' })).toHaveAttribute('aria-disabled', 'true');
    createWrapper(container).findTextFilter()!.findInput().setInputValue('');
    expect(await screen.findAllByText('You can edit')).not.toHaveLength(0);
    const editable = () =>
      createWrapper(container).findAllToggles().find((t) => t.getElement().textContent?.includes('Only radars I can edit'))!;
    editable().findNativeInput().click();
    await waitFor(() => expect(screen.queryByText('OTHER')).toBeNull());
    editable().findNativeInput().click();
    const table = createWrapper(container).findTable()!;
    await waitFor(() => expect(table.findPagination()!.findPageNumbers()).toHaveLength(2)); // 26 radars again
    table.findColumnSortingArea(1)!.click(); // by name, ascending
    table.findColumnSortingArea(1)!.click(); // descending
    table.findPagination()!.findNextPageButton().click();
    expect(table.findPagination()!.findCurrentPage().getElement().textContent).toBe('2');
    table.findPagination()!.findPreviousPageButton().click();
    expect(table.findPagination()!.findCurrentPage().getElement().textContent).toBe('1');
  });

  it('says when the toggle leaves nothing, and marks a taken address on the field (F3-079)', async () => {
    const { api } = fakeApi({
      'GET /api/radars': { items: [row('other', 'u-x', false)] },
      'GET /api/radar-templates': { items: [{ key: 'blank', schema: radarView().schema, purpose: '' }] },
      'POST /api/radars': () => {
        throw { status: 409, code: 'conflict', message: "a radar with slug 'crm' already exists", details: { code: 'slug_taken' } };
      },
    });
    const { container } = render(wrap(<RadarsPage />, { api, me: EDITOR, path: '/radars?mine=1&page=2' }));
    expect(await screen.findByText(/You cannot edit any radar yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create radar' }));
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('Name'), { target: { value: 'CRM' } });
    await waitFor(() => expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create' })).not.toHaveAttribute('aria-disabled'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('This address is taken by another radar. Choose another one.')).toBeInTheDocument();
    expect(screen.queryByText(/slug/)).toBeNull();
    expect(container).toBeTruthy();
  });
});

describe('F3-090: value and heading limits', () => {
  it('checks 40-character values and 30-character headings', async () => {
    expect(listError('rings', `A\n${'x'.repeat(41)}`)).toMatch(/at most 40 characters a value/);
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByLabelText('Sector heading'), { target: { value: 'x'.repeat(31) } });
    expect(screen.getByText('At most 30 characters (this is 31).')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save schema' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F3-059, F3-064: Settings Delete and History across tabs', () => {
  it('disables Delete past 10 versions for an owner, and reads History once across tab switches', async () => {
    const owner = { ...EDITOR, subject: 'u-admin', can: ['read', 'radar.edit', 'radar.admin'] };
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, current_version: 40 } }),
      'GET /api/radars/rivals/versions': { items: [] },
    });
    const { container } = render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, me: owner, path: '/radars/rivals?tab=settings' }));
    expect(await screen.findByRole('button', { name: 'Delete this radar' })).toHaveAttribute('aria-disabled', 'true');
    const tabs = createWrapper(container).findTabs()!;
    for (let i = 0; i < 3; i++) {
      tabs.findTabLinkById('history')!.click();
      await screen.findByText('No versions yet.');
      tabs.findTabLinkById('view')!.click();
    }
    expect(calls.filter((c) => c.path.endsWith('/versions'))).toHaveLength(1);
  });
});

describe('F3-072, F3-073: the Overview numbers', () => {
  it('counts failures from the last 100 runs and names the modules on a schedule', async () => {
    const { api } = fakeApi({
      'GET /api/health': { genai_enabled: true, budgets: [], recent_failures: [], runs: { failed: 400 } },
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
    expect(await screen.findByText('Failed of the last 100 runs')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '400' })).toBeInTheDocument();
    expect(screen.getByText('Modules on a schedule')).toBeInTheDocument();
  });
});

describe('F3-074, F3-028: the Feedback and Health tabs', () => {
  it('shows entry titles and voter emails, and words the drafts row', async () => {
    const { api } = fakeApi({
      'GET /api/feedback': { items: [{ node_id: 'signal/1', vote: 'down', comment: 'old', at: '2026-10-01T10:00:00Z', by: 'u-pat' }] },
      'GET /api/users': { items: [{ username: 'u-pat', email: 'pat@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: 'x' }] },
      'GET /api/graph/nodes/signal/1': node({ title: 'Agent sandboxing standardises' }),
      'GET /api/health': { genai_enabled: true, budgets: [{ module: 'draft', enabled: true, consumed: 0, limit: 1, state: 'open' }, { module: 'scout', enabled: false, consumed: 0, limit: 1, state: 'open' }], recent_failures: [], runs: {} },
      'GET /api/runs': { items: [] },
    });
    const { unmount } = render(wrap(<AdminPage />, { api, path: '/admin?tab=feedback' }));
    expect(await screen.findByRole('link', { name: 'Agent sandboxing standardises' })).toBeInTheDocument();
    expect(await screen.findByText('pat@example.com')).toBeInTheDocument();
    unmount();
    render(wrap(<AdminPage />, { api, path: '/admin?tab=health' }));
    expect(await screen.findByText('Never: editors request drafts')).toBeInTheDocument();
    expect(screen.getByText('No (Run now still works)')).toBeInTheDocument();
  });

  it('words a drafts module that is off', async () => {
    const { api } = fakeApi({
      'GET /api/health': { genai_enabled: true, budgets: [{ module: 'draft', enabled: false, consumed: 0, limit: 1, state: 'open' }, { module: 'scout', enabled: true, consumed: 0, limit: 1, state: 'open' }], recent_failures: [], runs: {} },
      'GET /api/runs': { items: [] },
    });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=health' }));
    expect(await screen.findByText('Off: editors cannot request drafts')).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });
});

describe('F3-076, F3-077, F3-091, F3-100: an insight page', () => {
  const routes = (
    <Routes>
      <Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} />
    </Routes>
  );
  it('withdraws your own Useful on a click, and says which vote is yours', async () => {
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/signal/1': detail({ feedback: { up: 1, down: 0, mine: { vote: 'up', comment: '' } } }),
      'DELETE /api/nodes/signal/1/feedback': { up: 0, down: 0, mine: null },
    });
    render(wrap(routes, { api, me: EDITOR, path: '/insights/nodes/signal/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Useful (1, your vote)' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('marks your Not useful too', async () => {
    const { api } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail({ feedback: { up: 0, down: 1, mine: { vote: 'down', comment: 'weak' } } }) });
    render(wrap(routes, { api, me: EDITOR, path: '/insights/nodes/signal/1' }));
    expect(await screen.findByRole('button', { name: 'Not useful (1, your vote)' })).toBeInTheDocument();
  });

  it('goes back to the custom list, limits the brief, and polls for a draft only while visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': detail({ node: idea, feedback: undefined }),
      'GET /api/modules': { items: [] },
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r1' },
    });
    render(wrap(routes, { api, me: EDITOR, path: '/insights/nodes/content_idea/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    const dialog = within(screen.getByRole('dialog'));
    fireEvent.change(dialog.getByRole('textbox'), { target: { value: 'x'.repeat(BRIEF_MAX + 1) } });
    expect(dialog.getByText(`At most ${BRIEF_MAX} characters.`)).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Start' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(dialog.getByRole('textbox'), { target: { value: 'short' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Start' }));
    await screen.findByText('Writing a draft');
    const reads = () => calls.filter((c) => c.path === '/api/insights/nodes/content_idea/1').length;
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const before = reads();
    await act(async () => void vi.advanceTimersByTime(3 * DRAFT_POLL_MS + 10));
    expect(reads()).toBe(before);
    hidden.mockReturnValue('visible');
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')));
    await waitFor(() => expect(reads()).toBe(before + 1)); // one read on return
    hidden.mockRestore();
  });

  it('breadcrumbs a custom module insight to its list', async () => {
    const insight = node({ id: 'insight/1', type: 'insight', title: 'A move', insight_kind: 'pm' });
    const { api } = fakeApi({ 'GET /api/insights/nodes/insight/1': detail({ node: insight }), 'GET /api/nav': { builtin: [], custom: [{ kind: 'insight:pm', label: 'Pricing moves', module: 'pm' }] } });
    render(wrap(routes, { api, path: '/insights/nodes/insight/1' }));
    expect(await screen.findByRole('link', { name: 'Pricing moves' })).toHaveAttribute('href', '/insights/insight:pm');
  });
});

describe('F3-098: the rescue CSV opens in Excel', () => {
  it('starts with a byte-order mark', () => {
    expect(entriesToCsv([]).charCodeAt(0)).toBe(0xfeff);
  });
});

describe('F3-099: a new app version is said', () => {
  it('shows Reload when /api/config reports another version', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const value = sessionValue({ api, config: CONFIG });
    const { rerender } = render(
      <TestSessionProvider value={value}>
        <TestRouter path="/">
          <AppShell>
            <div>page</div>
          </AppShell>
        </TestRouter>
      </TestSessionProvider>,
    );
    expect(screen.queryByText('A new version of the app is available')).toBeNull();
    rerender(
      <TestSessionProvider value={{ ...value, config: { ...CONFIG, version: '1.1.0' } }}>
        <TestRouter path="/">
          <AppShell>
            <div>page</div>
          </AppShell>
        </TestRouter>
      </TestSessionProvider>,
    );
    expect(await screen.findByText('A new version of the app is available')).toBeInTheDocument();
    expect(screen.getByText(/Reload to use it/)).toBeInTheDocument();
    const reload = vi.spyOn(pageReload, 'now').mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalled();
    reload.mockRestore();
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => pageReload.now()).not.toThrow(); // jsdom only reports that it cannot navigate
    quiet.mockRestore();
  });
});

describe('F3-108: a profile re-read only for a role refusal, at most once a minute', () => {
  it('ignores a per-radar 403 and throttles role refusals', async () => {
    const reread = vi.fn();
    const respond = (details: Record<string, unknown>) => async () => new Response(JSON.stringify({ code: 'forbidden', message: 'no', details }), { status: 403 });
    const perRadar = new ApiClient(() => 't', respond({ versions: 40 }) as unknown as typeof fetch);
    perRadar.setForbiddenHandler(reread);
    await perRadar.radars().catch(() => undefined);
    expect(reread).not.toHaveBeenCalled();
    const role = new ApiClient(() => 't', respond({ action: 'radar.create' }) as unknown as typeof fetch);
    role.setForbiddenHandler(reread);
    await role.radars().catch(() => undefined);
    await role.radars().catch(() => undefined);
    expect(reread).toHaveBeenCalledTimes(1);
  });
});
