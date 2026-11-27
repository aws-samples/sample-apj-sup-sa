// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Round-2 UI campaign: the What's new page, the Watch button and the round-2 client calls, each
 * crossed with the data and server states a person can meet.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Digest, DigestRow } from '@/api/types';
import WatchToggle from '@/components/WatchToggle';
import { fakeApi, wrap } from '@/test/session';

import WhatsNewPage from './WhatsNewPage';

const row = (id: string, title: string): DigestRow => ({
  id,
  type: id.split('/')[0] ?? '',
  title,
  summary: '',
  evidence_url: null,
  updated_at: '2026-10-25T01:00:00.000Z',
});

const KINDS = ['signal', 'trend', 'gap', 'content_idea'] as const;
const TITLES: Record<string, string> = {
  signal: 'New signals',
  trend: 'New trends',
  gap: 'New gaps',
  content_idea: 'New content ideas',
};

// Every subset of the four conclusion lists, crossed with 0-2 watched records and with or without a custom list.
const DIGEST_CASES = Array.from({ length: 16 }, (_, mask) => mask).flatMap((mask) =>
  [0, 1, 2].flatMap((watched) => [false, true].map((custom) => ({ mask, watched, custom }))),
);

describe('What\'s new: every combination of lists, watched records and custom lists', () => {
  it.each(DIGEST_CASES)('lists $mask / $watched watched / custom $custom', async ({ mask, watched, custom }) => {
    const present = KINDS.filter((_, i) => mask & (1 << i));
    const d: Digest = {
      since: '2026-10-18T00:00:00.000Z',
      until: '2026-10-25T02:00:00.000Z',
      total: present.length + watched + (custom ? 1 : 0),
      new: Object.fromEntries([
        ...KINDS.map((k) => [k, present.includes(k) ? [row(`${k}/a`, `A ${k}`)] : []]),
        ...(custom ? [['insight:tariff_item', [row('insight/t1', 'Steel duty up')]]] : []),
      ]),
      ...(watched === 2 ? { more: { trend: true } } : {}),
      // An API from before custom lists sends no labels at all.
      ...(custom ? { labels: { 'insight:tariff_item': 'Tariff changes' } } : mask % 2 ? {} : { labels: {} }),
      watched: Array.from({ length: watched }, (_, i) => ({
        node: row(`organization/w${i}`, `Watched ${i}`),
        new: i === 0 ? [row(`fact/f${i}`, `News ${i}`)] : [],
        count: i === 0 ? 1 : 0,
      })),
    };
    const { api } = fakeApi({ 'GET /api/me/digest': d });
    render(wrap(<WhatsNewPage />, { api }));
    expect(await screen.findByRole('heading', { name: "What's new", level: 1 })).toBeInTheDocument();
    for (const k of KINDS) {
      expect(screen.getByRole('heading', { name: TITLES[k] })).toBeInTheDocument();
      if (present.includes(k)) expect(screen.getByText(`A ${k}`)).toBeInTheDocument();
    }
    expect(screen.queryByText('New: Tariff changes') !== null).toBe(custom);
    expect(screen.queryByText('You watch no records yet.') !== null).toBe(watched === 0);
    if (watched > 1) expect(screen.getByText('Nothing new about it.')).toBeInTheDocument();
    expect(screen.queryByText(/More arrived after these/) !== null).toBe(watched === 2);
    // A cut list with nothing visible still lets the reader move the window on.
    expect(screen.getByRole('button', { name: 'Mark as read' })).toHaveProperty('disabled', d.total === 0 && watched !== 2);
  });
});

const IDS = ['organization/acme', 'product/p 1', 'topic/t%25', 'person/a.b', 'segment/sme'];
const SERVER = ['ok', 'refused', 'down'] as const;

describe('Watch: every record kind x starting state x server answer', () => {
  it.each(IDS.flatMap((id) => [false, true].flatMap((watching) => SERVER.map((server) => ({ id, watching, server })))))(
    '$id watching=$watching server=$server',
    async ({ id, watching, server }) => {
      const answer = () => {
        if (server === 'refused') throw { status: 400, code: 'validation', message: 'too many watches' };
        if (server === 'down') throw { status: 503, code: 'unavailable', message: 'try later' };
        return { id, watching: !watching };
      };
      const enc = id.split('/').map(encodeURIComponent).join('/');
      const { api, calls } = fakeApi({
        'GET /api/me/watches': { items: watching ? [{ id, type: id.split('/')[0], title: 'x' }] : [] },
        [`PUT /api/me/watches/nodes/${enc}`]: answer,
        [`DELETE /api/me/watches/nodes/${enc}`]: answer,
      });
      render(wrap(<WatchToggle id={id} />, { api }));
      const before = watching ? 'Watching' : 'Watch';
      const button = await screen.findByRole('button', { name: before });
      await waitFor(() => expect(button).not.toBeDisabled());
      fireEvent.click(button);
      if (server === 'ok') {
        expect(await screen.findByRole('button', { name: watching ? 'Watch' : 'Watching' })).toBeInTheDocument();
      } else {
        // A 503 message is capitalised, as every rate-limit or busy message is.
        expect(await screen.findByText(server === 'refused' ? 'too many watches' : /^Try later/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: before })).toBeInTheDocument(); // put back
      }
      const write = calls.filter((c) => c.path.startsWith('/api/me/watches/nodes/'));
      expect(write.map((c) => c.method)).toEqual([watching ? 'DELETE' : 'PUT']);
      expect(write[0]?.path).toBe(`/api/me/watches/nodes/${enc}`);
    },
  );
});

describe('client: marking read with and without the digest time', () => {
  it.each([undefined, '2026-10-25T02:00:00.000Z'])('upto=%s', async (upto) => {
    let body: unknown = null;
    const { api } = fakeApi({ 'POST /api/me/digest/read': (b: unknown) => ((body = b), { read_at: 'x' }) });
    await api.markDigestRead(upto);
    expect(body).toEqual(upto ? { upto } : {});
  });
});
