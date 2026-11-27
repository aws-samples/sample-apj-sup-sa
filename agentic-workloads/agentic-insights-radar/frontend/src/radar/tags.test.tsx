// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import RadarPage from '@/pages/RadarPage';
import { radarView } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

afterEach(() => vi.restoreAllMocks());

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const routes = (
  <>
    <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>
    <Routes><Route path="*" element={<Where />} /></Routes>
  </>
);
const versions = { items: [2, 1].map((version) => ({ version, created_at: '2026-10-01T10:00:00Z', created_by: 'user:u-admin', entry_count: 1, note: '' })) };
const openHistory = async (container: HTMLElement) => {
  await screen.findByRole('group', { name: 'Rivals radar' });
  createWrapper(container).findTabs()!.findTabLinkById('history')!.click();
  await screen.findByRole('button', { name: 'Copy a link to version 1' });
};

describe('version tags and links (CTR HA-007/008/009)', () => {
  it('tags a version, says why a look-alike is refused, removes a tag and copies a link', async () => {
    let tagged = false;
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
      'GET /api/radars/rivals/tags': () => ({ items: tagged ? [{ tag: 'Q3 2026', version: 1, by: 'u', at: null }] : [] }),
      'PUT /api/radars/rivals/versions/1/tag': (b: unknown) => {
        if ((b as { tag: string }).tag === 'q3-2026') throw { status: 409, code: 'conflict', message: "the tag 'Q3 2026' (version 2) reads the same; choose another name" };
        tagged = true;
        return { tag: 'Q3 2026', version: 1 };
      },
      'DELETE /api/radars/rivals/tags/Q3%202026': () => {
        tagged = false;
        return { deleted: true };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openHistory(container);
    fireEvent.click(screen.getByRole('button', { name: 'Tag version 1' }));
    const input = createWrapper(document.body).findModal()!.findContent().findInput()!;
    input.setInputValue('q3-2026');
    fireEvent.click(screen.getByRole('button', { name: 'Tag' }));
    expect(await screen.findByText(/reads the same; choose another name/)).toBeInTheDocument();
    input.setInputValue('Q3 2026');
    fireEvent.keyDown(input.findNativeInput().getElement(), { key: 'Enter', keyCode: 13 });
    expect(await screen.findByText('Version 1 is tagged Q3 2026.')).toBeInTheDocument();
    const remove = await screen.findByRole('button', { name: 'Remove the tag Q3 2026' });
    fireEvent.click(screen.getByRole('button', { name: 'Copy a link to version 1' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/radars/rivals?tag=Q3%202026`));
    fireEvent.click(remove);
    expect(await screen.findByText('The tag Q3 2026 was removed.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Copy a link to version 2' }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(`${window.location.origin}/radars/rivals?v=2`));
    expect(calls.filter((c) => c.method === 'PUT').length).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'Tag version 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Tag version 2' }));
    createWrapper(document.body).findModal()!.findDismissButton().click();
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
  });

  it('opens an old version read-only, and goes back to the current one', async () => {
    const old = radarView({ version: 1 });
    const { api } = fakeApi({
      'GET /api/radars/rivals': (_b: unknown, url: URL) => (url.searchParams.get('tag') || url.searchParams.get('v') ? old : radarView()),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
      'GET /api/radars/rivals/tags': { items: [{ tag: 'Board', version: 1, by: 'u', at: null }] },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openHistory(container);
    fireEvent.click(screen.getByRole('link', { name: 'Open' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/radars/rivals?tag=Board'));
    expect(await screen.findByText('Version 1 (Board), not the current version 2')).toBeInTheDocument();
    const tabs = createWrapper(container).findTabs()!;
    expect(tabs.findTabLinkById('edit')).toBeNull();
    expect(tabs.findTabLinkById('io')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open the current version' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/radars/rivals'));
  });

  it('shows tags without controls to a viewer, and reports a refused removal and a failed copy', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': (_b: unknown, url: URL) => radarView({ can_edit: false, version: url.searchParams.get('version') === '1' ? 1 : 2 }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
      'GET /api/radars/rivals/tags': { items: [{ tag: 'Board', version: 1, by: 'u', at: null }] },
    });
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => { throw new Error('no clipboard'); }) } });
    render(wrap(routes, { api, me: VIEWER, path: '/radars/rivals?v=1&tab=history' }));
    expect(await screen.findByText('Version 1, not the current version 2')).toBeInTheDocument();
    await screen.findByRole('button', { name: 'Copy a link to version 1' });
    expect(screen.getByText('Board')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Tag version/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Copy a link to version 1' }));
    expect(await screen.findByText('no clipboard')).toBeInTheDocument();
  });

  it('reports a refused tag removal', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/radars/rivals/versions': versions,
      'GET /api/radars/rivals/tags': { items: [{ tag: 'Board', version: 1, by: 'u', at: null }] },
      'DELETE /api/radars/rivals/tags/Board': () => {
        throw { status: 403, code: 'forbidden', message: 'not an editor' };
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals' }));
    await openHistory(container);
    fireEvent.click(screen.getByRole('button', { name: 'Remove the tag Board' }));
    expect(await screen.findByText('not an editor')).toBeInTheDocument();
  });
});
