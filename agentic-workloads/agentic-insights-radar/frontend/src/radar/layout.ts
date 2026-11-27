// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { RadarEntry, RadarSchema } from '@/api/types';

/**
 * Radar geometry for any number of sectors (1-8) and rings (1-6).
 *
 * Sectors split the circle into equal wedges starting at 12 o'clock and going clockwise. Rings
 * have equal AREA (radius_i = R * sqrt(i / M)), so outer rings are not crowded relative to their
 * size. Blips are placed by a seeded generator keyed on the entry key, so a radar draws the same
 * way on every visit and in every export; collisions are avoided with best-of-N sampling (the
 * internal radar's BlipPositioner approach, generalised beyond its fixed quadrants).
 */
/** Segment colours (the internal Tech Radar palette: every colour has at least 3:1 contrast on white). */
export const PALETTE = ['#2A9D8F', '#D65A3C', '#B8860B', '#264653', '#6E5BA6', '#5E8C5E', '#8B6A4A', '#5A6169', '#C56A2D'];

/** Black or white, whichever reads better on the given #rrggbb fill (WCAG contrast). */
export function readableText(hex: string): '#000000' | '#ffffff' {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  const [r, g, b] = m ? [m[1], m[2], m[3]] : [];
  if (!r || !g || !b) return '#ffffff';
  const lin = (c: string) => {
    const v = parseInt(c, 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const l = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return (1.05 / (l + 0.05)) >= ((l + 0.05) / 0.05) ? '#ffffff' : '#000000';
}

/** Split a segment name into lines of at most `max` characters, breaking at spaces, '&', '/' and '-'. */
export function wrapLabel(name: string, max = 14): string[] {
  const chunks = name.replace(/ & /g, ' & ').split(/[\s-]+/).filter(Boolean);
  const lines: string[] = [];
  let cur = '';
  for (const c of chunks) {
    if (!cur) cur = c;
    else if (cur.length + 1 + c.length <= max) cur = `${cur} ${c}`;
    else {
      lines.push(cur);
      cur = c;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Arc path around a blip for marker slot `index` of `count` (the Tech Radar phase arc), centred on 0,0. */
export function markerArc(index: number, count: number, inner: number, outer: number): string {
  if (count <= 1) {
    return `M${-outer},0 A${outer},${outer} 0 1,1 ${outer},0 A${outer},${outer} 0 1,1 ${-outer},0 Z ` +
      `M${-inner},0 A${inner},${inner} 0 1,0 ${inner},0 A${inner},${inner} 0 1,0 ${-inner},0 Z`;
  }
  const span = 360 / count;
  const a0 = index * span;
  const a1 = a0 + span;
  const o0 = polar(a0, outer);
  const o1 = polar(a1, outer);
  const i1 = polar(a1, inner);
  const i0 = polar(a0, inner);
  const f = (n: number) => n.toFixed(2);
  // With two or more markers a slice spans at most 180 degrees, so the small-arc flag is always 0.
  return `M${f(o0.x)},${f(o0.y)} A${outer},${outer} 0 0,1 ${f(o1.x)},${f(o1.y)} L${f(i1.x)},${f(i1.y)} ` +
    `A${inner},${inner} 0 0,0 ${f(i0.x)},${f(i0.y)} Z`;
}

export interface Wedge {
  name: string;
  color: string;
  start: number; // degrees, 0 = 12 o'clock, clockwise
  end: number;
}

export interface Band {
  name: string;
  inner: number;
  outer: number;
}

export interface PlacedBlip {
  entry: RadarEntry;
  number: number;
  x: number;
  y: number;
  color: string;
  glyph: string;
  /** Position of the entry's marker in the schema (-1 when it has none): which arc slot to draw. */
  markerIndex: number;
}

export interface Layout {
  radius: number;
  wedges: Wedge[];
  bands: Band[];
  blips: PlacedBlip[];
  unplaced: RadarEntry[];
}

/** Smart quotes as the API folds them (core/names.py _SMART): single ones to ', double ones to ". */
const SMART: Record<string, string> = { '\u2018': "'", '\u2019': "'", '\u2032': "'", '\u201c': '"', '\u201d': '"' };

/**
 * A name as the API compares names (NameNormalizer.title_key): compatibility forms folded (NFKC),
 * invisible format characters (Unicode Cf: zero-width spaces, bidi controls) dropped, smart quotes
 * mapped, inner whitespace collapsed, and full case folding ("STRASSE" and the sharp-s spelling are
 * one name; toUpperCase then toLowerCase is JavaScript's closest match to Python's casefold). Every
 * client-side check that two names are "the same" uses this, so it agrees with what the API refuses.
 */
export function fold(value: string): string {
  const folded = value
    .normalize('NFKC')
    .replace(/\p{Cf}/gu, '')
    .replace(/[\u2018\u2019\u2032\u201c\u201d]/g, (c) => SMART[c] as string);
  return folded.replace(/\s+/g, ' ').trim().toUpperCase().toLowerCase();
}

/** Mulberry32 seeded by a string hash: small, fast, deterministic. */
export function seeded(seed: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
  let a = h >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function polar(angleDeg: number, r: number): { x: number; y: number } {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: r * Math.cos(rad), y: r * Math.sin(rad) };
}

export function wedges(schema: RadarSchema): Wedge[] {
  const n = Math.max(1, schema.sectors.length);
  const span = 360 / n;
  return schema.sectors.map((s, i) => ({
    name: s.name,
    color: s.color ?? (PALETTE[i % PALETTE.length] as string),
    start: i * span,
    end: (i + 1) * span,
  }));
}

export function bands(schema: RadarSchema, radius: number): Band[] {
  const m = Math.max(1, schema.rings.length);
  return schema.rings.map((r, i) => ({
    name: r.name,
    inner: radius * Math.sqrt(i / m),
    outer: radius * Math.sqrt((i + 1) / m),
  }));
}

/** Room the chart gives a ring name, to the right of the 12 o'clock line (RadarChart draws it at x=4). */
export const RING_LABEL_WIDTH = 80;

export function layout(schema: RadarSchema, entries: RadarEntry[], radius = 300, blipRadius = 10): Layout {
  const ws = wedges(schema);
  const bs = bands(schema, radius);
  const wedgeOf = new Map(ws.map((w) => [fold(w.name), w]));
  const bandOf = new Map(bs.map((b) => [fold(b.name), b]));
  const glyphOf = new Map(schema.markers.map((m) => [fold(m.name), m.glyph ?? 'circle']));
  const markerIndexOf = new Map(schema.markers.map((m, i) => [fold(m.name), i]));
  const sorted = [...entries].sort((a, b) => {
    const sa = ws.findIndex((w) => fold(w.name) === fold(a.sector));
    const sb = ws.findIndex((w) => fold(w.name) === fold(b.sector));
    const ra = bs.findIndex((x) => fold(x.name) === fold(a.ring));
    const rb = bs.findIndex((x) => fold(x.name) === fold(b.ring));
    // Legend order (the Tech Radar's): sector, then sub-sector (entries without one first), ring, title.
    return sa - sb || fold(a.sub_sector ?? '').localeCompare(fold(b.sub_sector ?? ''), 'en') || ra - rb || a.title.localeCompare(b.title, 'en');
  });
  const placed: PlacedBlip[] = [];
  const unplaced: RadarEntry[] = [];
  const minGap = blipRadius * 2 + 3;
  for (const entry of sorted) {
    const w = wedgeOf.get(fold(entry.sector));
    const b = bandOf.get(fold(entry.ring));
    if (!w || !b) {
      unplaced.push(entry);
      continue;
    }
    const rand = seeded(entry.key || entry.title);
    let best = { x: 0, y: 0, d: -1 };
    for (let attempt = 0; attempt < 400; attempt++) {
      const lo = b.inner + blipRadius + 2;
      const hi = b.outer - blipRadius - 2;
      const r = hi > lo ? Math.sqrt(lo * lo + rand() * (hi * hi - lo * lo)) : (b.inner + b.outer) / 2;
      // Keep the blip's disc clear of the wedge edges: the angular margin grows near the centre.
      const margin = Math.min((w.end - w.start) / 2 - 0.01, (Math.asin(Math.min(1, (blipRadius + 3) / Math.max(r, 1))) * 180) / Math.PI);
      const angle = w.start + margin + rand() * (w.end - w.start - 2 * margin);
      const { x, y } = polar(angle, r);
      const nearest = placed.reduce((m, p) => Math.min(m, Math.hypot(p.x - x, p.y - y)), Infinity);
      // The chart writes ring names just right of the 12 o'clock line; keep blips off them.
      const onLabel = x > -blipRadius && x < RING_LABEL_WIDTH && Math.abs(y + (b.inner + b.outer) / 2) < blipRadius * 2 + 4; // disc plus marker arc, plus the label half-height
      const d = onLabel ? 0 : nearest;
      if (d > best.d) best = { x, y, d };
      if (d >= minGap) break;
    }
    placed.push({
      entry,
      number: placed.length + 1,
      x: best.x,
      y: best.y,
      color: w.color,
      glyph: (entry.marker && glyphOf.get(fold(entry.marker))) || 'circle',
      markerIndex: entry.marker ? (markerIndexOf.get(fold(entry.marker)) ?? -1) : -1,
    });
  }
  return { radius, wedges: ws, bands: bs, blips: placed, unplaced };
}
