// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about the shell's banners on a phone (F3-030). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { TestSessionProvider } from '@/auth/session';
import { ADMIN, CONFIG, fakeApi, sessionValue, TestRouter } from '@/test/session';

import AppShell from './AppShell';
import { useNotify } from './Notifications';

function Page() {
  const { notify } = useNotify();
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => notify('success', <>Draft requested. <a href="/x">Open it</a></>, 'draft')}>raise</button>
      <button onClick={() => notify('error', <>Broken <a href="/y">details</a></>, 'broken')}>fail</button>
      <button onClick={() => notify('info', 'Plain note')}>plain</button>
      <button onClick={() => void navigate('/other')}>go</button>
    </>
  );
}

function Other() {
  const navigate = useNavigate();
  return (
    <>
      <div>other page</div>
      <button onClick={() => void navigate('/')}>home</button>
    </>
  );
}

describe('F3-030: the sticky banners can be put away', () => {
  it('lets the paused and profile banners be dismissed, and drops link toasts when the page changes', async () => {
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    document.body.classList.add('awsui-motion-disabled'); // jsdom never ends the Flashbar's exit animation
    render(
      <TestSessionProvider value={sessionValue({ api, me: ADMIN, meError: new Error('busy'), config: { ...CONFIG, genai_enabled: false } })}>
        <TestRouter path="/">
          <AppShell>
            <Routes>
              <Route path="/" element={<Page />} />
              <Route path="/other" element={<Other />} />
            </Routes>
          </AppShell>
        </TestRouter>
      </TestSessionProvider>,
    );
    const alerts = () => createWrapper(document.body).findAllAlerts().map((a) => a.findHeader()?.getElement().textContent);
    expect(alerts()).toEqual(expect.arrayContaining(['AI features are paused', 'Your profile could not be read again']));
    for (const header of ['AI features are paused', 'Your profile could not be read again']) {
      createWrapper(document.body).findAllAlerts().find((a) => a.findHeader()?.getElement().textContent === header)!.findDismissButton()!.click();
    }
    await waitFor(() => expect(alerts()).not.toContain('AI features are paused'));
    expect(alerts()).not.toContain('Your profile could not be read again');
    fireEvent.click(screen.getByText('plain'));
    fireEvent.click(screen.getByText('raise'));
    expect(await screen.findByText(/Draft requested/)).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByText('go')));
    expect(await screen.findByText('other page')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Draft requested/)).toBeNull());
    expect(screen.getByText('Plain note')).toBeInTheDocument(); // it times out by itself
    await act(async () => fireEvent.click(screen.getByText('home')));
    fireEvent.click(await screen.findByText('fail'));
    expect(await screen.findByText(/Broken/)).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByText('go')));
    expect(await screen.findByText('other page')).toBeInTheDocument();
    expect(screen.getByText(/Broken/)).toBeInTheDocument(); // errors stay until dismissed
    document.body.classList.remove('awsui-motion-disabled');
    vi.restoreAllMocks();
  });
});
