// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { MAX_PAGES } from '@/api/client';
import { applyProfile } from '@/lib/profile';
import { CONFIG, fakeApi, wrap } from '@/test/session';

import AdminPage from './AdminPage';

const users = [
  { username: 'u-admin', email: 'admin@example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' },
  { username: 'u2', email: 'two@example.com', role: 'viewer', enabled: true, status: 'FORCE_CHANGE_PASSWORD', created_at: '2026-10-01T10:00:00Z' },
  { username: 'u3', email: 'three@example.com', role: 'editor', enabled: false, status: 'CONFIRMED', created_at: '2026-10-01T10:00:00Z' },
];

describe('AdminPage', () => {
  it('manages users', async () => {
    const { api, calls } = fakeApi({
      'GET /api/users': { items: users },
      'POST /api/users': users[1],
      'PATCH /api/users/u2': users[1],
      'PATCH /api/users/u3': users[2],
      'DELETE /api/users/u2': {},
    });
    const { container } = render(wrap(<AdminPage />, { api }));
    expect(await screen.findByText('two@example.com')).toBeInTheDocument();
    // F2-095: each Cognito status reads as itself, not "Invited" for every one of them.
    expect(screen.getByText('Invited, first sign-in pending')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    expect(screen.getByText(/You cannot change your own role/)).toBeInTheDocument();
    const selects = createWrapper(container).findAllSelects(); // your own row has no role picker
    selects[0]!.openDropdown();
    selects[0]!.selectOptionByValue('editor', { expandToViewport: true });
    expect(screen.getByText(/two@example.com becomes Editor \(was Viewer\)/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false); // nothing changes until confirmed
    fireEvent.click(screen.getByRole('button', { name: 'Change role' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ role: 'editor' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Enable' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/users/u3')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Delete two@example.com' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete two@example.com? They lose access');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete two@example.com' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false); // nothing is deleted on the first click
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'DELETE')?.path).toBe('/api/users/u2'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Invite user' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Invite user' }));
    const invite = createWrapper(screen.getByRole('dialog')).findSelect()!;
    invite.openDropdown();
    invite.selectOptionByValue('editor', { expandToViewport: true });
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: 'new@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ email: 'new@example.com', role: 'editor' }));
    expect(await screen.findByText('Invitation sent to new@example.com.')).toBeInTheDocument();
  }, 20_000); // several dialogs and a full user table

  it('filters users by email and says when the list is capped', async () => {
    const { api } = fakeApi({ 'GET /api/users': { items: users } });
    const { container, unmount } = render(wrap(<AdminPage />, { api }));
    expect(await screen.findByText('two@example.com')).toBeInTheDocument();
    expect(screen.queryByText(/Showing the first/)).toBeNull();
    const filter = createWrapper(container).findTextFilter()!;
    filter.findInput().setInputValue('  THREE ');
    expect(screen.queryByText('two@example.com')).toBeNull();
    expect(screen.getByText('three@example.com')).toBeInTheDocument();
    expect(screen.getAllByText('1 match').length).toBeGreaterThan(0);
    filter.findInput().setInputValue('nobody');
    expect(screen.getByText('No users match.')).toBeInTheDocument();
    expect(screen.getAllByText('0 matches').length).toBeGreaterThan(0);
    unmount();
    // CR-06: every page is read; only a pool past MAX_PAGES pages is shown in part.
    const pageOf = (_: unknown, url: URL) => {
      const n = Number(url.searchParams.get('cursor') ?? 0);
      return { items: [0, 1].map((j) => ({ ...users[1]!, username: `u${2 * n + j}`, email: `user${2 * n + j}@example.com` })), cursor: String(n + 1) };
    };
    const { api: big } = fakeApi({ 'GET /api/users': pageOf });
    render(wrap(<AdminPage />, { api: big }));
    expect(await screen.findByText(`Showing the first ${2 * MAX_PAGES} users`, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(`(${2 * MAX_PAGES}+)`)).toBeInTheDocument();
    expect(screen.getByText('user0@example.com')).toBeInTheDocument();
    expect(screen.queryByText('user25@example.com')).toBeNull(); // 25 a page
    const pager = createWrapper(document.body).findPagination()!;
    pager.findNextPageButton().click();
    expect(await screen.findByText('user25@example.com')).toBeInTheDocument();
    expect(screen.queryByText('user0@example.com')).toBeNull();
  }, 30_000); // renders a full page of users with their role selects

  it('does not claim AI is running before or without the kill switch state', async () => {
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/andon': () => { throw { status: 503, message: 'andon unavailable' }; },
      'GET /api/chat/settings': { daily_tokens: 1, per_user_daily_tokens: 1 },
    });
    const { container } = render(wrap(<AdminPage />, { api, config: { ...CONFIG, genai_enabled: false } }));
    createWrapper(container).findTabs()!.findTabLinkById('ai')!.click();
    expect(screen.getByText('AI features are paused')).toBeInTheDocument();
    expect(await screen.findByText(/andon unavailable/)).toBeInTheDocument();
    expect(screen.getByText('AI features are paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume AI features' })).toBeDisabled();
  });

  it('offers no sample data where the deployment turned it off', () => {
    applyProfile({ app_name: 'Example', tagline: '', about_md: '', version: 1, lists: {}, sections: { sample_data: false } });
    try {
      const { api } = fakeApi({ 'GET /api/users': { items: [] } });
      // An old link to the turned-off tab opens Users, not an empty page (code review r17).
      const { container } = render(wrap(<AdminPage />, { api, path: '/admin?tab=sample' }));
      expect(createWrapper(container).findTabs()!.findTabLinkById('sample')).toBeNull();
      expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement().textContent).toBe('Users');
    } finally {
      applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} });
    }
  });

  it('loads sample data and links to what it added', async () => {
    let n = 0;
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'POST /api/admin/sample-data': () => {
        n += 1;
        if (n === 1) throw { status: 500, message: 'table is busy' };
        return n === 2
          ? { nodes_created: 26, edges_created: 44, radar: 'sample-crm-competitors', skipped: [] }
          : n === 3
            ? { nodes_created: 0, edges_created: 0, radar: 'sample-crm-competitors', skipped: ['gap/sample-15'] }
            : { nodes_created: 0, edges_created: 0, radar: null, skipped: ['radar/x'] };
      },
    });
    const ui = (
      <Routes>
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/radars/:slug" element={<div>radar page</div>} />
        <Route path="/graph" element={<div>graph page</div>} />
      </Routes>
    );
    const { container, unmount } = render(wrap(ui, { api, path: '/admin' }));
    createWrapper(container).findTabs()!.findTabLinkById('sample')!.click();
    const button = await screen.findByRole('button', { name: 'Load sample data' });
    fireEvent.click(button);
    expect(await screen.findByText('table is busy')).toBeInTheDocument();
    fireEvent.click(button);
    expect(await screen.findByText(/Added 26 records and 44 links\./)).toBeInTheDocument();
    fireEvent.click(button);
    expect(await screen.findByText(/Added 0 records and 0 links \(1 were already there\)/)).toBeInTheDocument();
    fireEvent.click(button);
    expect(await screen.findByText(/another radar already uses its address/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'sample radar' })).toBeNull();
    n = 2;
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole('link', { name: 'sample radar' }));
    expect(await screen.findByText('radar page')).toBeInTheDocument();
    unmount();
    const again = render(wrap(ui, { api, path: '/admin' }));
    createWrapper(again.container).findTabs()!.findTabLinkById('sample')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Load sample data' }));
    fireEvent.click(await screen.findByRole('link', { name: 'knowledge graph' }));
    expect(await screen.findByText('graph page')).toBeInTheDocument();
  });

  it('says when the chat budget cannot be loaded', async () => {
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/andon': { genai_enabled: true, reason: '', updated_by: null, updated_at: null },
      'GET /api/chat/settings': () => { throw { status: 403, message: 'blocked by the firewall' }; },
    });
    const { container } = render(wrap(<AdminPage />, { api }));
    createWrapper(container).findTabs()!.findTabLinkById('ai')!.click();
    expect(await screen.findByText('The chat budget could not be loaded')).toBeInTheDocument();
    expect(screen.getByText(/blocked by the firewall/)).toBeInTheDocument();
  });

  it('controls the kill switch and chat budget', async () => {
    let enabled = true;
    const { api, calls } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/andon': () => ({ genai_enabled: enabled, reason: 'test', updated_by: 'user:u', updated_at: '2026-10-01T10:00:00Z' }),
      'PUT /api/andon': (b: unknown) => { enabled = (b as { genai_enabled: boolean }).genai_enabled; return { genai_enabled: enabled }; },
      'GET /api/chat/settings': { daily_tokens: 2_000_000, per_user_daily_tokens: 300_000 },
      'PUT /api/chat/settings': (b: unknown) => b,
    });
    const refreshConfig = vi.fn();
    const { container } = render(wrap(<AdminPage />, { api, refreshConfig }));
    createWrapper(container).findTabs()!.findTabLinkById('ai')!.click();
    expect(await screen.findByText(/Last changed/)).toBeInTheDocument();
    expect(screen.getByText('AI features are running')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause AI features' })).toHaveAttribute('aria-disabled', 'true'); // a reason first
    fireEvent.change(screen.getAllByRole('textbox')[0]!, { target: { value: 'incident 7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pause AI features' }));
    expect(screen.getByText('Pause AI features for everyone?')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Pause AI features' }).pop()!);
    expect(await screen.findByText('AI features are paused')).toBeInTheDocument();
    expect(refreshConfig).toHaveBeenCalledTimes(1); // the app-wide banner follows the switch
    expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/andon')?.body).toEqual({ genai_enabled: false, reason: 'incident 7' });
    fireEvent.click(screen.getByRole('button', { name: 'Resume AI features' }));
    expect(await screen.findByText('AI features are running')).toBeInTheDocument();
    const everyone = await screen.findByDisplayValue('2000000');
    fireEvent.change(everyone, { target: { value: '' } }); // a cleared field is not a 0 budget
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Enter a whole number of at least 0.')).toBeInTheDocument();
    fireEvent.change(everyone, { target: { value: '3000000' } });
    fireEvent.change(await screen.findByDisplayValue('300000'), { target: { value: '100000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.path === '/api/chat/settings' && c.method === 'PUT')?.body).toEqual({ daily_tokens: 3_000_000, per_user_daily_tokens: 100_000 }));
  });

  it('shows health and the audit log', async () => {
    const { api, fetcher } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [{ module: 'scout', enabled: true, consumed: 10, limit: 100, state: 'open' }], genai_enabled: false },
      'GET /api/runs': { items: [{ module: 'scout', run_id: 'r', status: 'failed', trigger: 'manual', started_at: '2026-10-01T10:00:00Z', requested_by: 'u', finished_at: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 0, nodes_written: 0, summary: '', error: 'boom' }] },
      'GET /api/audit': { items: [{ actor: 'user:u', email: 'a@example.com', action: 'radar.create', target: 'rivals', details: {}, at: '2026-10-01T10:00:00Z' }] },
    });
    const { container } = render(wrap(<AdminPage />, { api }));
    const tabs = createWrapper(container).findTabs()!;
    tabs.findTabLinkById('health')!.click();
    expect((await screen.findAllByText('boom')).length).toBeGreaterThan(0);
    expect(screen.getByText('AI features are paused.')).toBeInTheDocument();
    tabs.findTabLinkById('audit')!.click();
    expect(await screen.findByText('Created a radar')).toBeInTheDocument();
    createWrapper(container).findDatePicker()!.setInputValue('2026/09/30');
    await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).startsWith('/api/audit?date=2026-09-30'))).toBe(true));
  });

  it('has no invitations when people sign in through an identity provider', async () => {
    const { api } = fakeApi({ 'GET /api/users': { items: users } });
    render(wrap(<AdminPage />, { api, config: { ...CONFIG, identity_provider: 'SSO' } }));
    expect(await screen.findByText(/appear here after their first sign-in/)).toBeInTheDocument();
    expect(screen.getByText(/to remove one, change that deploy option/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Invite user' })).toBeNull();
    // Deleting does not keep a federated user out: the dialog says so and points at disabling.
    fireEvent.click(screen.getAllByRole('button', { name: /^Delete / })[0]!);
    expect(await screen.findByText(/can sign in again through SSO as a new viewer/)).toBeInTheDocument();
  });

  it('closes the invitation with its close button', async () => {
    const { api, calls } = fakeApi({ 'GET /api/users': { items: users } });
    render(wrap(<AdminPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Invite user' }));
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('shows a kill switch changed without a reason', async () => {
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/andon': { genai_enabled: true, reason: '', updated_by: 'user:u', updated_at: '2026-10-01T10:00:00Z' },
      'GET /api/chat/settings': { daily_tokens: 1, per_user_daily_tokens: 1 },
    });
    const { container } = render(wrap(<AdminPage />, { api }));
    createWrapper(container).findTabs()!.findTabLinkById('ai')!.click();
    expect(await screen.findByText(/by another administrator$/)).toBeInTheDocument(); // never a raw principal id
  });

  it('shows disabled budgets, runs without errors and system actors', async () => {
    const { api } = fakeApi({
      'GET /api/users': { items: [] },
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [{ module: 'scout', enabled: false, consumed: 0, limit: 100, state: 'open' }, { module: 'signal', enabled: true, consumed: 100, limit: 100, state: 'exhausted' }], genai_enabled: true },
      'GET /api/runs': { items: [{ module: 'scout', run_id: 'r', status: 'succeeded', trigger: 'manual', started_at: '2026-10-01T10:00:00Z', requested_by: 'u', finished_at: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, weighted_tokens: 0, nodes_written: 0, summary: '', error: null }] },
      'GET /api/audit': { items: [{ actor: 'module:scout', email: '', action: 'node.create', target: 'fact/1', details: {}, at: '2026-10-01T10:00:00Z' }] },
    });
    const { container } = render(wrap(<AdminPage />, { api }));
    const tabs = createWrapper(container).findTabs()!;
    tabs.findTabLinkById('health')!.click();
    expect(await screen.findByText('No (Run now still works)')).toBeInTheDocument();
    expect(screen.getByText('Stopped for today: its budget is used up')).toBeInTheDocument();
    expect(screen.getAllByText('Signals').length).toBeGreaterThan(0);
    expect(await screen.findByText('-')).toBeInTheDocument();
    expect(screen.queryByText('AI features are paused.')).toBeNull();
    tabs.findTabLinkById('audit')!.click();
    expect(await screen.findByText('Scout')).toBeInTheDocument(); // F2-100: not the raw actor id
  });
});
