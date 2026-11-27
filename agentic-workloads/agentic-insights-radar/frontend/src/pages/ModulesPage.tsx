// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
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
import Multiselect from '@cloudscape-design/components/multiselect';
import Popover from '@cloudscape-design/components/popover';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator, { type StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import TextFilter from '@cloudscape-design/components/text-filter';
import Toggle from '@cloudscape-design/components/toggle';
import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import type { Module, ModuleRun, RunStatus, Source, SourceWatch } from '@/api/types';
import { can, useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { Crumbs, LoadError, PageLoading, ReloadFailed, TableLoadError } from '@/components/ErrorState';
import Markdown from '@/components/Markdown';
import { useNav } from '@/components/NavContext';
import { useNotify } from '@/components/Notifications';
import McpServersEditor, { mcpProblem, withMcpGrant } from '@/components/McpServersEditor';
import PromptPanel from '@/components/PromptPanel';
import SlugField from '@/components/SlugField';
import UnsavedGuard, { DiscardWarning } from '@/components/UnsavedGuard';
import { WholeNumber } from '@/components/wholeNumber';
import { useAsync } from '@/hooks/useAsync';
import { useNarrow } from '@/hooks/useNarrow';
import { ago, clip, KIND_LABELS, nodeList, runStatusLabel, slugify, tokens, triggerLabel, when } from '@/lib/format';
import { sectionVisible } from '@/lib/profile';
import { useLoadingPageTitle, usePageTitle } from '@/lib/pageTitle';

export const GRANT_LABELS: Record<string, string> = {
  kg_read: 'Read the knowledge graph',
  kg_write_evidence: 'Record evidence and entities',
  kg_write_output: 'Publish its insights',
  product_knowledge: 'Search product knowledge',
  sources: 'Crawl configured sources',
  radar_suggest: 'Suggest radar changes',
  content_draft: 'Write content drafts',
  web_fetch: 'Fetch web pages',
  web_search: 'Web search',
  browser: 'Browser (JavaScript pages)',
  code_interpreter: 'Code interpreter',
  mcp: 'Attached MCP servers',
};
// "Attached MCP servers" is not offered: the app has no way to add a server, so the grant could be
// given and never used (r3 F3-058). A module that has it still shows it.
const CUSTOM_GRANTS = ['kg_read', 'kg_write_evidence', 'kg_write_output', 'product_knowledge', 'sources', 'web_fetch', 'web_search', 'browser', 'code_interpreter'];
/** Tools that reach outside; the API refuses them together with product knowledge (OUTBOUND_GRANTS in modules/model.py). */
const OUTBOUND = ['web_fetch', 'web_search', 'browser', 'code_interpreter', 'mcp'];

/** Why a set of tools cannot be saved, or '' when it can. */
export function grantConflict(grants: string[]): string {
  if (!grants.includes('product_knowledge')) return '';
  const outbound = grants.filter((g) => OUTBOUND.includes(g));
  if (!outbound.length) return '';
  // Every OUTBOUND code has a label of its own, so there is nothing to fall back to.
  return `"Search product knowledge" cannot be combined with a tool that reaches outside (${outbound
    .map((g) => GRANT_LABELS[g] as string)
    .join(', ')}): your own documents must not leave with an outbound request.`;
}

/** The options, with the ones that conflict with what is already chosen switched off. */
export function grantOptions(grants: string[]): { value: string; label: string; disabled?: boolean; disabledReason?: string }[] {
  const reason = 'Cannot be combined with "Search product knowledge": your own documents must not leave with an outbound request.';
  const privateKnowledge = grants.includes('product_knowledge');
  const outbound = grants.some((g) => OUTBOUND.includes(g));
  return CUSTOM_GRANTS.map((g) => {
    const label = GRANT_LABELS[g] as string;
    if (g === 'product_knowledge' && outbound && !privateKnowledge) return { value: g, label, disabled: true, disabledReason: reason };
    if (OUTBOUND.includes(g) && privateKnowledge && !grants.includes(g)) return { value: g, label, disabled: true, disabledReason: reason };
    return { value: g, label };
  });
}
const RUN_STATUS: Record<RunStatus, StatusIndicatorProps.Type> = {
  queued: 'pending',
  running: 'in-progress',
  succeeded: 'success',
  failed: 'error',
  budget_exhausted: 'warning',
  cancelled: 'stopped',
};
const INTERVALS = [
  { value: '0', label: 'On demand only' },
  { value: '60', label: 'Every hour' },
  { value: '360', label: 'Every 6 hours' },
  { value: '720', label: 'Every 12 hours' },
  { value: '1440', label: 'Daily' },
  { value: '10080', label: 'Weekly' },
];

/** Modules whose findings only make sense against a product profile. */
const NEEDS_PRODUCT = new Set(['gap', 'content_idea', 'content_draft']);

/** The order the built-in modules feed each other in (the setup alert lists them the same way). */
const PIPELINE = ['scout', 'signal', 'trend', 'gap', 'content', 'draft', 'curator'];

export function pipelineOrder(a: Module, b: Module): number {
  const rank = (m: Module) => (PIPELINE.includes(m.slug) ? PIPELINE.indexOf(m.slug) : PIPELINE.length);
  return rank(a) - rank(b) || a.name.localeCompare(b.name);
}

/** Backend limit (modules/model.py MAX_MODULE_SKILLS). */
export const MAX_MODULE_SKILLS = 10;

/** Backend limits on a schedule (modules/model.py): 0 = on demand, else 15 minutes to a week. */
export const INTERVAL_MIN = 15;
export const INTERVAL_MAX = 10080;
/** Backend limit (modules/model.py REPLACE_MIN_INSTRUCTIONS). */
export const REPLACE_MIN_INSTRUCTIONS = 200;

export const isListedInterval = (minutes: number): boolean => INTERVALS.some((i) => Number(i.value) === minutes);

/** What is wrong with a custom schedule, or ''. */
export function intervalProblem(minutes: number | null): string {
  if (minutes === null) return 'Enter a whole number of minutes.';
  return minutes < INTERVAL_MIN || minutes > INTERVAL_MAX ? `Between ${INTERVAL_MIN} and ${INTERVAL_MAX} minutes (a week).` : '';
}

export function intervalLabel(minutes: number): string {
  return INTERVALS.find((i) => Number(i.value) === minutes)?.label ?? `Every ${minutes} minutes`;
}

/** One answer to "when does it run": the schedule when it is on, else Paused, or On demand. */
/** Backend limit (modules/model.py INSTRUCTIONS_MAX). */
export const INSTRUCTIONS_MAX = 20_000;

export function scheduleState(m: Pick<Module, 'enabled' | 'interval_minutes' | 'template'>): { type: StatusIndicatorProps.Type; text: string } {
  // Content drafts never run on a schedule: `enabled` is what lets editors request drafts (r3 F3-028).
  if (m.template === 'draft') return m.enabled ? { type: 'info', text: 'On request (editors ask for drafts)' } : { type: 'stopped', text: 'Off: editors cannot request drafts' };
  if (m.interval_minutes === 0) return { type: 'info', text: 'On demand (Run now)' };
  if (!m.enabled) return { type: 'stopped', text: `Paused (${intervalLabel(m.interval_minutes).toLowerCase()} when on)` };
  return { type: 'success', text: intervalLabel(m.interval_minutes) };
}

export function RunState({ status }: { status: RunStatus }) {
  return <StatusIndicator type={RUN_STATUS[status]}>{runStatusLabel(status)}</StatusIndicator>;
}

/** Built-in list names and the names other modules already publish under: a new module may not reuse them. */
const BUILTIN_KINDS = new Set([...Object.keys(KIND_LABELS), 'signal', 'trend', 'gap', 'content_idea', 'content_draft', 'insight']);

export function ModulesPage() {
  usePageTitle('Modules');
  const { api, me } = useSession();
  const navigate = useNavigate();
  const modules = useAsync(() => api.modules(), [api]);
  const [creating, setCreating] = React.useState(false);
  const manage = can(me, 'module.manage');
  // With Radars off the Radar curator has nothing to curate; an administrator who turned it on still sees it.
  const items = [...(modules.data?.items ?? [])]
    .filter((m) => sectionVisible('radars') || m.template !== 'curator' || m.enabled)
    .sort(pipelineOrder);
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Agents that read your sources and write to the knowledge graph. Built-in modules ship turned off."
          actions={manage && <Button variant="primary" onClick={() => setCreating(true)}>Create module</Button>}
        >
          Modules
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Setup advice is for the people who can follow it. */}
        {manage && modules.data && !modules.data.items.some((m) => m.enabled) && (
          <Alert type="info" header="Every module is off. Turn them on in this order">
            1. <b>Scout</b>: add sources (feeds, sites), then Run now. It records facts and opinions in the knowledge graph.{' '}
            2. <b>Signals</b> and <b>Trends</b> turn that evidence into insights.{' '}
            3. <b>Gaps</b> and <b>Content ideas</b> compare them with your product (pick one under Products first).{' '}
            {sectionVisible('radars') && <>4. <b>Radar curator</b> proposes radar changes. </>}Open a module, switch on Run on schedule, and Save.
          </Alert>
        )}
        <Table wrapLines
          loading={modules.loading && !modules.data}
          items={items}
          trackBy="slug"
          variant="container"
          columnDefinitions={[
            {
              id: 'name',
              header: 'Module',
              cell: (m) => (
                <Link href={`/modules/${m.slug}`} onFollow={(e) => { e.preventDefault(); navigate(`/modules/${m.slug}`); }}>
                  {m.name}
                </Link>
              ),
            },
            { id: 'desc', header: 'What it does', cell: (m) => <Box fontSize="body-s">{m.description}</Box>, minWidth: 300 },
            {
              id: 'when',
              header: 'Runs',
              cell: (m) => {
                const s = scheduleState(m);
                return <StatusIndicator type={s.type}>{s.text}</StatusIndicator>;
              },
            },
            {
              id: 'budget',
              header: (
                <Popover
                  header="Today"
                  triggerType="text"
                  content="Tokens used today out of the module's daily budget. A module stops for the day when it reaches its budget, and starts again after midnight UTC."
                >
                  Today
                </Popover>
              ),
              cell: (m) => m.budget && <ProgressBar value={Math.min(100, (100 * m.budget.consumed) / Math.max(1, m.budget.limit))} additionalInfo={`${tokens(m.budget.consumed)} of ${tokens(m.budget.limit)}`} />,
              minWidth: 180,
            },
          ]}
          // A failed load must not read as "no modules": the setup alert is hidden then too.
          empty={
            <Box textAlign="center" padding="m">
              {modules.error ? (
                <SpaceBetween size="xs" alignItems="center">
                  <StatusIndicator type="error">{`The modules could not be loaded: ${modules.error.message}`}</StatusIndicator>
                  <Button onClick={modules.reload}>Retry</Button>
                </SpaceBetween>
              ) : (
                'No modules.'
              )}
            </Box>
          }
        />
      </SpaceBetween>
      {creating && <CreateModule existing={items} onClose={() => setCreating(false)} />}
    </ContentLayout>
  );
}

