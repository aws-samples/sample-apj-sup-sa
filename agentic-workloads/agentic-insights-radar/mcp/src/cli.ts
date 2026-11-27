// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * `insights-radar-mcp [login [--no-browser]|logout|serve]`: the Agentic Insights Radar MCP server.
 *
 * Configuration (environment):
 *   AIR_API_URL          the deployment's address (stack output AppUrl)
 *   AIR_MCP_CLIENT_ID    the MCP app client id (stack output McpClientId)
 *   AIR_COGNITO_DOMAIN   the sign-in domain to trust (stack output CognitoDomain, host name only; a copied
 *                        https:// or trailing slash is accepted). Set it: without it the domain the
 *                        deployment reports only has to *look* like the stack's own
 *                        (<prefix>-<account>.auth.<region>.amazoncognito.com), and a Cognito domain in an
 *                        attacker's own account has exactly that form (P2-011)
 *   AIR_MCP_RADAR_WRITES 1, true or yes to offer the radar edit tools
 *   AIR_MCP_CONFIG       a deployment.json to use instead of the one next to package.json
 *
 * An installation for one deployment can carry the first three as defaults, and its own command name,
 * token folder and tool list, in deployment.json (see `deployment.ts`). The environment still wins.
 */

import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

import { Authenticator, CognitoClient, type OpenBrowser, TokenStore } from './auth.js';
import { InsightsRadarApi, type TokenSource } from './client.js';
import { type DefaultKey, type Deployment, GENERIC, loadDeployment } from './deployment.js';
import type { Fetch } from './http.js';
import { isObject, pyRepr, pyStr, pyStrip, pyTruthy } from './py.js';
import { buildServer, toolNames } from './server.js';
import { WebIdentitySigner, webIdentityConfig } from './webIdentity.js';

/** What `sys.exit(message)` did in the Python server: the message goes to stderr and the exit code is 1. */
export class CliExit extends Error {
  override name = 'CliExit';
}

/** A setting from the environment, else the deployment's default, else ''. A blank variable is unset. */
function setting(name: DefaultKey, deployment: Deployment): string {
  // An empty AIR_MCP_WEB_IDENTITY_COMMAND is a choice (keep the browser sign-in), not an unset one.
  if (name === 'AIR_MCP_WEB_IDENTITY_COMMAND' && process.env[name] !== undefined) return pyStrip(process.env[name]);
  return pyStrip(process.env[name] ?? '') || deployment.defaults[name] || '';
}

function env(name: DefaultKey, deployment: Deployment): string {
  const value = setting(name, deployment);
  if (!value) throw new CliExit(`set ${name} (see the implementation guide: MCP server)`);
  return value;
}

const LOOPBACK = ['localhost', '127.0.0.1', '::1'];

/** The deployment address; tokens are sent to it, so it must be https (plain http only on loopback). */
export function apiUrl(deployment: Deployment = GENERIC): string {
  const url = env('AIR_API_URL', deployment).replace(/\/+$/, '');
  let parts: URL | null;
  try {
    parts = new URL(url);
  } catch {
    parts = null;
  }
  const host = parts ? parts.hostname.replace(/^\[(.*)\]$/, '$1') : '';
  const ok = parts && host && (parts.protocol === 'https:' || (parts.protocol === 'http:' && LOOPBACK.includes(host)));
  if (!ok) throw new CliExit('AIR_API_URL must be an https:// address (the stack output AppUrl)');
  return url;
}

// The hosted sign-in domain the stack creates: <prefix>-<12-digit account>.auth.<region>.amazoncognito.com.
export const STACK_DOMAIN = /^[a-z][a-z0-9-]{0,40}-\d{12}\.auth\.[a-z]{2}(?:-[a-z]+)+-\d\.amazoncognito\.com$/;

export function unpinnedWarning(domain: string): string {
  return (
    `WARNING: AIR_COGNITO_DOMAIN is not set, so this check only confirms that ${pyRepr(domain)} has the shape of ` +
    'an Amazon Cognito hosted-UI domain. Any AWS account can create one with that shape, including an ' +
    "attacker who gave you this AIR_API_URL. Set AIR_COGNITO_DOMAIN to your deployment's CognitoDomain " +
    'stack output before you type a password or a one-time code.'
  );
}

/** A domain as a user may copy it, from the stack output or the console's address bar: no scheme, no path,
 * no trailing slash or dot, lower case (P3-071). */
export function hostOnly(value: string): string {
  let text = pyStrip(value).toLowerCase();
  const scheme = text.indexOf('://');
  if (scheme >= 0) text = text.slice(scheme + 3);
  return text.split('/')[0].replace(/\.+$/, '');
}

/** The domain to send the user's password to. /api/config is unauthenticated, so a look-alike AIR_API_URL
 * could name a phishing page. With AIR_COGNITO_DOMAIN set, only that domain is accepted. Without it, the
 * shape of the domain is all that is checked, which is weak: say so loudly. */
export function cognitoDomain(
  reported: unknown,
  warn: (line: string) => void = stderrLine,
  deployment: Deployment = GENERIC,
): string {
  const pinned = hostOnly(setting('AIR_COGNITO_DOMAIN', deployment));
  const domain = pyStrip(pyTruthy(reported) ? pyStr(reported) : '').toLowerCase();
  if (pinned) {
    if (domain !== pinned) {
      throw new CliExit(
        `The deployment reports the sign-in domain ${pyRepr(domain)}, not AIR_COGNITO_DOMAIN=${pyRepr(pinned)}.`,
      );
    }
    return pinned;
  }
  if (!STACK_DOMAIN.test(domain)) {
    throw new CliExit(
      `The deployment reports the sign-in domain ${pyRepr(domain)}, which is not an Agentic Insights Radar ` +
        'Cognito domain. Check AIR_API_URL, or set AIR_COGNITO_DOMAIN to the domain you trust.',
    );
  }
  warn(unpinnedWarning(domain));
  return domain;
}

function stderrLine(line: string): void {
  process.stderr.write(`${line}\n`);
}

export interface SignIn extends TokenSource {
  readonly domain: string;
  login(options?: { openBrowser?: OpenBrowser }): Promise<unknown>;
}

/** Everything `main` reaches outside itself, so tests can stand in for it. */
export interface CliDeps {
  fetch?: Fetch;
  deployment?: Deployment;
  store?: (deployment: Deployment) => TokenStore;
  authenticatorFor?: (apiUrl: string, store: TokenStore, fetch: Fetch, deployment: Deployment) => Promise<SignIn>;
  signerFor?: (
    apiUrl: string,
    command: string,
    fetch: Fetch,
    deployment: Deployment,
  ) => Promise<WebIdentitySigner | null>;
  openBrowser?: OpenBrowser;
  serve?: (server: McpServer) => Promise<void>;
  print?: (line: string) => void;
}

export async function authenticatorFor(
  url: string,
  store: TokenStore,
  fetchFn: Fetch,
  deployment: Deployment = GENERIC,
): Promise<Authenticator> {
  const config = await new InsightsRadarApi(url, null, fetchFn).config(); // public endpoint
  if (!isObject(config)) throw new TypeError('the /api/config reply is not an object');
  if (!('cognito_domain' in config)) throw new Error("'cognito_domain'");
  const domain = cognitoDomain(config.cognito_domain, stderrLine, deployment);
  return new Authenticator(new CognitoClient(domain, env('AIR_MCP_CLIENT_ID', deployment)), store, {
    fetch: fetchFn,
    apiUrl: url,
    userPoolId: pyTruthy(config.user_pool_id) ? pyStr(config.user_pool_id) : '',
    name: deployment.name,
  });
}

async function stdio(server: McpServer): Promise<void> {
  await server.connect(new StdioServerTransport());
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export async function main(argv: string[] = process.argv.slice(2), deps: CliDeps = {}): Promise<void> {
  const fetchFn = deps.fetch ?? globalThis.fetch;
  const print = deps.print ?? ((line: string) => console.log(line));
  const command = argv[0] ?? 'serve';
  let deployment: Deployment;
  try {
    deployment = deps.deployment ?? loadDeployment(toolNames());
  } catch (err) {
    throw new CliExit(`The deployment configuration is not valid: ${message(err)}`);
  }
  if (!['login', 'logout', 'serve'].includes(command)) {
    throw new CliExit(`usage: ${deployment.name} [login|logout|serve]`);
  }
  const store = deps.store
    ? deps.store(deployment)
    : new TokenStore(undefined, { name: deployment.name, tokenDir: deployment.tokenDir });
  if (command === 'logout') {
    // Revoked at the Cognito pool saved with the session, so neither AIR_API_URL nor the deployment's
    // /config is needed: a broken address or a deployment that is down must not stop the revocation.
    try {
      await new Authenticator(new CognitoClient('', ''), store, { fetch: fetchFn, name: deployment.name }).revoke();
    } catch (err) {
      const kept = store.exists();
      const where = `It is kept on this machine: run logout again, or delete ${store.path} to sign out here only.`;
      throw new CliExit(`The session could not be revoked: ${message(err)}. ${kept ? where : 'It was removed here.'}`);
    }
    print('Signed out. The session can no longer be renewed; a token already issued expires within the hour.');
    if (setting('AIR_MCP_WEB_IDENTITY_COMMAND', deployment)) {
      // That command signs the server in again on its next start, without the session logout revoked.
      print(
        'AIR_MCP_WEB_IDENTITY_COMMAND is set: on a deployment that offers sign-in without a browser, the server ' +
          "still signs in with your identity provider's token. Unset it, or end that provider session, to stop it.",
      );
    }
    return;
  }
  const url = apiUrl(deployment);
  const idCommand = setting('AIR_MCP_WEB_IDENTITY_COMMAND', deployment);
  let signer: WebIdentitySigner | null = null;
  if (idCommand) {
    try {
      signer = await (deps.signerFor ?? signerFor)(url, idCommand, fetchFn, deployment);
    } catch (err) {
      throw new CliExit(`Could not sign in with AIR_MCP_WEB_IDENTITY_COMMAND: ${message(err)}`);
    }
  }
  if (signer) {
    if (command === 'login') {
      let who: string;
      try {
        who = await signer.subject();
        // The deployment must also accept this person (an account it knows, enabled): ask it, so login does
        // not say "signed in" to someone every tool call will then refuse.
        await new InsightsRadarApi(url, signer, fetchFn).request('GET', '/api/me');
      } catch (err) {
        throw new CliExit(`Sign-in failed: ${message(err)}`);
      }
      print(`No browser sign-in is needed: this deployment accepts your identity token. Signed in as ${who}.`);
      return;
    }
    await serveWith(new InsightsRadarApi(url, signer, fetchFn), deployment, deps);
    return;
  }
  let auth: SignIn;
  try {
    auth = await (deps.authenticatorFor ?? authenticatorFor)(url, store, fetchFn, deployment);
  } catch (err) {
    if (err instanceof CliExit) throw err;
    throw new CliExit(`Could not reach Agentic Insights Radar at ${url}: ${message(err)}`);
  }
  if (command === 'login') {
    print(`Signing in at https://${auth.domain} (check that this is your organisation's sign-in page).`);
    try {
      // --no-browser: print the address instead (a remote shell, or a test that drives the sign-in page)
      const openBrowser = argv.slice(1).includes('--no-browser') ? () => false : deps.openBrowser;
      await auth.login(openBrowser ? { openBrowser } : undefined);
    } catch (err) {
      // a busy port, a timeout, a refused sign-in (P3-070)
      throw new CliExit(`Sign-in failed: ${message(err)}`);
    }
    print('Signed in to Agentic Insights Radar.');
    return;
  }
  await serveWith(new InsightsRadarApi(url, auth, fetchFn), deployment, deps);
}

async function serveWith(api: InsightsRadarApi, deployment: Deployment, deps: CliDeps): Promise<void> {
  let profile: unknown;
  try {
    const config = await api.config();
    profile = isObject(config) ? (config.profile ?? null) : null;
  } catch {
    profile = null; // cosmetic: an unreachable config keeps the default text
  }
  // Radar edits are offered only when asked for: a read-only server cannot be talked into one.
  const writes = ['1', 'true', 'yes'].includes((process.env.AIR_MCP_RADAR_WRITES ?? '').toLowerCase());
  const options = { profile, radarWrites: writes, name: deployment.name, tools: deployment.tools };
  await (deps.serve ?? stdio)(buildServer(api, options));
}

/** The signer for a deployment that offers web identity sign-in, or null when it does not (then the
 * browser sign-in is used). The role it reports is checked against AIR_MCP_ROLE_ARN when that is set. */
export async function signerFor(
  url: string,
  command: string,
  fetchFn: Fetch,
  deployment: Deployment = GENERIC,
): Promise<WebIdentitySigner | null> {
  const config = await new InsightsRadarApi(url, null, fetchFn).config(); // public endpoint
  const wi = webIdentityConfig(config, setting('AIR_MCP_ROLE_ARN', deployment), setting('AIR_MCP_API_URL', deployment));
  return wi ? new WebIdentitySigner(wi, command, { fetch: fetchFn }) : null;
}
