// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import FileUpload from '@cloudscape-design/components/file-upload';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Pagination from '@cloudscape-design/components/pagination';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import Toggle from '@cloudscape-design/components/toggle';
import TokenGroup from '@cloudscape-design/components/token-group';
import { appName } from '@/lib/profile';
import React from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, needsShrinkConfirm, shrinkOf } from '@/api/client';
import type { Listing, Shrink } from '@/api/client';
import type { Dimension, ImportReport, RadarDiffChanges, RadarEntry, RadarMode, RadarSchema, RadarVersion, RadarView, Suggestion } from '@/api/types';
import { can, useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { TableLoadError } from '@/components/ErrorState';
import NodeLink from '@/components/NodeLink';
import { describeError, useNotify } from '@/components/Notifications';
import { WholeNumber } from '@/components/wholeNumber';
import type { AsyncState } from '@/hooks/useAsync';
import { useAsync } from '@/hooks/useAsync';
import { actorLabel, ago, clip, fileSize, plural, RADAR_MODES, roleLabel, typeLabel, when } from '@/lib/format';
import { storedText } from '@/lib/html';
import { headingError, listError, NAME_MAX, PURPOSE_MAX } from '@/pages/RadarsPage';

import { ENTRIES_MAX } from './EntryEditor';
import { download, pngScale, slugFile, svgMarkup, svgSize, svgToPng } from './export';
import { ExportSheet } from './ExportSheet';
import { fileIdentities, mergeKept } from './importFile';
import { fold } from './layout';

/**
 * Version-creating actions go through this, so unsaved entry edits are never replaced silently.
 * `run` resolves true only when a new version was really created: a failed action (a conflict, a
 * rejected import, a dropped connection) must not cost the user their unsaved rows.
 */
type BeforeNewVersion = (label: string, run: () => Promise<boolean>) => void;

// ---- import / export ----------------------------------------------------------------------

/** The import limits the API enforces (radars/service.py); checked here before a file is read. */
export const IMPORT_MAX_BYTES = 5 * 1024 * 1024;

interface ImportExportProps {
  view: RadarView;
  onChanged: () => void;
  beforeNewVersion: BeforeNewVersion;
  onViewRadar: () => void;
  /** Opens Edit entries, offered for entries already on the radar that a Merge cannot keep. */
  onEditEntries?: () => void;
  /** Unsaved changes in Edit entries, which exports leave out (r3 F3-096). */
  unsaved?: number;
}

/**
 * A 409 from an action that sends the loaded version: the radar moved on, so the page is read again
 * (the chosen file and mode stay) and a second try can work (r3 F3-042). Other failures as they are.
 */
function movedOn(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.details.code === undefined;
}
const MOVED_ON = 'Someone else saved this radar in the meantime. The page now shows the newer version: check it, then try again.';

export function ImportExportTab({ view, onChanged, beforeNewVersion, onViewRadar, onEditEntries, unsaved = 0 }: ImportExportProps) {
  const chart = React.useRef<SVGSVGElement>(null);
  const noEntries = view.entries.length === 0;
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const [files, setFiles] = React.useState<File[]>([]);
  const [report, setReport] = React.useState<ImportReport | null>(null);
  /** Between choosing a file and the dry run's answer, or the reason it could not be checked. */
  const [checking, setChecking] = React.useState(false);
  const [checkError, setCheckError] = React.useState<string | null>(null);
  /** The size of a chosen file over the limit (it is not read), or null. */
  const [tooBig, setTooBig] = React.useState<number | null>(null);
  const [text, setText] = React.useState('');
  const [mode, setMode] = React.useState<'replace' | 'merge'>('merge');
  const [busy, setBusy] = React.useState(false);
  const [confirmReplace, setConfirmReplace] = React.useState(false);
  /** The API refused the import because it would remove most of the radar: how much, for the dialog. */
  const [shrink, setShrink] = React.useState<{ before: number; after: number } | null>(null);
  const [exporting, setExporting] = React.useState<string | null>(null);
  const slug = view.radar.slug;
  const format = (files[0]?.name ?? '').toLowerCase().endsWith('.json') ? 'json' : 'csv';

  const latest = React.useRef(0);
  const check = async (f: File[]) => {
    // Files swapped quickly: only the latest file's check may set the report, whichever answers last.
    const request = ++latest.current;
    setFiles(f);
    setReport(null);
    setText('');
    setTooBig(null);
    setCheckError(null);
    setChecking(false);
    if (!f[0]) return;
    if (f[0].size > IMPORT_MAX_BYTES) {
      setTooBig(f[0].size); // never read a large file into the page: it would freeze the tab
      return;
    }
    setChecking(true);
    // A cloud-only file that fails to download, or one removed meanwhile: "Checking" stayed for ever (r3 F3-086).
    const body = await f[0].text().catch(() => null);
    if (request !== latest.current) return;
    if (body === null) {
      setChecking(false);
      setCheckError('The file could not be read: it may have been moved, or it is not on this device yet. Choose it again.');
      return;
    }
    setText(body);
    await preview(request, f[0].name.toLowerCase().endsWith('.json') ? 'json' : 'csv', body);
  };
  const preview = async (request: number, fmt: 'csv' | 'json', body: string) => {
    try {
      const result = await api.checkImport(slug, fmt, body);
      if (request === latest.current) setReport(result);
    } catch (err) {
      // Inline where the report would be, as well as a toast: the reason the Import button gives is
      // otherwise "choose a file whose rows all match", which is wrong for a file that cannot be read.
      if (request !== latest.current) return;
      setCheckError(describeError(err));
      fail(err);
    } finally {
      if (request === latest.current) setChecking(false);
    }
  };
  const run = async (confirmedShrink = false): Promise<boolean> => {
    setBusy(true);
    try {
      // allow_empty is the API's one confirmation flag: it covers an empty file and, since round 2,
      // a Replace that would remove more than half the entries.
      await api.importEntries(slug, format, text, view.radar.current_version, mode, confirmedShrink || (mode === 'replace' && Boolean(report?.empty)));
      notify(
        'success',
        <>
          Imported. A new version was saved.{' '}
          <Button variant="inline-link" onClick={onViewRadar}>View the radar</Button>
        </>,
        'radar-imported',
      );
      setFiles([]);
      setReport(null);
      setText('');
      onChanged();
      return true;
    } catch (err) {
      // The API refuses a Replace that would remove most of the radar until it is confirmed; the dry
      // run usually catches it first, but the radar can have grown since the file was checked.
      const counts = shrinkOf(err, 'shrinking_import');
      if (counts) {
        setShrink(counts);
        return false;
      }
      if (movedOn(err)) {
        notify('warning', MOVED_ON);
        onChanged();
        return false;
      }
      fail(err);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const start = () => (mode === 'replace' ? setConfirmReplace(true) : beforeNewVersion('Import', () => run()));

  const exportImage = async (kind: 'svg' | 'png') => {
    const svg = chart.current as SVGSVGElement; // the off-screen chart is always mounted
    const markup = svgMarkup(svg);
    const { width, height } = svgSize(svg);
    // The address is ASCII and unique, so every radar gets its own file name (a non-Latin name would not).
    if (kind === 'svg') return download(slugFile(slug, 'svg'), markup, 'image/svg+xml');
    // A two-column legend made a 4x canvas too large for iPhone and iPad, which gave a blank image.
    const scale = pngScale(width, height);
    download(slugFile(slug, 'png'), await svgToPng(markup, width, height, scale));
    if (scale < 4) notify('info', `The picture was drawn at ${scale}x instead of 4x, the most this browser can draw at once. For print quality, use the SVG.`);
  };
  const exportData = async (kind: 'csv' | 'json') => {
    const t = await api.exportEntries(slug, kind);
    download(slugFile(slug, kind), t, kind === 'csv' ? 'text/csv' : 'application/json');
  };
  /** One export at a time, with its button busy: a 4x PNG can take seconds, and a second click would download twice. */
  const runExport = (kind: 'svg' | 'png' | 'csv' | 'json') => {
    setExporting(kind);
    void (kind === 'svg' || kind === 'png' ? exportImage(kind) : exportData(kind))
      .catch(fail)
      .finally(() => setExporting(null));
  };
  // The radar's own headings, as the suggestion diff already does: "is not a ring" on a radar whose
  // ring heading is "Position" names a field the user has never seen.
  const fieldName: Record<string, string> = {
    sector: view.schema.sector_label.toLowerCase(),
    ring: view.schema.ring_label.toLowerCase(),
    marker: view.schema.marker_label.toLowerCase(),
  };
  const problems = report
    ? [
        ...report.unmatched.map(
          (u) => `Row ${u.row}: "${u.value}" is not a ${fieldName[u.field] ?? u.field}${u.suggestion ? ` (did you mean "${u.suggestion}"?)` : ''}`,
        ),
        ...report.duplicate_rows.map((r) => `Row ${r}: duplicate title`),
        ...(report.duplicate_keys ?? []).map((k) => `Key ${k} is on more than one row`),
      ]
    : [];
  // Entries already on the radar whose values no longer match its schema (after a schema change, say).
  // A Merge keeps them, and the API refuses to save a list with unmatched values, so it must say so
  // here instead of blaming the file: these entries are not in it.
  // Only the entries the file does not name are kept by a Merge: one whose title or key is in the file
  // is replaced by its row, so it cannot block the Merge. (The dry run is the file's own, see checkImport.)
  const ids = React.useMemo(() => (text ? fileIdentities(format, text) : null), [format, text]);
  const kept = ids ? mergeKept(view.entries, ids) : view.entries;
  const stale = unmatchedAfter(kept, view.schema, {});
  const merged = kept.length + (report?.rows ?? 0);
  const blockedReason = checking
    ? 'Checking the file.'
    : checkError
      ? 'That file could not be read: the message under Import says why.'
      : !report?.accepted
        ? 'Choose a file whose rows all match this radar first.'
        : mode === 'merge' && report.empty
          ? 'The file has no entries, so merging it would change nothing.'
          : mode === 'merge' && stale.length > 0
            ? `Fix the ${plural(stale.length, 'entry', 'entries')} already on this radar first, or choose Replace.`
            : mode === 'merge' && merged > ENTRIES_MAX
              ? `Merging would leave the radar with ${merged} entries, and a radar holds at most ${ENTRIES_MAX}. Remove some from the file or the radar, or choose Replace.`
              : '';
  // Viewers cannot import, so the card does not promise it (r3 F3-109); unsaved edits are not exported (F3-096).
  const exportWhat = view.can_edit
    ? 'The chart as a picture, or the entries as a file you can edit and import again.'
    : 'The chart as a picture, or the entries as a file you can open in a spreadsheet.';
  const exportCard = (
    <Container
      header={
        <Header
          variant="h2"
          description={unsaved > 0 ? `${exportWhat} Every export is of the saved version: your ${plural(unsaved, 'unsaved change')} in Edit entries ${unsaved === 1 ? 'is' : 'are'} not in it.` : exportWhat}
        >
          Export
        </Header>
      }
    >
      <SpaceBetween size="s" direction="horizontal">
        {/* An empty radar exported as an empty disc (CTR HU-061). */}
        <Button iconName="download" loading={exporting === 'svg'} disabled={noEntries || (exporting !== null && exporting !== 'svg')} disabledReason={noEntries ? NOTHING_TO_DRAW : undefined} onClick={() => runExport('svg')}>SVG</Button>
        <Button iconName="download" loading={exporting === 'png'} disabled={noEntries || (exporting !== null && exporting !== 'png')} disabledReason={noEntries ? NOTHING_TO_DRAW : undefined} onClick={() => runExport('png')}>PNG</Button>
        <Button iconName="download" loading={exporting === 'csv'} disabled={exporting !== null && exporting !== 'csv'} onClick={() => runExport('csv')}>CSV</Button>
        <Button iconName="download" loading={exporting === 'json'} disabled={exporting !== null && exporting !== 'json'} onClick={() => runExport('json')}>JSON</Button>
      </SpaceBetween>
    </Container>
  );
  return (
    <>
      {/* Off-screen copy for SVG/PNG export: the chart with its legend and marker key, so the numbers
          can be decoded outside the app. A picture only: no tab stops. */}
      <div aria-hidden="true" style={{ position: 'absolute', left: -10000, top: 0, width: 640 }}>
        <ExportSheet ref={chart} schema={view.schema} entries={view.entries} title={view.radar.name} />
      </div>
      {!view.can_edit ? (
        exportCard
      ) : (
        <ColumnLayout columns={2}>
          <Container
            header={
              <Header
                variant="h2"
                description={`CSV or JSON with one row per entry. Columns: title, ${view.schema.sector_label} (sector), ${view.schema.ring_label} (ring), and optionally ${view.schema.marker_label} (marker), sub_sector, description, link, and key (as exported: keep it, so a renamed row still updates its entry).`}
              >
                Import
              </Header>
            }
          >
            <SpaceBetween size="m">
              <Box>
                Not sure of the format?{' '}
                <Button variant="inline-link" onClick={() => void api.importTemplate(slug).then((t) => download(slugFile(`${slug}-template`, 'csv'), t, 'text/csv'), fail)}>
                  Download the CSV template
                </Button>{' '}
                with this radar&apos;s values filled in, edit it in a spreadsheet, then choose it here. Nothing changes until you press Import.
              </Box>
              <FileUpload
                value={files}
                onChange={({ detail }) => void check(detail.value)}
                accept=".csv,.json"
                i18nStrings={{
                  uploadButtonText: () => 'Choose file',
                  dropzoneText: () => 'Drop a CSV or JSON file',
                  removeFileAriaLabel: () => 'Remove file',
                  limitShowFewer: 'Show fewer',
                  limitShowMore: 'Show more',
                  errorIconAriaLabel: 'Error',
                }}
                fileErrors={tooBig === null ? undefined : [`This file is ${fileSize(tooBig)}; the limit is 5 MB.`]}
                constraintText="Up to 5 MB and 300 entries"
              />
              {/* The result is announced: choosing a file used to be followed by silence (r3 F3-081). */}
              <div className="air-sr-only" aria-live="polite">
                {checking
                  ? 'Checking the file.'
                  : checkError
                    ? 'The file could not be checked.'
                    : report
                      ? `${plural(report.rows, 'row')} read, ${plural(problems.length, 'problem')}.`
                      : ''}
              </div>
              {checking && (
                <StatusIndicator type="loading">Checking the file against this radar</StatusIndicator>
              )}
              {checkError && (
                <Alert type="error" header="The file could not be checked">
                  {checkError}
                </Alert>
              )}
              {report && (
                <Alert type={report.accepted ? 'success' : 'warning'} header={`${plural(report.rows, 'row')} read from the file`}>
                  {report.accepted ? (
                    report.empty ? (
                      mode === 'replace' ? (
                        'The file has no entries: importing it removes every entry from this radar.'
                      ) : (
                        'The file has no entries. Merging it would leave the radar exactly as it is.'
                      )
                    ) : (
                      <SpaceBetween size="xxs">
                        <Box>Every row matches this radar.</Box>
                        <Box>
                          {mode === 'replace'
                            ? `The file becomes the whole list: the radar's ${plural(view.entries.length, 'entry', 'entries')} are replaced, and entries the file does not mention are removed.`
                            : `Rows with the key or the title of an entry already on this radar update that entry, the others are added, and the radar's other entries are kept.`}
                        </Box>
                      </SpaceBetween>
                    )
                  ) : (
                    <SpaceBetween size="xs">
                      <Box>{`${plural(problems.length, 'problem')} to fix in the file before it can be imported:`}</Box>
                      <div style={{ maxHeight: '16rem', overflowY: 'auto' }}>
                        {problems.map((line, i) => <div key={`${i}:${line}`}>{line}</div>)}
                      </div>
                      <Button variant="inline-link" onClick={() => download(slugFile(`${slug}-import-problems`, 'txt'), problems.join('\n'), 'text/plain')}>
                        Download the list
                      </Button>
                    </SpaceBetween>
                  )}
                </Alert>
              )}
              <RadioGroup
                value={mode}
                onChange={({ detail }) => setMode(detail.value as 'replace' | 'merge')}
                items={[
                  { value: 'merge', label: 'Merge', description: 'Update entries with the same key or the same title, add new ones, keep the rest.' },
                  { value: 'replace', label: 'Replace', description: 'The file becomes the full list of entries: entries not in the file are removed.' },
                ]}
              />
              {mode === 'merge' && stale.length > 0 && (
                <Alert
                  type="warning"
                  header={`${plural(stale.length, 'entry', 'entries')} already on this radar ${stale.length === 1 ? 'does' : 'do'} not match it`}
                  action={onEditEntries && <Button onClick={onEditEntries}>Edit entries</Button>}
                >
                  {`A Merge keeps the entries the file does not mention, and these cannot be saved as they are: ${stale.map((e) => e.title).join(', ')}. Their ${view.schema.sector_label.toLowerCase()}, ${view.schema.ring_label.toLowerCase()} or ${view.schema.marker_label.toLowerCase()} is no longer in the schema. Fix them under Edit entries, or choose Replace so the file becomes the whole list.`}
                </Alert>
              )}
              <Button variant="primary" disabled={Boolean(blockedReason)} disabledReason={blockedReason} loading={busy} onClick={start}>
                Import
              </Button>
            </SpaceBetween>
          </Container>
          {exportCard}
        </ColumnLayout>
      )}
      {shrink && (
        <Modal
          visible
          onDismiss={() => !busy && setShrink(null)}
          header={`Remove ${plural(shrink.before - shrink.after, 'entry', 'entries')}?`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setShrink(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  loading={busy}
                  // The dialog stays, with its spinner, until the import has settled (r3 F3-036).
                  onClick={() => void run(true).finally(() => setShrink(null))}
                >
                  {`Import and remove ${plural(shrink.before - shrink.after, 'entry', 'entries')}`}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {`This file would leave the radar with ${plural(shrink.after, 'entry', 'entries')} instead of ${shrink.before}: ${plural(
            shrink.before - shrink.after,
            'entry',
            'entries',
          )} would be removed. That is most of the radar, so it needs confirming. The old list stays under History, where you can restore it.`}
        </Modal>
      )}
      {confirmReplace && report && (
        <Modal
          visible
          onDismiss={() => setConfirmReplace(false)}
          header={report.empty ? 'Remove every entry?' : 'Replace every entry?'}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setConfirmReplace(false)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    setConfirmReplace(false);
                    // This dialog already stated how many entries go: when the API would ask again (more
                    // than half removed, radars/service.py), this is that confirmation. The API's own
                    // dialog stays for a radar that grew after the check.
                    const shrinks = needsShrinkConfirm(report.before ?? 0, report.removed ?? 0);
                    beforeNewVersion('Import', () => run(shrinks));
                  }}
                >
                  Replace
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {report.empty
            ? `The file has no entries, so all ${plural(report.before ?? view.entries.length, 'entry', 'entries')} of the radar are removed. The old list stays under History, where you can restore it.`
            : `The radar's ${plural(report.before ?? view.entries.length, 'entry', 'entries')} are replaced by the ${plural(report.rows, 'row')} in the file: any entry whose title is not in the file is removed${
                report.removed ? `, which is ${plural(report.removed, 'entry', 'entries')}` : ''
              }. The old list stays under History, where you can restore it.`}
        </Modal>
      )}
    </>
  );
}

// ---- suggestions -------------------------------------------------------------------------------

/** An evidence link labelled with the record's title (the API sends only ids), falling back to its type. */
function EvidenceLink({ id }: { id: string }) {
  const { api } = useSession();
  const node = useAsync(() => api.node(id), [api, id]);
  const type = id.replace(/\/[\s\S]*$/, '');
  return <NodeLink node={{ id, title: node.data?.title ?? typeLabel(type) }} showSource={false} />;
}

interface SuggestionsProps {
  view: RadarView;
  /** Loaded by the page, which also shows the count on the tab. */
  list: AsyncState<{ items: Suggestion[] }>;
  onChanged: () => void;
  onOpenSettings: () => void;
  beforeNewVersion: BeforeNewVersion;
}

/** What happens to the listed suggestions, including how many autopilot is holding for review. */
/** Proposals already past the radar's delay: turning on auto-apply lets these in at the next tick. */
/** The proposals older than a delay of `days`: on autopilot with auto-apply, the ones the next tick applies. */
export function dueProposals(items: Suggestion[], days: number, now = Date.now()): Suggestion[] {
  return items.filter((s) => now - Date.parse(s.created_at) >= days * 86_400_000);
}

export function suggestionsHelp(view: RadarView, items: Suggestion[], now = Date.now()): string {
  const r = view.radar;
  if (r.mode !== 'autopilot') return 'Proposals from the Radar curator. Nothing changes until you accept.';
  if (r.auto_apply_agent) return `Applied automatically after ${r.auto_apply_days} days unless someone edits the entry first.`;
  const due = dueProposals(items, r.auto_apply_days, now).length;
  return (
    `Autopilot is on, but proposals from the Radar curator wait for review: an administrator can turn on Apply agent proposals without review in Settings. ` +
    `${plural(due, 'suggestion')} past the ${r.auto_apply_days}-day delay ${due === 1 ? 'is' : 'are'} held.`
  );
}

/** The entry fields a proposal can change, in the order the editor shows them. */
/**
 * Confirms a save or a restore the API refused because it would remove most of the radar: the
 * counts are the API's, and the action is sent again with allow_empty only from here.
 */
export function ConfirmShrink({
  shrink,
  doing,
  action,
  busy,
  onCancel,
  onConfirm,
}: {
  shrink: Shrink;
  /** What would remove them, as the start of a sentence: "Saving", "Restoring version 2". */
  doing: string;
  /** The confirm button's verb: "Save", "Restore". */
  action: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const removed = plural(shrink.before - shrink.after, 'entry', 'entries');
  return (
    <Modal
      visible
      onDismiss={() => !busy && onCancel()}
      header={`Remove ${removed}?`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" disabled={busy} onClick={onCancel}>Cancel</Button>
            <Button variant="primary" loading={busy} onClick={onConfirm}>{`${action} and remove ${removed}`}</Button>
          </SpaceBetween>
        </Box>
      }
    >
      {`${doing} would leave the radar with ${plural(shrink.after, 'entry', 'entries')} instead of ${shrink.before}: ${removed} would be removed. That is most of the radar, so it needs confirming. The current list stays under History, where you can restore it.`}
    </Modal>
  );
}

/** Why a non-administrator cannot choose Autopilot on a radar whose proposals apply without review. */
export const AUTOPILOT_ADMIN_ONLY =
  'Only an administrator can switch this radar into Autopilot, because an administrator has turned on Apply agent proposals without review for it.';

/** Why SVG and PNG are off on a radar with no entries. */
export const NOTHING_TO_DRAW = 'The radar has no entries yet, so the picture would be empty. Add entries first.';

/** Returned by a save that did not happen and needs no toast. */
const NOT_SAVED = Symbol('not saved');

/** The addresses a 400 unknown_editors names, or null for any other error. */
export function noAccountOf(err: unknown): string[] | null {
  if (!(err instanceof ApiError) || err.status !== 400 || err.details.code !== 'unknown_editors') return null;
  const named = err.details.unknown;
  return Array.isArray(named) && named.length ? named.map(String) : null;
}

/** What Access says about addresses no account signs in with, for an owner who cannot read the user list. */
export const NO_ACCOUNT = (emails: string[]) =>
  `No account signs in with ${emails.join(', ')}. Check the spelling: a mistyped address gives nobody access. Press Add anyway to add ${emails.length === 1 ? 'it' : 'them'} regardless.`;

/** What Accept or Discard says when someone else reviewed the proposal first. */
export const SUGGESTION_REVIEWED = 'That suggestion was already reviewed: someone else accepted or discarded it. The list has been reloaded.';
/** What it says when the entry already has another open suggestion waiting for review. */
export const SUGGESTION_PENDING =
  'This entry already has an open suggestion waiting for review, and an entry can have only one. Accept or discard that one first; the list has been reloaded.';

/** What a stale proposal says: the entry changed (or was removed) since it was proposed (CTR HA-013). */
export const SUGGESTION_STALE =
  'This entry changed or was removed after the suggestion was made, so the suggestion no longer applies and cannot be accepted. Discard it, or edit the entry directly; the list has been reloaded.';

/** The fields the backend compares (RadarEntry.same_content). */
const CONTENT_FIELDS = ['title', 'sector', 'sub_sector', 'ring', 'marker', 'description', 'link', 'node_id'] as const;

/** Whether a proposal still applies to the radar shown: an edit's entry still equals its base, and a new entry is still absent. */
export function stillApplies(sug: Suggestion, entries: RadarEntry[]): boolean {
  const current = entries.find((e) => e.key === sug.entry_key);
  if (!sug.base) return current === undefined;
  const base = sug.base;
  return current !== undefined && CONTENT_FIELDS.every((f) => (current[f] ?? '') === (base[f] ?? ''));
}

const PROPOSAL_FIELDS = ['title', 'sector', 'sub_sector', 'ring', 'marker', 'description', 'link', 'node_id'];

/** A field's name in the radar's own words, capitalised for the start of a line. */
export function proposalLabel(field: string, schema: RadarSchema): string {
  const label = fieldLabels([field], schema);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function SuggestionsTab({ view, list, onChanged, onOpenSettings, beforeNewVersion }: SuggestionsProps) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  /** The proposal being accepted or discarded, and which: the spinner goes on that button (r3 F3-063). */
  const [acting, setActing] = React.useState<{ key: string; accept: boolean } | null>(null);
  /** The proposal whose Discard is waiting to be confirmed, held whole: the list may reload meanwhile. */
  const [discarding, setDiscarding] = React.useState<Suggestion | null>(null);
  const slug = view.radar.slug;
  const s = view.schema;
  const act = async (sug: Suggestion, accept: boolean): Promise<boolean> => {
    setActing({ key: sug.entry_key, accept });
    try {
      if (accept) await api.acceptSuggestion(slug, sug.entry_key);
      else await api.discardSuggestion(slug, sug.entry_key);
      // Named: with several open, "Applied" alone did not say which one (CTR HU-022).
      const title = sug.base?.title ?? String(sug.proposal.title); // an edit has a base, a new entry a title
      notify('success', accept ? `Applied: ${title}, saved as a new version.` : `Discarded: ${title}.`);
      list.reload();
      onChanged();
      return true;
    } catch (err) {
      // Another editor acted first, or the radar moved on: the stale row used to stay and fail again (r3 F3-062, F3-042).
      // A 404 is a proposal that was claimed by another reviewer (or autopilot) first (B3-073, B3-074).
      const gone = err instanceof ApiError && err.status === 404;
      const pending = err instanceof ApiError && err.status === 409 && err.details.code === 'suggestion_pending';
      const stale = err instanceof ApiError && err.status === 409 && err.details.code === 'suggestion_stale';
      if (stale) {
        notify('warning', SUGGESTION_STALE);
        list.reload();
        onChanged();
      } else if (pending) {
        notify('warning', SUGGESTION_PENDING);
        list.reload();
      } else if (gone || movedOn(err)) {
        notify('warning', gone ? SUGGESTION_REVIEWED : MOVED_ON);
        list.reload();
        onChanged();
      } else fail(err);
      return false;
    } finally {
      setActing(null);
    }
  };
  if (view.radar.mode === 'manual') {
    return (
      <Alert
        header="AI suggestions are off for this radar"
        action={view.can_admin ? <Button onClick={onOpenSettings}>Open Settings</Button> : undefined}
      >
        When they are on, the Radar curator module reads new evidence in the knowledge graph and proposes entries to add or move. They wait
        here for you to accept or reject.{' '}
        {view.can_admin
          ? 'Turn them on in Settings (AI suggestions), and switch on the Radar curator under Modules.'
          : 'Ask the radar owner to turn them on.'}
      </Alert>
    );
  }
  if (list.error) {
    return (
      <Alert type="error" header="Suggestions could not be loaded" action={<Button onClick={list.reload}>Retry</Button>}>
        {list.error.message}
      </Alert>
    );
  }
  return (
    <>
    <Table wrapLines
      loading={list.loading && !list.data}
      items={list.data?.items ?? []}
      trackBy="entry_key"
      variant="container"
      header={
        <Header
          counter={`(${list.data?.items.length ?? 0})`}
          description={
            view.can_edit
              ? suggestionsHelp(view, list.data?.items ?? [])
              : 'Proposals from the Radar curator. Editors of this radar accept or discard them; nothing changes until they do.'
          }
        >
          Suggestions
        </Header>
      }
      columnDefinitions={[
        {
          id: 'what',
          header: 'Proposal',
          // Every proposed field, old and new: a new description or link used to show nothing at all,
          // and a rename showed only the new title (r3 F3-027).
          cell: (sug) => {
            const was = (sug.base ?? {}) as Record<string, unknown>;
            const now = sug.proposal as Record<string, unknown>;
            // Stored descriptions are HTML: shown as text, not as '<p>...' markup (CTR HC-136).
            const shown = (x: unknown) => (x === undefined || x === null || x === '' ? '-' : clip(storedText(String(x)), 80));
            return (
              <div>
                {!stillApplies(sug, view.entries) && (
                  <Box color="text-status-warning" fontSize="body-s">
                    No longer applicable: the entry changed or was removed after this was proposed. Discard it.
                  </Box>
                )}
                <strong>{sug.is_new ? `New: ${sug.proposal.title}` : `Change: ${sug.base?.title ?? sug.proposal.title}`}</strong>
                <ul className="air-evidence">
                  {PROPOSAL_FIELDS.filter((f) => now[f] !== undefined && !(sug.is_new && f === 'title')).map((f) => (
                    <li key={f}>
                      <Box fontSize="body-s">{`${proposalLabel(f, s)}: ${shown(was[f])} \u2192 ${shown(now[f])}`}</Box>
                    </li>
                  ))}
                </ul>
              </div>
            );
          },
        },
        {
          id: 'why',
          header: 'Reason',
          cell: (sug) => (
            <SpaceBetween size="xxxs">
              <Box fontSize="body-s">{sug.reason}</Box>
              {/* Which module made it, so a strange proposal can be traced back to its run. */}
              <Box fontSize="body-s" color="text-body-secondary">
                {`Proposed by ${actorLabel(sug.created_by)}`}
                {sug.run_id ? ` \u00b7 run ${sug.run_id.slice(0, 8)}` : ''}
              </Box>
            </SpaceBetween>
          ),
          minWidth: 320,
        },
        {
          id: 'evidence',
          header: 'Evidence',
          cell: (sug) => (
            <>
              {sug.evidence.slice(0, 3).map((id) => (
                <div key={id}>
                  <EvidenceLink id={id} />
                </div>
              ))}
              {sug.evidence.length > 3 && <Box fontSize="body-s" color="text-body-secondary">{`and ${sug.evidence.length - 3} more`}</Box>}
            </>
          ),
        },
        { id: 'when', header: 'Proposed', cell: (sug) => <span title={when(sug.created_at)}>{ago(sug.created_at)}</span> },
        {
          id: 'act',
          header: 'Actions', // a named column header, for screen readers (CTR QA-090)
          cell: (sug) =>
            view.can_edit && (
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  loading={acting?.key === sug.entry_key && acting.accept}
                  disabled={!stillApplies(sug, view.entries) || (acting !== null && !(acting.key === sug.entry_key && acting.accept))}
                  onClick={() => beforeNewVersion('Accept', () => act(sug, true))}
                >
                  Accept
                </Button>
                {/* Asks first: a discarded proposal, with its evidence, cannot be brought back, and
                    Discard sits next to Accept. */}
                <Button
                  variant="link"
                  loading={acting?.key === sug.entry_key && !acting.accept}
                  disabled={acting !== null && !(acting.key === sug.entry_key && !acting.accept)}
                  onClick={() => setDiscarding(sug)}
                >
                  Discard
                </Button>
              </SpaceBetween>
            ),
        },
      ]}
      empty={<Box textAlign="center" padding="m">No open suggestions.</Box>}
    />
      {discarding && (
        <Modal
          visible
          onDismiss={() => setDiscarding(null)}
          header="Discard this proposal?"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setDiscarding(null)}>Keep it</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    const sug = discarding;
                    setDiscarding(null);
                    void act(sug, false);
                  }}
                >
                  Discard
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          It leaves this list with the evidence it cites, and cannot be brought back. The Radar curator may propose it again on its next run.
        </Modal>
      )}
    </>
  );
}

// ---- history -----------------------------------------------------------------------------------

/** "sub_sector, link" means nothing to a reader: the radar's own words do. */
export function fieldLabels(fields: string[], schema: RadarSchema): string {
  const names: Record<string, string> = {
    title: 'title',
    sector: schema.sector_label.toLowerCase(),
    sub_sector: `sub-${schema.sector_label.toLowerCase()}`,
    ring: schema.ring_label.toLowerCase(),
    marker: schema.marker_label.toLowerCase(),
    description: 'description',
    link: 'link',
    node_id: 'linked record',
  };
  return fields.map((f) => names[f] ?? f.replace(/_/g, ' ')).join(', ');
}

function DiffView({ changes, schema }: { changes: RadarDiffChanges; schema: RadarSchema }) {
  // Each line names the dimension that moved: "Contoso: Leader -> Niche" alone did not say whether
  // that was a ring change or a sector change.
  const lines: string[] = [
    ...changes.added.map((e) => `Added ${e.title} (${e.ring})`),
    ...changes.removed.map((e) => `Removed ${e.title}`),
    ...changes.moved.map((m) => `${m.title}, ${schema.ring_label.toLowerCase()}: ${m.from} \u2192 ${m.to}`),
    ...changes.recategorised.map((m) => `${m.title}, ${schema.sector_label.toLowerCase()}: ${m.from} \u2192 ${m.to}`),
    ...changes.marker_changed.map((m) => `${m.title}, ${schema.marker_label.toLowerCase()}: ${m.from ?? '-'} \u2192 ${m.to ?? '-'}`),
    ...changes.edited.map((e) => `${e.title}: edited ${fieldLabels(e.fields, schema)}`),
  ];
  // Two entries can change the same way, so a line's text alone is not a unique key.
  return lines.length ? <ul>{lines.map((l, i) => <li key={`${i}:${l}`}>{l}</li>)}</ul> : <Box>No differences.</Box>;
}

const VERSIONS_PAGE = 20;

/** The backend's own note codes as sentences; anything a person wrote is shown as written. */
export function versionNote(note: string, schemaChanged: boolean): string {
  const map: Record<string, string> = {
    saved: 'Saved by hand',
    'imported csv (merge)': 'Imported a CSV file (merge)',
    'imported csv (replace)': 'Imported a CSV file (replace)',
    'imported json (merge)': 'Imported a JSON file (merge)',
    'imported json (replace)': 'Imported a JSON file (replace)',
    'schema changed': 'Schema changed',
  };
  if (!note) return schemaChanged ? 'Schema changed' : '-';
  const dropped = /^schema changed \((\d+) entries dropped\)$/.exec(note);
  if (dropped) return `Schema changed (${plural(Number(dropped[1]), 'entry', 'entries')} removed)`;
  const rolled = /^rolled back to v(\d+)$/.exec(note);
  if (rolled) return `Restored version ${rolled[1] as string}`;
  return map[note] ?? note;
}

export function HistoryTab({
  view,
  versions,
  onChanged,
  beforeNewVersion,
}: {
  view: RadarView;
  /** Loaded by the page, which keeps it across tab switches (r3 F3-064: every visit re-read every page). */
  versions: AsyncState<Listing<RadarVersion> | null>;
  onChanged: () => void;
  beforeNewVersion: BeforeNewVersion;
}) {
  const { api, me } = useSession();
  // Administrators already have the data the Audit tab uses: "Added by a person" tells two editors
  // sharing a radar nothing. Everyone else sees that, because only they may read the user list.
  // Keyed on the permission, not the profile: the profile is re-read every minute and on focus, and a
  // new object each time refetched every user (up to 20 pages) and blanked the "By" column meanwhile.
  const manageUsers = can(me, 'user.manage');
  const users = useAsync(() => (manageUsers ? api.users() : Promise.resolve(null)), [api, manageUsers]);
  const emails = React.useMemo(() => new Map((users.data?.items ?? []).map((u) => [u.username, u.email])), [users.data]);
  const { fail, notify } = useNotify();
  const slug = view.radar.slug;
  const navigate = useNavigate();
  const tags = useAsync(() => api.tags(slug), [api, slug], [view.radar.current_version]);
  const tagsOf = (n: number) => (tags.data?.items ?? []).filter((t) => t.version === n);
  /** The version being tagged, and the name typed for it. */
  const [tagging, setTagging] = React.useState<{ version: number; name: string; error?: string } | null>(null);
  const [tagBusy, setTagBusy] = React.useState(false);
  const saveTag = async (tagging: { version: number; name: string }) => {
    setTagBusy(true);
    try {
      const out = await api.tagVersion(slug, tagging.version, tagging.name);
      notify('success', `Version ${out.version} is tagged ${out.tag}.`);
      setTagging(null);
      tags.reload();
    } catch (err) {
      // A look-alike or a bad name is said in the dialog, where it can be fixed.
      setTagging({ ...tagging, error: (err as Error).message });
    } finally {
      setTagBusy(false);
    }
  };
  const removeTag = async (tag: string) => {
    try {
      await api.untag(slug, tag);
      notify('success', `The tag ${tag} was removed.`);
      tags.reload();
    } catch (err) {
      fail(err);
    }
  };
  /** A link to one version, by its tag when it has one (a tag reads better and survives renumbering). */
  const linkTo = (n: number) => {
    const tag = tagsOf(n)[0]?.tag;
    return `/radars/${slug}?${tag ? `tag=${encodeURIComponent(tag)}` : `v=${n}`}`;
  };
  const copyLink = async (n: number) => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${linkTo(n)}`);
      notify('success', 'Link copied.');
    } catch (err) {
      fail(err);
    }
  };

  const [diff, setDiff] = React.useState<{ from: number; to: number; changes: RadarDiffChanges } | null>(null);
  /** The pair being compared while its answer is on the way, so the dialog is not blank for 3 seconds. */
  const [comparing, setComparing] = React.useState<{ from: number; to: number } | null>(null);
  /** Only the newest comparison may set the diff: answers arriving out of order made the selects jump back. */
  const latestDiff = React.useRef(0);
  const [restoring, setRestoring] = React.useState<{ version: number; entries: number } | null>(null);
  /** The version being restored: the row says so, instead of only its links turning grey. */
  const [busy, setBusy] = React.useState<number | null>(null);
  const [page, setPage] = React.useState(1);
  const items = versions.data?.items ?? [];
  const pages = Math.max(1, Math.ceil(items.length / VERSIONS_PAGE));
  const current = Math.min(page, pages);
  const show = async (from: number, to: number) => {
    // From may never pass To: the API would answer with the differences the other way round.
    const [low, high] = from <= to ? [from, to] : [to, from];
    const request = ++latestDiff.current;
    setComparing({ from: low, to: high });
    try {
      const d = await api.diff(slug, low, high);
      if (request === latestDiff.current) setDiff({ from: low, to: high, changes: d.changes });
    } catch (err) {
      if (request === latestDiff.current) fail(err);
    } finally {
      if (request === latestDiff.current) setComparing(null);
    }
  };
  /** A restore the API refused because it removes most of the radar, waiting to be confirmed. */
  const [shrinking, setShrinking] = React.useState<(Shrink & { version: number }) | null>(null);
  const restore = async (version: number, allowEmpty = false): Promise<boolean> => {
    setBusy(version);
    try {
      await api.rollback(slug, version, view.radar.current_version, allowEmpty);
      notify('success', `Restored version ${version} as a new version.`);
      onChanged();
      return true;
    } catch (err) {
      const counts = shrinkOf(err, 'shrinking_rollback');
      if (counts) setShrinking({ ...counts, version });
      else if (movedOn(err)) {
        notify('warning', MOVED_ON);
        onChanged();
      } else fail(err);
      return false;
    } finally {
      setBusy(null);
    }
  };
  /** Who saved: "You", the person's email where it is known, else what kind of actor it was. */
  const who = (createdBy: string) => {
    if (me && createdBy === `user:${me.subject}`) return 'You';
    const email = createdBy.startsWith('user:') ? emails.get(createdBy.slice('user:'.length)) : undefined;
    return email ?? actorLabel(createdBy);
  };
  // Up to ~1,000 versions: filterable by number, date or author (CTR HU-080).
  const options = items.map((x) => ({ value: String(x.version), label: `Version ${x.version}`, description: `${when(x.created_at)} \u00b7 ${actorLabel(x.created_by)}` }));
  /** Which pair the dialog is about: the one on its way, or the one it is showing. */
  const pair = comparing ?? diff ?? { from: 0, to: 0 };
  return (
    <>
      <Table wrapLines
        loading={versions.loading && !versions.data}
        items={items.slice((current - 1) * VERSIONS_PAGE, current * VERSIONS_PAGE)}
        trackBy="version"
        variant="container"
        pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        empty={versions.error ? <TableLoadError what="versions" error={versions.error} onRetry={versions.reload} /> : <Box textAlign="center" padding="m">No versions yet.</Box>}
        header={<Header counter={versions.data ? `(${items.length}${versions.data.truncated ? '+' : ''})` : undefined} description="Every save is a version. Changes shows what moved since the version before (and lets you compare any two); Restore copies an old version into a new one, so nothing is lost.">Versions</Header>}
        columnDefinitions={[
          { id: 'v', header: 'Version', cell: (x) => (x.version === view.radar.current_version ? `${x.version} (current)` : x.version) },
          { id: 'when', header: 'Saved', cell: (x) => when(x.created_at) },
          { id: 'who', header: 'By', cell: (x) => who(x.created_by) },
          { id: 'n', header: 'Entries', cell: (x) => x.entry_count },
          { id: 'note', header: 'Note', cell: (x) => versionNote(x.note ?? '', Boolean(x.schema_changed)) },
          {
            id: 'tags',
            header: 'Tags',
            cell: (x) =>
              tagsOf(x.version).length ? (
                <SpaceBetween direction="horizontal" size="xxs">
                  {tagsOf(x.version).map((t) =>
                    view.can_edit ? (
                      <Button key={t.tag} variant="inline-link" iconName="close" iconAlign="right" ariaLabel={`Remove the tag ${t.tag}`} onClick={() => void removeTag(t.tag)}>
                        {t.tag}
                      </Button>
                    ) : (
                      <Badge key={t.tag}>{t.tag}</Badge>
                    ),
                  )}
                </SpaceBetween>
              ) : (
                '-'
              ),
          },
          {
            id: 'act',
            header: 'Actions', // a named column header, for screen readers (CTR QA-090)
            cell: (x) => (
              <SpaceBetween direction="horizontal" size="xs">
                {x.version > 1 && <Button variant="inline-link" onClick={() => void show(x.version - 1, x.version)}>Changes</Button>}
                {x.version !== view.radar.current_version && (
                  <Button variant="inline-link" href={linkTo(x.version)} onFollow={(e) => { e.preventDefault(); navigate(linkTo(x.version)); }}>
                    Open
                  </Button>
                )}
                <Button variant="inline-link" ariaLabel={`Copy a link to version ${x.version}`} onClick={() => void copyLink(x.version)}>Copy link</Button>
                {view.can_edit && (
                  <Button variant="inline-link" ariaLabel={`Tag version ${x.version}`} onClick={() => setTagging({ version: x.version, name: '' })}>Tag</Button>
                )}
                {view.can_edit && x.version !== view.radar.current_version && (
                  <Button
                    variant="inline-link"
                    loading={busy === x.version}
                    disabled={busy !== null}
                    onClick={() => setRestoring({ version: x.version, entries: x.entry_count })}
                  >
                    Restore
                  </Button>
                )}
              </SpaceBetween>
            ),
          },
        ]}
      />
      {(diff || comparing) && (
        <Modal
          visible
          onDismiss={() => {
            latestDiff.current += 1; // an answer still on its way must not re-open this
            setDiff(null);
            setComparing(null);
          }}
          header={`Changes from version ${pair.from} to version ${pair.to}`}
        >
          <SpaceBetween size="m">
            <ColumnLayout columns={2}>
              <FormField label="From">
                <Select
                  expandToViewport
                  filteringType="auto"
                  selectedOption={{ value: String(pair.from), label: `Version ${pair.from}` }}
                  onChange={({ detail }) => void show(Number(detail.selectedOption.value), pair.to)}
                  options={options}
                />
              </FormField>
              <FormField label="To">
                <Select
                  expandToViewport
                  filteringType="auto"
                  selectedOption={{ value: String(pair.to), label: `Version ${pair.to}` }}
                  onChange={({ detail }) => void show(pair.from, Number(detail.selectedOption.value))}
                  options={options}
                />
              </FormField>
            </ColumnLayout>
            {comparing ? <StatusIndicator type="loading">Comparing the versions</StatusIndicator> : <DiffView changes={(diff as { changes: RadarDiffChanges }).changes} schema={view.schema} />}
          </SpaceBetween>
        </Modal>
      )}
      {tagging && (
        <Modal
          visible
          onDismiss={() => !tagBusy && setTagging(null)}
          header={`Tag version ${tagging.version}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" disabled={tagBusy} onClick={() => setTagging(null)}>Cancel</Button>
                <Button variant="primary" loading={tagBusy} disabled={!tagging.name.trim()} onClick={() => void saveTag(tagging)}>Tag</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <FormField
            label="Tag"
            description="A name such as Q3 2026 or Board review. A link with the tag keeps showing this version after newer saves."
            errorText={tagging.error}
          >
            <Input
              value={tagging.name}
              onChange={({ detail }) => setTagging({ ...tagging, name: detail.value, error: undefined })}
              onKeyDown={({ detail }) => detail.key === 'Enter' && tagging.name.trim() && void saveTag(tagging)}
              ariaLabel="Tag"
            />
          </FormField>
        </Modal>
      )}
      {restoring && (
        <Modal
          visible
          onDismiss={() => setRestoring(null)}
          header={`Restore version ${restoring.version}?`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setRestoring(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    const { version } = restoring;
                    setRestoring(null); // one dialog at a time: the page may ask about unsaved edits next
                    beforeNewVersion('Restore', () => restore(version));
                  }}
                >
                  Restore
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {`Version ${restoring.version} (${plural(restoring.entries, 'entry', 'entries')}) is copied into a new version ${view.radar.current_version + 1}, which replaces the radar's current ${plural(view.entries.length, 'entry', 'entries')}. The current version stays in History.`}
        </Modal>
      )}
      {shrinking && (
        <ConfirmShrink
          shrink={shrinking}
          doing={`Restoring version ${shrinking.version}`}
          action="Restore"
          busy={busy === shrinking.version}
          onCancel={() => setShrinking(null)}
          onConfirm={() => void restore(shrinking.version, true).finally(() => setShrinking(null))}
        />
      )}
    </>
  );
}

