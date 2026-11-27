// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import Pagination from '@cloudscape-design/components/pagination';
import Popover from '@cloudscape-design/components/popover';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table, { type TableProps } from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Toggle from '@cloudscape-design/components/toggle';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import type { Dimension, Radar, RadarSchema } from '@/api/types';

import { can, useSession } from '@/auth/session';
import { useNotify } from '@/components/Notifications';
import SlugField from '@/components/SlugField';
import RadarEntryFinder from '@/components/RadarEntryFinder';
import FavouriteToggle, { useFavourites } from '@/components/FavouriteToggle';
import { useAsync } from '@/hooks/useAsync';
import { ago, modeLabel, plural, RADAR_MODES, slugify, when } from '@/lib/format';
import { radarsHelp, sectionVisible } from '@/lib/profile';
import { usePageTitle } from '@/lib/pageTitle';
import { fold } from '@/radar/layout';
import { RadarChart } from '@/radar/RadarChart';

/** The template a new radar starts from when the API offers it. */
export const DEFAULT_TEMPLATE = 'competitor_landscape';

export const TEMPLATE_NAMES: Record<string, string> = {
  technology_adoption: 'Technology adoption',
  competitor_landscape: 'Competitor landscape',
  vendor_map: 'Vendor map',
  customer_segments: 'Customer segments',
  blank: 'Blank',
};

/** Backend limits (radars/model.py): the chart is drawn for at most these many of each. */
export const LIMITS = { sectors: 8, rings: 6, markers: 6 } as const;

/** The backend's limits on a radar's purpose and name (radars/model.py). */
export const PURPOSE_MAX = 300;
export const NAME_MAX = 120;
/** The API's limits on a value name and a heading (radars/model.py DIM_NAME_MAX, labels 1-30): r3 F3-090. */
export const VALUE_MAX = 40;
export const HEADING_MAX = 30;
/** How many radars someone who is not an administrator may own (radars/service.py): r3 F3-060. */
export const OWNED_MAX = 25;

/** What is wrong with a heading, or ''. */
export function headingError(text: string): string {
  return text.trim().length > HEADING_MAX ? `At most ${HEADING_MAX} characters (this is ${text.trim().length}).` : '';
}

type Kind = keyof typeof LIMITS;

interface Draft {
  sector_label: string;
  ring_label: string;
  marker_label: string;
  sectors: string;
  rings: string;
  markers: string;
}

function toDraft(schema: RadarSchema): Draft {
  const text = (d: Dimension[]) => d.map((x) => x.name).join('\n');
  return {
    sector_label: schema.sector_label,
    ring_label: schema.ring_label,
    marker_label: schema.marker_label,
    sectors: text(schema.sectors),
    rings: text(schema.rings),
    markers: text(schema.markers),
  };
}

const names = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean);

/** The schema the draft describes; names kept from the template keep their colour and glyph. */
export function draftSchema(draft: Draft, base: RadarSchema): RadarSchema {
  const dims = (text: string, from: Dimension[]) =>
    names(text).map((name) => from.find((d) => d.name === name) ?? { name });
  return {
    sector_label: draft.sector_label.trim() || base.sector_label,
    ring_label: draft.ring_label.trim() || base.ring_label,
    marker_label: draft.marker_label.trim() || base.marker_label,
    sectors: dims(draft.sectors, base.sectors),
    rings: dims(draft.rings, base.rings),
    markers: dims(draft.markers, base.markers),
  };
}

/** What is wrong with one list, or '' when it is fine. */
export function listError(kind: Kind, text: string): string {
  const list = names(text);
  if (kind !== 'markers' && list.length === 0) return 'Add at least one, one per line.';
  if (list.length > LIMITS[kind]) return `At most ${LIMITS[kind]} (you have ${list.length}).`;
  const long = list.find((n) => n.length > VALUE_MAX);
  if (long) return `"${long.slice(0, 20)}\u2026" is too long: at most ${VALUE_MAX} characters a value.`;
  const seen = new Set<string>();
  for (const n of list) {
    const k = fold(n);
    if (seen.has(k)) return `"${n}" is listed twice.`;
    seen.add(k);
  }
  return '';
}

