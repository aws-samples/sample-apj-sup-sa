// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import Multiselect from '@cloudscape-design/components/multiselect';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import React from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import type { Edge, GraphBrief, Neighborhood, SearchHit } from '@/api/types';
import { seesPrivate, useSession } from '@/auth/session';
import { EdgeDetails, NodeDetails } from '@/components/graph/GraphDetails';
import { GraphScopeBanner } from '@/components/graph/GraphScopeBanner';
import { GraphView, legendDisplayName, type GraphEdge, type GraphNode } from '@/components/graph/GraphView';
import { useNav } from '@/components/NavContext';
import NodeLink from '@/components/NodeLink';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';
import { ago, clip, graphHref, PRIVATE_TYPES, TYPE_LABELS, typeLabel } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';

import { kindNames, toCanvas } from './graphData';

/** How many nodes the overview asks for when no node is chosen. */
export const OVERVIEW_LIMIT = 150;
export const SEARCH_LIMIT = 30;
/** Neighbours per page in the one-node view (the API draws the node itself plus this many). */
export const NEIGHBOUR_PAGE = 149;
const CANVAS_HEIGHT = 600;

/**
 * Why a focused node could not be loaded, by status: a 404 is "deleted", anything else is said as it
 * is. A viewer gets a 404 for a record kept from their role too, and the API does not say which.
 */
function focusProblem(error: unknown, privateOk: boolean): string {
  if (error instanceof ApiError && error.status === 404) {
    return privateOk
      ? 'This node could not be found. It may have been deleted.'
      : 'This node is not available to your role, or it has been deleted.';
  }
  return `This node could not be loaded: ${(error as Error).message}`;
}

/** A 404 for a viewer: nothing failed, so it is said as information, without a Retry that cannot help. */
function hiddenForViewer(error: unknown, privateOk: boolean): boolean {
  return !privateOk && error instanceof ApiError && error.status === 404;
}

type Selection = { kind: 'node'; id: string } | { kind: 'edge'; id: string } | null;

/** The canvas with its details panel beside it: a click shows details, a double-click opens the node's relationships. */
function Explorer({
  hood,
  mode,
  focusId,
  names,
  onScope,
}: {
  hood: Neighborhood;
  mode: 'overview' | 'scoped';
  focusId: string | null;
  names: Record<string, string>;
  onScope: (id: string) => void;
}) {
  const canvas = React.useMemo(() => toCanvas(hood), [hood]);
  const [selected, setSelected] = React.useState<Selection>(null);
  const byId = React.useMemo(() => new Map(hood.nodes.map((n) => [n.id, n])), [hood]);
  const edgeById = React.useMemo(
    () => new Map(canvas.edges.map((e) => [e.id, e.properties as unknown as Edge])),
    [canvas],
  );
  const node: GraphBrief | undefined = selected?.kind === 'node' ? byId.get(selected.id) : undefined;
  const edge: Edge | undefined = selected?.kind === 'edge' ? edgeById.get(selected.id) : undefined;
  const panel = node ? (
    <NodeDetails node={node} names={names} onScope={onScope} onClose={() => setSelected(null)} />
  ) : edge ? (
    <EdgeDetails edge={edge} from={byId.get(edge.from)} to={byId.get(edge.to)} onClose={() => setSelected(null)} />
  ) : null;
  const view = (
    <GraphView
      nodes={canvas.nodes}
      edges={canvas.edges}
      height={CANVAS_HEIGHT}
      mode={mode}
      focusId={focusId}
      selectedId={selected?.kind === 'node' ? selected.id : null}
      onNodeClick={(n: GraphNode) => setSelected({ kind: 'node', id: n.id })}
      onNodeDoubleClick={(n: GraphNode) => onScope(n.id)}
      onEdgeClick={(e: GraphEdge) => setSelected({ kind: 'edge', id: e.id })}
      names={names}
    />
  );
  // One layout whether or not the panel shows: wrapping the canvas only once something was clicked
  // remounted it, which threw the layout away and lost the second click of a double-click.
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>
      <div style={{ flex: '3 1 480px', minWidth: 0 }}>{view}</div>
      {panel && <div style={{ flex: '1 1 280px', minWidth: 0 }}>{panel}</div>}
    </div>
  );
}

