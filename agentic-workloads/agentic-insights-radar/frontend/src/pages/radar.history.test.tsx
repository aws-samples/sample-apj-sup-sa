// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { fireEvent, render, screen, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { EntryEvent } from '@/api/types';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage, { eventText } from './RadarPage';

const openAcme = async () => {
  await screen.findByRole('group', { name: 'Rivals radar' });
  fireEvent.click(within(screen.getByRole('region', { name: 'Legend entries' })).getByRole('button', { name: /^1\. Acme(,|$)/ }));
};
const page = <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>;

const ev = (over: Partial<EntryEvent>): EntryEvent => ({
  version: 2, at: '2026-10-20T00:00:00Z', by: 'user:u1', note: 'saved', ai: false, change: 'changed',
  title: 'Linak', sector: 'Direct', ring: 'Leader', ...over,
});

describe("an entry's history (CTR HA-003)", () => {
  it('says each change in words', () => {
    expect(eventText(ev({ change: 'added' }))).toBe('Added in Direct, Leader');
    expect(eventText(ev({ change: 'restored' }))).toBe('Put back in Direct, Leader');
    expect(eventText(ev({ change: 'removed' }))).toBe('Removed');
    expect(eventText(ev({ fields: ['title'] }))).toBe('Name changed');
    expect(eventText(ev({ ring_from: 'Niche', sector_from: 'Adjacent', fields: ['description', 'node_id', 'other'] })))
      .toBe('Moved from Niche to Leader; moved from Adjacent to Direct; description, linked record, other changed');
  });

  it('opens from the entry panel, newest first, and marks AI changes', async () => {
    const view = radarView();
    const key = view.entries[0]!.key;
    const { api } = fakeApi({
      'GET /api/radars/rivals': view,
      [`GET /api/radars/rivals/entries/${key}/history`]: {
        key, from_version: 3, to_version: 52, current: true,
        events: [ev({ version: 3, change: 'added' }), ev({ version: 9, ring_from: 'Niche', ai: true, by: 'module:curator', at: null })],
      },
    });
    render(wrap(page, { api, path: '/radars/rivals' }));
    await openAcme();
    fireEvent.click(await screen.findByText('Entry history'));
    expect(await screen.findByText('From version 3 (the newest 50 versions).')).toBeInTheDocument();
    const items = screen.getAllByRole('listitem').filter((li) => li.textContent?.startsWith('Version'));
    expect(items[0]).toHaveTextContent('Version 9');
    expect(items[0]).toHaveTextContent('AI suggestion');
    expect(items[1]).toHaveTextContent('Added in Direct, Leader');
  });

  it('says when the history cannot be loaded', async () => {
    const view = radarView();
    const { api } = fakeApi({ 'GET /api/radars/rivals': view });
    render(wrap(page, { api, path: '/radars/rivals' }));
    await openAcme();
    fireEvent.click(await screen.findByText('Entry history'));
    expect(await screen.findByText(/The history could not be loaded/)).toBeInTheDocument();
  });
});
