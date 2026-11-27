// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MAX_PAGES } from '@/api/client';
import type { Me } from '@/api/types';
import Markdown, { MAX_QUOTE_DEPTH } from '@/components/Markdown';
import { suggestionsHelp } from '@/radar/RadarTabs';
import { detail, node, radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import AdminPage from './AdminPage';
import InsightDetailPage from './InsightDetailPage';
import RadarPage from './RadarPage';

/** Red-team r1 review of the blue-team fixes (code-review.md CR-xx, security-review.md SR-xx). */

afterEach(() => vi.restoreAllMocks());

const OWNER: Me = { subject: 'u-ed', email: 'ed@example.com', role: 'editor', can: ['read', 'radar.edit', 'radar.admin', 'suggestion.review'] };
const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
  </Routes>
);
const autopilot = (auto_apply_agent?: boolean) => radarView({ radar: { ...radarView().radar, mode: 'autopilot', auto_apply_agent } });
const sug = (created_at: string, entry_key = 'k1') => ({
  entry_key,
  is_new: false,
  proposal: { ring: 'Challenger' },
  base: radarView().entries[0] ?? null,
  reason: 'Lost share',
  evidence: ['fact/1'],
  created_at,
  created_by: 'module:curator',
});

describe('CR-03: autopilot and agent proposals', () => {
  it('lets an administrator turn on Apply agent proposals without review, and saves it', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': autopilot(false),
      'GET /api/radars/rivals/suggestions': { items: [] }, // read before arming (CR2-F05)
      'PATCH /api/radars/rivals': autopilot(true).radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('settings')!.click();
    const toggle = await waitFor(() => createWrapper(container).findToggle()!);
    expect(toggle.findNativeInput().getElement()).not.toBeChecked();
    expect(screen.getByText(/When off, autopilot applies none of them/)).toBeInTheDocument();
    toggle.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ mode: 'autopilot', auto_apply_agent: true }));
  });

  it('shows the setting read-only to a radar owner who is not an administrator', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': autopilot(undefined) });
    const { container } = render(wrap(radarRoutes, { api, me: OWNER, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('settings')!.click();
    const toggle = await waitFor(() => createWrapper(container).findToggle()!);
    expect(toggle.findNativeInput().getElement()).toBeDisabled();
    expect(screen.getByText(/Only an administrator can let autopilot apply/)).toBeInTheDocument();
  });

  it('says in the mode description that the toggle is needed', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': autopilot(false) });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('settings')!.click();
    // F2-061: the toggle comes first, because without it autopilot applies nothing.
    expect(await screen.findByText(/only when an administrator has also turned on Apply agent proposals without review/)).toBeInTheDocument();
  });

  it('counts the suggestions autopilot is holding for review', async () => {
    const now = Date.parse('2026-10-20T10:00:00Z');
    const items = [sug('2026-10-01T10:00:00Z'), sug('2026-10-19T10:00:00Z', 'k2')];
    expect(suggestionsHelp(autopilot(false), items, now)).toContain('1 suggestion past the 7-day delay is held.');
    expect(suggestionsHelp(autopilot(false), [...items, sug('2026-10-02T10:00:00Z', 'k3')], now)).toContain('2 suggestions past the 7-day delay are held.');
    expect(suggestionsHelp(autopilot(true), items, now)).toBe('Applied automatically after 7 days unless someone edits the entry first.');
    expect(suggestionsHelp(radarView(), items, now)).toBe('Proposals from the Radar curator. Nothing changes until you accept.');
    const { api } = fakeApi({ 'GET /api/radars/rivals': autopilot(false), 'GET /api/radars/rivals/suggestions': { items } });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.click();
    expect(await screen.findByText(/proposals from the Radar curator wait for review/)).toBeInTheDocument();
  });
});

const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

