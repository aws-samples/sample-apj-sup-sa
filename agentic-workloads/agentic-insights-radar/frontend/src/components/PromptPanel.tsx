// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import FormField from '@cloudscape-design/components/form-field';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';

import { ApiError } from '@/api/client';
import type { Module, ModulePrompt, PromptVersion } from '@/api/types';
import { useSession } from '@/auth/session';
import { describeError, useNotify } from '@/components/Notifications';
import { ago, when } from '@/lib/format';

const PROMPT_PARTS: [keyof PromptVersion, string][] = [
  ['instructions', 'instructions'],
  ['system_prompt', 'template'],
  ['prompt_mode', 'mode'],
  ['skills', 'skills'],
];

/** What a version changed from the one before it ("" for the oldest kept). */
export function changedParts(v: PromptVersion, before: PromptVersion | undefined): string {
  if (!before) return 'Oldest kept version';
  const parts = PROMPT_PARTS.filter(([k]) => JSON.stringify(v[k]) !== JSON.stringify(before[k])).map(([, label]) => label);
  return parts.length ? `Changed: ${parts.join(', ')}` : 'No prompt change';
}

/** The kept versions of the prompt, newest first, each one restorable (a normal, audited save). */
function History({ module, onRestored }: { module: Module; onRestored: () => void }) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const [items, setItems] = React.useState<PromptVersion[] | null>(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(-1);
  React.useEffect(() => {
    let live = true;
    api.promptHistory(module.slug).then(
      (h) => live && setItems(h.items),
      (err: unknown) => live && setError(describeError(err)),
    );
    return () => {
      live = false;
    };
  }, [api, module.slug, module.version]);
  const restore = (v: PromptVersion, i: number) => {
    setBusy(i);
    void api
      .updateModule(
        module.slug,
        { instructions: v.instructions, system_prompt: v.system_prompt, prompt_mode: v.prompt_mode, skills: v.skills },
        module.version,
      )
      .then(
        () => {
          notify('success', `Restored the prompt saved ${ago(v.at)}.`);
          onRestored();
        },
        fail,
      )
      .finally(() => setBusy(-1));
  };
  if (error) return <Alert type="error">{`The history could not be read: ${error}`}</Alert>;
  if (!items) return <StatusIndicator type="loading">Reading the history</StatusIndicator>;
  if (!items.length) return <Box color="text-body-secondary">No earlier prompts yet: each save of the instructions, template, mode or skills is kept here.</Box>;
  return (
    <SpaceBetween size="s">
      {items.map((v, i) => (
        <ExpandableSection
          key={`${v.at}-${v.module_version}`}
          headerText={`${when(v.at)} by ${v.by || 'unknown'}`}
          headerDescription={i === 0 ? `${changedParts(v, items[i + 1])} (current)` : changedParts(v, items[i + 1])}
        >
          <SpaceBetween size="xs">
            <Box fontSize="body-s">{`Mode: ${v.prompt_mode === 'replace' ? 'Replace' : 'Extend'}. Template: ${v.system_prompt ? 'its own' : 'built-in'}. Skills: ${v.skills.join(', ') || 'none'}.`}</Box>
            <div className="air-pre air-prompt">{v.instructions || '(no instructions)'}</div>
            {v.system_prompt && <div className="air-pre air-prompt">{v.system_prompt}</div>}
            {i > 0 && (
              <Button loading={busy === i} disabled={busy >= 0} onClick={() => restore(v, i)}>
                Restore this version
              </Button>
            )}
          </SpaceBetween>
        </ExpandableSection>
      ))}
    </SpaceBetween>
  );
}

/**
 * A module's whole system prompt, for the people who manage modules (CTR's "Agent system prompt" panel).
 * Collapsed by default and read on first open: the template the module starts from (editable, with Reset
 * to default), the shared rules every prompt of its kind ends with (read-only: no edit removes them), and
 * the effective prompt exactly as a run gets it now. A save takes effect on the next run.
 */
