// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-025, F2-029, F2-036, F2-037, F2-039, F2-040, F2-085, F2-117. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { signInProblem } from '@/App';
import { sheetHeight, sheetLines } from '@/radar/ExportSheet';
import { layout } from '@/radar/layout';
import { detail, module as module_, node, SCHEMA } from '@/test/data';
import { ADMIN, chooseMoreAction, fakeApi, wrap } from '@/test/session';

import InsightDetailPage from './InsightDetailPage';
import { ModulePage } from './ModulesPage';

afterEach(() => vi.restoreAllMocks());

const detailRoutes = (
  <Routes>
    <Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} />
    <Route path="/insights/:kind" element={<div>signals list</div>} />
  </Routes>
);

const draftOf = (n: number) => ({
  edge: 'drafts' as const,
  direction: 'out' as const,
  node: { id: `content_draft/${n}`, type: 'content_draft', title: `Draft ${n}`, summary: '', updated_at: '2026-10-01T10:00:00Z' },
});

describe('F2-040: a collapsed draft downloads nothing', () => {
  it('reads a draft only once its section is opened', async () => {
    const idea = detail({
      node: node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' }),
      evidence: { ...detail().evidence, drafts: [draftOf(1), draftOf(2), draftOf(3)] },
      working_on: [],
    });
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [] },
      'GET /api/drafts/content_draft/1': new Response('# One'),
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/content_idea/1' }));
    expect(await screen.findByText(/Draft 1/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith('/api/drafts/'))).toHaveLength(0);
    fireEvent.click(screen.getByText(/Draft 1/));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/drafts/'))).toHaveLength(1));
    expect(await screen.findByText('One')).toBeInTheDocument();
  });
});

