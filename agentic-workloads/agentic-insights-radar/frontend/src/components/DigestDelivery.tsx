// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select, { type SelectProps } from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Toggle from '@cloudscape-design/components/toggle';
import React from 'react';

import { ApiError } from '@/api/client';
import type { Delivery, DeliveryChanges, DigestChannel, DeliveryKind, DeliveryOptions, DeliveryTest } from '@/api/types';
import { useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { describeError, useNotify, waitFor } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';
import { CHANNEL_ID } from '@/lib/channels';
import { ago, plural, when } from '@/lib/format';
import { browserTimeZone, hourLabel, scheduleText, timeZones, WEEKDAYS } from '@/lib/schedule';

/** The API's cap on deliveries per user. */
export const MAX_DELIVERIES = 5;

export const KINDS: DeliveryKind[] = ['slack_channel', 'slack_dm', 'email'];

export const KIND_LABELS: Record<DeliveryKind, string> = {
  slack_channel: 'Slack channel',
  slack_dm: 'Slack direct message',
  email: 'Email',
};

/** Why a kind cannot be picked here, or null when it can. */
export function unavailableReason(kind: DeliveryKind, o: DeliveryOptions): string | null {
  if (kind === 'slack_channel') {
    if (!o.slack_channel) return 'Slack channels are not set up on this deployment.';
    return o.channels.length ? null : 'No Slack channels to pick from yet: an administrator adds them under Administration, Digest delivery.';
  }
  if (kind === 'slack_dm') return o.slack_dm ? null : 'Slack DMs are not set up on this deployment.';
  if (!o.email) return 'Email is not set up on this deployment.';
  return o.email_address ? null : 'Your account has no verified email address to send to.';
}

/** What a test send says: sent, nothing to send, or why it failed. */
export function testMessage(d: Delivery, r: DeliveryTest): { type: 'success' | 'info' | 'error'; text: string } {
  if (r.sent) return { type: 'success', text: `Test sent to ${d.target_label}: ${plural(r.total, 'new record')}.` };
  if (r.error) return { type: 'error', text: `The test could not be sent to ${d.target_label}: ${r.error}` };
  return { type: 'info', text: `Nothing to send to ${d.target_label} yet: the digest is empty right now, so no test went out.` };
}

/** A refused test: the 10-minute limit is said in full, with when to try again. */
export function testError(err: unknown): string {
  if (err instanceof ApiError && err.status === 429 && 'limit_per_10_minutes' in err.details) {
    return `You can send one test every 10 minutes for each delivery.${err.retryAfter === null ? '' : ` Try again ${waitFor(err.retryAfter)}.`}`;
  }
  return describeError(err);
}

function LastSend({ d }: { d: Delivery }) {
  const at = d.last_sent_at ? ` ${ago(d.last_sent_at)}` : '';
  // A bounce or complaint stopped it for good: shown from `suppressed` and its own reason, which no later send
  // writes over (scan cycles 14 and 15).
  if (d.suppressed) {
    return (
      <SpaceBetween size="xxs">
        <StatusIndicator type="warning">Stopped: the address bounced or reported spam</StatusIndicator>
        {d.stop_reason && <Box variant="small">{d.stop_reason}</Box>}
      </SpaceBetween>
    );
  }
  if (d.last_status === 'ok') return <StatusIndicator type="success">{`Sent${at}`}</StatusIndicator>;
  if (d.last_status === 'empty') return <StatusIndicator type="info">{`Nothing new to send${at}`}</StatusIndicator>;
  if (d.last_status === 'error') {
    return (
      <SpaceBetween size="xxs">
        <StatusIndicator type="error">Failed</StatusIndicator>
        {d.last_error && <Box variant="small">{d.last_error}</Box>}
      </SpaceBetween>
    );
  }
  return <StatusIndicator type="pending">Not sent yet</StatusIndicator>;
}

interface Draft {
  kind: DeliveryKind;
  channel_id: string;
  cadence: 'daily' | 'weekly';
  weekday: number;
  hour: number;
  timezone: string;
}

const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: hourLabel(h) }));
const DAYS = WEEKDAYS.map((label, i) => ({ value: String(i), label }));

