// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Link, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { dismissKey } from '@/components/GettingStarted';
import { detail, node } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import HomePage from './HomePage';
import InsightDetailPage from './InsightDetailPage';
import InsightsPage from './InsightsPage';

const page = (items = [node()], cursor: string | null = null) => ({ items, cursor, sort: 'newest', page_size: 25 });

describe('HomePage', () => {
  it('shows operations for admins and the latest insights', async () => {
    const { api } = fakeApi({
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [{ module: 'scout', enabled: true, consumed: 1500, limit: 10, state: 'open' }], genai_enabled: false },
      'GET /api/insights/signals': page(),
      'GET /api/insights/trends': page([]),
      'GET /api/insights/gaps': () => { throw { status: 500, message: 'down' }; },
      'GET /api/insights/content': page([node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it', summary: '' })]),
    });
    render(wrap(<HomePage />, { api }));
    expect(await screen.findByText('Agent sandboxing standardises')).toBeInTheDocument();
    expect(await screen.findByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('1.5k')).toBeInTheDocument();
    expect(await screen.findByText('down')).toBeInTheDocument();
    expect(screen.getByText(/Nothing yet/)).toBeInTheDocument();
  });

  it('shows running AI features and opens a full list', async () => {
    const { api } = fakeApi({
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: true },
      'GET /api/insights/signals': page(),
      'GET /api/insights/trends': page([]),
      'GET /api/insights/gaps': page([]),
      'GET /api/insights/content': page([]),
    });
    render(wrap(<Routes><Route path="/" element={<HomePage />} /><Route path="/insights/:kind" element={<div>full list</div>} /></Routes>, { api }));
    expect(await screen.findByText('Running')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('link', { name: 'View all' })[0]!);
    expect(await screen.findByText('full list')).toBeInTheDocument();
  });

  it('hides operations, gaps and content ideas from viewers', async () => {
    const { api, calls } = fakeApi({ 'GET /api/insights/signals': page([]), 'GET /api/insights/trends': page([]), 'GET /api/insights/gaps': page([]), 'GET /api/insights/content': page([]) });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/insights/')).length).toBe(2));
    expect(calls.some((c) => c.path === '/api/health')).toBe(false);
  });
});