describe('F2-117: a failed draft download can be tried again', () => {
  it('offers Retry and then shows the draft', async () => {
    let hits = 0;
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_draft/1': detail({ node: node({ id: 'content_draft/1', type: 'content_draft', title: 'The draft' }) }),
      'GET /api/drafts/content_draft/1': () => {
        hits += 1;
        if (hits === 1) throw { status: 500, message: 'storage is broken' };
        return new Response('# Body');
      },
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/content_draft/1' }));
    expect(await screen.findByText('storage is broken')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Body')).toBeInTheDocument();
  });
});

describe('F2-036: the "Writing a draft" indicator stops', () => {
  it('says no draft arrived once it has given up', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const idea = detail({ node: node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' }), working_on: [] });
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [module_({ slug: 'draft', enabled: true })] },
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r1', status: 'queued' },
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/content_idea/1', me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByText('Writing a draft')).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(31 * 20_000);
    await waitFor(() => expect(screen.getByText(/No draft has arrived/)).toBeInTheDocument());
    expect(screen.queryByText('Writing a draft')).toBeNull();
    vi.useRealTimers();
  });
});

describe('F2-036: an editor is told to ask an administrator', () => {
  it('words the message for someone who cannot read the module runs', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const editor = { subject: 'u-ed', email: 'ed@example.com', role: 'editor' as const, can: ['read', 'content.draft'] };
    const idea = detail({ node: node({ id: 'content_idea/1', type: 'content_idea', title: 'Write it' }), working_on: [] });
    const { api } = fakeApi({
      'GET /api/insights/nodes/content_idea/1': idea,
      'GET /api/modules': { items: [module_({ slug: 'draft', enabled: true })] },
      'POST /api/nodes/content_idea/1/draft': { run_id: 'r1', status: 'queued' },
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/content_idea/1', me: editor }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write a draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByText('Writing a draft')).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(31 * 20_000);
    await waitFor(() => expect(screen.getByText(/ask an administrator to check the Content drafts module/)).toBeInTheDocument());
    vi.useRealTimers();
  });
});

describe('F2-037: an administrator can delete a graph record', () => {
  it('asks first, then removes it and goes back to the list', async () => {
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/signal/1': detail(),
      'DELETE /api/graph/nodes/signal/1': {},
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/signal/1' }));
    await chooseMoreAction('Delete this record');
    expect(screen.getByText(/leaves every list and every answer that cites it. A radar entry linked to it keeps its place/)).toBeInTheDocument();
    const dialog = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    fireEvent.click(within(dialog.getElement()).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('signals list')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/graph/nodes/signal/1')).toBe(true);
  });

  it('can be called off, and reports a failed delete', async () => {
    const { api, calls } = fakeApi({
      'GET /api/insights/nodes/signal/1': detail(),
      'DELETE /api/graph/nodes/signal/1': () => {
        throw { status: 403, message: 'not yours to delete' };
      },
    });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/signal/1' }));
    await chooseMoreAction('Delete this record');
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(false));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await chooseMoreAction('Delete this record');
    const dialog = createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    fireEvent.click(within(dialog.getElement()).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('not yours to delete')).toBeInTheDocument();
    expect(screen.queryByText('signals list')).toBeNull();
  });

  it('is not offered to someone without graph.edit', async () => {
    const { api } = fakeApi({ 'GET /api/insights/nodes/signal/1': detail() });
    render(wrap(detailRoutes, { api, path: '/insights/nodes/signal/1', me: { ...ADMIN, can: ['read'] } }));
    expect(await screen.findByText('Docker takes its spec to the CNCF')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull();
  });
});

describe('F2-039: deleting a module says what happens to its insights', () => {
  it('names the list that leaves the navigation', async () => {
    const custom = module_({ slug: 'pw', name: 'Pricing watch', builtin: false, output_kind: 'signal', grants: ['kg_read'] });
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
      'GET /api/nav': { builtin: [], custom: [] },
    });
    render(wrap(<Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>, { api, path: '/modules/pw' }));
    await chooseMoreAction('Delete module');
    expect(screen.getByText(/Everything it has published stays in the knowledge graph/)).toBeInTheDocument();
    expect(screen.getByText(/its list \(Signals\) leaves the navigation/)).toBeInTheDocument();
  });
});

describe('F2-029: the exported picture carries its legend', () => {
  it('lays out a heading per sector, a heading per ring and a numbered line per entry', () => {
    const entries = [
      { key: 'a', title: 'Acme', sector: 'Direct', ring: 'Leader', marker: 'Gaining', description: '' },
      { key: 'b', title: 'Beta', sector: 'Direct', ring: 'Challenger', description: '' },
      { key: 'c', title: 'Gamma', sector: 'Adjacent', ring: 'Leader', description: '' },
    ];
    const data = layout(SCHEMA, entries);
    const lines = sheetLines(data.blips, data.bands, data.wedges);
    expect(lines.map((l) => `${l.kind}:${l.text}`)).toEqual([
      'sector:Direct',
      'ring:Leader',
      'entry:Acme (Gaining)',
      'ring:Challenger',
      'entry:Beta',
      'sector:Adjacent',
      'ring:Leader',
      'entry:Gamma',
    ]);
    expect(lines.filter((l) => l.kind === 'entry').map((l) => l.number)).toEqual([1, 2, 3]);
  });

  it('clips a long title and grows the sheet for a long legend', () => {
    const long = 'A'.repeat(80);
    const data = layout(SCHEMA, [{ key: 'a', title: long, sector: 'Direct', ring: 'Leader', description: '' }]);
    const lines = sheetLines(data.blips, data.bands, data.wedges);
    expect(lines[2]?.text).toHaveLength(34);
    expect(sheetHeight(4, 1)).toBe(840); // the chart is the floor
    expect(sheetHeight(100, 0)).toBeGreaterThan(840);
    expect(sheetHeight(1, 60)).toBeGreaterThan(840);
  });
});

describe('F2-085: sign-in failures are said in words', () => {
  it('maps the ones the library words badly, and passes the rest through', () => {
    expect(signInProblem(undefined)).toBe('');
    expect(signInProblem(new Error('No matching state found in storage'))).toBe('Sign-in was interrupted, so it could not be finished. Sign in again.');
    expect(signInProblem(new Error('The authorization request expired'))).toBe('The sign-in took too long and expired. Sign in again.');
    expect(signInProblem(new Error('invalid_grant'))).toBe('invalid_grant');
  });
});
