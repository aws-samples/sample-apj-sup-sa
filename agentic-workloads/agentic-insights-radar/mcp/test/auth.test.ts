// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { EventEmitter } from 'node:events';
import childProcess from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AuthError,
  Authenticator,
  CognitoClient,
  defaultStore,
  loginHint,
  jwtClaims,
  jwtExpiry,
  openInBrowser,
  pkcePair,
  REDIRECT_URI,
  TokenStore,
} from '../src/auth.js';
import { b64url, COGNITO_DOMAIN, jwt, networkError, recorder } from './helpers.js';

const COGNITO = new CognitoClient(COGNITO_DOMAIN, 'mcp-client');
const HERE = { api_url: '', client_id: 'mcp-client', domain: COGNITO_DOMAIN };

let tmp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'air-auth-'));
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

function mode(file: string): number {
  return fs.statSync(file).mode & 0o777;
}

function formOf(body: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(body));
}

describe('PKCE and tokens', () => {
  it('pkce and expiry', () => {
    const { verifier, challenge } = pkcePair();
    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(verifier.length).toBeLessThanOrEqual(128);
    expect(challenge).toBe(createHash('sha256').update(verifier).digest('base64url'));
    expect(challenge).not.toContain('=');
    expect(jwtExpiry(jwt(123))).toBe(123);
    for (const bad of ['nope', 'a.b.c', `a.${Buffer.from('{"x":1}').toString('base64')}.c`]) {
      expect(() => jwtExpiry(bad)).toThrow(AuthError);
    }
  });

  it('reads exp as Python int() does', () => {
    expect(jwtExpiry(`h.${b64url({ exp: '77' })}.s`)).toBe(77);
    expect(jwtExpiry(`h.${b64url({ exp: 12.9 })}.s`)).toBe(12);
    expect(jwtExpiry(`h.${b64url({ exp: true })}.s`)).toBe(1);
    expect(jwtExpiry(`h.${b64url({ exp: false })}.s`)).toBe(0);
    for (const exp of ['soon', null, [1]]) {
      expect(() => jwtExpiry(`h.${b64url({ exp })}.s`)).toThrow('the stored token is malformed');
    }
    expect(() => jwtExpiry(`h.${b64url([1])}.s`)).toThrow(AuthError);
    expect(() => jwtExpiry(null)).toThrow('run `insights-radar-mcp login`');
  });

  it('p3_026 jwt claims of something that is not a JWT are empty', () => {
    expect(jwtClaims('not-a-jwt')).toEqual({});
    expect(jwtClaims('a.WyJsaXN0Il0.c')).toEqual({});
    expect(jwtClaims(`a.${b64url({ iss: 'x' })}.c`)).toEqual({ iss: 'x' });
  });
});

