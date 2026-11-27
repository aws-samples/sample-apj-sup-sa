// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

// Tables never need horizontal scrolling (2026-11-02): every Table wraps its lines, and the global stylesheet
// lets a long address or token break inside its cell.
const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx$/.test(f) && !/\.test\./.test(f) ? [p] : [];
  });

describe('tables', () => {
  it('every Table wraps its lines', () => {
    const tags = files(join(__dirname)).flatMap((p) =>
      [...readFileSync(p, 'utf8').matchAll(/<Table(\s\w*)/g)].map((m) => `${p}: <Table ${(m[1] ?? '').trim()}`.trimEnd()),
    );
    expect(tags.length).toBeGreaterThanOrEqual(20); // every Table in the app (20 on 2026-11-02)
    expect(tags.filter((t) => !t.endsWith('<Table wrapLines'))).toEqual([]);
  });

  it('cells break long text instead of widening the table', () => {
    const css = readFileSync(join(__dirname, 'styles.css'), 'utf8');
    expect(css).toMatch(/table:not\(\.air-table\) \{\s*table-layout: fixed;\s*width: 100%;/);
    expect(css).toMatch(/table:not\(\.air-table\) td \{\s*overflow-wrap: anywhere;/);
  });
});
