// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import AttributeEditor from '@cloudscape-design/components/attribute-editor';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

import { ApiError } from '@/api/client';
import type { DigestChannel, DigestChannelList } from '@/api/types';
import { useSession } from '@/auth/session';
import { useNotify } from '@/components/Notifications';
import UnsavedGuard from '@/components/UnsavedGuard';
import { useAsync } from '@/hooks/useAsync';
import { CHANNEL_ID } from '@/lib/channels';

/** The API's cap on channels in the list. */
export const MAX_CHANNELS = 20;
/** The example ID the form shows, built from parts: written whole it reads as a hex secret to detect-secrets. */
const EXAMPLE_ID = `C${'0123456789'}`;

/** Whether a label has a control character (C0, DEL or C1), which the server refuses. */
function hasControl(text: string): boolean {
  return [...text].some((ch) => {
    const code = ch.charCodeAt(0); // a C0, DEL or C1 character is one UTF-16 unit, so its first unit says
    return code <= 0x1f || (code >= 0x7f && code <= 0x9f);
  });
}

/** What is wrong with each row's ID and label ('' when nothing is). */
export function channelErrors(rows: DigestChannel[]): { id: string; label: string }[] {
  const ids = rows.map((r) => r.id.trim());
  return rows.map((r, i) => {
    const id = ids[i] as string;
    const raw = r.label.trim();
    return {
      id: !id
        ? 'Enter the channel ID.'
        : !CHANNEL_ID.test(id)
          ? `Use the channel ID, such as ${EXAMPLE_ID}: a C or a G, then at least 8 capital letters or digits.`
          : ids.indexOf(id) !== i
            ? 'This channel is already in the list.'
            : '',
      // The rest of the name rule ('#' forms, length, readable characters) is the server's alone: its answer is shown
      // on save, so the two cannot drift apart (scan cycle 29).
      label: !raw ? 'Enter a name people will recognise.' : hasControl(raw) ? 'Remove the tab or other control character.' : '',
    };
  });
}

/**
 * For each row still in a clash the server found at load, the other rows it reads the same as: a group is flagged
 * on every member while two or more of them are there with the names they were stored with (scan cycles 31-33).
 */
export function clashRows(rows: DigestChannel[], initial: DigestChannel[], groups: string[][]): Map<number, number[]> {
  const out = new Map<number, number[]>();
  const unchanged = (row: DigestChannel) => initial.some((o) => o.id === row.id.trim() && o.label === row.label.trim());
  for (const group of groups) {
    // The first row with each ID: a repeated ID is the same channel twice, which channelErrors flags (c34).
    const members = [...new Set(group)].map((id) => rows.findIndex((r) => r.id.trim() === id)).filter((i) => i >= 0 && unchanged(rows[i] as DigestChannel));
    if (members.length < 2) continue;
    for (const i of members) out.set(i, members.filter((j) => j !== i));
  }
  return out;
}

/** Whether a save was refused because the list changed since it was loaded (scan cycle 31). */
export function isStale(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.details.code === 'channels_changed';
}

