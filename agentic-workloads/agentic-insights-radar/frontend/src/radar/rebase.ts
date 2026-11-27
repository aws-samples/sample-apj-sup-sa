// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Three-way merge of unsaved entry edits onto a newer version of the radar.
 *
 * `base` is the version the user started from, `mine` their draft rows and `theirs` the newer saved
 * entries. Each of the user's own changes (an added, edited or removed entry, compared with `base`)
 * is applied to `theirs`; everything else is taken from `theirs`, so the other editor's work stays.
 * Where both sides changed the same entry the result keeps a sensible copy and reports a conflict,
 * so the user can review it before saving.
 */

import type { RadarEntry } from '@/api/types';

import { fold } from './layout';

/** Every field of an entry except its key, in the order the editor shows them. */
export const ENTRY_FIELDS = ['title', 'sector', 'ring', 'marker', 'description', 'node_id', 'link', 'sub_sector'] as const;
export type EntryField = (typeof ENTRY_FIELDS)[number];

const value = (e: RadarEntry, f: EntryField): string => String(e[f] ?? '');

/** The fields whose values differ (a missing value and an empty one are the same). */
export function changedFields(a: RadarEntry, b: RadarEntry): EntryField[] {
  return ENTRY_FIELDS.filter((f) => value(a, f) !== value(b, f));
}

const same = (a: RadarEntry, b: RadarEntry) => changedFields(a, b).length === 0;

export type ConflictKind =
  /** Both changed the same field to different values: the user's value is kept. */
  | 'both_edited'
  /** The user removed an entry that was changed in the newer version: the newer copy is kept. */
  | 'removed_but_changed'
  /** The user edited an entry that the newer version removed: the user's copy is kept. */
  | 'edited_but_removed'
  /** The user added an entry whose title is already on the merged list (the editor marks the duplicate). */
  | 'same_title';

export interface RebaseConflict {
  key: string;
  title: string;
  kind: ConflictKind;
  /** For `both_edited`: each field both changed, with the newer version's value (the user's is kept). */
  fields: { field: EntryField; theirs: string }[];
}

export interface RebaseResult {
  rows: RadarEntry[];
  /** How many of the user's changes (adds, edits, removals) landed on the newer version. */
  applied: number;
  /** The user's removals that were not applied, because the newer version changed the entry. */
  held: number;
  conflicts: RebaseConflict[];
}

export function rebaseEntries(base: RadarEntry[], mine: RadarEntry[], theirs: RadarEntry[]): RebaseResult {
  const baseBy = new Map(base.map((e) => [e.key, e]));
  const mineBy = new Map(mine.map((e) => [e.key, e]));
  const theirKeys = new Set(theirs.map((e) => e.key));
  const conflicts: RebaseConflict[] = [];
  const rows: RadarEntry[] = [];
  for (const t of theirs) {
    const b = baseBy.get(t.key);
    const m = mineBy.get(t.key);
    if (!b) {
      rows.push(t); // added in the newer version
    } else if (!m) {
      // removed by the user: it goes, unless the newer version changed it
      if (!same(t, b)) {
        rows.push(t);
        conflicts.push({ key: t.key, title: t.title, kind: 'removed_but_changed', fields: [] });
      }
    } else if (same(m, b)) {
      rows.push(t); // untouched by the user
    } else {
      const merged: RadarEntry = { ...t };
      const clashes: RebaseConflict['fields'] = [];
      for (const f of ENTRY_FIELDS) {
        const [mv, bv, tv] = [value(m, f), value(b, f), value(t, f)];
        if (mv === bv) continue; // only they changed it (or nobody did): theirs
        if (tv !== bv && tv !== mv) clashes.push({ field: f, theirs: tv });
        (merged as unknown as Record<string, unknown>)[f] = m[f];
      }
      // A value the user cleared is left out, as the editor and the API leave it out.
      rows.push(Object.fromEntries(Object.entries(merged).filter(([, x]) => x !== undefined)) as unknown as RadarEntry);
      if (clashes.length) conflicts.push({ key: t.key, title: merged.title, kind: 'both_edited', fields: clashes });
    }
  }
  const titles = new Set(rows.map((r) => fold(r.title)));
  // The user's new rows stay on top, where Add entry put them: appended, they jumped to the last page (r3 F3-037).
  const added: RadarEntry[] = [];
  for (const m of mine) {
    if (theirKeys.has(m.key)) continue;
    const b = baseBy.get(m.key);
    if (b && same(m, b)) continue; // removed in the newer version and not edited here: stays removed
    if (b) {
      rows.push(m);
      conflicts.push({ key: m.key, title: m.title, kind: 'edited_but_removed', fields: [] });
    } else {
      added.push(m);
      if (titles.has(fold(m.title))) conflicts.push({ key: m.key, title: m.title, kind: 'same_title', fields: [] });
    }
  }
  const own = ownChanges(base, mine);
  // A removal the newer version overruled did not land (r3 F3-033: "Your 1 change was re-applied" for none).
  const held = conflicts.filter((c) => c.kind === 'removed_but_changed').length;
  return { rows: [...added, ...rows], applied: own.changed.length + own.removed.length - held, held, conflicts };
}

/** The user's own changes against the version they started from: rows added or edited, and rows removed. */
export function ownChanges(base: RadarEntry[], mine: RadarEntry[]): { changed: RadarEntry[]; removed: RadarEntry[] } {
  const baseBy = new Map(base.map((e) => [e.key, e]));
  const kept = new Set(mine.map((e) => e.key));
  return {
    changed: mine.filter((m) => {
      const b = baseBy.get(m.key);
      return !b || !same(m, b);
    }),
    removed: base.filter((b) => !kept.has(b.key)),
  };
}
