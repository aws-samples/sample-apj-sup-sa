// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthError, Authenticator, TokenStore } from '../src/auth.js';
import { type Deployment, GENERIC } from '../src/deployment.js';
import { apiUrl, authenticatorFor, CliExit, cognitoDomain, hostOnly, main, type CliDeps } from '../src/cli.js';
import { COGNITO_DOMAIN, connect, networkError, recorder, STACK_DOMAIN, type Route } from './helpers.js';

const AIR = ['AIR_API_URL', 'AIR_MCP_CLIENT_ID', 'AIR_COGNITO_DOMAIN', 'AIR_MCP_RADAR_WRITES', 'AIR_MCP_CONFIG'];
let tmp: string;
let stderr: string[];

beforeEach(() => {
  for (const name of AIR) vi.stubEnv(name, undefined);
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'air-cli-'));
  // The generic server, whatever deployment.json this checkout carries.
  fs.writeFileSync(path.join(tmp, 'generic.json'), '{}');
  vi.stubEnv('AIR_MCP_CONFIG', path.join(tmp, 'generic.json'));
  stderr = [];
  vi.spyOn(process.stderr, 'write').mockImplementation((chunk: string | Uint8Array) => {
    stderr.push(String(chunk));
    return true;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

async function exitOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (err) {
    expect(err).toBeInstanceOf(CliExit);
    return (err as Error).message;
  }
  throw new Error('expected an exit');
}

function exitSync(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(CliExit);
    return (err as Error).message;
  }
  throw new Error('expected an exit');
}

function deployment(config: Route, extra: Record<string, Route> = {}) {
  return recorder({ 'GET /api/config': config, ...extra });
}

