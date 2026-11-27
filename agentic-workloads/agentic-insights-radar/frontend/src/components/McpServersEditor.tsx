// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import React from 'react';

import type { McpServer } from '@/api/types';

/** Backend limits (modules/model.py): at most 10 servers, names of 1-40 letters, digits, - or _. */
export const MAX_MCP_SERVERS = 10;
const NAME = /^[A-Za-z0-9_-]{1,40}$/;

/** What is wrong with the server list, or ''. The API checks the address again (no private or local hosts). */
export function mcpProblem(servers: McpServer[]): string {
  if (servers.length > MAX_MCP_SERVERS) return `At most ${MAX_MCP_SERVERS} MCP servers.`;
  const seen = new Set<string>();
  for (const s of servers) {
    if (!NAME.test(s.name)) return `Name "${s.name}": 1 to 40 letters, digits, - or _.`;
    if (seen.has(s.name)) return `Two servers are named "${s.name}".`;
    seen.add(s.name);
    if (!/^https:\/\/[^\s/]+\.[^\s]+$/.test(s.url ?? '')) return `"${s.name}": enter a public https:// address.`;
  }
  return '';
}

/** The tools a module may use, with the MCP grant following whether any server is attached. */
export function withMcpGrant(grants: string[], servers: McpServer[]): string[] {
  const rest = grants.filter((g) => g !== 'mcp');
  return servers.length ? [...rest, 'mcp'] : rest;
}

/**
 * The MCP servers a module's runs may call (CTR's per-agent MCP list). Their tools reach the model as
 * mcp_<server>_<tool>, fenced as untrusted output like every web result.
 */
export default function McpServersEditor({
  servers,
  onChange,
  blocked,
}: {
  servers: McpServer[];
  onChange: (next: McpServer[]) => void;
  /** Why this module may not have MCP servers, or ''. */
  blocked: string;
}) {
  const [name, setName] = React.useState('');
  const [url, setUrl] = React.useState('');
  const draft = { name: name.trim(), url: url.trim() };
  const addProblem = name || url ? mcpProblem([...servers, draft]) : '';
  const add = () => {
    onChange([...servers, draft]);
    setName('');
    setUrl('');
  };
  return (
    <FormField
      label="MCP servers"
      description="Remote MCP servers whose tools this module may call, as mcp_<name>_<tool>. Their answers are treated as untrusted web content."
      errorText={mcpProblem(servers) || undefined}
    >
      <SpaceBetween size="xs">
        {blocked && <Box color="text-status-inactive">{blocked}</Box>}
        <Table wrapLines
          variant="embedded"
          items={servers}
          trackBy="name"
          empty={<Box color="text-body-secondary">No MCP servers attached.</Box>}
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: (s) => s.name },
            { id: 'url', header: 'Address', cell: (s) => s.url },
            {
              id: 'remove',
              header: '',
              cell: (s) => (
                <Button variant="inline-link" ariaLabel={`Remove ${s.name}`} onClick={() => onChange(servers.filter((x) => x.name !== s.name))}>
                  Remove
                </Button>
              ),
            },
          ]}
        />
        {!blocked && servers.length < MAX_MCP_SERVERS && (
          <SpaceBetween direction="horizontal" size="xs" alignItems="end">
            <FormField label="Name">
              <Input value={name} placeholder="laws" onChange={({ detail }) => setName(detail.value)} />
            </FormField>
            <FormField label="Address" errorText={addProblem || undefined}>
              <Input value={url} placeholder="https://mcp.example.com/mcp" onChange={({ detail }) => setUrl(detail.value)} />
            </FormField>
            <Button disabled={!name.trim() || !url.trim() || Boolean(addProblem)} onClick={add}>
              Attach
            </Button>
          </SpaceBetween>
        )}
      </SpaceBetween>
    </FormField>
  );
}