describe('TokenStore', () => {
  it('token store is private', () => {
    const store = new TokenStore(path.join(tmp, 'cfg', 'tokens.json'));
    expect(store.load()).toBeNull();
    store.save({ id_token: 'x' });
    expect(store.load()).toEqual({ id_token: 'x' });
    expect(mode(store.path)).toBe(0o600);
    fs.writeFileSync(store.path, '[1]');
    expect(store.load()).toBeNull();
    store.clear();
    store.clear();
    expect(fs.existsSync(store.path)).toBe(false);
  });

  it('token store tightens an existing directory', () => {
    const folder = path.join(tmp, 'shared');
    fs.mkdirSync(folder, { mode: 0o755 });
    fs.chmodSync(folder, 0o755);
    const store = new TokenStore(path.join(folder, 'tokens.json'));
    store.save({ id_token: 'x' });
    store.save({ id_token: 'y' });
    expect(mode(folder)).toBe(0o700);
    expect(mode(store.path)).toBe(0o600);
    expect(store.load()).toEqual({ id_token: 'y' });
    expect(fs.readdirSync(folder)).toEqual(['tokens.json']); // no temporary files left behind
  });

  it('token store removes its temporary file when a write fails', () => {
    const store = new TokenStore(path.join(tmp, 'tokens.json'));
    vi.spyOn(fs, 'renameSync').mockImplementation(() => {
      throw new Error('disk full');
    });
    expect(() => store.save({ id_token: 'x' })).toThrow('disk full');
    expect(fs.readdirSync(tmp)).toEqual([]);
  });

  it('token store closes the file when the write itself fails', () => {
    const store = new TokenStore(path.join(tmp, 'tokens.json'));
    const close = vi.spyOn(fs, 'closeSync');
    vi.spyOn(fs, 'writeSync').mockImplementation(() => {
      throw new Error('disk full');
    });
    expect(() => store.save({ id_token: 'x' })).toThrow('disk full');
    expect(close).toHaveBeenCalledTimes(1);
    expect(fs.readdirSync(tmp)).toEqual([]);
  });

  it('empty XDG_CONFIG_HOME falls back to home', () => {
    vi.stubEnv('XDG_CONFIG_HOME', '');
    expect(new TokenStore().path).toBe(path.join(os.homedir(), '.config', 'insights-radar', 'tokens.json'));
    vi.stubEnv('XDG_CONFIG_HOME', tmp);
    expect(new TokenStore().path).toBe(path.join(tmp, 'insights-radar', 'tokens.json'));
    expect(defaultStore()).toBe(path.join(tmp, 'insights-radar', 'tokens.json'));
  });

  it('a corrupt token file reads as signed out', () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    fs.writeFileSync(store.path, '{not json');
    expect(store.load()).toBeNull();
    fs.writeFileSync(store.path, Buffer.from([0xff, 0xfe]));
    expect(store.load()).toBeNull();
  });

  it('a token file readable by others is made private again', () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    store.save({ id_token: 'x' });
    fs.chmodSync(store.path, 0o644);
    expect(store.load()).toEqual({ id_token: 'x' });
    expect(mode(store.path)).toBe(0o600);
  });

  it('a token file owned by someone else is refused', () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    store.save({ id_token: 'x' });
    const owner = fs.statSync(store.path).uid;
    vi.spyOn(process as { getuid: () => number }, 'getuid').mockReturnValue(owner + 1);
    expect(() => store.load()).toThrow(/belongs to another user; delete it and run `insights-radar-mcp login`$/);
    const named = new TokenStore(store.path, { name: 'acme-mcp' });
    expect(() => named.load()).toThrow(/belongs to another user; delete it and run `acme-mcp login`$/);
  });

  it("a deployment's token folder and command name", () => {
    vi.stubEnv('XDG_CONFIG_HOME', tmp);
    expect(new TokenStore(undefined, { tokenDir: 'acme-radar' }).path).toBe(
      path.join(tmp, 'acme-radar', 'tokens.json'),
    );
    expect(defaultStore('acme-radar')).toBe(path.join(tmp, 'acme-radar', 'tokens.json'));
    expect(loginHint()).toBe('run `insights-radar-mcp login`');
    expect(loginHint('acme-mcp')).toBe('run `acme-mcp login`');
    expect(() => jwtExpiry('x', 'acme-mcp')).toThrow('the stored token is malformed; run `acme-mcp login`');
  });
});

describe('revoke', () => {
  it('revoke invalidates the refresh token then forgets it', async () => {
    const { fetch, calls } = recorder({ 'POST /oauth2/revoke': { json: {} } });
    const store = new TokenStore(path.join(tmp, 't.json'));
    store.save({ id_token: 'i', refresh_token: 'r', deployment: HERE });
    await new Authenticator(COGNITO, store, { fetch }).revoke();
    expect(store.load()).toBeNull();
    expect(formOf(calls[0].body)).toEqual({ token: 'r', client_id: 'mcp-client' });
    expect(calls[0].headers.get('content-type')).toBe('application/x-www-form-urlencoded');
    await new Authenticator(COGNITO, store, { fetch }).revoke(); // nothing stored: nothing to revoke
    expect(calls).toHaveLength(1);
    store.save({ refresh_token: 'r', deployment: HERE });
    const failing = recorder({ 'POST /oauth2/revoke': { status: 400, json: {} } });
    await expect(new Authenticator(COGNITO, store, { fetch: failing.fetch }).revoke()).rejects.toThrow(
      'could not revoke the session (400)',
    );
    expect(store.load()).toEqual({ refresh_token: 'r', deployment: HERE }); // kept, so the user can retry
  });

  it('revoke goes to the pool that issued the token whatever is configured now', async () => {
    const { fetch, calls } = recorder({ 'POST /oauth2/revoke': { json: {} } });
    const store = new TokenStore(path.join(tmp, 't.json'));
    const a = { api_url: 'https://a.example.com', client_id: 'mcp-client', domain: COGNITO_DOMAIN };
    store.save({ refresh_token: 'r', deployment: a });
    const elsewhere = new CognitoClient('attacker.example', 'other-client');
    await new Authenticator(elsewhere, store, { fetch, apiUrl: 'https://b.example.com' }).revoke();
    expect(calls.map((c) => c.url.host)).toEqual([COGNITO_DOMAIN]);
    expect(store.load()).toBeNull();
    expect(formOf(calls[0].body).client_id).toBe('mcp-client');
    store.save({ refresh_token: 'r' }); // saved by an earlier version: no record of where it came from
    await expect(new Authenticator(elsewhere, store, { fetch }).revoke()).rejects.toThrow(
      'does not record its deployment',
    );
    expect(calls).toHaveLength(1); // nothing sent
    expect(store.load()).toBeNull(); // the local copy is gone
    store.save({ refresh_token: 'r', deployment: { domain: COGNITO_DOMAIN } });
    await expect(new Authenticator(elsewhere, store, { fetch }).revoke()).rejects.toThrow(AuthError);
  });
});

