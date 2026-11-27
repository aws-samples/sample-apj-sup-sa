// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * HTTP over the platform `fetch`, in the shape the rest of the server needs: the whole reply read as
 * text, redirects not followed (a token is never re-sent to wherever a 3xx points), and every transport
 * failure surfaced as an `HttpError` whose message names the cause ("connect ECONNREFUSED ...") rather
 * than undici's bare "fetch failed".
 */

export type Fetch = typeof globalThis.fetch;

export class HttpError extends Error {
  override name = 'HttpError';
}

export interface Reply {
  status: number;
  statusText: string;
  headers: Headers;
  text: string;
  url: string;
}

export interface SendOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  timeoutMs: number;
}

export async function send(fetchFn: Fetch, url: string, options: SendOptions): Promise<Reply> {
  let res: Response;
  let text: string;
  try {
    res = await fetchFn(url, {
      method: options.method ?? 'GET',
      headers: options.headers,
      body: options.body,
      redirect: 'manual',
      signal: AbortSignal.timeout(options.timeoutMs),
    });
    text = await res.text();
  } catch (err) {
    throw new HttpError(causeOf(err));
  }
  return { status: res.status, statusText: res.statusText, headers: res.headers, text, url };
}

function causeOf(err: unknown): string {
  if (err instanceof Error) {
    const cause = err.cause;
    if (cause instanceof Error && cause.message) return cause.message;
    return err.message;
  }
  return String(err);
}

/** A form body as `application/x-www-form-urlencoded`. */
export function form(fields: Record<string, string>): string {
  return new URLSearchParams(fields).toString();
}
