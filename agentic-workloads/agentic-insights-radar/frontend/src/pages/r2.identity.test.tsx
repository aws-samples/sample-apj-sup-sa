// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-023, F2-024, F2-035: who did it, a role that changed, and list names that could not be read. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from '@/components/AppShell';
import type { Me } from '@/api/types';
import { radarView } from '@/test/data';
import { ADMIN, fakeApi, VIEWER, wrap } from '@/test/session';

import AdminPage from './AdminPage';
import HomePage from './HomePage';
import InsightsPage from './InsightsPage';
import RadarPage from './RadarPage';

afterEach(() => vi.restoreAllMocks());

/** An editor of the radar who may not read the user list. */
const EDITOR: Me = { subject: 'u-ed', email: 'ed@example.com', role: 'editor', can: ['read', 'radar.edit', 'suggestion.review'] };

const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const versions = [
  { version: 2, created_at: '2026-10-02T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' },
  { version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-other', entry_count: 1, note: '' },
];
const users = {
  items: [
    { username: 'u-admin', email: 'admin@example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '' },
    { username: 'u-other', email: 'pat@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '' },
  ],
};

describe('F2-023: History and the kill switch name the person', () => {
  it('shows another editor\'s email to an administrator', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': { items: versions },
      'GET /api/users': users,
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=history' }));
    expect(await screen.findByText('pat@example.com', {}, { timeout: 5000 })).toBeInTheDocument(); // two lists to load, on a busy machine
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.queryByText('Added by a person')).toBeNull();
  });

  it('keeps "Added by a person" for someone who may not read the user list', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ can_admin: false }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': { items: versions },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=history', me: EDITOR }));
    expect(await screen.findAllByText('Added by a person')).toHaveLength(2);
    expect(calls.some((c) => c.path === '/api/users')).toBe(false);
  });

  it('names the administrator who last changed the kill switch', async () => {
    const { api } = fakeApi({
      'GET /api/andon': { genai_enabled: true, updated_at: '2026-10-02T10:00:00Z', updated_by: 'user:u-other', reason: 'incident' },
      'GET /api/users': users,
      'GET /api/chat/settings': { daily_tokens: 1000, per_user_daily_tokens: 100 },
      'GET /api/chat/status': { shared: { remaining: 900 }, mine: { remaining: 90 } },
    });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=ai' }));
    expect(await screen.findByText(/by pat@example.com/)).toBeInTheDocument();
  });
});

describe('F2-024: a changed role is noticed', () => {
  it('re-reads the profile after a 403 and says the role changed', async () => {
    let role: 'admin' | 'viewer' = 'admin';
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/me': () => (role === 'admin' ? ADMIN : VIEWER),
      'GET /api/radars': () => {
        role = 'viewer';
        throw { status: 403, code: 'forbidden', message: 'your role does not allow radar.create', details: { action: 'radar.create' } };
      },
    });
    function Load() {
      return (
        <button type="button" onClick={() => void api.radars().catch(() => undefined)}>
          load
        </button>
      );
    }
    // The shell owns the notice; refreshMe is what the session would call on a 403.
    const me = { ...ADMIN };
    const refreshMe = vi.fn();
    api.setForbiddenHandler(refreshMe);
    const view = render(wrap(<AppShell><Load /></AppShell>, { api, me, refreshMe }));
    fireEvent.click(screen.getByText('load'));
    await waitFor(() => expect(refreshMe).toHaveBeenCalled());
    view.unmount();
  });

  it('announces the new role once it is known', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const view = render(wrap(<AppShell><div>page</div></AppShell>, { api, me: ADMIN }));
    view.rerender(wrap(<AppShell><div>page</div></AppShell>, { api, me: VIEWER }));
    expect(await screen.findByText('Your role is now Viewer. What the app offers you has changed with it.')).toBeInTheDocument();
  });
});

describe('F2-035: list names that could not be read are not a deleted list', () => {
  it('says so on an insight list, with Retry', async () => {
    const { api } = fakeApi({
      'GET /api/nav': () => {
        throw { status: 503, message: 'nav is busy' };
      },
    });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/pricing' }));
    expect(await screen.findByText('The list names could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText(/may belong to a module that was deleted/)).toBeNull();
  });

  it('still says a list does not exist when the names did load', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/pricing' }));
    expect(await screen.findByText(/may belong to a module that was deleted/)).toBeInTheDocument();
  });

  it('says so on the Overview, with Retry', async () => {
    const { api } = fakeApi({
      'GET /api/nav': () => {
        throw { status: 500, message: 'nav is broken' };
      },
      'GET /api/health': { genai_enabled: true, budgets: [], recent_failures: [], runs: {} },
      'GET /api/insights/signals': { items: [] },
      'GET /api/insights/trends': { items: [] },
      'GET /api/insights/gaps': { items: [] },
      'GET /api/insights/content': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules': { items: [] },
      'GET /api/radars': { items: [] },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    render(wrap(<HomePage />, { api }));
    expect(await screen.findByText('The list names could not be loaded')).toBeInTheDocument();
    expect(createWrapper(document.body).findAllAlerts().some((a) => a.findContent().getElement().textContent?.includes('nav is broken'))).toBe(true);
  });
});
