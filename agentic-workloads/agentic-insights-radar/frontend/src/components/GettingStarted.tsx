// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { joinWords, KIND_LABELS } from '@/lib/format';
import { appName, listVisible, radarsHelp, sectionVisible } from '@/lib/profile';
import React from 'react';
import { useNavigate } from 'react-router-dom';

import { can, seesPrivate, useSession } from '@/auth/session';
import { type AsyncState, useAsync } from '@/hooks/useAsync';

/** Remembered per browser and per account: one person hiding it does not hide it for the next. */
export const DISMISS_KEY = 'air.getting-started.dismissed';

export const dismissKey = (email: string) => `${DISMISS_KEY}:${email.toLowerCase()}`;

interface Step {
  title: string;
  why: string;
  /** null while it is being checked, or when the check failed. */
  done: boolean | null;
  /** The check itself failed: not the same as "not done yet". */
  failed?: boolean;
  action: string;
  /** The link once the step is done: back to where it was done. */
  again: string;
  href: string;
}

function StepRow({ step, n }: { step: Step; n: number }) {
  const navigate = useNavigate();
  return (
    <div className="air-step">
      {/* A failed check used to look exactly like a step not done, so an administrator was told to
          add a product they had already added. */}
      <StatusIndicator type={step.failed ? 'warning' : step.done ? 'success' : 'pending'}>
        <b>
          {n}. {step.title}
        </b>
      </StatusIndicator>
      {step.failed && (
        <Box color="text-status-warning" fontSize="body-s" padding={{ left: 'l' }}>
          This step could not be checked, so it may already be done.
        </Box>
      )}
      <Box color="text-body-secondary" fontSize="body-s" padding={{ left: 'l' }}>
        {step.why}
      </Box>
      <Box padding={{ left: 'l' }}>
        <Button variant={step.done ? 'link' : 'normal'} onClick={() => navigate(step.href)}>
          {step.done ? step.again : step.action}
        </Button>
      </Box>
    </div>
  );
}

/** The line about Ask, which is left out while an administrator has paused AI features. */
function AskLine({ children }: { children: React.ReactNode }) {
  const { config } = useSession();
  return config.genai_enabled ? <>{children}</> : null;
}

/**
 * The setup steps and how many are done. Held in a hook so the checklist can collapse itself once
 * everything is done; `active` is false for everyone who cannot follow the steps, and then nothing
 * is requested.
 */
/** The built-in lists this deployment shows, by its own names: the copy below never names a hidden one. */
const shownLists = (kinds: string[]): string[] =>
  kinds.filter((k) => listVisible(k)).map((k) => KIND_LABELS[k] as string);

function useSetupSteps(active: boolean): { steps: Step[]; done: number; known: boolean } {
  const { api } = useSession();
  const products = useAsync(() => (active && sectionVisible('products') ? api.products() : Promise.resolve(null)), [api, active]);
  const modules = useAsync(() => (active ? api.modules() : Promise.resolve(null)), [api, active]);
  const radars = useAsync(() => (active && sectionVisible('radars') ? api.radars() : Promise.resolve(null)), [api, active]);
  const graph = useAsync(() => (active ? api.explore(1) : Promise.resolve(null)), [api, active]);
  // The step says to start with Scout and give it sources, so that is what it is ticked for: with
  // only the Radar curator on, nothing collects any evidence.
  const scoutOn = modules.data?.items.some((m) => m.slug === 'scout' && m.enabled) ?? false;
  const sources = useAsync(() => (scoutOn ? api.sources('scout') : Promise.resolve(null)), [api, scoutOn]);
  const seen = <T,>(state: AsyncState<T | null>, test: (data: T) => boolean): boolean | null =>
    state.data === undefined || state.data === null ? null : test(state.data);
  const productLists = shownLists(['gaps', 'content']);
  const builtOnScout = shownLists(['signals', 'trends', 'gaps']);
  const steps: Step[] = [
    // Only when a list that reads the product profile is shown here.
    ...(productLists.length === 0 || !sectionVisible('products') ? [] : [{
      title: 'Describe your product',
      why: `The ${joinWords(productLists)} ${productLists.length > 1 ? 'modules compare' : 'module compares'} what the market says with what your product does.`,
      done: seen(products, (d) => d.items.length > 0),
      failed: Boolean(products.error),
      action: 'Add a product',
      again: 'Open Products',
      href: '/products?create=1',
    }]),
    {
      title: 'Turn on the modules',
      why: `Built-in modules ship turned off. Start with Scout: give it sources (feeds, sites), then Run now.${builtOnScout.length ? ` ${joinWords(builtOnScout)} build on what Scout records.` : ''}`,
      done: modules.data === undefined || modules.data === null ? null : scoutOn ? (sources.data?.items.length ?? 0) > 0 : false,
      failed: Boolean(modules.error || sources.error),
      action: 'Open Modules',
      again: 'Open Modules',
      href: '/modules',
    },
    ...(!sectionVisible('radars') ? [] : [{
      title: 'Create a radar',
      why: `${radarsHelp()} Start from a template${sectionVisible('sample_data') ? ', or load the sample radar to see one filled in' : ''}.`,
      done: seen(radars, (d) => d.items.length > 0),
      failed: Boolean(radars.error),
      action: 'Create a radar',
      again: 'Open Radars',
      href: '/radars?create=1',
    }]),
    {
      title: 'Look at the knowledge graph',
      why: `Everything the modules record is linked here.${sectionVisible('sample_data') ? ' No data yet? Administration > Sample data loads a small example.' : ''}`,
      done: seen(graph, (d) => d.nodes.length > 0),
      failed: Boolean(graph.error),
      action: 'Open the graph',
      again: 'Open the graph',
      href: '/graph',
    },
  ];
  // The count waits until every check has answered (and is left out if one failed), so it never
  // shows "0 of 4" for a setup that is in fact done.
  return { steps, known: steps.every((x) => x.done !== null), done: steps.filter((x) => x.done).length };
}

