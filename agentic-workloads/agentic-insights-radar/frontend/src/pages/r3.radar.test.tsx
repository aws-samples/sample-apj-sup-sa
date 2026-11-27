// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about a radar page: saves, reloads, rebases and the entry editor. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Me, RadarEntry, RadarView } from '@/api/types';
import { TestSessionProvider } from '@/auth/session';
import { NotificationProvider } from '@/components/Notifications';
import { entryProblems, storedLength } from '@/radar/EntryEditor';
import { fold } from '@/radar/layout';
import { ADMIN, fakeApi, sessionValue, TestRouter, wrap } from '@/test/session';
import { radarView, SCHEMA } from '@/test/data';

import RadarPage, { diffSummary, duplicateProblems, STALE_CHECK_MS } from './RadarPage';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const tabs = (container: HTMLElement) => createWrapper(container).findTabs()!;
const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
const e = (key: string, title: string, over: Partial<RadarEntry> = {}): RadarEntry => ({ key, title, sector: 'Direct', ring: 'Leader', description: '', ...over });
const BASE = [e('k1', 'Acme'), e('k2', 'Contoso'), e('k3', 'Globex')];
function view(version: number, entries: RadarEntry[], over: Partial<RadarView> = {}): RadarView {
  const base = radarView();
  return { ...base, version, entries, radar: { ...base.radar, current_version: version, entry_count: entries.length }, ...over };
}
const conflict = () => {
  throw { status: 409, code: 'conflict', message: 'someone else saved this radar; reload and retry' };
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function openEdit(container: HTMLElement) {
  await screen.findByRole('group', { name: 'Rivals radar' });
  tabs(container).findTabLinkById('edit')!.click();
  return screen.findAllByLabelText(/^Title of /);
}

describe('F3-006: names fold the way the API folds them', () => {
  it('collapses spaces, drops invisible characters, maps quotes as the API does and folds case fully', () => {
    expect(fold('Acme  Corp')).toBe(fold('Acme Corp'));
    expect(fold('Acme\u200b')).toBe(fold('Acme'));
    expect(fold('STRASSE')).toBe(fold('Stra\u00dfe'));
    expect(fold('\u201cQuoted\u201d')).toBe('"quoted"');
    expect(fold('Don\u2019t')).toBe("don't");
    expect(entryProblems(SCHEMA, [e('a', 'Acme Corp'), e('b', 'Acme  Corp')]).map((p) => p.message)).toEqual(['Acme  Corp: "Acme  Corp" appears twice']);
  });
});

describe('F3-001: a duplicate-title 409 is not "someone else saved first"', () => {
  it('names the rows the API refused in the editor, and offers no reload', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': view(2, BASE),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'entry titles must be unique', details: { code: 'duplicate_title', rows: [2] } };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Contoso'), { target: { value: 'Contoso 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/Contoso 2: another entry already has this title once letter case/)).toBeInTheDocument();
    expect(screen.queryByText('Someone else saved this radar first')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save version' })).toHaveAttribute('aria-disabled', 'true');
    // Renaming the row clears the API's problem: it was about the rows that were sent.
    fireEvent.change(screen.getByLabelText('Title of Contoso 2'), { target: { value: 'Contoso Two' } });
    expect(screen.queryByText(/another entry already has this title/)).toBeNull();
  });

  it('reads only the row numbers it can place', () => {
    expect(duplicateProblems(BASE, [2, 9, 'x'])).toHaveLength(1);
    expect(duplicateProblems(BASE, 'rows')).toEqual([]);
  });

  it('reports any other 409 reason as it is', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': view(2, BASE),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, code: 'conflict', message: 'locked for review', details: { code: 'locked' } };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Contoso'), { target: { value: 'Contoso 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('locked for review')).toBeInTheDocument();
    expect(screen.queryByText('Someone else saved this radar first')).toBeNull();
  });
});

describe('F3-002: a failed reload keeps the page and the draft', () => {
  it('shows a banner with Retry instead of replacing the page after "Load the newer version"', async () => {
    let down = false;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => {
        if (down) throw { status: 503, code: 'throttled', message: 'the service is busy; retry shortly' };
        return view(3, BASE);
      },
      'PUT /api/radars/rivals/entries': conflict,
    });
    vi.spyOn(api as unknown as { wait: (ms: number) => Promise<void> }, 'wait').mockResolvedValue(undefined);
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Description of Acme'), { target: { value: 'mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    down = true;
    fireEvent.click(await screen.findByRole('button', { name: 'Load the newer version' }));
    expect(await screen.findByText('The radar could not be read again')).toBeInTheDocument();
    expect(screen.getByDisplayValue('mine')).toBeInTheDocument();
    expect(screen.queryByText('This radar could not be loaded')).toBeNull();
    down = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('The radar could not be read again')).toBeNull());
    expect(screen.getByDisplayValue('mine')).toBeInTheDocument();
  });
});