describe('GettingStarted', () => {
  const empty = { 'GET /api/insights/signals': page([]), 'GET /api/insights/trends': page([]), 'GET /api/insights/gaps': page([]), 'GET /api/insights/content': page([]) };

  it('ticks the setup steps from what exists, hides and comes back', async () => {
    localStorage.clear();
    const { api } = fakeApi({
      ...empty,
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: true },
      'GET /api/products': { items: [{ id: 'p1' }] },
      'GET /api/modules': { items: [{ slug: 'scout', enabled: false }] },
      'GET /api/radars': { items: [{ slug: 'r' }] },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    render(wrap(<Routes><Route path="/" element={<HomePage />} /><Route path="/modules/*" element={<div>modules page</div>} /></Routes>, { api }));
    expect(await screen.findByText('2 of 4 done.', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open Products' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    expect(localStorage.getItem(dismissKey('admin@example.com'))).toBe('1'); // per account, not per browser
    fireEvent.click(screen.getByRole('button', { name: 'Show getting started' }));
    expect(localStorage.getItem(dismissKey('admin@example.com'))).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Open Modules' }));
    expect(await screen.findByText('modules page')).toBeInTheDocument();
  });

  it('counts nothing done when lists fail or are empty, and links empty cards to their module', async () => {
    localStorage.clear();
    const { api } = fakeApi({ ...empty, 'GET /api/products': { items: [] } });
    render(wrap(<Routes><Route path="/" element={<HomePage />} /><Route path="/modules/:slug" element={<div>module page</div>} /></Routes>, { api }));
    await waitFor(() => expect(screen.getAllByRole('link', { name: 'Open the module' })).toHaveLength(4));
    // Some checks failed: no count rather than a misleading "0 of 4 done".
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add a product' })).toBeInTheDocument());
    expect(screen.queryByText(/of 4 done/)).toBeNull();
    fireEvent.click(screen.getAllByRole('link', { name: 'Open the module' })[0]!);
    expect(await screen.findByText('module page')).toBeInTheDocument();
  });

  it('waits for the profile before choosing which version to show', async () => {
    const { api } = fakeApi(empty);
    render(wrap(<HomePage />, { api, me: null }));
    expect(await screen.findByText('Early movements worth knowing about, each backed by evidence.')).toBeInTheDocument();
    expect(screen.queryByText('Getting started')).toBeNull();
  });

  it('gives readers a short tour and starts hidden when dismissed before', async () => {
    localStorage.clear();
    const { api } = fakeApi(empty);
    const view = render(wrap(<Routes><Route path="/" element={<HomePage />} /><Route path="/radars" element={<div>radars page</div>} /></Routes>, { api, me: VIEWER }));
    expect(await screen.findByText(/are what the AI modules found/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Radars' }));
    expect(await screen.findByText('radars page')).toBeInTheDocument();
    view.unmount();
    localStorage.setItem(dismissKey('viewer@example.com'), '1');
    render(wrap(<HomePage />, { api, me: VIEWER }));
    expect(screen.getByRole('button', { name: 'Show getting started' })).toBeInTheDocument();
    localStorage.clear();
  });
});

describe('InsightsPage', () => {
  it('lists, searches, sorts and loads more', async () => {
    const seen: string[] = [];
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [{ kind: 'insight:pricing_move', label: 'Pricing moves', module: 'pw' }] },
      'GET /api/insights/insight:pricing_move': (_: unknown, url: URL) => {
        seen.push(url.search);
        if (url.searchParams.get('cursor')) return page([node({ id: 'insight/2', title: 'Second' })]);
        return page([node({ id: 'insight/1', title: 'First', working_on: [{ user: 'u', email: 'e', since: 'x' }] })], 'c1');
      },
    });
    const { container } = render(
      wrap(
        <Routes>
          <Route path="/insights/:kind" element={<InsightsPage />} />
        </Routes>,
        { api, path: '/insights/insight:pricing_move' },
      ),
    );
    expect(await screen.findByText('First')).toBeInTheDocument();
    expect(await screen.findByText('Pricing moves')).toBeInTheDocument();
    expect(screen.getByText(/1 working on it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Second')).toBeInTheDocument();
    const w = createWrapper(container);
    w.findSegmentedControl()!.findSegments()[1]!.click();
    await waitFor(() => expect(seen.some((q) => q.includes('sort=connected'))).toBe(true));
    w.findSegmentedControl()!.findSegments()[2]!.click(); // CTR HU-077: title, A to Z
    await waitFor(() => expect(seen.some((q) => q.includes('sort=title'))).toBe(true));
    w.findTextFilter()!.findInput().setInputValue('motor');
    await waitFor(() => expect(seen.some((q) => q.includes('q=motor') && q.includes('sort=relevance'))).toBe(true), { timeout: 3000 });
    w.findSegmentedControl()!.findSegments()[0]!.click(); // a search can be re-sorted
    await waitFor(() => expect(seen.some((q) => q.includes('q=motor') && q.includes('sort=newest'))).toBe(true));
    const before = seen.length;
    w.findTextFilter()!.findInput().setInputValue('motor '); // the same search again keeps the user's sort
    await new Promise((r) => setTimeout(r, 1200));
    expect(seen.slice(before).every((q) => !q.includes('sort=relevance'))).toBe(true);
    w.findTextFilter()!.findInput().setInputValue('');
    await waitFor(() => expect(seen.at(-1)).not.toContain('q='));
    expect(seen.at(-1)).toContain('sort=newest');
  });

  it('leaves relevance when the search is cleared', async () => {
    const seen: string[] = [];
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/trends': (_: unknown, url: URL) => {
        seen.push(url.search);
        return page();
      },
    });
    const { container } = render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/trends' }));
    const w = createWrapper(container);
    w.findTextFilter()!.findInput().setInputValue('motor');
    await waitFor(() => expect(seen.some((q) => q.includes('sort=relevance'))).toBe(true), { timeout: 3000 });
    w.findTextFilter()!.findInput().setInputValue('');
    await waitFor(() => expect(seen.at(-1)).toContain('sort=newest'));
  });

  it('marks entries without a summary or confidence', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/trends': page([node({ summary: '', confidence: undefined })]) });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/trends' }));
    expect(await screen.findByText('No summary')).toBeInTheDocument();
    expect(screen.queryByText(/confidence/)).toBeNull();
  });

  it('shows errors and empty states', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/gaps': () => { throw { status: 500, message: 'broken' }; } });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/gaps' }));
    expect(await screen.findByText('The list could not be loaded: broken')).toBeInTheDocument();
  });

  it('says which module fills an empty list and links to it', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/trends': page([]) });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /><Route path="/modules/:slug" element={<div>trend module</div>} /></Routes>, { api, path: '/insights/trends' }));
    expect(await screen.findByText(/The Trends module writes these when it runs/)).toBeInTheDocument();
    expect(screen.getByText('Patterns that hold across many signals over weeks, not days.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Open the module' }));
    expect(await screen.findByText('trend module')).toBeInTheDocument();
  });

  it('reports a failed next page', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': (_: unknown, url: URL) => {
        if (url.searchParams.get('cursor')) throw { status: 500, message: 'page failed' };
        return page([], 'c');
      },
    });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/signals' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('page failed')).toBeInTheDocument();
  });

  it('reports a failed next page when entries are already listed', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': (_: unknown, url: URL) => {
        if (url.searchParams.get('cursor')) throw { status: 500, message: 'next page failed' };
        return page([node({ title: 'Listed' })], 'c');
      },
    });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/signals' }));
    expect(await screen.findByText('Listed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('next page failed')).toBeInTheDocument();
    expect(screen.getByText('Listed')).toBeInTheDocument();
  });
});

