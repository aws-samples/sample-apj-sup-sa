// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { ModuleRun } from '@/api/types';
import { module } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import { ModulePage } from './ModulesPage';

const run = (over: Partial<ModuleRun>): ModuleRun => ({
  module: 'signal', run_id: 'r1', status: 'running', trigger: 'manual', started_at: '2026-10-25T01:00:00Z',
  requested_by: 'u', finished_at: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
  weighted_tokens: 0, nodes_written: 0, summary: '', error: null, ...over,
});
const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;
const signal = module({ slug: 'signal', name: 'Signals', template: 'signal', output_kind: 'signal' });

describe('cancelling a run (CTR HC-024)', () => {
  it('asks first, then stops the run and shows it cancelled', async () => {
    let cancelled = false;
    const { api, calls } = fakeApi({
      'GET /api/modules/signal': signal,
      'GET /api/modules/signal/runs': () => ({
        items: [run({ status: cancelled ? 'cancelled' : 'running' }), run({ run_id: 'r0', status: 'succeeded' })],
        cursor: null,
      }),
      'POST /api/modules/signal/runs/r1/cancel': () => {
        cancelled = true;
        return run({ status: 'cancelled', summary: 'Cancelled by a@example.com.' });
      },
    });
    render(wrap(page, { api, path: '/modules/signal' }));
    const buttons = await screen.findAllByRole('button', { name: /^Cancel the run started/ });
    expect(buttons).toHaveLength(1); // only the running one
    fireEvent.click(buttons[0]!);
    const dialog = createWrapper(document.body).findModal()!;
    expect(dialog.getElement()).toHaveTextContent('stay counted');
    fireEvent.click(screen.getByRole('button', { name: 'Keep it running' }));
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /^Cancel the run started/ }));
    createWrapper(document.body).findModal()!.findDismissButton().click(); // the dialog's own close
    await waitFor(() => expect(createWrapper(document.body).findModal()).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /^Cancel the run started/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the run' }));
    expect(await screen.findByText('The run was cancelled.')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/r1/cancel'))).toBe(true);
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Cancel the run started/ })).toBeNull());
  });

  it('reports a refusal and reads the runs again', async () => {
    let reads = 0;
    const { api } = fakeApi({
      'GET /api/modules/signal': signal,
      'GET /api/modules/signal/runs': () => {
        reads += 1;
        return { items: [run({ status: reads > 1 ? 'succeeded' : 'running' })], cursor: null };
      },
      'POST /api/modules/signal/runs/r1/cancel': () => {
        throw { status: 409, code: 'conflict', message: 'run r1 has already finished (succeeded)' };
      },
    });
    render(wrap(page, { api, path: '/modules/signal' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Cancel the run started/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the run' }));
    expect(await screen.findByText(/has already finished/)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThan(1));
  });
});