/** Setup checklist for administrators; it ticks itself from what already exists. */
function AdminSteps({ steps, done, known }: { steps: Step[]; done: number; known: boolean }) {
  return (
    <SpaceBetween size="m">
      <Box>
        {known && `${done} of ${steps.length} done. `}
        <AskLine>
          You can also ask questions at any time with <b>Ask</b> at the top right; answers cite the entries they use.
        </AskLine>
      </Box>
      {steps.map((s, i) => (
        <StepRow key={s.title} step={s} n={i + 1} />
      ))}
    </SpaceBetween>
  );
}

/** What a viewer or editor can do, in a few lines. */
function ReaderSteps() {
  const { me } = useSession();
  const navigate = useNavigate();
  const editor = can(me, 'radar.create');
  return (
    <SpaceBetween size="s">
      <Box>
        <b>Insights</b> (left menu) are what the AI modules found:{' '}
        {joinWords(shownLists(seesPrivate(me) ? ['signals', 'trends', 'gaps', 'content'] : ['signals', 'trends']).map((w) => w.toLowerCase())) || 'records'}, each with its evidence.
        {can(me, 'feedback.give') && ' Vote Useful or Not useful to tune what they surface.'}
      </Box>
      {sectionVisible('radars') && (
      <Box>
        <b>Radars:</b> {radarsHelp()}{' '}
        {editor ? (
          <>
            You can create your own from a template and edit its entries.{' '}
            <Button variant="inline-link" onClick={() => navigate('/radars?create=1')}>
              Create a radar
            </Button>
          </>
        ) : (
          <Button variant="inline-link" onClick={() => navigate('/radars')}>
            Open Radars
          </Button>
        )}
      </Box>
      )}
      {can(me, 'content.draft') && seesPrivate(me) && listVisible('content') && listVisible('drafts') && (
        <Box>
          <b>Content ideas</b> can be turned into a first draft: open one and choose Write a draft.
        </Box>
      )}
      <AskLine>
        <Box>
          <b>Ask</b> (top right) answers questions from the knowledge graph, with sources.
        </Box>
      </AskLine>
    </SpaceBetween>
  );
}

/** Where storage access throws (blocked site data, some private modes), the choice is kept in memory. */
export function readDismissed(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

export function writeDismissed(key: string, value: boolean): void {
  try {
    if (value) localStorage.setItem(key, '1');
    else localStorage.removeItem(key);
  } catch {
    // kept in this page's state instead; the next visit shows the checklist again
  }
}

export default function GettingStarted() {
  const { me } = useSession();
  const setup = useSetupSteps(can(me, 'module.manage'));
  const key = me ? dismissKey(me.email) : '';
  const [dismissed, setDismissed] = React.useState<{ key: string; value: boolean } | null>(null);
  const hidden = dismissed?.key === key ? dismissed.value : readDismissed(key);
  const toggle = (value: boolean) => {
    writeDismissed(key, value);
    setDismissed({ key, value });
  };
  // Until the profile has loaded we do not know which version to show.
  if (!me) return null;
  const allDone = can(me, 'module.manage') && setup.known && setup.done === setup.steps.length;
  if (hidden) {
    return (
      <Box textAlign="right">
        <Button variant="inline-link" onClick={() => toggle(false)}>
          Show getting started
        </Button>
      </Box>
    );
  }
  if (allDone) {
    // Everything is set up: one line, above the insights the administrator came for.
    return (
      <Box textAlign="right">
        <StatusIndicator type="success">Setup is done.</StatusIndicator>{' '}
        <Button variant="inline-link" onClick={() => toggle(true)}>
          Hide getting started
        </Button>
      </Box>
    );
  }
  return (
    <Container
      header={
        <Header
          variant="h2"
          // Not the tagline: the page header right above already shows it.
          description={`${appName()} reads the sources you give it into a knowledge graph, then tells you what is changing and what to do about it.`}
          actions={<Button onClick={() => toggle(true)}>Hide</Button>}
        >
          Getting started
        </Header>
      }
    >
      {can(me, 'module.manage') ? <AdminSteps steps={setup.steps} done={setup.done} known={setup.known} /> : <ReaderSteps />}
    </Container>
  );
}
