// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import DatePicker from '@cloudscape-design/components/date-picker';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import Pagination from '@cloudscape-design/components/pagination';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import Textarea from '@cloudscape-design/components/textarea';
import TextFilter from '@cloudscape-design/components/text-filter';
import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import type { AuditItem, ChatLimits, Me, Role, SampleDataResult, User } from '@/api/types';
import { useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import DigestChannelsAdmin from '@/components/DigestChannelsAdmin';
import EntityHygienePanel from '@/components/EntityHygiene';
import { useNotify } from '@/components/Notifications';
import SkillLibrary from '@/components/SkillLibrary';
import UnsavedGuard from '@/components/UnsavedGuard';
import { WholeNumber } from '@/components/wholeNumber';
import { useAsync } from '@/hooks/useAsync';
import { actorLabel, ago, clip, moduleName, nodeHref, plural, sentence, tokens, when } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';
import { sectionVisible } from '@/lib/profile';

import { RunState } from './ModulesPage';

const ROLE_INFO: Record<Role, { label: string; description: string }> = {
  viewer: { label: 'Viewer', description: 'Reads everything, asks questions, cannot change anything' },
  editor: { label: 'Editor', description: 'Also creates and edits radars, reviews suggestions, votes, drafts content' },
  admin: { label: 'Administrator', description: 'Also manages modules, products, users, budgets and the AI kill switch' },
};
const roleOption = (value: Role) => ({ value, ...ROLE_INFO[value] });
const ROLES = (Object.keys(ROLE_INFO) as Role[]).map(roleOption);

const USERS_PAGE = 25;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Cognito's own status words, as a reader of the Users table would put them. */
const USER_STATUS: Record<string, string> = {
  CONFIRMED: 'Active',
  UNCONFIRMED: 'Invited, not signed in yet',
  FORCE_CHANGE_PASSWORD: 'Invited, first sign-in pending',
  RESET_REQUIRED: 'Password reset required',
  ARCHIVED: 'Archived',
  EXTERNAL_PROVIDER: 'Signs in through another provider',
};

export const userStatus = (status: string): string => USER_STATUS[status] ?? sentence(status.toLowerCase().replace(/_/g, ' '));

/** The signed-in administrator's own row: matched by account id, or by email for older accounts. */
const isSelf = (u: User, me: Me) => u.username === me.subject || u.email.toLowerCase() === me.email.toLowerCase();

/** "user:<id>" of the signed-in user reads "you"; another administrator reads as their email where known. */
export function principalLabel(principal: string | null | undefined, me: Me | null, emails?: Map<string, string>): string {
  if (!principal) return 'someone';
  if (me && principal === `user:${me.subject}`) return 'you';
  if (principal.startsWith('user:')) return emails?.get(principal.slice('user:'.length)) ?? 'another administrator';
  return principal;
}

type Pending = { user: User; changes: { role?: Role; enabled?: boolean } };

/**
 * The radars a user owns, for the delete dialog: deleting the account leaves them with a dead owner,
 * and nothing named them before (r3 F3-025). Each links to its Settings, where Transfer ownership is.
 */
function OwnedRadars({ username, subject }: { username: string; subject?: string }) {
  const { api } = useSession();
  const navigate = useNavigate();
  const radars = useAsync(() => api.radars(), [api]);
  if (radars.error) return <Box color="text-status-warning">{`The radars they own could not be checked: ${radars.error.message}`}</Box>;
  if (!radars.data) return <StatusIndicator type="loading">Checking which radars they own</StatusIndicator>;
  // A radar names its owner by subject, which is the username except for a federated user.
  const owned = radars.data.items.filter((r) => r.owner === username || (subject !== undefined && r.owner === subject));
  if (!owned.length) return <Box>They own no radars.</Box>;
  return (
    <SpaceBetween size="xxs">
      <Box color="text-status-warning">{`They own ${plural(owned.length, 'radar')}, which would be left with no owner. Transfer each one first, under its Settings:`}</Box>
      <ul className="air-evidence">
        {owned.map((r) => (
          <li key={r.slug}>
            <Link href={`/radars/${r.slug}?tab=settings`} onFollow={(e) => { e.preventDefault(); navigate(`/radars/${r.slug}?tab=settings`); }}>
              {r.name}
            </Link>
          </li>
        ))}
      </ul>
    </SpaceBetween>
  );
}

/** Which row is being changed, so Enable cannot be clicked twice on a slow link. */
type Applying = string | null;

function Users() {
  const { api, me, config } = useSession();
  const { fail, notify } = useNotify();
  // Federated sign-in: no invitations; people appear here after their first sign-in.
  const federated = Boolean(config.identity_provider);
  const howUsersSignIn = federated
    ? 'People sign in through the identity provider and appear here after their first sign-in, as viewers; change their role here. The administrators named in the deployment (adminEmails) become administrators again at every sign-in: to remove one, change that deploy option.'
    : 'Users sign in with their email and set up an authenticator app at first sign-in.';
  const users = useAsync(() => api.users(), [api]);
  const [inviting, setInviting] = React.useState(false);
  const [email, setEmail] = React.useState('');
  const [role, setRole] = React.useState<Role>('viewer');
  const [deleting, setDeleting] = React.useState<User | null>(null);
  const [pending, setPending] = React.useState<Pending | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [filter, setFilter] = React.useState('');
  const [page, setPage] = React.useState(1);
  const self = me as Me; // the page is shown only to signed-in administrators
  const all = users.data?.items ?? [];
  // Every page is read; only a pool larger than MAX_PAGES pages is shown in part.
  const capped = Boolean(users.data?.truncated);
  const needle = filter.trim().toLowerCase();
  const shown = needle ? all.filter((u) => u.email.toLowerCase().includes(needle)) : all;
  const pages = Math.max(1, Math.ceil(shown.length / USERS_PAGE));
  const current = Math.min(page, pages);
  const [applying, setApplying] = React.useState<Applying>(null);
  const apply = ({ user, changes }: Pending) => {
    setBusy(true);
    setApplying(user.username);
    void api
      .updateUser(user.username, changes)
      .then(() => {
        setPending(null);
        users.reload();
      }, fail)
      .finally(() => {
        setBusy(false);
        setApplying(null);
      });
  };
  const closeInvite = () => {
    setInviting(false);
    setEmail(''); // the next invitation starts empty
    setRole('viewer');
  };
  const invite = () => {
    if (!EMAIL.test(email.trim()) || busy) return;
    setBusy(true);
    void api
      .inviteUser(email.trim(), role)
      .then(() => {
        notify('success', `Invitation sent to ${email.trim()}.`);
        closeInvite();
        users.reload();
      }, fail)
      .finally(() => setBusy(false));
  };
  return (
    <>
      <Table wrapLines
        loading={users.loading && !users.data}
        items={shown.slice((current - 1) * USERS_PAGE, current * USERS_PAGE)}
        trackBy="username"
        variant="container"
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find users by email"
            filteringAriaLabel="Find users by email"
            countText={needle ? `${shown.length} ${shown.length === 1 ? 'match' : 'matches'}` : undefined}
            onChange={({ detail }) => {
              setFilter(detail.filteringText);
              setPage(1);
            }}
          />
        }
        pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        header={
          <Header
            // No "(0)" while the list loads: it read as an empty pool (r3 F3-094).
            counter={users.data ? (capped ? `(${all.length}+)` : `(${all.length})`) : undefined}
            description={
              capped
                ? `Showing the first ${all.length} users; the filter searches only these. ${howUsersSignIn}`
                : howUsersSignIn
            }
            actions={federated ? undefined : <Button variant="primary" onClick={() => setInviting(true)}>Invite user</Button>}
          >
            Users
          </Header>
        }
        empty={
          <Box textAlign="center" padding="m">
            {users.error ? (
              <SpaceBetween size="xs" alignItems="center">
                <StatusIndicator type="error">{`The users could not be loaded: ${users.error.message}`}</StatusIndicator>
                <Button onClick={users.reload}>Retry</Button>
              </SpaceBetween>
            ) : needle ? (
              'No users match.'
            ) : (
              'No users.'
            )}
          </Box>
        }
        columnDefinitions={[
          { id: 'email', header: 'Email', cell: (u) => u.email },
          {
            id: 'role',
            header: 'Role',
            cell: (u) =>
              isSelf(u, self) ? (
                <SpaceBetween size="xxxs">
                  <Box>{ROLE_INFO[u.role].label}</Box>
                  <Box fontSize="body-s" color="text-body-secondary">You cannot change your own role; another administrator can.</Box>
                </SpaceBetween>
              ) : (
                // A pick asks first: arrowing through the list must not save every role it passes.
                <Select
                  expandToViewport
                  ariaLabel={`Role of ${u.email}`}
                  selectedOption={roleOption(u.role)}
                  onChange={({ detail }) => detail.selectedOption.value !== u.role && setPending({ user: u, changes: { role: detail.selectedOption.value as Role } })}
                  options={ROLES}
                />
              ),
          },
          {
            id: 'status',
            header: 'Status',
            // "Invited" covered every status but CONFIRMED, including an account waiting for a reset.
            cell: (u) => (
              <StatusIndicator type={u.enabled ? (u.status === 'CONFIRMED' ? 'success' : 'pending') : 'stopped'}>
                {u.enabled ? userStatus(u.status) : 'Disabled'}
              </StatusIndicator>
            ),
          },
          { id: 'created', header: 'Added', cell: (u) => <span title={when(u.created_at)}>{ago(u.created_at)}</span> },
          {
            id: 'actions',
            header: 'Actions', // a named column header, for screen readers (CTR QA-090)
            cell: (u) =>
              !isSelf(u, self) && (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="inline-link"
                    loading={applying === u.username}
                    disabled={applying !== null && applying !== u.username}
                    onClick={() => (u.enabled ? setPending({ user: u, changes: { enabled: false } }) : apply({ user: u, changes: { enabled: true } }))}
                  >
                    {u.enabled ? 'Disable' : 'Enable'}
                  </Button>
                  <Button variant="inline-link" ariaLabel={`Delete ${u.email}`} onClick={() => setDeleting(u)}>Delete</Button>
                </SpaceBetween>
              ),
          },
        ]}
      />
      {pending && (
        <Modal
          visible
          onDismiss={() => setPending(null)}
          header={pending.changes.role ? 'Change the role?' : 'Disable this user?'}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setPending(null)}>Cancel</Button>
                <Button variant="primary" loading={busy} onClick={() => apply(pending)}>
                  {pending.changes.role ? 'Change role' : 'Disable'}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {pending.changes.role
            ? `${pending.user.email} becomes ${ROLE_INFO[pending.changes.role].label} (was ${ROLE_INFO[pending.user.role].label}): ${ROLE_INFO[pending.changes.role].description.toLowerCase()}.`
            : `${pending.user.email} can no longer sign in until someone enables the account again.`}
        </Modal>
      )}
      {deleting && (
        <ConfirmDelete
          header="Delete user"
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteUser(deleting.username).then(() => {
              setDeleting(null);
              users.reload();
            }, fail)
          }
        >
          <SpaceBetween size="s">
            <Box>
              {federated
                ? `Delete ${deleting.email}? They lose their role and records at once, but they can sign in again through ${config.identity_provider} as a new viewer (an administrator named in the deployment comes back as one). To block them, disable the account instead.`
                : `Delete ${deleting.email}? They lose access at once and this cannot be undone. Disable the user instead to keep the account.`}
            </Box>
            <OwnedRadars username={deleting.username} subject={deleting.subject} />
          </SpaceBetween>
        </ConfirmDelete>
      )}
      {inviting && (
        <Modal
          visible
          onDismiss={closeInvite}
          header="Invite user"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={closeInvite}>Cancel</Button>
                <Button variant="primary" loading={busy} disabled={!EMAIL.test(email.trim())} onClick={invite}>
                  Invite
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              invite();
            }}
          >
            <SpaceBetween size="m">
              <Box color="text-body-secondary">
                They get an email from the app with a temporary password. At first sign-in they choose a new password and set up an
                authenticator app for a one-time code. You can change their role or disable them at any time.
              </Box>
              <FormField label="Email"><Input type="email" value={email} onChange={({ detail }) => setEmail(detail.value)} /></FormField>
              <FormField label="Role"><Select expandToViewport selectedOption={roleOption(role)} onChange={({ detail }) => setRole(detail.selectedOption.value as Role)} options={ROLES} /></FormField>
            </SpaceBetween>
          </form>
        </Modal>
      )}
    </>
  );
}

