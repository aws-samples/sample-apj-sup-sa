// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { radarView, SCHEMA } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import RadarPage from './RadarPage';
import { slugify, slugTyping } from '@/lib/format';

import RadarsPage from './RadarsPage';

const visibleModal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
/** Cloudscape puts ariaLabel on the native control, so look the wrapper up through it. */
const area = (label: string) =>
  createWrapper(document.body).findAllTextareas().find((t) => t.findNativeTextarea().getElement().getAttribute('aria-label') === label);
const field = (label: string) =>
  createWrapper(document.body).findAllInputs().find((t) => t.findNativeInput().getElement().getAttribute('aria-label') === label);
/** A legend row by its number and title (its accessible name goes on with the ring and marker). */
const legendRow = (name: string) => within(screen.getByRole('region', { name: 'Legend entries' })).getByRole('button', { name: new RegExp(`^${name.replace('.', '\\.')}(,|$)`) });
const queryLegendRow = (name: string) => within(screen.getByRole('region', { name: 'Legend entries' })).queryByRole('button', { name: new RegExp(`^${name.replace('.', '\\.')}(,|$)`) });
const routes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /><Route path="/radars" element={<div>list</div>} /><Route path="/graph" element={<div>graph page</div>} /></Routes>;

afterEach(() => vi.restoreAllMocks());

describe('RadarsPage', () => {
  it('lists radars and creates one from a template', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars': { items: [{ ...radarView().radar, can_edit: true }, { ...radarView().radar, slug: 'm', name: 'Manual one', mode: 'manual', purpose: '' }] },
      'GET /api/radar-templates': { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }, { key: 'blank', purpose: 'Empty', schema: { ...SCHEMA, markers: [] } }] },
      'POST /api/radars': (b: unknown) => ({ ...radarView().radar, ...(b as object) }),
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /><Route path="/radars/:slug" element={<div>opened</div>} /></Routes>, { api, path: '/radars' }));
    expect(await screen.findByText('Rivals')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create radar' }));
    const modal = visibleModal();
    modal.findContent().findInput()!.setInputValue('Mid-market CRM vendors!');
    await waitFor(() => expect(area('Sectors')).toBeDefined());
    modal.findContent().findSelect()!.openDropdown();
    modal.findContent().findSelect()!.selectOptionByValue('blank', { expandToViewport: true });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ slug: 'mid-market-crm-vendors', name: 'Mid-market CRM vendors!', template: 'blank' });
  });

  it('customises headings and lists before creating, with limits and a preview', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars': { items: [] },
      'GET /api/radar-templates': { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }] },
      'POST /api/radars': (b: unknown) => ({ ...radarView().radar, ...(b as object) }),
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /><Route path="/radars/:slug" element={<div>opened</div>} /></Routes>, { api, path: '/radars' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create radar' }));
    const modal = visibleModal().findContent();
    expect(await screen.findByRole('group', { name: 'New radar' })).toBeInTheDocument();
    modal.findInput()!.setInputValue('Tools');
    await waitFor(() => expect(area('Sectors')).toBeDefined());
    expect(screen.getByRole('group', { name: 'Tools radar' })).toBeInTheDocument();
    const create = screen.getByRole('button', { name: 'Create' });
    area('Sectors')!.setTextareaValue('');
    expect(screen.getByText('Add at least one, one per line.')).toBeInTheDocument();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Fix the lists to update the preview.')).toBeInTheDocument(); // the preview stays, dimmed
    area('Sectors')!.setTextareaValue('a\nb\nc\nd\ne\nf\ng\nh\ni');
    expect(screen.getByText('At most 8 (you have 9).')).toBeInTheDocument();
    area('Markers')!.setTextareaValue('New\nnew');
    expect(screen.getByText('"new" is listed twice.')).toBeInTheDocument();
    area('Markers')!.setTextareaValue('');
    area('Rings')!.setTextareaValue('Inner');
    expect(screen.getByText('1 of at most 6')).toBeInTheDocument();
    area('Rings')!.setTextareaValue(SCHEMA.rings.map((r) => r.name).join('\n'));
    area('Sectors')!.setTextareaValue(`${SCHEMA.sectors[0]!.name}\nPlatforms`);
    for (const h of ['Sector heading', 'Ring heading', 'Marker heading']) field(h)!.setInputValue('');
    expect(screen.getByText('Sectors (one per line)')).toBeInTheDocument();
    expect(screen.getByText('Markers (one per line, optional)')).toBeInTheDocument();
    field('Sector heading')!.setInputValue('Area');
    field('Ring heading')!.setInputValue('');
    field('Marker heading')!.setInputValue('Trend');
    area('Purpose')!.setTextareaValue('  What we use  ');
    expect(screen.getByText('2 of at most 8')).toBeInTheDocument();
    fireEvent.click(create);
    expect(await screen.findByText('opened')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      slug: 'tools',
      name: 'Tools',
      purpose: 'What we use',
      schema: { ...SCHEMA, sector_label: 'Area', marker_label: 'Trend', sectors: [SCHEMA.sectors[0], { name: 'Platforms' }], markers: [] },
    });
  });

  it('hides creation from viewers', async () => {
    const { api } = fakeApi({ 'GET /api/radars': { items: [] } });
    render(wrap(<RadarsPage />, { api, me: VIEWER }));
    expect(await screen.findByText('No radars yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create radar' })).toBeNull();
  });

  it('slugifies names', () => {
    expect(slugify('  Hello, World!  ')).toBe('hello-world');
    expect(slugify('\u00dcn\u00efc\u00f6d\u00e9')).toBe('unicode');
    // While typing, a trailing hyphen stays, so "crm-rivals" can be typed by hand; a leading one goes.
    expect(slugTyping('crm-')).toBe('crm-');
    expect(slugTyping('-crm-rivals')).toBe('crm-rivals');
    expect(slugify('crm-')).toBe('crm');
  });
});