// ---- settings ----------------------------------------------------------------------------------

function names(text: string): { name: string }[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((name) => ({ name }));
}

/** Unsaved Settings fields; a missing field shows the saved value. */
export interface SettingsDraft {
  name?: string;
  purpose?: string;
  mode?: RadarMode;
  days?: string;
  auto_agent?: boolean;
  editor?: string;
  editors?: string[];
  sectors?: string;
  rings?: string;
  markers?: string;
  sector_label?: string;
  ring_label?: string;
  marker_label?: string;
}

const lines = (xs: { name: string }[]) => xs.map((s) => s.name).join('\n');

/** Every Settings field: the draft where there is one, else the saved value. */
export function settingsValues(view: RadarView, draft: SettingsDraft): Required<SettingsDraft> {
  const r = view.radar;
  return {
    name: draft.name ?? r.name,
    purpose: draft.purpose ?? r.purpose,
    mode: draft.mode ?? r.mode,
    days: draft.days ?? String(r.auto_apply_days),
    auto_agent: draft.auto_agent ?? Boolean(r.auto_apply_agent),
    editor: draft.editor ?? '',
    editors: draft.editors ?? r.editors,
    sectors: draft.sectors ?? lines(view.schema.sectors),
    rings: draft.rings ?? lines(view.schema.rings),
    markers: draft.markers ?? lines(view.schema.markers),
    sector_label: draft.sector_label ?? view.schema.sector_label,
    ring_label: draft.ring_label ?? view.schema.ring_label,
    marker_label: draft.marker_label ?? view.schema.marker_label,
  };
}

