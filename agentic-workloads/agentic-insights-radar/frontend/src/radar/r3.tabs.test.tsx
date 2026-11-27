// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about the radar tabs: import, suggestions and history. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry, Suggestion } from '@/api/types';
import RadarPage from '@/pages/RadarPage';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import { fileIdentities, mergeKept } from './importFile';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
  </Routes>
);
const e = (key: string, title: string): RadarEntry => ({ key, title, sector: 'Direct', ring: 'Leader', description: '' });
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

afterEach(() => vi.restoreAllMocks());

async function choose(container: HTMLElement, file: File) {
  await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
  fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [file] } });
}

describe('F3-087: Merge keeps what the API keeps', () => {
  it('lets a row take an entry key by title before deciding what is replaced', () => {
    const entries = [e('kA', 'Alpha'), e('kB', 'Beta'), e('kC', 'Gamma')];
    // One row titled like Alpha but carrying Beta's key: the API gives it Alpha's key, so Beta stays.
    const ids = fileIdentities('csv', 'key,title\nkB,Alpha\nkX,Delta\n')!;
    expect(mergeKept(entries, ids).map((x) => x.title)).toEqual(['Beta', 'Gamma']);
    // A renamed row with its own key still replaces its entry.
    expect(mergeKept(entries, fileIdentities('csv', 'key,title\nkC,Gamma renamed\n')!).map((x) => x.title)).toEqual(['Alpha', 'Beta']);
  });
});

describe('F3-088, F3-081, F3-086: the import help, the announced result and a file that cannot be read', () => {
  it('mentions the key column, announces the check, and says when the file cannot be read', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': { rows: 2, unmatched: [{ row: 1, field: 'ring', value: 'X' }], duplicate_rows: [], accepted: false },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    expect(await screen.findByText(/and key \(as exported: keep it, so a renamed row still updates its entry\)/)).toBeInTheDocument();
    expect(screen.getByText('Update entries with the same key or the same title, add new ones, keep the rest.')).toBeInTheDocument();
    await choose(container, new File(['title,sector,ring\nA,Direct,X\nB,Direct,Leader\n'], 'a.csv'));
    await waitFor(() => expect(container.querySelector('[aria-live="polite"]')!.textContent).toBe('2 rows read, 1 problem.'));
    const broken = new File(['x'], 'cloud.csv');
    vi.spyOn(broken, 'text').mockRejectedValue(new DOMException('not downloaded', 'NotReadableError'));
    await choose(container, broken);
    expect(await screen.findByText(/The file could not be read: it may have been moved/)).toBeInTheDocument();
    expect(screen.queryByText('Checking the file against this radar')).toBeNull();
    expect(container.querySelector('[aria-live="polite"]')!.textContent).toBe('The file could not be checked.');
  });
});

describe('F3-042: an import that lost a race reads the radar again', () => {
  it('reloads the view and says so, instead of a toast that repeats on every try', async () => {
    let version = 2;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        if ((b as { dry_run?: boolean }).dry_run) return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
        if ((b as { expected_version: number }).expected_version !== 3) {
          version = 3;
          throw { status: 409, code: 'conflict', message: 'someone else saved this radar; reload and retry' };
        }
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'b.csv'));
    await screen.findByText('Every row matches this radar.');
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(await screen.findByText(/Someone else saved this radar in the meantime/)).toBeInTheDocument();
    await screen.findByText(/^Version 3/);
    fireEvent.click(screen.getByRole('button', { name: 'Import' })); // the file and the mode are still chosen
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import') && !(c.body as { dry_run?: boolean }).dry_run).length).toBe(2));
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ expected_version: 3 });
  });
});

