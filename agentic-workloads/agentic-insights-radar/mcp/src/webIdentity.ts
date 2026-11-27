// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Signing in without a browser (the deploy options mcpIssuer and mcpAudience). A local command the user
 * trusts (AIR_MCP_WEB_IDENTITY_COMMAND) prints an OpenID Connect id token from the deployment's issuer for
 * its audience. The token goes to AWS STS only, never to the deployment: AssumeRoleWithWebIdentity gives a
 * short session of the deployment's MCP role named after the token's subject, and every API request is
 * signed with it (SigV4) and sent to the deployment's API Gateway URL under /iam.
 *
 * /api/config is unauthenticated, so what it reports is checked before use: the API URL must be an API
 * Gateway address, the role an IAM role ARN (and AIR_MCP_ROLE_ARN, when set, pins it), and the token must
 * come from the reported issuer for the reported audience and still be valid.
 */

import { execFile } from 'node:child_process';

import { Sha256 } from '@aws-crypto/sha256-js';
import { SignatureV4 } from '@smithy/signature-v4';

import { jwtClaims } from './auth.js';
import { type Fetch, form, send } from './http.js';
import { isObject, pyStr } from './py.js';

export class WebIdentityError extends Error {
  override name = 'WebIdentityError';
}

export interface WebIdentityConfig {
  roleArn: string;
  issuer: string;
  audience: string;
  apiUrl: string;
  region: string;
  /** The AWS STS endpoint in the API's region and partition. */
  stsUrl: string;
}

/** Signs one request: returns the headers to send (the given ones plus the signature). */
export interface RequestSigner {
  readonly apiUrl: string;
  sign(method: string, url: string, headers: Record<string, string>, body?: string): Promise<Record<string, string>>;
}

const API_HOST = /^[a-z0-9]{10}\.execute-api\.([a-z]{2}(?:-[a-z]+)+-\d)\.(amazonaws\.com(?:\.cn)?)$/;
const ROLE_ARN = /^arn:aws[a-z-]*:iam::\d{12}:role\/[\w+=,.@/-]{1,512}$/;
// The session name the role's trust policy requires is the token's subject, so it must be one STS accepts
// (2 to 64 of letters, digits and _+=,.@-); the API accepts the same.
const SUBJECT = /^[\w+=,.@-]{2,64}$/;
const COMMAND_TIMEOUT_MS = 15_000;
const SESSION_SECONDS = 900;
// An id token this close to expiry is treated as expired: STS, on its own clock, could refuse it.
const TOKEN_HEADROOM_MS = 30_000;
const REFRESH_MARGIN_MS = 60_000;

/** The deployment's mcp_web_identity block, checked, with the API Gateway URL the operator gave
 * (AIR_MCP_API_URL, the stack output McpApiUrl: the public config does not name the origin, so it
 * cannot be called around CloudFront's firewall by anyone who reads that config); null when the
 * deployment does not offer the option. */
export function webIdentityConfig(config: unknown, pinnedRole = '', apiUrlSetting = ''): WebIdentityConfig | null {
  if (!isObject(config) || !isObject(config.mcp_web_identity)) return null;
  const block = config.mcp_web_identity;
  const roleArn = pyStr(block.role_arn ?? '');
  const issuer = pyStr(block.issuer ?? '');
  const audience = pyStr(block.audience ?? '');
  const given = apiUrlSetting.trim();
  if (!given) {
    throw new WebIdentityError(
      "set AIR_MCP_API_URL to the deployment's McpApiUrl stack output to sign in with AIR_MCP_WEB_IDENTITY_COMMAND",
    );
  }
  let host = '';
  let apiUrl = given;
  try {
    const u = new URL(given);
    const bare = !u.port && !u.username && !u.password && !u.search && !u.hash;
    if (u.protocol === 'https:' && bare && (u.pathname === '/' || u.pathname === '')) {
      host = u.hostname;
      apiUrl = u.origin;
    }
  } catch {
    host = '';
  }
  const match = API_HOST.exec(host);
  if (!match) throw new WebIdentityError(`AIR_MCP_API_URL is not an API Gateway URL: ${given}`);
  const [, region, suffix] = match;
  if (!ROLE_ARN.test(roleArn))
    throw new WebIdentityError(`the deployment reports a role that is not an IAM role: ${roleArn}`);
  if (pinnedRole && pinnedRole !== roleArn) {
    throw new WebIdentityError(`the deployment reports the role ${roleArn}, not AIR_MCP_ROLE_ARN=${pinnedRole}`);
  }
  if (!/^https:\/\/[a-z0-9.-]+(?:\/[\w.~/-]*)?$/.test(issuer) || !audience) {
    throw new WebIdentityError('the deployment reports no valid issuer and audience for its id tokens');
  }
  return { roleArn, issuer, audience, apiUrl, region, stsUrl: `https://sts.${region}.${suffix}/` };
}

/** Runs the command (no shell) with the audience as its one argument and returns the token it prints. */
export type TokenCommand = (command: string, audience: string) => Promise<string>;

export const runTokenCommand: TokenCommand = (command, audience) =>
  new Promise((resolve, reject) => {
    execFile(
      command,
      [audience],
      { timeout: COMMAND_TIMEOUT_MS, maxBuffer: 64 * 1024, encoding: 'utf8' },
      (err, stdout, stderr) => {
        if (err) {
          const why = String(stderr || err.message)
            .trim()
            .split('\n')[0];
          reject(new WebIdentityError(`the web identity command failed: ${why}`));
        } else {
          resolve(String(stdout).trim());
        }
      },
    );
  });

