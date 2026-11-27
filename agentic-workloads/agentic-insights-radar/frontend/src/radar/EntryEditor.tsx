// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Pagination from '@cloudscape-design/components/pagination';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';

import type { RadarEntry, RadarSchema } from '@/api/types';
import { plural } from '@/lib/format';

import { fold } from './layout';

export function newKey(): string {
  return crypto.randomUUID();
}

type Field = 'title' | 'sector' | 'ring' | 'marker' | 'link' | 'description' | 'sub_sector';

/** Backend limits (radars/model.py), checked here so Save does not fail with a server message. */
export const TITLE_MAX = 120;
export const ENTRIES_MAX = 300;
export const DESCRIPTION_MAX = 4000;
export const SUB_SECTOR_MAX = 120;

/** Characters as the API counts them (Python's len): code points, not UTF-16 units. */
const chars = (text: string): number => [...text].length;

/** A title as the API stores it (NameNormalizer.display): NFC, no invisible characters, single-spaced. */
export function displayTitle(title: string): string {
  return title.normalize('NFC').replace(/\p{Cf}/gu, '').replace(/\s+/g, ' ').trim();
}

/**
 * A description's length once the API has stored it: tags are dropped, and & < > are stored as
 * entities, so each `&` counts five (r3 F3-089: a long text with ampersands failed only at Save).
 */
export function storedLength(description: string): number {
  // Tags are dropped until none is left. One pass already gives the same text; the loop states the
  // intent so a scanner (and a reader) need not prove that.
  let text = description;
  for (let prev = ''; prev !== text; ) {
    prev = text;
    text = text.replace(/<[^>]*>/g, '');
  }
  const entities = (text.match(/&/g)?.length ?? 0) * 4 + (text.match(/[<>]/g)?.length ?? 0) * 3;
  return chars(text) + entities;
}

export interface Problem {
  key: string;
  row: number;
  field: Field;
  /** The row's title, so the message can jump to it (the row number is useless under a filter). */
  title: string;
  message: string;
}

/** Every problem in the rows, each tied to its row and cell (so the editor can mark the cell). */
export function entryProblems(schema: RadarSchema, entries: RadarEntry[]): Problem[] {
  const problems: Problem[] = [];
  const names = (xs: { name: string }[]) => new Set(xs.map((x) => fold(x.name)));
  const sectors = names(schema.sectors);
  const rings = names(schema.rings);
  const markers = names(schema.markers);
  const seen = new Set<string>();
  if (entries.length > ENTRIES_MAX) {
    problems.push({
      key: '',
      row: 0,
      field: 'title',
      title: '',
      message: `A radar holds at most ${ENTRIES_MAX} entries; this draft has ${entries.length}. Remove ${entries.length - ENTRIES_MAX} before saving.`,
    });
  }
  entries.forEach((e, i) => {
    const row = i + 1;
    // Named by the entry: "Row 37" meant nothing with a filter on, and Add entry renumbered them all.
    const name = e.title.trim() || `Row ${row} (no title yet)`;
    const add = (field: Field, message: string) =>
      problems.push({ key: e.key, row, field, title: e.title.trim(), message: `${name}: ${message}` });
    if (!e.title.trim()) add('title', 'a title is required');
    const titleLength = chars(displayTitle(e.title));
    if (titleLength > TITLE_MAX) add('title', `a title must be ${TITLE_MAX} characters or fewer (this one is ${titleLength})`);
    const t = fold(e.title);
    if (t && seen.has(t)) add('title', `"${e.title}" appears twice`);
    seen.add(t);
    if (!sectors.has(fold(e.sector))) add('sector', `choose a ${schema.sector_label.toLowerCase()}`);
    if (!rings.has(fold(e.ring))) add('ring', `choose a ${schema.ring_label.toLowerCase()}`);
    if (e.marker && !markers.has(fold(e.marker))) add('marker', `unknown ${schema.marker_label.toLowerCase()}`);
    // The API stores only public https links (radars/model.py), so the editor holds every row to the same rule.
    if (e.link && !/^https:\/\/[^\s/]+\.[^\s]+$/.test(e.link)) add('link', 'the link must be a full https:// web address');
    const stored = storedLength(e.description);
    if (stored > DESCRIPTION_MAX) add('description', `a description must be ${DESCRIPTION_MAX} characters or fewer as stored, where each & counts 5 (this one is ${stored})`);
    const sub = chars((e.sub_sector ?? '').normalize('NFC').replace(/\s+/g, ' ').trim());
    if (sub > SUB_SECTOR_MAX) add('sub_sector', `a sub-${schema.sector_label.toLowerCase()} must be ${SUB_SECTOR_MAX} characters or fewer (this one is ${sub})`);
  });
  return problems;
}

