// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 backend contracts: restricted products, shrink confirmations, reviewed suggestions, queued deletes, autopilot, Retry-After. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiClient, ApiError, needsShrinkConfirm, retryAfterOf, shrinkOf } from '@/api/client';
import type { Me, RadarEntry, Suggestion } from '@/api/types';
import { describeError, reopensAt, waitFor as waitText } from '@/components/Notifications';
import { AUTOPILOT_ADMIN_ONLY, NO_ACCOUNT, noAccountOf, NOTHING_TO_DRAW, SUGGESTION_PENDING, SUGGESTION_STALE, stillApplies } from '@/radar/RadarTabs';
import { detail, node, product, radarView } from '@/test/data';
import { ADMIN, fakeApi, VIEWER, wrap } from '@/test/session';

import InsightDetailPage from './InsightDetailPage';
import { ProductPage } from './ProductsPage';
import RadarPage from './RadarPage';

const EDITOR = { ...ADMIN, subject: 'u-ed', role: 'editor' as const, can: ['read', 'chat', 'radar.create', 'radar.edit', 'product.read', 'graph.edit'] };
const radarRoutes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>;
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
const entry = (i: number): RadarEntry => ({ key: `k${i}`, title: `Rival ${i}`, sector: 'Direct', ring: 'Leader', description: '' });

afterEach(() => {
  vi.restoreAllMocks();
});

describe('a restricted product', () => {
  const restricted = product({
    restricted: true,
    capabilities: [],
    segments: [],
    competitors: [],
    notes: '',
    created_by: '',
    stats: {},
    doc_urls: ['https://docs.example.com'],
  });

  it('shows the name, summary and documentation sites with one line about the rest, and no empty sections', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': restricted });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, me: VIEWER, path: '/products/p1' }));
    expect(await screen.findByRole('heading', { name: 'DeskPro' })).toBeInTheDocument();
    expect(screen.getByText(/Your role sees the name, summary and documentation sites of a product/)).toBeInTheDocument();
    expect(screen.getByText('Documentation sites')).toBeInTheDocument();
    expect(screen.getByText('docs.example.com')).toBeInTheDocument();
    for (const label of ['Capabilities', 'Segments', 'Competitors']) expect(screen.queryByText(label)).toBeNull();
  });

  it('keeps the full profile for an administrator', async () => {
    const { api } = fakeApi({ 'GET /api/products/p1': product(), 'GET /api/products/p1/documents': { items: [] }, 'GET /api/modules': { items: [] } });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    expect(await screen.findByText('Capabilities')).toBeInTheDocument();
    expect(screen.getByText('Documentation')).toBeInTheDocument();
    expect(screen.queryByText(/Your role sees the name/)).toBeNull();
  });
});

