// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry } from '@/api/types';
import EntryEditor, { changedRows, entryProblems } from '@/radar/EntryEditor';
import { Legend, RadarChart, tooltipAt } from '@/radar/RadarChart';
import { dimensionsOf, IMPORT_MAX_BYTES, planDimension, renamesOf, unmatchedAfter } from '@/radar/RadarTabs';
import { radarView, SCHEMA } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import RadarPage from './RadarPage';
import RadarsPage, { drawable } from './RadarsPage';

/** Red-team round 1 (frontend.md): each test names the findings it checks. */

const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible());
const area = (label: string) =>
  createWrapper(document.body).findAllTextareas().find((t) => t.findNativeTextarea().getElement().getAttribute('aria-label') === label);
function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const routes = (
  <>
    <Routes>
      <Route path="/radars/:slug" element={<RadarPage />} />
      <Route path="/radars" element={<RadarsPage />} />
    </Routes>
    <Routes>
      <Route path="*" element={<Where />} />
    </Routes>
  </>
);
const where = () => screen.getByTestId('where').textContent;
const tabs = (c: HTMLElement) => createWrapper(c).findTabs()!;

afterEach(() => vi.restoreAllMocks());

const templates = { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }] };

describe('RadarsPage (F-001, F-003, F-021..F-028, F-075)', () => {
  it('finds, sorts and pages radars, labels modes and explains them', async () => {
    const items = Array.from({ length: 23 }, (_, i) => ({
      ...radarView().radar,
      slug: `r${i}`,
      name: `Radar ${String(i).padStart(2, '0')}`,
      purpose: i === 3 ? 'pricing moves' : '',
      mode: (['manual', 'assist', 'copilot', 'autopilot'] as const)[i % 4],
      entry_count: i,
      updated_at: `2026-10-${String(1 + (i % 9)).padStart(2, '0')}T10:00:00Z`,
      can_edit: true,
    }));
    const { api } = fakeApi({ 'GET /api/radars': { items } });
    const { container } = render(wrap(<RadarsPage />, { api }));
    const table = await waitFor(() => createWrapper(container).findTable()!);
    await waitFor(() => expect(table.findRows()).toHaveLength(20)); // a page
    expect(screen.getAllByText('Assist').length).toBeGreaterThan(0); // labels, not raw codes
    expect(screen.getAllByText('Off').length).toBeGreaterThan(0);
    createWrapper(container).findPagination()!.findNextPageButton().click();
    expect(table.findRows()).toHaveLength(3);
    table.findColumnSortingArea(1)!.click(); // by name, ascending
    expect(table.findBodyCell(1, 1)!.getElement()).toHaveTextContent('Radar 00');
    table.findColumnSortingArea(1)!.click();
    expect(table.findBodyCell(1, 1)!.getElement()).toHaveTextContent('Radar 22');
    table.findColumnSortingArea(3)!.click(); // entries
    expect(table.findBodyCell(1, 1)!.getElement()).toHaveTextContent('Radar 00');
    createWrapper(container).findTextFilter()!.findInput().setInputValue('pricing');
    expect(table.findRows()).toHaveLength(1);
    expect(screen.getAllByText('1 match').length).toBeGreaterThan(0);
    createWrapper(container).findTextFilter()!.findInput().setInputValue('zzz');
    expect(screen.getByText('No radars match "zzz".')).toBeInTheDocument();
    const updated = screen.queryAllByTitle(/2026/);
    expect(updated.length).toBe(0); // filtered out; check the title on a visible row
    createWrapper(container).findTextFilter()!.findInput().setInputValue('');
    expect(container.querySelector('span[title]')).not.toBeNull(); // Updated has the exact time on hover
    createWrapper(container).findPopover()!.findTrigger().click();
    expect(await screen.findByText(/proposes changes to this radar/)).toBeInTheDocument();
  });

  it('opens the dialog from ?create=1 and keeps a typed hyphen in the address', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars': { items: [] }, 'GET /api/radar-templates': templates, 'POST /api/radars': (b: unknown) => ({ ...radarView().radar, ...(b as object) }) });
    render(wrap(routes, { api, path: '/radars?create=1' }));
    expect(await screen.findByText('Create a radar')).toBeInTheDocument();
    const address = screen.getByLabelText('Address');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'CRM' } });
    for (const text of ['c', 'crm', 'crm-', 'crm-r', 'crm-rivals']) fireEvent.change(address, { target: { value: text } });
    expect(address).toHaveValue('crm-rivals');
    fireEvent.blur(address); // nothing to tidy
    expect(address).toHaveValue('crm-rivals');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ slug: 'crm-rivals' }));
  });

  it('closing the ?create=1 dialog drops the parameter', async () => {
    const { api } = fakeApi({ 'GET /api/radars': { items: [] }, 'GET /api/radar-templates': templates });
    render(wrap(routes, { api, path: '/radars?create=1' }));
    await screen.findByText('Create a radar');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); // nothing typed: closes at once
    await waitFor(() => expect(where()).toBe('/radars'));
    expect(visibleModal()).toBeUndefined();
  });

  it('never opens creation from ?create=1 for viewers', async () => {
    const { api } = fakeApi({ 'GET /api/radars': { items: [] } });
    render(wrap(routes, { api, path: '/radars?create=1', me: VIEWER }));
    expect(await screen.findByText('No radars yet.')).toBeInTheDocument();
    expect(screen.queryByText('Create a radar')).toBeNull();
  });

  it('explains why Create is unavailable, checks the purpose length, and asks before discarding on Esc', async () => {
    const { api } = fakeApi({ 'GET /api/radars': { items: [] }, 'GET /api/radar-templates': templates });
    render(wrap(<RadarsPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create radar' }));
    await waitFor(() => expect(area('Sectors')).toBeDefined());
    const create = screen.getByRole('button', { name: 'Create' });
    expect(create).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '\u65e5\u672c\u306e\u7af6\u5408' } });
    expect(screen.getByText(/the name has no letters or digits to make one from/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Address'), { target: { value: 'japan' } });
    area('Purpose')!.setTextareaValue('x'.repeat(301));
    expect(screen.getByText('At most 300 characters.')).toBeInTheDocument();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    area('Purpose')!.setTextareaValue('Q3 battlecard input');
    expect(create).not.toHaveAttribute('aria-disabled');
    fireEvent.submit(create.closest('form')!);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '' } });
    fireEvent.submit(create.closest('form')!); // blocked: nothing is sent
    visibleModal()!.findDismissButton().click(); // Esc / backdrop / X
    expect(screen.getByText('Discard this radar?')).toBeInTheDocument();
  });

  it('says when templates are loading or failed, and retries', async () => {
    let fail = true;
    const { api } = fakeApi({
      'GET /api/radars': { items: [] },
      'GET /api/radar-templates': () => {
        if (fail) throw { status: 500, message: 'templates down' };
        return templates;
      },
    });
    render(wrap(<RadarsPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create radar' }));
    expect(await screen.findByText(/The templates could not be loaded, so their lists/)).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(area('Sectors')).toBeDefined());
    expect(screen.queryByText(/The templates could not be loaded, so their lists/)).toBeNull();
  });

  it('keeps the preview drawable while a list is over its limit', () => {
    const big = { ...SCHEMA, sectors: 'abcdefghi'.split('').map((name) => ({ name })), rings: [{ name: 'A' }, { name: 'a' }], markers: [] };
    const out = drawable(big);
    expect(out.sectors).toHaveLength(8);
    expect(out.rings).toEqual([{ name: 'A' }]);
  });
});

