// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { Source } from '@/api/types';
import { module } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import { ModulePage, sourcesShown, WatchCell } from './ModulesPage';

const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;
const scout = module({ slug: 'scout', name: 'Scout', template: 'scout', output_kind: undefined, grants: ['sources'] });
const at = '2026-10-30T08:00:00Z';
const watch = (status: 'ok' | 'empty' | 'error', pending = 0, error = '') => ({ checked_at: at, status, error, items: 5, pending, dropped: 0 });
const src = (id: string, extra: Partial<Source> = {}): Source => ({ id, module: 'scout', name: `Source ${id}`, type: 'rss', address: `https://${id}.example.com/feed`, enabled: true, ...extra });

const SOURCES: Source[] = [
  src('a', { watch: watch('ok', 3) }),
  src('b', { watch: watch('ok') }),
  src('c', { watch: watch('error', 0, 'FetchError: returned HTTP 403') }),
  src('d', { watch: watch('empty') }),
  src('e', { watch: null }),
];

const routes = (items: Source[], check: () => unknown = () => ({ requested: true })) => ({
  'GET /api/modules/scout': scout,
  'GET /api/models': { items: [] },
  'GET /api/products': { items: [] },
  'GET /api/modules/scout/runs': { items: [], cursor: null },
  'GET /api/modules/scout/sources': { items },
  'POST /api/modules/scout/sources/check': check,
  'PATCH /api/modules/scout/sources/a': () => SOURCES[0],
});

