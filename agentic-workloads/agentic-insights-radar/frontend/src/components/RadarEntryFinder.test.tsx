// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { RadarEntryHit } from '@/api/types';
import { fakeApi, wrap } from '@/test/session';

import RadarEntryFinder, { OnRadars } from './RadarEntryFinder';

const hit = (over: Partial<RadarEntryHit> = {}): RadarEntryHit => ({
  radar: 'rivals', radar_name: 'Rivals', version: 3, key: 'k1', title: 'Linak', sector: 'Direct', ring: 'Leader', ...over,
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}

describe('finding an entry across radars (CTR HA-076)', () => {
  it('finds where something is placed and opens that radar filtered to it', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/entries': { items: [hit(), hit({ radar: 'vendors', radar_name: 'Vendors', key: 'v1', sector: 'Tools', sub_sector: 'Desks', ring: 'Trial' })] },
    });
    render(wrap(<><RadarEntryFinder /><Routes><Route path="*" element={<Where />} /></Routes></>, { api }));
    expect(screen.getByRole('button', { name: 'Find' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByLabelText("An entry's name"), { target: { value: ' linak ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    expect(await screen.findByText('Tools / Desks')).toBeInTheDocument();
    expect(screen.getByText('Rivals')).toBeInTheDocument();
    expect(calls.at(-1)?.path).toBe('/api/radars/entries');
    fireEvent.click(screen.getAllByRole('link', { name: 'Linak' })[0]!);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/radars/rivals?q=Linak'));
  });

  it('says when no radar places it, and reports a failure', async () => {
    let fail = false;
    const { api } = fakeApi({
      'GET /api/radars/entries': () => {
        if (fail) throw { status: 500, code: 'internal', message: 'radars are down' };
        return { items: [] };
      },
    });
    render(wrap(<RadarEntryFinder />, { api }));
    fireEvent.change(screen.getByLabelText("An entry's name"), { target: { value: 'nothing' } });
    fireEvent.submit(screen.getByLabelText("An entry's name").closest('form')!);
    expect(await screen.findByText('No radar places it.')).toBeInTheDocument();
    fail = true;
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    expect(await screen.findByText('radars are down')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("An entry's name"), { target: { value: 'x' } });
    fireEvent.submit(screen.getByLabelText("An entry's name").closest('form')!); // too short: nothing is asked
  });

  it('lists the radars that place a node, and nothing when none do', async () => {
    const placed = fakeApi({ 'GET /api/radars/entries': { items: [hit()] } });
    const first = render(wrap(<OnRadars nodeId="organization/1" />, { api: placed.api }));
    expect(await screen.findByText('On radars')).toBeInTheDocument();
    first.unmount();
    const none = fakeApi({ 'GET /api/radars/entries': { items: [] } });
    const { container } = render(wrap(<OnRadars nodeId="organization/1" />, { api: none.api }));
    await waitFor(() => expect(none.calls.length).toBe(1));
    expect(container.textContent).not.toContain('On radars');
  });
});