describe('a save or a restore that removes most of the radar', () => {
  const many = radarView({ entries: [1, 2, 3, 4].map(entry), radar: { ...radarView().radar, entry_count: 4 } });

  it('confirms Save version with the counts, then sends it again with allow_empty', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': many });
    let finish: () => void = () => undefined;
    const save = vi.spyOn(api, 'saveEntries').mockImplementation((_slug, _rows, _v, _note, allowEmpty) =>
      allowEmpty
        ? new Promise((resolve) => (finish = () => resolve({ ...many.radar, current_version: 3 })))
        : Promise.reject(new ApiError(400, 'validation_failed', 'this would remove 3 of 4 entries', { code: 'shrinking_save', before: 4, after: 1, removed: 3 })),
    );
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
    await screen.findAllByLabelText(/^Title of /);
    const table = createWrapper(container).findTable()!;
    for (const row of [2, 3, 4]) table.findRowSelectionArea(row)!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Remove 3' }));
    const ask = async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
      expect(await screen.findByText('Remove 3 entries?')).toBeInTheDocument();
    };
    await ask();
    expect(screen.getByText(/Saving would leave the radar with 1 entry instead of 4: 3 entries would be removed/)).toBeInTheDocument();
    visibleModal().findDismissButton().click(); // closing it is a Cancel: nothing is sent
    await waitFor(() => expect(screen.queryByText('Remove 3 entries?')).toBeNull());
    await ask();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Remove 3 entries?')).toBeNull());
    await ask();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Save and remove 3 entries' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(4));
    expect(save.mock.calls.map((c) => c[4])).toEqual([false, false, false, true]);
    visibleModal().findDismissButton().click(); // still saving: the dialog stays with its spinner
    expect(screen.getByText('Remove 3 entries?')).toBeInTheDocument();
    finish();
    await waitFor(() => expect(screen.queryByText('Remove 3 entries?')).toBeNull());
  });

  it('confirms a Restore with the counts, and Cancel sends nothing more', async () => {
    const sent: unknown[] = [];
    const { api } = fakeApi({
      'GET /api/radars/rivals': many,
      'GET /api/radars/rivals/versions': { items: [{ version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' }] },
      'POST /api/radars/rivals/rollback': (body: unknown) => {
        sent.push(body);
        if (!(body as { allow_empty?: boolean }).allow_empty) {
          throw { status: 400, code: 'validation_failed', message: 'this would remove 3 of 4 entries', details: { code: 'shrinking_rollback', before: 4, after: 1, removed: 3 } };
        }
        return { ...many.radar, current_version: 3 };
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    const restore = async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
      fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
      expect(await screen.findByText(/Restoring version 1 would leave the radar with 1 entry instead of 4/)).toBeInTheDocument();
    };
    await restore();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Remove 3 entries?')).toBeNull());
    expect(sent).toHaveLength(1);
    await restore();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore and remove 3 entries' }));
    expect(await screen.findByText('Restored version 1 as a new version.')).toBeInTheDocument();
    expect(sent).toHaveLength(3);
    expect((sent[2] as Record<string, unknown>).allow_empty).toBe(true);
  });

  it('sends allow_empty on a confirmed save and nothing extra otherwise', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/radars/rivals/entries': radarView().radar });
    await api.saveEntries('rivals', [], 2, 'note', true);
    await api.saveEntries('rivals', [], 2);
    expect(calls.map((c) => (c.body as Record<string, unknown>).allow_empty)).toEqual([true, undefined]);
  });

  it('reads the counts only from the code it was asked about', () => {
    // The real shape (core/errors.py to_body): validation_failed, with the specific code in details (CR3-05).
    const err = new ApiError(400, 'validation_failed', 'x', { code: 'shrinking_save', before: 4, after: 1 });
    expect(shrinkOf(err, 'shrinking_save')).toEqual({ before: 4, after: 1 });
    expect(shrinkOf(err, 'shrinking_rollback')).toBeNull();
    expect(shrinkOf(new ApiError(400, 'validation_failed', 'x', { code: 'shrinking_save' }), 'shrinking_save')).toBeNull();
    // A top-level code is not where the API puts it.
    expect(shrinkOf(new ApiError(400, 'shrinking_save', 'x', { before: 4, after: 1 }), 'shrinking_save')).toBeNull();
  });

  it('asks for the confirmation before sending a Replace exactly when the API would (CR3-05)', () => {
    expect(needsShrinkConfirm(10, 9)).toBe(true);
    expect(needsShrinkConfirm(4, 3)).toBe(true);
    expect(needsShrinkConfirm(4, 2)).toBe(false); // half is not more than half
    expect(needsShrinkConfirm(2, 2)).toBe(false); // fewer than three removed
    expect(needsShrinkConfirm(10, 0)).toBe(false);
    expect(shrinkOf(new Error('x'), 'shrinking_save')).toBeNull();
  });
});

describe('a suggestion with another one open', () => {
  const suggestion: Suggestion = {
    entry_key: 'k1',
    is_new: false,
    proposal: { ring: 'Challenger' },
    base: radarView().entries[0] as RadarEntry,
    reason: 'Lost share',
    evidence: [],
    created_at: '2026-10-01T10:00:00Z',
    created_by: 'module:curator',
  };

  it('explains a 409 suggestion_pending and reloads the list', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [suggestion] },
      'POST /api/radars/rivals/suggestions/k1/accept': () => {
        throw { status: 409, code: 'conflict', message: 'this entry already has an open suggestion', details: { code: 'suggestion_pending' } };
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText(SUGGESTION_PENDING)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/suggestions')).length).toBeGreaterThanOrEqual(2));
  });
});

describe('a suggestion that no longer applies (CTR HA-013)', () => {
  const base = radarView().entries[0] as RadarEntry;
  const sug = (over: Partial<Suggestion>): Suggestion => ({
    entry_key: base.key,
    is_new: false,
    proposal: { ring: 'Challenger' },
    base,
    reason: 'Lost share',
    evidence: [],
    created_at: '2026-10-01T10:00:00Z',
    created_by: 'module:curator',
    ...over,
  });

  it('compares the entry with the base the way the backend does', () => {
    const entries = radarView().entries;
    expect(stillApplies(sug({}), entries)).toBe(true);
    expect(stillApplies(sug({ base: { ...base, ring: 'Something else' } }), entries)).toBe(false);
    expect(stillApplies(sug({ entry_key: 'gone' }), entries)).toBe(false);
    expect(stillApplies(sug({ is_new: true, base: null, entry_key: 'brand-new' }), entries)).toBe(true);
    expect(stillApplies(sug({ is_new: true, base: null }), entries)).toBe(false);
    // An absent optional field equals an empty one.
    expect(stillApplies(sug({ base: { ...base, link: '' } }), entries.map((e) => ({ ...e, link: undefined })))).toBe(true);
  });

  it('marks a stale edit, disables Accept and keeps Discard', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [sug({ base: { ...base, ring: 'Something else' } })] },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByText(/No longer applicable/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Discard' })).not.toBeDisabled();
  });

  it('explains a 409 suggestion_stale and reloads the list', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [sug({})] },
      [`POST /api/radars/rivals/suggestions/${base.key}/accept`]: () => {
        throw { status: 409, code: 'conflict', message: 'the entry changed since this suggestion was made', details: { code: 'suggestion_stale' } };
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText(SUGGESTION_STALE)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/suggestions')).length).toBeGreaterThanOrEqual(2));
  });
});

