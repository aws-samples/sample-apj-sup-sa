// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 frontend review: Settings saves (CR2-F05 auto-apply confirmation, CR2-F10 transfer). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarMode } from '@/api/types';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage from './RadarPage';

const routes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>;
afterEach(() => vi.restoreAllMocks());

const daysAgo = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();
const suggestion = (key: string, age: number) => ({
  entry_key: key,
  is_new: true,
  proposal: { title: `Proposal ${key}` },
  base: null,
  reason: 'New evidence',
  evidence: [],
  created_at: daysAgo(age),
  created_by: 'module:curator',
});
const radar = (mode: RadarMode, over: object = {}) =>
  radarView({ radar: { ...radarView().radar, mode, auto_apply_days: 30, auto_apply_agent: false, ...over } });

describe('CR2-F05: the auto-apply confirmation counts what will really apply', () => {
  it('reads the proposals a radar holds while suggestions are off before turning autopilot on', async () => {
    const twelve = Array.from({ length: 12 }, (_, i) => suggestion(`k${i}`, 40));
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radar('manual'),
      'GET /api/radars/rivals/suggestions': { items: twelve },
      'PATCH /api/radars/rivals': radar('autopilot', { auto_apply_agent: true }).radar,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    await screen.findByText('General');
    expect(calls.some((c) => c.path.endsWith('/suggestions'))).toBe(false); // the page does not list them while off
    const select = createWrapper(container).findSelect()!;
    select.openDropdown();
    select.selectOptionByValue('autopilot', { expandToViewport: true });
    createWrapper(container).findAllToggles()[0]!.findNativeInput().click();
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    expect(await screen.findByText('Apply 12 proposals without review?')).toBeInTheDocument();
    expect(screen.getByText(/already past the 30-day delay/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  });

  it('counts the proposals a shorter delay lets in, with the new delay', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radar('autopilot', { auto_apply_agent: true }),
      'GET /api/radars/rivals/suggestions': { items: [suggestion('old', 40), suggestion('mid', 5), suggestion('new', 0)] },
      'PATCH /api/radars/rivals': radar('autopilot', { auto_apply_agent: true, auto_apply_days: 1 }).radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByLabelText('Apply after (days)'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    // "old" is past the 30 days already, so this save does not change what happens to it.
    expect(await screen.findByText('Apply 1 proposal without review?')).toBeInTheDocument();
    expect(screen.getByText('Proposal mid')).toBeInTheDocument();
    expect(screen.getByText(/already past the 1-day delay/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save and apply 1 proposal' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ auto_apply_days: 1 }));
  });

  it('does not ask when the delay grows, and reports a list that cannot be read', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radar('autopilot', { auto_apply_agent: true }),
      'GET /api/radars/rivals/suggestions': () => {
        throw { status: 500, message: 'suggestions are unavailable' };
      },
      'PATCH /api/radars/rivals': radar('autopilot', { auto_apply_agent: true }).radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    const days = await screen.findByLabelText('Apply after (days)');
    fireEvent.change(days, { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    expect(await screen.findByText('suggestions are unavailable')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    fireEvent.change(days, { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save general' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ auto_apply_days: 45 }));
  });
});
