// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import Header from '@cloudscape-design/components/header';
import Badge from '@cloudscape-design/components/badge';
import Link from '@cloudscape-design/components/link';
import Multiselect from '@cloudscape-design/components/multiselect';
import Select from '@cloudscape-design/components/select';
import Popover from '@cloudscape-design/components/popover';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Toggle from '@cloudscape-design/components/toggle';
import TokenGroup from '@cloudscape-design/components/token-group';
import React from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import type { AttributeFieldDef, GraphNode, NavEntry } from '@/api/types';
import { seesPrivate, useSession } from '@/auth/session';
import { ErrorState, PageLoading } from '@/components/ErrorState';
import { useNav } from '@/components/NavContext';
import { useNotify } from '@/components/Notifications';
import NodeLink from '@/components/NodeLink';
import { useAsync } from '@/hooks/useAsync';
import { ago, clip, datedText, KIND_HELP, KIND_LABELS, NOT_FOR_ROLE, plural, PRIVATE_KINDS, when } from '@/lib/format';
import { useLoadingPageTitle } from '@/lib/pageTitle';
import { download, slugFile } from '@/radar/export';

interface Help {
  about: string;
  /** The module that writes the list; '' when it is not known. */
  module: string;
  moduleName: string;
  /** What to do when the list is empty, when that is not "run the module". */
  empty?: string;
}

interface ListProps {
  kind: string;
  sort: string;
  q: string;
  /** Field filters (key -> values, any of which matches) and the field-sort direction. */
  filters: Record<string, string[]>;
  order: string;
  /** Closed gaps or retired content ideas are listed too (the server leaves them out otherwise). */
  includeEnded: boolean;
  /** Told the list's fields once the first page arrives, so the page can draw the filters. */
  onSchema: (schema: AttributeFieldDef[]) => void;
  label: string;
  filter: React.ReactNode;
  help: Help;
}

/** Card summaries are clipped so cards in one row stay a similar height; the full text is on the entry. */
const SUMMARY_CHARS = 240;

/**
 * The pages added with Load more, per list and query, for the rest of the session: opening a card
 * and coming Back used to start again at the first page (r3 F3-049).
 */
const loadedPages = new Map<string, { more: GraphNode[]; next: string | null | undefined }>();

/** Test seam: forget the pages kept for Back. */
export function forgetLoadedPages(): void {
  loadedPages.clear();
}

/** One query's results; re-mounted (keyed) when the query changes. "Load more" appends pages. */
/** The enum values of a record as badges, and its first date field, for a card. */
export function FieldSummary({ node, schema }: { node: GraphNode; schema: AttributeFieldDef[] }) {
  const attrs = (node.attributes ?? {}) as Record<string, unknown>;
  const label = (f: AttributeFieldDef, v: string) => f.values?.find((c) => c.value === v)?.label ?? v;
  const badges = schema
    .filter((f) => f.type === 'enum' && typeof attrs[f.key] === 'string')
    .map((f) => {
      const v = attrs[f.key] as string;
      const rank = f.values?.find((c) => c.value === v)?.rank ?? 0;
      // Rank 1 is the most pressing value of a field (for example "Act now"): it gets the warning colour.
      return <Badge key={f.key} color={rank === 1 ? 'red' : rank === 2 ? 'blue' : 'grey'}>{`${f.label}: ${label(f, v)}`}</Badge>;
    });
  const lists = schema
    .filter((f) => f.type === 'string_list' && Array.isArray(attrs[f.key]) && (attrs[f.key] as unknown[]).length)
    .map((f) => `${f.label}: ${(attrs[f.key] as string[]).map((v) => label(f, v)).join(', ')}`);
  const date = schema.find((f) => f.type === 'date' && typeof attrs[f.key] === 'string');
  if (!badges.length && !lists.length && !date) return null;
  return (
    <SpaceBetween size="xxs">
      {badges.length > 0 && <SpaceBetween size="xs" direction="horizontal">{badges}</SpaceBetween>}
      {lists.map((t) => <Box key={t} fontSize="body-s">{t}</Box>)}
      {date && <Box fontSize="body-s">{`${date.label}: ${datedText(attrs[date.key] as string)}`}</Box>}
    </SpaceBetween>
  );
}

