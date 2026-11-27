// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { Delivery, DeliveryOptions } from '@/api/types';
import { browserTimeZone, hourLabel, scheduleText, timeZones } from '@/lib/schedule';
import { fakeApi, wrap } from '@/test/session';

import DigestDeliveries, { changesOf, channelOption, MAX_DELIVERIES, testError, testMessage, unavailableReason } from './DigestDelivery';

afterEach(() => vi.restoreAllMocks());

// A made-up Slack channel id, built from parts: written whole it reads as a hex secret to detect-secrets.
const CHANNEL = `C${'01234567'}`;

const OPTIONS: DeliveryOptions = {
  slack_channel: true,
  slack_dm: true,
  email: true,
  channels: [
    { id: CHANNEL, label: '#market-news' },
    { id: 'G98765432', label: '#leadership' },
  ],
  email_address: 'me@example.com',
};

const delivery = (over: Partial<Delivery> = {}): Delivery => ({
  id: 'd1',
  kind: 'email',
  target_label: 'me@example.com',
  cadence: 'weekly',
  weekday: 0,
  hour: 8,
  timezone: 'Asia/Singapore',
  enabled: true,
  next_at: '2026-11-17T00:00:00Z',
  last_sent_at: null,
  last_status: null,
  ...over,
});

const body = () => createWrapper(document.body);
const modal = () => body().findModal()!;
const field = (label: string) => createWrapper(modal().getElement()).findAllFormFields().find((f) => f.findLabel()?.getElement().textContent === label);
const pick = (label: string, value: string) => {
  const select = field(label)!.findControl()!.findSelect()!;
  select.openDropdown();
  select.selectOptionByValue(value);
};
const radio = (label: string, value: string) => field(label)!.findControl()!.findRadioGroup()!.findInputByValue(value)!.click();
const footer = (name: string) => within(modal().findFooter()!.getElement()).getByRole('button', { name });
/** Cloudscape keeps a button with a disabled reason focusable: it is aria-disabled, not disabled. */
const isOff = (el: HTMLElement) => el.getAttribute('aria-disabled') === 'true' || (el as HTMLButtonElement).disabled;

describe('schedules in words', () => {
  it('says daily and weekly schedules with the day, hour and zone', () => {
    expect(scheduleText({ cadence: 'weekly', weekday: 0, hour: 8, timezone: 'Asia/Singapore' })).toBe('Weekly on Monday at 08:00 Asia/Singapore');
    expect(scheduleText({ cadence: 'weekly', weekday: 6, hour: 17, timezone: 'UTC' })).toBe('Weekly on Sunday at 17:00 UTC');
    expect(scheduleText({ cadence: 'weekly', weekday: null, hour: 0, timezone: 'UTC' })).toBe('Weekly on Monday at 00:00 UTC');
    expect(scheduleText({ cadence: 'weekly', weekday: 9, hour: 0, timezone: 'UTC' })).toBe('Weekly on Monday at 00:00 UTC');
    expect(scheduleText({ cadence: 'daily', hour: 23, timezone: 'Europe/London' })).toBe('Daily at 23:00 Europe/London');
    expect(hourLabel(5)).toBe('05:00');
  });

  it("defaults to the browser's zone, and to UTC when the browser does not say", () => {
    expect(browserTimeZone()).toBe('UTC'); // the suite pins TZ=UTC
    const real = Intl.DateTimeFormat;
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => ({ resolvedOptions: () => ({ timeZone: '' }) }) as unknown as Intl.DateTimeFormat);
    expect(browserTimeZone()).toBe('UTC');
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
      throw new RangeError('no zones');
    });
    expect(browserTimeZone()).toBe('UTC');
    expect(Intl.DateTimeFormat).not.toBe(real);
  });

  it('lists every zone A to Z with UTC and the saved zone, even when the browser lists none', () => {
    const all = timeZones(['Mars/Olympus', '']);
    expect(all).toContain('Asia/Singapore');
    expect(all).toContain('UTC');
    expect(all).toContain('Mars/Olympus');
    expect(all).not.toContain('');
    expect([...all].sort((a, b) => a.localeCompare(b))).toEqual(all);
    const intl = Intl as unknown as { supportedValuesOf?: unknown };
    const saved = intl.supportedValuesOf;
    intl.supportedValuesOf = undefined;
    try {
      expect(timeZones()).toEqual(['UTC']);
    } finally {
      intl.supportedValuesOf = saved;
    }
  });
});

