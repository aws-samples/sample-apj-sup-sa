// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide, forceManyBody, forceX, forceY } from 'd3';
import React from 'react';

import { typeLabel } from '@/lib/format';

/**
 * The graph canvas, ported from the CoP Tech Radar's knowledge-graph viewer: zoom and pan, drag to
 * rearrange, size by connections, level of detail by zoom, coloured and directed edges, a filtering
 * legend, hover tooltips and dimming outside the selection. Node and edge types are this
 * deployment's own (a custom insight kind gets a stable colour of its own).
 */

export interface GraphNode {
  id: string;
  /** The node type (``signal``, ``organization``, ``insight:regulatory_change`` ...). */
  label: string;
  properties: Record<string, unknown>;
  /** Number of 1-hop neighbours not present in the current canvas. Drives the "+N" affordance. */
  hiddenNeighbourCount?: number;
  /** Full degree from the API; drives node sizing. */
  degree?: number;
  /** One-node view paging (on the focus node): total neighbours, this page's offset, page size. */
  neighbourTotal?: number;
  neighbourOffset?: number;
  neighbourPageSize?: number;
}
export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  properties: Record<string, unknown>;
}

/** Which render mode the canvas is in: drives the level-of-detail label rule. */
export type GraphMode = 'overview' | 'scoped';

const NODE_COLORS: Record<string, string> = {
  // entities
  organization: '#8c564b',
  person: '#e377c2',
  product: '#ff7f0e',
  topic: '#9467bd',
  segment: '#7f7f7f',
  // evidence
  fact: '#4e79a7',
  opinion: '#bcbd22',
  event: '#17becf',
  adoption: '#59a14f',
  sentiment: '#76b7b2',
  // outputs
  signal: '#e6550d',
  trend: '#d62728',
  gap: '#9e2a2b',
  content_idea: '#3182bd',
  content_draft: '#6baed6',
  insight: '#c2410c',
};
const PALETTE = ['#1b9e77', '#d95f02', '#7570b3', '#e7298a', '#66a61e', '#e6ab02', '#a6761d', '#1f78b4'];

/** A node type's colour: the built-in one, else a stable colour from the type's name. */
export function nodeColor(label: string): string {
  const known = NODE_COLORS[label];
  if (known) return known;
  let h = 0;
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length] as string;
}

const SELECTED_STROKE = '#ec7211';

/** Evidence types: hidden from the overview by default (the legend turns them on). */
export const EVIDENCE_LABELS = new Set<string>(['fact', 'opinion', 'event', 'adoption', 'sentiment']);

/** The legend and label name of a node type (custom kinds come as ``insight:<kind>``). */
export function legendDisplayName(label: string, names: Record<string, string> = {}): string {
  return names[label] ?? typeLabel(label.startsWith('insight:') ? label.slice(8) : label);
}

/** Plain text from a short HTML or Markdown-ish body (for tooltips and screen-reader names). */
export function plainText(raw: string): string {
  return raw.replace(/<[^>]*>/g, ' ').replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim();
}

/** The first value that is a string with text in it: "" or whitespace falls through to the next. */
export function firstText(...values: unknown[]): string | undefined {
  return values.find((v): v is string => typeof v === 'string' && v.trim() !== '');
}

/**
 * Edge-aware hover-tooltip placement: next to the cursor, flipped near the right/bottom edges so it
 * never covers the hovered node (which would block the click). Clamped to stay on-screen.
 */
export function tooltipPlacement(
  px: number, py: number, cw: number, ch: number,
  tw = 280, th = 140, gap = 16,
): { left: number; top: number } {
  const flipX = px + gap + tw > cw;
  const flipY = py + gap + th > ch;
  const left = flipX ? Math.max(4, px - gap - tw) : px + gap;
  const top = flipY ? Math.max(4, py - gap - th) : py + gap;
  return { left, top };
}

// Level of detail on the overview, revealed as the user zooms in: hub labels at any zoom, then the
// "+N" badge, then the other node labels, then edge labels. The one-node view always shows labels.
const ZOOM_BADGE = 1.6;
const ZOOM_LABEL = 2.6;
const ZOOM_EDGE_LABEL = 3.6;

function nodeTitle(n: GraphNode): string {
  const t = n.properties.title;
  return typeof t === 'string' && t.trim() ? t : n.id;
}

function nodeAccessibleName(n: GraphNode, names: Record<string, string> = {}): string {
  const body = n.properties.summary;
  const kind = legendDisplayName(n.label, names);
  if (typeof body !== 'string' || !body.trim()) return `${kind}: ${nodeTitle(n)}`;
  const clean = plainText(body);
  return `${kind}: ${nodeTitle(n)}. ${clean.length > 100 ? `${clean.slice(0, 99)}\u2026` : clean}`;
}

// Degree -> radius by mode: an even cloud of small dots on the overview, big labelled discs in the
// one-node view.
function radiusForDegree(d: number, scoped = false): number {
  if (scoped) return Math.max(14, Math.min(42, 14 + 6 * Math.sqrt(d)));
  return Math.max(3, Math.min(11, 3 + 1.4 * Math.sqrt(d)));
}

// Edges are coloured by relationship; a dashed stroke marks the counter relationships
// (contradicts, competes with) so colour alone does not carry that distinction.
interface EdgeFamily { key: string; name: string; color: string; dash: number[] | null; width: number }
const EDGE_FAMILIES: EdgeFamily[] = [
  { key: 'supports', name: 'Supports', color: '#2ca02c', dash: null, width: 1.1 },
  { key: 'contradicts', name: 'Contradicts', color: '#d62728', dash: [5, 4], width: 1.1 },
  { key: 'about', name: 'About', color: '#e6550d', dash: null, width: 1.0 },
  { key: 'addresses', name: 'Addresses', color: '#7570b3', dash: null, width: 1.0 },
  { key: 'reported_by', name: 'Reported by', color: '#4e79a7', dash: null, width: 0.9 },
  { key: 'held_by', name: 'Held by', color: '#bcbd22', dash: null, width: 0.9 },
  { key: 'adopted_by', name: 'Adopted by', color: '#59a14f', dash: null, width: 0.9 },
  { key: 'offers', name: 'Offers', color: '#f28e2b', dash: null, width: 1.0 },
  { key: 'competes_with', name: 'Competes with', color: '#d37295', dash: [5, 4], width: 1.0 },
  { key: 'partners_with', name: 'Partners with', color: '#ffbe7d', dash: null, width: 1.0 },
  { key: 'uses', name: 'Uses', color: '#499894', dash: null, width: 1.0 },
  { key: 'targets', name: 'Targets', color: '#9467bd', dash: null, width: 1.0 },
  { key: 'part_of', name: 'Part of', color: '#8c564b', dash: null, width: 0.9 },
  { key: 'affiliated_with', name: 'Affiliated with', color: '#e377c2', dash: null, width: 0.9 },
  { key: 'has_draft', name: 'Has draft', color: '#6baed6', dash: null, width: 0.9 },
  { key: 'related', name: 'Related to', color: '#bbbbbb', dash: null, width: 0.7 },
];
const EDGE_FAMILY_BY_KEY: Record<string, EdgeFamily> = Object.fromEntries(EDGE_FAMILIES.map((f) => [f.key, f]));
export function edgeFamilyKey(label: string): string {
  const L = (label || '').toLowerCase();
  return L in EDGE_FAMILY_BY_KEY ? L : 'related';
}
function edgeStyle(label: string): EdgeFamily {
  return EDGE_FAMILY_BY_KEY[edgeFamilyKey(label)] as EdgeFamily;
}
/** The canvas colour for an edge's relationship, shared with the edge-detail panel's swatch. */
export function edgeFamilyColor(label: string): string {
  return edgeStyle(label).color;
}