/** The insight kind a module address publishes under (the API's snake_case form). */
export function kindOf(slug: string): string {
  return slug.replace(/-/g, '_').replace(/^[^a-z]+/, '').slice(0, 40) || 'insight';
}

function CreateModule({ existing, onClose }: { existing: Module[]; onClose: () => void }) {
  const { api } = useSession();
  const { fail } = useNotify();
  const { refresh } = useNav();
  const navigate = useNavigate();
  const [name, setName] = React.useState('');
  // null until the field is edited, so deleting the last character does not snap the name back.
  const [address, setAddress] = React.useState<string | null>(null);
  const [label, setLabel] = React.useState('');
  const [instructions, setInstructions] = React.useState('');
  const [grants, setGrants] = React.useState<string[]>(['kg_read', 'kg_write_output', 'web_search', 'web_fetch']);
  const [busy, setBusy] = React.useState(false);
  const [leaving, setLeaving] = React.useState(false);
  // A name with no Latin letters or digits slugifies to '', so the address is editable, as for radars.
  const slug = address ?? slugify(name);
  const dirty = Boolean(name.trim() || address !== null || label.trim() || instructions.trim());
  // A filled-in form is never thrown away by Esc or a stray click on the backdrop.
  const close = () => (dirty ? setLeaving(true) : onClose());
  const kind = kindOf(slugify(slug));
  const taken = BUILTIN_KINDS.has(kind) || existing.some((m) => m.insight_kind === kind);
  const short = instructions.trim().length < 20;
  const long = instructions.trim().length > INSTRUCTIONS_MAX;
  const publishes = grants.includes('kg_write_output');
  const blocked = !name.trim()
    ? 'Enter a name first.'
    : !slugify(slug)
      ? 'Enter an address first.'
      : taken
        ? 'Choose another address: its list name is taken.'
        : short
          ? 'Write at least 20 characters of instructions.'
          : long
            ? `Shorten the instructions to ${INSTRUCTIONS_MAX} characters.`
            : !publishes
            ? 'Keep "Publish its insights": without it the module can never publish.'
            : grantConflict(grants);
  const create = () => {
    setBusy(true);
    void api
      .createModule({
        slug: slugify(slug),
        name: name.trim(),
        description: instructions.trim().replace(/\n[\s\S]*$/, '').slice(0, 200), // the first line
        insight_kind: kind,
        insight_label: label.trim() || name.trim(),
        instructions: instructions.trim(),
        grants,
        interval_minutes: 0,
      })
      .then((m) => {
        refresh(); // its list appears in the side navigation at once
        navigate(`/modules/${m.slug}`);
      }, fail)
      .finally(() => setBusy(false));
  };
  return (
    <Modal
      visible
      size="large"
      onDismiss={close}
      header="Create a module"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={close}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={Boolean(blocked)} disabledReason={blocked} onClick={create}>Create</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {leaving && <DiscardWarning what="this module" onKeep={() => setLeaving(false)} onDiscard={onClose} />}
        <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="Pricing watch" /></FormField>
        <SlugField
          value={slug}
          onChange={setAddress}
          usedIn="the module's link"
          errorText={
            name.trim() && !slugify(slug)
              ? 'Enter an address: the name has no letters or digits to make one from.'
              : taken && slugify(slug)
                ? `Its insights would be listed as "${kind}", which is already a list name. Choose another address.`
                : undefined
          }
        />
        <FormField label="What it publishes" description={`Shown in the navigation, e.g. Pricing moves. Its insights are kept as "${kind}".`}>
          <Input value={label} onChange={({ detail }) => setLabel(detail.value)} />
        </FormField>
        <FormField
          label="Instructions"
          description="What to look for, what counts, what to ignore. The module reads the evidence in the graph and publishes one insight per finding."
          constraintText={`At least 20 and at most ${INSTRUCTIONS_MAX} characters (${instructions.trim().length} so far).`}
          errorText={long ? `At most ${INSTRUCTIONS_MAX} characters.` : undefined}
        >
          <Textarea value={instructions} onChange={({ detail }) => setInstructions(detail.value)} rows={8} />
        </FormField>
        <FormField
          label="Tools"
          errorText={grantConflict(grants)}
          warningText={publishes ? undefined : 'Without "Publish its insights" this module runs but can never publish anything.'}
        >
          <Multiselect
            expandToViewport
            selectedOptions={grants.map((g) => ({ value: g, label: GRANT_LABELS[g] }))}
            onChange={({ detail }) => setGrants(detail.selectedOptions.map((o) => o.value as string))}
            options={grantOptions(grants)}
          />
        </FormField>
        <Box fontSize="body-s" color="text-body-secondary">It starts on demand only and turned off; set a schedule and turn it on after a test run.</Box>
      </SpaceBetween>
    </Modal>
  );
}

