// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-008, F2-010, F2-011: schema rename pairing is a choice, not a guess applied silently. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Dimension, RadarEntry } from '@/api/types';
import RadarPage from '@/pages/RadarPage';
import { clearedOf, dimensionsOf, planDimension, renamesOf } from '@/radar/RadarTabs';
import { radarView, SCHEMA } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const dims = (...names: string[]): Dimension[] => names.map((name) => ({ name }));
/** The schema list textarea named by its FormField label (the radar's own heading). */
const setList = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

afterEach(() => vi.restoreAllMocks());

describe('F2-008: a rename is only guessed where a value kept its place', () => {
  it('does not turn an unrelated delete plus an add into a rename', () => {
    // Adopt/Trial/Assess/Hold -> Adopt/Pilot/Hold/Explore: only Trial kept its position.
    const plan = planDimension(dims('Adopt', 'Trial', 'Assess', 'Hold'), 'Adopt\nPilot\nHold\nExplore');
    expect(plan.removed).toEqual(['Trial', 'Assess']);
    expect(plan.added).toEqual(['Pilot', 'Explore']);
    expect(renamesOf(plan)).toEqual({ Trial: 'Pilot' });
    expect(plan.choices['Assess']).toEqual({ action: 'remove' });
  });

  it('never crosses two renames over when the order changed', () => {
    // A/B/C -> C/B2/A2: pairing by list order gave {A: B2, B: A2}, swapping the entries.
    const plan = planDimension(dims('A', 'B', 'C'), 'C\nB2\nA2');
    expect(renamesOf(plan)).toEqual({ B: 'B2' });
    expect(plan.choices['A']).toEqual({ action: 'remove' });
  });

  it('still pairs a plain rename, keeping the colour and glyph', () => {
    const prev: Dimension[] = [{ name: 'Direct', color: '#123456', glyph: 'triangle' }];
    const plan = planDimension(prev, 'Direct rivals');
    expect(renamesOf(plan)).toEqual({ Direct: 'Direct rivals' });
    expect(dimensionsOf(prev, plan)).toEqual([{ name: 'Direct rivals', color: '#123456', glyph: 'triangle' }]);
  });

  it('pairs a rename even when another value was removed as well (F2-010)', () => {
    // Leader/Challenger/Niche -> Leaders/Challenger: no rename was inferred at all before.
    const plan = planDimension(dims('Leader', 'Challenger', 'Niche'), 'Leaders\nChallenger');
    expect(renamesOf(plan)).toEqual({ Leader: 'Leaders' });
    expect(plan.choices['Niche']).toEqual({ action: 'remove' });
  });

  it('keeps the entries of a lost marker by default (F2-011)', () => {
    const plan = planDimension(dims('MVP', 'Scale'), 'Scale', true);
    expect(plan.choices['MVP']).toEqual({ action: 'clear' });
    expect(clearedOf(plan)).toEqual(['MVP']);
    expect(renamesOf(plan)).toEqual({});
  });
});

describe('F2-008: the confirm dialog lets each choice be changed', () => {
  const schema = { ...SCHEMA, rings: dims('Adopt', 'Trial', 'Assess', 'Hold') };
  const entries: RadarEntry[] = [
    { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Trial', description: '' },
    { key: 'k2', title: 'Beta', sector: 'Direct', ring: 'Assess', description: '' },
  ];

  it('changes a guessed rename into a removal, and a removal into a rename', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema, entries }),
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Position')).toBeInTheDocument());
    setList('Position', 'Adopt\nPilot\nHold\nExplore');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    // r3 F3-093, F3-112: each value says how many entries carry it, and an untouched choice is a guess.
    expect(await screen.findByText('"Trial" (1 entry): Rename it to "Pilot" (guessed: check it)')).toBeInTheDocument();
    expect(screen.getByText('"Assess" (1 entry): Remove it, and its entries with it (guessed: check it)')).toBeInTheDocument();
    expect(screen.getByText('1 entry would be removed')).toBeInTheDocument();
    // Pair Assess with Explore instead: nothing is removed any more.
    const selects = createWrapper(document.body).findAllSelects();
    const assess = selects.find((x) => x.findTrigger().getElement().textContent?.startsWith('"Assess"'))!;
    assess.openDropdown();
    assess.selectOptionByValue('rename:Explore', { expandToViewport: true });
    expect(await screen.findByText('Every entry still matches the new schema.')).toBeInTheDocument();
    // And drop the guessed Trial -> Pilot rename.
    const trial = createWrapper(document.body).findAllSelects().find((x) => x.findTrigger().getElement().textContent?.startsWith('"Trial"'))!;
    trial.openDropdown();
    trial.selectOptionByValue('remove', { expandToViewport: true });
    expect(await screen.findByText('1 entry would be removed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save and remove 1 entry' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/schema'))?.body).toMatchObject({ renames: { ring: { Assess: 'Explore' } }, drop_unmatched: true }));
  });
});

