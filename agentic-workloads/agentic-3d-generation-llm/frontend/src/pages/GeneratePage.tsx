// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from 'react';
import { createGeneration, getGeneration } from '../api';
import { GenerationView, isRunning, mergeResults, POLL_MS } from '../GenerationView';
import type { CardMode } from '../ModelCard';
import type { Generation, Model, ModelChoices } from '../types';

// Obstacles a commercial cleaning robot meets in supermarkets, malls and offices, for simulation scenes.
const EXAMPLES = [
  'a supermarket shopping cart',
  'a yellow wet floor sign',
  'a shopping mall bench',
  'an office chair on wheels',
];
const MAX_PROMPT = 1000;

export function GeneratePage({ models }: { models: Model[] }) {
  const [prompt, setPrompt] = useState('');
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loadingJob, setLoadingJob] = useState(false); // opening a past generation
  const [choices, setChoices] = useState<ModelChoices>({}); // dropdown picks; a missing slot runs its default
  const pollRef = useRef<number | undefined>(undefined);

  const load = useCallback(async (jobId: string) => {
    try {
      const g = await getGeneration(jobId);
      setGeneration((prev) => mergeResults(prev, g));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const job = new URLSearchParams(window.location.search).get('job');
    if (job) {
      setLoadingJob(true);
      load(job).finally(() => setLoadingJob(false));
    }
  }, [load]);

  const running = isRunning(generation);

  // Poll the current job until every model has finished.
  useEffect(() => {
    window.clearTimeout(pollRef.current);
    if (!generation || !running) return;
    pollRef.current = window.setTimeout(() => load(generation.jobId), POLL_MS);
    return () => window.clearTimeout(pollRef.current);
  }, [generation, running, load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = prompt.trim();
    if (text.length < 3) return;
    setSubmitting(true);
    setError(null);
    try {
      const g = await createGeneration(text, choices);
      setGeneration(g);
      window.history.replaceState(null, '', `/?job=${g.jobId}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  const mode: CardMode = generation ? 'job' : loadingJob ? 'loading' : 'idle';

  // Picking a different model sets up the next run: clear finished results so the cards show the new line-up.
  function choose(slotKey: string, modelKey: string) {
    setChoices((prev) => ({ ...prev, [slotKey]: modelKey }));
    if (generation) {
      setGeneration(null);
      window.history.replaceState(null, '', '/');
    }
  }

  return (
    <>
      <form className="prompt" onSubmit={submit}>
        <label htmlFor="prompt">Describe a 3D object</label>
        <textarea
          id="prompt"
          value={prompt}
          maxLength={MAX_PROMPT}
          rows={3}
          placeholder="e.g. a supermarket shopping cart"
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e);
          }}
        />
        <div className="prompt__row">
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button type="button" key={ex} className="chip" onClick={() => setPrompt(ex)}>
                {ex}
              </button>
            ))}
          </div>
          <button type="submit" className="primary" disabled={submitting || running || prompt.trim().length < 3}>
            {submitting ? 'Starting…' : running ? 'Generating…' : 'Generate with 4 models'}
          </button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>

      <GenerationView
        generation={generation}
        models={models}
        mode={mode}
        choices={choices}
        onChoose={choose}
        pickerDisabled={running || submitting || loadingJob}
      />
    </>
  );
}