describe('id token', () => {
  it('id token refreshes near expiry', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const now = 1_000_000;
    const { fetch, calls } = recorder({
      'POST /oauth2/token': { json: { id_token: jwt(now + 3600), access_token: 'a' } },
    });
    const auth = new Authenticator(COGNITO, store, { fetch, now: () => now });
    await expect(auth.idToken()).rejects.toThrow('not signed in');
    store.save({ id_token: jwt(now + 3600), deployment: HERE });
    expect(await auth.idToken()).toBe(jwt(now + 3600));
    expect(calls).toEqual([]);
    store.save({ id_token: jwt(now + 60), deployment: HERE });
    await expect(auth.idToken()).rejects.toThrow('expired');
    store.save({ id_token: jwt(now + 60), refresh_token: 'r', deployment: HERE });
    expect(await auth.idToken()).toBe(jwt(now + 3600));
    expect(store.load()?.refresh_token).toBe('r'); // kept: Cognito does not rotate it
    expect(formOf(calls[0].body)).toEqual({ grant_type: 'refresh_token', refresh_token: 'r', client_id: 'mcp-client' });
  });

  it('refreshes one at a time', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const now = 1_000_000;
    const { fetch, calls } = recorder({ 'POST /oauth2/token': { json: { id_token: jwt(now + 3600) } } });
    store.save({ id_token: jwt(now + 60), refresh_token: 'r', deployment: HERE });
    const auth = new Authenticator(COGNITO, store, { fetch, now: () => now });
    const tokens = await Promise.all([auth.idToken(), auth.idToken(), auth.idToken()]);
    expect(new Set(tokens)).toEqual(new Set([jwt(now + 3600)]));
    expect(calls).toHaveLength(1);
  });

  it('an expired refresh token asks for login', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const now = 1_000_000;
    store.save({ id_token: jwt(now + 60), refresh_token: 'r', deployment: HERE });
    const { fetch } = recorder({ 'POST /oauth2/token': { status: 400, text: 'invalid_grant' } });
    await expect(new Authenticator(COGNITO, store, { fetch, now: () => now }).idToken()).rejects.toThrow(
      'session expired; run `insights-radar-mcp login`',
    );
  });

  it('a network failure during refresh is reported as it is', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const now = 1_000_000;
    store.save({ id_token: jwt(now + 60), refresh_token: 'r', deployment: HERE });
    const { fetch } = recorder({ 'POST /oauth2/token': networkError('no route') });
    const auth = new Authenticator(COGNITO, store, { fetch, now: () => now });
    await expect(auth.idToken()).rejects.toThrow('no route');
    // The lock is released after a failure.
    await expect(auth.idToken()).rejects.toThrow('no route');
  });

  it('tokens are used only for the deployment that issued them', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const { fetch } = recorder({
      'POST /oauth2/token': { json: { id_token: jwt(Date.now() / 1000 + 3600), refresh_token: 'r' } },
    });
    await new Authenticator(COGNITO, store, { fetch, apiUrl: 'https://a.example.com/' }).exchange('code', 'verifier');
    expect(await new Authenticator(COGNITO, store, { fetch, apiUrl: 'https://a.example.com' }).idToken()).toBeTruthy();
    await expect(
      new Authenticator(COGNITO, store, { fetch, apiUrl: 'https://b.example.com' }).idToken(),
    ).rejects.toThrow('different deployment');
    await expect(
      new Authenticator(new CognitoClient(COGNITO_DOMAIN, 'other-client'), store, {
        fetch,
        apiUrl: 'https://a.example.com',
      }).idToken(),
    ).rejects.toThrow('different deployment');
    const saved = store.load() as Record<string, unknown>;
    store.save({ ...saved, deployment: { ...(saved.deployment as object), extra: 1 } });
    await expect(
      new Authenticator(COGNITO, store, { fetch, apiUrl: 'https://a.example.com' }).idToken(),
    ).rejects.toThrow('different deployment');
    store.save({ id_token: jwt(Date.now() / 1000 + 3600) }); // saved by an earlier version: sign in again
    await expect(
      new Authenticator(COGNITO, store, { fetch, apiUrl: 'https://a.example.com' }).idToken(),
    ).rejects.toThrow('different deployment');
  });

  it('uses the wall clock by default', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    store.save({ id_token: jwt(Date.now() / 1000 + 3600), deployment: HERE });
    expect(await new Authenticator(COGNITO, store).idToken()).toBe(store.load()?.id_token);
  });
});