describe('the accept and discard notices name the entry (CTR HU-022)', () => {
  it('says which suggestion was applied or discarded', async () => {
    const base = radarView().entries[0] as RadarEntry;
    const items = [
      { entry_key: base.key, is_new: false, proposal: { ring: 'Challenger' }, base, reason: 'r', evidence: [], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' },
      { entry_key: 'n1', is_new: true, proposal: { title: 'Desky', sector: 'Direct', ring: 'Watch' }, base: null, reason: 'r', evidence: [], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' },
    ];
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items },
      [`POST /api/radars/rivals/suggestions/${base.key}/accept`]: radarView().radar,
      'DELETE /api/radars/rivals/suggestions/n1': {},
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions' }));
    fireEvent.click((await screen.findAllByRole('button', { name: 'Accept' }))[0]!);
    expect(await screen.findByText(`Applied: ${base.title}, saved as a new version.`)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Discard' })[1]!);
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Discard' }));
    expect(await screen.findByText('Discarded: Desky.')).toBeInTheDocument();
  });
});

describe('an empty radar and the action columns (CTR HU-061, QA-090)', () => {
  it('turns SVG and PNG off on a radar with no entries', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: [] }),
      'GET /api/radars/rivals/suggestions': { items: [] },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=io' }));
    for (const name of ['SVG', 'PNG']) expect(await screen.findByRole('button', { name })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getAllByText(NOTHING_TO_DRAW).length).toBeGreaterThan(0);
  });

  it('names the Suggestions action column', async () => {
    const base = radarView().entries[0] as RadarEntry;
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': {
        items: [{ entry_key: base.key, is_new: false, proposal: { ring: 'Challenger' }, base, reason: 'r', evidence: [], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' }],
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByRole('columnheader', { name: 'Actions' })).toBeInTheDocument();
  });
});

describe('an editor address no account signs in with (CTR HCB-037)', () => {
  it('names the address, saves nothing, and adds it on the second press', async () => {
    let first = true;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PUT /api/radars/rivals/access': () => {
        if (first) {
          first = false;
          throw { status: 400, code: 'validation_failed', message: 'no account', details: { code: 'unknown_editors', unknown: ['pta@example.com'] } };
        }
        return radarView().radar;
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByLabelText('Editor email'), { target: { value: 'pta@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save access' }));
    expect(await screen.findByText(NO_ACCOUNT(['pta@example.com']))).toBeInTheDocument();
    expect(screen.queryByText('Access updated.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add anyway' }));
    expect(await screen.findByText('Access updated.')).toBeInTheDocument();
    const puts = calls.filter((c) => c.method === 'PUT' && c.path.endsWith('/access')).map((c) => (c.body as Record<string, unknown>).confirm_unknown);
    expect(puts).toEqual([undefined, true]);
  });

  it('reports any other access error as before', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PUT /api/radars/rivals/access': () => {
        throw { status: 400, code: 'validation_failed', message: "editor 'x' must be an email address", details: {} };
      },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByLabelText('Editor email'), { target: { value: 'pat@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save access' }));
    expect(await screen.findByText(/must be an email address/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save access' })).toBeInTheDocument();
  });

  it('reads the addresses only from an unknown_editors 400', () => {
    expect(noAccountOf(new ApiError(400, 'validation_failed', 'x', { code: 'unknown_editors', unknown: ['a@b.c'] }))).toEqual(['a@b.c']);
    expect(noAccountOf(new ApiError(400, 'validation_failed', 'x', { code: 'unknown_editors' }))).toBeNull();
    expect(noAccountOf(new ApiError(400, 'validation_failed', 'x', { code: 'other' }))).toBeNull();
    expect(noAccountOf(new ApiError(409, 'conflict', 'x', { code: 'unknown_editors', unknown: ['a@b.c'] }))).toBeNull();
    expect(noAccountOf(new Error('x'))).toBeNull();
    expect(NO_ACCOUNT(['a@b.c', 'd@e.f'])).toContain('add them');
  });
});