function SampleData() {
  const { api } = useSession();
  const { fail } = useNotify();
  const navigate = useNavigate();
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<SampleDataResult | null>(null);
  const load = () => {
    setBusy(true);
    void api
      .loadSampleData()
      .then(setResult, fail)
      .finally(() => setBusy(false));
  };
  const go = (href: string) => (e: CustomEvent) => {
    e.preventDefault();
    navigate(href);
  };
  return (
    <Container
      header={
        <Header variant="h2" description="A small, fictional CRM market: companies, products, topics, evidence, signals, trends and a gap, all linked, plus a competitor radar whose entries point at the graph. Loading again adds nothing.">
          Sample data
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Button loading={busy} onClick={load}>Load sample data</Button>
        {result && (
          <Alert type="success">
            Added {result.nodes_created} records and {result.edges_created} links
            {result.skipped.length > 0 ? ` (${result.skipped.length} were already there)` : ''}. Open the{' '}
            {result.radar ? (
              <>
                <Link href={`/radars/${result.radar}`} onFollow={go(`/radars/${result.radar}`)}>sample radar</Link> or the{' '}
              </>
            ) : null}
            <Link href="/graph" onFollow={go('/graph')}>knowledge graph</Link>.
            {result.radar ? null : ' The sample radar was not created: another radar already uses its address.'}
          </Alert>
        )}
      </SpaceBetween>
    </Container>
  );
}

function KillSwitch() {
  const { api, config, me, refreshConfig } = useSession();
  const { fail, notify } = useNotify();
  const state = useAsync(() => api.andon(), [api]);
  // Only administrators see this tab, and they may read the user list: name who paused AI, rather
  // than making them open the audit log to find out.
  const users = useAsync(() => api.users(), [api]);
  const emails = React.useMemo(() => new Map((users.data?.items ?? []).map((u) => [u.username, u.email])), [users.data]);
  const [reason, setReason] = React.useState('');
  const [confirming, setConfirming] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  // Until /api/andon answers (or if it fails), show the state the app itself was configured with.
  const enabled = state.data?.genai_enabled ?? config.genai_enabled;
  const flip = () => {
    setBusy(true);
    void api
      .setAndon(!enabled, reason.trim())
      .then(() => {
        notify(enabled ? 'warning' : 'success', enabled ? 'AI features paused. Running modules stop at their next step.' : 'AI features resumed.');
        setReason('');
        setConfirming(false);
        state.reload();
        refreshConfig(); // the app-wide "AI features are paused" banner
      }, fail)
      .finally(() => setBusy(false));
  };
  return (
    <Container header={<Header variant="h2" description="Pauses every module, chat and product search at once. Radars, the graph, search and exports keep working.">AI kill switch</Header>}>
      <SpaceBetween size="m">
        {state.error && <Alert type="error">Could not read the kill switch: {state.error.message}</Alert>}
        <StatusIndicator type={enabled ? 'success' : 'stopped'}>{enabled ? 'AI features are running' : 'AI features are paused'}</StatusIndicator>
        {state.data?.updated_at && (
          <Box fontSize="body-s" color="text-body-secondary">
            Last changed {ago(state.data.updated_at)} by {principalLabel(state.data.updated_by, me, emails)}
            {state.data.reason ? `: ${state.data.reason}` : ''}
          </Box>
        )}
        <FormField label="Reason (recorded in the audit log)" description={enabled ? 'Required to pause: it tells everyone why AI stopped.' : undefined}>
          <Textarea value={reason} onChange={({ detail }) => setReason(detail.value)} rows={2} />
        </FormField>
        {enabled ? (
          // Pausing stops AI for everyone: it asks first, and needs a reason.
          <Button disabled={!state.data || !reason.trim()} disabledReason={state.data ? 'Enter a reason first.' : 'The current state is not known yet.'} onClick={() => setConfirming(true)}>
            Pause AI features
          </Button>
        ) : (
          <Button variant="primary" loading={busy} disabled={!state.data} onClick={flip}>Resume AI features</Button>
        )}
      </SpaceBetween>
      {confirming && (
        <Modal
          visible
          onDismiss={() => setConfirming(false)}
          header="Pause AI features for everyone?"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setConfirming(false)}>Cancel</Button>
                <Button variant="primary" loading={busy} onClick={flip}>Pause AI features</Button>
              </SpaceBetween>
            </Box>
          }
        >
          Every module stops at its next step, and chat and product search stop for everyone until an administrator resumes them.
        </Modal>
      )}
    </Container>
  );
}

