// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { AttributeFieldDef } from '@/api/types';
import { node } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import InsightsPage, { FieldSummary } from './InsightsPage';

const SCHEMA: AttributeFieldDef[] = [
  { key: 'urgency', label: 'Urgency', type: 'enum', required: true, filter: true, sort: true, help: '',
    values: [{ value: 'act_now', label: 'Act now', rank: 1 }, { value: 'plan', label: 'Plan', rank: 2 }, { value: 'watch', label: 'Watch', rank: 3 }] },
  { key: 'countries', label: 'Countries', type: 'string_list', required: false, filter: true, sort: false, help: '',
    values: Array.from({ length: 10 }, (_, i) => ({ value: `C${i}`, label: `Country ${i}`, rank: 0 })) },
  { key: 'effective_date', label: 'Effective date', type: 'date', required: false, filter: false, sort: true, help: '' },
  { key: 'tags', label: 'Tags', type: 'string_list', required: false, filter: true, sort: false, help: '' },
];

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const routes = (
  <>
    <Routes>
      <Route path="/insights/:kind" element={<InsightsPage />} />
    </Routes>
    <Routes>
      <Route path="*" element={<Where />} />
    </Routes>
  </>
);
const where = () => screen.getByTestId('where').textContent;

describe('typed fields on a list', () => {
  it('shows badges, list values and the first date on a card', () => {
    const n = node({ attributes: { urgency: 'act_now', countries: ['C1', 'X9'], effective_date: '2026-11-01', tags: [] } });
    render(<FieldSummary node={n} schema={SCHEMA} />);
    expect(screen.getByText('Urgency: Act now')).toBeInTheDocument();
    expect(screen.getByText('Countries: Country 1, X9')).toBeInTheDocument();
    // With how far it is from today, which moves: only the prefix is fixed.
    expect(screen.getByText(/^Effective date: 2026-11-01 \((today|in \d+ days?|\d+ days? ago)\)$/)).toBeInTheDocument();
  });

  it('colours by rank and draws nothing without fields', () => {
    const { container, rerender } = render(<FieldSummary node={node({ attributes: { urgency: 'plan' } })} schema={SCHEMA} />);
    expect(screen.getByText('Urgency: Plan')).toBeInTheDocument();
    rerender(<FieldSummary node={node({ attributes: { urgency: 'watch' } })} schema={SCHEMA} />);
    expect(screen.getByText('Urgency: Watch')).toBeInTheDocument();
    rerender(<FieldSummary node={node({ attributes: { urgency: 'gone' } })} schema={[{ ...SCHEMA[0]!, values: undefined }]} />);
    expect(screen.getByText('Urgency: gone')).toBeInTheDocument();
    rerender(<FieldSummary node={node({ attributes: undefined as unknown as Record<string, unknown> })} schema={SCHEMA} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('draws filters and a field sort from the schema and keeps them in the address', async () => {
    const page = { items: [node({ attributes: { urgency: 'plan' } })], cursor: 'next', sort: 'newest', page_size: 25, schema: SCHEMA, total: 30, truncated: true };
    const { api, fetcher } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?f.urgency=plan&sort=attr:effective_date&order=desc' }));
    await screen.findByText('Urgency: Plan');
    const urls = (fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) => new URL(String(c[0]), 'https://x'));
    const asked = urls.find((u) => u.pathname === '/api/insights/signals')!;
    expect(asked.searchParams.get('filter.urgency')).toBe('plan');
    expect(asked.searchParams.get('sort')).toBe('attr:effective_date');
    expect(asked.searchParams.get('order')).toBe('desc');
    expect(screen.getByText('(1 of 30 shown)')).toBeInTheDocument();
    expect(screen.getByText(/read the newest 2,000 records only/)).toBeInTheDocument();
    const w = createWrapper(container);
    // The filters are drawn once the first page has told the page its fields.
    await waitFor(() => expect(w.findAllMultiselects()).toHaveLength(2)); // tags has no values: no filter for it
    const multis = w.findAllMultiselects();
    multis[1]!.openDropdown();
    multis[1]!.selectOptionByValue('C3');
    await waitFor(() => expect(where()).toContain('f.countries=C3'));
    expect(where()).toContain('f.urgency=plan');
    // Clearing a filter's last value drops it from the address.
    await waitFor(() => expect(w.findAllMultiselects()).toHaveLength(2));
    const urgency = w.findAllMultiselects()[0]!;
    urgency.openDropdown();
    urgency.selectOptionByValue('plan');
    await waitFor(() => expect(where()).not.toContain('f.urgency'));
    const select = w.findSelect()!;
    select.openDropdown();
    select.selectOptionByValue('attr:urgency|asc');
    await waitFor(() => expect(where()).toContain('sort=attr%3Aurgency'));
    expect(where()).not.toContain('order=');
    w.findSegmentedControl()!.findSegments()[0]!.click(); // back to Newest
    await waitFor(() => expect(where()).not.toContain('sort='));
  });

  it('names a field sort from a stale link by its key', async () => {
    const page = { items: [], cursor: null, sort: 'newest', page_size: 25, schema: SCHEMA };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page });
    render(wrap(routes, { api, path: '/insights/signals?sort=attr:retired&order=desc' }));
    expect(await screen.findByText('retired (descending)')).toBeInTheDocument();
  });

  it('leaves an empty filter out of the request', async () => {
    const { api, fetcher } = fakeApi({ 'GET /api/insights/signals': { items: [], cursor: null, sort: 'newest', page_size: 25 } });
    await api.insights('signals', { filters: { urgency: [], countries: ['ID'] } });
    const url = new URL(String((fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls[0]![0]), 'https://x');
    expect(url.searchParams.has('filter.urgency')).toBe(false);
    expect(url.searchParams.get('filter.countries')).toBe('ID');
  });

  it('says a filtered list is empty because of the filters', async () => {
    const page = { items: [], cursor: null, sort: 'newest', page_size: 25, schema: SCHEMA };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page });
    render(wrap(routes, { api, path: '/insights/signals?f.urgency=act_now' }));
    expect(await screen.findByText(/or clear a filter/)).toBeInTheDocument();
  });

  it('counts a page without a total as shown so far, and hides the field sort during a search', async () => {
    const page = { items: [node()], cursor: 'next', sort: 'relevance', page_size: 25, schema: SCHEMA };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?q=rules&sort=attr:urgency' }));
    expect(await screen.findByText('(1 shown)')).toBeInTheDocument();
    await waitFor(() => expect(createWrapper(container).findAllMultiselects()).toHaveLength(2));
    expect(container.querySelector('[aria-label="Sort by a field"]')).toBeNull();
  });

  it('shows filters that have no control as removable tokens, and clears them all', async () => {
    const page = { items: [node()], cursor: null, sort: 'newest', page_size: 25, schema: SCHEMA };
    const { api } = fakeApi({ 'GET /api/nav': { builtin: [], custom: [] }, 'GET /api/insights/signals': page });
    const { container } = render(wrap(routes, { api, path: '/insights/signals?f.tags=acme&f.urgency=plan,stale&f.gone=x' }));
    await screen.findByText('Tags: acme');
    // The multiselects draw token groups too; the loose filters are the one holding "Tags: acme".
    const tokens = createWrapper(container)
      .findAllTokenGroups()
      .find((g) => g.getElement().textContent?.includes('Tags: acme'))!;
    expect(tokens.findTokens().map((t) => t.getElement().textContent)).toEqual(['Tags: acme', 'Urgency: stale', 'gone: x']); // a field no longer named keeps its key
    tokens.findToken(2)!.findDismiss().click(); // 1-based: the second token
    await waitFor(() => expect(where()).toBe('/insights/signals?f.tags=acme&f.urgency=plan&f.gone=x'));
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(where()).toBe('/insights/signals'));
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });

  it('offers a way out of a link whose filter the list no longer has', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': (_b: unknown, url: URL) => {
        if (url.searchParams.has('filter.stage')) throw { status: 400, message: "'stage' is not a filterable field of this list" };
        return { items: [], cursor: null, sort: 'newest', page_size: 25, schema: SCHEMA };
      },
    });
    render(wrap(routes, { api, path: '/insights/signals?f.stage=adopted' }));
    const way = await screen.findByRole('button', { name: 'Clear filters and sort' });
    // No field list arrived, so no filter is drawn as an unlabelled token.
    expect(screen.queryByText('stage: adopted')).toBeNull();
    fireEvent.click(way);
    await waitFor(() => expect(where()).toBe('/insights/signals'));
    expect(screen.queryByRole('button', { name: 'Clear filters and sort' })).toBeNull();
  });
});

