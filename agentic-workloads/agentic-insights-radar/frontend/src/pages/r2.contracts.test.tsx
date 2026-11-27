// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** The round-2 backend contracts the frontend has to follow (F2-extra 1 to 5). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { GraphBrief } from '@/api/types';
import { draftErrors, PRODUCT_NAME_MAX, PRODUCT_TEXT_MAX, ProductPage, toDraft } from './ProductsPage';
import { ModulePage, SOURCE_INSTRUCTION_MAX, SOURCE_NAME_MAX, sourceProblem } from './ModulesPage';
import { module as module_, product, radarView } from '@/test/data';
import { ADMIN, fakeApi, wrap } from '@/test/session';

import GraphPage from './GraphPage';
import RadarPage from './RadarPage';

afterEach(() => vi.restoreAllMocks());

const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const productRoutes = <Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>;
const moduleRoutes = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;

/** Exactly what /api/graph/explore now sends: no description, attributes, aliases or version. */
const brief = (over: Partial<GraphBrief> = {}): GraphBrief => ({
  id: 'signal/1',
  type: 'signal',
  category: 'output',
  title: 'Agent sandboxing standardises',
  summary: 'A'.repeat(300),
  degree: 3,
  updated_at: '2026-10-01T10:00:00Z',
  ...over,
});

describe('F2-extra 1: the graph draws brief nodes', () => {
  it('draws the picture and its node list from a node with no description or attributes', async () => {
    const { api, calls } = fakeApi({
      'GET /api/graph/explore': { nodes: [brief(), brief({ id: 'fact/2', type: 'fact', category: 'evidence', title: 'Docker news' })], edges: [], truncated: false },
    });
    render(wrap(<GraphPage />, { api, path: '/graph' }));
    const first = await screen.findByTestId('graph-node-signal/1');
    expect(screen.getByText('The graph as a list: 2 nodes and 0 links')).toBeInTheDocument();
    expect(first.textContent).toMatch(/^Signal: Agent sandboxing standardises\. A{99}/);
    // The picture asks for nothing else: anything needing the whole record reads it with api.node.
    expect(calls.filter((c) => c.path.startsWith('/api/graph/nodes/'))).toHaveLength(0);
  });

  it('opens a neighbourhood from brief nodes too', async () => {
    const { api } = fakeApi({
      'GET /api/graph/explore': { nodes: [brief()], edges: [] },
      'GET /api/graph/nodes/signal/1/neighbors': { nodes: [brief(), brief({ id: 'fact/3', type: 'fact', category: 'evidence', title: 'Near' })], edges: [], truncated: true },
    });
    render(wrap(<GraphPage />, { api, path: '/graph?focus=signal%2F1' }));
    expect(await screen.findByTestId('graph-node-fact/3')).toBeInTheDocument();
    expect(screen.getByTestId('graph-scope-banner')).toHaveTextContent('Showing relationships for Agent sandboxing standardises');
  });
});