function InsightList({ kind, sort, q, label, filter, help, filters, order, includeEnded, onSchema }: ListProps) {
  const { api } = useSession();
  const navigate = useNavigate();
  const { fail } = useNotify();
  const asked = {
    sort,
    q: q || undefined,
    filters,
    order: sort.startsWith('attr:') ? order : undefined,
    include_closed: includeEnded ? ('true' as const) : undefined,
  };
  const filterKey = JSON.stringify(filters);
  const first = useAsync(() => api.insights(kind, asked), [api, kind, sort, q, filterKey, order, includeEnded]);
  const cacheKey = `${kind}|${sort}|${q}|${filterKey}|${order}|${includeEnded}`;
  const schema = first.data?.schema ?? [];
  React.useEffect(() => {
    if (first.data) onSchema(first.data.schema ?? []);
  }, [first.data, onSchema]);
  const [kept] = React.useState(() => loadedPages.get(cacheKey));
  const [more, setMore] = React.useState<GraphNode[]>(kept?.more ?? []);
  const [next, setNext] = React.useState<string | null | undefined>(kept?.next);
  const [busy, setBusy] = React.useState(false);
  /** Where the cards added by the last Load more start, so focus goes to the first of them (r3 F3-103). */
  const [added, setAdded] = React.useState<{ at: number; count: number } | null>(null);
  const cardsBox = React.useRef<HTMLDivElement>(null);
  const firstItems = first.data?.items ?? [];
  const seen = new Set(firstItems.map((n) => n.id));
  const items = [...firstItems, ...more.filter((n) => !seen.has(n.id))];
  const cursor = next === undefined ? first.data?.cursor : next;
  React.useEffect(() => {
    if (!added) return;
    const links = cardsBox.current?.querySelectorAll('li a');
    (links?.[added.at] as HTMLElement | undefined)?.focus();
  }, [added]);
  const loadMore = async (from: string) => {
    setBusy(true);
    try {
      const page = await api.insights(kind, { ...asked, cursor: from });
      const grown = [...more, ...page.items];
      setMore(grown);
      setNext(page.cursor);
      loadedPages.set(cacheKey, { more: grown, next: page.cursor });
      setAdded({ at: items.length, count: page.items.length });
    } catch (err) {
      fail(err); // a notification: the Cards `empty` slot is hidden once items exist
    } finally {
      setBusy(false);
    }
  };
  const empty = first.error ? (
    <SpaceBetween size="s" alignItems="center">
      <StatusIndicator type="error">{`The list could not be loaded: ${first.error.message}`}</StatusIndicator>
      <SpaceBetween size="xs" direction="horizontal">
        <Button onClick={first.reload}>Retry</Button>
        {/* A link can carry a filter or field sort the list no longer has; its controls are drawn only from a
            page that loaded, so this is the way back. */}
        {(Object.keys(filters).length > 0 || sort.startsWith('attr:')) && (
          <Button onClick={() => navigate(`/insights/${kind}`)}>Clear filters and sort</Button>
        )}
      </SpaceBetween>
    </SpaceBetween>
  ) : q || Object.keys(filters).length ? (
    'No matches. Try fewer or different words, or clear a filter.'
  ) : help.empty ? (
    help.empty
  ) : (
    <>
      Nothing published here yet. The {help.moduleName} module writes these when it runs.{' '}
      {help.module && (
        <Link
          href={`/modules/${help.module}`}
          onFollow={(e) => {
            e.preventDefault();
            navigate(`/modules/${help.module}`);
          }}
        >
          Open the module
        </Link>
      )}
    </>
  );
  return (
    <div ref={cardsBox}>
    <div className="air-sr-only" aria-live="polite">{added ? `${plural(added.count, 'more card')} loaded.` : ''}</div>
    <Cards
      items={items}
      loading={first.loading && items.length === 0}
      loadingText="Loading"
      trackBy="id"
      cardsPerRow={[{ cards: 1 }, { minWidth: 700, cards: 2 }]}
      header={
        // While more can be loaded the count is what is shown so far, and says so.
        <Header
          variant="h1"
          description={first.data?.truncated ? `${help.about} Filters and field sorts read the newest 2,000 records only.` : help.about}
          counter={items.length ? (cursor ? (first.data?.total ? `(${items.length} of ${first.data.total} shown)` : `(${items.length} shown)`) : `(${items.length})`) : undefined}
        >
          {label}
        </Header>
      }
      filter={filter}
      cardDefinition={{
        header: (n) => <NodeLink node={n} showSource={false} />,
        sections: [
          { id: 'fields', content: (n) => <FieldSummary node={n} schema={schema} /> },
          { id: 'summary', content: (n) => (n.summary ? clip(n.summary, SUMMARY_CHARS) : <Box color="text-body-secondary">No summary</Box>) },
          {
            id: 'meta',
            content: (n) => (
              <Box fontSize="body-s" color="text-body-secondary">
                Updated <span title={when(n.updated_at)}>{ago(n.updated_at)}</span>{' \u00b7 '}
                {/* A Popover, not a title: a touch or keyboard user cannot read a title attribute. */}
                <Popover
                  dismissButton={false}
                  position="top"
                  size="small"
                  triggerType="text"
                  content="Evidence and related records linked to this in the knowledge graph."
                >
                  {plural(n.degree, 'link')}
                </Popover>
                {typeof n.confidence === 'number' ? (
                  <>
                    {' \u00b7 '}
                    <Popover
                      dismissButton={false}
                      position="top"
                      size="small"
                      triggerType="text"
                      content="How sure the module is, from the strength and agreement of its evidence."
                    >
                      {`confidence ${Math.round(n.confidence * 100)}%`}
                    </Popover>
                  </>
                ) : (
                  ''
                )}
                {n.working_on?.length ? ` \u00b7 ${n.working_on.length} working on it` : ''}
              </Box>
            ),
          },
        ],
      }}
      empty={
        <Box textAlign="center" color="inherit" padding="l">
          {empty}
        </Box>
      }
      pagination={
        cursor ? (
          <Button onClick={() => void loadMore(cursor)} loading={busy}>
            Load more
          </Button>
        ) : undefined
      }
    />
    </div>
  );
}

