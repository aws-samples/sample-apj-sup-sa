// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Round-2 MCP campaign: every round-2 tool crossed with every kind of API reply a client can meet.
 *
 * A tool must hand the model a result for a 2xx and an error result (never an exception, never the raw
 * reply) for everything else, and an HTML page from the edge must read as edge_rejected.
 */

import { describe, expect, it } from 'vitest';

import { buildServer } from '../src/server.js';
import { api, connect, outcome, recorder, type Reply } from './helpers.js';

const TOOLS: Record<string, Record<string, unknown>> = {
  radar_versions: { slug: 'rivals' },
  radar_data_diff: { slug: 'rivals', from_version: 1, to_version: 2 },
  radar_entry_history: { slug: 'rivals', entry_key: 'k1' },
  get_radar_version: { slug: 'rivals', tag: 'Q3 2026' },
  radar_tags: { slug: 'rivals' },
  ask_graph: { question: 'who cut prices?' },
  research_entity: { node_id: 'organization/acme' },
  whats_new: { since: '2026-10-01T00:00:00Z' },
  my_watches: {},
  find_radar_entries: { query: 'Linak' },
  import_radar_entries: { slug: 'rivals', text: 'title\nX', expected_version: 2, confirm: true },
  tag_radar_version: { slug: 'rivals', version: 2, tag: 'Board', confirm: true },
  restore_radar_version: { slug: 'rivals', version: 1, expected_version: 2, confirm: true },
};

const REPLIES: Record<string, Reply> = {
  ok: { status: 200, json: { items: [{ title: '<b>x</b>' }] } },
  bad_request: { status: 400, json: { code: 'validation_failed', message: 'bad input' } },
  signed_out: { status: 401, json: { message: 'sign in' } },
  forbidden: { status: 403, json: { message: 'editors only' } },
  not_found: { status: 404, json: { message: 'no such radar' } },
  conflict: { status: 409, json: { message: 'version moved on' } },
  throttled: { status: 429, json: { message: 'slow down' } },
  server_error: { status: 500, json: { message: 'boom' } },
  edge_html: { status: 403, html: '<html><body>Request blocked</body></html>' },
  not_json: { status: 200, text: 'plain text' },
};

function server(reply: Reply) {
  const rec = recorder({}, () => reply);
  return { server: buildServer(api(rec.fetch), { radarWrites: true }), rec };
}

const CASES = Object.keys(TOOLS).flatMap((tool) => Object.keys(REPLIES).map((reply) => [tool, reply] as const));

describe('round-2 campaign', () => {
  it.each(CASES)('every tool meets every reply: %s / %s', async (tool, reply) => {
    const { server: s, rec } = server(REPLIES[reply]);
    const out = await outcome(await connect(s), tool, TOOLS[tool]);
    expect(rec.calls).toHaveLength(1); // one request per tool call, no retries on a client error
    if (reply === 'ok') {
      expect(out.isError).toBe(false);
      expect(out.text).toContain('untrusted-tool-output');
      expect(out.text).not.toContain('<b>'); // fenced, markup escaped
    } else {
      expect(out.isError, out.text).toBe(true);
      if (reply === 'edge_html' || reply === 'not_json') {
        expect(out.text.includes('edge_rejected') || out.text.includes('not JSON'), out.text).toBe(true);
      }
      expect(out.text).not.toContain('    at '); // no stack trace
    }
  });

  const BAD_ARGS: Record<string, [string, Record<string, unknown>]> = {
    research_no_slash: ['research_entity', { node_id: 'acme' }],
    research_dots: ['research_entity', { node_id: 'organization/..' }],
    research_empty_kind: ['research_entity', { node_id: '/acme' }],
    history_dot_slug: ['radar_entry_history', { slug: '..', entry_key: 'k1' }],
    tags_dot_slug: ['radar_tags', { slug: '.' }],
    tag_unconfirmed: ['tag_radar_version', { slug: 'rivals', version: 2, tag: 'Board' }],
    restore_unconfirmed: ['restore_radar_version', { slug: 'rivals', version: 1, expected_version: 2 }],
  };

  it.each(Object.keys(BAD_ARGS))('bad arguments never reach the api: %s', async (name) => {
    const [tool, args] = BAD_ARGS[name];
    const { server: s, rec } = server(REPLIES.ok);
    const out = await outcome(await connect(s), tool, args);
    expect(out.isError).toBe(true);
    expect(rec.calls).toEqual([]);
  });
});
