// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Authenticator, TokenStore } from '../src/auth.js';
import { InsightsRadarApi } from '../src/client.js';
import { quote } from '../src/py.js';
import { CliExit, main, signerFor } from '../src/cli.js';
import { GENERIC } from '../src/deployment.js';
import {
  runTokenCommand,
  WebIdentityError,
  WebIdentitySigner,
  webIdentityConfig,
  type WebIdentityConfig,
} from '../src/webIdentity.js';
import { jwt, recorder, type Route } from './helpers.js';

const API = 'https://abcde12345.execute-api.us-east-1.amazonaws.com';
const ROLE = 'arn:aws:iam::111122223333:role/air-McpRole-1';
const BLOCK = { role_arn: ROLE, issuer: 'https://id.example.com', audience: 'https://app.example.com' };
const CONFIG: WebIdentityConfig = {
  roleArn: ROLE,
  issuer: 'https://id.example.com',
  audience: 'https://app.example.com',
  apiUrl: API,
  region: 'us-east-1',
  stsUrl: 'https://sts.us-east-1.amazonaws.com/',
};
const NOW = 1_800_000_000_000;
// Placeholder STS credentials, passed by name so secret scanners see no literal value.
const FAKE_KEY_ID = 'AKIA';
const FAKE_KEY_MATERIAL = 'fake-key-material';

function token(extra: Record<string, unknown> = {}): string {
  return jwt(NOW / 1000 + 3600, { iss: CONFIG.issuer, aud: CONFIG.audience, sub: 'alice', ...extra });
}

function sts(expiration: unknown = NOW / 1000 + 900): Route {
  return {
    json: {
      AssumeRoleWithWebIdentityResponse: {
        AssumeRoleWithWebIdentityResult: {
          Credentials: {
            AccessKeyId: FAKE_KEY_ID,
            SecretAccessKey: FAKE_KEY_MATERIAL,
            SessionToken: 'session',
            Expiration: expiration,
          },
        },
      },
    },
  };
}

describe('webIdentityConfig', () => {
  it('is null when the deployment does not offer it', () => {
    expect(webIdentityConfig(null)).toBeNull();
    expect(webIdentityConfig({ mcp_web_identity: null })).toBeNull();
  });

  it('reads and checks the block, with the API URL the operator gave', () => {
    expect(webIdentityConfig({ mcp_web_identity: BLOCK }, '', `${API}/`)).toEqual(CONFIG);
    expect(webIdentityConfig({ mcp_web_identity: BLOCK }, ROLE, API)).toEqual(CONFIG);
    // The public config's api_url (an older stack) is never used: the origin comes from the operator.
    expect(webIdentityConfig({ mcp_web_identity: { ...BLOCK, api_url: 'https://evil.example.com' } }, '', API)).toEqual(
      CONFIG,
    );
  });

  it('uses the partition of the API URL for STS', () => {
    const cn = webIdentityConfig(
      { mcp_web_identity: BLOCK },
      '',
      'https://abcde12345.execute-api.cn-north-1.amazonaws.com.cn',
    );
    expect(cn?.region).toBe('cn-north-1');
    expect(cn?.stsUrl).toBe('https://sts.cn-north-1.amazonaws.com.cn/');
  });

  it('needs AIR_MCP_API_URL', () => {
    expect(() => webIdentityConfig({ mcp_web_identity: BLOCK })).toThrow('set AIR_MCP_API_URL');
  });

  it.each([
    ['https://evil.example.com'],
    ['http://abcde12345.execute-api.us-east-1.amazonaws.com'],
    [`${API}/stage`],
    [`${API}:8443`],
    [`${API}?v=1`],
    [`${API}#x`],
    ['https://u:p@abcde12345.execute-api.us-east-1.amazonaws.com'],
    ['https://abcde12345.execute-api.us-east-1.amazonaws.com.evil.example'],
    ['not a url'],
  ])('refuses the API URL %s', (url) => {
    expect(() => webIdentityConfig({ mcp_web_identity: BLOCK }, '', url)).toThrow('is not an API Gateway URL');
  });

  it.each([
    [{ role_arn: 'arn:aws:iam::111122223333:user/x' }, 'not an IAM role'],
    [{ role_arn: undefined }, 'not an IAM role'],
    [{ issuer: 'http://id.example.com' }, 'no valid issuer'],
    [{ issuer: undefined }, 'no valid issuer'],
    [{ audience: '' }, 'no valid issuer'],
    [{ audience: undefined }, 'no valid issuer'],
  ])('refuses %j', (change, message) => {
    expect(() => webIdentityConfig({ mcp_web_identity: { ...BLOCK, ...change } }, '', API)).toThrow(message);
  });

  it('refuses a role other than the pinned one', () => {
    expect(() => webIdentityConfig({ mcp_web_identity: BLOCK }, 'arn:aws:iam::111122223333:role/other', API)).toThrow(
      'not AIR_MCP_ROLE_ARN',
    );
  });
});