describe('main', () => {
  it('cli', async () => {
    expect(await exitOf(main(['serve']))).toMatch(/AIR_API_URL/);
    vi.stubEnv('AIR_API_URL', 'https://api.example.com/');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'mcp-client');
    const store = new TokenStore(path.join(tmp, 't.json'));
    const printed: string[] = [];
    const ran: McpServer[] = [];
    let configCalls = 0;
    let flaky = false;
    const rec = deployment(() => {
      configCalls++;
      if (flaky && configCalls > 1) throw networkError('down');
      return { json: { cognito_domain: STACK_DOMAIN } };
    });
    const deps: CliDeps = {
      fetch: rec.fetch,
      store: () => store,
      print: (line) => printed.push(line),
      serve: async (server) => {
        ran.push(server);
      },
    };
    const revoked: string[] = [];
    vi.spyOn(Authenticator.prototype, 'revoke').mockImplementation(async () => {
      revoked.push('yes');
    });
    await main(['logout'], deps);
    expect(revoked).toEqual(['yes']);
    expect(printed.join('\n')).toContain('Signed out');
    printed.length = 0;
    const logged: string[] = [];
    vi.spyOn(Authenticator.prototype, 'login').mockImplementation(async () => {
      logged.push('yes');
      return {};
    });
    await main(['login'], deps);
    expect(logged).toEqual(['yes']);
    expect(printed.join('\n')).toContain('Signed in');
    expect(printed.join('\n')).toContain(`Signing in at https://${STACK_DOMAIN}`);
    configCalls = 0;
    await main([], deps);
    expect(ran).toHaveLength(1);
    // The profile read after sign-in is cosmetic: when it fails the server still starts.
    flaky = true;
    configCalls = 0;
    await main([], deps);
    expect(ran).toHaveLength(2);
    expect(configCalls).toBe(2);
    expect(await exitOf(main(['dance'], deps))).toMatch(/^usage: insights-radar-mcp \[login\|logout\|serve\]$/);
  });

  it('serves the profile instructions, and radar writes only when asked', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'mcp-client');
    vi.stubEnv('AIR_COGNITO_DOMAIN', STACK_DOMAIN);
    const rec = deployment({ json: { cognito_domain: STACK_DOMAIN, profile: { app_name: 'Acme Radar', lists: {} } } });
    const ran: McpServer[] = [];
    const deps: CliDeps = {
      fetch: rec.fetch,
      store: () => new TokenStore(path.join(tmp, 't.json')),
      serve: async (server) => {
        ran.push(server);
      },
    };
    for (const value of ['', 'TRUE', 'yes', '1', 'no']) {
      vi.stubEnv('AIR_MCP_RADAR_WRITES', value);
      await main(['serve'], deps);
    }
    const tools = [];
    for (const server of ran) {
      const client = await connect(server);
      tools.push((await client.listTools()).tools.some((t) => t.name === 'import_radar_entries'));
      expect(client.getInstructions()).toMatch(/^Acme Radar is an insights radar/);
    }
    expect(tools).toEqual([false, true, true, true, false]);
    expect(stderr.join('')).toBe(''); // pinned: no warning
  });

  it('a config that is not an object keeps the default instructions', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'mcp-client');
    let n = 0;
    const rec = deployment(() => (++n === 1 ? { json: { cognito_domain: STACK_DOMAIN } } : { json: ['x'] }));
    let served: McpServer | undefined;
    await main(['serve'], {
      fetch: rec.fetch,
      store: () => new TokenStore(path.join(tmp, 't.json')),
      serve: async (server) => {
        served = server;
      },
    });
    const client = await connect(served as McpServer);
    expect(client.getInstructions()).toMatch(/^Agentic Insights Radar tracks/);
  });

  it('serves over stdio by default', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'mcp-client');
    const rec = deployment({ json: { cognito_domain: STACK_DOMAIN } });
    const connected = vi.spyOn(StdioServerTransport.prototype, 'start').mockResolvedValue(undefined);
    await main(['serve'], { fetch: rec.fetch, store: () => new TokenStore(path.join(tmp, 't.json')) });
    expect(connected).toHaveBeenCalledTimes(1);
  });

  it('uses the platform fetch, token store and console by default', async () => {
    vi.stubEnv('XDG_CONFIG_HOME', tmp);
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    const realFetch = vi.spyOn(globalThis, 'fetch');
    await main(['logout']);
    expect(log.mock.calls[0][0]).toMatch(/^Signed out/);
    expect(realFetch).not.toHaveBeenCalled(); // nothing stored: nothing sent
    const argv = process.argv;
    process.argv = ['node', 'insights-radar-mcp', 'logout'];
    try {
      await main();
    } finally {
      process.argv = argv;
    }
    expect(log).toHaveBeenCalledTimes(2);
  });

  it.each(['http://api.example.com', 'ftp://api.example.com', 'api.example.com', 'https://'])(
    'cli requires https: %s',
    async (url) => {
      vi.stubEnv('AIR_API_URL', url);
      expect(await exitOf(main(['serve']))).toMatch(/https/);
    },
  );

  it('cli allows plain http on loopback', () => {
    vi.stubEnv('AIR_API_URL', 'http://127.0.0.1:8080/');
    expect(apiUrl()).toBe('http://127.0.0.1:8080');
    vi.stubEnv('AIR_API_URL', ' http://localhost// ');
    expect(apiUrl()).toBe('http://localhost');
    vi.stubEnv('AIR_API_URL', 'http://[::1]:9000');
    expect(apiUrl()).toBe('http://[::1]:9000');
    vi.stubEnv('AIR_API_URL', 'HTTPS://Api.Example.com/x/');
    expect(apiUrl()).toBe('HTTPS://Api.Example.com/x');
  });

  it('cli logout keeps the session when cognito cannot be reached', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const store = new TokenStore(path.join(tmp, 't.json'));
    const here = { api_url: 'https://api.example.com', client_id: 'mcp-client', domain: COGNITO_DOMAIN };
    store.save({ refresh_token: 'r', deployment: here });
    const offline = recorder({}, networkError('no route'));
    expect(await exitOf(main(['logout'], { fetch: offline.fetch, store: () => store }))).toMatch(
      /could not be revoked: no route\. It is kept on this machine: run logout again, or delete .*t\.json to sign out here only\.$/,
    );
    expect(store.load()).not.toBeNull(); // so a retry can still revoke it
  });

  it('cli logout reports a refused revocation and a non-error failure', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    store.save({ refresh_token: 'r', deployment: { api_url: '', client_id: 'c', domain: COGNITO_DOMAIN } });
    const refused = recorder({ 'POST /oauth2/revoke': { status: 400, json: {} } });
    expect(await exitOf(main(['logout'], { fetch: refused.fetch, store: () => store }))).toBe(
      `The session could not be revoked: could not revoke the session (400). It is kept on this machine: run logout again, or delete ${store.path} to sign out here only.`,
    );
    vi.spyOn(Authenticator.prototype, 'revoke').mockImplementation(() => Promise.reject('odd'));
    expect(await exitOf(main(['logout'], { store: () => store }))).toMatch(/^The session could not be revoked: odd\./);
  });

  it('cli explains an unreachable deployment', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const down = deployment({ status: 502, json: { message: 'bad gateway' } });
    for (const command of ['serve', 'login']) {
      expect(
        await exitOf(main([command], { fetch: down.fetch, store: () => new TokenStore(path.join(tmp, 't.json')) })),
      ).toMatch(/^Could not reach Agentic Insights Radar at https:\/\/api\.example\.com: Server error '502/);
    }
    const offline = recorder({}, networkError('getaddrinfo ENOTFOUND api.example.com'));
    expect(await exitOf(main(['serve'], { fetch: offline.fetch }))).toBe(
      'Could not reach Agentic Insights Radar at https://api.example.com: getaddrinfo ENOTFOUND api.example.com',
    );
    expect(
      await exitOf(
        main(['serve'], {
          authenticatorFor: () => Promise.reject('plain'),
          store: () => new TokenStore(path.join(tmp, 't.json')),
        }),
      ),
    ).toBe('Could not reach Agentic Insights Radar at https://api.example.com: plain');
  });

  it('cli logout removes a corrupt token file', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const store = new TokenStore(path.join(tmp, 't.json'));
    fs.writeFileSync(store.path, '{not json');
    await main(['logout'], { store: () => store, print: () => undefined });
    expect(fs.existsSync(store.path)).toBe(false);
  });

  it('cli logout revokes without an api url', async () => {
    const store = new TokenStore(path.join(tmp, 't.json'));
    const here = { api_url: 'https://api.example.com', client_id: 'mcp-client', domain: COGNITO_DOMAIN };
    store.save({ refresh_token: 'r', deployment: here });
    const { fetch, calls } = recorder({ 'POST /oauth2/revoke': { json: {} } });
    const printed: string[] = [];
    await main(['logout'], { fetch, store: () => store, print: (line) => printed.push(line) });
    expect(calls.map((c) => c.url.host)).toEqual([COGNITO_DOMAIN]);
    expect(fs.existsSync(store.path)).toBe(false);
    expect(printed.join('\n')).toContain('Signed out');
    store.save({ refresh_token: 'r' }); // an earlier version's file: removed, nothing sent
    expect(await exitOf(main(['logout'], { fetch, store: () => store }))).toMatch(/It was removed here\.$/);
    expect(calls).toHaveLength(1);
    expect(fs.existsSync(store.path)).toBe(false);
  });

  it.each([
    ['not JSON', { html: '<html>' }, /Unexpected token|JSON/],
    ['not an object', { json: ['x'] }, /the \/api\/config reply is not an object/],
    ['no domain', { json: { user_pool_id: 'p' } }, /: 'cognito_domain'$/],
  ] as const)('cli explains a config reply that is %s', async (_name, config, detail) => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const rec = deployment(config);
    const text = await exitOf(
      main(['serve'], { fetch: rec.fetch, store: () => new TokenStore(path.join(tmp, 't.json')) }),
    );
    expect(text).toMatch(/^Could not reach/);
    expect(text).toMatch(detail);
  });

  it('p3_070 a failed sign-in is a message not a traceback', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const busy = {
      domain: 'x.auth.us-east-1.amazoncognito.com',
      idToken: async () => 't',
      login: async () => {
        throw new AuthError('cannot listen on 127.0.0.1:8765');
      },
    };
    expect(
      await exitOf(
        main(['login'], {
          authenticatorFor: async () => busy,
          print: () => undefined,
          store: () => new TokenStore(path.join(tmp, 't.json')),
        }),
      ),
    ).toBe('Sign-in failed: cannot listen on 127.0.0.1:8765');
    const odd = { ...busy, login: () => Promise.reject('closed') };
    expect(
      await exitOf(
        main(['login'], {
          authenticatorFor: async () => odd,
          print: () => undefined,
          store: () => new TokenStore(path.join(tmp, 't.json')),
        }),
      ),
    ).toBe('Sign-in failed: closed');
  });

  it('login passes the browser through and saves the session for this deployment', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'mcp-client');
    vi.stubEnv('AIR_COGNITO_DOMAIN', STACK_DOMAIN);
    const exp = Math.trunc(Date.now() / 1000) + 3600;
    const idToken = `h.${Buffer.from(JSON.stringify({ exp, iss: 'https://cognito-idp/us-east-1_P' })).toString('base64url')}.s`;
    const rec = deployment(
      { json: { cognito_domain: STACK_DOMAIN, user_pool_id: 'us-east-1_P' } },
      { 'POST /oauth2/token': { json: { id_token: idToken, refresh_token: 'r' } } },
    );
    const store = new TokenStore(path.join(tmp, 't.json'));
    const opened: string[] = [];
    await main(['login'], {
      fetch: rec.fetch,
      store: () => store,
      print: () => undefined,
      openBrowser: (url) => {
        opened.push(url);
        const state = new URL(url).searchParams.get('state');
        setTimeout(() => void fetch(`http://127.0.0.1:8765/callback?code=c&state=${state}`), 50);
        return true;
      },
    });
    expect(opened[0].startsWith(`https://${STACK_DOMAIN}/oauth2/authorize?`)).toBe(true);
    expect(store.load()?.deployment).toEqual({
      api_url: 'https://api.example.com',
      client_id: 'mcp-client',
      domain: STACK_DOMAIN,
    });
  });
});

