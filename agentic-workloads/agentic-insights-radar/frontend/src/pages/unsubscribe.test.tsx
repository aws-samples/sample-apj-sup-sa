// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '@/api/client';
import { fakeApi, TestRouter } from '@/test/session';

const auth = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('react-oidc-context', () => ({
  useAuth: () => auth.value,
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import App from '@/App';

import UnsubscribePage from './UnsubscribePage';

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});

const page = (path: string, routes: Record<string, unknown> = {}) => {
  const { api, calls } = fakeApi(routes);
  render(
    <TestRouter path={path}>
      <UnsubscribePage api={api} />
    </TestRouter>,
  );
  return calls;
};

describe('Unsubscribe page', () => {
  it('changes nothing on opening, and unsubscribes on the button', async () => {
    const calls = page('/unsubscribe?t=tok.123', { 'POST /api/digest/unsubscribe': { ok: true } });
    expect(screen.getByRole('heading', { name: 'Unsubscribe from the digest' })).toBeInTheDocument();
    expect(document.title).toMatch(/^Unsubscribe/);
    expect(calls).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('status')).toHaveTextContent('You are unsubscribed.');
    expect(screen.queryByRole('button')).toBeNull();
    expect(calls).toEqual([{ method: 'POST', path: '/api/digest/unsubscribe', body: { t: 'tok.123' } }]);
  });

  it('says a link without a token is incomplete', () => {
    page('/unsubscribe');
    expect(screen.getByText(/This unsubscribe link is incomplete/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says a refused link cannot be used, with no button to try again', async () => {
    page('/unsubscribe?t=bad', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 400, code: 'invalid_token', message: 'bad token' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This unsubscribe link is incomplete or no longer valid.');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says the wait once for a key that could not be read just now, and offers Try again (scan cycle 49)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () =>
        new Response(JSON.stringify({ code: 'unsubscribe_retry', message: 'the unsubscribe link could not be checked just now.' }), {
          status: 503,
          headers: { 'Retry-After': '60' },
        }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    const alert = await screen.findByRole('alert');
    expect((alert.textContent?.match(/minute/g) ?? []).length).toBe(1);
    // c50: the whole text, so a run-on sentence or a lost wait shows up.
    expect(alert).toHaveTextContent(/^We could not confirm that you are unsubscribed\. Unsubscribing twice is harmless, so try again in a minute, or sign in and turn the delivery off under What's new\.$/);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('keeps Try again for a gateway 503 with no code or wait, as one whole sentence (scan cycles 50, 51)', async () => {
    page('/unsubscribe?t=tok', {
      // A real HTTP API or Lambda-service 503: no code and no Retry-After.
      'POST /api/digest/unsubscribe': () => new Response(JSON.stringify({ message: 'Service Unavailable' }), { status: 503 }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    const alert = await screen.findByRole('alert');
    // c52: one fixed text, with no 'may or may not have been saved: reload' advice this page cannot follow.
    expect(alert).toHaveTextContent(/^We could not confirm that you are unsubscribed\. Unsubscribing twice is harmless, so try again, or sign in and turn the delivery off under What's new\.$/);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('offers no Try again when only an operator can fix it (scan cycle 49)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 503, code: 'unsubscribe_unavailable', message: 'unsubscribing is not available right now' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /^Unsubscribing is not available right now\. To stop the email, sign in and turn the delivery off under What's new\.$/,
    );
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says an expired link has expired, as the API page does (scan cycle 43)', async () => {
    page('/unsubscribe?t=old', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 410, code: 'link_expired', message: 'expired' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This unsubscribe link has expired.');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says when email unsubscribe is not used on this deployment, as the API page does (scan cycle 42)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 404, code: 'not_found', message: 'not used' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Unsubscribe links are not used on this deployment');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('offers Try again when a firewall blocked the request, as the API never answers 403 (scan cycle 23)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 403, code: 'blocked', message: 'blocked' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).not.toHaveTextContent('This unsubscribe link is incomplete or no longer valid.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('calls any other refused request a bad link with no Try again, as the API page does (scan cycle 53)', async () => {
    for (const status of [413, 415, 422]) {
      page('/unsubscribe?t=tok', {
        'POST /api/digest/unsubscribe': () => {
          throw { status, code: 'refused', message: 'refused' };
        },
      });
      fireEvent.click(screen.getAllByRole('button', { name: 'Unsubscribe' }).at(-1)!);
      expect((await screen.findAllByRole('alert')).at(-1)).toHaveTextContent('This unsubscribe link is incomplete or no longer valid.');
      expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
      cleanup();
    }
  });

  it('reads a 401 as unsubscribe links switched off, since the public route never sends one (scan cycle 54)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () => {
        throw { status: 401, code: 'unauthorized', message: 'Unauthorized' };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Unsubscribing by link is switched off on this deployment. If digests still reach you, sign in and turn the delivery off under What's new.");
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('offers Try again when the call fails with no API answer at all (scan cycle 54)', async () => {
    const api = { unsubscribe: () => Promise.reject(new TypeError('Failed to fetch')) } as unknown as ApiClient;
    render(
      <TestRouter path="/unsubscribe?t=tok">
        <UnsubscribePage api={api} />
      </TestRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not confirm that you are unsubscribed.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('offers Try again on a throttle, with the wait it gives (scan cycle 53)', async () => {
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () =>
        new Response(JSON.stringify({ code: 'throttled', message: 'slow down' }), { status: 429, headers: { 'Retry-After': '60' } }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('so try again in a minute');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('offers Try again when the server failed, and then succeeds', async () => {
    let down = true;
    page('/unsubscribe?t=tok', {
      'POST /api/digest/unsubscribe': () => {
        if (down) throw { status: 500, code: 'internal', message: 'the server failed' };
        return { ok: true };
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not confirm that you are unsubscribed.');
    down = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('status')).toHaveTextContent('You are unsubscribed.');
  });

  it('sends no sign-in with its request', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ ok: true })));
    render(
      <TestRouter path="/unsubscribe?t=abc">
        <UnsubscribePage />
      </TestRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(await screen.findByRole('status')).toBeInTheDocument();
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/digest/unsubscribe');
    expect(init.headers).not.toHaveProperty('authorization');
  });

  it('opens signed out, outside the sign-in gate', async () => {
    auth.value = { isLoading: false, isAuthenticated: false, error: undefined };
    window.history.replaceState({}, '', '/unsubscribe?t=abc');
    render(<App />);
    expect(await screen.findByRole('button', { name: 'Unsubscribe' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Signing in\u2026')).toBeNull());
  });
});

describe('describeError end punctuation (scan cycle 51)', () => {
  it('counts a stop before a closing bracket or quote, and drops trailing spaces', async () => {
    const { ApiError } = await import('@/api/client');
    const { describeError } = await import('@/components/Notifications');
    expect(describeError(new ApiError(429, 'rate_limited', 'too many requests (wait a minute.)', undefined, 40))).toBe(
      'Too many requests (wait a minute.) Try again in 40 seconds.',
    );
    expect(describeError(new ApiError(503, 'busy', 'busy  ', undefined, 2))).toBe('Busy. Try again in a moment.');
  });
});
