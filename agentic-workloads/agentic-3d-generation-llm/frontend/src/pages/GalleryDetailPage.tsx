// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteGeneration, getGeneration } from '../api';
import { fmtWhen } from '../format';
import { GenerationView, isRunning, mergeResults, POLL_MS } from '../GenerationView';
import { Link, navigate } from '../router';
import type { Generation, Model } from '../types';

export function GalleryDetailPage({ jobId, models }: { jobId: string; models: Model[] }) {
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const pollRef = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const g = await getGeneration(jobId);
      setGeneration((prev) => mergeResults(prev, g));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [jobId]);

  useEffect(() => {
    setGeneration(null);
    load();
  }, [load]);

  // A generation that is still running keeps updating here too.
  const running = isRunning(generation);
  useEffect(() => {
    window.clearTimeout(pollRef.current);
    if (!running) return;
    pollRef.current = window.setTimeout(load, POLL_MS);
    return () => window.clearTimeout(pollRef.current);
  }, [generation, running, load]);

  async function remove() {
    if (!generation) return;
    const ok = window.confirm(
      `Delete "${generation.prompt}" and all ${generation.results.length} model generations? This can't be undone.`,
    );
    if (!ok) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteGeneration(generation.jobId);
      navigate('/gallery');
    } catch (e) {
      setDeleteError((e as Error).message);
      setDeleting(false);
    }
  }

  return (
    <section className="detail">
      <div className="page-head">
        <Link href="/gallery" className="back">← Back to gallery</Link>
        {generation && <p className="muted">Generated {fmtWhen(generation.createdAt)}</p>}
      </div>
      {error ? (
        <p className="error" role="alert">{error === 'Not found' ? 'This generation was not found. It may have expired.' : error}</p>
      ) : (
        <GenerationView generation={generation} models={models} mode={generation ? 'job' : 'loading'} />
      )}
      {generation && (
        <div className="danger-zone">
          <div>
            <strong>Delete this object</strong>
            <p className="muted">Removes all {generation.results.length} model generations, their GLB files and Blender scripts.</p>
            {running && <p className="muted">You can delete it once every model has finished.</p>}
            {deleteError && <p className="error" role="alert">{deleteError}</p>}
          </div>
          <button type="button" className="danger" onClick={remove} disabled={deleting || running}>
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      )}
    </section>
  );
}
