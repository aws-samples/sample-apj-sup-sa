// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import { useEffect, useRef, useState } from 'react';
import { ApiError, apiWrite, useApi } from '../api';
import { READ_ONLY_REASON, useApp, useWriteErrors } from '../context';
import type { GroupRow, Models } from '../types';
import { ErrorState } from '../components/common';
import { DataTable, Page } from './_kit';

const allows = (pats: string[], id: string) => pats.some(p => p === id || (p.endsWith('*') && id.startsWith(p.slice(0, -1))));

export default function ModelAccess() {
  const groups = useApi<{ groups: GroupRow[] }>('/api/v2/groups');
  const models = useApi<Models>('/api/v2/models');
  const { isAdmin, notify } = useApp();
  const onErr = useWriteErrors();
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  // Groups whose draft follows the server: all of them on first load, then only the one just saved (or reloaded after
  // a conflict), so saving one group keeps unsaved ticks in the others.
  const resync = useRef<Set<string> | 'all'>('all');
  useEffect(() => {
    if (!groups.data || !models.data) return;
    const fresh = (g: GroupRow) => models.data!.models.map(m => m.id).filter(id => allows(g.models_effective, id));
    const which = resync.current;
    setDraft(prev => Object.fromEntries(groups.data!.groups.map(g =>
      [g.group, which === 'all' || which.has(g.group) || !(g.group in prev) ? fresh(g) : prev[g.group]])));
    resync.current = new Set();
  }, [groups.data, models.data]);
  // Add to the pending set, never replace it: another group's resync (after a 409) must survive this save.
  const markResync = (group: string) => { if (resync.current !== 'all') resync.current.add(group); };
  // A stored wildcard (ow/*) stays a wildcard while every catalog model it covers is still ticked, so models added
  // later are still allowed; once one of them is unticked it is replaced by the explicit list. A pattern that matches
  // no catalog model (set ahead of a model joining, say) has no model checkbox: it is shown in "Other patterns" with its
  // own checkbox, kept unless the admin unticks it there, so a save for another tick never drops it unseen. Only
  // wildcards can be kept: the API accepts no explicit ID outside the catalog, so such an ID (a model that left the
  // catalog) is listed as removed on the next save instead of making every save for the group fail.
  const ids = (models.data?.models ?? []).map(m => m.id);
  const uncovered = (g: GroupRow) => g.models_effective.filter(p => !ids.some(id => allows([p], id)));
  const others = (g: GroupRow) => uncovered(g).filter(p => p.endsWith('*'));
  const retired = (g: GroupRow) => uncovered(g).filter(p => !p.endsWith('*'));
  const [dropped, setDropped] = useState<Record<string, string[]>>({});
  const toSave = (g: GroupRow) => {
    const picked = draft[g.group] ?? [];
    const kept = g.models_effective.filter(p => {
      const covered = ids.filter(id => allows([p], id));
      return p.endsWith('*') && covered.length > 0 && covered.every(id => picked.includes(id));
    });
    const other = others(g).filter(p => !(dropped[g.group] ?? []).includes(p));
    return [...other, ...kept, ...picked.filter(id => !allows(kept, id))];
  };
  // Groups with a save in flight: a second click would PUT the same version and get a 409 for the admin's own edit.
  // The ref blocks a double click before React re-renders; the state shows the button as loading.
  const inFlight = useRef(new Set<string>());
  const [saving, setSaving] = useState<string[]>([]);
  // The version comes from the settled list only: while the post-save reload runs, the kept rows carry the old one.
  // Saving needs the catalog and this group's draft: without them toSave() would drop every explicit model.
  const save = async (row: GroupRow) => {
    const g = groups.settled?.groups.find(x => x.group === row.group);
    if (!g || !models.settled || draft[g.group] === undefined || inFlight.current.has(g.group)) return;
    inFlight.current.add(g.group);
    setSaving([...inFlight.current]);
    try {
      await apiWrite('PUT', `/api/v2/policy/groups/${encodeURIComponent(g.group)}`, { models: toSave(g), version: g.version });
      notify({ type: 'success', header: `Model access for ${g.group} saved`, content: 'The gateway applies it within a minute.' });
      markResync(g.group);
      setDropped(d => { const n = { ...d }; delete n[g.group]; return n; });
      groups.reload();
    } catch (e) {
      // Only a conflict reloads (and so resyncs) the group; any other error keeps the admin's draft for a retry.
      if (e instanceof ApiError && e.status === 409) {
        markResync(g.group);
        setDropped(d => { const n = { ...d }; delete n[g.group]; return n; });  // follow the reload, like the ticks
      }
      onErr(e, `Saving model access for ${g.group}`, groups.reload, []); }
    finally { inFlight.current.delete(g.group); setSaving([...inFlight.current]); }
  };
  const ms = models.data?.models ?? [];
  return (
    <Page title="Model access" description="Which models each IdP group may call. Saving a group stores its list in DynamoDB, replacing the deployment default for that group.">
      {models.error && <ErrorState error={models.error} onRetry={models.reload} what="the model catalog (needed to save model access)" />}
      <DataTable<GroupRow> title="Groups x models" items={groups.data?.groups ?? []} load={groups} columns={[
        { id: 'g', header: 'Group', cell: g => <>{g.group}<br /><small>{g.models_source === 'env' ? 'deployment default' : 'set in dashboard'}</small></> },
        ...ms.map(m => ({ id: m.id, header: m.display_name, cell: (g: GroupRow) => (
          <Checkbox disabled={!isAdmin || g.editable === false} checked={(draft[g.group] ?? []).includes(m.id)}
            onChange={e => { const on = e.detail.checked; setDraft(d => ({ ...d, [g.group]: on ? [...(d[g.group] ?? []).filter(x => x !== m.id), m.id] : (d[g.group] ?? []).filter(x => x !== m.id) })); }} />) })),
        { id: 'o', header: 'Other patterns', cell: (g: GroupRow) => others(g).length + retired(g).length === 0 ? '-' : <>{retired(g).length > 0 && <div><small>Not in the catalog, removed on save: {retired(g).join(', ')}</small></div>}{others(g).map(pat => (
          <Checkbox key={pat} disabled={!isAdmin || g.editable === false} checked={!(dropped[g.group] ?? []).includes(pat)}
            description="matches no listed model yet"
            onChange={e => { const keep = e.detail.checked; setDropped(d => ({ ...d, [g.group]: keep ? (d[g.group] ?? []).filter(x => x !== pat) : [...(d[g.group] ?? []), pat] })); }}>{pat}</Checkbox>))}</> },
        { id: 's', header: 'Save', cell: g => <Button variant="inline-link" loading={saving.includes(g.group)} disabled={!isAdmin || g.editable === false || !groups.settled || !models.settled || draft[g.group] === undefined} disabledReason={!isAdmin ? READ_ONLY_REASON : g.editable === false ? 'Users without groups get the organization policy; edit that instead.' : 'Loading the current settings'} onClick={() => save(g)}>Save</Button> },
      ]} />
    </Page>
  );
}
