// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import React from 'react';

import type { RadarEntry, RadarSchema } from '@/api/types';

import { fold, layout, markerArc, type PlacedBlip, polar, readableText, wrapLabel } from './layout';

export interface RadarChartProps {
  schema: RadarSchema;
  entries: RadarEntry[];
  selected?: string | null;
  onSelect?: (entry: RadarEntry | null) => void;
  size?: number;
  title?: string;
  /** Keys of the entries that match the legend filter; the others are dimmed. Absent = no filter. */
  visible?: ReadonlySet<string> | null;
  /** False for a picture only (the export copy, previews): no focusable or clickable blips. */
  interactive?: boolean;
  /** The "skip to the legend" link; off where the legend already comes first (r3 F3-021). */
  skipLink?: boolean;
  /** The marker key; off where the picture around the chart draws its own (the export sheet: r3 F3-097). */
  markerKey?: boolean;
}

const BLIP = 10;
/** Room left and right of the circle for segment names, which sit outside it (wrapped onto lines). */
const LABEL_GUTTER = 100;
/** Gap between a blip's disc and its marker arc, and the arc's thickness (Tech Radar proportions). */
const ARC_GAP = 2.5;
const ARC_WIDTH = 2.5;

const TIP_WIDTH = 220;
const TIP_HEIGHT = 34;

/** Where the hover tooltip goes: above the blip, or below it near the top, and always inside the picture. */
export function tooltipAt(b: { x: number; y: number }, half: number): string {
  const edge = half + LABEL_GUTTER - TIP_WIDTH / 2 - 2;
  const x = Math.max(-edge, Math.min(edge, b.x));
  // The box is drawn from -38 to -4 relative to this point: flip below the blip when it would leave the top.
  const above = b.y - BLIP - 10;
  const y = above - 38 < -half ? b.y + BLIP + 10 + TIP_HEIGHT + 4 : above;
  return `translate(${x.toFixed(1)},${y.toFixed(1)})`;
}

/**
 * The radar as an SVG drawn by React (no D3 DOM mutation), so it can be exported as-is. It follows the
 * internal Tech Radar look: a light disc with thin ring circles, dark segment lines from the centre,
 * ring names in capitals beside a divider at 12 o'clock, segment names outside the circle, and numbered
 * discs in the segment colour with the marker drawn as an arc slice around the disc.
 */
