// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about requests that get no answer (F3-014, F3-015, F3-016, F3-043). */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiClient, ApiError, BLOCKED_MESSAGE, noAnswerMessage, REQUEST_TIMEOUT_MS } from './client';

const client = (fetcher: (url: string, init?: RequestInit) => Promise<Response>) => new ApiClient(() => 'tok', fetcher as unknown as typeof fetch);
const caught = (p: Promise<unknown>) => p.then(() => null, (e: unknown) => e as ApiError);

afterEach(() => vi.restoreAllMocks());

describe('F3-014: a gateway error page on a write may have been saved', () => {
  it('says so for a write, and only that it failed for a read', async () => {
    const page = async () => new Response('<html>504 Gateway Timeout</html>', { status: 504 });
    const write = await caught(client(page).saveEntries('r', [], 1));
    expect(write).toMatchObject({ status: 504, code: 'gateway', uncertain: true });
    expect((write as ApiError).message).toMatch(/may or may not have been saved/);
    const read = await caught(client(page).me());
    expect((read as ApiError).message).toBe('The server did not answer properly (504).');
    const json = await caught(client(async () => new Response('{"code":"conflict","message":"x"}', { status: 409 })).me());
    expect((json as ApiError).uncertain).toBe(false);
  });
});

describe('F3-015: an HTML 403 is worded neutrally', () => {
  it('does not tell a throttled office to remove an attack', async () => {
    const err = await caught(client(async () => new Response('<html>', { status: 403 })).me());
    expect((err as ApiError).message).toBe(BLOCKED_MESSAGE);
    expect(BLOCKED_MESSAGE).not.toMatch(/looks like an attack/);
    expect(BLOCKED_MESSAGE).toMatch(/Wait a minute and try again/);
  });
});

describe('F3-016: every request has a timeout', () => {
  it('sends a signal and turns a timeout into a clear error', async () => {
    const seen: (AbortSignal | null | undefined)[] = [];
    const timeout = vi.spyOn(AbortSignal, 'timeout');
    const err = await caught(
      client(async (_url, init) => {
        seen.push(init?.signal);
        throw new DOMException('signal timed out', 'TimeoutError');
      }).saveEntries('r', [], 1),
    );
    expect(timeout).toHaveBeenCalledWith(REQUEST_TIMEOUT_MS);
    expect(seen[0]).toBeInstanceOf(AbortSignal);
    expect(err).toMatchObject({ status: 0, code: 'timeout', uncertain: true });
    expect((err as ApiError).message).toBe(noAnswerMessage('timeout', true));
  });
});

describe('F3-043: a dropped connection is said in plain words', () => {
  it('maps the browser TypeError, and says offline when the browser knows', async () => {
    const drop = async () => {
      throw new TypeError('Load failed');
    };
    expect(await caught(client(drop).me())).toMatchObject({ code: 'network', message: 'No connection: the server could not be reached. Check your network and try again.' });
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    expect(await caught(client(drop).me())).toMatchObject({ message: 'No connection: you are offline. Check your network and try again.' });
  });
});
