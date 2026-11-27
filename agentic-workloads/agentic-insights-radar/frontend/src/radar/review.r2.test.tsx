// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-047, F2-049, F2-050, F2-052, F2-053, F2-058, F2-067, F2-068, F2-073, F2-092, F2-122. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import InsightsPage from '@/pages/InsightsPage';
import { ModulesPage } from '@/pages/ModulesPage';
import RadarPage from '@/pages/RadarPage';
import RadarsPage from '@/pages/RadarsPage';
import { module as module_, node, radarView, SCHEMA } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

afterEach(() => vi.restoreAllMocks());

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
const versions = {
  items: [4, 3, 2, 1].map((version) => ({ version, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' })),
};

describe('F2-047: the Changes dialog', () => {
  it('says it is comparing, keeps only the newest answer, and never lets From pass To', async () => {
    const answers = new Map<string, (changes: unknown) => void>();
    const failures = new Map<string, (err: Error) => void>();
    const none = { added: [], removed: [], moved: [], recategorised: [], marker_changed: [], edited: [] };
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
    });
    api.diff = (_slug, from, to) =>
      new Promise((resolve, reject) => {
        answers.set(`${from}-${to}`, (changes) => resolve({ from, to, changes: changes as never }));
        failures.set(`${from}-${to}`, reject);
      });
    render(wrap(routes, { api, path: '/radars/rivals?tab=history' }));
    fireEvent.click((await screen.findAllByRole('button', { name: 'Changes' }, { timeout: 10_000 }))[0]!);
    expect(await screen.findByText('Comparing the versions')).toBeInTheDocument();
    expect(visibleModal().findHeader().getElement()).toHaveTextContent('Changes from version 3 to version 4');
    answers.get('3-4')!({ ...none, added: [{ title: 'Beta', ring: 'Leader' }] });
    expect(await screen.findByText('Added Beta (Leader)')).toBeInTheDocument();
    const selects = () => createWrapper(visibleModal().getElement()).findAllSelects();
    const pick = async (which: 0 | 1, version: string, header: string) => {
      selects()[which]!.openDropdown();
      selects()[which]!.selectOptionByValue(version, { expandToViewport: true });
      await waitFor(() => expect(visibleModal().findHeader().getElement()).toHaveTextContent(header));
    };
    // From equal to To: nothing to swap.
    await pick(0, '4', 'Changes from version 4 to version 4');
    answers.get('4-4')!({ ...none, removed: [{ title: 'Gone' }] });
    expect(await screen.findByText('Removed Gone')).toBeInTheDocument();
    // To below From: the pair is swapped rather than asked for backwards.
    await pick(1, '1', 'Changes from version 1 to version 4');
    answers.get('1-4')!(none);
    expect(await screen.findByText('No differences.')).toBeInTheDocument();
    // An answer that arrives after a newer comparison was asked for does not pull the dialog back.
    await pick(1, '2', 'Changes from version 1 to version 2');
    await pick(1, '3', 'Changes from version 1 to version 3');
    answers.get('1-2')!({ ...none, added: [{ title: 'Stale', ring: 'Leader' }] });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText('Added Stale (Leader)')).toBeNull();
    // Nor does a superseded comparison that fails: its error is no longer wanted.
    await pick(0, '2', 'Changes from version 2 to version 3');
    failures.get('1-3')!(new Error('stale diff failure'));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText('stale diff failure')).toBeNull();
    // Closing it drops an answer still on its way, rather than re-opening on it.
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(false));
  });
});

describe('F2-049: a restore says which row is running', () => {
  it('shows the row busy until it lands', async () => {
    let version = 3;
    const { api, fetcher } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
      'POST /api/radars/rivals/rollback': () => {
        version = 4;
        return radarView().radar;
      },
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=history' }));
    fireEvent.click((await screen.findAllByRole('button', { name: 'Restore' }, { timeout: 10_000 }))[0]!);
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await held;
      return real(...args);
    });
    fireEvent.click(createWrapper(visibleModal().getElement()).findAllButtons().pop()!.getElement());
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Restore' })[0]).toBeDisabled());
    release();
    await waitFor(() => expect(screen.getByText(/Restored version/)).toBeInTheDocument());
  });
});

const suggestion = (over: Record<string, unknown> = {}) => ({
  entry_key: 'k1',
  is_new: false,
  proposal: { ring: 'Challenger' },
  base: radarView().entries[0],
  reason: 'Lost share',
  evidence: ['fact/1', 'fact/2', 'fact/3', 'fact/4', 'fact/5'],
  created_at: '2026-10-01T10:00:00Z',
  created_by: 'module:curator',
  run_id: '0192abcd-ef01-7000-8000-000000000000',
  ...over,
});

