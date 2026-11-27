// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert, { type AlertProps } from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Spinner from '@cloudscape-design/components/spinner';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import React from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { ApiError, shrinkOf, type Shrink } from '@/api/client';
import type { RadarEntry } from '@/api/types';
import { useSession } from '@/auth/session';
import { Crumbs, LoadError, PageLoading, ReloadFailed } from '@/components/ErrorState';
import NodeLink, { GraphLink } from '@/components/NodeLink';
import { useNotify } from '@/components/Notifications';
import UnsavedGuard from '@/components/UnsavedGuard';
import FavouriteToggle, { useFavourites } from '@/components/FavouriteToggle';
import { useAsync } from '@/hooks/useAsync';
import { actorLabel, ago, hostOf, plural, sentence, when } from '@/lib/format';
import { useLoadingPageTitle } from '@/lib/pageTitle';
import { isSafeHref } from '@/lib/html';
import EntryEditor, { changedRows, type Problem } from '@/radar/EntryEditor';
import { download, entriesToCsv, slugFile } from '@/radar/export';
import { Legend, matchingKeys, RadarChart } from '@/radar/RadarChart';
import { ConfirmShrink, HistoryTab, ImportExportTab, type SettingsDraft, settingsDirty, SettingsTab, SuggestionsTab } from '@/radar/RadarTabs';
import { type EntryField, ownChanges, type RebaseConflict, rebaseEntries } from '@/radar/rebase';
import type { EntryEvent, RadarDiffChanges } from '@/api/types';

/** Opens the linked record by its title once it is known. */
function LinkedRecord({ id }: { id: string }) {
  const { api } = useSession();
  const node = useAsync(() => api.node(id), [api, id]);
  // A 404 used to read "Open the linked record" as well, so the only way to find out the link was
  // broken was to follow it to a "not found" page.
  if (node.error instanceof ApiError && node.error.status === 404) {
    return <Box color="text-status-inactive" fontSize="body-s">Linked record not found: it may have been deleted.</Box>;
  }
  return <NodeLink node={{ id, title: node.data?.title ?? 'Open the linked record' }} showSource={false} />;
}

const FIELD_NAMES: Record<string, string> = {
  title: 'name', description: 'description', marker: 'marker', sub_sector: 'sub-sector', link: 'link', node_id: 'linked record',
};

/** One version's change to an entry, in words. */
export function eventText(e: EntryEvent): string {
  if (e.change === 'added') return `Added in ${e.sector}, ${e.ring}`;
  if (e.change === 'restored') return `Put back in ${e.sector}, ${e.ring}`;
  if (e.change === 'removed') return 'Removed';
  const parts: string[] = [];
  if (e.ring_from) parts.push(`moved from ${e.ring_from} to ${e.ring}`);
  if (e.sector_from) parts.push(`moved from ${e.sector_from} to ${e.sector}`);
  if (e.fields?.length) parts.push(`${e.fields.map((f) => FIELD_NAMES[f] ?? f).join(', ')} changed`);
  return sentence(parts.join('; '));
}

/** An entry's changes over the radar's newest versions, read when the section is opened (CTR HA-003). */
function EntryHistoryList({ slug, entryKey }: { slug: string; entryKey: string }) {
  const { api } = useSession();
  const history = useAsync(() => api.entryHistory(slug, entryKey), [api, slug, entryKey]);
  if (history.error) return <Box color="text-status-error" fontSize="body-s">{`The history could not be loaded: ${history.error.message}`}</Box>;
  if (!history.data) return <Spinner />;
  const h = history.data;
  return (
    <SpaceBetween size="xxs">
      {h.from_version > 1 && <Box fontSize="body-s" color="text-body-secondary">{`From version ${h.from_version} (the newest ${h.to_version - h.from_version + 1} versions).`}</Box>}
      <ul className="air-history">
        {[...h.events].reverse().map((e) => (
          <li key={e.version}>
            <Box fontSize="body-s">
              <b>{`Version ${e.version}`}</b>{` \u00b7 ${eventText(e)}`}
              {e.ai && <> <Badge color="blue">AI suggestion</Badge></>}
            </Box>
            <Box fontSize="body-s" color="text-body-secondary">
              {[e.by && actorLabel(e.by), e.at && ago(e.at)].filter(Boolean).join(' \u00b7 ')}
            </Box>
          </li>
        ))}
      </ul>
    </SpaceBetween>
  );
}

