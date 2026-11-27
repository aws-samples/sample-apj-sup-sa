// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { FlashbarProps } from '@cloudscape-design/components/flashbar';
import { ApiError } from './api';
import type { Me } from './types';

export const READ_ONLY_REASON = 'Read-only role';

type Flash = { type: FlashbarProps.Type; header?: string; content?: ReactNode; requestId?: string };

type Ctx = {
  me: Me | null;
  isAdmin: boolean;
  flashes: FlashbarProps.MessageDefinition[];
  notify: (f: Flash) => void;
  notifyError: (e: unknown, what: string) => void;
};

const AppCtx = createContext<Ctx | null>(null);

let flashId = 0;

export function AppProvider({ me, children }: { me: Me | null; children: ReactNode }) {
  const [items, setItems] = useState<(Flash & { id: string })[]>([]);
  const dismiss = useCallback((id: string) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const notify = useCallback(
    (f: Flash) => {
      const id = `f${++flashId}`;
      setItems((xs) => [{ ...f, id }, ...xs].slice(0, 5));
      if (f.type === 'success') setTimeout(() => dismiss(id), 8000);
    },
    [dismiss],
  );
  const notifyError = useCallback(
    (e: unknown, what: string) => {
      const err = e instanceof ApiError ? e : new ApiError(0, String(e));
      notify({ type: 'error', header: `${what}: ${err.message}`, requestId: err.requestId });
    },
    [notify],
  );
  const flashes = useMemo<FlashbarProps.MessageDefinition[]>(
    () =>
      items.map((f) => ({
        id: f.id,
        type: f.type,
        header: f.header,
        content: (
          <>
            {f.content}
            {f.requestId && <div>Request ID: {f.requestId}</div>}
          </>
        ),
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => dismiss(f.id),
      })),
    [items, dismiss],
  );
  const value = useMemo<Ctx>(
    () => ({ me, isAdmin: me?.role === 'admin', flashes, notify, notifyError }),
    [me, flashes, notify, notifyError],
  );
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error('useApp outside AppProvider');
  return c;
}

/** Standard handling for a failed write: 409 reloads, everything else is a flash with the request ID. Returns the 400 field, if any. */
export function useWriteErrors() {
  const { notify, notifyError } = useApp();
  return useCallback(
    // `fields`: the form fields the caller shows errors on; a 400 naming any other field is flashed instead, so it is
    // never silently dropped (omit it when the caller renders every field it sends).
    (e: unknown, what: string, reload?: () => void, fields?: string[]): { field: string; message: string } | undefined => {
      if (e instanceof ApiError && e.status === 409) {
        notify({ type: 'warning', header: 'This item was changed by someone else, reloaded', content: 'Review the current values and make your change again.', requestId: e.requestId });
        reload?.();
        return undefined;
      }
      if (e instanceof ApiError && e.status === 400 && e.field && (!fields || fields.includes(e.field))) return { field: e.field, message: e.message };
      notifyError(e, what);
      return undefined;
    },
    [notify, notifyError],
  );
}
