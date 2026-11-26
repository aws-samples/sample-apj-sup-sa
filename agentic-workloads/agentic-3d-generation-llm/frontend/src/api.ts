import type { GalleryPage, Generation, Model, ModelChoices } from './types';

// Same origin as the site: CloudFront routes /api/* to API Gateway. The session lives in httpOnly
// cookies set by the CloudFront sign-in gate, so there is no token handling in the page.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { credentials: 'same-origin', ...init });
  if (res.status === 401) {
    // Session expired: reloading sends the browser through the sign-in gate (which refreshes it).
    window.location.reload();
    throw new Error('Signing in again…');
  }
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

/** Image to 3D: `image` is a base64 JPEG or PNG (no data: prefix needed). */
export const createImageGeneration = (image: string, description: string, models: ModelChoices = {}) =>
  request<Generation>('/image-generations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image, description, models }),
  });

export const listGallery = (nextToken?: string) =>
  request<GalleryPage>(`/gallery${nextToken ? `?nextToken=${encodeURIComponent(nextToken)}` : ''}`);

export const deleteGeneration = (jobId: string) =>
  request<{ jobId: string; deletedFiles: number }>(`/generations/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