describe('F3-036: the shrinking-import dialog waits for the import', () => {
  it('keeps the dialog, with its spinner, until the confirmed import settles', async () => {
    let finish: () => void = () => undefined;
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries: Array.from({ length: 10 }, (_, i) => e(`k${i}`, `Entry ${i}`)) }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        if ((b as { dry_run?: boolean }).dry_run) return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
        throw { status: 400, code: 'validation_failed', message: 'too much', details: { code: 'shrinking_import', before: 10, after: 1, removed: 9 } };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'one.csv'));
    await screen.findByText('Every row matches this radar.');
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(await screen.findByText('Remove 9 entries?')).toBeInTheDocument();
    vi.spyOn(api, 'importEntries').mockImplementation(() => new Promise((r) => (finish = () => r(radarView().radar))));
    fireEvent.click(screen.getByRole('button', { name: 'Import and remove 9 entries' }));
    expect(screen.getByText('Remove 9 entries?')).toBeInTheDocument();
    visibleModal().findDismissButton().click(); // not while it runs
    expect(screen.getByText('Remove 9 entries?')).toBeInTheDocument();
    await act(async () => finish());
    await waitFor(() => expect(screen.queryByText('Remove 9 entries?')).toBeNull());
  });
});

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

describe('F3-062, F3-063: suggestions that someone else handled, and which button is busy', () => {
  it('reloads the list on a 404, and spins Discard (not Accept) while discarding', async () => {
    let open = true;
    let finish: () => void = () => undefined;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': () => ({ items: open ? [suggestion] : [] }),
      'POST /api/radars/rivals/suggestions/k1/accept': () => {
        open = false;
        throw { status: 404, code: 'not_found', message: 'no such suggestion' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    vi.spyOn(api, 'discardSuggestion').mockImplementation(() => new Promise((r) => (finish = () => r({}))));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Discard' }));
    const table = within(createWrapper(container).findTable()!.getElement());
    const button = (text: string) => createWrapper(createWrapper(container).findTable()!.getElement()).findAllButtons().find((x) => x.getElement().textContent === text)!;
    await waitFor(() => expect(button('Discard').findLoadingIndicator()).not.toBeNull());
    expect(button('Accept').findLoadingIndicator()).toBeNull(); // it used to spin Accept, which read as "accepting"
    expect(button('Accept').getElement()).toBeDisabled();
    await act(async () => finish());
    fireEvent.click(await table.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText(/That suggestion was already reviewed: someone else accepted or discarded it/)).toBeInTheDocument();
    expect(await screen.findByText('No open suggestions.')).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/suggestions')).length).toBeGreaterThanOrEqual(2);
  });
});

describe('F3-018: a failed History load is not an empty history', () => {
  it('shows the error with Retry', async () => {
    let fail = true;
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/versions': () => {
        if (fail) throw { status: 500, message: 'versions are down' };
        return { items: [] };
      },
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=history' }));
    expect(await screen.findByText('The versions could not be loaded: versions are down')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No versions yet.')).toBeInTheDocument();
  });
});

describe('F3-042, F3-062: other failures are still said as they are', () => {
  it('reports a failed accept and a failed restore with their own message', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [suggestion] },
      'POST /api/radars/rivals/suggestions/k1/accept': () => {
        throw { status: 500, message: 'accept broke' };
      },
      'GET /api/radars/rivals/versions': { items: [{ version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' }] },
      'POST /api/radars/rivals/rollback': () => {
        throw { status: 500, message: 'restore broke' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText('accept broke')).toBeInTheDocument();
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
    expect(await screen.findByText('restore broke')).toBeInTheDocument();
  });
});

describe('F3-027: a proposal shows every field it changes', () => {
  it('lists old and new values in the radar words, and the old title on a rename', async () => {
    const change: Suggestion = { ...suggestion, proposal: { title: 'Acme Inc', description: 'Now the market leader', link: 'https://acme.example.org', sub_sector: 'Suites' } };
    const added: Suggestion = { ...suggestion, entry_key: 'k9', is_new: true, base: null, proposal: { title: 'Umbrella', sector: 'Direct', ring: 'Leader' } };
    const orphan: Suggestion = { ...suggestion, entry_key: 'k8', base: null, proposal: { title: 'Orphan', ring: 'Leader' } };
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [change, added, orphan] } });
    render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByText('Change: Acme')).toBeInTheDocument();
    expect(screen.getByText('Title: Acme \u2192 Acme Inc')).toBeInTheDocument();
    expect(screen.getByText('Description: Market leader \u2192 Now the market leader')).toBeInTheDocument();
    expect(screen.getByText('Link: https://acme.example.com \u2192 https://acme.example.org')).toBeInTheDocument();
    expect(screen.getByText('Sub-category: - \u2192 Suites')).toBeInTheDocument();
    expect(screen.getByText('New: Umbrella')).toBeInTheDocument();
    expect(screen.getAllByText('Position: - \u2192 Leader')).toHaveLength(2);
    expect(screen.queryByText(/Title: - \u2192 Umbrella/)).toBeNull();
    expect(screen.getByText('Change: Orphan')).toBeInTheDocument();
  });
});