const SECTIONS = {
  meta: ['name', 'purpose', 'mode', 'days', 'auto_agent'],
  // 'editor' is the field being typed: an address typed and not added used to be dropped in silence.
  access: ['editors', 'editor'],
  schema: ['sectors', 'rings', 'markers', 'sector_label', 'ring_label', 'marker_label'],
} as const;

/** Whether a Settings section (or, without one, any section) differs from the saved radar. */
export function settingsDirty(view: RadarView, draft: SettingsDraft, section?: keyof typeof SECTIONS): boolean {
  const now = settingsValues(view, draft);
  const saved = settingsValues(view, {});
  const keys = section ? SECTIONS[section] : [...SECTIONS.meta, ...SECTIONS.access, ...SECTIONS.schema];
  return keys.some((k) => JSON.stringify(now[k]) !== JSON.stringify(saved[k]));
}

/** What happens to a value the new list no longer has. */
export type DimensionChoice = { action: 'remove' } | { action: 'clear' } | { action: 'rename'; to: string };

/** One dimension's new list, what it lost and gained, and what happens to each lost value. */
export interface DimensionPlan {
  /** The names in the new list, in order. */
  names: string[];
  /** Values the new list no longer has, in their old order. */
  removed: string[];
  /** Values the new list adds, in their new order. */
  added: string[];
  choices: Record<string, DimensionChoice>;
}

