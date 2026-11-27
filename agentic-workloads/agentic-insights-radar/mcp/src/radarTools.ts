// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Radar history and editing tools (CTR's MCP radar tools, B7/HC-121).
 *
 * Reading a radar's history is always offered. Changing a radar is offered only when the server is started
 * with `AIR_MCP_RADAR_WRITES=true`: a read-only server cannot be talked into a write, and the API still
 * applies the user's role and the radar's own editors to every call. Every write needs `confirm=true`, which
 * a model may pass only after the user has confirmed in the conversation; an import without it is a dry run
 * that returns the validation report.
 */

import { z } from 'zod';

import { bool, int, strings } from './args.js';
import { nodePath, type Query } from './client.js';
import { type Call, type Define, failure, READ, seg, WRITE } from './toolKit.js';

export const CONFIRM = 'ask the user to confirm, then call again with confirm=true';

export function registerRadarTools(define: Define, call: Call, { writes }: { writes: boolean }): void {
  define(
    'radar_versions',
    {
      description: "A radar's versions, newest first: who saved each, when, its note and entry count.",
      annotations: READ,
      inputSchema: { slug: z.string() },
    },
    ({ slug }) => call('radar_versions', 'GET', () => `/api/radars/${seg(slug)}/versions`),
  );

  define(
    'radar_data_diff',
    {
      description:
        'What changed between two versions of a radar: entries added, removed, moved ring, moved sector, ' +
        'marker changed and edited (with the fields).',
      annotations: READ,
      inputSchema: { slug: z.string(), from_version: int(), to_version: int() },
    },
    ({ slug, from_version, to_version }) => {
      const [low, high] = [from_version, to_version].sort((a, b) => a - b);
      return call('radar_data_diff', 'GET', () => `/api/radars/${seg(slug)}/diff`, undefined, {
        query: { from: low, to: high },
      });
    },
  );

  define(
    'radar_entry_history',
    {
      description:
        "One entry's changes over the radar's newest 50 versions, oldest first, with who saved each and " +
        'whether it came from an AI suggestion.',
      annotations: READ,
      inputSchema: { slug: z.string(), entry_key: z.string() },
    },
    ({ slug, entry_key }) =>
      call('radar_entry_history', 'GET', () => `/api/radars/${seg(slug)}/entries/${seg(entry_key)}/history`),
  );

  define(
    'get_radar_version',
    {
      description: 'A radar as it was: by version number or by tag (see radar_tags).',
      annotations: READ,
      inputSchema: { slug: z.string(), version: int(0), tag: z.string().default('') },
    },
    ({ slug, version, tag }) => {
      const query: Query = tag ? { tag } : version ? { version } : {};
      return call('get_radar_version', 'GET', () => `/api/radars/${seg(slug)}`, undefined, { query });
    },
  );

  define(
    'radar_tags',
    {
      description: "A radar's named versions (tags), newest version first.",
      annotations: READ,
      inputSchema: { slug: z.string() },
    },
    ({ slug }) => call('radar_tags', 'GET', () => `/api/radars/${seg(slug)}/tags`),
  );

  define(
    'ask_graph',
    {
      description:
        'The records that answer a question, each with its evidence, plus the cited sources and how well ' +
        'they back an answer (coverage: none, loose, no_sources, single_source or sourced). Answer from ' +
        'these records only, cite their sources, and follow the note: say plainly when nothing matches or ' +
        'nothing is sourced, instead of answering from general knowledge.',
      annotations: READ,
      inputSchema: { question: z.string(), types: strings(null) },
    },
    ({ question, types }) => call('ask_graph', 'POST', '/api/graph/ask', { question, types: types ?? [] }),
  );

  define(
    'research_entity',
    {
      description:
        'A research brief on one graph record (an id such as "product/abc" from search_graph): its ' +
        'summary, newest evidence, the trends, gaps and signals linked to it, the radars that place it, ' +
        'and its sources with a coverage flag. Cite the sources and follow the note.',
      annotations: READ,
      inputSchema: { node_id: z.string() },
    },
    ({ node_id }) => call('research_entity', 'GET', () => `/api/graph/nodes/${nodePath(node_id)}/brief`),
  );

  define(
    'whats_new',
    {
      description:
        'What is new in the graph since ``since`` (ISO 8601) or since the user last read their digest: ' +
        'new signals, trends, gaps and content ideas, and the new records about each record they watch. ' +
        'Reading it here does not mark it read.',
      annotations: READ,
      inputSchema: { since: z.string().default('') },
    },
    ({ since }) => call('whats_new', 'GET', '/api/me/digest', undefined, { query: since ? { since } : null }),
  );

  define(
    'my_watches',
    {
      description: 'The graph records the user watches; their news shows in whats_new.',
      annotations: READ,
      inputSchema: {},
    },
    () => call('my_watches', 'GET', '/api/me/watches'),
  );

  if (!writes) return;

  define(
    'import_radar_entries',
    {
      description:
        'Add or change radar entries from CSV or JSON text (columns as in export_radar). mode "merge" ' +
        'updates entries with the same title and adds the rest; "replace" makes the file the whole radar. ' +
        'Without confirm=true this is a dry run returning the validation report (values that match no ' +
        'sector, ring or marker, repeated rows): show it to the user, and pass confirm=true only after ' +
        "they confirm. expected_version is the radar's current version (from get_radar). If the report " +
        'lists unmatched values or an empty result and the user still wants it, pass allow_unmatched or ' +
        'allow_empty with confirm.',
      annotations: WRITE,
      inputSchema: {
        slug: z.string(),
        text: z.string(),
        expected_version: int(),
        format: z.string().default('csv'),
        mode: z.string().default('merge'),
        allow_unmatched: bool(false),
        allow_empty: bool(false),
        confirm: bool(false),
      },
    },
    ({ slug, text, expected_version, format, mode, allow_unmatched, allow_empty, confirm }) => {
      const body: Record<string, unknown> = { format: format === 'json' ? 'json' : 'csv', text, mode };
      if (allow_unmatched) body.allow_unmatched = true;
      if (allow_empty) body.allow_empty = true;
      const path = (): string => `/api/radars/${seg(slug)}/import`;
      if (!confirm) return call('import_radar_entries', 'POST', path, { ...body, dry_run: true });
      return call('import_radar_entries', 'POST', path, { ...body, expected_version });
    },
  );

  define(
    'tag_radar_version',
    {
      description: 'Name a radar version (for example "Q3 2026"), so it can be opened and shared by its name.',
      annotations: WRITE,
      inputSchema: { slug: z.string(), version: int(), tag: z.string(), confirm: bool(false) },
    },
    async ({ slug, version, tag, confirm }) => {
      if (!confirm) throw failure('tag_radar_version', CONFIRM);
      return call('tag_radar_version', 'PUT', () => `/api/radars/${seg(slug)}/versions/${version}/tag`, { tag });
    },
  );

  define(
    'restore_radar_version',
    {
      description:
        'Copy an older version into a new current version (nothing is lost: the current one stays in the ' +
        "history). expected_version is the radar's current version.",
      annotations: WRITE,
      inputSchema: {
        slug: z.string(),
        version: int(),
        expected_version: int(),
        confirm: bool(false),
      },
    },
    async ({ slug, version, expected_version, confirm }) => {
      if (!confirm) throw failure('restore_radar_version', CONFIRM);
      return call('restore_radar_version', 'POST', () => `/api/radars/${seg(slug)}/rollback`, {
        version,
        expected_version,
      });
    },
  );
}