describe('CTR HC-136: a stored HTML description is shown as text in a proposal', () => {
  it('drops the markup and keeps the words', async () => {
    const html: Suggestion = { ...suggestion, proposal: { title: 'Acme', description: '<p>Now <b>first</b> &amp; best</p>' } };
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [html] } });
    render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByText('Description: Market leader \u2192 Now first & best')).toBeInTheDocument();
    expect(screen.queryByText(/<p>/)).toBeNull();
  });
});

describe('F3-029, F3-092, F3-093, F3-112: the schema rename dialog', () => {
  const setList = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const select = (start: string) => createWrapper(document.body).findAllSelects().find((x) => x.findTrigger().getElement().textContent?.startsWith(start))!;

  it('moves a removed value into a kept one, says when two go into one, counts entries and marks guesses', async () => {
    const entries = [
      { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' },
      { key: 'k2', title: 'Beta', sector: 'Direct', ring: 'Challenger', description: '' },
      { key: 'k3', title: 'Gamma', sector: 'Direct', ring: 'Challenger', description: '' },
    ];
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }), 'PUT /api/radars/rivals/schema': radarView().radar });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Position')).toBeInTheDocument());
    // Merging two rings: Challenger goes into Leader. The marker goes too, carried by no entry.
    setList('Position', 'Leader');
    setList('Momentum', '');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    expect(await screen.findByText('"Challenger" (2 entries): Remove it, and its entries with it (guessed: check it)')).toBeInTheDocument();
    expect(screen.getByText(/^"Gaining" \(0 entries\)/)).toBeInTheDocument();
    const challenger = select('"Challenger"');
    challenger.openDropdown();
    challenger.selectOptionByValue('rename:Leader', { expandToViewport: true });
    expect(await screen.findByText('"Challenger" (2 entries): Move its entries to "Leader"')).toBeInTheDocument();
    expect(screen.getByText('Every entry still matches the new schema.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/schema'))?.body).toMatchObject({ renames: { ring: { Challenger: 'Leader' } }, drop_unmatched: false }));
  });

  it('says a rename target already taken merges the two', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Position')).toBeInTheDocument());
    setList('Position', 'Pilot');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    expect(await screen.findByText('"Leader" (1 entry): Rename it to "Pilot" (guessed: check it)')).toBeInTheDocument();
    const challenger = select('"Challenger"');
    challenger.openDropdown();
    const offered = challenger.findDropdown({ expandToViewport: true }).findOptions().map((o) => o.getElement().textContent);
    expect(offered).toContain('Rename it to "Pilot" (merges with "Leader")');
  });
});

describe('F3-031: a PNG that fits the browser canvas', () => {
  it('chooses the largest scale under the canvas limit', async () => {
    const { pngScale } = await import('./export');
    expect(pngScale(840, 840)).toBe(4);
    expect(pngScale(1624, 840)).toBe(3);
    expect(pngScale(100_000, 100_000)).toBe(1);
    expect(pngScale(0, 0)).toBe(4);
  });

  it('says when the picture was drawn below print quality', async () => {
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    class LoadedImage {
      onload: (() => void) | null = null;
      set src(_: string) {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal('Image', LoadedImage);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '' } as never);
    const sizes: number[][] = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (this: HTMLCanvasElement, cb) {
      sizes.push([this.width, this.height]);
      cb(new Blob(['png'], { type: 'image/png' }));
    });
    // 60 entries: a two-column legend, as wide as the finding's 1,624 units.
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries: Array.from({ length: 60 }, (_, i) => e(`k${i}`, `Entry ${i}`)) }) });
    render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    fireEvent.click(await screen.findByRole('button', { name: 'PNG' }));
    expect(await screen.findByText(/The picture was drawn at 3x instead of 4x/)).toBeInTheDocument();
    const [w = 0, h = 0] = sizes[0] ?? [];
    expect(w * h).toBeLessThanOrEqual(4096 * 4096);
    vi.unstubAllGlobals();
  });
});
