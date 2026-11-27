// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 red-team findings about list loads that fail. */
import { act, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MAX_PAGES } from '@/api/client';
import AppShell from '@/components/AppShell';
import { module as module_, product, radarView } from '@/test/data';
import { ADMIN, fakeApi, wrap } from '@/test/session';

import AdminPage from './AdminPage';
import { ModulePage, ModulesPage } from './ModulesPage';
import { PRODUCT_POLL_MS, ProductPage, ProductsPage } from './ProductsPage';
import RadarsPage from './RadarsPage';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const throws = (status: number, message: string) => () => {
  throw { status, message };
};

describe('F2-006: a failed list load is not an empty list', () => {
  it('shows the error and Retry instead of "No radars yet"', async () => {
    const { api } = fakeApi({ 'GET /api/radars': throws(503, 'the service is busy; retry shortly') });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars' }));
    expect(await screen.findByText(/The radars could not be loaded: the service is busy/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText('No radars yet.')).toBeNull();
  });

  it('retries the radars list', async () => {
    let hits = 0;
    const { api } = fakeApi({
      'GET /api/radars': () => {
        hits += 1;
        if (hits === 1) throw { status: 500, message: 'broken' }; // a 503 GET is retried by the client (F2-015)
        return { items: [{ ...radarView().radar, can_edit: true }] };
      },
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars' }));
    (await screen.findByRole('button', { name: 'Retry' })).click();
    expect(await screen.findByText('Rivals')).toBeInTheDocument();
  });

  it('shows the error instead of "No products yet"', async () => {
    const { api } = fakeApi({ 'GET /api/products': throws(503, 'too many requests') });
    render(wrap(<Routes><Route path="/products" element={<ProductsPage />} /></Routes>, { api, path: '/products' }));
    expect(await screen.findByText(/The products could not be loaded: too many requests/)).toBeInTheDocument();
    expect(screen.queryByText('No products yet.')).toBeNull();
  });

  it('keeps the products list after a retry', async () => {
    let hits = 0;
    const { api } = fakeApi({
      'GET /api/products': () => {
        hits += 1;
        if (hits === 1) throw { status: 500, message: 'broken' }; // a 503 GET is retried by the client (F2-015)
        return { items: [product()] };
      },
    });
    render(wrap(<Routes><Route path="/products" element={<ProductsPage />} /></Routes>, { api, path: '/products' }));
    (await screen.findByRole('button', { name: 'Retry' })).click();
    expect(await screen.findByText('DeskPro')).toBeInTheDocument();
  });

  it('shows the error instead of an empty modules table', async () => {
    const { api } = fakeApi({ 'GET /api/modules': throws(500, 'module store unavailable') });
    render(wrap(<Routes><Route path="/modules" element={<ModulesPage />} /></Routes>, { api, path: '/modules' }));
    expect(await screen.findByText(/The modules could not be loaded: module store unavailable/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

describe('F2-012: polling stops once the session has ended', () => {
  it('does not re-read a product being indexed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const waiting = { ...product(), web_status: 'indexing' as const };
    const { api, calls } = fakeApi({ 'GET /api/products/p1': waiting, 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1', sessionEnded: true }));
    expect(await screen.findByText('Indexing')).toBeInTheDocument();
    const before = calls.length;
    await act(async () => void vi.advanceTimersByTime(4 * PRODUCT_POLL_MS));
    expect(calls.length).toBe(before);
    vi.useRealTimers();
  });

  it('still re-reads it while the session works', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const waiting = { ...product(), web_status: 'indexing' as const };
    const { api, calls } = fakeApi({ 'GET /api/products/p1': waiting, 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText('Indexing')).toBeInTheDocument();
    const before = calls.length;
    await act(async () => void vi.advanceTimersByTime(PRODUCT_POLL_MS + 10));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
    vi.useRealTimers();
  });
});

const run = (n: number) => ({
  module: 'scout',
  run_id: `r${n}`,
  status: 'failed' as const,
  trigger: 'schedule',
  started_at: '2026-10-01T10:00:00Z',
  requested_by: '',
  finished_at: null,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  weighted_tokens: 0,
  nodes_written: 0,
  summary: '',
  error: null,
});

describe('F2-014: the runs list counts every page', () => {
  it('reads past the first page and marks a capped list', async () => {
    const { api, calls } = fakeApi({
      'GET /api/modules/scout': module_({ slug: 'scout', grants: ['kg_read'] }),
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/scout/runs': (_: unknown, url: URL) => {
        const n = Number(url.searchParams.get('cursor') ?? 0);
        return { items: [run(n)], cursor: n + 1 < 3 ? String(n + 1) : null };
      },
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText('(3)')).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/runs'))).toHaveLength(3);
  });

  it('marks a module\'s run list as incomplete at the page cap', async () => {
    let n = 0;
    const { api } = fakeApi({
      'GET /api/modules/scout': module_({ slug: 'scout', grants: ['kg_read'] }),
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/scout/runs': () => ({ items: [{ ...run(n++), run_id: `x${n}` }], cursor: 'more' }),
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/scout' }));
    expect(await screen.findByText(`(${MAX_PAGES}+)`)).toBeInTheDocument();
  });

  it('marks Administration > Health runs as incomplete at the page cap', async () => {
    let n = 0;
    const { api } = fakeApi({
      'GET /api/health': { genai_enabled: true, budgets: [], recent_failures: [] },
      'GET /api/runs': () => ({ items: [{ ...run(n++), run_id: `y${n}` }], cursor: 'more' }),
    });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=health' }));
    expect(await screen.findByText(`(${MAX_PAGES}+)`)).toBeInTheDocument();
    expect(screen.getByText(/older ones are not loaded/)).toBeInTheDocument();
  });
});

describe('F2-007: a failed /api/me is not a viewer', () => {
  it('replaces the page with a retryable message', () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const refreshMe = vi.fn();
    render(
      wrap(<AppShell><div>page body</div></AppShell>, {
        api,
        me: null,
        meError: new Error('profile store unavailable'),
        meLoading: false,
        refreshMe,
      }),
    );
    expect(screen.getByRole('heading', { name: 'Your profile could not be loaded' })).toBeInTheDocument();
    expect(document.title).toBe('Your profile could not be loaded - Agentic Insights Radar'); // r3 F3-046
    expect(screen.getByText(/profile store unavailable/)).toBeInTheDocument();
    expect(screen.queryByText('page body')).toBeNull();
    screen.getByRole('button', { name: 'Retry' }).click();
    expect(refreshMe).toHaveBeenCalled();
  });

  it('shows the page once the profile is there', () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppShell><div>page body</div></AppShell>, { api, me: ADMIN, meError: new Error('stale') }));
    expect(screen.getByText('page body')).toBeInTheDocument();
  });
});