const SORTS = ['newest', 'connected', 'title', 'relevance'];
/** Lists whose entries can end their life, and the switch that brings those back (CTR HC-126/127, HC-130). */
const ENDED_TOGGLE: Record<string, string> = { gaps: 'Show closed gaps', content: 'Show retired ideas' };

/** One kind's list. Keyed by kind, so nothing typed for one list carries over to the next. */
function KindPage({ kind }: { kind: string }) {
  const { nav, loading, error, refresh } = useNav(); // shared with the side navigation (one /api/nav per session)
  // The search and the sort live in the address: Back, reload and shared links keep them.
  const [params, setParams] = useSearchParams();
  const submitted = params.get('q') ?? '';
  const asked = params.get('sort') ?? '';
  // A field sort ("attr:<key>") is checked by the server against the list's fields: a stale link shows its refusal.
  const sort =
    (SORTS.includes(asked) && (asked !== 'relevance' || submitted)) || (asked.startsWith('attr:') && !submitted)
      ? asked
      : submitted
        ? 'relevance'
        : 'newest';
  const order = params.get('order') === 'desc' ? 'desc' : 'asc';
  const endedLabel = ENDED_TOGGLE[kind];
  const includeEnded = Boolean(endedLabel) && params.get('ended') === '1';
  // Field filters live in the address as f.<key>=a,b, like the search and the sort.
  const filters = React.useMemo(() => {
    const out: Record<string, string[]> = {};
    params.forEach((value, key) => {
      if (key.startsWith('f.') && value) out[key.slice(2)] = value.split(',').filter(Boolean);
    });
    return out;
  }, [params]);
  // null until the first page answers: before then no filter has a control yet, so none is "loose".
  const [loaded, setSchema] = React.useState<AttributeFieldDef[] | null>(null);
  const schema = loaded ?? [];
  const onSchema = React.useCallback((s: AttributeFieldDef[]) => setSchema(s), []);
  // Keyed on what was submitted, so a link with another ?q= opened while already on this list puts
  // its words in the box instead of leaving the old ones there.
  const [typed, setTyped] = React.useState({ from: submitted, text: submitted });
  const query = typed.from === submitted ? typed.text : submitted;
  const setQuery = (text: string) => setTyped({ from: submitted, text });
  const builtin = KIND_HELP[kind];
  const custom = nav?.custom.find((c) => c.kind === kind);
  // A custom list's name arrives with /api/nav: until then the title is not final, so the raw kind is
  // not announced first (r3 F3-047).
  useLoadingPageTitle(!builtin && !custom && loading, KIND_LABELS[kind] ?? custom?.label ?? kind, 'Insights');
  const { me, api } = useSession();
  const { fail } = useNotify();
  const [exporting, setExporting] = React.useState(false);
  // The API refuses these lists to a viewer (403): say why instead of showing the refusal as a failure.
  if (PRIVATE_KINDS.has(kind) && !seesPrivate(me)) return <ErrorState title="Not available to your role">{NOT_FOR_ROLE}</ErrorState>;
  if (!builtin && !custom) {
    // A custom kind's title and module come from /api/nav: wait for it, then say if the list does not exist.
    if (loading) return <PageLoading />;
    // A brief API error used to report a perfectly good custom list as deleted.
    if (error) {
      return (
        <ErrorState title="The list names could not be loaded" onRetry={refresh}>
          {`Whether there is a list called "${kind}" is not known, because the list names could not be read. ${error.message}`}
        </ErrorState>
      );
    }
    return (
      <ErrorState title="List not found">
        {`There is no insight list called "${kind}". It may belong to a module that was deleted.`}
      </ErrorState>
    );
  }
  const own = custom as NavEntry; // only read for a kind that is not built in, which has one
  const label = KIND_LABELS[kind] ?? own.label;
  const help: Help = builtin ?? { about: `Published by the ${own.label} module.`, module: own.module, moduleName: own.label };
  const update = (q: string, s: string, f: Record<string, string[]> = filters, o: string = order) =>
    setParams(
      () => {
        const next = new URLSearchParams();
        if (q) next.set('q', q);
        if (s && s !== (q ? 'relevance' : 'newest')) next.set('sort', s);
        if (s.startsWith('attr:') && o === 'desc') next.set('order', 'desc');
        for (const [key, values] of Object.entries(f)) if (values.length) next.set(`f.${key}`, values.join(','));
        if (includeEnded) next.set('ended', '1');
        return next;
      },
      { replace: true },
    );
  const filterable = schema.filter((f) => f.filter && f.values?.length);
  const offered = (key: string, v: string) => filterable.some((f) => f.key === key && f.values?.some((c) => c.value === v));
  const loose = loaded === null ? [] : Object.entries(filters).flatMap(([key, values]) =>
    values.filter((v) => !offered(key, v)).map((v) => [key, v] as [string, string]),
  );
  const active = Object.values(filters).some((values) => values.length > 0);
  const sortable = schema.filter((f) => f.sort);
  // A new search starts on relevance and the user may then re-sort it; clearing it leaves relevance.
  const submit = (text: string) => {
    if (text === submitted) return;
    update(text, text ? 'relevance' : sort === 'relevance' ? 'newest' : sort);
  };
  const short = query.trim().length === 1;
  const filter = (
    <SpaceBetween size="m" direction="horizontal">
      <TextFilter
        filteringText={query}
        filteringPlaceholder="Search these entries"
        filteringAriaLabel="Search these entries"
        countText={short ? 'Type at least 2 characters' : undefined}
        onChange={({ detail }) => {
          setQuery(detail.filteringText);
          if (!detail.filteringText) submit('');
        }}
        onDelayedChange={({ detail }) => {
          const text = detail.filteringText.trim();
          if (text.length !== 1) submit(text);
        }}
      />
      {filterable.map((f) => {
        const chosen = filters[f.key] ?? [];
        // `filterable` keeps only fields with values to choose from.
        const options = (f.values as NonNullable<AttributeFieldDef['values']>).map((c) => ({ value: c.value, label: c.label }));
        return (
          <Multiselect
            key={f.key}
            placeholder={f.label}
            ariaLabel={`Filter by ${f.label}`}
            filteringType={options.length > 8 ? 'auto' : 'none'}
            options={options}
            selectedOptions={options.filter((o) => chosen.includes(o.value))}
            onChange={({ detail }) =>
              update(submitted, sort, { ...filters, [f.key]: detail.selectedOptions.map((o) => o.value as string) })
            }
            tokenLimit={2}
          />
        );
      })}
      {/* Filters with no control here (a free-form field, or a value the field no longer offers, from an
          Overview link or an old address): shown so they can be removed rather than silently applied. */}
      {loose.length > 0 && (
        <TokenGroup
          i18nStrings={{ limitShowMore: 'Show more', limitShowFewer: 'Show fewer' }}
          items={loose.map(([key, v]) => ({
            label: `${schema.find((f) => f.key === key)?.label ?? key}: ${v}`,
            dismissLabel: `Remove filter ${v}`,
          }))}
          onDismiss={({ detail }) => {
            const [key, v] = loose[detail.itemIndex] as [string, string]; // from `filters`, so the key is there
            update(submitted, sort, { ...filters, [key]: (filters[key] as string[]).filter((x) => x !== v) });
          }}
        />
      )}
      {active && (
        <Button variant="inline-link" onClick={() => update(submitted, sort, {})}>
          Clear filters
        </Button>
      )}
      {sortable.length > 0 && !submitted && (
        <Select
          ariaLabel="Sort by a field"
          placeholder="Sort by a field"
          selectedOption={
            sort.startsWith('attr:')
              ? { value: `${sort}|${order}`, label: `${sortable.find((f) => `attr:${f.key}` === sort)?.label ?? sort.slice(5)} (${order === 'desc' ? 'descending' : 'ascending'})` }
              : null
          }
          options={sortable.flatMap((f) => [
            { value: `attr:${f.key}|asc`, label: `${f.label} (ascending)` },
            { value: `attr:${f.key}|desc`, label: `${f.label} (descending)` },
          ])}
          onChange={({ detail }) => {
            const [s, o] = (detail.selectedOption.value as string).split('|');
            update(submitted, s as string, filters, o as string);
          }}
        />
      )}
      <SegmentedControl
        selectedId={sort.startsWith('attr:') ? null : sort}
        onChange={({ detail }) => update(submitted, detail.selectedId)}
        label="Sort"
        options={[
          { id: 'newest', text: 'Newest' },
          { id: 'connected', text: 'Most connected' },
          { id: 'title', text: 'Title A to Z' }, // CTR HU-077
          { id: 'relevance', text: 'Relevance', disabled: !submitted, disabledReason: 'Search first: relevance ranks the results of a search.' },
        ]}
      />
      {endedLabel && (
        <Toggle
          checked={includeEnded}
          onChange={({ detail }) =>
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                if (detail.checked) next.set('ended', '1');
                else next.delete('ended');
                return next;
              },
              { replace: true },
            )
          }
        >
          {endedLabel}
        </Toggle>
      )}
      <ButtonDropdown
        ariaLabel="Export this list"
        loading={exporting}
        // An export takes the filters and a field sort; a search ranks by relevance, which a file cannot keep.
        disabled={Boolean(submitted) || (sort !== 'newest' && !sort.startsWith('attr:'))}
        disabledReason="Exports keep the filters and a field sort: clear the search and pick Newest or a field sort."
        items={[
          { id: 'csv', text: 'CSV (spreadsheet)' },
          { id: 'md', text: 'Markdown (report table)' },
        ]}
        onItemClick={({ detail }) => {
          const format = detail.id as 'csv' | 'md';
          setExporting(true);
          api
            .exportList(kind, format, { filters, ...(sort.startsWith('attr:') ? { sort, order } : {}) })
            // A byte-order mark so Excel reads the CSV as UTF-8.
            .then((text) => download(slugFile(`${label} ${new Date().toISOString().slice(0, 10)}`, format, 'list'), format === 'csv' ? `\uFEFF${text}` : text, format === 'csv' ? 'text/csv' : 'text/markdown'))
            .catch(fail)
            .finally(() => setExporting(false));
        }}
      >
        Export
      </ButtonDropdown>
    </SpaceBetween>
  );
  return (
    <InsightList
      key={`${kind}|${sort}|${submitted}|${JSON.stringify(filters)}|${order}|${includeEnded}`}
      kind={kind}
      sort={sort}
      q={submitted}
      filters={filters}
      order={order}
      includeEnded={includeEnded}
      onSchema={onSchema}
      label={label}
      filter={filter}
      help={help}
    />
  );
}

export default function InsightsPage() {
  const { kind = 'signals' } = useParams();
  return <KindPage key={kind} kind={kind} />;
}
