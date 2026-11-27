// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import BarChart from '@cloudscape-design/components/bar-chart';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Link from '@cloudscape-design/components/link';
import PieChart from '@cloudscape-design/components/pie-chart';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../api';
import { RepairButton } from '../components/actions';
import { BoostModal } from '../components/BoostModal';
import { Estimated, InlineError, StateIndicator, TableEmpty } from '../components/common';
import { EditLimitsModal } from '../components/EditLimitsModal';
import { READ_ONLY_REASON, useApp } from '../context';
import { compact, int, pct, shortDate, usd, utc } from '../format';
import type { Overview as OverviewT, Period } from '../types';
import { boostRefusal } from '../boost';

export default function Overview() {
  const ov = useApi<OverviewT>('/api/v2/overview');
  const { isAdmin } = useApp();
  const navigate = useNavigate();
  const [edit, setEdit] = useState<{ user: string; period: Period } | null>(null);
  const [boost, setBoost] = useState<{ user: string; period: Period } | null>(null);
  const d = ov.data;

  const series = useMemo(() => {
    if (!d) return [];
    const models = d.model_mix.map((m) => m.model);
    for (const row of d.daily) for (const m of Object.keys(row.by_model)) if (!models.includes(m)) models.push(m);
    return models.map((m) => ({
      title: m,
      type: 'bar' as const,
      data: d.daily.map((row) => ({ x: shortDate(row.date), y: row.by_model[m] ?? 0 })),
      valueFormatter: (y: number) => usd(y),
    }));
  }, [d]);

  const chartStatus = ov.loading && !d ? 'loading' : ov.error ? 'error' : 'finished';
  const k = d?.kpis;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={d ? `Month to date for ${d.month}. Updated ${utc(d.generated_at)}.` : 'Month to date'}
          actions={<Button iconName="refresh" ariaLabel="Refresh" loading={ov.loading} onClick={ov.reload} />}
        >
          Overview
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">This month</Header>}>
          {ov.error ? (
            <InlineError error={ov.error} onRetry={ov.reload} />
          ) : (
            <KeyValuePairs
              columns={6}
              items={[
                {
                  label: 'Spend, month to date',
                  value: k ? <Estimated><Box variant="awsui-value-large">{usd(k.mtd_usd)}</Box></Estimated> : '-',
                  // ORG monthly_usd is the default cap for each user, not a budget for the whole organisation.
                  info: k?.org_budget_usd != null ? <Box variant="small">{k.org_budget_usd === 0 ? 'organization cap $0: users without their own or a group cap are blocked' : `default cap ${usd(k.org_budget_usd)} per user`}</Box> : undefined,
                },
                { label: 'Spend today', value: <Box variant="awsui-value-large">{k ? usd(k.today_usd) : '-'}</Box> },
                { label: 'Projected month end', value: <Box variant="awsui-value-large">{k ? usd(k.projected_usd) : '-'}</Box> },
                { label: 'Active users today', value: <Box variant="awsui-value-large">{k ? int(k.active_users_today) : '-'}</Box> },
                { label: 'Requests, month to date', value: <Box variant="awsui-value-large">{k ? compact(k.requests_mtd) : '-'}</Box> },
              ]}
            />
          )}
        </Container>

        <Grid gridDefinition={[{ colspan: { default: 12, l: 8 } }, { colspan: { default: 12, l: 4 } }]}>
          <Container header={<Header variant="h2" description="Estimated USD per day, stacked by model">Spend per day</Header>}>
            <BarChart
              series={series}
              xScaleType="categorical"
              stackedBars
              height={260}
              xTitle="Day (UTC)"
              yTitle="USD"
              yTickFormatter={(y) => `$${compact(y)}`}
              statusType={chartStatus}
              errorText={ov.error?.message}
              onRecoveryClick={ov.reload}
              recoveryText="Retry"
              loadingText="Loading spend"
              empty={<TableEmpty title="No spend yet" subtitle="No requests this month." />}
              noMatch={<TableEmpty title="No matching data" />}
              ariaLabel="Spend per day by model"
              hideFilter={series.length < 2}
            />
          </Container>
          <Container header={<Header variant="h2" description="Share of estimated spend this month">Model mix</Header>}>
            <PieChart
              variant="donut"
              size="medium"
              data={(d?.model_mix ?? []).map((m) => ({ title: m.model, value: m.usd, requests: m.requests }))}
              detailPopoverContent={(datum, sum) => [
                { key: 'Spend', value: usd(datum.value) },
                { key: 'Share', value: pct((datum.value / sum) * 100, 1) },
                { key: 'Requests', value: int((datum as { requests?: number }).requests) },
              ]}
              segmentDescription={(datum, sum) => pct((datum.value / sum) * 100)}
              innerMetricValue={d ? usd(d.kpis.mtd_usd) : undefined}
              innerMetricDescription="MTD"
              statusType={chartStatus}
              errorText={ov.error?.message}
              onRecoveryClick={ov.reload}
              recoveryText="Retry"
              loadingText="Loading model mix"
              empty={<TableEmpty title="No spend yet" />}
              hideFilter
              ariaLabel="Model mix"
            />
          </Container>
        </Grid>

        <Table
          header={
            <Header
              variant="h2"
              counter={d ? `(${d.attention.length})` : undefined}
              description="Users at or above the critical threshold, blocked, or with a usage record that cannot be read"
              actions={<Button onClick={() => navigate('/users')}>View all users</Button>}
            >
              Needs attention
            </Header>
          }
          variant="container"
          loading={ov.loading && !d}
          loadingText="Loading"
          items={d?.attention ?? []}
          trackBy={(r) => `${r.user}-${r.period}`}
          empty={ov.error ? <InlineError error={ov.error} onRetry={ov.reload} /> : <TableEmpty title="Nobody needs attention" subtitle="No user is near or over a cap." />}
          columnDefinitions={[
            { id: 'user', header: 'User', cell: (r) => <Link href={`#/users/${encodeURIComponent(r.user)}`} onFollow={(e) => { e.preventDefault(); navigate(`/users/${encodeURIComponent(r.user)}`); }}>{r.user}</Link> },
            { id: 'groups', header: 'Groups', cell: (r) => r.groups.join(', ') || '-' },
            { id: 'period', header: 'Period', cell: (r) => r.period },
            { id: 'spent', header: 'Spent / cap', cell: (r) => r.cap === null ? `${usd(r.spent)} / no cap` : `${usd(r.spent)} / ${usd(r.cap)}` },
            { id: 'pct', header: 'Used', cell: (r) => pct(r.pct) },
            { id: 'state', header: 'State', cell: (r) => r.unreadable ? (r.cap === null ? 'Usage record cannot be read' : 'Usage record cannot be read: calls refused') : <StateIndicator state={r.state} /> },
            {
              id: 'actions',
              header: 'Actions',
              cell: (r) => r.unreadable ? <RepairButton user={r.user} period={r.period} capped={r.cap !== null} onDone={ov.reload} /> : (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="inline-link" disabled={!isAdmin} disabledReason={READ_ONLY_REASON} onClick={() => setEdit({ user: r.user, period: r.period })}>
                    Raise cap
                  </Button>
                  <Button variant="inline-link" disabled={!isAdmin || !!boostRefusal([r], r.period)}
                    disabledReason={!isAdmin ? READ_ONLY_REASON : boostRefusal([r], r.period) === 'no cap' ? 'No cap to boost' : 'A $0 cap is a block: raise the cap instead'}
                    onClick={() => setBoost({ user: r.user, period: r.period })}>
                    Boost
                  </Button>
                </SpaceBetween>
              ),
            },
          ]}
        />
      </SpaceBetween>
      <EditLimitsModal user={edit?.user ?? null} focus={edit?.period} onClose={(c) => { setEdit(null); if (c) ov.reload(); }} />
      <BoostModal user={boost?.user ?? null} period={boost?.period} onClose={(c) => { setBoost(null); if (c) ov.reload(); }} />
    </ContentLayout>
  );
}
