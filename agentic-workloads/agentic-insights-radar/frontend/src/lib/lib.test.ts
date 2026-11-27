// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import { actorLabel, ago, clip, modeLabel, fileSize, hostOf, KIND_LABELS, moduleName, nodeHref, plural, roleLabel, runStatusLabel, sentence, tokens, triggerLabel, typeLabel, when } from './format';
import { canonicalHref, isSafeHref, resolvesInApp } from './html';

describe('isSafeHref', () => {
  it.each([
    'https://example.com/a',
    'http://example.com',
    'mailto:a@example.com',
    '/insights/nodes/signal/1',
    '#section',
    'relative/path',
  ])('allows %s', (href) => expect(isSafeHref(href)).toBe(true));

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' javascript:alert(1)',
    'java\u0009script:alert(1)',
    'java\u200bscript:alert(1)',
    'jav&#97;script:alert(1)',
    '&#106;avascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:x',
    '//evil.example.com',
    '\\\\evil.example.com',
    '/\\evil.example.com',
    '',
    '\u0000',
  ])('blocks %s', (href) => expect(isSafeHref(href)).toBe(false));

  it('canonicalises entities and invisible characters', () => {
    expect(canonicalHref('a&amp;b\u200b')).toBe('a&b');
    expect(canonicalHref('plain')).toBe('plain');
  });

  it('knows which internal links resolve', () => {
    expect(resolvesInApp('/insights/nodes/gap/1')).toBe(true);
    expect(resolvesInApp('/radars/x?y=1')).toBe(true);
    expect(resolvesInApp('/')).toBe(true);
    expect(resolvesInApp('/radar/techniques/x')).toBe(false);
    expect(resolvesInApp('/drafts/content_draft/1')).toBe(false); // App.tsx has no /drafts route
  });
});

describe('format', () => {
  const now = Date.parse('2026-10-02T12:00:00Z');
  it.each([
    ['2026-10-02T11:59:50Z', 'just now'],
    ['2026-10-02T11:58:00Z', '2m ago'],
    ['2026-10-02T09:00:00Z', '3h ago'],
    ['2026-09-29T12:00:00Z', '3d ago'],
    ['2026-10-02T12:00:30Z', 'just now'], // a server clock a little ahead
  ])('ago(%s) = %s', (iso, out) => expect(ago(iso, now)).toBe(out));

  it('shows a date for old moments and for clearly future ones (clock skew), not "just now"', () => {
    for (const iso of ['2026-07-01T12:00:00Z', '2024-10-01T12:00:00Z', '2026-10-03T12:00:00Z']) {
      expect(ago(iso, now)).toBe(new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }));
    }
  });

  it('pluralises, clips at a word, and formats sizes and labels', () => {
    expect(plural(1, 'entry', 'entries')).toBe('1 entry');
    expect(plural(3, 'entry', 'entries')).toBe('3 entries');
    expect(plural(2, 'link')).toBe('2 links');
    expect(clip('short', 10)).toBe('short');
    expect(clip('Iceberg and Parquet queries run fast', 26)).toBe('Iceberg and Parquet\u2026');
    expect(clip('Supercalifragilistic', 8)).toBe('Supercal\u2026');
    expect(fileSize(500)).toBe('500 B');
    expect(fileSize(2048)).toBe('2.0 KB');
    expect(fileSize(40 * 1024 * 1024)).toBe('40 MB');
    expect(fileSize(3 * 1024 ** 4)).toBe('3072 GB');
    expect(roleLabel('admin')).toBe('Administrator');
    expect(roleLabel('owner')).toBe('owner');
    expect(runStatusLabel('budget_exhausted')).toBe('Budget used up');
    expect(runStatusLabel('paused_by_admin')).toBe('Paused by admin');
    expect(triggerLabel('manual')).toBe('Started by a person');
    expect(triggerLabel('webhook')).toBe('Webhook');
    expect(sentence('market_shift')).toBe('Market shift');
    expect(modeLabel('assist')).toBe('Assist');
    expect(modeLabel('future_mode')).toBe('future_mode');
  });

  it('handles missing and invalid dates', () => {
    expect(ago(null)).toBe('-');
    expect(ago('nope')).toBe('nope');
    expect(when(undefined)).toBe('-');
    expect(when('nope')).toBe('nope');
    expect(when('2026-10-02T12:00:00Z')).toMatch(/2026/);
  });

  it.each([
    [0, '0'],
    [999, '999'],
    [1500, '1.5k'],
    [25_000, '25k'],
    [1_500_000, '1.5M'],
    [12_000_000, '12M'],
  ])('tokens(%d) = %s', (n, out) => expect(tokens(n)).toBe(out));

  it('labels types and kinds', () => {
    expect(typeLabel('content_idea')).toBe('Content idea');
    expect(typeLabel('new_kind')).toBe('new kind');
    expect(KIND_LABELS.gaps).toBe('Gaps');
    expect(nodeHref('gap/1')).toBe('/insights/nodes/gap/1');
  });

  it('encodes each part of a node link', () => {
    expect(nodeHref('content idea/a b?c#d')).toBe('/insights/nodes/content%20idea/a%20b%3Fc%23d');
    expect(nodeHref('gap/x/y')).toBe('/insights/nodes/gap/x%2Fy');
    expect(nodeHref('orphan')).toBe('/insights/nodes/orphan/');
  });

  it('shows the host of a source, or the URL as written', () => {
    expect(hostOf('https://news.example.com/a?b=1')).toBe('news.example.com');
    expect(hostOf('/radars/rivals')).toBe('/radars/rivals');
    expect(hostOf('mailto:a@example.com')).toBe('mailto:a@example.com');
  });
});