const RENAME_PREFIX = 'rename:';

/** A choice as a Select option value; the prefix keeps a value named "remove" from colliding. */
export function choiceValue(choice: DimensionChoice): string {
  return choice.action === 'rename' ? `${RENAME_PREFIX}${choice.to}` : choice.action;
}

export function parseChoice(value: string): DimensionChoice {
  if (value === 'remove' || value === 'clear') return { action: value };
  return { action: 'rename', to: value.slice(RENAME_PREFIX.length) };
}

/**
 * The plan for one dimension: the new list, the values it lost and gained, and a first guess at what
 * happens to each lost value. A lost value is guessed to be a rename only when a new value took its
 * exact place in the list, so an unrelated delete plus an add is not read as a rename and two renames
 * combined with a reorder cannot cross over. Every guess is shown and can be changed before saving.
 * With `clearable` (markers, which are optional on an entry) the fallback keeps the entries and drops
 * the marker instead of removing them.
 */
export function planDimension(prev: Dimension[], text: string, clearable = false): DimensionPlan {
  const next = names(text).map((n) => n.name);
  const before = prev.map((p) => p.name);
  const has = (xs: string[], n: string) => xs.some((x) => fold(x) === fold(n));
  const removed = before.filter((p) => !has(next, p));
  const added = next.filter((n) => !has(before, n));
  const choices: Record<string, DimensionChoice> = {};
  for (const name of removed) {
    const took = next[before.indexOf(name)];
    choices[name] = took !== undefined && added.includes(took) ? { action: 'rename', to: took } : { action: clearable ? 'clear' : 'remove' };
  }
  return { names: next, removed, added, choices };
}