describe('closed gaps and retired ideas (CTR HC-130)', () => {
  it('brings them back with a switch that lives in the address and survives a re-sort', async () => {
    const seen: string[] = [];
    const page = (_b: unknown, url: URL) => {
      seen.push(url.searchParams.get('include_closed') ?? '');
      return { items: [node({ id: 'gap/1', type: 'gap', title: url.searchParams.get('include_closed') ? 'Closed one' : 'Open one' })], cursor: null, sort: 'newest', page_size: 25 };
    };
    const { api } = fakeApi({ 'GET /api/insights/gaps': page, 'GET /api/insights/signals': page });
    render(wrap(routes, { api, path: '/insights/gaps' }));
    expect(await screen.findByText('Open one')).toBeInTheDocument();
    const toggle = createWrapper(document.body).findToggle()!;
    expect(toggle.getElement()).toHaveTextContent('Show closed gaps');
    toggle.findNativeInput().click();
    expect(await screen.findByText('Closed one')).toBeInTheDocument();
    expect(where()).toBe('/insights/gaps?ended=1');
    createWrapper(document.body).findSegmentedControl()!.findSegments()[2]!.click(); // Title A to Z
    await waitFor(() => expect(where()).toBe('/insights/gaps?sort=title&ended=1'));
    createWrapper(document.body).findToggle()!.findNativeInput().click();
    await waitFor(() => expect(where()).toBe('/insights/gaps?sort=title'));
    expect(seen).toContain('true');
  });

  it('offers retired ideas on the content list and nothing on the others', async () => {
    const empty = { items: [], cursor: null, sort: 'newest', page_size: 25 };
    const { api } = fakeApi({ 'GET /api/insights/content': empty, 'GET /api/insights/signals': empty });
    const first = render(wrap(routes, { api, path: '/insights/content?ended=1' }));
    expect(await screen.findByText('Show retired ideas')).toBeInTheDocument();
    first.unmount();
    render(wrap(routes, { api, path: '/insights/signals?ended=1' }));
    await screen.findByText(/Search these entries|Nothing published/);
    expect(createWrapper(document.body).findToggle()).toBeNull();
  });
});
