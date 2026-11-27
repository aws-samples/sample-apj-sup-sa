// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useApi } from '../api';
import { BoostModal, canBoost } from '../components/BoostModal';
import { ErrorState, SourceBadge, StateIndicator } from '../components/common';
import { EndBoostButton, RepairButton } from '../components/actions';
import { EditLimitsModal } from '../components/EditLimitsModal';
import { READ_ONLY_REASON, useApp } from '../context';
import { int, pct, usd, utc } from '../format';
import type { Limit, UserDetail as UD } from '../types';
import { DataTable, Page } from './_kit';

export default function UserDetail() {
  const { user = '' } = useParams();
  const r = useApi<UD>(`/api/v2/users/${encodeURIComponent(user)}`);
  const { isAdmin } = useApp();
  const [edit, setEdit] = useState(false);
  const [boost, setBoost] = useState(false);
  if (r.error) return <ErrorState error={r.error} onRetry={r.reload} />;
  // Only this user's answer: after navigating from another user the kept copy is theirs, and its boost button would
  // act on this user.
  const d = r.data?.user === user ? r.data : null;
  const version = r.settled?.user === user ? r.settled.version : undefined;  // the limits version this page loaded
  const endBoost = <EndBoostButton user={user} version={version} onDone={r.reload} />;
  return (
    <Page title={user} description={d ? `Groups: ${d.groups.join(', ') || 'none seen'}${d.removed ? ' (removed from the deployment\'s users)' : ''}` : undefined}
      actions={<SpaceBetween direction="horizontal" size="xs">
        <Button disabled={!isAdmin || !!d?.removed || !canBoost(d?.limits)} disabledReason={!isAdmin ? READ_ONLY_REASON : d?.removed ? 'Removed from the deployment\'s users: add them back in Terraform to edit' : 'No period has a cap above $0 to boost'} onClick={() => setBoost(true)}>Grant boost</Button>
        <Button variant="primary" disabled={!isAdmin || !!d?.removed} disabledReason={!isAdmin ? READ_ONLY_REASON : 'Removed from the deployment\'s users: add them back in Terraform to edit'} onClick={() => setEdit(true)}>Edit limits</Button>
      </SpaceBetween>}>
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Summary</Header>}>
          <KeyValuePairs columns={4} items={[
            { label: 'Spend this month', value: usd(d?.limits.find(l => l.period === 'Monthly')?.spent) },
            { label: 'RPM limit', value: d?.rpm.value ?? 'none' },
            { label: 'Active boost', value: d?.boost ? <>{`${usd(d.boost.usd)} ${d.boost.period} until ${utc(d.boost.until)} (${d.boost.reason})${d.boost.in_effect === false ? ', held back: that period has no cap or a $0 cap (it applies again if a cap above $0 is set before it ends)' : ''} `}{endBoost}</>
              : d?.boost_state === 'expired' ? <>{'none in effect; an expired boost is still recorded '}{endBoost}</>
              : d?.boost_state === 'unreadable' ? <>{'none in effect; the item holds boost values that cannot be read '}{endBoost}</> : 'none' },
            { label: 'Models allowed', value: d?.models.join(', ') || 'none' },
          ]} />
        </Container>
        <Tabs tabs={[
          { id: 'limits', label: 'Limits', content: <DataTable<Limit> title="Effective limits" items={d?.limits ?? []} load={r} columns={[
            { id: 'p', header: 'Period', cell: l => l.period }, { id: 'c', header: 'Cap', cell: l => (l.cap === null ? 'none' : usd(l.cap)) },
            { id: 's', header: 'Source', cell: l => <SourceBadge source={l.source} /> }, { id: 'b', header: 'Boost', cell: l => (l.boost ? usd(l.boost) : '-') },
            { id: 'u', header: 'Spent', cell: l => usd(l.spent) }, { id: 'pc', header: 'Used', cell: l => pct(l.pct) },
            { id: 'st', header: 'State', cell: l => l.unreadable ? <>{l.cap === null ? 'Usage record cannot be read ' : 'Usage record cannot be read: calls refused '}<RepairButton user={user} period={l.period} capped={l.cap !== null} removed={!!d?.removed} onDone={r.reload} /></> : <StateIndicator state={l.state} /> }, { id: 'r', header: 'Resets (UTC)', cell: l => utc(l.resets_at) }]} /> },
          { id: 'models', label: 'Usage by model', content: <DataTable title="This month by model" items={d?.by_model_mtd ?? []} load={r} columns={[
            { id: 'm', header: 'Model', cell: m => m.model }, { id: 'u', header: 'Spend', cell: m => usd(m.usd) }, { id: 'q', header: 'Requests', cell: m => int(m.requests) },
            { id: 'i', header: 'Input', cell: m => int(m.input) }, { id: 'o', header: 'Output', cell: m => int(m.output) }]} /> },
          { id: 'daily', label: 'Last 30 days', content: <DataTable title="Daily spend" items={d?.daily ?? []} load={r} columns={[
            { id: 'd', header: 'Date', cell: x => x.date }, { id: 'u', header: 'Spend', cell: x => usd(x.usd) }, { id: 'q', header: 'Requests', cell: x => int(x.requests) }]} /> },
          { id: 'audit', label: 'Activity', content: <DataTable title="Changes" items={d?.audit ?? []} load={r} empty="No changes yet" columns={[
            { id: 'a', header: 'When (UTC)', cell: x => utc(x.at) }, { id: 'w', header: 'Who', cell: x => x.actor }, { id: 'ac', header: 'Action', cell: x => x.action },
            { id: 'af', header: 'After', cell: x => JSON.stringify(x.after) }, { id: 'r', header: 'Reason', cell: x => x.reason || '-' }]} /> },
        ]} />
      </SpaceBetween>
      <EditLimitsModal user={edit ? user : null} onClose={c => { setEdit(false); if (c) r.reload(); }} />
      <BoostModal user={boost ? user : null} version={version} limits={r.settled?.user === user ? r.settled.limits : undefined} onClose={c => { setBoost(false); if (c) r.reload(); }} />
    </Page>
  );
}
