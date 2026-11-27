// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { render, screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '@/App';
import AppShell, { navItems } from '@/components/AppShell';
import ChatPanel from '@/components/ChatPanel';
import { applyProfile, sectionVisible } from '@/lib/profile';
import { detail, module } from '@/test/data';
import { ADMIN, CONFIG, VIEWER, fakeApi, wrap } from '@/test/session';

import HomePage from './HomePage';
import InsightDetailPage from './InsightDetailPage';
import { ModulesPage } from './ModulesPage';

const empty = { items: [], cursor: null, sort: 'newest', page_size: 25 };
const radarsOff = () =>
  applyProfile({ app_name: 'Trade Radar', tagline: '', about_md: '', version: 1, lists: {}, sections: { radars: false } });

afterEach(() => applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} }));

const hrefs = (items: ReturnType<typeof navItems>) => items.map((i) => ('href' in i ? i.href : i.type));
const home = {
  'GET /api/nav': { builtin: [], custom: [] },
  'GET /api/insights/signals': empty,
  'GET /api/insights/trends': empty,
  'GET /api/insights/gaps': empty,
  'GET /api/insights/content': empty,
  'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: true },
};

describe('a deployment without radars', () => {
  it('has no Radars in the menu, and keeps it when the profile says nothing', () => {
    radarsOff();
    expect(sectionVisible('radars')).toBe(false);
    expect(hrefs(navItems(undefined, true))).not.toContain('/radars');
    applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} });
    expect(hrefs(navItems(undefined, true))).toContain('/radars');
  });

  it('answers a radar address with a turned-off notice', async () => {
    radarsOff();
    const { unmount } = render(wrap(<AppRoutes />, { api: fakeApi({}).api, path: '/radars' }));
    expect(await screen.findByText('Radars is turned off')).toBeInTheDocument();
    unmount();
    render(wrap(<AppRoutes />, { api: fakeApi({}).api, path: '/radars/rivals' }));
    expect(await screen.findByText('Radars is turned off')).toBeInTheDocument();
  });

  it('leaves the radar step and the Radars line out of Getting started, and never lists radars', async () => {
    radarsOff();
    const admin = fakeApi(home);
    const { unmount } = render(wrap(<HomePage />, { api: admin.api, me: ADMIN }));
    expect(await screen.findByText(/Everything the modules record is linked here/)).toBeInTheDocument();
    expect(screen.queryByText(/Start from a template/)).toBeNull();
    expect(admin.calls.some((c) => c.path === '/api/radars')).toBe(false);
    unmount();
    render(wrap(<HomePage />, { api: fakeApi(home).api, me: VIEWER }));
    expect(await screen.findByText(/answers questions from the knowledge graph/)).toBeInTheDocument();
    expect(screen.queryByText('Radars:')).toBeNull();
  });

  it('does not look a record up on radars', async () => {
    radarsOff();
    const { api, calls } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail() });
    render(wrap(<Routes><Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} /></Routes>, { api, path: '/insights/nodes/signal/1' }));
    expect(await screen.findAllByText(detail().node.title)).not.toHaveLength(0);
    expect(calls.some((c) => c.path === '/api/radars/entries')).toBe(false);
  });

  it('hides the Radar curator unless an administrator turned it on, and its step in the setup advice', async () => {
    radarsOff();
    const scout = module({ slug: 'scout', name: 'Scout', template: 'scout', enabled: false });
    const curator = module({ slug: 'curator', name: 'Radar curator', template: 'curator', enabled: false });
    const { unmount } = render(wrap(<ModulesPage />, { api: fakeApi({ 'GET /api/modules': { items: [scout, curator] } }).api, me: ADMIN }));
    expect(await screen.findByText(/Every module is off/)).toBeInTheDocument();
    expect(screen.getAllByText('Scout').length).toBeGreaterThan(0);
    expect(screen.queryByText('Radar curator')).toBeNull();
    unmount();
    const on = { ...curator, enabled: true };
    render(wrap(<ModulesPage />, { api: fakeApi({ 'GET /api/modules': { items: [scout, on] } }).api, me: ADMIN }));
    expect(await screen.findByText('Radar curator')).toBeInTheDocument();
  });

  it('does not offer radars in the assistant', () => {
    radarsOff();
    const { unmount } = render(wrap(<ChatPanel fetcher={vi.fn() as unknown as typeof fetch} />, { api: fakeApi({}).api }));
    expect(screen.getByText(/^Ask about .*the knowledge graph\. Answers cite/).textContent).not.toMatch(/radars/);
    unmount();
    render(wrap(<ChatPanel fetcher={vi.fn() as unknown as typeof fetch} />, { api: fakeApi({}).api, config: { ...CONFIG, genai_enabled: false } }));
    expect(screen.getByText('The assistant is paused by an administrator. Search and the knowledge graph still work.')).toBeInTheDocument();
  });

  it('words the paused banner without radars, and works where the browser refuses sessionStorage', async () => {
    radarsOff();
    const refused = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    const paused = { ...CONFIG, genai_enabled: false };
    const { unmount } = render(wrap(<AppShell><div>page body</div></AppShell>, { api: fakeApi({}).api, config: paused }));
    expect(await screen.findByText(/^An administrator has paused the assistant.* The knowledge graph, its searches/)).toBeInTheDocument();
    expect(screen.getByText('page body')).toBeInTheDocument();
    unmount();
    refused.mockRestore();
    applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} });
    render(wrap(<AppShell><div>page body</div></AppShell>, { api: fakeApi({}).api, config: paused }));
    expect(await screen.findByText(/Radars, the knowledge graph, their searches/)).toBeInTheDocument();
  });
});

describe('a deployment without sample data', () => {
  it('does not point Getting started at the sample data', async () => {
    applyProfile({ app_name: 'Example', tagline: '', about_md: '', version: 1, lists: {}, sections: { sample_data: false } });
    const admin = fakeApi({ ...home, 'GET /api/radars': { items: [] } });
    render(wrap(<HomePage />, { api: admin.api, me: ADMIN }));
    expect(await screen.findByText(/^Everything the modules record is linked here\.$/)).toBeInTheDocument();
    expect(screen.getByText(/Start from a template\.$/)).toBeInTheDocument();
    expect(screen.queryByText(/Sample data loads|load the sample radar/)).toBeNull();
  });
});
