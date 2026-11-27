// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useCallback, useRef } from 'react';

/** Runs an async save at most once at a time. A ref, not state: two clicks in the same frame both see a state flag as
 * false, but the ref is set at once. A second write would carry the same version and come back as a false 409 (the
 * admin's own first save), or apply twice. Each call of the hook guards its own action. */
export function useInFlight() {
  const busy = useRef(false);
  return useCallback(async (fn: () => Promise<void>) => {
    if (busy.current) return;
    busy.current = true;
    try { await fn(); } finally { busy.current = false; }
  }, []);
}
