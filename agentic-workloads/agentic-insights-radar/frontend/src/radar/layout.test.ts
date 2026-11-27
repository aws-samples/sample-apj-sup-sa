// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import type { RadarEntry, RadarSchema } from '@/api/types';
import { SCHEMA } from '@/test/data';

import { bands, fold, layout, markerArc, PALETTE, polar, readableText, RING_LABEL_WIDTH, seeded, wedges, wrapLabel } from './layout';

const schema = (sectors: number, rings: number, markers: string[] = []): RadarSchema => ({
  sectors: Array.from({ length: sectors }, (_, i) => ({ name: `S${i}` })),
  rings: Array.from({ length: rings }, (_, i) => ({ name: `R${i}` })),
  markers: markers.map((name, i) => ({ name, glyph: ['triangle', 'square', 'diamond', 'star', 'ring', 'circle'][i] })),
  sector_label: 'Sector',
  ring_label: 'Ring',
  marker_label: 'Marker',
});

const entry = (title: string, sector: string, ring: string, marker?: string): RadarEntry => ({
  key: `k-${title}`,
  title,
  sector,
  ring,
  description: '',
  marker,
});

describe('radar layout', () => {
  it('splits the circle into equal wedges and equal-area rings', () => {
    const ws = wedges(schema(3, 4));
    expect(ws.map((w) => [w.start, w.end])).toEqual([[0, 120], [120, 240], [240, 360]]);
    expect(ws[0]?.color).toBe(PALETTE[0]);
    const bs = bands(schema(3, 4), 100);
    expect(bs[0]?.inner).toBe(0);
    expect(bs[3]?.outer).toBeCloseTo(100);
    const areas = bs.map((b) => b.outer ** 2 - b.inner ** 2);
    areas.forEach((a) => expect(a).toBeCloseTo(areas[0] ?? 0));
  });

  it('places every matching entry inside its cell, deterministically, without overlap', () => {
    const s = schema(4, 4, ['Up', 'Down']);
    const entries = Array.from({ length: 60 }, (_, i) => entry(`E${i}`, `S${i % 4}`, `R${i % 3}`, i % 5 === 0 ? 'Up' : undefined));
    const a = layout(s, entries, 300, 10);
    const b = layout(s, [...entries].reverse(), 300, 10);
    expect(a.blips).toHaveLength(60);
    expect(a.blips.map((x) => [x.entry.key, x.x, x.y])).toEqual(b.blips.map((x) => [x.entry.key, x.x, x.y]));
    for (const blip of a.blips) {
      const r = Math.hypot(blip.x, blip.y);
      const band = a.bands.find((x) => x.name === blip.entry.ring);
      expect(r).toBeGreaterThanOrEqual((band?.inner ?? 0) - 0.01);
      expect(r).toBeLessThanOrEqual((band?.outer ?? 0) + 0.01);
      const angle = ((Math.atan2(blip.y, blip.x) * 180) / Math.PI + 90 + 360) % 360;
      const wedge = a.wedges.find((w) => w.name === blip.entry.sector);
      expect(angle).toBeGreaterThanOrEqual((wedge?.start ?? 0) - 0.01);
      expect(angle).toBeLessThanOrEqual((wedge?.end ?? 0) + 0.01);
    }
    const close = a.blips.flatMap((p, i) => a.blips.slice(i + 1).filter((q) => Math.hypot(p.x - q.x, p.y - q.y) < 20));
    expect(close.length).toBeLessThan(3);
    expect(a.blips.find((x) => x.entry.marker === 'Up')?.glyph).toBe('triangle');
    expect(a.blips.map((x) => x.number)).toEqual(Array.from({ length: 60 }, (_, i) => i + 1));
  });

  it('matches values tolerantly and reports the rest', () => {
    const out = layout(schema(2, 2), [entry('A', ' s0 ', 'r1'), entry('B', 'Nowhere', 'R0'), entry('C', 'S1', 'R9')]);
    expect(out.blips.map((b) => b.entry.title)).toEqual(['A']);
    expect(out.unplaced.map((e) => e.title)).toEqual(['B', 'C']);
    expect(fold('Don\u2019t')).toBe("don't");
  });

  it('handles single-sector and single-ring radars', () => {
    const out = layout(schema(1, 1), [entry('Solo', 'S0', 'R0')]);
    expect(out.blips).toHaveLength(1);
    expect(out.wedges[0]).toMatchObject({ start: 0, end: 360 });
    expect(bands(schema(1, 2), 100)[1]!.inner).toBeCloseTo(100 * Math.sqrt(0.5));
  });

  it('places entries without a key by their title', () => {
    const keyless = { ...entry('Keyless', 'S0', 'R0'), key: '' };
    const a = layout(schema(2, 2), [keyless]).blips[0]!;
    const b = layout(schema(2, 2), [{ ...keyless, key: 'other' }]).blips[0]!;
    const c = layout(schema(2, 2), [{ ...keyless, key: 'Keyless' }]).blips[0]!;
    expect([a.x, a.y]).toEqual([c.x, c.y]);
    expect([a.x, a.y]).not.toEqual([b.x, b.y]);
  });

  it('centres blips in rings too thin to hold one', () => {
    const out = layout(schema(1, 12), [entry('Thin', 'S0', 'R11')], 60);
    const band = out.bands[11]!;
    const blip = out.blips[0]!;
    expect(Math.hypot(blip.x, blip.y)).toBeCloseTo((band.inner + band.outer) / 2);
  });

  it('stays deterministic', () => {
    const r1 = seeded('x');
    const r2 = seeded('x');
    expect([r1(), r1()]).toEqual([r2(), r2()]);
    expect(polar(90, 10).x).toBeCloseTo(10);
    expect(wedges(schema(2, 1))[0]).toMatchObject({ start: 0, end: 180 });
  });
});


