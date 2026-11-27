// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Download helpers for radar exports (SVG, PNG, CSV, JSON). */

import type { RadarEntry } from '@/api/types';

import { needsGuard } from './importFile';

/** The columns the API exports and accepts again (radars/codecs.py COLUMNS), in that order. */
export const ENTRY_COLUMNS = ['key', 'title', 'sector', 'ring', 'marker', 'description', 'node_id', 'link', 'sub_sector'] as const;

/**
 * Entries as the same CSV the API exports, so a file written here can be imported again. Cells that
 * start like a spreadsheet formula get a leading apostrophe, as the API's export does.
 */
export function entriesToCsv(entries: RadarEntry[]): string {
  const cell = (value: string) => {
    // As the API guards (codecs._needs_guard): a formula start, or quotes in front of one, so a
    // literal "'=draft" survives the round trip instead of losing its quote on import.
    const guarded = needsGuard(value) ? `'${value}` : value;
    return /["\n\r,]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
  };
  const row = (e: RadarEntry) => ENTRY_COLUMNS.map((c) => cell(String((e as unknown as Record<string, unknown>)[c] ?? ''))).join(',');
  // A byte-order mark, so Excel on Windows reads the file as UTF-8 and does not garble "Zurich" with
  // an umlaut before it is imported again (r3 F3-098); the importer strips it.
  return `\uFEFF${[ENTRY_COLUMNS.join(','), ...entries.map(row), ''].join('\n')}`;
}

export function download(filename: string, body: Blob | string, type = 'text/plain'): void {
  const blob = typeof body === 'string' ? new Blob([body], { type }) : body;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The SVG's drawing size from its viewBox (the radar is wider than tall: labels sit in side gutters). */
export function svgSize(svg: SVGSVGElement): { width: number; height: number } {
  const [, , width = 0, height = 0] = (svg.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number);
  return width > 0 && height > 0 ? { width, height } : { width: 640, height: 640 };
}

export function svgMarkup(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const { width, height } = svgSize(svg);
  clone.setAttribute('width', String(width));
  clone.setAttribute('height', String(height));
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
}

/** The most canvas pixels drawn at once: iOS Safari fails above 4096 x 4096 (r3 F3-031). */
export const CANVAS_MAX_PIXELS = 4096 * 4096;

/** Print quality (4x) where the canvas allows it, else the largest whole scale that fits, at least 1. */
export function pngScale(width: number, height: number, wanted = 4): number {
  return Math.max(1, Math.min(wanted, Math.floor(Math.sqrt(CANVAS_MAX_PIXELS / Math.max(1, width * height)))));
}

/** Render the SVG to a PNG at `scale` times its size (print quality at 4). */
export function svgToPng(markup: string, width: number, height: number, scale = 4): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        reject(new Error('canvas is not available'));
        return;
      }
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('PNG encoding failed'))), 'image/png');
    };
    img.onerror = () => reject(new Error('SVG could not be rendered'));
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;
  });
}

/** A download file name from a title; `fallback` when the title has no Latin letters or digits. */
export function slugFile(name: string, ext: string, fallback = 'radar'): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || fallback;
  return `${base}.${ext}`;
}