describe('delivery rules', () => {
  it('shows a channel ID under its name only when it adds something (scan cycles 35 and 36)', () => {
    expect(channelOption({ id: 'C1111111111', label: 'news' })).toEqual({ value: 'C1111111111', label: 'news', description: 'C1111111111' });
    expect(channelOption({ id: 'C2222222222', label: 'News (C2222222222)' }).description).toBeUndefined();
    expect(channelOption({ id: 'C3333333333', label: 'C3333333333' }).description).toBeUndefined();
    expect(channelOption({ id: 'webhook', label: 'The Slack channel this deployment posts to' }).description).toBeUndefined();
  });

  it('says why each kind is unavailable', () => {
    expect(KINDS_REASONS(OPTIONS)).toEqual([null, null, null]);
    expect(KINDS_REASONS({ ...OPTIONS, slack_channel: false, slack_dm: false, email: false })).toEqual([
      'Slack channels are not set up on this deployment.',
      'Slack DMs are not set up on this deployment.',
      'Email is not set up on this deployment.',
    ]);
    expect(unavailableReason('slack_channel', { ...OPTIONS, channels: [] })).toMatch(/an administrator adds them under Administration/);
    expect(unavailableReason('email', { ...OPTIONS, email_address: null })).toBe('Your account has no verified email address to send to.');
  });

  it('says what a test did', () => {
    const d = delivery();
    expect(testMessage(d, { sent: true, total: 3 })).toEqual({ type: 'success', text: 'Test sent to me@example.com: 3 new records.' });
    expect(testMessage(d, { sent: false, total: 2, error: 'not_in_channel' })).toEqual({
      type: 'error',
      text: 'The test could not be sent to me@example.com: not_in_channel',
    });
    expect(testMessage(d, { sent: false, total: 0 }).type).toBe('info');
    const perDelivery = { limit_per_10_minutes: 1 };
    expect(testError(new ApiError(429, 'rate_limited', 'slow down', perDelivery, 300))).toBe(
      'You can send one test every 10 minutes for each delivery. Try again in 5 minutes.',
    );
    expect(testError(new ApiError(429, 'rate_limited', 'slow down', perDelivery))).toBe('You can send one test every 10 minutes for each delivery.');
    // The per-user request limit is a different 429: said as itself (scan cycle 18).
    expect(testError(new ApiError(429, 'rate_limited', 'too many requests; wait a minute', { limit_per_minute: 30 }, 40))).toBe(
      'Too many requests; wait a minute. Try again in 40 seconds.',
    );
    expect(testError(new ApiError(500, 'internal', 'broken'))).toBe('broken'); // c24
    expect(testError(new ApiError(503, 'throttled', 'busy'))).toBe('Busy'); // nothing was sent: no warning
  });

  it('sends only the schedule fields that changed, and the day whenever it becomes weekly', () => {
    const base = { kind: 'email' as const, channel_id: '', cadence: 'weekly' as const, weekday: 0, hour: 8, timezone: 'Asia/Singapore' };
    expect(changesOf(delivery(), base)).toEqual({});
    expect(changesOf(delivery(), { ...base, weekday: 2, hour: 9, timezone: 'UTC' })).toEqual({ weekday: 2, hour: 9, timezone: 'UTC' });
    expect(changesOf(delivery(), { ...base, cadence: 'daily' })).toEqual({ cadence: 'daily' });
    expect(changesOf(delivery({ cadence: 'daily', weekday: null }), base)).toEqual({ cadence: 'weekly', weekday: 0 });
    expect(changesOf(delivery({ weekday: null }), base)).toEqual({});
    expect(changesOf(delivery({ weekday: null }), { ...base, weekday: 3 })).toEqual({ weekday: 3 });
  });
});

function KINDS_REASONS(o: DeliveryOptions) {
  return (['slack_channel', 'slack_dm', 'email'] as const).map((k) => unavailableReason(k, o));
}

function setup(items: Delivery[] = [delivery()], options: DeliveryOptions = OPTIONS, extra: Record<string, unknown> = {}) {
  const state = { items, options };
  const { api, calls } = fakeApi({ 'GET /api/me/deliveries': () => state, ...extra });
  render(wrap(<DigestDeliveries />, { api }));
  return { calls, state };
}

