// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.doUnmock('../src/cli.js');
  vi.resetModules();
  vi.restoreAllMocks();
  process.exitCode = undefined;
});

type Cli = typeof import('../src/cli.js');

async function run(outcome: (cli: Cli) => Promise<void>): Promise<string> {
  vi.resetModules();
  vi.doMock('../src/cli.js', async (importOriginal) => {
    const cli = await importOriginal<Cli>();
    return { ...cli, main: vi.fn(() => outcome(cli)) };
  });
  const written: string[] = [];
  vi.spyOn(process.stderr, 'write').mockImplementation((chunk: string | Uint8Array) => {
    written.push(String(chunk));
    return true;
  });
  await import('../src/index.js');
  return written.join('');
}

describe('the entry point', () => {
  it('runs the command and exits quietly on success', async () => {
    expect(await run(async () => undefined)).toBe('');
    expect(process.exitCode).toBeUndefined();
  });

  it('prints a CliExit message alone and exits 1, as sys.exit(message) did', async () => {
    expect(
      await run(async (cli) => Promise.reject(new cli.CliExit('usage: insights-radar-mcp [login|logout|serve]'))),
    ).toBe('usage: insights-radar-mcp [login|logout|serve]\n');
    expect(process.exitCode).toBe(1);
  });

  it('prints anything else with its stack', async () => {
    expect(await run(async () => Promise.reject(new Error('bug')))).toMatch(/^Error: bug\n\s+at /);
    expect(await run(async () => Promise.reject('odd'))).toBe('odd\n');
    expect(process.exitCode).toBe(1);
  });
});
