// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 frontend review: the save-conflict recovery (CR2-F02, CR2-F09) and "Load it" (CR2-F06). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry, RadarView } from '@/api/types';
import { ownChanges, rebaseEntries } from '@/radar/rebase';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage from './RadarPage';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
  </Routes>
);
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

afterEach(() => vi.restoreAllMocks());

const e = (key: string, title: string, over: Partial<RadarEntry> = {}): RadarEntry => ({
  key,
  title,
  sector: 'Direct',
  ring: 'Leader',
  description: '',
  ...over,
});

const BASE = [e('k1', 'Acme'), e('k2', 'Contoso'), e('k3', 'Globex'), e('k4', 'Initech')];
/** Editor B's save: Acme moved to Challenger, Contoso removed, Umbrella added. */
const THEIRS = [e('k1', 'Acme', { ring: 'Challenger' }), e('k3', 'Globex'), e('k4', 'Initech'), e('k5', 'Umbrella')];

function view(version: number, entries: RadarEntry[]): RadarView {
  const base = radarView();
  return { ...base, version, entries, radar: { ...base.radar, current_version: version, entry_count: entries.length } };
}

describe('rebaseEntries', () => {
  it('applies each side once: their edits, removals and additions, and mine', () => {
    const mine = [e('k1', 'Acme Corp'), e('k2', 'Contoso'), e('k3', 'Globex', { description: 'A note' }), e('k9', 'Hooli')];
    const r = rebaseEntries(BASE, mine, THEIRS);
    expect(r.rows).toEqual([
      e('k9', 'Hooli'), // added here: on top, where Add entry put it (r3 F3-037)
      e('k1', 'Acme Corp', { ring: 'Challenger' }), // different fields: both kept
      e('k3', 'Globex', { description: 'A note' }),
      // Initech was removed here and untouched there: it goes
      e('k5', 'Umbrella'),
    ]);
    expect(r.applied).toBe(4);
    expect(r.conflicts).toEqual([]);
  });

  it('reports every kind of clash and keeps a copy that loses nothing silently', () => {
    const base = [e('k1', 'Acme'), e('k2', 'Contoso'), e('k3', 'Globex'), e('k4', 'Initech', { marker: 'Gaining' })];
    const theirs = [
      e('k1', 'Acme', { ring: 'Challenger', description: 'theirs' }),
      e('k3', 'Globex', { ring: 'Challenger' }),
      e('k4', 'Initech', { marker: 'Gaining' }),
      e('k5', 'Umbrella'),
    ];
    const mine = [
      e('k1', 'Acme', { ring: 'Challenger', description: 'mine' }), // same ring as theirs, different description
      e('k2', 'Contoso', { description: 'edited' }), // they removed it
      // Globex removed here, changed there
      e('k4', 'Initech'), // marker cleared here
      e('k6', 'umbrella'), // the same title as their addition
    ];
    const r = rebaseEntries(base, mine, theirs);
    expect(r.rows).toEqual([
      e('k6', 'umbrella'),
      e('k1', 'Acme', { ring: 'Challenger', description: 'mine' }),
      e('k3', 'Globex', { ring: 'Challenger' }),
      e('k4', 'Initech'),
      e('k5', 'Umbrella'),
      e('k2', 'Contoso', { description: 'edited' }),
    ]);
    expect(r.rows[3]).not.toHaveProperty('marker');
    // The Globex removal did not land: it is held, not counted as re-applied (r3 F3-033).
    expect(r).toMatchObject({ applied: 4, held: 1 });
    expect(r.conflicts).toEqual([
      { key: 'k1', title: 'Acme', kind: 'both_edited', fields: [{ field: 'description', theirs: 'theirs' }] },
      { key: 'k3', title: 'Globex', kind: 'removed_but_changed', fields: [] },
      { key: 'k2', title: 'Contoso', kind: 'edited_but_removed', fields: [] },
      { key: 'k6', title: 'umbrella', kind: 'same_title', fields: [] },
    ]);
  });

  it('leaves an entry removed on both sides, or removed there and untouched here, removed', () => {
    const r = rebaseEntries(BASE, [e('k1', 'Acme'), e('k2', 'Contoso')], [e('k1', 'Acme')]);
    expect(r.rows).toEqual([e('k1', 'Acme')]);
    expect(r.applied).toBe(2); // Globex and Initech, removed here (and there)
  });

  it('lists the rows the user added or edited, and the ones they removed', () => {
    expect(ownChanges(BASE, [e('k1', 'Acme', { marker: '' }), e('k3', 'Globex', { ring: 'Challenger' }), e('k9', 'Hooli')])).toEqual({
      changed: [e('k3', 'Globex', { ring: 'Challenger' }), e('k9', 'Hooli')],
      removed: [e('k2', 'Contoso'), e('k4', 'Initech')],
    });
  });
});

