// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-019, F2-020, F2-021, F2-042: what the UI offers while AI is paused or a module is off. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Me } from '@/api/types';
import { detail, module as module_, node, product, radarView } from '@/test/data';
import { ADMIN, CONFIG, fakeApi, wrap } from '@/test/session';

import InsightDetailPage from './InsightDetailPage';
import { ProductPage } from './ProductsPage';
import RadarPage from './RadarPage';

const paused = { ...CONFIG, genai_enabled: false };
const productRoutes = <Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>;
const ideaRoutes = <Routes><Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} /></Routes>;
const radarRoutes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /><Route path="/radars" element={<div>list</div>} /></Routes>;
const EDITOR: Me = { subject: 'u-ed', email: 'ed@example.com', role: 'editor', can: ['read', 'chat', 'content.draft', 'working.mark', 'radar.edit'] };

afterEach(() => vi.restoreAllMocks());

describe('F2-019: product search is paused with the rest of AI', () => {
  it('disables Search with the reason, and says so in the card', async () => {
    const { api, calls } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1', config: paused }));
    expect(await screen.findByText(/this search is paused too/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Search' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByLabelText('Question')).toBeDisabled();
    expect(calls.some((c) => c.path.endsWith('/ask'))).toBe(false);
  });

  it('works while AI is running', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    await screen.findByText(/See what the modules find/);
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'residency' } });
    expect(screen.getByRole('button', { name: 'Search' })).not.toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F2-020: Write a draft knows whether the drafts module is on', () => {
  const idea = detail({ node: node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' }), working_on: [] });

  it('disables it and leaves it out of "What you can do" while the module is off', async () => {
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [module_({ slug: 'draft', name: 'Content drafts', enabled: false })] },
    });
    render(wrap(ideaRoutes, { api, path: '/insights/nodes/content_idea/1', me: EDITOR }));
    const button = await screen.findByRole('button', { name: 'Write a draft' });
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'true'));
    expect(screen.queryByText(/Choose Write a draft/)).toBeNull();
  });

  it('offers it once the module is on', async () => {
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [module_({ slug: 'draft', name: 'Content drafts', enabled: true })] },
    });
    render(wrap(ideaRoutes, { api, path: '/insights/nodes/content_idea/1', me: EDITOR }));
    const button = await screen.findByRole('button', { name: 'Write a draft' });
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled', 'true'));
    expect(screen.getByText(/Choose Write a draft/)).toBeInTheDocument();
  });

  it('still offers it to someone who may run modules anyway', async () => {
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [module_({ slug: 'draft', name: 'Content drafts', enabled: false })] },
    });
    render(wrap(ideaRoutes, { api, path: '/insights/nodes/content_idea/1', me: ADMIN }));
    const button = await screen.findByRole('button', { name: 'Write a draft' });
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled', 'true'));
  });

  it('asks for no module list on another kind of entry', async () => {
    const { api, calls } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail() });
    render(wrap(ideaRoutes, { api, path: '/insights/nodes/signal/1' }));
    expect(await screen.findByText('Docker takes its spec to the CNCF')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/api/modules')).toBe(false);
  });
});

const autopilot = (over: object = {}) =>
  radarView({ radar: { ...radarView().radar, mode: 'autopilot', auto_apply_days: 7, auto_apply_agent: false, ...over } });

/** Ages relative to now, so the 7-day delay is tested without touching the clock. */
const daysAgo = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();

const suggestion = (key: string, createdAt: string, title: string) => ({
  entry_key: key,
  is_new: true,
  proposal: { title },
  base: null,
  reason: 'New evidence',
  evidence: [],
  created_at: createdAt,
  created_by: 'module:curator',
});

describe('F2-021: arming unreviewed agent changes is confirmed', () => {
  it('names the proposals that would land at once, and saves nothing when cancelled', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': autopilot(),
      'GET /api/radars/rivals/suggestions': {
        items: [suggestion('a', daysAgo(30), 'Overdue one'), suggestion('b', daysAgo(1), 'Still waiting')],
      },
      'PATCH /api/radars/rivals': autopilot({ auto_apply_agent: true }).radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(createWrapper(container).findAllToggles()).toHaveLength(1));
    createWrapper(container).findAllToggles()[0]!.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    expect(await screen.findByText('Apply 1 proposal without review?')).toBeInTheDocument();
    expect(screen.getByText('Overdue one')).toBeInTheDocument();
    expect(screen.queryByText('Still waiting')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Save and apply 1 proposal' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ auto_apply_agent: true }));
  });

  it('lists the first ten of many, names them however the proposal does, and can be dismissed', async () => {
    // The first two carry no proposal title: one falls back to the entry it changes, one to its key.
    const many = [
      { ...suggestion('base', daysAgo(30), ''), proposal: {}, base: radarView().entries[0] ?? null },
      { ...suggestion('bare', daysAgo(30), ''), proposal: {}, base: null },
      ...Array.from({ length: 11 }, (_, i) => suggestion(`k${i}`, daysAgo(30), `Proposal ${i}`)),
    ];
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': autopilot(),
      'GET /api/radars/rivals/suggestions': { items: many },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(createWrapper(container).findAllToggles()).toHaveLength(1));
    createWrapper(container).findAllToggles()[0]!.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    expect(await screen.findByText('Apply 13 proposals without review?')).toBeInTheDocument();
    expect(screen.getByText('and 3 more.')).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument(); // named by the entry it changes
    expect(screen.getByText('bare')).toBeInTheDocument(); // nothing else to name it by
    expect(screen.getByText(/13 proposals from the Radar curator are already past/)).toBeInTheDocument();
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByText('Apply 13 proposals without review?')).toBeNull());
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  });

  it('saves without asking when nothing is past the delay', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': autopilot(),
      'GET /api/radars/rivals/suggestions': { items: [suggestion('b', daysAgo(1), 'Still waiting')] },
      'PATCH /api/radars/rivals': autopilot({ auto_apply_agent: true }).radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(createWrapper(container).findAllToggles()).toHaveLength(1));
    createWrapper(container).findAllToggles()[0]!.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ auto_apply_agent: true }));
    expect(screen.queryByText(/without review\?/)).toBeNull();
  });
});

describe('F2-042: an owner can switch unreviewed agent changes off', () => {
  const OWNER: Me = { subject: 'u-admin', email: 'admin@example.com', role: 'editor', can: ['read', 'radar.edit', 'radar.admin', 'suggestion.review'] };

  it('leaves the toggle usable when it is on, and locked when it is off', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': autopilot({ auto_apply_agent: true }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PATCH /api/radars/rivals': autopilot().radar,
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings', me: OWNER }));
    expect(await screen.findByText(/You can switch it off on your own radar/)).toBeInTheDocument();
    const toggle = createWrapper(container).findAllToggles().pop()!;
    expect(toggle.findNativeInput().getElement()).not.toBeDisabled();
    toggle.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ auto_apply_agent: false }));
  });

  it('keeps it locked for an owner while it is off', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': autopilot(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings', me: OWNER }));
    await screen.findByText(/Only an administrator can let autopilot apply/);
    expect(createWrapper(container).findAllToggles().pop()!.findNativeInput().getElement()).toBeDisabled();
  });
});