function ChatBudget() {
  const { api } = useSession();
  const limits = useAsync(() => api.chatLimits(), [api]);
  if (limits.error) {
    return (
      <Alert type="error" header="The chat budget could not be loaded" action={<Button onClick={limits.reload}>Retry</Button>}>
        {limits.error.message}
      </Alert>
    );
  }
  if (!limits.data) return null;
  return <ChatBudgetForm key={JSON.stringify(limits.data)} initial={limits.data} onSaved={limits.reload} />;
}

const grouped = (n: number) => n.toLocaleString('en-US');

function ChatBudgetForm({ initial, onSaved }: { initial: ChatLimits; onSaved: () => void }) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const status = useAsync(() => api.chatStatus(), [api]);
  const [daily, setDaily] = React.useState(() => WholeNumber.of(initial.daily_tokens));
  const [perUser, setPerUser] = React.useState(() => WholeNumber.of(initial.per_user_daily_tokens));
  const [busy, setBusy] = React.useState(false);
  const over = daily.value !== null && perUser.value !== null && perUser.value > daily.value;
  const limits = daily.value === null || perUser.value === null || over ? null : { daily_tokens: daily.value, per_user_daily_tokens: perUser.value };
  const dirty = daily.value !== initial.daily_tokens || perUser.value !== initial.per_user_daily_tokens;
  // Save is disabled while either field is invalid, so limits is set whenever save runs.
  const save = () => {
    setBusy(true);
    void api
      .setChatLimits(limits as ChatLimits)
      .then(() => {
        notify('success', 'Saved.');
        onSaved();
      }, fail)
      .finally(() => setBusy(false));
  };
  const used = status.data ? Math.max(0, initial.daily_tokens - status.data.shared.remaining) : null;
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`Daily weighted-token ceilings for the assistant.${used === null ? '' : ` Used today: ${grouped(used)} of ${grouped(initial.daily_tokens)} (${tokens(used)}).`}`}
          actions={
            <Button disabled={!limits || !dirty} disabledReason={limits ? 'Nothing changed yet.' : 'Fix the highlighted field first.'} loading={busy} onClick={save}>
              Save
            </Button>
          }
        >
          Chat budget
        </Header>
      }
    >
      <UnsavedGuard dirty={dirty}>The chat budget has unsaved changes. They are lost if you leave the page now.</UnsavedGuard>
      <ColumnLayout columns={2}>
        <FormField label="Everyone, per day" constraintText={daily.value === null ? undefined : `${grouped(daily.value)} tokens`} errorText={daily.error}>
          <Input type="number" value={daily.text} onChange={({ detail }) => setDaily(daily.with(detail.value))} />
        </FormField>
        <FormField
          label="Each user, per day"
          constraintText={perUser.value === null ? undefined : `${grouped(perUser.value)} tokens`}
          errorText={perUser.error ?? (over ? 'Each user cannot get more than everyone together.' : undefined)}
        >
          <Input type="number" value={perUser.text} onChange={({ detail }) => setPerUser(perUser.with(detail.value))} />
        </FormField>
      </ColumnLayout>
    </Container>
  );
}