describe('deleting a record with more than 500 links', () => {
  it('says the delete was queued and will finish shortly', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/nodes/signal/1': detail({ node: node({ title: 'Big hub' }) }),
      'DELETE /api/graph/nodes/signal/1': new Response(JSON.stringify({ queued: true, edges: 812 }), { status: 202 }),
    });
    render(
      wrap(
        <Routes>
          <Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} />
          <Route path="/insights/signals" element={<div>signals list</div>} />
        </Routes>,
        { api, me: EDITOR, path: '/insights/nodes/signal/1' },
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete this record' }));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('signals list')).toBeInTheDocument();
    expect(screen.getByText(/"Big hub" is being deleted with its 812 links\. It will finish shortly/)).toBeInTheDocument();
  });
});

describe('autopilot while proposals apply without review', () => {
  const armed = radarView({ radar: { ...radarView().radar, mode: 'copilot', auto_apply_agent: true }, can_admin: true });
  const settings = (me: Me = EDITOR) => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': armed, 'GET /api/radars/rivals/suggestions': { items: [] }, 'GET /api/users': { items: [] } });
    const { container } = render(wrap(radarRoutes, { api, me, path: '/radars/rivals?tab=settings' }));
    return container;
  };
  const autopilot = async (container: HTMLElement) => {
    await screen.findByText('Danger zone');
    const mode = createWrapper(container).findAllSelects()[0]!;
    mode.openDropdown();
    return mode.findDropdown({ expandToViewport: true }).findOptionByValue('autopilot')!;
  };

  it('disables Autopilot for an owner who is not an administrator, with the reason', async () => {
    const option = await autopilot(settings());
    expect(option.getElement().className).toMatch(/disabled/);
    expect(option.getElement().closest('[aria-disabled="true"]')).not.toBeNull();
    fireEvent.mouseMove(option.getElement());
    expect((await screen.findAllByText(AUTOPILOT_ADMIN_ONLY)).length).toBeGreaterThan(0);
  });

  it('leaves Autopilot open to an administrator', async () => {
    const option = await autopilot(settings(ADMIN));
    expect(option.getElement().className).not.toMatch(/disabled/);
  });
});

describe('Retry-After', () => {
  it('has no wait for paused AI features, even with a header', () => {
    expect(retryAfterOf(503, {}, new Headers({ 'retry-after': '1' }), 'genai_disabled')).toBeNull();
    expect(retryAfterOf(503, {}, new Headers({ 'retry-after': '1' }), 'throttled')).toBe(1);
  });

  it('says when a closed daily budget opens again as a clock time', () => {
    const err = new ApiError(503, 'budget_exhausted', 'the daily budget is used up', {}, 3600);
    expect(describeError(err)).toBe(`The daily budget is used up. The daily budget opens again at midnight UTC, ${reopensAt(3600)} your time.`);
    expect(reopensAt(0, Date.UTC(2026, 9, 8, 0, 0))).toBe(new Date(Date.UTC(2026, 9, 8, 0, 0)).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }));
    expect(waitText(3600)).toBe('in an hour');
    expect(waitText(7200)).toBe('in 2 hours');
  });

  it('does not send a GET again for paused AI features or a closed budget', async () => {
    for (const code of ['genai_disabled', 'budget_exhausted']) {
      const fetcher = vi.fn(async () => new Response(JSON.stringify({ code, message: 'no' }), { status: 503, headers: { 'retry-after': '5' } }));
      const api = new ApiClient(() => 't', fetcher as unknown as typeof fetch);
      const error = await api.health().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).retryAfter).toBe(code === 'genai_disabled' ? null : 5);
      expect(fetcher).toHaveBeenCalledTimes(1);
    }
  });
});
