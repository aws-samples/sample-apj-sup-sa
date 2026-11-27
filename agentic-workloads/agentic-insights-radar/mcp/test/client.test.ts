// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import { ApiError, edge, EDGE_REJECTED, InsightsRadarApi, nodePath } from '../src/client.js';
import { HttpError } from '../src/http.js';
import { quoteKind, seg } from '../src/toolKit.js';
import { networkError, recorder, STATIC } from './helpers.js';

function api(routes: Parameters<typeof recorder>[0]) {
  const rec = recorder(routes);
  return { api: new InsightsRadarApi('https://api.example.com/', STATIC, rec.fetch), calls: rec.calls };
}

async function failureOf(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    return err as ApiError;
  }
  throw new Error('expected a failure');
}

const reply = (status: number, headers: Record<string, string> = {}) => ({
  status,
  statusText: '',
  headers: new Headers(headers),
  text: '<html/>',
  url: '',
});

describe('InsightsRadarApi', () => {
  it('api client', async () => {
    const { api: client, calls } = api({
      'GET /api/me': { json: { role: 'viewer' } },
      'GET /api/config': { json: { cognito_domain: 'd' } },
      'GET /api/bad': { status: 403, json: { message: 'nope' } },
      'GET /api/html': { status: 502, text: '<html>' },
      'GET /api/edge': { status: 200, html: '<html><body>Request blocked</body></html>' },
      'GET /api/junk': { status: 200, text: 'not json' },
      'GET /api/plain': { status: 500, text: 'Internal Server Error' },
      'GET /api/text': { status: 200, text: 'a,b' },
      'GET /api/slow': { status: 429, text: '' },
      'GET /api/throttled': { status: 429, json: { message: 'Too Many Requests' } },
      'GET /api/waf429': {
        status: 429,
        json: { code: 'rate_limited', message: 'Too many requests. Wait about five minutes.' },
      },
      'GET /api/quota': { status: 429, json: { code: 'rate_limited', message: 'try again tomorrow' } },
    });
    expect(await client.request('GET', '/api/me', undefined, { x: null, y: 1 })).toEqual({ role: 'viewer' });
    expect(calls[0].headers.get('authorization')).toBe('Bearer tok');
    expect(calls[0].headers.get('accept')).toBe('application/json');
    expect(calls[0].url.search).toBe('?y=1');
    expect(await client.config()).toEqual({ cognito_domain: 'd' });
    expect(calls[1].headers.get('authorization')).toBeNull(); // the public endpoint gets no token
    expect(await client.request('GET', '/api/text', undefined, null, true)).toBe('a,b');
    expect((await failureOf(client.request('GET', '/api/bad'))).message).toBe('403: nope');
    // CTR QA-007/QA-048: an HTML reply from CloudFront or the WAF is named, not pasted or a decode error.
    for (const [p, status] of [
      ['/api/html', 502],
      ['/api/edge', 200],
    ] as const) {
      expect((await failureOf(client.request('GET', p))).message).toBe(
        `${status}: edge_rejected: refused before the API (HTML reply)`,
      );
    }
    expect((await failureOf(client.request('GET', '/api/junk'))).message).toBe('200: the API reply is not JSON');
    expect((await failureOf(client.request('GET', '/api/plain'))).message).toBe('500: Internal Server Error');
    // A bare 429 (no body) still says to wait (round 34 code review).
    expect((await failureOf(client.request('GET', '/api/slow'))).message).toMatch(
      /^429: too many requests; too many requests: wait a few minutes/,
    );
    // API Gateway's own throttle is JSON too (round 35 review); the firewall's already says to wait.
    expect((await failureOf(client.request('GET', '/api/throttled'))).message).toMatch(
      /^429: Too Many Requests; too many requests: wait a few minutes/,
    );
    expect((await failureOf(client.request('GET', '/api/waf429'))).message).toBe(
      '429: Too many requests. Wait about five minutes.',
    );
    // The API's own 429s say their own cause and wait: no hint added (round 36 code review).
    expect((await failureOf(client.request('GET', '/api/quota'))).message).toBe('429: try again tomorrow');
    const signedOut = await failureOf(new InsightsRadarApi('https://x', null).request('GET', '/api/me'));
    expect(signedOut.status).toBe(401);
    expect(signedOut.message).toBe('401: not signed in');
    for (const bad of ['bad', '../x', 'gap/..', './1']) {
      expect(() => nodePath(bad)).toThrow(ApiError);
    }
    for (const dots of ['.', '..']) {
      expect(() => seg(dots)).toThrow('not a valid name');
    }
    expect(nodePath('gap/a-b')).toBe('gap/a-b');
    expect(quoteKind('insight:x y')).toBe('insight:x%20y');
  });

  it('an error reply that is not an object still reports its status', async () => {
    const { api: client } = api({ 'GET /api/radars': { status: 502, json: ['bad', 'gateway'] } });
    const err = await failureOf(client.request('GET', '/api/radars'));
    expect(err.status).toBe(502);
    expect(err.message).toContain('bad');
  });

  it('an error object without a message quotes the reply, and a null message reads as Python wrote it', async () => {
    const { api: client } = api({
      'GET /api/a': { status: 400, json: { code: 'x' } },
      'GET /api/b': { status: 400, json: { message: null } },
      'GET /api/long': { status: 500, text: 'é'.repeat(300) },
    });
    expect((await failureOf(client.request('GET', '/api/a'))).message).toBe('400: {"code":"x"}');
    expect((await failureOf(client.request('GET', '/api/b'))).message).toBe('400: None');
    // Cut at 200 characters, not 200 UTF-16 units or bytes.
    expect((await failureOf(client.request('GET', '/api/long'))).message).toBe(`500: ${'é'.repeat(200)}`);
  });

  it('sends a JSON body, query values as text, and no body when there is none', async () => {
    const { api: client, calls } = api({ 'POST /api/x': { json: {} }, 'GET /api/y': { json: {} } });
    await client.request('POST', '/api/x', { a: 1 }, { flag: true, n: 0, empty: '', gone: undefined });
    expect(calls[0].body).toBe('{"a":1}');
    expect(calls[0].headers.get('content-type')).toBe('application/json');
    expect(calls[0].url.search).toBe('?flag=true&n=0');
    await client.request('GET', '/api/y', null);
    expect(calls[1].body).toBe('');
    expect(calls[1].headers.get('content-type')).toBeNull();
    expect(calls[1].url.search).toBe('');
  });

  it('does not follow redirects', async () => {
    const seen: RequestInit[] = [];
    const fetchFn = (async (_url: string, init: RequestInit) => {
      seen.push(init);
      return new Response(null, { status: 302, headers: { location: 'https://elsewhere.example' } });
    }) as unknown as typeof fetch;
    const client = new InsightsRadarApi('https://api.example.com', STATIC, fetchFn);
    expect((await failureOf(client.request('GET', '/api/me'))).message).toBe('302: the API reply is not JSON');
    expect(seen[0].redirect).toBe('manual');
  });

  it('a network failure names its cause', async () => {
    const { api: client } = api({ 'GET /api/me': networkError('getaddrinfo ENOTFOUND api.example.com') });
    await expect(client.request('GET', '/api/me')).rejects.toThrow(
      new HttpError('getaddrinfo ENOTFOUND api.example.com'),
    );
    const plain = recorder({ 'GET /api/me': new Error('socket hang up') });
    await expect(new InsightsRadarApi('https://x', STATIC, plain.fetch).request('GET', '/api/me')).rejects.toThrow(
      'socket hang up',
    );
    const odd = (async () => {
      throw 'odd';
    }) as unknown as typeof fetch;
    await expect(new InsightsRadarApi('https://x', STATIC, odd).request('GET', '/api/me')).rejects.toThrow('odd');
  });

  it('config raises for a status that is not 2xx and for a reply that is not JSON', async () => {
    const { api: client } = api({});
    await expect(client.config()).rejects.toThrow("Client error '404' for url 'https://api.example.com/api/config'");
    const down = recorder({ 'GET /api/config': { status: 502, text: 'x' } });
    await expect(new InsightsRadarApi('https://x', null, down.fetch).config()).rejects.toThrow(/^Server error '502'/);
    const moved = recorder({ 'GET /api/config': { status: 301, headers: { location: 'https://y' } } });
    await expect(new InsightsRadarApi('https://x', null, moved.fetch).config()).rejects.toThrow(
      /^Redirect response '301'/,
    );
    const html = recorder({ 'GET /api/config': { html: '<html>' } });
    await expect(new InsightsRadarApi('https://x', null, html.fetch).config()).rejects.toThrow(SyntaxError);
  });

  it('uses the platform fetch by default', () => {
    expect(new InsightsRadarApi('https://x', null)).toBeInstanceOf(InsightsRadarApi);
  });
});

