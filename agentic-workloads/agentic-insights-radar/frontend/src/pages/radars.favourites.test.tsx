// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import RadarPage from './RadarPage';
import RadarsPage from './RadarsPage';

const rivals = { ...radarView().radar, can_edit: false };
const vendors = { ...radarView().radar, slug: 'vendors', name: 'Vendors', can_edit: false };

describe('favourite radars (CTR sec. 9.3)', () => {
  it('stars a radar, lists favourites first, and shows them on their own', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars': { items: [rivals, vendors] },
      'GET /api/me/favourites': { radars: [] },
      'PUT /api/me/favourites/radars/vendors': { slug: 'vendors', favourite: true },
      'DELETE /api/me/favourites/radars/vendors': { slug: 'vendors', favourite: false },
    });
    render(wrap(<RadarsPage />, { api }));
    const star = await screen.findByRole('button', { name: 'Add Vendors to favourites' });
    await waitFor(() => expect(star).not.toBeDisabled());
    fireEvent.click(star);
    expect(await screen.findByRole('button', { name: 'Remove Vendors from favourites' })).toBeInTheDocument();
    const names = () => createWrapper(document.body).findTable()!.findRows().map((r) => r.getElement().textContent ?? '');
    expect(names()[0]).toContain('Vendors'); // favourites first
    const only = createWrapper(document.body).findAllToggles().find((t) => t.getElement().textContent?.includes('Only favourites'))!;
    only.findNativeInput().click();
    await waitFor(() => expect(names()).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Vendors from favourites' }));
    expect(await screen.findByText(/No favourites yet/)).toBeInTheDocument();
    only.findNativeInput().click(); // every radar again
    await waitFor(() => expect(names()).toHaveLength(2));
    expect(calls.filter((c) => c.path.startsWith('/api/me/favourites/')).map((c) => c.method)).toEqual(['PUT', 'DELETE']);
  });

  it('puts the star back when the server refuses, on the radar page too', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/me/favourites': { radars: ['rivals'] },
      'DELETE /api/me/favourites/radars/rivals': () => {
        throw { status: 500, code: 'internal', message: 'try later' };
      },
    });
    render(wrap(<Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>, { api, path: '/radars/rivals' }));
    const star = await screen.findByRole('button', { name: 'Remove Rivals from favourites' });
    await waitFor(() => expect(star).not.toBeDisabled());
    fireEvent.click(star);
    expect(await screen.findByText('try later')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Rivals from favourites' })).toBeInTheDocument();
  });
});
