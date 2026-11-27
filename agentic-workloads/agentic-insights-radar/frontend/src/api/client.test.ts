// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it, vi } from 'vitest';

import { ApiClient, ApiError, BLOCKED_MESSAGE, nodePath, USERS_LIMIT } from './client';

function client(response: Response | (() => Response), token: () => string | undefined = () => 'tok') {
  const fetcher = vi.fn(async () => (typeof response === 'function' ? response() : response));
  return { api: new ApiClient(token, fetcher as unknown as typeof fetch), fetcher };
}

describe('ApiClient', () => {
  it('sends the bearer token, JSON body and query', async () => {
    const { api, fetcher } = client(new Response(JSON.stringify({ items: [] })));
    await api.insights('insight:pricing_move', { sort: 'newest', q: '', cursor: undefined });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/insights/insight:pricing_move?sort=newest');
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok');
    const { api: api2, fetcher: f2 } = client(new Response('{}'), () => undefined);
    await api2.feedback('gap/a b', 'down', 'why');
    const [url2, init2] = f2.mock.calls[0] as unknown as [string, RequestInit];
    expect(url2).toBe('/api/nodes/gap/a%20b/feedback');
    expect(init2.body).toBe(JSON.stringify({ vote: 'down', comment: 'why' }));
    expect((init2.headers as Record<string, string>).authorization).toBeUndefined();
  });

  it('withdraws a vote with DELETE and asks for the capped users page', async () => {
    const { api, fetcher } = client(() => new Response(JSON.stringify({ up: 1, down: 0, mine: null })));
    expect(await api.withdrawFeedback('signal/1')).toEqual({ up: 1, down: 0, mine: null });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/nodes/signal/1/feedback');
    expect(init.method).toBe('DELETE');
    expect(init.body).toBeUndefined();
    await api.users();
    expect((fetcher.mock.calls[1] as unknown as [string])[0]).toBe(`/api/users?limit=${USERS_LIMIT}`);
  });

  it('turns error bodies into ApiError', async () => {
    const { api } = client(new Response(JSON.stringify({ code: 'validation_failed', message: 'bad', details: { errors: ['x'] } }), { status: 400 }));
    await expect(api.me()).rejects.toMatchObject({ status: 400, code: 'validation_failed', message: 'bad', details: { errors: ['x'] } });
    const { api: api2 } = client(new Response('<html>', { status: 500, statusText: 'Internal Server Error' }));
    const err = (await api2.me().catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('error');
    expect(err.message).toBe('Internal Server Error');
    // Details that are not an object (a proxy's body) become empty details (scan cycle 19).
    // A JSON body that is not an object is handled like a non-JSON one: the WAF's 403 is still "blocked" (c21).
    const { api: api5 } = client(new Response('"Forbidden"', { status: 403, statusText: 'Forbidden' }));
    expect(((await api5.me().catch((e: unknown) => e)) as ApiError).code).toBe('blocked');
    // API Gateway's own object (no string code) is not the API's answer: its 403 is "blocked" (scan cycle 22).
    const { api: api6 } = client(new Response('{"message":"Forbidden"}', { status: 403, statusText: 'Forbidden' }));
    expect(((await api6.me().catch((e: unknown) => e)) as ApiError).code).toBe('blocked');
    // A JSON body that is not an object at all is an error with the status text (scan cycle 20).
    for (const body of ['null', '[1]', '"x"']) {
      const { api: api4 } = client(new Response(body, { status: 500, statusText: 'Internal Server Error' }));
      const e4 = (await api4.me().catch((e: unknown) => e)) as ApiError;
      expect(e4).toBeInstanceOf(ApiError);
      expect(e4.details).toEqual({});
    }
    for (const details of ['text', 7, ['x'], null]) {
      const { api: api3 } = client(new Response(JSON.stringify({ code: 'rate_limited', message: 'slow', details }), { status: 429 }));
      const e3 = (await api3.me().catch((e: unknown) => e)) as ApiError;
      expect(e3.details).toEqual({});
    }
  });

  it('reports a 401 to the session (so it can ask the user to sign in again)', async () => {
    const { api } = client(() => new Response('{"code":"unauthorized","message":"Unauthorized"}', { status: 401 }));
    await expect(api.me()).rejects.toMatchObject({ status: 401 }); // no handler yet: still just an error
    const expired = vi.fn();
    api.setUnauthorizedHandler(expired);
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledTimes(1);
    expect(expired).toHaveBeenCalledWith('unauthorized');
    // CR-13: the code is passed on, and a 401 without a JSON body counts as an expired session.
    const { api: html } = client(() => new Response('<html>', { status: 401 }));
    html.setUnauthorizedHandler(expired);
    await expect(html.me()).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenLastCalledWith('unauthorized');
  });

  it('still says what failed when an HTTP/2 error has no status text', async () => {
    const { api } = client(new Response('<html>', { status: 500, statusText: '' }));
    await expect(api.me()).rejects.toMatchObject({ status: 500, code: 'error', message: 'Request failed' });
  });

  it('explains a request the firewall blocked (an HTML 403, not the API JSON one)', async () => {
    const { api } = client(new Response('<html><body>403 Forbidden</body></html>', { status: 403 }));
    await expect(api.me()).rejects.toMatchObject({ status: 403, code: 'blocked', message: BLOCKED_MESSAGE });
    const { api: api2 } = client(new Response(JSON.stringify({ code: 'forbidden', message: 'no' }), { status: 403 }));
    await expect(api2.me()).rejects.toMatchObject({ code: 'forbidden', message: 'no' });
  });

  it('returns text for exports and drafts', async () => {
    const { api } = client(() => new Response('a,b\n'));
    expect(await api.exportEntries('r', 'csv')).toBe('a,b\n');
    expect(await api.draftMarkdown('content_draft/1')).toBe('a,b\n');
    expect(await api.importTemplate('r')).toBe('a,b\n');
  });

  it('builds node paths', () => {
    expect(nodePath('signal/01a-b')).toBe('signal/01a-b');
    expect(nodePath('weird')).toBe('weird/');
    expect(nodePath('../..')).toBe('-/-');
    expect(nodePath('signal/..')).toBe('signal/-');
  });

  it('covers every route helper', async () => {
    const { api, fetcher } = client(() => new Response('{"entries":[]}'));
    const calls: Promise<unknown>[] = [
      api.config(), api.nav(), api.chatStatus(), api.insight('gap/1'), api.setWorking('content_idea/1', true),
      api.setWorking('content_idea/1', false), api.requestDraft('content_idea/1', ''), api.requestDraft('content_idea/1', 'b'),
      api.search('q'), api.node('a/b'), api.deleteNode('a/b'), api.neighbors('a/b'), api.explore(), api.radarTemplates(),
      api.radars(), api.createRadar({ slug: 's', name: 'n' }), api.radar('s', 2), api.updateRadar('s', { name: 'x' }, 1),
      api.saveEntries('s', [], 1), api.checkImport('s', 'csv', 'x'), api.importEntries('s', 'json', '[]', 1, 'merge'), api.importEntries('s', 'json', '[]', 1, 'replace', true),
      api.versions('s'), api.rollback('s', 1, 2), api.diff('s', 1, 2), api.updateSchema('s', {} as never, 1, {}, false),
      api.setAccess('s', [], 1), api.deleteRadar('s'), api.suggestions('s'), api.acceptSuggestion('s', 'k'),
      api.acceptSuggestion('s', 'k', { ring: 'x' }), api.discardSuggestion('s', 'k'), api.products(), api.product('p'),
      api.createProduct({}), api.updateProduct('p', {}, 1), api.deleteProduct('p'), api.resyncProduct('p'),
      api.askProduct('p', 'q'), api.documents('p'), api.presignUpload('p', 'f', 1), api.completeUpload('p', 'd'),
      api.deleteDocument('p', 'd'), api.models(), api.modules(), api.module('m'), api.createModule({}),
      api.updateModule('m', {}, 1), api.deleteModule('m'), api.runModule('m'), api.moduleRuns('m'), api.moduleRunsFirst('m'), api.runs(),
      api.sources('m'), api.addSource('m', {}), api.updateSource('m', 's', {}), api.deleteSource('m', 's'), api.users(),
      api.inviteUser('a@example.com', 'viewer'), api.updateUser('u', { role: 'admin' }), api.deleteUser('u'), api.andon(),
      api.setAndon(false, ''), api.setAndon(true, 'r'), api.chatLimits(), api.setChatLimits({ daily_tokens: 1, per_user_daily_tokens: 1 }),
      api.audit('2026-10-02'), api.health(),
    ];
    await Promise.all(calls);
    expect(fetcher).toHaveBeenCalledTimes(calls.length);
  });
});
