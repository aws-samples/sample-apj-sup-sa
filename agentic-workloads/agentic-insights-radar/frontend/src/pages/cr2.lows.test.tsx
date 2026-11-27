// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 frontend review, the low findings outside the radar editor: CR2-F12..F18 and SR2-F01. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from '@/components/AppShell';
import ChatPanel from '@/components/ChatPanel';
import Markdown from '@/components/Markdown';
import { APP_TITLE } from '@/lib/pageTitle';
import { module as module_, node, product, radarView } from '@/test/data';
import { ADMIN, chooseMoreAction, fakeApi, wrap } from '@/test/session';

import AdminPage from './AdminPage';
import GraphPage from './GraphPage';
import { ModulePage } from './ModulesPage';
import { PRODUCT_POLL_MS, PRODUCT_POLL_TRIES, ProductPage } from './ProductsPage';
import RadarPage from './RadarPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('CR2-F12: a failed answer keeps what was typed meanwhile', () => {
  it('restores the question only into an empty input', async () => {
    const { api } = fakeApi({});
    const rejects: ((e: Error) => void)[] = [];
    const fetcher = vi.fn(() => new Promise<Response>((_, reject) => rejects.push(reject)));
    render(wrap(<ChatPanel fetcher={fetcher as unknown as typeof fetch} />, { api }));
    const input = screen.getByPlaceholderText('Ask a question');
    fireEvent.change(input, { target: { value: 'First question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    fireEvent.change(input, { target: { value: 'Second, half typed' } });
    await act(async () => rejects[0]!(new TypeError('Failed to fetch')));
    expect(await screen.findByText(/Chat could not be reached/)).toBeInTheDocument();
    expect(input).toHaveValue('Second, half typed');
    // With nothing typed, the failed question comes back to try again.
    fireEvent.change(input, { target: { value: 'Third' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await act(async () => rejects[1]!(new TypeError('Failed to fetch')));
    await waitFor(() => expect(input).toHaveValue('Third'));
  });
});

describe('CR2-F13: deleting a module with unsaved configuration does not ask to leave', () => {
  it('goes to the list without "Leave without saving?"', async () => {
    const custom = module_({ slug: 'pw', name: 'Pricing watch', template: 'custom', builtin: false, grants: ['kg_read', 'kg_write_output'], interval_minutes: 0 });
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
      'GET /api/nav': { builtin: [], custom: [] },
      'DELETE /api/modules/pw': {},
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /><Route path="/modules" element={<div>list</div>} /></Routes>, { api, path: '/modules/pw' }));
    fireEvent.change(await screen.findByDisplayValue('Agent platforms'), { target: { value: 'Unsaved task' } });
    await chooseMoreAction('Delete module');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('list')).toBeInTheDocument();
    expect(screen.queryByText('Leave without saving?')).toBeNull();
  });
});

describe('CR2-F14: History does not refetch users on every profile re-read', () => {
  it('reads the user list once while the permission stays the same', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': { items: [{ version: 2, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-ed', entry_count: 1, note: 'saved' }], cursor: null },
      'GET /api/users': { items: [{ username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '' }], cursor: null },
    });
    const routes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>;
    const view = render(wrap(routes, { api, path: '/radars/rivals?tab=history' }));
    expect(await screen.findByText('ed@example.com')).toBeInTheDocument();
    view.rerender(wrap(routes, { api, me: { ...ADMIN }, path: '/radars/rivals?tab=history' })); // a re-read profile is a new object
    view.rerender(wrap(routes, { api, me: { ...ADMIN }, path: '/radars/rivals?tab=history' }));
    expect(screen.getByText('ed@example.com')).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/api/users')).toHaveLength(1);
  });
});

describe('CR2-F15: a cut neighbourhood says so', () => {
  it('shows that the view left connections out', async () => {
    const hood = (truncated: boolean) => ({
      nodes: [node({ id: 'organization/1', title: 'Hub', type: 'organization', category: 'entity' }), node({ id: 'signal/2', title: 'Spoke' })],
      edges: [],
      truncated,
    });
    const { api } = fakeApi({ 'GET /api/graph/nodes/organization/1/neighbors': hood(true) });
    render(wrap(<GraphPage />, { api, path: '/graph?focus=organization/1' }));
    expect(await screen.findByText(/Showing 2 of the connected nodes: this one has more than the view can draw/)).toBeInTheDocument();
  });

  it('says nothing when everything is drawn', async () => {
    const { api } = fakeApi({ 'GET /api/graph/nodes/organization/1/neighbors': { nodes: [node({ id: 'organization/1', title: 'Hub', type: 'organization' })], edges: [] } });
    render(wrap(<GraphPage />, { api, path: '/graph?focus=organization/1' }));
    expect((await screen.findAllByText('Hub')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/of the connected nodes/)).toBeNull();
  });
});

