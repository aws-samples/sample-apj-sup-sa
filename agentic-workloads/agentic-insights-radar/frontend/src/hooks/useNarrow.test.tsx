// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useNarrow } from './useNarrow';

const original = globalThis.ResizeObserver;

afterEach(() => {
  globalThis.ResizeObserver = original;
});

function Probe() {
  const [ref, narrow] = useNarrow<HTMLDivElement>(900);
  return <div ref={ref}>{narrow ? 'narrow' : 'wide'}</div>;
}

describe('useNarrow', () => {
  it('stays wide where the browser has no ResizeObserver', () => {
    // @ts-expect-error jsdom may lack it; the hook must cope
    globalThis.ResizeObserver = undefined;
    render(<Probe />);
    expect(screen.getByText('wide')).toBeInTheDocument();
  });

  it('follows the width of its container', () => {
    let fire: ((entries: unknown[]) => void) | undefined;
    const disconnect = vi.fn();
    globalThis.ResizeObserver = vi.fn(function (this: unknown, cb: (entries: unknown[]) => void) {
      fire = cb;
      return { observe: vi.fn(), disconnect };
    }) as unknown as typeof ResizeObserver;
    const { unmount } = render(<Probe />);
    act(() => fire?.([{ contentRect: { width: 640 } }]));
    expect(screen.getByText('narrow')).toBeInTheDocument();
    act(() => fire?.([{ contentRect: { width: 1100 } }]));
    expect(screen.getByText('wide')).toBeInTheDocument();
    act(() => fire?.([])); // no entry: reads the element (0 in jsdom, which is not narrow)
    expect(screen.getByText('wide')).toBeInTheDocument();
    unmount();
    expect(disconnect).toHaveBeenCalled();
  });

  it('observes a container that mounts after the first render', () => {
    const observe = vi.fn();
    globalThis.ResizeObserver = vi.fn(function () {
      return { observe, disconnect: vi.fn() };
    }) as unknown as typeof ResizeObserver;
    function Late({ show }: { show: boolean }) {
      const [ref, narrow] = useNarrow<HTMLDivElement>(900);
      return show ? <div ref={ref}>{narrow ? 'narrow' : 'wide'}</div> : <span>loading</span>;
    }
    const { rerender } = render(<Late show={false} />);
    expect(observe).not.toHaveBeenCalled();
    rerender(<Late show />);
    expect(observe).toHaveBeenCalledTimes(1);
  });
});