describe('F3-007: the newer-version banner is above the tabs and checked while drafting', () => {
  it('tells an editor in Edit entries, without a focus change', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let current = view(2, BASE);
    const { api } = fakeApi({ 'GET /api/radars/rivals': () => current });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Description of Acme'), { target: { value: 'mine' } });
    current = view(5, BASE);
    await act(async () => void vi.advanceTimersByTime(STALE_CHECK_MS + 10));
    expect(await screen.findByText('Version 5 has been saved since this page was opened')).toBeInTheDocument();
    expect(tabs(container).findActiveTab()!.getElement().textContent).toMatch(/Edit entries/);
    // Hidden tabs are not asked.
    current = view(6, BASE);
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await act(async () => void vi.advanceTimersByTime(STALE_CHECK_MS + 10));
    hidden.mockRestore();
    expect(screen.getByText('Version 5 has been saved since this page was opened')).toBeInTheDocument();
  });

  it('says nothing when the check itself fails', async () => {
    let down = false;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => {
        if (down) throw { status: 500, message: 'down' };
        return view(2, BASE);
      },
    });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    down = true;
    await act(async () => void window.dispatchEvent(new Event('focus')));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/has been saved since this page was opened/)).toBeNull();
    expect(screen.queryByText(/could not be/)).toBeNull();
  });

  it('asks once for a tab switch that fires both focus and visibilitychange (F3-106)', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': view(2, BASE) });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    const before = calls.length;
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(calls.length - before).toBe(1);
  });
});

describe('F3-008: a save keeps the rows until the new version has loaded', () => {
  it('keeps the saved text, read-only, instead of showing the old version for a while', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': (body: unknown) => {
        current = view(3, (body as { entries: RadarEntry[] }).entries);
        return current.radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    let release: () => void = () => undefined;
    const real = api.radar;
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    vi.spyOn(api, 'radar').mockImplementation((slug) => new Promise((resolve) => (release = () => resolve(real(slug)))));
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('Saved as a new version.')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Acme Corp')).toHaveAttribute('readonly');
    expect(screen.queryByDisplayValue('Acme')).toBeNull();
    await act(async () => release());
    await waitFor(() => expect(screen.getByDisplayValue('Acme Corp')).not.toHaveAttribute('readonly'));
    expect(screen.queryByText(/re-applied/)).toBeNull();
    expect(screen.getByText('Edit any cell or add an entry', { exact: false })).toBeInTheDocument();
  });
});

describe('F3-014: a save that got no answer is checked before anything is said', () => {
  const gateway = () => new Response('<html>504</html>', { status: 504 });
  it('says it was saved when the new version holds the rows', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': (body: unknown) => {
        current = view(3, (body as { entries: RadarEntry[] }).entries);
        return gateway();
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/The answer was lost on the way, but the new version is there/)).toBeInTheDocument();
    expect(screen.queryByText('Someone else saved this radar first')).toBeNull();
  });

  it('says nothing was saved when the version did not move, and "may be your save" when it moved differently', async () => {
    let current = view(2, BASE);
    let moved = false;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': () => {
        if (moved) current = view(3, [e('k9', 'Other')]);
        return gateway();
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/nothing was saved: your edits are still here/)).toBeInTheDocument();
    moved = true;
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/the radar is now at version 3. It may be your save/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
  });

  it('reports the uncertain failure when the radar cannot be read either', async () => {
    let down = false;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => (down ? gateway() : view(2, BASE)),
      'PUT /api/radars/rivals/entries': () => {
        down = true;
        return gateway();
      },
    });
    vi.spyOn(api as unknown as { wait: (ms: number) => Promise<void> }, 'wait').mockResolvedValue(undefined);
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText(/may or may not have been saved/)).toBeInTheDocument();
  });
});