/** The plan with one lost value's choice replaced. */
export function withChoice(plan: DimensionPlan, value: string, choice: DimensionChoice): DimensionPlan {
  return { ...plan, choices: { ...plan.choices, [value]: choice } };
}

/** The new dimension list: values kept or renamed keep their colour and glyph. */
export function dimensionsOf(prev: Dimension[], plan: DimensionPlan): Dimension[] {
  return plan.names.map((name) => {
    const kept = prev.find((p) => fold(p.name) === fold(name));
    if (kept) return { ...kept, name };
    const from = prev.find((p) => {
      const choice = plan.choices[p.name];
      return choice?.action === 'rename' && choice.to === name;
    });
    return from ? { ...from, name } : { name };
  });
}

/** The renames to send to the API: only the pairs the user left or chose. */
export function renamesOf(plan: DimensionPlan): Record<string, string> {
  const pairs = plan.removed
    .map((from) => [from, plan.choices[from]] as const)
    .filter(([, choice]) => choice?.action === 'rename')
    .map(([from, choice]) => [from, (choice as { to: string }).to]);
  return Object.fromEntries(pairs);
}

/** Lost values whose entries should stay, without the value. */
export function clearedOf(plan: DimensionPlan): string[] {
  return plan.removed.filter((from) => plan.choices[from]?.action === 'clear');
}

