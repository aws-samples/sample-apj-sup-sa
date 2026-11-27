// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { render, screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '@/App';
import { navItems } from '@/components/AppShell';
import { applyProfile, DEFAULT_RADARS_HELP, radarsHelp, sectionVisible } from '@/lib/profile';
import { ADMIN, VIEWER, fakeApi, wrap } from '@/test/session';

import HomePage from './HomePage';
import { ModulePage } from './ModulesPage';
import RadarsPage from './RadarsPage';
import { module } from '@/test/data';

const empty = { items: [], cursor: null, sort: 'newest', page_size: 25 };
const TRADE_HELP = 'Each radar places trade measures by how soon they need action.';
const trade = (sections: Record<string, boolean> | undefined, radars_help?: string) =>
  applyProfile({ app_name: 'Trade Radar', tagline: '', about_md: '', version: 1, lists: {}, sections, radars_help });

afterEach(() => applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} }));

const hrefs = (items: ReturnType<typeof navItems>) => items.map((i) => ('href' in i ? i.href : i.type));

describe('deployment sections and radar wording', () => {
  it('shows Products and the built-in radar text when the profile says nothing', () => {
    trade(undefined);
    expect(sectionVisible('products')).toBe(true);
    expect(radarsHelp()).toBe(DEFAULT_RADARS_HELP);
    expect(hrefs(navItems(undefined, false))).toContain('/products');
  });

  it('hides Products from the menu and uses the deployment radar text', () => {
    trade({ products: false }, TRADE_HELP);
    expect(sectionVisible('products')).toBe(false);
    expect(sectionVisible('something-else')).toBe(true);
    expect(radarsHelp()).toBe(TRADE_HELP);
    expect(hrefs(navItems(undefined, true))).not.toContain('/products');
  });

  it('answers a Products address with a turned-off notice, not the page', async () => {
    trade({ products: false });
    const { api } = fakeApi({});
    render(wrap(<AppRoutes />, { api, path: '/products' }));
    expect(await screen.findByText('Products is turned off')).toBeInTheDocument();
    expect(screen.getByText(/does not use Products/)).toBeInTheDocument();
  });

  it('answers a product detail address the same way', async () => {
    trade({ products: false });
    render(wrap(<AppRoutes />, { api: fakeApi({}).api, path: '/products/p1' }));
    expect(await screen.findByText('Products is turned off')).toBeInTheDocument();
  });

  it('drops the product step and words the radar step from the profile in Getting started', async () => {
    trade({ products: false }, TRADE_HELP);
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': empty,
      'GET /api/insights/trends': empty,
      'GET /api/insights/gaps': empty,
      'GET /api/insights/content': empty,
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: true },
    });
    render(wrap(<HomePage />, { api, me: ADMIN }));
    expect(await screen.findByText(new RegExp(`${TRADE_HELP} Start from a template`))).toBeInTheDocument();
    // Gaps and Content ideas are still listed, but Products is off: no step that leads to it.
    expect(screen.queryByText('Describe your product')).toBeNull();
    expect(screen.queryByText(/competitors, vendors or technologies/)).toBeNull();
  });

  it('tells a reader what a radar is in this deployment', async () => {
    trade(undefined, TRADE_HELP);
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': empty, 'GET /api/insights/trends': empty });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    expect(await screen.findByText(TRADE_HELP)).toBeInTheDocument();
  });

  it('describes radars on the Radars page with the deployment text', async () => {
    trade(undefined, TRADE_HELP);
    const { api } = fakeApi({ 'GET /api/radars': { items: [] } });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars' }));
    expect(await screen.findByText(new RegExp(`${TRADE_HELP} Open one to see its chart`))).toBeInTheDocument();
  });

  it('drops the Product picker from a module and does not list products when Products is off', async () => {
    const gap = module({ slug: 'gap', name: 'Gaps', template: 'gap', output_kind: 'gap', grants: ['kg_read', 'kg_write_output', 'product_knowledge'] });
    const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;
    const routes = { 'GET /api/modules/gap': gap, 'GET /api/modules/gap/runs': { items: [], cursor: null }, 'GET /api/products': { items: [] }, 'GET /api/models': { items: [] } };
    trade({ products: false });
    const off = fakeApi(routes);
    const { unmount } = render(wrap(page, { api: off.api, path: '/modules/gap', me: ADMIN }));
    expect(await screen.findByText('Daily token budget')).toBeInTheDocument();
    expect(screen.queryByText('The product this module compares the market against.')).toBeNull();
    expect(off.calls.some((c) => c.path.startsWith('/api/products'))).toBe(false);
    unmount();
    trade(undefined);
    const on = fakeApi(routes);
    render(wrap(page, { api: on.api, path: '/modules/gap', me: ADMIN }));
    expect(await screen.findByText('The product this module compares the market against.')).toBeInTheDocument();
  });
});
