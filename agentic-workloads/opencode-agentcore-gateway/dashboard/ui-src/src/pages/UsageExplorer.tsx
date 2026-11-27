// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { qs, useApi } from '../api';
import { useApp } from '../context';
import { int, usd } from '../format';
import type { Usage } from '../types';
import { DataTable, Page } from './_kit';

// Default range: this UTC month to date, worked out on each render so a tab left open past midnight moves with the day.
const todayUtc = () => new Date().toISOString().slice(0, 10);
const opt = (v: string) => ({ value: v, label: v ? v[0].toUpperCase() + v.slice(1) : v });
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// A text filter that applies on Enter or blur, not on every keystroke (a half-typed date was a 400 per key).
function DraftInput({ value, onCommit, placeholder, valid }: { value: string; onCommit: (v: string) => void; placeholder?: string; valid?: (v: string) => boolean }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const ok = !valid || draft === '' || valid(draft);
  const commit = () => { if (ok && draft !== value) onCommit(draft.trim()); };
  return <Input value={draft} invalid={!ok} placeholder={placeholder} onChange={e => setDraft(e.detail.value)} onBlur={commit}
    onKeyDown={e => { if (e.detail.key === 'Enter') commit(); }} />;
}
export default function UsageExplorer() {
  const [sp, setSp] = useSearchParams();
  // An empty parameter (#/usage?group_by=) means the default, as a missing one does.
  const v = (k: string, d = '') => sp.get(k) || d;
  const set = (k: string, val: string) => { const n = new URLSearchParams(sp); if (val) n.set(k, val); else n.delete(k); setSp(n); };
  const today = todayUtc();
  const params = { from: v('from', today.slice(0, 8) + '01'), to: v('to', today), granularity: v('granularity', 'day'), group_by: v('group_by', 'model'),
    user: v('user'), group: v('group'), model: v('model'), tag: v('tag'), dim: v('dim') };
  const r = useApi<Usage>('/api/v2/usage' + qs(params));
  // The tag dimensions come from /me, so the controls stay usable when a usage query fails (a stale ?dim=, say).
  // Unknown (undefined) until /me or a usage answer arrives: the tag controls are then neither hidden nor offered for
  // clearing, so a deep link with tag settings is not undone while the page loads.
  const known = useApp().me?.tag_dims ?? r.data?.tag_dims;
  const dims = known ?? [];
  const tagsOff = known !== undefined && known.length === 0;
  const tagParamsSet = !!(params.dim || params.tag || params.group_by === 'tag');
  const badDim = !!params.dim && known !== undefined && !known.includes(params.dim);  // e.g. a bookmark from before a change
  const clearTags = () => { const n = new URLSearchParams(sp); n.delete('dim'); n.delete('tag'); if (n.get('group_by') === 'tag') n.delete('group_by'); setSp(n); };
  return (
    <Page title="Usage explorer" description="Spend and tokens by model, user, group or tag for any period. Filters combine with AND. Spend is an estimate metered by the gateway.">
      <SpaceBetween size="l">
        <SpaceBetween direction="horizontal" size="s">
          <FormField label="From"><DraftInput value={params.from} onCommit={x => set('from', x)} placeholder="YYYY-MM-DD" valid={x => DATE.test(x)} /></FormField>
          <FormField label="To"><DraftInput value={params.to} onCommit={x => set('to', x)} placeholder="YYYY-MM-DD" valid={x => DATE.test(x)} /></FormField>
          <FormField label="Granularity"><Select selectedOption={opt(params.granularity)} options={['day', 'month'].map(opt)} onChange={e => set('granularity', e.detail.selectedOption.value!)} /></FormField>
          <FormField label="Group by"><Select selectedOption={opt(params.group_by)} options={(tagsOff ? ['model', 'user', 'group'] : ['model', 'user', 'group', 'tag']).map(opt)} onChange={e => set('group_by', e.detail.selectedOption.value!)} /></FormField>
          <FormField label="User"><DraftInput value={params.user} onCommit={x => set('user', x)} placeholder="any" /></FormField>
          <FormField label="Group"><DraftInput value={params.group} onCommit={x => set('group', x)} placeholder="any" /></FormField>
          <FormField label="Model"><DraftInput value={params.model} onCommit={x => set('model', x)} placeholder="any, e.g. kimi-k3 or ow/kimi-k3" /></FormField>
          {!tagsOff && dims.length > 0 && (
            <FormField label="Tag dimension" description="for Group by: Tag"
              errorText={badDim ? `${params.dim} is not a tag dimension here; pick one of ${dims.join(', ')}` : undefined}>
              {/* The value shown is the one in the URL, so a stale dim is visible (as invalid) rather than masked. */}
              <Select selectedOption={badDim ? { value: params.dim, label: params.dim } : opt(params.dim || (r.data?.dim ?? dims[0]))}
                invalid={badDim} options={dims.map(opt)} onChange={e => set('dim', e.detail.selectedOption.value!)} />
            </FormField>
          )}
          {!tagsOff && <FormField label="Tag"><DraftInput value={params.tag} onCommit={x => set('tag', x)} placeholder="any, e.g. team=payments" /></FormField>}
          {tagsOff && tagParamsSet && (
            <FormField label="Tags are off for this gateway"><Button onClick={clearTags}>Clear tag settings</Button></FormField>
          )}
        </SpaceBetween>
        {/* settled, not data: the notes and totals describe this query, never the last one while it loads or fails */}
        {r.settled?.notes.map(n => <Alert key={n} type="info">{n}</Alert>)}
        <DataTable title={r.settled ? `Totals: ${usd(r.settled.total.usd)}, ${int(r.settled.total.requests)} requests` : 'Totals'} items={r.data?.table ?? []} load={r} columns={[
          { id: 'k', header: params.group_by[0].toUpperCase() + params.group_by.slice(1), cell: x => x.key }, { id: 'u', header: 'Spend', cell: x => usd(x.usd) },
          { id: 'q', header: 'Requests', cell: x => int(x.requests) }, { id: 'i', header: 'Input', cell: x => int(x.input) }, { id: 'o', header: 'Output', cell: x => int(x.output) }]} />
      </SpaceBetween>
    </Page>
  );
}