describe('node ids and the edge', () => {
  it('mcp test findings 2026-11-03', () => {
    // The 109-query MCP test on a live deployment: ids, depth, the firewall's 403, and list_modules' size.
    const rule = /node ids are .<type>\/<id>.: a lowercase type/;
    expect(() => nodePath('topic/äöü')).toThrow(rule); // non-ASCII: the router answered with its own path
    expect(() => nodePath('topic/..')).toThrow(rule);
    // Ids have no spaces: a pasted id's surrounding space is dropped, one inside it is refused (round 33 review).
    expect(nodePath(' organization/acme-1\n')).toBe('organization/acme-1');
    expect(() => nodePath('organization/acme 1')).toThrow(rule);
    expect(edge(reply(403, { 'content-type': 'text/html' }))).toContain('firewall');
    expect(edge(reply(403, { 'content-type': 'text/html' }))).toContain('network address is blocked');
    expect(edge(reply(502, { 'content-type': 'text/html' }))).not.toContain('firewall');
    expect(edge(reply(502))).toBe(EDGE_REJECTED);
    // The firewall's rate limit answers 429, with no "rephrase" advice (round 33 code review).
    expect(edge(reply(429, { 'content-type': 'text/html' }))).not.toContain('rephrase');
  });

  it('quotes ids and segments as Python quote(safe="") does', () => {
    expect(nodePath("topic/a!b'c(d)e*f~g")).toBe('topic/a%21b%27c%28d%29e%2Af~g');
    expect(seg('a b/c?d')).toBe('a%20b%2Fc%3Fd');
    expect(seg('ré')).toBe('r%C3%A9');
    expect(() => nodePath(`${'a'.repeat(41)}/x`)).toThrow(ApiError);
    expect(() => nodePath(`a/${'x'.repeat(201)}`)).toThrow(ApiError);
    expect(nodePath(`a_b/${'x'.repeat(200)}`)).toBe(`a_b/${'x'.repeat(200)}`);
    expect(() => nodePath('Topic/1')).toThrow(ApiError);
    expect(() => nodePath('topic/a/b')).toThrow(ApiError);
  });
});
