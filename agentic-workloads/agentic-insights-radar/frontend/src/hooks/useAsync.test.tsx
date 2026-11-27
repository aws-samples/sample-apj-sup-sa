// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useAsync } from './useAsync';

describe('useAsync', () => {
  it('loads, reloads and follows deps', async () => {
    let n = 0;
    const { result, rerender } = renderHook(({ dep }) => useAsync(async () => `${dep}-${++n}`, [dep]), { initialProps: { dep: 'a' } });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe('a-1'));
    act(() => result.current.reload());
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBe('a-1'); // keeps the last value while reloading
    await waitFor(() => expect(result.current.data).toBe('a-2'));
    rerender({ dep: 'b' });
    await waitFor(() => expect(result.current.data).toBe('b-3'));
  });

  it('reports errors and ignores stale results', async () => {
    let resolveFirst: (v: string) => void = () => undefined;
    const { result, rerender } = renderHook(
      ({ dep }) => useAsync(() => (dep === 'slow' ? new Promise<string>((r) => (resolveFirst = r)) : Promise.reject(new Error('boom'))), [dep]),
      { initialProps: { dep: 'slow' } },
    );
    rerender({ dep: 'fail' });
    await waitFor(() => expect(result.current.error?.message).toBe('boom'));
    await act(async () => resolveFirst('late'));
    expect(result.current.data).toBeUndefined();
    const { result: r2 } = renderHook(() => useAsync(() => Promise.reject('plain'), []));
    await waitFor(() => expect(r2.current.error?.message).toBe('plain'));
  });

  it('never returns the previous deps\' data once the deps change', async () => {
    const pending = new Map<string, (v: string) => void>();
    const { result, rerender } = renderHook(({ dep }) => useAsync(() => (dep === 'a' ? Promise.resolve('A') : new Promise<string>((r) => pending.set(dep, r))), [dep]), {
      initialProps: { dep: 'a' },
    });
    await waitFor(() => expect(result.current.data).toBe('A'));
    rerender({ dep: 'b' });
    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(true);
    act(() => result.current.reload()); // reloading while the first load of b is still pending
    expect(result.current.data).toBeUndefined();
    await act(async () => pending.get('b')?.('B'));
    expect(result.current).toMatchObject({ data: 'B', loading: false });
  });

  it('keeps the data while `refreshOn` triggers a new load of the same deps', async () => {
    let n = 0;
    const { result, rerender } = renderHook(({ v }) => useAsync(async () => `run-${++n}`, ['m'], [v]), { initialProps: { v: 0 } });
    await waitFor(() => expect(result.current.data).toBe('run-1'));
    rerender({ v: 1 });
    expect(result.current).toMatchObject({ data: 'run-1', loading: true });
    await waitFor(() => expect(result.current).toMatchObject({ data: 'run-2', loading: false }));
  });
});

describe('r3 F3-002: a failed reload keeps the last data', () => {
  it('returns the previous data with the error, and drops neither until the deps change', async () => {
    let fail = false;
    const { result, rerender } = renderHook(({ dep }) => useAsync(async () => {
      if (fail) throw new Error('down');
      return `${dep}-ok`;
    }, [dep]), { initialProps: { dep: 'a' } });
    await waitFor(() => expect(result.current.data).toBe('a-ok'));
    fail = true;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error?.message).toBe('down'));
    expect(result.current).toMatchObject({ data: 'a-ok', loading: false });
    rerender({ dep: 'b' });
    await waitFor(() => expect(result.current.error?.message).toBe('down'));
    expect(result.current.data).toBeUndefined(); // another radar: nothing of the first one is shown
  });
});
