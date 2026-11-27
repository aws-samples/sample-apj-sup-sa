// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import { navItems } from '@/components/AppShell';
import { KIND_HELP, KIND_LABELS, moduleName, typeLabel } from '@/lib/format';
import { aboutMd, appName, applyProfile, DEFAULT_APP_NAME, listOrder, listVisible, relabelNav, tagline } from '@/lib/profile';
import { usePageTitle } from '@/lib/pageTitle';
import { renderHook } from '@testing-library/react';

const lp = (label: string, item: string, extra: Partial<{ help: string; visible: boolean; order: number }> = {}) => ({
  label,
  item,
  help: '',
  visible: true,
  order: 0,
  ...extra,
  set: ['label', 'item', ...Object.keys(extra)],
});

describe('deployment profile', () => {
  it('leaves the built-in text alone when the server sends no profile', () => {
    applyProfile(undefined);
    applyProfile(null);
    expect(appName()).toBe(DEFAULT_APP_NAME);
    expect(KIND_LABELS.signals).toBe('Signals');
    expect(listVisible('gaps')).toBe(true);
    expect(listOrder('gaps')).toBe(100);
  });

  it('keeps the built-in module names for lists the deployment did not configure', () => {
    applyProfile({
      app_name: DEFAULT_APP_NAME,
      tagline: '',
      about_md: '',
      version: 0,
      // The server merges its defaults in; entries without "set" were not configured here.
      lists: { drafts: { label: 'Drafts', item: 'Draft', help: 'Server default text.', visible: true, order: 5 } },
    });
    expect(moduleName('draft')).toBe('Content drafts');
    expect(KIND_HELP.drafts?.moduleName).toBe('Content drafts');
    expect(KIND_HELP.drafts?.about).not.toBe('Server default text.');
  });

  it('renames lists, records and their modules, hides lists and orders the navigation', () => {
    applyProfile({
      app_name: 'Trade Radar',
      tagline: 'Rule changes that move freight',
      about_md: '# About',
      version: 2,
      lists: {
        signals: lp('Regulatory changes', 'Regulatory change', { help: 'Rule changes, each with a source.', order: 2 }),
        trends: lp('Themes', 'Theme', { order: 1 }),
        gaps: lp('Gaps', 'Gap', { visible: false }),
        content: lp('Content ideas', 'Content idea', { visible: false }),
        drafts: lp('Drafts', 'Draft', { visible: false }),
        'insight:watchlist': lp('Watchlist', 'Watch item', { order: 3 }),
        'insight:retired': lp('Retired', 'Retired item', { visible: false }),
      },
    });
    expect(appName()).toBe('Trade Radar');
    expect(tagline()).toBe('Rule changes that move freight');
    expect(aboutMd()).toBe('# About');
    expect(document.title).toBe('Trade Radar');
    expect(KIND_LABELS.signals).toBe('Regulatory changes');
    expect(KIND_HELP.signals?.about).toBe('Rule changes, each with a source.');
    expect(KIND_HELP.trends?.about).toContain('Patterns'); // an empty help keeps the built-in text
    expect(typeLabel('signal')).toBe('Regulatory change');
    expect(moduleName('signal')).toBe('Regulatory changes');
    expect(KIND_LABELS['insight:watchlist']).toBeUndefined(); // custom labels come from /api/nav

    const items = navItems({ builtin: [], custom: [{ kind: 'insight:watchlist', label: 'Watchlist', module: 'w' }, { kind: 'insight:hidden', label: 'Hidden', module: 'h' }, { kind: 'insight:retired', label: 'Retired', module: 'r' }] }, false);
    const section = items.find((i) => i.type === 'section') as unknown as { items: readonly { text: string }[] };
    expect(section.items.map((i) => i.text)).toEqual(['Themes', 'Regulatory changes', 'Watchlist', 'Hidden']);

    renderHook(() => usePageTitle('Radars'));
    expect(document.title).toBe('Radars - Trade Radar');
  });

  it('falls back to the built-in name and blank texts for an incomplete profile', () => {
    applyProfile({ app_name: '', lists: {}, version: 1 } as unknown as Parameters<typeof applyProfile>[0]);
    expect(appName()).toBe(DEFAULT_APP_NAME);
    expect(tagline()).toBe('');
    expect(aboutMd()).toBe('');
    applyProfile({ app_name: 'X', version: 1 } as unknown as Parameters<typeof applyProfile>[0]);
    expect(listVisible('signals')).toBe(true);
  });
});

describe('custom list names', () => {
  it('takes a custom list name from the profile when it sets one', () => {
    applyProfile({
      app_name: 'X', tagline: '', about_md: '', version: 1,
      lists: { 'insight:rules': { label: 'Rule changes', item: 'Rule change', help: '', visible: true, order: 3 } },
    });
    const nav = relabelNav({
      builtin: [],
      custom: [{ kind: 'insight:rules', label: 'Rules', module: 'm' }, { kind: 'insight:other', label: 'Other', module: 'o' }],
    });
    expect(nav.custom.map((c) => c.label)).toEqual(['Rule changes', 'Other']);
  });
});
