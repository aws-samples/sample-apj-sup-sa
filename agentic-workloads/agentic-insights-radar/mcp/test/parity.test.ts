// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * The tool set, descriptions, parameters and annotations match the Python server this package replaced.
 * `fixtures/python-tools.json` is the Python server's own tools/list (default and AIR_MCP_RADAR_WRITES on),
 * recorded from mcp/src/insights_radar_mcp (insights-radar-mcp 1.0) before it was removed.
 */
import { readFileSync } from 'node:fs';

import type { Tool } from '@modelcontextprotocol/sdk/types.js';
import { describe, expect, it } from 'vitest';

import { buildServer } from '../src/server.js';
import { api, connect, recorder } from './helpers.js';

interface PyTool {
  name: string;
  description: string;
  inputSchema: { properties: Record<string, Record<string, unknown>>; required?: string[] };
  annotations: Record<string, boolean>;
}

const PYTHON = JSON.parse(readFileSync(new URL('./fixtures/python-tools.json', import.meta.url), 'utf8')) as Record<
  'default' | 'writes',
  PyTool[]
>;

const words = (text: string | undefined): string => (text ?? '').split(/\s+/).join(' ').trim();

function shape(schema: Record<string, unknown>): unknown {
  const { title: _title, ...rest } = schema;
  return rest;
}

// zod 4 states the safe-integer range on every integer; pydantic did not. Same accepted values in practice.
function lean(schema: unknown): unknown {
  if (schema === null || typeof schema !== 'object') return schema;
  const { minimum, maximum, ...rest } = schema as Record<string, unknown>;
  if (minimum === Number.MIN_SAFE_INTEGER && maximum === Number.MAX_SAFE_INTEGER) return rest;
  return schema;
}

async function tools(writes: boolean): Promise<Tool[]> {
  const client = await connect(buildServer(api(recorder().fetch), { radarWrites: writes }));
  return (await client.listTools()).tools;
}

describe('parity with the Python server', () => {
  it.each([
    ['default', false],
    ['writes', true],
  ] as const)('tools/list matches (%s)', async (key, writes) => {
    const ts = await tools(writes);
    const py = PYTHON[key];
    expect(ts.map((t) => t.name)).toEqual(py.map((t) => t.name));
    for (const [i, t] of ts.entries()) {
      const p = py[i];
      expect(words(t.description), t.name).toBe(words(p.description));
      expect(t.annotations, t.name).toEqual(p.annotations);
      const props = t.inputSchema.properties as Record<string, Record<string, unknown>>;
      expect(Object.keys(props), t.name).toEqual(Object.keys(p.inputSchema.properties));
      for (const [name, schema] of Object.entries(p.inputSchema.properties)) {
        expect(lean(props[name]), `${t.name}.${name}`).toEqual(shape(schema));
      }
      expect(t.inputSchema.required ?? [], t.name).toEqual(p.inputSchema.required ?? []);
      expect(t.outputSchema?.properties, t.name).toEqual({ result: { type: 'string' } });
    }
  });
});