describe('F3-009, F3-010, F3-038, F3-110, F3-111, F3-033: working through a rebase', () => {
  it('marks the rows, filters to them, jumps to one, takes their value, and words Discard by version', async () => {
    const base = [e('k1', 'Acme', { sub_sector: 'Suites' }), e('k2', 'Contoso'), e('k3', 'Globex', { description: 'old' }), e('k4', 'Initech')];
    let current = view(2, base);
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => current,
      'PUT /api/radars/rivals/entries': () => {
        current = view(3, [e('k1', 'Acme', { description: 'theirs' }), e('k2', 'Contoso', { ring: 'Challenger' }), e('k3', 'Globex'), e('k4', 'Initech')]);
        throw { status: 409, code: 'conflict', message: 'someone else saved this radar', details: { expected: 2, actual: 3 } };
      },
      'GET /api/radars/rivals/diff': { from: 2, to: 3, changes: { added: [], removed: [], moved: [{ key: 'k2', title: 'Contoso', from: 'Leader', to: 'Challenger' }], recategorised: [], marker_changed: [], edited: [{ key: 'k1', title: 'Acme', fields: ['description'] }] } },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Description of Acme'), { target: { value: 'mine' } });
    fireEvent.change(screen.getByLabelText('Sub-category of Acme'), { target: { value: 'Mine' } });
    fireEvent.change(screen.getByLabelText('Description of Globex'), { target: { value: 'mine too' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Contoso' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    // F3-111: what the other save changed, before the choice.
    expect(await screen.findByText('What changed since your version 2. Version 3: 2 changed.')).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Load the newer version' }));
    // F3-033: the overruled removal is held back, not counted as re-applied.
    expect(await screen.findByText('Your 2 changes were re-applied on version 3, and 1 removal held back')).toBeInTheDocument();
    // F3-038: the note takes focus, so a screen reader hears it.
    const note = createWrapper(container).findAllAlerts().find((a) => /re-applied/.test(a.getElement().textContent ?? ''))!;
    await waitFor(() => expect(note.getElement().contains(document.activeElement)).toBe(true));
    // F3-009: the rows are marked, and can be shown on their own.
    expect(screen.getByText('3 entries changed on both sides are marked below.')).toBeInTheDocument();
    expect(screen.getAllByText('Changed on both sides: check it')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Show only those' }));
    expect(screen.getAllByLabelText(/^Title of /)).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Show every entry' }));
    expect(screen.getAllByLabelText(/^Title of /)).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: /^Contoso: you removed it/ }));
    await waitFor(() => expect(screen.getAllByLabelText(/^Title of /)).toHaveLength(1));
    expect(document.activeElement).toBe(screen.getByLabelText('Title of Contoso'));
    // F3-110: Discard names the version it goes back to.
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByText(/the entries go back to version 3: the newer version, with the other editor's changes/)).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Keep editing' }));
    // F3-010: the other editor's values, without retyping them (a value they cleared is cleared).
    for (const b of screen.getAllByRole('button', { name: "Use the newer version's values" })) fireEvent.click(b);
    expect(screen.queryByText(/you and the newer version both changed it/)).toBeNull();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find entries by title' }), { target: { value: '' } });
    expect(await screen.findByDisplayValue('theirs')).toBeInTheDocument();
    expect(screen.getByLabelText('Sub-category of Acme')).toHaveValue('');
    expect(screen.getByLabelText('Description of Globex')).toHaveValue('');
    expect(screen.queryByDisplayValue('mine too')).toBeNull();
    // F3-034: nothing of the user's is left (the removal was held back), so the draft and its note go.
    await waitFor(() => expect(screen.queryByText(/re-applied on version/)).toBeNull());
    expect(screen.getByText('Edit any cell or add an entry', { exact: false })).toBeInTheDocument();
  });

  it('summarises a diff with nothing in it', () => {
    expect(diffSummary(4, { added: [e('a', 'A')], removed: [e('b', 'B')], moved: [], recategorised: [], marker_changed: [], edited: [] })).toBe('Version 4: 1 added, 1 removed.');
    expect(diffSummary(4, { added: [], removed: [], moved: [], recategorised: [], marker_changed: [], edited: [] })).toBe('Version 4: no entry changes (settings or schema only).');
  });
});

