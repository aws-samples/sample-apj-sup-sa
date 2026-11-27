// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useApi } from '../api';
import { usd } from '../format';
import type { GroupRow } from '../types';
import { DataTable, Page } from './_kit';

export default function Groups() {
  const r = useApi<{ groups: GroupRow[] }>('/api/v2/groups');
  return (
    <Page title="Groups" description="IdP groups seen in tokens this month, their spend, limits and models.">
      <DataTable<GroupRow> title="Groups" items={r.data?.groups ?? []} load={r} filterText={g => g.group} columns={[
        { id: 'g', header: 'Group', cell: g => g.group }, { id: 'm', header: 'Members seen', cell: g => g.members_seen.join(', ') || '-' },
        { id: 'u', header: 'Spend (MTD)', cell: g => usd(g.mtd.usd) }, { id: 'q', header: 'Requests', cell: g => g.mtd.requests },
        { id: 'p', header: 'Limits set', cell: g => Object.entries(g.policy).filter(([k]) => k !== 'models').map(([k, v]) => `${k}=${v}`).join(', ') || '-' },
        { id: 'md', header: 'Models', cell: g => g.models_effective.join(', ') || 'none' },
      ]} />
    </Page>
  );
}
