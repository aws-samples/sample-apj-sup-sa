// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

import type { DigestRow } from '@/api/types';
import { useSession } from '@/auth/session';
import DigestDeliveries from '@/components/DigestDelivery';
import { LoadError, PageLoading } from '@/components/ErrorState';
import NodeLink from '@/components/NodeLink';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';
import { ago, plural, when } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';

const SECTIONS: [string, string][] = [
  ['signal', 'New signals'],
  ['trend', 'New trends'],
  ['gap', 'New gaps'],
  ['content_idea', 'New content ideas'],
];

const link = (r: DigestRow) => ({ id: r.id, title: r.title, evidence_url: r.evidence_url ?? undefined });

function Rows({ rows, empty }: { rows: DigestRow[]; empty: string }) {
  if (rows.length === 0) return <Box color="text-status-inactive">{empty}</Box>;
  return (
    <ul className="air-digest">
      {rows.map((r) => (
        <li key={r.id}>
          <NodeLink node={link(r)} /> <Box variant="small" display="inline">{ago(r.updated_at)}</Box>
          {r.summary && <Box variant="p">{r.summary}</Box>}
        </li>
      ))}
    </ul>
  );
}

/** What changed since the last read: new conclusions, and news about the records you watch. */
export default function WhatsNewPage() {
  usePageTitle("What's new");
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const digest = useAsync(() => api.digest(), [api]);
  const [busy, setBusy] = React.useState(false);
  if (digest.error) {
    return <LoadError error={digest.error} what="what's new" back={{ href: '/', label: 'Go to Overview' }} onRetry={digest.reload} />;
  }
  if (!digest.data) return <PageLoading />;
  const d = digest.data;
  const markRead = async () => {
    setBusy(true);
    try {
      await api.markDigestRead(d.until);
      notify('success', 'Marked as read: the next digest starts where this one ends.');
      digest.reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={`${plural(d.total, 'new record')} since ${when(d.since)}.`}
          actions={
            <Button onClick={() => void markRead()} loading={busy} disabled={d.total === 0 && !Object.values(d.more ?? {}).some(Boolean)}>
              Mark as read
            </Button>
          }
        >
          What&apos;s new
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2" description="Use Watch on a record's page to follow it here.">Watched records</Header>}>
          {d.watched.length === 0 ? (
            <Box color="text-status-inactive">You watch no records yet.</Box>
          ) : (
            <SpaceBetween size="m">
              {d.watched.map((w) => (
                <div key={w.node.id}>
                  <Box variant="h3">
                    <NodeLink node={link(w.node)} showSource={false} /> ({plural(w.count, 'new record')})
                  </Box>
                  <Rows rows={w.new} empty="Nothing new about it." />
                </div>
              ))}
            </SpaceBetween>
          )}
        </Container>
        {[...SECTIONS, ...Object.entries(d.labels ?? {}).map(([kind, label]): [string, string] => [kind, `New: ${label}`])].map(([type, title]) => (
          <Container key={type} header={<Header variant="h2">{title}</Header>}>
            <Rows rows={d.new[type] ?? []} empty="None." />
            {d.more?.[type] && (
              <Box variant="small" color="text-body-secondary">
                More arrived after these: they show here once you mark this digest as read.
              </Box>
            )}
          </Container>
        ))}
        <DigestDeliveries />
      </SpaceBetween>
    </ContentLayout>
  );
}
