// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import Table from '@cloudscape-design/components/table';
import React from 'react';

import { ApiError } from '@/api/client';
import type { EntityBrief, MergeReport } from '@/api/types';
import { useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { TableLoadError } from '@/components/ErrorState';
import NodeLink from '@/components/NodeLink';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';
import { plural, typeLabel } from '@/lib/format';

const name = (e: EntityBrief) => `${e.title} (${typeLabel(e.type)}, ${plural(e.degree, 'link')})`;

const idLink = (id: string) => <NodeLink node={{ id, title: id }} showSource={false} />;

/**
 * The merges a refusal says must be finished first (code review r73): the API names them in its details, as
 * `from` (merges into this entity that stopped part way), `entity` and `into` (a linked entity's merge that
 * stopped part way), or `into` alone (this entity's own merge into another).
 */
export function unfinishedMerges(error: unknown, from: string): { dup: string; into: string }[] {
  if (!(error instanceof ApiError)) return [];
  const d = error.details;
  if (Array.isArray(d.from)) return d.from.filter((x): x is string => typeof x === 'string').map((dup) => ({ dup, into: from }));
  if (typeof d.into !== 'string') return [];
  return [{ dup: typeof d.entity === 'string' ? d.entity : from, into: d.into }];
}

/** What an applied merge did, for its success message (code review r74): the dialog closes, so this is all it shows. */
export function mergeOutcome(r: MergeReport): string {
  const parts = [plural(r.moved, 'link') + ' moved'];
  if (r.redirected.length) {
    const via = [...new Set(r.redirected.map((x) => x.via))].join(', ');
    parts.push(`${plural(r.redirected.length, 'link')} taken on to ${via}, where a linked entity's earlier merge went`);
  }
  if (r.dropped.length) parts.push(`${plural(r.dropped.length, 'link')} dropped`);
  return `${parts.join('; ')}.`;
}

/** Merging one entity into another: the dry run's report first, then Merge does it. */
function MergeDialog({
  from,
  into,
  onDone,
  onChanged,
  onDismiss,
}: {
  from: EntityBrief;
  into: EntityBrief;
  onDone: () => void;
  /** The graph changed but this merge is still to do (an earlier merge was finished from here). */
  onChanged: () => void;
  onDismiss: () => void;
}) {
  const { api } = useSession();
  const { notify, fail } = useNotify();
  const plan = useAsync(() => api.mergeEntity(from.id, into.id), [api, from.id, into.id]);
  const [busy, setBusy] = React.useState(false);
  const [refused, setRefused] = React.useState<unknown>(null);
  const merge = async () => {
    setBusy(true);
    try {
      notify('success', `${from.title} was merged into ${into.title}. ${mergeOutcome(await api.mergeEntity(from.id, into.id, true))}`);
      onDone();
    } catch (err) {
      setRefused(err);
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  const blockers = unfinishedMerges(refused ?? plan.error, from.id);
  const finish = async (b: { dup: string; into: string }) => {
    setBusy(true);
    try {
      await api.mergeEntity(b.dup, b.into, true);
      notify('success', `The merge of ${b.dup} into ${b.into} was finished.`);
      if (b.dup === from.id) {
        // This entity's own earlier merge: it is gone now, so there is nothing left to merge here (code review r74).
        onDone();
        return;
      }
      setRefused(null);
      plan.reload();
      onChanged();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  const unfinished = blockers.length > 0 && (
    <SpaceBetween size="xs">
      <Box>An earlier merge stopped part way. Finish it first, then merge again:</Box>
      {blockers.map((b) => (
        <SpaceBetween key={`${b.dup}>${b.into}`} direction="horizontal" size="xs" alignItems="center">
          <span>
            {idLink(b.dup)} into {idLink(b.into)}
          </span>
          <Button disabled={busy} ariaLabel={`Finish merging ${b.dup} into ${b.into}`} onClick={() => void finish(b)}>
            Finish that merge
          </Button>
        </SpaceBetween>
      ))}
    </SpaceBetween>
  );
  const report: MergeReport | undefined = plan.data;
  return (
    <Modal
      visible
      onDismiss={() => !busy && onDismiss()}
      header={`Merge ${from.title} into ${into.title}?`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" disabled={busy} onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!report} onClick={() => void merge()}>Merge</Button>
          </SpaceBetween>
        </Box>
      }
    >
      {plan.error ? (
        <SpaceBetween size="s">
          <Box color="text-status-error">{`What the merge would do could not be worked out: ${plan.error.message}`}</Box>
          {unfinished}
        </SpaceBetween>
      ) : !report ? (
        <Spinner />
      ) : (
        <SpaceBetween size="s">
          <Box>{`${from.title} is deleted. Everything linked to it is linked to ${into.title} instead, and its name becomes one of ${into.title}'s other names, so later mentions land there.`}</Box>
          <KeyValuePairs
            columns={2}
            items={[
              { label: 'Links moved', value: String(report.moved) },
              { label: 'Links dropped', value: report.dropped.length ? report.dropped.map((d) => `${d.type}: ${d.why}`).join('; ') : 'None' },
              ...(report.redirected.length
                ? [{ label: 'Links taken on', value: report.redirected.map((r) => `${r.type}: to ${r.via} instead of ${r.to === report.duplicate ? r.from : r.to}`).join('; ') }]
                : []),
              { label: 'Names added', value: report.aliases_added.join(', ') || 'None' },
              { label: 'Descriptions updated', value: String(report.descriptions_rewritten.length) },
            ]}
          />
          {unfinished}
        </SpaceBetween>
      )}
    </Modal>
  );
}

/** Administration > Entities: duplicates to merge and entities nothing links to (CTR HC-105/120/124/140). */
export default function EntityHygienePanel() {
  const { api } = useSession();
  const { notify, fail } = useNotify();
  const report = useAsync(() => api.entityHygiene(), [api]);
  const [merging, setMerging] = React.useState<{ from: EntityBrief; into: EntityBrief } | null>(null);
  const [deleting, setDeleting] = React.useState<EntityBrief | null>(null);
  const data = report.data;
  const failed = report.error ? <TableLoadError what="the entity report" error={report.error} onRetry={report.reload} /> : undefined;
  return (
    <SpaceBetween size="l">
      {merging && (
        <MergeDialog
          from={merging.from}
          into={merging.into}
          onDismiss={() => setMerging(null)}
          onChanged={report.reload}
          onDone={() => {
            setMerging(null);
            report.reload();
          }}
        />
      )}
      {deleting && (
        <ConfirmDelete
          header={`Delete ${deleting.title}?`}
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteNode(deleting.id).then(
              () => {
                notify('success', `${deleting.title} was deleted.`);
                setDeleting(null);
                report.reload();
              },
              (err: unknown) => {
                fail(err);
                setDeleting(null);
              },
            )
          }
        >
          Nothing links to this entity. Deleting it cannot be undone.
        </ConfirmDelete>
      )}
      <Table wrapLines
        loading={report.loading && !data}
        items={data?.duplicates ?? []}
        trackBy={(g) => g.map((e) => e.id).join('|')}
        variant="container"
        header={
          <Header
            variant="h2"
            counter={data ? `(${data.duplicate_count})` : undefined}
            description="Entities that look like one thing: the same name spelt another way, or one's other name is another's name. The best-linked one is listed first; merge the others into it."
            actions={<Button iconName="refresh" ariaLabel="Refresh the entity report" onClick={report.reload} />}
          >
            Likely duplicates
          </Header>
        }
        columnDefinitions={[
          { id: 'keep', header: 'Keep', cell: (g) => <NodeLink node={{ id: (g[0] as EntityBrief).id, title: name(g[0] as EntityBrief) }} showSource={false} /> },
          {
            id: 'others',
            header: 'Merge into it',
            cell: (g) => (
              <SpaceBetween size="xxs">
                {g.slice(1).map((e) => (
                  <SpaceBetween key={e.id} direction="horizontal" size="xs" alignItems="center">
                    <NodeLink node={{ id: e.id, title: name(e) }} showSource={false} />
                    <Button variant="inline-link" ariaLabel={`Merge ${e.title} into ${(g[0] as EntityBrief).title}`} onClick={() => setMerging({ from: e, into: g[0] as EntityBrief })}>
                      Merge
                    </Button>
                  </SpaceBetween>
                ))}
              </SpaceBetween>
            ),
          },
        ]}
        empty={failed ?? <Box textAlign="center" padding="m">No likely duplicates.</Box>}
      />
      <Table wrapLines
        loading={report.loading && !data}
        items={data?.orphans ?? []}
        trackBy="id"
        variant="container"
        header={
          <Header
            variant="h2"
            counter={data ? `(${data.orphan_count})` : undefined}
            description={data ? `Entities no evidence, output or other entity links to, out of ${data.scanned} read.` : undefined}
          >
            Unlinked entities
          </Header>
        }
        columnDefinitions={[
          { id: 'title', header: 'Entity', cell: (e) => <NodeLink node={{ id: e.id, title: e.title }} showSource={false} /> },
          { id: 'type', header: 'Type', cell: (e) => typeLabel(e.type) },
          { id: 'delete', header: 'Actions', cell: (e) => <Button variant="inline-link" ariaLabel={`Delete ${e.title}`} onClick={() => setDeleting(e)}>Delete</Button> },
        ]}
        empty={failed ?? <Box textAlign="center" padding="m">Every entity is linked.</Box>}
      />
    </SpaceBetween>
  );
}
