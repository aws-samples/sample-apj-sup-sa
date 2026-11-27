// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AttributeFieldDef } from '@/api/types';
import { node } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import InsightsPage from './InsightsPage';

const SCHEMA: AttributeFieldDef[] = [
  { key: 'urgency', label: 'Urgency', type: 'enum', required: true, filter: true, sort: true, help: '',
    values: [{ value: 'act_now', label: 'Act now', rank: 1 }] },
];
const page = { items: [node({ attributes: { urgency: 'act_now' } })], cursor: null, sort: 'newest', page_size: 25, schema: SCHEMA };
const routes = (
  <Routes>
    <Route path="/insights/:kind" element={<InsightsPage />} />
  </Routes>
);

function saved() {
  const blobs: Blob[] = [];
  const names: string[] = [];
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: (b: Blob) => (blobs.push(b), 'blob:x'), revokeObjectURL: () => undefined }));
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    names.push(this.download);
  });
  return { blobs, names };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function exportAs(container: HTMLElement, id: string) {
  const dd = createWrapper(container).findButtonDropdown()!;
  await waitFor(() => expect(dd.findNativeButton().getElement().getAttribute('aria-disabled')).not.toBe('true'));
  dd.openDropdown();
  dd.findItemById(id)!.click();
}

describe('exporting a list', () => {
  it('saves a CSV with the filters and field sort on screen, readable by Excel', async () => {
    const { blobs, names } = saved();
    const { api, calls, fetcher } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': page,
      'GET /api/insights/signals/export': 'Title\r\nA\r\n',
    });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?f.urgency=act_now&sort=attr:urgency&order=desc' }));
    await screen.findByText('Urgency: Act now');
    await exportAs(container, 'csv');
    await waitFor(() => expect(names).toHaveLength(1));
    expect(names[0]).toMatch(/^signals-\d{4}-\d{2}-\d{2}\.csv$/);
    // Blob.text() drops a byte-order mark, so read the bytes: EF BB BF, then the file.
    const bytes = new Uint8Array(await blobs[0]!.arrayBuffer());
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes.slice(3))).toBe('Title\r\nA\r\n');
    const urls = (fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) => new URL(String(c[0]), 'https://x'));
    const asked = urls.find((u) => u.pathname === '/api/insights/signals/export')!;
    expect(asked.searchParams.get('format')).toBe('csv');
    expect(asked.searchParams.get('filter.urgency')).toBe('act_now');
    expect(asked.searchParams.get('sort')).toBe('attr:urgency');
    expect(asked.searchParams.get('order')).toBe('desc');
    expect(calls.some((c) => c.path === '/api/insights/signals/export')).toBe(true);
  });

  it('saves Markdown as it comes and says when the export fails', async () => {
    const { blobs, names } = saved();
    let fails = false;
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': page,
      'GET /api/insights/signals/export': () => {
        if (fails) throw { status: 500, message: 'export broke' };
        return '# Signals\n';
      },
    });
    const { container } = render(wrap(routes, { api, path: '/insights/signals' }));
    await screen.findByText('Urgency: Act now');
    await exportAs(container, 'md');
    await waitFor(() => expect(names).toEqual([expect.stringMatching(/\.md$/)]));
    expect(await blobs[0]!.text()).toBe('# Signals\n');
    fails = true;
    await exportAs(container, 'csv');
    expect(await screen.findByText(/export broke/)).toBeInTheDocument();
  });

  it('asks for the format alone when nothing is filtered', async () => {
    const { api, fetcher } = fakeApi({ 'GET /api/insights/signals/export': 'x' });
    expect(await api.exportList('signals', 'md')).toBe('x');
    const url = new URL(String((fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls[0]![0]), 'https://x');
    expect([...url.searchParams.keys()]).toEqual(['format']);
  });

  it('is off while searching or sorting in an order a file cannot keep', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': page,
    });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?sort=title' }));
    await screen.findByText('Urgency: Act now');
    expect(createWrapper(container).findButtonDropdown()!.findNativeButton().getElement().getAttribute('aria-disabled')).toBe('true');
  });
});
