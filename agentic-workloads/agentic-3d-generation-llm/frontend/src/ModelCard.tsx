// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from 'react';
import { fmtBytes, fmtCost, fmtSeconds, fmtTokens, secondsSince } from './format';
import { Link } from './router';
import type { MeshMetrics, Model, ModelResult } from './types';

// Matches MAX_ATTEMPTS on the AgentCore runtime (infra/lib/stack.ts).
const MAX_ATTEMPTS = 3;

const STATUS_LABEL ={ PENDING: 'Queued', RUNNING: 'Generating', SUCCEEDED: 'Done', FAILED: 'Failed' } as const;

function useElapsed(since: string | undefined, active: boolean) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return secondsSince(since);
}

/** 'idle': nothing generated or opened yet. 'loading': a past generation is being fetched. */
export type CardMode = 'idle' | 'loading' | 'job';

/** model-viewer with a spinner until the GLB has downloaded and rendered. */
function GlbViewer({ src, alt }: { src: string; alt: string }) {
  const ref = useRef<HTMLElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => {
    setState('loading');
    const el = ref.current;
    if (!el) return;
    const onLoad = () => setState('ready');
    const onError = () => setState('error');
    el.addEventListener('load', onLoad);
    el.addEventListener('error', onError);
    return () => {
      el.removeEventListener('load', onLoad);
      el.removeEventListener('error', onError);
    };
  }, [src]);
  return (
    <>
      <model-viewer
        ref={ref}
        src={src}
        alt={alt}
        camera-controls
        auto-rotate
        shadow-intensity="1"
        exposure="1"
        interaction-prompt="none"
      />
      {state !== 'ready' && (
        <div className={`viewer__msg viewer__overlay${state === 'error' ? ' viewer__msg--error' : ''}`}>
          {state === 'loading' ? (
            <>
              <span className="spinner" aria-hidden />
              <span>Loading 3D model…</span>
            </>
          ) : (
            <span>The 3D model couldn't be loaded. Try Download GLB.</span>
          )}
        </div>
      )}
    </>
  );
}

const ISSUES: { key: keyof MeshMetrics; label: string; hint: string }[] = [
  { key: 'holes', label: 'Holes', hint: 'Open boundary loops (edges with only one face)' },
  { key: 'flippedFaces', label: 'Flipped faces', hint: 'Faces wound opposite to the rest of their piece' },
  { key: 'looseVertices', label: 'Loose vertices', hint: 'Vertices not part of any face' },
  { key: 'zeroAreaFaces', label: 'Zero-area faces', hint: 'Degenerate faces with no area' },
  { key: 'duplicateVertices', label: 'Duplicate vertices', hint: 'Overlapping vertices within 0.01 mm of another' },
];

/** Mesh-quality counts measured in Blender after the script ran. */
function MeshQuality({ mesh }: { mesh: MeshMetrics }) {
  const issues = ISSUES.reduce((n, i) => n + (mesh[i.key] as number), 0);
  return (
    <section className="mesh" aria-label="Mesh quality">
      <div className="mesh__head">
        <span className="mesh__title">Mesh quality</span>
        <span className={`mesh__flag ${mesh.watertight ? 'mesh__flag--ok' : 'mesh__flag--warn'}`}
          title={mesh.watertight ? 'Closed surface: no open or non-manifold edges' : `${fmtTokens(mesh.boundaryEdges)} open and ${fmtTokens(mesh.nonManifoldEdges)} non-manifold edges`}>
          {mesh.watertight ? 'Watertight' : 'Not watertight'}
        </span>
        <span className="muted mesh__totals">
          {fmtTokens(mesh.triangles)} tris · {fmtTokens(mesh.vertices)} verts · {fmtTokens(mesh.objects)} part{mesh.objects === 1 ? '' : 's'}
          {mesh.pieces !== mesh.objects && ` (${fmtTokens(mesh.pieces)} pieces)`}
        </span>
      </div>
      <ul className="mesh__issues">
        {ISSUES.map((i) => {
          const n = mesh[i.key] as number;
          return (
            <li key={i.key} className={n > 0 ? 'mesh__issue mesh__issue--warn' : 'mesh__issue'} title={i.hint}>
              <span>{i.label}</span>
              <strong>{n > 0 ? fmtTokens(n) : '✓'}</strong>
            </li>
          );
        })}
      </ul>
      {issues === 0 && mesh.watertight && <p className="mesh__note muted">No mesh problems found.</p>}
      {mesh.partial && <p className="mesh__note muted">Some checks were skipped for part of this model, so counts may be low.</p>}
    </section>
  );
}