describe('token exchange', () => {
  it('token errors', async () => {
    const { fetch } = recorder({ 'POST /oauth2/token': { status: 400, text: 'invalid_grant' } });
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')), { fetch });
    await expect(auth.exchange('code', 'verifier')).rejects.toThrow('sign-in failed (400): invalid_grant');
    const odd = recorder({ 'POST /oauth2/token': { json: ['not', 'an', 'object'] } });
    await expect(
      new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')), { fetch: odd.fetch }).exchange('c', 'v'),
    ).rejects.toThrow('the token reply is not an object');
  });

  it('p3_026 a token from another user pool is refused', async () => {
    const token = (iss: string): string => `h.${b64url({ iss, exp: 9999999999 })}.s`;
    for (const [iss, ok] of [
      ['https://cognito-idp.us-east-1.amazonaws.com/us-east-1_REAL', true],
      ['https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER', false],
    ] as const) {
      const { fetch, calls } = recorder({
        'POST /oauth2/token': { json: { id_token: token(iss), refresh_token: 'r' } },
      });
      const store = new TokenStore(path.join(tmp, `${ok}.json`));
      const auth = new Authenticator(COGNITO, store, { fetch, userPoolId: 'us-east-1_REAL' });
      if (ok) {
        await auth.exchange('code', 'verifier');
        expect(fs.existsSync(store.path)).toBe(true);
        expect(formOf(calls[0].body)).toEqual({
          grant_type: 'authorization_code',
          code: 'code',
          redirect_uri: REDIRECT_URI,
          code_verifier: 'verifier',
          client_id: 'mcp-client',
        });
      } else {
        await expect(auth.exchange('code', 'verifier')).rejects.toThrow(
          `the sign-in returned a token from '${iss}', not the deployment's user pool`,
        );
        expect(fs.existsSync(store.path)).toBe(false);
      }
    }
    const { fetch } = recorder({ 'POST /oauth2/token': { json: { refresh_token: 'r' } } });
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 'none.json')), { fetch, userPoolId: 'p' });
    await expect(auth.exchange('c', 'v')).rejects.toThrow("from '', not the deployment's user pool");
  });

  it('authorize url', () => {
    const url = new Authenticator(COGNITO, new TokenStore('/nonexistent')).authorizeUrl('chal', 'st');
    expect(url.startsWith('https://mr.auth.us-east-1.amazoncognito.com/oauth2/authorize?')).toBe(true);
    expect(url).toContain('code_challenge_method=S256');
    expect(url).toContain('client_id=mcp-client');
    expect(url).toContain('state=st');
    expect(url).toContain('redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback');
    expect(url).toContain('scope=openid+email+profile');
  });
});

/** A browser that, a moment after being asked to open the sign-in page, follows the redirect. */
function browser(query?: string, { pathname = '/callback', extra = [] as string[], method = 'GET' } = {}) {
  return (url: string): boolean => {
    const state = new URL(url).searchParams.get('state');
    const base = REDIRECT_URI.slice(0, REDIRECT_URI.lastIndexOf('/'));
    setTimeout(async () => {
      for (const p of extra) await fetch(`${base}${p}`, { method }).catch(() => undefined);
      await fetch(`${base}${pathname}?${query ?? `code=abc&state=${state}`}`).catch(() => undefined);
    }, 100);
    return false;
  };
}