/** The part of a schema the chart can draw: within the limits and without repeats (for a preview of a list being fixed). */
export function drawable(schema: RadarSchema): RadarSchema {
  const fit = (xs: Dimension[], max: number) => xs.filter((d, i) => xs.findIndex((x) => fold(x.name) === fold(d.name)) === i).slice(0, max);
  return { ...schema, sectors: fit(schema.sectors, LIMITS.sectors), rings: fit(schema.rings, LIMITS.rings), markers: fit(schema.markers, LIMITS.markers) };
}

function CreateRadar({ onClose }: { onClose: () => void }) {
  const { api } = useSession();
  const { fail } = useNotify();
  const navigate = useNavigate();
  const templates = useAsync(() => api.radarTemplates(), [api]);
  /** The API refused the address as taken: said on the Address field, in its words (r3 F3-079). */
  const [taken, setTaken] = React.useState<string | null>(null);
  const [name, setName] = React.useState('');
  // null until the field is edited: the fallback to the name must not put the address back when the
  // user deletes the last character to retype it.
  const [slug, setSlug] = React.useState<string | null>(null);
  const [purpose, setPurpose] = React.useState('');
  // null until a template is chosen or the list arrives: a hard-coded key the backend has renamed
  // away would be sent as an unknown template with no hint why Create failed.
  const [picked, setPicked] = React.useState<string | null>(null);
  /** Edits per template, so switching templates shows each one's own lists. */
  const [drafts, setDrafts] = React.useState<Record<string, Draft>>({});
  const [busy, setBusy] = React.useState(false);
  const [leaving, setLeaving] = React.useState(false);
  const first = templates.data?.items[0]?.key;
  const template = picked ?? (templates.data?.items.some((t) => t.key === DEFAULT_TEMPLATE) ? DEFAULT_TEMPLATE : (first ?? DEFAULT_TEMPLATE));
  const setTemplate = setPicked;
  const chosen = templates.data?.items.find((t) => t.key === template);
  const edited = template in drafts;
  const draft = drafts[template] ?? (chosen ? toDraft(chosen.schema) : null);
  const effectiveSlug = slug ?? slugify(name);
  const schema = chosen && draft ? draftSchema(draft, chosen.schema) : null;
  const errors = draft
    ? { sectors: listError('sectors', draft.sectors), rings: listError('rings', draft.rings), markers: listError('markers', draft.markers) }
    : { sectors: '', rings: '', markers: '' };
  const headingErrors = draft
    ? { sector: headingError(draft.sector_label), ring: headingError(draft.ring_label), marker: headingError(draft.marker_label) }
    : { sector: '', ring: '', marker: '' };
  const invalid = Boolean(errors.sectors || errors.rings || errors.markers || headingErrors.sector || headingErrors.ring || headingErrors.marker);
  const noAddress = Boolean(name.trim()) && !slugify(effectiveSlug);
  const tooLong = purpose.trim().length > PURPOSE_MAX;
  const dirty = Boolean(name.trim() || slug || purpose.trim() || Object.keys(drafts).length);
  const blocked = templates.loading && !templates.data
    ? 'Waiting for the templates.'
    : !name.trim()
    ? 'Enter a name first.'
    : noAddress
      ? 'Enter an address first.'
      : invalid
        ? 'Fix the lists first.'
        : tooLong
          ? `Shorten the purpose to ${PURPOSE_MAX} characters.`
          : '';
  const change = (current: Draft, patch: Partial<Draft>) => setDrafts((all) => ({ ...all, [template]: { ...current, ...patch } }));
  // A filled-in form is never thrown away by a stray click on the backdrop or Esc.
  const close = () => (dirty ? setLeaving(true) : onClose());
  const submit = async () => {
    if (blocked) return;
    setBusy(true);
    try {
      const body = {
        slug: slugify(effectiveSlug),
        name: name.trim(),
        ...(edited && schema ? { schema } : { template }),
        // Shown under the radar's title and edited in Settings: one field, "purpose", everywhere.
        ...(purpose.trim() ? { purpose: purpose.trim() } : {}),
      };
      const r = await api.createRadar(body);
      navigate(`/radars/${r.slug}`);
    } catch (err) {
      if (err instanceof ApiError && err.details.code === 'slug_taken') setTaken(slugify(effectiveSlug));
      else fail(err);
    } finally {
      setBusy(false);
    }
  };
  const count = (d: Draft, kind: Kind) => `${names(d[kind]).length} of at most ${LIMITS[kind]}`;
  const templateOptions = (templates.data?.items ?? []).map((t) => ({ value: t.key, label: TEMPLATE_NAMES[t.key] ?? t.key }));
  return (
    <Modal visible onDismiss={close} header="Create a radar" size="max">
      <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <Form
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button formAction="none" variant="link" onClick={close}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={Boolean(blocked)} disabledReason={blocked}>Create</Button>
            </SpaceBetween>
          }
        >
          <SpaceBetween size="m">
            {leaving && (
              <Alert
                type="warning"
                // Esc at the bottom of a tall form left the warning out of view, so Esc seemed to do
                // nothing at all.
                ref={(el) => el?.focus()}
                header="Discard this radar?"
                action={
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button formAction="none" onClick={() => setLeaving(false)}>Keep editing</Button>
                    <Button formAction="none" onClick={onClose}>Discard</Button>
                  </SpaceBetween>
                }
              >
                What you entered is lost if you close the dialog now.
              </Alert>
            )}
            <ColumnLayout columns={2}>
              <SpaceBetween size="m">
                <FormField label="Name">
                  <Input ariaLabel="Name" value={name} onChange={({ detail }) => setName(detail.value)} placeholder="What this radar tracks" />
                </FormField>
                <SlugField
                  value={effectiveSlug}
                  onChange={setSlug}
                  usedIn="the radar's link"
                  errorText={
                    noAddress
                      ? 'Enter an address: the name has no letters or digits to make one from.'
                      : taken !== null && taken === slugify(effectiveSlug)
                        ? 'This address is taken by another radar. Choose another one.'
                        : undefined
                  }
                />
                <FormField
                  label="Purpose"
                  constraintText={`Optional. Shown under the radar's title and read by the Radar curator; you can change it in Settings. ${purpose.trim().length} of ${PURPOSE_MAX} characters.`}
                  errorText={tooLong ? `At most ${PURPOSE_MAX} characters.` : undefined}
                >
                  <Textarea ariaLabel="Purpose" rows={2} value={purpose} onChange={({ detail }) => setPurpose(detail.value)} placeholder={chosen?.purpose} />
                </FormField>
                <FormField label="Start from" description={chosen?.purpose}>
                  <Select
                    expandToViewport
                    selectedOption={{ value: template, label: TEMPLATE_NAMES[template] ?? template }}
                    onChange={({ detail }) => setTemplate(detail.selectedOption.value as string)}
                    options={templateOptions}
                    statusType={templates.error ? 'error' : templates.loading ? 'loading' : 'finished'}
                    loadingText="Loading templates"
                    errorText="The templates could not be loaded."
                  />
                </FormField>
                {templates.error && (
                  <Alert type="error" action={<Button formAction="none" onClick={templates.reload}>Retry</Button>}>
                    The templates could not be loaded, so their lists cannot be shown or edited. You can still create the radar from the template
                    as it is. ({templates.error.message})
                  </Alert>
                )}
                {draft && (
                  <>
                    <ColumnLayout columns={3}>
                      <FormField label="Sector heading" errorText={headingErrors.sector || undefined}>
                        <Input ariaLabel="Sector heading" value={draft.sector_label} onChange={({ detail }) => change(draft, { sector_label: detail.value })} />
                      </FormField>
                      <FormField label="Ring heading" errorText={headingErrors.ring || undefined}>
                        <Input ariaLabel="Ring heading" value={draft.ring_label} onChange={({ detail }) => change(draft, { ring_label: detail.value })} />
                      </FormField>
                      <FormField label="Marker heading" errorText={headingErrors.marker || undefined}>
                        <Input ariaLabel="Marker heading" value={draft.marker_label} onChange={({ detail }) => change(draft, { marker_label: detail.value })} />
                      </FormField>
                    </ColumnLayout>
                    <FormField label={`${draft.sector_label || 'Sectors'} (one per line)`} constraintText={count(draft, 'sectors')} errorText={errors.sectors}>
                      <Textarea ariaLabel="Sectors" rows={4} value={draft.sectors} onChange={({ detail }) => change(draft, { sectors: detail.value })} />
                    </FormField>
                    <FormField label={`${draft.ring_label || 'Rings'} (one per line, inner first)`} constraintText={count(draft, 'rings')} errorText={errors.rings}>
                      <Textarea ariaLabel="Rings" rows={4} value={draft.rings} onChange={({ detail }) => change(draft, { rings: detail.value })} />
                    </FormField>
                    <FormField label={`${draft.marker_label || 'Markers'} (one per line, optional)`} constraintText={count(draft, 'markers')} errorText={errors.markers}>
                      <Textarea ariaLabel="Markers" rows={3} value={draft.markers} onChange={({ detail }) => change(draft, { markers: detail.value })} />
                    </FormField>
                  </>
                )}
              </SpaceBetween>
              {schema && (
                <Box>
                  <Box variant="h4">Preview</Box>
                  {/* Kept while a list is being fixed (no layout jump), dimmed with the reason. */}
                  {invalid && <Box color="text-status-warning">Fix the lists to update the preview.</Box>}
                  <div style={{ opacity: invalid ? 0.35 : 1 }}>
                    <RadarChart schema={drawable(schema)} entries={[]} size={480} title={name.trim() || 'New'} interactive={false} />
                  </div>
                </Box>
              )}
            </ColumnLayout>
          </SpaceBetween>
        </Form>
      </form>
    </Modal>
  );
}