export const RadarChart = React.forwardRef<SVGSVGElement, RadarChartProps>(function RadarChart(
  { schema, entries, selected, onSelect, size = 640, title, visible, interactive = true, skipLink = true, markerKey = true },
  ref,
) {
  const radius = size / 2 - 50;
  const data = React.useMemo(() => layout(schema, entries, radius, BLIP), [schema, entries, radius]);
  const [hover, setHover] = React.useState<PlacedBlip | null>(null);
  const half = size / 2;
  const markers = schema.markers.length;
  const outer = data.bands.length ? (data.bands[data.bands.length - 1] as { outer: number }).outer : radius;
  return (
    <>
      {/* 60 blips are 60 tab stops before the legend, which repeats every entry as a button: one link
          past them. It is out of the way until it has focus (.air-skip). */}
      {interactive && skipLink && data.blips.length > 1 && (
        <button type="button" className="air-skip" onClick={() => document.getElementById('air-legend-start')?.focus()}>
          Skip the chart and go to the legend
        </button>
      )}
    <svg
      ref={ref}
      role="group"
      aria-label={title ? `${title} radar` : 'Radar'}
      viewBox={`${-half - LABEL_GUTTER} ${-half} ${size + 2 * LABEL_GUTTER} ${size}`}
      width="100%"
      style={{ maxWidth: size + 2 * LABEL_GUTTER, display: 'block', margin: '0 auto', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif' }}
      onClick={() => onSelect?.(null)}
    >
      <title>{title ?? 'Radar'}</title>
      <circle className="air-radar-disc" cx={0} cy={0} r={outer} fill="#fafbfc" stroke="#3d434d" strokeWidth={1.25} />
      {data.bands.map((b) => (
        <circle key={`ring-${b.name}`} className="air-radar-ring" cx={0} cy={0} r={b.outer} fill="none" stroke="#b7bdc5" strokeWidth={1} />
      ))}
      {data.wedges.length > 1 &&
        data.wedges.map((w) => {
          const p = polar(w.start, outer);
          return <line key={`edge-${w.name}`} className="air-radar-line" x1={0} y1={0} x2={p.x} y2={p.y} stroke="#3d434d" strokeWidth={1.5} />;
        })}
      {data.bands.map((b) => (
        <text
          key={`ring-label-${b.name}`}
          className="air-radar-ring-label"
          x={4}
          y={-(b.inner + b.outer) / 2}
          dominantBaseline="middle"
          fontSize={10}
          fontWeight={600}
          letterSpacing={1.2}
          fill="#4a5260"
        >
          {b.name.toUpperCase()}
        </text>
      ))}
      {data.wedges.map((w) => {
        const p = polar((w.start + w.end) / 2, outer + 34);
        const lines = wrapLabel(w.name);
        const lh = 15;
        return (
          <text
            key={`sector-${w.name}`}
            className="air-radar-segment-label"
            x={p.x}
            y={p.y}
            textAnchor="middle"
            fontSize={13}
            fontWeight={600}
            letterSpacing={0.6}
            fill="#1d2127"
          >
            <title>{w.name}</title>
            {lines.map((line, i) => (
              <tspan key={line + String(i)} x={p.x} y={p.y + (i - (lines.length - 1) / 2) * lh} dominantBaseline="middle">
                {line}
              </tspan>
            ))}
          </text>
        );
      })}
      {data.blips.map((b) => {
        const active = selected === b.entry.key;
        const dim = visible ? !visible.has(b.entry.key) : false;
        // Filtered-out blips stay drawn (faded) but leave the tab order and the accessibility tree.
        const live = interactive && !dim;
        const handlers = live
          ? {
              role: 'button',
              tabIndex: 0,
              // The sector is part of the name: unlike the legend, the chart has no heading to supply it.
              'aria-label': `${b.number}. ${b.entry.title}, ${b.entry.sector}, ${b.entry.ring}${b.entry.marker ? `, ${b.entry.marker}` : ''}`,
              'aria-pressed': active,
              onMouseEnter: () => setHover(b),
              onMouseLeave: () => setHover(null),
              onFocus: () => setHover(b),
              onBlur: () => setHover(null),
              // A tap fires mouseenter but no mouseleave until the next tap elsewhere, so the tooltip
              // sat over the neighbouring blips; the detail panel is what a touch user reads anyway.
              onPointerUp: (e: React.PointerEvent) => e.pointerType === 'touch' && setHover(null),
              onClick: (e: React.MouseEvent) => {
                e.stopPropagation();
                onSelect?.(b.entry);
              },
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelect?.(b.entry);
                }
              },
            }
          : { 'aria-hidden': true };
        return (
          <g
            key={b.entry.key}
            transform={`translate(${b.x.toFixed(1)},${b.y.toFixed(1)})`}
            opacity={dim ? 0.2 : 1}
            style={{ cursor: live && onSelect ? 'pointer' : 'default' }}
            {...handlers}
          >
            {/* The ring is drawn by CSS on :focus-visible: Firefox and Safari outline no SVG group. */}
            <circle className="air-focus-ring" r={BLIP + ARC_GAP + ARC_WIDTH + 4} fill="none" />
            <circle className="air-blip" r={BLIP} fill={b.color} stroke="rgba(0,0,0,0.55)" strokeWidth={1} />
            {b.markerIndex >= 0 && (
              <path className="air-blip-marker" d={markerArc(b.markerIndex, markers, BLIP + ARC_GAP, BLIP + ARC_GAP + ARC_WIDTH)} fill={b.color} />
            )}
            {active && <circle r={BLIP + ARC_GAP + ARC_WIDTH + 2} fill="none" stroke="#000" strokeWidth={2} />}
            <text textAnchor="middle" dominantBaseline="central" fontSize={BLIP} fontWeight={700} fill={readableText(b.color)}>
              {b.number}
            </text>
          </g>
        );
      })}
      {markerKey && markers > 1 && (
        // The arcs encode which marker an entry carries; without a key a reader sees partial rings
        // and has to hover every blip to tell MVP from Scale.
        <g transform={`translate(${-half - LABEL_GUTTER + 16},${half - 16 - markers * 16})`} className="air-marker-key">
          <text x={0} y={0} fontSize={11} fontWeight={700} fill="#1d2127">{`${schema.marker_label}:`}</text>
          {schema.markers.map((m, i) => (
            <g key={m.name} transform={`translate(8,${(i + 1) * 16})`}>
              <circle r={6} fill="none" stroke="#9aa3ad" strokeWidth={1} />
              <path d={markerArc(i, markers, 8, 10.5)} fill="#1d2127" />
              <text x={18} y={4} fontSize={11} fill="#16191f">{m.name}</text>
            </g>
          ))}
        </g>
      )}
      {hover && (
        <g transform={tooltipAt(hover, half)} pointerEvents="none">
          <rect x={-TIP_WIDTH / 2} y={-38} width={TIP_WIDTH} height={TIP_HEIGHT} rx={4} fill="#0f141a" opacity={0.92} />
          <text textAnchor="middle" y={-24} fontSize={12} fontWeight={600} fill="#fff">
            {hover.entry.title.length > 34 ? `${hover.entry.title.slice(0, 33)}\u2026` : hover.entry.title}
          </text>
          <text textAnchor="middle" y={-10} fontSize={10} fill="#cbd5e0">
            {[hover.entry.sector, hover.entry.sub_sector, hover.entry.ring, hover.entry.marker].filter(Boolean).join(' / ')}
          </text>
        </g>
      )}
    </svg>
    </>
  );
});

export interface LegendProps extends Omit<RadarChartProps, 'size' | 'title' | 'visible' | 'interactive'> {
  /** Filter text (owned by the page so it can live in the address as ?q=). */
  query?: string;
  onQueryChange?: (q: string) => void;
  /** Opens the entry editor, offered next to entries the chart cannot place. */
  onEditEntries?: () => void;
}