describe('RadarPage', () => {
  it('links an entry to its node in the knowledge graph', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/graph/nodes/organization/1': { id: 'organization/1', title: 'Acme Inc.' } });
    render(wrap(routes, { api, path: '/radars/rivals' }));
    fireEvent.click(await waitFor(() => legendRow('1. Acme')));
    const link = screen.getByRole('link', { name: 'View in Knowledge Graph' });
    expect(link).toHaveAttribute('href', '/graph?focus=organization%2F1');
    expect(await screen.findByRole('link', { name: 'Acme Inc.' })).toHaveAttribute('href', '/insights/nodes/organization/1');
    fireEvent.click(link);
    expect(await screen.findByText('graph page')).toBeInTheDocument();
  });

  it('shows the chart, legend and entry details', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    expect(await screen.findByRole('group', { name: 'Rivals radar' })).toBeInTheDocument();
    fireEvent.click(legendRow('1. Acme'));
    expect(screen.getByText('Market leader')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'acme.example.com' })).toHaveAttribute('href', 'https://acme.example.com'); // the host, not the raw URL
    expect(within(screen.getByRole('region', { name: 'Entry: Acme' })).getByText('Acme')).toHaveFocus(); // the heading takes focus, so it is announced and in view
    expect(screen.getByRole('link', { name: 'Open the linked record' })).toBeInTheDocument(); // until its title loads
    const blip = container.querySelector('g[role="button"]')!;
    fireEvent.mouseEnter(blip);
    fireEvent.keyDown(blip, { key: 'Enter' });
    fireEvent.mouseLeave(blip);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByText('Market leader')).toBeNull();
    fireEvent.click(legendRow('1. Acme'));
    fireEvent.keyDown(screen.getByText('Market leader'), { key: 'Escape' });
    expect(screen.queryByText('Market leader')).toBeNull();
  });

  it('filters the legend, dims the chart and keeps the filter in the address', async () => {
    const view = radarView({
      entries: [
        { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', description: 'Market leader' },
        { key: 'k2', title: 'Beta', sector: 'Direct', ring: 'Leader', description: 'New entrant', marker: 'Gaining' },
      ],
    });
    const { api } = fakeApi({ 'GET /api/radars/rivals': view });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?q=gain' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    expect(queryLegendRow('1. Acme')).toBeNull(); // pre-filtered from ?q=
    expect(screen.getAllByText('1 hidden').length).toBeGreaterThan(0); // shown and announced
    const opacity = () => [...container.querySelectorAll('svg[role="group"] g[opacity]')].map((g) => g.getAttribute('opacity'));
    expect(opacity()).toEqual(['0.2', '1']);
    expect(container.querySelectorAll('svg[role="group"] g[role="button"]')).toHaveLength(1); // the faded one is not focusable
    const filter = createWrapper(container).findTextFilter()!;
    filter.findInput().setInputValue('zzz');
    expect(await screen.findByText('No matches for "zzz"')).toBeInTheDocument();
    filter.findInput().setInputValue('');
    await waitFor(() => expect(opacity()).toEqual(['1', '1']));
    expect(legendRow('1. Acme')).toBeInTheDocument();
  });

  it('edits and saves entries as a new version', async () => {
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'PUT /api/radars/rivals/entries': radarView().radar });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Add entry' }));
    const save = screen.getByRole('button', { name: 'Save version' });
    expect(save).toHaveAttribute('aria-disabled', 'true'); // the new row has no title yet
    const titles = screen.getAllByLabelText(/^Title of /);
    fireEvent.change(titles[0]!, { target: { value: 'Acme' } }); // the new row is on top
    expect(screen.getByText(/appears twice/)).toBeInTheDocument();
    fireEvent.change(titles[0]!, { target: { value: 'Beta' } });
    fireEvent.change(screen.getAllByLabelText(/^Description of /)[0]!, { target: { value: 'New entrant' } });
    const w = createWrapper(container);
    for (const i of [0, 1]) {
      // The new row starts with no sector and no ring: the user chooses them.
      const select = w.findAllSelects()[i]!;
      select.openDropdown();
      select.selectOption(1, { expandToViewport: true });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')).toBeTruthy());
    const body = calls.find((c) => c.method === 'PUT')!.body as { entries: { title: string }[]; expected_version: number };
    expect(body.entries.map((e) => e.title)).toEqual(['Beta', 'Acme']);
    expect(body.expected_version).toBe(2);
  });

  it('imports, exports and downloads', async () => {
    const created: Blob[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((b) => { created.push(b as Blob); return 'blob:x'; });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/export': new Response('title\nAcme\n'),
      'GET /api/radars/rivals/template': new Response('title,sector\n'),
      'POST /api/radars/rivals/import': (b: unknown) => ((b as { dry_run?: boolean }).dry_run ? { rows: 1, unmatched: [], duplicate_rows: [], accepted: true } : radarView().radar),
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'SVG' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'CSV' }));
    expect(screen.getByRole('button', { name: 'JSON' })).toBeDisabled(); // one export at a time
    await waitFor(() => expect(click).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole('button', { name: 'JSON' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: 'JSON' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole('button', { name: 'Download the CSV template' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(4));
    expect(click.mock.contexts.map((a) => (a as HTMLAnchorElement).download)).toEqual(['rivals.svg', 'rivals.csv', 'rivals.json', 'rivals-template.csv']);
    const file = new File(['title,sector,ring\nBeta,Direct,Leader\n'], 'radar.csv', { type: 'text/csv' });
    createWrapper(container).findFileUpload()!.findNativeInput().getElement();
    fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [file] } });
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import')).length).toBe(2));
    fireEvent.click(await screen.findByRole('button', { name: 'View the radar' }));
    expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement()).toHaveTextContent('Radar');
    expect(calls.filter((c) => c.path.endsWith('/import'))[1]!.body).toMatchObject({ format: 'csv', mode: 'merge', expected_version: 2 });
    expect(created.length).toBeGreaterThanOrEqual(4);
  });

  it('flags unmatched import rows', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': { rows: 2, unmatched: [{ row: 2, field: 'ring', value: 'Leeder', suggestion: 'Leader' }], duplicate_rows: [3], duplicate_keys: ['k1'], accepted: false },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    const input = (await waitFor(() => createWrapper(container).findFileUpload()!)).findNativeInput().getElement();
    fireEvent.change(input, { target: { files: [new File(['[]'], 'r.json')] } });
    expect(await screen.findByText(/did you mean "Leader"/)).toBeInTheDocument();
    expect(screen.getByText('Row 3: duplicate title')).toBeInTheDocument();
    expect(screen.getByText('Key k1 is on more than one row')).toBeInTheDocument(); // CTR HA-033
    expect(screen.getByText('3 problems to fix in the file before it can be imported:')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('reviews suggestions', async () => {
    const sug = { entry_key: 'k1', is_new: false, proposal: { ring: 'Challenger' }, base: radarView().entries[0], reason: 'Lost share', evidence: ['fact/1'], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' };
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [sug, { ...sug, entry_key: 'k2', is_new: true, proposal: { title: 'Gamma', sector: 'Direct' }, base: null }] },
      'POST /api/radars/rivals/suggestions/k1/accept': radarView().radar,
      'DELETE /api/radars/rivals/suggestions/k2': {},
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.click();
    expect(await screen.findByText('Change: Acme')).toBeInTheDocument();
    expect(screen.getByText(/Position: Leader/)).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Fact' })).toHaveLength(2); // the title is unknown: its type
    expect(createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.getElement()).toHaveTextContent('Suggestions (2)');
    fireEvent.click(screen.getAllByRole('button', { name: 'Accept' })[0]!);
    expect(screen.getAllByRole('button', { name: 'Discard' })[1]).toBeDisabled(); // the row is busy: no second request
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/accept'))).toBe(true));
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Discard' })[1]).not.toBeDisabled());
    fireEvent.click(screen.getAllByRole('button', { name: 'Discard' })[1]!);
    // F2-052: it asks first, because a discarded proposal and its evidence cannot be brought back.
    expect(await screen.findByText('Discard this proposal?')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Discard' }).pop()!);
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('explains manual radars have no suggestions', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'manual' } }) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.click();
    expect(await screen.findByText(/AI suggestions are off/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }));
    expect(await screen.findByText(/Suggestions come from the Radar curator module/)).toBeInTheDocument();
  });

  it('does not offer Settings to people who cannot change the radar', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'manual' }, can_admin: false }) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.click();
    expect(await screen.findByText(/AI suggestions are off/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open Settings' })).toBeNull();
  });

  it('shows history, diffs and restores', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/versions': { items: [{ version: 2, created_at: '2026-10-02T10:00:00Z', created_by: 'u', entry_count: 1, note: '' }, { version: 1, created_at: '2026-10-01T10:00:00Z', created_by: 'u', entry_count: 0, note: 'first', schema_changed: false }] },
      'GET /api/radars/rivals/diff': { from: 1, to: 2, changes: { added: [{ key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' }], removed: [], moved: [{ key: 'k', title: 'B', from: 'a', to: 'b' }], recategorised: [], marker_changed: [{ key: 'k', title: 'C', from: null, to: 'x' }], edited: [{ key: 'k', title: 'D', fields: ['description'] }] } },
      'POST /api/radars/rivals/rollback': radarView().radar,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    expect(await screen.findByText('2 (current)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Changes' }));
    expect(await screen.findByText('Added Acme (Leader)')).toBeInTheDocument();
    expect(screen.getByText('D: edited description')).toBeInTheDocument();
    visibleModal().findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    visibleModal().findDismissButton().click(); // a stray Esc restores nothing
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    expect(visibleModal().findContent().getElement()).toHaveTextContent('Version 1 (0 entries) is copied into a new version 3');
    expect(calls.some((c) => c.path.endsWith('/rollback'))).toBe(false);
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).pop()!);
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/rollback'))?.body).toEqual({ version: 1, expected_version: 2 }));
  });

  it('saves settings, access and schema, and deletes', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'PATCH /api/radars/rivals': radarView().radar,
      'PUT /api/radars/rivals/access': radarView().radar,
      'PUT /api/radars/rivals/schema': radarView().radar,
      'DELETE /api/radars/rivals': {},
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    const w = createWrapper(container);
    w.findTabs()!.findTabLinkById('settings')!.click();
    const select = await waitFor(() => w.findTabs()!.findTabContent()!.findSelect()!);
    select.openDropdown();
    select.selectOptionByValue('autopilot', { expandToViewport: true });
    fireEvent.change(await screen.findByDisplayValue('7'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ mode: 'autopilot', auto_apply_days: 3, version_lock: 3 }));
    fireEvent.change(screen.getByPlaceholderText('name@example.com'), { target: { value: 'New@Example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save access' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/access'))?.body).toEqual({ editors: ['ed@example.com', 'new@example.com'], version_lock: 3 }));
    fireEvent.change(screen.getByDisplayValue(/Leader/), { target: { value: 'Leader\nChallenger\nNiche' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    expect(screen.getByText('Every entry still matches the new schema.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    await waitFor(() => expect((calls.find((c) => c.path.endsWith('/schema'))?.body as { schema: { rings: unknown[] } }).schema.rings).toHaveLength(3));
    fireEvent.change(screen.getByDisplayValue(/Adjacent/), { target: { value: 'Direct\nAdjacent\nNew' } });
    fireEvent.change(screen.getByDisplayValue(/Gaining/), { target: { value: 'Gaining\nFading' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete this radar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Delete this radar' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Delete "Rivals" and all its versions?');
    // Deleting every version is typed out first (CTR QA-089): Delete is off until the slug matches exactly.
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
    const typed = screen.getByLabelText('Type rivals to confirm');
    fireEvent.change(typed, { target: { value: 'Rivals' } });
    expect(screen.getByText('Must be exactly rivals (case-sensitive)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
    fireEvent.change(typed, { target: { value: 'rivals' } });
    expect(screen.queryByText('Must be exactly rivals (case-sensitive)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('list')).toBeInTheDocument();
  });

  it('limits read-only users and reports load errors', async () => {
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ can_edit: false, can_admin: false }), 'GET /api/radars/none': () => { throw { status: 404, message: 'Radar none not found' }; } });
    const { container, unmount } = render(wrap(routes, { api, path: '/radars/rivals', me: VIEWER }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    const tabs = createWrapper(container).findTabs()!;
    expect(tabs.findTabLinkById('edit')).toBeNull();
    expect(tabs.findTabLinkById('settings')).toBeNull();
    unmount();
    render(wrap(routes, { api, path: '/radars/none' }));
    expect(await screen.findByText('Radar not found')).toBeInTheDocument();
  });

  it('opens entries without a marker and lists unplaced ones', async () => {
    const view = radarView({
      radar: { ...radarView().radar, purpose: '' },
      entries: [
        { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', description: '' },
        { key: 'k2', title: 'Lost', sector: 'Elsewhere', ring: 'Leader', description: '' },
      ],
    });
    const { api } = fakeApi({ 'GET /api/radars/rivals': view });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    expect(screen.queryByText('Who competes with us')).toBeNull();
    expect(screen.getByText(/1 entry does not match this radar's/)).toBeInTheDocument();
    expect(screen.getByText('Lost (Elsewhere / Leader)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Fix them in Edit entries' }));
    expect(createWrapper(container).findTabs()!.findActiveTab()!.getElement()).toHaveTextContent('Edit entries');
    createWrapper(container).findTabs()!.findTabLinkById('view')!.click();
    expect(legendRow('1. Acme')).toBeInTheDocument(); // the legend item, no marker
    fireEvent.click(container.querySelector('g[role="button"]')!);
    expect(screen.getByText('Direct \u00b7 Leader')).toBeInTheDocument();
  });

  it('reports a failed save', async () => {
    // A 409 is handled on its own (F2-002): this is the ordinary-failure path.
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'PUT /api/radars/rivals/entries': () => { throw { status: 400, message: 'someone saved first' }; } });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('edit')!.click();
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save version' }));
    expect(await screen.findByText('someone saved first')).toBeInTheDocument();
  });

  it('reports failed checks and imports, and starts over when the file is removed', async () => {
    let checks = 0;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': (b: unknown) => {
        if ((b as { dry_run?: boolean }).dry_run) {
          checks += 1;
          if (checks === 1) throw { status: 400, message: 'unreadable file' };
          if (checks === 2) return { rows: 1, unmatched: [{ row: 2, field: 'sector', value: 'Nowhere', suggestion: null }], duplicate_rows: [], accepted: false };
          return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
        }
        throw { status: 409, message: 'radar changed' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    const upload = await waitFor(() => createWrapper(container).findFileUpload()!);
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['?'], 'radar.csv')] } });
    // F2-038: the reason is inline where the report would be, as well as in a toast.
    expect(await screen.findAllByText('unreadable file')).toHaveLength(2);
    expect(screen.getByText('The file could not be checked')).toBeInTheDocument();
    upload.findFileTokens()[0]!.findRemoveButton().click();
    await waitFor(() => expect(upload.findFileTokens()).toHaveLength(0));
    expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(1); // nothing to check without a file
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['?'], 'radar.csv')] } });
    expect(await screen.findByText('Row 2: "Nowhere" is not a category')).toBeInTheDocument(); // the radar's heading (F2-044)
    fireEvent.change(upload.findNativeInput().getElement(), { target: { files: [new File(['[]'], 'radar.json')] } });
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument();
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument(); // checked again as Replace (CR-07)
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ dry_run: true, mode: 'replace' });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(screen.getByText(/any entry whose title is not in the file is removed/)).toBeInTheDocument();
    visibleModal().findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText(/Someone else saved this radar in the meantime/)).toBeInTheDocument(); // a 409: read again (r3 F3-042)
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ format: 'json', mode: 'replace' });
    fireEvent.dragEnter(document, { dataTransfer: { types: ['Files'], items: [], files: [] } });
    fireEvent.dragOver(document, { dataTransfer: { types: ['Files'], items: [], files: [] } });
    expect(await screen.findByText('Drop a CSV or JSON file')).toBeInTheDocument();
  });

  it('shows only the latest file\'s check when files are swapped quickly', async () => {
    const { api, calls, fetcher } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'POST /api/radars/rivals/import': (b: unknown) => {
        const text = (b as { text: string }).text;
        if (text === 'A') throw { status: 400, message: 'stale failure' };
        if (text === 'B') return { rows: 1, unmatched: [{ row: 2, field: 'sector', value: 'Stale', suggestion: null }], duplicate_rows: [], accepted: false };
        return { rows: 1, unmatched: [], duplicate_rows: [], accepted: true };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    const upload = await waitFor(() => createWrapper(container).findFileUpload()!);
    const input = upload.findNativeInput().getElement();
    const real = fetcher.getMockImplementation()!;
    const gate = () => {
      let open = () => {};
      const opened = new Promise<void>((r) => { open = r; });
      fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => { await opened; return real(...args); });
      return open;
    };
    let readSlow: (text: string) => void = () => {};
    const slow = new File(['D'], 'slow.csv');
    slow.text = () => new Promise<string>((r) => { readSlow = r; });
    fireEvent.change(input, { target: { files: [slow] } });
    const openA = gate();
    // Count the checks only: the page also reads the radar and the caller's favourites.
    const imports = () => fetcher.mock.calls.filter(([url]) => String(url).endsWith('/import')).length;
    fireEvent.change(input, { target: { files: [new File(['A'], 'a.csv')] } });
    await waitFor(() => expect(imports()).toBe(1));
    const openB = gate();
    fireEvent.change(input, { target: { files: [new File(['B'], 'b.csv')] } });
    await waitFor(() => expect(imports()).toBe(2));
    fireEvent.change(input, { target: { files: [new File(['C'], 'c.csv')] } });
    expect(await screen.findByText('Every row matches this radar.')).toBeInTheDocument();
    openA();
    openB();
    readSlow('D');
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(3));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('Every row matches this radar.')).toBeInTheDocument();
    expect(screen.queryByText('stale failure')).toBeNull();
    expect(screen.queryByText(/Stale/)).toBeNull();
    expect(calls.some((c) => (c.body as { text?: string } | undefined)?.text === 'D')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(4));
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ text: 'C' });
  });

  it('exports the radar as a PNG', async () => {
    const names: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download); });
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    class LoadedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_: string) { setTimeout(() => this.onload?.(), 0); }
    }
    vi.stubGlobal('Image', LoadedImage);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '' } as never);
    const sizes: number[][] = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (this: HTMLCanvasElement, cb) {
      sizes.push([this.width, this.height]);
      cb(new Blob(['png'], { type: 'image/png' }));
    });
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView() });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('io')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'PNG' }));
    await waitFor(() => expect(names).toEqual(['rivals.png']));
    const [w = 0, h = 1] = sizes[0] ?? [];
    // F2-029: the exported picture is the chart plus a legend column, so it is wider than the chart.
    expect(w / h).toBeCloseTo((840 + 380 + 24) / 840);
    expect(w).toBeGreaterThan(840);
    vi.unstubAllGlobals();
  });

  it('explains autopilot and reports a failed review', async () => {
    const sug = { entry_key: 'k1', is_new: false, proposal: { ring: 'Challenger' }, base: radarView().entries[0], reason: 'Lost share', evidence: [], created_at: '2026-10-01T10:00:00Z', created_by: 'module:curator' };
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, mode: 'autopilot', auto_apply_days: 5, auto_apply_agent: true } }),
      'GET /api/radars/rivals/suggestions': { items: [sug] },
      'POST /api/radars/rivals/suggestions/k1/accept': () => { throw { status: 409, message: 'entry was edited' }; },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('suggestions')!.click();
    expect(await screen.findByText(/Applied automatically after 5 days/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    expect(await screen.findByText(/Someone else saved this radar in the meantime/)).toBeInTheDocument(); // a 409: read again (r3 F3-042)
  });

  it('shows every kind of change, no change and failed diffs', async () => {
    const changes = { added: [], removed: [{ key: 'k', title: 'Gone', sector: 'Direct', ring: 'Leader', description: '' }], moved: [], recategorised: [{ key: 'k', title: 'R', from: 'Direct', to: 'Adjacent' }], marker_changed: [{ key: 'k', title: 'M', from: 'Gaining', to: null }], edited: [] };
    const none = { added: [], removed: [], moved: [], recategorised: [], marker_changed: [], edited: [] };
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, current_version: 4 } }),
      'GET /api/radars/rivals/versions': { items: [4, 3, 2].map((version) => ({ version, created_at: '2026-10-02T10:00:00Z', created_by: 'u', entry_count: 1, note: '', schema_changed: version === 3 })) },
      'GET /api/radars/rivals/diff': (_: unknown, url: URL) => {
        const to = url.searchParams.get('to');
        if (to === '2') throw { status: 500, message: 'diff failed' };
        return { from: 1, to: Number(to), changes: to === '4' ? changes : none };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
    expect(await screen.findByText('Schema changed')).toBeInTheDocument();
    const buttons = () => screen.getAllByRole('button', { name: 'Changes' });
    fireEvent.click(buttons()[0]!);
    expect(await screen.findByText('Removed Gone')).toBeInTheDocument();
    // F2-046: each line names the dimension that moved, so a ring change cannot read as a sector change.
    expect(screen.getByText('R, category: Direct \u2192 Adjacent')).toBeInTheDocument();
    expect(screen.getByText('M, momentum: Gaining \u2192 -')).toBeInTheDocument();
    // CTR HU-080: the version lists filter as you type, and say when and by whom each was saved.
    const from = visibleModal().findContent().findSelect()!;
    from.openDropdown();
    from.findFilteringInput({ expandToViewport: true })!.setInputValue('3');
    const shown = from.findDropdown({ expandToViewport: true }).findOptions().map((o) => o.getElement().textContent);
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatch(/^Version 3/);
    visibleModal().findDismissButton().click();
    await waitFor(() => expect(visibleModal()).toBeUndefined());
    fireEvent.click(buttons()[1]!);
    expect(await screen.findByText('No differences.')).toBeInTheDocument();
    visibleModal().findDismissButton().click();
    fireEvent.click(buttons()[2]!);
    expect(await screen.findByText('diff failed')).toBeInTheDocument();
  });

  it('renames, removes editors and shows an owner without an email', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ radar: { ...radarView().radar, owner_email: '', owner: 'u-owner' } }),
      'PATCH /api/radars/rivals': radarView().radar,
      'PUT /api/radars/rivals/access': radarView().radar,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    const w = createWrapper(container);
    w.findTabs()!.findTabLinkById('settings')!.click();
    expect(await screen.findByText('Owner: u-owner')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('Rivals'), { target: { value: 'Competitors' } });
    fireEvent.change(screen.getByDisplayValue('Who competes with us'), { target: { value: 'Mid-market rivals' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ name: 'Competitors', purpose: 'Mid-market rivals' }));
    w.findTokenGroup()!.findToken(1)!.findDismiss().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save access' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/access'))?.body).toEqual({ editors: [], version_lock: 3 }));
  });
});

