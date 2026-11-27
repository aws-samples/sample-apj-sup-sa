// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ConfirmDelete from '@/components/ConfirmDelete';
import Markdown from '@/components/Markdown';
import { module, product } from '@/test/data';
import { chooseMoreAction, CONFIG, fakeApi, VIEWER, wrap } from '@/test/session';

import AdminPage, { actionLabel, budgetState, localDate, principalLabel } from './AdminPage';
import { cursorLabel, kindOf, ModulePage, ModulesPage, pipelineOrder, scheduleState, sourceProblem, startedLabel } from './ModulesPage';
import { DOCUMENT_MAX_BYTES, draftErrors, PRODUCT_POLL_MS, ProductPage, ProductsPage, toDraft } from './ProductsPage';

/** Red-team round 1 (frontend.md): each test names the findings it checks. */

const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible());
function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const where = () => screen.getByTestId('where').textContent;

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('Products (F-117..F-130)', () => {
  const page = () => (
    <>
      <Routes>
        <Route path="/products/:id" element={<ProductPage />} />
        <Route path="/products" element={<ProductsPage />} />
      </Routes>
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </>
  );

  it('offers Add product in an empty list, opens from ?create=1 and checks the documentation URLs', async () => {
    const { api, calls } = fakeApi({ 'GET /api/products': { items: [] } });
    render(wrap(page(), { api, path: '/products?create=1' }));
    expect(await screen.findByText('Add product', { selector: 'h2 *, h2' })).toBeInTheDocument();
    const add = within(screen.getByRole('dialog')).getByRole('button', { name: 'Add' });
    expect(add).toHaveAttribute('aria-disabled', 'true'); // no name yet
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: 'DeskPro' } });
    const urls = document.querySelectorAll('[role="dialog"] textarea')[4]!;
    fireEvent.change(urls, { target: { value: 'docs.example.com' } });
    expect(screen.getAllByText(/Each line must be a full https:\/\/ address: docs.example.com/).length).toBeGreaterThan(0);
    expect(add).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    // F2-022: a filled-in profile asks before it is thrown away.
    expect(await screen.findByText('Discard this product?')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(where()).toBe('/products'));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    fireEvent.click(screen.getAllByRole('button', { name: 'Add product' }).pop()!); // the one in the empty state
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('tells viewers who adds products', async () => {
    const { api } = fakeApi({ 'GET /api/products': { items: [] } });
    render(wrap(<ProductsPage />, { api, me: VIEWER, path: '/products?create=1' }));
    expect(await screen.findByText('An administrator adds them.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('clips long summaries at a word', async () => {
    const { api } = fakeApi({ 'GET /api/products': { items: [product({ summary: `${'explains alarms and '.repeat(20)}end` })] } });
    render(wrap(<ProductsPage />, { api }));
    expect(await screen.findByText(/explains alarms and\u2026$/)).toBeInTheDocument();
  });

  it('validates the edit form, shows progress and confirms the save', async () => {
    let resolve: () => void = () => undefined;
    const { api } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    api.updateProduct = () => new Promise((r) => (resolve = () => r(product())));
    render(wrap(page(), { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const name = document.querySelector('[role="dialog"] input')!;
    fireEvent.change(name, { target: { value: ' ' } });
    expect(screen.getAllByText('Enter the product name.').length).toBeGreaterThan(0);
    const save = within(screen.getByRole('dialog')).getByRole('button', { name: 'Save' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(name, { target: { value: 'DeskPro 2' } });
    fireEvent.click(save);
    expect(createWrapper(screen.getByRole('dialog')).findAllButtons().pop()!.findLoadingIndicator()).not.toBeNull();
    await act(async () => resolve());
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
  });

  it('shows crawl problems, the documentation it reads, notes as written, sizes, and polls while indexing', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    let status: 'indexing' | 'ready' = 'indexing';
    const notes = 'Premium segment\nNo free tier';
    const { api, calls } = fakeApi({
      'GET /api/products/p1': () => product({ web_status: status, notes, doc_urls: ['https://docs.example.com/', 'javascript:alert(1)'], stats: { crawl: { errors: ['403 https://docs.example.com/x', 'dns', 'robots.txt', 'timeout'] } } }),
      'GET /api/products/p1/documents': { items: [{ id: 'd1', product_id: 'p1', filename: 'big.pdf', content_type: 'application/pdf', size: 40 * 1024 * 1024, status: 'failed', error: 'unreadable PDF', uploaded_by: 'u', uploaded_at: '2026-10-01T10:00:00Z' }] },
      'GET /api/modules': { items: [] },
    });
    const { container } = render(wrap(page(), { api, path: '/products/p1' }));
    expect(await screen.findByText(/Last crawl problems: 403 https:\/\/docs.example.com\/x; dns; robots.txt \(and 1 more\)/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /https:\/\/docs.example.com\// })).toBeInTheDocument();
    expect(screen.getByText('javascript:alert(1)').tagName).toBe('LI'); // shown, never linked
    expect(container.querySelector('.air-pre')!.textContent).toBe(notes);
    expect(await screen.findByText('40 MB')).toBeInTheDocument();
    expect(screen.getByText('unreadable PDF')).toBeInTheDocument();
    const reads = () => calls.filter((c) => c.path === '/api/products/p1').length;
    const before = reads();
    status = 'ready';
    act(() => vi.advanceTimersByTime(PRODUCT_POLL_MS));
    await waitFor(() => expect(reads()).toBe(before + 1));
    await screen.findByText('Ready');
    act(() => vi.advanceTimersByTime(PRODUCT_POLL_MS * 2));
    expect(reads()).toBe(before + 1); // nothing in progress any more
  });

  it('lists a few crawl problems without a count, and waits for the documents', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': product({ stats: { crawl: { errors: ['dns'] } } }), 'GET /api/modules': { items: [] } });
    api.documents = () => new Promise(() => undefined);
    render(wrap(page(), { api, path: '/products/p1' }));
    expect(await screen.findByText('Last crawl problems: dns')).toBeInTheDocument();
    expect(screen.queryByText('No documents.')).toBeNull(); // still loading, not empty
  });

  it('numbers passages instead of showing a raw score', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] }, 'POST /api/products/p1/ask': { items: [{ text: 'a', source: 'document:x', score: 0.02, product_id: 'p1' }] } });
    render(wrap(page(), { api, path: '/products/p1' }));
    fireEvent.change(await screen.findByLabelText('Question'), { target: { value: 'residency' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(/^1\. document:x$/)).toBeInTheDocument();
    expect(screen.queryByText(/score/)).toBeNull();
  });

  it('refuses files over 50 MB before uploading', async () => {
    const { api, calls } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    const { container } = render(wrap(page(), { api, path: '/products/p1' }));
    await screen.findByRole('heading', { name: 'DeskPro' });
    const big = new File(['x'], 'huge.pdf');
    Object.defineProperty(big, 'size', { value: DOCUMENT_MAX_BYTES + 1 });
    fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [big] } });
    expect(await screen.findByText(/over the 50 MB limit/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload' })).toHaveAttribute('aria-disabled', 'true');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('checks a profile draft field by field', () => {
    expect(draftErrors(toDraft(product()))).toEqual({});
    const many = { ...toDraft(product()), doc_urls: 'a\nb\nc\nd' };
    expect(draftErrors(many).doc_urls).toMatch(/a, b, c, \.\.\.$/);
  });
});

describe('Modules (F-002, F-084, F-131..F-147, F-168)', () => {
  it('lists modules in pipeline order with one schedule column, and keeps setup advice for admins', async () => {
    const items = [
      module({ slug: 'content', name: 'Content ideas' }),
      module({ slug: 'zz', name: 'Zed watch', builtin: false }),
      module({ slug: 'scout', name: 'Scout', enabled: true }),
      module({ slug: 'draft', name: 'Content drafts', template: 'draft' }),
      module({ slug: 'aa', name: 'Alpha watch', builtin: false, interval_minutes: 0 }),
    ];
    const { api } = fakeApi({ 'GET /api/modules': { items } });
    const { container, unmount } = render(wrap(<ModulesPage />, { api }));
    await screen.findByText('Zed watch');
    const names = createWrapper(container).findTable()!.findRows().map((r) => r.getElement().querySelector('a')!.textContent);
    expect(names).toEqual(['Scout', 'Content ideas', 'Content drafts', 'Alpha watch', 'Zed watch']);
    expect(screen.getByText('Every 6 hours')).toBeInTheDocument();
    expect(screen.getAllByText('On demand (Run now)')).toHaveLength(1);
    // The drafts module never runs on a schedule: whether editors may request drafts (r3 F3-028).
    expect(screen.getByText('Off: editors cannot request drafts')).toBeInTheDocument();
    expect(screen.getAllByText('Paused (every 6 hours when on)')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Modules' })).toBeInTheDocument();
    unmount();
    const off = fakeApi({ 'GET /api/modules': { items: [module()] } });
    render(wrap(<ModulesPage />, { api: off.api, me: VIEWER }));
    await screen.findByText('Scout');
    expect(screen.queryByText(/Every module is off/)).toBeNull(); // advice viewers cannot follow
  });

  it('checks a new module: hyphens, its list name, 20 characters and publishing, and refreshes the navigation', async () => {
    const { api, calls } = fakeApi({ 'GET /api/modules': { items: [module({ slug: 'deals', insight_kind: 'deals', builtin: false })] }, 'POST /api/modules': module({ slug: 'pricing-watch' }), 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<><Routes><Route path="/" element={<ModulesPage />} /><Route path="/modules/:slug" element={<div>opened</div>} /></Routes></>, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    const create = within(screen.getByRole('dialog')).getByRole('button', { name: 'Create' });
    const address = screen.getByLabelText('Address');
    fireEvent.change(document.querySelectorAll('[role="dialog"] input')[0]!, { target: { value: 'Signals' } });
    expect(screen.getByText(/already a list name/)).toBeInTheDocument(); // the built-in list
    for (const text of ['pricing-', 'pricing-watch']) fireEvent.change(address, { target: { value: text } });
    expect(address).toHaveValue('pricing-watch');
    expect(screen.getByText(/Its insights are kept as "pricing_watch"/)).toBeInTheDocument();
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Too short' } });
    expect(screen.getByText('At least 20 and at most 20000 characters (9 so far).')).toBeInTheDocument();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Find pricing changes by vendors.' } });
    const tools = visibleModal()!.findContent().findMultiselect()!;
    tools.openDropdown();
    tools.selectOptionByValue('kg_write_output', { expandToViewport: true }); // off: it could never publish
    expect(screen.getByText(/runs but can never publish anything/)).toBeInTheDocument();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    tools.selectOptionByValue('kg_write_output', { expandToViewport: true }); // the list is still open
    fireEvent.change(address, { target: { value: 'deals' } });
    expect(screen.getByText(/already a list name/)).toBeInTheDocument(); // another module's list
    fireEvent.change(address, { target: { value: 'pricing-watch' } });
    fireEvent.click(create);
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ slug: 'pricing-watch', insight_kind: 'pricing_watch' });
    expect(calls.filter((c) => c.path === '/api/nav').length).toBeGreaterThanOrEqual(2); // re-read after create
  });

  it('runs once at a time, and not while AI is paused', async () => {
    let resolve: () => void = () => undefined;
    const { api, calls } = fakeApi({ 'GET /api/modules/scout': module({ grants: [] }), 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/scout/runs': { items: [] } });
    const run = api.runModule;
    api.runModule = (slug) => new Promise((r) => (resolve = () => r(run(slug) as never)));
    const { unmount } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    const button = await screen.findByRole('button', { name: 'Run now' });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => resolve());
    expect(calls.filter((c) => c.path.endsWith('/run'))).toHaveLength(1);
    unmount();
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout', config: { ...CONFIG, genai_enabled: false } }));
    expect(await screen.findByRole('button', { name: 'Run now' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('edits a source in place, checks its address, toggles at once and formats its read position', async () => {
    let fail = true;
    const src = { id: 's1', module: 'scout', name: 'Blog', type: 'rss' as const, address: 'https://blog.example.com/feed', instruction: 'Only releases', cursor: '2026-10-06T10:02:11.123Z', read_at: '2026-10-06T10:02:11.123Z', enabled: true };
    const { api, calls } = fakeApi({
      'GET /api/modules/scout': module({ grants: ['sources'] }),
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/scout/runs': { items: [] },
      'GET /api/modules/scout/sources': { items: [src, { ...src, id: 's2', name: 'API', cursor: 'etag-abc', read_at: '', instruction: undefined, address: undefined }] },
      'PATCH /api/modules/scout/sources/s1': () => {
        if (fail) {
          fail = false;
          throw { status: 500, message: 'toggle failed' };
        }
        return src;
      },
    });
    const { container } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText('Only releases')).toBeInTheDocument(); // the instruction is shown
    expect(screen.getByText('Position saved')).toBeInTheDocument(); // an opaque cursor
    expect(screen.getByRole('columnheader', { name: 'Last read' })).toBeInTheDocument();
    expect(screen.getByText('Never')).toBeInTheDocument(); // s2 has never been read
    const toggles = createWrapper(container).findAllToggles().filter((t) => t.findNativeInput().getElement().closest('table'));
    toggles[0]!.findNativeInput().click();
    expect(toggles[0]!.findNativeInput().getElement()).not.toBeChecked(); // at once, before the answer
    expect(await screen.findByText('toggle failed')).toBeInTheDocument();
    await waitFor(() => expect(toggles[0]!.findNativeInput().getElement()).toBeChecked()); // put back
    fireEvent.click(screen.getByRole('button', { name: 'Edit API' })); // a source whose configuration is empty
    expect(document.querySelectorAll('[role="dialog"] input')[0]).toHaveValue('API');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Blog' }));
    const inputs = document.querySelectorAll('[role="dialog"] input');
    expect(screen.getByText('Edit source')).toBeInTheDocument();
    fireEvent.change(inputs[inputs.length - 1]!, { target: { value: 'blog.example.com/feed' } });
    expect(screen.getAllByText('Enter a public https:// address.').length).toBeGreaterThan(0);
    fireEvent.change(inputs[inputs.length - 1]!, { target: { value: 'https://blog.example.com/rss' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH').pop()?.body).toMatchObject({ address: 'https://blog.example.com/rss', instruction: 'Only releases' }));
  });

  it('shows runs with labels, exact times and small summaries', async () => {
    const run = { module: 'scout', run_id: 'r1', status: 'budget_exhausted', trigger: 'manual', started_at: '2026-10-01T10:00:00Z', requested_by: 'u', finished_at: 'x', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 10, nodes_written: 1, summary: `${'Long summary words '.repeat(10)}`, error: null };
    const { api } = fakeApi({ 'GET /api/modules/scout': module({ grants: [] }), 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/scout/runs': { items: [run] } });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText('Budget used up')).toBeInTheDocument();
    expect(screen.getByText('Started by a person')).toBeInTheDocument();
    expect(screen.getByText(/2026/)).toBeInTheDocument(); // older than an hour: the date and time
    expect(screen.getByText(/Long summary words.*\u2026$/)).toBeInTheDocument();
  });

  it('shows the product only to modules that use one, with a warning when it is missing', async () => {
    const { api } = fakeApi({ 'GET /api/modules/gap': module({ slug: 'gap', name: 'Gaps', output_kind: 'gap', grants: [] }), 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/gap/runs': { items: [] } });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/gap' }));
    expect(await screen.findByText('Product')).toBeInTheDocument();
  });

  it("links viewers to what a module found, and refreshes the navigation when one is deleted", async () => {
    const custom = module({ slug: 'pw', name: 'Pricing watch', builtin: false, enabled: false, grants: [] });
    const { api, calls } = fakeApi({ 'GET /api/modules/pw': custom, 'GET /api/nav': { builtin: [], custom: [{ kind: 'insight:pw', label: 'Pricing moves', module: 'pw' }] }, 'GET /api/modules/signal': module({ slug: 'signal', name: 'Signals', output_kind: 'signal', grants: [] }) });
    const { unmount } = render(wrap(<><Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/modules/pw', me: VIEWER }));
    fireEvent.click(await screen.findByRole('link', { name: 'See what it found: Pricing moves' }));
    expect(where()).toBe('/insights/insight:pw');
    unmount();
    const second = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/signal', me: VIEWER }));
    expect(await screen.findByRole('link', { name: 'See what it found: Signals' })).toHaveAttribute('href', '/insights/signals');
    second.unmount();
    const admin = fakeApi({ 'GET /api/modules/pw': custom, 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/pw/runs': { items: [] }, 'DELETE /api/modules/pw': {}, 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /><Route path="/modules" element={<div>list</div>} /></Routes>, { api: admin.api, path: '/modules/pw' }));
    await chooseMoreAction('Delete module');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('list')).toBeInTheDocument();
    expect(admin.calls.filter((c) => c.path === '/api/nav').length).toBeGreaterThanOrEqual(2);
    void calls;
  });

  it('has small helpers that read well', () => {
    expect(kindOf('9-lives')).toBe('lives');
    expect(kindOf('---')).toBe('insight');
    expect(cursorLabel(undefined)).toBe('-');
    expect(cursorLabel('2026-10-06T10:02:11.123Z')).toMatch(/2026/);
    expect(cursorLabel('2026-99-99')).toBe('Position saved');
    expect(sourceProblem({ type: 'rss', name: ' ', address: 'https://x.example.com', instruction: '' })).toBe('Enter a name.');
    expect(sourceProblem({ type: 'rss', name: 'a', address: 'https://x.example.com', instruction: '' })).toBe('');
    const now = Date.parse('2026-10-05T12:00:00Z');
    expect(startedLabel('2026-10-05T11:30:00Z', now)).toBe('30m ago');
    expect(scheduleState({ enabled: true, interval_minutes: 60, template: 'scout' }).text).toBe('Every hour');
    expect(pipelineOrder(module({ slug: 'x', name: 'B' }), module({ slug: 'y', name: 'A' }))).toBeGreaterThan(0);
  });
});

describe('Administration (F-148..F-162)', () => {
  const users = [
    { username: 'u-admin', email: 'Admin@Example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' },
    { username: 'u2', email: 'two@example.com', role: 'viewer', enabled: true, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' },
  ];

  it('asks before disabling, keeps role picks until confirmed, and invites once with Enter', async () => {
    let resolve: () => void = () => undefined;
    const { api, calls } = fakeApi({ 'GET /api/users': { items: users }, 'PATCH /api/users/u2': users[1], 'POST /api/users': users[1] });
    const invite = api.inviteUser;
    api.inviteUser = (e, r) => new Promise((ok) => (resolve = () => ok(invite(e, r) as never)));
    const { container } = render(wrap(<AdminPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    expect(screen.getByText(/can no longer sign in/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }));
    visibleModal()!.findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: false }));
    const role = createWrapper(container).findAllSelects()[0]!;
    role.openDropdown();
    role.selectOptionByValue('viewer', { expandToViewport: true }); // the same role: nothing to confirm
    expect(screen.queryByText('Change the role?')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Invite user' }));
    const email = document.querySelector('[role="dialog"] input')!;
    fireEvent.change(email, { target: { value: 'typed@example.com' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Invite user' }));
    expect(document.querySelector('[role="dialog"] input')).toHaveValue(''); // starts empty again
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: 'new@example.com' } });
    fireEvent.submit(document.querySelector('[role="dialog"] form')!);
    fireEvent.submit(document.querySelector('[role="dialog"] form')!); // a second Enter while sending
    await act(async () => resolve());
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('opens the tab in the address, links runs to their module, and shows health errors with Refresh', async () => {
    const run = { module: 'scout', run_id: 'r', status: 'failed', trigger: 'manual', started_at: '2026-10-01T10:00:00Z', requested_by: 'u', finished_at: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 0, nodes_written: 0, summary: '', error: 'Traceback line one\nmore' };
    let healthy = false;
    const { api } = fakeApi({
      'GET /api/health': () => {
        if (!healthy) throw { status: 500, message: 'health down' };
        return { runs: {}, recent_failures: [], budgets: [], genai_enabled: true };
      },
      'GET /api/runs': () => {
        if (!healthy) throw { status: 500, message: 'runs down' };
        return { items: Array.from({ length: 25 }, (_, i) => ({ ...run, run_id: `r${i}` })) };
      },
    });
    const { container } = render(wrap(<><AdminPage /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/admin?tab=health' }));
    expect(await screen.findByText('The health check failed: health down')).toBeInTheDocument();
    expect(screen.getByText('The runs could not be loaded: runs down')).toBeInTheDocument();
    healthy = true;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('No modules.')).toBeInTheDocument();
    expect(await screen.findAllByText('Traceback line one')).toHaveLength(20);
    createWrapper(container).findAllPaginations().pop()!.findNextPageButton().click();
    expect(screen.getAllByText('Traceback line one')).toHaveLength(5);
    fireEvent.click(screen.getAllByRole('link', { name: 'Scout' })[0]!);
    expect(where()).toBe('/modules/scout');
  });

  it('reads the audit log in local time, filters it, names actions and people, and shows its errors', async () => {
    let fail = true;
    const entry = { actor: 'user:u-admin', email: 'admin@example.com', action: 'user.delete', target: 'u2', details: {}, at: '2026-10-01T10:00:00Z' };
    const { api, fetcher } = fakeApi({
      'GET /api/users': { items: users },
      'GET /api/audit': () => {
        if (fail) throw { status: 500, message: 'audit down' };
        return { items: [entry, { ...entry, action: 'andon.set', target: 'global', at: '2026-10-01T11:00:00Z' }, { ...entry, action: 'new.thing', target: 'x', at: '2026-10-01T12:00:00Z' }] };
      },
    });
    const { container } = render(wrap(<><AdminPage /><Routes><Route path="*" element={<Where />} /></Routes></>, { api, path: '/admin?tab=audit' }));
    expect(await screen.findByText('The audit log could not be loaded: audit down')).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([url]) => String(url) === `/api/audit?date=${localDate()}&limit=1000`)).toBe(true);
    expect(screen.getByText(/in your time zone/)).toBeInTheDocument();
    fail = false;
    createWrapper(container).findDatePicker()!.setInputValue('2026/10/01');
    expect(await screen.findByText('Deleted a user')).toBeInTheDocument();
    expect(screen.getByText('two@example.com')).toBeInTheDocument(); // the target's email, not its id
    expect(screen.getByText('Changed the AI kill switch')).toBeInTheDocument();
    expect(screen.getByText('New thing')).toBeInTheDocument();
    createWrapper(container).findTextFilter()!.findInput().setInputValue('kill switch');
    expect(screen.queryByText('Deleted a user')).toBeNull();
    createWrapper(container).findTextFilter()!.findInput().setInputValue('nothing like this');
    expect(screen.getByText('No entries match.')).toBeInTheDocument();
    createWrapper(container).findTabs()!.findTabLinkById('users')!.click();
    expect(where()).toBe('/admin');
    createWrapper(container).findTabs()!.findTabLinkById('sample')!.click();
    expect(where()).toBe('/admin?tab=sample');
  });

  it('checks the chat budget against itself and shows today\'s use', async () => {
    const { api, calls } = fakeApi({
      'GET /api/andon': { genai_enabled: true, reason: '', updated_by: null, updated_at: null },
      'GET /api/chat/settings': { daily_tokens: 2_000_000, per_user_daily_tokens: 300_000 },
      'GET /api/chat/status': { genai_enabled: true, allowed: true, shared: { state: 'open', remaining: 1_500_000 }, user: { state: 'open', remaining: 1 } },
    });
    render(wrap(<AdminPage />, { api, path: '/?tab=ai' }));
    expect(await screen.findByText(/Used today: 500,000 of 2,000,000/)).toBeInTheDocument();
    expect(screen.getByText('2,000,000 tokens')).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toHaveAttribute('aria-disabled', 'true'); // nothing changed
    fireEvent.change(screen.getByDisplayValue('300000'), { target: { value: '' } });
    expect(screen.getAllByText('Enter a whole number of at least 0.').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('Each user, per day'), { target: { value: '5000000' } });
    expect(screen.getByText('Each user cannot get more than everyone together.')).toBeInTheDocument();
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('says when users cannot be loaded, and pages a long audit day', async () => {
    const entry = { actor: 'user:x', email: 'x@example.com', action: 'radar.save', target: 'r', details: {}, at: '2026-10-01T10:00:00Z' };
    const { api } = fakeApi({
      'GET /api/users': () => { throw { status: 500, message: 'users down' }; },
      'GET /api/audit': { items: Array.from({ length: 30 }, (_, i) => ({ ...entry, target: `r${i}` })) },
    });
    const { container, unmount } = render(wrap(<AdminPage />, { api }));
    expect(await screen.findByText('The users could not be loaded: users down')).toBeInTheDocument();
    unmount();
    const second = render(wrap(<AdminPage />, { api, path: '/?tab=audit' }));
    await screen.findByText('r0');
    createWrapper(second.container).findPagination()!.findNextPageButton().click();
    expect(await screen.findByText('r29')).toBeInTheDocument();
    void container;
  });

  it('names who changed the kill switch', () => {
    const me = { subject: 'u1', email: 'a@example.com', role: 'admin' as const, can: [] };
    expect(principalLabel('user:u1', me)).toBe('you');
    expect(principalLabel('user:u9', me)).toBe('another administrator');
    expect(principalLabel('system:x', me)).toBe('system:x');
    expect(principalLabel(null, me)).toBe('someone');
    expect(budgetState('half_open')).toBe("Almost out of today's budget: 90% or more used"); // CTR HC-013
    expect(budgetState('frozen')).toBe('Frozen');
    expect(actionLabel('radar.create')).toBe('Created a radar');
    expect(localDate(new Date(2026, 0, 5, 7, 0))).toBe('2026-01-05');
  });

  it('cancels a pause', async () => {
    const { api, calls } = fakeApi({ 'GET /api/andon': { genai_enabled: true, reason: '', updated_by: null, updated_at: null }, 'GET /api/chat/settings': { daily_tokens: 1, per_user_daily_tokens: 1 } });
    render(wrap(<AdminPage />, { api, path: '/?tab=ai' }));
    await screen.findByText('AI features are running');
    fireEvent.change(screen.getAllByRole('textbox')[0]!, { target: { value: 'test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pause AI features' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Pause AI features' }));
    visibleModal()!.findDismissButton().click();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });
});

describe('Markdown (F-177) and the delete dialog (F-179)', () => {
  it('keeps list numbers, nests lists, and renders quotes, rules and _emphasis_', () => {
    const md = ['Intro', '', '3. Step three', '4. Step four', '   - detail a', '   - detail b', '5. Step five', '', '> Quoted **text**', '> more', '', '---', '', 'An _important_ point in snake_case_name.'].join('\n');
    const { container } = render(<MemoryRouter><Markdown markdown={md} /></MemoryRouter>);
    expect(container.querySelector('ol')).toHaveAttribute('start', '3');
    expect(container.querySelectorAll('ol > li')).toHaveLength(3);
    expect(container.querySelectorAll('ol > li ul > li')).toHaveLength(2);
    expect(container.querySelector('blockquote strong')?.textContent).toBe('text');
    expect(container.querySelector('blockquote')?.textContent).toContain('more');
    expect(container.querySelector('hr')).not.toBeNull();
    expect(container.querySelector('em')?.textContent).toBe('important');
    expect(container.textContent).toContain('snake_case_name');
  });

  it('ends a list at a shallower item', () => {
    const { container } = render(<MemoryRouter><Markdown markdown={['  - inner first', '- outer'].join('\n')} /></MemoryRouter>);
    expect(container.querySelectorAll('ul')).toHaveLength(2);
  });

  it('keeps the delete dialog while the delete runs', async () => {
    let done: () => void = () => undefined;
    const onDismiss = vi.fn();
    render(<ConfirmDelete header="Delete thing" onConfirm={() => new Promise<void>((r) => (done = r))} onDismiss={onDismiss}>Sure?</ConfirmDelete>);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    createWrapper(document.body).findModal()!.findDismissButton().click();
    expect(onDismiss).not.toHaveBeenCalled();
    await act(async () => done());
    createWrapper(document.body).findModal()!.findDismissButton().click();
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