describe('F2-011: a lost marker can clear instead of removing the entries', () => {
  const schema = { ...SCHEMA, markers: dims('Gaining', 'MVP') };
  const entries: RadarEntry[] = [
    { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', marker: 'MVP', description: '' },
    { key: 'k2', title: 'Beta', sector: 'Direct', ring: 'Leader', marker: 'Gaining', description: '' },
  ];

  it('saves the cleared entries first, then the schema', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema, entries, radar: { ...radarView().radar, schema, current_version: 2 } }),
      'PUT /api/radars/rivals/entries': { ...radarView().radar, current_version: 3 },
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Momentum')).toBeInTheDocument());
    setList('Momentum', 'Gaining');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    expect(await screen.findByText('"MVP" (1 entry): Keep its entries without a momentum (guessed: check it)')).toBeInTheDocument();
    expect(screen.getByText(/1 entry keeps everything but its momentum/)).toBeInTheDocument();
    expect(screen.getByText('Every entry still matches the new schema.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/schema'))).toBe(true));
    const saved = calls.find((c) => c.path.endsWith('/entries'))!.body as { entries: RadarEntry[]; expected_version: number; note: string };
    expect(saved.entries.map((e) => e.marker)).toEqual([undefined, 'Gaining']);
    expect(saved.expected_version).toBe(2);
    expect(saved.note).toBe('cleared the momentum on 1 entry');
    // The schema change then runs against the version the entry save created.
    expect(calls.find((c) => c.path.endsWith('/schema'))!.body).toMatchObject({ expected_version: 3, drop_unmatched: false });
  });

  it('says "their" when more than one entry carries the lost marker', async () => {
    const two: RadarEntry[] = [
      { key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', marker: 'MVP', description: '' },
      { key: 'k2', title: 'Beta', sector: 'Direct', ring: 'Leader', marker: 'MVP', description: '' },
    ];
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ schema, entries: two, radar: { ...radarView().radar, schema } }) });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Momentum')).toBeInTheDocument());
    setList('Momentum', 'Gaining');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    expect(await screen.findByText(/2 entries keep everything but their momentum/)).toBeInTheDocument();
  });

  it('removes the entries when the user asks for that instead', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema, entries, radar: { ...radarView().radar, schema } }),
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Momentum')).toBeInTheDocument());
    setList('Momentum', 'Gaining');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    const mvp = (await waitFor(() => createWrapper(document.body).findAllSelects())).find((x) => x.findTrigger().getElement().textContent?.startsWith('"MVP"'))!;
    mvp.openDropdown();
    mvp.selectOptionByValue('remove', { expandToViewport: true });
    expect(await screen.findByText('1 entry would be removed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save and remove 1 entry' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/schema'))?.body).toMatchObject({ drop_unmatched: true }));
    expect(calls.some((c) => c.path.endsWith('/entries'))).toBe(false);
  });

  it('reports a failed entry save and makes no schema change', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ schema, entries, radar: { ...radarView().radar, schema } }),
      'PUT /api/radars/rivals/entries': () => {
        throw { status: 409, message: 'someone else saved this radar' };
      },
      'PUT /api/radars/rivals/schema': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Momentum')).toBeInTheDocument());
    setList('Momentum', 'Gaining');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    await screen.findByText(/Keep its entries without a momentum/);
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    expect(await screen.findByText(/Someone else changed this radar in the meantime/)).toBeInTheDocument(); // read again (r3 F3-013)
    expect(calls.some((c) => c.path.endsWith('/schema'))).toBe(false);
  });

  it('reloads and says what was saved when the entries save but the schema does not (CR2-F04)', async () => {
    let version = 2;
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ schema, entries, version, radar: { ...radarView().radar, schema, current_version: version } }),
      'PUT /api/radars/rivals/entries': () => {
        version = 3;
        return { ...radarView().radar, current_version: 3 };
      },
      'PUT /api/radars/rivals/schema': () => {
        throw { status: 409, message: 'someone else saved this radar' };
      },
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await waitFor(() => expect(screen.getByLabelText('Momentum')).toBeInTheDocument());
    setList('Momentum', 'Gaining');
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }));
    await screen.findByText(/Keep its entries without a momentum/);
    const loads = calls.filter((c) => c.method === 'GET' && c.path === '/api/radars/rivals').length;
    fireEvent.click(screen.getAllByRole('button', { name: 'Save schema' }).pop()!);
    expect(
      await screen.findByText(
        'Cleared the momentum on 1 entry and saved that as version 3, but the schema change was not saved: someone else saved this radar The schema fields still hold your change: press Save schema to try again.',
      ),
    ).toBeInTheDocument();
    // The page moved on to version 3, so trying again is not another conflict.
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET' && c.path === '/api/radars/rivals').length).toBeGreaterThan(loads));
    expect(await screen.findByText(/^Version 3/)).toBeInTheDocument();
    expect(screen.getByLabelText('Momentum')).toHaveValue('Gaining');
  });
});