export function validateEntries(schema: RadarSchema, entries: RadarEntry[]): string[] {
  return entryProblems(schema, entries).map((p) => p.message);
}


/** How many rows differ from the saved entries (edited, added or removed), compared row by row. */
export function changedRows(rows: RadarEntry[], saved: RadarEntry[]): number {
  const before = new Map(saved.map((e) => [e.key, JSON.stringify(e)]));
  const edited = rows.filter((r) => before.get(r.key) !== JSON.stringify(r)).length;
  const kept = new Set(rows.map((r) => r.key));
  return edited + saved.filter((e) => !kept.has(e.key)).length;
}

interface Props {
  schema: RadarSchema;
  /** The saved entries of the version being edited. */
  entries: RadarEntry[];
  /** The draft rows. The page owns them so they survive switching tabs (only the active tab is mounted). */
  rows: RadarEntry[];
  onRowsChange: (rows: RadarEntry[]) => void;
  saving: boolean;
  onSave: (entries: RadarEntry[]) => void;
  /** Problems the API found that the rows themselves cannot show (a title it folds onto another). */
  extraProblems?: Problem[];
  /** Entries changed on both sides by a rebase: marked, and offered as a filter (r3 F3-009). */
  conflictKeys?: string[];
  /** Brings one entry on screen (a conflict line in the page's alert was clicked); `n` counts the clicks. */
  jump?: { key: string; title: string; n: number } | null;
  /** The version the draft was re-applied on, for the Discard dialog (r3 F3-110). */
  rebasedOn?: number | null;
}

/** A cell's accessible name: "Position of Acme". Without the entry a screen reader reads "Title, edit
 *  text" twenty-five times with nothing to tell the rows apart. */
export const rowName = (field: string, row: RadarEntry): string => `${field} of ${row.title.trim() || 'the new entry'}`;

const PAGE = 25;
const SHOWN_PROBLEMS = 10;

