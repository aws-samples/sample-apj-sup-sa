// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { MAX_MCP_SERVERS, mcpProblem, withMcpGrant } from '@/components/McpServersEditor';
import type { Module } from '@/api/types';
import { module } from '@/test/data';
import { ADMIN, fakeApi, wrap } from '@/test/session';

import { ModulePage } from './ModulesPage';

const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;
const routes = (m: Module, patch: () => unknown = () => m) => ({
  [`GET /api/modules/${m.slug}`]: m,
  [`GET /api/modules/${m.slug}/runs`]: { items: [], cursor: null },
  'GET /api/models': { items: [] },
  'GET /api/products': { items: [] },
  [`GET /api/modules/${m.slug}/sources`]: { items: [] },
  [`PATCH /api/modules/${m.slug}`]: patch,
});
const field = (container: HTMLElement, label: string) =>
  createWrapper(container).findAllFormFields().find((f) => f.findLabel()?.getElement().textContent === label)!.findControl()!;

describe('MCP servers on a module', () => {
  it('checks a list the way the API does, and keeps the grant in step with it', () => {
    const ok = { name: 'laws', url: 'https://mcp.example.com/mcp' };
    expect(mcpProblem([ok])).toBe('');
    expect(mcpProblem([{ ...ok, name: 'bad name' }])).toMatch(/1 to 40 letters/);
    expect(mcpProblem([ok, ok])).toBe('Two servers are named "laws".');
    expect(mcpProblem([{ ...ok, url: 'http://mcp.example.com' }])).toMatch(/public https/);
    expect(mcpProblem([{ name: 'x' }])).toMatch(/public https/);
    expect(mcpProblem(Array.from({ length: MAX_MCP_SERVERS + 1 }, (_, i) => ({ ...ok, name: `s${i}` })))).toBe('At most 10 MCP servers.');
    expect(withMcpGrant(['kg_read'], [ok])).toEqual(['kg_read', 'mcp']);
    expect(withMcpGrant(['kg_read', 'mcp'], [])).toEqual(['kg_read']);
  });

  it('attaches a server to a built-in module and sends the grant with it', async () => {
    const scout = module({ slug: 'scout', name: 'Scout', template: 'scout', builtin: true, grants: ['kg_read', 'sources'], version: 2 });
    const { api, calls } = fakeApi(routes(scout));
    const { container } = render(wrap(page, { api, path: '/modules/scout', me: ADMIN }));
    expect(await screen.findByText('No MCP servers attached.')).toBeInTheDocument();
    field(container, 'Name').findInput()!.setInputValue('laws');
    field(container, 'Address').findInput()!.setInputValue('http://insecure.example.com');
    expect(screen.getByText(/"laws": enter a public https/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Attach' })).toBeDisabled();
    field(container, 'Address').findInput()!.setInputValue('https://mcp.example.com/mcp');
    fireEvent.click(screen.getByRole('button', { name: 'Attach' }));
    expect(screen.getByText('https://mcp.example.com/mcp')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({
        grants: ['kg_read', 'sources', 'mcp'],
        mcp_servers: [{ name: 'laws', url: 'https://mcp.example.com/mcp' }],
      }),
    );
  });

  it('removes a server from a custom module, and the grant goes with the last one', async () => {
    const custom = module({
      slug: 'watch', name: 'Watch', template: 'custom', builtin: false, grants: ['kg_read', 'mcp'], version: 4,
      mcp_servers: [{ name: 'laws', url: 'https://mcp.example.com/mcp' }],
    });
    const { api, calls } = fakeApi(routes(custom));
    render(wrap(page, { api, path: '/modules/watch', me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove laws' }));
    expect(screen.getByText('No MCP servers attached.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ grants: ['kg_read'], mcp_servers: [] }));
  });

  it('leaves a built-in module\'s tools alone when its servers did not change', async () => {
    const scout = module({ slug: 'scout', name: 'Scout', template: 'scout', builtin: true, grants: ['kg_read', 'sources'], version: 2 });
    const { api, calls } = fakeApi(routes(scout));
    const { container } = render(wrap(page, { api, path: '/modules/scout', me: ADMIN }));
    await screen.findByText('No MCP servers attached.');
    createWrapper(container).findTextarea()!.setTextareaValue('Focus on Acme.');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toBeDefined());
    const body = calls.find((c) => c.method === 'PATCH')!.body as Record<string, unknown>;
    expect('grants' in body || 'mcp_servers' in body).toBe(false);
  });

  it('says why a module that reads product knowledge cannot have MCP servers', async () => {
    const gap = module({ slug: 'gap', name: 'Gaps', template: 'gap', builtin: true, output_kind: 'gap', grants: ['kg_read', 'product_knowledge'] });
    render(wrap(page, { api: fakeApi(routes(gap)).api, path: '/modules/gap', me: ADMIN }));
    expect(await screen.findByText(/searches product knowledge, so it cannot call MCP servers/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Attach' })).toBeNull();
  });
});
