// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Textarea from '@cloudscape-design/components/textarea';
import { useEffect, useState } from 'react';
import { useInFlight } from '../inflight';
import { apiWrite, useApi } from '../api';
import { useApp, useWriteErrors } from '../context';
import { utc } from '../format';
import type { Limit, Period, UserDetail, WriteResult } from '../types';
import { PERIODS } from '../types';
import { parseCap } from '../validate';
import { ErrorState } from './common';
import { boostRefusal, takesBoost } from '../boost';

// The Grant boost buttons: enabled while the rows are unknown (the dialog says why a period is refused) or one period takes it.
export const canBoost = (rows: Limit[] | undefined) => !rows || rows.some(takesBoost);

// version and limits: the user's limits version and rows when the caller already has them (the user page), so no
// extra fetch is needed. A period with no cap is not offered: the API refuses a boost that would change nothing.
export function BoostModal({ user, period, version, limits, onClose }: { user: string | null; period?: Period; version?: string; limits?: Limit[]; onClose: (changed: boolean) => void }) {
  const { notify } = useApp();
  const writeErrors = useWriteErrors();
  const [amount, setAmount] = useState('');
  const [p, setP] = useState<Period>(period ?? 'Monthly');
  const [hours, setHours] = useState('24');
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
  const [serverErr, setServerErr] = useState<{ field: string; message: string } | undefined>();
  const [saving, setSaving] = useState(false);
  // The user's current limits version, sent with the grant so another admin's change in between is a 409.
  const detail = useApi<UserDetail>(user && (version === undefined || limits === undefined) ? `/api/v2/users/${encodeURIComponent(user)}` : null);
  // The settled answer only, so a stale version never gives a false 409.
  const mine = detail.settled?.user === user ? detail.settled : undefined;
  const fresh = version ?? mine?.version;
  const rows = limits ?? mine?.limits;
  const refusal = (x: Period) => boostRefusal(rows, x);
  const refused = (x: Period) => refusal(x) !== undefined;
  // The first period the API accepts (undefined until the limits load, or when none does).
  const firstOk = rows ? PERIODS.find((x) => !refused(x)) : undefined;
  // Without a period from the caller the dialog opens on firstOk (in the reset below), and moves there whenever it
  // sits on a refused period, e.g. when the limits arrive after it opened. It only moves off a refused period.
  const start = () => period ?? firstOk ?? 'Monthly';
  const better = !period && refused(p) ? firstOk : undefined;
  useEffect(() => {
    if (user && better) setP(better);
  }, [user, better]);
  const once = useInFlight();

  useEffect(() => {
    if (user) {
      setAmount('');
      setP(start());
      setHours('24');
      setReason('');
      setTouched(false);
      setServerErr(undefined);
    }
  }, [user, period]);

  // A server error describes the values that were sent: drop it once the admin edits any of them.
  useEffect(() => setServerErr(undefined), [amount, p, hours, reason]);

  const [usdV, usdErr0] = parseCap(amount, 0.01, 100000);
  const usdErr = usdErr0 ?? (touched && usdV === null ? 'Enter an amount' : undefined);
  const [hoursV, hoursErr0] = parseCap(hours, 1, 720, true);
  const hoursErr = hoursErr0 ?? (hoursV === null ? 'Enter 1 to 720 hours' : undefined);
  const reasonErr = touched && !reason.trim() ? 'A reason is required for a boost' : undefined;
  const until = hoursV ? new Date(Date.now() + hoursV * 3600000).toISOString() : null;
  const errFor = (f: string, local?: string) => local ?? (serverErr?.field === f ? serverErr.message : undefined);

  const save = () => once(async () => {
    setTouched(true);
    if (!user || usdV === null || hoursV === null || !reason.trim() || refused(p)) return;
    setSaving(true);
    setServerErr(undefined);
    try {
      await apiWrite<WriteResult>('POST', `/api/v2/users/${encodeURIComponent(user)}/boost`, { usd: usdV, period: p, hours: hoursV, reason: reason.trim(), version: fresh });
      notify({ type: 'success', header: `Boost granted to ${user}`, content: `+$${usdV} on the ${p.toLowerCase()} cap until ${utc(until)}.` });
      onClose(true);
    } catch (e) {
      // On a 409 the version came from the caller's page (reload it by closing) or from our own fetch (refetch).
      setServerErr(writeErrors(e, `Granting a boost to ${user}`, version !== undefined ? () => onClose(true) : detail.reload, ['usd', 'period', 'hours', 'reason']));
    } finally {
      setSaving(false);
    }
  });

  return (
    <Modal
      visible={user !== null}
      onDismiss={() => onClose(false)}
      header={`Grant temporary boost: ${user ?? ''}`}
      closeAriaLabel="Close"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => onClose(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={saving || (fresh === undefined && !detail.error)} disabled={!!usdErr0 || !!hoursErr || fresh === undefined || refused(p)} onClick={save}>
              Grant boost
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="l">
          {detail.error && (
            <ErrorState error={detail.error} onRetry={detail.reload} what="the user's current limits (needed to grant a boost)" />
          )}
          <Box variant="p" color="text-body-secondary">
            A boost adds to the user's effective cap for one period until it expires. It replaces any boost the user already has.
          </Box>
          <ColumnLayout columns={3}>
            <FormField label="Amount (USD)" constraintText="0.01 to 100,000" errorText={errFor('usd', usdErr)}>
              <Input type="number" inputMode="decimal" value={amount} autoFocus onChange={({ detail }) => setAmount(detail.value)} placeholder="50" />
            </FormField>
            <FormField label="Period" errorText={errFor('period', refusal(p) === 'no cap' ? `No ${p.toLowerCase()} cap: a boost would change nothing` : refusal(p) ? `The ${p.toLowerCase()} cap is $0 (a block): raise the cap instead` : undefined)}>
              <Select
                selectedOption={{ value: p, label: p }}
                options={PERIODS.map((x) => ({ value: x, label: x, disabled: refused(x), description: refusal(x) }))}
                onChange={({ detail }) => setP(detail.selectedOption.value as Period)}
              />
            </FormField>
            <FormField label="Duration (hours)" constraintText={until ? `Ends ${utc(until)}` : '1 to 720'} errorText={errFor('hours', hoursErr)}>
              <Input type="number" inputMode="numeric" value={hours} onChange={({ detail }) => setHours(detail.value)} />
            </FormField>
          </ColumnLayout>
          <FormField label="Reason" description="Required. Saved in the audit log." errorText={errFor('reason', reasonErr)}>
            <Textarea value={reason} rows={2} onChange={({ detail }) => setReason(detail.value)} placeholder="Release week, incident follow-up, ..." />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  );
}
