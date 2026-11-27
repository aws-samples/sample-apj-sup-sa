// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about the graph, the radar view, the export sheet and administration. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AuditItem } from '@/api/types';
import { ExportSheet } from '@/radar/ExportSheet';
import { node, radarView, SCHEMA } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import AdminPage, { utcDaysOf } from './AdminPage';
import GraphPage, { SEARCH_LIMIT } from './GraphPage';
import RadarPage from './RadarPage';

const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
  </Routes>
);

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.TZ;
});

describe('F3-069, F3-095: the graph as text, and a full page of results', () => {
  const org = node({ id: 'organization/1', type: 'organization', category: 'entity', title: 'Acme', degree: 9 });
  const others = Array.from({ length: 12 }, (_, i) => node({ id: `fact/${i}`, type: 'fact', category: 'evidence', title: `Fact ${i}`, degree: i }));
  const overview = { nodes: [org, ...others], edges: [{ from: 'fact/1', type: 'about', to: 'organization/1', created_by: 'u', created_at: 'x' }] };

  it('lists the links between the nodes as text (F3-069)', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': overview });
    render(wrap(<GraphPage />, { api }));
    await screen.findByTestId('graph-node-organization/1');
    expect(screen.getByText('The graph as a list: 13 nodes and 1 links')).toBeInTheDocument();
    const links = screen.getByRole('list', { name: 'Links in this picture' });
    expect(within(links).getByRole('button', { name: 'Fact 1 about Acme' })).toBeInTheDocument();
  });

  it('says a full page of search results may not be all (F3-095)', async () => {
    const hits = Array.from({ length: SEARCH_LIMIT }, (_, i) => ({ node: node({ id: `fact/${i}`, title: `Hit ${i}` }), score: 1 }));
    const { api } = fakeApi({ 'GET /api/graph/explore': { nodes: [], edges: [] }, 'POST /api/graph/search': { items: hits } });
    render(wrap(<GraphPage />, { api }));
    fireEvent.change(screen.getByLabelText('Search the graph'), { target: { value: 'common' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(`The first ${SEARCH_LIMIT} matches: refine the search or filter by type to see others.`)).toBeInTheDocument();
    expect(screen.getByText(`Results (${SEARCH_LIMIT}+)`)).toBeInTheDocument();
  });
});

describe('F3-021: on a narrow screen the legend comes first in the markup', () => {
  it('puts the legend before the chart and drops the skip link, and follows a resize', async () => {
    const width = vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(800);
    const entries = [1, 2].map((i) => ({ key: `k${i}`, title: `E${i}`, sector: 'Direct', ring: 'Leader', description: '' }));
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }) });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    const order = () => [...(container.querySelector('.air-radar-layout') as HTMLElement).children].map((c) => c.className);
    expect(order()).toEqual(['air-radar-legend', 'air-radar-chart']);
    expect(screen.queryByRole('button', { name: 'Skip the chart and go to the legend' })).toBeNull();
    width.mockReturnValue(1400);
    fireEvent(window, new Event('resize'));
    await waitFor(() => expect(order()).toEqual(['air-radar-chart', 'air-radar-legend']));
    expect(screen.getByRole('button', { name: 'Skip the chart and go to the legend' })).toBeInTheDocument();
  });
});

describe('F3-065: an empty radar says so', () => {
  it('offers Add entries and Import to editors, and says "no entries yet" to viewers', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries: [] }) });
    const { container, unmount } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    expect(await screen.findByText('No entries yet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import a file' }));
    expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement().textContent).toMatch(/Import and export/);
    createWrapper(container).findTabs()!.findTabLinkById('view')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Add entries' }));
    expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement().textContent).toMatch(/Edit entries/);
    unmount();
    const viewer = fakeApi({ 'GET /api/radars/rivals': radarView({ entries: [], can_edit: false, can_admin: false }) });
    render(wrap(radarRoutes, { api: viewer.api, path: '/radars/rivals' }));
    expect(await screen.findByText('This radar has no entries yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add entries' })).toBeNull();
  });
});

describe('F3-097: the exported sheet draws one marker key', () => {
  it('leaves the nested chart without its own key', () => {
    const schema = { ...SCHEMA, markers: [{ name: 'MVP' }, { name: 'PMF' }, { name: 'Scale' }] };
    const { container } = render(<ExportSheet schema={schema} entries={radarView().entries} title="Rivals" />);
    expect(container.querySelectorAll('.air-marker-key')).toHaveLength(0);
    expect([...container.querySelectorAll('text')].filter((t) => t.textContent === 'Momentum:')).toHaveLength(1);
  });
});