/** The fields Configuration edits, carried onto a version read again after a conflict. */
const EDITABLE = ['enabled', 'instructions', 'prompt_mode', 'skills', 'model_key', 'interval_minutes', 'daily_token_budget', 'max_concurrency', 'product_id', 'grants', 'name', 'insight_label', 'mcp_servers'] as const;

function Configure({ module, onSaved, gone, carry, onConflict }: { module: Module; onSaved: () => void; gone: boolean; carry: Module | null; onConflict: (edits: Module) => void }) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const navigate = useNavigate();
  const models = useAsync(() => api.models(), [api]);
  const library = useAsync(() => api.skills(), [api]);
  // With Products turned off there is no picker, so there is nothing to list.
  const products = useAsync(() => (sectionVisible('products') ? api.products() : Promise.resolve({ items: [] })), [api]);
  const [m, setM] = React.useState(() => (carry ? { ...module, ...Object.fromEntries(EDITABLE.map((k) => [k, carry[k]])) } : module));
  const [saving, setSaving] = React.useState(false);
  // Weighted tokens price like input tokens (output is weighted 5x, close to its 5x price), so the input price bounds the day.
  const price = models.data?.items.find((x) => x.key === m.model_key)?.price_in_out[0];
  const maxCost = price === undefined ? null : (m.daily_token_budget / 1_000_000) * price;
  const [budget, setBudget] = React.useState(() => WholeNumber.of(m.daily_token_budget));
  const [concurrency, setConcurrency] = React.useState(() => WholeNumber.of(m.max_concurrency, 1));
  // A schedule not in the list (set through the API, or typed here) is edited as minutes.
  const [customEvery, setCustomEvery] = React.useState(() => (isListedInterval(m.interval_minutes) ? null : WholeNumber.of(m.interval_minutes)));
  const everyProblem = customEvery === null ? '' : intervalProblem(customEvery.value);
  const replaceProblem = m.prompt_mode === 'replace' && m.instructions.trim().length < REPLACE_MIN_INSTRUCTIONS
    ? `Replace mode needs instructions of at least ${REPLACE_MIN_INSTRUCTIONS} characters: they are the whole job description.`
    : '';
  const valid =
    (m.skills?.length ?? 0) <= MAX_MODULE_SKILLS &&
    budget.value !== null && concurrency.value !== null && m.instructions.length <= INSTRUCTIONS_MAX && !everyProblem && !replaceProblem && !mcpProblem(m.mcp_servers);
  const mcpBlocked = m.grants.includes('product_knowledge')
    ? 'This module searches product knowledge, so it cannot call MCP servers: your own documents must not leave with an outbound request.'
    : '';
  // Compared with what was last stored, not with the prop: a save whose response does not change the
  // module (the same version, the same fields) must still leave the form clean.
  const [stored, setStored] = React.useState(() => JSON.stringify(module));
  const dirty = JSON.stringify(m) !== stored;
  // The product matters only to modules that compare against one (and stays visible while one is set, so it can be cleared).
  const usesProduct =
    sectionVisible('products') && (NEEDS_PRODUCT.has(module.output_kind ?? '') || m.grants.includes('product_knowledge') || Boolean(module.product_id));
  const save = () => {
    const changes: Partial<Module> = {
      enabled: m.enabled,
      instructions: m.instructions,
      prompt_mode: m.prompt_mode ?? 'extend',
      skills: m.skills ?? [],
      model_key: m.model_key,
      interval_minutes: m.interval_minutes,
      // m keeps the last valid entry of each number field, and Save is disabled while one is invalid.
      daily_token_budget: m.daily_token_budget,
      max_concurrency: m.max_concurrency,
      product_id: m.product_id,
    };
    if (!module.builtin) {
      Object.assign(changes, { grants: withMcpGrant(m.grants, m.mcp_servers), name: m.name, insight_label: m.insight_label, mcp_servers: m.mcp_servers });
    } else if (JSON.stringify(m.mcp_servers) !== JSON.stringify(module.mcp_servers)) {
      // A built-in's tools are its template's, but MCP servers can be attached to any module: the grant follows the list.
      Object.assign(changes, { grants: withMcpGrant(module.grants, m.mcp_servers), mcp_servers: m.mcp_servers });
    }
    setSaving(true);
    const sending = JSON.stringify(m);
    void api
      .updateModule(module.slug, changes, module.version)
      .then(
        () => {
          setStored(sending);
          notify('success', 'Saved.');
          onSaved();
        },
        (err: unknown) => {
          // Another administrator saved first: every retry sent the same old version (r3 F3-013). The
          // module is read again, and these edits are carried onto it.
          if (err instanceof ApiError && err.status === 409) {
            notify('warning', 'Someone else changed this module while you were editing. It was read again and your changes are kept on top of it: check them, then Save again.');
            onConflict(m);
          } else fail(err);
        },
      )
      .finally(() => setSaving(false));
  };
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={dirty ? 'You have unsaved changes. Save to apply them.' : undefined}
          actions={
            <Button
              variant="primary"
              loading={saving}
              disabled={!dirty || !valid || Boolean(grantConflict(m.grants))}
              disabledReason={valid && !grantConflict(m.grants) ? 'Nothing changed yet.' : 'Fix the highlighted field first.'}
              onClick={save}
            >
              Save
            </Button>
          }
        >
          Configuration
        </Header>
      }
    >
      <SpaceBetween size="m">
        <UnsavedGuard dirty={dirty && !gone}>
          This module has unsaved configuration changes (instructions, schedule, model, budget or tools). They are lost if you leave the
          page now.
        </UnsavedGuard>
        {module.template === 'draft' ? (
          // The drafts module never runs on a schedule: its switch is whether editors may ask for drafts (r3 F3-028).
          <Toggle
            checked={m.enabled}
            onChange={({ detail }) => setM({ ...m, enabled: detail.checked })}
            description="Switch on and Save: editors can then press Write a draft on a content idea. It never runs on a schedule."
          >
            Let editors request drafts
          </Toggle>
        ) : (
        <Toggle
          checked={m.enabled}
          // On with "On demand only" the module never runs: picking a daily schedule is what the
          // toggle promises, so turning it on from no schedule chooses one.
          onChange={({ detail }) =>
            setM({ ...m, enabled: detail.checked, interval_minutes: detail.checked && m.interval_minutes === 0 ? 1440 : m.interval_minutes })
          }
          description="Switch on and Save: the module then runs on the schedule below. Run now works whether this is on or off."
        >
          Run on schedule
        </Toggle>
        )}
        {m.enabled && m.interval_minutes === 0 && module.template !== 'draft' && (
          <Alert type="warning">
            This module is on but its schedule is &quot;On demand only&quot;, so it never runs by itself. Pick a schedule below, or use Run now.
          </Alert>
        )}
        <ColumnLayout columns={3}>
          {module.template !== 'draft' && <FormField label="Schedule" errorText={everyProblem || undefined}>
            <SpaceBetween size="xs">
              <Select
                expandToViewport
                selectedOption={customEvery !== null ? { value: 'custom', label: 'Custom' } : { value: String(m.interval_minutes), label: intervalLabel(m.interval_minutes) }}
                onChange={({ detail }) => {
                  if (detail.selectedOption.value === 'custom') {
                    setCustomEvery(WholeNumber.of(m.interval_minutes || 240));
                    setM({ ...m, interval_minutes: m.interval_minutes || 240 });
                  } else {
                    setCustomEvery(null);
                    setM({ ...m, interval_minutes: Number(detail.selectedOption.value) });
                  }
                }}
                options={[...INTERVALS, { value: 'custom', label: 'Custom', description: `Every N minutes, ${INTERVAL_MIN} to ${INTERVAL_MAX}` }]}
              />
              {customEvery !== null && (
                <Input
                  type="number"
                  ariaLabel="Run every this many minutes"
                  value={customEvery.text}
                  onChange={({ detail }) => {
                    const next = customEvery.with(detail.value);
                    setCustomEvery(next);
                    if (next.value !== null && !intervalProblem(next.value)) setM({ ...m, interval_minutes: next.value });
                  }}
                />
              )}
            </SpaceBetween>
          </FormField>}
          <FormField label="Model">
            <Select
              expandToViewport
              selectedOption={{ value: m.model_key, label: models.data?.items.find((x) => x.key === m.model_key)?.label ?? m.model_key }}
              onChange={({ detail }) => setM({ ...m, model_key: detail.selectedOption.value as string })}
              options={(models.data?.items ?? []).map((x) => ({ value: x.key, label: x.label, description: `${x.tier} \u00b7 $${x.price_in_out[0]}/$${x.price_in_out[1]} per 1M tokens` }))}
            />
          </FormField>
          {usesProduct && (
            <FormField
              label="Product"
              description="The product this module compares the market against."
              warningText={NEEDS_PRODUCT.has(module.output_kind ?? '') && !m.product_id ? 'Pick your product: without one this module has nothing to compare the market against.' : undefined}
            >
              <Select
                expandToViewport
                selectedOption={{ value: m.product_id ?? '', label: products.data?.items.find((p) => p.id === m.product_id)?.name ?? 'None' }}
                onChange={({ detail }) => setM({ ...m, product_id: detail.selectedOption.value || null })}
                options={[{ value: '', label: 'None' }, ...(products.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
            </FormField>
          )}
          <FormField
            label="Daily token budget"
            description={`Weighted tokens (output counts 5x, cache reads 0.1x). 0 stops the module.${maxCost === null ? '' : ` With this model that is at most about $${maxCost.toFixed(2)} a day.`}`}
            errorText={budget.error}
          >
            <Input
              type="number"
              value={budget.text}
              onChange={({ detail }) => {
                const next = budget.with(detail.value);
                setBudget(next);
                setM({ ...m, daily_token_budget: next.value ?? m.daily_token_budget });
              }}
            />
          </FormField>
          <FormField label="Runs at the same time" errorText={concurrency.error}>
            <Input
              type="number"
              value={concurrency.text}
              onChange={({ detail }) => {
                const next = concurrency.with(detail.value);
                setConcurrency(next);
                setM({ ...m, max_concurrency: next.value ?? m.max_concurrency });
              }}
            />
          </FormField>
        </ColumnLayout>
        <FormField
          label="Instructions"
          description={module.builtin ? 'Added to the built-in prompt: your focus, your market, what matters to you.' : 'The whole task for this module.'}
          constraintText={`${m.instructions.length} of ${INSTRUCTIONS_MAX} characters.`}
          errorText={m.instructions.length > INSTRUCTIONS_MAX ? `At most ${INSTRUCTIONS_MAX} characters: shorten it before saving.` : undefined}
        >
          <Textarea value={m.instructions} rows={8} onChange={({ detail }) => setM({ ...m, instructions: detail.value })} />
        </FormField>
        {module.template !== 'draft' && (
          <FormField
            label="How the instructions are used"
            errorText={replaceProblem || undefined}
          >
            <RadioGroup
              value={m.prompt_mode ?? 'extend'}
              onChange={({ detail }) => setM({ ...m, prompt_mode: detail.value as 'extend' | 'replace' })}
              items={[
                { value: 'extend', label: 'Extend the template', description: "The instructions fill the template's focus: what to look for, what matters to you." },
                {
                  value: 'replace',
                  label: 'Replace the template',
                  description: `The instructions are the whole job description (at least ${REPLACE_MIN_INSTRUCTIONS} characters). The shared rules for quality, writing and web content are still added.`,
                },
              ]}
            />
          </FormField>
        )}
        {!module.builtin && (
          <FormField label="Tools" errorText={grantConflict(m.grants)}>
            <Multiselect
              expandToViewport
              selectedOptions={m.grants.map((g) => ({ value: g, label: GRANT_LABELS[g] ?? g }))}
              onChange={({ detail }) => setM({ ...m, grants: detail.selectedOptions.map((o) => o.value as string) })}
              options={grantOptions(m.grants)}
            />
          </FormField>
        )}
        {module.builtin && <Box fontSize="body-s" color="text-body-secondary">Tools: {m.grants.map((g) => GRANT_LABELS[g] ?? g).join(', ')}</Box>}
        <FormField
          label="Skills"
          description="Instructions this module's runs load when a task needs them (only each skill's description is in the prompt until then)."
          secondaryControl={<Link href="/admin?tab=skills" onFollow={(e) => { e.preventDefault(); navigate('/admin?tab=skills'); }}>Skill library</Link>}
          errorText={(m.skills?.length ?? 0) > MAX_MODULE_SKILLS ? `At most ${MAX_MODULE_SKILLS} skills on a module.` : undefined}
        >
          <Multiselect
            expandToViewport
            statusType={library.loading && !library.data ? 'loading' : library.error ? 'error' : 'finished'}
            errorText={library.error ? `The skill library could not be read: ${library.error.message}` : undefined}
            placeholder="Choose skills"
            selectedOptions={(m.skills ?? []).map((s) => ({ value: s, label: s }))}
            onChange={({ detail }) => setM({ ...m, skills: detail.selectedOptions.map((o) => o.value as string) })}
            options={(library.data?.items ?? []).map((s) => ({ value: s.name, label: s.name, description: s.description, tags: s.default ? ['Default'] : undefined }))}
          />
        </FormField>
        {module.template !== 'draft' && (
          <McpServersEditor servers={m.mcp_servers} onChange={(next) => setM({ ...m, mcp_servers: next })} blocked={mcpBlocked} />
        )}
      </SpaceBetween>
    </Container>
  );
}

const SOURCE_TYPES: Record<Source['type'], string> = {
  rss: 'RSS or Atom feed',
  web_page: 'Web page',
  dynamic_web_page: 'Web page that needs JavaScript',
  public_api: 'Public API (JSON)',
};
const SOURCE_TYPE_OPTIONS = Object.entries(SOURCE_TYPES).map(([value, label]) => ({ value, label }));

type SourceDraft = Pick<Source, 'type' | 'name'> & { address: string; instruction: string; id?: string };

/** Backend limits (modules/model.py), checked here so Add or Save does not fail with a server message. */
export const SOURCE_NAME_MAX = 120;
export const SOURCE_INSTRUCTION_MAX = 4000;

/** What is wrong with a source draft, or ''. */
export function sourceProblem(d: SourceDraft): string {
  if (!d.name.trim()) return 'Enter a name.';
  if (d.name.trim().length > SOURCE_NAME_MAX) return `Shorten the name to ${SOURCE_NAME_MAX} characters (it is ${d.name.trim().length}).`;
  if (!/^https:\/\/[^\s/]+\.[^\s]+$/.test(d.address.trim())) return 'Enter a public https:// address.';
  if (d.instruction.trim().length > SOURCE_INSTRUCTION_MAX) {
    return `Shorten the instruction to ${SOURCE_INSTRUCTION_MAX} characters (it is ${d.instruction.trim().length}).`;
  }
  return '';
}

/** The read position as a person reads it: a time when it is one, otherwise just that the source has one. */
export function cursorLabel(cursor: string | undefined): string {
  if (!cursor) return '-';
  return /^\d{4}-\d{2}-\d{2}/.test(cursor) && !Number.isNaN(Date.parse(cursor)) ? when(cursor) : 'Position saved';
}

const SOURCES_PAGE = 25;

/** A source has a problem when the watcher could not read it or found no items on it. */
const sourceHasProblem = (s: Source): boolean => s.watch?.status === 'error' || s.watch?.status === 'empty';

export function sourcesShown(items: Source[], filter: string, problemsOnly: boolean): Source[] {
  const q = filter.trim().toLowerCase();
  return items.filter(
    (s) => (!problemsOnly || sourceHasProblem(s)) && (!q || s.name.toLowerCase().includes(q) || (s.address ?? '').toLowerCase().includes(q)),
  );
}

/** What the source watcher last saw on a source: when, the outcome, and how many new items wait for a run. */
export function WatchCell({ watch }: { watch: SourceWatch | null }) {
  if (!watch?.checked_at) return <Box fontSize="body-s" color="text-body-secondary">Not checked yet</Box>;
  const status: [StatusIndicatorProps.Type, string] =
    watch.status === 'error'
      ? ['error', clip(watch.error || 'Could not read it', 160)]
      : watch.status === 'empty'
        ? ['warning', 'Lists no items']
        : ['success', watch.pending ? `${watch.pending} new waiting` : 'Up to date'];
  return (
    <SpaceBetween size="xxxs">
      <StatusIndicator type={status[0]}>{status[1]}</StatusIndicator>
      <Box fontSize="body-s" color="text-body-secondary">{when(watch.checked_at)}</Box>
    </SpaceBetween>
  );
}

function Sources({ module }: { module: Module }) {
  const { api, me } = useSession();
  const { fail, notify } = useNotify();
  const list = useAsync(() => api.sources(module.slug), [api, module.slug]);
  const [draft, setDraft] = React.useState<SourceDraft | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [deleting, setDeleting] = React.useState<Source | null>(null);
  const [restarting, setRestarting] = React.useState<Source | null>(null);
  /** Toggles shown at once, before the request answers (and put back if it fails). */
  const [toggled, setToggled] = React.useState<Record<string, boolean>>({});
  const manage = can(me, 'module.manage');
  // Narrow when the fixed-width columns would leave Name and Address under 360px between them: 582px of fixed
  // columns for managers, 352px for viewers (code review r21), plus 40px for the container's padding and borders
  // around the table (code review r22).
  const [wrapRef, narrow] = useNarrow<HTMLDivElement>((manage ? 582 : 352) + 360 + 40);
  const problem = draft ? sourceProblem(draft) : '';
  const [filter, setFilter] = React.useState('');
  const [problemsOnly, setProblemsOnly] = React.useState(false);
  const [page, setPage] = React.useState(1);
  const [checking, setChecking] = React.useState(false);
  const all = list.data?.items ?? [];
  const shown = sourcesShown(all, filter, problemsOnly);
  const pages = Math.max(1, Math.ceil(shown.length / SOURCES_PAGE));
  const at = Math.min(page, pages);
  const checkNow = () => {
    setChecking(true);
    void api
      .checkSources(module.slug)
      .then(() => notify('success', 'Checking every source for new items. Reload in a minute or two to see the results.'), fail)
      .finally(() => setChecking(false));
  };
  /** What the dialog opened with, so Esc on a changed draft asks first (r3 F3-056). */
  const [opened, setOpened] = React.useState<string>('');
  const [leaving, setLeaving] = React.useState(false);
  const open = (d: SourceDraft) => {
    setOpened(JSON.stringify(d));
    setLeaving(false);
    setDraft(d);
  };
  const close = () => (draft && JSON.stringify(draft) !== opened ? setLeaving(true) : setDraft(null));
  const toggle = (s: Source, on: boolean) => {
    setToggled((t) => ({ ...t, [s.id]: on }));
    void api
      .updateSource(module.slug, s.id, { enabled: on })
      .then(list.reload, fail)
      .finally(() =>
        setToggled((t) => Object.fromEntries(Object.entries(t).filter(([id]) => id !== s.id))),
      );
  };
  const save = (d: SourceDraft) => {
    setBusy(true);
    const body = { type: d.type, name: d.name.trim(), address: d.address.trim(), instruction: d.instruction.trim() };
    void (d.id ? api.updateSource(module.slug, d.id, body) : api.addSource(module.slug, body))
      .then(() => {
        setDraft(null);
        list.reload();
      }, fail)
      .finally(() => setBusy(false));
  };
  return (
    <div ref={wrapRef}>
      <Table wrapLines
        loading={list.loading && !list.data}
        items={shown.slice((at - 1) * SOURCES_PAGE, at * SOURCES_PAGE)}
        trackBy="id"
        variant="container"
        header={
          <Header
            variant="h2"
            counter={list.data ? `(${all.length})` : undefined}
            description="Where this module reads. The source watcher checks every source for new items every two hours, and each run reads only what is new."
            actions={
              manage && (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button loading={checking} onClick={checkNow}>Check now</Button>
                  <Button onClick={() => open({ type: 'rss', name: '', address: '', instruction: '' })}>Add source</Button>
                </SpaceBetween>
              )
            }
          >
            Sources
          </Header>
        }
        filter={
          <SpaceBetween direction="horizontal" size="m" alignItems="center">
            <TextFilter
              filteringText={filter}
              filteringPlaceholder="Find a source by name or address"
              filteringAriaLabel="Find a source"
              countText={filter || problemsOnly ? `${shown.length} match${shown.length === 1 ? '' : 'es'}` : undefined}
              onChange={({ detail }) => {
                setFilter(detail.filteringText);
                setPage(1);
              }}
            />
            {manage && (
              <Toggle checked={problemsOnly} onChange={({ detail }) => { setProblemsOnly(detail.checked); setPage(1); }}>
                Only sources with problems
              </Toggle>
            )}
          </SpaceBetween>
        }
        pagination={<Pagination currentPageIndex={at} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: (s) => s.name },
          {
            id: 'address',
            header: 'Address',
            cell: (s) => (
              <SpaceBetween size="xxxs">
                <Box fontSize="body-s">{s.address ?? '-'}</Box>
                {s.instruction && <Box fontSize="body-s" color="text-body-secondary">{s.instruction}</Box>}
                {narrow && (
                  // What the hidden columns said, so a source's problem stays visible (code review r20).
                  <>
                    <Box fontSize="body-s" color="text-body-secondary">
                      {s.cursor ? `Read up to ${cursorLabel(s.cursor)}, last read` : 'Last read'} {s.read_at ? when(s.read_at) : 'never'}
                    </Box>
                    {manage && <WatchCell watch={s.watch ?? null} />}
                  </>
                )}
              </SpaceBetween>
            ),
          },
          // In a narrow container the fixed-width columns would leave Name and Address no room (code review r19):
          // what they show moves under the address.
          ...(narrow
            ? []
            : [
                { id: 'cursor', header: 'Read up to', width: 100, cell: (s: Source) => <Box fontSize="body-s">{cursorLabel(s.cursor)}</Box> },
                // When a run last fetched it: a source that never reads (a page that needs JavaScript, a blocked
                // site) shows as Never here instead of only as an empty graph.
                { id: 'read', header: 'Last read', width: 100, cell: (s: Source) => <Box fontSize="body-s">{s.read_at ? when(s.read_at) : 'Never'}</Box> },
                ...(manage ? [{ id: 'watch', header: 'Last check', width: 120, cell: (s: Source) => <WatchCell watch={s.watch ?? null} /> }] : []),
              ]),
          {
            id: 'on',
            header: 'On',
            width: 72, // the switch plus cell padding
            // Named per source: a screen reader read "switch, on" five times (r3 F3-057).
            cell: (s) => <Toggle ariaLabel={`Read ${s.name}`} checked={toggled[s.id] ?? s.enabled} disabled={!manage || s.id in toggled} onChange={({ detail }) => toggle(s, detail.checked)} />,
          },
          {
            id: 'act',
            header: 'Actions', // a named column header, for screen readers (CTR QA-090)
            width: manage ? 190 : 80, // Edit, Start over and Delete on one line; Name and Address take the rest
            cell: (s) =>
              manage && (
                <SpaceBetween direction="horizontal" size="xs">
                  {/* Editing keeps the source's read position, which deleting and re-adding would lose. */}
                  <Button variant="inline-link" ariaLabel={`Edit ${s.name}`} onClick={() => open({ id: s.id, type: s.type, name: s.name, address: s.address ?? '', instruction: s.instruction ?? '' })}>Edit</Button>
                  <Button variant="inline-link" ariaLabel={`Start ${s.name} over`} onClick={() => setRestarting(s)}>Start over</Button>
                  <Button variant="inline-link" ariaLabel={`Delete ${s.name}`} onClick={() => setDeleting(s)}>Delete</Button>
                </SpaceBetween>
              ),
          },
        ]}
        empty={
          list.error ? <TableLoadError what="sources" error={list.error} onRetry={list.reload} /> :
          <Box textAlign="center" padding="m">
            No sources yet. Add the feeds, sites or pages you want this module to read: competitor blogs, release notes, news, analyst
            feeds. Each run picks up where the last one stopped.
          </Box>
        }
      />
      {draft && (
        <Modal
          visible
          onDismiss={close}
          header={draft.id ? 'Edit source' : 'Add source'}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={close}>Cancel</Button>
                <Button variant="primary" loading={busy} disabled={Boolean(problem)} disabledReason={problem} onClick={() => save(draft)}>
                  {draft.id ? 'Save' : 'Add'}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {leaving && <DiscardWarning what="this source" onKeep={() => setLeaving(false)} onDiscard={() => setDraft(null)} />}
            <FormField
              label="Name"
              constraintText={`${draft.name.trim().length} of ${SOURCE_NAME_MAX} characters.`}
              errorText={draft.name.trim() && problem.includes('name') ? problem : undefined}
            >
              <Input value={draft.name} onChange={({ detail }) => setDraft({ ...draft, name: detail.value })} />
            </FormField>
            <FormField label="Type"><Select expandToViewport selectedOption={{ value: draft.type, label: SOURCE_TYPES[draft.type] }} onChange={({ detail }) => setDraft({ ...draft, type: detail.selectedOption.value as Source['type'] })} options={SOURCE_TYPE_OPTIONS} /></FormField>
            <FormField label="Address" description="Public https URL." errorText={draft.address.trim() && problem.includes('https') ? problem : undefined}>
              <Input value={draft.address} onChange={({ detail }) => setDraft({ ...draft, address: detail.value })} />
            </FormField>
            <FormField
              label="Instruction (optional)"
              description="What to read here and what to skip."
              constraintText={`${draft.instruction.trim().length} of ${SOURCE_INSTRUCTION_MAX} characters.`}
              errorText={draft.instruction.trim() && problem.includes('instruction') ? problem : undefined}
            >
              <Textarea value={draft.instruction} onChange={({ detail }) => setDraft({ ...draft, instruction: detail.value })} />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {deleting && (
        <ConfirmDelete
          header="Delete source"
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteSource(module.slug, deleting.id).then(() => {
              setDeleting(null);
              list.reload();
            }, fail)
          }
        >
          {`Delete the source "${deleting.name}"? Its read position is lost and this cannot be undone. To change its address or instruction, use Edit instead.`}
        </ConfirmDelete>
      )}
      {restarting && (
        <Modal
          visible
          onDismiss={() => setRestarting(null)}
          header={`Start "${restarting.name}" over?`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setRestarting(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  loading={busy}
                  onClick={() => {
                    setBusy(true);
                    void api
                      .updateSource(module.slug, restarting.id, { cursor: '' })
                      .then(() => {
                        notify('success', `"${restarting.name}" starts over: the next check takes the last day's items again.`);
                        setRestarting(null);
                        list.reload();
                      }, fail)
                      .finally(() => setBusy(false));
                  }}
                >
                  Start over
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          Its read position and what the watcher has seen on it are cleared. The next check treats it as new: items from the last 24
          hours wait for the Scout again, and older ones are taken as already known.
        </Modal>
      )}
    </div>
  );
}

/** A run's start: relative for the last hour, the date and time after that (rows stay distinguishable). */
export function startedLabel(iso: string, now = Date.now()): string {
  return now - new Date(iso).getTime() < 3_600_000 ? ago(iso, now) : when(iso);
}

const RUNS_PAGE = 25;

function Runs({ module, version }: { module: Module; version: number }) {
  const { api, sessionEnded } = useSession();
  const { notify, fail } = useNotify();
  const [stopping, setStopping] = React.useState<ModuleRun | null>(null);
  const [busy, setBusy] = React.useState(false);
  const runs = useAsync(() => api.moduleRuns(module.slug), [api, module.slug], [version]);
  /** The newest page, re-read while a run is going: every page used to be re-read every 15 s (r3 F3-017). */
  const [fresh, setFresh] = React.useState<ModuleRun[] | null>(null);
  const [page, setPage] = React.useState(1);
  const all = React.useMemo(() => {
    const listed = runs.data?.items ?? [];
    if (!fresh) return listed;
    const seen = new Set(fresh.map((r) => r.run_id));
    return [...fresh, ...listed.filter((r) => !seen.has(r.run_id))];
  }, [runs.data, fresh]);
  const active = !sessionEnded && all.some((r) => r.status === 'running' || r.status === 'queued');
  React.useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => {
      if (document.visibilityState !== 'visible') return; // nobody is watching a hidden tab
      void api.moduleRunsFirst(module.slug).then((r) => setFresh(r.items), () => undefined);
    }, 15000);
    return () => clearInterval(t);
  }, [active, api, module.slug]);
  const pages = Math.max(1, Math.ceil(all.length / RUNS_PAGE));
  const current = Math.min(page, pages);
  const cancel = async (run: ModuleRun) => {
    setBusy(true);
    try {
      await api.cancelRun(module.slug, run.run_id);
      setFresh(null);
      runs.reload();
      notify('success', 'The run was cancelled.');
      setStopping(null);
    } catch (err) {
      fail(err);
      setStopping(null);
      runs.reload(); // it may have finished on its own meanwhile
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
    {stopping && (
      <Modal
        visible
        onDismiss={() => !busy && setStopping(null)}
        header="Cancel this run?"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setStopping(null)}>Keep it running</Button>
              <Button variant="primary" loading={busy} onClick={() => void cancel(stopping)}>Cancel the run</Button>
            </SpaceBetween>
          </Box>
        }
      >
        The run stops now. What it has already written stays, and the tokens it may have used stay counted
        against today&apos;s budget.
      </Modal>
    )}
    <Table wrapLines
      loading={runs.loading && !runs.data}
      items={all.slice((current - 1) * RUNS_PAGE, current * RUNS_PAGE)}
      pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
      trackBy="run_id"
      variant="container"
      header={
        <Header
          variant="h2"
          counter={runs.data ? `(${all.length}${runs.data.truncated ? '+' : ''})` : undefined}
          actions={
            <Button
              iconName="refresh"
              ariaLabel="Refresh"
              onClick={() => {
                setFresh(null);
                runs.reload();
              }}
            />
          }
        >
          Runs
        </Header>
      }
      columnDefinitions={[
        { id: 'status', header: 'Status', cell: (r: ModuleRun) => <RunState status={r.status} /> },
        { id: 'when', header: 'Started', cell: (r) => <span title={when(r.started_at)}>{startedLabel(r.started_at)}</span> },
        { id: 'trigger', header: 'Started by', cell: (r) => triggerLabel(r.trigger) },
        { id: 'tokens', header: 'Tokens', cell: (r) => tokens(r.weighted_tokens) },
        { id: 'written', header: 'Written', cell: (r) => r.nodes_written },
        {
          id: 'stop',
          header: 'Actions',
          cell: (r) =>
            r.status === 'running' || r.status === 'queued' ? (
              <Button variant="inline-link" ariaLabel={`Cancel the run started ${startedLabel(r.started_at)}`} onClick={() => setStopping(r)}>
                Cancel
              </Button>
            ) : null,
        },
        {
          id: 'summary',
          header: 'Summary',
          cell: (r) =>
            r.summary || r.error ? (
              <ExpandableSection variant="footer" headerText={clip((r.error ?? r.summary).split('\n')[0] as string, 80)}>
                {r.error && <Box color="text-status-error">{r.error}</Box>}
                <Markdown markdown={r.summary} />
                {r.status === 'succeeded' && r.nodes_written === 0 && (
                  <Box color="text-body-secondary" fontSize="body-s">
                    {module.slug === 'scout'
                      ? 'Nothing new in the sources since the last run. Add more sources below, or wait for them to publish.'
                      : 'Nothing new to work from. Run Scout first (and Signals before Trends or Gaps), or widen the instructions above.'}
                  </Box>
                )}
              </ExpandableSection>
            ) : (
              '-'
            ),
          minWidth: 320,
        },
      ]}
      empty={runs.error ? <TableLoadError what="runs" error={runs.error} onRetry={runs.reload} /> : <Box textAlign="center" padding="m">No runs yet.</Box>}
    />
    </>
  );
}

