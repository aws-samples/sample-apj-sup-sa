// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

/** A callback ref for a container and whether it is at most ``maxPx`` wide. The container, not the viewport:
 * side navigation, tools or a split panel can squeeze a table on a wide screen (code review r20). A callback
 * ref, so a container that mounts late or is replaced is observed too (code review r21). Without
 * ResizeObserver (very old browsers) it stays wide. */
export function useNarrow<T extends HTMLElement>(maxPx: number): [(el: T | null) => void, boolean] {
  const [el, setEl] = React.useState<T | null>(null);
  const [narrow, setNarrow] = React.useState(false);
  React.useEffect(() => {
    if (!el || typeof ResizeObserver !== 'function') return undefined;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? el.clientWidth;
      setNarrow(width > 0 && width <= maxPx);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [el, maxPx]);
  return [setEl, narrow];
}