describe('RadarPage unsaved edits', () => {
  const unload = () => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  };

  it('keeps entry and settings edits across tab switches, warns before unload, and resets after saving', async () => {
    let version = 2;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'PUT /api/radars/rivals/entries': () => { version = 3; return radarView().radar; },
      'PATCH /api/radars/rivals': radarView().radar,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    const tabs = () => createWrapper(container).findTabs()!;
    expect(unload()).toBe(false);
    tabs().findTabLinkById('edit')!.click();
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    expect(unload()).toBe(true);
    tabs().findTabLinkById('view')!.click();
    await screen.findByRole('group', { name: 'Rivals radar' });
    tabs().findTabLinkById('edit')!.click();
    expect(await screen.findByDisplayValue('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText(/^1 unsaved change:/)).toBeInTheDocument();
    tabs().findTabLinkById('settings')!.click();
    fireEvent.change(await screen.findByDisplayValue('Rivals'), { target: { value: 'Competitors' } });
    fireEvent.change(screen.getByDisplayValue(/Leader/), { target: { value: 'Leader\nChallenger\nNiche' } });
    tabs().findTabLinkById('history')!.click();
    tabs().findTabLinkById('settings')!.click();
    expect(await screen.findByDisplayValue('Competitors')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/Niche/)).toBeInTheDocument();
    tabs().findTabLinkById('edit')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Save version' }));
    await waitFor(() => expect(screen.getByText('Version 3', { exact: false })).toBeInTheDocument());
    expect(screen.getByDisplayValue('Acme')).toBeInTheDocument(); // the new version's saved copy
    expect(unload()).toBe(true); // Settings still has unsaved fields
    tabs().findTabLinkById('settings')!.click();
    fireEvent.click((await screen.findAllByRole('button', { name: 'Save general' }))[0]!);
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ name: 'Competitors' }));
    expect(await screen.findByDisplayValue('Rivals')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/Niche/)).toBeInTheDocument(); // the unsaved schema section is kept
    fireEvent.change(screen.getByDisplayValue(/Niche/), { target: { value: 'Leader\nChallenger' } });
    expect(unload()).toBe(false);
  });

  it('starts Settings afresh for another radar', async () => {
    function Go() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate('/radars/other')}>other radar</button>;
    }
    const other = radarView({ radar: { ...radarView().radar, slug: 'other', name: 'Other' } });
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView(), 'GET /api/radars/other': other });
    const { container } = render(wrap(<>{routes}<Go /></>, { api, path: '/radars/rivals' }));
    await screen.findByRole('group', { name: 'Rivals radar' });
    createWrapper(container).findTabs()!.findTabLinkById('settings')!.click();
    fireEvent.change(await screen.findByDisplayValue('Rivals'), { target: { value: 'Competitors' } });
    fireEvent.click(screen.getByRole('button', { name: 'other radar' }));
    expect(await screen.findByText('Leave without saving?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stay on this page' }));
    expect(screen.getByDisplayValue('Competitors')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'other radar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Leave and lose changes' }));
    await screen.findByRole('group', { name: 'Other radar' });
    createWrapper(container).findTabs()!.findTabLinkById('settings')!.click();
    expect(await screen.findByDisplayValue('Other')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('Other'), { target: { value: 'Others' } });
    expect(screen.getByDisplayValue('Others')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Who competes with us')).toBeInTheDocument();
  });
});