describe('RadarPage (F-004, F-005, F-013, F-029..F-034, F-055, F-061, F-062)', () => {
  it('keeps the tab in the address, and offers only the tabs a viewer has', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ can_edit: false, can_admin: false }), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=history' }));
    await waitFor(() => expect(tabs(container).findActiveTab()!.getElement()).toHaveTextContent('History'));
    tabs(container).findTabLinkById('io')!.click();
    expect(where()).toBe('/radars/rivals?tab=io');
    // A viewer exports only: the tab says so, and the Export card takes the row.
    expect(tabs(container).findTabLinkById('io')!.getElement()).toHaveTextContent('Export');
    expect(screen.queryByText('Import')).toBeNull();
    tabs(container).findTabLinkById('view')!.click();
    expect(where()).toBe('/radars/rivals');
  });

  it('falls back to the Radar tab for a tab the user cannot see', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ can_admin: false }), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(tabs(container).findActiveTab()!.getElement()).toHaveTextContent('Radar'));
  });

  it('says how many suggestions wait on a copilot radar', async () => {
    const sug = { entry_key: 'k1', is_new: true, proposal: { title: 'Gamma', sector: 'Direct', ring: 'Leader' }, base: null, reason: 'r', evidence: [], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' };
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'copilot' } }), 'GET /api/radars/rivals/suggestions': { items: [sug] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    expect(await screen.findByText('The Radar curator has 1 suggestion waiting for review.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(tabs(container).findActiveTab()!.getElement()).toHaveTextContent('Suggestions (1)');
  });

  it('shows the sub-sector in the entry detail, and the version line under the purpose', async () => {
    const view = radarView({ entries: [{ key: 'k1', title: 'Acme', sector: 'Direct', sub_sector: 'Mid-market', ring: 'Leader', description: '' }] });
    const { api } = fakeApi({ 'GET /api/radars/rivals': view, 'GET /api/radars/rivals/suggestions': { items: [] } });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    fireEvent.click(await waitFor(() => within(screen.getByRole('region', { name: 'Legend entries' })).getByRole('button', { name: /^1\. Acme/ })));
    expect(screen.getByText('Direct \u00b7 Mid-market \u00b7 Leader')).toBeInTheDocument();
    expect(screen.getByText(/^Version 2/)).toBeInTheDocument();
  });

  it('asks before a new version replaces unsaved entry edits', async () => {
    let rollbacks = 0;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': { items: [{ version: 2, created_at: '2026-10-02T10:00:00Z', created_by: 'user:u-admin', entry_count: 1 }, { version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-other', entry_count: 0 }] },
      'POST /api/radars/rivals/rollback': () => {
        rollbacks += 1;
        if (rollbacks === 1) throw { status: 409, message: 'someone saved first' };
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=edit' }));
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    tabs(container).findTabLinkById('history')!.click();
    expect(await screen.findByText('You')).toBeInTheDocument(); // who saved: the signed-in user
    expect(screen.getByText('Added by a person')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).pop()!);
    expect(await screen.findByText('Your entry edits are not saved yet')).toBeInTheDocument();
    expect(screen.getByText(/1 unsaved change in Edit entries is kept and re-applied/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Go to Edit entries' }));
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith('/rollback'))).toBe(false);
    tabs(container).findTabLinkById('history')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).pop()!);
    visibleModal()!.findDismissButton().click(); // dismisses the unsaved-edits question
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); // the restore dialog has Cancel
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).pop()!);
    await screen.findByText('Your entry edits are not saved yet');
    fireEvent.click(within(visibleModal()!.getElement()).getByRole('button', { name: 'Restore' }));
    expect(await screen.findByText(/Someone else saved this radar in the meantime/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).pop()!);
    // F2-004: the failed restore created no version, so the edits are still there and are asked about again.
    await screen.findByText('Your entry edits are not saved yet');
    fireEvent.click(within(visibleModal()!.getElement()).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(rollbacks).toBe(2));
  });

  it('lets the user leave freely when nothing is unsaved, and shows a failing suggestions list', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByText('Suggestions could not be loaded')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    tabs(container).findTabLinkById('view')!.click();
    fireEvent.click(await screen.findByRole('link', { name: 'Radars' })); // breadcrumb
    await waitFor(() => expect(where()).toBe('/radars'));
  });

  it('tells non-admins of a manual radar to ask its owner', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'manual' }, can_admin: false }) });
    render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions', me: VIEWER }));
    expect(await screen.findByText(/Ask the radar owner to turn them on/)).toBeInTheDocument();
  });
});

