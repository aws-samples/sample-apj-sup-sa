// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-028, F2-030, F2-031, F2-032, F2-038, F2-070: the radar view, the editor and the import check. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry } from '@/api/types';
import RadarPage from '@/pages/RadarPage';
import { radarView, SCHEMA } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import EntryEditor, { ENTRIES_MAX, entryProblems, TITLE_MAX, validateEntries } from './EntryEditor';

afterEach(() => vi.restoreAllMocks());

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);

function Editor({ initial }: { initial: RadarEntry[] }) {
  const [rows, setRows] = React.useState(initial);
  return <EntryEditor schema={SCHEMA} entries={initial} rows={rows} onRowsChange={setRows} saving={false} onSave={vi.fn()} />;
}

const row = (i: number, over: Partial<RadarEntry> = {}): RadarEntry => ({
  key: `k${i}`,
  title: `Entry ${i}`,
  sector: 'Direct',
  ring: 'Leader',
  description: '',
  ...over,
});

describe('F2-031: a problem names its entry and jumps to it', () => {
  it('names the entry rather than a position in the whole draft', () => {
    const rows = [row(1), row(2, { title: 'Contoso', ring: 'Nowhere' })];
    expect(validateEntries(SCHEMA, rows)).toEqual(['Contoso: choose a position']);
    expect(entryProblems(SCHEMA, rows)[0]).toMatchObject({ key: 'k2', title: 'Contoso', field: 'ring' });
  });

  it('filters the table to the row the message names, whatever page it was on', async () => {
    const rows = [...Array.from({ length: 40 }, (_, i) => row(i)), row(99, { title: 'Contoso', ring: 'Nowhere' })];
    const { container } = render(<Editor initial={rows} />);
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(25); // Contoso is on page 2
    fireEvent.click(screen.getByRole('button', { name: 'Contoso: choose a position' }));
    await waitFor(() => expect(createWrapper(container).findTable()!.findRows()).toHaveLength(1));
    // The filter box and the row's title cell both hold it now; the row is on screen either way.
    expect(screen.getAllByDisplayValue('Contoso').length).toBe(2);
  });
});

describe('F2-032: Remove says how many, and only what is on screen', () => {
  it('counts the selection and clears it when the filter or the page changes', async () => {
    const rows = Array.from({ length: 30 }, (_, i) => row(i, { title: i === 0 ? 'Acme' : `Entry ${i}` }));
    const { container } = render(<Editor initial={rows} />);
    const table = createWrapper(container).findTable()!;
    table.findRowSelectionArea(1)!.click();
    table.findRowSelectionArea(2)!.click();
    expect(await screen.findByRole('button', { name: 'Remove 2' })).toBeInTheDocument();
    // Filtering used to keep the hidden rows selected, and Remove took them too.
    createWrapper(container).findTextFilter()!.findInput().setInputValue('acme');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled());
    createWrapper(container).findTextFilter()!.findInput().setInputValue('');
    await waitFor(() => expect(createWrapper(container).findTable()!.findRows()).toHaveLength(25));
    createWrapper(container).findTable()!.findRowSelectionArea(1)!.click();
    expect(await screen.findByRole('button', { name: 'Remove 1' })).toBeInTheDocument();
    createWrapper(container).findPagination()!.findNextPageButton().click();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled());
  });
});

describe('F2-070: the editor checks the limits the API enforces', () => {
  it('reports a title over the limit and a draft over the entry cap', () => {
    const long = 'x'.repeat(TITLE_MAX + 1);
    expect(validateEntries(SCHEMA, [row(1, { title: long })])).toEqual([
      `${long}: a title must be ${TITLE_MAX} characters or fewer (this one is ${TITLE_MAX + 1})`,
    ]);
    const tooMany = Array.from({ length: ENTRIES_MAX + 2 }, (_, i) => row(i));
    expect(validateEntries(SCHEMA, tooMany)[0]).toBe(
      `A radar holds at most ${ENTRIES_MAX} entries; this draft has ${ENTRIES_MAX + 2}. Remove 2 before saving.`,
    );
  });

  it('shows the cap without pretending it belongs to a row', () => {
    render(<Editor initial={Array.from({ length: ENTRIES_MAX + 1 }, (_, i) => row(i))} />);
    expect(screen.getByText(/A radar holds at most 300 entries/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save version' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('F2-028 and F2-030: the legend scrolls with the page and comes first when narrow', () => {
  it('lays the chart and the legend out in one grid, in that order', async () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1400); // a wide screen: chart first
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    const layout = container.querySelector('.air-radar-layout') as HTMLElement;
    expect(layout).not.toBeNull();
    expect([...layout.children].map((c) => c.className)).toEqual(['air-radar-chart', 'air-radar-legend']);
  });
});

describe('F2-038: choosing a file shows that it is being checked', () => {
  it('says so while the dry run runs, and says why it failed inline', async () => {
    let fail = false;
    const { api, fetcher } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': () => {
        if (fail) throw { status: 400, message: 'the file has a broken quote on line 3' };
        return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    const upload = await waitFor(() => createWrapper(container).findFileUpload()!);
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await held;
      return real(...args);
    });
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['title\nBeta\n'], 'rows.csv')] } });
    expect(await screen.findByText('Checking the file against this radar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
    release();
    await waitFor(() => expect(screen.queryByText('Checking the file against this radar')).toBeNull());
    // A file the API cannot read says so where the report would be, not just as a toast.
    fail = true;
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['"'], 'broken.csv')] } });
    expect(await screen.findByText('The file could not be checked')).toBeInTheDocument();
    expect(screen.getAllByText('the file has a broken quote on line 3').length).toBeGreaterThan(0);
  });
});

describe('F2-071 and F2-065: naming the cells, and skipping the blips', () => {
  it('names every cell by its row', () => {
    render(<Editor initial={[row(1, { title: 'Acme' }), row(2, { title: '' })]} />);
    expect(screen.getByLabelText('Title of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Description of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Link of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Sub-category of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Category of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Position of Acme')).toBeInTheDocument();
    expect(screen.getByLabelText('Momentum of Acme')).toBeInTheDocument();
    // A row with no title yet still has a name of its own.
    expect(screen.getByLabelText('Title of the new entry')).toBeInTheDocument();
  });

  it('offers one link past the blips, and none when there is only one', async () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1400); // a wide screen: the chart comes first
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: [row(1), row(2)] }),
      'GET /api/radars/rivals/suggestions': { items: [] },
    });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    const skip = await screen.findByRole('button', { name: 'Skip the chart and go to the legend' });
    fireEvent.click(skip);
    expect(document.activeElement).toBe(document.getElementById('air-legend-start'));
    const single = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(routes, { api: single.api, path: '/radars/rivals' }));
    await waitFor(() => expect(screen.getAllByRole('region', { name: 'Legend entries' })).toHaveLength(2));
    expect(screen.getAllByRole('button', { name: 'Skip the chart and go to the legend' })).toHaveLength(1);
  });
});
