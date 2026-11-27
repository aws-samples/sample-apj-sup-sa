// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Button from '@cloudscape-design/components/button';
import Link from '@cloudscape-design/components/link';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../api';
import { RepairButton } from '../components/actions';
import { BoostModal, canBoost } from '../components/BoostModal';
import { SourceBadge, StateIndicator } from '../components/common';
import { EditLimitsModal } from '../components/EditLimitsModal';
import { READ_ONLY_REASON, useApp } from '../context';
import { downloadCsv } from '../csv';
import { ago, int, usd } from '../format';
import type { UserRow } from '../types';
import { DataTable, Page } from './_kit';

export default function Users() {
  const r = useApi<{ users: UserRow[] }>('/api/v2/users');
  const { isAdmin } = useApp();
  const nav = useNavigate();
  const [edit, setEdit] = useState<string | null>(null);
  const [boost, setBoost] = useState<string | null>(null);
  const users = r.data?.users ?? [];
  const lim = (u: UserRow, p: string) => u.limits.find(l => l.period === p);
  const bar = (u: UserRow, p: string) => {
    const l = lim(u, p);
    if (l?.unreadable) return <SpaceBetween size="xxs"><StatusIndicator type={l.cap === null ? 'warning' : 'error'}>
      {l.cap === null ? 'Usage record cannot be read' : 'Usage record cannot be read: calls refused'}</StatusIndicator>
      <RepairButton user={u.user} period={l.period} capped={l.cap !== null} removed={u.removed} onDone={r.reload} /></SpaceBetween>;
    if (!l || l.cap === null) return <span>{usd(l?.spent)} <SourceBadge source={null} /></span>;
    return <ProgressBar value={Math.min(100, l.pct ?? 0)} status={l.state === 'blocked' ? 'error' : 'in-progress'}
      additionalInfo={<>{usd(l.spent)} of {usd(l.cap)} <SourceBadge source={l.source} /></>} />;
  };
  return (
    <Page title="Users" description="Spend and limits per person this month. Limits resolve: user override, then the most generous group, then org policy, then the default.">
      <DataTable<UserRow> title="Users" items={users} load={r} filterText={u => `${u.user} ${u.groups.join(' ')} ${u.state}`}
        actions={<Button iconName="download" onClick={() => downloadCsv('users.csv', ['user', 'groups', 'mtd_usd', 'today_usd', 'requests', 'state'], users.map(u => [u.user, u.groups.join(';'), u.mtd.usd, u.today_usd, u.mtd.requests, u.state]))}>Export CSV</Button>}
        columns={[
          { id: 'user', header: 'User', cell: u => <><Link onFollow={e => { e.preventDefault(); nav(`/users/${encodeURIComponent(u.user)}`); }} href={`#/users/${encodeURIComponent(u.user)}`}>{u.user}</Link>{u.removed ? ' (removed)' : ''}</>, sortingField: 'user' },
          { id: 'groups', header: 'Groups', cell: u => u.groups.join(', ') || '-' },
          { id: 'state', header: 'State', cell: u => <StateIndicator state={u.state} />, minWidth: 120 },
          { id: 'monthly', header: 'Monthly', cell: u => bar(u, 'Monthly'), minWidth: 200 },
          { id: 'daily', header: 'Daily', cell: u => bar(u, 'Daily'), minWidth: 200 },
          { id: 'hourly', header: 'Hourly', cell: u => bar(u, 'Hourly'), minWidth: 180 },
          { id: 'req', header: 'Requests (MTD)', cell: u => int(u.mtd.requests) },
          { id: 'rpm', header: 'RPM limit', cell: u => u.rpm.value ?? '-' },
          { id: 'seen', header: 'Last seen', cell: u => ago(u.last_seen) },
          { id: 'act', header: 'Actions', cell: u => (
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="inline-link" disabled={!isAdmin || !!u.removed} disabledReason={!isAdmin ? READ_ONLY_REASON : 'Removed from the deployment\'s users: add them back in Terraform to edit'} onClick={() => setEdit(u.user)}>Edit limits</Button>
              <Button variant="inline-link" disabled={!isAdmin || !!u.removed || !canBoost(u.limits)} disabledReason={!isAdmin ? READ_ONLY_REASON : u.removed ? 'Removed from the deployment\'s users: add them back in Terraform to edit' : 'No period has a cap above $0 to boost'} onClick={() => setBoost(u.user)}>Grant boost</Button>
            </SpaceBetween>) },
        ]} />
      <EditLimitsModal user={edit} onClose={c => { setEdit(null); if (c) r.reload(); }} />
      <BoostModal user={boost} onClose={c => { setBoost(null); if (c) r.reload(); }} />
    </Page>
  );
}
