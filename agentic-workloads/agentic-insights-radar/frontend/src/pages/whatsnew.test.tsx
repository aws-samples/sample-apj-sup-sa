// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Digest } from '@/api/types';
import WatchToggle from '@/components/WatchToggle';
import { fakeApi, wrap } from '@/test/session';

import WhatsNewPage from './WhatsNewPage';

const row = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
  id,
  type: id.split('/')[0] ?? '',
  title,
  summary: '',
  evidence_url: null,
  updated_at: '2026-10-25T01:00:00.000Z',
  ...extra,
});

const digest = (over: Partial<Digest> = {}): Digest => ({
  since: '2026-10-18T00:00:00.000Z',
  until: '2026-10-25T02:00:00.000Z',
  total: 3,
  new: {
    signal: [],
    trend: [row('trend/t1', 'Recalls rise', { summary: 'More recalls' })],
    gap: [],
    content_idea: [],
    'insight:tariff_item': [row('insight/i1', 'Steel duty up')],
  },
  labels: { 'insight:tariff_item': 'Tariff changes' },
  watched: [
    {
      node: row('organization/acme', 'Acme'),
      new: [row('fact/f1', 'Acme recalls', { evidence_url: 'https://a.example/1' })],
      count: 2,
    },
    { node: row('product/p1', 'Widget'), new: [], count: 0 },
  ],
  ...over,
});

describe("What's new (CTR subscription-digest)", () => {
  it('lists news about watched records and new conclusions, and marks them read', async () => {
    let read = false;
    const { api, calls } = fakeApi({
      'GET /api/me/digest': () => (read ? digest({ total: 0, watched: [], new: {} }) : digest()),
      'POST /api/me/digest/read': () => {
        read = true;
        return { read_at: '2026-10-25T02:00:00.000Z' };
      },
    });
    render(wrap(<WhatsNewPage />, { api }));
    expect(await screen.findByText('Acme recalls')).toBeInTheDocument();
    expect(screen.getByText('Recalls rise')).toBeInTheDocument();
    expect(screen.getByText('More recalls')).toBeInTheDocument();
    expect(screen.getByText('New: Tariff changes')).toBeInTheDocument();
    expect(screen.getByText('Steel duty up')).toBeInTheDocument();
    expect(screen.getByText('Nothing new about it.')).toBeInTheDocument();
    expect(screen.getByText(/\(2 new records\)/)).toBeInTheDocument();
    expect(screen.getByText(/3 new records since/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mark as read' }));
    expect(await screen.findByText('You watch no records yet.')).toBeInTheDocument();
    expect(screen.getByText(/Marked as read/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mark as read' })).toBeDisabled();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/api/me/digest/read']);
  });

  it('says when marking as read fails, and when the digest cannot load', async () => {
    const { api } = fakeApi({
      'GET /api/me/digest': digest(),
      'POST /api/me/digest/read': () => {
        throw { status: 500, code: 'internal', message: 'try later' };
      },
    });
    render(wrap(<WhatsNewPage />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mark as read' }));
    expect(await screen.findByText('try later')).toBeInTheDocument();
    const broken = fakeApi({
      'GET /api/me/digest': () => {
        throw { status: 500, code: 'internal', message: 'down' };
      },
    });
    render(wrap(<WhatsNewPage />, { api: broken.api }));
    expect(await screen.findByText(/what's new/i, { selector: 'h1, h2, div, p, span' })).toBeInTheDocument();
  });

  it('watches and unwatches a record, and puts it back when the server refuses', async () => {
    let fail = false;
    const { api, calls } = fakeApi({
      'GET /api/me/watches': { items: [] },
      'PUT /api/me/watches/nodes/organization/acme': () => {
        if (fail) throw { status: 400, code: 'validation', message: 'too many' };
        return { id: 'organization/acme', watching: true };
      },
      'DELETE /api/me/watches/nodes/organization/acme': { id: 'organization/acme', watching: false },
    });
    render(wrap(<WatchToggle id="organization/acme" />, { api }));
    const watch = await screen.findByRole('button', { name: 'Watch' });
    await waitFor(() => expect(watch).not.toBeDisabled());
    fireEvent.click(watch);
    fireEvent.click(await screen.findByRole('button', { name: 'Watching' }));
    fail = true;
    fireEvent.click(await screen.findByRole('button', { name: 'Watch' }));
    expect(await screen.findByText('too many')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Watch' })).toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith('/api/me/watches/')).map((c) => c.method)).toEqual(['PUT', 'DELETE', 'PUT']);
  });

  it('shows a record already watched, and asks for news since a time', async () => {
    let asked = '';
    const { api } = fakeApi({
      'GET /api/me/watches': { items: [{ id: 'product/p1', type: 'product', title: 'Widget' }] },
      'GET /api/me/digest': (_b: unknown, url: URL) => {
        asked = url.search;
        return digest();
      },
    });
    render(wrap(<WatchToggle id="product/p1" />, { api }));
    expect(await screen.findByRole('button', { name: 'Watching' })).toBeInTheDocument();
    await api.digest('2026-10-01T00:00:00Z');
    expect(asked).toContain('since=2026-10-01');
  });
});

describe('What\'s new with a cut list and nothing visible (code review r10)', () => {
  it('still lets the reader mark it read', async () => {
    const { api } = fakeApi({
      'GET /api/me/digest': { ...digest({ total: 0, watched: [], new: {} }), more: { gap: true }, labels: {} },
    });
    render(wrap(<WhatsNewPage />, { api }));
    expect(await screen.findByRole('button', { name: 'Mark as read' })).not.toBeDisabled();
  });
});
