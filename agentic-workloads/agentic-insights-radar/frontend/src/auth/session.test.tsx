// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADMIN, CONFIG, VIEWER } from '@/test/session';

import { CALLBACK_PATH, can, logoutUrl, oidcSettings, revokeRefreshToken } from './session';

afterEach(() => vi.unstubAllGlobals());

describe('session helpers', () => {
  it('builds Cognito OIDC settings', () => {
    const s = oidcSettings(CONFIG, 'https://app.example.com');
    expect(s.authority).toBe('https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool');
    expect(s.redirect_uri).toBe(`https://app.example.com${CALLBACK_PATH}`);
    expect(s.response_type).toBe('code');
    expect('extraQueryParams' in s).toBe(false);
    const federated = oidcSettings({ ...CONFIG, identity_provider: 'SSO' }, 'https://app.example.com');
    expect(federated.extraQueryParams).toEqual({ identity_provider: 'SSO' });
    window.history.pushState({}, '', '/auth/callback?code=x&state=y');
    s.onSigninCallback();
    expect(window.location.search).toBe('');
  });

  it('builds the Cognito logout URL', () => {
    expect(logoutUrl(CONFIG, 'https://app.example.com')).toBe(
      'https://mr.auth.us-east-1.amazoncognito.com/logout?client_id=client&logout_uri=https%3A%2F%2Fapp.example.com%2F',
    );
  });

  it('revokes a refresh token at Cognito, and never fails sign-out', async () => {
    const fetch = vi.fn(async () => new Response('', { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    await revokeRefreshToken(CONFIG, 'rt-1');
    expect(fetch).toHaveBeenCalledWith('https://mr.auth.us-east-1.amazoncognito.com/oauth2/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'token=rt-1&client_id=client',
      signal: expect.any(AbortSignal), // a stalled connection cannot hold sign-out
    });
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
    await expect(revokeRefreshToken(CONFIG, 'rt-1')).resolves.toBeUndefined();
  });

  it('checks capabilities', () => {
    expect(can(ADMIN, 'user.manage')).toBe(true);
    expect(can(VIEWER, 'user.manage')).toBe(false);
    expect(can(null, 'read')).toBe(false);
  });
});
