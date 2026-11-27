// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Edge, Neighborhood } from '@/api/types';
import { node } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import GraphPage, { NEIGHBOUR_PAGE } from './GraphPage';
import { canvasType, kindNames, toCanvas } from './graphData';

const edge = (from: string, type: string, to: string): Edge => ({ from, type, to, created_by: 'module:scout', created_at: '2026-10-02T00:00:00Z', confidence: 0.9 });
const org = node({ id: 'organization/1', type: 'organization', category: 'entity', title: 'Acme Motors', summary: '', degree: 2 });
const fact = node({ id: 'fact/1', type: 'fact', category: 'evidence', title: 'Acme ships', summary: 'Shipped in October', degree: 1 });
const sig = node({ id: 'signal/9', type: 'signal', category: 'output', title: 'Price war', degree: 1 });
const nodeButton = (id: string) => screen.getByTestId(`graph-node-${id}`);

describe('GraphPage', () => {
  it('searches, opens a result as relationships, shows details on a click and follows "Show its relationships"', async () => {
    const hood: Neighborhood = { nodes: [org, fact], edges: [edge('fact/1', 'about', 'organization/1'), edge('fact/1', 'about', 'ghost/1')], total: 1, offset: 0 };
    const offsets: string[] = [];
    const { api, calls } = fakeApi({
      'POST /api/graph/search': { items: [{ node: org, score: 0.03, semantic_rank: 1, keyword_rank: 1 }] },
      'GET /api/graph/nodes/organization/1/neighbors': (_b: unknown, url: URL) => {
        offsets.push(url.searchParams.get('offset') ?? '');
        return hood;
      },
      'GET /api/graph/nodes/fact/1/neighbors': { nodes: [fact, org], edges: hood.edges, total: 1, offset: 0 },
    });
    const { container } = render(wrap(<GraphPage />, { api }));
    const w = createWrapper(container);
    w.findMultiselect()!.openDropdown();
    w.findMultiselect()!.selectOptionByValue('organization', { expandToViewport: true });
    const input = screen.getByLabelText('Search the graph');
    fireEvent.change(input, { target: { value: 'acme' } });
    fireEvent.submit(input.closest('form')!);
    fireEvent.click(await screen.findByRole('link', { name: /Acme Motors/ })); // a result is a real link (F2-123)
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ query: 'acme', types: ['organization'], limit: 30 });
    expect(await screen.findByTestId('graph-scope-banner')).toHaveTextContent('Showing relationships for Acme Motors');
    expect(offsets).toEqual(['0']);
    // the link to a node that is not drawn is dropped
    expect(screen.getByTestId('graph-edge-list').querySelectorAll('li')).toHaveLength(1);
    expect(screen.getByTestId('graph-edge-list')).toHaveTextContent('Acme ships about Acme Motors');
    fireEvent.click(nodeButton('fact/1'));
    const details = await screen.findByTestId('graph-node-details');
    expect(details).toHaveTextContent('Shipped in October');
    fireEvent.click(within(details).getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('graph-node-details')).toBeNull();
    fireEvent.click(nodeButton('fact/1'));
    fireEvent.click(within(await screen.findByTestId('graph-node-details')).getByTestId('graph-details-scope'));
    await waitFor(() => expect(calls.some((c) => c.path.includes('fact/1/neighbors'))).toBe(true));
  });

  it('shows an edge on a click, opens a node on a double-click and goes back to the overview', async () => {
    const { api, calls } = fakeApi({
      'GET /api/graph/explore': { nodes: [org, sig], edges: [edge('signal/9', 'about', 'organization/1')] },
      'GET /api/graph/nodes/signal/9/neighbors': () => {
        throw { status: 500, message: 'graph is down' };
      },
    });
    render(wrap(<GraphPage />, { api }));
    expect(await screen.findByText(/The 2 most connected nodes/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('graph-edge-signal/9|about|organization/1'));
    const panel = await screen.findByTestId('graph-edge-details');
    expect(panel).toHaveTextContent('Price war about Acme Motors');
    expect(panel).toHaveTextContent('90%');
    fireEvent.click(within(panel).getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('graph-edge-details')).toBeNull();
    fireEvent.doubleClick(nodeButton('signal/9'));
    expect(await screen.findByText('This node could not be loaded: graph is down')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('signal/9/neighbors'))).toHaveLength(2));
  });

  it('pages through a node with more neighbours than one picture holds', async () => {
    const offsets: string[] = [];
    const { api } = fakeApi({
      'GET /api/graph/nodes/organization/1/neighbors': (_b: unknown, url: URL) => {
        offsets.push(url.searchParams.get('offset') ?? '');
        return { nodes: [{ ...org, degree: 300 }, fact], edges: [edge('fact/1', 'about', 'organization/1')], total: 300, offset: Number(url.searchParams.get('offset')) };
      },
    });
    render(wrap(<GraphPage />, { api, path: '/?focus=organization%2F1' }));
    expect(await screen.findByText(/showing neighbours 1\u2013149 of 300/)).toBeInTheDocument();
    expect(nodeButton('organization/1').dataset.hidden).toBe('299');
    fireEvent.click(screen.getByTestId('scope-next'));
    expect(await screen.findByText(/showing neighbours 150\u2013298 of 300/)).toBeInTheDocument();
    expect(offsets).toEqual(['0', String(NEIGHBOUR_PAGE)]);
    fireEvent.click(screen.getByRole('button', { name: 'Back to overview' }));
    await waitFor(() => expect(screen.queryByTestId('graph-scope-banner')).toBeNull());
  });

  it('keeps the latest neighbourhood when an older answer arrives last', async () => {
    const a = node({ id: 'organization/a', type: 'organization', category: 'entity', title: 'Alpha' });
    const b = node({ id: 'organization/b', type: 'organization', category: 'entity', title: 'Beta' });
    const hoodOf = (n: typeof a, near: string): Neighborhood => ({ nodes: [n, node({ id: `fact/${near}`, type: 'fact', category: 'evidence', title: near })], edges: [] });
    const { api } = fakeApi({ 'POST /api/graph/search': { items: [a, b].map((n) => ({ node: n, score: 1, semantic_rank: 1, keyword_rank: 1 })) } });
    const answers = new Map<string, [(h: Neighborhood) => void, (e: Error) => void]>();
    api.neighbors = (id) => new Promise((resolve, reject) => answers.set(id, [resolve, reject]));
    render(wrap(<GraphPage />, { api }));
    const input = screen.getByLabelText('Search the graph');
    fireEvent.change(input, { target: { value: 'org' } });
    fireEvent.submit(input.closest('form')!);
    fireEvent.click(await screen.findByRole('link', { name: 'Alpha' }));
    fireEvent.click(screen.getByRole('link', { name: 'Beta' }));
    await act(async () => answers.get('organization/b')![0](hoodOf(b, 'Near beta')));
    expect(await screen.findByTestId('graph-node-fact/Near beta')).toBeInTheDocument();
    await act(async () => answers.get('organization/a')![0](hoodOf(a, 'Near alpha')));
    expect(screen.queryByTestId('graph-node-fact/Near alpha')).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Alpha' }));
    await act(async () => answers.get('organization/a')![1](new Error('current failure')));
    expect(await screen.findByText('This node could not be loaded: current failure')).toBeInTheDocument();
  });

  it('reports a failed search, ignores short queries and says when nothing matched', async () => {
    let fail = true;
    const { api, calls } = fakeApi({
      'POST /api/graph/search': () => {
        if (fail) throw { status: 500, message: 'search is down' };
        return { items: [] };
      },
      'GET /api/graph/explore': { nodes: [], edges: [] },
    });
    render(wrap(<GraphPage />, { api }));
    expect(await screen.findByText('The graph is empty. Run a module, or search once it has data.')).toBeInTheDocument();
    const input = screen.getByLabelText('Search the graph');
    fireEvent.change(input, { target: { value: ' a ' } });
    fireEvent.submit(input.closest('form')!);
    expect(screen.getByText('Type at least 2 characters.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
    fireEvent.change(input, { target: { value: 'acme' } });
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByText('search is down')).toBeInTheDocument();
    fail = false;
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByText('No matches.')).toBeInTheDocument();
  });

  it('says a full page of results may not be all of them', async () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ node: node({ id: `fact/${i}`, type: 'fact', title: `Fact ${i}` }), score: 1, semantic_rank: i, keyword_rank: i }));
    const { api } = fakeApi({ 'POST /api/graph/search': { items }, 'GET /api/graph/explore': { nodes: [], edges: [] } });
    render(wrap(<GraphPage />, { api }));
    const input = screen.getByLabelText('Search the graph');
    fireEvent.change(input, { target: { value: 'fact' } });
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByText(/The first 30 matches/)).toBeInTheDocument();
    expect(screen.getByText('Results (30+)')).toBeInTheDocument();
  });

  it('tells a viewer a hidden node is not available, without a Retry', async () => {
    const { api } = fakeApi({ 'GET /api/graph/nodes/gap/1/neighbors': () => { throw { status: 404, message: 'not found' }; } });
    render(wrap(<GraphPage />, { api, me: VIEWER, path: '/?focus=gap%2F1' }));
    expect(await screen.findByText('This node is not available to your role, or it has been deleted.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('says an editor deleted node could not be found, and retries a failed overview', async () => {
    let down = true;
    const { api } = fakeApi({
      'GET /api/graph/nodes/fact/1/neighbors': () => { throw { status: 404, message: 'not found' }; },
      'GET /api/graph/explore': () => {
        if (down) throw { status: 503, message: 'busy' };
        return { nodes: [org], edges: [] };
      },
    });
    const { unmount } = render(wrap(<GraphPage />, { api, path: '/?focus=fact%2F1' }));
    expect(await screen.findByText('This node could not be found. It may have been deleted.')).toBeInTheDocument();
    unmount();
    render(wrap(<GraphPage />, { api }));
    expect(await screen.findByText(/The graph could not be loaded: busy/)).toBeInTheDocument();
    down = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/The 1 most connected nodes/)).toBeInTheDocument();
  });
});