describe('actorLabel', () => {
  it('names who made something in words', () => {
    expect(actorLabel('user:abc', null, 'signal/sample-11')).toBe('Sample data');
    // F2-089: the module's own display name, as the navigation and the Modules page use it.
    expect(actorLabel('module:signal', 'signal')).toBe('Signals');
    expect(actorLabel('module:trend')).toBe('Trends');
    expect(actorLabel('module:pricing-watch')).toBe('Pricing-watch module'); // a custom module keeps its slug
    expect(actorLabel('user:abc')).toBe('Added by a person');
    expect(actorLabel('system')).toBe('system');
    expect(actorLabel('')).toBe('Unknown');
  });
});

describe('moduleName', () => {
  it('names built-in modules and keeps custom slugs', () => {
    expect(moduleName('curator')).toBe('Radar curator');
    expect(moduleName('pricing-watch')).toBe('pricing-watch');
  });
});

describe('datedText', () => {
  it('says how far a calendar date is from today and leaves other text alone', async () => {
    const { datedText } = await import('@/lib/format');
    const today = new Date(2026, 9, 23, 12, 0); // noon on 23 October in the test's own time zone
    expect(datedText('2026-08-27', today)).toBe('2026-08-27 (57 days ago)');
    expect(datedText('2026-10-22', today)).toBe('2026-10-22 (1 day ago)');
    expect(datedText('2026-10-23', today)).toBe('2026-10-23 (today)');
    expect(datedText('2026-10-24', today)).toBe('2026-10-24 (in 1 day)');
    expect(datedText('2027-01-20', today)).toBe('2027-01-20 (in 89 days)');
    expect(datedText('9999-99-99', today)).toBe('9999-99-99');
    expect(datedText('soon', today)).toBe('soon');
    expect(datedText('2000-01-01')).toMatch(/^2000-01-01 \(\d+ days ago\)$/);
  });

  it("counts from the reader's own calendar day, not the UTC one", async () => {
    const { datedText } = await import('@/lib/format');
    // The suite runs in UTC (vite.config.ts), where the two days coincide: switch to a zone where they
    // do not. 2026-10-23T12:00Z is already 24 October in Auckland (UTC+13).
    const zone = process.env.TZ;
    process.env.TZ = 'Pacific/Auckland';
    try {
      expect(datedText('2026-10-24', new Date('2026-10-23T12:00:00Z'))).toBe('2026-10-24 (today)');
    } finally {
      process.env.TZ = zone;
    }
    // 23:30 and 00:30 local time, wherever the test runs: the local date decides, at both ends of the day.
    expect(datedText('2026-10-23', new Date(2026, 9, 23, 23, 30))).toBe('2026-10-23 (today)');
    expect(datedText('2026-10-23', new Date(2026, 9, 23, 0, 30))).toBe('2026-10-23 (today)');
    expect(datedText('2026-10-24', new Date(2026, 9, 23, 23, 30))).toBe('2026-10-24 (in 1 day)');
  });
});
