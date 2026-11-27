// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { qs, useApi } from '../api';
import { utc } from '../format';
import type { AuditRow } from '../types';
import { DataTable, Page } from './_kit';

const FILTERS = [
  ['target', 'Target', 'user:alice, group:eng, org'],
  ['actor', 'Who', 'admin email'],
  ['from', 'From (UTC)', 'YYYY-MM-DD'],
  ['to', 'To (UTC)', 'YYYY-MM-DD'],
] as const;
type Key = (typeof FILTERS)[number][0];

// The API returns the newest 500 matching rows, so the filters go to the server (an older change is still found);
// the table's own Find box then narrows what was returned.
export default function AuditLog() {
  const [sp, setSp] = useSearchParams();
  const applied = Object.fromEntries(FILTERS.map(([k]) => [k, sp.get(k) ?? ''])) as Record<Key, string>;
  const [draft, setDraft] = useState(applied);
  const key = FILTERS.map(([k]) => applied[k]).join('\u0000');
  useEffect(() => setDraft(applied), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const r = useApi<{ rows: AuditRow[]; complete?: boolean }>('/api/v2/audit' + qs(applied));
  const search = () => {
    const n = new URLSearchParams();
    for (const [k] of FILTERS) if (draft[k].trim()) n.set(k, draft[k].trim());
    setSp(n);
  };
  return (
    <Page title="Audit log" description="Every change made from this dashboard: who, what, before, after and why. Kept 365 days.">
      <SpaceBetween size="l">
        <SpaceBetween direction="horizontal" size="s" alignItems="end">
          {FILTERS.map(([k, label, ph]) => (
            <FormField key={k} label={label} errorText={r.error?.field === k ? r.error.message : undefined}>
              <Input value={draft[k]} placeholder={ph} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.detail.value }))}
                onKeyDown={(e) => { if (e.detail.key === 'Enter') search(); }} />
            </FormField>
          ))}
          <Button onClick={search}>Search</Button>
        </SpaceBetween>
        {r.data?.complete === false && (
          <Alert type="info">The search stopped after the newest 20,000 changes{applied.from || applied.to ? ' in this range. Narrow From and To' : '. Set From and To'} to search further back.</Alert>
        )}
        <DataTable<AuditRow> title="Changes" items={r.data?.rows ?? []} load={r} empty="No matching changes" filterText={x => `${x.actor} ${x.action} ${x.target}`} columns={[
          { id: 'at', header: 'When (UTC)', cell: x => utc(x.at) }, { id: 'w', header: 'Who', cell: x => x.actor }, { id: 'a', header: 'Action', cell: x => x.action },
          { id: 't', header: 'Target', cell: x => x.target }, { id: 'b', header: 'Before', cell: x => <code>{JSON.stringify(x.before)}</code> },
          { id: 'af', header: 'After', cell: x => <code>{JSON.stringify(x.after)}</code> }, { id: 'r', header: 'Reason', cell: x => x.reason || '-' },
        ]} />
      </SpaceBetween>
    </Page>
  );
}
