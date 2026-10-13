export interface ConnectConfig {
  /** CCP v2 URL, e.g. https://anycompany-pay-xxx.my.connect.aws/ccp-v2/ */
  ccpUrl: string;
  region: string;
  /** Base URL of the Cases API Gateway (no trailing slash). */
  casesApiUrl?: string;
  /** Base URL of the Chat API Gateway (no trailing slash); client calls `${chatApiUrl}/chat/start`. */
  chatApiUrl?: string;
  /** Base URL of the transaction Search API (no trailing slash); client calls `${searchApiUrl}/transactions`. */
  searchApiUrl?: string;
  /** Base URL of the opt-in Screen-share API (no trailing slash); client calls `${screenShareApiUrl}/screenshare/start`. */
  screenShareApiUrl?: string;
}

let cached: ConnectConfig | null = null;

/**
 * Loads Amazon Connect config from the same runtime file as auth config
 * (written by the container entrypoint from ECS task env vars). Falls back to
 * Vite env vars for local dev. Returns null if Connect isn't configured.
 */
export async function loadConnectConfig(): Promise<ConnectConfig | null> {
  if (cached) return cached;

  try {
    const res = await fetch("/auth-config.json", { cache: "no-store" });
    if (res.ok) {
      const cfg = await res.json();
      if (cfg?.connect?.ccpUrl) {
        cached = {
          ccpUrl: cfg.connect.ccpUrl,
          region: cfg.connect.region ?? "",
          casesApiUrl: cfg.connect.casesApiUrl || undefined,
          chatApiUrl: cfg.connect.chatApiUrl || undefined,
          searchApiUrl: cfg.connect.searchApiUrl || undefined,
          screenShareApiUrl: cfg.connect.screenShareApiUrl || undefined,
        };
        return cached;
      }
    }
  } catch {
    // fall through to env-based config
  }

  const env = (import.meta as unknown as { env: Record<string, string> }).env;
  if (env.VITE_CONNECT_CCP_URL) {
    cached = {
      ccpUrl: env.VITE_CONNECT_CCP_URL,
      region: env.VITE_CONNECT_REGION ?? "",
    };
    return cached;
  }
  return null;
}