describe('CR-06: lists follow their cursor', () => {
  it('reads every page of versions, users and audit rows', async () => {
    const paged = (make: (n: number) => unknown, pages: number) => (_: unknown, url: URL) => {
      const n = Number(url.searchParams.get('cursor') ?? 0);
      return { items: [make(n)], cursor: n + 1 < pages ? String(n + 1) : null };
    };
    const version = (n: number) => ({ version: 3 - n, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' });
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/versions': paged(version, 3),
      'GET /api/users': paged((n) => ({ username: `u${n}`, email: `u${n}@example.com`, role: 'viewer', enabled: true, status: 'CONFIRMED', created_at: '' }), 2),
      'GET /api/audit': paged((n) => ({ at: `2026-10-01T10:0${n}:00Z`, actor: 'user:u-admin', email: '', action: 'radar.create', target: `r${n}` }), 2),
    });
    expect((await api.versions('rivals')).items.map((v) => v.version)).toEqual([3, 2, 1]);
    expect((await api.users()).items).toHaveLength(2);
    expect((await api.audit('2026-10-01')).items.map((a) => a.target)).toEqual(['r0', 'r1']);
    expect(calls.filter((c) => c.path === '/api/radars/rivals/versions')).toHaveLength(3);
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    expect(await screen.findByText('(3)')).toBeInTheDocument();
    expect(createWrapper(container).findTabs()!.findTabContent()!.findTable()!.findRows()).toHaveLength(3); // version 1 came from the third page
  });

  it('marks a list with more than MAX_PAGES pages as incomplete', async () => {
    const endless = (_: unknown, url: URL) => ({ items: [{ at: '2026-10-01T10:00:00Z', actor: 'a', email: '', action: 'x.y', target: url.searchParams.get('cursor') ?? '0' }], cursor: 'more' });
    const { api } = fakeApi({ 'GET /api/audit': endless, 'GET /api/users': { items: [] } });
    const out = await api.audit('2026-10-01');
    expect(out.items).toHaveLength(MAX_PAGES);
    expect(out.truncated).toBe(true);
    const { container } = render(wrap(<AdminPage />, { api, path: '/admin?tab=audit' }));
    createWrapper(container).findTabs()!.findTabLinkById('audit')!.click();
    expect(await screen.findByText(`(${MAX_PAGES}+)`)).toBeInTheDocument();
  });

  it('marks an incomplete version history', async () => {
    let n = 0;
    const version = () => ({ version: 1000 - n++, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' });
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/versions': () => ({ items: [version()], cursor: 'more' }) });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    expect(await screen.findByText(`(${MAX_PAGES}+)`)).toBeInTheDocument();
  });
});

describe('CR-07 / F2-001: the import preview describes the file, not the merged list', () => {
  it('checks the file itself, and an empty Replace needs confirming and sends allow_empty', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': (b: unknown) => {
        const body = b as { dry_run?: boolean; mode: string };
        if (!body.dry_run) return radarView().radar;
        return { rows: 0, unmatched: [], duplicate_rows: [], accepted: true, empty: true };
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    const upload = await waitFor(() => createWrapper(container).findFileUpload()!);
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['title,sector,ring\n'], 'empty.csv')] } });
    // F2-001: the dry run is always the file on its own, so its counts and row numbers are the file's.
    expect(await screen.findByText(/Merging it would leave the radar exactly as it is/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ dry_run: true, mode: 'replace' });
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    expect(await screen.findByText(/The file has no entries: importing it removes every entry/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(1); // the mode does not re-check
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(visibleModal().findHeader().getElement()).toHaveTextContent('Remove every entry?');
    expect(visibleModal().findContent().getElement()).toHaveTextContent('The file has no entries, so all 1 entry of the radar are removed.');
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ mode: 'replace', allow_empty: true }));
  });

  it('does not check again when the mode changes before a file is chosen', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    (await waitFor(() => createWrapper(container).findRadioGroup()!)).findInputByValue('replace')!.click();
    expect(calls.some((c) => c.path.endsWith('/import'))).toBe(false);
  });
});

describe('CR-08: colleagues without an id get distinct rows', () => {
  it('renders two anonymous colleagues without a duplicate key warning', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': detail({
        node: idea,
        working_on: [
          { user: '', email: 'a colleague', since: '2026-10-01T10:00:00Z' },
          { user: '', email: 'a colleague', since: '2026-10-01T10:00:00Z' },
        ],
      }),
    });
    render(wrap(<Routes><Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} /></Routes>, { api, path: '/insights/nodes/content_idea/1' }));
    expect(await screen.findByText('Working on this')).toBeInTheDocument();
    expect(screen.getAllByText('A colleague')).toHaveLength(2); // F2-090: a sentence, not a lower-case fragment
    expect(error.mock.calls.some((c) => String(c[0]).includes('same key'))).toBe(false);
  });
});

describe('SR-12: deeply nested quotes', () => {
  it('stops nesting at MAX_QUOTE_DEPTH and shows the rest as text', () => {
    const { container } = render(<Markdown markdown={'>'.repeat(50_000) + ' deep'} />);
    expect(container.querySelectorAll('blockquote')).toHaveLength(MAX_QUOTE_DEPTH + 1);
    expect(container.textContent).toContain('deep');
    const shallow = render(<Markdown markdown={'> > hi'} />);
    expect(shallow.container.querySelectorAll('blockquote')).toHaveLength(2);
  });
});
