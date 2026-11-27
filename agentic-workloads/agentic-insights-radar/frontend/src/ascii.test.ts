// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

// Source stays pure ASCII: invisible or look-alike characters in code are what scanners (and attackers)
// hide behind. Write any other character as a \u escape.
const files = import.meta.glob<string>(['/src/**/*.{ts,tsx,css}', '/index.html'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

describe('source files', () => {
  it('are found', () => {
    expect(Object.keys(files).length).toBeGreaterThan(30);
  });

  it.each(Object.entries(files))('%s is ASCII only', (_, text) => {
    const bad = [...text].map((c, i) => [c, i] as const).filter(([c]) => c.charCodeAt(0) > 0x7e);
    expect(bad.map(([c, i]) => `U+${c.charCodeAt(0).toString(16).padStart(4, '0')} at ${i}`)).toEqual([]);
  });
});