describe('login', () => {
  it('login round trip', async () => {
    const { fetch, calls } = recorder({
      'POST /oauth2/token': { json: { id_token: jwt(Date.now() / 1000 + 3600), refresh_token: 'r' } },
    });
    const store = new TokenStore(path.join(tmp, 't.json'));
    const printed: string[] = [];
    const tokens = await new Authenticator(COGNITO, store, { fetch }).login({
      openBrowser: browser(undefined, { extra: ['/favicon.ico'] }),
      timeoutMs: 10_000,
      print: (line) => printed.push(line),
    });
    expect(tokens.refresh_token).toBe('r');
    expect(store.load()).toEqual(tokens);
    expect(calls[0].body).toContain('code_verifier=');
    expect(printed.join('\n')).toContain('Open this address');
  });

  it('answers the browser and ignores other methods', async () => {
    const { fetch } = recorder({
      'POST /oauth2/token': { json: { id_token: jwt(Date.now() / 1000 + 3600), refresh_token: 'r' } },
    });
    const statuses: number[] = [];
    // The recorder fetch is the Cognito side; the browser uses the real fetch for the loopback.
    let browsing: Promise<void> = Promise.resolve();
    const realOpen = (url: string): boolean => {
      const state = new URL(url).searchParams.get('state');
      browsing = (async () => {
        statuses.push((await globalThis.fetch('http://127.0.0.1:8765/callback', { method: 'POST' })).status);
        const res = await globalThis.fetch(`http://127.0.0.1:8765/callback?code=abc&state=${state}&code=other`);
        statuses.push(res.status, (await res.text()).includes('You can close this tab') ? 1 : 0);
      })();
      return true;
    };
    const store = new TokenStore(path.join(tmp, 't.json'));
    await new Authenticator(COGNITO, store, { fetch }).login({ openBrowser: realOpen, timeoutMs: 10_000 });
    await browsing;
    expect(statuses).toEqual([501, 200, 1]);
  });

  it.each(['code=abc&state=wrong', 'error=access_denied&state='])(
    'a redirect for another request is ignored: %s',
    async (query) => {
      // A stray request to the callback (wrong or missing state) cannot end or hijack the login.
      const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')), { fetch: recorder().fetch });
      await expect(
        auth.login({ openBrowser: browser(query), timeoutMs: 1500, print: () => undefined }),
      ).rejects.toThrow('timed out');
    },
  );

  it('login refused and timeout', async () => {
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')), { fetch: recorder().fetch });
    const refuse = (url: string): boolean => {
      const state = new URL(url).searchParams.get('state');
      return browser(`error=access_denied&state=${state}`)(url);
    };
    await expect(auth.login({ openBrowser: refuse, timeoutMs: 10_000, print: () => undefined })).rejects.toThrow(
      'refused: access_denied',
    );
    const blank = (url: string): boolean => {
      const state = new URL(url).searchParams.get('state');
      return browser(`code=&state=${state}`)(url); // a blank code is no code (parse_qs drops blanks)
    };
    await expect(auth.login({ openBrowser: blank, timeoutMs: 10_000, print: () => undefined })).rejects.toThrow(
      'refused: unknown error',
    );
    await expect(auth.login({ openBrowser: () => true, timeoutMs: 200 })).rejects.toThrow('timed out');
  });

  it('prints the address with console.log by default', async () => {
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')), { fetch: recorder().fetch });
    await expect(auth.login({ openBrowser: () => false, timeoutMs: 100 })).rejects.toThrow('timed out');
    expect(log.mock.calls[0][0]).toContain('Open this address to sign in:\nhttps://mr.auth');
  });

  it('a busy redirect port is explained', async () => {
    const blocker = net.createServer();
    await new Promise<void>((resolve) => blocker.listen(8765, '127.0.0.1', resolve));
    try {
      const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')));
      expect(auth.domain).toBe(COGNITO_DOMAIN);
      await expect(auth.login({ openBrowser: () => true, timeoutMs: 100 })).rejects.toThrow(
        /cannot listen on 127\.0\.0\.1:8765.*Address already in use/,
      );
    } finally {
      await new Promise((resolve) => blocker.close(resolve));
    }
  });

  it('waits five minutes by default, and prints the address when no browser opens', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      vi.spyOn(process, 'platform', 'get').mockReturnValue('linux');
      vi.stubEnv('DISPLAY', '');
      vi.stubEnv('WAYLAND_DISPLAY', '');
      const log = vi.spyOn(console, 'log').mockImplementation(() => undefined);
      const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')));
      const pending = auth.login();
      const failed = expect(pending).rejects.toThrow('timed out waiting for sign-in');
      await vi.waitFor(() => expect(log).toHaveBeenCalled());
      await vi.advanceTimersByTimeAsync(299_999);
      await vi.advanceTimersByTimeAsync(1);
      await failed;
    } finally {
      vi.useRealTimers();
    }
  });

  it('names other listen failures by their message', async () => {
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')));
    const listen = vi.spyOn(net.Server.prototype, 'listen').mockImplementation(function (this: net.Server) {
      queueMicrotask(() => this.emit('error', new Error('odd failure')));
      return this;
    });
    await expect(auth.login({ openBrowser: () => true, timeoutMs: 100 })).rejects.toThrow(
      'cannot listen on 127.0.0.1:8765 for the sign-in redirect (odd failure); close the program',
    );
    listen.mockRestore();
  });

  it('binds the loopback address only', async () => {
    const listen = vi.spyOn(net.Server.prototype, 'listen');
    const auth = new Authenticator(COGNITO, new TokenStore(path.join(tmp, 't.json')));
    await expect(auth.login({ openBrowser: () => true, timeoutMs: 50 })).rejects.toThrow('timed out');
    expect(listen.mock.calls[0].slice(0, 2)).toEqual([8765, '127.0.0.1']);
  });
});

