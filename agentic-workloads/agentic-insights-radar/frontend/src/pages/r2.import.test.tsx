// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-001: the Merge preview describes the file, not the merged list. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { radarView, SCHEMA } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage from './RadarPage';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);

afterEach(() => vi.restoreAllMocks());

/** The radar's 40 entries, all matching the schema. */
const many = Array.from({ length: 40 }, (_, i) => ({
  key: `k${i}`,
  title: `Entry ${i}`,
  sector: 'Direct',
  ring: 'Leader',
  description: '',
}));

async function openImport(api: ReturnType<typeof fakeApi>['api'], file: File) {
  const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
  await screen.findByRole('group', { name: 'Rivals radar' });
  createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
  const upload = await waitFor(() => createWrapper(container).findFileUpload()!);
  fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [file] } });
  return { container };
}

describe('F2-001: the Merge preview is about the file', () => {
  it('counts the file\'s rows and numbers its problems from the file, not from the merged list', async () => {
    const seen: { mode?: string }[] = [];
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many, radar: { ...radarView().radar, entry_count: 40 } }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        seen.push(b as { mode?: string });
        // The API's own dry run in replace mode: 2 rows, a problem on file row 1.
        return { rows: 2, unmatched: [{ row: 1, field: 'ring', value: 'Leadr', suggestion: 'Leader' }], duplicate_rows: [], accepted: false };
      },
    });
    await openImport(api, new File(['title,sector,ring\nBeta,Direct,Leadr\nGamma,Direct,Leader\n'], 'rows.csv'));
    // Before the fix a Merge dry run answered "42 rows read" and "Row 41".
    expect(await screen.findByText('2 rows read from the file')).toBeInTheDocument();
    // F2-044: the radar's own heading ("Position"), not the API's field name.
    expect(screen.getByText(/^Row 1: "Leadr" is not a position/)).toBeInTheDocument();
    expect(seen.map((b) => b.mode)).toEqual(['replace']);
  });

  it('says what a Merge does to the entries that are not in the file', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: many }),
      'POST /api/radars/rivals/import': { rows: 2, unmatched: [], duplicate_rows: [], accepted: true },
    });
    const { container } = await openImport(api, new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'rows.csv'));
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument();
    expect(screen.getByText(/Rows with the key or the title of an entry already on this radar update that entry/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).not.toHaveAttribute('aria-disabled', 'true');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    expect(await screen.findByText(/entries the file does not mention are removed/)).toBeInTheDocument();
  });

  it('reports entries already on the radar separately, and does not call them problems in the file', async () => {
    // A schema change left three entries with a ring the schema no longer has.
    const entries = [
      { key: 'a', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' },
      { key: 'b', title: 'Stale One', sector: 'Direct', ring: 'Assess', description: '' },
      { key: 'c', title: 'Stale Two', sector: 'Nowhere', ring: 'Leader', description: '' },
    ];
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries, schema: SCHEMA }),
      'POST /api/radars/rivals/import': { rows: 1, unmatched: [], duplicate_rows: [], accepted: true },
    });
    const { container } = await openImport(api, new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'rows.csv'));
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument();
    expect(screen.queryByText(/problems to fix in the file/)).toBeNull();
    expect(screen.getByText('2 entries already on this radar do not match it')).toBeInTheDocument();
    expect(screen.getByText(/Stale One, Stale Two/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
    // Replace is a way out: the stale entries go with the rest of the old list.
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    await waitFor(() => expect(screen.queryByText('2 entries already on this radar do not match it')).toBeNull());
    expect(screen.getByRole('button', { name: 'Import' })).not.toHaveAttribute('aria-disabled', 'true');
    // So is Edit entries, which the alert links to.
    createWrapper(container).findRadioGroup()!.findInputByValue('merge')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit entries' }));
    expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement()).toHaveTextContent('Edit entries');
  });

  it('names one stale entry in the singular', async () => {
    const entries = [{ key: 'b', title: 'Stale One', sector: 'Direct', ring: 'Assess', description: '' }];
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries }),
      'POST /api/radars/rivals/import': { rows: 1, unmatched: [], duplicate_rows: [], accepted: true },
    });
    await openImport(api, new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'rows.csv'));
    expect(await screen.findByText('1 entry already on this radar does not match it')).toBeInTheDocument();
  });

  it('merging an empty file is refused instead of being confirmed as a wipe', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': { rows: 0, unmatched: [], duplicate_rows: [], accepted: true, empty: true },
    });
    await openImport(api, new File(['title,sector,ring\n'], 'empty.csv'));
    expect(await screen.findByText(/Merging it would leave the radar exactly as it is/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByText('Remove every entry?')).toBeNull();
    expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(1);
  });

  it('does not offer Edit entries to someone who cannot edit', async () => {
    const entries = [{ key: 'b', title: 'Stale One', sector: 'Direct', ring: 'Assess', description: '' }];
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries, can_edit: false, can_admin: false }) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    expect(await screen.findByRole('button', { name: 'SVG' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit entries' })).toBeNull();
  });
});