describe('F3-035: a draft with no net change is no draft', () => {
  it('announces no rebase for 0 changes after a newer version loads', async () => {
    let current = view(2, BASE);
    const { api } = fakeApi({ 'GET /api/radars/rivals': () => current });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acmex' } });
    fireEvent.change(screen.getByLabelText('Title of Acmex'), { target: { value: 'Acme' } });
    current = view(3, [...BASE, e('k4', 'Initech')]);
    await act(async () => void window.dispatchEvent(new Event('focus')));
    fireEvent.click(await screen.findByRole('button', { name: 'Load it' }));
    expect(await screen.findByLabelText('Title of Initech')).toBeInTheDocument();
    expect(screen.queryByText(/re-applied on version/)).toBeNull();
  });
});

describe('F3-066, F3-096: an unsaved draft is visible from other tabs', () => {
  it('marks the tab labels and says exports leave the draft out', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': view(2, BASE) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    expect(tabs(container).findTabLinkById('edit')!.getElement().textContent).toBe('Edit entries (1 unsaved)');
    tabs(container).findTabLinkById('io')!.click();
    expect(await screen.findByText(/Every export is of the saved version: your 1 unsaved change in Edit entries is not in it/)).toBeInTheDocument();
    tabs(container).findTabLinkById('edit')!.click();
    fireEvent.change(await screen.findByLabelText('Title of Globex'), { target: { value: 'Globex Corp' } });
    tabs(container).findTabLinkById('io')!.click();
    expect(await screen.findByText(/your 2 unsaved changes in Edit entries are not in it/)).toBeInTheDocument();
    tabs(container).findTabLinkById('settings')!.click();
    fireEvent.change(await screen.findByDisplayValue('Rivals'), { target: { value: 'Rivals 2' } });
    expect(tabs(container).findTabLinkById('settings')!.getElement().textContent).toBe('Settings (unsaved)');
  });

  it('words the export card for viewers (F3-109)', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': view(2, BASE, { can_edit: false, can_admin: false }) });
    render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    expect(await screen.findByText('The chart as a picture, or the entries as a file you can open in a spreadsheet.')).toBeInTheDocument();
    expect(screen.queryByText(/edit and import again/)).toBeNull();
  });
});