export default function PromptPanel({ module, onSaved }: { module: Module; onSaved: () => void }) {
  const { api } = useSession();
  const { fail, notify } = useNotify();
  const [expanded, setExpanded] = React.useState(false);
  const [prompt, setPrompt] = React.useState<ModulePrompt | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    return api.modulePrompt(module.slug).then(
      (p) => {
        setError(null);
        setPrompt(p);
        setDraft(p.override || p.default);
      },
      (err: unknown) => setError(describeError(err)),
    );
  }, [api, module.slug]);

  // Read again after the module changes (instructions, mode, product): the effective prompt follows them.
  React.useEffect(() => {
    if (expanded) void load();
  }, [expanded, load, module.version]);

  const save = (systemPrompt: string, done: string) => {
    setBusy(true);
    void api
      .updateModule(module.slug, { system_prompt: systemPrompt }, module.version)
      .then(
        () => {
          notify('success', done);
          onSaved();
        },
        (err: unknown) => {
          if (err instanceof ApiError && err.status === 409) {
            notify('warning', 'Someone else changed this module while you were editing. It was read again: check the template, then save again.');
            onSaved();
          } else fail(err);
        },
      )
      .finally(() => setBusy(false));
  };

  const custom = Boolean(prompt?.override);
  const dirty = prompt !== null && draft !== (prompt.override || prompt.default);
  const tooLong = prompt !== null && draft.length > prompt.max_override;
  return (
    <ExpandableSection
      variant="container"
      expanded={expanded}
      onChange={({ detail }) => setExpanded(detail.expanded)}
      headerText="Prompt"
      headerDescription="The whole system prompt this module runs with: its template, the rules every prompt ends with, and the result."
    >
      {error ? (
        <Alert type="error" action={<Button onClick={() => void load()}>Try again</Button>}>
          {`The prompt could not be read: ${error}`}
        </Alert>
      ) : !prompt ? (
        <StatusIndicator type="loading">Reading the prompt</StatusIndicator>
      ) : (
        <SpaceBetween size="m">
          <SpaceBetween direction="horizontal" size="s">
            <StatusIndicator type={custom ? 'info' : 'success'}>{custom ? 'Your own template' : 'Built-in template'}</StatusIndicator>
            <Box color="text-body-secondary">
              {prompt.mode === 'replace'
                ? 'Mode: Replace. The instructions are the whole job description; the template is not used.'
                : 'Mode: Extend. The instructions fill the template where it says ${instructions}.'}
            </Box>
          </SpaceBetween>
          <Tabs
            tabs={[
              {
                id: 'effective',
                label: 'Prompt a run gets',
                content: (
                  <SpaceBetween size="xs">
                    <Box fontSize="body-s" color="text-body-secondary">
                      Built from the current configuration: template, instructions, fields, product and the shared rules. Save the
                      configuration first to see a change here.
                    </Box>
                    <div className="air-pre air-prompt">{prompt.effective}</div>
                    <CopyToClipboard
                      variant="button"
                      textToCopy={prompt.effective}
                      copyButtonText="Copy the prompt"
                      copySuccessText="Prompt copied"
                      copyErrorText="The prompt could not be copied"
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'template',
                label: 'Template',
                content: (
                  <SpaceBetween size="s">
                    {prompt.mode === 'replace' && (
                      <Alert type="info">This module is in Replace mode, so its runs do not use the template. Switch the mode to Extend to use it.</Alert>
                    )}
                    <FormField
                      label="Template"
                      description="The job description before the shared rules. Usually left as it is: your focus belongs in Instructions."
                      constraintText={`${draft.length} of ${prompt.max_override} characters.`}
                      errorText={tooLong ? `At most ${prompt.max_override} characters: shorten it before saving.` : undefined}
                    >
                      <Textarea value={draft} rows={22} spellcheck={false} disabled={busy} onChange={({ detail }) => setDraft(detail.value)} />
                    </FormField>
                    {prompt.slots.length > 0 && (
                      <Box fontSize="body-s" color="text-body-secondary">
                        {`Filled in at run time: ${prompt.slots.map((s) => `\${${s.name}} is ${s.means}`).join('; ')}.`}
                      </Box>
                    )}
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button variant="primary" loading={busy} disabled={!dirty || tooLong} onClick={() => save(draft, 'Template saved. It takes effect on the next run.')}>
                        Save template
                      </Button>
                      <Button loading={busy} disabled={!custom} onClick={() => save('', 'Back to the built-in template.')}>
                        Reset to default
                      </Button>
                    </SpaceBetween>
                  </SpaceBetween>
                ),
              },
              { id: 'history', label: 'History', content: <History module={module} onRestored={onSaved} /> },
              {
                id: 'rules',
                label: `Shared rules (${prompt.partials.length})`,
                content: (
                  <SpaceBetween size="s">
                    <Box fontSize="body-s" color="text-body-secondary">
                      Appended to every prompt of this kind, after the template and the instructions. They cannot be edited here, so no
                      change to a module can remove the rules for handling content from the web.
                    </Box>
                    {prompt.partials.map((p) => (
                      <ExpandableSection key={p.name} headerText={p.name}>
                        <div className="air-pre air-prompt">{p.text}</div>
                      </ExpandableSection>
                    ))}
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </SpaceBetween>
      )}
    </ExpandableSection>
  );
}
