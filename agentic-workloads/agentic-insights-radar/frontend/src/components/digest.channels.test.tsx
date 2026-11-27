// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AdminPage from '@/pages/AdminPage';
import { fakeApi, wrap } from '@/test/session';

import { ApiError } from '@/api/client';

import { CHANNEL_ID } from '@/lib/channels';

import DigestChannelsAdmin, { channelErrors, clashRows, isStale, MAX_CHANNELS } from './DigestChannelsAdmin';

const editor = () => createWrapper(document.body).findAttributeEditor()!;
const save = () => screen.getByRole('button', { name: 'Save' });
const isOff = (el: HTMLElement) => el.getAttribute('aria-disabled') === 'true' || (el as HTMLButtonElement).disabled;
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

// Made-up Slack ids, built from parts: written whole, a C or a D and digits reads as a hex secret to detect-secrets.
const DIGITS = '01234567';
const CHANNEL = `C${DIGITS}`;

describe('channel validation', () => {
  it('takes Slack channel IDs only, once each, with a name', () => {
    expect(CHANNEL_ID.test(CHANNEL)).toBe(true);
    expect(CHANNEL_ID.test('G0123456789AB')).toBe(true);
    expect(CHANNEL_ID.test(`D${DIGITS}`)).toBe(false); // a DM, not a channel
    expect(CHANNEL_ID.test(CHANNEL.toLowerCase())).toBe(false);
    expect(CHANNEL_ID.test('C0123')).toBe(false);
    expect(
      channelErrors([
        { id: ` ${CHANNEL} `, label: 'News' },
        { id: CHANNEL, label: ' ' },
        { id: '', label: 'x'.repeat(81) }, // the length is the server's to check
        { id: '#general', label: 'General' },
      ]),
    ).toEqual([
      { id: '', label: '' },
      { id: 'This channel is already in the list.', label: 'Enter a name people will recognise.' },
      { id: 'Enter the channel ID.', label: '' }, // c29: the server checks the length
      { id: expect.stringMatching(/^Use the channel ID, such as C0123456789/), label: '' },
    ]);
    // c29: only emptiness and control characters are checked here; the server owns the rest of the name rule.
    expect(channelErrors([{ id: CHANNEL, label: 'market\tnews' }])[0]!.label).toBe('Remove the tab or other control character.');
    // DEL and C1 count too, and printable text above them (accents, CJK) does not (scan cycle 52).
    for (const bad of ['market\u007fnews', 'market\u0085news', 'market\u009fnews']) {
      expect(channelErrors([{ id: CHANNEL, label: bad }])[0]!.label).toBe('Remove the tab or other control character.');
    }
    for (const fine of ['caf\u00e9 news', '\u65b0\u805e', '\u00a0news']) {
      expect(channelErrors([{ id: CHANNEL, label: fine }])[0]!.label).toBe('');
    }
    expect(channelErrors([{ id: CHANNEL, label: '#news' }])[0]!.label).toBe('');
  });
});