/** Entries that would match no value after the schema change (the API removes them, so the user is asked). */
export function unmatchedAfter(entries: RadarEntry[], schema: RadarSchema, renames: Record<string, Record<string, string>>): RadarEntry[] {
  const set = (xs: Dimension[]) => new Set(xs.map((x) => fold(x.name)));
  const sectors = set(schema.sectors);
  const rings = set(schema.rings);
  const markers = set(schema.markers);
  const moved = (dim: string, value: string) => renames[dim]?.[value] ?? value;
  return entries.filter(
    (e) =>
      !sectors.has(fold(moved('sector', e.sector))) ||
      !rings.has(fold(moved('ring', e.ring))) ||
      (!!e.marker && !markers.has(fold(moved('marker', e.marker)))),
  );
}

/** A heading as typed, or the saved one when the field was left blank. */
const heading = (typed: string, saved: string) => typed.trim() || saved;

/** The three dimensions a schema has, with the field each one's heading lives in. */
export const DIMENSIONS = [
  { key: 'sector', heading: 'sector_label' },
  { key: 'ring', heading: 'ring_label' },
  { key: 'marker', heading: 'marker_label' },
] as const;

export type SchemaPlans = Record<(typeof DIMENSIONS)[number]['key'], DimensionPlan>;

export interface SchemaPlan {
  schema: RadarSchema;
  renames: Record<string, Record<string, string>>;
  /** Entries whose marker the user chose to clear: saved as entries before the schema change. */
  cleared: RadarEntry[];
  /** The entry list with those markers cleared, ready to save. */
  rows: RadarEntry[];
  /** Entries the schema change would still remove. */
  dropped: RadarEntry[];
}

/** Everything the confirm dialog shows and the save sends, from the three dimension plans. */
export function plannedSchema(view: RadarView, headings: Pick<Required<SettingsDraft>, 'sector_label' | 'ring_label' | 'marker_label'>, plans: SchemaPlans): SchemaPlan {
  const schema: RadarSchema = {
    ...view.schema,
    sector_label: heading(headings.sector_label, view.schema.sector_label),
    ring_label: heading(headings.ring_label, view.schema.ring_label),
    marker_label: heading(headings.marker_label, view.schema.marker_label),
    sectors: dimensionsOf(view.schema.sectors, plans.sector),
    rings: dimensionsOf(view.schema.rings, plans.ring),
    markers: dimensionsOf(view.schema.markers, plans.marker),
  };
  const renames = Object.fromEntries(
    DIMENSIONS.map(({ key }) => [key, renamesOf(plans[key])] as const).filter(([, map]) => Object.keys(map).length > 0),
  );
  const clear = new Set(clearedOf(plans.marker).map(fold));
  const carries = (e: RadarEntry) => !!e.marker && clear.has(fold(e.marker));
  const cleared = view.entries.filter(carries);
  const rows = cleared.length ? view.entries.map((e) => (carries(e) ? { ...e, marker: undefined } : e)) : view.entries;
  return { schema, renames, cleared, rows, dropped: unmatchedAfter(rows, schema, renames) };
}

