type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const cache = new Map<string, CacheEntry<unknown>>();
// In-flight loads keyed by cache key, so concurrent misses share one load instead
// of each starting their own (critical when a load takes longer than the TTL).
const inflight = new Map<string, Promise<unknown>>();

export async function withServerCache<T>(key: string, ttlMs: number, load: () => Promise<T>): Promise<T> {
  // ttlMs <= 0 disables value caching, but still dedupes concurrent in-flight loads.
  const now = Date.now();
  if (ttlMs > 0) {
    const hit = cache.get(key) as CacheEntry<T> | undefined;
    if (hit && hit.expiresAt > now) {
      return hit.value;
    }
  }

  const pending = inflight.get(key) as Promise<T> | undefined;
  if (pending) return pending;

  const promise = (async () => {
    try {
      const value = await load();
      if (ttlMs > 0) {
        cache.set(key, { expiresAt: Date.now() + ttlMs, value });
      }
      return value;
    } finally {
      inflight.delete(key);
    }
  })();

  inflight.set(key, promise);
  return promise;
}

export function clearServerCache() {
  cache.clear();
}