function EntryDetail({ slug, entry, onClose }: { slug: string; entry: RadarEntry; onClose: () => void }) {
  const heading = React.useRef<HTMLDivElement>(null);
  // The panel may open far from the legend row that opened it: bring it into view and move focus to it.
  // No aria-live: the panel also takes focus, and a screen reader would then read it twice.
  React.useEffect(() => {
    heading.current?.scrollIntoView?.({ block: 'nearest' });
    heading.current?.focus();
  }, [entry.key]);
  return (
    <div onKeyDown={(e) => e.key === 'Escape' && onClose()} role="region" aria-label={`Entry: ${entry.title}`}>
      <Container
        header={
          <Header variant="h2" actions={<Button variant="icon" iconName="close" ariaLabel="Close" onClick={onClose} />}>
            <div ref={heading} tabIndex={-1}>{entry.title}</div>
          </Header>
        }
      >
        <SpaceBetween size="xs">
          <Box>
            {[entry.sector, entry.sub_sector, entry.ring, entry.marker].filter(Boolean).join(' \u00b7 ')}
          </Box>
          {/* Line breaks typed in the editor are kept (r3 F3-078). */}
          {entry.description && <Box><div className="air-pre">{entry.description}</div></Box>}
          {entry.node_id && (
            <SpaceBetween direction="horizontal" size="s">
              <LinkedRecord id={entry.node_id} />
              <GraphLink id={entry.node_id} />
            </SpaceBetween>
          )}
          {entry.link && isSafeHref(entry.link) && (
            <a href={entry.link} target="_blank" rel="noopener noreferrer" title={entry.link}>{hostOf(entry.link)}</a>
          )}
          <ExpandableSection headerText="Entry history" variant="footer" key={entry.key}>
            <EntryHistoryList slug={slug} entryKey={entry.key} />
          </ExpandableSection>
        </SpaceBetween>
      </Container>
    </div>
  );
}

const TABS = ['view', 'edit', 'io', 'suggestions', 'history', 'settings'];

/** What re-applying the user's edits on a newer version did, shown until they dismiss it. */
interface Rebased {
  version: number;
  applied: number;
  /** Removals the newer version overruled. */
  held: number;
  conflicts: RebaseConflict[];
}

/** Unsaved entry rows, with the version (and its saved entries) they were last based on. */
interface EntryDraft {
  slug: string;
  version: number;
  base: RadarEntry[];
  rows: RadarEntry[];
  rebased: Rebased | null;
}

/**
 * The draft as it applies to the loaded version: on a newer one the user's changes are re-applied to
 * it. A draft never holds rows equal to its base: setRows drops it then (r3 F3-035).
 */
function currentDraft(d: EntryDraft, version: number, entries: RadarEntry[]): { rows: RadarEntry[]; rebased: Rebased | null } {
  if (d.version === version) return { rows: d.rows, rebased: d.rebased };
  const r = rebaseEntries(d.base, d.rows, entries);
  return { rows: r.rows, rebased: { version, applied: r.applied, held: r.held, conflicts: r.conflicts } };
}

/** One line per entry both sides changed, in the radar's own words. */
function conflictLine(c: RebaseConflict, label: (f: EntryField) => string): string {
  if (c.kind === 'removed_but_changed') return `${c.title}: you removed it, but the newer version changed it, so it is kept. Remove it again if it should go.`;
  if (c.kind === 'edited_but_removed') return `${c.title}: the newer version removed it, and your edited copy is kept. Remove it again if it should go.`;
  if (c.kind === 'same_title') return `${c.title}: you added it, and the radar already has an entry with this title. Rename or remove one of them.`;
  const fields = c.fields.map((f) => `${label(f.field)} (the newer version has "${f.theirs}")`).join(', ');
  return `${c.title}: you and the newer version both changed it. Your value is kept for ${fields}.`;
}

/** "Version 7: 2 added, 1 removed, 3 changed", from the diff, so the user can judge the other save (r3 F3-111). */
export function diffSummary(to: number, c: RadarDiffChanges): string {
  const changed = new Set([...c.moved, ...c.recategorised, ...c.marker_changed, ...c.edited].map((x) => x.key)).size;
  const parts = [
    c.added.length ? `${c.added.length} added` : '',
    c.removed.length ? `${c.removed.length} removed` : '',
    changed ? `${changed} changed` : '',
  ].filter(Boolean);
  return `Version ${to}: ${parts.length ? parts.join(', ') : 'no entry changes (settings or schema only)'}.`;
}

