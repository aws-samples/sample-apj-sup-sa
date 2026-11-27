// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useCallback, useEffect, useRef, useState } from 'react';

// API v2 client. Same origin as the UI, so no CORS and the ALB session cookie rides along.

export class ApiError extends Error {
  status: number;
  requestId?: string;
  field?: string;
  current?: unknown;
  constructor(status: number, message: string, body: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.requestId = typeof body.request_id === 'string' ? body.request_id : undefined;
    this.field = typeof body.field === 'string' ? body.field : undefined;
    this.current = body.current;
  }
}

const MOCK = import.meta.env.VITE_MOCK === '1';

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (MOCK) {
    // Compiled out of the production build (vite.config.ts pins VITE_MOCK to "0").
    const mock = await import('./mock/handler');
    return mock.handle(method, path, body) as Promise<T>;
  }
  const init: RequestInit = { method, credentials: 'same-origin', headers: { accept: 'application/json' } };
  if (method !== 'GET') {
    init.headers = { ...init.headers, 'content-type': 'application/json', 'x-ocgw-csrf': '1' };
    init.body = JSON.stringify(body ?? {});
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, `Network error: ${(e as Error).message}`);
  }
  let parsed: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!res.ok) {
    const b = (parsed && typeof parsed === 'object' ? parsed : {}) as Record<string, unknown>;
    const msg = typeof b.error === 'string' ? b.error : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, msg, b);
  }
  if (parsed === null) throw new ApiError(res.status, 'Response was not JSON (session may have expired; reload the page)');
  return parsed as T;
}

export const apiGet = <T>(path: string) => request<T>('GET', path);
export const apiWrite = <T>(method: 'PUT' | 'POST' | 'DELETE', path: string, body: unknown) => request<T>(method, path, body);

export function qs(params: Record<string, string | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : '';
}

// data: the last answer, kept while a refetch runs so pages do not flash. settled: the answer for the current path
// and the latest reload only, null until that request finishes. Use settled for anything that is sent back (a
// version) or fills a form, so a stale copy never causes a false 409 or overwrites what the user typed.
export type Loadable<T> = { data: T | null; settled: T | null; loading: boolean; error: ApiError | null; reload: () => void };

export function useApi<T>(path: string | null): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [fetched, setFetched] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const key = path === null ? null : `${tick} ${path}`;
  useEffect(() => {
    if (path === null || key === null) {
      // Closed (a modal with no user): forget the last answer, so reopening for the same path is a fresh load and
      // never hands back the copy from before the last save as settled.
      ++seq.current;
      setFetched(null);
      setData(null);
      setError(null);
      return;
    }
    const my = ++seq.current;
    setError(null);
    apiGet<T>(path)
      .then((d) => {
        if (my === seq.current) setData(d);
      })
      .catch((e: unknown) => {
        if (my === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (my === seq.current) setFetched(key);
      });
  }, [path, key]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  // Derived, not stored: true from the very first render after a path change or reload, with no gap.
  const loading = key !== null && fetched !== key;
  const settled = !loading && key !== null && !error ? data : null;
  return { data, settled, loading, error: loading ? null : error, reload };
}
