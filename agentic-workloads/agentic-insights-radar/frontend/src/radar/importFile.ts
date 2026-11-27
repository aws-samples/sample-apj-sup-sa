// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Which existing entries an import file names, read the way the API reads it (radars/codecs.py), so
 * the page can tell what a Merge keeps without a second round trip: a Merge replaces every entry
 * whose title or key is in the file and keeps the others.
 */

import { fold } from './layout';

/** The API's header aliases that matter here (radars/codecs.py ALIASES). */
const ALIASES: Record<string, string> = { name: 'title', id: 'key' };

const canon = (header: string): string => {
  const key = header.trim().toLowerCase().replace(/[ -]/g, '_');
  return ALIASES[key] ?? key;
};

/** A cell that starts like a spreadsheet formula, after any quotes in front of it (codecs._needs_guard). */
export function needsGuard(cell: string): boolean {
  const stripped = cell.replace(/^'+/, '');
  return /^[=+\-@\t\r]/.test(stripped);
}

const unguard = (cell: string): string => (cell.startsWith("'") && needsGuard(cell.slice(1)) ? cell.slice(1) : cell);

/** RFC 4180 records; a quote opens a quoted field only at the start of the field, as Python's csv does. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i] as string;
    if (quoted) {
      if (c !== '"') cell += c;
      else if (text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = false;
    } else if (c === '"' && cell === '') quoted = true;
    else if (c === ',') {
      row.push(cell);
      cell = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += c;
  }
  if (cell !== '' || row.length) rows.push([...row, cell]);
  return rows;
}

function rowsOf(format: 'csv' | 'json', text: string): Record<string, unknown>[] | null {
  if (format === 'csv') {
    const [header, ...body] = parseCsv(text.replace(/^\uFEFF/, ''));
    if (!header) return null;
    const names = header.map(canon);
    return body.map((cells) => Object.fromEntries(names.map((n, i) => [n, unguard(cells[i] ?? '')])));
  }
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  const list = Array.isArray(data) ? data : (data as { entries?: unknown } | null)?.entries;
  if (!Array.isArray(list)) return null;
  return list
    .filter((r): r is Record<string, unknown> => typeof r === 'object' && r !== null && !Array.isArray(r))
    .map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [canon(k), v])));
}

export interface FileIdentities {
  titles: Set<string>;
  keys: Set<string>;
  /** Each row's folded title and key, in file order. */
  rows: { title: string; key: string }[];
}

/** The folded titles and the keys of the file's rows, or null when the file cannot be read. */
export function fileIdentities(format: 'csv' | 'json', text: string): FileIdentities | null {
  const rows = rowsOf(format, text);
  if (!rows) return null;
  const text_ = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
  const pairs = rows.map((r) => ({ title: fold(text_(r.title)), key: text_(r.key).trim() })).filter((r) => r.title);
  return {
    titles: new Set(pairs.map((r) => r.title)),
    keys: new Set(rows.map((r) => text_(r.key).trim()).filter(Boolean)),
    rows: pairs,
  };
}

/**
 * The existing entries a Merge keeps, decided as the API decides (radars/service.py _prepare_import):
 * a file row whose title matches an entry first takes that entry's key, and only then does the Merge
 * keep every entry whose title and key no row has. With the file's raw keys, a row titled like entry
 * A but carrying B's key made the page think B was replaced (r3 F3-087).
 */
export function mergeKept<T extends { key: string; title: string }>(entries: T[], ids: FileIdentities): T[] {
  const byTitle = new Map(entries.map((e) => [fold(e.title), e.key]));
  const keys = new Set(ids.rows.map((r) => byTitle.get(r.title) ?? r.key));
  return entries.filter((e) => !ids.titles.has(fold(e.title)) && !keys.has(e.key));
}
