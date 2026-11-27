// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

/**
 * Which mounted pages hold unsaved changes.
 *
 * Every exit from the app unmounts the page before the browser leaves: Sign out assigns a new
 * address, and Sign in again sets the OIDC navigator, which makes the gate show "Signing in". The
 * page's own `beforeunload` listener and navigation blocker are gone by then, so the browser never
 * asks. The flag therefore lives outside React's tree, where the shell and the session can read it.
 */
/** A way to take a page's unsaved changes out of the app (a download), offered when it must be left. */
export interface Rescue {
  label: string;
  run: () => void;
}

class DirtyPages {
  private readonly ids = new Set<string>();
  private readonly listeners = new Set<() => void>();
  private any = false;
  private readonly rescueById = new Map<string, Rescue>();
  private rescueList: Rescue[] = [];

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };

  readonly snapshot = (): boolean => this.any;

  /** The rescues of the pages that are dirty now (a stable array between changes). */
  readonly rescues = (): Rescue[] => this.rescueList;

  set(id: string, dirty: boolean, rescue?: Rescue): void {
    if (dirty) this.ids.add(id);
    else this.ids.delete(id);
    const before = this.rescueById.get(id);
    if (dirty && rescue) this.rescueById.set(id, rescue);
    else this.rescueById.delete(id);
    const rescued = before !== this.rescueById.get(id);
    if (rescued) this.rescueList = [...this.rescueById.values()];
    const any = this.ids.size > 0;
    if (any === this.any && !rescued) return;
    this.any = any;
    for (const listener of this.listeners) listener();
  }
}

export const dirtyPages = new DirtyPages();

/** Reports whether this page has unsaved changes, for as long as it is mounted, and how to rescue them. */
export function useReportDirty(dirty: boolean, rescue?: Rescue): void {
  const id = React.useId();
  // The latest rescue runs, while the registry keeps one stable entry per label.
  const latest = React.useRef(rescue);
  React.useEffect(() => {
    latest.current = rescue;
  });
  const label = rescue?.label;
  React.useEffect(() => {
    dirtyPages.set(id, dirty, label === undefined ? undefined : { label, run: () => (latest.current as Rescue).run() });
    return () => dirtyPages.set(id, false);
  }, [id, dirty, label]);
}

/** The rescues offered by the pages that hold unsaved changes now. */
export function useRescues(): Rescue[] {
  return React.useSyncExternalStore(dirtyPages.subscribe, dirtyPages.rescues);
}

/** True while any mounted page holds unsaved changes. */
export function useAnyDirty(): boolean {
  return React.useSyncExternalStore(dirtyPages.subscribe, dirtyPages.snapshot);
}