/** Inline table editor for a radar's entries. Saving creates a new radar version. */
export default function EntryEditor({ schema, entries, rows, onRowsChange, saving, onSave, extraProblems = [], conflictKeys = [], jump = null, rebasedOn = null }: Props) {
  const [selected, setSelected] = React.useState<RadarEntry[]>([]);
  const [filter, setFilter] = React.useState('');
  const [page, setPage] = React.useState(1);
  const [discarding, setDiscarding] = React.useState(false);
  const [onlyConflicts, setOnlyConflicts] = React.useState(false);
  /** The cell to focus; `n` changes on every request, so a second click on the same problem works (r3 F3-080). */
  const [focus, setFocus] = React.useState<{ key: string; field: Field; n: number } | null>(null);
  const target = React.useRef<{ focus(): void } | null>(null);
  const grab = (key: string, field: Field) => (focus?.key === key && focus.field === field ? (el: { focus(): void } | null) => void (target.current = el) : undefined);
  const problems = [...entryProblems(schema, rows), ...extraProblems];
  const bad = new Map(problems.map((p) => [`${p.key}:${p.field}`, p.message]));
  const invalid = (r: RadarEntry, field: Field) => bad.has(`${r.key}:${field}`);
  const changes = changedRows(rows, entries);
  const dirty = changes > 0;
  const needle = fold(filter);
  const conflicted = new Set(conflictKeys);
  const filtering = onlyConflicts && conflicted.size > 0;
  const shown = rows.filter((r) => (!needle || fold(r.title).includes(needle)) && (!filtering || conflicted.has(r.key)));
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const current = Math.min(page, pages);
  React.useEffect(() => {
    if (focus) target.current?.focus();
  }, [focus]);
  /** Shows only `title`'s row, on page 1, with nothing selected: Remove must never take rows out of sight (r3 F3-005). */
  const showRow = (title: string) => {
    setFilter(title);
    setOnlyConflicts(false);
    setPage(1);
    setSelected([]);
  };
  const jumpN = jump?.n;
  const [seenJump, setSeenJump] = React.useState(jumpN);
  if (jump && jumpN !== seenJump) {
    setSeenJump(jumpN);
    showRow(jump.title);
    setFocus({ key: jump.key, field: 'title', n: jump.n });
  }
  const options = (xs: { name: string }[]) => xs.map((x) => ({ value: x.name, label: x.name }));
  const set = (key: string, patch: Partial<RadarEntry>) => onRowsChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const add = () => {
    // A new row goes on top, in view, with its title focused. Sector and ring start empty: the
    // user chooses them (validation asks), so nothing is filed somewhere they did not pick.
    const key = newKey();
    onRowsChange([{ key, title: '', sector: '', ring: '', description: '' }, ...rows]);
    showRow('');
    setFocus({ key, field: 'title', n: (focus?.n ?? 0) + 1 });
  };
  return (
    <SpaceBetween size="m">
      {problems.length > 0 && (
        <Alert type="error" header={`${plural(problems.length, 'problem')} to fix before saving`}>
          <ul>
            {problems.slice(0, SHOWN_PROBLEMS).map((p) => (
              <li key={`${p.key}:${p.field}:${p.message}`}>
                {p.title ? (
                  // Filtering to the title is what brings the row on screen whatever page it was on.
                  <button
                    type="button"
                    className="air-linkish"
                    onClick={() => {
                      showRow(p.title);
                      setFocus({ key: p.key, field: p.field, n: (focus?.n ?? 0) + 1 });
                    }}
                  >
                    {p.message}
                  </button>
                ) : (
                  p.message
                )}
              </li>
            ))}
          </ul>
          {problems.length > SHOWN_PROBLEMS && <Box>{`and ${problems.length - SHOWN_PROBLEMS} more. The cells with a problem are marked.`}</Box>}
        </Alert>
      )}
      {conflicted.size > 0 && (
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <Box color="text-status-warning">{`${plural(conflicted.size, 'entry', 'entries')} changed on both sides ${conflicted.size === 1 ? 'is' : 'are'} marked below.`}</Box>
          <Button
            onClick={() => {
              setOnlyConflicts((o) => !o);
              setFilter('');
              setPage(1);
              setSelected([]);
            }}
          >
            {filtering ? 'Show every entry' : 'Show only those'}
          </Button>
        </SpaceBetween>
      )}
      <Table wrapLines
        items={shown.slice((current - 1) * PAGE, current * PAGE)}
        trackBy="key"
        selectionType="multi"
        ariaLabels={{
          selectionGroupLabel: 'Entries',
          allItemsSelectionLabel: () => 'Select every entry on this page',
          itemSelectionLabel: (_data, row) => `Select ${row.title || 'the new entry'}`,
        }}
        isItemDisabled={() => saving}
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        variant="container"
        stickyColumns={{ first: 1 }}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find entries by title"
            filteringAriaLabel="Find entries by title"
            countText={needle ? plural(shown.length, 'match', 'matches') : undefined}
            onChange={({ detail }) => {
              setFilter(detail.filteringText);
              setPage(1);
              setSelected([]); // Remove must never take rows the user can no longer see
            }}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={current}
            pagesCount={pages}
            onChange={({ detail }) => {
              setPage(detail.currentPageIndex);
              setSelected([]);
            }}
          />
        }
        header={
          <Header
            counter={`(${rows.length})`}
            description={
              // While a save runs the rows are read-only: a save reloads the version it created, which
              // would silently drop anything typed after Save version was pressed.
              saving
                ? 'Saving a new version: the rows are read-only until it is stored.'
                : dirty
                  ? `${plural(changes, 'unsaved change')}: Save version keeps them, Discard changes throws them away.`
                  : 'Edit any cell or add an entry, then Save version. Each save is a new version you can compare or restore under History. To change the categories or rings themselves, use Settings. Long descriptions and links wrap inside their cells.'
            }
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  disabled={selected.length === 0 || saving}
                  onClick={() => {
                    const drop = new Set(selected.map((s) => s.key));
                    onRowsChange(rows.filter((r) => !drop.has(r.key)));
                    setSelected([]);
                  }}
                >
                  {selected.length > 0 ? `Remove ${selected.length}` : 'Remove'}
                </Button>
                <Button iconName="add-plus" disabled={saving} onClick={add}>
                  Add entry
                </Button>
                <Button disabled={!dirty || saving} onClick={() => setDiscarding(true)}>
                  Discard changes
                </Button>
                <Button
                  variant="primary"
                  loading={saving}
                  disabled={!dirty || problems.length > 0}
                  disabledReason={problems.length > 0 ? 'Fix the problems listed above first.' : 'Nothing to save yet: edit a cell or add an entry.'}
                  onClick={() => onSave(rows)}
                >
                  Save version
                </Button>
              </SpaceBetween>
            }
          >
            Entries
          </Header>
        }
        columnDefinitions={[
          {
            id: 'title',
            header: 'Title',
            cell: (r) => (
              <SpaceBetween size="xxxs">
                <Input
                  ref={grab(r.key, 'title')}
                  value={r.title}
                  ariaLabel={rowName('Title', r)}
                  readOnly={saving}
                  invalid={invalid(r, 'title')}
                  onChange={({ detail }) => set(r.key, { title: detail.value })}
                />
                {conflicted.has(r.key) && <Box fontSize="body-s" color="text-status-warning">Changed on both sides: check it</Box>}
              </SpaceBetween>
            ),
            minWidth: 200,
          },
          {
            id: 'sector',
            header: schema.sector_label,
            cell: (r) => (
              <Select
                ref={grab(r.key, 'sector')}
                expandToViewport
                ariaLabel={rowName(schema.sector_label, r)}
                disabled={saving}
                invalid={invalid(r, 'sector')}
                placeholder="Choose"
                selectedOption={r.sector ? { value: r.sector, label: r.sector } : null}
                onChange={({ detail }) => set(r.key, { sector: detail.selectedOption.value as string })}
                options={options(schema.sectors)}
              />
            ),
          },
          {
            id: 'sub_sector',
            header: `Sub-${schema.sector_label.toLowerCase()}`,
            cell: (r) => (
              <Input
                ref={grab(r.key, 'sub_sector')}
                value={r.sub_sector ?? ''}
                ariaLabel={rowName(`Sub-${schema.sector_label.toLowerCase()}`, r)}
                readOnly={saving}
                invalid={invalid(r, 'sub_sector')}
                placeholder="Optional"
                onChange={({ detail }) => set(r.key, { sub_sector: detail.value || undefined })}
              />
            ),
            minWidth: 140,
          },
          {
            id: 'ring',
            header: schema.ring_label,
            cell: (r) => (
              <Select
                ref={grab(r.key, 'ring')}
                expandToViewport
                ariaLabel={rowName(schema.ring_label, r)}
                disabled={saving}
                invalid={invalid(r, 'ring')}
                placeholder="Choose"
                selectedOption={r.ring ? { value: r.ring, label: r.ring } : null}
                onChange={({ detail }) => set(r.key, { ring: detail.selectedOption.value as string })}
                options={options(schema.rings)}
              />
            ),
          },
          ...(schema.markers.length
            ? [
                {
                  id: 'marker',
                  header: schema.marker_label,
                  cell: (r: RadarEntry) => (
                    <Select
                      ref={grab(r.key, 'marker')}
                      expandToViewport
                      ariaLabel={rowName(schema.marker_label, r)}
                      disabled={saving}
                      invalid={invalid(r, 'marker')}
                      selectedOption={r.marker ? { value: r.marker, label: r.marker } : { value: '', label: '-' }}
                      onChange={({ detail }) => set(r.key, { marker: detail.selectedOption.value || undefined })}
                      options={[{ value: '', label: '-' }, ...options(schema.markers)]}
                    />
                  ),
                },
              ]
            : []),
          {
            id: 'description',
            header: 'Description',
            cell: (r) => (
              <Textarea
                ref={grab(r.key, 'description')}
                rows={2}
                value={r.description}
                ariaLabel={rowName('Description', r)}
                readOnly={saving}
                invalid={invalid(r, 'description')}
                onChange={({ detail }) => set(r.key, { description: detail.value })}
              />
            ),
            minWidth: 260,
          },
          {
            id: 'link',
            header: 'Link',
            cell: (r) => (
              <Input
                ref={grab(r.key, 'link')}
                value={r.link ?? ''}
                ariaLabel={rowName('Link', r)}
                readOnly={saving}
                placeholder="https://..."
                invalid={invalid(r, 'link')}
                onChange={({ detail }) => set(r.key, { link: detail.value || undefined })}
              />
            ),
            minWidth: 200,
          },
        ]}
        empty={<Box textAlign="center" padding="m">{needle ? 'No entries match.' : 'No entries. Add one, or import a CSV or JSON file.'}</Box>}
      />
      {discarding && (
        <Modal
          visible
          onDismiss={() => setDiscarding(false)}
          header="Discard your changes?"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setDiscarding(false)}>Keep editing</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    setDiscarding(false);
                    onRowsChange(entries);
                  }}
                >
                  Discard
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {rebasedOn === null
            ? `Your unsaved edits (${plural(changes, 'change')}) are thrown away, and the entries go back to the saved version.`
            : `Your unsaved edits (${plural(changes, 'change')}) are thrown away, and the entries go back to version ${rebasedOn}: the newer version, with the other editor's changes. The version you started from is under History.`}
        </Modal>
      )}
    </SpaceBetween>
  );
}