/** Editor A on version 2: renames Acme, writes a Globex note and removes Initech. */
async function editAsA(container: HTMLElement) {
  await screen.findByRole('group', { name: 'Rivals radar' });
  createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
  fireEvent.change(await screen.findByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
  fireEvent.change(screen.getByLabelText('Description of Globex'), { target: { value: 'A note' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Initech' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove 1' }));
  expect(screen.getByText(/^3 unsaved changes:/)).toBeInTheDocument();
}

describe("CR2-F02: a save conflict re-applies the user's changes on the newer version", () => {
  it("keeps both editors' changes", async () => {
    let current = view(2, BASE);
    const puts: { entries: RadarEntry[]; expected_version: number }[] = [];
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': (body: unknown) => {
        const b = body as { entries: RadarEntry[]; expected_version: number };
        if (b.expected_version !== current.version) {
          throw { status: 409, code: 'conflict', message: 'someone else saved this radar; reload and retry' };
        }
        puts.push(b);
        current = view(4, b.entries);
        return current.radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editAsA(container);
    current = view(3, THEIRS); // editor B saves first
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('Someone else saved this radar first')).toBeInTheDocument();
    // CR2-F09: the stale page's version number is not named as the one that won.
    expect(visibleModal().getElement().textContent).not.toMatch(/version 2/);
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText('Your 3 changes were re-applied on version 3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
    expect(screen.getByDisplayValue('A note')).toBeInTheDocument();
    expect(screen.getByLabelText('Title of Umbrella')).toBeInTheDocument(); // B's addition
    expect(screen.queryByLabelText('Title of Contoso')).toBeNull(); // B's removal
    expect(screen.queryByLabelText('Title of Initech')).toBeNull(); // A's removal
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.expected_version).toBe(3);
    expect(puts[0]!.entries).toEqual([
      e('k1', 'Acme Corp', { ring: 'Challenger' }),
      e('k3', 'Globex', { description: 'A note' }),
      e('k5', 'Umbrella'),
    ]);
    // The saved version is the page now: no draft, no note left over.
    await waitFor(() => expect(screen.queryByText(/re-applied on version/)).toBeNull());
    expect(screen.getByText('Edit any cell or add an entry', { exact: false })).toBeInTheDocument();
  });

  it("lists the entries both sides changed, in the radar's words, until dismissed", async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editAsA(container);
    current = view(3, [e('k1', 'Acme Inc'), e('k3', 'Globex', { ring: 'Challenger', description: 'B note' }), e('k4', 'Initech', { ring: 'Challenger' })]);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText('3 entries changed on both sides: check them before saving.')).toBeInTheDocument();
    expect(screen.getByText('Globex: you and the newer version both changed it. Your value is kept for description (the newer version has "B note").')).toBeInTheDocument();
    expect(screen.getByText('Acme Corp: you and the newer version both changed it. Your value is kept for title (the newer version has "Acme Inc").')).toBeInTheDocument();
    expect(screen.getByText('Initech: you removed it, but the newer version changed it, so it is kept. Remove it again if it should go.')).toBeInTheDocument();
    // A further edit keeps the note; Dismiss removes it and keeps the rows.
    fireEvent.change(screen.getByLabelText('Title of Initech'), { target: { value: 'Initech 2' } });
    expect(screen.getByText(/3 entries changed on both sides: check/)).toBeInTheDocument();
    const alert = createWrapper(container).findAllAlerts().find((a) => /re-applied/.test(a.getElement().textContent ?? ''))!;
    alert.findDismissButton()!.click();
    await waitFor(() => expect(screen.queryByText(/re-applied on version/)).toBeNull());
    expect(screen.getByDisplayValue('Initech 2')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
  });

  it('names the other two clashes, and one re-applied change in the singular', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
    fireEvent.change(await screen.findByLabelText('Description of Contoso'), { target: { value: 'mine' } });
    current = view(3, [e('k1', 'Acme'), e('k3', 'Globex'), e('k4', 'Initech')]);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText('Your 1 change was re-applied on version 3')).toBeInTheDocument();
    expect(screen.getByText('1 entry changed on both sides: check it before saving.')).toBeInTheDocument();
    expect(screen.getByText('Contoso: the newer version removed it, and your edited copy is kept. Remove it again if it should go.')).toBeInTheDocument();
    // Added under a title the newer version also added:
    fireEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    fireEvent.change(screen.getByLabelText('Title of the new entry'), { target: { value: 'Umbrella' } });
    const selects = createWrapper(container).findAllSelects();
    selects[0]!.openDropdown();
    selects[0]!.selectOptionByValue('Direct', { expandToViewport: true });
    selects[1]!.openDropdown();
    selects[1]!.selectOptionByValue('Leader', { expandToViewport: true });
    current = view(4, [e('k1', 'Acme'), e('k3', 'Globex'), e('k4', 'Initech'), e('k5', 'Umbrella')]);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText(/Umbrella: you added it, and the radar already has an entry with this title/)).toBeInTheDocument();
  });

  it("downloads only the user's own rows and says what Merge does with them", async () => {
    const files: { name: string; body: string }[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const blobs: string[] = [];
    const RealBlob = Blob;
    vi.stubGlobal(
      'Blob',
      class extends RealBlob {
        constructor(parts: BlobPart[], opts?: BlobPropertyBag) {
          super(parts, opts);
          blobs.push(parts.join(''));
        }
      },
    );
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      files.push({ name: this.download, body: blobs.at(-1) ?? '' });
    });
    const { api } = fakeApi({
      'GET /api/radars/rivals': view(2, BASE),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editAsA(container);
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/The CSV holds only the 2 entries you added or edited/)).toBeInTheDocument();
    expect(screen.getByText(/remove them again by hand: Initech\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download my changes as CSV' }));
    vi.unstubAllGlobals();
    // The removals travel in a second file, since a Merge cannot express them (r3 F3-012).
    expect(files.map((f) => f.name)).toEqual(['rivals-my-changes.csv', 'rivals-my-removals.txt']);
    expect(files[1]!.body).toContain('Initech');
    const lines = files[0]!.body.trim().split('\n');
    expect(lines.slice(1).map((l) => l.split(',')[1])).toEqual(['Acme Corp', 'Globex']);
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
  });
});

