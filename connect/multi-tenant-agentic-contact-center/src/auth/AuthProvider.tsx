import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  hostedUiBase,
  isAuthConfigured,
  loadAuthConfig,
  type AuthConfig,
} from "./authConfig";
import { randomString, sha256Challenge } from "./pkce";
import {
  clearTokens,
  decodeJwt,
  loadTokens,
  saveTokens,
  type StoredTokens,
} from "./tokens";

export interface AuthUser {
  email: string;
  groups: string[];
  /** Tenant identity from the JWT (custom:merchant_id). Undefined for admins. */
  merchantId?: string;
  /** Human-readable tenant name from the JWT (custom:merchant_name). */
  merchantName?: string;
}

interface AuthContextValue {
  isLoading: boolean;
  isAuthenticated: boolean;
  isConfigured: boolean;
  user: AuthUser | null;
  /** Redirect to the Cognito hosted UI login page. */
  login: (returnTo?: string) => Promise<void>;
  /** Clear local tokens and end the Cognito session. */
  logout: () => Promise<void>;
  /** Complete the OAuth code exchange after redirect. Returns the path to navigate to. */
  handleRedirectCallback: () => Promise<string>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const VERIFIER_KEY = "anycompany-pay.auth.pkce_verifier";
const STATE_KEY = "anycompany-pay.auth.state";
const RETURN_KEY = "anycompany-pay.auth.return_to";

function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

function userFromTokens(tokens: StoredTokens | null): AuthUser | null {
  if (!tokens) return null;
  const claims = decodeJwt(tokens.idToken);
  if (!claims) return null;
  return {
    email: (claims.email as string) ?? "",
    groups: (claims["cognito:groups"] as string[]) ?? [],
    merchantId: (claims["custom:merchant_id"] as string) || undefined,
    merchantName: (claims["custom:merchant_name"] as string) || undefined,
  };
}

async function exchange(
  cfg: AuthConfig,
  params: Record<string, string>
): Promise<StoredTokens> {
  const res = await fetch(`${hostedUiBase(cfg)}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params).toString(),
  });
  if (!res.ok) {
    throw new Error(`Token request failed (${res.status})`);
  }
  const data = await res.json();
  return {
    idToken: data.id_token,
    accessToken: data.access_token,
    refreshToken: data.refresh_token ?? params.refresh_token,
    expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [tokens, setTokens] = useState<StoredTokens | null>(null);
  const [isLoading, setLoading] = useState(true);

  // Bootstrap: load config + restore/refresh any existing session.
  useEffect(() => {
    let active = true;
    (async () => {
      const cfg = await loadAuthConfig();
      if (!active) return;
      setConfig(cfg);

      const existing = loadTokens();
      if (existing && existing.expiresAt > Date.now() + 30_000) {
        setTokens(existing);
      } else if (existing?.refreshToken && isAuthConfigured(cfg)) {
        try {
          const refreshed = await exchange(cfg, {
            grant_type: "refresh_token",
            client_id: cfg.clientId,
            refresh_token: existing.refreshToken,
          });
          if (!active) return;
          saveTokens(refreshed);
          setTokens(refreshed);
        } catch {
          clearTokens();
        }
      } else if (existing) {
        clearTokens();
      }
      if (active) setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(
    async (returnTo?: string) => {
      const cfg = config ?? (await loadAuthConfig());
      if (!isAuthConfigured(cfg)) {
        throw new Error("Authentication is not configured");
      }
      const verifier = randomString(64);
      const challenge = await sha256Challenge(verifier);
      const state = randomString(24);
      sessionStorage.setItem(VERIFIER_KEY, verifier);
      sessionStorage.setItem(STATE_KEY, state);
      if (returnTo) sessionStorage.setItem(RETURN_KEY, returnTo);

      const params = new URLSearchParams({
        response_type: "code",
        client_id: cfg.clientId,
        redirect_uri: redirectUri(),
        scope: "openid email profile",
        state,
        code_challenge_method: "S256",
        code_challenge: challenge,
      });
      window.location.assign(`${hostedUiBase(cfg)}/oauth2/authorize?${params}`);
    },
    [config]
  );

  const handleRedirectCallback = useCallback(async (): Promise<string> => {
    const cfg = config ?? (await loadAuthConfig());
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    const err = url.searchParams.get("error");
    if (err) {
      throw new Error(url.searchParams.get("error_description") ?? err);
    }
    const storedState = sessionStorage.getItem(STATE_KEY);
    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    if (!code || !state || state !== storedState || !verifier) {
      throw new Error("Invalid authentication response");
    }

    const next = await exchange(cfg, {
      grant_type: "authorization_code",
      client_id: cfg.clientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    });
    saveTokens(next);
    setTokens(next);

    const returnTo = sessionStorage.getItem(RETURN_KEY) || "/";
    sessionStorage.removeItem(VERIFIER_KEY);
    sessionStorage.removeItem(STATE_KEY);
    sessionStorage.removeItem(RETURN_KEY);
    return returnTo;
  }, [config]);

  const logout = useCallback(async () => {
    const cfg = config ?? (await loadAuthConfig());
    clearTokens();
    setTokens(null);
    if (!isAuthConfigured(cfg)) {
      window.location.assign("/");
      return;
    }
    const params = new URLSearchParams({
      client_id: cfg.clientId,
      logout_uri: `${window.location.origin}/`,
    });
    window.location.assign(`${hostedUiBase(cfg)}/logout?${params}`);
  }, [config]);

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isAuthenticated: !!tokens && tokens.expiresAt > Date.now(),
      isConfigured: !!config && isAuthConfigured(config),
      user: userFromTokens(tokens),
      login,
      logout,
      handleRedirectCallback,
    }),
    [isLoading, tokens, config, login, logout, handleRedirectCallback]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