/** The schedule fields that differ from the saved delivery. */
export function changesOf(d: Delivery, draft: Draft): DeliveryChanges {
  const out: DeliveryChanges = {};
  if (draft.cadence !== d.cadence) out.cadence = draft.cadence;
  // Weekly needs its day: sent when it changed, and always when the cadence changes to weekly.
  if (draft.cadence === 'weekly' && (out.cadence || draft.weekday !== (d.weekday ?? 0))) out.weekday = draft.weekday;
  if (draft.hour !== d.hour) out.hour = draft.hour;
  if (draft.timezone !== d.timezone) out.timezone = draft.timezone;
  return out;
}

function DeliveryForm({ delivery, options, onDismiss, onSaved }: { delivery?: Delivery; options: DeliveryOptions; onDismiss: () => void; onSaved: () => void }) {
  const { api } = useSession();
  const { notify } = useNotify();
  const [draft, setDraft] = React.useState<Draft>(() =>
    delivery
      ? { kind: delivery.kind, channel_id: delivery.channel_id ?? '', cadence: delivery.cadence, weekday: delivery.weekday ?? 0, hour: delivery.hour, timezone: delivery.timezone }
      : {
          kind: KINDS.find((k) => !unavailableReason(k, options)) as DeliveryKind,
          channel_id: '',
          cadence: 'weekly',
          weekday: 0,
          hour: 8,
          timezone: browserTimeZone(),
        },
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const zones = React.useMemo(() => timeZones([draft.timezone, browserTimeZone()]).map((z) => ({ value: z, label: z })), [draft.timezone]);
  const changes = delivery ? changesOf(delivery, draft) : null;
  const blocked = changes
    ? Object.keys(changes).length === 0
      ? 'Nothing changed yet.'
      : null
    : draft.kind === 'slack_channel' && !draft.channel_id
      ? 'Pick a channel first.'
      : null;
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      if (delivery) {
        await api.updateDelivery(delivery.id, changes as DeliveryChanges);
        notify('success', `Saved the schedule for ${delivery.target_label}.`);
      } else {
        const made = await api.createDelivery({
          kind: draft.kind,
          ...(draft.kind === 'slack_channel' ? { channel_id: draft.channel_id } : {}),
          cadence: draft.cadence,
          ...(draft.cadence === 'weekly' ? { weekday: draft.weekday } : {}),
          hour: draft.hour,
          timezone: draft.timezone,
          enabled: true,
        });
        notify('success', `Added: ${scheduleText(made)}, to ${made.target_label}.`);
      }
      onSaved();
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  };
  const channel = options.channels.find((c) => c.id === draft.channel_id);
  return (
    <Modal
      visible
      onDismiss={() => !busy && onDismiss()}
      header={delivery ? `Edit delivery to ${delivery.target_label}` : 'Add a digest delivery'}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" disabled={busy} onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!!blocked} disabledReason={blocked ?? undefined} onClick={() => void save()}>
              {delivery ? 'Save' : 'Add delivery'}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {error && <Alert type="error" header="Not saved">{error}</Alert>}
        {delivery ? (
          <FormField label="Delivers to">
            <Box>{`${KIND_LABELS[delivery.kind]}: ${delivery.target_label}`}</Box>
          </FormField>
        ) : (
          <FormField label="Deliver by">
            <RadioGroup
              value={draft.kind}
              onChange={({ detail }) => set({ kind: detail.value as DeliveryKind })}
              items={KINDS.map((k) => {
                const why = unavailableReason(k, options);
                return { value: k, label: KIND_LABELS[k], disabled: !!why, description: why ?? undefined };
              })}
            />
          </FormField>
        )}
        {!delivery && draft.kind === 'slack_channel' && (
          <FormField label="Channel" description="Channels an administrator has set up for digests.">
            <Select
              selectedOption={channel ? channelOption(channel) : null}
              onChange={({ detail }) => set({ channel_id: String(detail.selectedOption.value) })}
              options={options.channels.map(channelOption)}
              placeholder="Pick a channel"
            />
          </FormField>
        )}
        {!delivery && draft.kind === 'slack_dm' && <Box>The app&apos;s Slack bot sends it to you as a direct message.</Box>}
        {!delivery && draft.kind === 'email' && (
          <FormField label="Sent to" constraintText="Your own verified address: it cannot be changed here.">
            {/* Email can be picked only when the address is known (unavailableReason). */}
            <Input value={String(options.email_address)} readOnly />
          </FormField>
        )}
        <FormField label="How often">
          <RadioGroup
            value={draft.cadence}
            onChange={({ detail }) => set({ cadence: detail.value as Draft['cadence'] })}
            items={[
              { value: 'daily', label: 'Daily' },
              { value: 'weekly', label: 'Weekly' },
            ]}
          />
        </FormField>
        {draft.cadence === 'weekly' && (
          <FormField label="Day">
            <Select
              selectedOption={DAYS[draft.weekday] as SelectProps.Option}
              onChange={({ detail }) => set({ weekday: Number(detail.selectedOption.value) })}
              options={DAYS}
            />
          </FormField>
        )}
        <FormField label="Time" description="Digests go out within 15 minutes after the hour.">
          <Select selectedOption={HOURS[draft.hour] as SelectProps.Option} onChange={({ detail }) => set({ hour: Number(detail.selectedOption.value) })} options={HOURS} />
        </FormField>
        <FormField label="Time zone">
          <Select
            selectedOption={{ value: draft.timezone, label: draft.timezone }}
            onChange={({ detail }) => set({ timezone: String(detail.selectedOption.value) })}
            options={zones}
            filteringType="auto"
          />
        </FormField>
        <Box variant="small" color="text-body-secondary">{scheduleText(draft)}</Box>
      </SpaceBetween>
    </Modal>
  );
}