describe('GraphPage: the canvas survives a click', () => {
  it('keeps the same canvas when the details panel opens, so a double-click still lands', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': { nodes: [org, fact], edges: [edge('fact/1', 'about', 'organization/1')] } });
    render(wrap(<GraphPage />, { api }));
    const before = await screen.findByTestId('graph-view');
    fireEvent.click(nodeButton('organization/1'));
    expect(await screen.findByTestId('graph-node-details')).toBeInTheDocument();
    expect(screen.getByTestId('graph-view')).toBe(before);
  });
});

describe('GraphPage: keyword-only search', () => {
  it('says when search by meaning was unavailable', async () => {
    const { api } = fakeApi({
      'GET /api/graph/explore': { nodes: [], edges: [] },
      'POST /api/graph/search': { items: [{ node: org, score: 0.01, semantic_rank: null, keyword_rank: 1 }], degraded: true },
    });
    render(wrap(<GraphPage />, { api }));
    fireEvent.change(screen.getByLabelText('Search the graph'), { target: { value: 'acme' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(/Search by meaning is unavailable right now/)).toBeInTheDocument();
  });
});

describe('GraphPage: an answer without the focus node', () => {
  it('names the node by its id and counts from the page total, or says nothing', async () => {
    const { api } = fakeApi({
      'GET /api/graph/nodes/topic/7/neighbors': { nodes: [fact], edges: [], total: 5, offset: 0 },
      'GET /api/graph/nodes/topic/8/neighbors': { nodes: [fact], edges: [] },
    });
    const first = render(wrap(<GraphPage />, { api, path: '/graph?focus=topic/7' }));
    expect(await screen.findByTestId('graph-scope-banner')).toHaveTextContent('Showing relationships for topic/7');
    expect(screen.getByTestId('graph-scope-banner')).toHaveTextContent('5 connections');
    first.unmount();
    render(wrap(<GraphPage />, { api, path: '/graph?focus=topic/8' }));
    expect(await screen.findByTestId('graph-scope-banner')).toHaveTextContent('0 connections');
  });
});

describe('graph data', () => {
  it('names custom insight kinds and counts the connections a picture leaves out', () => {
    expect(canvasType({ type: 'insight', insight_kind: 'regulatory_change' })).toBe('insight:regulatory_change');
    expect(canvasType({ type: 'insight' })).toBe('insight');
    expect(kindNames({ builtin: [], custom: [{ kind: 'regulatory_change', label: 'Regulatory changes', module: 'm' }] })).toEqual({ 'insight:regulatory_change': 'Regulatory changes' });
    expect(kindNames(undefined)).toEqual({});
    const { nodes, edges } = toCanvas({ nodes: [org, fact], edges: [edge('fact/1', 'about', 'organization/1')] });
    expect(nodes.find((n) => n.id === 'organization/1')?.hiddenNeighbourCount).toBe(1);
    expect(nodes.find((n) => n.id === 'fact/1')?.hiddenNeighbourCount).toBe(0);
    expect(edges[0]?.id).toBe('fact/1|about|organization/1');
  });
});