const BUDGET_STATES: Record<string, string> = {
  open: 'Can run today',
  warn: "Close to today's budget: 70% or more used",
  // 90% or more used. It was labelled "Trying again after a stop", a circuit-breaker term (CTR HC-013).
  half_open: "Almost out of today's budget: 90% or more used",
  closed: 'Stopped for today: its budget is used up',
  exhausted: 'Stopped for today: its budget is used up',
};

export const budgetState = (state: string): string => BUDGET_STATES[state] ?? sentence(state);

const RUNS_PAGE = 20;

function Health() {
  const { api } = useSession();
  const navigate = useNavigate();
  const health = useAsync(() => api.health(), [api]);
  const runs = useAsync(() => api.runs(), [api]);
  const [page, setPage] = React.useState(1);
  const items = runs.data?.items ?? [];
  const pages = Math.max(1, Math.ceil(items.length / RUNS_PAGE));
  const current = Math.min(page, pages);
  const refresh = () => {
    health.reload();
    runs.reload();
  };
  return (
    <SpaceBetween size="l">
      {health.data && !health.data.genai_enabled && <Alert type="warning">AI features are paused.</Alert>}
      <Table wrapLines
        loading={health.loading && !health.data}
        items={health.data?.budgets ?? []}
        trackBy="module"
        variant="container"
        header={<Header actions={<Button iconName="refresh" onClick={refresh}>Refresh</Button>}>Budgets today</Header>}
        columnDefinitions={[
          { id: 'm', header: 'Module', cell: (b) => moduleName(b.module) },
          {
            id: 'on',
            header: 'Runs on schedule',
            // The drafts module never runs on a schedule: its flag is whether editors may request drafts (r3 F3-028).
            cell: (b) => (b.module === 'draft' ? (b.enabled ? 'Never: editors request drafts' : 'Off: editors cannot request drafts') : b.enabled ? 'Yes' : 'No (Run now still works)'),
          },
          { id: 'used', header: 'Used', cell: (b) => `${tokens(b.consumed)} of ${tokens(b.limit)}` },
          { id: 'state', header: 'Budget', cell: (b) => budgetState(b.state) },
        ]}
        empty={<Box textAlign="center" padding="m">{health.error ? <StatusIndicator type="error">{`The health check failed: ${health.error.message}`}</StatusIndicator> : 'No modules.'}</Box>}
      />
      <Table wrapLines
        loading={runs.loading && !runs.data}
        items={items.slice((current - 1) * RUNS_PAGE, current * RUNS_PAGE)}
        trackBy="run_id"
        variant="container"
        header={
          <Header
            counter={runs.data ? `(${items.length}${runs.data.truncated ? '+' : ''})` : undefined}
            description={runs.data?.truncated ? `Showing the first ${items.length} runs; older ones are not loaded.` : undefined}
          >
            Recent runs
          </Header>
        }
        pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        columnDefinitions={[
          {
            id: 'm',
            header: 'Module',
            cell: (r) => (
              <Link href={`/modules/${r.module}`} onFollow={(e) => { e.preventDefault(); navigate(`/modules/${r.module}`); }}>
                {moduleName(r.module)}
              </Link>
            ),
          },
          { id: 's', header: 'Status', cell: (r) => <RunState status={r.status} /> },
          { id: 'w', header: 'Started', cell: (r) => when(r.started_at) },
          { id: 't', header: 'Tokens', cell: (r) => tokens(r.weighted_tokens) },
          {
            id: 'e',
            header: 'Error',
            cell: (r) =>
              r.error ? (
                <ExpandableSection variant="footer" headerText={clip(r.error.split('\n')[0] as string, 60)}>
                  <Box fontSize="body-s" variant="code">{r.error}</Box>
                </ExpandableSection>
              ) : (
                '-'
              ),
          },
        ]}
        empty={<Box textAlign="center" padding="m">{runs.error ? <StatusIndicator type="error">{`The runs could not be loaded: ${runs.error.message}`}</StatusIndicator> : 'No runs yet.'}</Box>}
      />
    </SpaceBetween>
  );
}