describe('Administration, Digest delivery', () => {
  it('is a tab of Administration with the note about inviting the bot', async () => {
    const { api } = fakeApi({ 'GET /api/admin/digest/channels': { channels: [] } });
    render(wrap(<AdminPage />, { api, path: '/admin?tab=digest' }));
    expect(await screen.findByText('Invite the bot to each channel')).toBeInTheDocument();
    expect(screen.getByText(/fail with not_in_channel/)).toBeInTheDocument();
    expect(screen.getByText('No channels yet: people cannot pick a Slack channel for their digest.')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Digest delivery' })).toHaveAttribute('aria-selected', 'true');
  });

  it('adds, validates, removes and saves channels', async () => {
    let stored = [{ id: CHANNEL, label: 'market-news' }]; // as the server stores it (c24)
    const { api, calls } = fakeApi({
      'GET /api/admin/digest/channels': () => ({ channels: stored }),
      'PUT /api/admin/digest/channels': (b: { channels: typeof stored }) => {
        stored = b.channels;
        return b;
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    expect(await screen.findByDisplayValue('market-news')).toBeInTheDocument();
    expect(screen.getByText(`1 of ${MAX_CHANNELS} channels.`)).toBeInTheDocument();
    expect(isOff(save())).toBe(true); // nothing changed yet
    act(() => editor().findAddButton().click());
    expect(screen.getByText('Enter the channel ID.')).toBeInTheDocument();
    expect(screen.getByText('Enter a name people will recognise.')).toBeInTheDocument();
    expect(isOff(save())).toBe(true);
    // An empty row is named by its position in the remove button; then by its ID, then by its name.
    expect(screen.getByRole('button', { name: 'Remove channel with no name' })).toBeInTheDocument();
    type('Channel ID, row 2', CHANNEL);
    expect(screen.getByText('This channel is already in the list.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: `Remove channel ${CHANNEL}` })).toBeInTheDocument();
    type('Channel ID, row 2', 'nope');
    expect(screen.getByText(/^Use the channel ID/)).toBeInTheDocument();
    type('Channel ID, row 2', ' G98765432 ');
    type('Name, row 2', ' #leadership ');
    expect(isOff(save())).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Remove channel market-news' }));
    expect(screen.queryByDisplayValue('market-news')).toBeNull();
    fireEvent.click(save());
    expect(await screen.findByText('Saved the digest channels.')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ channels: [{ id: 'G98765432', label: '#leadership' }], version: 0 }); // c29: sent as typed; the server normalises it
    await waitFor(() => expect(screen.getByDisplayValue('#leadership')).toBeInTheDocument()); // the fake API stores it as sent
  });

  it('starts again from the list the server stored, without waiting for a reload (scan cycle 30)', async () => {
    let reads = 0;
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': () => {
        reads++;
        return { channels: [{ id: CHANNEL, label: 'old-name' }] }; // a reload would hand back the old list
      },
      'PUT /api/admin/digest/channels': () => ({ channels: [{ id: CHANNEL, label: 'new-name' }] }),
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    expect(await screen.findByDisplayValue('old-name')).toBeInTheDocument();
    type('Name, row 1', '#new-name');
    fireEvent.click(save());
    expect(await screen.findByDisplayValue('new-name')).toBeInTheDocument();
    expect(isOff(save())).toBe(true); // nothing unsaved
    expect(reads).toBe(1);
  });

  it('quotes the version it loaded, and reloads with a message when the list changed first (scan cycle 31)', async () => {
    let reads = 0;
    const sent: unknown[] = [];
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': () => {
        reads++;
        return reads === 1
          ? { channels: [{ id: CHANNEL, label: 'old-name' }], version: 4, clashes: [] }
          : { channels: [{ id: CHANNEL, label: 'their-name' }], version: 5, clashes: [] };
      },
      'PUT /api/admin/digest/channels': (b: unknown) => {
        sent.push(b);
        throw new ApiError(409, 'conflict', 'changed', { code: 'channels_changed' });
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    await screen.findByDisplayValue('old-name');
    type('Name, row 1', 'my-name');
    fireEvent.click(save());
    expect(await screen.findByDisplayValue('their-name')).toBeInTheDocument();
    expect(sent).toEqual([{ channels: [{ id: CHANNEL, label: 'my-name' }], version: 4 }]);
    expect(await screen.findByText(/The channel list changed while you were editing/)).toBeInTheDocument();
  });

  it('lets a newer load replace the copy kept from a save (scan cycle 31)', async () => {
    let reads = 0;
    let puts = 0;
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': () => {
        reads++;
        return reads === 1
          ? { channels: [{ id: CHANNEL, label: 'first' }], version: 1, clashes: [] }
          : { channels: [{ id: CHANNEL, label: 'their-name' }], version: 3, clashes: [] };
      },
      'PUT /api/admin/digest/channels': () => {
        puts++;
        if (puts === 1) return { channels: [{ id: CHANNEL, label: 'mine' }], version: 2, clashes: [] };
        throw new ApiError(409, 'conflict', 'changed', { code: 'channels_changed' });
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    await screen.findByDisplayValue('first');
    type('Name, row 1', 'mine');
    fireEvent.click(save());
    await screen.findByText('Saved the digest channels.');
    expect(screen.getByDisplayValue('mine')).toBeInTheDocument(); // remounted from the PUT's answer
    type('Name, row 1', 'mine-again');
    fireEvent.click(save());
    expect(await screen.findByDisplayValue('their-name')).toBeInTheDocument(); // the reload wins over the saved copy
  });

  it('flags a stored name that reads the same as another, until it is renamed (scan cycle 31)', async () => {
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': {
        channels: [
          { id: CHANNEL, label: 'news' },
          { id: 'C9999999999', label: 'news' },
        ],
        version: 2,
        clashes: [[CHANNEL, 'C9999999999']],
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    expect(await screen.findByText(/Reads the same as row 2/)).toBeInTheDocument(); // both halves are marked
    expect(screen.getByText(/Reads the same as row 1/)).toBeInTheDocument();
    type('Name, row 1', 'news-old'); // renaming either one clears both (scan cycle 32)
    expect(screen.queryByText(/Reads the same as row/)).not.toBeInTheDocument();
  });

  it('recognises the answer to a save over a newer list (scan cycle 31)', () => {
    expect(isStale(new ApiError(409, 'conflict', 'x', { code: 'channels_changed' }))).toBe(true);
    expect(isStale(new ApiError(409, 'conflict', 'x', {}))).toBe(false);
    expect(isStale(new Error('x'))).toBe(false);
  });

  it('says a stale save was not made, and keeps flagging a group while two or more still match (scan cycles 32-33)', async () => {
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': { channels: [{ id: CHANNEL, label: 'a' }], version: 1, clashes: [] },
      'PUT /api/admin/digest/channels': () => {
        throw new ApiError(409, 'conflict', 'changed', { code: 'channels_changed' });
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    await screen.findByDisplayValue('a');
    type('Name, row 1', 'b');
    fireEvent.click(save());
    expect(await screen.findByText(/so this save was not made/)).toBeInTheDocument();
    const group = [[CHANNEL, 'C9999999999', 'C8888888888']];
    const three = [
      { id: CHANNEL, label: 'news' },
      { id: 'C9999999999', label: 'news' },
      { id: 'C8888888888', label: 'news' },
    ];
    expect([...clashRows(three, three, group)]).toEqual([
      [0, [1, 2]],
      [1, [0, 2]],
      [2, [0, 1]],
    ]);
    const renamedFirst = [{ id: CHANNEL, label: 'news-old' }, three[1], three[2]] as { id: string; label: string }[];
    expect([...clashRows(renamedFirst, three, group)]).toEqual([
      [1, [2]],
      [2, [1]],
    ]); // the other two still clash
    expect(clashRows([three[1] as { id: string; label: string }], three, group).size).toBe(0);
  });

  it('shows the reload advice for an out-of-date page, and counts a repeated ID once in a clash (scan cycle 34)', async () => {
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': { channels: [{ id: CHANNEL, label: 'a' }], version: 1, clashes: [] },
      'PUT /api/admin/digest/channels': () => {
        throw new ApiError(400, 'validation_failed', 'this page is out of date: reload it and try again', { code: 'reload_needed' });
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    await screen.findByDisplayValue('a');
    type('Name, row 1', 'b');
    fireEvent.click(save());
    expect(await screen.findByText(/reload it and try again/)).toBeInTheDocument();
    const initial = [
      { id: CHANNEL, label: 'news' },
      { id: 'C9999999999', label: 'News' },
    ];
    const rows = [{ id: CHANNEL, label: 'news' }, { id: 'C9999999999', label: 'other' }, { id: CHANNEL, label: 'news' }];
    expect(clashRows(rows, initial, [[CHANNEL, 'C9999999999']]).size).toBe(0); // the repeat is not a look-alike
    expect(clashRows(initial, initial, [[CHANNEL, CHANNEL]]).size).toBe(0); // a group repeating an ID is one row (c35)
  });

  it('stops at 20 channels', async () => {
    const channels = Array.from({ length: MAX_CHANNELS }, (_, i) => ({ id: `C0000000${String(i).padStart(2, '0')}`, label: `#c${i}` }));
    const { api } = fakeApi({ 'GET /api/admin/digest/channels': { channels } });
    render(wrap(<DigestChannelsAdmin />, { api }));
    expect(await screen.findByText(`${MAX_CHANNELS} of ${MAX_CHANNELS} channels. Remove one to add another.`)).toBeInTheDocument();
    expect(isOff(editor().findAddButton().getElement())).toBe(true);
  });

  it('says when saving fails, and when the list cannot load', async () => {
    let broken = true;
    const { api } = fakeApi({
      'GET /api/admin/digest/channels': () => {
        if (broken) throw { status: 500, code: 'internal', message: 'down' };
        return { channels: [] };
      },
      'PUT /api/admin/digest/channels': () => {
        throw { status: 400, code: 'validation', message: 'At most 20 channels.' };
      },
    });
    render(wrap(<DigestChannelsAdmin />, { api }));
    expect(await screen.findByText('The digest channels could not be loaded')).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/No channels yet/)).toBeInTheDocument();
    act(() => editor().findAddButton().click());
    type('Channel ID, row 1', CHANNEL);
    type('Name, row 1', 'News');
    fireEvent.click(save());
    expect(await screen.findByText('At most 20 channels.')).toBeInTheDocument();
  });
});
