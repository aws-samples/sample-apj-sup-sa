// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import React from 'react';
import { useNavigate } from 'react-router-dom';

import type { RadarEntryHit } from '@/api/types';
import { useSession } from '@/auth/session';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';

/** Where an entry sits, opened with the radar's legend filtered to it. */
function EntryLink({ hit }: { hit: RadarEntryHit }) {
  const navigate = useNavigate();
  const href = `/radars/${hit.radar}?q=${encodeURIComponent(hit.title)}`;
  return (
    <Link href={href} onFollow={(e) => { e.preventDefault(); navigate(href); }}>
      {hit.title}
    </Link>
  );
}

const COLUMNS = [
  { id: 'entry', header: 'Entry', cell: (h: RadarEntryHit) => <EntryLink hit={h} /> },
  { id: 'radar', header: 'Radar', cell: (h: RadarEntryHit) => h.radar_name },
  { id: 'sector', header: 'Sector', cell: (h: RadarEntryHit) => (h.sub_sector ? `${h.sector} / ${h.sub_sector}` : h.sector) },
  { id: 'ring', header: 'Ring', cell: (h: RadarEntryHit) => h.ring },
];

/** On the Radars page: which radars place something, and where (CTR HA-076). */
export default function RadarEntryFinder() {
  const { api } = useSession();
  const { fail } = useNotify();
  const [text, setText] = React.useState('');
  const [hits, setHits] = React.useState<RadarEntryHit[] | null>(null);
  const [busy, setBusy] = React.useState(false);
  const short = text.trim().length < 2;
  const find = async () => {
    setBusy(true);
    try {
      setHits((await api.findRadarEntries({ q: text.trim() })).items);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Container header={<Header variant="h2" description="Which radars place something, and in which ring.">Find an entry across radars</Header>}>
      <SpaceBetween size="s">
        <form onSubmit={(e) => { e.preventDefault(); if (!short) void find(); }}>
          <SpaceBetween direction="horizontal" size="xs">
            <Input value={text} onChange={({ detail }) => setText(detail.value)} placeholder="An entry's name" ariaLabel="An entry's name" type="search" />
            <Button variant="primary" loading={busy} disabled={short} disabledReason="Type at least 2 characters.">Find</Button>
          </SpaceBetween>
        </form>
        {hits && (
          <Table wrapLines variant="embedded" items={hits} trackBy={(h) => `${h.radar}#${h.key}`} columnDefinitions={COLUMNS} empty={<Box textAlign="center">No radar places it.</Box>} />
        )}
      </SpaceBetween>
    </Container>
  );
}

/** On a node's page: the radars that place it (entries linked to it), if any. */
export function OnRadars({ nodeId }: { nodeId: string }) {
  const { api } = useSession();
  const found = useAsync(() => api.findRadarEntries({ node_id: nodeId }), [api, nodeId]);
  const hits = found.data?.items ?? [];
  if (!hits.length) return null; // nothing to say, and a failed lookup is not worth an error on the page
  return (
    <Container header={<Header variant="h2" counter={`(${hits.length})`}>On radars</Header>}>
      <Table wrapLines variant="embedded" items={hits} trackBy={(h) => `${h.radar}#${h.key}`} columnDefinitions={COLUMNS} />
    </Container>
  );
}