/** Today in the browser's time zone, as YYYY-MM-DD (toISOString would give the UTC date). */
export function localDate(d = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const ACTION_LABELS: Record<string, string> = {
  'admin.sample_data': 'Loaded the sample data',
  'andon.set': 'Changed the AI kill switch',
  'chat.settings': 'Changed the chat budget',
  'document.delete': 'Deleted a document',
  'document.upload_requested': 'Started a document upload',
  'document.uploaded': 'Uploaded a document',
  'feedback.withdraw': 'Withdrew a vote',
  'graph.delete': 'Deleted a graph record',
  'graph.delete_dequeued': 'Cleared a queued delete of a record already gone',
  'radar.tag': 'Tagged a radar version',
  'radar.untag': 'Removed a radar version tag',
  'graph.merge': 'Merged a duplicate entity',
  'module.run_cancel': 'Cancelled a run',
  'product.sync': 'Re-read product web pages',
  'source.add': 'Added a source',
  'source.update': 'Changed a source',
  'source.delete': 'Deleted a source',
  'suggestion.accept': 'Accepted a suggestion',
  'suggestion.discard': 'Discarded a suggestion',
  'working.set': 'Started working on an idea',
  'working.unset': 'Stopped working on an idea',
  'user.invite': 'Invited a user',
  'user.update': 'Changed a user',
  'user.delete': 'Deleted a user',
  'radar.create': 'Created a radar',
  'radar.update': 'Changed a radar',
  'radar.save': 'Saved radar entries',
  'radar.import': 'Imported radar entries',
  'radar.rollback': 'Restored a radar version',
  'radar.schema': 'Changed a radar schema',
  'radar.access': 'Changed radar access',
  'radar.delete': 'Deleted a radar',
  'module.create': 'Created a module',
  'module.update': 'Changed a module',
  'module.delete': 'Deleted a module',
  'module.run': 'Started a module run',
  'product.create': 'Added a product',
  'product.update': 'Changed a product',
  'product.delete': 'Deleted a product',
  'feedback.give': 'Voted',
  'content.draft': 'Requested a draft',
};

/** A sentence for an audit action code ("radar.create" -> "Created a radar"); unknown codes read as words. */
export const actionLabel = (action: string): string => ACTION_LABELS[action] ?? sentence(action.replace(/\./g, ' '));

const AUDIT_PAGE = 25;

/**
 * The UTC days a local day overlaps: audit rows are kept by UTC day, so a local day east or west of
 * UTC spans two of them (r3 F3-019: before 08:00 in Singapore "today" read "No activity").
 */
export function utcDaysOf(date: string): string[] {
  const [y = 0, m = 1, d = 1] = date.split('-').map(Number);
  const start = new Date(y, m - 1, d);
  const end = new Date(y, m - 1, d + 1, 0, 0, 0, -1);
  return [...new Set([start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)])];
}