describe('Tech Radar drawing helpers', () => {
  it('picks black or white for contrast, and white for an unparseable colour', () => {
    expect(readableText('#264653')).toBe('#ffffff');
    expect(readableText('#fafbfc')).toBe('#000000');
    expect(readableText('teal')).toBe('#ffffff');
    expect(readableText('#050505')).toBe('#ffffff'); // near-black: the linear part of the sRGB curve
  });

  it('wraps names at 14 characters on spaces and hyphens', () => {
    expect(wrapLabel('Tools & Frameworks')).toEqual(['Tools &', 'Frameworks']);
    expect(wrapLabel('Languages-and-Frameworks')).toEqual(['Languages and', 'Frameworks']);
    expect(wrapLabel('')).toEqual([]);
  });

  it('gives an entry whose marker is not in the schema no arc slot', () => {
    const schema = { sectors: [{ name: 'S' }], rings: [{ name: 'R' }], markers: [{ name: 'M' }] } as unknown as RadarSchema;
    const placed = layout(schema, [{ key: 'k', title: 'T', sector: 'S', ring: 'R', marker: 'Other', description: '' } as RadarEntry]);
    expect(placed.blips[0]!.markerIndex).toBe(-1);
  });

  it('draws one slice per marker, and a full ring when there is only one marker', () => {
    expect(markerArc(0, 2, 12, 15)).toMatch(/^M0\.00,-15\.00 A15,15 0 0,1 /);
    expect(markerArc(0, 1, 12, 15)).toContain('A15,15 0 1,1 15,0');
    expect(markerArc(0, 1, 12, 15)).toContain('A12,12 0 1,0 12,0');
  });
});

describe('ring labels', () => {
  it('keeps blips off the ring names beside the 12 o\'clock line', () => {
    const schema = { ...SCHEMA, sectors: [{ name: 'Only' }] };
    const entries = Array.from({ length: 12 }, (_, i) => ({ key: `k${i}`, title: `T${i}`, sector: 'Only', ring: SCHEMA.rings[0]!.name, description: '' }));
    const { blips, bands } = layout(schema, entries, 300, 10);
    const mid = (bands[0]!.inner + bands[0]!.outer) / 2;
    expect(blips.filter((b) => b.x > -10 && b.x < RING_LABEL_WIDTH && Math.abs(b.y + mid) < 24)).toEqual([]);
  });
});
