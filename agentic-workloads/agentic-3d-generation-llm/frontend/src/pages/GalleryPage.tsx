// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from 'react';
import { listGallery } from '../api';
import { fmtWhen } from '../format';
import { Link } from '../router';
import type { GalleryItem } from '../types';

function Tile({ item }: { item: GalleryItem }) {
  const inProgress = item.finished < item.total;
  return (
    <li>
      <Link href={`/gallery/${item.jobId}`} className="tile" title={item.prompt}>
        <div className="tile__viewer">
          {item.preview ? (
            <model-viewer
              src={item.preview.glbUrl}
              alt={`3D model of ${item.prompt}`}
              auto-rotate
              loading="lazy"
              shadow-intensity="1"
              exposure="1"
              interaction-prompt="none"
            />
          ) : (
            <div className="viewer__msg">
              {inProgress ? (
                <>
                  <span className="spinner" aria-hidden />
                  <span>Generating…</span>
                </>
              ) : (
                <span>No model was generated</span>
              )}
            </div>
          )}
        </div>
        <div className="tile__body">
          <span className="tile__prompt">{item.prompt}</span>
          <span className="muted tile__meta">
            {fmtWhen(item.createdAt)} · {item.succeeded}/{item.total} models
            {item.preview && item.preview.modelKey !== 'gpt-6-astra' ? ` · preview: ${item.preview.modelName}` : ''}
          </span>
        </div>
      </Link>
    </li>
  );
}

export function GalleryPage() {
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [nextToken, setNextToken] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(async (token?: string) => {
    setLoading(true);
    try {
      const page = await listGallery(token);
      setItems((prev) => (token ? [...prev, ...page.items] : page.items));
      setNextToken(page.nextToken);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPage();
  }, [fetchPage]);

  return (
    <section className="gallery">
      <div className="page-head">
        <h2>Gallery</h2>
        <p className="muted">Past generations, previewed with GPT-6 Astra's model. Open one to compare all four models.</p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted">
          Nothing here yet. <Link href="/">Generate</Link> your first 3D object.
        </p>
      )}
      <ul className="tiles">
        {items.map((item) => (
          <Tile key={item.jobId} item={item} />
        ))}
      </ul>
      {loading && (
        <div className="gallery__loading">
          <span className="spinner" aria-hidden /> <span className="muted">Loading…</span>
        </div>
      )}
      {!loading && nextToken && (
        <div className="gallery__more">
          <button type="button" className="chip" onClick={() => fetchPage(nextToken)}>
            Load more
          </button>
        </div>
      )}
    </section>
  );
}
