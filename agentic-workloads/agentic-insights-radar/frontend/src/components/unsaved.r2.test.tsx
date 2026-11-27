// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-022: unsaved work on pages other than the radar is guarded too. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdminPage from '@/pages/AdminPage';
import { ModulePage, ModulesPage } from '@/pages/ModulesPage';
import { ProductPage, ProductsPage } from '@/pages/ProductsPage';
import { module as module_, product } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

afterEach(() => vi.restoreAllMocks());

function Go({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      elsewhere
    </button>
  );
}

const unload = () => {
  const e = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
};

describe('a module\'s configuration', () => {
  const custom = module_({ slug: 'pw', name: 'Pricing watch', template: 'custom', builtin: false, grants: ['kg_read', 'kg_write_output'] });
  const routes = (
    <>
      <Routes>
        <Route path="/modules/:slug" element={<ModulePage />} />
        <Route path="/modules" element={<div>module list</div>} />
      </Routes>
      <Go to="/modules" />
    </>
  );

  it('asks before leaving with a rewritten instruction, and warns the browser', async () => {
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
    });
    render(wrap(routes, { api, path: '/modules/pw' }));
    await screen.findByRole('heading', { name: 'Pricing watch' });
    expect(unload()).toBe(false);
    fireEvent.change(screen.getByDisplayValue('Agent platforms'), { target: { value: 'Watch pricing pages only' } });
    expect(unload()).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'elsewhere' }));
    expect(await screen.findByText('Leave without saving?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stay on this page' }));
    expect(screen.getByDisplayValue('Watch pricing pages only')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'elsewhere' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Leave and lose changes' }));
    expect(await screen.findByText('module list')).toBeInTheDocument();
  });

  it('lets go once the change is saved, even when the version does not move', async () => {
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
      'PATCH /api/modules/pw': custom,
    });
    render(wrap(routes, { api, path: '/modules/pw' }));
    await screen.findByRole('heading', { name: 'Pricing watch' });
    fireEvent.change(screen.getByDisplayValue('Agent platforms'), { target: { value: 'Watch pricing pages only' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(unload()).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'elsewhere' }));
    expect(await screen.findByText('module list')).toBeInTheDocument();
  });
});

describe('the product Edit dialog', () => {
  it('asks before Esc or a click on the backdrop throws the profile away', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const dialog = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;
    fireEvent.change(screen.getByDisplayValue('DeskPro'), { target: { value: 'DeskPro Plus' } });
    dialog().findDismissButton().click();
    expect(await screen.findByText('Discard these changes?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('DeskPro Plus')).toBeInTheDocument();
    dialog().findDismissButton().click();
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByDisplayValue('DeskPro Plus')).toBeNull());
  });

  it('closes straight away when nothing was changed', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { api, path: '/products/p1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(await screen.findByDisplayValue('DeskPro')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByDisplayValue('DeskPro')).toBeNull());
  });
});

describe('the Create module and Add product dialogs', () => {
  const dialog = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

  it('keeps a half-written module when the user chooses Keep editing', async () => {
    const { api } = fakeApi({ 'GET /api/modules': { items: [] } });
    render(wrap(<ModulesPage />, { api, path: '/modules' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    fireEvent.change(screen.getByPlaceholderText('Pricing watch'), { target: { value: 'Pricing watch' } });
    dialog().findDismissButton().click();
    expect(await screen.findByText('Discard this module?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('Pricing watch')).toBeInTheDocument();
    dialog().findDismissButton().click();
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByDisplayValue('Pricing watch')).toBeNull());
  });

  it('closes Create module straight away when nothing was typed', async () => {
    const { api } = fakeApi({ 'GET /api/modules': { items: [] } });
    render(wrap(<ModulesPage />, { api, path: '/modules' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create module' }));
    expect(screen.getByPlaceholderText('Pricing watch')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByPlaceholderText('Pricing watch')).toBeNull());
  });

  it('keeps a half-written product when the user chooses Keep editing', async () => {
    const { api } = fakeApi({ 'GET /api/products': { items: [] } });
    render(wrap(<ProductsPage />, { api, path: '/products' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add product' }));
    fireEvent.change(document.querySelector('[role="dialog"] input')!, { target: { value: 'DeskPro' } });
    dialog().findDismissButton().click();
    expect(await screen.findByText('Discard this product?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('DeskPro')).toBeInTheDocument();
  });
});

describe('the chat budget', () => {
  it('asks before leaving with an unsaved limit', async () => {
    const { api } = fakeApi({
      'GET /api/chat/settings': { daily_tokens: 1000, per_user_daily_tokens: 100 },
      'GET /api/chat/status': { shared: { remaining: 900 }, mine: { remaining: 90 } },
      'GET /api/andon': { genai_enabled: true },
    });
    render(
      wrap(
        <>
          <AdminPage />
          <Go to="/" />
        </>,
        { api, path: '/admin?tab=ai' },
      ),
    );
    const field = await screen.findByLabelText('Everyone, per day');
    fireEvent.change(field, { target: { value: '2000' } });
    fireEvent.click(screen.getByRole('button', { name: 'elsewhere' }));
    expect(await screen.findByText(/The chat budget has unsaved changes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stay on this page' }));
    expect(screen.getByDisplayValue('2000')).toBeInTheDocument();
  });
});
