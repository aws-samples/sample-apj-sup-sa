export interface StoredTokens {
  idToken: string;
  accessToken: string;
  refreshToken?: string;
  /** epoch milliseconds */
  expiresAt: number;
}

export interface IdClaims {
  email?: string;
  "cognito:groups"?: string[];
  [key: string]: unknown;
}

const KEY = "anycompany-pay.auth.tokens";

export function saveTokens(tokens: StoredTokens): void {
  localStorage.setItem(KEY, JSON.stringify(tokens));
}

export function loadTokens(): StoredTokens | null {
  const raw = localStorage.getItem(KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredTokens;
  } catch {
    return null;
  }
}

export function clearTokens(): void {
  localStorage.removeItem(KEY);
}

/** Decodes a JWT payload without verifying the signature (tokens come directly from Cognito over HTTPS). */
export function decodeJwt(token: string): IdClaims | null {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(decodeURIComponent(escape(json))) as IdClaims;
  } catch {
    return null;
  }
}
