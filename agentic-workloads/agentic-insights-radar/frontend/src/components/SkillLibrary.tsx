// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FileUpload from '@cloudscape-design/components/file-upload';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';

import { ApiError } from '@/api/client';
import type { Skill, SkillSummary } from '@/api/types';
import { useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { TableLoadError } from '@/components/ErrorState';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';
import { ago, when } from '@/lib/format';

export const UPLOAD_STRINGS = {
  uploadButtonText: () => 'Choose file',
  dropzoneText: () => 'Drop a .md file to upload',
  removeFileAriaLabel: () => 'Remove file',
  limitShowFewer: 'Show fewer files',
  limitShowMore: 'Show more files',
  errorIconAriaLabel: 'Error',
};

/** Backend limit (modules/skills.py MAX_SKILL_BYTES). */
export const MAX_SKILL_BYTES = 64 * 1024;
const NAME = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

/** The skill's name from its frontmatter, or why it has none the API would accept. */
export function skillName(content: string): { name: string; problem: string } {
  if (new TextEncoder().encode(content).length > MAX_SKILL_BYTES) return { name: '', problem: 'A skill is at most 64 KiB.' };
  const head = /^---\r?\n([\s\S]*?)\r?\n---/.exec(content);
  if (!head) return { name: '', problem: 'A skill starts with frontmatter: ---, a name: line, a description: line, ---.' };
  // Read line by line, not with a pattern built from the key (semgrep detect-non-literal-regexp).
  const field = (key: string) => {
    const line = (head[1] as string).split(/\r?\n/).find((l) => l.startsWith(`${key}:`));
    return line?.slice(key.length + 1).trim().replace(/^['"]|['"]$/g, '') ?? '';
  };
  const name = field('name');
  if (!NAME.test(name)) return { name: '', problem: 'The name must be 1-64 lower-case letters, digits or hyphens.' };
  if (!field('description')) return { name, problem: 'Add a description: the model decides from it when to load the skill.' };
  return { name, problem: '' };
}

/** Saves a skill as a file the browser downloads. */
export function download(skill: Pick<Skill, 'name' | 'content'>): void {
  const url = URL.createObjectURL(new Blob([skill.content], { type: 'text/markdown' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${skill.name}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Opens a skill, reads it, and saves an edit or a new one. */
function SkillEditor({ name, onClose, onSaved }: { name: string | null; onClose: () => void; onSaved: () => void }) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const existing = useAsync(() => (name ? api.skill(name) : Promise.resolve(null)), [api, name]);
  const [text, setText] = React.useState<string | null>(null);
  const [files, setFiles] = React.useState<File[]>([]);
  const [saving, setSaving] = React.useState(false);
  const content = text ?? existing.data?.content ?? '';
  const parsed = skillName(content);
  const renamed = name !== null && parsed.name !== '' && parsed.name !== name;
  const problem = !content.trim() ? '' : renamed ? `The frontmatter names "${parsed.name}": keep it "${name}", or add it as a new skill.` : parsed.problem;
  const pick = (picked: File[]) => {
    setFiles(picked);
    const file = picked[0];
    if (file) void file.text().then(setText);
  };
  const save = () => {
    setSaving(true);
    void api
      .saveSkill(parsed.name, content, existing.data?.version ?? 0)
      .then(
        () => {
          notify('success', `Skill ${parsed.name} saved. Runs read it from now on.`);
          onSaved();
        },
        (err: unknown) => {
          if (err instanceof ApiError && err.status === 409) notify('warning', 'This skill was changed or added by someone else. Close, reopen and try again.');
          else fail(err);
        },
      )
      .finally(() => setSaving(false));
  };
  return (
    <Modal
      visible
      size="large"
      onDismiss={onClose}
      header={name ? `Skill: ${name}` : 'Add a skill'}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onClose}>Cancel</Button>
            <Button variant="primary" loading={saving} disabled={!content.trim() || Boolean(problem) || text === null} onClick={save}>
              Save skill
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {!name && (
          <FormField label="Upload a skill (.md)" description="Or paste it below.">
            <FileUpload
              value={files}
              onChange={({ detail }) => pick(detail.value)}
              accept=".md,.markdown,text/markdown"
              constraintText="One Markdown file that starts with name and description frontmatter, at most 64 KiB."
              i18nStrings={UPLOAD_STRINGS}
            />
          </FormField>
        )}
        <FormField
          label="Skill"
          description="Frontmatter (name, description), then the instructions. The description is what a run sees until it loads the skill."
          errorText={problem || undefined}
          constraintText={`${new TextEncoder().encode(content).length} of ${MAX_SKILL_BYTES} bytes.`}
        >
          <Textarea
            value={content}
            rows={22}
            spellcheck={false}
            placeholder={'---\nname: my-skill\ndescription: When to use it.\n---\n\nThe instructions.'}
            onChange={({ detail }) => setText(detail.value)}
          />
        </FormField>
      </SpaceBetween>
    </Modal>
  );
}

/**
 * The skill library (CTR's agent skills): Markdown instructions a module's runs load when a task needs
 * them. Attach them to modules on each module's page.
 */
export default function SkillLibrary() {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const list = useAsync(() => api.skills(), [api]);
  const [editing, setEditing] = React.useState<string | null | undefined>(undefined);
  const [deleting, setDeleting] = React.useState<SkillSummary | null>(null);
  const done = () => {
    setEditing(undefined);
    list.reload();
  };
  if (list.error && !list.data) return <TableLoadError error={list.error} what="skills" onRetry={list.reload} />;
  return (
    <>
      <Table wrapLines
        loading={list.loading && !list.data}
        loadingText="Reading the skill library"
        items={list.data?.items ?? []}
        trackBy="name"
        header={
          <Header
            counter={list.data ? `(${list.data.items.length})` : undefined}
            description="Instructions a module's runs load when a task needs them. Attach them to modules on each module's page."
            actions={<Button onClick={() => setEditing(null)}>Add a skill</Button>}
          >
            Skill library
          </Header>
        }
        empty={<Box textAlign="center" color="text-body-secondary">No skills yet.</Box>}
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            cell: (s) => (
              <SpaceBetween direction="horizontal" size="xxs">
                <span>{s.name}</span>
                {s.default && <Badge>{s.edited ? 'Default, edited' : 'Default'}</Badge>}
              </SpaceBetween>
            ),
          },
          { id: 'description', header: 'When it is used', cell: (s) => s.description },
          { id: 'modules', header: 'Modules', cell: (s) => (s.modules?.length ? s.modules.join(', ') : '-') },
          { id: 'size', header: 'Size', cell: (s) => `${Math.ceil(s.size / 1024)} KiB` },
          { id: 'updated', header: 'Changed', cell: (s) => <span title={when(s.updated_at)}>{ago(s.updated_at)}</span> },
          {
            id: 'actions',
            header: '',
            cell: (s) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" onClick={() => setEditing(s.name)}>Open</Button>
                <Button variant="inline-link" onClick={() => void api.skill(s.name).then(download, fail)}>Download</Button>
                <Button
                  variant="inline-link"
                  disabled={s.default || Boolean(s.modules?.length)}
                  disabledReason={s.default ? 'A default skill is kept: detach it from modules instead.' : 'Detach it from its modules first.'}
                  onClick={() => setDeleting(s)}
                >
                  Delete
                </Button>
              </SpaceBetween>
            ),
          },
        ]}
      />
      {editing !== undefined && <SkillEditor key={editing ?? 'new'} name={editing} onClose={() => setEditing(undefined)} onSaved={done} />}
      {deleting && (
        <ConfirmDelete
          header={`Delete the skill ${deleting.name}?`}
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteSkill(deleting.name).then(() => {
              notify('success', `Skill ${deleting.name} deleted.`);
              setDeleting(null);
              list.reload();
            }, fail)
          }
        >
          It leaves the library for good. No module uses it.
        </ConfirmDelete>
      )}
    </>
  );
}
