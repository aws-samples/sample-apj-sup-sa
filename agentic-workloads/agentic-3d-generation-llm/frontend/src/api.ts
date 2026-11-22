// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { GalleryPage, Generation, Model, ModelChoices } from './types';

// Same origin as the site: CloudFront routes /api/* to API Gateway.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.message ?? `Request failed (${res.status})`);
  return body as T;
}

export const listModels = () => request<{ models: Model[] }>('/models').then((r) => r.models);

export const getGeneration = (jobId: string) => request<Generation>(`/generations/${encodeURIComponent(jobId)}`);

export const createGeneration = (prompt: string, models: ModelChoices = {}) =>
  request<Generation>('/generations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, models }),
  });

export const listGallery = (nextToken?: string) =>
  request<GalleryPage>(`/gallery${nextToken ? `?nextToken=${encodeURIComponent(nextToken)}` : ''}`);

export const deleteGeneration = (jobId: string) =>
  request<{ jobId: string; deletedFiles: number }>(`/generations/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
