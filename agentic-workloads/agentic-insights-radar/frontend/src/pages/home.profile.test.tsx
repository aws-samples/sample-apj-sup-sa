// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { applyProfile } from '@/lib/profile';
import { joinWords } from '@/lib/format';
import { ADMIN, VIEWER, fakeApi, wrap } from '@/test/session';

import HomePage from './HomePage';

const empty = { items: [], cursor: null, sort: 'newest', page_size: 25 };
const lp = (label: string, item: string, visible = true, order = 0) => ({ label, item, help: '', visible, order, set: ['label', 'item', 'visible', 'order'] });

afterEach(() => applyProfile({ app_name: '', tagline: '', about_md: '', version: 0, lists: {} }));

describe('Overview with a deployment profile', () => {
  it('leaves out hidden lists, follows the profile order and shows the tagline', async () => {
    applyProfile({
      app_name: 'Trade Radar',
      tagline: 'Rule changes that move freight',
      about_md: 'Reads **public sources** only.',
      version: 1,
      lists: {
        signals: lp('Regulatory changes', 'Regulatory change', true, 2),
        trends: lp('Themes', 'Theme', true, 1),
        gaps: lp('Gaps', 'Gap', false),
        content: lp('Content ideas', 'Content idea', false),
      },
    });
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': empty,
      'GET /api/insights/trends': empty,
      'GET /api/insights/gaps': empty,
      'GET /api/insights/content': empty,
      'GET /api/health': { runs: {}, recent_failures: [], budgets: [], genai_enabled: true },
    });
    render(wrap(<HomePage />, { api, me: ADMIN }));
    // Once, in the page header: Getting started does not repeat it.
    expect(await screen.findAllByText('Rule changes that move freight')).toHaveLength(1);
    const themes = await screen.findByRole('heading', { name: /Themes/ });
    const changes = screen.getByRole('heading', { name: /Regulatory changes/ });
    expect(themes.compareDocumentPosition(changes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole('heading', { name: /^Gaps/ })).toBeNull();
    expect(screen.queryByRole('heading', { name: /Content ideas/ })).toBeNull();
    expect(screen.getByRole('heading', { name: 'About this deployment' })).toBeInTheDocument();
    expect(screen.getByText('public sources').tagName).toBe('STRONG');
    // Getting started names this deployment's lists and has no product step without Gaps or Content ideas.
    expect(await screen.findByText(/Regulatory changes and Themes build on what Scout records/)).toBeInTheDocument();
    expect(screen.queryByText('Describe your product')).toBeNull();
    expect(screen.queryByText(/Gaps/)).toBeNull();
  });

  it('tells a reader about the lists this deployment shows', async () => {
    applyProfile({
      app_name: 'Trade Radar', tagline: '', about_md: '', version: 1,
      lists: { signals: lp('Regulatory changes', 'Regulatory change'), trends: lp('Themes', 'Theme', false) },
    });
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': empty });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    expect(await screen.findByText(/regulatory changes, each with its evidence/)).toBeInTheDocument();
  });

  it('words the steps for a deployment that keeps only content ideas', async () => {
    applyProfile({
      app_name: 'Trade Radar', tagline: '', about_md: '', version: 1,
      lists: {
        signals: lp('Signals', 'Signal', false), trends: lp('Trends', 'Trend', false), gaps: lp('Gaps', 'Gap', false),
      },
    });
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/content': empty });
    render(wrap(<HomePage />, { api, me: ADMIN }));
    expect(await screen.findByText(/The Content ideas module compares what the market says/)).toBeInTheDocument();
    expect(screen.getByText(/then Run now\.$/)).toBeInTheDocument();
  });

  it('says records when a reader sees none of the built-in lists', async () => {
    applyProfile({
      app_name: 'Trade Radar', tagline: '', about_md: '', version: 1,
      lists: { signals: lp('Signals', 'Signal', false), trends: lp('Trends', 'Trend', false) },
    });
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] } });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    expect(await screen.findByText(/records, each with its evidence/)).toBeInTheDocument();
  });

  it('joins words for the copy', () => {
    expect([joinWords([]), joinWords(['a']), joinWords(['a', 'b']), joinWords(['a', 'b', 'c'])]).toEqual(['', 'a', 'a and b', 'a, b and c']);
  });
});
