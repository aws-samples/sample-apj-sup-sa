// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { fakeApi, wrap } from '@/test/session';

import { ApiError } from '@/api/client';

import EntityHygienePanel, { mergeOutcome, unfinishedMerges } from './EntityHygiene';

const keep = { id: 'organization/1', type: 'organization', title: 'Acme Motors', degree: 9 };
const dup = { id: 'organization/2', type: 'organization', title: 'ACME Motor Co', degree: 1 };
const lone = { id: 'person/3', type: 'person', title: 'Ada', degree: 0 };
const plan = {
  duplicate: dup.id, into: keep.id, applied: false, edges: 2, moved: 1,
  dropped: [{ from: dup.id, type: 'competes_with', to: keep.id, why: 'it would link the kept entity to itself' }],
  redirected: [], aliases_added: ['ACME Motor Co'], descriptions_rewritten: ['trend/1'],
};

describe('Administration > Entities', () => {
  it('shows what a merge would do, merges, and reads the report again', async () => {
    let merged = false;
    const { api, calls } = fakeApi({
      'GET /api/graph/hygiene': () => ({
        scanned: 3, orphans: [lone], orphan_count: 1, duplicates: merged ? [] : [[keep, dup]], duplicate_count: merged ? 0 : 1,
      }),
      'POST /api/graph/nodes/organization/2/merge': (body: unknown) => {
        if ((body as { apply: boolean }).apply) merged = true;
        return { ...plan, applied: (body as { apply: boolean }).apply };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Merge ACME Motor Co into Acme Motors' }));
    const dialog = createWrapper(document.body).findModal()!;
    expect(await screen.findByText(/would link the kept entity to itself/)).toBeInTheDocument();
    expect(dialog.getElement()).toHaveTextContent('ACME Motor Co');
    fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
    expect(await screen.findByText('ACME Motor Co was merged into Acme Motors. 1 link moved; 1 link dropped.')).toBeInTheDocument();
    expect(await screen.findByText('No likely duplicates.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST').map((c) => (c.body as { apply: boolean }).apply)).toEqual([false, true]);
  });

  it('says when the dry run or the merge fails, and closes on Cancel', async () => {
    const { api } = fakeApi({
      'GET /api/graph/hygiene': { scanned: 2, orphans: [], orphan_count: 0, duplicates: [[keep, dup]], duplicate_count: 1 },
      'POST /api/graph/nodes/organization/2/merge': (body: unknown) => {
        if ((body as { apply: boolean }).apply) throw { status: 409, code: 'conflict', message: 'too many links' };
        return { ...plan, dropped: [], aliases_added: [] };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: /^Merge ACME/ }));
    await waitFor(() => expect(screen.getAllByText('None')).toHaveLength(2)); // nothing dropped, no new names
    fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
    expect(await screen.findByText('too many links')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /^Merge ACME/ }));
    createWrapper(document.body).findModal()!.findDismissButton().click();
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    expect(screen.getByText('Every entity is linked.')).toBeInTheDocument();
  });

  it('names a merge that stopped part way and finishes it (code review r73)', async () => {
    let finished = false;
    const { api, calls } = fakeApi({
      'GET /api/graph/hygiene': { scanned: 2, orphans: [], orphan_count: 0, duplicates: [[keep, dup]], duplicate_count: 1 },
      'POST /api/graph/nodes/organization/2/merge': () => {
        if (!finished) throw { status: 409, code: 'conflict', message: 'a merge into this entity stopped part way; run it again to finish it', details: { from: ['organization/9', 7] } };
        return { ...plan, redirected: [
          { from: dup.id, type: 'partners_with', to: 'organization/5', via: 'organization/6' },
          { from: 'organization/7', type: 'supplies', to: dup.id, via: 'organization/8' },
        ] };
      },
      'POST /api/graph/nodes/organization/9/merge': () => {
        finished = true;
        return { ...plan, duplicate: 'organization/9', into: dup.id, applied: true };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: /^Merge ACME/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Finish merging organization/9 into organization/2' }));
    expect(await screen.findByText('The merge of organization/9 into organization/2 was finished.')).toBeInTheDocument();
    expect(
      await screen.findByText('partners_with: to organization/6 instead of organization/5; supplies: to organization/8 instead of organization/7'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge' })).toBeEnabled();
    expect(calls.find((c) => c.path.includes('organization/9'))?.body).toEqual({ into: dup.id, apply: true });
    expect(calls.filter((c) => c.path === '/api/graph/hygiene')).toHaveLength(2); // the tables are read again
  });

  it("closes once it has finished this entity's own earlier merge (code review r74)", async () => {
    let gone = false;
    const { api } = fakeApi({
      'GET /api/graph/hygiene': () => ({ scanned: 2, orphans: [], orphan_count: 0, duplicates: gone ? [] : [[keep, dup]], duplicate_count: gone ? 0 : 1 }),
      'POST /api/graph/nodes/organization/2/merge': (body: unknown) => {
        if ((body as { into: string }).into === 'organization/6') {
          gone = true;
          return { ...plan, into: 'organization/6', applied: true };
        }
        throw { status: 409, code: 'conflict', message: 'a merge of this entity into another stopped part way; finish that one', details: { into: 'organization/6' } };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: /^Merge ACME/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Finish merging organization/2 into organization/6' }));
    expect(await screen.findByText('The merge of organization/2 into organization/6 was finished.')).toBeInTheDocument();
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    expect(await screen.findByText('No likely duplicates.')).toBeInTheDocument();
  });

  it('says what an applied merge did', () => {
    const via = (to: string) => ({ from: 'a', type: 't', to, via: 'organization/6' });
    expect(mergeOutcome({ ...plan, moved: 2, dropped: [], redirected: [via('x'), via('y')] })).toBe(
      "2 links moved; 2 links taken on to organization/6, where a linked entity's earlier merge went.",
    );
  });

  it('names a linked entity whose merge stopped part way when the merge itself is refused', async () => {
    const { api } = fakeApi({
      'GET /api/graph/hygiene': { scanned: 2, orphans: [], orphan_count: 0, duplicates: [[keep, dup]], duplicate_count: 1 },
      'POST /api/graph/nodes/organization/2/merge': (body: unknown) => {
        if ((body as { apply: boolean }).apply) throw { status: 409, code: 'conflict', message: "a linked entity's merge stopped part way; finish it first", details: { entity: 'organization/5', into: 'organization/6' } };
        return plan;
      },
      'POST /api/graph/nodes/organization/5/merge': () => {
        throw { status: 409, code: 'conflict', message: 'still busy' };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: /^Merge ACME/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Merge' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Finish merging organization/5 into organization/6' }));
    expect(await screen.findByText('still busy')).toBeInTheDocument();
  });

  it('reads the unfinished merges out of a refusal', () => {
    expect(unfinishedMerges(new Error('x'), 'a')).toEqual([]);
    expect(unfinishedMerges(new ApiError(409, 'conflict', 'x', {}), 'a')).toEqual([]);
    expect(unfinishedMerges(new ApiError(409, 'conflict', 'x', { into: 'b' }), 'a')).toEqual([{ dup: 'a', into: 'b' }]);
  });

  it('reports a dry run that cannot be worked out', async () => {
    const { api } = fakeApi({
      'GET /api/graph/hygiene': { scanned: 2, orphans: [], orphan_count: 0, duplicates: [[keep, dup]], duplicate_count: 1 },
      'POST /api/graph/nodes/organization/2/merge': () => {
        throw { status: 400, code: 'validation', message: 'only entities can be merged' };
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: /^Merge ACME/ }));
    expect(await screen.findByText(/could not be worked out: only entities can be merged/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge' })).toBeDisabled();
  });

  it('deletes an unlinked entity after asking, and reports a refusal', async () => {
    let gone = false;
    const { api } = fakeApi({
      'GET /api/graph/hygiene': () => ({ scanned: 1, orphans: gone ? [] : [lone], orphan_count: gone ? 0 : 1, duplicates: [], duplicate_count: 0 }),
      'DELETE /api/graph/nodes/person/3': () => {
        gone = true;
        return {};
      },
    });
    render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Ada' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); // changed their mind
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Delete Ada' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Ada was deleted.')).toBeInTheDocument();
    expect(await screen.findByText('Every entity is linked.')).toBeInTheDocument();
  });

  it('reports a refused delete and a report that cannot be loaded', async () => {
    const { api } = fakeApi({
      'GET /api/graph/hygiene': { scanned: 1, orphans: [lone], orphan_count: 1, duplicates: [], duplicate_count: 0 },
      'DELETE /api/graph/nodes/person/3': () => {
        throw { status: 403, code: 'forbidden', message: 'not allowed' };
      },
    });
    const first = render(wrap(<EntityHygienePanel />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Ada' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('not allowed')).toBeInTheDocument();
    first.unmount();
    const broken = fakeApi({});
    render(wrap(<EntityHygienePanel />, { api: broken.api }));
    expect((await screen.findAllByText(/could not be loaded/)).length).toBeGreaterThan(0);
  });
});

describe('the Entities tab', () => {
  it('opens from the address (it was missing from the tab list, so it fell back to Users)', async () => {
    const { default: AdminPage } = await import('@/pages/AdminPage');
    const { api } = fakeApi({ 'GET /api/graph/hygiene': { scanned: 0, orphans: [], orphan_count: 0, duplicates: [], duplicate_count: 0 } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=entities' }));
    expect(await screen.findByText('No likely duplicates.')).toBeInTheDocument();
  });
});