describe('the sign-in domain', () => {
  it.each([
    'login.attacker.example',
    'air-111122223333.auth.us-east-1.amazoncognito.com.evil.example',
    'air.auth.us-east-1.amazoncognito.com',
    '',
  ])("a sign-in domain that is not the stack's is refused: %s", (domain) => {
    expect(exitSync(() => cognitoDomain(domain))).toContain('not an Agentic Insights Radar Cognito domain');
  });

  it('the stack domain or a pinned one is accepted', () => {
    expect(cognitoDomain(STACK_DOMAIN.toUpperCase(), () => undefined)).toBe(STACK_DOMAIN);
    vi.stubEnv('AIR_COGNITO_DOMAIN', 'login.example.com');
    expect(cognitoDomain('login.example.com')).toBe('login.example.com');
    expect(exitSync(() => cognitoDomain(STACK_DOMAIN))).toBe(
      `The deployment reports the sign-in domain '${STACK_DOMAIN}', not AIR_COGNITO_DOMAIN='login.example.com'.`,
    );
    expect(exitSync(() => cognitoDomain(null))).toContain("sign-in domain '', not");
  });

  it('p2011 an unpinned sign-in domain is warned about', () => {
    // The shape check passes for a Cognito hosted UI in any AWS account, including an attacker's, so an
    // unpinned domain is not a control: the user is told before a password is typed (P2-011).
    const attacker = 'corp-111122223333.auth.us-east-1.amazoncognito.com';
    expect(cognitoDomain(attacker)).toBe(attacker);
    const warning = stderr.join('');
    expect(warning).toContain('AIR_COGNITO_DOMAIN is not set');
    expect(warning).toContain(`'${attacker}'`);
    expect(warning).toContain('CognitoDomain');
    stderr.length = 0;
    vi.stubEnv('AIR_COGNITO_DOMAIN', attacker);
    expect(cognitoDomain(attacker)).toBe(attacker);
    expect(stderr.join('')).toBe(''); // pinned: nothing to warn about
  });

  it.each([
    'air-111122223333.auth.us-east-1.amazoncognito.com',
    'https://air-111122223333.auth.us-east-1.amazoncognito.com/',
    'AIR-111122223333.auth.us-east-1.amazoncognito.com.',
    'https://air-111122223333.auth.us-east-1.amazoncognito.com/login?client_id=x',
  ])('p3_071 the pin accepts the forms a user copies: %s', (pinned) => {
    vi.stubEnv('AIR_COGNITO_DOMAIN', pinned);
    const domain = 'air-111122223333.auth.us-east-1.amazoncognito.com';
    expect(cognitoDomain(domain)).toBe(domain);
  });

  it('host only', () => {
    expect(hostOnly('  HTTPS://A.Example.COM../x/y ')).toBe('a.example.com');
    expect(hostOnly('a.example.com')).toBe('a.example.com');
  });

  it('the client id must be set when the deployment has no default', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const rec = deployment({ json: { cognito_domain: STACK_DOMAIN } });
    expect(
      await exitOf(main(['login'], { fetch: rec.fetch, store: () => new TokenStore(path.join(tmp, 't.json')) })),
    ).toBe('set AIR_MCP_CLIENT_ID (see the implementation guide: MCP server)');
  });
});