interface SettingsProps {
  view: RadarView;
  /** Open proposals, so turning on auto-apply can say how many would land at once. */
  suggestions: Suggestion[];
  onChanged: () => void;
  /** Owned by the page so unsaved fields survive switching tabs (only the active tab is mounted). */
  draft: SettingsDraft;
  onDraft: (patch: SettingsDraft) => void;
  beforeNewVersion: BeforeNewVersion;
  /** The radar is gone: the page leaves without asking about unsaved fields. */
  onDeleted: () => void;
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** The most versions a radar may have for an owner who is not an administrator to delete it (radars/service.py). */
export const OWNER_DELETE_MAX_VERSIONS = 10;

export function SettingsTab({ view, suggestions, onChanged, draft, onDraft, beforeNewVersion, onDeleted }: SettingsProps) {
  const { api, me } = useSession();
  const { fail, notify } = useNotify();
  const r = view.radar;
  const values = settingsValues(view, draft);
  const { name, purpose, mode, days, auto_agent: autoAgent, editor, editors, sectors, rings, markers } = values;
  const isAdmin = me?.role === 'admin';
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  /** The proposals a General save would let in unreviewed at once, waiting for the user to confirm. */
  const [due, setDue] = React.useState<{ items: Suggestion[]; days: number } | null>(null);
  /** Transferring a radar is administrator-only (radars_access): an owner could otherwise push a
   *  radar onto any editor without their consent and fill their per-owner cap. */
  const [transferring, setTransferring] = React.useState(false);
  /** The chosen person, kept whole: the user list can reload while the dialog is open. */
  const [newOwner, setNewOwner] = React.useState<{ username: string; email: string } | null>(null);
  const users = useAsync(() => (isAdmin ? api.users() : Promise.resolve(null)), [api, isAdmin]);
  const candidates = (users.data?.items ?? []).filter((u) => u.enabled && u.role !== 'viewer' && u.username !== r.owner);
  const [saving, setSaving] = React.useState<string | null>(null);
  /** Editors the API found no account for on the last Save access: the next one adds them anyway (CTR HCB-037). */
  const [noAccount, setNoAccount] = React.useState<string[]>([]);
  // An owner who is not an administrator may switch unreviewed agent changes off on their own radar;
  // only an administrator may switch them on (radars/service.py update_meta).
  const canArmAgent = isAdmin || Boolean(r.auto_apply_agent);
  /** Only an administrator may switch this radar into autopilot: its proposals would then apply without review. */
  const armsAutopilot = !isAdmin && r.mode !== 'autopilot' && Boolean(r.auto_apply_agent);
  // Proposals apply without review only on autopilot with the toggle on. A save that turns that on
  // (the toggle, or autopilot itself) or shortens the delay lets every proposal now past it in at once.
  const autoBefore = r.mode === 'autopilot' && Boolean(r.auto_apply_agent);
  const autoAfter = mode === 'autopilot' && autoAgent;
  /** The three dimension plans while the confirm dialog is open; the user can change any pairing. */
  const [plans, setPlans] = React.useState<SchemaPlans | null>(null);
  const dayCount = new WholeNumber(days, 1);
  const listErrors = { sectors: listError('sectors', sectors), rings: listError('rings', rings), markers: listError('markers', markers) };
  const headingErrors = { sector: headingError(values.sector_label), ring: headingError(values.ring_label), marker: headingError(values.marker_label) };
  const schemaInvalid = Boolean(listErrors.sectors || listErrors.rings || listErrors.markers || headingErrors.sector || headingErrors.ring || headingErrors.marker);
  const dirty = { meta: settingsDirty(view, draft, 'meta'), access: settingsDirty(view, draft, 'access'), schema: settingsDirty(view, draft, 'schema') };
  // Clearing a heading field falls back to the saved heading, and trailing spaces or blank lines mark
  // the section dirty: Save schema then wrote a version identical to the one before it.
  const schemaUnchanged =
    JSON.stringify(plannedSchema(view, values, {
      sector: planDimension(view.schema.sectors, sectors),
      ring: planDimension(view.schema.rings, rings),
      marker: planDimension(view.schema.markers, markers, true),
    }).schema) === JSON.stringify(view.schema);
  const saveWith = (section: string, work: () => Promise<unknown>, done: string, clear: SettingsDraft): Promise<boolean> => {
    setSaving(section);
    return work()
      .then(
        (res) => {
          if (res === NOT_SAVED) return false;
          notify('success', done);
          onDraft(clear); // a saved section drops its draft fields, so they show the saved values again
          onChanged();
          return true;
        },
        (err: unknown) => {
          // A stale version lock: the radar is read again with the fields kept, so the next Save works (r3 F3-013).
          if (err instanceof ApiError && err.status === 409) {
            notify('warning', 'Someone else changed this radar in the meantime. It was read again and your fields are kept: press Save again to put them on top.');
            onChanged();
          } else fail(err);
          return false;
        },
      )
      .finally(() => setSaving(null));
  };
  const saveMeta = () =>
    void (async () => {
      const newDays = dayCount.value as number;
      if (!autoAfter || (autoBefore && newDays >= r.auto_apply_days)) return runSaveMeta();
      // Read the proposals now: the page lists them only while the saved mode offers suggestions, so a
      // radar switched off and back to autopilot counted none of the ones it still holds.
      setSaving('meta');
      const listed = await api.suggestions(r.slug).then(
        (x) => x.items,
        (err: unknown) => {
          fail(err);
          return null;
        },
      );
      setSaving(null);
      if (!listed) return undefined;
      const already = new Set(autoBefore ? dueProposals(listed, r.auto_apply_days) : []);
      const items = dueProposals(listed, newDays).filter((x) => !already.has(x));
      if (items.length) return setDue({ items, days: newDays });
      return runSaveMeta();
    })();
  const runSaveMeta = () =>
    void saveWith(
      'meta',
      // Days are saved only for autopilot, where the field is shown and checked.
      () =>
        api.updateRadar(
          r.slug,
          {
            name: name.trim(),
            purpose: purpose.trim(),
            mode,
            ...(mode === 'autopilot' ? { auto_apply_days: dayCount.value as number, auto_apply_agent: autoAgent } : {}),
          },
          r.version_lock,
        ),
      'Saved.',
      { name: undefined, purpose: undefined, mode: undefined, days: undefined, auto_agent: undefined },
    );
  // Still in the list, the addresses the API named: only then does Save access confirm them.
  const unconfirmed = noAccount.filter((e) => editors.includes(e));
  const saveAccess = () =>
    void saveWith(
      'access',
      () =>
        api.setAccess(r.slug, editors, r.version_lock, undefined, unconfirmed.length > 0).then(
          (radar) => {
            setNoAccount([]);
            return radar;
          },
          (err: unknown) => {
            const named = noAccountOf(err);
            if (!named) throw err;
            setNoAccount(named);
            return NOT_SAVED;
          },
        ),
      'Access updated.',
      { editors: undefined },
    );
  // The saved editors go with the transfer: unsaved ones in the Access section stay a draft, for Save
  // access and its account check, rather than being saved on the side.
  const transfer = (owner: string) =>
    void saveWith('owner', () => api.setAccess(r.slug, r.editors, r.version_lock, owner), 'The radar has a new owner.', {}).then(
      (ok) => ok && setTransferring(false),
    );
  /** Emails that have an account, for administrators (only they may read the list). */
  const accounts = isAdmin && users.data ? new Set(users.data.items.filter((u) => u.enabled).map((u) => u.email.toLowerCase())) : null;
  // Whether the person has an account used to come out only when Save access failed, after the other
  // editors had been added too, with nothing saying which one was wrong.
  const unknownEditors = accounts ? editors.filter((e) => !accounts.has(e.toLowerCase())) : [];
  const addEditor = () => {
    if (!EMAIL.test(editor.trim())) return;
    onDraft({ editors: [...new Set([...editors, editor.trim().toLowerCase()])], editor: undefined });
  };
  const planSchema = () => {
    setChosen(new Set());
    setPlans({
      sector: planDimension(view.schema.sectors, sectors),
      ring: planDimension(view.schema.rings, rings),
      // A marker is optional on an entry, so "keep the entries without one" is the safe default.
      marker: planDimension(view.schema.markers, markers, true),
    });
  };
  const schemaPlan = plans ? plannedSchema(view, values, plans) : null;
  const clearLabel = heading(values.marker_label, view.schema.marker_label).toLowerCase();
  const saveSchema = (plan: SchemaPlan) =>
    beforeNewVersion('Save schema', () =>
      saveWith(
        'schema',
        async () => {
          // Clearing a marker is an entry change, not a schema change: those rows are saved first, so
          // the schema change that follows finds every entry matching. Both are versions in History.
          if (!plan.cleared.length) return api.updateSchema(r.slug, plan.schema, r.current_version, plan.renames, plan.dropped.length > 0);
          const what = `the ${clearLabel} on ${plural(plan.cleared.length, 'entry', 'entries')}`;
          const saved = await api.saveEntries(r.slug, plan.rows, r.current_version, `cleared ${what}`);
          try {
            return await api.updateSchema(r.slug, plan.schema, saved.current_version, plan.renames, plan.dropped.length > 0);
          } catch (err) {
            // Two writes: the first is a version already. Load it, so the page (and the next Save
            // schema) is not left on the version before it, and say exactly what was and was not saved.
            onChanged();
            throw new Error(
              `Cleared ${what} and saved that as version ${saved.current_version}, but the schema change was not saved: ${describeError(err)} The schema fields still hold your change: press Save schema to try again.`,
              { cause: err },
            );
          }
        },
        'Schema updated as a new version.',
        { sectors: undefined, rings: undefined, markers: undefined, sector_label: undefined, ring_label: undefined, marker_label: undefined },
      ),
    );
  /**
   * The options for one lost value: remove it, keep its entries without it, rename it to a new value,
   * or move its entries into a value the list keeps (r3 F3-029: merging two rings meant losing one's
   * entries). A target another lost value already goes to says it merges with it (F3-092).
   */
  const choiceOptions = (plan: DimensionPlan, clearable: boolean, value: string) => {
    const kept = plan.names.filter((n) => !plan.added.includes(n));
    const sharing = (to: string) =>
      plan.removed.filter((other) => other !== value && plan.choices[other]?.action === 'rename' && (plan.choices[other] as { to: string }).to === to);
    const merges = (to: string) => (sharing(to).length ? ` (merges with "${sharing(to).join('", "')}")` : '');
    return [
      { value: 'remove', label: 'Remove it, and its entries with it' },
      ...(clearable ? [{ value: 'clear', label: `Keep its entries without a ${clearLabel}` }] : []),
      ...plan.added.map((to) => ({ value: choiceValue({ action: 'rename', to }), label: `Rename it to "${to}"${merges(to)}` })),
      ...kept.map((to) => ({ value: choiceValue({ action: 'rename', to }), label: `Move its entries to "${to}"` })),
    ];
  };
  /** Which lost values the user has chosen for: the others still hold a guess, and say so (r3 F3-112). */
  const [chosen, setChosen] = React.useState<ReadonlySet<string>>(new Set());
  /** How many entries carry a value, so the choice is made knowing what it costs (r3 F3-093). */
  const carrying = (dim: 'sector' | 'ring' | 'marker', value: string) => view.entries.filter((e) => fold(e[dim] ?? '') === fold(value)).length;
  const saveButton = (section: keyof typeof dirty, label: string, onClick: () => void, invalid = '') => (
    <Button
      variant={dirty[section] ? 'primary' : 'normal'}
      loading={saving === section}
      // Every section saves with the version lock of the last load, so a second save before the
      // reload lands would send a stale one and get a conflict.
      disabled={!dirty[section] || Boolean(invalid) || (saving !== null && saving !== section)}
      disabledReason={invalid || (saving !== null ? 'Another section is being saved.' : 'Nothing changed yet.')}
      onClick={onClick}
    >
      {label}
    </Button>
  );
  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            description={dirty.meta ? 'Unsaved changes: press Save general to keep them.' : 'Name, purpose and how AI suggestions work on this radar.'}
            actions={saveButton(
              'meta',
              'Save general',
              saveMeta,
              !name.trim()
                ? 'Enter a name first.'
                : name.trim().length > NAME_MAX
                  ? `Shorten the name to ${NAME_MAX} characters.`
                  : purpose.trim().length > PURPOSE_MAX
                    ? `Shorten the purpose to ${PURPOSE_MAX} characters.`
                    : mode === 'autopilot' && dayCount.error
                      ? 'Fix the number of days first.'
                      : '',
            )}
          >
            General
          </Header>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Name"
            constraintText={`${name.trim().length} of ${NAME_MAX} characters.`}
            errorText={name.trim().length > NAME_MAX ? `At most ${NAME_MAX} characters.` : undefined}
          >
            <Input value={name} onChange={({ detail }) => onDraft({ name: detail.value })} />
          </FormField>
          <FormField
            label="Purpose"
            description="Shown under the radar's title, and read by the Radar curator."
            constraintText={`${purpose.trim().length} of ${PURPOSE_MAX} characters.`}
            errorText={purpose.trim().length > PURPOSE_MAX ? `At most ${PURPOSE_MAX} characters.` : undefined}
          >
            <Textarea value={purpose} onChange={({ detail }) => onDraft({ purpose: detail.value })} rows={2} />
          </FormField>
          <FormField
            label="AI suggestions"
            description={`${(RADAR_MODES.find((m) => m.value === mode) as (typeof RADAR_MODES)[number]).description} Suggestions come from the Radar curator module, which must be on under Modules.`}
            // Off does not discard them: they are simply not listed, and come back when it is on again.
            warningText={
              mode === 'manual' && r.mode !== 'manual' && suggestions.length > 0
                ? `${plural(suggestions.length, 'open proposal')} stay as they are while suggestions are off: they are not listed, and they come back when you switch them on again. Review or discard them first if you do not want that.`
                : undefined
            }
          >
            <Select
              expandToViewport
              selectedOption={{ value: mode, label: RADAR_MODES.find((m) => m.value === mode)?.label }}
              onChange={({ detail }) => onDraft({ mode: detail.selectedOption.value as RadarMode })}
              // Switching into autopilot while proposals apply without review starts unattended applies,
              // so the API takes an administrator for it (B3-009): the option says so instead of a 403.
              options={RADAR_MODES.map((m) =>
                m.value === 'autopilot' && armsAutopilot
                  ? { value: m.value, label: m.label, description: m.description, disabled: true, disabledReason: AUTOPILOT_ADMIN_ONLY }
                  : { value: m.value, label: m.label, description: m.description },
              )}
            />
          </FormField>
          {mode === 'autopilot' && (
            <FormField label="Apply after (days)" errorText={dayCount.error}>
              <Input type="number" value={days} onChange={({ detail }) => onDraft({ days: detail.value })} />
            </FormField>
          )}
          {mode === 'autopilot' && (
            <FormField
              label="Radar curator proposals"
              description={
                isAdmin
                  ? 'When on, proposals from the Radar curator module apply after the delay without anyone reviewing them, as long as the evidence they cite still exists. When off, autopilot applies none of them: they wait under Suggestions like in Copilot.'
                  : canArmAgent
                    ? 'An administrator switched this on. You can switch it off on your own radar, which sends proposals back to Suggestions for review; only an administrator can switch it on again.'
                    : 'Only an administrator can let autopilot apply the Radar curator\'s proposals without review. Until then they wait under Suggestions like in Copilot.'
              }
            >
              <Toggle checked={autoAgent} disabled={!canArmAgent} onChange={({ detail }) => onDraft({ auto_agent: detail.checked })}>
                Apply agent proposals without review
              </Toggle>
            </FormField>
          )}
        </SpaceBetween>
      </Container>
      <Container
        header={
          <Header
            variant="h2"
            description={
              dirty.access
                ? 'Unsaved changes: press Save access, or the people you added do not get access.'
                : `Editors can change entries and review suggestions. They need an ${appName()} account first: an administrator invites people under Administration > Users.`
            }
            actions={saveButton(
              'access',
              unconfirmed.length ? 'Add anyway' : 'Save access',
              saveAccess,
              unknownEditors.length ? `${unknownEditors.join(', ')} ${unknownEditors.length === 1 ? 'has' : 'have'} no account: remove them, or invite them first.` : '',
            )}
          >
            Access
          </Header>
        }
      >
        <SpaceBetween size="s">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              addEditor();
            }}
          >
            <SpaceBetween direction="horizontal" size="xs">
              <Input value={editor} onChange={({ detail }) => onDraft({ editor: detail.value })} placeholder="name@example.com" type="email" ariaLabel="Editor email" />
              <Button disabled={!EMAIL.test(editor.trim())}>Add</Button>
            </SpaceBetween>
          </form>
          <TokenGroup
            items={editors.map((e) => ({ label: e, dismissLabel: `Remove ${e}` }))}
            onDismiss={({ detail }) => onDraft({ editors: editors.filter((_, i) => i !== detail.itemIndex) })}
          />
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            {unconfirmed.length > 0 && (
              <Box fontSize="body-s" color="text-status-warning">
                {NO_ACCOUNT(unconfirmed)}
              </Box>
            )}
            {unknownEditors.length > 0 && (
            <Box fontSize="body-s" color="text-status-warning">
              {`${unknownEditors.join(', ')} ${unknownEditors.length === 1 ? 'has' : 'have'} no ${appName()} account yet. An administrator invites people under Administration > Users.`}
            </Box>
          )}
          <Box fontSize="body-s" color="text-body-secondary">Owner: {r.owner_email || r.owner}</Box>
            {/* A deleted owner's account id, with nothing saying so (r3 F3-025). */}
            {isAdmin && users.data && !users.data.items.some((u) => u.username === r.owner) && (
              <Box fontSize="body-s" color="text-status-warning">That account no longer exists: transfer the radar to someone.</Box>
            )}
            {isAdmin && (
              <Button variant="inline-link" onClick={() => setTransferring(true)}>
                Transfer ownership
              </Button>
            )}
          </SpaceBetween>
        </SpaceBetween>
      </Container>
      <Container
        header={
          <Header
            variant="h2"
            description="One value per line, in order (rings: inner first). Saving creates a new version. Renaming a value renames it on every entry; you are told first which entries would be removed because their value is gone."
            actions={saveButton(
              'schema',
              'Save schema',
              planSchema,
              schemaInvalid ? 'Fix the lists first.' : schemaUnchanged ? 'The schema is the same as the saved one.' : '',
            )}
          >
            Schema
          </Header>
        }
      >
        <SpaceBetween size="m">
          <ColumnLayout columns={3}>
            <FormField label="Sector heading" errorText={headingErrors.sector || undefined}><Input ariaLabel="Sector heading" value={values.sector_label} onChange={({ detail }) => onDraft({ sector_label: detail.value })} /></FormField>
            <FormField label="Ring heading" errorText={headingErrors.ring || undefined}><Input ariaLabel="Ring heading" value={values.ring_label} onChange={({ detail }) => onDraft({ ring_label: detail.value })} /></FormField>
            <FormField label="Marker heading" errorText={headingErrors.marker || undefined}><Input ariaLabel="Marker heading" value={values.marker_label} onChange={({ detail }) => onDraft({ marker_label: detail.value })} /></FormField>
          </ColumnLayout>
          <ColumnLayout columns={3}>
            <FormField label={view.schema.sector_label} errorText={listErrors.sectors}><Textarea value={sectors} onChange={({ detail }) => onDraft({ sectors: detail.value })} rows={6} /></FormField>
            <FormField label={view.schema.ring_label} errorText={listErrors.rings}><Textarea value={rings} onChange={({ detail }) => onDraft({ rings: detail.value })} rows={6} /></FormField>
            <FormField label={view.schema.marker_label} errorText={listErrors.markers}><Textarea value={markers} onChange={({ detail }) => onDraft({ markers: detail.value })} rows={6} /></FormField>
          </ColumnLayout>
        </SpaceBetween>
      </Container>
      <Container header={<Header variant="h2" description="Removes the radar and all its versions for everyone. This cannot be undone; export it first if you may need it.">Danger zone</Header>}>
        {/* The API refuses an owner who is not an administrator past 10 versions: said before, not after the confirm (r3 F3-059). */}
        <Button
          iconName="remove"
          disabled={!isAdmin && r.current_version > OWNER_DELETE_MAX_VERSIONS}
          disabledReason={`This radar has ${r.current_version} versions. Past ${OWNER_DELETE_MAX_VERSIONS}, only an administrator can delete a radar: ask one.`}
          onClick={() => setConfirmDelete(true)}
        >
          Delete this radar
        </Button>
      </Container>
      {plans && schemaPlan && (
        <Modal
          visible
          onDismiss={() => setPlans(null)}
          header="Save the schema as a new version?"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setPlans(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    saveSchema(schemaPlan);
                    setPlans(null);
                  }}
                >
                  {schemaPlan.dropped.length ? `Save and remove ${plural(schemaPlan.dropped.length, 'entry', 'entries')}` : 'Save schema'}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {DIMENSIONS.filter(({ key }) => plans[key].removed.length > 0).map(({ key, heading: field }) => (
              <FormField
                key={key}
                label={`${heading(values[field], view.schema[field])}: values that are gone`}
                description={
                  plans[key].added.length
                    ? 'Each one was guessed to be a rename only where a new value took its exact place. Change any of them.'
                    : 'Nothing was added to rename them to.'
                }
              >
                <SpaceBetween size="xs">
                  {plans[key].removed.map((value) => {
                    const options = choiceOptions(plans[key], key === 'marker', value);
                    const current = choiceValue(plans[key].choices[value] as DimensionChoice);
                    // Every choice a plan can hold is one of the options offered, so this always matches.
                    const picked = options.find((o) => o.value === current) as { value: string; label: string };
                    const guess = chosen.has(`${key}:${value}`) ? '' : ' (guessed: check it)';
                    return (
                      <Select
                        key={value}
                        expandToViewport
                        ariaLabel={`What happens to "${value}"`}
                        selectedOption={{ value: current, label: `"${value}" (${plural(carrying(key, value), 'entry', 'entries')}): ${picked.label}${guess}` }}
                        options={options}
                        onChange={({ detail }) => {
                          setChosen((c) => new Set([...c, `${key}:${value}`]));
                          setPlans({ ...plans, [key]: withChoice(plans[key], value, parseChoice(detail.selectedOption.value as string)) });
                        }}
                      />
                    );
                  })}
                </SpaceBetween>
              </FormField>
            ))}
            {schemaPlan.cleared.length > 0 && (
              <Box>
                {`${plural(schemaPlan.cleared.length, 'entry', 'entries')} ${schemaPlan.cleared.length === 1 ? 'keeps everything but its' : 'keep everything but their'} ${clearLabel}, saved as a version before the schema change: ${schemaPlan.cleared.map((e) => e.title).join(', ')}.`}
              </Box>
            )}
            {schemaPlan.dropped.length > 0 ? (
              <Alert type="warning" header={`${plural(schemaPlan.dropped.length, 'entry', 'entries')} would be removed`}>
                {`Their value is no longer in the schema: ${schemaPlan.dropped.map((e) => e.title).join(', ')}. To keep them, choose a rename above, or cancel and move them in Edit entries. Removed entries stay in the previous version under History.`}
              </Alert>
            ) : (
              <Box>Every entry still matches the new schema.</Box>
            )}
          </SpaceBetween>
        </Modal>
      )}
      {transferring && (
        <Modal
          visible
          onDismiss={() => setTransferring(false)}
          header="Transfer ownership"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setTransferring(false)}>Cancel</Button>
                <Button
                  variant="primary"
                  loading={saving === 'owner'}
                  disabled={!newOwner}
                  disabledReason="Choose the new owner first."
                  onClick={() => transfer((newOwner as { username: string }).username)}
                >
                  Transfer
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <FormField
            label="New owner"
            description="An enabled editor or administrator. They can change everything about this radar, including deleting it; you keep your own access only if you are also an editor of it."
            errorText={users.error ? `The users could not be loaded: ${users.error.message}` : undefined}
          >
            <Select
              expandToViewport
              ariaLabel="New owner"
              placeholder="Choose a person"
              statusType={users.error ? 'error' : users.loading ? 'loading' : 'finished'}
              loadingText="Loading users"
              selectedOption={newOwner ? { value: newOwner.username, label: newOwner.email } : null}
              onChange={({ detail }) => setNewOwner({ username: detail.selectedOption.value as string, email: detail.selectedOption.label as string })}
              options={candidates.map((u) => ({ value: u.username, label: u.email, description: roleLabel(u.role) }))}
            />
          </FormField>
        </Modal>
      )}
      {due && (
        <Modal
          visible
          onDismiss={() => setDue(null)}
          header={`Apply ${plural(due.items.length, 'proposal')} without review?`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setDue(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    setDue(null);
                    runSaveMeta();
                  }}
                >
                  {`Save and apply ${plural(due.items.length, 'proposal')}`}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="s">
            <Box>
              {`${plural(due.items.length, 'proposal')} from the Radar curator ${due.items.length === 1 ? 'is' : 'are'} already past the ${due.days}-day delay, so ${due.items.length === 1 ? 'it applies' : 'they apply'} at the next autopilot tick, minutes from now, with nobody reviewing ${due.items.length === 1 ? 'it' : 'them'}:`}
            </Box>
            <ul>
              {due.items.slice(0, 10).map((x) => (
                <li key={x.entry_key}>{x.proposal.title ?? x.base?.title ?? x.entry_key}</li>
              ))}
            </ul>
            {due.items.length > 10 && <Box>{`and ${due.items.length - 10} more.`}</Box>}
            <Box>Cancel to review them under Suggestions first.</Box>
          </SpaceBetween>
        </Modal>
      )}
      {confirmDelete && (
        <ConfirmDelete header="Delete radar" confirmText={r.slug} onDismiss={() => setConfirmDelete(false)} onConfirm={() => api.deleteRadar(r.slug).then(onDeleted, fail)}>
          {`Delete "${r.name}" and all its versions? This cannot be undone.`}
        </ConfirmDelete>
      )}
    </SpaceBetween>
  );
}
