// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about the product and module pages. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ModuleRun } from '@/api/types';
import { module as module_, product } from '@/test/data';
import { ADMIN, fakeApi, VIEWER, wrap } from '@/test/session';

import { GRANT_LABELS, ModulePage, ModulesPage, scheduleState } from './ModulesPage';
import { draftErrors, PRODUCT_POLL_MS, PRODUCT_POLL_TRIES, ProductPage, toDraft, withoutQuery } from './ProductsPage';

const productRoutes = <Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>;
const moduleRoutes = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
const EDITOR = { ...ADMIN, role: 'editor' as const, can: ['read', 'product.read'] };

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('F3-003: a failed poll keeps the product page and its Edit dialog', () => {
  it('keeps the dialog and the typed text, with a banner, when a re-read fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fail = false;
    const { api } = fakeApi({
      'GET /api/products/p1': () => {
        if (fail) throw { status: 500, message: 'Internal error' };
        return product({ web_status: 'indexing' });
      },
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.change(within(visibleModal().getElement()).getByDisplayValue('Premium segment'), { target: { value: 'Rewritten notes' } });
    fail = true;
    await act(async () => void vi.advanceTimersByTime(PRODUCT_POLL_MS + 10));
    expect(await screen.findByText('The product could not be read again')).toBeInTheDocument();
    expect(screen.queryByText('This product could not be loaded')).toBeNull();
    expect(within(visibleModal().getElement()).getByDisplayValue('Rewritten notes')).toBeInTheDocument();
  });
});

describe('F3-013: a product edit that lost a race can be saved again', () => {
  it('reads the product again and keeps the dialog, so the next Save sends the new version', async () => {
    let version = 1;
    const sent: number[] = [];
    const { api } = fakeApi({
      'GET /api/products/p1': () => product({ version }),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'PATCH /api/products/p1': (b: unknown) => {
        sent.push((b as { version: number }).version);
        if ((b as { version: number }).version !== 2) {
          version = 2;
          throw { status: 409, code: 'conflict', message: 'product was changed by someone else' };
        }
        return product({ version: 3 });
      },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.change(within(visibleModal().getElement()).getByDisplayValue('Premium segment'), { target: { value: 'Mine' } });
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Someone else changed this product while you were editing/)).toBeInTheDocument();
    expect(within(visibleModal().getElement()).getByDisplayValue('Mine')).toBeInTheDocument();
    await waitFor(() => expect(within(visibleModal().getElement()).getByRole('button', { name: 'Save' })).not.toHaveAttribute('aria-disabled'));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([1, 2]));
  });

  it('still reports any other failure as it is', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'PATCH /api/products/p1': () => {
        throw { status: 400, message: 'name is too long' };
      },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.change(within(visibleModal().getElement()).getByDisplayValue('Premium segment'), { target: { value: 'Mine' } });
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('name is too long')).toBeInTheDocument();
  });
});