/** The insight list a module fills, if it has one. */
function outputList(m: Module, custom: { kind: string; label: string; module: string }[]): { href: string; label: string } | null {
  const mine = custom.find((c) => c.module === m.slug);
  if (mine) return { href: `/insights/${mine.kind}`, label: mine.label };
  return nodeList(m.output_kind ?? '');
}

/**
 * How far a module has read the graph's evidence, and a way to have it read again: after its
 * instructions change, the records it already wrote keep the old judgement until it reads their
 * evidence again. (Modules with sources keep a position per source instead.)
 */
export function ReadingPosition({ module: m, onChanged }: { module: Module; onChanged: () => void }) {
  const { api } = useSession();
  const { notify, fail } = useNotify();
  const [open, setOpen] = React.useState(false);
  const [from, setFrom] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const read = m.progress?.last_processed_at ?? null;
  const save = (at: string | null) => {
    setBusy(true);
    api
      .resetModuleProgress(m.slug, at)
      .then(() => {
        notify('success', at ? `The next run reads again from ${from}.` : 'The next run reads all evidence again.');
        setOpen(false);
        onChanged();
      }, fail)
      .finally(() => setBusy(false));
  };
  return (
    <Container header={<Header variant="h2" description="The evidence this module has already read; it only reads what is newer.">Reading position</Header>}>
      <SpaceBetween size="s">
        {/* A cursor is "<time>#<node id>" once a run has recorded one: the time is the part a person reads. */}
        <Box>{read ? `Read up to ${when(read.split('#')[0])}.` : 'Has not read any evidence yet.'}</Box>
        <Box>
          <Button onClick={() => setOpen(true)}>Read again from a date</Button>
        </Box>
      </SpaceBetween>
      {open && (
        <Modal
          visible
          header="Read again from a date"
          onDismiss={() => setOpen(false)}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setOpen(false)}>Cancel</Button>
                <Button loading={busy} onClick={() => save(null)}>From the beginning</Button>
                <Button variant="primary" loading={busy} disabled={!from} onClick={() => save(`${from}T00:00:00Z`)}>
                  Read again
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <Box>
              The next run reads every evidence record from this date under the current instructions. It may update
              or add records, and it spends tokens doing so.
            </Box>
            <FormField label="From">
              <DatePicker value={from} onChange={({ detail }) => setFrom(detail.value)} isDateEnabled={(d) => d <= new Date()} placeholder="YYYY/MM/DD" openCalendarAriaLabel={(d) => `Choose a date${d ? `, ${d}` : ''}`} />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
    </Container>
  );
}

export function ModulePage() {
  const { slug = '' } = useParams();
  const { api, me, config } = useSession();
  const { fail, notify } = useNotify();
  const { nav, refresh } = useNav();
  const navigate = useNavigate();
  const module = useAsync(() => api.module(slug), [api, slug]);
  useLoadingPageTitle(!module.data && !module.error, module.data?.name, 'Modules');
  const [runTick, setRunTick] = React.useState(0);
  /** Configuration edits a conflict sent back, put on the version read again. */
  const [carry, setCarry] = React.useState<Module | null>(null);
  const [running, setRunning] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  // Once the module is deleted there is nothing left to save: leave without asking.
  const [deleted, setDeleted] = React.useState(false);
  // From an effect: the navigation blocker takes the new "nothing to save" state only once it has
  // rendered, so navigating in the same callback asked "Leave without saving?" for a deleted module.
  React.useEffect(() => {
    if (deleted) navigate('/modules');
  }, [deleted, navigate]);
  // A failed re-read (after a save, say) keeps the page and the configuration being edited (r3 F3-002).
  if (module.error && !module.data) return <LoadError error={module.error} what="Module" back={{ href: '/modules', label: 'Back to modules' }} onRetry={module.reload} />;
  if (!module.data) return <PageLoading />;
  const m = module.data;
  const manage = can(me, 'module.manage');
  const runsSources = m.grants.includes('sources');
  const output = outputList(m, nav?.custom ?? []);
  const paused = !config.genai_enabled;
  const run = () => {
    setRunning(true);
    void api
      .runModule(m.slug)
      .then(() => {
        notify('success', 'Run started.');
        setRunTick((t) => t + 1);
      }, fail)
      .finally(() => setRunning(false));
  };
  return (
    <ContentLayout
      breadcrumbs={<Crumbs items={[{ text: 'Modules', href: '/modules' }, { text: m.name, href: `/modules/${m.slug}` }]} />}
      header={
        <Header
          variant="h1"
          description={m.description}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {can(me, 'module.run') && m.template !== 'draft' && (
                <Button iconName="caret-right-filled" loading={running} disabled={paused} disabledReason="AI features are paused by an administrator." onClick={run}>
                  Run now
                </Button>
              )}
              {/* In a menu, away from Run now, as on products (r3 F3-102). */}
              {manage && !m.builtin && (
                <ButtonDropdown variant="icon" ariaLabel="More actions" items={[{ id: 'delete', text: 'Delete module' }]} onItemClick={() => setDeleting(true)} />
              )}
            </SpaceBetween>
          }
        >
          {m.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        {module.error && <ReloadFailed error={module.error} what="module" onRetry={module.reload} />}
        {manage ? (
          <Configure
            key={m.version}
            module={m}
            onSaved={() => {
              setCarry(null);
              module.reload();
            }}
            gone={deleted}
            carry={carry}
            onConflict={(edits) => {
              setCarry(edits);
              module.reload();
            }}
          />
        ) : (
          <Container>
            <SpaceBetween size="xs">
              <Box>{scheduleState(m).text}</Box>
              {output && (
                <Link href={output.href} onFollow={(e) => { e.preventDefault(); navigate(output.href); }}>
                  {`See what it found: ${output.label}`}
                </Link>
              )}
            </SpaceBetween>
          </Container>
        )}
        {manage && m.template !== 'draft' && <PromptPanel module={m} onSaved={module.reload} />}
        {manage && !runsSources && m.template !== 'draft' && <ReadingPosition module={m} onChanged={module.reload} />}
        {runsSources && <Sources module={m} />}
        {can(me, 'module.manage') && <Runs module={m} version={runTick} />}
      </SpaceBetween>
      {deleting && (
        <ConfirmDelete
          header="Delete module"
          onDismiss={() => setDeleting(false)}
          onConfirm={() =>
            api.deleteModule(m.slug).then(() => {
              refresh(); // its list leaves the side navigation
              setDeleted(true); // the effect above leaves the page once the guard has let go
            }, fail)
          }
        >
          {`Delete the module "${m.name}"? This cannot be undone. Everything it has published stays in the knowledge graph${
            output ? `, but its list (${output.label}) leaves the navigation, so those records are then only reachable through the Knowledge graph page and its search` : ''
          }.`}
        </ConfirmDelete>
      )}
    </ContentLayout>
  );
}
