// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { GraphScopeBanner } from './GraphScopeBanner';

describe('GraphScopeBanner', () => {
  it('names the node and its connections, with no pager on one page', () => {
    render(<GraphScopeBanner title="Acme" kind="Organization" connections={1} onExit={vi.fn()} />);
    expect(screen.getByTestId('graph-scope-banner').textContent).toContain('Showing relationships for Acme');
    expect(screen.getByText(/1 connection$/)).toBeTruthy();
    expect(screen.queryByTestId('scope-next')).toBeNull();
  });

  it('pages through neighbours and goes back to the overview', () => {
    const onPage = vi.fn();
    const onExit = vi.fn();
    const { rerender } = render(
      <GraphScopeBanner
        title="Hub"
        kind="Signal"
        connections={300}
        page={{ total: 300, offset: 0, pageSize: 149, onPage }}
        onExit={onExit}
      />,
    );
    expect(screen.getByText(/300 connections \u00b7 showing neighbours 1\u2013149 of 300/)).toBeTruthy();
    expect(screen.getByTestId('scope-prev').hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByTestId('scope-next'));
    expect(onPage).toHaveBeenCalledWith(149);
    rerender(
      <GraphScopeBanner
        title="Hub"
        kind="Signal"
        connections={300}
        page={{ total: 300, offset: 298, pageSize: 149, onPage }}
        onExit={onExit}
      />,
    );
    expect(screen.getByText(/neighbours 299\u2013300 of 300/)).toBeTruthy();
    expect(screen.getByTestId('scope-next').hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByTestId('scope-prev'));
    expect(onPage).toHaveBeenLastCalledWith(149);
    fireEvent.click(screen.getByRole('button', { name: 'Back to overview' }));
    expect(onExit).toHaveBeenCalled();
  });

  it('disables the pager while a page loads', () => {
    render(
      <GraphScopeBanner title="Hub" kind="Signal" connections={300} page={{ total: 300, offset: 149, pageSize: 149, onPage: vi.fn(), loading: true }} onExit={vi.fn()} />,
    );
    expect(screen.getByTestId('scope-prev').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTestId('scope-next').hasAttribute('disabled')).toBe(true);
  });
});