type Row = Radar & { can_edit: boolean };

const SORTS = ['name', 'entries', 'updated'];

const PAGE = 20;

const COMPARE: Record<string, (a: Row, b: Row) => number> = {
  name: (a, b) => a.name.localeCompare(b.name),
  entries: (a, b) => a.entry_count - b.entry_count,
  updated: (a, b) => a.updated_at.localeCompare(b.updated_at),
};

export default function RadarsPage() {
  usePageTitle('Radars');
  const { api, me } = useSession();
  const navigate = useNavigate();
  const radars = useAsync(() => api.radars(), [api]);
  const favourites = useFavourites();
  // ?create=1 opens the dialog straight away (the Getting started checklist links here).
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = React.useState(() => params.get('create') === '1' && can(me, 'radar.create'));
  // The filter, the sorting and the page live in the address, so Back from a radar keeps them (r3 F3-050).
  const filter = params.get('q') ?? '';
  const asked = params.get('sort') ?? '';
  const sorting: { column: TableProps.SortingColumn<Row>; descending: boolean } = {
    column: { sortingField: SORTS.includes(asked) ? asked : 'updated' },
    descending: params.get('dir') !== 'asc',
  };
  const page = Math.max(1, Number(params.get('page')) || 1);
  const mine = params.get('mine') === '1';
  const starred = params.get('fav') === '1';
  const update = (patch: Record<string, string>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v) next.set(k, v);
          else next.delete(k);
        }
        return next;
      },
      { replace: true },
    );
  const setFilter = (q: string) => update({ q, page: '' });
  const setPage = (n: number) => update({ page: n > 1 ? String(n) : '' });
  const closeCreate = () => {
    setCreating(false);
    if (params.has('create')) update({ create: '' });
  };
  const needle = filter.trim().toLowerCase();
  const all = radars.data?.items ?? [];
  // Which radars this editor may change, so they need not open each one to find out (r3 F3-061).
  const matching = all.filter(
    (r) =>
      (!needle || `${r.name} ${r.purpose}`.toLowerCase().includes(needle)) &&
      (!mine || r.can_edit) &&
      (!starred || favourites.favourites.has(r.slug)),
  );
  // Past the per-owner cap the API refuses Create only after the whole form (r3 F3-060).
  const owned = all.filter((r) => r.owner === me?.subject).length;
  const capped = me?.role !== 'admin' && owned >= OWNED_MAX;
  const compare = COMPARE[sorting.column.sortingField as string] as (a: Row, b: Row) => number;
  // Favourites first, then the chosen order (CTR sec. 9.3).
  const fav = (r: Row) => (favourites.favourites.has(r.slug) ? 0 : 1);
  const sorted = [...matching].sort((a, b) => fav(a) - fav(b) || (sorting.descending ? -compare(a, b) : compare(a, b)));
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
  const current = Math.min(page, pages);
  return (
    <>
      <Table wrapLines
        loading={radars.loading && !radars.data}
        loadingText="Loading radars"
        items={sorted.slice((current - 1) * PAGE, current * PAGE)}
        trackBy="slug"
        sortingColumn={sorting.column}
        sortingDescending={sorting.descending}
        onSortingChange={({ detail }) =>
          update({ sort: String(detail.sortingColumn.sortingField), dir: detail.isDescending ? '' : 'asc', page: '' })
        }
        filter={
          <SpaceBetween direction="horizontal" size="s" alignItems="center">
            <TextFilter
              filteringText={filter}
              filteringPlaceholder="Find radars by name or purpose"
              filteringAriaLabel="Find radars by name or purpose"
              countText={needle ? plural(matching.length, 'match', 'matches') : undefined}
              onChange={({ detail }) => setFilter(detail.filteringText)}
            />
            <Toggle checked={starred} onChange={({ detail }) => update({ fav: detail.checked ? '1' : '', page: '' })}>
              Only favourites
            </Toggle>
            {can(me, 'radar.edit') && (
              <Toggle checked={mine} onChange={({ detail }) => update({ mine: detail.checked ? '1' : '', page: '' })}>
                Only radars I can edit
              </Toggle>
            )}
          </SpaceBetween>
        }
        pagination={<Pagination currentPageIndex={current} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        header={
          <Header
            variant="h1"
            counter={radars.data ? `(${all.length})` : undefined}
            description={`${radarsHelp()} Open one to see its chart and legend, or create your own from a template.`}
            actions={
              can(me, 'radar.create') && (
                <Button
                  variant="primary"
                  disabled={capped}
                  disabledReason={`You own ${owned} radars, the most one person may own. Delete one, or ask an administrator to transfer one to someone else.`}
                  onClick={() => setCreating(true)}
                >
                  Create radar
                </Button>
              )
            }
          >
            Radars
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            sortingField: 'name',
            cell: (r) => (
              <SpaceBetween size="xxxs">
                <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
                  <FavouriteToggle slug={r.slug} name={r.name} favourites={favourites} />
                <Link href={`/radars/${r.slug}`} onFollow={(e) => { e.preventDefault(); navigate(`/radars/${r.slug}`); }}>
                  {r.name}
                </Link>
                </SpaceBetween>
                {r.can_edit && <Box fontSize="body-s" color="text-status-success">You can edit</Box>}
              </SpaceBetween>
            ),
          },
          { id: 'purpose', header: 'Purpose', cell: (r) => r.purpose || '-' },
          { id: 'entries', header: 'Entries', sortingField: 'entries', cell: (r) => r.entry_count },
          {
            id: 'mode',
            header: (
              <Popover
                header="AI suggestions"
                triggerType="text"
                content={
                  <SpaceBetween size="xs">
                    <Box>Whether the Radar curator module proposes changes to this radar:</Box>
                    {RADAR_MODES.map((m) => (
                      <Box key={m.value} fontSize="body-s"><b>{m.label}</b>: {m.description}</Box>
                    ))}
                  </SpaceBetween>
                }
              >
                AI suggestions
              </Popover>
            ),
            // "Autopilot" alone promised changes a radar whose toggle is off never applies.
            cell: (r) => (r.mode === 'autopilot' && !r.auto_apply_agent ? 'Autopilot (held for review)' : modeLabel(r.mode)),
          },
          { id: 'updated', header: 'Updated', sortingField: 'updated', cell: (r) => <span title={when(r.updated_at)}>{ago(r.updated_at)}</span> },
        ]}
        empty={
          radars.error ? (
            // A failed load must never read as "there are none": that invites Create radar over a full list.
            <Box textAlign="center" padding="l">
              <SpaceBetween size="xs" alignItems="center">
                <StatusIndicator type="error">{`The radars could not be loaded: ${radars.error.message}`}</StatusIndicator>
                <Button onClick={radars.reload}>Retry</Button>
              </SpaceBetween>
            </Box>
          ) : needle ? (
            <Box textAlign="center" padding="l">No radars match &quot;{filter.trim()}&quot;.</Box>
          ) : starred ? (
            <Box textAlign="center" padding="l">No favourites yet. Choose the star next to a radar to add it.</Box>
          ) : mine ? (
            <Box textAlign="center" padding="l">You cannot edit any radar yet. The owner of a radar can add you under its Settings.</Box>
          ) : (
          <Box textAlign="center" padding="l">
            <SpaceBetween size="xs">
              <b>No radars yet.</b>
              <span>
                {can(me, 'radar.create') ? 'Choose Create radar and start from a template.' : 'An editor or administrator can create one.'}
                {can(me, 'sample.load') && sectionVisible('sample_data') ? ' To see a finished example first, load the sample data under Administration > Sample data.' : ''}
              </span>
            </SpaceBetween>
          </Box>
          )
        }
      />
      <Box margin={{ top: 'l' }}>
        <RadarEntryFinder />
      </Box>
      {creating && <CreateRadar onClose={closeCreate} />}
    </>
  );
}