/** The day `days` from `date` (YYYY-MM-DD), in the browser's own time zone. */
export function shiftDate(date: string, days: number): string {
  const [y = 0, m = 1, d = 1] = date.split('-').map(Number);
  return localDate(new Date(y, m - 1, d + days));
}

function Audit() {
  const { api } = useSession();
  const [date, setDate] = React.useState(localDate());
  const [filter, setFilter] = React.useState('');
  const [page, setPage] = React.useState(1);
  const log = useAsync(async () => {
    const days = utcDaysOf(date);
    const parts = await Promise.all(days.map((day) => api.audit(day)));
    // In UTC the one partition is the day. Otherwise the two are merged and cut to the local day.
    if (parts.length === 1) return parts[0] as (typeof parts)[number];
    const items = parts
      .flatMap((p) => p.items)
      .filter((a) => localDate(new Date(a.at)) === date)
      .sort((a, b) => b.at.localeCompare(a.at));
    return { items, truncated: parts.some((p) => p.truncated) };
  }, [api, date]);
  // User targets are account ids: show the person's email when it is known.
  const users = useAsync(() => api.users(), [api]);
  const emails = new Map((users.data?.items ?? []).map((u) => [u.username, u.email]));
  const target = (a: AuditItem) => emails.get(a.target) ?? a.target;
  const needle = filter.trim().toLowerCase();
  const all = log.data?.items ?? [];
  const shown = needle ? all.filter((a) => [a.email, a.actor, a.action, actionLabel(a.action), target(a)].some((v) => v.toLowerCase().includes(needle))) : all;
  const pages = Math.max(1, Math.ceil(shown.length / AUDIT_PAGE));
  const current = Math.min(page, pages);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const show = (next: string) => {
    setDate(next);
    setPage(1);
  };
  return (
    <Table wrapLines
      loading={log.loading && !log.data}
      items={shown.slice((current - 1) * AUDIT_PAGE, current * AUDIT_PAGE)}
      // The index is part of the key: a bulk agent write produces identical rows in the same millisecond.
      trackBy={(a) => `${a.at}|${a.actor}|${a.action}|${a.target}|${all.indexOf(a)}`}
      variant="container"
      filter={
        <TextFilter
          filteringText={filter}
          filteringPlaceholder="Find by person, action or target"
          filteringAriaLabel="Find by person, action or target"
          countText={needle ? plural(shown.length, 'match', 'matches') : undefined}
          onChange={({ detail }) => {
            setFilter(detail.filteringText);
            setPage(1);
          }}
        />
      }
      pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
      header={
        <Header
          counter={log.data ? `(${all.length}${log.data.truncated ? '+' : ''})` : undefined}
          description={
            log.data?.truncated
              ? `One day at a time, in your time zone (${zone}). Showing the first ${all.length} rows of that day; the filter searches only these, and older rows are not loaded.`
              : `One day at a time, in your time zone (${zone}).`
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              {/* Going back a week took seven trips through the calendar. */}
              <Button iconName="angle-left" variant="icon" ariaLabel="The day before" onClick={() => show(shiftDate(date, -1))} />
              <DatePicker
                value={date}
                onChange={({ detail }) => show(detail.value)}
                placeholder="YYYY/MM/DD"
                isDateEnabled={(d) => localDate(d) <= localDate()}
                openCalendarAriaLabel={(selected) => ['Choose the day', selected].filter(Boolean).join(', ')}
                i18nStrings={{ previousMonthAriaLabel: 'Previous month', nextMonthAriaLabel: 'Next month', todayAriaLabel: 'Today' }}
              />
              <Button
                iconName="angle-right"
                variant="icon"
                ariaLabel="The day after"
                disabled={date >= localDate()}
                onClick={() => show(shiftDate(date, 1))}
              />
            </SpaceBetween>
          }
        >
          Audit log
        </Header>
      }
      columnDefinitions={[
        { id: 'at', header: 'When', cell: (a) => when(a.at) },
        // A module's rows used to read "module:signal"; actorLabel gives them the name the app uses.
        { id: 'who', header: 'Who', cell: (a) => a.email || actorLabel(a.actor) },
        { id: 'what', header: 'Action', cell: (a) => <span title={a.action}>{actionLabel(a.action)}</span> },
        { id: 'target', header: 'Target', cell: (a) => <Box fontSize="body-s">{target(a)}</Box> },
      ]}
      empty={
        <Box textAlign="center" padding="m">
          {log.error ? <StatusIndicator type="error">{`The audit log could not be loaded: ${log.error.message}`}</StatusIndicator> : needle ? 'No entries match.' : 'No activity that day.'}
        </Box>
      }
    />
  );
}