describe('F3-053: Save with nothing changed', () => {
  it('is disabled until the draft differs', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(within(visibleModal().getElement()).getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F3-023, F3-024: what non-managers see on a product', () => {
  it('shows no "Used by" line, no crawl errors and no query strings to an editor', async () => {
    const { api, calls } = fakeApi({
      'GET /api/products/p1': product({ doc_urls: ['https://docs.example.com/guide?token=secret', 'https://docs.example.com/'], stats: { crawl: { errors: ['https://docs.example.com/guide?token=secret: 403'] } } }),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1', me: EDITOR }));
    expect(await screen.findByText('docs.example.com/guide')).toBeInTheDocument();
    expect(screen.getByText('docs.example.com')).toBeInTheDocument();
    expect(screen.queryByText(/token=secret/)).toBeNull();
    expect(screen.queryByText(/Last crawl problems/)).toBeNull();
    expect(screen.queryByText(/No module uses this product yet/)).toBeNull();
    expect(calls.some((c) => c.path === '/api/modules')).toBe(false);
  });

  it('cuts a query off an address it cannot parse too', () => {
    expect(withoutQuery('not a url?token=x')).toBe('not a url');
    expect(withoutQuery('https://docs.example.com/a/b?x=1#f')).toBe('docs.example.com/a/b');
  });
});

describe('F3-054: the item limits are checked before Save', () => {
  it('counts documentation URLs and list items', () => {
    const many = (n: number, f: (i: number) => string) => Array.from({ length: n }, (_, i) => f(i)).join('\n');
    const d = { ...toDraft(product()), doc_urls: many(41, (i) => `https://d${i}.example.com/`), capabilities: many(101, (i) => `c${i}`), segments: 'a', competitors: many(101, (i) => `x${i}`) };
    expect(draftErrors(d)).toMatchObject({ doc_urls: 'At most 40 lines (this has 41).', capabilities: 'At most 100 lines (this has 101).', competitors: 'At most 100 lines (this has 101).' });
    expect(draftErrors(d)).not.toHaveProperty('segments');
    // A bad address is still the first thing said about the URLs.
    expect(draftErrors({ ...d, doc_urls: `${d.doc_urls}\nhttp://x` }).doc_urls).toMatch(/full https:\/\/ address/);
  });
});

describe('F3-052, F3-018: a stopped poll and a failed documents load say so', () => {
  it('offers Refresh once polling gave up, and Retry for documents', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let docsDown = true;
    const { api } = fakeApi({
      'GET /api/products/p1': product({ web_status: 'indexing' }),
      'GET /api/products/p1/documents': () => {
        if (docsDown) throw { status: 500, message: 'documents are down' };
        return { items: [] };
      },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    expect(await screen.findByText('The documents could not be loaded: documents are down')).toBeInTheDocument();
    await act(async () => void vi.advanceTimersByTime((PRODUCT_POLL_TRIES + 2) * PRODUCT_POLL_MS));
    expect(await screen.findByText(/This page stopped checking for progress after 15 minutes/)).toBeInTheDocument();
    docsDown = false;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(screen.queryByText(/stopped checking/)).toBeNull());
    expect(await screen.findByText('No documents.')).toBeInTheDocument();
  });
});

describe('F3-105: a long passage can be read whole', () => {
  it('offers Show more and Show less', async () => {
    const long = `${'a'.repeat(700)}TAIL`;
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'POST /api/products/p1/ask': { items: [{ source: 'docs', text: long }, { source: 'docs', text: 'short' }] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    fireEvent.change(await screen.findByLabelText('Question'), { target: { value: 'residency' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Show more' }));
    expect(screen.getByText(/TAIL/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(screen.queryByText(/TAIL/)).toBeNull();
  });
});

const moduleApi = (over: Record<string, unknown> = {}) =>
  fakeApi({ 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/scout/runs': { items: [] }, 'GET /api/modules/scout/sources': { items: [] }, 'GET /api/nav': { builtin: [], custom: [] }, ...over });

describe('F3-013: a module configuration that lost a race keeps the edits', () => {
  it('carries the edits onto the version read again, and saves them with it', async () => {
    let version = 1;
    const sent: { version: number; instructions?: string }[] = [];
    const { api, calls } = moduleApi({
      'GET /api/modules/scout': () => module_({ version }),
      'PATCH /api/modules/scout': (b: unknown) => {
        sent.push(b as { version: number });
        if ((b as { version: number }).version !== 2) {
          version = 2;
          throw { status: 409, code: 'conflict', message: 'the module was changed by someone else' };
        }
        return module_({ version: 3 });
      },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    fireEvent.change(await screen.findByDisplayValue('Agent platforms'), { target: { value: 'My rewrite' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Someone else changed this module while you were editing/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET' && c.path === '/api/modules/scout')).toHaveLength(2));
    await act(async () => new Promise((r) => setTimeout(r, 50))); // the answer to that re-read lands
    expect(await screen.findByDisplayValue('My rewrite')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.map((s) => s.version)).toEqual([1, 2]));
    expect(sent[1]).toMatchObject({ instructions: 'My rewrite' });
  });
});

describe('F3-013: other module save failures', () => {
  it('are reported as they are', async () => {
    const { api } = moduleApi({
      'GET /api/modules/scout': module_(),
      'PATCH /api/modules/scout': () => {
        throw { status: 400, message: 'budget too large' };
      },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    fireEvent.change(await screen.findByDisplayValue('Agent platforms'), { target: { value: 'My rewrite' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('budget too large')).toBeInTheDocument();
  });
});

describe('F3-028: the drafts module has its own switch', () => {
  it('says what enabled means for drafts, and sets no schedule', async () => {
    expect(scheduleState({ enabled: true, interval_minutes: 0, template: 'draft' }).text).toBe('On request (editors ask for drafts)');
    const { api, calls } = fakeApi({
      'GET /api/modules/draft': module_({ slug: 'draft', template: 'draft', interval_minutes: 0, grants: ['kg_read'] }),
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/draft/runs': { items: [] },
      'PATCH /api/modules/draft': module_({ slug: 'draft', template: 'draft', version: 2 }),
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/draft' }));
    const toggle = await screen.findByText('Let editors request drafts');
    expect(screen.queryByText('Run on schedule')).toBeNull();
    expect(screen.queryByText('Schedule')).toBeNull();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ enabled: true, interval_minutes: 0 }));
  });
});

describe('F3-055: the instructions limit', () => {
  it('blocks Create module above the limit', async () => {
    const { api } = fakeApi({ 'GET /api/modules': { items: [] }, 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<ModulesPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    fireEvent.change(document.querySelectorAll('[role="dialog"] input')[0]!, { target: { value: 'Pricing watch' } });
    fireEvent.change(document.querySelector('[role="dialog"] textarea')!, { target: { value: 'x'.repeat(20_001) } });
    expect(screen.getByText('At most 20000 characters.')).toBeInTheDocument();
    const create = within(screen.getByRole('dialog')).getByRole('button', { name: 'Create' });
    expect(create).toHaveAttribute('aria-disabled', 'true');
  });

  it('counts and blocks above 20000 characters', async () => {
    const { api } = moduleApi({ 'GET /api/modules/scout': module_() });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    fireEvent.change(await screen.findByDisplayValue('Agent platforms'), { target: { value: 'x'.repeat(20_001) } });
    expect(screen.getByText('At most 20000 characters: shorten it before saving.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F3-056, F3-057: sources', () => {
  it('asks before Esc throws a written source away, and names each switch', async () => {
    const { api } = moduleApi({
      'GET /api/modules/scout': module_(),
      'GET /api/modules/scout/sources': { items: [{ id: 's1', module: 'scout', name: 'Vendor feed', type: 'rss', enabled: true, address: 'https://v.example.com/feed' }] },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    expect(await screen.findByLabelText('Read Vendor feed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    visibleModal().findDismissButton().click(); // nothing typed: closes
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    fireEvent.change(within(visibleModal().getElement()).getAllByRole('textbox').pop()!, { target: { value: 'Long reading instruction' } });
    visibleModal().findDismissButton().click();
    expect(await screen.findByText('Discard this source?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('Long reading instruction')).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('F3-058: no MCP grant without a way to add a server', () => {
  it('does not offer "Attached MCP servers", but still names it where a module has it', async () => {
    const custom = module_({ slug: 'pw', name: 'Pricing watch', template: 'custom', builtin: false, grants: ['kg_read', 'kg_write_output', 'mcp'] });
    const { api } = fakeApi({ 'GET /api/modules/pw': custom, 'GET /api/models': { items: [] }, 'GET /api/products': { items: [] }, 'GET /api/modules/pw/runs': { items: [] } });
    const { container } = render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/pw' }));
    await screen.findByRole('heading', { name: 'Pricing watch' });
    const tools = createWrapper(container).findMultiselect()!;
    tools.openDropdown();
    const offered = tools.findDropdown({ expandToViewport: true }).findOptions().map((o) => o.getElement().textContent);
    expect(offered.some((t) => t?.includes(GRANT_LABELS.mcp as string))).toBe(false);
    expect(tools.findTokens().map((t) => t.getElement().textContent)).toContain(GRANT_LABELS.mcp);
  });
});

describe('F3-017: a running module polls one page, not every page', () => {
  it('reads only the newest page while a run is going, pauses when hidden, and pages the table', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const run = (i: number, status: ModuleRun['status'] = 'succeeded'): ModuleRun =>
      ({ run_id: `r${i}`, module: 'scout', status, trigger: 'manual', started_at: '2026-10-01T10:00:00Z', weighted_tokens: 0, nodes_written: 0, summary: '' }) as ModuleRun;
    const listed = [run(0, 'running'), ...Array.from({ length: 29 }, (_, i) => run(i + 1))];
    const { api, calls } = moduleApi({ 'GET /api/modules/scout': module_(), 'GET /api/modules/scout/runs': { items: listed } });
    const first = vi.spyOn(api, 'moduleRunsFirst').mockResolvedValue({ items: [run(0, 'succeeded')] });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    expect(await screen.findByRole('heading', { name: 'Runs' })).toBeInTheDocument();
    expect(await screen.findByText('(30)')).toBeInTheDocument();
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await act(async () => void vi.advanceTimersByTime(15_010));
    expect(first).not.toHaveBeenCalled();
    hidden.mockRestore();
    const pages = calls.filter((c) => c.path.endsWith('/runs')).length;
    first.mockRejectedValueOnce(new Error('a dropped poll says nothing'));
    await act(async () => void vi.advanceTimersByTime(15_010));
    await waitFor(() => expect(first).toHaveBeenCalledTimes(1));
    await act(async () => void vi.advanceTimersByTime(15_010));
    await waitFor(() => expect(first).toHaveBeenCalledTimes(2));
    expect(calls.filter((c) => c.path.endsWith('/runs')).length).toBe(pages); // no full re-read
    expect(await screen.findAllByText('Succeeded')).not.toHaveLength(0);
    await act(async () => void vi.advanceTimersByTime(15_010));
    expect(first).toHaveBeenCalledTimes(2); // nothing running any more
    const runsTable = createWrapper(document.body).findAllTables().find((t) => /Runs/.test(t.findHeaderSlot()?.getElement().textContent ?? ''))!;
    expect(runsTable.findRows()).toHaveLength(25);
    runsTable.findPagination()!.findNextPageButton().click();
    expect(runsTable.findRows()).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/runs')).length).toBe(pages + 1));
  });

  it('says a failed runs load is a failure (F3-018)', async () => {
    const { api } = moduleApi({ 'GET /api/modules/scout': module_(), 'GET /api/modules/scout/runs': () => { throw { status: 500, message: 'runs are down' }; }, 'GET /api/modules/scout/sources': () => { throw { status: 429, message: 'slow down' }; } });
    render(wrap(moduleRoutes, { api, path: '/modules/scout' }));
    expect(await screen.findByText('The runs could not be loaded: runs are down')).toBeInTheDocument();
    expect(await screen.findByText('The sources could not be loaded: slow down')).toBeInTheDocument();
    expect(screen.queryByText(/No sources yet/)).toBeNull();
  });

  it('keeps the module page when a re-read fails (F3-002)', async () => {
    let fail = false;
    const { api } = moduleApi({
      'GET /api/modules/scout': () => {
        if (fail) throw { status: 500, message: 'down' };
        return module_();
      },
      'PATCH /api/modules/scout': () => {
        fail = true;
        return module_({ version: 2 });
      },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/scout', me: ADMIN }));
    fireEvent.change(await screen.findByDisplayValue('Agent platforms'), { target: { value: 'Saved text' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('The module could not be read again')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Scout' })).toBeInTheDocument();
  });
});

describe('F3-028: viewers read the same schedule wording', () => {
  it('shows the schedule state', async () => {
    const { api } = fakeApi({ 'GET /api/modules/scout': module_({ enabled: false, grants: [] }) });
    render(wrap(moduleRoutes, { api, path: '/modules/scout', me: VIEWER }));
    expect(await screen.findByText('Paused (every 6 hours when on)')).toBeInTheDocument();
  });
});
