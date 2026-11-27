// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fakeApi, wrap } from '@/test/session';

vi.mock('@/pages/GraphPage', () => ({
  default: () => {
    throw new Error('graph exploded');
  },
}));
vi.mock('@/pages/RadarsPage', () => ({ default: () => <div>radars list</div> }));

import { AppRoutes } from './App';

afterEach(() => vi.restoreAllMocks());

describe('page error boundary', () => {
  it('clears when the user navigates to another page', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<AppRoutes />, { api, path: '/graph' }));
    expect(await screen.findByText('graph exploded')).toBeInTheDocument();
    act(() => createWrapper(document.body).findSideNavigation()!.findLinkByHref('/radars')!.click());
    expect(await screen.findByText('radars list')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong on this page')).toBeNull();
  });
});