describe('RadarsPage creation', () => {
  it('handles unknown and empty templates, custom addresses, failures and cancelling', async () => {
    const empty = { ...SCHEMA, sectors: [], rings: [], markers: [] };
    const { api, calls } = fakeApi({
      'GET /api/radars': { items: [radarView().radar] },
      'GET /api/radar-templates': { items: [{ key: 'competitor_landscape', purpose: 'Competitors', schema: SCHEMA }, { key: 'partner_map', purpose: 'Partners', schema: empty }] },
      'POST /api/radars': () => { throw { status: 409, message: 'address taken' }; },
    });
    render(wrap(<Routes><Route path="/radars" element={<RadarsPage />} /><Route path="/radars/:slug" element={<div>opened</div>} /></Routes>, { api, path: '/radars' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create radar' }));
    const modal = visibleModal();
    const inputs = document.querySelectorAll('[role="dialog"] input');
    fireEvent.change(inputs[0]!, { target: { value: 'Partners' } });
    fireEvent.change(inputs[1]!, { target: { value: 'My Partners!' } });
    expect(screen.getByDisplayValue('my-partners-')).toBeInTheDocument();
    fireEvent.blur(inputs[1]!);
    expect(screen.getByDisplayValue('my-partners')).toBeInTheDocument();
    await waitFor(() => expect(area('Sectors')).toBeDefined());
    const select = modal.findContent().findSelect()!;
    select.openDropdown();
    select.selectOptionByValue('partner_map', { expandToViewport: true });
    expect(select.findTrigger().getElement()).toHaveTextContent('partner_map');
    await waitFor(() => expect(screen.getAllByText('Add at least one, one per line.')).toHaveLength(2));
    expect(screen.getByRole('button', { name: 'Create' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Fix the lists to update the preview.')).toBeInTheDocument();
    select.openDropdown();
    select.selectOptionByValue('competitor_landscape', { expandToViewport: true });
    await waitFor(() => expect(screen.queryByText('Add at least one, one per line.')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('address taken')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ slug: 'my-partners', name: 'Partners', template: 'competitor_landscape' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByText('Discard this radar?')).toBeInTheDocument(); // filled in: it asks first
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    visibleModal().findDismissButton().click();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(visibleModal()).toBeUndefined());
    fireEvent.click(screen.getByRole('link', { name: 'Rivals' }));
    expect(await screen.findByText('opened')).toBeInTheDocument();
  });
});