/** A dropdown in the card header to pick which model runs in this slot. */
export interface SlotPicker {
  options: Model[];
  value: string;
  onChange: (key: string) => void;
  disabled: boolean;
}

export function ModelCard({ result, fastest, cheapest, mode, picker }: {
  result: ModelResult;
  fastest: boolean;
  cheapest: boolean;
  mode: CardMode;
  picker?: SlotPicker;
}) {
  const { model, status } = result;
  const running = mode === 'job' && (status === 'RUNNING' || status === 'PENDING');
  const elapsed = useElapsed(result.startedAt, running);

  return (
    <article className={`card${mode === 'job' ? ` card--${status.toLowerCase()}` : ''}`}>
      <header className="card__head">
        <div>
          {picker ? (
            <select
              className="model-select"
              aria-label="Model for this slot"
              value={picker.value}
              disabled={picker.disabled}
              onChange={(e) => picker.onChange(e.target.value)}
            >
              {picker.options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.name}
                </option>
              ))}
            </select>
          ) : (
            <h3>{model.name}</h3>
          )}
          <span className="muted">{model.provider} · <code>{model.modelId}</code></span>
        </div>
        {mode === 'job' && <span className={`badge badge--${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>}
      </header>

      <div className="viewer">
        {mode === 'idle' ? (
          <div className="viewer__msg">
            <span>
              Enter a prompt above to generate a 3D object, or browse past generations in the <Link href="/gallery">Gallery</Link>.
            </span>
          </div>
        ) : mode === 'loading' ? (
          <div className="viewer__msg">
            <span className="spinner" aria-hidden />
            <span>Loading generation…</span>
          </div>
        ) : status === 'SUCCEEDED' && result.glbUrl ? (
          <GlbViewer src={result.glbUrl} alt={`3D model generated by ${model.name}`} />
        ) : status === 'FAILED' ? (
          <div className="viewer__msg viewer__msg--error">
            {(result.attempts ?? 0) >= MAX_ATTEMPTS ? (
              <>
                <strong>3D generation has failed after {MAX_ATTEMPTS} attempts</strong>
                {result.error && (
                  <details className="viewer__details">
                    <summary>Show last error</summary>
                    <pre>{result.error}</pre>
                  </details>
                )}
              </>
            ) : (
              <>
                <strong>No model</strong>
                <span>{result.error ?? 'Generation failed'}</span>
              </>
            )}
          </div>
        ) : (
          <div className="viewer__msg">
            <span className="spinner" aria-hidden />
            <span>{status === 'PENDING' ? 'Waiting to start…' : `Writing and running the Blender script… ${fmtSeconds(elapsed)}`}</span>
          </div>
        )}
      </div>

      <dl className="stats">
        <div>
          <dt>Total tokens</dt>
          <dd>{fmtTokens(result.totalTokens)}</dd>
          <small>{result.totalTokens !== undefined ? `${fmtTokens(result.inputTokens)} in · ${fmtTokens(result.outputTokens)} out${result.cacheReadTokens ? ` · ${fmtTokens(result.cacheReadTokens)} cached` : ''}` : ' '}</small>
        </div>
        <div>
          <dt>Time taken {fastest && <span className="tag">fastest</span>}</dt>
          <dd>{running ? fmtSeconds(elapsed) : fmtSeconds(result.durationSeconds)}</dd>
          <small>{result.llmSeconds !== undefined ? `LLM ${fmtSeconds(result.llmSeconds)} · Blender ${fmtSeconds(result.blenderSeconds)}` : ' '}</small>
        </div>
        <div>
          <dt>Token cost {cheapest && <span className="tag">cheapest</span>}</dt>
          <dd>{fmtCost(result.costUsd)}</dd>
          <small>{model.pricing ? `$${model.pricing.inputPerMTok} / $${model.pricing.outputPerMTok} per 1M in/out` : ' '}</small>
        </div>
      </dl>

      {status === 'SUCCEEDED' && result.mesh && <MeshQuality mesh={result.mesh} />}

      <footer className="card__foot">
        <span className="muted">
          {result.attempts !== undefined && `${result.attempts} attempt${result.attempts === 1 ? '' : 's'}`}
          {result.glbBytes ? ` · ${fmtBytes(result.glbBytes)}` : ''}
        </span>
        <span className="links">
          {result.scriptUrl && <a href={result.scriptUrl} target="_blank" rel="noreferrer">Blender script</a>}
          {result.glbUrl && <a href={result.glbUrl} download={`${model.key}.glb`}>Download GLB</a>}
        </span>
      </footer>
    </article>
  );
}