describe('openInBrowser', () => {
  class FakeChild extends EventEmitter {
    unref = vi.fn();
  }

  function platform(name: NodeJS.Platform): void {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(name);
  }

  it('reports no browser on a Linux box without a display', async () => {
    platform('linux');
    vi.stubEnv('DISPLAY', '');
    vi.stubEnv('WAYLAND_DISPLAY', '');
    const spawn = vi.spyOn(childProcess, 'spawn');
    expect(await openInBrowser('https://x')).toBe(false);
    expect(spawn).not.toHaveBeenCalled();
  });

  it.each([
    ['linux', 'xdg-open', ['https://x']],
    ['darwin', 'open', ['https://x']],
    ['win32', 'rundll32', ['url.dll,FileProtocolHandler', 'https://x']],
  ] as const)('uses the platform launcher on %s', async (name, command, args) => {
    platform(name);
    vi.stubEnv('DISPLAY', ':0');
    const child = new FakeChild();
    const spawn = vi.spyOn(childProcess, 'spawn').mockReturnValue(child as unknown as childProcess.ChildProcess);
    const opened = openInBrowser('https://x');
    child.emit('exit', 0);
    expect(await opened).toBe(true);
    expect(spawn).toHaveBeenCalledWith(command, args, { stdio: 'ignore', detached: true });
    expect(child.unref).toHaveBeenCalled();
  });

  it('is false when the launcher fails or is missing', async () => {
    platform('linux');
    vi.stubEnv('WAYLAND_DISPLAY', 'wayland-0');
    const failed = new FakeChild();
    vi.spyOn(childProcess, 'spawn').mockReturnValueOnce(failed as unknown as childProcess.ChildProcess);
    const a = openInBrowser('https://x');
    failed.emit('exit', 3);
    expect(await a).toBe(false);
    const missing = new FakeChild();
    vi.spyOn(childProcess, 'spawn').mockReturnValueOnce(missing as unknown as childProcess.ChildProcess);
    const b = openInBrowser('https://x');
    missing.emit('error', new Error('ENOENT'));
    expect(await b).toBe(false);
    vi.spyOn(childProcess, 'spawn').mockImplementationOnce(() => {
      throw new Error('EPERM');
    });
    expect(await openInBrowser('https://x')).toBe(false);
  });

  it('is true when the launcher keeps running', async () => {
    vi.useFakeTimers();
    try {
      platform('darwin');
      const child = new FakeChild();
      vi.spyOn(childProcess, 'spawn').mockReturnValue(child as unknown as childProcess.ChildProcess);
      const opened = openInBrowser('https://x');
      vi.advanceTimersByTime(3000);
      expect(await opened).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
