// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import { useEffect, useState } from 'react';
import { useInFlight } from '../inflight';
import { apiWrite, useApi } from '../api';
import { READ_ONLY_REASON, useApp, useWriteErrors } from '../context';
import { usd, utc } from '../format';
import { parseCap } from '../validate';
import type { GroupRow, Policy } from '../types';
import { CAP_FIELD, CAP_FIELDS, PERIODS } from '../types';
import { EndBoostButton } from '../components/actions';
import { ConfirmModal } from '../components/ConfirmModal';
import { DataTable, Page } from './_kit';

const FIELDS = [...PERIODS.map((p) => [CAP_FIELD[p], `${p} cap (USD)`] as const), ['rpm', 'Requests per minute'] as const];
type Vals = Record<string, string>;
// An empty field clears the cap (null); anything else must parse, or Number() would give NaN, JSON would send null and
// the API would silently remove the cap.
const toBody = (v: Vals) => Object.fromEntries(FIELDS.map(([k, label]) => {
  const [n, err] = parseCap(v[k] ?? '', k === 'rpm' ? 1 : 0, 100000, k === 'rpm');  // same upper bound for rpm and USD caps
  if (err) throw new Error(`${label}: ${err}`);
  return [k, n];
}));
const fromObj = (o: Record<string, unknown> | undefined) => Object.fromEntries(FIELDS.map(([k]) => [k, o?.[k] === null || o?.[k] === undefined ? '' : String(o[k])]));

// Asked for before the typed confirmation, so the admin does not confirm a save the server then refuses (400).
const ZERO_REASON = 'A reason is required when a cap is set to 0';

const LIMIT_FIELDS: string[] = [...CAP_FIELDS, 'rpm'];

function CapFieldsForm({ vals, set, err, readOnly }: { vals: Vals; set: (v: Vals) => void; err?: { field: string; message: string }; readOnly?: boolean }) {
  return (
    <ColumnLayout columns={4}>
      {FIELDS.map(([k, label]) => (
        <FormField key={k} label={label} constraintText="Empty = not set here" errorText={err?.field === k ? err.message : undefined}>
          <Input type="number" inputMode="decimal" value={vals[k] ?? ''} readOnly={readOnly} onChange={e => set({ ...vals, [k]: e.detail.value })} />
        </FormField>))}
    </ColumnLayout>
  );
}