/**
 * A picker option for a channel. The ID is shown under the name only when it adds something: not for a name that
 * already ends with it or is the ID itself, and not for the webhook's internal value (scan cycles 35 to 37).
 */
export function channelOption(c: DigestChannel): { value: string; label: string; description?: string } {
  const shown = CHANNEL_ID.test(c.id) && c.label !== c.id && !c.label.endsWith(`(${c.id})`);
  return shown ? { value: c.id, label: c.label, description: c.id } : { value: c.id, label: c.label };
}

/**
 * The digest in Slack or by email on a schedule. Each delivery keeps its own place in the digest, so
 * reading it here does not skip anything there, and an empty digest sends nothing.
 */
export default function DigestDeliveries() {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const list = useAsync(() => api.deliveries(), [api]);
  const [editing, setEditing] = React.useState<Delivery | 'new' | null>(null);
  const [deleting, setDeleting] = React.useState<Delivery | null>(null);
  const [testing, setTesting] = React.useState<string | null>(null);
  const [switching, setSwitching] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<{ type: 'success' | 'info' | 'error'; text: string } | null>(null);
  const header = (actions?: React.ReactNode) => (
    <Header
      variant="h2"
      actions={actions}
      description="Get this digest in Slack or by email on a schedule. Each delivery keeps its own place: reading it here skips nothing there, and an empty digest sends nothing."
    >
      Digest delivery
    </Header>
  );
  if (!list.data) {
    if (!list.error) return <Container header={header()}><Box color="text-body-secondary">Loading deliveries</Box></Container>;
    const missing = list.error instanceof ApiError && list.error.status === 404;
    return (
      <Container header={header()}>
        {missing ? (
          <Box color="text-status-inactive">Digest delivery is not available on this deployment.</Box>
        ) : (
          <Alert type="error" header="Your deliveries could not be loaded" action={<Button onClick={list.reload}>Retry</Button>}>
            {list.error.message}
          </Alert>
        )}
      </Container>
    );
  }
  const { items, options } = list.data;
  const reasons = KINDS.map((k) => unavailableReason(k, options)).filter((r): r is string => !!r);
  const none = reasons.length === KINDS.length;
  const full = items.length >= MAX_DELIVERIES;
  const addBlocked = none
    ? 'No way to deliver digests is set up on this deployment.'
    : full
      ? `You have ${MAX_DELIVERIES} deliveries, the most allowed. Delete one to add another.`
      : undefined;
  const add = (
    <Button disabled={!!addBlocked} disabledReason={addBlocked} onClick={() => setEditing('new')}>
      Add delivery
    </Button>
  );
  const done = () => {
    setEditing(null);
    list.reload();
  };
  const toggle = (d: Delivery, enabled: boolean) => {
    setSwitching(d.id);
    void api
      .updateDelivery(d.id, { enabled })
      .then(() => {
        notify('success', enabled ? `Turned on: digests go to ${d.target_label} again.` : `Paused: no digests go to ${d.target_label} until you turn it on.`);
        list.reload();
      }, (err: unknown) => {
        fail(err);
        list.reload(); // a 409 means the row changed (stopped, deleted, switched): show it as it is now (c17)
      })
      .finally(() => setSwitching(null));
  };
  const test = (d: Delivery) => {
    setTesting(d.id);
    setResult(null);
    void api
      .testDelivery(d.id)
      .then(
        (r) => {
          setResult(testMessage(d, r));
          if (r.error) list.reload(); // a deleted delivery leaves the list (scan cycles 17 and 18)
        },
        (err: unknown) => {
          setResult({ type: 'error', text: testError(err) });
          list.reload(); // a stopped delivery shows as stopped (scan cycle 17)
        },
      )
      .finally(() => setTesting(null));
  };
  return (
    <Container header={header(add)}>
      <SpaceBetween size="m">
        {list.error && (
          <Alert type="error" header="Your deliveries could not be read again" action={<Button onClick={list.reload}>Retry</Button>}>
            {`This shows what was read last. ${list.error.message}`}
          </Alert>
        )}
        <div aria-live="polite">
          {result && (
            <Alert type={result.type} dismissible onDismiss={() => setResult(null)}>
              {result.text}
            </Alert>
          )}
        </div>
        <Table wrapLines
          variant="embedded"
          ariaLabels={{ tableLabel: 'Digest deliveries' }}
          items={items}
          trackBy="id"
          columnDefinitions={[
            {
              id: 'to',
              header: 'Delivers to',
              cell: (d) => (
                <div>
                  <Box variant="small">{KIND_LABELS[d.kind]}</Box>
                  <Box>{d.target_label}</Box>
                </div>
              ),
            },
            { id: 'schedule', header: 'Schedule', cell: (d) => scheduleText(d) },
            {
              id: 'on',
              header: 'On',
              cell: (d) => (
                <Toggle
                  checked={d.enabled}
                  disabled={switching === d.id || !!d.suppressed}
                  ariaLabel={`Send digests to ${d.target_label}`}
                  onChange={({ detail }) => toggle(d, detail.checked)}
                >
                  {d.suppressed ? 'Stopped' : d.enabled ? 'On' : 'Paused'}
                </Toggle>
              ),
            },
            { id: 'last', header: 'Last send', cell: (d) => <LastSend d={d} /> },
            {
              id: 'next',
              header: 'Next send',
              cell: (d) => (d.suppressed ? 'Stopped' : d.enabled ? when(d.next_at) : 'Paused'),
            },
            {
              id: 'actions',
              header: 'Actions',
              cell: (d) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="inline-link" ariaLabel={`Edit delivery to ${d.target_label}`} onClick={() => setEditing(d)}>Edit</Button>
                  <Button variant="inline-link" ariaLabel={`Send a test to ${d.target_label} now`} loading={testing === d.id} disabled={!!testing || !!d.suppressed} onClick={() => test(d)}>
                    Send test now
                  </Button>
                  <Button variant="inline-link" ariaLabel={`Delete delivery to ${d.target_label}`} onClick={() => setDeleting(d)}>Delete</Button>
                </SpaceBetween>
              ),
            },
          ]}
          empty={
            <Box textAlign="center" padding="m">
              {none ? 'Digest delivery is not set up on this deployment.' : 'No deliveries yet. Add one to get this digest in Slack or by email.'}
            </Box>
          }
        />
        {reasons.length > 0 && (
          <Box variant="small" color="text-body-secondary">
            {`Not available: ${reasons.join(' ')}`}
          </Box>
        )}
      </SpaceBetween>
      {editing && <DeliveryForm delivery={editing === 'new' ? undefined : editing} options={options} onDismiss={() => setEditing(null)} onSaved={done} />}
      {deleting && (
        <ConfirmDelete
          header="Delete this delivery?"
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteDelivery(deleting.id).then(() => {
              notify('success', `Deleted: digests no longer go to ${deleting.target_label}.`);
              setDeleting(null);
              list.reload();
            }, fail)
          }
        >
          {`Digests stop going to ${deleting.target_label} (${KIND_LABELS[deleting.kind]}, ${scheduleText(deleting)}). To stop them for a while instead, turn the delivery off.`}
        </ConfirmDelete>
      )}
    </Container>
  );
}
