// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** What the READMEs must keep telling the operator (ported from the Python server's documentation tests). */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const read = (name: string): string => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const ROOT_README = read('../README.md');
const README = read('README.md');

describe('the READMEs', () => {
  it('p2011 tell the operator to pin the sign-in domain', () => {
    for (const [name, text] of [
      ['README.md', ROOT_README],
      ['mcp/README.md', README],
    ]) {
      expect(text, name).toContain('AIR_COGNITO_DOMAIN');
      expect(text, name).toContain('CognitoDomain');
    }
    // The old claim that the shape check stops a phishing page is gone.
    expect(README).not.toContain('cannot send you');
  });

  it('p3_069 set the pin in the blocks a user copies', () => {
    const blocks = [...README.matchAll(/```(?:bash|json)\n([\s\S]*?)```/g)].map((m) => m[1]);
    const install = blocks.find((b) => b.includes('insights-radar-mcp login')) ?? '';
    const config = blocks.find((b) => b.includes('"mcpServers"')) ?? '';
    expect(install).toContain('export AIR_COGNITO_DOMAIN=');
    expect(config).toContain('"AIR_COGNITO_DOMAIN"');
    expect(() => JSON.parse(config)).not.toThrow(); // the client configuration is valid JSON as printed
  });

  it('sr09 call confirm a speed bump', () => {
    expect(README).toContain('speed bump, not a human gate');
    expect(README).toContain('client\napproval is the real safeguard');
  });

  it('show a deployment.json the server accepts', async () => {
    const { parseDeployment } = await import('../src/deployment.js');
    const { toolNames } = await import('../src/server.js');
    const example = [...README.matchAll(/```json\n([\s\S]*?)```/g)]
      .map((m) => m[1])
      .find((b) => b.includes('"tokenDir"'));
    expect(parseDeployment(example ?? '', toolNames()).name).toBe('acme-radar-mcp');
  });
});
