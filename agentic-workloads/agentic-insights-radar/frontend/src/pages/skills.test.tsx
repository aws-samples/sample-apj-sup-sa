// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { Skill, SkillSummary } from '@/api/types';
import SkillLibrary, { download, MAX_SKILL_BYTES, skillName, UPLOAD_STRINGS } from '@/components/SkillLibrary';
import { module } from '@/test/data';
import { ADMIN, fakeApi, wrap } from '@/test/session';

import { ModulePage } from './ModulesPage';

const TEXT = '---\nname: rules-of-origin\ndescription: How to read a rules-of-origin notice.\n---\n\nRead the HS chapter first.\n';
const summary = (over: Partial<SkillSummary> = {}): SkillSummary => ({
  name: 'rules-of-origin', description: 'How to read a rules-of-origin notice.', size: 2048, default: false, edited: false,
  updated_by: 'u', updated_at: '2026-10-30T08:00:00Z', version: 1, modules: [], ...over,
});
const HUMANIZER = summary({ name: 'humanizer', description: 'Rewrite AI-sounding prose.', default: true, modules: ['signal'] });
const SLOP = summary({ name: 'no-ai-slop', description: 'Cut AI slop.', default: true, edited: true });
const skill = (s: SkillSummary, content = TEXT): Skill => ({ ...s, content });
const modal = () => createWrapper(document.body).findAllModals().find((m) => m.isVisible())!;

describe('a skill read from its frontmatter', () => {
  it('names itself, or says what is missing', () => {
    expect(skillName(TEXT)).toEqual({ name: 'rules-of-origin', problem: '' });
    expect(skillName("---\nname: 'quoted'\ndescription: \"d\"\n---\nx").name).toBe('quoted');
    expect(skillName('# no frontmatter').problem).toMatch(/starts with frontmatter/);
    expect(skillName('---\nname: Bad Name\ndescription: d\n---\nx').problem).toMatch(/lower-case/);
    expect(skillName('---\nname: ok\n---\nx')).toEqual({ name: 'ok', problem: expect.stringMatching(/Add a description/) });
    expect(skillName(TEXT + 'x'.repeat(MAX_SKILL_BYTES)).problem).toBe('A skill is at most 64 KiB.');
  });

  it('words the upload box', () => {
    expect([UPLOAD_STRINGS.uploadButtonText(), UPLOAD_STRINGS.dropzoneText(), UPLOAD_STRINGS.removeFileAriaLabel()]).toEqual([
      'Choose file', 'Drop a .md file to upload', 'Remove file',
    ]);
  });

  it('downloads as a Markdown file', () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const make = vi.fn(() => 'blob:x');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: make, revokeObjectURL: revoke });
    download({ name: 'rules-of-origin', content: TEXT });
    expect(click).toHaveBeenCalled();
    expect(revoke).toHaveBeenCalledWith('blob:x');
    click.mockRestore();
  });
});