describe('a deployment.json', () => {
  const ACME: Deployment = {
    name: 'acme-radar-mcp',
    tokenDir: 'acme-radar',
    defaults: {
      AIR_API_URL: 'https://radar.example.com/',
      AIR_MCP_CLIENT_ID: 'acme-client',
      AIR_COGNITO_DOMAIN: 'https://ACME-111122223333.auth.us-east-1.amazoncognito.com/',
    },
    tools: ['search', 'get_node', 'import_radar_entries'],
  };
  const ACME_DOMAIN = 'acme-111122223333.auth.us-east-1.amazoncognito.com';

  function write(value: unknown): string {
    const file = path.join(tmp, 'deployment.json');
    fs.writeFileSync(file, typeof value === 'string' ? value : JSON.stringify(value));
    vi.stubEnv('AIR_MCP_CONFIG', file);
    return file;
  }

  it('supplies the defaults, and the environment overrides each one', async () => {
    expect(apiUrl(ACME)).toBe('https://radar.example.com');
    expect(cognitoDomain(ACME_DOMAIN, undefined, ACME)).toBe(ACME_DOMAIN); // pinned by the default
    expect(exitSync(() => cognitoDomain(STACK_DOMAIN, undefined, ACME))).toContain('not AIR_COGNITO_DOMAIN');
    vi.stubEnv('AIR_API_URL', 'https://other.example.com');
    expect(apiUrl(ACME)).toBe('https://other.example.com');
    vi.stubEnv('AIR_API_URL', '   ');
    expect(apiUrl(ACME)).toBe('https://radar.example.com'); // blank is unset
    vi.stubEnv('AIR_COGNITO_DOMAIN', STACK_DOMAIN);
    expect(cognitoDomain(STACK_DOMAIN, undefined, ACME)).toBe(STACK_DOMAIN);
    vi.stubEnv('AIR_COGNITO_DOMAIN', undefined);
    const rec = deployment({ json: { cognito_domain: ACME_DOMAIN, user_pool_id: 'us-east-1_ACME' } });
    const store = new TokenStore(path.join(tmp, 't.json'));
    const auth = await authenticatorFor(apiUrl(ACME), store, rec.fetch, ACME);
    expect(auth.domain).toBe(ACME_DOMAIN);
    expect(auth.authorizeUrl('c', 's')).toContain('client_id=acme-client');
    expect(rec.calls[0].url.toString()).toBe('https://radar.example.com/api/config');
    vi.stubEnv('AIR_MCP_CLIENT_ID', 'from-env');
    const again = await authenticatorFor(apiUrl(ACME), store, rec.fetch, ACME);
    expect(again.authorizeUrl('c', 's')).toContain('client_id=from-env');
    await expect(again.idToken()).rejects.toThrow('not signed in; run `acme-radar-mcp login`');
  });

  it('names the command, the server and the token folder, and limits the tools', async () => {
    write({ ...ACME, defaults: { ...ACME.defaults } });
    vi.stubEnv('XDG_CONFIG_HOME', tmp);
    expect(await exitOf(main(['dance']))).toBe('usage: acme-radar-mcp [login|logout|serve]');
    const exp = Math.trunc(Date.now() / 1000) + 3600;
    const token = `h.${Buffer.from(JSON.stringify({ exp })).toString('base64url')}.s`;
    const saved = new TokenStore(path.join(tmp, 'acme-radar', 'tokens.json'));
    saved.save({
      id_token: token,
      deployment: { api_url: 'https://radar.example.com', client_id: 'acme-client', domain: ACME_DOMAIN },
    });
    const calls: string[] = [];
    const rec = recorder(
      {
        'GET /api/config': { json: { cognito_domain: ACME_DOMAIN } },
        'POST /api/graph/search': (req) => {
          calls.push(req.headers.get('authorization') ?? '');
          return { json: { items: [] } };
        },
      },
      undefined,
    );
    let served: McpServer | undefined;
    await main([], {
      fetch: rec.fetch,
      serve: async (server) => {
        served = server;
      },
    });
    const client = await connect(served as McpServer);
    expect(client.getServerVersion()?.name).toBe('acme-radar-mcp');
    expect((await client.listTools()).tools.map((t) => t.name)).toEqual(['search', 'get_node']); // writes are off
    await client.callTool({ name: 'search', arguments: { query: 'x' } });
    expect(calls).toEqual([`Bearer ${token}`]); // the session saved in acme-radar/, for this deployment
    vi.stubEnv('AIR_MCP_RADAR_WRITES', 'true');
    await main(['serve'], { fetch: rec.fetch, serve: async (server) => void (served = server) });
    const writer = await connect(served as McpServer);
    expect((await writer.listTools()).tools.map((t) => t.name)).toEqual(['search', 'get_node', 'import_radar_entries']);
    expect(stderr.join('')).toBe(''); // pinned by the default: no warning
  });

  it('logout uses its token folder and its name', async () => {
    write({ name: 'acme-radar-mcp', tokenDir: 'acme-radar' });
    vi.stubEnv('XDG_CONFIG_HOME', tmp);
    const saved = new TokenStore(path.join(tmp, 'acme-radar', 'tokens.json'));
    saved.save({ refresh_token: 'r' }); // no deployment recorded: removed, not revoked
    expect(await exitOf(main(['logout'], { print: () => undefined }))).toBe(
      'The session could not be revoked: the saved session does not record its deployment, so it was not revoked. ' +
        'It was removed here.',
    );
    expect(saved.exists()).toBe(false);
  });

  it('stops the server when the file is not valid', async () => {
    const file = write({ name: 'acme', tools: ['search', 'rm_rf'] });
    expect(await exitOf(main(['serve']))).toBe(
      `The deployment configuration is not valid: ${file}: tools: no such tool "rm_rf"`,
    );
    write('{"name": "acme", "extra": 1}');
    expect(await exitOf(main(['logout']))).toMatch(/: unknown key "extra" \(one of name, tokenDir, defaults, tools\)$/);
    vi.stubEnv('AIR_MCP_CONFIG', path.join(tmp, 'missing.json'));
    expect(await exitOf(main(['login']))).toMatch(
      /^The deployment configuration is not valid: .*missing\.json: cannot be read/,
    );
  });

  it('a test deployment passes its own', async () => {
    expect(await exitOf(main(['dance'], { deployment: { ...GENERIC, name: 'other-mcp' } }))).toBe(
      'usage: other-mcp [login|logout|serve]',
    );
  });
});

describe('login --no-browser', () => {
  it('prints the address instead of opening a browser', async () => {
    vi.stubEnv('AIR_API_URL', 'https://api.example.com');
    const opened: string[] = [];
    let options: { openBrowser?: (url: string) => boolean | Promise<boolean> } | undefined;
    const signIn = {
      domain: STACK_DOMAIN,
      idToken: async () => 't',
      login: async (o?: typeof options) => {
        options = o;
      },
    };
    const deps: CliDeps = {
      authenticatorFor: async () => signIn,
      print: () => undefined,
      store: () => new TokenStore(path.join(tmp, 't.json')),
      openBrowser: (url) => {
        opened.push(url);
        return true;
      },
    };
    await main(['login', '--no-browser'], deps);
    expect(await options?.openBrowser?.('https://x')).toBe(false);
    await main(['login'], deps);
    expect(await options?.openBrowser?.('https://y')).toBe(true);
    expect(opened).toEqual(['https://y']);
    await main(['login'], { ...deps, openBrowser: undefined });
    expect(options).toBeUndefined(); // the platform browser
  });
});
