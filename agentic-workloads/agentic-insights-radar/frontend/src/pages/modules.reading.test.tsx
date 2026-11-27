// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { module } from '@/test/data';
import { fakeApi, VIEWER, wrap } from '@/test/session';

import { ModulePage } from './ModulesPage';

const signal = (lp: string | null) =>
  module({ slug: 'signal', name: 'Regulatory changes', template: 'signal', output_kind: 'signal', grants: ['kg_read', 'kg_write_output'], progress: { last_processed_at: lp } });
const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /></Routes>;

describe('reading position of a module', () => {
  it('shows how far it has read and reads again from a date or from the beginning', async () => {
    const { api, calls } = fakeApi({
      'GET /api/modules/signal': signal('2026-10-23T17:17:00Z'),
      'GET /api/modules/signal/runs': { items: [], cursor: null },
      'PUT /api/modules/signal/progress': (body: unknown) => body,
    });
    render(wrap(page, { api, path: '/modules/signal' }));
    expect(await screen.findByText(/^Read up to /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    const dialog = createWrapper(document.body).findModal()!;
    expect(dialog.findFooter()!.findAllButtons().find((b) => b.getElement().textContent === 'Read again')!.getElement()).toBeDisabled();
    dialog.findContent().findDatePicker()!.setInputValue('2026/07/01');
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ last_processed_at: '2026-07-01T00:00:00Z' }));
    expect(await screen.findByText('The next run reads again from 2026-07-01.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    fireEvent.click(await screen.findByRole('button', { name: 'From the beginning' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT').at(-1)?.body).toEqual({ last_processed_at: null }));
    expect(await screen.findByText('The next run reads all evidence again.')).toBeInTheDocument();
  });

  it('reads the time out of a recorded cursor and offers no date after today', async () => {
    const { api } = fakeApi({
      'GET /api/modules/signal': signal('2026-10-23T17:17:00.000Z#signal/01a169cc'),
      'GET /api/modules/signal/runs': { items: [], cursor: null },
    });
    render(wrap(page, { api, path: '/modules/signal' }));
    const line = await screen.findByText(/^Read up to /);
    expect(line.textContent).not.toContain('#');
    expect(line.textContent).not.toContain('signal/');
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    const picker = createWrapper(document.body).findModal()!.findContent().findDatePicker()!;
    picker.findOpenCalendarButton().click();
    const calendar = picker.findCalendar()!;
    expect(calendar.findDateAt(1, 1).getElement()).toBeInTheDocument();
    const disabled = calendar.getElement().querySelectorAll('[aria-disabled="true"]');
    const today = new Date();
    const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    // Every day after today in this month is disabled (none when today is the month's last day).
    expect(disabled.length).toBeGreaterThanOrEqual(lastDay - today.getDate());
  });

  it('says when nothing has been read, closes on Cancel, and reports a refusal', async () => {
    const { api, calls } = fakeApi({
      'GET /api/modules/signal': signal(null),
      'GET /api/modules/signal/runs': { items: [], cursor: null },
      'PUT /api/modules/signal/progress': () => {
        throw { status: 409, message: 'the module is running' };
      },
    });
    render(wrap(page, { api, path: '/modules/signal' }));
    expect(await screen.findByText('Has not read any evidence yet.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    createWrapper(document.body).findModal()!.findDismissButton().click();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Read again from a date' }));
    fireEvent.click(await screen.findByRole('button', { name: 'From the beginning' }));
    expect(await screen.findByText(/the module is running/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });

  it('is not shown to a viewer, nor for a module that reads sources', async () => {
    const { api } = fakeApi({ 'GET /api/modules/signal': signal(null), 'GET /api/modules/scout': module(), 'GET /api/modules/scout/sources': { items: [] }, 'GET /api/modules/scout/runs': { items: [], cursor: null } });
    const { unmount } = render(wrap(page, { api, path: '/modules/signal', me: VIEWER }));
    expect(await screen.findByRole('heading', { name: 'Regulatory changes' })).toBeInTheDocument();
    expect(screen.queryByText('Reading position')).toBeNull();
    unmount();
    render(wrap(page, { api, path: '/modules/scout' }));
    expect(await screen.findByRole('heading', { name: 'Scout' })).toBeInTheDocument();
    expect(screen.queryByText('Reading position')).toBeNull();
  });
});