describe('the skill library', () => {
  it('lists the skills, which modules use them, and keeps the defaults and the ones in use from deletion', async () => {
    const { api } = fakeApi({ 'GET /api/skills': { items: [HUMANIZER, SLOP, summary()] } });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    expect(await screen.findByText('humanizer')).toBeInTheDocument();
    expect(screen.getByText('Default, edited')).toBeInTheDocument();
    expect(screen.getByText('Default')).toBeInTheDocument();
    expect(screen.getByText('signal')).toBeInTheDocument();
    const deletes = screen.getAllByRole('button', { name: 'Delete' });
    expect(deletes[0]).toHaveAttribute('aria-disabled', 'true');
    expect(deletes[2]).not.toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('(3)')).toBeInTheDocument();
  });

  it('adds a skill from a pasted file, and refuses one without a valid frontmatter', async () => {
    const { api, calls } = fakeApi({ 'GET /api/skills': { items: [] }, 'PUT /api/skills/rules-of-origin': () => skill(summary()) });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add a skill' }));
    const area = modal().findContent().findTextarea()!;
    area.setTextareaValue('# nothing');
    expect(screen.getByText(/starts with frontmatter/)).toBeInTheDocument();
    expect(within(modal().getElement()).getByRole('button', { name: 'Save skill' })).toBeDisabled();
    area.setTextareaValue(TEXT);
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Save skill' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ content: TEXT, version: 0 }));
    expect(await screen.findByText('Skill rules-of-origin saved. Runs read it from now on.')).toBeInTheDocument();
  });

  it('adds a skill from an uploaded .md file', async () => {
    const { api, calls } = fakeApi({ 'GET /api/skills': { items: [] }, 'PUT /api/skills/rules-of-origin': () => skill(summary()) });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add a skill' }));
    const file = new File([TEXT], 'rules-of-origin.md', { type: 'text/markdown' });
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(TEXT) });
    const input = modal().findContent().findFileUpload()!.findNativeInput().getElement();
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(modal().findContent().findTextarea()!.getTextareaValue()).toBe(TEXT));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Save skill' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
  });

  it('keeps the text when the picked file is removed', async () => {
    const { api } = fakeApi({ 'GET /api/skills': { items: [] } });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add a skill' }));
    const file = new File([TEXT], 'rules-of-origin.md', { type: 'text/markdown' });
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(TEXT) });
    fireEvent.change(modal().findContent().findFileUpload()!.findNativeInput().getElement(), { target: { files: [file] } });
    await waitFor(() => expect(modal().findContent().findTextarea()!.getTextareaValue()).toBe(TEXT));
    fireEvent.click(screen.getByRole('button', { name: 'Remove file' }));
    expect(modal().findContent().findTextarea()!.getTextareaValue()).toBe(TEXT);
  });

  it('opens a skill, edits it with its version, and will not rename it', async () => {
    const { api, calls } = fakeApi({
      'GET /api/skills': { items: [summary({ version: 3 })] },
      'GET /api/skills/rules-of-origin': skill(summary({ version: 3 })),
      'PUT /api/skills/rules-of-origin': () => skill(summary({ version: 4 })),
    });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open' }));
    await waitFor(() => expect(modal().findContent().findTextarea()!.getTextareaValue()).toBe(TEXT));
    expect(within(modal().getElement()).getByRole('button', { name: 'Save skill' })).toBeDisabled(); // nothing changed yet
    modal().findContent().findTextarea()!.setTextareaValue(TEXT.replace('name: rules-of-origin', 'name: other'));
    expect(screen.getByText(/keep it "rules-of-origin", or add it as a new skill/)).toBeInTheDocument();
    modal().findContent().findTextarea()!.setTextareaValue(TEXT + 'Then the annex.\n');
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Save skill' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ content: TEXT + 'Then the annex.\n', version: 3 }));
  });

  it('says when someone else saved first, or the save failed, and deletes after asking', async () => {
    const { api, calls } = fakeApi({
      'GET /api/skills': { items: [summary()] },
      'PUT /api/skills/rules-of-origin': () => { throw { status: 409, message: 'conflict' }; },
      'DELETE /api/skills/rules-of-origin': {},
    });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add a skill' }));
    modal().findContent().findTextarea()!.setTextareaValue(TEXT);
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Save skill' }));
    expect(await screen.findByText(/changed or added by someone else/)).toBeInTheDocument();
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(createWrapper(document.body).findAllModals().some((m) => m.isVisible())).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(await screen.findByText('Skill rules-of-origin deleted.')).toBeInTheDocument();
  });

  it('reports a failed save, download or read', async () => {
    let reads = 0;
    const { api } = fakeApi({
      'GET /api/skills': () => {
        reads += 1;
        if (reads === 1) throw { status: 500, message: 'library down' };
        return { items: [summary()] };
      },
      'GET /api/skills/rules-of-origin': () => { throw { status: 500, message: 'read failed' }; },
      'PUT /api/skills/rules-of-origin': () => { throw { status: 500, message: 'table busy' }; },
    });
    render(wrap(<SkillLibrary />, { api, me: ADMIN }));
    expect(await screen.findByText(/library down/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Try again|Retry/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(await screen.findByText('read failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add a skill' }));
    modal().findContent().findTextarea()!.setTextareaValue(TEXT);
    fireEvent.click(within(modal().getElement()).getByRole('button', { name: 'Save skill' }));
    expect(await screen.findByText('table busy')).toBeInTheDocument();
  });
});

describe('skills on a module', () => {
  const page = <Routes><Route path="/modules/:slug" element={<ModulePage />} /><Route path="/admin" element={<div>admin page</div>} /></Routes>;
  const signal = module({ slug: 'signal', name: 'Signals', template: 'signal', output_kind: 'signal', builtin: true, grants: ['kg_read'], skills: ['humanizer'], version: 5 });
  const routes = (library: unknown = { items: [HUMANIZER, summary()] }) => ({
    'GET /api/modules/signal': signal,
    'GET /api/modules/signal/runs': { items: [], cursor: null },
    'GET /api/models': { items: [] },
    'GET /api/products': { items: [] },
    'GET /api/skills': library,
    'PATCH /api/modules/signal': () => signal,
  });

  it('attaches a skill from the library and saves the list', async () => {
    const { api, calls } = fakeApi(routes());
    const { container } = render(wrap(page, { api, path: '/modules/signal', me: ADMIN }));
    await screen.findByText('Skills');
    const field = createWrapper(container).findAllFormFields().find((f) => f.findLabel()?.getElement().textContent === 'Skills')!;
    const select = field.findControl()!.findMultiselect()!;
    select.openDropdown();
    select.selectOptionByValue('rules-of-origin', { expandToViewport: true });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ skills: ['humanizer', 'rules-of-origin'] }));
    fireEvent.click(screen.getByText('Skill library'));
    expect(await screen.findByText('admin page')).toBeInTheDocument();
  });

  it('refuses more than ten skills on a module', async () => {
    const many = { ...signal, skills: Array.from({ length: 11 }, (_, i) => `s${i}`) };
    const { api } = fakeApi({ ...routes(), 'GET /api/modules/signal': many });
    render(wrap(page, { api, path: '/modules/signal', me: ADMIN }));
    expect(await screen.findByText('At most 10 skills on a module.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('says when the library could not be read', async () => {
    const { api } = fakeApi(routes(() => { throw { status: 500, message: 'down' }; }));
    const { container } = render(wrap(page, { api, path: '/modules/signal', me: ADMIN }));
    await screen.findByText('Skills');
    const field = createWrapper(container).findAllFormFields().find((f) => f.findLabel()?.getElement().textContent === 'Skills')!;
    field.findControl()!.findMultiselect()!.openDropdown();
    expect(await screen.findByText('The skill library could not be read: down')).toBeInTheDocument();
  });
});
