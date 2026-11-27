// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * The deployment this server is installed for, read from an optional `deployment.json` next to
 * `package.json` (or the file `AIR_MCP_CONFIG` names):
 *
 *   {
 *     "name": "acme-radar-mcp",                  the command and MCP server name
 *     "tokenDir": "acme-radar",                  the folder under ~/.config (or $XDG_CONFIG_HOME) for tokens
 *     "defaults": {                              used when the environment does not set them
 *       "AIR_API_URL": "https://radar.example.com",
 *       "AIR_MCP_CLIENT_ID": "...",
 *       "AIR_COGNITO_DOMAIN": "acme-111122223333.auth.us-east-1.amazoncognito.com"
 *     },
 *     "tools": ["search", "get_node"]            only these tools are offered
 *   }
 *
 * Every field is optional. Without the file the server is the generic `insights-radar-mcp`: tokens in
 * `insights-radar`, no defaults (the environment must set the addresses) and every tool. The file is read
 * strictly: an unknown key, a value that is not a string, or a tool that does not exist stops the server,
 * so a typo cannot silently fall back to the generic behaviour.
 */

import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

import { isObject, pyStrip } from './py.js';

export const DEFAULT_KEYS = [
  'AIR_API_URL',
  'AIR_MCP_CLIENT_ID',
  'AIR_COGNITO_DOMAIN',
  // Sign in without a browser (webIdentity.ts): the command that prints an id token, the role to pin and
  // the API Gateway URL (stack output McpApiUrl) the signed requests go to.
  'AIR_MCP_WEB_IDENTITY_COMMAND',
  'AIR_MCP_ROLE_ARN',
  'AIR_MCP_API_URL',
] as const;
export type DefaultKey = (typeof DEFAULT_KEYS)[number];

export interface Deployment {
  readonly name: string;
  readonly tokenDir: string;
  readonly defaults: Readonly<Partial<Record<DefaultKey, string>>>;
  /** The tools to offer, or null for all of them. */
  readonly tools: readonly string[] | null;
}

export const GENERIC: Deployment = Object.freeze({
  name: 'insights-radar-mcp',
  tokenDir: 'insights-radar',
  defaults: Object.freeze({}),
  tools: null,
});

/** `deployment.json` in the package folder: next to package.json, one level above src/ and dist/. */
export const PACKAGE_FILE = fileURLToPath(new URL('../deployment.json', import.meta.url));

export class DeploymentError extends Error {
  override name = 'DeploymentError';
}

const FIELDS = ['name', 'tokenDir', 'defaults', 'tools'];
// A command name and a folder name: no path separators, no leading dot, nothing a shell would read.
const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

function text(value: unknown, field: string): string {
  if (typeof value !== 'string') throw new DeploymentError(`${field} must be a string`);
  return value;
}

function simpleName(value: unknown, field: string): string {
  const name = text(value, field);
  if (!NAME.test(name)) {
    throw new DeploymentError(
      `${field} must be 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit`,
    );
  }
  return name;
}

function defaultsOf(value: unknown): Partial<Record<DefaultKey, string>> {
  if (!isObject(value)) throw new DeploymentError('defaults must be an object');
  const out: Partial<Record<DefaultKey, string>> = {};
  for (const [key, v] of Object.entries(value)) {
    if (!(DEFAULT_KEYS as readonly string[]).includes(key)) {
      throw new DeploymentError(`defaults: unknown key ${JSON.stringify(key)} (one of ${DEFAULT_KEYS.join(', ')})`);
    }
    out[key as DefaultKey] = text(v, `defaults.${key}`);
  }
  return Object.freeze(out);
}

function toolsOf(value: unknown, known: readonly string[]): readonly string[] {
  if (!Array.isArray(value) || value.length === 0) throw new DeploymentError('tools must be a non-empty list');
  const names = value.map((v, i) => text(v, `tools[${i}]`));
  const unknown = names.filter((n) => !known.includes(n));
  if (unknown.length) {
    throw new DeploymentError(`tools: no such tool ${unknown.map((n) => JSON.stringify(n)).join(', ')}`);
  }
  const repeated = names.filter((n, i) => names.indexOf(n) !== i);
  if (repeated.length) throw new DeploymentError(`tools: ${JSON.stringify(repeated[0])} is listed twice`);
  return Object.freeze(names);
}

/** A deployment.json document, checked strictly. `known` is every tool the server can offer. */
export function parseDeployment(source: string, known: readonly string[]): Deployment {
  let data: unknown;
  try {
    data = JSON.parse(source);
  } catch (err) {
    throw new DeploymentError(`not valid JSON (${(err as Error).message})`);
  }
  if (!isObject(data)) throw new DeploymentError('must be a JSON object');
  const extra = Object.keys(data).filter((k) => !FIELDS.includes(k));
  if (extra.length) {
    throw new DeploymentError(`unknown key ${JSON.stringify(extra[0])} (one of ${FIELDS.join(', ')})`);
  }
  return Object.freeze({
    name: 'name' in data ? simpleName(data.name, 'name') : GENERIC.name,
    tokenDir: 'tokenDir' in data ? simpleName(data.tokenDir, 'tokenDir') : GENERIC.tokenDir,
    defaults: 'defaults' in data ? defaultsOf(data.defaults) : GENERIC.defaults,
    tools: 'tools' in data ? toolsOf(data.tools, known) : GENERIC.tools,
  });
}

export interface LoadOptions {
  env?: NodeJS.ProcessEnv;
  packageFile?: string;
}

/** The deployment: the file AIR_MCP_CONFIG names (which must exist), else the package's deployment.json
 * if there is one, else the generic server. Errors name the file. */
export function loadDeployment(known: readonly string[], options: LoadOptions = {}): Deployment {
  const env = options.env ?? process.env;
  const chosen = pyStrip(env.AIR_MCP_CONFIG ?? '');
  const file = chosen || (options.packageFile ?? PACKAGE_FILE);
  let source: string;
  try {
    source = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (!chosen && (err as NodeJS.ErrnoException).code === 'ENOENT') return GENERIC;
    throw new DeploymentError(`${file}: cannot be read (${(err as Error).message})`);
  }
  try {
    return parseDeployment(source, known);
  } catch (err) {
    throw new DeploymentError(`${file}: ${(err as Error).message}`);
  }
}
