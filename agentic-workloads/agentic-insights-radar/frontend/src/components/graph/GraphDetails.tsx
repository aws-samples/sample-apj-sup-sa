// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

import type { Edge, GraphBrief } from '@/api/types';
import NodeLink from '@/components/NodeLink';
import { ago, clip } from '@/lib/format';

import { edgeFamilyColor, humanizeEdge, legendDisplayName } from './GraphView';

/**
 * The graph's side panel (ported from the CoP Tech Radar): a click on a node shows what it is without
 * leaving the graph, with a way to show its relationships or open its page; a click on an edge shows
 * the relationship, which way it points, and who added it when.
 */
export function NodeDetails({
  node,
  names,
  onScope,
  onClose,
}: {
  node: GraphBrief;
  names?: Record<string, string>;
  onScope: (id: string) => void;
  onClose: () => void;
}): React.ReactElement {
  return (
    <Container
      data-testid="graph-node-details"
      header={
        <Header
          variant="h3"
          actions={<Button variant="icon" iconName="close" ariaLabel="Close details" onClick={onClose} />}
          description={<Badge>{legendDisplayName(node.type, names)}</Badge>}
        >
          {node.title}
        </Header>
      }
    >
      <SpaceBetween size="s">
        {node.summary && <Box>{clip(node.summary, 400)}</Box>}
        <KeyValuePairs
          columns={2}
          items={[
            { label: 'Connections', value: String(node.degree) },
            { label: 'Updated', value: ago(node.updated_at) },
          ]}
        />
        <SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => onScope(node.id)} data-testid="graph-details-scope">
            Show its relationships
          </Button>
          <NodeLink node={{ id: node.id, title: 'Open page', evidence_url: node.evidence_url }} />
        </SpaceBetween>
      </SpaceBetween>
    </Container>
  );
}

export function EdgeDetails({
  edge,
  from,
  to,
  onClose,
}: {
  edge: Edge;
  from?: GraphBrief;
  to?: GraphBrief;
  onClose: () => void;
}): React.ReactElement {
  const confidence = typeof edge.confidence === 'number' ? `${Math.round(edge.confidence * 100)}%` : 'not given';
  return (
    <Container
      data-testid="graph-edge-details"
      header={
        <Header
          variant="h3"
          actions={<Button variant="icon" iconName="close" ariaLabel="Close details" onClick={onClose} />}
        >
          <span
            aria-hidden="true"
            data-testid="edge-swatch"
            style={{ display: 'inline-block', width: 18, height: 4, marginRight: 8, verticalAlign: 'middle', background: edgeFamilyColor(edge.type) }}
          />
          {humanizeEdge(edge.type)}
        </Header>
      }
    >
      <SpaceBetween size="s">
        <Box>
          <NodeLink node={from ?? { id: edge.from, title: edge.from }} showSource={false} /> {humanizeEdge(edge.type)}{' '}
          <NodeLink node={to ?? { id: edge.to, title: edge.to }} showSource={false} />
        </Box>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Added by', value: edge.created_by || 'unknown' },
            { label: 'Added', value: ago(edge.created_at) },
            { label: 'Confidence', value: confidence },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}
