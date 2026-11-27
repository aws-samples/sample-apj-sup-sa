// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { Edge, GraphBrief } from '@/api/types';

import { EdgeDetails, NodeDetails } from './GraphDetails';


const node: GraphBrief = {
  id: 'signal/1',
  type: 'insight:regulatory_change',
  category: 'output',
  title: 'Indonesia: TKDN raised',
  summary: 'The local-content threshold rises to 40%.',
  degree: 4,
  updated_at: '2026-10-20T00:00:00Z',
};

describe('NodeDetails', () => {
  it('shows the node, uses the profile name for its kind, and scopes or closes', () => {
    const onScope = vi.fn();
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <NodeDetails node={node} names={{ 'insight:regulatory_change': 'Regulatory change' }} onScope={onScope} onClose={onClose} />
      </MemoryRouter>,
    );
    const panel = screen.getByTestId('graph-node-details');
    expect(panel.textContent).toContain('Indonesia: TKDN raised');
    expect(panel.textContent).toContain('Regulatory change');
    expect(panel.textContent).toContain('40%');
    expect(screen.getByText('Open page').closest('a')?.getAttribute('href')).toBe('/insights/nodes/signal/1');
    fireEvent.click(screen.getByTestId('graph-details-scope'));
    expect(onScope).toHaveBeenCalledWith('signal/1');
    fireEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('leaves the summary out when there is none', () => {
    render(
      <MemoryRouter>
        <NodeDetails node={{ ...node, summary: '' }} onScope={vi.fn()} onClose={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('graph-node-details').textContent).not.toContain('40%');
  });
});

describe('EdgeDetails', () => {
  const edge: Edge = { from: 'signal/1', type: 'about', to: 'organization/2', created_by: 'scout', created_at: '2026-10-21T00:00:00Z', confidence: 0.8 };

  it('shows both ends, the relationship and who added it', () => {
    render(
      <MemoryRouter>
        <EdgeDetails edge={edge} from={node} to={{ ...node, id: 'organization/2', title: 'Komdigi', type: 'organization' }} onClose={vi.fn()} />
      </MemoryRouter>,
    );
    const panel = screen.getByTestId('graph-edge-details');
    expect(panel.textContent).toContain('Indonesia: TKDN raised about Komdigi');
    expect(panel.textContent).toContain('scout');
    expect(panel.textContent).toContain('80%');
    expect((screen.getByTestId('edge-swatch') as HTMLElement).style.background).not.toBe('');
  });

  it('falls back to ids and says when confidence or author are missing', () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <EdgeDetails edge={{ ...edge, created_by: '', confidence: undefined }} onClose={onClose} />
      </MemoryRouter>,
    );
    const panel = screen.getByTestId('graph-edge-details');
    expect(panel.textContent).toContain('signal/1 about organization/2');
    expect(panel.textContent).toContain('unknown');
    expect(panel.textContent).toContain('not given');
    fireEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(onClose).toHaveBeenCalled();
  });
});
