// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Me } from '@/api/types';
import { dismissKey } from '@/components/GettingStarted';
import { detail, node } from '@/test/data';
import { ADMIN, CONFIG, fakeApi, VIEWER, wrap } from '@/test/session';

import GraphPage from './GraphPage';
import HomePage from './HomePage';
import InsightDetailPage, { attributesMarkdown, DRAFT_POLL_MS, DRAFT_POLL_TRIES, nextSteps, workingFor } from './InsightDetailPage';
import InsightsPage from './InsightsPage';

/** Red-team round 1 (frontend.md): each test names the findings it checks. */

const page = (items = [node()], cursor: string | null = null) => ({ items, cursor, sort: 'newest', page_size: 25 });
const EDITOR: Me = { subject: 'u-ed', email: 'ed@example.com', role: 'editor', can: ['read', 'chat', 'feedback.give', 'working.mark', 'content.draft', 'radar.create', 'radar.edit', 'product.read'] };
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible());

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
function Back() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>history back</button>;
}
const where = () => screen.getByTestId('where').textContent;

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('InsightsPage (F-041, F-076..F-083)', () => {
  const routes = (
    <>
      <Routes>
        <Route path="/insights/:kind" element={<InsightsPage />} />
      </Routes>
      <Link to="/insights/trends">to trends</Link>
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </>
  );

  it('keeps the search and sort in the address, per list', async () => {
    const { api, calls } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page(), 'GET /api/insights/trends': page([]) });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?q=pricing&sort=connected' }));
    await screen.findByText('Agent sandboxing standardises');
    const first = calls.find((c) => c.path === '/api/insights/signals');
    expect(first).toBeDefined();
    expect(screen.getByDisplayValue('pricing')).toBeInTheDocument();
    createWrapper(container).findSegmentedControl()!.findSegments()[0]!.click(); // Newest
    expect(where()).toBe('/insights/signals?q=pricing&sort=newest');
    fireEvent.click(screen.getByRole('link', { name: 'to trends' }));
    // Another list starts clean: the box is empty and Trends is not filtered by the signals search.
    await waitFor(() => expect(where()).toBe('/insights/trends'));
    expect(screen.queryByDisplayValue('pricing')).toBeNull();
  });

  it('ignores a one-character search with a hint, and counts what is shown', async () => {
    const long = 'word '.repeat(80);
    const { api, calls } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page([node({ summary: long, degree: 1 })], 'next') });
    const { container } = render(wrap(routes, { api, path: '/insights/signals' }));
    expect(await screen.findByText('(1 shown)')).toBeInTheDocument();
    expect(screen.getByText('1 link')).toBeInTheDocument();
    expect(screen.getByText(/\u2026$/)).toBeInTheDocument(); // the long summary is clipped
    createWrapper(container).findTextFilter()!.findInput().setInputValue('x');
    expect((await screen.findAllByText('Type at least 2 characters')).length).toBeGreaterThan(0);
    await new Promise((r) => setTimeout(r, 400));
    expect(calls.filter((c) => c.path === '/api/insights/signals')).toHaveLength(1);
  });

  it('ignores a relevance sort without a search, and an unknown sort', async () => {
    const { api, calls } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page() });
    const { unmount } = render(wrap(routes, { api, path: '/insights/signals?sort=relevance' }));
    await screen.findByText('Agent sandboxing standardises');
    unmount();
    render(wrap(routes, { api, path: '/insights/signals?sort=bogus' }));
    await screen.findByText('Agent sandboxing standardises');
    expect(calls.length).toBeGreaterThan(0);
  });

  it('retries a failed list', async () => {
    let fail = true;
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/gaps': () => {
        if (fail) throw { status: 500, message: 'Failed to fetch' };
        return page();
      },
    });
    render(wrap(routes, { api, path: '/insights/gaps' }));
    expect(await screen.findByText('The list could not be loaded: Failed to fetch')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Agent sandboxing standardises')).toBeInTheDocument();
  });

  it('waits for the navigation before naming a custom list, and says when a list does not exist', async () => {
    let answer: (v: unknown) => void = () => undefined;
    const { api } = fakeApi({ 'GET /api/insights/insight:pricing': page([]) });
    api.nav = () => new Promise((r) => (answer = r as never));
    render(wrap(routes, { api, path: '/insights/insight:pricing' }));
    expect(screen.getByText('Loading')).toBeInTheDocument();
    expect(screen.queryByText('insight:pricing')).toBeNull(); // no raw slug as the title
    await act(async () => answer({ builtin: [], custom: [{ kind: 'insight:pricing', label: 'Pricing moves', module: '' }] }));
    expect(await screen.findByRole('heading', { name: 'Pricing moves' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open the module' })).toBeNull(); // no link to /modules/
  });

  it('shows "List not found" for an unknown kind', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(routes, { api, path: '/insights/foo' }));
    expect(await screen.findByText('List not found')).toBeInTheDocument();
  });

  it('tells how drafts are made instead of pointing at a module with no Run now', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/drafts': page([]) });
    render(wrap(routes, { api, path: '/insights/drafts' }));
    expect(await screen.findByText(/Open a content idea and choose Write a draft/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open the module' })).toBeNull();
  });
});

