// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_KEYS,
  DeploymentError,
  GENERIC,
  loadDeployment,
  PACKAGE_FILE,
  parseDeployment,
} from '../src/deployment.js';
import { toolNames } from '../src/server.js';

const KNOWN = toolNames();
let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'air-deployment-'));
  vi.stubEnv('AIR_MCP_CONFIG', undefined);
});

afterEach(() => {
  vi.unstubAllEnvs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

function refusal(source: unknown): string {
  try {
    parseDeployment(typeof source === 'string' ? source : JSON.stringify(source), KNOWN);
  } catch (err) {
    expect(err).toBeInstanceOf(DeploymentError);
    return (err as Error).message;
  }
  throw new Error('expected a refusal');
}

describe('parseDeployment', () => {
  it('reads every field', () => {
    const d = parseDeployment(
      JSON.stringify({
        name: 'acme-radar-mcp',
        tokenDir: 'acme-radar',
        defaults: {
          AIR_API_URL: 'https://radar.example.com',
          AIR_MCP_CLIENT_ID: 'client',
          AIR_COGNITO_DOMAIN: 'acme-111122223333.auth.us-east-1.amazoncognito.com',
        },
        tools: ['search', 'get_node', 'import_radar_entries'],
      }),
      KNOWN,
    );
    expect(d).toEqual({
      name: 'acme-radar-mcp',
      tokenDir: 'acme-radar',
      defaults: {
        AIR_API_URL: 'https://radar.example.com',
        AIR_MCP_CLIENT_ID: 'client',
        AIR_COGNITO_DOMAIN: 'acme-111122223333.auth.us-east-1.amazoncognito.com',
      },
      tools: ['search', 'get_node', 'import_radar_entries'],
    });
    expect(Object.isFrozen(d) && Object.isFrozen(d.defaults) && Object.isFrozen(d.tools)).toBe(true);
  });

  it('leaves out what is not set: the generic server', () => {
    expect(parseDeployment('{}', KNOWN)).toEqual(GENERIC);
    expect(parseDeployment('{"defaults": {}}', KNOWN)).toEqual(GENERIC);
    expect(parseDeployment('{"name": "x.y_z-1"}', KNOWN)).toEqual({ ...GENERIC, name: 'x.y_z-1' });
    expect(GENERIC).toEqual({ name: 'insights-radar-mcp', tokenDir: 'insights-radar', defaults: {}, tools: null });
    expect(DEFAULT_KEYS).toEqual([
      'AIR_API_URL',
      'AIR_MCP_CLIENT_ID',
      'AIR_COGNITO_DOMAIN',
      'AIR_MCP_WEB_IDENTITY_COMMAND',
      'AIR_MCP_ROLE_ARN',
      'AIR_MCP_API_URL',
    ]);
  });

  it('refuses what it does not understand', () => {
    expect(refusal('{"name": ')).toMatch(/^not valid JSON \(/);
    expect(refusal([])).toBe('must be a JSON object');
    expect(refusal('null')).toBe('must be a JSON object');
    expect(refusal({ name: 'a', server: 'b' })).toBe('unknown key "server" (one of name, tokenDir, defaults, tools)');
    expect(refusal({ defaults: { AIR_API_URL: 'https://x', AWS_REGION: 'us-east-1' } })).toBe(
      'defaults: unknown key "AWS_REGION" (one of AIR_API_URL, AIR_MCP_CLIENT_ID, AIR_COGNITO_DOMAIN, AIR_MCP_WEB_IDENTITY_COMMAND, AIR_MCP_ROLE_ARN, AIR_MCP_API_URL)',
    );
    expect(refusal({ defaults: { AIR_MCP_RADAR_WRITES: 'true' } })).toMatch(
      /^defaults: unknown key "AIR_MCP_RADAR_WRITES"/,
    );
    expect(refusal({ defaults: ['https://x'] })).toBe('defaults must be an object');
    expect(refusal({ tools: ['search', 'delete_everything', 'x'] })).toBe(
      'tools: no such tool "delete_everything", "x"',
    );
    expect(refusal({ tools: ['search', 'get_node', 'search'] })).toBe('tools: "search" is listed twice');
    expect(refusal({ tools: [] })).toBe('tools must be a non-empty list');
    expect(refusal({ tools: 'search' })).toBe('tools must be a non-empty list');
  });

  it('takes strings only', () => {
    expect(refusal({ name: 7 })).toBe('name must be a string');
    expect(refusal({ tokenDir: null })).toBe('tokenDir must be a string');
    expect(refusal({ defaults: { AIR_API_URL: 1 } })).toBe('defaults.AIR_API_URL must be a string');
    expect(refusal({ defaults: { AIR_MCP_CLIENT_ID: true } })).toBe('defaults.AIR_MCP_CLIENT_ID must be a string');
    expect(refusal({ tools: ['search', 3] })).toBe('tools[1] must be a string');
  });

  it.each(['', '../x', 'a/b', '.hidden', 'has space', 'x'.repeat(65), 'semi;colon', '$(id)'])(
    'refuses a name or folder a shell or a path would misread: %j',
    (bad) => {
      const rule = "must be 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit";
      expect(refusal({ name: bad })).toBe(`name ${rule}`);
      expect(refusal({ tokenDir: bad })).toBe(`tokenDir ${rule}`);
    },
  );
});

describe('loadDeployment', () => {
  it('is the generic server when the package has no deployment.json', () => {
    expect(loadDeployment(KNOWN, { packageFile: path.join(tmp, 'deployment.json') })).toBe(GENERIC);
    expect(loadDeployment(KNOWN, { env: { AIR_MCP_CONFIG: '  ' }, packageFile: path.join(tmp, 'none.json') })).toBe(
      GENERIC,
    );
  });

  it("reads the package's deployment.json, next to package.json", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    expect(PACKAGE_FILE).toBe(path.join(here, '..', 'deployment.json'));
    expect(fs.existsSync(path.join(path.dirname(PACKAGE_FILE), 'package.json'))).toBe(true);
    const file = path.join(tmp, 'deployment.json');
    fs.writeFileSync(file, JSON.stringify({ name: 'acme-mcp' }));
    expect(loadDeployment(KNOWN, { packageFile: file }).name).toBe('acme-mcp');
    // The process environment and the real package file are the defaults (an installation may have added one).
    expect(typeof loadDeployment(KNOWN).name).toBe('string');
  });

  it('AIR_MCP_CONFIG names the file, which must exist', () => {
    const file = path.join(tmp, 'other.json');
    fs.writeFileSync(file, JSON.stringify({ tokenDir: 'other' }));
    const packageFile = path.join(tmp, 'deployment.json');
    fs.writeFileSync(packageFile, JSON.stringify({ tokenDir: 'package' }));
    expect(loadDeployment(KNOWN, { env: { AIR_MCP_CONFIG: ` ${file} ` }, packageFile }).tokenDir).toBe('other');
    vi.stubEnv('AIR_MCP_CONFIG', file);
    expect(loadDeployment(KNOWN, { packageFile }).tokenDir).toBe('other');
    const missing = path.join(tmp, 'missing.json');
    expect(() => loadDeployment(KNOWN, { env: { AIR_MCP_CONFIG: missing }, packageFile })).toThrow(
      new RegExp(`^${missing.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}: cannot be read \\(ENOENT`),
    );
  });

  it('names the file in every refusal', () => {
    const file = path.join(tmp, 'deployment.json');
    fs.writeFileSync(file, '{"tools": ["nope"]}');
    expect(() => loadDeployment(KNOWN, { packageFile: file })).toThrow(`${file}: tools: no such tool "nope"`);
    fs.rmSync(file);
    fs.mkdirSync(file); // present but not readable as a file: not the same as absent
    expect(() => loadDeployment(KNOWN, { packageFile: file })).toThrow(`${file}: cannot be read (EISDIR`);
  });
});
