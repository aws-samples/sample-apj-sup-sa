#!/usr/bin/env node
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Package entry point (dist/index.js, the `insights-radar-mcp` command).
 * `cli.ts` owns the commands; this file only turns a `CliExit` into a message on stderr and exit code 1.
 */
import { CliExit, main } from './cli.js';

try {
  await main(process.argv.slice(2));
} catch (err) {
  process.stderr.write(`${err instanceof CliExit ? err.message : err instanceof Error ? err.stack : String(err)}\n`);
  process.exitCode = 1;
}