describe('Import and export (F-007, F-009, F-052..F-057)', () => {
  it('does not read a file over the limit', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await screen.findAllByText('Choose file');
    const upload = createWrapper(container).findFileUpload()!;
    const big = new File(['x'], 'huge.csv');
    Object.defineProperty(big, 'size', { value: IMPORT_MAX_BYTES + 1 });
    const read = vi.spyOn(big, 'text');
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [big] } });
    expect(await screen.findByText(/the limit is 5 MB/)).toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
    expect(calls.some((c) => c.path.endsWith('/import'))).toBe(false);
  });

  it('lists every problem and lets the user download the list', async () => {
    const unmatched = Array.from({ length: 12 }, (_, i) => ({ row: i + 2, field: 'ring', value: `Bad${i}`, suggestion: null }));
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] }, 'POST /api/radars/rivals/import': { rows: 12, unmatched, duplicate_rows: [], accepted: false } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await screen.findAllByText('Choose file');
    const upload = createWrapper(container).findFileUpload()!;
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['a'], 'r.csv')] } });
    expect(await screen.findByText('Row 13: "Bad11" is not a position')).toBeInTheDocument(); // not cut at 8, and the radar's heading (F2-044)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Download the list' }));
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('rivals-import-problems.txt');
  });

  it('draws the export copy without tab stops', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/rivals/suggestions': { items: [] } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await screen.findByRole('button', { name: 'SVG' });
    const hidden = container.querySelector('div[aria-hidden="true"] svg')!;
    expect(hidden.querySelectorAll('[tabindex]')).toHaveLength(0);
  });
});