describe('CR2-F16: feedback on custom module insights can be listed', () => {
  it('offers custom module insights in the Feedback tab', async () => {
    const { api, calls } = fakeApi({ 'GET /api/feedback': { items: [], cursor: null } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=feedback' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/feedback')).toBe(true));
    const kind = createWrapper(document.body).findAllSelects().pop()!;
    kind.openDropdown();
    kind.selectOptionByValue('insight', { expandToViewport: true });
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/feedback')).toHaveLength(2));
  });
});

describe('CR2-F17: a page that loads its name first is announced and focused once it has it', () => {
  it('focuses the radar heading after the radar arrives, and announces only its final title', async () => {
    function Go() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate('/radars/rivals')}>to the radar</button>;
    }
    let arrive: (v: unknown) => void = () => undefined;
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/radars/rivals/suggestions': { items: [] },
    });
    // The radar arrives when the test says so: until then the page is a spinner.
    const original = api.radar;
    vi.spyOn(api, 'radar').mockImplementation((slug: string) => (slug === 'rivals' ? new Promise((resolve) => (arrive = resolve as (v: unknown) => void)) : original(slug)));
    render(
      wrap(
        <AppShell>
          <Routes>
            <Route path="/" element={<Go />} />
            <Route path="/radars/:slug" element={<RadarPage />} />
          </Routes>
        </AppShell>,
        { api, path: '/' },
      ),
    );
    const live = () => document.querySelector('#air-page [aria-live="polite"]');
    const link = screen.getByRole('button', { name: 'to the radar' });
    link.focus();
    fireEvent.click(link);
    await waitFor(() => expect(document.title).toBe(`Radars - ${APP_TITLE}`));
    expect(live()).toHaveTextContent(/^$/); // not "Radars" first
    await act(async () => arrive(radarView()));
    const heading = await screen.findByRole('heading', { level: 1, name: /Rivals/ });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(live()).toHaveTextContent(`Rivals - Radars - ${APP_TITLE}`);
  });
});

describe('CR2-F18: product polling restarts with new work', () => {
  it('polls again after Re-read web pages, once it had given up', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const stuck = { ...product(), web_status: 'indexing' as const };
    const { api, calls } = fakeApi({
      'GET /api/products/p1': stuck,
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'POST /api/products/p1/sync': {},
    });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText('Indexing')).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync((PRODUCT_POLL_TRIES + 2) * PRODUCT_POLL_MS);
    const reads = () => calls.filter((c) => c.method === 'GET' && c.path === '/api/products/p1').length;
    const exhausted = reads();
    await vi.advanceTimersByTimeAsync(5 * PRODUCT_POLL_MS);
    expect(reads()).toBe(exhausted); // it gave up
    fireEvent.click(screen.getByRole('button', { name: 'Re-read web pages' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const after = reads();
    await vi.advanceTimersByTimeAsync(3 * PRODUCT_POLL_MS);
    expect(reads()).toBeGreaterThan(after + 1); // watching the new crawl
  });
});

describe('SR2-F01: a bare URL with a query string is not a live link', () => {
  it('links a plain address but leaves one carrying data as text', () => {
    render(wrap(<Markdown markdown={'See https://docs.example.com/guide and https://collector.example/c?d=secret-passage then https://x.example/p#frag'} />, { api: fakeApi({}).api }));
    expect(screen.getByRole('link', { name: /docs\.example\.com\/guide/ })).toHaveAttribute('href', 'https://docs.example.com/guide');
    expect(screen.queryByRole('link', { name: /collector/ })).toBeNull();
    expect(screen.getByText(/https:\/\/collector\.example\/c\?d=secret-passage/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /x\.example/ })).toBeNull();
    // A formatted link with a query is held to the same rule, with its full address shown (r3 F3-022).
    render(wrap(<Markdown markdown={'[the form](https://forms.example.com/f?id=7)'} />, { api: fakeApi({}).api }));
    expect(screen.queryByRole('link', { name: /the form/ })).toBeNull();
    expect(screen.getByText('(https://forms.example.com/f?id=7)')).toBeInTheDocument();
  });
});
