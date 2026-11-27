// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import type React from 'react';
import { useNavigate } from 'react-router-dom';

import type { AttributeFieldDef, GraphNode } from '@/api/types';
import { can, seesPrivate, useSession } from '@/auth/session';
import GettingStarted from '@/components/GettingStarted';
import Markdown from '@/components/Markdown';
import { useNav } from '@/components/NavContext';
import NodeLink from '@/components/NodeLink';
import { useAsync } from '@/hooks/useAsync';
import { ago, clip, KIND_HELP, KIND_LABELS, PRIVATE_KINDS, tokens } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';
import { aboutMd, listOrder, listVisible, tagline } from '@/lib/profile';

/** A link that stays inside the app. */
function AppLink({ href, children }: { href: string; children: React.ReactNode }) {
  const navigate = useNavigate();
  return (
    <Link
      href={href}
      onFollow={(e) => {
        e.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </Link>
  );
}

/** Counts per value of each filterable field, each a link to the filtered list, and the dates coming
 * up. Only for a list whose module declares typed fields; a list without them shows just its latest items. */
function Facets({ kind, schema }: { kind: string; schema: AttributeFieldDef[] }) {
  const { api } = useSession();
  const facets = useAsync(() => api.facets(kind), [api, kind]);
  if (facets.error) return <StatusIndicator type="error">{facets.error.message}</StatusIndicator>;
  const data = facets.data;
  if (!data) return <Spinner />;
  const byKey = new Map(schema.map((f) => [f.key, f]));
  const labelOf = (f: AttributeFieldDef | undefined, value: string) => f?.values?.find((v) => v.value === value)?.label ?? value;
  return (
    <SpaceBetween size="s">
      {Object.entries(data.counts).map(([key, tally]) => {
        const field = byKey.get(key);
        const shown = Object.entries(tally).filter(([, n]) => n > 0);
        if (shown.length === 0) return null;
        return (
          <Box key={key} fontSize="body-s">
            <Box variant="strong" fontSize="body-s">
              {`By ${(field?.label ?? key).toLowerCase()}${data.counts_cut?.includes(key) ? ` (the ${shown.length} most common)` : ''}: `}
            </Box>
            {shown.map(([value, n], i) => (
              <span key={value}>
                {i > 0 && ' \u00b7 '}
                {/* A filter takes comma-separated values, so a value holding a comma cannot be linked to. */}
                {value.includes(',') ? (
                  `${labelOf(field, value)} ${n}`
                ) : (
                  <AppLink href={`/insights/${kind}?f.${encodeURIComponent(key)}=${encodeURIComponent(value)}`}>{`${labelOf(field, value)} ${n}`}</AppLink>
                )}
              </span>
            ))}
          </Box>
        );
      })}
      {data.upcoming.length > 0 && (
        <div>
          <Box variant="strong" fontSize="body-s">{`Coming up in the next ${data.days} days`}</Box>
          {data.upcoming.slice(0, 8).map((u) => (
            <Box key={`${u.node.id}-${u.field}`} fontSize="body-s">
              {`${u.date} \u00b7 ${(byKey.get(u.field)?.label ?? u.field).toLowerCase()}: `}
              <NodeLink node={u.node} showSource={false} />
            </Box>
          ))}
        </div>
      )}
      {data.truncated && <Box fontSize="body-s" color="text-body-secondary">Counted from the first {data.scanned} records.</Box>}
    </SpaceBetween>
  );
}

function Latest({ kind, label, help }: { kind: string; label: string; help: { about: string; module: string; moduleName: string } }) {
  const { api } = useSession();
  const page = useAsync(() => api.insights(kind), [api, kind]);
  const items: GraphNode[] = page.data?.items.slice(0, 5) ?? [];
  const schema = page.data?.schema ?? [];
  const typed = schema.some((f) => f.filter || f.type === 'date');
  return (
    <Container header={<Header variant="h2" description={help.about} actions={<AppLink href={`/insights/${kind}`}>View all</AppLink>}>{label}</Header>}>
      {page.loading ? (
        <Spinner />
      ) : page.error ? (
        <SpaceBetween size="xs">
          <StatusIndicator type="error">{page.error.message}</StatusIndicator>
          <Button onClick={page.reload}>Retry</Button>
        </SpaceBetween>
      ) : items.length === 0 ? (
        <Box color="text-body-secondary">
          Nothing yet. These appear when the {help.moduleName} module runs.{' '}
          {help.module && <AppLink href={`/modules/${help.module}`}>Open the module</AppLink>}
        </Box>
      ) : (
        <SpaceBetween size="s">
          {typed && <Facets kind={kind} schema={schema} />}
          {items.map((n) => (
            <div key={n.id}>
              <NodeLink node={n} showSource={false} />
              <Box fontSize="body-s" color="text-body-secondary">
                {ago(n.updated_at)}
                {n.summary ? ` \u00b7 ${clip(n.summary, 160)}` : ''}
              </Box>
            </div>
          ))}
        </SpaceBetween>
      )}
    </Container>
  );
}

function Operations() {
  const { api } = useSession();
  const health = useAsync(() => api.health(), [api]);
  if (health.error) {
    // A failing health check is itself news for an administrator: say so instead of hiding the card.
    return (
      <Container header={<Header variant="h2">Operations</Header>}>
        <SpaceBetween size="xs">
          <StatusIndicator type="error">{`The health check failed: ${health.error.message}`}</StatusIndicator>
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <Button onClick={health.reload}>Retry</Button>
            <AppLink href="/admin?tab=health">Open Administration &gt; Health</AppLink>
          </SpaceBetween>
        </SpaceBetween>
      </Container>
    );
  }
  if (!health.data) return null;
  const enabled = health.data.budgets.filter((b) => b.enabled);
  const used = health.data.budgets.reduce((n, b) => n + b.consumed, 0);
  return (
    <Container header={<Header variant="h2">Operations</Header>}>
      <ColumnLayout columns={4} variant="text-grid">
        <div>
          <Box variant="awsui-key-label">AI features</Box>
          <StatusIndicator type={health.data.genai_enabled ? 'success' : 'stopped'}>
            {health.data.genai_enabled ? 'Running' : 'Paused'}
          </StatusIndicator>
        </div>
        <div>
          {/* The flag the Modules page calls "Run on schedule": modules used with Run now are not counted (r3 F3-073). */}
          <Box variant="awsui-key-label">Modules on a schedule</Box>
          <Box variant="awsui-value-large">{enabled.length}</Box>
        </div>
        <div>
          {/* The modules' budgets only: the assistant has a budget of its own, under Administration. */}
          <Box variant="awsui-key-label">Module tokens today</Box>
          <Box variant="awsui-value-large">{tokens(used)}</Box>
        </div>
        <div>
          {/* The count from the last 100 runs: the list of failures is capped at 10, so "10" could mean 400 (r3 F3-072). */}
          <Box variant="awsui-key-label">Failed of the last 100 runs</Box>
          <Box variant="awsui-value-large">
            <AppLink href="/admin?tab=health">{String(health.data.runs.failed ?? 0)}</AppLink>
          </Box>
        </div>
      </ColumnLayout>
    </Container>
  );
}

const OVERVIEW_KINDS = ['signals', 'trends', 'gaps', 'content'];

export default function HomePage() {
  usePageTitle('Overview');
  const { me } = useSession();
  const { nav, error, refresh } = useNav();
  const lists = [
    // A viewer is refused gaps and content ideas (403): their cards would only show the refusal.
    ...OVERVIEW_KINDS.filter((k) => seesPrivate(me) || !PRIVATE_KINDS.has(k)).map((k) => ({ kind: k, label: KIND_LABELS[k] as string, help: KIND_HELP[k] as (typeof KIND_HELP)[string] })),
    // Custom modules' lists are on the Overview too, next to the built-in ones.
    ...(nav?.custom ?? []).map((c) => ({ kind: c.kind, label: c.label, help: { about: `Published by the ${c.label} module.`, module: c.module, moduleName: c.label } })),
  ]
    // A list the deployment profile hides is not on the Overview either; the rest follow its order.
    .filter((l) => listVisible(l.kind))
    .map((l, i) => ({ ...l, at: listOrder(l.kind) * 1000 + i }))
    .sort((a, b) => a.at - b.at);
  return (
    <ContentLayout header={<Header variant="h1" description={tagline() || 'What changed in your market, and what to do about it.'}>Overview</Header>}>
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" header="The list names could not be loaded" action={<Button onClick={refresh}>Retry</Button>}>
            {`Any list a custom module publishes is missing from this page and from the navigation until they are read. ${error.message}`}
          </Alert>
        )}
        {aboutMd() && (
          // The deployment's own notes, set in its profile: what it watches, its sources, what not to enter.
          <Container header={<Header variant="h2">About this deployment</Header>}>
            <Markdown markdown={aboutMd()} />
          </Container>
        )}
        <GettingStarted />
        {can(me, 'health.read') && <Operations />}
        <ColumnLayout columns={2}>
          {lists.map((l) => (
            <Latest key={l.kind} {...l} />
          ))}
        </ColumnLayout>
      </SpaceBetween>
    </ContentLayout>
  );
}
