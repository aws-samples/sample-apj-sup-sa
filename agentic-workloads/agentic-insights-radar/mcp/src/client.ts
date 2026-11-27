// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Agentic Insights Radar API calls made as the signed-in user. */

import { type Fetch, HttpError, type Reply, send } from './http.js';
import { cpSlice, isObject, pyStr, pyStrip, quote } from './py.js';
import type { RequestSigner } from './webIdentity.js';

export interface TokenSource {
  idToken(): Promise<string>;
}

export class ApiError extends Error {
  override name = 'ApiError';
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(`${status}: ${message}`);
  }
}

// What a graph id is made of: a lowercase type and a printable-ASCII id without spaces (a uuid7, or a fixed id
// such as 'sample-01'). Anything else is refused here: the API's router answered a non-ASCII id with "no route
// for <its own path>" (MCP test, 2026-11-03). Surrounding space a model pasted is dropped first.
const NODE_ID = /^[a-z_]{1,40}\/[!-.0-~]{1,200}$/;

export const NODE_ID_RULE =
  "node ids are '<type>/<id>': a lowercase type, a slash and the id exactly as search or list results " +
  "return it, for example 'signal/01a17bc4-2354-7d0d-b671-cb65f2a48b13'";

export function nodePath(nodeId: string): string {
  const id = pyStrip(nodeId);
  const slash = id.indexOf('/');
  const uid = id.slice(slash + 1);
  if (!NODE_ID.test(id) || uid === '.' || uid === '..') throw new ApiError(400, NODE_ID_RULE);
  return `${quote(id.slice(0, slash))}/${quote(uid)}`;
}

/** What an HTML reply means: CloudFront or the WAF answered, not the API, and its markup says nothing
 * useful to the model (CTR QA-007). */
export const EDGE_REJECTED = 'edge_rejected: refused before the API (HTML reply)';
// A 403 from the edge is the web firewall. Most often it blocked request text that looks like markup or code
// (MCP test), but an address on AWS's IP reputation list gets the same 403 (round 33 code review).
export const FIREWALL_HINT =
  "; the deployment's firewall refused it: usually text that looks like markup or code, so rephrase without it." +
  ' If plain text is refused too, this network address is blocked: tell the user';
// A 429 that does not say why (API Gateway's throttle, an old edge): rephrasing only adds requests.
export const RATE_HINT = '; too many requests: wait a few minutes before trying again';

function isHtml(res: Reply): boolean {
  return (res.headers.get('content-type') ?? '').includes('text/html') || res.text.trimStart().startsWith('<');
}

export function edge(res: Reply): string {
  return EDGE_REJECTED + (res.status === 403 ? FIREWALL_HINT : '');
}

function parseJson(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export class InsightsRadarApi {
  private readonly base: string;

  constructor(
    baseUrl: string,
    private readonly tokens: TokenSource | RequestSigner | null,
    private readonly fetchFn: Fetch = globalThis.fetch,
  ) {
    this.base = baseUrl.replace(/\/+$/, '');
  }

  async request(method: string, path: string, body?: unknown, query?: Query | null, text = false): Promise<unknown> {
    if (this.tokens === null) throw new ApiError(401, 'not signed in');
    let headers: Record<string, string> = { accept: 'application/json' };
    let payload: string | undefined;
    if (body !== undefined && body !== null) {
      headers['content-type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query ?? {})) {
      if (v !== null && v !== undefined && v !== '') params.append(k, String(v));
    }
    const qs = params.toString();
    let url = `${this.base}${path}${qs ? `?${qs}` : ''}`;
    if ('sign' in this.tokens) {
      // Signed for the API Gateway host (SigV4 covers it), on the routes behind its IAM authorizer. The query
      // is sent RFC 3986 encoded, as SigV4 signs it: URLSearchParams writes a space as '+', which SigV4 reads
      // as a literal plus, so a query with a space would not match its signature.
      const signedQs = [...params].map(([k, v]) => `${quote(k)}=${quote(v)}`).join('&');
      url = `${this.tokens.apiUrl}/iam${path}${signedQs ? `?${signedQs}` : ''}`;
      headers = await this.tokens.sign(method, url, headers, payload);
    } else {
      headers.authorization = `Bearer ${await this.tokens.idToken()}`;
    }
    const res = await send(this.fetchFn, url, {
      method,
      headers,
      body: payload,
      timeoutMs: 60_000,
    });
    if (res.status >= 400) {
      const parsed = parseJson(res.text);
      const reply = parsed.ok ? parsed.value : undefined;
      let message: string;
      if (isObject(reply)) {
        message = 'message' in reply ? pyStr(reply.message) : cpSlice(res.text, 200);
      } else {
        message = isHtml(res) ? edge(res) : cpSlice(res.text, 200);
      }
      if (res.status === 429 && !(isObject(reply) && 'code' in reply)) {
        // Not the API's own 429 (a quota or a busy workspace says its own cause and wait, round 36
        // review), nor the firewall's, which has a code too: API Gateway's bare throttle, or an old edge.
        message = (message || 'too many requests') + RATE_HINT;
      }
      throw new ApiError(res.status, message);
    }
    if (text) return res.text;
    const parsed = parseJson(res.text);
    if (!parsed.ok) {
      // A 200 page from CloudFront or a WAF used to surface as a bare JSON parse error (CTR QA-048).
      throw new ApiError(res.status, isHtml(res) ? edge(res) : 'the API reply is not JSON');
    }
    return parsed.value;
  }

  /** The deployment's public configuration (no token is sent). */
  async config(): Promise<unknown> {
    const res = await send(this.fetchFn, `${this.base}/api/config`, { timeoutMs: 60_000 });
    if (res.status < 200 || res.status >= 300) {
      const kind = res.status >= 500 ? 'Server error' : res.status >= 400 ? 'Client error' : 'Redirect response';
      throw new HttpError(`${kind} '${`${res.status} ${res.statusText}`.trim()}' for url '${res.url}'`);
    }
    return JSON.parse(res.text);
  }
}