describe('F3-067: Esc that a field used does not close the entry panel', () => {
  it('keeps the panel for an Esc in the legend filter, and closes it for one elsewhere', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': view(2, BASE) });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    const legend = within(await screen.findByRole('region', { name: 'Legend entries' }));
    fireEvent.click(legend.getByRole('button', { name: /^1\. Acme/ }));
    expect(await screen.findByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    const filter = document.querySelector('.air-radar-legend input')!;
    fireEvent.keyDown(filter, { key: 'Escape' });
    expect(screen.getByRole('region', { name: 'Entry: Acme' })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Entry: Acme' })).toBeNull());
  });
});

describe('F3-068: the unsaved-edits dialog stays while its action runs', () => {
  it('ignores Esc until the restore has settled', async () => {
    let finish: () => void = () => undefined;
    const { api } = fakeApi({
      'GET /api/radars/rivals': view(2, BASE),
      'GET /api/radars/rivals/versions': { items: [{ version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' }] },
    });
    vi.spyOn(api, 'rollback').mockImplementation(() => new Promise((r) => (finish = () => r(view(3, BASE).radar))));
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.change(screen.getByLabelText('Title of Acme'), { target: { value: 'Acme Corp' } });
    fireEvent.change(screen.getByLabelText('Title of Globex'), { target: { value: 'Globex Corp' } });
    tabs(container).findTabLinkById('history')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
    await screen.findByText('Your entry edits are not saved yet');
    expect(screen.getByText(/Your 2 unsaved changes in Edit entries are kept/)).toBeInTheDocument();
    fireEvent.click(within(visibleModal().getElement()).getByRole('button', { name: 'Restore' }));
    visibleModal().findDismissButton().click();
    expect(screen.getByText('Your entry edits are not saved yet')).toBeInTheDocument();
    await act(async () => finish());
    await waitFor(() => expect(screen.queryByText('Your entry edits are not saved yet')).toBeNull());
  });
});

describe('F3-107: a role change reads the radar again', () => {
  it('re-reads the view when the role changes', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': view(2, BASE) });
    let setMe: (me: Me) => void = () => undefined;
    function Host() {
      const [me, set] = React.useState<Me>(ADMIN);
      setMe = set;
      return (
        <TestSessionProvider value={sessionValue({ api, me })}>
          <NotificationProvider>
            <TestRouter path="/radars/rivals">{routes}</TestRouter>
          </NotificationProvider>
        </TestSessionProvider>
      );
    }
    render(<Host />);
    await screen.findByRole('group', { name: 'Rivals radar' });
    const reads = () => calls.filter((c) => c.path === '/api/radars/rivals').length;
    const before = reads();
    act(() => setMe({ ...ADMIN, role: 'viewer', can: ['read'] }));
    await waitFor(() => expect(reads()).toBe(before + 1));
  });
});

describe('F3-026, F3-078: descriptions are text', () => {
  it('shows "R&D" and keeps line breaks, in the panel and the editor', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': view(2, [e('k1', 'Acme', { description: 'R&amp;D <b>fast</b> &amp; cheap<br>line two' })]) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    const legend = within(await screen.findByRole('region', { name: 'Legend entries' }));
    fireEvent.click(legend.getByRole('button', { name: /^1\. Acme/ }));
    const panel = await screen.findByRole('region', { name: 'Entry: Acme' });
    const text = panel.querySelector('.air-pre')!;
    expect(text.textContent).toBe('R&D fast & cheap\nline two');
    tabs(container).findTabLinkById('edit')!.click();
    expect(await screen.findByLabelText('Description of Acme')).toHaveValue('R&D fast & cheap\nline two');
    expect(screen.getByText('Edit any cell or add an entry', { exact: false })).toBeInTheDocument(); // decoding is no change
  });
});

describe('F3-005, F3-080: problem links clear the selection and focus the named cell', () => {
  it('drops the selection and focuses the ring, again on a second click', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': view(2, [e('k1', 'Alpha'), e('k2', 'Charlie', { ring: 'Nowhere' })]) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openEdit(container);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Alpha' }));
    expect(screen.getByRole('button', { name: 'Remove 1' })).toBeInTheDocument();
    const link = screen.getByRole('button', { name: /Charlie: choose a position/ });
    fireEvent.click(link);
    expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled();
    const ring = () => screen.getByLabelText('Position of Charlie');
    await waitFor(() => expect(ring()).toHaveFocus());
    (document.activeElement as HTMLElement).blur();
    fireEvent.click(link);
    await waitFor(() => expect(ring()).toHaveFocus());
    // Add entry clears the selection too.
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find entries by title' }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Alpha' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled();
  });
});

describe('F3-089: the other limits are checked per cell', () => {
  it('counts each & as five, and checks the sub-sector and the title as the API counts them', () => {
    expect(storedLength('R&D <b>x</b>')).toBe('R&amp;D x'.length);
    const long = '&'.repeat(801);
    const problems = entryProblems(SCHEMA, [e('a', 'Acme', { description: long, sub_sector: 'x'.repeat(121) }), e('b', `${'\u{1F600}'.repeat(100)}  pad`)]);
    expect(problems.map((p) => p.field)).toEqual(['description', 'sub_sector']);
    expect(problems[0]!.message).toMatch(/this one is 4005/);
    expect(entryProblems(SCHEMA, [e('a', 'x'.repeat(121))])[0]!.message).toMatch(/this one is 121/);
  });
});
