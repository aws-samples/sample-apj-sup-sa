// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import ConfirmDelete from '@/components/ConfirmDelete';
import { module, product } from '@/test/data';
import { chooseMoreAction, fakeApi, VIEWER, wrap } from '@/test/session';

import { intervalLabel, ModulePage, ModulesPage } from './ModulesPage';
import { fromDraft, ProductPage, ProductsPage, toDraft, uploadDocument } from './ProductsPage';

const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
const dialog = () => within(screen.getByRole('dialog'));

describe('ConfirmDelete', () => {
  it('shows progress while deleting and cancels', async () => {
    let done: () => void = () => undefined;
    const onConfirm = vi.fn(() => new Promise<void>((r) => (done = r)));
    const onDismiss = vi.fn();
    render(<ConfirmDelete header="Delete thing" onConfirm={onConfirm} onDismiss={onDismiss}>Sure?</ConfirmDelete>);
    const modal = createWrapper(document.body).findModal()!;
    expect(modal.findHeader().getElement()).toHaveTextContent('Delete thing');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(modal.findFooter()!.findAllButtons()[1]!.findLoadingIndicator()).not.toBeNull();
    await act(async () => done());
    expect(modal.findFooter()!.findAllButtons()[1]!.findLoadingIndicator()).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe('Products', () => {
  it('lists and creates products', async () => {
    const { api, calls } = fakeApi({ 'GET /api/products': { items: [product()] }, 'POST /api/products': product({ id: 'p2' }) });
    render(wrap(<Routes><Route path="/" element={<ProductsPage />} /><Route path="/products/:id" element={<div>opened</div>} /></Routes>, { api }));
    expect(await screen.findByText('DeskPro')).toBeInTheDocument();
    expect(screen.getByText('Ready')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    const modal = visibleModal();
    expect(modal).toBeTruthy();
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: ' New product ' } });
    const areas = document.querySelectorAll('[role="dialog"] textarea');
    fireEvent.change(areas[1]!, { target: { value: 'a\n\n b \n' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ name: 'New product', capabilities: ['a', 'b'] });
  });

  it('adds a product once however often Add is clicked, and allows a retry after a failure', async () => {
    let fails = true;
    const { api, calls } = fakeApi({
      'GET /api/products': { items: [] },
      'POST /api/products': () => {
        if (fails) { fails = false; throw { status: 500, message: 'try again' }; }
        return new Promise((resolve) => setTimeout(() => resolve(product({ id: 'p2' })), 20));
      },
    });
    render(wrap(<Routes><Route path="/" element={<ProductsPage />} /><Route path="/products/:id" element={<div>opened</div>} /></Routes>, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add product' }));
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: 'New' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('try again')).toBeInTheDocument();
    const add = screen.getByRole('button', { name: 'Add' });
    fireEvent.click(add);
    fireEvent.click(add);
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2); // the failed one and one retry
  });

  it('shows a dash for a product without a summary and closes the form with its close button', async () => {
    const { api, calls } = fakeApi({ 'GET /api/products': { items: [product({ summary: '' })] } });
    render(wrap(<ProductsPage />, { api }));
    expect(await screen.findByText('-')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('shows an empty profile, finds no passages and offers a drop zone', async () => {
    const empty = product({ capabilities: [], segments: [], competitors: [], web_crawled_at: null, web_pages: 0, web_status: 'none', stats: {} });
    const { api } = fakeApi({ 'GET /api/products/p1': empty, 'GET /api/products/p1/documents': { items: [] }, 'POST /api/products/p1/ask': { items: [] } });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText('No documentation')).toBeInTheDocument();
    expect(screen.getAllByText('-')).toHaveLength(3);
    expect(screen.getByText('0 pages')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'usb?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('No passages found.')).toBeInTheDocument();
    fireEvent.dragEnter(document, { dataTransfer: { types: ['Files'], items: [], files: [] } });
    fireEvent.dragOver(document, { dataTransfer: { types: ['Files'], items: [], files: [] } });
    expect(await screen.findByText('Drop files')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('shows which modules use a product and links to them', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [module({ slug: 'gap', name: 'Gaps', product_id: 'p1' }), module({ slug: 'trend', name: 'Trends' }), module({ slug: 'content', name: 'Content ideas', product_id: 'p1' })] },
    });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /><Route path="/modules/:slug" element={<div>gap module</div>} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText('Used by:', { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Gaps' }));
    expect(await screen.findByText('gap module')).toBeInTheDocument();
  });

  it('says when no module uses a product yet', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText(/No module uses this product yet/)).toBeInTheDocument();
  });

  it('opens a product from the list', async () => {
    const { api } = fakeApi({ 'GET /api/products': { items: [product()] } });
    render(wrap(<Routes><Route path="/" element={<ProductsPage />} /><Route path="/products/:id" element={<div>opened p1</div>} /></Routes>, { api }));
    fireEvent.click(await screen.findByRole('link', { name: 'DeskPro' }));
    expect(await screen.findByText('opened p1')).toBeInTheDocument();
  });

  it('reports missing products and failed searches and uploads', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'POST /api/products/p1/ask': () => { throw { status: 503, message: 'knowledge is down' }; },
      'POST /api/products/p1/documents': () => { throw { status: 413, message: 'too large' }; },
    });
    const page = () => render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    const first = page();
    fireEvent.change(await screen.findByLabelText('Question'), { target: { value: 'usb?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('Knowledge is down')).toBeInTheDocument(); // a throttle reads as a sentence (F2-015)
    first.unmount();
    const { container, unmount } = page();
    await screen.findByRole('heading', { name: 'DeskPro' });
    const upload = createWrapper(container).findFileUpload()!.findNativeInput().getElement();
    fireEvent.change(upload, { target: { files: [new File(['abc'], 'big.pdf')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByText(/0 of 1 uploaded\. too large/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    unmount();
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/nope' }));
    expect(await screen.findByText('Product not found')).toBeInTheDocument();
  });

  it('converts drafts both ways', () => {
    expect(fromDraft(toDraft(product()))).toMatchObject({ name: 'DeskPro', doc_urls: ['https://docs.example.com/'] });
    expect(toDraft().name).toBe('');
  });

  it('shows a product, searches its knowledge and manages documents', async () => {
    const { api, calls } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [{ id: 'd1', product_id: 'p1', filename: 'spec.pdf', content_type: 'application/pdf', size: 2048, status: 'indexing', uploaded_by: 'u', uploaded_at: '2026-10-01T10:00:00Z' }] },
      'POST /api/products/p1/ask': { items: [{ text: 'x'.repeat(800), source: 'https://docs.example.com/a', score: 0.5, product_id: 'p1' }, { text: 'short', source: 'document:spec.pdf', score: 0.4, product_id: 'p1' }] },
      'POST /api/products/p1/sync': {},
      'PATCH /api/products/p1': product({ summary: 'Updated' }),
      'DELETE /api/products/p1/documents/d1': {},
      'DELETE /api/products/p1': {},
      'POST /api/products/p1/documents': { document: { id: 'd2' }, upload: { url: 'https://s3.example.com/', fields: { key: 'docs/p1/d2/a.txt' } } },
      'POST /api/products/p1/documents/d2/complete': {},
    });
    const post = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(null, { status: 204 }));
    const { container } = render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /><Route path="/products" element={<div>list</div>} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByRole('heading', { name: 'DeskPro' })).toBeInTheDocument();
    expect(screen.getByText(/page limit reached/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'usb?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    // F2-111: the host and the page, with the full address in the title; a long URL wrapped over three lines.
    expect(await screen.findByText('docs.example.com \u2013 a')).toBeInTheDocument();
    expect(screen.getByText(/document:spec.pdf/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Re-read web pages' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/sync'))).toBe(true));
    await screen.findByText('spec.pdf');
    fireEvent.click(screen.getByRole('button', { name: 'Delete spec.pdf' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete spec.pdf' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete spec.pdf?');
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(dialog().getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path.endsWith('/d1'))).toBe(true));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const upload = createWrapper(container).findFileUpload()!.findNativeInput().getElement();
    fireEvent.change(upload, { target: { files: [new File(['abc'], 'a.txt', { type: 'text/plain' })] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/d2/complete'))).toBe(true));
    expect(post).toHaveBeenCalledWith('https://s3.example.com/', expect.objectContaining({ method: 'POST' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(screen.getByRole('dialog')).getByDisplayValue('Premium segment'), { target: { value: 'Premium segment, EU' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // Delete sits in a menu, away from Edit (the breadcrumbs have a menu of their own).
    const more = createWrapper(container).findAllButtonDropdowns().find((b) => b.findNativeButton().getElement().getAttribute('aria-label') === 'More actions')!;
    more.openDropdown();
    more.findItemById('delete')!.click();
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete the product "DeskPro"?');
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.path === '/api/products/p1' && c.method === 'DELETE')).toBe(false);
    more.openDropdown();
    more.findItemById('delete')!.click();
    fireEvent.click(dialog().getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('list')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/api/products/p1' && c.method === 'DELETE')).toBe(true);
    post.mockRestore();
  });

  it('retries only the files a partly failed upload did not store', async () => {
    let failB = true;
    const { api, calls } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'POST /api/products/p1/documents': (body: unknown) => {
        if ((body as { filename: string }).filename === 'b.txt' && failB) {
          failB = false;
          throw { status: 500, message: 'b failed' };
        }
        return { document: { id: 'd' }, upload: { url: 'https://s3.example.com/', fields: {} } };
      },
      'POST /api/products/p1/documents/d/complete': {},
    });
    const post = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(null, { status: 204 }));
    const { container } = render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    await screen.findByRole('heading', { name: 'DeskPro' });
    const files = ['a.txt', 'b.txt', 'c.txt'].map((n) => new File(['x'], n, { type: 'text/plain' }));
    fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files } });
    const listed = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/documents')).length;
    const before = listed();
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByText(/^1 of 3 uploaded\. b failed/)).toBeInTheDocument(); // says what was stored
    await waitFor(() => expect(listed()).toBe(before + 1));
    const names = () => createWrapper(container).findFileUpload()!.findFileTokens().map((t) => t.findFileName().getElement().textContent);
    expect(names()).toEqual(['b.txt', 'c.txt']);
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(names()).toEqual([]));
    const presigned = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/documents')).map((c) => (c.body as { filename: string }).filename);
    expect(presigned).toEqual(['a.txt', 'b.txt', 'b.txt', 'c.txt']);
    post.mockRestore();
  });

  it('reports failed uploads and hides management from viewers', async () => {
    const { api, calls } = fakeApi({ 'POST /api/products/p1/documents': { document: { id: 'd' }, upload: { url: 'https://s3.example.com/', fields: {} } }, 'GET /api/products/p1': product({ web_status: 'failed', stats: {} }), 'GET /api/products/p1/documents': { items: [] } });
    await expect(uploadDocument(api, 'p1', new File(['x'], 'x.txt'), (async () => new Response('', { status: 403 })) as unknown as typeof fetch)).rejects.toThrow('failed (403)');
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1', me: VIEWER }));
    expect(await screen.findByText('Failed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    // r2 SR2-12: a viewer neither searches the documents nor lists them, and the page does not ask for them.
    expect(screen.getByText(/Editors and administrators can search this product's documents/)).toBeInTheDocument();
    expect(screen.queryByText('Search product knowledge')).toBeNull();
    expect(calls.some((c) => c.path.endsWith('/documents') && c.method === 'GET')).toBe(false);
  });
});

describe('Modules', () => {
  it('lists modules and creates a custom one', async () => {
    const { api, calls } = fakeApi({ 'GET /api/modules': { items: [module(), module({ slug: 'w', name: 'Watch', enabled: true, interval_minutes: 45 })] }, 'POST /api/modules': module({ slug: 'pricing-watch' }) });
    render(wrap(<Routes><Route path="/" element={<ModulesPage />} /><Route path="/modules/:slug" element={<div>opened</div>} /></Routes>, { api }));
    expect(await screen.findByText('Scout')).toBeInTheDocument();
    expect(screen.getByText('Every 45 minutes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create module' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create module' }));
    const inputs = document.querySelectorAll('[role="dialog"] input');
    fireEvent.change(inputs[2]!, { target: { value: 'Pricing moves' } });
    fireEvent.change(inputs[0]!, { target: { value: 'Pricing watch' } });
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Find pricing changes by vendors and publish one insight each.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ slug: 'pricing-watch', insight_kind: 'pricing_watch', insight_label: 'Pricing moves', interval_minutes: 0 });
  });

  it('names a new module after itself and lets you choose its tools', async () => {
    const { api, calls } = fakeApi({ 'GET /api/modules': { items: [] }, 'POST /api/modules': module({ slug: 'deal-watch' }) });
    render(wrap(<Routes><Route path="/" element={<ModulesPage />} /><Route path="/modules/:slug" element={<div>opened</div>} /></Routes>, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    fireEvent.change(document.querySelectorAll('[role="dialog"] input')[0]!, { target: { value: 'Deal watch' } });
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Track large deals by competitors.\nIgnore rumours and unsourced claims.' } });
    const tools = visibleModal().findContent().findMultiselect()!;
    tools.openDropdown();
    tools.selectOptionByValue('web_search', { expandToViewport: true }); // toggles it off
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({
      insight_label: 'Deal watch',
      description: 'Track large deals by competitors.',
      grants: ['kg_read', 'kg_write_output', 'web_fetch'],
    });
  });

  it('needs an address when the name has no letters or digits to make one from', async () => {
    const { api, calls } = fakeApi({ 'GET /api/modules': { items: [] }, 'POST /api/modules': module({ slug: 'price-watch' }) });
    render(wrap(<Routes><Route path="/" element={<ModulesPage />} /><Route path="/modules/:slug" element={<div>opened</div>} /></Routes>, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    const inputs = () => document.querySelectorAll('[role="dialog"] input');
    fireEvent.change(inputs()[0]!, { target: { value: '\u4fa1\u683c\u76e3\u8996' } });
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Find pricing changes by vendors and publish one insight each.' } });
    expect(screen.getByRole('button', { name: 'Create' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getAllByText(/Enter an address/).length).toBeGreaterThan(0);
    fireEvent.change(inputs()[1]!, { target: { value: 'Price Watch' } });
    expect(screen.getByDisplayValue('price-watch')).toBeInTheDocument();
    expect(screen.queryByText(/Enter an address/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ slug: 'price-watch', insight_kind: 'price_watch' });
  });

  it('sets the schedule, product, budgets and tools of a custom module', async () => {
    const custom = module({ slug: 'pw', name: 'Pricing watch', template: 'custom', builtin: false, grants: ['kg_read', 'future_tool'], product_id: 'p1' });
    const { api, calls } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [product()] },
      'GET /api/modules/pw/runs': { items: [] },
      'PATCH /api/modules/pw': custom,
    });
    const { container } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/pw' }));
    expect(await screen.findByText('future_tool')).toBeInTheDocument();
    const w = createWrapper(container);
    const selects = w.findAllSelects();
    selects[0]!.openDropdown();
    selects[0]!.selectOptionByValue('1440', { expandToViewport: true });
    await waitFor(() => expect(w.findAllSelects()[2]!.findTrigger().getElement()).toHaveTextContent('DeskPro'));
    selects[2]!.openDropdown();
    selects[2]!.selectOptionByValue('', { expandToViewport: true });
    const budget = screen.getByDisplayValue('2000000');
    fireEvent.change(budget, { target: { value: '5000' } });
    fireEvent.change(budget, { target: { value: '0' } }); // typed 0 is a real setting: it stops the module
    const concurrency = screen.getByDisplayValue('1');
    fireEvent.change(concurrency, { target: { value: '3' } });
    fireEvent.change(concurrency, { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(concurrency, { target: { value: '1' } });
    const tools = w.findMultiselect()!;
    tools.openDropdown();
    tools.selectOptionByValue('web_fetch', { expandToViewport: true });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({
      interval_minutes: 1440,
      product_id: null,
      daily_token_budget: 0,
      max_concurrency: 1,
      grants: ['kg_read', 'future_tool', 'web_fetch'],
    }));
  });

  it('lists unknown tools of a built-in module by name and shows sources not read yet', async () => {
    const scout = module({ grants: ['sources', 'future_tool'] });
    const { api, calls } = fakeApi({
      'GET /api/modules/scout': scout,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/scout/runs': { items: [] },
      'GET /api/modules/scout/sources': { items: [{ id: 's1', module: 'scout', name: 'Blog', type: 'rss', address: 'https://blog.example.com/feed', instruction: '', cursor: '', enabled: true, created_at: 'x', updated_at: 'x' }] },
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText('Tools: Crawl configured sources, future_tool')).toBeInTheDocument();
    expect(await screen.findByText('Blog')).toBeInTheDocument();
    expect(screen.getByText('-')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('shows viewers a source without its configuration', async () => {
    const { api } = fakeApi({
      'GET /api/modules/scout': module({ grants: ['sources'] }),
      'GET /api/modules/scout/runs': { items: [] },
      'GET /api/modules/scout/sources': { items: [{ id: 's1', module: 'scout', name: 'Vendor feed', type: 'public_api', enabled: true }] },
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout', me: VIEWER }));
    expect(await screen.findByText('Vendor feed')).toBeInTheDocument();
    expect(screen.getAllByText('-')).toHaveLength(2); // address and read-up-to are hidden
  });

  it('tells viewers when an enabled module runs', async () => {
    const { api } = fakeApi({ 'GET /api/modules/scout': module({ enabled: true, grants: [] }) });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout', me: VIEWER }));
    expect(await screen.findByText('Every 6 hours')).toBeInTheDocument();
  });

  it('opens a module from the list and reports a missing one', async () => {
    const { api } = fakeApi({ 'GET /api/modules': { items: [module()] } });
    render(wrap(<Routes><Route path="/" element={<ModulesPage />} /><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api }));
    fireEvent.click(await screen.findByRole('link', { name: 'Scout' }));
    expect(await screen.findByText('Module not found')).toBeInTheDocument();
  });

  it('warns when a module that compares against a product has none', async () => {
    const { api } = fakeApi({
      'GET /api/modules/gap': module({ slug: 'gap', name: 'Gaps', output_kind: 'gap', grants: [] }),
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/gap/runs': { items: [{ module: 'gap', run_id: 'r0', status: 'succeeded', trigger: 'schedule', started_at: '2026-10-01T08:00:00Z', requested_by: 'u', finished_at: 'x', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 10, nodes_written: 0, summary: 'Nothing new.', error: null }] },
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/gap' }));
    expect(await screen.findByText(/without one this module has nothing to compare the market against/)).toBeInTheDocument();
    expect(await screen.findByText(/Nothing new to work from/)).toBeInTheDocument();
  });

  it('configures, runs and shows sources and runs', async () => {
    const runs = [
      { module: 'scout', run_id: 'r1', status: 'succeeded', trigger: 'manual', started_at: '2026-10-01T10:00:00Z', requested_by: 'u', finished_at: 'x', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 1200, nodes_written: 3, summary: 'Wrote **three** nodes', error: null },
      { module: 'scout', run_id: 'r2', status: 'failed', trigger: 'schedule', started_at: '2026-10-01T09:00:00Z', requested_by: 'u', finished_at: 'x', input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 0, nodes_written: 0, summary: '', error: 'EventLoopException' },
      { module: 'scout', run_id: 'r0', status: 'succeeded', trigger: 'schedule', started_at: '2026-10-01T08:00:00Z', requested_by: 'u', finished_at: 'x', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 10, nodes_written: 0, summary: 'Nothing new.', error: null },
      { module: 'scout', run_id: 'r3', status: 'running', trigger: 'manual', started_at: '2026-10-01T11:00:00Z', requested_by: 'u', finished_at: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 0, nodes_written: 0, summary: '', error: null },
    ];
    const { api, calls } = fakeApi({
      'GET /api/modules/scout': module(),
      'GET /api/models': { items: [{ key: 'sonnet-5-5', label: 'Claude Sonnet 5.5', tier: 'balanced', price_in_out: [3, 15], default: true }, { key: 'haiku-4-5', label: 'Claude Haiku 4.5', tier: 'fast', price_in_out: [1, 5], default: false }] },
      'GET /api/products': { items: [product()] },
      'GET /api/modules/scout/sources': { items: [{ id: 's1', module: 'scout', name: 'Blog', type: 'rss', address: 'https://blog.example.com/feed', instruction: '', cursor: '2026-10-01', enabled: true, created_at: 'x', updated_at: 'x' }] },
      'GET /api/modules/scout/runs': { items: runs },
      'PATCH /api/modules/scout': module({ version: 2 }),
      'POST /api/modules/scout/run': runs[2],
      'POST /api/modules/scout/sources': {},
      'PATCH /api/modules/scout/sources/s1': {},
      'DELETE /api/modules/scout/sources/s1': {},
    });
    const { container } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText('Blog')).toBeInTheDocument();
    expect(await screen.findByText('1.2k')).toBeInTheDocument();
    const w = createWrapper(container);
    w.findToggle()!.findNativeInput().click();
    const selects = w.findAllSelects();
    selects[1]!.openDropdown();
    selects[1]!.selectOptionByValue('haiku-4-5', { expandToViewport: true });
    expect(screen.queryByText('Product')).toBeNull(); // Scout compares nothing against a product
    const budget = screen.getByDisplayValue('2000000');
    const concurrency = screen.getByDisplayValue('1');
    fireEvent.change(budget, { target: { value: '' } }); // a cleared field must not save as a 0 budget
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(budget, { target: { value: '5000' } });
    fireEvent.change(concurrency, { target: { value: '0' } });
    expect(screen.getByText('Enter a whole number of at least 1.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(concurrency, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH' && c.path === '/api/modules/scout')?.body).toMatchObject({ enabled: true, model_key: 'haiku-4-5', product_id: null, daily_token_budget: 5000, max_concurrency: 2, version: 1 }));
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/run'))).toBe(true));
    w.findAllExpandableSections()[0]!.findExpandButton().click();
    expect(await screen.findByText('three')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    const sourceType = createWrapper(document.querySelector('[role="dialog"]') as HTMLElement).findSelect()!;
    sourceType.openDropdown();
    sourceType.selectOptionByValue('web_page', { expandToViewport: true });
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'Only release notes' } });
    const dialogInputs = document.querySelectorAll('[role="dialog"] input');
    fireEvent.change(dialogInputs[0]!, { target: { value: 'Forum' } });
    fireEvent.change(dialogInputs[dialogInputs.length - 1]!, { target: { value: 'https://forum.example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.path.endsWith('/sources'))?.body).toMatchObject({ type: 'web_page', instruction: 'Only release notes' }));
    await waitFor(() => expect(visibleModal()).toBeUndefined());
    // The Sources table (Configuration has an MCP servers table above it).
    const sources = w.findAllTables().find((t) => t.findHeaderSlot()?.getElement().textContent?.includes('Sources'))!;
    sources.findBodyCell(1, 6)!.findToggle()!.findNativeInput().click();
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH' && c.path.endsWith('/sources/s1'))?.body).toEqual({ enabled: false }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Blog' }));
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete Blog' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete the source "Blog"?');
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(dialog().getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'DELETE')?.path).toBe('/api/modules/scout/sources/s1'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  }, 20_000); // walks the whole module page: configuration, runs and sources

  it('edits custom modules and limits viewers', async () => {
    const custom = module({ slug: 'pw', name: 'Pricing watch', template: 'custom', builtin: false, grants: ['kg_read', 'kg_write_output'], interval_minutes: 0 });
    const { api, calls } = fakeApi({ 'GET /api/modules/pw': custom, 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/pw/runs': { items: [] }, 'PATCH /api/modules/pw': custom, 'DELETE /api/modules/pw': {} });
    const { unmount } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /><Route path="/modules" element={<div>list</div>} /></Routes>, { api, path: '/modules/pw' }));
    expect(await screen.findByRole('heading', { name: 'Pricing watch' })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('Agent platforms'), { target: { value: 'New task' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ instructions: 'New task', grants: ['kg_read', 'kg_write_output'], name: 'Pricing watch' }));
    await chooseMoreAction('Delete module');
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete the module "Pricing watch"?');
    fireEvent.click(dialog().getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await chooseMoreAction('Delete module');
    fireEvent.click(dialog().getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('list')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/modules/pw')).toBe(true);
    unmount();
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/pw', me: VIEWER }));
    expect(await screen.findByText('On demand (Run now)')).toBeInTheDocument();
    expect(intervalLabel(10080)).toBe('Weekly');
  });
});

