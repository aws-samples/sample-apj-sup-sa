// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export type Status = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

export interface Model {
  key: string;
  name: string;
  provider: string;
  modelId: string;
  /** Missing only for a model that is neither active nor listed as retired. */
  pricing?: { inputPerMTok: number; outputPerMTok: number };
  /** For a slot with a dropdown (Claude): every model it can run, default first. */
  options?: Model[];
}

/** Slot key (the slot's default model key) -> chosen model key. */
export type ModelChoices = Record<string, string>;

export interface ModelResult {
  model: Model;
  status: Status;
  startedAt?: string;
  finishedAt?: string;
  durationSeconds?: number;
  llmSeconds?: number;
  blenderSeconds?: number;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  totalTokens?: number;
  llmCalls?: number;
  attempts?: number;
  costUsd?: number;
  glbBytes?: number;
  glbUrl?: string;
  scriptUrl?: string;
  /** Mesh-quality counts measured in Blender (missing for runs before they were added). */
  mesh?: MeshMetrics;
  error?: string;
}

export interface MeshMetrics {
  objects: number;
  /** Connected pieces across all objects. */
  pieces: number;
  vertices: number;
  faces: number;
  triangles: number;
  /** Open boundary loops. */
  holes: number;
  boundaryEdges: number;
  nonManifoldEdges: number;
  flippedFaces: number;
  looseVertices: number;
  looseEdges: number;
  zeroAreaFaces: number;
  duplicateVertices: number;
  watertight: boolean;
  /** Some checks were skipped (a very large mesh, or an object that couldn't be measured). */
  partial?: boolean;
}

export interface Generation {
  jobId: string;
  prompt: string;
  createdAt: string;
  results: ModelResult[];
}

export const isDone = (r: ModelResult) => r.status === 'SUCCEEDED' || r.status === 'FAILED';

export interface GalleryItem {
  jobId: string;
  prompt: string;
  createdAt: string;
  succeeded: number;
  finished: number;
  total: number;
  /** GPT-6 Astra's model, or the first model that succeeded. */
  preview: { modelKey: string; modelName: string; glbUrl: string } | null;
}

export interface GalleryPage {
  items: GalleryItem[];
  nextToken?: string;
}