/** Entries matching the filter text in any field shown for them, case-insensitive. Empty text = no filter. */
export function matchingKeys(entries: RadarEntry[], query: string): Set<string> | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  return new Set(
    entries
      .filter((e) => [e.title, e.description, e.marker ?? '', e.sub_sector ?? '', e.sector, e.ring].some((v) => v.toLowerCase().includes(q)))
      .map((e) => e.key),
  );
}

/** Sub-sector groups in legend order: [folded key, label as first written]; '' = no sub-sector. */
function subGroups(blips: PlacedBlip[]): [string, string][] {
  const groups = new Map<string, string>();
  for (const b of blips) {
    const label = b.entry.sub_sector ?? '';
    const key = fold(label);
    if (!groups.has(key)) groups.set(key, label);
  }
  return [...groups.entries()];
}

/**
 * The legend as in the internal Tech Radar: a filter box with a hidden count, segment headings
 * underlined in the segment colour, ring sub-headings, and numbered rows (zebra-striped) whose badge
 * matches the blip on the chart. Selecting a row selects the blip.
 */
export function Legend({ schema, entries, onSelect, selected, query = '', onQueryChange, onEditEntries }: LegendProps) {
  const data = React.useMemo(() => layout(schema, entries), [schema, entries]);
  const visible = React.useMemo(() => matchingKeys(entries, query), [entries, query]);
  const shown = data.blips.filter((b) => !visible || visible.has(b.entry.key));
  const hidden = data.blips.length - shown.length;
  return (
    <SpaceBetween size="s">
      {/* Where the chart's skip link lands. */}
      <div id="air-legend-start" tabIndex={-1} />
      <TextFilter
        filteringText={query}
        filteringPlaceholder={`Filter by title, description, ${schema.sector_label.toLowerCase()}, ${schema.ring_label.toLowerCase()} or ${schema.marker_label.toLowerCase()}`}
        filteringAriaLabel="Filter entries"
        countText={hidden > 0 ? `${hidden} hidden` : ''}
        onChange={({ detail }) => onQueryChange?.(detail.filteringText)}
      />
      {shown.length === 0 && data.blips.length > 0 && <Box color="text-status-inactive">No matches for &quot;{query}&quot;</Box>}
      <div className="air-legend" role="region" aria-label="Legend entries">
        {data.wedges.map((w) => {
          const inSector = shown.filter((b) => fold(b.entry.sector) === fold(w.name));
          if (!inSector.length) return null;
          let row = 0;
          return (
            <section key={w.name}>
              <h2 className="air-legend-segment" style={{ borderBottomColor: w.color }}>
                {w.name}
              </h2>
              {subGroups(inSector).map(([sub, label]) => (
                <div key={`sub:${sub}`}>
                  {sub && <h3 className="air-legend-sub">{label}</h3>}
                  {data.bands.map((band) => {
                    const items = inSector.filter((b) => fold(b.entry.sub_sector ?? '') === sub && fold(b.entry.ring) === fold(band.name));
                    if (!items.length) return null;
                    return (
                      <div key={band.name}>
                        <div className="air-legend-ring">{band.name}</div>
                        {items.map((b) => {
                          row += 1;
                          const cls = ['air-legend-item', row % 2 === 0 ? 'air-legend-even' : '', selected === b.entry.key ? 'air-active' : '']
                            .filter(Boolean)
                            .join(' ');
                          return (
                            <button
                              key={b.entry.key}
                              type="button"
                              className={cls}
                              aria-label={`${b.number}. ${b.entry.title}, ${b.entry.ring}${b.entry.marker ? `, ${b.entry.marker}` : ''}`}
                              aria-pressed={selected === b.entry.key}
                              onClick={() => onSelect?.(b.entry)}
                            >
                              <span className="air-legend-badge" style={{ color: w.color }}>
                                {b.number}
                              </span>
                              <span className="air-legend-title">
                                {b.entry.title}
                                {b.entry.marker ? <span className="air-marker"> ({b.entry.marker})</span> : null}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              ))}
            </section>
          );
        })}
        {data.unplaced.length > 0 && (
          <section>
            <h2 className="air-legend-segment">Not shown</h2>
            <p>
              {data.unplaced.length === 1 ? '1 entry does' : `${data.unplaced.length} entries do`} not match this radar&apos;s{' '}
              {schema.sector_label.toLowerCase()} or {schema.ring_label.toLowerCase()} values:
            </p>
            <ul>
              {data.unplaced.map((e) => (
                <li key={e.key}>{`${e.title} (${e.sector || '-'} / ${e.ring || '-'})`}</li>
              ))}
            </ul>
            {onEditEntries && (
              <button type="button" className="air-linkish" onClick={onEditEntries}>
                Fix them in Edit entries
              </button>
            )}
          </section>
        )}
      </div>
      {data.blips.length > 1 && (
        <Box fontSize="body-s" color="text-body-secondary">
          Numbers follow the legend order, so adding or moving an entry can renumber the ones after it. Refer to entries by name.
        </Box>
      )}
    </SpaceBetween>
  );
}
