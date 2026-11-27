// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Sign in to Agentic Insights Radar's Cognito user pool from the command line (OAuth code + PKCE).
 *
 * `insights-radar-mcp login` opens the hosted sign-in page in a browser and waits on a loopback
 * address for the redirect. Tokens are stored in `~/.config/insights-radar/tokens.json` (the folder is
 * the deployment's `tokenDir`) with owner-only permissions and refreshed with the refresh token when the
 * ID token is near expiry.
 * The MCP server sends the ID token to the API: the API authorizes with it as the signed-in user.
 */

import childProcess from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

import { GENERIC } from './deployment.js';
import { type Fetch, form, send } from './http.js';
import { cpSlice, isObject, pyRepr } from './py.js';

export const REDIRECT_PORT = 8765;
// 127.0.0.1, not localhost: browsers may resolve localhost to ::1, which this server does not bind.
export const REDIRECT_HOST = '127.0.0.1';
export const REDIRECT_URI = `http://${REDIRECT_HOST}:${REDIRECT_PORT}/callback`;
export const REFRESH_MARGIN = 300;

/** What every "sign in again" message tells the user to run. */
export function loginHint(name: string = GENERIC.name): string {
  return `run \`${name} login\``;
}

export function defaultStore(tokenDir: string = GENERIC.tokenDir): string {
  // `||`: an empty XDG_CONFIG_HOME must not turn this into a path relative to the working directory.
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(base, tokenDir, 'tokens.json');
}

export class AuthError extends Error {
  override name = 'AuthError';
}

export class CognitoClient {
  constructor(
    readonly domain: string, // e.g. air-123.auth.us-east-1.amazoncognito.com
    readonly clientId: string,
  ) {}

  get base(): string {
    return `https://${this.domain}`;
  }
}

export function pkcePair(): { verifier: string; challenge: string } {
  const verifier = randomBytes(64).toString('base64url').slice(0, 96);
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

function payloadOf(token: string): unknown {
  const parts = token.split('.');
  if (parts.length < 2) throw new RangeError('no payload');
  return JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
}

/** The payload of a JWT, unverified (Cognito issued it over TLS; this only reads it), or {}. */
export function jwtClaims(token: string): Record<string, unknown> {
  try {
    const claims = payloadOf(token);
    return isObject(claims) ? claims : {};
  } catch {
    return {};
  }
}

/** The `exp` claim, read as Python's `int()` reads it. */
export function jwtExpiry(token: unknown, name: string = GENERIC.name): number {
  try {
    if (typeof token !== 'string') throw new TypeError('not a string');
    const claims = payloadOf(token);
    if (!isObject(claims) || !('exp' in claims)) throw new TypeError('no exp');
    return pyInt(claims.exp);
  } catch {
    throw new AuthError(`the stored token is malformed; ${loginHint(name)}`);
  }
}

function pyInt(value: unknown): number {
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string' && /^\s*[+-]?\d+\s*$/.test(value)) return parseInt(value, 10);
  throw new TypeError('not an integer');
}

export type Tokens = Record<string, unknown>;

export interface StoreOptions {
  /** The command name the messages tell the user to run. */
  name?: string;
  /** The folder under the config home, when no file is given. */
  tokenDir?: string;
}

export class TokenStore {
  readonly path: string;
  private readonly name: string;

  constructor(file?: string, options: StoreOptions = {}) {
    this.path = file ?? defaultStore(options.tokenDir);
    this.name = options.name ?? GENERIC.name;
  }

  exists(): boolean {
    return fs.existsSync(this.path);
  }

  load(): Tokens | null {
    if (!this.exists()) return null;
    this.checkPrivate();
    let data: unknown;
    try {
      data = JSON.parse(fs.readFileSync(this.path, 'utf8'));
    } catch {
      return null; // a corrupt or partly written file holds no usable session
    }
    return isObject(data) ? data : null;
  }

  /** A token file someone else owns is refused; one readable by others (restored from a backup, synced,
   * copied) is made owner-only again before it is used. */
  private checkPrivate(): void {
    /* v8 ignore next */
    if (typeof process.getuid !== 'function') return; // Windows has no POSIX owner
    const info = fs.statSync(this.path);
    if (info.uid !== process.getuid()) {
      throw new AuthError(`${this.path} belongs to another user; delete it and ${loginHint(this.name)}`);
    }
    if (info.mode & 0o077) fs.chmodSync(this.path, 0o600);
  }

  /** Write atomically; the file is owner-only from the moment it exists. */
  save(tokens: Tokens): void {
    const dir = path.dirname(this.path);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    fs.chmodSync(dir, 0o700); // mkdir's mode does not apply to a directory that already exists
    // A unique temporary file per write, created 0600 and exclusively, so a login and a running server
    // refreshing at the same moment cannot remove or interleave each other's file.
    const tmp = path.join(dir, `.${path.basename(this.path)}.${randomBytes(6).toString('hex')}.tmp`);
    try {
      const fd = fs.openSync(tmp, 'wx', 0o600);
      try {
        fs.writeSync(fd, JSON.stringify(tokens));
      } finally {
        fs.closeSync(fd);
      }
      fs.renameSync(tmp, this.path);
    } catch (err) {
      fs.rmSync(tmp, { force: true });
      throw err;
    }
  }

  clear(): void {
    fs.rmSync(this.path, { force: true });
  }
}

export type OpenBrowser = (url: string) => boolean | Promise<boolean>;

/** Open `url` in the user's browser, as Python's `webbrowser.open` does: false when none can be started. */
export function openInBrowser(url: string): Promise<boolean> {
  const env = process.env;
  if (process.platform === 'linux' && !env.DISPLAY && !env.WAYLAND_DISPLAY) return Promise.resolve(false);
  const [command, args]: [string, string[]] =
    process.platform === 'darwin'
      ? ['open', [url]]
      : process.platform === 'win32'
        ? ['rundll32', ['url.dll,FileProtocolHandler', url]] // no shell: the URL is never parsed by cmd
        : ['xdg-open', [url]];
  return new Promise((resolve) => {
    let child: childProcess.ChildProcess;
    try {
      child = childProcess.spawn(command, args, { stdio: 'ignore', detached: true });
    } catch {
      resolve(false);
      return;
    }
    // A launcher that is still running after a few seconds has handed the address to a browser.
    const timer = setTimeout(() => resolve(true), 3000);
    timer.unref();
    child.once('error', () => {
      clearTimeout(timer);
      resolve(false);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      resolve(code === 0);
    });
    child.unref();
  });
}

export interface LoginOptions {
  openBrowser?: OpenBrowser;
  timeoutMs?: number;
  print?: (line: string) => void;
}

const ERRNO_TEXT: Record<string, string> = {
  EADDRINUSE: 'Address already in use',
  EACCES: 'Permission denied',
  EADDRNOTAVAIL: 'Cannot assign requested address',
};

interface Deployment {
  api_url: string;
  client_id: string;
  domain: string;
}

function sameDeployment(saved: unknown, here: Deployment): boolean {
  if (!isObject(saved)) return false;
  const keys = Object.keys(saved).sort();
  return (
    keys.join(',') === 'api_url,client_id,domain' &&
    saved.api_url === here.api_url &&
    saved.client_id === here.client_id &&
    saved.domain === here.domain
  );
}

export interface AuthenticatorOptions {
  fetch?: Fetch;
  now?: () => number;
  apiUrl?: string;
  userPoolId?: string;
  /** The command name the messages tell the user to run. */
  name?: string;
}

export class Authenticator {
  private readonly deployment: Deployment;
  private readonly fetchFn: Fetch;
  private readonly now: () => number;
  private readonly pool: string;
  private readonly name: string;
  private lock: Promise<unknown> = Promise.resolve();

  constructor(
    private readonly cognito: CognitoClient,
    private readonly store: TokenStore,
    options: AuthenticatorOptions = {},
  ) {
    // Tokens are saved with the deployment they were issued for, and used only for that one: pointing
    // AIR_API_URL at another deployment must not send it this one's token.
    this.deployment = {
      api_url: (options.apiUrl ?? '').replace(/\/+$/, ''),
      client_id: cognito.clientId,
      domain: cognito.domain,
    };
    this.pool = options.userPoolId ?? '';
    this.name = options.name ?? GENERIC.name;
    this.fetchFn = options.fetch ?? globalThis.fetch;
    this.now = options.now ?? (() => Date.now() / 1000);
  }

  get domain(): string {
    return this.cognito.domain;
  }

  authorizeUrl(challenge: string, state: string): string {
    const query = form({
      response_type: 'code',
      client_id: this.cognito.clientId,
      redirect_uri: REDIRECT_URI,
      scope: 'openid email profile',
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
    });
    return `${this.cognito.base}/oauth2/authorize?${query}`;
  }

  private async tokenRequest(fields: Record<string, string>): Promise<Tokens> {
    const res = await send(this.fetchFn, `${this.cognito.base}/oauth2/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form({ ...fields, client_id: this.cognito.clientId }),
      timeoutMs: 20_000,
    });
    if (res.status !== 200) throw new AuthError(`sign-in failed (${res.status}): ${cpSlice(res.text, 200)}`);
    const body = JSON.parse(res.text) as unknown;
    if (!isObject(body)) throw new AuthError('sign-in failed: the token reply is not an object');
    return body;
  }

  async exchange(code: string, verifier: string): Promise<Tokens> {
    let tokens = await this.tokenRequest({
      grant_type: 'authorization_code',
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
    });
    const issuer = String(jwtClaims(String(tokens.id_token ?? '')).iss ?? '');
    if (this.pool && issuer.split('/').pop() !== this.pool) {
      // The token was issued by another user pool than the deployment reports (P3-026).
      throw new AuthError(`the sign-in returned a token from ${pyRepr(issuer)}, not the deployment's user pool`);
    }
    tokens = { ...tokens, deployment: this.deployment };
    this.store.save(tokens);
    return tokens;
  }

  /** Invalidate the refresh token at Cognito, so a copy of the token file can no longer be renewed.
   * An ID token already issued stays valid until it expires (at most an hour). */
  async revoke(): Promise<void> {
    const tokens = this.store.load() ?? {};
    const saved = tokens.deployment || {};
    if ('refresh_token' in tokens) {
      // Revoked at the Cognito pool that issued it (saved at login), never at the one configured now:
      // AIR_API_URL may point elsewhere, and its /config names the domain the token would be posted to.
      if (!(isObject(saved) && saved.domain && saved.client_id)) {
        this.store.clear();
        throw new AuthError('the saved session does not record its deployment, so it was not revoked');
      }
      const res = await send(this.fetchFn, `https://${String(saved.domain)}/oauth2/revoke`, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: form({ token: String(tokens.refresh_token), client_id: String(saved.client_id) }),
        timeoutMs: 20_000,
      });
      if (res.status !== 200) throw new AuthError(`could not revoke the session (${res.status})`);
    }
    this.store.clear();
  }

  /** A valid ID token, refreshed when it expires within five minutes. One refresh at a time. */
  idToken(): Promise<string> {
    const run = this.lock.then(() => this.currentIdToken());
    this.lock = run.catch(() => undefined);
    return run;
  }

  private async currentIdToken(): Promise<string> {
    const tokens = this.store.load();
    if (!tokens || !('id_token' in tokens)) throw new AuthError(`not signed in; ${loginHint(this.name)}`);
    if (!sameDeployment(tokens.deployment, this.deployment)) {
      throw new AuthError(`signed in to a different deployment; ${loginHint(this.name)}`);
    }
    if (jwtExpiry(tokens.id_token, this.name) - this.now() > REFRESH_MARGIN) return String(tokens.id_token);
    if (!('refresh_token' in tokens)) throw new AuthError(`the session expired; ${loginHint(this.name)}`);
    let fresh: Tokens;
    try {
      fresh = await this.tokenRequest({ grant_type: 'refresh_token', refresh_token: String(tokens.refresh_token) });
    } catch (err) {
      // revoked, or past the refresh token's lifetime
      if (err instanceof AuthError) throw new AuthError(`the session expired; ${loginHint(this.name)}`, { cause: err });
      throw err;
    }
    const merged = { ...tokens, ...fresh }; // Cognito does not return a new refresh token
    this.store.save(merged);
    return String(merged.id_token);
  }

  async login(options: LoginOptions = {}): Promise<Tokens> {
    const openBrowser = options.openBrowser ?? openInBrowser;
    const timeoutMs = options.timeoutMs ?? 300_000;
    const print = options.print ?? ((line: string) => console.log(line));
    const { verifier, challenge } = pkcePair();
    const state = randomBytes(24).toString('base64url');
    const received: Record<string, string> = {};
    let finish!: () => void;
    const done = new Promise<void>((resolve) => (finish = resolve));

    const server = http.createServer((req, res) => {
      res.setHeader('connection', 'close');
      if (req.method !== 'GET') {
        res.writeHead(501).end();
        return;
      }
      const url = new URL(String(req.url), REDIRECT_URI);
      if (url.pathname !== '/callback') {
        res.writeHead(404).end();
        return;
      }
      const params: Record<string, string> = {};
      for (const [k, v] of url.searchParams) {
        if (v !== '' && !(k in params)) params[k] = v; // first value of each, blanks dropped (parse_qs)
      }
      if (params.state !== state) {
        // Not the redirect for this login (another page or process): ignore it, keep waiting.
        res.writeHead(400).end();
        return;
      }
      Object.assign(received, params);
      const ok = 'code' in received;
      res.writeHead(ok ? 200 : 400, { 'content-type': 'text/plain; charset=utf-8' });
      res.end(ok ? 'Signed in to Agentic Insights Radar. You can close this tab.' : 'Sign-in failed.');
      finish();
    });
    // A browser's idle pre-connection must not block the real callback.
    server.requestTimeout = 10_000;
    server.headersTimeout = 10_000;
    server.keepAliveTimeout = 0;

    try {
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject);
        server.listen(REDIRECT_PORT, REDIRECT_HOST, () => {
          server.off('error', reject);
          resolve();
        });
      });
    } catch (err) {
      // the redirect URI is registered with this port, so no other port works
      const code = (err as NodeJS.ErrnoException).code ?? '';
      const reason = ERRNO_TEXT[code] ?? (err as Error).message;
      throw new AuthError(
        `cannot listen on ${REDIRECT_HOST}:${REDIRECT_PORT} for the sign-in redirect (${reason}); ` +
          'close the program using that port and run login again',
      );
    }
    let timer: NodeJS.Timeout | undefined;
    try {
      const url = this.authorizeUrl(challenge, state);
      if (!(await openBrowser(url))) print(`Open this address to sign in:\n${url}`);
      const signedIn = await Promise.race([
        done.then(() => true),
        new Promise<boolean>((resolve) => (timer = setTimeout(() => resolve(false), timeoutMs))),
      ]);
      if (!signedIn) throw new AuthError('timed out waiting for sign-in');
    } finally {
      clearTimeout(timer);
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
    if (!('code' in received)) throw new AuthError(`sign-in was refused: ${received.error ?? 'unknown error'}`);
    return this.exchange(received.code, verifier);
  }
}
