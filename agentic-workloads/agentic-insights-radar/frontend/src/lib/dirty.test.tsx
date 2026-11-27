// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-009: the shared unsaved-changes registry both exits from the app read. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from '@/components/AppShell';
import RadarPage from '@/pages/RadarPage';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import { dirtyPages, useAnyDirty, useReportDirty } from './dirty';

afterEach(() => vi.restoreAllMocks());

/** The account menu in the top navigation, which holds Sign out. */
const accountMenu = () => createWrapper(document.body).findTopNavigation()!.findUtility(2)!.findMenuDropdownType()!;

function Probe({ dirty }: { dirty: boolean }) {
  useReportDirty(dirty);
  return <div>{useAnyDirty() ? 'dirty' : 'clean'}</div>;
}

describe('the dirty-page registry', () => {
  it('reports any mounted page with unsaved changes, and forgets it on unmount', async () => {
    const first = render(<Probe dirty={false} />);
    expect(screen.getByText('clean')).toBeInTheDocument();
    first.rerender(<Probe dirty />);
    await waitFor(() => expect(screen.getByText('dirty')).toBeInTheDocument());
    const second = render(<Probe dirty />);
    await waitFor(() => expect(screen.getAllByText('dirty')).toHaveLength(2));
    second.unmount();
    await waitFor(() => expect(screen.getByText('dirty')).toBeInTheDocument()); // the first one still is
    first.unmount();
    expect(dirtyPages.snapshot()).toBe(false);
  });

  it('notifies a listener only when the answer changes', () => {
    const listener = vi.fn();
    const stop = dirtyPages.subscribe(listener);
    dirtyPages.set('a', true);
    dirtyPages.set('b', true);
    expect(listener).toHaveBeenCalledTimes(1);
    dirtyPages.set('a', false);
    dirtyPages.set('b', false);
    expect(listener).toHaveBeenCalledTimes(2);
    stop();
    dirtyPages.set('a', true);
    expect(listener).toHaveBeenCalledTimes(2);
    dirtyPages.set('a', false);
  });
});

const routes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);

describe('F2-009: Sign out asks about unsaved changes', () => {
  it('signs out straight away when nothing is unsaved', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    const signOut = vi.fn(async () => undefined);
    render(wrap(<AppShell><div>page</div></AppShell>, { api, signOut }));
    accountMenu().openDropdown();
    accountMenu().findItemById('signout')!.click();
    await waitFor(() => expect(signOut).toHaveBeenCalled());
  });

  it('asks first while a page holds unsaved entry edits, and can be cancelled', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/radars/rivals': radarView() });
    const signOut = vi.fn(async () => undefined);
    render(wrap(<AppShell>{routes}</AppShell>, { api, path: '/radars/rivals?tab=edit', signOut }));
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    accountMenu().openDropdown();
    accountMenu().findItemById('signout')!.click();
    expect(await screen.findByText('Sign out without saving?')).toBeInTheDocument();
    expect(signOut).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Stay signed in' }));
    await waitFor(() => expect(screen.queryByText('Sign out without saving?')).toBeNull());
    expect(screen.getByDisplayValue('Acme Corp')).toBeInTheDocument();
    accountMenu().openDropdown();
    accountMenu().findItemById('signout')!.click();
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out and lose changes' }));
    await waitFor(() => expect(signOut).toHaveBeenCalled());
  });

  it('can be dismissed', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/radars/rivals': radarView() });
    const signOut = vi.fn(async () => undefined);
    render(wrap(<AppShell>{routes}</AppShell>, { api, path: '/radars/rivals?tab=edit', signOut }));
    fireEvent.change((await screen.findAllByLabelText(/^Title of /))[0]!, { target: { value: 'Acme Corp' } });
    accountMenu().openDropdown();
    accountMenu().findItemById('signout')!.click();
    expect(await screen.findByText('Sign out without saving?')).toBeInTheDocument();
    createWrapper(document.body).findAllModals().find((m) => m.isVisible())!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByText('Sign out without saving?')).toBeNull());
    expect(signOut).not.toHaveBeenCalled();
  });
});
