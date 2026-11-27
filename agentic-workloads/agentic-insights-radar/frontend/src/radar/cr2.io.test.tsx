// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-2 frontend review: import checks (CR2-F07, CR2-F08), the CSV guard (CR2-F11), transfer (CR2-F10). */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RadarEntry } from '@/api/types';
import RadarPage from '@/pages/RadarPage';
import { radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import { entriesToCsv } from './export';
import { fileIdentities, needsGuard, parseCsv } from './importFile';

const routes = <Routes><Route path="/radars/:slug" element={<RadarPage />} /></Routes>;
afterEach(() => vi.restoreAllMocks());

const entry = (key: string, title: string, over: Partial<RadarEntry> = {}): RadarEntry => ({ key, title, sector: 'Direct', ring: 'Leader', description: '', ...over });

async function choose(container: HTMLElement, body: string, name = 'file.csv') {
  await waitFor(() => expect(createWrapper(container).findFileUpload()).not.toBeNull());
  fireEvent.change(createWrapper(container).findFileUpload()!.findNativeInput().getElement(), { target: { files: [new File([body], name)] } });
  await screen.findByText(/read from the file/);
}

describe('CR2-F11: the CSV guard is the API guard', () => {
  it('guards quotes in front of a formula start, so the cell survives an import', () => {
    expect(needsGuard("'=draft")).toBe(true);
    expect(needsGuard("''+1")).toBe(true);
    expect(needsGuard("'plain")).toBe(false);
    expect(needsGuard("'")).toBe(false);
    const csv = entriesToCsv([entry('k1', "'=draft"), entry('k2', 'it\'s fine')]);
    expect(csv.split('\n')[1]!.split(',')[1]).toBe("''=draft");
    expect(csv.split('\n')[2]!.split(',')[1]).toBe("it's fine");
    // Read back the way the API reads it: one quote comes off and the title is what it was.
    expect([...fileIdentities('csv', csv)!.titles]).toEqual(["'=draft", "it's fine"]);
  });
});

describe('fileIdentities', () => {
  it('reads CSV like the API: quotes, escaped quotes, CRLF, a BOM and header aliases', () => {
    expect(parseCsv('a,"b,""c"""\r\nx,y\rz')).toEqual([['a', 'b,"c"'], ['x', 'y'], ['z']]);
    expect(parseCsv('a,b\n')).toEqual([['a', 'b']]);
    expect(parseCsv('a,x"y"')).toEqual([['a', 'x"y"']]); // a quote inside a field is literal
    const ids = fileIdentities('csv', '\uFEFFId, Name ,ring\nk1, Acme ,Leader\n,  ,x\n,Beta\n')!;
    expect([...ids.titles]).toEqual(['acme', 'beta']);
    expect([...ids.keys]).toEqual(['k1']);
    expect(fileIdentities('csv', '')).toBeNull();
  });

  it('reads JSON lists and {entries}, and gives up on anything else', () => {
    expect([...fileIdentities('json', '[{"title":"Acme","key":"k1"},{"name":7},null,[1],{"title":{"x":1}}]')!.titles]).toEqual(['acme', '7']);
    expect([...fileIdentities('json', '{"entries":[{"Title":"Beta"}]}')!.titles]).toEqual(['beta']);
    expect(fileIdentities('json', '{"rows":[]}')).toBeNull();
    expect(fileIdentities('json', 'null')).toBeNull();
    expect(fileIdentities('json', '{oops')).toBeNull();
  });
});

describe('CR2-F07: Merge is judged on the merged list', () => {
  const report = (rows: number) => ({ rows, unmatched: [], duplicate_rows: [], accepted: true, before: 2, removed: 0 });

  it('does not block on an unmatched entry the file replaces', async () => {
    const entries = [entry('k1', 'Acme', { ring: 'Gone' }), entry('k2', 'Beta')];
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }), 'POST /api/radars/rivals/import': report(1) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, 'title,sector,ring\nACME,Direct,Leader\n');
    expect(screen.queryByText(/already on this radar does not match it/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Import' })).not.toHaveAttribute('aria-disabled');
  });

  it('still blocks on an unmatched entry the Merge keeps, and on a merged list over the limit', async () => {
    const entries = [entry('k1', 'Acme', { ring: 'Gone' }), entry('k2', 'Beta')];
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }), 'POST /api/radars/rivals/import': report(1) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, 'title,sector,ring\nBeta,Direct,Leader\n');
    expect(screen.getByText('1 entry already on this radar does not match it')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('says a Merge would go over 300 entries before Import is pressed', async () => {
    const entries = Array.from({ length: 299 }, (_, i) => entry(`k${i}`, `E${i}`));
    const { api, calls } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }), 'POST /api/radars/rivals/import': report(2) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, '[{"title":"New 1"},{"title":"New 2"}]', 'more.json');
    const button = screen.getByRole('button', { name: 'Import' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    fireEvent.mouseEnter(button);
    expect((await screen.findAllByText(/Merging would leave the radar with 301 entries/)).length).toBeGreaterThan(0);
    // Replace is the whole list, so it is judged on the file alone.
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    expect(screen.getByRole('button', { name: 'Import' })).not.toHaveAttribute('aria-disabled');
    expect(calls.filter((c) => c.path.endsWith('/import'))).toHaveLength(1);
  });

  it('keeps the old rule when the file cannot be read here', async () => {
    const entries = [entry('k1', 'Acme', { ring: 'Gone' })];
    const { api } = fakeApi({ 'GET /api/radars/rivals': radarView({ entries }), 'POST /api/radars/rivals/import': report(1) });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, '{"title":"Acme"}', 'odd.json');
    expect(screen.getByText('1 entry already on this radar does not match it')).toBeInTheDocument();
  });
});

