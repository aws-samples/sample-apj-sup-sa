"use client";

import { createContext, useContext, useEffect, useState } from "react";
import type { PropsWithChildren } from "react";
import { API_BASE } from "@/lib/api";

// Runtime console config fetched from the BFF (GET /api/console/config) on first load, so the
// static build artifact carries no environment-specific values (Console URL, Cognito client
// id). clientId/region/userPoolId are public, not secrets. apiBase is inherently same-origin
// (we need it to even call /config), so it is not taken from the response.
export type ConsoleConfig = {
  region: string;
  userPoolId: string;
  appClientId: string;
  // Whether reads require login. Default true (fail-closed) if the BFF omits it.
  requireAuthForReads: boolean;
  // The admin Cognito group; the UI grants write access when the id token's cognito:groups
  // contains it. Empty string → no group gate (any logged-in user may write — bootstrap).
  adminGroup: string;
};

type ConfigState =
  | { status: "loading" }
  | { status: "ready"; config: ConsoleConfig }
  | { status: "error"; error: string };

const ConfigContext = createContext<ConfigState | null>(null);

export function ConsoleConfigProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<ConfigState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/config`);
        if (!res.ok) throw new Error(`config request failed (${res.status})`);
        const data = await res.json();
        const auth = data?.auth ?? {};
        if (!auth.appClientId || !auth.userPoolId) {
          throw new Error("console config is missing Cognito settings");
        }
        if (!cancelled) {
          setState({
            status: "ready",
            config: {
              region: String(auth.region || "ap-northeast-2"),
              userPoolId: String(auth.userPoolId),
              appClientId: String(auth.appClientId),
              // Fail-closed: only an explicit false opens anonymous reads.
              requireAuthForReads: auth.requireAuthForReads !== false,
              adminGroup: String(auth.adminGroup || ""),
            },
          });
        }
      } catch (e) {
        if (!cancelled) {
          setState({ status: "error", error: e instanceof Error ? e.message : "config load failed" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return <ConfigContext.Provider value={state}>{children}</ConfigContext.Provider>;
}

// Full state (loading/ready/error) — for code that must distinguish boot phases.
export function useConsoleConfigState(): ConfigState {
  const ctx = useContext(ConfigContext);
  if (!ctx) throw new Error("useConsoleConfigState must be used within ConsoleConfigProvider");
  return ctx;
}

// Convenience: the resolved config or null while loading/errored. Anonymous reads don't need
// it; only auth (login) does, and the Sign-in control is disabled until it resolves.
export function useConsoleConfig(): ConsoleConfig | null {
  const state = useConsoleConfigState();
  return state.status === "ready" ? state.config : null;
}