interface Session {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiresAt: number;
  subject: string;
}

export interface SignerOptions {
  fetch?: Fetch;
  run?: TokenCommand;
  now?: () => number;
}

export class WebIdentitySigner implements RequestSigner {
  readonly apiUrl: string;
  private session: Session | null = null;
  private pending: Promise<Session> | null = null;
  private readonly fetchFn: Fetch;
  private readonly run: TokenCommand;
  private readonly now: () => number;

  constructor(
    private readonly config: WebIdentityConfig,
    private readonly command: string,
    options: SignerOptions = {},
  ) {
    this.apiUrl = config.apiUrl;
    this.fetchFn = options.fetch ?? globalThis.fetch;
    this.run = options.run ?? runTokenCommand;
    this.now = options.now ?? Date.now;
  }

  /** The signed-in subject (for messages); signs in if needed. */
  async subject(): Promise<string> {
    return (await this.credentials()).subject;
  }

  async sign(
    method: string,
    url: string,
    headers: Record<string, string>,
    body?: string,
  ): Promise<Record<string, string>> {
    const s = await this.credentials();
    const u = new URL(url);
    const query: Record<string, string | string[]> = {};
    for (const [k, v] of u.searchParams) {
      const had = query[k];
      query[k] = had === undefined ? v : Array.isArray(had) ? [...had, v] : [had, v];
    }
    const signer = new SignatureV4({
      service: 'execute-api',
      region: this.config.region,
      credentials: { accessKeyId: s.accessKeyId, secretAccessKey: s.secretAccessKey, sessionToken: s.sessionToken },
      sha256: Sha256,
    });
    const signed = await signer.sign({
      method,
      protocol: 'https:',
      hostname: u.hostname,
      path: u.pathname,
      query,
      headers: { ...headers, host: u.hostname },
      body,
    });
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(signed.headers)) out[k] = String(v);
    return out;
  }

  private async credentials(): Promise<Session> {
    if (this.session && this.session.expiresAt - REFRESH_MARGIN_MS > this.now()) return this.session;
    this.pending ??= this.assume().finally(() => {
      this.pending = null;
    });
    this.session = await this.pending;
    return this.session;
  }

  private async assume(): Promise<Session> {
    const token = await this.run(this.command, this.config.audience);
    const subject = this.check(token);
    const asked = this.now();
    const res = await send(this.fetchFn, this.config.stsUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json' },
      body: form({
        Action: 'AssumeRoleWithWebIdentity',
        Version: '2011-06-15',
        RoleArn: this.config.roleArn,
        RoleSessionName: subject,
        WebIdentityToken: token,
        DurationSeconds: String(SESSION_SECONDS),
      }),
      timeoutMs: 30_000,
    });
    let reply: unknown;
    try {
      reply = JSON.parse(res.text);
    } catch {
      reply = null;
    }
    if (res.status !== 200) {
      const error = isObject(reply) && isObject(reply.Error) ? reply.Error : null;
      const why = error ? `${pyStr(error.Code ?? '')}: ${pyStr(error.Message ?? '')}` : `HTTP ${res.status}`;
      throw new WebIdentityError(`AWS STS refused the sign-in (${why})`);
    }
    const c = credentialsOf(reply);
    if (!c) throw new WebIdentityError('AWS STS answered without credentials');
    // STS's Expiration is on AWS's clock. Counted on ours from before the request as well, a local clock
    // that runs behind still refreshes before AWS stops accepting the credentials.
    return { ...c, expiresAt: Math.min(c.expiresAt, asked + SESSION_SECONDS * 1000), subject };
  }

  /** The token's subject, after checking it is from the issuer, for the audience and still valid. */
  private check(token: string): string {
    const claims = jwtClaims(token);
    if (Object.keys(claims).length === 0) {
      throw new WebIdentityError('the web identity command did not print an id token');
    }
    const aud = claims.aud;
    const audiences = Array.isArray(aud) ? aud.map(String) : [String(aud ?? '')];
    if (String(claims.iss ?? '') !== this.config.issuer) {
      throw new WebIdentityError(`the id token is from ${String(claims.iss)}, not ${this.config.issuer}`);
    }
    if (!audiences.includes(this.config.audience)) {
      throw new WebIdentityError(`the id token is not for ${this.config.audience}`);
    }
    if (typeof claims.exp !== 'number' || claims.exp * 1000 - TOKEN_HEADROOM_MS <= this.now()) {
      throw new WebIdentityError('the id token has expired: sign in again with your identity provider');
    }
    const subject = String(claims.sub ?? '');
    if (!SUBJECT.test(subject)) throw new WebIdentityError('the id token has no usable subject');
    return subject;
  }
}

function credentialsOf(reply: unknown): Omit<Session, 'subject'> | null {
  if (!isObject(reply)) return null;
  const r = reply.AssumeRoleWithWebIdentityResponse;
  const result = isObject(r) ? r.AssumeRoleWithWebIdentityResult : null;
  const c = isObject(result) ? result.Credentials : null;
  if (!isObject(c)) return null;
  const [keyId, keyMaterial, sessionToken] = [c.AccessKeyId, c.SecretAccessKey, c.SessionToken];
  if (!isString(keyId) || !isString(keyMaterial) || !isString(sessionToken)) return null;
  const exp = typeof c.Expiration === 'number' ? c.Expiration * 1000 : Date.parse(String(c.Expiration));
  if (!Number.isFinite(exp)) return null;
  return { accessKeyId: keyId, secretAccessKey: keyMaterial, sessionToken, expiresAt: exp };
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}