/** The insight kinds people vote on, as the feedback list offers them. */
const FEEDBACK_TYPES = [
  { value: 'signal', label: 'Signals' },
  { value: 'trend', label: 'Trends' },
  { value: 'gap', label: 'Gaps' },
  { value: 'content_idea', label: 'Content ideas' },
  { value: 'content_draft', label: 'Drafts' },
  // Every custom module writes its findings as `insight` nodes (its list is an insight kind of them).
  { value: 'insight', label: 'Custom module insights' },
];

const FEEDBACK_PAGE = 25;

/** A voted-on entry by its title (the list sends only its id): r3 F3-074. */
function EntryTitle({ id }: { id: string }) {
  const { api } = useSession();
  const node = useAsync(() => api.node(id), [api, id]);
  return <>{node.data?.title ?? id}</>;
}

/**
 * Votes and "Not useful" comments in one place. The help text says the modules learn from feedback,
 * and an administrator had to open entries one by one to find out what people had said.
 */
function Feedback() {
  const { api } = useSession();
  const navigate = useNavigate();
  // The whole option, so the Select always has one to show: it can only ever be from this list.
  const [kind, setKind] = React.useState(FEEDBACK_TYPES[0] as { value: string; label: string });
  const [page, setPage] = React.useState(1);
  const votes = useAsync(() => api.feedbackList(kind.value), [api, kind.value]);
  // Older votes carry an account id: the person's email where it is known, as History and Audit show.
  const users = useAsync(() => api.users(), [api]);
  const emails = new Map((users.data?.items ?? []).map((u) => [u.username, u.email]));
  const all = votes.data?.items ?? [];
  const pages = Math.max(1, Math.ceil(all.length / FEEDBACK_PAGE));
  const current = Math.min(page, pages);
  const down = all.filter((v) => v.vote === 'down').length;
  return (
    <Table wrapLines
      loading={votes.loading && !votes.data}
      items={all.slice((current - 1) * FEEDBACK_PAGE, current * FEEDBACK_PAGE)}
      trackBy={(v) => `${v.node_id}|${v.by}|${v.at}`}
      variant="container"
      pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
      header={
        <Header
          counter={votes.data ? `(${all.length}${votes.data.truncated ? '+' : ''})` : undefined}
          description={`Newest first. ${plural(down, 'vote')} of these ${down === 1 ? 'is' : 'are'} "Not useful"; the modules read the comments to improve.`}
          actions={
            <Select
              expandToViewport
              ariaLabel="Kind of insight"
              selectedOption={kind}
              onChange={({ detail }) => {
                setKind({ value: detail.selectedOption.value as string, label: detail.selectedOption.label as string });
                setPage(1);
              }}
              options={FEEDBACK_TYPES}
            />
          }
        >
          Feedback
        </Header>
      }
      columnDefinitions={[
        { id: 'at', header: 'When', cell: (v) => when(v.at) },
        { id: 'who', header: 'Who', cell: (v) => emails.get(v.by) ?? v.by },
        {
          id: 'vote',
          header: 'Vote',
          cell: (v) => (
            <StatusIndicator type={v.vote === 'up' ? 'success' : 'warning'}>{v.vote === 'up' ? 'Useful' : 'Not useful'}</StatusIndicator>
          ),
        },
        { id: 'comment', header: 'Comment', cell: (v) => v.comment || '-', minWidth: 320 },
        {
          id: 'entry',
          header: 'Entry',
          cell: (v) => (
            <Link href={nodeHref(v.node_id)} onFollow={(e) => { e.preventDefault(); navigate(nodeHref(v.node_id)); }}>
              <EntryTitle id={v.node_id} />
            </Link>
          ),
        },
      ]}
      empty={
        <Box textAlign="center" padding="m">
          {votes.error ? (
            <SpaceBetween size="xs" alignItems="center">
              <StatusIndicator type="error">{`The feedback could not be loaded: ${votes.error.message}`}</StatusIndicator>
              <Button onClick={votes.reload}>Retry</Button>
            </SpaceBetween>
          ) : (
            'No votes on this kind of entry yet.'
          )}
        </Box>
      }
    />
  );
}