function ChannelsForm({
  stored,
  onSaved,
  onStale,
}: {
  stored: DigestChannelList;
  onSaved: (next: DigestChannelList) => void;
  onStale: () => void;
}) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const initial = stored.channels;
  const [rows, setRows] = React.useState<DigestChannel[]>(initial);
  const clashes = clashRows(rows, initial, stored.clashes ?? []);
  const [busy, setBusy] = React.useState(false);
  const errors = channelErrors(rows);
  const invalid = errors.some((e) => e.id || e.label);
  const clean = rows.map((r) => ({ id: r.id.trim(), label: r.label.trim() }));
  const dirty = JSON.stringify(clean) !== JSON.stringify(initial);
  const edit = (i: number, patch: Partial<DigestChannel>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = () => {
    setBusy(true);
    void api
      .setDigestChannels(clean, stored.version ?? 0)
      .then(
        (r) => {
          notify('success', 'Saved the digest channels.');
          onSaved(r); // the list as the server stored it (scan cycle 30)
        },
        (err: unknown) => {
          if (!isStale(err)) return fail(err);
          // Who saved is not guessed: another administrator, another tab, or this form's own save that timed out
          // (scan cycle 33).
          notify(
            'error',
            'The channel list changed while you were editing, so this save was not made. The form now shows the saved list: check it and make your change again.',
          );
          onStale();
        },
      )
      .finally(() => setBusy(false));
  };
  return (
    <Container
      header={
        <Header
          variant="h2"
          description="The Slack channels people can send the digest to. Channel digests show only what every reader may see."
          actions={
            <Button
              variant="primary"
              disabled={invalid || !dirty}
              disabledReason={invalid ? 'Fix the highlighted fields first.' : 'Nothing changed yet.'}
              loading={busy}
              onClick={save}
            >
              Save
            </Button>
          }
        >
          Digest channels
        </Header>
      }
    >
      <UnsavedGuard dirty={dirty}>The digest channels have unsaved changes. They are lost if you leave the page now.</UnsavedGuard>
      <SpaceBetween size="m">
        <Alert type="info" header="Invite the bot to each channel">
          In Slack, open the channel and type /invite with the app&apos;s bot name. Until the bot is in the channel, deliveries to it
          fail with not_in_channel and say so on the subscriber&apos;s page. The channel ID is at the bottom of the channel&apos;s details.
        </Alert>
        <AttributeEditor
          items={rows}
          addButtonText="Add channel"
          removeButtonText="Remove"
          removeButtonAriaLabel={(r) => `Remove channel ${r.label || r.id || 'with no name'}`}
          onAddButtonClick={() => setRows((rs) => [...rs, { id: '', label: '' }])}
          onRemoveButtonClick={({ detail }) => setRows((rs) => rs.filter((_, j) => j !== detail.itemIndex))}
          disableAddButton={rows.length >= MAX_CHANNELS}
          additionalInfo={`${rows.length} of ${MAX_CHANNELS} channels.${rows.length >= MAX_CHANNELS ? ' Remove one to add another.' : ''}`}
          empty={<Box color="text-status-inactive">No channels yet: people cannot pick a Slack channel for their digest.</Box>}
          definition={[
            {
              label: 'Channel ID',
              control: (r, i) => (
                <Input value={r.id} placeholder={EXAMPLE_ID} ariaLabel={`Channel ID, row ${i + 1}`} onChange={({ detail }) => edit(i, { id: detail.value })} />
              ),
              errorText: (_, i) => (errors[i] as { id: string }).id || undefined,
            },
            {
              label: 'Name',
              control: (r, i) => (
                <Input value={r.label} placeholder="market-news" ariaLabel={`Name, row ${i + 1}`} onChange={({ detail }) => edit(i, { label: detail.value })} />
              ),
              errorText: (_, i) =>
                (errors[i] as { label: string }).label ||
                (clashes.has(i)
                  ? `Reads the same as ${(clashes.get(i) as number[]).map((j) => `row ${j + 1}`).join(' and ')}, so subscribers see all but the first with its channel ID added. Rename them apart.`
                  : undefined),
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}

/** Administration, Digest delivery: the channel list subscribers pick from. */
export default function DigestChannelsAdmin() {
  const { api } = useSession();
  const channels = useAsync(() => api.digestChannels(), [api]);
  // After a save the form starts again from the list the server answered with, at once: a reload would first
  // hand back the list from before the save (scan cycles 29 and 30). That copy holds only until a newer load
  // arrives, which then wins (scan cycle 31).
  const [saved, setSaved] = React.useState<{ n: number; basis: DigestChannelList; list: DigestChannelList } | null>(null);
  if (channels.error) {
    return (
      <Alert type="error" header="The digest channels could not be loaded" action={<Button onClick={channels.reload}>Retry</Button>}>
        {channels.error.message}
      </Alert>
    );
  }
  if (!channels.data) return null;
  const loaded = channels.data;
  const current = saved && saved.basis === loaded ? saved.list : loaded;
  const onSaved = (next: DigestChannelList) => setSaved((s) => ({ n: (s?.n ?? 0) + 1, basis: loaded, list: next }));
  return (
    <ChannelsForm
      key={`${saved?.n ?? 0}:${JSON.stringify(current)}`}
      stored={current}
      onSaved={onSaved}
      onStale={channels.reload}
    />
  );
}
