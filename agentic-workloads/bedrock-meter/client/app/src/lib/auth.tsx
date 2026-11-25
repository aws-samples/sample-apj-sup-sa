"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { PropsWithChildren } from "react";
import { API_BASE, setAuthToken } from "@/lib/api";
import { useConsoleConfig } from "@/lib/config";

// Direct Cognito USER_PASSWORD_AUTH against the IdP endpoint — no amplify/cognito-sdk
// dependency. region + appClientId come from the runtime config (GET /api/console/config),
// not the build, so the artifact is environment-independent. The id token is held in memory +
// sessionStorage (survives reload, cleared on tab close) and attached as a Bearer header to
// write calls by lib/api.
type AuthState = {
  loggedIn: boolean;
  email: string | null;
  // True when the logged-in user may perform writes: in the admin group (cognito:groups), or
  // — when no admin group is configured — any logged-in user (bootstrap). Read-only viewers
  // are loggedIn but NOT canWrite, so write controls disable for them. The BFF re-checks; this
  // is UX only.
  canWrite: boolean;
  // Null until the runtime config resolves; the Sign-in control stays disabled until then.
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

const STORAGE_KEY = "bedrock-meter-id-token";

async function cognitoRequest<T>(target: string, payload: Record<string, unknown>): Promise<T> {
  const res = await fetch(target, {
    method: "POST",
    headers: {
      "content-type": "application/x-amz-json-1.1",
      "x-amz-target": `AWSCognitoIdentityProviderService.${payload.__operation}`,
    },
    body: JSON.stringify(Object.fromEntries(Object.entries(payload).filter(([key]) => key !== "__operation"))),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || "Cognito request failed");
  return data as T;
}

function decodeEmail(idToken: string): string | null {
  try {
    const payload = JSON.parse(atob(idToken.split(".")[1]));
    return payload.email || payload["cognito:username"] || null;
  } catch {
    return null;
  }
}

function isExpired(idToken: string): boolean {
  try {
    const payload = JSON.parse(atob(idToken.split(".")[1]));
    return typeof payload.exp === "number" && payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

// Ask the BFF for the caller's tier. The server decides isAdmin from the FULL rule (admin group
// OR the email/username allowlist), so the UI mirrors it exactly without the allowlist ever
// being sent to the browser. Failure → not a writer (fail-closed for UX; BFF re-checks anyway).
async function fetchCanWrite(idToken: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/me`, { headers: { authorization: `Bearer ${idToken}` } });
    if (!res.ok) return false;
    return Boolean((await res.json())?.isAdmin);
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [token, setToken] = useState<string | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const config = useConsoleConfig();

  // Rehydrate a non-expired token on mount, then refresh the write tier from the BFF.
  useEffect(() => {
    const stored = typeof window !== "undefined" ? window.sessionStorage.getItem(STORAGE_KEY) : null;
    if (stored && !isExpired(stored)) {
      setToken(stored);
      setAuthToken(stored);
      fetchCanWrite(stored).then(setCanWrite);
    } else if (stored) {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const login = useCallback(
    async (username: string, password: string) => {
      if (!config) throw new Error("Console config is still loading; please retry");
      const endpoint = `https://cognito-idp.${config.region}.amazonaws.com/`;
      const data = await cognitoRequest<{
        AuthenticationResult?: { IdToken?: string };
        ChallengeName?: string;
        Session?: string;
      }>(endpoint, {
        __operation: "InitiateAuth",
        AuthFlow: "USER_PASSWORD_AUTH",
        ClientId: config.appClientId,
        AuthParameters: { USERNAME: username, PASSWORD: password },
      });

      if (data.AuthenticationResult?.IdToken) {
        const idToken = data.AuthenticationResult.IdToken;
        window.sessionStorage.setItem(STORAGE_KEY, idToken);
        setAuthToken(idToken);
        setToken(idToken);
        setCanWrite(await fetchCanWrite(idToken));
        return;
      }

      throw new Error(`Unsupported Cognito challenge: ${data.ChallengeName || "unknown"}`);
    },
    [config],
  );

  const logout = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_KEY);
    setAuthToken(null);
    setToken(null);
    setCanWrite(false);
  }, []);

  const value = useMemo<AuthState>(
    () => {
      // canWrite reflects the BFF's authoritative admin tier (fetched from /api/console/me),
      // which honors BOTH the admin group AND the email/username allowlist. So an allowlist-only
      // bootstrap admin gets write controls enabled, matching what the server permits.
      return {
        loggedIn: Boolean(token),
        email: token ? decodeEmail(token) : null,
        canWrite: Boolean(token) && canWrite,
        ready: Boolean(config),
        login,
        logout,
      };
    },
    [token, canWrite, config, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