const TABS = ['users', 'ai', 'skills', 'digest', 'health', 'entities', 'audit', 'feedback', 'sample'];

export default function AdminPage() {
  usePageTitle('Administration');
  // The tab lives in the address (?tab=health), so other pages can link straight to it.
  const [params, setParams] = useSearchParams();
  // A tab the deployment turned off (such as Sample data) falls back to Users, not to an empty page.
  const shown = TABS.filter((t) => t !== 'sample' || sectionVisible('sample_data'));
  const tab = shown.includes(params.get('tab') ?? '') ? (params.get('tab') as string) : 'users';
  return (
    <ContentLayout header={<Header variant="h1">Administration</Header>}>
      <Tabs
        activeTabId={tab}
        onChange={({ detail }) => setParams(detail.activeTabId === 'users' ? {} : { tab: detail.activeTabId }, { replace: true })}
        tabs={[
          { id: 'users', label: 'Users', content: <Users /> },
          { id: 'ai', label: 'AI controls', content: <SpaceBetween size="l"><KillSwitch /><ChatBudget /></SpaceBetween> },
          { id: 'skills', label: 'Skills', content: <SkillLibrary /> },
          { id: 'digest', label: 'Digest delivery', content: <DigestChannelsAdmin /> },
          { id: 'health', label: 'Health', content: <Health /> },
          { id: 'entities', label: 'Entities', content: <EntityHygienePanel /> },
          { id: 'audit', label: 'Audit log', content: <Audit /> },
          { id: 'feedback', label: 'Feedback', content: <Feedback /> },
          ...(sectionVisible('sample_data') ? [{ id: 'sample', label: 'Sample data', content: <SampleData /> }] : []),
        ]}
      />
    </ContentLayout>
  );
}
