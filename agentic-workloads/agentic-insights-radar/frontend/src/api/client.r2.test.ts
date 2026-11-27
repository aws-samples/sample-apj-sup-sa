// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-012, F2-013, F2-014, F2-015: what the client does with 401s, 429s, 503s and cursors. */
import { describe, expect, it, vi } from 'vitest';

import { ApiClient, ApiError, MAX_PAGES, retryAfterOf } from './client';
import { describeError, waitFor } from '@/components/Notifications';

function client(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => handler(url, init));
  return { api: new ApiClient(() => 'id-token', fetcher as unknown as typeof fetch), fetcher };
}

const json = (status: number, body: object, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers });

describe('F2-012: a 401 is marked, so callers do not toast behind the dialog', () => {
  it('marks a 401 and nothing else', async () => {
    const { api } = client(() => json(401, { code: 'unauthorized', message: 'Unauthorized' }));
    await expect(api.radars()).rejects.toMatchObject({ sessionEnded: true });
    const other = client(() => json(403, { code: 'forbidden', message: 'forbidden' }));
    await expect(other.api.radars()).rejects.toMatchObject({ sessionEnded: false });
  });

  it('is skipped by the error toast but other failures are not', () => {
    // Inline messages say the session ended, in words (r3 F3-044), never the raw "Unauthorized".
    expect(describeError(new ApiError(401, 'unauthorized', 'Unauthorized'))).toBe('Your session has expired: sign in again, then try again.');
    expect(describeError(new ApiError(401, 'account_disabled', 'Unauthorized'))).toBe('Your account is disabled, so this could not be done.');
    expect(new ApiError(401, 'unauthorized', 'Unauthorized').sessionEnded).toBe(true);
  });
});

describe('F2-014: a capped list of runs says so on the page', () => {
  it('counts the runs it read and marks the list', async () => {
    const { api } = client(() => json(200, { items: [{ run_id: 'r' }], cursor: 'more' }));
    const out = await api.moduleRuns('scout');
    expect(out.truncated).toBe(true);
    expect(out.items).toHaveLength(MAX_PAGES);
  });
});

describe('F2-015: rate limits and throttles say when to come back', () => {
  it('reads the wait from the body, then from the header, and from neither', () => {
    expect(retryAfterOf(429, { retry_after: 40 }, new Headers())).toBe(40);
    expect(retryAfterOf(503, {}, new Headers({ 'retry-after': '2' }))).toBe(2);
    expect(retryAfterOf(503, {}, new Headers())).toBeNull();
    expect(retryAfterOf(503, { retry_after: -1 }, new Headers({ 'retry-after': 'soon' }))).toBeNull();
    expect(retryAfterOf(400, { retry_after: 40 }, new Headers())).toBeNull(); // only a 429 or a 503
  });

  it('turns the wait into words', () => {
    expect(waitFor(0)).toBe('in a moment');
    expect(waitFor(40)).toBe('in 40 seconds');
    expect(waitFor(60)).toBe('in a minute');
    expect(waitFor(150)).toBe('in 3 minutes');
    expect(waitFor(7200)).toBe('in 2 hours');
    expect(waitFor(86_400)).toBe('tomorrow');
  });

  it('says it in the toast, as a sentence', () => {
    const err = new ApiError(429, 'rate_limited', 'too many requests; wait a minute and try again', { limit_per_minute: 60 }, 40);
    expect(describeError(err)).toBe('Too many requests; wait a minute and try again. Try again in 40 seconds.');
    expect(describeError(new ApiError(400, 'bad', 'row 2: title is required'))).toBe('row 2: title is required');
  });

  it('sends a GET again once after a 503, and gives up after the second', async () => {
    let hits = 0;
    const { api, fetcher } = client(() => {
      hits += 1;
      return hits === 1 ? json(503, { code: 'throttled', message: 'busy' }) : json(200, { items: [] });
    });
    await expect(api.radars()).resolves.toEqual({ items: [] });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const always = client(() => json(503, { code: 'throttled', message: 'busy' }));
    await expect(always.api.radars()).rejects.toThrow('busy');
    expect(always.fetcher).toHaveBeenCalledTimes(2);
  });

  it('does not send a write again, or a GET that failed another way', async () => {
    const post = client(() => json(503, { code: 'throttled', message: 'busy' }));
    await expect(post.api.createProduct({})).rejects.toThrow('busy');
    expect(post.fetcher).toHaveBeenCalledTimes(1);
    const limited = client(() => json(429, { code: 'rate_limited', message: 'too many requests' }));
    await expect(limited.api.radars()).rejects.toThrow('too many requests');
    expect(limited.fetcher).toHaveBeenCalledTimes(1);
  });

  it('waits no longer than the cap the API asks for', async () => {
    const waits: number[] = [];
    class Timed extends ApiClient {
      protected override wait(ms: number): Promise<void> {
        waits.push(ms);
        return Promise.resolve();
      }
    }
    const fetcher = vi.fn(async () => json(503, { code: 'throttled', message: 'busy' }, { 'retry-after': '600' }));
    const api = new Timed(() => 'id-token', fetcher as unknown as typeof fetch);
    await expect(api.radars()).rejects.toThrow('busy');
    expect(waits).toEqual([2000]);
  });
});

describe('F2-013: a failed page after the first keeps what was read', () => {
  it('returns the pages already read and marks the list incomplete', async () => {
    let page = 0;
    const { api } = client(() => {
      page += 1;
      if (page <= 2) return json(200, { items: [{ username: `u${page}`, email: `u${page}@example.com` }], cursor: String(page) });
      return json(500, { code: 'error', message: 'page 3 is broken' });
    });
    const out = await api.users();
    expect(out.items.map((u) => u.username)).toEqual(['u1', 'u2']);
    expect(out.truncated).toBe(true);
  });

  it('still reports a first page that fails, so the caller can offer Retry', async () => {
    const { api } = client(() => json(500, { code: 'error', message: 'page 1 is broken' }));
    await expect(api.users()).rejects.toThrow('page 1 is broken');
  });
});

describe('F2-014: the runs lists follow their cursor', () => {
  it('reads every page of a module\'s runs and of all runs', async () => {
    const paged = (total: number) => (url: string) => {
      const n = Number(new URL(url, 'https://app.example.com').searchParams.get('cursor') ?? 0);
      return json(200, { items: [{ run_id: `r${n}` }], cursor: n + 1 < total ? String(n + 1) : null });
    };
    const mine = client(paged(3));
    expect((await mine.api.moduleRuns('scout')).items.map((r) => r.run_id)).toEqual(['r0', 'r1', 'r2']);
    const all = client(paged(2));
    expect((await all.api.runs()).items).toHaveLength(2);
    expect(all.fetcher).toHaveBeenCalledTimes(2);
  });

  it('stops at the page cap and says the list is incomplete', async () => {
    const { api } = client(() => json(200, { items: [{ run_id: 'r' }], cursor: 'more' }));
    const out = await api.runs();
    expect(out.items).toHaveLength(MAX_PAGES);
    expect(out.truncated).toBe(true);
  });
});
