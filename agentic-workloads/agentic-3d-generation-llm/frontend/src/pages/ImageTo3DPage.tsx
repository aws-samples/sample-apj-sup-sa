import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import { createImageGeneration, getGeneration } from '../api';
import { GenerationView, InputImagePanel, isRunning, mergeResults, POLL_MS } from '../GenerationView';
import type { CardMode } from '../ModelCard';
import type { Generation, Model, ModelChoices } from '../types';

// The upload is redrawn in the browser before it is sent: at most 1024 px on its longest side, as a JPEG.
// That keeps the request small and drops metadata such as GPS location.
const MAX_SIDE = 1024;
const MIN_SIDE = 64;
const MAX_ASPECT = 2.5; // the background-removal model's limit
const MAX_DESCRIPTION = 300;
const SAMPLE = { src: '/samples/unitree-go2.jpg', name: 'Unitree Go2 robot dog', description: 'A Unitree Go2 quadruped robot dog' };

interface Prepared {
  base64: string;
  previewUrl: string;
  width: number;
  height: number;
}

async function prepareImage(blob: Blob): Promise<Prepared> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(blob);
  } catch {
    throw new Error("That file couldn't be read as an image. Try a JPEG, PNG or WebP.");
  }
  const { width, height } = bitmap;
  if (Math.min(width, height) < MIN_SIDE) throw new Error(`The image must be at least ${MIN_SIDE} px on each side.`);
  if (Math.max(width, height) / Math.min(width, height) > MAX_ASPECT) {
    throw new Error(`The image is too narrow: crop it to no more than ${MAX_ASPECT}× wider than tall (or taller than wide).`);
  }
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error("This browser can't process images.");
  ctx.fillStyle = '#fff'; // JPEG has no transparency
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const jpeg = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not encode the image.'))), 'image/jpeg', 0.92),
  );
  const base64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.onerror = () => reject(new Error('Could not read the image.'));
    reader.readAsDataURL(jpeg);
  });
  return { base64, previewUrl: URL.createObjectURL(jpeg), width: canvas.width, height: canvas.height };
}

export function ImageTo3DPage({ models }: { models: Model[] }) {
  const [image, setImage] = useState<Prepared | null>(null);
  const [description, setDescription] = useState('');
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loadingJob, setLoadingJob] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [choices, setChoices] = useState<ModelChoices>({});
  const pollRef = useRef<number | undefined>(undefined);
  const fileRef = useRef<HTMLInputElement>(null);
  // The job this page is showing; a late response for any other job (e.g. a slow shared link) is dropped.
  const wantedJob = useRef<string | null>(null);

  const load = useCallback(async (jobId: string) => {
    try {
      const g = await getGeneration(jobId);
      if (wantedJob.current !== jobId) return;
      setGeneration((prev) => mergeResults(prev, g));
      setError(null);
    } catch (e) {
      if (wantedJob.current !== jobId) return;
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const job = new URLSearchParams(window.location.search).get('job');
    if (job) {
      wantedJob.current = job;
      setLoadingJob(true);
      load(job).finally(() => setLoadingJob(false));
    }
  }, [load]);

  // Free the preview's object URL when it is replaced or the page closes.
  useEffect(() => () => { if (image) URL.revokeObjectURL(image.previewUrl); }, [image]);

  const running = isRunning(generation);
  useEffect(() => {
    window.clearTimeout(pollRef.current);
    if (!generation || !running) return;
    pollRef.current = window.setTimeout(() => load(generation.jobId), POLL_MS);
    return () => window.clearTimeout(pollRef.current);
  }, [generation, running, load]);

  async function pick(blob: Blob, sampleDescription?: string) {
    setError(null);
    try {
      setImage(await prepareImage(blob));
      if (sampleDescription !== undefined) setDescription(sampleDescription);
      if (generation) {
        // A new image sets up the next run.
        wantedJob.current = null;
        setGeneration(null);
        window.history.replaceState(null, '', '/image-to-3d');
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function useSample() {
    try {
      const res = await fetch(SAMPLE.src);
      if (!res.ok) throw new Error();
      await pick(await res.blob(), SAMPLE.description);
    } catch {
      setError("The sample image couldn't be loaded.");
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file && !busy) pick(file);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!image || busy) return;
    setSubmitting(true);
    setError(null);
    try {
      const g = await createImageGeneration(image.base64, description.trim(), choices);
      wantedJob.current = g.jobId;
      setGeneration(g);
      window.history.replaceState(null, '', `/image-to-3d?job=${g.jobId}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function choose(slotKey: string, modelKey: string) {
    setChoices((prev) => ({ ...prev, [slotKey]: modelKey }));
    if (generation) {
      wantedJob.current = null;
      setGeneration(null);
      window.history.replaceState(null, '', '/image-to-3d');
    }
  }

  const mode: CardMode = generation ? 'job' : loadingJob ? 'loading' : 'idle';
  // Opening a shared job counts as busy too, so nothing new can start until it has loaded.
  const busy = submitting || running || loadingJob;

  return (
    <>
      <form className="prompt" onSubmit={submit}>
        <label htmlFor="image-file">Upload a photo of an object</label>
        <div
          className={`dropzone${dragging ? ' dropzone--over' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          {image ? (
            <img className="dropzone__preview" src={image.previewUrl} alt="Selected image" />
          ) : (
            <div className="dropzone__hint muted">
              <strong>Drop an image here</strong> or choose a file. JPEG, PNG or WebP, one object, at least {MIN_SIDE} px.
            </div>
          )}
          <div className="dropzone__actions">
            <input
              ref={fileRef}
              id="image-file"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="visually-hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file && !busy) pick(file);
                e.target.value = '';
              }}
            />
            <button type="button" className="secondary" onClick={() => fileRef.current?.click()} disabled={busy}>
              {image ? 'Choose another image' : 'Choose an image'}
            </button>
            <button type="button" className="chip" onClick={useSample} disabled={busy}>
              Try the sample: {SAMPLE.name}
            </button>
            {image && <span className="muted">{image.width}×{image.height} px, sent as JPEG</span>}
          </div>
        </div>
        <label htmlFor="image-description" className="prompt__sub">What is it? (optional, helps the models)</label>
        <input
          id="image-description"
          className="text-input"
          value={description}
          maxLength={MAX_DESCRIPTION}
          placeholder="e.g. a quadruped robot dog, about 70 cm long"
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="prompt__row">
          <p className="muted prompt__note">
            Uploads are scanned for malware, the background is removed, and each model gets the result. Uploads are public: they show in
            the Gallery. Up to 100 a day across all users.
          </p>
          <button type="submit" className="primary" disabled={!image || busy}>
            {submitting ? 'Uploading…' : running ? 'Generating…' : 'Generate with 4 models'}
          </button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>

      {generation?.input && <InputImagePanel generation={generation} original={image?.previewUrl} />}

      <GenerationView
        generation={generation}
        models={models}
        mode={mode}
        choices={choices}
        onChoose={choose}
        pickerDisabled={busy || loadingJob}
        idleHint={generation?.kind === 'image' || !generation ? 'Upload an image above to turn it into a 3D object' : undefined}
      />
    </>
  );
}
