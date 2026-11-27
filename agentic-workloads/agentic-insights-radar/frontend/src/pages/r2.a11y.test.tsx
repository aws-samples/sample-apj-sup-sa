// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-026, F2-027, F2-033, F2-034, F2-041 and the accessibility polish around them. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry } from '@/api/types';
import AppShell from '@/components/AppShell';
import { APP_TITLE, pageTitle } from '@/lib/pageTitle';
import { node, radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import GraphPage from './GraphPage';
import RadarPage from './RadarPage';
import RadarsPage from './RadarsPage';

afterEach(() => vi.restoreAllMocks());

const many: RadarEntry[] = Array.from({ length: 30 }, (_, i) => ({
  key: `k${i}`,
  title: `Entry ${i}`,
  sector: 'Direct',
  ring: 'Leader',
  description: '',
}));

describe('F2-033: controls Cloudscape labels from its own bundle have a name', () => {
  it('names the Pagination arrows, through the app shell', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/radars': { items: Array.from({ length: 30 }, (_, i) => ({ ...radarView().radar, slug: `r${i}`, name: `Radar ${i}`, can_edit: true })) },
    });
    render(wrap(<AppShell><Routes><Route path="/radars" element={<RadarsPage />} /></Routes></AppShell>, { api, path: '/radars' }));
    expect(await screen.findByText('Radar 0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeInTheDocument();
  });

  it('names each editor\'s remove button and each selection checkbox', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries: many }), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(
      wrap(
        <Routes>
          <Route path="/radars/:slug" element={<RadarPage />} />
        </Routes>,
        { api, path: '/radars/rivals?tab=settings' },
      ),
    );
    expect(await screen.findByRole('button', { name: 'Remove ed@example.com' })).toBeInTheDocument();
    createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
    expect(await screen.findByRole('checkbox', { name: 'Select Entry 0' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select every entry on this page' })).toBeInTheDocument();
  });
});

describe('F2-034: every page names itself', () => {
  it('sets the tab title from the page and the radar', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    await waitFor(() => expect(document.title).toBe(`Rivals - Radars - ${APP_TITLE}`));
  });

  it('announces the page and focuses its heading after an in-app navigation', async () => {
    function Go() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate('/radars')}>to radars</button>;
    }
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/radars': { items: [] } });
    render(
      wrap(
        <AppShell>
          <Routes>
            <Route path="/" element={<Go />} />
            <Route path="/radars" element={<RadarsPage />} />
          </Routes>
        </AppShell>,
        { api, path: '/' },
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'to radars' }));
    const heading = await screen.findByRole('heading', { level: 1, name: /Radars/ });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    const live = document.querySelector('#air-page [aria-live="polite"]');
    expect(live).toHaveTextContent(`Radars - ${APP_TITLE}`);
  });

  it('ignores a repeated title and keeps the app name on its own', () => {
    pageTitle.set(`Radars - ${APP_TITLE}`);
    const before = document.title;
    pageTitle.set(`Radars - ${APP_TITLE}`);
    expect(document.title).toBe(before);
    pageTitle.set(APP_TITLE);
    expect(document.title).toBe(APP_TITLE);
  });
});

describe('F2-041: closing the entry panel gives focus back', () => {
  it('returns focus to the legend row that opened it, and Esc works from the page', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    const legend = within(await screen.findByRole('region', { name: 'Legend entries' }));
    const row = legend.getByRole('button', { name: /^1\. Acme/ });
    row.focus();
    fireEvent.click(row);
    expect(await screen.findByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    // Esc from outside the panel closes it, and focus goes back to the row.
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Entry: Acme' })).toBeNull());
    expect(document.activeElement).toBe(row);
  });

  it('is not announced twice: the panel takes focus instead of being a live region', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    const legend = within(await screen.findByRole('region', { name: 'Legend entries' }));
    fireEvent.click(legend.getByRole('button', { name: /^1\. Acme/ }));
    const panel = await screen.findByRole('region', { name: 'Entry: Acme' });
    expect(panel).not.toHaveAttribute('aria-live');
  });

  it('does not try to restore an SVG blip, which cannot take focus back from the panel', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    const blip = container.querySelector('g[role="button"]') as SVGGElement;
    blip.focus();
    fireEvent.click(blip);
    expect(await screen.findByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Entry: Acme' })).toBeNull());
  });

  it('closes when the chart background is clicked, without moving focus', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    const legend = within(await screen.findByRole('region', { name: 'Legend entries' }));
    fireEvent.click(legend.getByRole('button', { name: /^1\. Acme/ }));
    expect(await screen.findByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    fireEvent.click(container.querySelector('svg[role="group"]')!);
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Entry: Acme' })).toBeNull());
  });

  it('leaves focus alone when the panel was opened without it', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    fireEvent.click(container.querySelector('g[role="button"]')!);
    expect(await screen.findByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Entry: Acme' })).toBeNull());
  });
});

describe('F2-064: a blip names its sector', () => {
  it('reads number, title, sector, ring and marker', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    expect(container.querySelector('g[role="button"]')).toHaveAttribute('aria-label', '1. Acme, Direct, Leader, Gaining');
  });
});

const hood = (ids: string[]) => ({
  nodes: ids.map((id, i) => node({ id, title: `Node ${i}`, degree: 1 })),
  edges: [],
});

describe('F2-026 and F2-027: the graph from the keyboard', () => {
  it('lists every node as a native button, and Enter on one shows what it is', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': hood(['signal/1', 'signal/2']) });
    render(wrap(<GraphPage />, { api, path: '/graph' }));
    const first = await screen.findByTestId('graph-node-signal/1');
    expect(first.tagName).toBe('BUTTON');
    first.focus();
    expect(document.activeElement).toBe(first);
    fireEvent.click(first); // a native button turns Enter and Space into a click
    expect(await screen.findByTestId('graph-node-details')).toHaveTextContent('Node 0');
  });

  it('opens a node from the list and shows its relationships', async () => {
    const { api } = fakeApi({
      'GET /api/graph/explore': hood(['signal/1', 'signal/2']),
      'GET /api/graph/nodes/signal/1/neighbors': { ...hood(['signal/1', 'signal/3']), total: 1, offset: 0 },
    });
    render(wrap(<GraphPage />, { api, path: '/graph' }));
    fireEvent.doubleClick(await screen.findByTestId('graph-node-signal/1'));
    expect(await screen.findByTestId('graph-scope-banner')).toHaveTextContent('Showing relationships for Node 0');
    expect(screen.getByTestId('graph-node-signal/3')).toBeInTheDocument();
  });
});
