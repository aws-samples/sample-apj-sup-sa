export interface AuthConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  /** Cognito hosted UI domain, e.g. anycompany-pay-xxx.auth.ap-southeast-1.amazoncognito.com */
  domain: string;
}

let cached: AuthConfig | null = null;

/**
 * Loads Cognito config at runtime. In the deployed container the config is
 * written to /auth-config.json by the container entrypoint (populated from ECS
 * task environment variables). For local dev it falls back to Vite env vars.
 */
export async function loadAuthConfig(): Promise<AuthConfig> {
  if (cached) return cached;

  try {
    const res = await fetch("/auth-config.json", { cache: "no-store" });
    if (res.ok) {
      const cfg = (await res.json()) as AuthConfig;
      if (cfg.clientId && cfg.domain) {
        cached = normalize(cfg);
        return cached;
      }
    }
  } catch {
    // fall through to env-based config
  }

  const env = (import.meta as unknown as { env: Record<string, string> }).env;
  cached = normalize({
    region: env.VITE_COGNITO_REGION ?? "",
    userPoolId: env.VITE_COGNITO_USER_POOL_ID ?? "",
    clientId: env.VITE_COGNITO_CLIENT_ID ?? "",
    domain: env.VITE_COGNITO_DOMAIN ?? "",
  });
  return cached;
}

function normalize(cfg: AuthConfig): AuthConfig {
  return {
    ...cfg,
    domain: cfg.domain.replace(/^https?:\/\//, "").replace(/\/+$/, ""),
  };
}

export function isAuthConfigured(cfg: AuthConfig): boolean {
  return Boolean(cfg.clientId && cfg.domain);
}

export function hostedUiBase(cfg: AuthConfig): string {
  return `https://${cfg.domain}`;
}
