// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

export interface AsyncState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

interface Result<T> {
  request: object;
  data?: T;
  error?: Error;
}

function same(a: React.DependencyList, b: React.DependencyList): boolean {
  return a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
}

/**
 * Run `load` when `deps` change. A request token is derived during render when the deps, the
 * reload counter or `refreshOn` change (React's documented "adjust state on prop change"
 * pattern); state is set only when the latest request settles, and `loading` is derived from
 * whether it has.
 *
 * A reload (or a `refreshOn` change) keeps showing the last data until the new load settles, but
 * new `deps` never show the previous deps' data: the `generation` token changes with the deps only.
 * A reload that fails keeps the last data too, next to its `error`: a page that is open with a
 * draft must not be replaced by an error page because one background re-read failed (r3 F3-002).
 * A page tells the two apart by whether `data` is there.
 */
export function useAsync<T>(load: () => Promise<T>, deps: React.DependencyList, refreshOn: React.DependencyList = []): AsyncState<T> {
  const [tick, setTick] = React.useState(0);
  const [snapshot, setSnapshot] = React.useState(() => ({ deps, refreshOn, tick, request: {} as object, generation: {} as object }));
  if (!same(snapshot.deps, deps)) setSnapshot({ deps, refreshOn, tick, request: {}, generation: {} });
  else if (snapshot.tick !== tick || !same(snapshot.refreshOn, refreshOn)) setSnapshot({ ...snapshot, refreshOn, tick, request: {} });
  const { request, generation } = snapshot;
  const [result, setResult] = React.useState<Result<T> | null>(null);
  const [last, setLast] = React.useState<{ generation: object; data: T } | null>(null);
  React.useEffect(() => {
    let live = true;
    load().then(
      (data) => {
        if (!live) return;
        setResult({ request, data });
        setLast({ generation, data });
      },
      (error: unknown) => live && setResult({ request, error: error instanceof Error ? error : new Error(String(error)) }),
    );
    return () => {
      live = false;
    };
    // `load` is a fresh closure every render; the request token is what identifies a load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);
  const reload = React.useCallback(() => setTick((t) => t + 1), []);
  const current = result?.request === request ? result : null;
  const previous = last?.generation === generation ? last.data : undefined;
  return { data: current && !current.error ? current.data : previous, error: current?.error, loading: !current, reload };
}