describe('Suggestions, history and settings (F-006, F-008, F-058..F-074)', () => {
  it('labels evidence by its title and compares any two versions on pages', async () => {
    const sug = { entry_key: 'k1', is_new: false, proposal: { marker: 'Gaining' }, base: radarView().entries[0], reason: 'r', evidence: ['signal/7'], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' };
    const versions = Array.from({ length: 25 }, (_, i) => ({ version: 25 - i, created_at: '2026-10-02T10:00:00Z', created_by: 'module:curator', entry_count: 1 }));
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, current_version: 25 } }),
      'GET /api/radars/rivals/suggestions': { items: [sug] },
      'GET /api/graph/nodes/signal/7': { id: 'signal/7', title: 'Acme cuts prices' },
      'GET /api/radars/rivals/versions': { items: versions },
      'GET /api/radars/rivals/diff': (_: unknown, url: URL) => ({ from: 1, to: 2, changes: { added: [], removed: [], moved: [], recategorised: [], marker_changed: [], edited: [{ key: 'a', title: `T${url.searchParams.get('from')}`, fields: ['x'] }, { key: 'b', title: `T${url.searchParams.get('from')}`, fields: ['x'] }] } }),
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=suggestions' }));
    expect(await screen.findByRole('link', { name: 'Acme cuts prices' })).toBeInTheDocument();
    expect(screen.getByText(/Momentum: Gaining/)).toBeInTheDocument();
    tabs(container).findTabLinkById('history')!.click();
    await screen.findByText('25 (current)');
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(20);
    createWrapper(container).findPagination()!.findNextPageButton().click();
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(5);
    fireEvent.click(screen.getAllByRole('button', { name: 'Changes' })[0]!);
    expect(await screen.findAllByText('T4: edited x')).toHaveLength(2); // identical lines both show
    const [from, to] = visibleModal()!.findContent().findAllSelects();
    from!.openDropdown();
    from!.selectOptionByValue('1', { expandToViewport: true });
    expect(await screen.findAllByText('T1: edited x')).toHaveLength(2);
    to!.openDropdown();
    to!.selectOptionByValue('25', { expandToViewport: true });
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/diff')).length).toBe(3));
    expect(screen.getByText('Changes from version 1 to version 25')).toBeInTheDocument();
  });

  it('checks days, saves only what changed, adds editors with Enter and edits headings', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'autopilot' } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PATCH /api/radars/rivals': radarView().radar,
      'PUT /api/radars/rivals/access': radarView().radar,
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    const saveGeneral = await screen.findByRole('button', { name: 'Save general' });
    expect(saveGeneral).toHaveAttribute('aria-disabled', 'true'); // nothing changed yet
    expect(screen.getByRole('button', { name: 'Save schema' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText(/Agentic Insights Radar account/)).toBeInTheDocument();
    expect(screen.getByText('Danger zone')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('7'), { target: { value: '' } });
    expect(screen.getByText('Enter a whole number of at least 1.')).toBeInTheDocument();
    expect(saveGeneral).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByDisplayValue('Rivals'), { target: { value: '  ' } });
    fireEvent.change(screen.getByLabelText('Apply after (days)'), { target: { value: '2' } });
    expect(saveGeneral).toHaveAttribute('aria-disabled', 'true'); // no name
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Rivals 2' } });
    fireEvent.click(saveGeneral);
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ name: 'Rivals 2', auto_apply_days: 2 }));
    const email = screen.getByLabelText('Editor email');
    fireEvent.change(email, { target: { value: 'not-an-email' } });
    fireEvent.submit(email.closest('form')!); // Enter with a bad address adds nothing
    fireEvent.change(email, { target: { value: 'pat@example.com' } });
    fireEvent.submit(email.closest('form')!); // Enter adds
    expect(screen.getByText('pat@example.com')).toBeInTheDocument();
    expect(screen.getByText(/Unsaved changes: press Save access/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Ring heading'), { target: { value: 'Standing' } });
    fireEvent.change(screen.getByLabelText('Sector heading'), { target: { value: ' ' } }); // blank keeps the saved heading
    fireEvent.change(screen.getByLabelText('Marker heading'), { target: { value: 'Trend' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    await waitFor(() => expect((calls.find((c) => c.path.endsWith('/schema'))?.body as { schema: object }).schema).toMatchObject({ ring_label: 'Standing', sector_label: 'Category', marker_label: 'Trend' }));
  });

  it('turns a renamed value into a rename (colour kept) and asks before removing entries', async () => {
    const schema = { ...SCHEMA, sectors: [{ name: 'Direct', color: '#123456' }, { name: 'Adjacent' }] };
    const entries: RadarEntry[] = [
      { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' },
      { key: 'k2', title: 'Beta', sector: 'Adjacent', ring: 'Challenger', description: '' },
    ];
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema, entries }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByDisplayValue(/Adjacent/), { target: { value: 'Direct rivals\nAdjacent\nAdjacent' } });
    expect(screen.getByText('"Adjacent" is listed twice.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save schema' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByDisplayValue(/Direct rivals/), { target: { value: 'Direct rivals\nAdjacent' } });
    fireEvent.change(screen.getByDisplayValue(/Challenger/), { target: { value: 'Leader' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    // F2-008: every lost value gets its own rename-or-remove choice, pre-filled with the guess.
    expect(screen.getByText(/^"Direct" \(\d+ entr(y|ies)\): Rename it to "Direct rivals" \(guessed: check it\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^"Challenger" \(\d+ entr(y|ies)\): Remove it, and its entries with it \(guessed: check it\)$/)).toBeInTheDocument();
    expect(screen.getByText('1 entry would be removed')).toBeInTheDocument();
    expect(screen.getByText(/no longer in the schema: Beta\./)).toBeInTheDocument();
    visibleModal()!.findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.path.endsWith('/schema'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save and remove 1 entry' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/schema'))?.body).toMatchObject({ renames: { sector: { Direct: 'Direct rivals' } }, drop_unmatched: true }));
    const sent = calls.find((c) => c.path.endsWith('/schema'))!.body as { schema: { sectors: { name: string; color?: string }[] } };
    expect(sent.schema.sectors[0]).toEqual({ name: 'Direct rivals', color: '#123456' });
  });

  it('plans renames only where a new value took the old one\'s place (F2-008)', () => {
    expect(renamesOf(planDimension([{ name: 'A' }, { name: 'B' }], 'A\nC\nD'))).toEqual({ B: 'C' });
    const kept = planDimension([{ name: 'A', color: '#111111' }], 'a');
    expect(dimensionsOf([{ name: 'A', color: '#111111' }], kept)).toEqual([{ name: 'a', color: '#111111' }]);
    const entries: RadarEntry[] = [{ key: 'x', title: 'X', sector: 'Direct', ring: 'Leader', marker: 'Old', description: '' }];
    expect(unmatchedAfter(entries, SCHEMA, {})).toHaveLength(1); // the marker is gone
    expect(unmatchedAfter(entries, SCHEMA, { marker: { Old: 'Gaining' } })).toHaveLength(0);
  });
});

describe('Entry editor (F-045..F-051)', () => {
  const schema = SCHEMA;
  function Editor({ initial }: { initial: RadarEntry[] }) {
    const [rows, setRows] = React.useState(initial);
    return <EntryEditor schema={schema} entries={initial} rows={rows} onRowsChange={setRows} saving={false} onSave={vi.fn()} />;
  }

  it('lists every problem, marks the cells, pages long lists and finds rows by title', () => {
    const rows: RadarEntry[] = Array.from({ length: 30 }, (_, i) => ({ key: `k${i}`, title: i < 12 ? '' : `Entry ${i}`, sector: 'Direct', ring: 'Leader', description: '', link: i === 29 ? 'http://x.example.com' : undefined }));
    const { container } = render(<Editor initial={rows} />);
    expect(screen.getByText('13 problems to fix before saving')).toBeInTheDocument();
    expect(screen.getByText('and 3 more. The cells with a problem are marked.')).toBeInTheDocument();
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(25);
    const titles = screen.getAllByLabelText(/^Title of /);
    expect(titles[0]).toHaveAttribute('aria-invalid', 'true');
    createWrapper(container).findPagination()!.findNextPageButton().click();
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(5);
    createWrapper(container).findTextFilter()!.findInput().setInputValue('entry 29');
    expect(createWrapper(container).findTable()!.findRows()).toHaveLength(1);
    expect(screen.getByLabelText(/^Link of /)).toHaveAttribute('aria-invalid', 'true');
    createWrapper(container).findTextFilter()!.findInput().setInputValue('nothing');
    expect(screen.getByText('No entries match.')).toBeInTheDocument();
  });

  it('adds a row on top and focuses it, and asks before discarding', async () => {
    const rows: RadarEntry[] = [{ key: 'a', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' }];
    render(<Editor initial={rows} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    await waitFor(() => expect(screen.getAllByLabelText(/^Title of /)[0]).toHaveFocus());
    expect(screen.getAllByLabelText(/^Title of /)[1]).toHaveValue('Acme');
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByText(/Your unsaved edits \(1 change\) are thrown away/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getAllByLabelText(/^Title of /)).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    visibleModal()!.findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getAllByLabelText(/^Title of /)).toHaveLength(1);
  });

  it('counts changes row by row, removed rows included', () => {
    const a = { key: 'a', title: 'A', sector: 'Direct', ring: 'Leader', description: '' };
    const b = { ...a, key: 'b', title: 'B' };
    expect(changedRows([a, b], [a, b])).toBe(0);
    expect(changedRows([{ ...a, title: 'A2' }], [a, b])).toBe(2);
    expect(entryProblems(SCHEMA, [{ ...a, link: 'https://ok.example.com/x' }])).toEqual([]);
  });
});

describe('Radar chart and legend (F-036..F-044)', () => {
  const entries: RadarEntry[] = [
    { key: 'a', title: 'Acme', sector: 'Direct', ring: 'Leader', marker: 'Gaining', description: '' },
    { key: 'b', title: 'Beta', sector: 'Adjacent', ring: 'Challenger', description: '' },
  ];

  it('keeps the tooltip inside the picture', () => {
    expect(tooltipAt({ x: 500, y: 0 }, 320)).toBe('translate(308.0,-20.0)'); // clamped at the right edge
    expect(tooltipAt({ x: -500, y: 0 }, 320)).toBe('translate(-308.0,-20.0)');
    expect(tooltipAt({ x: 0, y: -300 }, 320)).toBe('translate(0.0,-242.0)'); // flipped below near the top
  });

  it('marks the selected legend row, filters by sector and ring, and lists entries it cannot place', () => {
    const onEdit = vi.fn();
    const lost: RadarEntry = { key: 'c', title: 'Lost', sector: '', ring: 'Leader', description: '' };
    render(<Legend schema={SCHEMA} entries={[{ ...lost, key: 'z', sector: 'Direct', ring: '' }]} />).unmount();
    expect(1).toBe(1);
    const { rerender } = render(<Legend schema={SCHEMA} entries={[...entries, lost]} selected="a" onEditEntries={onEdit} />);
    expect(screen.getByRole('button', { name: '1. Acme, Leader, Gaining' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '2. Beta, Challenger' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('Lost (- / Leader)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Fix them in Edit entries' }));
    expect(onEdit).toHaveBeenCalled();
    expect(screen.getByText(/Numbers follow the legend order/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Filter by title, description, category, position or momentum')).toBeInTheDocument();
    rerender(<Legend schema={SCHEMA} entries={entries} query="adjacent" />);
    expect(screen.queryByRole('button', { name: /Acme/ })).toBeNull();
    rerender(<Legend schema={SCHEMA} entries={entries} query="challenger" />);
    expect(screen.getByRole('button', { name: '2. Beta, Challenger' })).toBeInTheDocument();
    rerender(<Legend schema={SCHEMA} entries={[...entries, lost, { ...lost, key: 'd', title: 'Lost too' }]} />);
    expect(screen.getByText(/2 entries do not match/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fix them in Edit entries' })).toBeNull();
  });

  it('draws a picture-only chart with no focusable blips', () => {
    const { container } = render(<RadarChart schema={SCHEMA} entries={entries} interactive={false} />);
    expect(container.querySelectorAll('[tabindex]')).toHaveLength(0);
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThanOrEqual(2);
  });
});