describe('the source watcher on the Sources table', () => {
  it('shows what the watcher last saw on each source', async () => {
    render(wrap(page, { api: fakeApi(routes(SOURCES)).api, path: '/modules/scout' }));
    // The first render in the file loads the page cold: over the 1 s default under a busy full run (r34 gates).
    expect(await screen.findByText('3 new waiting', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('Up to date')).toBeInTheDocument();
    expect(screen.getByText('FetchError: returned HTTP 403')).toBeInTheDocument();
    expect(screen.getByText('Lists no items')).toBeInTheDocument();
    expect(screen.getByText('Not checked yet')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Last check' })).toBeInTheDocument();
    expect(screen.getByText('(5)')).toBeInTheDocument();
  });

  it('filters by name or address and to the sources with problems', async () => {
    const { container } = render(wrap(page, { api: fakeApi(routes(SOURCES)).api, path: '/modules/scout' }));
    await screen.findByText('3 new waiting');
    const w = createWrapper(container);
    w.findTextFilter()!.findInput().setInputValue('a.example');
    expect((await screen.findAllByText('1 match')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Up to date')).toBeNull();
    w.findTextFilter()!.findInput().setInputValue('');
    w.findAllToggles().find((t) => t.getElement().textContent?.includes('Only sources with problems'))!.findNativeInput().click();
    expect(await screen.findByText('Lists no items')).toBeInTheDocument(); // the count shows only with a search text
    expect(screen.getByText('Lists no items')).toBeInTheDocument();
    expect(screen.queryByText('3 new waiting')).toBeNull();
  });

  it('pages a long list 25 at a time', async () => {
    const many = Array.from({ length: 30 }, (_, i) => src(`s${String(i).padStart(2, '0')}`, { watch: watch('ok') }));
    const { container } = render(wrap(page, { api: fakeApi(routes(many)).api, path: '/modules/scout' }));
    expect(await screen.findByText('Source s00')).toBeInTheDocument();
    expect(screen.queryByText('Source s29')).toBeNull();
    createWrapper(container).findPagination()!.findNextPageButton().click();
    expect(await screen.findByText('Source s29')).toBeInTheDocument();
  });

  it('asks for a check now and says so, or says why it could not', async () => {
    const ok = fakeApi(routes(SOURCES));
    const { unmount } = render(wrap(page, { api: ok.api, path: '/modules/scout' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
    expect(await screen.findByText(/Checking every source for new items/)).toBeInTheDocument();
    expect(ok.calls.some((c) => c.method === 'POST' && c.path === '/api/modules/scout/sources/check')).toBe(true);
    unmount();
    const failing = fakeApi(routes(SOURCES, () => { throw { status: 400, message: 'this deployment has no source watcher' }; }));
    render(wrap(page, { api: failing.api, path: '/modules/scout' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(screen.getByText('this deployment has no source watcher')).toBeInTheDocument());
  });

  it('gives a viewer neither the watch column nor the problem filter', async () => {
    const plain = SOURCES.map((s) => ({ ...s, watch: undefined }));
    render(wrap(page, { api: fakeApi(routes(plain)).api, path: '/modules/scout', me: VIEWER }));
    expect(await screen.findByText('Source a')).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Last check' })).toBeNull();
    expect(screen.queryByText('Only sources with problems')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull();
  });

  it('reads a watch without an error text and filters on a missing address', () => {
    render(<WatchCell watch={{ ...watch('error'), error: '' }} />);
    expect(screen.getByText('Could not read it')).toBeInTheDocument();
    expect(sourcesShown([{ ...src('x'), address: undefined }], 'zzz', false)).toEqual([]);
    expect(sourcesShown([src('x', { watch: undefined })], '', true)).toEqual([]);
  });

  it('starts a source over after asking, and reports a failure', async () => {
    const { api, calls } = fakeApi(routes(SOURCES));
    render(wrap(page, { api, path: '/modules/scout' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Start Source a over' }));
    expect(screen.getByText(/items from the last 24\s+hours wait for the Scout again/)).toBeInTheDocument();
    const dialog = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    fireEvent.click(within(dialog.getElement()).getByRole('button', { name: 'Start over' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ cursor: '' }));
    expect(await screen.findByText(/"Source a" starts over/)).toBeInTheDocument();
  });

  it('keeps the dialog when starting over fails, and closes it on Cancel', async () => {
    const { api } = fakeApi({ ...routes(SOURCES), 'PATCH /api/modules/scout/sources/a': () => { throw { status: 500, message: 'busy table' }; } });
    render(wrap(page, { api, path: '/modules/scout' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Start Source a over' }));
    const dialog = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    fireEvent.click(within(dialog().getElement()).getByRole('button', { name: 'Start over' }));
    expect(await screen.findByText('busy table')).toBeInTheDocument();
    fireEvent.click(within(dialog().getElement()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Start Source a over' }));
    dialog().findDismissButton().click();
    await waitFor(() => expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(false));
  });

  it('drops the fixed-width columns in a narrow container and keeps what they said under the address', async () => {
    const original = globalThis.ResizeObserver;
    globalThis.ResizeObserver = vi.fn(function (this: unknown, cb: (entries: unknown[]) => void) {
      // A full entry: Cloudscape's own observers read the box sizes.
      const box = [{ inlineSize: 600, blockSize: 20 }];
      return { observe: (target: Element) => cb([{ target, contentRect: { width: 600, height: 20 }, borderBoxSize: box, contentBoxSize: box }]), unobserve: vi.fn(), disconnect: vi.fn() };
    }) as unknown as typeof ResizeObserver;
    try {
      const items = [src('a', { read_at: at, cursor: at }), src('b', { watch: watch('error', 0, 'FetchError: returned HTTP 403') })];
      render(wrap(page, { api: fakeApi(routes(items)).api, path: '/modules/scout' }));
      expect(await screen.findByText('Last read never')).toBeInTheDocument();
      expect(screen.getByText(/^Read up to .+, last read /)).toBeInTheDocument();
      expect(screen.getByText('FetchError: returned HTTP 403')).toBeInTheDocument(); // the watcher status stays visible
      expect(screen.queryByRole('columnheader', { name: 'Last check' })).toBeNull();
      expect(screen.queryByRole('columnheader', { name: 'Read up to' })).toBeNull();
    } finally {
      globalThis.ResizeObserver = original;
    }
  });
});