function ConflictSummary({ slug, from, to }: { slug: string; from: number; to: number }) {
  const { api } = useSession();
  const diff = useAsync(() => api.diff(slug, from, to), [api, slug, from, to]);
  if (!diff.data) return null;
  return <Box>{`What changed since your version ${from}. ${diffSummary(to, diff.data.changes)}`}</Box>;
}

/** The rows a duplicate-title refusal names, as editor problems on those rows (r3 F3-001). */
export function duplicateProblems(sent: RadarEntry[], rows: unknown): Problem[] {
  const numbers = Array.isArray(rows) ? rows.filter((r): r is number => typeof r === 'number') : [];
  return numbers
    .map((row) => ({ row, e: sent[row - 1] }))
    .filter((x): x is { row: number; e: RadarEntry } => Boolean(x.e))
    .map(({ row, e }) => ({
      key: e.key,
      row,
      field: 'title' as const,
      title: e.title.trim(),
      message: `${e.title.trim()}: another entry already has this title once letter case, spaces, quotes and invisible characters are ignored. Rename one of them.`,
    }));
}

/** Below this width the legend comes before the chart (styles.css .air-radar-layout). */
export const NARROW_WIDTH = 1100;

/** Whether the window is at most NARROW_WIDTH wide, following resizes. The window, not the container: it must
 * agree with the viewport media query in styles.css (hooks/useNarrow measures a container instead). */
function useNarrowWindow(): boolean {
  const [narrow, setNarrow] = React.useState(() => window.innerWidth <= NARROW_WIDTH);
  React.useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth <= NARROW_WIDTH);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return narrow;
}

/** How often an open radar with unsaved entry edits asks whether a newer version was saved (r3 F3-007). */
export const STALE_CHECK_MS = 120_000;