describe('F2-extra 2: a passage is plain text', () => {
  it('renders HTML and Markdown in a passage as the characters they are', async () => {
    const hostile = '<img src=x onerror=alert(1)> and [a link](https://evil.example.com) **bold**';
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'POST /api/products/p1/ask': { items: [{ text: hostile, source: 'https://docs.example.com/a', score: 1, product_id: 'p1' }] },
    });
    const { container } = render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.change(await screen.findByLabelText('Question'), { target: { value: 'anything' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(container.querySelector('.air-passage img')).toBeNull();
    expect(container.querySelector('.air-passage a[href="https://evil.example.com"]')).toBeNull();
    expect(container.querySelector('.air-passage strong')).toBeNull();
  });
});

describe('F2-extra 3: the tighter field limits', () => {
  it('checks the product name, summary and notes', () => {
    expect(draftErrors(toDraft(product()))).toEqual({});
    expect(draftErrors({ ...toDraft(product()), name: 'x'.repeat(PRODUCT_NAME_MAX + 1) }).name).toBe(
      `At most ${PRODUCT_NAME_MAX} characters (this is ${PRODUCT_NAME_MAX + 1}).`,
    );
    expect(draftErrors({ ...toDraft(product()), summary: 'y'.repeat(PRODUCT_TEXT_MAX + 1) }).summary).toContain(`At most ${PRODUCT_TEXT_MAX}`);
    expect(draftErrors({ ...toDraft(product()), notes: 'z'.repeat(PRODUCT_TEXT_MAX + 1) }).notes).toContain(`At most ${PRODUCT_TEXT_MAX}`);
  });

  it('checks a source name and instruction', () => {
    const ok = { type: 'rss' as const, name: 'Blog', address: 'https://blog.example.com/feed', instruction: '' };
    expect(sourceProblem(ok)).toBe('');
    expect(sourceProblem({ ...ok, name: 'n'.repeat(SOURCE_NAME_MAX + 1) })).toContain(`Shorten the name to ${SOURCE_NAME_MAX}`);
    expect(sourceProblem({ ...ok, instruction: 'i'.repeat(SOURCE_INSTRUCTION_MAX + 1) })).toContain(`Shorten the instruction to ${SOURCE_INSTRUCTION_MAX}`);
  });

  it('refuses Add until the source fits', async () => {
    const scout = module_({ slug: 'scout', grants: ['sources', 'kg_read'] });
    const { api } = fakeApi({
      'GET /api/modules/scout': scout,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/scout/runs': { items: [] },
      'GET /api/modules/scout/sources': { items: [] },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }));
    const dialog = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    dialog.findContent().findInput()!.setInputValue('n'.repeat(SOURCE_NAME_MAX + 1));
    // Once inline under the field, once as the Add button's reason.
    expect((await screen.findAllByText(new RegExp(`Shorten the name to ${SOURCE_NAME_MAX}`))).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Add' })).toHaveAttribute('aria-disabled', 'true');
    dialog.findContent().findInput()!.setInputValue('Blog');
    dialog.findContent().findAllInputs()[1]!.setInputValue('https://blog.example.com/feed');
    dialog.findContent().findTextarea()!.setTextareaValue('i'.repeat(SOURCE_INSTRUCTION_MAX + 1));
    expect((await screen.findAllByText(new RegExp(`Shorten the instruction to ${SOURCE_INSTRUCTION_MAX}`))).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Add' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F2-extra 4: a Replace that removes most of the radar is confirmed', () => {
  const many = Array.from({ length: 50 }, (_, i) => ({ key: `k${i}`, title: `Entry ${i}`, sector: 'Direct', ring: 'Leader', description: '' }));

  it('counts what would go in the Replace dialog, from the dry run', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many, radar: { ...radarView().radar, entry_count: 50 } }),
      'POST /api/radars/rivals/import': (b: unknown) =>
        (b as { dry_run?: boolean }).dry_run
          ? { rows: 1, unmatched: [], duplicate_rows: [], accepted: true, before: 50, removed: 49 }
          : radarView().radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
    const upload = createWrapper(container).findFileUpload()!;
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['title\nBeta\n'], 'one.csv')] } });
    await screen.findByText('Every row matches this radar.');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(await screen.findByText(/which is 49 entries/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ mode: 'replace' }));
  });

  it('asks again and resends with the flag when the API refuses it', async () => {
    let confirmed = false;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many, radar: { ...radarView().radar, entry_count: 50 } }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        const body = b as { dry_run?: boolean; allow_empty?: boolean };
        if (body.dry_run) return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true, before: 2, removed: 1 };
        if (!body.allow_empty) {
          throw { status: 400, code: 'validation_failed', message: 'this would remove 49 of 50 entries', details: { code: 'shrinking_import', before: 50, after: 1, removed: 49 } };
        }
        confirmed = true;
        return radarView().radar;
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
    const upload = createWrapper(container).findFileUpload()!;
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['title\nBeta\n'], 'one.csv')] } });
    await screen.findByText('Every row matches this radar.');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    // The dry run did not see it (the radar grew since), so the API's refusal is what asks.
    expect(await screen.findByText('Remove 49 entries?')).toBeInTheDocument();
    expect(screen.getByText(/instead of 50/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import and remove 49 entries' }));
    await waitFor(() => expect(confirmed).toBe(true));
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ allow_empty: true });
  });

  it('reports a refusal that does not say how many, instead of showing a blank count', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        if ((b as { dry_run?: boolean }).dry_run) return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
        throw { status: 400, code: 'validation_failed', message: 'this would remove most of the radar', details: { code: 'shrinking_import' } };
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
    fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [new File(['title\nBeta\n'], 'one.csv')] } });
    await screen.findByText('Every row matches this radar.');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText('this would remove most of the radar')).toBeInTheDocument();
    expect(screen.queryByText(/^Remove /)).toBeNull();
  });

  it('can be called off, and imports nothing', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        if ((b as { dry_run?: boolean }).dry_run) return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
        throw { status: 400, code: 'validation_failed', message: 'too much', details: { code: 'shrinking_import', before: 50, after: 1, removed: 49 } };
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
    fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [new File(['title\nBeta\n'], 'one.csv')] } });
    await screen.findByText('Every row matches this radar.');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText('Remove 49 entries?')).toBeInTheDocument();
    // Esc or a click on the backdrop closes it just as Cancel does.
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByText('Remove 49 entries?')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText('Remove 49 entries?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Remove 49 entries?')).toBeNull());
    // Two refusals (one per Replace), and nothing with allow_empty: the import never happened.
    const imports = calls.filter((c) => c.path.endsWith('/import')).filter((c) => !(c.body as { dry_run?: boolean }).dry_run);
    expect(imports).toHaveLength(2);
    expect(imports.some((c) => (c.body as { allow_empty?: boolean }).allow_empty)).toBe(false);
  });
});