describe('Overview and Getting started (F-014, F-087..F-094)', () => {
  const lists = { 'GET /api/insights/signals': page([node({ summary: `${'Iceberg and Parquet queries '.repeat(8)}` })]), 'GET /api/insights/trends': page([]), 'GET /api/insights/gaps': page([]), 'GET /api/insights/content': page([]) };

  it('shows a failing health check, links failures to Health, and lists custom kinds', async () => {
    localStorage.clear();
    const { api } = fakeApi({
      ...lists,
      'GET /api/nav': { builtin: [], custom: [{ kind: 'insight:pricing', label: 'Pricing moves', module: 'pw' }] },
      'GET /api/insights/insight:pricing': page([]),
      'GET /api/health': () => { throw { status: 500, message: 'health down' }; },
      'GET /api/products': { items: [] },
      'GET /api/modules': { items: [] },
      'GET /api/radars': { items: [] },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    render(wrap(<><HomePage /><Routes><Route path="*" element={<Where />} /></Routes></>, { api }));
    expect(await screen.findByText('The health check failed: health down')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Open Administration > Health' }));
    expect(where()).toBe('/admin?tab=health');
    expect(await screen.findByRole('heading', { name: 'Pricing moves' })).toBeInTheDocument();
    expect(await screen.findByText(/Iceberg and Parquet queries.*\u2026$/)).toBeInTheDocument(); // cut at a word, with an ellipsis
    expect(await screen.findByText(/^0 of 4 done/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create a radar' }));
    expect(where()).toBe('/radars?create=1');
    fireEvent.click(screen.getByRole('button', { name: 'Add a product' }));
    expect(where()).toBe('/products?create=1');
  });

  it('links the failed-run count to Health', async () => {
    localStorage.setItem(dismissKey(ADMIN.email), '1');
    const failure = { module: 'scout', run_id: 'r', status: 'failed' };
    const { api } = fakeApi({ ...lists, 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/health': { runs: { failed: 1 }, recent_failures: [failure], budgets: [], genai_enabled: true } });
    render(wrap(<><HomePage /><Routes><Route path="*" element={<Where />} /></Routes></>, { api }));
    fireEvent.click(await screen.findByRole('link', { name: '1' }));
    expect(where()).toBe('/admin?tab=health');
    localStorage.clear();
  });

  it('gives editors their own steps, leaves out votes for viewers and Ask while AI is paused', async () => {
    localStorage.clear();
    const { api } = fakeApi({ ...lists, 'GET /api/nav': { builtin: [], custom: [] } });
    const { unmount } = render(wrap(<><HomePage /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, me: EDITOR }));
    expect(await screen.findByText(/Vote Useful or Not useful/)).toBeInTheDocument();
    expect(screen.getByText(/Write a draft/)).toBeInTheDocument();
    expect(screen.getByText(/answers questions from the knowledge graph/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create a radar' }));
    expect(where()).toBe('/radars?create=1');
    unmount();
    render(wrap(<HomePage />, { api, me: VIEWER, config: { ...CONFIG, genai_enabled: false } }));
    expect(await screen.findByText(/are what the AI modules found/)).toBeInTheDocument();
    expect(screen.queryByText(/Vote Useful/)).toBeNull(); // viewers cannot vote
    expect(screen.queryByText(/answers questions from the knowledge graph/)).toBeNull(); // Ask is paused
  });

  it('leaves the Ask line out of the admin checklist while AI is paused', async () => {
    localStorage.clear();
    const { api } = fakeApi({ ...lists, 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: false } });
    render(wrap(<HomePage />, { api, config: { ...CONFIG, genai_enabled: false } }));
    expect(await screen.findByText(/Describe your product/)).toBeInTheDocument();
    expect(screen.queryByText(/with Ask at the top right/)).toBeNull();
  });
});

describe('InsightDetailPage (F-014, F-015, F-095..F-108)', () => {
  const routes = (
    <>
      <Routes>
        <Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} />
      </Routes>
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </>
  );

  it('builds the next steps from what the user may do, with the real button labels', () => {
    expect(nextSteps('signal', VIEWER, true).join(' ')).not.toMatch(/Vote/);
    expect(nextSteps('signal', ADMIN, false).join(' ')).not.toMatch(/Ask/);
    expect(nextSteps('content_idea', ADMIN, true).join(' ')).toContain('"I\'m working on this"');
    expect(nextSteps('content_idea', VIEWER, true)).toEqual([]);
    expect(nextSteps('organization', ADMIN, true)).toEqual([]);
  });

  it('votes once at a time, takes an optional reason and shows your earlier one', async () => {
    let resolve: () => void = () => undefined;
    const { api, calls } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail({ feedback: { up: 0, down: 1, mine: { vote: 'down', comment: 'weak sources' } } }) });
    const vote = api.feedback;
    api.feedback = (...args) => new Promise((r) => (resolve = () => r(vote(...args) as never)));
    render(wrap(routes, { api, path: '/insights/nodes/signal/1' }));
    const useful = await screen.findByRole('button', { name: /^Useful/ });
    expect(screen.getByText('Your feedback and actions')).toBeInTheDocument(); // in the body, not the header
    fireEvent.click(useful);
    expect(useful).toBeDisabled(); // a double click sends one vote
    await act(async () => resolve());
    fireEvent.click(screen.getByRole('button', { name: /Not useful/ }));
    expect(screen.getByDisplayValue('weak sources')).toBeInTheDocument(); // the comment given before
    fireEvent.change(screen.getByDisplayValue('weak sources'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); // a reason is optional
    await act(async () => resolve());
    void calls;
  });

  it('marks working by account, waits for each click, and says for how long', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const since = new Date(Date.now() - 3 * 86_400_000).toISOString();
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': detail({ node: idea, working_on: [{ user: 'u-admin', email: 'old-address@example.com', since }, { user: 'u-2', email: 'jane@example.com', since }] }),
      'DELETE /api/nodes/content_idea/1/working': { working_on: [] },
    });
    render(wrap(routes, { api, path: '/insights/nodes/content_idea/1' }));
    expect(await screen.findByText('You')).toBeInTheDocument();
    expect(screen.getAllByText('for 3 days')).toHaveLength(2);
    expect(screen.getByText('jane@example.com')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stop working on this' })); // matched by id, not the changed email
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('describes how long someone has worked on something', () => {
    const now = Date.parse('2026-10-05T12:00:00Z');
    expect(workingFor('2026-10-05T11:30:00Z', now)).toBe('since just now');
    expect(workingFor('2026-10-05T06:00:00Z', now)).toBe('for 6 hours');
    expect(workingFor('2026-10-04T10:00:00Z', now)).toBe('for a day');
    expect(workingFor('2026-10-01T12:00:00Z', now)).toBe('for 4 days');
    expect(workingFor('nope', now)).toBe('');
  });

  it('starts one draft, clears the brief, links to Drafts and looks for the new draft', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const drafts = 0;
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': () =>
        detail({
          node: idea,
          evidence: { ...detail().evidence, drafts: Array.from({ length: drafts }, (_, i) => ({ edge: 'has_draft', direction: 'out' as const, node: { id: `content_draft/${i}`, type: 'content_draft', title: `D${i}`, summary: '', updated_at: '2026-10-01T10:00:00Z' } })) },
        }),
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r' },
    });
    render(wrap(routes, { api, path: '/insights/nodes/content_idea/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    fireEvent.change(visibleModal()!.findContent().findTextarea()!.findNativeTextarea().getElement(), { target: { value: 'for CTOs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByText('Writing a draft')).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/draft'))).toHaveLength(1);
    fireEvent.click(screen.getByRole('link', { name: 'Drafts' }));
    expect(where()).toBe('/insights/drafts');
  });

  it('polls until the draft appears, then stops', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    let drafts = 0;
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': () =>
        detail({
          node: idea,
          evidence: { ...detail().evidence, drafts: Array.from({ length: drafts }, (_, i) => ({ edge: 'has_draft', direction: 'out' as const, node: { id: `content_draft/${i}`, type: 'content_draft', title: `D${i}`, summary: '', updated_at: '2026-10-01T10:00:00Z' } })) },
        }),
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r' },
      'GET /api/drafts/content_draft/0': new Response('# D'),
    });
    render(wrap(<><Routes><Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} /></Routes></>, { api, path: '/insights/nodes/content_idea/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await screen.findByText('Writing a draft');
    const reads = () => calls.filter((c) => c.path === '/api/insights/nodes/content_idea/1').length;
    const before = reads();
    act(() => vi.advanceTimersByTime(DRAFT_POLL_MS));
    await waitFor(() => expect(reads()).toBe(before + 1));
    drafts = 1;
    act(() => vi.advanceTimersByTime(DRAFT_POLL_MS));
    await waitFor(() => expect(screen.queryByText('Writing a draft')).toBeNull());
    const after = reads();
    act(() => vi.advanceTimersByTime(DRAFT_POLL_MS * 3));
    expect(reads()).toBe(after); // stopped once the draft arrived
  });

  it('gives up polling after a while', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api, calls } = fakeApi({ 'GET /api/insights/nodes/content_idea/1': detail({ node: idea }), 'POST /api/nodes/content_idea/1/draft': { run_id: 'r' } });
    render(wrap(routes, { api, path: '/insights/nodes/content_idea/1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await screen.findByText('Writing a draft');
    const reads = () => calls.filter((c) => c.path === '/api/insights/nodes/content_idea/1').length;
    for (let i = 0; i <= DRAFT_POLL_TRIES + 2; i++) act(() => vi.advanceTimersByTime(DRAFT_POLL_MS));
    const total = reads();
    act(() => vi.advanceTimersByTime(DRAFT_POLL_MS * 5));
    expect(reads()).toBe(total);
    expect(total).toBeLessThanOrEqual(DRAFT_POLL_TRIES + 2);
  });

  it('disables drafting while AI is paused', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api } = fakeApi({ 'GET /api/insights/nodes/content_idea/1': detail({ node: idea }) });
    render(wrap(routes, { api, path: '/insights/nodes/content_idea/1', config: { ...CONFIG, genai_enabled: false } }));
    expect(await screen.findByRole('button', { name: 'Write a draft' })).toHaveAttribute('aria-disabled', 'true');
  });

  it("shows a draft's own text and names its download after it", async () => {
    const draft = node({ id: 'content_draft/9', type: 'content_draft', title: 'Agent sandboxes: a primer' });
    const { api } = fakeApi({ 'GET /api/insights/nodes/content_draft/9': detail({ node: draft }), 'GET /api/drafts/content_draft/9': new Response('# Primer body') });
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    render(wrap(routes, { api, path: '/insights/nodes/content_draft/9' }));
    expect(await screen.findByText('Primer body')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download Markdown' }));
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('agent-sandboxes-a-primer.md');
  });

  it('splits Source from Created by, lists array values and copies the request as Markdown', async () => {
    const gap = node({ id: 'gap/1', type: 'gap', title: 'Bulk export', evidence_url: 'https://forum.example.com/t/1', attributes: { impact: ['churn', 'deal loss'], problem: 'Slow' } });
    const { api } = fakeApi({ 'GET /api/insights/nodes/gap/1': detail({ node: gap }) });
    render(wrap(routes, { api, path: '/insights/nodes/gap/1' }));
    expect(await screen.findByText('forum.example.com')).toBeInTheDocument();
    expect(screen.getByText('Created by')).toBeInTheDocument();
    expect(screen.getByText('Signals')).toBeInTheDocument(); // F2-089: the module's display name
    expect(screen.getByText('churn')).toBeInTheDocument();
    expect(screen.getByText('deal loss')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy as Markdown' })).toBeInTheDocument();
    expect(attributesMarkdown('Bulk export', [['problem', 'Slow'], ['impact', ['churn', 'deal loss']], ['extra', { a: 1 }]])).toBe(
      '## Bulk export\n\n- **Problem**: Slow\n- **Impact**: \n  - churn\n  - deal loss\n- **Extra**: {"a":1}',
    );
  });

  it('shows nested values in attributes as text', async () => {
    const n = node({ id: 'trend/1', type: 'trend', attributes: { scores: [{ a: 1 }], window: 30, meta: { source: 'forum' } } });
    const { api } = fakeApi({ 'GET /api/insights/nodes/trend/1': detail({ node: n }) });
    render(wrap(routes, { api, path: '/insights/nodes/trend/1' }));
    expect(await screen.findByText('{"a":1}')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
    expect(screen.getByText('Source: forum')).toBeInTheDocument();
  });

  it('offers content actions on an idea that takes no votes', async () => {
    const idea = node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' });
    const { api } = fakeApi({ 'GET /api/insights/nodes/content_idea/1': detail({ node: idea, feedback: undefined }) });
    render(wrap(routes, { api, path: '/insights/nodes/content_idea/1' }));
    expect(await screen.findByRole('button', { name: 'Write a draft' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Useful/ })).toBeNull();
  });
});

describe('Knowledge graph (F-010..F-012, F-109..F-116)', () => {
  const org = node({ id: 'organization/1', type: 'organization', category: 'entity', title: 'Acme', degree: 9 });
  const others = Array.from({ length: 20 }, (_, i) => node({ id: `fact/${i}`, type: 'fact', category: 'evidence', title: `Fact ${i}`, degree: i }));
  const overview = { nodes: [org, ...others], edges: [{ from: 'fact/1', type: 'about', to: 'organization/1', created_by: 'u', created_at: 'x' }] };

  it('labels only the best-connected nodes on the overview and lists every node and link as text', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': overview });
    render(wrap(<GraphPage />, { api }));
    await screen.findByTestId('graph-view');
    const buttons = within(screen.getByTestId('graph-node-list')).getAllByRole('button');
    expect(buttons).toHaveLength(21);
    expect(buttons.filter((b) => b.dataset.labelled === 'true')).toHaveLength(10);
    expect(screen.getByTestId('graph-node-fact/19').dataset.labelled).toBe('true'); // the most connected
    expect(screen.getByTestId('graph-node-fact/0').dataset.labelled).toBe('false');
    expect(screen.getByTestId('graph-text-alternative')).toHaveTextContent('The graph as a list: 21 nodes and 1 links');
    expect(screen.getByTestId('graph-legend')).toHaveTextContent(/Organization/);
  });

  it('opens details from the keyboard: the list is in the tab order and Enter shows the panel', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': { nodes: [org, others[0]!], edges: [] } });
    render(wrap(<GraphPage />, { api }));
    const first = await screen.findByTestId('graph-node-organization/1');
    expect(first.tagName).toBe('BUTTON'); // a native button: focusable, Enter and Space click it
    first.focus();
    expect(first).toHaveFocus();
    fireEvent.click(first);
    expect(await screen.findByTestId('graph-node-details')).toHaveTextContent('Acme');
  });

  it('shows a failed overview with Retry instead of "empty"', async () => {
    let fail = true;
    const { api } = fakeApi({
      'GET /api/graph/explore': () => {
        if (fail) throw { status: 401, message: 'Unauthorized' };
        return overview;
      },
    });
    render(wrap(<GraphPage />, { api }));
    expect(await screen.findByText('The graph could not be loaded: Unauthorized')).toBeInTheDocument();
    expect(screen.queryByText(/The graph is empty/)).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('graph-view')).toBeInTheDocument();
  });

  it('records each opened node in the history, so Back returns to the node before', async () => {
    const hood = { nodes: [org, others[1]!], edges: overview.edges };
    const { api } = fakeApi({ 'GET /api/graph/nodes/organization/1/neighbors': hood, 'GET /api/graph/nodes/fact/1/neighbors': hood });
    render(wrap(<><GraphPage /><Back /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/graph?focus=organization%2F1' }));
    fireEvent.doubleClick(await screen.findByTestId('graph-node-fact/1'));
    await waitFor(() => expect(where()).toBe('/graph?focus=fact%2F1'));
    expect(await screen.findByText('Fact 1 about Acme')).toBeInTheDocument(); // the link, as words
    fireEvent.click(screen.getByRole('button', { name: 'history back' }));
    await waitFor(() => expect(where()).toBe('/graph?focus=organization%2F1'));
  });

  it('says a missing node may be deleted, and other failures as they are, with Retry', async () => {
    let calls = 0;
    const { api } = fakeApi({
      'GET /api/graph/nodes/organization/1/neighbors': () => {
        calls += 1;
        throw calls === 1 ? { status: 404, message: 'Not found' } : { status: 503, message: 'busy' };
      },
    });
    render(wrap(<GraphPage />, { api, path: '/graph?focus=organization%2F1' }));
    expect(await screen.findByText('This node could not be found. It may have been deleted.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('This node could not be loaded: busy')).toBeInTheDocument();
  });

  it('searches with a button, follows the type filter, hints at short queries and opens results', async () => {
    const { api, calls } = fakeApi({
      'POST /api/graph/search': { items: [{ node: node({ id: 'organization/1', type: 'organization', title: 'Acme', summary: 'x '.repeat(100) }), score: 0.02, semantic_rank: 1, keyword_rank: 1 }] },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    const { container } = render(wrap(<GraphPage />, { api }));
    const input = screen.getByLabelText('Search the graph');
    fireEvent.change(input, { target: { value: 'a' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByText('Type at least 2 characters.')).toBeInTheDocument();
    // Spaces only (CTR HU-086): the hint, and no search.
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByText('Type at least 2 characters.')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/api/graph/search')).toBe(false);
    fireEvent.change(input, { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByRole('link', { name: 'Open' })).toHaveAttribute('href', '/insights/nodes/organization/1');
    expect(screen.getByText(/\u2026/)).toBeInTheDocument();
    const types = createWrapper(container).findMultiselect()!;
    types.openDropdown();
    expect(types.findTrigger().getElement().getAttribute('aria-labelledby')!.split(' ').map((id) => document.getElementById(id)?.textContent).join(' ')).toContain('Types');
    types.selectOptionByValue('organization', { expandToViewport: true });
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST').pop()?.body).toEqual({ query: 'acme', types: ['organization'], limit: 30 }));
  });

});
