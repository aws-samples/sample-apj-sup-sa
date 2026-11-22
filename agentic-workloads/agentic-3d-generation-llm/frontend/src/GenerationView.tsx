// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from 'react';
import { fmtCost, fmtSeconds, fmtTokens } from './format';
import { ModelCard, type CardMode, type SlotPicker } from './ModelCard';
import { isDone, type Generation, type Model, type ModelChoices, type ModelResult } from './types';

export const POLL_MS = 2500;

/** Keep the GLB link a card already has, so polling doesn't reload a finished model. */
export function mergeResults(prev: Generation | null, next: Generation): Generation {
  if (!prev || prev.jobId !== next.jobId) return next;
  const old = new Map(prev.results.map((r) => [r.model.key, r]));
  return {
    ...next,
    results: next.results.map((r) => {
      const o = old.get(r.model.key);
      return o?.glbUrl && r.status === 'SUCCEEDED' ? { ...r, glbUrl: o.glbUrl, scriptUrl: o.scriptUrl } : r;
    }),
  };
}

export const isRunning = (g: Generation | null) => !!g && g.results.some((r) => !isDone(r));

/** Summary bar and the 2x2 grid of model cards for one generation (or empty cards). */
export function GenerationView({ generation, models, mode, choices, onChoose, pickerDisabled = false }: {
  generation: Generation | null;
  /** The slots, in display order. A slot with `options` gets a dropdown when `onChoose` is given. */
  models: Model[];
  mode: CardMode;
  choices?: ModelChoices;
  onChoose?: (slotKey: string, modelKey: string) => void;
  pickerDisabled?: boolean;
}) {
  // Before a run, each card shows the model chosen for its slot.
  const chosenModel = (slot: Model) => slot.options?.find((o) => o.key === choices?.[slot.key]) ?? slot;
  const results: ModelResult[] = generation?.results ?? models.map((slot) => ({ model: chosenModel(slot), status: 'PENDING' }));
  const summary = useMemo(() => {
    const done = results.filter((r) => r.status === 'SUCCEEDED');
    const finished = results.filter(isDone);
    const sum = (f: (r: ModelResult) => number | undefined) => finished.reduce((a, r) => a + (f(r) ?? 0), 0);
    const best = (f: (r: ModelResult) => number | undefined) =>
      done.length > 1 ? done.reduce((a, b) => ((f(a) ?? 1e9) <= (f(b) ?? 1e9) ? a : b)).model.key : undefined;
    return {
      tokens: sum((r) => r.totalTokens),
      cost: sum((r) => r.costUsd),
      wall: finished.length ? Math.max(...finished.map((r) => r.durationSeconds ?? 0)) : undefined,
      succeeded: done.length,
      finished: finished.length,
      fastest: best((r) => r.durationSeconds),
      cheapest: best((r) => r.costUsd),
    };
  }, [results]);

  return (
    <>
      {generation && (
        <section className="summary" aria-live="polite">
          <span className="summary__prompt">“{generation.prompt}”</span>
          <span>{summary.finished}/{results.length} finished · {summary.succeeded} succeeded</span>
          <span>Tokens <strong>{fmtTokens(summary.tokens)}</strong></span>
          <span>Cost <strong>{fmtCost(summary.cost)}</strong></span>
          <span>Slowest <strong>{fmtSeconds(summary.wall)}</strong></span>
        </section>
      )}
      <section className="grid">
        {results.map((r, i) => {
          // Results come back in slot order, so card i belongs to slot i.
          const slot = models[i];
          const picker: SlotPicker | undefined =
            onChoose && slot?.options && slot.options.length > 1
              ? { options: slot.options, value: r.model.key, onChange: (key) => onChoose(slot.key, key), disabled: pickerDisabled }
              : undefined;
          return (
            <ModelCard
              key={slot?.key ?? r.model.key}
              result={r}
              mode={mode}
              picker={picker}
              fastest={summary.fastest === r.model.key}
              cheapest={summary.cheapest === r.model.key}
            />
          );
        })}
      </section>
    </>
  );
}
