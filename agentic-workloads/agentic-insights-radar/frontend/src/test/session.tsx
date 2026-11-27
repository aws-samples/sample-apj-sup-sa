// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { fireEvent, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { ApiClient } from '@/api/client';
import type { AppConfig, Me } from '@/api/types';
import { TestSessionProvider } from '@/auth/session';
import { NotificationProvider } from '@/components/Notifications';

export const CONFIG: AppConfig = {
  region: 'us-east-1',
  user_pool_id: 'us-east-1_Pool',
  user_pool_client_id: 'client',
  cognito_domain: 'mr.auth.us-east-1.amazoncognito.com',
  chat_runtime_arn: 'arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/chat-1',
  version: '1.0.0',
  genai_enabled: true,
};

export const ADMIN: Me = {
  subject: 'u-admin',
  email: 'admin@example.com',
  role: 'admin',
  can: ['read', 'chat', 'feedback.give', 'working.mark', 'content.draft', 'radar.create', 'radar.edit', 'radar.admin',
    'suggestion.review', 'product.manage', 'product.read', 'module.manage', 'module.run', 'budget.manage', 'andon.manage',
    'user.manage', 'audit.read', 'health.read', 'graph.edit', 'sample.load'],
};
export const VIEWER: Me = { subject: 'u-viewer', email: 'viewer@example.com', role: 'viewer', can: ['read', 'chat'] };

export type Route = (method: string, path: string, body: unknown, url: URL) => unknown;

/** An ApiClient over a routing table: `routes['GET /api/x']` returns the body (or throws). */
export function fakeApi(routes: Record<string, unknown | ((body: unknown, url: URL) => unknown)>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'https://app.example.com');
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, body });
    const key = `${method} ${url.pathname}`;
    if (!(key in routes)) return new Response(JSON.stringify({ code: 'not_found', message: `no route ${key}` }), { status: 404 });
    let value = routes[key];
    try {
      if (typeof value === 'function') value = (value as (b: unknown, u: URL) => unknown)(body, url);
    } catch (err) {
      // `details` is passed through: the API carries retry_after, shrinking-import counts and
      // validation rows there, and the app reads them.
      const e = err as { status?: number; code?: string; message?: string; details?: Record<string, unknown> };
      return new Response(JSON.stringify({ code: e.code ?? 'error', message: e.message, details: e.details }), { status: e.status ?? 400 });
    }
    if (value instanceof Response) return value.clone(); // a body can be read once; routes may be hit repeatedly
    return new Response(typeof value === 'string' ? value : JSON.stringify(value), { status: 200 });
  });
  return { api: new ApiClient(() => 'id-token', fetcher as unknown as typeof fetch), calls, fetcher };
}

export function wrap(
  ui: React.ReactNode,
  {
    api,
    me = ADMIN,
    config = CONFIG,
    path = '/',
    refreshConfig = vi.fn(),
    ...rest
  }: { api: ApiClient; me?: Me | null; config?: AppConfig; path?: string; refreshConfig?: () => void } & Partial<
    Omit<React.ComponentProps<typeof TestSessionProvider>['value'], 'api' | 'config' | 'me' | 'refreshConfig'>
  >,
) {
  const value = sessionValue({ config, refreshConfig, api, me, ...rest });
  return (
    <TestSessionProvider value={value}>
      <NotificationProvider>
        <TestRouter path={path}>{ui}</TestRouter>
      </NotificationProvider>
    </TestSessionProvider>
  );
}

/** A full session value for tests; `over` replaces any part. */
export function sessionValue(over: Partial<React.ComponentProps<typeof TestSessionProvider>['value']> & { api: ApiClient }) {
  return {
    config: CONFIG,
    refreshConfig: vi.fn(),
    me: ADMIN as Me | null,
    meLoading: false,
    meError: undefined as Error | undefined,
    refreshMe: vi.fn(),
    expire: vi.fn(),
    sessionEnded: false,
    signOut: vi.fn(async () => undefined),
    idToken: () => 'id-token' as string | undefined,
    ...over,
  };
}

/** A data router (as in the app), so pages can block navigation while they hold unsaved edits. */
export function TestRouter({ path = '/', children }: { path?: string; children: React.ReactNode }) {
  const [router] = React.useState(() => createMemoryRouter([{ path: '*', element: children }], { initialEntries: [path] }));
  return <RouterProvider router={router} />;
}

/** Opens a page header's "More actions" menu and chooses `text` (Delete lives there, away from the other buttons). */
export async function chooseMoreAction(text: string): Promise<void> {
  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: text }));
}