export default function GraphPage() {
  usePageTitle('Knowledge graph');
  const { api, me } = useSession();
  const privateOk = seesPrivate(me);
  const { fail } = useNotify();
  const { nav } = useNav();
  const names = React.useMemo(() => kindNames(nav), [nav]);
  const [query, setQuery] = React.useState('');
  const [types, setTypes] = React.useState<string[]>([]);
  const [hits, setHits] = React.useState<SearchHit[] | null>(null);
  const [keywordsOnly, setKeywordsOnly] = React.useState(false);
  const [tooShort, setTooShort] = React.useState(false);
  const [params, setParams] = useSearchParams();
  const focus = params.get('focus');
  const offset = Math.max(0, Number.parseInt(params.get('offset') ?? '0', 10) || 0);
  /** The neighborhood and the node and page it was loaded for, so a new focus never shows the old picture. */
  const [hood, setHood] = React.useState<{ key: string; data: Neighborhood | null; error?: unknown } | null>(null);
  const [attempt, setAttempt] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const key = `${focus}#${offset}`;
  // The overview is only drawn without a focus; a deep link does not pay for it.
  const overview = useAsync(() => (focus ? Promise.resolve(null) : api.explore(OVERVIEW_LIMIT)), [api, Boolean(focus)]);

  // The chosen node lives in the address (?focus=type/uid&offset=N) so a node can be linked to from anywhere.
  React.useEffect(() => {
    if (!focus) return undefined;
    // Fast clicks: only the latest request may set the neighborhood, whichever answers last.
    let live = true;
    api.neighbors(focus, 1, offset).then(
      (next) => live && setHood({ key, data: next }),
      (error: unknown) => live && setHood({ key, data: null, error }),
    );
    return () => {
      live = false;
    };
  }, [api, focus, offset, key, attempt]);

  const search = async (forTypes = types) => {
    const text = query.trim();
    // A query of only spaces used to do nothing without a word (CTR HU-086).
    setTooShort(query.length > 0 && text.length < 2);
    if (text.length < 2) return;
    setBusy(true);
    try {
      const found = await api.search(text, forTypes, SEARCH_LIMIT);
      setHits(found.items);
      setKeywordsOnly(Boolean(found.degraded));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  // Each node opened is a step in the history, so Back returns to the node before.
  const open = (id: string) => setParams({ focus: id });
  const current = hood && hood.key === key ? hood : null;
  const focused = current?.data?.nodes.find((n) => n.id === focus);
  const total = current?.data?.total;
  return (
    <ContentLayout header={<Header variant="h1" description="Everything the modules have recorded: organizations, people, products, topics, evidence and insights.">Knowledge graph</Header>}>
      <SpaceBetween size="l">
        <Container>
          <form onSubmit={(e) => { e.preventDefault(); void search(); }}>
            <SpaceBetween size="xs">
              <Grid gridDefinition={[{ colspan: { default: 12, s: 7 } }, { colspan: { default: 12, s: 5 } }]}>
                <SpaceBetween direction="horizontal" size="xs">
                  <Input type="search" value={query} onChange={({ detail }) => setQuery(detail.value)} placeholder="Search the graph" ariaLabel="Search the graph" />
                  <Button variant="primary" loading={busy}>Search</Button>
                </SpaceBetween>
                <Multiselect
                  expandToViewport
                  ariaLabel="Types"
                  selectedOptions={types.map((t) => ({ value: t, label: typeLabel(t) }))}
                  onChange={({ detail }) => {
                    const next = detail.selectedOptions.map((o) => o.value as string);
                    setTypes(next);
                    if (hits) void search(next); // the results follow the type filter
                  }}
                  // A viewer's search never returns gaps, content ideas or drafts: offering them found nothing.
                  options={Object.keys(TYPE_LABELS)
                    .filter((t) => privateOk || !PRIVATE_TYPES.has(t))
                    .map((t) => ({ value: t, label: typeLabel(t) }))}
                  placeholder="All types"
                />
              </Grid>
              {tooShort && <Box color="text-status-warning" fontSize="body-s">Type at least 2 characters.</Box>}
              {busy ? (
                <Spinner />
              ) : hits && (
                <ExpandableSection
                  defaultExpanded
                  headerText={`Results (${hits.length}${hits.length >= SEARCH_LIMIT ? '+' : ''})`}
                  // A full page looked complete: say there may be more (r3 F3-095).
                  headerDescription={hits.length >= SEARCH_LIMIT ? `The first ${SEARCH_LIMIT} matches: refine the search or filter by type to see others.` : undefined}
                >
                  {keywordsOnly && (
                    <Box margin={{ bottom: 's' }}>
                      <StatusIndicator type="warning">
                        Search by meaning is unavailable right now, so these are matches on the words you typed.
                      </StatusIndicator>
                    </Box>
                  )}
                  {hits.length === 0 ? (
                    <Box>No matches.</Box>
                  ) : (
                    <SpaceBetween size="s">
                      {hits.map((h) => (
                        <div key={h.node.id}>
                          <Badge>{legendDisplayName(h.node.type, names)}</Badge>{' '}
                          {/* A real link: a button could not be middle-clicked or copied as an address. */}
                          <Link
                            href={graphHref(h.node.id)}
                            onFollow={(e) => {
                              e.preventDefault();
                              open(h.node.id);
                            }}
                          >
                            {h.node.title}
                          </Link>{' '}
                          <NodeLink node={{ id: h.node.id, title: 'Open' }} showSource={false} />
                          <Box fontSize="body-s" color="text-body-secondary">
                            {clip(h.node.summary, 140)}{' \u00b7 '}{ago(h.node.updated_at)}
                          </Box>
                        </div>
                      ))}
                    </SpaceBetween>
                  )}
                </ExpandableSection>
              )}
            </SpaceBetween>
          </form>
        </Container>
        <Container header={<Header variant="h2">{focus ? (focused?.title ?? 'Relationships') : 'Overview'}</Header>}>
          <SpaceBetween size="s">
            {focus && current?.data ? (
              <>
                <GraphScopeBanner
                  title={focused?.title ?? focus}
                  kind={legendDisplayName(focused?.type ?? '', names)}
                  connections={focused?.degree ?? total ?? 0}
                  page={
                    total !== undefined
                      ? { total, offset, pageSize: NEIGHBOUR_PAGE, onPage: (next) => setParams({ focus, offset: String(next) }) }
                      : undefined
                  }
                  onExit={() => setParams({})}
                />
                {/* An answer without paging (an older server) can still be cut: say so (CR2-F15). */}
                {current.data.truncated && total === undefined && (
                  <StatusIndicator type="info">
                    {`Showing ${current.data.nodes.length} of the connected nodes: this one has more than the view can draw.`}
                  </StatusIndicator>
                )}
                <Explorer key={key} hood={current.data} mode="scoped" focusId={focus} names={names} onScope={open} />
              </>
            ) : focus && current ? (
              hiddenForViewer(current.error, privateOk) ? (
                <StatusIndicator type="info">{focusProblem(current.error, privateOk)}</StatusIndicator>
              ) : (
                <SpaceBetween size="xs">
                  <StatusIndicator type="error">{focusProblem(current.error, privateOk)}</StatusIndicator>
                  <Button onClick={() => setAttempt((a) => a + 1)}>Retry</Button>
                </SpaceBetween>
              )
            ) : focus ? (
              <Spinner />
            ) : overview.error ? (
              <SpaceBetween size="xs">
                <StatusIndicator type="error">{`The graph could not be loaded: ${overview.error.message}`}</StatusIndicator>
                <Button onClick={overview.reload}>Retry</Button>
              </SpaceBetween>
            ) : overview.data && overview.data.nodes.length > 0 ? (
              <>
                <Box fontSize="body-s" color="text-body-secondary">
                  The {overview.data.nodes.length} most connected nodes. Scroll to zoom and drag to move; labels appear as you zoom in.
                  Click a node or a link to see what it is, double-click a node to show everything it is connected to.
                </Box>
                <Explorer hood={overview.data} mode="overview" focusId={null} names={names} onScope={open} />
              </>
            ) : overview.loading ? (
              <Spinner />
            ) : (
              <Box color="text-body-secondary">The graph is empty. Run a module, or search once it has data.</Box>
            )}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
