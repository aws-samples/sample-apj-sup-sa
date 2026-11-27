// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

import { appName } from '@/lib/profile';

/** The built-in product name; a deployment profile renames it (`appName()`). */
export const APP_TITLE = 'Agentic Insights Radar';

/**
 * The current page's name. It lives outside React's tree because the page knows it (a radar's name
 * arrives with the radar) while the shell is what announces it: without this every browser tab and
 * every history entry read "Agentic Insights Radar", and a route change was announced nowhere.
 */
interface TitleState {
  title: string;
  /** False while the page is still loading the data its name comes from (a radar's name, say). */
  ready: boolean;
}

class PageTitle {
  private current: TitleState = { title: APP_TITLE, ready: true };
  private readonly listeners = new Set<() => void>();

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };

  /** The title and whether it is final: the shell announces and focuses a page only once it is. */
  readonly state = (): TitleState => this.current;

  set(title: string, ready = true): void {
    if (title === this.current.title && ready === this.current.ready) return;
    this.current = { title, ready };
    document.title = title;
    for (const listener of this.listeners) listener();
  }
}

export const pageTitle = new PageTitle();

/** Names this page: "Rivals - Radars - Agentic Insights Radar". Empty parts are left out. */
export function usePageTitle(...parts: (string | undefined | null)[]): void {
  useLoadingPageTitle(false, ...parts);
}

/**
 * The same, for a page whose name arrives with its data: while `loading` the title is not final, so
 * the shell neither announces "Radars" and then "Rivals - Radars" nor focuses a spinner.
 */
export function useLoadingPageTitle(loading: boolean, ...parts: (string | undefined | null)[]): void {
  const title = [...parts.filter((p): p is string => Boolean(p)), appName()].join(' - ');
  React.useEffect(() => {
    pageTitle.set(title, !loading);
  }, [title, loading]);
}