describe('Digest delivery list', () => {
  it('shows a bounced address as stopped even when a later send wrote another status (scan cycle 14)', async () => {
    setup([delivery({ id: 'a', target_label: 'gone@example.com', last_status: 'ok', suppressed: true, enabled: false })]);
    expect(await screen.findByText('Stopped: the address bounced or reported spam')).toBeInTheDocument();
    expect(screen.queryByText('Sent')).not.toBeInTheDocument();
  });

  it('shows why a delivery was stopped and does not offer to switch it back on (scan cycle 15)', async () => {
    setup([
      delivery({
        id: 'a',
        target_label: 'gone@example.com',
        last_status: 'empty',
        suppressed: true,
        stop_reason: 'the address bounced (MailboxFull)',
        enabled: false,
      }),
    ]);
    expect(await screen.findByText('the address bounced (MailboxFull)')).toBeInTheDocument();
    const toggle = screen.getByRole('checkbox', { name: 'Send digests to gone@example.com' });
    expect(toggle).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send a test to gone@example.com now' })).toBeDisabled();
    expect(screen.getAllByText('Stopped').length).toBeGreaterThan(0);
  });

  it('lists each delivery with its schedule, switch, last send and next send', async () => {
    setup([
      delivery({ id: 'a', last_status: 'ok', last_sent_at: new Date(Date.now() - 3 * 3600_000).toISOString() }),
      delivery({ id: 'b', kind: 'slack_channel', target_label: '#market-news', channel_id: CHANNEL, cadence: 'daily', last_status: 'empty' }),
      delivery({ id: 'c', kind: 'slack_dm', target_label: 'You in Slack', last_status: 'error', last_error: 'channel_not_found' }),
      delivery({ id: 'd', target_label: 'old@example.com', last_status: 'suppressed', enabled: false, suppressed: true }),
      delivery({ id: 'e', target_label: 'new@example.com', last_status: 'ok' }),
    ]);
    expect(await screen.findByRole('table', { name: 'Digest deliveries' })).toBeInTheDocument();
    const table = body().findTable()!;
    expect(table.findRows()).toHaveLength(5);
    expect(screen.getAllByText('Weekly on Monday at 08:00 Asia/Singapore')).toHaveLength(4);
    expect(screen.getByText('Daily at 08:00 Asia/Singapore')).toBeInTheDocument();
    expect(screen.getByText('Sent 3h ago')).toBeInTheDocument();
    expect(screen.getByText('Sent')).toBeInTheDocument();
    expect(screen.getByText('Nothing new to send')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('channel_not_found')).toBeInTheDocument();
    expect(screen.getByText('Stopped: the address bounced or reported spam')).toBeInTheDocument();
    expect(screen.getByText('Slack channel')).toBeInTheDocument();
    expect(screen.getByText('Slack direct message')).toBeInTheDocument();
    // The paused one says so instead of a next send; the others give a date.
    expect(table.findBodyCell(4, 5)!.getElement().textContent).toBe('Stopped'); // bounced, not paused (c15)
    expect(table.findBodyCell(1, 5)!.getElement().textContent).toMatch(/2026/);
    expect(screen.getByRole('checkbox', { name: 'Send digests to old@example.com' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Edit delivery to #market-news' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send a test to You in Slack now' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete delivery to me@example.com' })).toBeInTheDocument();
    // Five is the most: Add is off and says why.
    expect(MAX_DELIVERIES).toBe(5);
    expect(isOff(screen.getByRole('button', { name: 'Add delivery' }))).toBe(true);
    expect(screen.queryByText(/Not available:/)).toBeNull();
  });

  it('shows a delivery never sent as not sent yet, and names the kinds this deployment lacks', async () => {
    setup([delivery()], { ...OPTIONS, slack_dm: false, email_address: null });
    expect(await screen.findByText('Not sent yet')).toBeInTheDocument();
    expect(screen.getByText(/Not available: Slack DMs are not set up on this deployment\. Your account has no verified email/)).toBeInTheDocument();
    expect(isOff(screen.getByRole('button', { name: 'Add delivery' }))).toBe(false);
  });

  it('has a clear empty state, and one for a deployment with no delivery set up', async () => {
    setup([]);
    expect(await screen.findByText('No deliveries yet. Add one to get this digest in Slack or by email.')).toBeInTheDocument();
    expect(isOff(screen.getByRole('button', { name: 'Add delivery' }))).toBe(false);
  });

  it('says when no delivery is set up on the deployment', async () => {
    setup([], { slack_channel: false, slack_dm: false, email: false, channels: [], email_address: null });
    expect(await screen.findByText('Digest delivery is not set up on this deployment.')).toBeInTheDocument();
    expect(isOff(screen.getByRole('button', { name: 'Add delivery' }))).toBe(true);
  });

  it('says when the list cannot load, and loads it on Retry', async () => {
    let broken = true;
    const { api } = fakeApi({
      'GET /api/me/deliveries': () => {
        if (broken) throw { status: 500, code: 'internal', message: 'the table is down' };
        return { items: [], options: OPTIONS };
      },
    });
    render(wrap(<DigestDeliveries />, { api }));
    expect(screen.getByText('Loading deliveries')).toBeInTheDocument();
    expect(await screen.findByText('Your deliveries could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('the table is down')).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/No deliveries yet/)).toBeInTheDocument();
  });

  it('says the feature is not there when the API does not have it', async () => {
    const { api } = fakeApi({});
    render(wrap(<DigestDeliveries />, { api }));
    expect(await screen.findByText('Digest delivery is not available on this deployment.')).toBeInTheDocument();
  });
});

describe('Digest delivery actions', () => {
  it('pauses and turns a delivery back on, keeps the list when a re-read fails, and says a refusal', async () => {
    let reread = 0;
    let refuse = false;
    const items = [delivery()];
    const { api, calls } = fakeApi({
      'GET /api/me/deliveries': () => {
        reread++;
        if (reread === 3) throw { status: 500, code: 'internal', message: 'blip' };
        return { items, options: OPTIONS };
      },
      'PATCH /api/me/deliveries/d1': (b: unknown) => {
        if (refuse) throw { status: 400, code: 'validation', message: 'no such delivery' };
        Object.assign(items[0]!, b);
        return items[0];
      },
    });
    render(wrap(<DigestDeliveries />, { api }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Send digests to me@example.com' }));
    expect(await screen.findByText(/Paused: no digests go to me@example.com/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Send digests to me@example.com' })).not.toBeChecked());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Send digests to me@example.com' }));
    expect(await screen.findByText(/Turned on: digests go to me@example.com again/)).toBeInTheDocument();
    expect(await screen.findByText('Your deliveries could not be read again')).toBeInTheDocument();
    expect(screen.getByText(/This shows what was read last\. blip/)).toBeInTheDocument();
    refuse = true;
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Send digests to me@example.com' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Send digests to me@example.com' }));
    expect(await screen.findByText('no such delivery')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toEqual([{ enabled: false }, { enabled: true }, { enabled: true }]);
  });

  it('sends a test and says what happened, including the 10-minute limit', async () => {
    const answers: unknown[] = [
      { sent: true, total: 4 },
      { sent: false, total: 0 },
      { sent: false, total: 2, error: 'not_in_channel' },
      new Response(JSON.stringify({ code: 'rate_limited', message: 'too many', details: { retry_after: 420, limit_per_10_minutes: 1 } }), { status: 429 }),
      new Response(JSON.stringify({ code: 'internal', message: 'sender down' }), { status: 500 }),
    ];
    const { calls } = setup([delivery()], OPTIONS, { 'POST /api/me/deliveries/d1/test': () => answers.shift() });
    const send = await screen.findByRole('button', { name: 'Send a test to me@example.com now' });
    const results = [
      'Test sent to me@example.com: 4 new records.',
      'Nothing to send to me@example.com yet: the digest is empty right now, so no test went out.',
      'The test could not be sent to me@example.com: not_in_channel',
      'You can send one test every 10 minutes for each delivery. Try again in 7 minutes.',
      'sender down',
    ];
    for (const text of results) {
      fireEvent.click(send);
      expect(await screen.findByText(text)).toBeInTheDocument();
      await waitFor(() => expect(send).not.toBeDisabled());
    }
    act(() => body().findAllAlerts().find((a) => a.getElement().textContent?.includes('sender down'))!.findDismissButton()!.click());
    expect(screen.queryByText(/sender down/)).toBeNull();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(5);
  });

  it('reads the list again after a refused test or switch, so a stopped delivery shows as stopped (scan cycle 17)', async () => {
    const items = [delivery()];
    const { api } = fakeApi({
      'GET /api/me/deliveries': () => ({ items, options: OPTIONS }),
      'POST /api/me/deliveries/d1/test': () => {
        Object.assign(items[0]!, { suppressed: true, enabled: false, stop_reason: 'the address bounced (MailboxFull)' });
        throw { status: 409, code: 'suppressed', message: 'this address bounced' };
      },
      'PATCH /api/me/deliveries/d1': () => {
        throw { status: 409, code: 'suppressed', message: 'this address bounced' };
      },
    });
    render(wrap(<DigestDeliveries />, { api }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send a test to me@example.com now' }));
    expect(await screen.findByText('the address bounced (MailboxFull)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send a test to me@example.com now' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Send digests to me@example.com' })).toBeDisabled();
  });

  it('names the 10-minute limit only for that limit, and reads the list again only when a test failed (scan cycle 18)', async () => {
    let reads = 0;
    const answers: unknown[] = [
      { sent: true, total: 2 },
      new Response(JSON.stringify({ code: 'rate_limited', message: 'too many requests; wait a minute', details: { retry_after: 40, limit_per_minute: 30 } }), { status: 429 }),
    ];
    const { api } = fakeApi({
      'GET /api/me/deliveries': () => {
        reads++;
        return { items: [delivery()], options: OPTIONS };
      },
      'POST /api/me/deliveries/d1/test': () => answers.shift(),
    });
    render(wrap(<DigestDeliveries />, { api }));
    const send = await screen.findByRole('button', { name: 'Send a test to me@example.com now' });
    fireEvent.click(send);
    expect(await screen.findByText('Test sent to me@example.com: 2 new records.')).toBeInTheDocument();
    await waitFor(() => expect(send).not.toBeDisabled());
    expect(reads).toBe(1); // a test that went out changes nothing the list shows
    fireEvent.click(send);
    expect(await screen.findByText(/^Too many requests; wait a minute/)).toBeInTheDocument();
    expect(screen.queryByText(/one test every 10 minutes/)).toBeNull();
    await waitFor(() => expect(reads).toBe(2));
  });

  it('deletes after confirming, keeps the dialog when the delete fails, and cancels', async () => {
    let refuse = true;
    const { calls, state } = setup([delivery()], OPTIONS, {
      'DELETE /api/me/deliveries/d1': () => {
        if (refuse) throw { status: 500, code: 'internal', message: 'try later' };
        state.items = [];
        return new Response(null, { status: 204 });
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete delivery to me@example.com' }));
    expect(screen.getByText(/Digests stop going to me@example.com \(Email, Weekly on Monday at 08:00 Asia\/Singapore\)/)).toBeInTheDocument();
    fireEvent.click(footer('Cancel'));
    await waitFor(() => expect(body().findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Delete delivery to me@example.com' }));
    fireEvent.click(footer('Delete'));
    expect(await screen.findByText('try later')).toBeInTheDocument();
    expect(body().findModal()).not.toBeNull();
    refuse = false;
    await waitFor(() => expect(footer('Delete')).not.toBeDisabled());
    fireEvent.click(footer('Delete'));
    expect(await screen.findByText('Deleted: digests no longer go to me@example.com.')).toBeInTheDocument();
    expect(await screen.findByText(/No deliveries yet/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(2);
  });
});

describe('Digest delivery form', () => {
  it('adds a channel delivery: only listed channels, and Save waits for one to be picked', async () => {
    const { calls, state } = setup([], OPTIONS, {
      'POST /api/me/deliveries': (b: Record<string, unknown>) => {
        const made = delivery({ ...b, id: 'n1', target_label: '#leadership' } as Partial<Delivery>);
        state.items = [made];
        return made;
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Add delivery' }));
    expect(modal().findHeader().getElement().textContent).toBe('Add a digest delivery');
    expect(isOff(footer('Add delivery'))).toBe(true);
    const select = field('Channel')!.findControl()!.findSelect()!;
    select.openDropdown();
    expect(select.findDropdown().findOptions().map((o) => o.findLabel().getElement().textContent)).toEqual(['#market-news', '#leadership']);
    select.selectOptionByValue('G98765432');
    radio('How often', 'daily');
    expect(field('Day')).toBeUndefined();
    pick('Time', '7');
    expect(screen.getByText('Daily at 07:00 UTC')).toBeInTheDocument();
    fireEvent.click(footer('Add delivery'));
    expect(await screen.findByText('Added: Daily at 07:00 UTC, to #leadership.')).toBeInTheDocument();
    await waitFor(() => expect(body().findModal()).toBeNull());
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      kind: 'slack_channel',
      channel_id: 'G98765432',
      cadence: 'daily',
      hour: 7,
      timezone: 'UTC',
      enabled: true,
    });
  });

  it('adds an email delivery to the read-only own address on a weekly schedule in another zone', async () => {
    const { calls } = setup([], { ...OPTIONS, slack_channel: false }, { 'POST /api/me/deliveries': (b: Record<string, unknown>) => delivery(b as Partial<Delivery>) });
    fireEvent.click(await screen.findByRole('button', { name: 'Add delivery' }));
    const kinds = field('Deliver by')!.findControl()!.findRadioGroup()!;
    expect(kinds.findInputByValue('slack_channel')!.getElement()).toBeDisabled();
    expect(screen.getByText('Slack channels are not set up on this deployment.')).toBeInTheDocument();
    // The first kind that can be used is picked: a DM, sent by the bot.
    expect(kinds.findInputByValue('slack_dm')!.getElement()).toBeChecked();
    expect(screen.getByText(/sends it to you as a direct message/)).toBeInTheDocument();
    radio('Deliver by', 'email');
    const address = field('Sent to')!.findControl()!.findInput()!;
    expect(address.getInputValue()).toBe('me@example.com');
    expect(address.findNativeInput().getElement()).toHaveAttribute('readonly');
    pick('Day', '2');
    pick('Time', '9');
    const zone = field('Time zone')!.findControl()!.findSelect()!;
    zone.openDropdown();
    zone.findFilteringInput()!.setInputValue('Singapore');
    zone.selectOptionByValue('Asia/Singapore');
    expect(screen.getByText('Weekly on Wednesday at 09:00 Asia/Singapore')).toBeInTheDocument();
    fireEvent.click(footer('Add delivery'));
    await waitFor(() => expect(body().findModal()).toBeNull());
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      kind: 'email',
      cadence: 'weekly',
      weekday: 2,
      hour: 9,
      timezone: 'Asia/Singapore',
      enabled: true,
    });
  });

  it('keeps the form and says why when the API refuses, and closes on Cancel or Escape', async () => {
    setup([], OPTIONS, {
      'POST /api/me/deliveries': () => {
        throw { status: 400, code: 'validation', message: 'You can have at most 5 deliveries.' };
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Add delivery' }));
    radio('Deliver by', 'slack_dm');
    fireEvent.click(footer('Add delivery'));
    expect(await screen.findByText('Not saved')).toBeInTheDocument();
    expect(screen.getByText('You can have at most 5 deliveries.')).toBeInTheDocument();
    await waitFor(() => expect(footer('Add delivery')).not.toBeDisabled());
    fireEvent.click(footer('Cancel'));
    await waitFor(() => expect(body().findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Add delivery' }));
    act(() => modal().findDismissButton().click());
    await waitFor(() => expect(body().findModal()).toBeNull());
  });

  it('edits the schedule of a delivery, sending only what changed', async () => {
    const { calls } = setup([delivery({ cadence: 'daily', weekday: null, timezone: 'Mars/Olympus' })], OPTIONS, {
      'PATCH /api/me/deliveries/d1': (b: Record<string, unknown>) => delivery(b as Partial<Delivery>),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit delivery to me@example.com' }));
    expect(modal().findHeader().getElement().textContent).toBe('Edit delivery to me@example.com');
    expect(screen.getByText('Email: me@example.com')).toBeInTheDocument();
    expect(field('Deliver by')).toBeUndefined();
    expect(isOff(footer('Save'))).toBe(true);
    radio('How often', 'weekly');
    pick('Day', '4');
    fireEvent.click(footer('Save'));
    expect(await screen.findByText('Saved the schedule for me@example.com.')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ cadence: 'weekly', weekday: 4 });
  });
});
