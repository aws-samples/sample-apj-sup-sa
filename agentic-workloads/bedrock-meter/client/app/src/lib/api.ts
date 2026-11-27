import type {
  AuditEvent,
  AuditRecord,
  ConsoleUser,
  GlobalQuotaPolicy,
  LimitOverride,
  LimitsSummary,
  ModelTrend,
  ModelTrendMetric,
  ModelTrendPeriod,
  OverviewData,
  TrendGranularity,
  UsagePoint,
  UserStatus,
} from "@/lib/types";

export type UserFilters = {
  status?: UserStatus | "all";
  q?: string;
  team?: string;
};

export type LimitUpdateInput = {
  username: string;
  changes: Omit<LimitOverride, "username">;
  reason: string;
};

export type ReasonedUserInput = {
  username: string;
  reason: string;
};

export type QuotaTimeZoneUpdateInput = {
  quotaTimeZone: string;
  reason: string;
};

export class ConsoleApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ConsoleApiError";
  }
}

function queryString(values: Record<string, string | number | boolean | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "all" && value !== "") {
      params.set(key, String(value));
    }
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

// Production: the BFF serves the static UI and the API on the same origin, so this is the
// fixed same-origin path "/api/console" — the build artifact bakes no environment value.
// NEXT_PUBLIC_CONSOLE_API_BASE is a DEV-ONLY override (point `npm run dev` at a deployed BFF).
export const API_BASE =
  process.env.NODE_ENV === "development"
    ? process.env.NEXT_PUBLIC_CONSOLE_API_BASE?.replace(/\/+$/, "") || "/api/console"
    : "/api/console";

// Cognito id-token for write calls. Set after login (see lib/auth). Reads send none;
// writes attach it as a Bearer header which the BFF verifies (aws-jwt-verify).
let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  // Allowlist-only console: the BFF requires a verified token on every data path (read too),
  // so attach the Bearer token on all methods, not just writes.
  if (authToken) {
    headers.authorization = `Bearer ${authToken}`;
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : undefined;

  if (!response.ok) {
    const message =
      typeof data === "object" && data && "error" in data
        ? String((data as { error: unknown }).error)
        : `Console API request failed (${response.status})`;
    throw new ConsoleApiError(message, response.status, data);
  }

  return data as T;
}

export async function getOverview(): Promise<OverviewData> {
  return request<OverviewData>("/overview");
}

export type UserPageFilters = UserFilters & { pageSize?: number; cursor?: string };

export type UserPage = {
  users: ConsoleUser[];
  nextCursor: string | null;
  totalCount: number;
};

export async function getUsers(filters: UserPageFilters = {}): Promise<UserPage> {
  return request<UserPage>(
    `/users${queryString({
      status: filters.status,
      q: filters.q,
      team: filters.team,
      pageSize: filters.pageSize,
      cursor: filters.cursor,
    })}`,
  );
}

export async function getUser(username: string): Promise<ConsoleUser> {
  return request<ConsoleUser>(`/users/${encodeURIComponent(username)}`);
}

export type HistoryGranularity = TrendGranularity;

export async function getUserHistory(
  username: string,
  granularity: HistoryGranularity = "day",
): Promise<UsagePoint[]> {
  // hour → backend uses a fixed last-24h window; period is ignored there.
  const period = granularity === "hour" ? "last24h" : "last30d";
  return request<UsagePoint[]>(
    `/users/${encodeURIComponent(username)}/history${queryString({ period, granularity })}`,
  );
}

export type ModelTrendFilters = {
  period: ModelTrendPeriod;
  metric: ModelTrendMetric;
  top: number;
  granularity: TrendGranularity;
  from?: string;
  to?: string;
};

export async function getModelTrend(filters: ModelTrendFilters): Promise<ModelTrend> {
  return request<ModelTrend>(`/stats/model-trend${queryString({ ...filters })}`);
}

export async function getLimits(): Promise<LimitsSummary> {
  return request<LimitsSummary>("/limit-policies");
}

export async function getUserLimit(username: string): Promise<LimitOverride> {
  return request<LimitOverride>(`/limit-policies/${encodeURIComponent(username)}`);
}

export async function saveUserLimits({ username, changes, reason }: LimitUpdateInput) {
  return request(`/limit-policies/${encodeURIComponent(username)}`, {
    method: "PUT",
    body: JSON.stringify({ changes, reason }),
  });
}

export async function deleteUserLimit({ username, reason }: ReasonedUserInput) {
  return request(`/limit-policies/${encodeURIComponent(username)}`, {
    method: "DELETE",
    body: JSON.stringify({ reason }),
  });
}

export async function saveQuotaTimeZone({ quotaTimeZone, reason }: QuotaTimeZoneUpdateInput) {
  return request<GlobalQuotaPolicy & { updated: boolean; countersReset: boolean }>("/settings/quota", {
    method: "PUT",
    body: JSON.stringify({ quotaTimeZone, reason }),
  });
}

export async function forceUnblockUser({ username, reason }: ReasonedUserInput) {
  return request(`/admin/unblock/${encodeURIComponent(username)}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function getBlockedUsers(): Promise<ConsoleUser[]> {
  return request<ConsoleUser[]>("/blocked");
}

export type AuditFilters = {
  event?: AuditEvent | "all";
  target?: string;
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string;
};

export type AuditPage = {
  records: AuditRecord[];
  nextCursor: string | null;
};

export async function getAudit(filters: AuditFilters = {}): Promise<AuditPage> {
  return request<AuditPage>(
    `/audit${queryString({
      event: filters.event,
      target: filters.target,
      from: filters.from,
      to: filters.to,
      limit: filters.limit,
      cursor: filters.cursor,
    })}`,
  );
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong while loading the console data.";
}
