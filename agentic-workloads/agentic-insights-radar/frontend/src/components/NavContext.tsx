// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

import type { Nav } from '@/api/types';
import { useSession } from '@/auth/session';
import { useAsync } from '@/hooks/useAsync';
import { relabelNav } from '@/lib/profile';

export interface NavState {
  nav: Nav | undefined;
  loading: boolean;
  /** Why the list names could not be read. Without them a custom list looks deleted. */
  error: Error | undefined;
  /** Re-read /api/nav, e.g. after a custom module is created or deleted. */
  refresh: () => void;
}

const NavContext = React.createContext<NavState | null>(null);

/** One /api/nav for the whole app: the side navigation and every insight list share it. */
export function NavProvider({ children }: { children: React.ReactNode }) {
  const { api } = useSession();
  const state = useAsync(() => api.nav().then(relabelNav), [api]);
  const value = React.useMemo(
    () => ({ nav: state.data, loading: state.loading, error: state.error, refresh: state.reload }),
    [state.data, state.loading, state.error, state.reload],
  );
  return <NavContext.Provider value={value}>{children}</NavContext.Provider>;
}

/** The shared navigation; outside a NavProvider (a page rendered on its own) it loads its own copy. */
export function useNav(): NavState {
  const shared = React.useContext(NavContext);
  const { api } = useSession();
  const own = useAsync(() => (shared ? Promise.resolve(undefined) : api.nav().then(relabelNav)), [api, shared === null]);
  return shared ?? { nav: own.data, loading: own.loading, error: own.error, refresh: own.reload };
}