/** An edge's relationship as words for its midpoint label: ``competes_with`` -> "competes with". */
export function humanizeEdge(label: string): string {
  return (label || '').replace(/_/g, ' ').toLowerCase().trim();
}

// react-force-graph node/link runtime shapes (it mutates these with x/y/source/target).
interface FgNode {
  id: string;
  label: string;
  title: string;
  data: GraphNode;
  deg: number;
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
}
// FE-3 - the slice of d3's link force we reconfigure (the library ships loose
// types via react-force-graph's `d3Force`, so we narrow at the use site instead
// of casting to `any`). Both methods are chainable (return the force).
interface LinkForce {
  distance(fn: (l: { source: FgNode; target: FgNode }) => number): LinkForce;
  strength(s: number): LinkForce;
}
interface FgLink {
  id: string;
  label: string;
  source: string | FgNode;
  target: string | FgNode;
  data: GraphEdge;
}

const DOUBLE_CLICK_MS = 250;

/** A link end's title: react-force-graph swaps the id for the node object once the layout starts. */
function titleOf(end: string | FgNode, byId: Map<string, FgNode>): string {
  // Only links between drawn nodes are kept, so an id always finds its node.
  return typeof end === 'string' ? (byId.get(end) as FgNode).title : end.title;
}

/** Dark canvas when the page is in Cloudscape dark mode or the system prefers dark. */
function detectDark(): boolean {
  if (document.body.classList.contains('awsui-dark-mode')) return true;
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function GraphView({
  nodes: nodesRaw,
  edges,
  height = 600,
  mode = 'overview',
  focusId,
  selectedId: selectedIdProp,
  onNodeClick,
  onNodeDoubleClick,
  onEdgeClick,
  names = {},
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  height?: number;
  /** Render mode -> label level-of-detail: overview labels the hubs, scoped is one node and its neighbours. */
  mode?: GraphMode;
  /** Scoped-view focus node id - when it changes, recenter + zoom-to-fit on it (Quick-style). */
  focusId?: string | null;
  /** Parent-controlled selected node id. Falls back to internal state when omitted. */
  selectedId?: string | null;
  /** Single-click -> open the detail pane (NO 1-hop highlight). */
  onNodeClick?: (node: GraphNode) => void;
  /** Double-click -> enter the scoped/ego view centred on this node. */
  onNodeDoubleClick?: (node: GraphNode) => void;
  /** Click an edge -> open the edge detail pane. */
  onEdgeClick?: (edge: GraphEdge) => void;
  /** The deployment's own names for its node types (custom insight kinds), for labels and the legend. */
  names?: Record<string, string>;
}): React.ReactElement {
  // H6-044 - the KG canvas + legend hardcoded light colors, rendering as a bright
  // white island in dark mode. Detect the active theme (provider-independent:
  // Cloudscape's applyMode sets an `awsui-dark-mode` class on the document; fall
  // back to the OS preference) so the canvas bg + node label fill flip. Re-checks
  // on OS-scheme change so `auto` mode tracks it live.
  const [dark, setDark] = React.useState(detectDark);
  React.useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setDark(detectDark());
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  const canvasBg = dark ? '#161d26' : '#fafafa';
  const labelColor = dark ? '#e9ebed' : '#1a1a1a';
  const fgRef = React.useRef<{
    d3ReheatSimulation?: () => void;
    zoomToFit?: (ms?: number, px?: number) => void;
    centerAt?: (x?: number, y?: number, ms?: number) => void;
    zoom?: () => number;
    // react-force-graph's force accessor: get/set a named d3 force. The library
    // ships loose types; we narrow the read result to the small slice we use
    // (the link force's distance/strength setters) via LinkForce below.
    d3Force?: (name: string, force?: unknown) => unknown;
  } | null>(null);
  // sec. 55.15 (#1, scoped LOD) - the globalScale the graph fits to after zoomToFit.
  // Scoped/ego graphs (few nodes) fit at a MUCH higher zoom than the dense bird
  // view, so the bird-view absolute thresholds (1.6/2.6/3.6) can't be reused for
  // scoped; instead scoped LOD reveals relative to THIS fitted base (labelMore at
  // ~1.4x, edge labels at ~2x). Captured on each fit; 0 until the first fit.
  const baseScaleRef = React.useRef<number>(0);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  // True once the layout has settled. After this, paintNode pins each live node
  // it draws (fx/fy) so dragging one never ripples to the rest - pinning the
  // EXACT objects force-graph renders avoids the stale-array problem of pinning
  // our memo's copy. Driven by a PAINT-FRAME counter (not timers/onEngineStop,
  // which proved unreliable): force-graph paints once per simulation tick during
  // cooldown, so after ~cooldownTicks frames the layout is done. Reset on data
  // change via the effect below.
  const settledRef = React.useRef(false);
  const paintFrames = React.useRef(0);
  const [width, setWidth] = React.useState(800);
  const [internalSelectedId, setInternalSelectedId] = React.useState<string | null>(null);
  const [hoverId, setHoverId] = React.useState<string | null>(null);
  // Cursor position (container-relative) so the hover tooltip can be placed next
  // to the pointer with edge-aware flipping - never on top of the hovered node.
  const [hoverPos, setHoverPos] = React.useState<{ x: number; y: number } | null>(null);
  const [hoverEdgeId, setHoverEdgeId] = React.useState<string | null>(null);
  const [hidden, setHidden] = React.useState<Set<string>>(new Set()); // legend type-toggles
  const selectedId = selectedIdProp !== undefined ? selectedIdProp : internalSelectedId;
  // Scoped/ego view is the small, fully-labelled "big discs + edge labels" mode
  // (Quick h4/h5). Bird views (overview/radar) are the small-dot cloud (h1).
  const isScoped = mode === 'scoped';

  // Measure the container so the canvas fills it.
  React.useEffect(() => {
    const el = containerRef.current as HTMLDivElement;
    const update = () => setWidth(el.clientWidth || 800);
    update();
    // An older browser (and jsdom) has no ResizeObserver: follow the window instead.
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update);
      return () => window.removeEventListener('resize', update);
    }
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Browser zoom changes `devicePixelRatio` AFTER mount, and force-graph only
  // re-sizes its canvases from `adjustCanvasSize` (a width/height prop change),
  // baking the context pixel scale in on init. So a zoom leaves the HIDDEN
  // hit-test ("shadow") canvas painted at the old ratio while the pointer is
  // sampled at the new one: hover/click then only land near a node's centre
  // instead of anywhere on the disc (QA 2026-10-06 #3 - measured on dev:
  // every offset 0->0.9r missed after a runtime DPR change, all hit on reload).
  // A width nudge (re-running adjustCanvasSize) re-sizes the bitmaps but did NOT
  // restore hit-testing (verified on dev: canvas at 1.5 px/CSS px, hover still
  // missed everywhere), while a fresh mount at the new ratio always works. So
  // REMOUNT the canvas (keyed on the ratio) when it changes. Node positions live
  // on the node objects and survive; only the zoom/pan framing resets (the
  // auto-fit re-frames it).
  const [dpr, setDpr] = React.useState(() => window.devicePixelRatio);
  React.useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(`(resolution: ${dpr}dppx)`);
    const onChange = () => setDpr(window.devicePixelRatio);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [dpr]);

  // Quick-style auto-fit: after the layout settles, frame the WHOLE graph so the
  // user never lands zoomed into one hub (the "neuron" close-up). Runs on every
  // mode / focus / data-size change. Scoped ego graphs are small so they fit
  // tight; the dense all-radars view zooms out to show every radar at once.
  // (Keyed below on `fitKey` once nodeCount is known.)

  const nodes = nodesRaw;

  // gap-1 fix - RENDERED degree, computed client-side from the visible edges, so
  // node SIZE + label level-of-detail work on EVERY backend path (the
  // personalised + single-radar explore paths don't annotate `degree`; only the
  // all-radars overview + scoped paths do). Falls back to the backend's full
  // `degree` when richer.
  const degreeById = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const e of edges) {
      m.set(e.from, (m.get(e.from) ?? 0) + 1);
      m.set(e.to, (m.get(e.to) ?? 0) + 1);
    }
    return m;
  }, [edges]);
  const effDegree = React.useCallback(
    (n: GraphNode) => Math.max(n.degree ?? 0, degreeById.get(n.id) ?? 0),
    [degreeById],
  );

  const presentLabels = React.useMemo(() => {
    const seen = new Set<string>();
    for (const n of nodes) seen.add(n.label);
    return Array.from(seen).sort();
  }, [nodes]);

  // Apply legend type-toggles -> visible node set + the edges between them.
  const graphData = React.useMemo(() => {
    // HU-029 - the scope's centre node is always drawn: hiding its type left a
    // cloud of disconnected neighbours under a banner still naming the focus.
    const visible = nodes.filter((n) => !hidden.has(n.label) || n.id === focusId);
    const visIds = new Set(visible.map((n) => n.id));
    const fgNodes: FgNode[] = visible.map((n) => ({
      id: n.id, label: n.label, title: nodeTitle(n), data: n, deg: effDegree(n),
    }));
    const fgLinks: FgLink[] = edges
      .filter((e) => visIds.has(e.from) && visIds.has(e.to))
      .map((e) => ({ id: e.id, label: e.label, source: e.from, target: e.to, data: e }));
    return { nodes: fgNodes, links: fgLinks };
  }, [nodes, edges, hidden, effDegree, focusId]);

  // sec. 55.15 - single-click FOCUS set: the selected node + its 1-hop neighbours,
  // and the edge ids that connect them. When a node is selected, everything NOT
  // in this set is blurred (dimmed + label hidden) so the user sees just that
  // node's neighbourhood. Null when nothing is selected (full graph, no blur).
  const focus = React.useMemo(() => {
    if (!selectedId) return null;
    const nodeIds = new Set<string>([selectedId]);
    const edgeIds = new Set<string>();
    for (const e of edges) {
      if (e.from === selectedId) { nodeIds.add(e.to); edgeIds.add(e.id); }
      else if (e.to === selectedId) { nodeIds.add(e.from); edgeIds.add(e.id); }
    }
    return { nodeIds, edgeIds };
  }, [selectedId, edges]);

  // Which edge families are actually present -> the edge legend only lists those.
  const presentEdgeFamilies = React.useMemo(() => {
    const keys = new Set<string>();
    for (const e of edges) keys.add(edgeFamilyKey(e.label));
    return EDGE_FAMILIES.filter((f) => keys.has(f.key));
  }, [edges]);

  // -- Layout forces (the "neuron / sea-urchin" fix) --------------------------
  // The default d3-force config (charge -30, no collision) collapses each
  // radar's blips into a tight radial burst on top of the hub, because
  // radar->blip is a pure STAR topology. Quick Desktop spreads its ~400 nodes
  // evenly because it runs (a) a COLLISION force sized to each node so leaves
  // can't overlap, and (b) strong charge repulsion + a generous link distance.
  // We reconfigure the simulation through react-force-graph's d3Force() ref
  // whenever the rendered node set changes, then reheat so it re-settles.
  const nodeCount = graphData.nodes.length;
  React.useEffect(() => {
    const fg = fgRef.current;
    if (!fg?.d3Force) return;
    if (isScoped) {
      // Scoped/ego (Quick h4/h5): few BIG discs. Strong repulsion + roomy links so
      // the large labelled circles + edge labels don't overlap. Extra collision
      // padding reserves room for the label drawn BELOW each disc.
      fg.d3Force('charge', forceManyBody().strength(-1400).distanceMax(2000));
      fg.d3Force('collide', forceCollide<FgNode>((n) => radiusForDegree(n.deg, true) + 26).strength(1).iterations(4));
      const link = fg.d3Force('link') as LinkForce | undefined;
      if (link) {
        link.distance((l: { source: FgNode; target: FgNode }) => {
          const a = typeof l.source === 'object' ? radiusForDegree(l.source.deg, true) : 16;
          const b = typeof l.target === 'object' ? radiusForDegree(l.target.deg, true) : 16;
          return 110 + a + b;
        }).strength(0.35);
      }
      fg.d3Force('x', forceX(0).strength(0.05));
      fg.d3Force('y', forceY(0).strength(0.05));
      fg.d3ReheatSimulation?.();
      return;
    }
    // Bird view - aim for Quick h1: ONE even cloud of small dots, not wide-fanned
    // sunbursts. Moderate charge (enough to avoid clumping, not so much it blows
    // each radar into a giant ring), SHORT links (compact dot-clusters), and
    // collision = radius + small pad (just prevents overlap). Together these pack
    // the graph into an evenly-spaced mesh-like blob.
    const charge = forceManyBody()
      .strength(nodeCount > 120 ? -55 : nodeCount > 40 ? -70 : -90)
      .distanceMax(420);
    fg.d3Force('charge', charge);
    fg.d3Force(
      'collide',
      forceCollide<FgNode>((n) => radiusForDegree(n.deg) + 2.5).strength(1).iterations(2),
    );
    // Short hub->leaf links -> a radar's blips sit in a tight halo close to the hub
    // (a dense dot-cluster), so the whole org reads as one cloud, not N suns.
    const link = fg.d3Force('link') as LinkForce | undefined;
    if (link) {
      link.distance((l: { source: FgNode; target: FgNode }) => {
        const a = typeof l.source === 'object' ? radiusForDegree(l.source.deg) : 4;
        const b = typeof l.target === 'object' ? radiusForDegree(l.target.deg) : 4;
        return 18 + a + b;
      }).strength(0.5);
    }
    // Centering pull so the separate radars gather into one shared cloud rather
    // than drifting into isolated islands.
    fg.d3Force('x', forceX(0).strength(0.06));
    fg.d3Force('y', forceY(0).strength(0.06));
    fg.d3ReheatSimulation?.();
  }, [nodeCount, isScoped]);

  // Quick-style auto-fit (see note above): frame the whole graph once the layout
  // has settled, on every data/mode/focus change, so the user never lands zoomed
  // into a single hub. Longer settle wait for bigger graphs (more warmup ticks).
  // Also PIN every node (fx/fy) at the same moment: after this, dragging a node
  // moves ONLY that node - the rest are fixed so no force ripples through them
  // (Quick behaviour). force-graph mutates these exact node objects in place, so
  // setting fx/fy here sticks. We don't `onEngineStop`-pin because that closure
  // can capture a stale graphData; `graphData.nodes` here is always current.
  React.useEffect(() => {
    settledRef.current = false; paintFrames.current = 0; // new layout - re-settle
    baseScaleRef.current = 0; // invalidate until this layout re-fits
    const wait = nodeCount > 120 ? 900 : nodeCount > 40 ? 650 : 450;
    const fit = setTimeout(() => { fgRef.current?.zoomToFit?.(500, 70); }, wait);
    // Backstop: if onEngineStop is slow to fire, mark settled anyway so paintNode
    // starts pinning (the layout is visually done by `wait`).
    const settle = setTimeout(() => { settledRef.current = true; }, wait + 200);
    // Capture the fitted globalScale AFTER the fit animation completes - the
    // baseline for scoped-mode relative LOD thresholds (#1).
    const capture = setTimeout(() => {
      const z = fgRef.current?.zoom?.();
      if (typeof z === 'number' && z > 0) baseScaleRef.current = z;
    }, wait + 600);
    return () => { clearTimeout(fit); clearTimeout(settle); clearTimeout(capture); };
  }, [nodeCount, mode, focusId, dpr]);

  // gap-1 fix / sec. 55.15 (#1) - level-of-detail LABEL set by mode. This is the set
  // always labelled AT REST (default fitted zoom); more reveal as you zoom in
  // (paintNode's labelZoom). The scoped view used to label ALL 24 nodes at once
  // regardless of zoom - convoluted once ego graphs got dense. Now scoped labels
  // only its top few hubs at rest (like the overview), the rest on zoom-in:
  //   overview -> top 10 by degree (the hubs)
  //   scoped   -> top 8 by degree (the biggest discs); more on zoom-in
  // (hovered + selected are always labelled, handled in paintNode.)
  const labelSet = React.useMemo(() => {
    const sorted = [...nodes].sort((a, b) => effDegree(b) - effDegree(a));
    const k = mode === 'scoped' ? Math.min(sorted.length, 8) : Math.min(sorted.length, 10);
    return new Set(sorted.slice(0, k).map((n) => n.id));
  }, [nodes, mode, effDegree]);

  // gap-3/gap-4 fix - click vs double-click disambiguation. react-force-graph
  // has NO onNodeDoubleClick, and a single mouse double-click fires onNodeClick
  // TWICE. We defer the single-click (select -> pane) by DOUBLE_CLICK_MS; a second
  // click on the same node within that window cancels it and fires the
  // double-click (-> scoped/ego view) instead.
  const clickTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastClickId = React.useRef<string | null>(null);
  React.useEffect(() => () => { if (clickTimer.current) clearTimeout(clickTimer.current); }, []);

  const selectNode = React.useCallback((n: FgNode) => {
    setInternalSelectedId(n.id);
    onNodeClick?.(n.data);
  }, [onNodeClick]);
  const scopeNode = React.useCallback((n: FgNode) => {
    onNodeDoubleClick?.(n.data);
  }, [onNodeDoubleClick]);

  const handleCanvasNodeClick = React.useCallback((n: FgNode) => {
    if (clickTimer.current && lastClickId.current === n.id) {
      clearTimeout(clickTimer.current);
      clickTimer.current = null;
      lastClickId.current = null;
      scopeNode(n);
      return;
    }
    if (clickTimer.current) clearTimeout(clickTimer.current);
    lastClickId.current = n.id;
    clickTimer.current = setTimeout(() => {
      clickTimer.current = null;
      lastClickId.current = null;
      selectNode(n);
    }, DOUBLE_CLICK_MS);
  }, [selectNode, scopeNode]);

  const selectEdge = React.useCallback((l: FgLink) => {
    onEdgeClick?.(l.data);
  }, [onEdgeClick]);

  // Custom node paint. Two looks (matching Quick's two modes):
  //   bird view  -> small dot, hairline stroke, LOD label to the side (only hubs)
  //   scoped/ego -> BIG disc, every node labelled centred BELOW it (Quick h4/h5)
  // NO focus dimming: a single click selects (ring) without hiding the rest.
  const paintNode = React.useCallback((node: FgNode, ctx: CanvasRenderingContext2D, scale: number) => {
    // PIN each live node force-graph renders to its current spot (fx/fy) ONCE the
    // layout has settled, so dragging one node never disturbs the others (Quick
    // behaviour). `settledRef` flips true on engine-stop (or the backstop timer);
    // pinning force-graph's OWN node object (passed here) is the only reliable
    // handle - the input graphData array is copied internally. The dragged node
    // already has fx/fy from force-graph during the drag, so this is a no-op for it.
    if (settledRef.current && !Number.isFinite(node.fx) && Number.isFinite(node.x)) {
      node.fx = node.x; node.fy = node.y;
    }
    const r = radiusForDegree(node.deg, isScoped);
    const isSel = node.id === selectedId;
    const isHover = node.id === hoverId;
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const col = nodeColor(node.label);
    // sec. 55.15 - single-click focus blur (#9): when a node is selected, everything
    // outside its 1-hop neighbourhood dims and loses its label, so the user sees
    // just that node's connections. The selected node + neighbours stay full.
    const inFocus = !focus || focus.nodeIds.has(node.id);
    const dimmed = !!focus && !inFocus;
    ctx.globalAlpha = dimmed ? 0.12 : 1;
    // sec. 55.15 - bird-view detail reveals progressively on zoom so the dot-cloud
    // never crowds (owner-specified hierarchy): top-degree hubs are labelled at
    // ANY zoom; the small "+N" badge reveals at ZOOM_BADGE; MORE node labels
    // reveal at ZOOM_LABEL; edge labels reveal last at ZOOM_EDGE_LABEL (handled
    // in paintLink). Each threshold strictly > the previous. Scoped view always
    // shows labels + edge labels regardless of zoom.
    // sec. 55.15 (#1) - LOD thresholds are mode-aware. Bird view uses absolute
    // globalScale steps. Scoped/ego graphs fit at a much higher globalScale, so
    // their steps are RELATIVE to the fitted base (baseScaleRef): reveal more
    // labels at ~1.4x the fit, edge labels at ~2x. Until the base is captured
    // (baseScaleRef 0, briefly on first paint) scoped shows only the top-N set.
    const base = baseScaleRef.current;
    const badgeZoom = isScoped
      ? (base > 0 && scale > base * 1.2)
      : scale > ZOOM_BADGE;
    const labelZoom = isScoped
      ? (base > 0 && scale > base * 1.4)
      : scale > ZOOM_LABEL;

    // sec. 55.15 - fading "there's more this way" stub-edges (scoped view only): a
    // node that still has connections leading OFF the visible 1-hop set gets a
    // few short radial lines that fade out, hinting you can re-scope to it
    // without cluttering the canvas with unreachable nodes. Drawn FIRST so they
    // sit under the disc. Direction = radially outward from the layout centre
    // (0,0), spread into a small fan; count scales with hidden degree.
    const hidden = node.data.hiddenNeighbourCount ?? 0;
    if (isScoped && hidden > 0) {
      const baseAng = Math.atan2(y, x); // outward from centre
      const n = Math.min(5, 1 + Math.floor(hidden / 2));
      const len = (r + 26) ;
      for (let i = 0; i < n; i++) {
        const ang = baseAng + (i - (n - 1) / 2) * 0.32;
        const x0 = x + Math.cos(ang) * r;
        const y0 = y + Math.sin(ang) * r;
        const x1 = x + Math.cos(ang) * (r + len);
        const y1 = y + Math.sin(ang) * (r + len);
        const grad = ctx.createLinearGradient(x0, y0, x1, y1);
        grad.addColorStop(0, 'rgba(150,150,150,0.5)');
        grad.addColorStop(1, 'rgba(150,150,150,0)');
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    }

    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fillStyle = col;
    ctx.fill();
    // Hairline stroke on small dots (a thick ring on a 3px dot reads as a blob);
    // selection/hover get a bold orange/navy ring so they still stand out.
    if (isSel || isHover) {
      ctx.lineWidth = isSel ? 2.5 : 2;
      ctx.strokeStyle = isSel ? SELECTED_STROKE : '#232f3e';
      ctx.stroke();
    } else {
      ctx.lineWidth = isScoped ? 1 : 0.5;
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.stroke();
    }
    // sec. 55.15 - hover affordance: a dashed orange halo ring around the hovered
    // node signals it's interactive (double-click to explore). Quick-style cue so
    // the user discovers double-click without reading the tooltip.
    if (isHover) {
      ctx.beginPath();
      ctx.arc(x, y, r + (isScoped ? 7 : 4), 0, 2 * Math.PI);
      ctx.setLineDash([3, 2.5]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = SELECTED_STROKE;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // "+N" hidden-neighbour badge (top-right) - SMALL (#5) so it doesn't rival
    // the node itself, and shown only once zoomed in (or hover/select), in both
    // modes, so the default bird view stays a clean dot-cloud. Scoped discs are
    // big enough that it always reads. Hidden on dimmed (out-of-focus) nodes.
    const hiddenN = node.data.hiddenNeighbourCount ?? 0;
    if (hiddenN > 0 && !dimmed && (isScoped || badgeZoom || isHover || isSel)) {
      // Badge radius scales with the node but stays small relative to it.
      const br = Math.max(isScoped ? 6 : 3.2, Math.min(isScoped ? 9 : 5.5, r * 0.45));
      const bx = x + r * 0.78, by = y - r * 0.78;
      ctx.beginPath();
      ctx.arc(bx, by, br, 0, 2 * Math.PI);
      ctx.fillStyle = '#232f3e'; ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = `700 ${(br * 1.25).toFixed(1)}px system-ui`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(hiddenN > 99 ? '99+' : `+${hiddenN}`, bx, by);
    }
    // Labels. Scoped: label EVERY node, centred below the disc (Quick h4/h5).
    // Bird: top-degree hubs are always labelled; the REST reveal their label only
    // once zoomed in FURTHER than the badge threshold (#3 - labels later than the
    // "+N" badge, so dots don't crowd with text too early). Hover/select always
    // labels. Out-of-focus (dimmed) nodes never show a label (#9).
    // sec. 55.15 (#1) - BOTH modes now gate non-hub labels on zoom (was `isScoped ||`,
    // which force-labelled every scoped node at any zoom -> the convoluted view).
    // Selected/hovered/top-N are always labelled; the rest reveal past labelZoom.
    const showLabel = !dimmed && (isSel || isHover || labelSet.has(node.id) || labelZoom);
    if (showLabel) {
      const t = node.title.length > 36 ? node.title.slice(0, 36) + '\u2026' : node.title;
      ctx.fillStyle = labelColor; // H6-044 - theme-aware label fill
      if (isScoped) {
        ctx.font = `${(isSel ? 13 : 12) / scale}px system-ui`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillText(t, x, y + r + 3 / scale);
      } else {
        // graph-space font / scale keeps a stable on-screen size while zooming.
        ctx.font = `${(isSel || isHover ? 12 : 11) / scale}px system-ui`;
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.fillText(t, x + r + 2, y);
      }
    }
    ctx.globalAlpha = 1; // reset for the next node force-graph paints
  }, [selectedId, hoverId, labelSet, isScoped, focus, labelColor]);

  // Pointer HIT-AREA paint. react-force-graph derives the default hover/click
  // target from `nodeVal` (~ sqrt val-nodeRelSize), but we draw the VISIBLE disc with
  // a custom radiusForDegree (up to ~42px in scoped mode) - so the two decouple
  // and the hit-circle ends up far smaller than the disc, forcing the user to aim
  // at the centre. Repaint the hit area to match the visible disc + a small pad
  // so hover/click feel forgiving. The pad is larger in scoped mode (big discs,
  // few nodes -> no ambiguity) and tiny in bird view (dense dot-cloud -> keep
  // targets from overlapping). `color` is the library-provided unique hit colour.
  const paintNodePointerArea = React.useCallback((node: FgNode, color: string, ctx: CanvasRenderingContext2D) => {
    const r = radiusForDegree(node.deg, isScoped) + (isScoped ? 8 : 2);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI);
    ctx.fill();
  }, [isScoped]);

  // Link colour = per-type palette; brighten the hovered edge; fade edges that
  // aren't part of the focused node's neighbourhood (#9 single-click blur).
  const linkColor = React.useCallback((link: FgLink) => {
    if (link.id === hoverEdgeId) return '#232f3e';
    const fam = edgeStyle(link.label);
    if (focus && !focus.edgeIds.has(link.id)) {
      // dim out-of-focus edges to a faint grey
      return 'rgba(180,180,180,0.12)';
    }
    return fam.color;
  }, [hoverEdgeId, focus]);

  // Scoped/ego view draws the relationship name on each edge, ROTATED to follow
  // the edge angle (#1) - flipped 180 deg when it would otherwise read upside-down
  // (Quick h4/h5 / spec sec. 55.15 item 20.3). Painted as an extra layer ('after')
  // over the link line, with a background-coloured halo so it stays readable
  // crossing other edges. In BIRD view edge labels are off by default (too
  // dense) but reveal once the user zooms past ZOOM_EDGE_LABEL - the last tier
  // of the progressive bird-view LOD (#1), after node labels.
  const paintLink = React.useCallback((link: FgLink, ctx: CanvasRenderingContext2D, scale: number) => {
    // sec. 55.15 (#1) - edge labels are the LAST LOD tier in BOTH modes now (was
    // always-on in scoped, which crowded a dense ego graph). Bird view: past the
    // absolute ZOOM_EDGE_LABEL. Scoped: past ~2x the fitted base zoom (relative,
    // since ego graphs fit at a higher globalScale). Until base is captured,
    // scoped suppresses edge labels (shown only when clearly zoomed in).
    if (isScoped) {
      const base = baseScaleRef.current;
      if (!(base > 0 && scale > base * 2)) return;
    } else if (scale <= ZOOM_EDGE_LABEL) {
      return; // bird view: only when zoomed in far
    }
    if (focus && !focus.edgeIds.has(link.id)) return; // out-of-focus -> no label
    const s = link.source as FgNode;
    const t = link.target as FgNode;
    if (typeof s !== 'object' || typeof t !== 'object') return;
    const sx = s.x ?? 0, sy = s.y ?? 0, tx = t.x ?? 0, ty = t.y ?? 0;
    const label = humanizeEdge(link.label);
    if (!label) return;
    const fam = edgeStyle(link.label);
    const mx = (sx + tx) / 2;
    const my = (sy + ty) / 2;
    let ang = Math.atan2(ty - sy, tx - sx);
    // Keep text upright: if the edge points "leftish", flip 180 deg so it's not upside-down.
    if (ang > Math.PI / 2 || ang < -Math.PI / 2) ang += Math.PI;
    // Near-vertical edges -> rotated text would read bottom-to-top (hard to read).
    // Once the edge is steeper than ~55 deg from horizontal, draw the label
    // HORIZONTALLY instead (a vertical edge has plenty of horizontal room beside
    // it). Shallower/moderate edges keep following the line (Quick h4/h5 look).
    const STEEP = (55 * Math.PI) / 180;
    if (Math.abs(ang) > STEEP) ang = 0;
    ctx.save();
    ctx.translate(mx, my);
    ctx.rotate(ang);
    ctx.font = `${10 / scale}px system-ui`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // sec. 55.15 (#4) - sit the label RIGHT ON the edge line (yOff 0) so in a crowded
    // scope it's unambiguous which line a label belongs to. The white halo
    // (strokeText, drawn first) masks the coloured line behind the glyphs so the
    // line doesn't strike through the text, and the fill uses the edge FAMILY
    // colour so label and line read as one unit. Hovered edge -> bold navy.
    const yOff = 0;
    ctx.lineWidth = 3.5 / scale;
    ctx.strokeStyle = 'rgba(250,250,250,0.95)';
    ctx.strokeText(label, 0, yOff);
    ctx.fillStyle = link.id === hoverEdgeId ? '#232f3e' : fam.color;
    ctx.fillText(label, 0, yOff);
    ctx.restore();
  }, [isScoped, hoverEdgeId, focus]);

  const nodeById = new Map(graphData.nodes.map((n) => [n.id, n]));
  return (
    <div
      data-testid="graph-view"
      ref={containerRef}
      style={{ position: 'relative' }}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        setHoverPos({ x: e.clientX - r.left, y: e.clientY - r.top });
      }}
      onMouseLeave={() => setHoverPos(null)}
    >
      <div style={{ width: '100%', height, background: canvasBg, border: '1px solid var(--color-border-divider-default, #ddd)', borderRadius: 6, overflow: 'hidden' }}>
        <ForceGraph2D
          key={`fg-dpr-${dpr}`}
          ref={fgRef as never}
          width={width}
          height={height}
          graphData={graphData}
          nodeId="id"
          nodeLabel={() => ''}
          nodeVal={(n: FgNode) => Math.max(1, n.deg)}
          nodeColor={(n: FgNode) => nodeColor(n.label)}
          nodeCanvasObject={paintNode as never}
          nodeCanvasObjectMode={() => 'replace'}
          // Hit-area matches the visible disc (+pad) so hover/click isn't pinned
          // to the node centre - esp. the big scoped-view discs.
          nodePointerAreaPaint={paintNodePointerArea as never}
          linkColor={linkColor as never}
          linkWidth={(l: FgLink) => {
            const base = edgeStyle(l.label).width;
            // Scoped edges are a touch bolder (few of them, big discs); hover pops.
            const w = isScoped ? base + 0.8 : base;
            return l.id === hoverEdgeId ? w + 1.2 : w;
          }}
          linkLineDash={(l: FgLink) => edgeStyle(l.label).dash}
          linkLabel={() => ''}
          linkCanvasObject={paintLink as never}
          linkCanvasObjectMode={() => 'after'}
          linkCurvature={0.12}
          // Directional arrows in BOTH modes (#2) - bird view arrows are smaller
          // so they don't clutter the dot-cloud; scoped arrows are prominent.
          linkDirectionalArrowLength={isScoped ? 6 : 3.5}
          // Place the arrow tip just OUTSIDE the target disc, not at a fixed 0.96
          // of the centre-to-centre line. In the scoped/ego view most edges point
          // INTO the big focus disc (r up to ~42px); a fixed relPos buried the
          // arrowhead under that disc (painted after the link layer) - so opinion
          // edges (OPINED_ABOUT / HAS_COLLECTIVE_OPINION_IN, all pointing at the
          // focus) appeared arrow-less. Compute relPos from the live segment length
          // so the tip clears the target radius (+ arrow length + a small gap).
          linkDirectionalArrowRelPos={((l: FgLink) => {
            const s = l.source as FgNode;
            const t = l.target as FgNode;
            if (typeof s !== 'object' || typeof t !== 'object') return 0.96;
            const dx = (t.x ?? 0) - (s.x ?? 0);
            const dy = (t.y ?? 0) - (s.y ?? 0);
            const len = Math.hypot(dx, dy);
            if (len < 1) return 0.5;
            const tr = radiusForDegree(t.deg, isScoped);
            const arrow = isScoped ? 6 : 3.5;
            // back the tip off the target centre by (target radius + arrow + gap)
            const off = tr + arrow + (isScoped ? 4 : 2);
            return Math.max(0.05, Math.min(0.95, (len - off) / len));
          }) as never}
          linkDirectionalArrowColor={linkColor as never}
          linkHoverPrecision={6}
          // Run more layout ticks off-screen (warmup) + a longer cooldown so the
          // collision/charge forces fully separate dense radars BEFORE the user
          // sees them - no live "neuron" relax animation.
          warmupTicks={nodeCount > 120 ? 80 : 40}
          cooldownTicks={200}
          d3VelocityDecay={0.35}
          // Pinning happens in the settle effect above (every node gets fx/fy),
          // so by the time the user can interact, the layout is frozen and
          // dragging one node never ripples to the others. The dragged node keeps
          // its dropped position. Collisions are allowed (drag onto others = fine).
          onEngineTick={() => { paintFrames.current += 1; if (paintFrames.current > 30) settledRef.current = true; }}
          onEngineStop={() => { settledRef.current = true; }}
          enableNodeDrag
          onNodeDragEnd={(n: FgNode) => { n.fx = n.x; n.fy = n.y; }}
          onNodeClick={handleCanvasNodeClick as never}
          onNodeHover={((n: FgNode | null) => setHoverId(n?.id ?? null)) as never}
          onLinkClick={selectEdge as never}
          onLinkHover={((l: FgLink | null) => setHoverEdgeId(l?.id ?? null)) as never}
          onBackgroundClick={() => setInternalSelectedId(null)}
        />
      </div>

      {/* Off-canvas a11y + test seam: the canvas is opaque to the a11y tree, so
          expose visible nodes + edges as lists with click/dblclick wiring. The
          off-canvas buttons bypass the canvas click-timer and call the handlers
          directly (single vs double map 1:1). */}
      <details data-testid="graph-text-alternative">
      <summary>{`The graph as a list: ${graphData.nodes.length} nodes and ${graphData.links.length} links`}</summary>
      <ul data-testid="graph-node-list" aria-label="Nodes in this picture">
        {graphData.nodes.map((n) => (
          <li key={n.id}>
            <button
              data-testid={`graph-node-${n.id}`}
              data-degree={n.deg}
              data-hidden={n.data.hiddenNeighbourCount ?? 0}
              data-labelled={labelSet.has(n.id) ? 'true' : 'false'}
              onClick={() => selectNode(n)}
              onDoubleClick={() => scopeNode(n)}
            >
              {nodeAccessibleName(n.data, names)}
            </button>
          </li>
        ))}
      </ul>
      <ul data-testid="graph-edge-list" aria-label="Links in this picture">
        {graphData.links.map((l) => (
          <li key={l.id}>
            <button
              data-testid={`graph-edge-${l.id}`}
              data-family={edgeFamilyKey(l.label)}
              onClick={() => selectEdge(l)}
            >
              {`${titleOf(l.source, nodeById)} ${humanizeEdge(l.label)} ${titleOf(l.target, nodeById)}`}
            </button>
          </li>
        ))}
      </ul>
      </details>

      {hoverId && (() => {
        const n = graphData.nodes.find((x) => x.id === hoverId);
        if (!n) return null;
        const p = n.data.properties;
        // An empty description ("" on topics and organisations, whose text is in summary) falls through.
        const desc = firstText(p.description, p.summary, p.abstract);
        const prov = firstText(p.source, p.evidence_url, p.provenance);
        // Place the tooltip OFFSET from the cursor, flipping to the opposite side
        // when near a viewport edge so it never covers the hovered node (which
        // would block the click). Falls back to top-left if we have no cursor pos.
        const cw = width; // kept current by the container's ResizeObserver
        let style: React.CSSProperties = { ...tooltipStyle };
        if (hoverPos) {
          const { left, top } = tooltipPlacement(hoverPos.x, hoverPos.y, cw, height);
          style = { ...tooltipStyle, left, top };
        }
        return (
          <div data-testid="graph-tooltip" style={style}>
            <div style={{ fontWeight: 700, marginBottom: 2 }}>{n.title}</div>
            <span style={chipStyle}>{legendDisplayName(n.label, names)}</span>
            {desc && <div style={{ marginTop: 6, color: '#cfd3d8', fontSize: 11, maxHeight: 80, overflow: 'hidden' }}>{plainText(String(desc)).slice(0, 200)}</div>}
            {prov && <div style={{ marginTop: 4, color: '#9aa4b0', fontSize: 10 }}>{String(prov).slice(0, 60)}</div>}
            <div style={{ marginTop: 6, paddingTop: 5, borderTop: '1px solid rgba(255,255,255,0.18)', color: '#cfd3d8', fontSize: 10, display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><MouseHint color="#ec7211" /> Details</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><MouseHint double color="#ec7211" /> Explore connections</span>
            </div>
          </div>
        );
      })()}

      <Legend
        present={presentLabels}
        hidden={hidden}
        edgeFamilies={presentEdgeFamilies}
        names={names}
        onToggle={(lbl) => {
          setHidden((prev) => { const next = new Set(prev); if (next.has(lbl)) next.delete(lbl); else next.add(lbl); return next; });
        }}
      />
    </div>
  );
}

const tooltipStyle: React.CSSProperties = {
  position: 'absolute', top: 12, left: 12, maxWidth: 280, background: '#232f3e',
  color: '#fff', borderRadius: 8, padding: '10px 12px', fontSize: 12,
  fontFamily: 'system-ui, sans-serif', boxShadow: '0 4px 12px rgba(0,0,0,0.3)', pointerEvents: 'none', zIndex: 5,
};
const chipStyle: React.CSSProperties = {
  display: 'inline-block', background: 'rgba(255,255,255,0.15)', borderRadius: 4,
  padding: '1px 6px', fontSize: 10, textTransform: 'lowercase',
};

// sec. 55.15 #4 - a tiny mouse glyph whose LEFT button pulses to represent a click.
// `double` makes the pulse blink twice (double-click). Pure inline SVG + SMIL so
// it animates without JS/CSS deps; `color` tints the active button. The mouse
// body uses currentColor so it inherits the surrounding text colour.
function MouseHint({ double = false, color = '#ec7211' }: { double?: boolean; color?: string }): React.ReactElement {
  // One click = a single 0->1->0 opacity pulse per cycle; double = two quick pulses
  // then a pause, via a keyTimes/values envelope on the same 1.4s cycle.
  const values = double ? '0;1;0;1;0;0' : '0;1;0;0';
  const keyTimes = double ? '0;0.12;0.24;0.36;0.48;1' : '0;0.25;0.5;1';
  return (
    <svg width="13" height="18" viewBox="0 0 13 18" aria-hidden style={{ flex: '0 0 auto', verticalAlign: 'middle' }}>
      {/* mouse body */}
      <rect x="1" y="1" width="11" height="16" rx="5.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      {/* divider + scroll line */}
      <line x1="6.5" y1="1.5" x2="6.5" y2="7" stroke="currentColor" strokeWidth="0.9" />
      {/* left button fill that pulses */}
      <path d="M1.6 6.2 V6 a5 5 0 0 1 4.9 -5 V6.2 Z" fill={color}>
        <animate attributeName="opacity" values={values} keyTimes={keyTimes} dur="1.4s" repeatCount="indefinite" />
      </path>
    </svg>
  );
}

function Legend({
  present,
  hidden,
  edgeFamilies,
  names,
  onToggle,
}: {
  present: string[];
  hidden: Set<string>;
  edgeFamilies: EdgeFamily[];
  names: Record<string, string>;
  onToggle: (label: string) => void;
}): React.ReactElement {
  if (present.length === 0) return <></>;
  return (
    <div
      data-testid="graph-legend"
      style={{
        // H6-044 - theme-aware so the legend isn't a white box on a dark canvas.
        position: 'absolute', top: 8, right: 8, background: 'var(--color-background-container-content, rgba(255,255,255,0.94))',
        border: '1px solid var(--color-border-divider-default, #ddd)', borderRadius: 4, padding: '8px 10px', fontSize: 11,
        fontFamily: 'system-ui, sans-serif', boxShadow: '0 1px 3px rgba(0,0,0,0.08)', maxHeight: 340, overflowY: 'auto',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--color-text-body-default, #333)' }}>Node types (click to toggle)</div>
      {present.map((label) => {
        const off = hidden.has(label);
        return (
          <button
            key={label}
            data-testid={`legend-${label}`}
            // HU-029 - the toggle state for assistive tech: pressed = shown.
            aria-pressed={!off}
            onClick={() => onToggle(label)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, width: '100%',
              background: 'none', border: 'none', padding: '1px 2px', cursor: 'pointer',
              opacity: off ? 0.4 : 1, textAlign: 'left', font: 'inherit', color: 'var(--color-text-body-default, #333)',
            }}
          >
            <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: nodeColor(label), border: '1px solid #fff', boxShadow: '0 0 0 1px #ccc' }} />
            <span style={{ textDecoration: off ? 'line-through' : 'none' }}>{legendDisplayName(label, names)}{EVIDENCE_LABELS.has(label) ? ' (evidence)' : ''}</span>
          </button>
        );
      })}
      {edgeFamilies.length > 0 && (
        <div data-testid="graph-edge-legend" style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid #eee' }}>
          <div style={{ fontWeight: 600, marginBottom: 4, color: '#333' }}>Relationships</div>
          <div style={{ maxHeight: 168, overflowY: 'auto' }}>
            {edgeFamilies.map((f) => (
              <div key={f.key} data-testid={`edge-legend-${f.key}`} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                {/* directed swatch: line + arrowhead (edges are directional, #2) */}
                <svg width="24" height="8" style={{ flex: '0 0 auto' }} aria-hidden>
                  <line x1="1" y1="4" x2="18" y2="4" stroke={f.color} strokeWidth={Math.max(1.5, f.width)} strokeDasharray={f.dash ? f.dash.join(' ') : undefined} />
                  <path d="M18 1.5 L23 4 L18 6.5 Z" fill={f.color} />
                </svg>
                <span style={{ color: '#333' }}>{f.name}</span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 4, color: '#888', fontSize: 10 }}>Click an edge for details.</div>
        </div>
      )}
      {/* Persistent interaction hint (#4) - mouse glyphs convey click vs double-click. */}
      <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid #eee', color: '#666', fontSize: 10, lineHeight: 1.6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#888' }}><MouseHint color="#ec7211" /></span> Click a node for details
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#888' }}><MouseHint double color="#ec7211" /></span> Double-click to explore
        </div>
      </div>
    </div>
  );
}
