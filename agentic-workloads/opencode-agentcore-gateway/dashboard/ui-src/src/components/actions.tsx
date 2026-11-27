// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Small admin actions the pages share: end a boost now, repair an unreadable usage record.
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import { useState } from 'react';
import { apiWrite } from '../api';
import { READ_ONLY_REASON, useApp, useWriteErrors } from '../context';
import { useInFlight } from '../inflight';
import { ConfirmModal } from './ConfirmModal';

/** "End boost" for one user: DELETE /api/v2/users/{user}/boost. version: the user's limits version the row was shown
 * with, so ending a boost from a stale page (another admin granted a new one since) is a 409, not the new boost gone. */
export function EndBoostButton({ user, version, onDone }: { user: string; version?: string; onDone: () => void }) {
  const { isAdmin, notify } = useApp();
  const onErr = useWriteErrors();
  const once = useInFlight();
  const [busy, setBusy] = useState(false);
  const end = () => once(async () => {
    setBusy(true);
    try {
      await apiWrite('DELETE', `/api/v2/users/${encodeURIComponent(user)}/boost`, { reason: 'ended from the dashboard', ...(version ? { version } : {}) });
      notify({ type: 'success', header: `Boost for ${user} ended`, content: 'The gateway applies it within a minute.' });
      onDone();
    } catch (e) { onErr(e, `Ending the boost for ${user}`, onDone); } finally { setBusy(false); }
  });
  return <Button variant="inline-link" loading={busy} disabled={!isAdmin || version === undefined} disabledReason={isAdmin ? 'Loading the current settings' : READ_ONLY_REASON} onClick={end}>End boost</Button>;
}

/** "Repair record" for one user's window whose usage record the gateway cannot read (a hand edit): POST
 * /api/v2/users/{user}/repair removes only the unreadable spend values, with a reason for the audit log. Raising the
 * cap or a boost cannot help such a row: the gateway refuses a capped unreadable window whatever the cap. */
export function RepairButton({ user, period, capped, removed, onDone }: { user: string; period: string; capped: boolean; removed?: boolean; onDone: () => void }) {
  const { isAdmin, notify } = useApp();
  const onErr = useWriteErrors();
  const once = useInFlight();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const repair = () => once(async () => {
    setBusy(true);
    try {
      await apiWrite('POST', `/api/v2/users/${encodeURIComponent(user)}/repair`, { period, reason: reason.trim() });
      notify({ type: 'success', header: `${period} usage record for ${user} repaired`, content: 'Model spend is set again by the next usage sync (within 5 minutes).' });
      setOpen(false);
      onDone();
    } catch (e) { onErr(e, `Repairing ${user}'s ${period.toLowerCase()} usage record`, onDone); } finally { setBusy(false); }
  });
  return (
    <>
      <Button variant="inline-link" disabled={!isAdmin || !!removed} disabledReason={!isAdmin ? READ_ONLY_REASON : 'Removed from the deployment\'s users: add them back in Terraform to edit'} onClick={() => { setReason(''); setOpen(true); }}>Repair record</Button>
      <ConfirmModal visible={open} header={`Repair ${user}'s ${period.toLowerCase()} usage record`} confirmLabel="Repair" loading={busy}
        impact={<>The values in this window that are not valid amounts are removed. {capped ? 'The gateway stops refusing calls for it. ' : ''}
          Web search spend in this window restarts at $0; model spend is set again by the next usage sync.</>}
        onConfirm={() => { if (reason.trim()) repair(); }} onCancel={() => setOpen(false)}>
        <FormField label="Reason" description="Saved in the audit log." errorText={reason.trim() ? undefined : 'A reason is required.'}>
          <Input value={reason} onChange={({ detail }) => setReason(detail.value)} ariaRequired />
        </FormField>
      </ConfirmModal>
    </>
  );
}
