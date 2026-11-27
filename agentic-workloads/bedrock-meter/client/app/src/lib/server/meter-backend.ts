import { resolveAwsCredentials } from "@/lib/server/aws-credentials";
import { signAwsRequest } from "@/lib/server/sigv4";

export class BackendRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "BackendRequestError";
  }
}

function backendEndpoint(): string {
  const endpoint = process.env.BEDROCK_METER_API_ENDPOINT;
  if (!endpoint) {
    throw new Error("BEDROCK_METER_API_ENDPOINT is not configured.");
  }
  return endpoint.replace(/\/+$/, "");
}

function regionFromEndpoint(endpoint: string): string | undefined {
  const host = new URL(endpoint).host;
  return host.match(/\.execute-api\.([^.]+)\.amazonaws\.com/)?.[1];
}

export function meterRegion(): string {
  return (
    process.env.BEDROCK_METER_AWS_REGION ||
    process.env.AWS_REGION ||
    process.env.AWS_DEFAULT_REGION ||
    regionFromEndpoint(backendEndpoint()) ||
    "ap-northeast-2"
  );
}

function appendQuery(path: string, query?: Record<string, string | number | boolean | undefined>) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  // URLSearchParams encodes space as '+', but SigV4 canonicalQuery (which re-reads
  // url.searchParams and RFC3986-encodes) signs spaces as '%20'. The wire URL must match the
  // signed form, so normalize '+' → '%20' (toString only emits '+' for spaces; a literal '+'
  // is already '%2B'). Otherwise any query value with a space (Users search, audit target)
  // signs as %20 but transmits '+' → API Gateway canonicalizes to %2B → signature mismatch → 403.
  const serialized = params.toString().replace(/\+/g, "%20");
  return serialized ? `${path}?${serialized}` : path;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function meterFetch<T>(
  path: string,
  options: {
    method?: "GET" | "PUT" | "POST" | "DELETE";
    query?: Record<string, string | number | boolean | undefined>;
    body?: unknown;
    // Extra request headers to include in the SigV4 SIGNED set (so Core can trust them as
    // tamper-evident). Used to forward the operator identity on console writes.
    headers?: Record<string, string>;
  } = {},
): Promise<T> {
  const method = options.method || "GET";
  const body = options.body === undefined ? "" : JSON.stringify(options.body);
  const url = new URL(appendQuery(path, options.query), backendEndpoint());
  const headers = signAwsRequest({
    method,
    url,
    region: meterRegion(),
    service: "execute-api",
    body,
    credentials: resolveAwsCredentials(),
    extraHeaders: {
      ...(body ? { "content-type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
  });

  const response = await fetch(url, {
    method,
    body: body || undefined,
    headers,
    cache: "no-store",
  });
  const parsed = await parseResponseBody(response);

  if (!response.ok) {
    const detailMessage =
      typeof parsed === "object" && parsed && "error" in parsed
        ? String((parsed as { error: unknown }).error)
        : response.statusText;
    throw new BackendRequestError(detailMessage || "Backend request failed", response.status, parsed);
  }

  return parsed as T;
}