export default function RadarPage() {
  const { slug = '' } = useParams();
  // The legend filter (?q=) and the tab (?tab=) live in the address, so a view can be shared and survives reload.
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const setParam = (key: string, value: string, fallback = '') =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value && value !== fallback) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  const setQuery = (q: string) => setParam('q', q);
  const { api, me } = useSession();
  const { fail, notify } = useNotify();
  const narrow = useNarrowWindow();
  // A link can pin a version by tag (?tag=) or by number (?v=): the page shows it read-only (CTR HA-009).
  const pinTag = params.get('tag') ?? '';
  const pinVersion = Number(params.get('v')) || undefined;
  const view = useAsync(() => api.radar(slug, pinVersion, pinTag || undefined), [api, slug, pinVersion, pinTag]);
  const favourites = useFavourites();
  const loadedView = view.data;
  const pinned = Boolean((pinTag || pinVersion) && loadedView && loadedView.version !== loadedView.radar.current_version);
  // An old version cannot be edited, imported into or reviewed: those act on the current one.
  const v = React.useMemo(
    () => (loadedView && pinned ? { ...loadedView, can_edit: false, can_admin: false } : loadedView),
    [loadedView, pinned],
  );
  // History's versions are read once History is first opened, and kept across tab switches (r3 F3-064).
  const [historySeen, setHistorySeen] = React.useState(false);
  if (!historySeen && params.get('tab') === 'history') setHistorySeen(true);
  const versions = useAsync(() => (historySeen ? api.versions(slug) : Promise.resolve(null)), [api, slug, historySeen], [v?.radar.current_version]);
  // A role change makes the loaded can_edit and can_admin stale: read the radar again (r3 F3-107).
  const role = me?.role;
  const [seenRole, setSeenRole] = React.useState(role);
  if (role !== seenRole) {
    setSeenRole(role);
    view.reload();
  }
  // Autopilot, another editor or an accepted suggestion can move the radar on while this page is
  // open; the only sign used to be a save failing with a conflict.
  const [latest, setLatest] = React.useState<number | null>(null);
  const loaded = v?.radar.current_version;
  /** When the check last ran: focus and visibilitychange both fire on one tab switch (r3 F3-106). */
  const lastCheck = React.useRef(0);
  /** Reads the radar for its version only; a failed check says nothing (the next one may work). */
  const checkLatest = React.useCallback(() => {
    if (document.visibilityState !== 'visible' || Date.now() - lastCheck.current < 2000) return;
    lastCheck.current = Date.now();
    void api.radar(slug).then((fresh) => setLatest(fresh.radar.current_version), () => undefined);
  }, [api, slug]);
  React.useEffect(() => {
    if (loaded === undefined) return undefined;
    const check = checkLatest;
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
    return () => {
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', check);
    };
  }, [checkLatest, loaded]);
  const stale = !pinned && loaded !== undefined && latest !== null && latest > loaded;
  const suggestions = useAsync(() => (v && v.radar.mode !== 'manual' ? api.suggestions(slug) : Promise.resolve({ items: [] })), [api, slug, v?.radar.mode], [v?.radar.current_version]);
  // The key, not the entry: after a save or reload the panel shows the current copy (or closes if it is gone).
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  /** What opened the panel (a legend row's button or a blip's SVG group), so closing it goes back there. */
  const opener = React.useRef<HTMLOrSVGElement | null>(null);
  const setSelected = (entry: RadarEntry | null) => {
    if (entry) opener.current = document.activeElement as HTMLOrSVGElement | null;
    setSelectedKey(entry?.key ?? null);
  };
  const closePanel = () => {
    setSelectedKey(null);
    // Without this, Esc or the close button leaves focus on the page body and the user starts again.
    opener.current?.focus();
    opener.current = null;
  };
  // Esc closes the panel from anywhere on the page, not only while focus is inside it.
  React.useEffect(() => {
    if (!selectedKey) return undefined;
    const onKey = (e: KeyboardEvent) => {
      // Not an Esc a dialog, a dropdown or a field already used (closing it, clearing a filter): r3 F3-067.
      const target = e.target instanceof Element ? e.target : null;
      const handled = e.defaultPrevented || Boolean(target?.closest('input, textarea, select, [role="dialog"], [role="listbox"]'));
      if (e.key !== 'Escape' || handled) return;
      setSelectedKey(null);
      opener.current?.focus();
      opener.current = null;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedKey]);
  const [saving, setSaving] = React.useState(false);
  // Unsaved edits live here, not in the tabs: Cloudscape Tabs mounts only the active tab, so tab
  // state is lost on every switch. Entry rows remember the version they started from: when a newer
  // version is loaded (Load it, or after a save conflict) the user's own changes are re-applied on top
  // of it, so neither their edits nor the other editor's are lost. A save, or an action the user
  // accepted would replace the edits, drops the draft. Settings fields belong to the radar and each
  // section drops its own fields when it saves.
  const [entryDraft, setEntryDraft] = React.useState<EntryDraft | null>(null);
  const [settings, setSettings] = React.useState<{ slug: string; draft: SettingsDraft }>({ slug, draft: {} });
  /** A version-creating action waiting for the user to accept that unsaved entry edits are lost. */
  const [pending, setPending] = React.useState<{ label: string; run: () => Promise<boolean> } | null>(null);
  const [applying, setApplying] = React.useState(false);
  /** A save failed because someone else saved first (the version they saved, when the API said): the draft is kept, with ways to rescue it. */
  const [conflict, setConflict] = React.useState<{ actual: number | null } | null>(null);
  /** The API refused titles it folds onto another one: those rows, while the rows sent are still the draft. */
  const [dupes, setDupes] = React.useState<{ rows: RadarEntry[]; problems: Problem[] } | null>(null);
  /** A save the API refused because it removes most of the radar, with the rows to send again once confirmed. */
  const [shrinking, setShrinking] = React.useState<(Shrink & { rows: RadarEntry[] }) | null>(null);
  /** A saved version on its way: the draft stays (read-only) until it has loaded (r3 F3-008). */
  const [awaiting, setAwaiting] = React.useState<number | null>(null);
  if (awaiting !== null && v && v.version >= awaiting) {
    setAwaiting(null);
    setEntryDraft(null);
  }
  /** A conflict line was clicked: Edit entries filters to that row. */
  const [jump, setJump] = React.useState<{ key: string; title: string; n: number } | null>(null);
  const rebaseAlert = React.useRef<AlertProps.Ref>(null);
  const selected = v?.entries.find((e) => e.key === selectedKey) ?? null;
  const draft = React.useMemo(() => (v && entryDraft?.slug === slug ? currentDraft(entryDraft, v.version, v.entries) : null), [v, entryDraft, slug]);
  const rows = draft?.rows ?? null;
  const settingsDraft = settings.slug === slug ? settings.draft : {};
  const changes = React.useMemo(() => (v && rows ? changedRows(rows, v.entries) : 0), [rows, v]);
  // An editor who keeps the tab in front is told too, not only on focus (r3 F3-007).
  const drafting = changes > 0;
  React.useEffect(() => {
    if (!drafting) return undefined;
    const timer = setInterval(checkLatest, STALE_CHECK_MS);
    return () => clearInterval(timer);
  }, [checkLatest, drafting]);
  // The note about re-applied edits takes focus when it appears, so a screen reader hears it (r3 F3-038).
  const rebasedOn = draft?.rebased?.version ?? null;
  React.useEffect(() => {
    if (rebasedOn !== null) rebaseAlert.current?.focus();
  }, [rebasedOn]);
  // Once the radar is deleted there is nothing left to save: leave without asking.
  const [deleted, setDeleted] = React.useState(false);
  const navigate = useNavigate();
  useLoadingPageTitle(!v && !view.error, v?.radar.name, 'Radars');
  const dirty = !deleted && !!v && (changes > 0 || settingsDirty(v, settingsDraft));
  React.useEffect(() => {
    if (deleted) navigate('/radars');
  }, [deleted, navigate]);
  // Only a first load that failed replaces the page: a failed re-read keeps it, and the draft (r3 F3-002).
  if (view.error && !v) return <LoadError error={view.error} what="Radar" back={{ href: '/radars', label: 'Back to radars' }} onRetry={view.reload} />;
  if (!v) return <PageLoading />;
  const ids = TABS.filter((t) => (t !== 'edit' || v.can_edit) && (t !== 'settings' || v.can_admin));
  const tab = ids.includes(params.get('tab') ?? '') ? (params.get('tab') as string) : 'view';
  const setTab = (id: string) => setParam('tab', id, 'view');
  const waiting = suggestions.data?.items.length ?? 0;
  const rebased = draft?.rebased ? { ...draft.rebased, rows: draft.rows } : null;
  const own = ownChanges(v.entries, rows ?? v.entries);
  // Removals cannot travel in a CSV a Merge imports, so they go in a second file that lists them (r3 F3-012).
  const downloadMine = () => {
    download(slugFile(`${slug}-my-changes`, 'csv'), entriesToCsv(own.changed), 'text/csv');
    if (own.removed.length) {
      const list = own.removed.map((e) => e.title).join('\n');
      download(slugFile(`${slug}-my-removals`, 'txt'), `Entries removed but not saved. Remove them again after importing the CSV:\n${list}\n`, 'text/plain');
    }
  };
  const rescueLabel = own.removed.length ? 'Download my entry changes (CSV, and the list of removals)' : 'Download my entry changes as CSV';
  const labels: Partial<Record<EntryField, string>> = {
    sector: v.schema.sector_label,
    ring: v.schema.ring_label,
    marker: v.schema.marker_label,
    sub_sector: 'sub-sector',
    node_id: 'linked record',
  };
  const fieldLabel = (f: EntryField) => (labels[f] ?? f).toLowerCase();
  const patchSettings = (patch: SettingsDraft) =>
    setSettings((s) => ({ slug, draft: { ...(s.slug === slug ? s.draft : {}), ...patch } }));
  /**
   * Import, accept, restore and schema saves make a new version. Unsaved entry edits are kept and
   * re-applied on top of it, as Load it does (r3 F3-011): the dialog only says so first.
   */
  const beforeNewVersion = (label: string, run: () => Promise<boolean>) => (changes > 0 ? setPending({ label, run }) : void run());
  const saved = (version: number, message: string) => {
    notify('success', message);
    setAwaiting(version);
    view.reload();
  };
  /** A save that got no answer may have been stored: read the radar and compare before saying anything (r3 F3-014). */
  const settle = async (sent: RadarEntry[], err: ApiError) => {
    const before = v.radar.current_version;
    const fresh = await api.radar(slug).catch(() => null);
    if (!fresh) return fail(err);
    const now = fresh.radar.current_version;
    if (now > before && changedRows(sent, fresh.entries) === 0) return saved(now, 'Saved as a new version. The answer was lost on the way, but the new version is there.');
    if (now > before) {
      return fail(new Error(`The server did not answer, and the radar is now at version ${now}. It may be your save: check it under History before you press Save version again. Your edits are still here.`));
    }
    return fail(new Error('The server did not answer, and nothing was saved: your edits are still here. Press Save version to try again.'));
  };
  const save = async (entries: RadarEntry[], allowEmpty = false) => {
    setSaving(true);
    try {
      const radar = await api.saveEntries(slug, entries, v.radar.current_version, '', allowEmpty);
      saved(radar.current_version, 'Saved as a new version.');
    } catch (err) {
      const counts = shrinkOf(err, 'shrinking_save');
      // A 409 is a version conflict only when the API does not name another reason: a duplicate title
      // also answers 409, and "someone else saved first" then sent the user round in circles (r3 F3-001).
      const reason = err instanceof ApiError && err.status === 409 ? err.details.code : null;
      if (counts) setShrinking({ ...counts, rows: entries });
      else if (reason === 'duplicate_title') setDupes({ rows: entries, problems: duplicateProblems(entries, (err as ApiError).details.rows) });
      else if (err instanceof ApiError && err.status === 409 && reason === undefined) {
        const actual = err.details.actual;
        setConflict({ actual: typeof actual === 'number' ? actual : null });
      } else if (err instanceof ApiError && err.uncertain) await settle(entries, err);
      else fail(err);
    } finally {
      setSaving(false);
    }
  };
  const setRows = (next: RadarEntry[], note: Rebased | null = draft?.rebased ?? null) =>
    // Rows that equal the saved entries are no draft: Discard (or undoing every edit) also clears the rebase note (r3 F3-034).
    setEntryDraft(changedRows(next, v.entries) === 0 ? null : { slug, version: v.version, base: v.entries, rows: next, rebased: note });
  /** Takes the other editor's values for one entry both sides changed (r3 F3-010). A cleared optional value is left out. */
  const takeTheirs = (note: Rebased & { rows: RadarEntry[] }, c: RebaseConflict) => {
    const theirs = Object.fromEntries(c.fields.map((f) => [f.field, f.theirs === '' && f.field !== 'description' ? undefined : f.theirs]));
    const next = note.rows.map((r) => (r.key === c.key ? (JSON.parse(JSON.stringify({ ...r, ...theirs })) as RadarEntry) : r));
    setRows(next, { version: note.version, applied: note.applied, held: note.held, conflicts: note.conflicts.filter((x) => x.key !== c.key) });
  };
  const settingsChanged = settingsDirty(v, settingsDraft);
  const legend = (
    <div className="air-radar-legend">
      <Legend
        schema={v.schema}
        entries={v.entries}
        selected={selected?.key ?? null}
        onSelect={setSelected}
        query={query}
        onQueryChange={setQuery}
        onEditEntries={v.can_edit ? () => setTab('edit') : undefined}
      />
    </div>
  );
  return (
    <ContentLayout
      breadcrumbs={<Crumbs items={[{ text: 'Radars', href: '/radars' }, { text: v.radar.name, href: `/radars/${slug}` }]} />}
      header={
        <Header
          variant="h1"
          actions={<FavouriteToggle slug={slug} name={v.radar.name} favourites={favourites} />}
          description={
            <>
              {v.radar.purpose && <Box variant="span" display="block">{v.radar.purpose}</Box>}
              <Box variant="span" fontSize="body-s" color="text-body-secondary">
                Version {v.version}{' \u00b7 '}updated <span title={when(v.radar.updated_at)}>{ago(v.radar.updated_at)}</span>
              </Box>
            </>
          }
        >
          {v.radar.name}
        </Header>
      }
    >
      <SpaceBetween size="m">
      {view.error && <ReloadFailed error={view.error} what="radar" onRetry={view.reload} />}
      {/* Above the tabs, where an editor working in Edit entries sees it too (r3 F3-007). */}
      {pinned && v && (
        <Alert
          type="info"
          header={`Version ${v.version}${pinTag ? ` (${pinTag})` : ''}, not the current version ${v.radar.current_version}`}
          action={<Button onClick={() => setParams({}, { replace: false })}>Open the current version</Button>}
        >
          An older version is shown as it was saved, read-only. History lists every version and its tags.
        </Alert>
      )}
      {stale && (
        <Alert
          type="info"
          header={`Version ${latest as number} has been saved since this page was opened`}
          action={<Button onClick={() => { setLatest(null); view.reload(); }}>Load it</Button>}
        >
          {changes > 0
            ? `You are looking at version ${v.version}. Loading the new one keeps your ${plural(changes, 'unsaved change')} in Edit entries: they are re-applied on top of it for you to review.`
            : `You are looking at version ${v.version}.`}
        </Alert>
      )}
      {rebased && (
        <Alert
          ref={rebaseAlert}
          type={rebased.conflicts.length ? 'warning' : 'info'}
          header={`Your ${plural(rebased.applied, 'change')} ${rebased.applied === 1 ? 'was' : 'were'} re-applied on version ${rebased.version}${rebased.held ? `, and ${plural(rebased.held, 'removal')} held back` : ''}`}
          dismissible
          dismissAriaLabel="Dismiss"
          onDismiss={() => setEntryDraft({ slug, version: v.version, base: v.entries, rows: rebased.rows, rebased: null })}
          action={tab !== 'edit' ? <Button onClick={() => setTab('edit')}>Review in Edit entries</Button> : undefined}
        >
          <SpaceBetween size="xxs">
            <Box>
              The changes saved in the newer version are kept, and your own additions, edits and removals are applied on top of them. Nothing is saved
              until you press Save version.
            </Box>
            {rebased.conflicts.length > 0 && (
              <>
                <Box>{`${plural(rebased.conflicts.length, 'entry', 'entries')} changed on both sides: check ${rebased.conflicts.length === 1 ? 'it' : 'them'} before saving.`}</Box>
                <ul>
                  {rebased.conflicts.map((c) => (
                    <li key={c.key}>
                      {/* Each line brings its row on screen in Edit entries (r3 F3-009). */}
                      <button
                        type="button"
                        className="air-linkish"
                        onClick={() => {
                          setTab('edit');
                          setJump((j) => ({ key: c.key, title: c.title, n: (j?.n ?? 0) + 1 }));
                        }}
                      >
                        {conflictLine(c, fieldLabel)}
                      </button>
                      {c.kind === 'both_edited' && (
                        <>
                          {' '}
                          <Button variant="inline-link" onClick={() => takeTheirs(rebased, c)}>Use the newer version&apos;s values</Button>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </SpaceBetween>
        </Alert>
      )}
      <Tabs
        activeTabId={tab}
        onChange={({ detail }) => setTab(detail.activeTabId)}
        tabs={[
          {
            id: 'view',
            label: 'Radar',
            content: (
              <SpaceBetween size="m">
                {v.radar.mode === 'copilot' && waiting > 0 && (
                  <Alert type="info" action={<Button onClick={() => setTab('suggestions')}>Review</Button>}>
                    {`The Radar curator has ${plural(waiting, 'suggestion')} waiting for review.`}
                  </Alert>
                )}
                {/* A first-time owner used to see an empty circle and a filter box (r3 F3-065). */}
                {v.entries.length === 0 && (
                  <Alert
                    header="No entries yet"
                    action={
                      v.can_edit && (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button onClick={() => setTab('edit')}>Add entries</Button>
                          <Button onClick={() => setTab('io')}>Import a file</Button>
                        </SpaceBetween>
                      )
                    }
                  >
                    {v.can_edit ? 'Add entries one by one, or import a CSV or JSON file.' : 'This radar has no entries yet.'}
                  </Alert>
                )}
                {/* The chart gets most of the row where there is room; on a narrow screen the legend
                    comes first, in the markup as on screen, because the blip numbers are then too small
                    and the keyboard must go the way the eye does (r3 F3-021). */}
                <div className="air-radar-layout">
                  {narrow && legend}
                  <div className="air-radar-chart">
                    <SpaceBetween size="m">
                    <RadarChart schema={v.schema} entries={v.entries} selected={selected?.key ?? null} onSelect={setSelected} title={v.radar.name} visible={matchingKeys(v.entries, query)} skipLink={!narrow} />
                      {selected && <EntryDetail slug={slug} entry={selected} onClose={closePanel} />}
                    </SpaceBetween>
                  </div>
                  {!narrow && legend}
                </div>
              </SpaceBetween>
            ),
          },
          ...(v.can_edit
            ? [
                {
                  id: 'edit',
                  // A draft is marked from every tab, not only inside its own (r3 F3-066).
                  label: changes > 0 ? `Edit entries (${changes} unsaved)` : 'Edit entries',
                  content: (
                    <EntryEditor
                      key={v.version}
                      schema={v.schema}
                      entries={v.entries}
                      rows={rows ?? v.entries}
                      onRowsChange={setRows}
                      saving={saving || awaiting !== null}
                      onSave={(e) => void save(e)}
                      extraProblems={dupes && dupes.rows === rows ? dupes.problems : []}
                      conflictKeys={rebased?.conflicts.map((c) => c.key)}
                      jump={jump}
                      rebasedOn={rebased?.version ?? null}
                    />
                  ),
                },
              ]
            : []),
          ...(pinned
            ? []
            : [
          {
            id: 'io',
            label: v.can_edit ? 'Import and export' : 'Export',
            content: (
              <ImportExportTab
                view={v}
                onChanged={view.reload}
                beforeNewVersion={beforeNewVersion}
                onViewRadar={() => setTab('view')}
                onEditEntries={v.can_edit ? () => setTab('edit') : undefined}
                unsaved={changes}
              />
            ),
          },
          {
            id: 'suggestions',
            label: waiting > 0 ? `Suggestions (${waiting})` : 'Suggestions',
            content: <SuggestionsTab view={v} list={suggestions} onChanged={view.reload} onOpenSettings={() => setTab('settings')} beforeNewVersion={beforeNewVersion} />,
          },
              ]),
          { id: 'history', label: 'History', content: <HistoryTab view={v} versions={versions} onChanged={view.reload} beforeNewVersion={beforeNewVersion} /> },
          ...(v.can_admin
            ? [
                {
                  id: 'settings',
                  label: settingsChanged ? 'Settings (unsaved)' : 'Settings',
                  content: (
                    <SettingsTab
                      view={v}
                      suggestions={suggestions.data?.items ?? []}
                      onChanged={view.reload}
                      draft={settingsDraft}
                      onDraft={patchSettings}
                      beforeNewVersion={beforeNewVersion}
                      onDeleted={() => setDeleted(true)}
                    />
                  ),
                },
              ]
            : []),
        ]}
      />
      </SpaceBetween>
      {pending && (
      <Modal
        visible
        // Not while the action runs: it goes on anyway, so closing the dialog looked like cancelling it (r3 F3-068).
        onDismiss={() => !applying && setPending(null)}
        header="Your entry edits are not saved yet"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => {
                  setPending(null);
                  setTab('edit');
                }}
              >
                Go to Edit entries
              </Button>
              <Button
                variant="primary"
                loading={applying}
                onClick={() => {
                  const { run } = pending;
                  setApplying(true);
                  // The draft stays: the page re-applies it on the version the action made.
                  void run().finally(() => {
                    setApplying(false);
                    setPending(null);
                  });
                }}
              >
                {pending.label}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {`${pending.label} saves a new version. Your ${plural(changes, 'unsaved change')} in Edit entries ${changes === 1 ? 'is' : 'are'} kept and re-applied on top of it: entries changed on both sides are listed for you to check, and nothing of yours is saved until you press Save version.`}
      </Modal>
      )}
      <UnsavedGuard
        dirty={dirty}
        rescue={changes > 0 ? { label: rescueLabel, run: downloadMine } : undefined}
      >
        This radar has unsaved changes in Edit entries or Settings. They are lost if you leave the page now.
      </UnsavedGuard>
      {conflict && (
      <Modal
        visible
        onDismiss={() => setConflict(null)}
        header="Someone else saved this radar first"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setConflict(null)}>Keep editing</Button>
              <Button
                iconName="download"
                onClick={downloadMine}
              >
                Download my changes as CSV
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setConflict(null);
                  setTab('edit');
                  view.reload(); // the draft is then re-applied on the newer version
                }}
              >
                Load the newer version
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="xs">
          {conflict.actual !== null && conflict.actual > v.version && <ConflictSummary slug={slug} from={v.version} to={conflict.actual} />}
          <Box>
            {`Your ${plural(changes, 'change')} could not be saved: another editor, an accepted suggestion or autopilot saved a newer version while you were working, and saving on top of it would undo their work. Your edits are still here. Load the newer version to re-apply your additions, edits and removals on top of it: entries changed on both sides are listed for you to check, and nothing is saved until you press Save version.`}
          </Box>
          <Box color="text-body-secondary">
            {`The CSV holds only the ${plural(own.changed.length, 'entry', 'entries')} you added or edited. Imported in Merge mode, those rows replace the saved entries with the same title, so a change the other editor made to one of them is lost.${
              own.removed.length ? ` Entries you removed are not in the file; remove them again by hand: ${own.removed.map((e) => e.title).join(', ')}.` : ''
            }`}
          </Box>
        </SpaceBetween>
      </Modal>
      )}
      {shrinking && (
        <ConfirmShrink
          shrink={shrinking}
          doing="Saving"
          action="Save"
          busy={saving}
          onCancel={() => setShrinking(null)}
          onConfirm={() => void save(shrinking.rows, true).finally(() => setShrinking(null))}
        />
      )}
    </ContentLayout>
  );
}