export default function Budgets() {
  const pol = useApi<Policy>('/api/v2/policy');
  const groups = useApi<{ groups: GroupRow[] }>('/api/v2/groups');
  const { isAdmin, notify } = useApp();
  const onErr = useWriteErrors();
  const [org, setOrg] = useState<Vals>({});
  const [reason, setReason] = useState('');
  const [err, setErr] = useState<{ field: string; message: string }>();
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<GroupRow | null>(null);
  const [gv, setGv] = useState<Vals>({});
  const [gReason, setGReason] = useState('');
  const [gErr, setGErr] = useState<{ field: string; message: string }>();
  const [gBusy, setGBusy] = useState(false);
  // The org form fills from, and saves the version of, the settled policy only. While a reload runs (after a save) the
  // fields are read-only and Save waits, so neither a stale version (a false 409) nor the refill can undo new typing.
  const settled = pol.settled;
  useEffect(() => { if (settled) setOrg(fromObj(settled.org as unknown as Record<string, unknown>)); }, [settled]);

  // One guard per action, so a group save is never dropped because an org save is still in flight.
  // A $0 cap blocks everyone it applies to, so newly setting one asks for a typed confirmation first, as blocking a
  // single user does in Edit limits.
  const [confirm, setConfirm] = useState<'org' | 'group' | null>(null);
  const newZeros = (vals: Vals, before: Record<string, unknown> | undefined) =>
    CAP_FIELDS.filter(k => (vals[k] ?? '').trim() !== '' && Number(vals[k]) === 0 && before?.[k] !== 0);
  const orgZeros = newZeros(org, settled?.org as unknown as Record<string, unknown>);
  const groupZeros = edit ? newZeros(gv, edit.policy as Record<string, unknown>) : [];
  const orgOnce = useInFlight();
  const groupOnce = useInFlight();
  const saveOrg = () => orgOnce(async () => {
    if (!settled) return;
    setBusy(true); setErr(undefined);
    try {
      await apiWrite('PUT', '/api/v2/policy/org', { ...toBody(org), version: settled.org.version ?? null, reason: reason || undefined });
      notify({ type: 'success', header: 'Organization policy saved', content: 'The gateway applies it within a minute.' });
      setReason('');  // the reason belonged to this save; the next one must not be audited with it
      pol.reload();
    } catch (e) { setErr(onErr(e, 'Saving the organization policy', pol.reload, [...LIMIT_FIELDS, 'reason'])); } finally { setBusy(false); }
  });
  const saveGroup = () => groupOnce(async () => {
    if (!edit) return;
    setGBusy(true); setGErr(undefined);
    try {
      await apiWrite('PUT', `/api/v2/policy/groups/${encodeURIComponent(edit.group)}`, { ...toBody(gv), version: edit.version, reason: gReason || undefined });
      notify({ type: 'success', header: `Limits for group ${edit.group} saved`, content: 'The gateway applies them within a minute.' });
      setEdit(null); groups.reload();
    } catch (e) {
      // On a 409 close the editor as well as reloading: it holds the old row (and version), so a retry from it would
      // only get 409 again. Reopening Edit starts from the current values.
      setGErr(onErr(e, `Saving group ${edit.group}`, () => { setEdit(null); groups.reload(); }, [...LIMIT_FIELDS, 'reason']));
    } finally { setGBusy(false); }
  });
  // Clear the group's limits only: null caps fall back to the organization and deployment defaults, and the group's
  // model allow-list (edited on Model access, in the same POLICY item) stays as it is. DELETE would remove both.
  const clearGroupLimits = () => groupOnce(async () => {
    if (!edit) return;
    setGBusy(true); setGErr(undefined);
    try {
      await apiWrite('PUT', `/api/v2/policy/groups/${encodeURIComponent(edit.group)}`,
        { ...Object.fromEntries(LIMIT_FIELDS.map(k => [k, null])), version: edit.version, reason: gReason || undefined });
      notify({ type: 'success', header: `Limits for group ${edit.group} cleared`, content: 'Its members get the organization and deployment limits within a minute; its model access is unchanged.' });
      setEdit(null); groups.reload();
    } catch (e) {
      setGErr(onErr(e, `Clearing the limits for group ${edit.group}`, () => { setEdit(null); groups.reload(); }, [...LIMIT_FIELDS, 'reason']));
    } finally { setGBusy(false); }
  });
  const d = pol.data;
  return (
    <Page title="Budgets & limits" description="Dollar caps per month, day and hour (UTC) and a requests-per-minute cap. A user override beats the most generous group, which beats the org policy, which beats the default.">
      <SpaceBetween size="l">
        <Form actions={<Button variant="primary" loading={busy || (pol.loading && !pol.error)} disabled={!isAdmin || !settled} disabledReason={isAdmin ? undefined : READ_ONLY_REASON} onClick={() => (!orgZeros.length ? saveOrg() : reason.trim() ? setConfirm('org') : setErr({ field: 'reason', message: ZERO_REASON }))}>Save organization policy</Button>}>
          <Container header={<Header variant="h2" description={d ? `Defaults from deployment: monthly ${usd(d.env_defaults.monthly_usd)}, daily ${usd(d.env_defaults.daily_usd)}, hourly ${usd(d.env_defaults.hourly_usd)}` : undefined}>Organization policy</Header>}>
            <SpaceBetween size="m">
              <CapFieldsForm vals={org} set={v => { setOrg(v); if (err && LIMIT_FIELDS.includes(err.field)) setErr(undefined); }} err={err} readOnly={!settled} />
              <FormField label="Reason (written to the audit log)" errorText={err?.field === 'reason' ? err.message : undefined}>
                <Input value={reason} onChange={e => { setReason(e.detail.value); if (err?.field === 'reason') setErr(undefined); }} placeholder="Required when a cap is 0" />
              </FormField>
            </SpaceBetween>
          </Container>
        </Form>
        <DataTable<GroupRow> title="Group limits" items={groups.data?.groups ?? []} load={groups} filterText={g => g.group} columns={[
          { id: 'g', header: 'Group', cell: g => g.group },
          { id: 'm', header: 'Members seen', cell: g => g.members_seen.join(', ') || '-' },
          { id: 'mtd', header: 'Spend (MTD)', cell: g => usd(g.mtd.usd) },
          ...FIELDS.map(([k, label]) => ({ id: k, header: label, cell: (g: GroupRow) => { const v = (g.policy as Record<string, unknown>)[k]; return v === undefined || v === null ? '-' : k === 'rpm' ? String(v) : usd(v as number); } })),
          { id: 'a', header: 'Actions', cell: g => <Button variant="inline-link" disabled={!isAdmin || g.editable === false || !groups.settled} disabledReason={!isAdmin ? READ_ONLY_REASON : g.editable === false ? 'Users without groups get the organization policy; edit that instead.' : 'Loading the current settings'} onClick={() => { const cur = groups.settled?.groups.find(x => x.group === g.group); if (!cur) return; setEdit(cur); setGv(fromObj(cur.policy as Record<string, unknown>)); setGReason(''); setGErr(undefined); }}>Edit</Button> },
        ]} />
        <DataTable title="Active boosts" items={d?.boosts ?? []} load={pol} empty="No active boosts" columns={[
          { id: 'u', header: 'User', cell: b => b.user }, { id: 'a', header: 'Amount', cell: b => usd(b.usd) }, { id: 'p', header: 'Period', cell: b => (b.in_effect === false ? `${b.period} (held back: no cap or a $0 cap)` : b.period) },
          { id: 't', header: 'Until (UTC)', cell: b => utc(b.until) }, { id: 'r', header: 'Reason', cell: b => b.reason },
          { id: 'x', header: 'Actions', cell: b => <EndBoostButton user={b.user} version={pol.settled ? b.version : undefined} onDone={pol.reload} /> }]} />
      </SpaceBetween>
      <Modal visible={!!edit && confirm !== 'group'} onDismiss={() => setEdit(null)} header={`Edit limits for group ${edit?.group ?? ''}`} size="large"
        footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs"><Button variant="link" onClick={() => setEdit(null)}>Cancel</Button>{edit?.version != null ? <Button loading={gBusy} onClick={clearGroupLimits}>Clear group limits</Button> : null /* any stored version, a hand-written 0 or "" included, as put_policy reads one */}<Button variant="primary" loading={gBusy} onClick={() => (!groupZeros.length ? saveGroup() : gReason.trim() ? setConfirm('group') : setGErr({ field: 'reason', message: ZERO_REASON }))}>Save</Button></SpaceBetween></Box>}>
        <SpaceBetween size="m">
          <CapFieldsForm vals={gv} set={v => { setGv(v); if (gErr && LIMIT_FIELDS.includes(gErr.field)) setGErr(undefined); }} err={gErr} />
          <FormField label="Reason" errorText={gErr?.field === 'reason' ? gErr.message : undefined}><Input value={gReason} onChange={e => { setGReason(e.detail.value); if (gErr?.field === 'reason') setGErr(undefined); }} placeholder="Required when a cap is 0" /></FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmModal
        visible={confirm !== null}
        header={confirm === 'org' ? 'Block users on the organization policy?' : `Block group ${edit?.group ?? ''}?`}
        impact={<>Setting the {(confirm === 'org' ? orgZeros : groupZeros).map(f => f.replace('_usd', '')).join(' and ')} cap to $0 blocks{' '}
          {confirm === 'org' ? 'every user who has no user or group cap for that period' : <>every member of <b>{edit?.group}</b> without a user cap or a higher group cap</>}.
          Their next request is refused with the budget message until the cap is raised. Any live boost a member has on that
          period is held back while the cap is $0 and applies again if the cap is raised before it ends (End boost, in the
          Active boosts list here, removes it).</>}
        confirmWord={confirm === 'org' ? 'org' : edit?.group ?? ''}
        confirmLabel="Block"
        loading={confirm === 'org' ? busy : gBusy}
        onConfirm={() => { const which = confirm; setConfirm(null); if (which === 'org') saveOrg(); else saveGroup(); }}
        onCancel={() => setConfirm(null)}
      />
    </Page>
  );
}