describe('InsightDetailPage', () => {
  const routes = (ui = <InsightDetailPage />) => <Routes><Route path="/insights/nodes/:type/:uid" element={ui} /></Routes>;

  it('renders evidence and records feedback', async () => {
    let votes = 0;
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/signal/1': () => detail({ feedback: { up: votes, down: 0, mine: null } }),
      'POST /api/nodes/signal/1/feedback': () => ({ up: ++votes, down: 0, mine: null }),
    });
    render(wrap(routes(), { api, path: '/insights/nodes/signal/1' }));
    expect(await screen.findByRole('link', { name: 'View in Knowledge Graph' })).toHaveAttribute('href', '/graph?focus=signal%2F1');
    expect(await screen.findByRole('heading', { name: 'Agent sandboxing standardises' })).toBeInTheDocument();
    expect(screen.getByText('taking')).toBeInTheDocument();
    expect(screen.getByText('Docker news')).toBeInTheDocument();
    expect(screen.getByText('70%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Useful/ }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/feedback'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: /Not useful/ }));
    const modal = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    modal.findContent().findTextarea()!.setTextareaValue('weak sources');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/feedback')).pop()?.body).toEqual({ vote: 'down', comment: 'weak sources' }));
  });

  it('supports working on, drafting and reading drafts for content ideas', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it', attributes: { audience: 'engineers', content_type: 'blog_post', markdown_key: 'x' }, evidence_url: 'https://src.example.com/a' });
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': detail({
        node: idea,
        working_on: [{ user: 'u-admin', email: 'admin@example.com', since: '2026-10-01T10:00:00Z' }],
        evidence: { ...detail().evidence, drafts: [{ edge: 'has_draft', direction: 'out', node: { id: 'content_draft/9', type: 'content_draft', title: 'Draft one', summary: '', updated_at: '2026-10-01T10:00:00Z' } }] },
      }),
      'DELETE /api/nodes/content_idea/1/working': { working_on: [] },
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r' },
      'GET /api/drafts/content_draft/9': new Response('# Hello draft'),
    });
    render(wrap(routes(), { api, path: '/insights/nodes/content_idea/1' }));
    expect(await screen.findByRole('heading', { name: 'Write it' })).toBeInTheDocument();
    const labels = screen.getAllByText(/^(Content type|Audience)$/).map((e) => e.textContent);
    expect(labels).toEqual(['Content type', 'Audience']);
    expect(screen.getByText('src.example.com')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stop working on this' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Write a draft' }));
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findContent().findTextarea()!.setTextareaValue('short');
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/draft'))?.body).toEqual({ brief: 'short' }));
    expect(await screen.findByText(/being written/)).toBeInTheDocument();
    createWrapper(document.body).findExpandableSection()!.findExpandButton().click();
    expect(await screen.findByText('Hello draft')).toBeInTheDocument();
  });

  it('reports failed votes and drafts, and closes dialogs without sending', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': detail({
        node: idea,
        evidence: { ...detail().evidence, drafts: [1, 2].map((i) => ({ edge: 'has_draft', direction: 'out' as const, node: { id: `content_draft/${i}`, type: 'content_draft', title: `Draft ${i}`, summary: '', updated_at: '2026-10-01T10:00:00Z' } })) },
      }),
      'POST /api/nodes/content_idea/1/feedback': () => { throw { status: 429, message: 'slow down' }; },
      'GET /api/drafts/content_draft/1': new Response('# Body one'),
      'GET /api/drafts/content_draft/2': () => { throw { status: 404, message: 'draft gone' }; },
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    render(wrap(routes(), { api, path: '/insights/nodes/content_idea/1' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Useful/ }));
    expect(await screen.findByText('Slow down')).toBeInTheDocument(); // a rate limit reads as a sentence (F2-015)
    const visible = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible());
    fireEvent.click(screen.getByRole('button', { name: /Not useful/ }));
    visible()!.findFooter()!.findAllButtons()[0]!.click();
    await waitFor(() => expect(visible()).toBeUndefined());
    fireEvent.click(screen.getByRole('button', { name: 'Write a draft' }));
    visible()!.findFooter()!.findAllButtons()[0]!.click();
    await waitFor(() => expect(visible()).toBeUndefined());
    expect(calls.some((c) => c.path.endsWith('/draft'))).toBe(false);
    const sections = createWrapper(document.body).findAllExpandableSections();
    sections[0]!.findExpandButton().click();
    sections[1]!.findExpandButton().click();
    expect(await screen.findByText('draft gone')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Download Markdown' }));
    expect(click).toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it('shows an improvement request with its attributes, and no feedback for non-insights', async () => {
    const gap = node({
      id: 'gap/1',
      type: 'gap',
      insight_kind: 'pricing_move',
      confidence: undefined,
      module: undefined,
      created_by: 'user:u-admin',
      attributes: { zeta: { level: 2 }, problem: 'Slow exports', alpha: 'first' },
    });
    const { api } = fakeApi({ 'GET /api/insights/nodes/gap/1': detail({ node: gap, feedback: undefined }) });
    render(wrap(routes(), { api, path: '/insights/nodes/gap/1' }));
    expect(await screen.findByText('Improvement request')).toBeInTheDocument();
    expect(screen.getByText('Pricing move')).toBeInTheDocument();
    expect(screen.getByText('-')).toBeInTheDocument();
    expect(screen.getByText('Added by a person')).toBeInTheDocument();
    expect(screen.getAllByText(/^(Problem|Alpha|Zeta)$/).map((e) => e.textContent)).toEqual(['Problem', 'Alpha', 'Zeta']);
    expect(screen.getByText('Level: 2')).toBeInTheDocument(); // not raw JSON
    expect(screen.queryByRole('button', { name: /Useful/ })).toBeNull();
  });

  it('highlights your own vote', async () => {
    for (const vote of ['up', 'down'] as const) {
      const { api } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail({ feedback: { up: 1, down: 1, mine: { vote, comment: '' } } }) });
      const { unmount } = render(wrap(routes(), { api, path: '/insights/nodes/signal/1' }));
      const chosen = await screen.findByRole('button', { name: vote === 'up' ? /^Useful/ : /Not useful/ });
      const other = screen.getByRole('button', { name: vote === 'up' ? /Not useful/ : /^Useful/ });
      expect(chosen.className).toMatch(/variant-primary/);
      expect(other.className).not.toMatch(/variant-primary/);
      unmount();
    }
  });

  it('withdraws your own vote, reports a failed withdrawal and hides the control without a vote or permission', async () => {
    let mine: { vote: 'up'; comment: string } | null = { vote: 'up', comment: '' };
    let fails = true;
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/signal/1': () => detail({ feedback: { up: mine ? 1 : 0, down: 0, mine } }),
      'DELETE /api/nodes/signal/1/feedback': () => {
        if (fails) throw { status: 500, message: 'withdraw failed' };
        mine = null;
        return { up: 0, down: 0, mine: null };
      },
    });
    const { unmount } = render(wrap(routes(), { api, path: '/insights/nodes/signal/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Withdraw my vote' }));
    expect(await screen.findByText('withdraw failed')).toBeInTheDocument();
    fails = false;
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw my vote' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Withdraw my vote' })).toBeNull());
    expect(screen.getByRole('button', { name: /^Useful/ })).toHaveTextContent('Useful (0)');
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(2);
    unmount();
    mine = { vote: 'up', comment: '' };
    render(wrap(routes(), { api, path: '/insights/nodes/signal/1', me: VIEWER }));
    expect(await screen.findByRole('button', { name: /^Useful/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByRole('button', { name: 'Withdraw my vote' })).toBeNull();
  });

  it('closes the feedback and draft dialogs with their close buttons', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api, calls } = fakeApi({ 'GET /api/insights/nodes/content_idea/1': detail({ node: idea }) });
    render(wrap(routes(), { api, path: '/insights/nodes/content_idea/1' }));
    const visible = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible());
    fireEvent.click(await screen.findByRole('button', { name: /Not useful/ }));
    visible()!.findDismissButton().click();
    await waitFor(() => expect(visible()).toBeUndefined());
    fireEvent.click(screen.getByRole('button', { name: 'Write a draft' }));
    visible()!.findDismissButton().click();
    await waitFor(() => expect(visible()).toBeUndefined());
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('shows a relative source link as written instead of crashing', async () => {
    const { api } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail({ node: node({ evidence_url: '/radars/rivals' }) }) });
    render(wrap(routes(), { api, path: '/insights/nodes/signal/1' }));
    expect(await screen.findByRole('link', { name: '/radars/rivals' })).toHaveAttribute('href', '/radars/rivals');
  });

  it('never shows the previous entry under the next one while it loads', async () => {
    const { api } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail() });
    let finish: (d: ReturnType<typeof detail>) => void = () => undefined;
    const load = api.insight;
    api.insight = (id) => (id === 'signal/2' ? new Promise((r) => (finish = r)) : load(id));
    const ui = <><Link to="/insights/nodes/signal/2">next</Link><InsightDetailPage /></>;
    render(wrap(routes(ui), { api, path: '/insights/nodes/signal/1' }));
    expect(await screen.findByRole('heading', { name: 'Agent sandboxing standardises' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'next' }));
    expect(screen.queryByRole('heading', { name: 'Agent sandboxing standardises' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Useful/ })).toBeNull();
    await act(async () => finish(detail({ node: node({ id: 'signal/2', title: 'Second signal' }) })));
    expect(await screen.findByRole('heading', { name: 'Second signal' })).toBeInTheDocument();
  });

  it('shows a failed load with Retry, and a way back to where the record lives', async () => {
    let fail = true;
    const { api } = fakeApi({
      'GET /api/insights/nodes/organization/x': () => {
        if (fail) throw { status: 500, message: 'server error' };
        return detail({ node: node({ id: 'organization/x', type: 'organization', title: 'Acme' }) });
      },
    });
    render(wrap(routes(), { api, path: '/insights/nodes/organization/x' }));
    expect(await screen.findByText('This entry could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to knowledge graph' })).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Acme' })).toBeInTheDocument();
  });

  it('shows load errors', async () => {
    const { api } = fakeApi({ 'GET /api/insights/nodes/gap/x': () => { throw { status: 404, message: 'gone' }; } });
    render(wrap(routes(), { api, path: '/insights/nodes/gap/x' }));
    expect(await screen.findByText('Entry not found')).toBeInTheDocument();
  });
});
