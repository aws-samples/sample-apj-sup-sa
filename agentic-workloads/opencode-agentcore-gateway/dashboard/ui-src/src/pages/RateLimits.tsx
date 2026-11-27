// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useApi } from '../api';
import type { RateLimits as RL } from '../types';
import { DataTable, Page } from './_kit';

export default function RateLimits() {
  const r = useApi<RL>('/api/v2/ratelimits');
  const i = r.data?.interceptor;
  // The deployment default (Terraform default_rpm) applies to everyone no org, group or user cap covers.
  const rows = i ? [...(i.default != null ? [{ scope: i.org !== null ? 'Deployment default (not in effect: the organization cap replaces it)' : 'Deployment default', rpm: i.default }] : []), ...(i.org !== null ? [{ scope: 'Organization', rpm: i.org }] : []), ...Object.entries(i.groups).map(([g, v]) => ({ scope: `Group ${g}`, rpm: v })),
    ...Object.entries(i.users).map(([u, v]) => ({ scope: `User ${u}`, rpm: v }))] : [];
  return (
    <Page title="Rate limits" description="Exact per-user requests-per-minute caps (gateway interceptor, edit them under Budgets & limits or per user) and the AgentCore Gateway's own per-user limits (set in Terraform: rpm_default, tpm_default, rpm_overrides).">
      <DataTable title="Interceptor RPM caps" items={rows} load={r} empty="No RPM caps set" columns={[{ id: 's', header: 'Scope', cell: x => x.scope }, { id: 'r', header: 'Requests per minute', cell: x => x.rpm }]} />
      <DataTable title="AgentCore Gateway rate limits" items={r.data?.gateway ?? []} load={r} columns={[
        { id: 'u', header: 'User', cell: g => g.user ?? 'Everyone (default)' }, { id: 'k', header: 'Dimension', cell: g => `${g.dimension} = ${g.value}` },
        { id: 'r', header: 'Requests/min', cell: g => g.requests_per_minute ?? '-' }, { id: 't', header: 'Tokens/min', cell: g => g.tokens_per_minute ?? '-' },
        { id: 's', header: 'Status', cell: g => g.status }]} />
    </Page>
  );
}
