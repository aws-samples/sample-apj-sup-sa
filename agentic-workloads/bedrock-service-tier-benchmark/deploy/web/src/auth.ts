// OAuth 2.0 authorization code flow with PKCE against the Cognito hosted UI.
// Tokens live in sessionStorage only (cleared when the tab closes); no client secret exists.

export interface Config {
  apiUrl: string;
  cognitoDomain: string;
  clientId: string;
  identityProvider?: string; // "Federate" for the Midway variant
}

const VERIFIER = "pkce_verifier";
const STATE = "oauth_state";
const TOKEN = "id_token";

function b64url(bytes: Uint8Array): string {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function random(n = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(n)));
}

export async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(digest));
}

const redirectUri = () => `${window.location.origin}/`;

export async function login(cfg: Config): Promise<void> {
  const verifier = random(48);
  const state = random(16);
  sessionStorage.setItem(VERIFIER, verifier);
  sessionStorage.setItem(STATE, state);
  const q = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: redirectUri(),
    scope: "openid email",
    state,
    code_challenge: await challenge(verifier),
    code_challenge_method: "S256",
  });
  if (cfg.identityProvider) q.set("identity_provider", cfg.identityProvider);
  window.location.assign(`${cfg.cognitoDomain}/oauth2/authorize?${q}`);
}

export async function completeLogin(cfg: Config): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code) return;
  const expected = sessionStorage.getItem(STATE);
  sessionStorage.removeItem(STATE);
  window.history.replaceState({}, "", "/");
  if (!expected || params.get("state") !== expected) throw new Error("Sign-in state mismatch");
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cfg.clientId,
    code,
    redirect_uri: redirectUri(),
    code_verifier: sessionStorage.getItem(VERIFIER) ?? "",
  });
  sessionStorage.removeItem(VERIFIER);
  const r = await fetch(`${cfg.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) throw new Error(`Sign-in failed (${r.status})`);
  const tokens = (await r.json()) as { id_token: string };
  sessionStorage.setItem(TOKEN, tokens.id_token);
}

export function token(): string | null {
  const t = sessionStorage.getItem(TOKEN);
  if (!t) return null;
  try {
    const payload = JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    if (payload.exp * 1000 < Date.now() + 30_000) {
      sessionStorage.removeItem(TOKEN);
      return null;
    }
  } catch {
    return null;
  }
  return t;
}

export function logout(cfg: Config): void {
  sessionStorage.clear();
  const q = new URLSearchParams({ client_id: cfg.clientId, logout_uri: redirectUri() });
  window.location.assign(`${cfg.cognitoDomain}/logout?${q}`);
}