describe('CR2-F06: "Load it" keeps unsaved entry edits', () => {
  it('re-applies the draft on the new version instead of dropping it', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({ 'GET /api/radars/rivals': () => current });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await editAsA(container);
    current = view(3, THEIRS);
    window.dispatchEvent(new Event('focus'));
    createWrapper(container).findTabs()!.findTabLinkById('view')!.click();
    expect(await screen.findByText('Version 3 has been saved since this page was opened')).toBeInTheDocument();
    expect(screen.getByText(/Loading the new one keeps your 3 unsaved changes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load it' }));
    expect(await screen.findByText('Your 3 changes were re-applied on version 3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review in Edit entries' }));
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
    expect(screen.getByLabelText('Title of Umbrella')).toBeInTheDocument();
    expect(screen.getByText(/^3 unsaved changes:/)).toBeInTheDocument();
  });

  it('says nothing about edits when there are none', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({ 'GET /api/radars/rivals': () => current });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    current = view(3, THEIRS);
    window.dispatchEvent(new Event('focus'));
    expect(await screen.findByText('Version 3 has been saved since this page was opened')).toBeInTheDocument();
    expect(screen.getByText('You are looking at version 2.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load it' }));
    await waitFor(() => expect(screen.queryByText(/has been saved since/)).toBeNull());
    expect(screen.queryByText(/re-applied/)).toBeNull();
  });
});
