// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Test doubles: a recording fetch, a fixed token source, JWTs, and an MCP client wired to a server. */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';

import { InsightsRadarApi } from '../src/client.js';
import type { Fetch } from '../src/http.js';

export const COGNITO_DOMAIN = 'mr.auth.us-east-1.amazoncognito.com';
export const STACK_DOMAIN = 'air-111122223333.auth.us-east-1.amazoncognito.com';

export function b64url(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString('base64url');
}

export function jwt(exp: number, extra: Record<string, unknown> = {}): string {
  return `${b64url({ alg: 'none' })}.${b64url({ exp: Math.trunc(exp), sub: 'u', ...extra })}.sig`;
}

export interface Recorded {
  method: string;
  url: URL;
  headers: Headers;
  body: string;
}

/** A reply as a test writes it: a status and a JSON value, text, or HTML. */
export interface Reply {
  status?: number;
  json?: unknown;
  text?: string;
  html?: string;
  headers?: Record<string, string>;
}

export function reply(spec: Reply): Response {
  const headers = new Headers(spec.headers);
  let body: string | null = null;
  if (spec.json !== undefined) {
    body = JSON.stringify(spec.json);
    if (!headers.has('content-type')) headers.set('content-type', 'application/json');
  } else if (spec.html !== undefined) {
    body = spec.html;
    if (!headers.has('content-type')) headers.set('content-type', 'text/html; charset=utf-8');
  } else if (spec.text !== undefined) {
    body = spec.text;
    if (!headers.has('content-type')) headers.set('content-type', 'text/plain; charset=utf-8');
  }
  return new Response(body, { status: spec.status ?? 200, headers });
}

export type Route = Reply | ((req: Recorded) => Reply | Promise<Reply>) | Error;

export interface Recorder {
  fetch: Fetch;
  calls: Recorded[];
}

/**
 * A fetch that answers from `routes` ("METHOD /path" -> a reply, a function of the request, or an Error to
 * throw as a network failure), or from `fallback` for every request. Unknown routes are a 404.
 */
export function recorder(routes: Record<string, Route> = {}, fallback?: Route): Recorder {
  const calls: Recorded[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input));
    const req: Recorded = {
      method: init?.method ?? 'GET',
      url,
      headers: new Headers(init?.headers),
      body: typeof init?.body === 'string' ? init.body : '',
    };
    calls.push(req);
    let route = fallback ?? routes[`${req.method} ${url.pathname}`] ?? { status: 404, json: { message: 'no route' } };
    if (route instanceof Error) throw route;
    if (typeof route === 'function') route = await route(req);
    return reply(route);
  }) as Fetch;
  return { fetch, calls };
}

/** The error undici's fetch raises for a network failure: "fetch failed", the real reason in `cause`. */
export function networkError(reason: string): TypeError {
  return new TypeError('fetch failed', { cause: new Error(reason) });
}

export const STATIC = { idToken: async () => 'tok' };

export function api(fetch: Fetch, tokens: { idToken(): Promise<string> } | null = STATIC): InsightsRadarApi {
  return new InsightsRadarApi('https://api.example.com', tokens, fetch);
}

export async function connect(server: McpServer): Promise<Client> {
  const [serverSide, clientSide] = InMemoryTransport.createLinkedPair();
  await server.connect(serverSide);
  const client = new Client({ name: 'test', version: '1.0.0' });
  await client.connect(clientSide);
  return client;
}

export interface Outcome {
  isError: boolean;
  text: string;
  structured?: unknown;
}

/** What the client gets: an error result (isError, the fenced message) or the tool's result. */
export async function outcome(client: Client, name: string, args: Record<string, unknown> = {}): Promise<Outcome> {
  const result = await client.callTool({ name, arguments: args });
  const content = result.content as { type: string; text: string }[];
  return { isError: result.isError === true, text: content[0].text, structured: result.structuredContent };
}

/** The JSON inside a fenced result. */
export function fencedJson(out: string): unknown {
  return JSON.parse(fencedBody(out));
}

export function fencedBody(out: string): string {
  const start = out.indexOf('\n') + 1;
  const end = out.lastIndexOf('\n</untrusted-tool-output>');
  return out.slice(start, end);
}
