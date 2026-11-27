// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import Textarea from '@cloudscape-design/components/textarea';
import { useEffect, useMemo, useState } from 'react';
import { useInFlight } from '../inflight';
import { apiWrite, useApi } from '../api';
import { useApp, useWriteErrors } from '../context';
import { usd, utc } from '../format';
import type { CapFields, Period, UserDetail, WriteResult } from '../types';
import { CAP_FIELD, CAP_FIELDS, PERIODS } from '../types';
import { capToInput, parseCap } from '../validate';
import { ErrorState, SourceBadge } from './common';
import { ConfirmModal } from './ConfirmModal';

type Field = 'monthly_usd' | 'daily_usd' | 'hourly_usd' | 'rpm';

export function EditLimitsModal({ user, focus, onClose }: { user: string | null; focus?: Period; onClose: (changed: boolean) => void }) {
  const detail = useApi<UserDetail>(user ? `/api/v2/users/${encodeURIComponent(user)}` : null);
  const { notify } = useApp();
  const writeErrors = useWriteErrors();
  const [values, setValues] = useState<Record<Field, string>>({ monthly_usd: '', daily_usd: '', hourly_usd: '', rpm: '' });
  const [reason, setReason] = useState('');
  const [serverErr, setServerErr] = useState<{ field: string; message: string } | undefined>();
  const [saving, setSaving] = useState(false);
  const once = useInFlight();
  const [confirming, setConfirming] = useState(false);
  // Only the settled answer counts: filling the form (or sending the version) from a stale copy would overwrite what
  // the admin types, or give a false 409 for their own last save.
  const d = user && detail.settled?.user === user ? detail.settled : null;

  useEffect(() => {
    if (!d) return;
    const o = d.override;
    setValues({ monthly_usd: capToInput(o.monthly_usd), daily_usd: capToInput(o.daily_usd), hourly_usd: capToInput(o.hourly_usd), rpm: capToInput(o.rpm) });
    setReason('');
    setServerErr(undefined);
  }, [d]);

  // A server error describes the values that were sent: drop it once the admin edits any of them.
  useEffect(() => setServerErr(undefined), [values, reason]);

  const parsed = useMemo(() => {
    const out = {} as Record<Field, [number | null, string | undefined]>;
    for (const f of CAP_FIELDS) out[f] = parseCap(values[f]);
    out.rpm = parseCap(values.rpm, 1, 100000, true);
    return out;
  }, [values]);

  const changes = useMemo(() => {
    const body: CapFields = {};
    if (!d) return body;
    for (const f of Object.keys(parsed) as Field[]) {
      const [v, err] = parsed[f];
      if (err) continue;
      const cur = d.override[f] ?? null;
      if (v !== cur) body[f] = v;
    }
    return body;
  }, [parsed, d]);

  const zeroCaps = CAP_FIELDS.filter((f) => changes[f] === 0);
  const hasErrors = Object.values(parsed).some(([, e]) => e);
  const reasonErr = zeroCaps.length && !reason.trim() ? 'A reason is required when a cap is set to 0' : undefined;
  const nChanges = Object.keys(changes).length;

  const close = (changed: boolean) => {
    setConfirming(false);
    onClose(changed);
  };

  const save = () => once(async () => {
    if (!user) return;
    setSaving(true);
    setServerErr(undefined);
    try {
      await apiWrite<WriteResult>('PUT', `/api/v2/users/${encodeURIComponent(user)}/limits`, { ...changes, version: d?.version, ...(reason.trim() ? { reason: reason.trim() } : {}) });
      notify({ type: 'success', header: `Limits for ${user} saved`, content: 'The gateway applies the change within a minute.' });
      close(true);
    } catch (e) {
      setConfirming(false);
      setServerErr(writeErrors(e, `Saving limits for ${user}`, detail.reload));
    } finally {
      setSaving(false);
    }
  });

  const errFor = (f: string, local?: string) => local ?? (serverErr?.field === f ? serverErr.message : undefined);

  return (
    <>
      <Modal
        visible={user !== null && !confirming}
        onDismiss={() => close(false)}
        size="large"
        header={`Edit limits: ${user ?? ''}`}
        closeAriaLabel="Close"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => close(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!d || hasErrors || !!reasonErr || nChanges === 0}
                loading={saving}
                onClick={() => (zeroCaps.length ? setConfirming(true) : save())}
              >
                Save changes
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {detail.loading && !d ? (
          <Spinner />
        ) : detail.error ? (
          <ErrorState error={detail.error} onRetry={detail.reload} what="current limits" />
        ) : d ? (
          <Form>
            <SpaceBetween size="l">
              <Box variant="p" color="text-body-secondary">
                A user override wins over group, org and default caps. Leave a field empty to remove the override and fall back to the next
                source.
              </Box>
              <ColumnLayout columns={3} borders="vertical">
                {PERIODS.map((p) => {
                  const lim = d.limits.find((l) => l.period === p);
                  const f = CAP_FIELD[p];
                  return (
                    <FormField
                      key={p}
                      label={`${p} cap (USD)`}
                      description={
                        <SpaceBetween size="xxxs">
                          <span>
                            Effective {lim?.cap === null || lim?.cap === undefined ? 'no cap' : usd(lim.cap)} {lim && <SourceBadge source={lim.source} />}
                            {lim?.boost ? ` (${usd(lim.base)} plus a ${usd(lim.boost)} boost)` : ''}
                          </span>
                          <span>
                            Spent {usd(lim?.spent)}, resets {utc(lim?.resets_at)}
                          </span>
                        </SpaceBetween>
                      }
                      constraintText="0 to 100,000. 0 blocks the user."
                      errorText={errFor(f, parsed[f][1])}
                    >
                      <Input
                        type="number"
                        inputMode="decimal"
                        value={values[f]}
                        autoFocus={p === (focus ?? 'Monthly')}
                        placeholder="Not set"
                        onChange={({ detail: x }) => setValues((v) => ({ ...v, [f]: x.value }))}
                      />
                    </FormField>
                  );
                })}
              </ColumnLayout>
              <FormField
                label="Requests per minute (interceptor)"
                description={`Effective ${d.rpm.value ?? 'no limit'}${d.rpm.source ? ` from ${d.rpm.source}` : ''}`}
                constraintText="Whole number, 1 to 100,000."
                errorText={errFor('rpm', parsed.rpm[1])}
              >
                <Input type="number" inputMode="numeric" value={values.rpm} placeholder="Not set" onChange={({ detail: x }) => setValues((v) => ({ ...v, rpm: x.value }))} />
              </FormField>
              {d.boost && (
                <Alert type="info" header="Active boost">
                  +{usd(d.boost.usd)} on the {d.boost.period.toLowerCase()} cap until {utc(d.boost.until)}: {d.boost.reason}
                  {d.boost.in_effect === false ? ' (held back while that period has no cap or a $0 cap; it applies again if a cap above $0 is set before it ends)' : ''}
                </Alert>
              )}
              <FormField
                label={
                  <span>
                    Reason <i>{zeroCaps.length ? '- required' : '- optional'}</i>
                  </span>
                }
                description="Saved in the audit log."
                errorText={errFor('reason', reasonErr)}
              >
                <Textarea value={reason} rows={2} onChange={({ detail: x }) => setReason(x.value)} placeholder="Why this change" />
              </FormField>
            </SpaceBetween>
          </Form>
        ) : null}
      </Modal>
      <ConfirmModal
        visible={confirming}
        header={`Block ${user}?`}
        impact={
          <>
            Setting the {zeroCaps.map((f) => f.replace('_usd', '')).join(' and ')} cap to $0 blocks <b>{user}</b> now. Their next opencode
            request is refused with the budget message until the cap is raised.
            {d?.boost && zeroCaps.includes(CAP_FIELD[d.boost.period]) && (
              <> Their {usd(d.boost.usd)} {d.boost.period.toLowerCase()} boost is held back while the cap is $0 and applies again if the cap is raised before {utc(d.boost.until)}; End boost on the user's page (or in Budgets & limits, Active boosts) removes it.</>
            )}
          </>
        }
        confirmWord={user ?? ''}
        confirmLabel="Block user"
        loading={saving}
        onConfirm={save}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
