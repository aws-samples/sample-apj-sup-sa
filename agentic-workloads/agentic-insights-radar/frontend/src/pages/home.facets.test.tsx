// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { AttributeFieldDef, GraphNode } from '@/api/types';
import { VIEWER, fakeApi, wrap } from '@/test/session';

import HomePage from './HomePage';

const node = (id: string, title: string, attributes: Record<string, unknown> = {}): GraphNode => ({
  id,
  type: 'signal',
  category: 'output',
  title,
  summary: '',
  description: '',
  attributes,
  aliases: [],
  created_by: 'module:signal',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  version: 1,
  degree: 0,
});

const schema: AttributeFieldDef[] = [
  // Fields that are neither filters nor dates come first: they alone would not turn the counts on.
  { key: 'note', label: 'Note', type: 'text', required: false, filter: false, sort: false, help: '' },
  {
    key: 'urgency', label: 'Urgency', type: 'enum', required: true, filter: true, sort: true, help: '',
    values: [{ value: 'act_now', label: 'Act now', rank: 1 }, { value: 'plan', label: 'Plan', rank: 2 }],
  },
  { key: 'countries', label: 'Countries', type: 'string_list', required: false, filter: true, sort: false, help: '' },
  { key: 'effective_date', label: 'Takes effect', type: 'date', required: false, filter: false, sort: true, help: '' },
];
const empty = { items: [], cursor: null, sort: 'newest', page_size: 25 };
const base = {
  'GET /api/nav': { builtin: [], custom: [] },
  // An older server sends no schema at all.
  'GET /api/insights/trends': empty,
};
const changes = { ...empty, items: [node('signal:1', 'Indonesia: new rule')], schema };

describe('Overview of a list with typed fields', () => {
  it('counts each value with a link to the filtered list and lists what is coming up', async () => {
    const { api, calls } = fakeApi({
      ...base,
      'GET /api/insights/signals': changes,
      'GET /api/insights/signals/facets': {
        total: 3,
        scanned: 2000,
        counts: { urgency: { act_now: 2, plan: 0 }, countries: { ID: 2, VN: 1, 'Asia, Pacific': 1 }, gone: { x: 1 } },
        upcoming: [
          { field: 'effective_date', date: '2026-11-01', node: node('signal:2', 'Vietnam: licence rule') },
          { field: 'old_date', date: '2026-11-02', node: node('signal:3', 'India: filing rule') },
        ],
        days: 90,
        truncated: true,
        counts_cut: ['countries'],
        schema,
      },
    });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    const actNow = await screen.findByRole('link', { name: 'Act now 2' });
    expect(actNow.getAttribute('href')).toBe('/insights/signals?f.urgency=act_now');
    expect(screen.queryByText(/Plan 0/)).toBeNull(); // nothing to click through to
    expect(screen.getByRole('link', { name: 'ID 2' }).getAttribute('href')).toBe('/insights/signals?f.countries=ID');
    expect(screen.getByText('By countries (the 3 most common):')).toBeInTheDocument();
    expect(screen.getByText('By gone:')).toBeInTheDocument(); // a field the schema no longer names keeps its key
    expect(screen.getByText('Coming up in the next 90 days')).toBeInTheDocument();
    expect(screen.getByText(/2026-11-01 \u00b7 takes effect:/u)).toBeInTheDocument();
    expect(screen.getByText('Vietnam: licence rule')).toBeInTheDocument();
    expect(screen.getByText(/2026-11-02 \u00b7 old_date:/u)).toBeInTheDocument(); // a field no longer named keeps its key
    expect(screen.getByText('Counted from the first 2000 records.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Asia, Pacific 1' })).toBeNull(); // a filter cannot take a comma
    expect(screen.getByText(/Asia, Pacific 1/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/api/insights/trends/facets')).toEqual([]); // no typed fields: no call
  });

  it('shows a failed count and nothing when there is nothing to count', async () => {
    const { api } = fakeApi({
      ...base,
      'GET /api/insights/signals': changes,
      'GET /api/insights/signals/facets': () => {
        throw { status: 500, message: 'counting failed' };
      },
    });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    expect(await screen.findByText('counting failed')).toBeInTheDocument();
  });

  it('draws no count row whose values are all zero and no upcoming block when none are due', async () => {
    const { api } = fakeApi({
      ...base,
      'GET /api/insights/signals': changes,
      'GET /api/insights/signals/facets': {
        total: 1, scanned: 1, counts: { urgency: { act_now: 0 }, countries: { ID: 1 } }, upcoming: [], days: 30,
        truncated: false, schema,
      },
    });
    render(wrap(<HomePage />, { api, me: VIEWER }));
    await screen.findByText('Indonesia: new rule');
    // Wait for the counts themselves: a row that has values proves the all-zero one was looked at and
    // left out (scan cycle 63, where an earlier pass of this check could run before the counts arrived).
    expect(await screen.findByText('By countries:')).toBeInTheDocument();
    expect(screen.queryByText(/By urgency/)).toBeNull();
    expect(screen.queryByText(/Coming up/)).toBeNull();
  });
});