describe('F3-019: an audit day is the local day', () => {
  it('reads the one or two UTC days a local day overlaps', () => {
    expect(utcDaysOf('2026-10-08')).toEqual(['2026-10-08']); // the tests run in UTC
    process.env.TZ = 'Asia/Singapore';
    expect(utcDaysOf('2026-10-08')).toEqual(['2026-10-07', '2026-10-08']);
    process.env.TZ = 'America/Los_Angeles';
    expect(utcDaysOf('2026-10-07')).toEqual(['2026-10-07', '2026-10-08']);
  });

  it('merges both partitions, keeps only the local day and sorts newest first', async () => {
    process.env.TZ = 'Asia/Singapore';
    const row = (at: string, target: string): AuditItem => ({ at, actor: 'user:u', email: 'a@example.com', action: 'radar.create', target, details: {} });
    const local = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const today = `${local.getFullYear()}-${pad(local.getMonth() + 1)}-${pad(local.getDate())}`;
    const [first = '', second = ''] = utcDaysOf(today);
    const startUtc = new Date(new Date(local.getFullYear(), local.getMonth(), local.getDate()).getTime()).toISOString();
    const before = new Date(Date.parse(startUtc) - 60_000).toISOString(); // the evening before, locally
    const early = new Date(Date.parse(startUtc) + 60_000).toISOString(); // just after local midnight
    const later = new Date(Date.parse(startUtc) + 9 * 3_600_000).toISOString(); // the next UTC day
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/audit': (_b: unknown, url: URL) => {
        const date = url.searchParams.get('date');
        if (date === first) return { items: [row(early, 'early'), row(before, 'yesterday')] };
        if (date === second) return { items: [row(later, 'later')], cursor: null };
        return { items: [] };
      },
    });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=audit' }));
    const table = await screen.findByText('later');
    expect(table).toBeInTheDocument();
    expect(screen.getByText('early')).toBeInTheDocument();
    expect(screen.queryByText('yesterday')).toBeNull();
    const cells = [...document.querySelectorAll('td')].map((td) => td.textContent).filter((t) => t === 'early' || t === 'later');
    expect(cells).toEqual(['later', 'early']);
  });
});

describe('F3-025, F3-094: deleting a user who owns radars, and the users counter', () => {
  it('names the radars they own, with a way to their Settings, and shows no counter while loading', async () => {
    let release: () => void = () => undefined;
    const users = { items: [{ username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' }] };
    const { api } = fakeApi({
      'GET /api/users': users,
      'GET /api/radars': { items: [{ ...radarView().radar, slug: 'rivals', name: 'Rivals', owner: 'u-ed', can_edit: true }, { ...radarView().radar, slug: 'other', name: 'Other', owner: 'u-admin', can_edit: true }] },
    });
    const real = api.users;
    vi.spyOn(api, 'users').mockImplementation(() => new Promise((r) => (release = () => r(real()))));
    render(wrap(<Routes><Route path="/admin" element={<AdminPage />} /><Route path="/radars/:slug" element={<div>radar settings</div>} /></Routes>, { api, path: '/admin' }));
    expect(await screen.findByRole('heading', { name: 'Users' })).toBeInTheDocument();
    expect(screen.queryByText('(0)')).toBeNull();
    release();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete ed@example.com' }));
    const dialog = within(screen.getByRole('dialog'));
    expect(await dialog.findByText(/They own 1 radar, which would be left with no owner/)).toBeInTheDocument();
    expect(dialog.queryByText('Other')).toBeNull();
    fireEvent.click(dialog.getByRole('link', { name: 'Rivals' }));
    expect(await screen.findByText('radar settings')).toBeInTheDocument();
  });

  it('finds the radars of a federated user, whose radars name their subject', async () => {
    const users = { items: [{ username: 'SSO_ed', subject: 'sub-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'EXTERNAL_PROVIDER', created_at: '2026-10-01T10:00:00Z' }] };
    const { api } = fakeApi({
      'GET /api/users': users,
      'GET /api/radars': { items: [{ ...radarView().radar, slug: 'rivals', name: 'Rivals', owner: 'sub-ed', can_edit: true }] },
    });
    render(wrap(<AdminPage />, { api, path: '/admin' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete ed@example.com' }));
    expect(await within(screen.getByRole('dialog')).findByText(/They own 1 radar/)).toBeInTheDocument();
  });

  it('says when they own none, or when that cannot be checked', async () => {
    const users = { items: [{ username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' }] };
    let down = false;
    const { api } = fakeApi({
      'GET /api/users': users,
      'GET /api/radars': () => {
        if (down) throw { status: 500, message: 'radars are down' };
        return { items: [] };
      },
    });
    render(wrap(<AdminPage />, { api, path: '/admin' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete ed@example.com' }));
    expect(await screen.findByText('They own no radars.')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    down = true;
    fireEvent.click(await screen.findByRole('button', { name: 'Delete ed@example.com' }));
    expect(await screen.findByText('The radars they own could not be checked: radars are down')).toBeInTheDocument();
  });

  it('flags a radar whose owner no longer exists in Settings', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, owner: 'u-gone', owner_email: '' } }),
      'GET /api/users': { items: [] },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    expect(await screen.findByText('That account no longer exists: transfer the radar to someone.')).toBeInTheDocument();
  });
});
