// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

// Vitest runs with css: false, so a rule cannot be observed through a rendered element. These read
// the stylesheet itself, which is what the CSS-only round-2 fixes changed.
// Read from disk: the test environment is configured with css: false, so importing the stylesheet
// (even ?raw) yields an empty module.
const css = readFileSync(new URL('./styles.css', `file://${import.meta.filename}`), 'utf8');

/** The declarations of every rule whose selector list is exactly `selector`. */
function rule(selector: string): string {
  const found = css
    .split('}')
    .map((block) => block.split('{'))
    .filter((parts) => parts.length === 2 && (parts[0] as string).trim().split('\n').pop()?.trim() === selector)
    .map((parts) => (parts[1] as string).trim());
  return found.join(' ');
}

describe('styles.css', () => {
  it('is parsed by selector', () => {
    expect(css.length).toBeGreaterThan(100);
    expect(rule('.air-pre')).toContain('white-space: pre-wrap');
  });

  it('F2-082: a multi-line question keeps its line breaks', () => {
    expect(rule('.air-user')).toContain('white-space: pre-wrap');
  });

  it('F2-028: the legend has no height limit and no nested scroll area', () => {
    const legend = rule('.air-legend');
    expect(legend).toContain('columns:');
    expect(legend).not.toContain('max-height');
    expect(legend).not.toContain('overflow-y');
    expect(legend).not.toContain('column-fill');
  });

  it('F2-030, r3 F3-021: one column on a narrow screen, with no visual reordering', () => {
    expect(css).toContain('@media (max-width: 1100px)');
    // The page puts the legend first in the markup there: CSS `order` sent the keyboard the other way.
    expect(rule('.air-radar-chart')).not.toContain('order');
    expect(rule('.air-radar-legend')).not.toContain('order');
  });

  it('F2-066: a focused blip and a focused graph node draw a ring', () => {
    expect(rule("[role='button']:focus-visible > .air-focus-ring")).toContain('stroke:');
    expect(rule('.air-graph-node:focus-visible > circle')).toContain('stroke:');
  });

  it('F2-065: the skip link is out of the way until it has focus', () => {
    expect(rule('.air-skip')).toContain('left: -10000px');
    expect(rule('.air-skip:focus-visible')).toContain('position: static');
  });
});