describe('CR2-F08: a shrinking Replace is confirmed once', () => {
  it('sends the confirmation with the Replace the user confirmed', async () => {
    const entries = Array.from({ length: 40 }, (_, i) => entry(`k${i}`, `E${i}`));
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        const body = b as { dry_run?: boolean; allow_empty?: boolean };
        if (body.dry_run) return { rows: 5, unmatched: [], duplicate_rows: [], accepted: true, before: 40, removed: 35 };
        if (!body.allow_empty) throw { status: 400, code: 'validation_failed', message: 'too much', details: { code: 'shrinking_import', before: 40, after: 5, removed: 35 } };
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, 'title\nA\nB\nC\nD\nE\n');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(screen.getByText(/which is 35 entries/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText(/Imported\. A new version was saved\./)).toBeInTheDocument();
    expect(screen.queryByText('Remove 35 entries?')).toBeNull();
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).toMatchObject({ mode: 'replace', allow_empty: true });
  });

  it('sends it for a Replace with as many rows whose titles are renamed (CR3-05)', async () => {
    const entries = Array.from({ length: 5 }, (_, i) => entry(`k${i}`, `E${i}`));
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries }),
      'POST /api/radars/rivals/import': (b: unknown) => {
        const body = b as { dry_run?: boolean; allow_empty?: boolean };
        if (body.dry_run) return { rows: 5, unmatched: [], duplicate_rows: [], accepted: true, before: 5, removed: 5 };
        if (!body.allow_empty) throw { status: 400, code: 'validation_failed', message: 'too much', details: { code: 'shrinking_import', before: 5, after: 5, removed: 5 } };
        return radarView().radar;
      },
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, 'title\nV\nW\nX\nY\nZ\n');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(await screen.findByText(/Imported\. A new version was saved\./)).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/import')).map((c) => (c.body as { allow_empty?: boolean }).allow_empty)).toContain(true);
  });

  it('does not send it for a Replace that keeps most of the radar', async () => {
    const entries = Array.from({ length: 4 }, (_, i) => entry(`k${i}`, `E${i}`));
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView({ entries }),
      'POST /api/radars/rivals/import': (b: unknown) =>
        (b as { dry_run?: boolean }).dry_run ? { rows: 3, unmatched: [], duplicate_rows: [], accepted: true, before: 4, removed: 1 } : radarView().radar,
    });
    const { container } = render(wrap(routes, { api, path: '/radars/rivals?tab=io' }));
    await choose(container, 'title\nA\nB\nC\n');
    createWrapper(container).findRadioGroup()!.findInputByValue('replace')!.click();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    await screen.findByText(/Imported\. A new version was saved\./);
    expect(calls.filter((c) => c.path.endsWith('/import')).pop()!.body).not.toHaveProperty('allow_empty');
  });
});

describe('CR2-F10: Transfer ownership sends only the saved editors', () => {
  it('leaves unsaved editors in the Access draft', async () => {
    const { api, calls } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/users': { items: [{ username: 'u-ed', email: 'ed@example.com', role: 'editor', enabled: true, status: 'CONFIRMED', created_at: '' }] },
      'PUT /api/radars/rivals/access': radarView().radar,
    });
    render(wrap(routes, { api, path: '/radars/rivals?tab=settings' }));
    fireEvent.change(await screen.findByLabelText('Editor email'), { target: { value: 'nobody@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText(/nobody@example.com has no Agentic Insights Radar account yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Transfer ownership' }));
    const select = createWrapper(document.body).findAllSelects().pop()!;
    select.openDropdown();
    select.selectOptionByValue('u-ed', { expandToViewport: true });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Transfer' }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/access'))?.body).toMatchObject({ editors: ['ed@example.com'], owner: { subject: 'u-ed' } }));
    expect(screen.getByText(/nobody@example.com has no Agentic Insights Radar account yet/)).toBeInTheDocument(); // still a draft
  });
});