describe('F2-extra 5: transferring a radar is administrator-only', () => {
  const users = {
    items: [
      { username: 'u-admin', email: 'admin@example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '' },
      { username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '' },
      { username: 'u-view', email: 'viewer@example.com', role: 'viewer', enabled: true, status: 'CONFIRMED', created_at: '' },
      { username: 'u-off', email: 'off@example.com', role: 'editor', enabled: false, status: 'CONFIRMED', created_at: '' },
    ],
  };

  it('offers only enabled editors and administrators, and sends the account id', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': users,
      'PUT /api/radars/rivals/access': radarView().radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Transfer ownership' }));
    const select = createWrapper(document.body).findAllSelects().pop()!;
    select.openDropdown();
    expect(select.findDropdown({ expandToViewport: true }).findOptions().map((o) => o.getElement().textContent)).toEqual([
      expect.stringContaining('ed@example.com'),
    ]);
    select.selectOptionByValue('u-ed', { expandToViewport: true });
    fireEvent.click(screen.getByRole('button', { name: 'Transfer' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/access'))?.body).toMatchObject({ owner: { subject: 'u-ed' } }));
    expect(container).toBeTruthy();
  });

  it('is not offered to an owner who is not an administrator', async () => {
    const owner = { ...ADMIN, role: 'editor' as const, can: ['read', 'radar.edit', 'radar.admin'] };
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings', me: owner }));
    expect(await screen.findByText('Owner: admin@example.com')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Transfer ownership' })).toBeNull();
    expect(calls.some((c) => c.path === '/api/users')).toBe(false);
  });

  it('says the list is still loading', async () => {
    const { api, fetcher } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': users,
    });
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementation(async (...args: Parameters<typeof real>) => {
      if (String(args[0]).includes('/api/users')) await held;
      return real(...args);
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Transfer ownership' }));
    const select = createWrapper(document.body).findAllSelects().pop()!;
    select.openDropdown();
    expect(await screen.findByText('Loading users')).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByText('Loading users')).toBeNull());
  });

  it('reports a failed transfer and keeps the dialog open', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': () => {
        throw { status: 500, message: 'user store broken' };
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Transfer ownership' }));
    expect(await screen.findByText('The users could not be loaded: user store broken')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Transfer' })).toHaveAttribute('aria-disabled', 'true');
    // Esc or a click on the backdrop closes it too.
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Transfer' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Transfer ownership' }));
    await screen.findByRole('button', { name: 'Transfer' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Transfer' })).toBeNull());
  });
});
