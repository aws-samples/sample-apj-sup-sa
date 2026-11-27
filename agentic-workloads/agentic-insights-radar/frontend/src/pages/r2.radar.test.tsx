// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 red-team findings about editing a radar: conflicts, saves in flight, failed actions. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { entriesToCsv } from '@/radar/export';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage from './RadarPage';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const tabs = (container: HTMLElement) => createWrapper(container).findTabs()!;
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

afterEach(() => vi.restoreAllMocks());

/** Opens Edit entries and types a new title, leaving one unsaved change. */
async function editOneRow(container: HTMLElement) {
  await screen.findByRole('group', { name: 'Rivals radar' });
  tabs(container).findTabLinkById('edit')!.click();
  fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
  expect(screen.getByText(/^1 unsaved change:/)).toBeInTheDocument();
}

describe('F2-002: a save that loses a race keeps the edits', () => {
  it('offers the edits as CSV and an explicit reload instead of an error toast', async () => {
    const files: { name: string; body: string }[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(Blob.prototype, 'text').mockImplementation(async function (this: Blob) {
      return '';
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      files.push({ name: this.download, body: '' });
    });
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar; reload and retry' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('Someone else saved this radar first')).toBeInTheDocument();
    expect(screen.getByText(/Your edits are still here/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download my changes as CSV' }));
    expect(files.map((f) => f.name)).toEqual(['rivals-my-changes.csv']);
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument(); // still there
  });

  it('loads the newer version and keeps the edits on it (CR2-F02)', async () => {
    let version = 2;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'PUT /api/radars/rivals/entries': () => {
        version = 5;
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText('Your 1 change was re-applied on version 5')).toBeInTheDocument();
    expect(screen.getAllByLabelText(/^Title of /).map((e) => (e as HTMLInputElement).value)).toEqual(['Acme Corp']);
  });

  it('keeps the edits when the conflict dialog is dismissed', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('Someone else saved this radar first')).toBeInTheDocument();
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(screen.queryByText('Someone else saved this radar first')).toBeNull());
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
  });

  it('still shows an ordinary failure as a toast', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 400, message: 'at most 300 entries' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('at most 300 entries')).toBeInTheDocument();
    expect(screen.queryByText('Someone else saved this radar first')).toBeNull();
  });

  it('writes the same columns the import accepts, with the formula guard', () => {
    const csv = entriesToCsv([
      { key: 'k1', title: '=cmd()', sector: 'Direct', ring: 'Leader', description: 'has, a comma and "quotes"', link: 'https://a.example.com' },
    ]);
    expect(csv.split('\n')[0]).toBe('\uFEFFkey,title,sector,ring,marker,description,node_id,link,sub_sector'); // r3 F3-098: a BOM for Excel
    expect(csv.split('\n')[1]).toBe('k1,\'=cmd(),Direct,Leader,,"has, a comma and ""quotes""",,https://a.example.com,');
  });
});

describe('F2-004: a failed Settings save reports its reason', () => {
  it('shows the error and keeps the typed fields', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'PATCH /api/radars/rivals': () => {
        throw { status: 409, message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    tabs(container).findTabLinkById('settings')!.click();
    fireEvent.change(await screen.findByDisplayValue('Rivals'), { target: { value: 'Competitors' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    // A stale lock reads the radar again and keeps the field (r3 F3-013).
    expect(await screen.findByText(/Someone else changed this radar in the meantime/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('Competitors')).toBeInTheDocument();
  });
});

describe('F2-003: the rows are read-only while a save runs', () => {
  it('makes every cell read-only and the row actions unavailable until the save lands', async () => {
    let version = 2;
    const { api, fetcher } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'PUT /api/radars/rivals/entries': () => {
        version = 3;
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    // The save is held open: fakeApi answers synchronously, so the pause has to be in the fetcher.
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await held;
      return real(...args);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    await waitFor(() => expect(screen.getByText(/the rows are read-only/)).toBeInTheDocument());
    expect(screen.getAllByLabelText(/^Title of /)[0]).toHaveAttribute('readonly');
    expect(screen.getAllByLabelText(/^Description of /)[0]).toHaveAttribute('readonly');
    expect(screen.getAllByLabelText(/^Link of /)[0]).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: 'Add entry' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByText('Version 3', { exact: false })).toBeInTheDocument());
    expect(screen.getAllByLabelText(/^Title of /)[0]).not.toHaveAttribute('readonly');
  });
});

describe('F2-004: "anyway" waits for the action before dropping the draft', () => {
  it('keeps the edits when the accept it allowed fails', async () => {
    const suggestion = {
      entry_key: 'k1',
      is_new: false,
      proposal: { ring: 'Challenger' },
      base: radarView().entries[0],
      reason: 'Lost share',
      evidence: [],
      created_at: '2026-10-01T10:00:00Z',
      created_by: 'module:curator',
    };
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'assist' } }),
      'GET /api/radars/rivals/suggestions': { items: [suggestion] },
      'POST /api/radars/rivals/suggestions/k1/accept': () => {
        throw { status: 409, message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    tabs(container).findTabLinkById('suggestions')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText('Your entry edits are not saved yet')).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Accept' }));
    expect(await screen.findByText(/Someone else saved this radar in the meantime/)).toBeInTheDocument();
    tabs(container).findTabLinkById('edit')!.click();
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText(/^1 unsaved change:/)).toBeInTheDocument();
  });

  it('keeps the draft, re-applied on the version the action made (r3 F3-011)', async () => {
    let version = 2;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'GET /api/radars/rivals/versions': { items: [{ version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' }] },
      'POST /api/radars/rivals/rollback': () => {
        version = 3;
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editOneRow(container);
    tabs(container).findTabLinkById('history')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
    expect(await screen.findByText('Your entry edits are not saved yet')).toBeInTheDocument();
    expect(screen.getByText(/Your 1 unsaved change in Edit entries is kept and re-applied on top of it/)).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(screen.getByText(/Restored version 1 as a new version/)).toBeInTheDocument());
    expect(await screen.findByText('Your 1 change was re-applied on version 3')).toBeInTheDocument();
    tabs(container).findTabLinkById('edit')!.click();
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
  });
});