describe('F2-050, F2-052, F2-053: the Suggestions tab', () => {
  it('counts the evidence it left out, names the module and asks before discarding', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [suggestion()] },
      'DELETE /api/radars/rivals/suggestions/k1': {},
      'GET /api/graph/nodes/fact/1': node({ id: 'fact/1', type: 'fact', title: 'Docker news' }),
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    await screen.findByText('Lost share');
    expect(screen.getByText('and 2 more')).toBeInTheDocument();
    expect(screen.getByText(/Proposed by Radar curator/)).toBeInTheDocument();
    expect(screen.getByText(/run 0192abcd/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(await screen.findByText('Discard this proposal?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep it' }));
    await waitFor(() => expect(screen.queryByText('Discard this proposal?')).toBeNull());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    visibleModal().findDismissButton().click(); // Esc or the backdrop keeps it too
    await waitFor(() => expect(screen.queryByText('Discard this proposal?')).toBeNull());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Discard' }).pop()!);
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('shows no evidence line when a proposal cites none', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [suggestion({ evidence: [] })] },
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    await screen.findByText('Lost share');
    expect(screen.queryByText(/and \d+ more/)).toBeNull();
  });
});

describe('F2-058 and F2-122: the Settings warnings', () => {
  const users = {
    items: [
      { username: 'u-admin', email: 'admin@example.com', role: 'admin', enabled: true, status: 'CONFIRMED', created_at: '' },
      { username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '' },
    ],
  };

  it('names an editor with no account and refuses Save access', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, editors: ['ed@example.com', 'nobody@example.com'] } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': users,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    expect(await screen.findByText(/nobody@example.com has no Agentic Insights Radar account yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save access' })).toHaveAttribute('aria-disabled', 'true');
    const tokens = createWrapper(document.body).findTokenGroup()!;
    tokens.findToken(1)!.findDismiss().click(); // removing one makes the section dirty
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save access' })).toHaveAttribute('aria-disabled', 'true'));
  });

  it('names two editors with no account in the plural', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, editors: ['one@example.com', 'two@example.com'] } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': users,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    expect(await screen.findByText(/one@example.com, two@example.com have no Agentic Insights Radar account yet/)).toBeInTheDocument();
  });

  it('says what happens to the open proposals when suggestions are switched off', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'assist' } }),
      'GET /api/radars/rivals/suggestions': { items: [suggestion(), suggestion({ entry_key: 'k2' })] },
      'GET /api/users': users,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await screen.findByText('Danger zone');
    const mode = createWrapper(container).findAllSelects()[0]!;
    mode.openDropdown();
    mode.selectOptionByValue('manual', { expandToViewport: true });
    expect(await screen.findByText(/2 open proposals stay as they are while suggestions are off/)).toBeInTheDocument();
  });
});

describe('F2-067 and F2-068: the chart on a touch screen, and its marker key', () => {
  it('draws a key for the marker arcs and hides a tapped tooltip', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema: { ...SCHEMA, markers: [{ name: 'Gaining' }, { name: 'Steady' }] } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/graph/nodes/organization/1': node({ id: 'organization/1', title: 'Acme Inc' }),
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    const key = container.querySelector('.air-marker-key');
    expect(key).not.toBeNull();
    expect(key).toHaveTextContent('Momentum:');
    expect(key).toHaveTextContent('Gaining');
    expect(key).toHaveTextContent('Steady');
    const blip = container.querySelector('g[role="button"]') as SVGGElement;
    fireEvent.mouseEnter(blip);
    expect(await screen.findByText(/Direct \/ Leader/)).toBeInTheDocument();
    fireEvent.pointerUp(blip, { pointerType: 'touch' });
    await waitFor(() => expect(screen.queryByText(/Direct \/ Leader/)).toBeNull());
  });
});

describe('F2-073: a figure explains itself where a touch user can read it', () => {
  it('uses a Popover for links and confidence, and for Today', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': { items: [node({ degree: 4, confidence: 0.72 })] },
    });
    render(wrap(<Routes><Route path="/insights/:kind" element={<InsightsPage />} /></Routes>, { api, path: '/insights/signals' }));
    fireEvent.click(await screen.findByText('4 links'));
    expect(await screen.findByText(/Evidence and related records linked to this/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('confidence 72%'));
    expect(await screen.findByText(/How sure the module is/)).toBeInTheDocument();
    const modules = fakeApi({ 'GET /api/modules': { items: [module_({ slug: 'scout' })] } });
    render(wrap(<ModulesPage />, { api: modules.api, path: '/modules' }));
    fireEvent.click((await screen.findAllByText('Today')).pop()!);
    expect(await screen.findByText(/starts again after midnight UTC/)).toBeInTheDocument();
  });
});

describe('F2-092: the Discard warning in Create radar takes focus', () => {
  it('focuses it, so Esc at the bottom of the form is not silent', async () => {
    const { api } = fakeApi({
      'GET /api/radars': { items: [] },
      'GET /api/radar-templates': { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }] },
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /></Routes>, { api, path: '/radars?create=1' }));
    const name = await waitFor(() => screen.getByLabelText('Name'));
    fireEvent.change(name, { target: { value: 'Rivals' } });
    visibleModal().findDismissButton().click();
    const warning = await screen.findByText('Discard this radar?');
    await waitFor(() => expect(document.activeElement?.contains(warning) || document.activeElement === warning).toBe(true));
  });
});