describe('WebIdentitySigner', () => {
  it('exchanges the token with STS once and signs requests for the API host', async () => {
    const rec = recorder({ 'POST /': sts() });
    const run = vi.fn(async () => token());
    const signer = new WebIdentitySigner(CONFIG, '/bin/idtoken', { fetch: rec.fetch, run, now: () => NOW });
    expect(await signer.subject()).toBe('alice');
    const headers = await signer.sign('GET', `${API}/iam/api/me?a=1&a=2&b=3`, { accept: 'application/json' });
    expect(headers.authorization).toMatch(
      /^AWS4-HMAC-SHA256 Credential=AKIA\/\d{8}\/us-east-1\/execute-api\/aws4_request/,
    );
    expect(headers['x-amz-security-token']).toBe('session');
    expect(headers.host).toBe('abcde12345.execute-api.us-east-1.amazonaws.com');
    await signer.sign('POST', `${API}/iam/api/x`, {}, '{}');
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith('/bin/idtoken', CONFIG.audience);
    const call = rec.calls[0];
    expect(call.url.host).toBe('sts.us-east-1.amazonaws.com');
    const form = new URLSearchParams(call.body);
    expect(form.get('Action')).toBe('AssumeRoleWithWebIdentity');
    expect(form.get('RoleArn')).toBe(ROLE);
    expect(form.get('RoleSessionName')).toBe('alice');
    expect(form.get('DurationSeconds')).toBe('900');
  });

  it('signs in again near expiry, and once for callers that arrive together', async () => {
    let now = NOW;
    const rec = recorder({ 'POST /': sts(new Date(NOW + 900_000).toISOString()) });
    const run = vi.fn(async () => token());
    const signer = new WebIdentitySigner(CONFIG, 'c', { fetch: rec.fetch, run, now: () => now });
    await Promise.all([signer.subject(), signer.subject()]);
    expect(run).toHaveBeenCalledTimes(1);
    now = NOW + 850_000;
    await signer.subject();
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('refreshes on its own clock when the local clock runs behind AWS', async () => {
    // AWS is 3 minutes ahead: STS's Expiration reads as 18 minutes away locally, but the credentials
    // last 15 minutes from the request, so the signer renews by then.
    let now = NOW;
    const rec = recorder({ 'POST /': sts(new Date(NOW + 1_080_000).toISOString()) });
    const run = vi.fn(async () => token());
    const signer = new WebIdentitySigner(CONFIG, 'c', { fetch: rec.fetch, run, now: () => now });
    await signer.subject();
    now = NOW + 850_000;
    await signer.subject();
    expect(run).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['not-a-jwt', 'did not print an id token'],
    [token({ iss: 'https://other.example.com' }), 'is from https://other.example.com'],
    [token({ aud: 'other' }), 'is not for'],
    [token({ aud: ['x', CONFIG.audience] }), null],
    [jwt(NOW / 1000 - 1, { iss: CONFIG.issuer, aud: CONFIG.audience, sub: 'alice' }), 'has expired'],
    [jwt(NOW / 1000 + 10, { iss: CONFIG.issuer, aud: CONFIG.audience, sub: 'alice' }), 'has expired'],
    [token({ exp: 'soon' }), 'has expired'],
    [token({ sub: 'a' }), 'no usable subject'],
    [token({ sub: 'al ice' }), 'no usable subject'],
    [token({ sub: 'x'.repeat(65) }), 'no usable subject'],
    [token({ sub: undefined }), 'no usable subject'],
    [token({ iss: undefined }), 'is from undefined'],
    [token({ aud: undefined }), 'is not for'],
  ])('checks the token %#', async (tok, message) => {
    const rec = recorder({ 'POST /': sts() });
    const signer = new WebIdentitySigner(CONFIG, 'c', { fetch: rec.fetch, run: async () => tok, now: () => NOW });
    if (message === null) {
      expect(await signer.subject()).toBe('alice');
    } else {
      await expect(signer.subject()).rejects.toThrow(message);
      expect(rec.calls).toHaveLength(0); // the token never left
    }
  });

  it.each([
    [{ status: 403, json: { Error: { Code: 'AccessDenied', Message: 'no' } } }, 'AccessDenied: no'],
    [{ status: 400, json: { Error: { Code: 'InvalidIdentityToken' } } }, 'InvalidIdentityToken: '],
    [{ status: 400, json: { Error: { Message: 'm' } } }, ': m'],
    [{ status: 500, text: 'down' }, 'HTTP 500'],
    [{ status: 400, json: { other: 1 } }, 'HTTP 400'],
    [{ text: 'not json' }, 'without credentials'],
    [{ json: { AssumeRoleWithWebIdentityResponse: {} } }, 'without credentials'],
    [{ json: { AssumeRoleWithWebIdentityResponse: { AssumeRoleWithWebIdentityResult: {} } } }, 'without credentials'],
    [{ json: { other: 1 } }, 'without credentials'],
    [
      {
        json: {
          AssumeRoleWithWebIdentityResponse: {
            AssumeRoleWithWebIdentityResult: { Credentials: { AccessKeyId: 'a', SecretAccessKey: 's' } },
          },
        },
      },
      'without credentials',
    ],
    [sts('never'), 'without credentials'],
  ])('reports what STS said %#', async (route, message) => {
    const rec = recorder({ 'POST /': route });
    const signer = new WebIdentitySigner(CONFIG, 'c', { fetch: rec.fetch, run: async () => token(), now: () => NOW });
    await expect(signer.subject()).rejects.toThrow(message);
  });

  it('defaults to the real fetch, command and clock', () => {
    expect(new WebIdentitySigner(CONFIG, 'c').apiUrl).toBe(API);
  });
});

describe('runTokenCommand', () => {
  let tmp: string;
  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'air-wi-'));
  });
  afterEach(() => fs.rmSync(tmp, { recursive: true, force: true }));

  function script(body: string): string {
    const file = path.join(tmp, 'cmd.sh');
    fs.writeFileSync(file, `#!/bin/sh\n${body}\n`, { mode: 0o700 });
    return file;
  }

  it('passes the audience as its one argument and returns what it printed', async () => {
    expect(await runTokenCommand(script('echo "  tok-$1  "'), 'aud')).toBe('tok-aud');
  });

  it('names the failure from stderr, else the error', async () => {
    await expect(runTokenCommand(script('echo "cookie expired" >&2; exit 3'), 'a')).rejects.toThrow(
      'the web identity command failed: cookie expired',
    );
    await expect(runTokenCommand(path.join(tmp, 'missing'), 'a')).rejects.toBeInstanceOf(WebIdentityError);
  });
});

