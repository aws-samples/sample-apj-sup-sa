// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

import type { RadarEntry, RadarSchema } from '@/api/types';

import { fold, layout, markerArc, type PlacedBlip } from './layout';
import { RadarChart } from './RadarChart';

/**
 * The picture as it leaves the app: the chart with its legend and its marker key beside it. Without
 * the legend an exported SVG or PNG is numbered circles nobody outside the app can decode, and the
 * marker arcs have no key at all.
 *
 * It is one SVG with the chart nested in it (SVG may nest SVG), so the existing export path - serialise,
 * then draw through an image onto a canvas for the PNG - needs no change.
 */
const CHART = 840;
const COLUMN = 380;
const PADDING = 24;
const LINE = 17;
const TITLE_CHARS = 34;

/** A legend line: a heading, or a numbered entry. */
export interface SheetLine {
  kind: 'sector' | 'ring' | 'entry';
  text: string;
  color: string;
  number?: number;
}

const clipTitle = (text: string) => (text.length > TITLE_CHARS ? `${text.slice(0, TITLE_CHARS - 1)}\u2026` : text);

/** The legend in reading order: a sector heading, then a ring heading, then its numbered entries. */
export function sheetLines(blips: PlacedBlip[], bands: { name: string }[], wedges: { name: string; color: string }[]): SheetLine[] {
  const lines: SheetLine[] = [];
  for (const wedge of wedges) {
    const inSector = blips.filter((b) => fold(b.entry.sector) === fold(wedge.name));
    if (!inSector.length) continue;
    lines.push({ kind: 'sector', text: wedge.name, color: wedge.color });
    for (const band of bands) {
      const items = inSector.filter((b) => fold(b.entry.ring) === fold(band.name));
      if (!items.length) continue;
      lines.push({ kind: 'ring', text: band.name, color: wedge.color });
      for (const b of items) {
        const marker = b.entry.marker ? ` (${b.entry.marker})` : '';
        lines.push({ kind: 'entry', text: `${clipTitle(b.entry.title)}${marker}`, color: wedge.color, number: b.number });
      }
    }
  }
  return lines;
}

/** How tall the sheet has to be for the chart, the legend columns and the marker key. */
export function sheetHeight(lines: number, markers: number): number {
  const legend = 2 * PADDING + lines * LINE;
  const key = markers ? 2 * PADDING + (markers + 1) * LINE : 0;
  return Math.max(CHART, legend, key);
}

export interface ExportSheetProps {
  schema: RadarSchema;
  entries: RadarEntry[];
  title: string;
}

/** Drawn off-screen and exported as-is; never interactive, so it has no tab stops. */
export const ExportSheet = React.forwardRef<SVGSVGElement, ExportSheetProps>(function ExportSheet({ schema, entries, title }, ref) {
  const data = React.useMemo(() => layout(schema, entries, CHART / 2 - 50, 10), [schema, entries]);
  const lines = React.useMemo(() => sheetLines(data.blips, data.bands, data.wedges), [data]);
  // One column per 44 lines, so a 60-entry radar stays on one page instead of one very long strip.
  const perColumn = Math.max(1, Math.floor((CHART - 2 * PADDING) / LINE));
  const columns = Math.max(1, Math.ceil(lines.length / perColumn));
  const width = CHART + columns * COLUMN + PADDING;
  const height = sheetHeight(Math.min(lines.length, perColumn), schema.markers.length);
  return (
    <svg
      ref={ref}
      role="img"
      aria-label={`${title} radar with its legend`}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      xmlns="http://www.w3.org/2000/svg"
      style={{
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif',
        background: '#ffffff',
      }}
    >
      <rect x={0} y={0} width={width} height={height} fill="#ffffff" />
      <svg x={0} y={0} width={CHART} height={CHART}>
        <RadarChart schema={schema} entries={entries} title={title} size={CHART - 2 * 100} interactive={false} markerKey={false} />
      </svg>
      {lines.map((line, i) => {
        const column = Math.floor(i / perColumn);
        const y = PADDING + (i % perColumn) * LINE + LINE;
        const x = CHART + column * COLUMN;
        if (line.kind === 'sector') {
          return (
            <g key={`s${i}`}>
              <text x={x} y={y} fontSize={14} fontWeight={700} fill="#1d2127">{line.text}</text>
              <line x1={x} y1={y + 4} x2={x + COLUMN - PADDING} y2={y + 4} stroke={line.color} strokeWidth={2} />
            </g>
          );
        }
        if (line.kind === 'ring') {
          return <text key={`r${i}`} x={x} y={y} fontSize={11} fontWeight={600} letterSpacing={0.8} fill="#4a5260">{line.text.toUpperCase()}</text>;
        }
        return (
          <text key={`e${i}`} x={x} y={y} fontSize={12} fill="#16191f">
            <tspan fontWeight={700} fill={line.color}>{`${line.number}. `}</tspan>
            {line.text}
          </text>
        );
      })}
      {schema.markers.length > 0 && (
        <g transform={`translate(${PADDING},${CHART - PADDING - (schema.markers.length + 1) * LINE})`}>
          <text x={0} y={0} fontSize={12} fontWeight={700} fill="#1d2127">{`${schema.marker_label}:`}</text>
          {schema.markers.map((marker, i) => (
            <g key={marker.name} transform={`translate(10,${(i + 1) * LINE})`}>
              <circle r={7} fill="none" stroke="#9aa3ad" strokeWidth={1} />
              <path d={markerArc(i, schema.markers.length, 9.5, 12)} fill="#1d2127" />
              <text x={20} y={4} fontSize={12} fill="#16191f">{marker.name}</text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
});
