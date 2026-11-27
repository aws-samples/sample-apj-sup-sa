// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 backend contract: viewers are refused gaps, content ideas, drafts and private records. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { navItems } from '@/components/AppShell';
import { detail, node } from '@/test/data';
import { ADMIN, fakeApi, VIEWER, wrap } from '@/test/session';

import GraphPage from './GraphPage';
import HomePage from './HomePage';
import InsightDetailPage from './InsightDetailPage';
import InsightsPage, { forgetLoadedPages } from './InsightsPage';

const EDITOR = { ...ADMIN, subject: 'u-ed', role: 'editor' as const, can: ['read', 'chat', 'radar.create', 'radar.edit', 'product.read', 'content.draft'] };
const NOT_FOUND = () => {
  throw { status: 404, code: 'not_found', message: 'node not found' };
};

afterEach(() => {
  vi.restoreAllMocks();
  forgetLoadedPages();
});

describe('navigation', () => {
  it('leaves the private lists out for a viewer and keeps them for everyone else', () => {
    const viewer = JSON.stringify(navItems(undefined, false, false));
    expect(viewer).toContain('/insights/signals');
    expect(viewer).toContain('/insights/trends');
    for (const k of ['gaps', 'content', 'drafts']) expect(viewer).not.toContain(`/insights/${k}"`);
    const editor = JSON.stringify(navItems(undefined, false));
    for (const k of ['gaps', 'content', 'drafts']) expect(editor).toContain(`/insights/${k}"`);
  });
});

describe('Overview', () => {
  const routes = () => ({
    'GET /api/nav': { builtin: [], custom: [] },
    'GET /api/insights/signals': { items: [] },
    'GET /api/insights/trends': { items: [] },
    'GET /api/insights/gaps': { items: [] },
    'GET /api/insights/content': { items: [] },
  });

  it('shows a viewer no Gaps or Content ideas card, never asks for them, and does not mention them', async () => {
    const { api, calls } = fakeApi(routes());
    render(wrap(<HomePage />, { api, me: { ...VIEWER, can: [...VIEWER.can, 'content.draft'] } }));
    expect(await screen.findByRole('heading', { name: 'Signals' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Gaps' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Content ideas' })).toBeNull();
    expect(screen.getByText(/signals and trends, each with its evidence/)).toBeInTheDocument();
    expect(screen.queryByText(/can be turned into a first draft/)).toBeNull();
    await waitFor(() => expect(calls.some((c) => c.path === '/api/insights/trends')).toBe(true));
    expect(calls.some((c) => c.path === '/api/insights/gaps' || c.path === '/api/insights/content')).toBe(false);
  });

  it('keeps every card and the draft line for an editor', async () => {
    const { api } = fakeApi(routes());
    render(wrap(<HomePage />, { api, me: EDITOR }));
    expect(await screen.findByRole('heading', { name: 'Gaps' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Content ideas' })).toBeInTheDocument();
    expect(screen.getByText(/signals, trends, gaps and content ideas/)).toBeInTheDocument();
    expect(screen.getByText(/can be turned into a first draft/)).toBeInTheDocument();
  });
});

describe('insight lists', () => {
  const list = (path: string, me = VIEWER) => {
    const { api, calls } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/gaps': { items: [node({ id: 'gap/1', type: 'gap', title: 'A gap' })] } });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, me, path }));
    return calls;
  };

  it('tells a viewer a private list is not available to their role, without asking the API', async () => {
    for (const kind of ['gaps', 'content', 'drafts']) {
      const calls = list(`/insights/${kind}`);
      expect(await screen.findByRole('heading', { name: 'Not available to your role' })).toBeInTheDocument();
      expect(screen.getByText(/not available to your role\. Ask an administrator/)).toBeInTheDocument();
      expect(calls.some((c) => c.path.startsWith('/api/insights/'))).toBe(false);
      document.body.innerHTML = '';
    }
  });

  it('shows the list to an editor', async () => {
    list('/insights/gaps', EDITOR);
    expect(await screen.findByText('A gap')).toBeInTheDocument();
  });
});

describe('a record kept from a viewer', () => {
  const page = (path: string, me = VIEWER, found = false) => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      [`GET /api${path}`]: found ? detail() : NOT_FOUND,
    });
    render(wrap(<Routes><Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} /></Routes>, { api, me, path }));
  };

  it('reads a 404 for a gap, content idea or draft as not available to the role, with a way back to the Overview', async () => {
    page('/insights/nodes/gap/1');
    expect(await screen.findByRole('heading', { name: 'Not available to your role' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to overview' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('reads a 404 for any other record as not available or deleted', async () => {
    page('/insights/nodes/signal/1');
    expect(await screen.findByRole('heading', { name: 'Entry not available' })).toBeInTheDocument();
    expect(screen.getByText(/not available to your role, or it has been deleted/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to signals' })).toBeInTheDocument();
  });

  it('still reads a 404 as not found for an editor', async () => {
    page('/insights/nodes/gap/1', EDITOR);
    expect(await screen.findByRole('heading', { name: 'Entry not found' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to gaps' })).toBeInTheDocument();
  });
});

describe('knowledge graph', () => {
  const graph = (me = VIEWER) => {
    const { api } = fakeApi({ 'GET /api/graph/nodes/gap/1/neighbors': NOT_FOUND });
    render(wrap(<GraphPage />, { api, me, path: '/graph?focus=gap%2F1' }));
  };

  it('tells a viewer a focused record is not available to their role, without Retry', async () => {
    graph();
    expect(await screen.findByText('This node is not available to your role, or it has been deleted.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('keeps the not-found message and Retry for an editor', async () => {
    graph(EDITOR);
    expect(await screen.findByText('This node could not be found. It may have been deleted.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('offers a viewer no gap, content idea or draft type to search for', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': { nodes: [], edges: [] } });
    const { container, unmount } = render(wrap(<GraphPage />, { api, me: VIEWER }));
    const multiselect = createWrapper(container).findMultiselect();
    multiselect!.openDropdown();
    const viewerTypes = (multiselect!.findDropdown({ expandToViewport: true }).findOptions() ?? []).map((o) => o.getElement().textContent);
    expect(viewerTypes).toContain('Signal');
    expect(viewerTypes).not.toContain('Gap');
    expect(viewerTypes).not.toContain('Content idea');
    unmount();
    const editor = render(wrap(<GraphPage />, { api, me: EDITOR }));
    const other = createWrapper(editor.container).findMultiselect();
    other!.openDropdown();
    expect((other!.findDropdown({ expandToViewport: true }).findOptions() ?? []).map((o) => o.getElement().textContent)).toContain('Gap');
  });
});