describe('the client and the CLI', () => {
  const AIR = [
    'AIR_API_URL',
    'AIR_MCP_WEB_IDENTITY_COMMAND',
    'AIR_MCP_ROLE_ARN',
    'AIR_MCP_API_URL',
    'AIR_MCP_CONFIG',
    'AIR_MCP_CLIENT_ID',
  ];
  let tmp: string;
  beforeEach(() => {
    for (const name of AIR) vi.stubEnv(name, undefined);
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'air-wi-cli-'));
    fs.writeFileSync(path.join(tmp, 'generic.json'), '{}');
    vi.stubEnv('AIR_MCP_CONFIG', path.join(tmp, 'generic.json'));
    vi.stubEnv('AIR_API_URL', 'https://app.example.com');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  function signer(fetch = recorder({ 'POST /': sts() }).fetch): WebIdentitySigner {
    return new WebIdentitySigner(CONFIG, 'c', { fetch, run: async () => token(), now: () => NOW });
  }

  it('a signed request goes to the API host under /iam with no bearer token', async () => {
    const rec = recorder({}, { json: { ok: true } });
    const api = new InsightsRadarApi('https://app.example.com', signer(), rec.fetch);
    expect(await api.request('POST', '/api/search', { q: 'x' }, { k: 1, empty: '' })).toEqual({ ok: true });
    const call = rec.calls[0];
    expect(call.url.href).toBe(`${API}/iam/api/search?k=1`);
    expect(call.headers.get('authorization')).toMatch(/^AWS4-HMAC-SHA256 /);
    expect(call.headers.get('content-type')).toBe('application/json');
  });

  it('a signed request without a query, and a repeated query parameter, are signed as sent', async () => {
    const rec = recorder({}, { json: [] });
    const api = new InsightsRadarApi('https://app.example.com', signer(), rec.fetch);
    await api.request('GET', '/api/me');
    expect(rec.calls[0].url.href).toBe(`${API}/iam/api/me`);
    const h = await signer().sign('GET', `${API}/iam/api/x?a=1&a=2&a=3`, {});
    expect(h.authorization).toMatch(/SignedHeaders=host;x-amz-content-sha256;x-amz-date;x-amz-security-token/);
  });

  it('main finds the signer itself when none is injected', async () => {
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    const lines: string[] = [];
    await main(['login'], {
      fetch: recorder({ 'GET /api/config': { json: { cognito_domain: 'x' } } }).fetch,
      authenticatorFor: async () => ({ domain: 'd', idToken: async () => 't', login: async () => undefined }),
      print: (l) => lines.push(l),
    });
    expect(lines.at(-1)).toBe('Signed in to Agentic Insights Radar.');
  });

  it('an empty AIR_MCP_WEB_IDENTITY_COMMAND overrides a deployment.json default and keeps the browser sign-in', async () => {
    const cfg = path.join(tmp, 'with-command.json');
    fs.writeFileSync(cfg, JSON.stringify({ defaults: { AIR_MCP_WEB_IDENTITY_COMMAND: '/bin/idtoken' } }));
    vi.stubEnv('AIR_MCP_CONFIG', cfg);
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '');
    const signerFor = vi.fn(async () => signer());
    const lines: string[] = [];
    await main(['login'], {
      signerFor,
      authenticatorFor: async () => ({ domain: 'd', idToken: async () => 't', login: async () => undefined }),
      print: (l) => lines.push(l),
    });
    expect(signerFor).not.toHaveBeenCalled();
    expect(lines.at(-1)).toBe('Signed in to Agentic Insights Radar.');
  });

  it('signerFor is null for a deployment without the option', async () => {
    const rec = recorder({ 'GET /api/config': { json: { cognito_domain: 'x' } } });
    expect(await signerFor('https://app.example.com', 'c', rec.fetch)).toBeNull();
    vi.stubEnv('AIR_MCP_API_URL', API);
    const on = recorder({ 'GET /api/config': { json: { mcp_web_identity: BLOCK } } });
    expect(await signerFor('https://app.example.com', 'c', on.fetch, GENERIC)).toBeInstanceOf(WebIdentitySigner);
  });

  it('login needs no browser and says who signed in; serve uses the signer', async () => {
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    const lines: string[] = [];
    const me = recorder({ 'GET /iam/api/me': { json: { role: 'viewer' } } });
    await main(['login'], { signerFor: async () => signer(), fetch: me.fetch, print: (l) => lines.push(l) });
    expect(lines[0]).toMatch(/No browser sign-in is needed.*Signed in as alice\./);
    expect(me.calls[0].headers.get('authorization')).toMatch(/^AWS4-HMAC-SHA256 /);
    const served: unknown[] = [];
    await main(['serve'], {
      signerFor: async () => signer(),
      fetch: recorder({ 'GET /api/config': { json: { profile: null } } }).fetch,
      serve: async (s) => {
        served.push(s);
      },
    });
    expect(served).toHaveLength(1);
  });

  it('login reports the API refusing the person', async () => {
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    const refused = recorder({
      'GET /iam/api/me': { status: 401, json: { message: 'sign in to the web app once before using this client' } },
    });
    await expect(main(['login'], { signerFor: async () => signer(), fetch: refused.fetch })).rejects.toThrow(
      'Sign-in failed: 401: sign in to the web app once before using this client',
    );
  });

  it('logout says the identity-provider sign-in still works while the command is set', async () => {
    const revoke = vi.spyOn(Authenticator.prototype, 'revoke').mockResolvedValue(undefined);
    const store = { exists: () => false, path: '/x' } as unknown as TokenStore;
    const lines: string[] = [];
    await main(['logout'], { store: () => store, print: (l) => lines.push(l) });
    expect(lines).toHaveLength(1);
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    await main(['logout'], { store: () => store, print: (l) => lines.push(l) });
    expect(lines.at(-1)).toMatch(
      /AIR_MCP_WEB_IDENTITY_COMMAND is set: on a deployment that offers sign-in without a browser/,
    );
    revoke.mockRestore();
  });

  it('a signed query is sent RFC 3986 encoded, a space as %20', async () => {
    const rec = recorder({}, { json: [] });
    const api = new InsightsRadarApi('https://app.example.com', signer(), rec.fetch);
    await api.request('GET', '/api/search', undefined, { q: "foo bar (it's)*!", n: 2 });
    expect(rec.calls[0].url.search).toBe('?q=foo%20bar%20%28it%27s%29%2A%21&n=2');
    expect(quote('a+b ~-._')).toBe('a%2Bb%20~-._');
  });

  it('a failing sign-in stops with the reason', async () => {
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    const broken = new WebIdentitySigner(CONFIG, 'c', {
      run: async () => {
        throw new WebIdentityError('cookie expired');
      },
    });
    await expect(main(['login'], { signerFor: async () => broken })).rejects.toThrow('Sign-in failed: cookie expired');
    await expect(
      main(['serve'], {
        signerFor: async () => {
          throw new Error('bad config');
        },
      }),
    ).rejects.toThrow(new CliExit('Could not sign in with AIR_MCP_WEB_IDENTITY_COMMAND: bad config'));
  });

  it('without the option on the deployment, falls back to the browser sign-in', async () => {
    vi.stubEnv('AIR_MCP_WEB_IDENTITY_COMMAND', '/bin/idtoken');
    const lines: string[] = [];
    await main(['login'], {
      signerFor: async () => null,
      authenticatorFor: async () => ({ domain: 'd', idToken: async () => 't', login: async () => undefined }),
      print: (l) => lines.push(l),
    });
    expect(lines.at(-1)).toBe('Signed in to Agentic Insights Radar.');
  });
});

describe('subject alphabet', () => {
  it.each(['00u1aBcD', 'Alice+x=y,z@example.com', 'ab'])('accepts the STS session name %s', async (sub) => {
    const rec = recorder({ 'POST /': sts() });
    const signer = new WebIdentitySigner(CONFIG, 'c', {
      fetch: rec.fetch,
      run: async () => token({ sub }),
      now: () => NOW,
    });
    expect(await signer.subject()).toBe(sub);
  });
});
