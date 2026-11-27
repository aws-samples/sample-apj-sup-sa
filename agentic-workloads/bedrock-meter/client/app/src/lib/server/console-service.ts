import { clearServerCache, withServerCache } from "@/lib/server/cache";
import { BackendRequestError, meterFetch } from "@/lib/server/meter-backend";
import type {
  AuditRecord,
  ConsoleUser,
  GlobalQuotaPolicy,
  LimitOverride,
  LimitsSummary,
  ModelTrend,
  ModelTrendMetric,
  ModelTrendPeriod,
  ModelTrendPoint,
  OverviewData,
  QuotaUsage,
  TrendGranularity,
  UsagePoint,
  UserStatus,
} from "@/lib/types";

type BackendUsageSnapshot = {
  username: string;
  user_arn: string;
  as_of: string;
  // Last token-accrual time from the DDB DAY row (matches /usage/aggregate's
  // last_updated). null when there is no activity today.
  last_activity?: string | null;
  day: {
    input_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    output_tokens: number;
    total_tokens: number;
    invocation_count: number;
    limit?: number;
  };
  week: {
    total_tokens: number;
    limit?: number;
  };
  month: {
    total_tokens: number;
    limit?: number;
  };
  state: {
    blocked: boolean;
    blocked_scope?: "DAY" | "WEEK" | "MONTH" | null;
    blocked_reason?: string | null;
    blocked_at?: string | null;
  };
  config: {
    exempt?: boolean;
    warning_threshold_pct?: number;
  };
};

type BackendHistory = {
  // Complete, gap-free bucket keys (UTC) over the window — used to zero-fill.
  buckets?: string[];
  rows: Array<{
    day: string;
    input_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    output_tokens: number;
  }>;
};

type BackendTopUsers = {
  rows: Array<{
    username?: string;
    user_arn: string;
    value: number;
  }>;
};

type BackendBlockedUsers = {
  rows: Array<{
    username?: string;
    user_arn: string;
    blocked_scope?: "DAY" | "WEEK" | "MONTH" | null;
    blocked_reason?: string | null;
    blocked_at?: string | null;
  }>;
};

type BackendUsers = {
  rows: Array<{
    username?: string;
    user_arn: string;
  }>;
};

type BackendAggregateRow = {
  username?: string;
  user_arn: string;
  display_name: string;
  principal_type?: "user" | "role";
  manageable?: boolean;
  status: UserStatus;
  // Active quota-calendar day — what the day card shows. latest_day_* is the last active
  // day (used for "last updated"), which may be earlier than today.
  today_total_tokens: number;
  today_invocation_count: number;
  latest_day: string | null;
  latest_day_total_tokens: number;
  latest_day_invocation_count: number;
  last_updated: string | null;
  state: {
    blocked: boolean;
    blocked_scope?: "DAY" | "WEEK" | "MONTH" | null;
    blocked_reason?: string | null;
    blocked_at?: string | null;
  };
  config: {
    exempt?: boolean;
    warning_threshold_pct?: number;
    has_override?: boolean;
  };
  effective_limits: {
    daily?: number;
    weekly?: number;
    monthly?: number;
  };
  week_total_tokens?: number;
  month_total_tokens?: number;
};

type BackendAggregate = {
  limit: number;
  as_of: string;
  rows: BackendAggregateRow[];
};

type BackendModelTrend = {
  period: ModelTrendPeriod;
  granularity: TrendGranularity;
  metric: ModelTrendMetric;
  top: number;
  models: string[];
  // Complete, gap-free bucket keys (UTC) over the window — used to zero-fill.
  buckets?: string[];
  rows: Array<{
    day: string;
    model_id: string;
    total_tokens: number;
    invocations: number;
  }>;
};

type BackendLimit = {
  username: string;
  user_arn: string;
  effective: {
    dailyTokenLimit?: number;
    weeklyTokenLimit?: number;
    monthlyTokenLimit?: number;
    warningThresholdPct?: number;
    exempt?: boolean;
  };
};

type BackendGlobalQuotaPolicy = GlobalQuotaPolicy & {
  updated?: boolean;
  countersReset?: boolean;
};

type UserFilters = {
  status?: UserStatus | "all";
  q?: string;
  team?: string;
};

type UserMetadata = {
  displayName?: string;
  team?: string;
};

type LimitPatch = Partial<Omit<LimitOverride, "username">>;

const USERNAME_PATTERN = /^[A-Za-z0-9+=,.@_-]{1,64}$/;
// Short BFF read cache: long enough to absorb the multi-second Athena aggregate
// on rapid re-renders, short enough that read-after-write looks instant.
// BFF value cache disabled (0): aggregate/overview/users/limits now derive from fast
// DDB scans, and the one Athena-bound read (model-trend) is covered by the backend's
// own Athena query cache. withServerCache still dedups concurrent in-flight loads, so
// a burst of identical requests shares one load without serving stale values.
const CACHE_TTL_MS = 0;
// Recent Activity on the Overview is a glanceable summary, capped to the latest N.
const OVERVIEW_EVENT_LIMIT = 15;

function envNumber(name: string | string[], defaultValue: number, min: number, max: number): number {
  const names = Array.isArray(name) ? name : [name];
  const raw = names.map((key) => process.env[key]).find((value) => value !== undefined);
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return defaultValue;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

function parseCsvEnv(name: string): string[] {
  return (process.env[name] || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function metadataByUsername(): Record<string, UserMetadata> {
  const raw = process.env.BEDROCK_METER_USER_METADATA;
  if (!raw) return {};

  try {
    return JSON.parse(raw) as Record<string, UserMetadata>;
  } catch {
    return {};
  }
}

function assertUsername(username: string): string {
  if (!USERNAME_PATTERN.test(username)) {
    throw new Error(`Invalid IAM username: ${username}`);
  }
  return username;
}

function usernameFromArn(arn: string): string | undefined {
  const match = arn.match(/:user\/([A-Za-z0-9+=,.@_-]{1,64})$/);
  return match?.[1];
}

function unique(values: Array<string | undefined>): string[] {
  return Array.from(new Set(values.filter(Boolean) as string[]));
}

function quota(used: number, limit?: number): QuotaUsage {
  const safeLimit = Number(limit || 0);
  return {
    used,
    limit: safeLimit,
    percentage: safeLimit > 0 ? Math.round((used / safeLimit) * 100) : 0,
  };
}

function userStatus(snapshot: BackendUsageSnapshot): UserStatus {
  if (snapshot.config.exempt) return "exempt";
  if (snapshot.state.blocked) return "blocked";

  const threshold = snapshot.config.warning_threshold_pct || 80;
  const pressure = [
    quota(snapshot.day.total_tokens, snapshot.day.limit).percentage,
    quota(snapshot.week.total_tokens, snapshot.week.limit).percentage,
    quota(snapshot.month.total_tokens, snapshot.month.limit).percentage,
  ];
  return pressure.some((value) => value >= threshold) ? "warning" : "normal";
}

function toConsoleUser(snapshot: BackendUsageSnapshot): ConsoleUser {
  const metadata = metadataByUsername()[snapshot.username] || {};

  return {
    username: snapshot.username,
    userArn: snapshot.user_arn,
    principalType: "user",
    manageable: true,
    displayName: metadata.displayName,
    team: metadata.team,
    status: userStatus(snapshot),
    blockedScope: snapshot.state.blocked_scope || undefined,
    blockedReason: snapshot.state.blocked_reason || undefined,
    blockedAt: snapshot.state.blocked_at || undefined,
    day: quota(snapshot.day.total_tokens, snapshot.day.limit),
    week: quota(snapshot.week.total_tokens, snapshot.week.limit),
    month: quota(snapshot.month.total_tokens, snapshot.month.limit),
    // Last real token accrual, not the query time — keeps parity with the Users tab.
    lastUpdated: snapshot.last_activity || undefined,
    breakdown: {
      rawInput: snapshot.day.input_tokens,
      cacheRead: snapshot.day.cache_read_tokens,
      cacheWrite: snapshot.day.cache_write_tokens,
      output: snapshot.day.output_tokens,
    },
  };
}

function toUsagePoint(row: BackendHistory["rows"][number]): UsagePoint {
  return {
    day: row.day,
    input: row.input_tokens,
    cacheRead: row.cache_read_tokens,
    cacheWrite: row.cache_write_tokens,
    output: row.output_tokens,
  };
}

function toLimitOverride(limit: BackendLimit): LimitOverride {
  return {
    username: limit.username,
    dailyTokenLimit: limit.effective.dailyTokenLimit || 0,
    weeklyTokenLimit: limit.effective.weeklyTokenLimit || 0,
    monthlyTokenLimit: limit.effective.monthlyTokenLimit || 0,
    warningThresholdPct: limit.effective.warningThresholdPct || 80,
    exempt: Boolean(limit.effective.exempt),
  };
}

function aggregateRowToConsoleUser(row: BackendAggregateRow): ConsoleUser {
  const username = row.username || usernameFromArn(row.user_arn) || row.user_arn;
  const metadata = metadataByUsername()[username] || {};
  const dayLimit = row.effective_limits.daily;
  const weeklyLimit = row.effective_limits.weekly;
  const monthlyLimit = row.effective_limits.monthly;
  return {
    username,
    userArn: row.user_arn,
    principalType: row.principal_type || "user",
    manageable: row.manageable !== false,
    displayName: metadata.displayName,
    team: metadata.team,
    status: row.status,
    blockedScope: row.state.blocked_scope || undefined,
    blockedReason: row.state.blocked_reason || undefined,
    blockedAt: row.state.blocked_at || undefined,
    // Current quota-calendar day, matching User Detail — not the last active day.
    day: quota(row.today_total_tokens ?? 0, dayLimit),
    week: quota(row.week_total_tokens ?? 0, weeklyLimit),
    month: quota(row.month_total_tokens ?? 0, monthlyLimit),
    lastUpdated: row.last_updated || undefined,
    breakdown: { rawInput: 0, cacheRead: 0, cacheWrite: 0, output: 0 },
  };
}

async function discoverUsernames(): Promise<string[]> {
  return withServerCache("discover-usernames", CACHE_TTL_MS, async () => {
    const topLimit = envNumber("BEDROCK_METER_TOP_USERS_LIMIT", 50, 1, 100);
    let users: BackendUsers;
    try {
      users = await meterFetch<BackendUsers>("/users", { query: { limit: topLimit } });
    } catch (error) {
      // Fall back ONLY when /users is genuinely missing (route 404). Auth errors
      // (401/403) and config errors (503) must surface to the caller; the fallback
      // path uses the same credentials and would just fail again.
      if (error instanceof BackendRequestError && error.status === 404) {
        const [top, blocked] = await Promise.all([
          meterFetch<BackendTopUsers>("/usage/top", {
            query: { metric: "totalTokens", period: "month", limit: topLimit },
          }),
          meterFetch<BackendBlockedUsers>("/admin/blocked"),
        ]);
        users = { rows: [...top.rows, ...blocked.rows] };
      } else {
        throw error;
      }
    }
    const configured = [
      ...parseCsvEnv("BEDROCK_METER_USERS"),
      ...parseCsvEnv("BEDROCK_METER_EXTRA_USERS"),
    ].filter((username) => USERNAME_PATTERN.test(username));

    return unique([
      ...configured,
      ...users.rows.map((row) => row.username || usernameFromArn(row.user_arn)),
    ]);
  });
}

async function userSnapshot(username: string): Promise<BackendUsageSnapshot> {
  return meterFetch<BackendUsageSnapshot>(`/usage/${assertUsername(username)}`);
}

async function knownUsers(): Promise<ConsoleUser[]> {
  return withServerCache("known-users", CACHE_TTL_MS, async () => {
    const aggregateLimit = envNumber("BEDROCK_METER_AGGREGATE_LIMIT", 100, 1, 200);
    const aggregate = await meterFetch<BackendAggregate>("/usage/aggregate", {
      query: { limit: aggregateLimit },
    });
    return aggregate.rows
      .map(aggregateRowToConsoleUser)
      .sort((left, right) => right.month.used - left.month.used);
  });
}

export type UserPageFilters = UserFilters & { pageSize?: number; cursor?: string };

export type UserPage = {
  users: ConsoleUser[];
  nextCursor: string | null;
  totalCount: number;
  // All-user day/week/month totals, summed by the backend BEFORE filter/paging — correct
  // regardless of page size or the aggregate top-N cap. Used for the Overview KPI.
  totals: { day: number; week: number; month: number };
  statusCounts: Record<UserStatus, number>;
};

type BackendUserPage = {
  rows: BackendAggregateRow[];
  totals: { day: number; week: number; month: number };
  status_counts?: Partial<Record<UserStatus, number>>;
  all_user_count?: number;
  total_count: number;
  nextCursor: string | null;
};

/** Cursor-paginated, server-filtered/sorted Users page. status + q are applied on the
 *  backend (over all users); team is console-only metadata so it's post-filtered on the
 *  returned page (narrows within the page only). Sort is month-credits desc. */
export async function listUsersPage(filters: UserPageFilters = {}): Promise<UserPage> {
  const query: Record<string, string | number> = {};
  query.page_size = Math.min(200, Math.max(1, Math.trunc(filters.pageSize || 25)));
  if (filters.status && filters.status !== "all") query.status = filters.status;
  if (filters.q?.trim()) query.q = filters.q.trim();
  if (filters.cursor) query.cursor = filters.cursor;

  const page = await meterFetch<BackendUserPage>("/users", { query });
  let users = page.rows.map(aggregateRowToConsoleUser);
  if (filters.team && filters.team !== "all") {
    users = users.filter((user) => user.team === filters.team);
  }
  return {
    users,
    nextCursor: page.nextCursor ?? null,
    totalCount: page.total_count,
    totals: page.totals ?? { day: 0, week: 0, month: 0 },
    statusCounts: {
      normal: page.status_counts?.normal ?? 0,
      warning: page.status_counts?.warning ?? 0,
      blocked: page.status_counts?.blocked ?? 0,
      exempt: page.status_counts?.exempt ?? 0,
    },
  };
}

export async function getUser(username: string): Promise<ConsoleUser> {
  return toConsoleUser(await userSnapshot(username));
}

export async function getUserHistory(
  username: string,
  period = "last30d",
  granularity = "day",
): Promise<UsagePoint[]> {
  const history = await meterFetch<BackendHistory>(`/usage/${assertUsername(username)}/history`, {
    query: { period, granularity },
  });

  // Zero-fill every reported bucket so empty days/hours render as 0 and the time
  // axis stays evenly spaced (mirrors the model-trend chart behavior).
  const byDay = new Map<string, UsagePoint>();
  for (const bucket of history.buckets ?? []) {
    byDay.set(bucket, { day: bucket, input: 0, cacheRead: 0, cacheWrite: 0, output: 0 });
  }
  for (const row of history.rows) {
    byDay.set(row.day, toUsagePoint(row));
  }
  return Array.from(byDay.values()).sort((a, b) => a.day.localeCompare(b.day));
}

const TREND_PERIODS: ModelTrendPeriod[] = [
  "week",
  "month",
  "last7d",
  "last30d",
  "custom",
  "last24h",
  "last48h",
];
const TREND_METRICS: ModelTrendMetric[] = ["totalTokens", "invocations"];
const TREND_GRANULARITIES: TrendGranularity[] = ["day", "hour"];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export async function getModelTrend(
  period: ModelTrendPeriod = "month",
  metric: ModelTrendMetric = "totalTokens",
  top = 10,
  granularity: TrendGranularity = "day",
  dateFrom?: string,
  dateTo?: string,
): Promise<ModelTrend> {
  const safePeriod = TREND_PERIODS.includes(period) ? period : "month";
  const safeMetric = TREND_METRICS.includes(metric) ? metric : "totalTokens";
  const safeGranularity = TREND_GRANULARITIES.includes(granularity) ? granularity : "day";
  const safeTop = Math.min(20, Math.max(1, Math.trunc(Number(top) || 10)));

  // Custom range is day-granularity only; hour stays on its fixed 48h window.
  const isCustom = safePeriod === "custom" && safeGranularity === "day";
  if (isCustom && (!ISO_DATE.test(dateFrom ?? "") || !ISO_DATE.test(dateTo ?? ""))) {
    throw new Error("custom range requires from and to as YYYY-MM-DD");
  }
  const from = isCustom ? dateFrom : undefined;
  const to = isCustom ? dateTo : undefined;

  return withServerCache(
    `model-trend:${safePeriod}:${safeGranularity}:${safeMetric}:${safeTop}:${from ?? ""}:${to ?? ""}`,
    CACHE_TTL_MS,
    async () => {
      const backend = await meterFetch<BackendModelTrend>("/usage/by-model/trend", {
        query: {
          period: safePeriod,
          metric: safeMetric,
          top: safeTop,
          granularity: safeGranularity,
          ...(from ? { from } : {}),
          ...(to ? { to } : {}),
        },
      });

      const valueField = safeMetric === "invocations" ? "invocations" : "total_tokens";

      // Seed every bucket the backend reports with 0 for each model, so empty
      // buckets render as 0 and the time axis stays evenly spaced (no jumps).
      const byDay = new Map<string, ModelTrendPoint>();
      for (const bucket of backend.buckets ?? []) {
        const point = { day: bucket } as ModelTrendPoint;
        for (const model of backend.models) point[model] = 0;
        byDay.set(bucket, point);
      }

      for (const row of backend.rows) {
        // Backend rows are confined to `buckets`; fall back to a fresh point if absent.
        const point = byDay.get(row.day) || ({ day: row.day } as ModelTrendPoint);
        point[row.model_id] = (Number(point[row.model_id]) || 0) + Number(row[valueField] || 0);
        byDay.set(row.day, point);
      }

      return {
        period: backend.period,
        granularity: backend.granularity,
        metric: backend.metric,
        top: backend.top,
        models: backend.models,
        points: Array.from(byDay.values()).sort((left, right) => left.day.localeCompare(right.day)),
      };
    },
  );
}

// All-user daily history from ONE backend DDB scan (GET /usage/history/all), replacing
// the former per-user fan-out (one Athena query per sampled user → ~15s cold). The
// backend sums every user, so there is no sampling — totalUsers === sampledUsers.
async function aggregateHistory(
  totalUsers: number,
): Promise<{ rows: UsagePoint[]; sampledUsers: number; totalUsers: number }> {
  const response = await meterFetch<BackendHistory>("/usage/history/all", { query: { days: 30 } });

  const byDay = new Map<string, UsagePoint>();
  for (const bucket of response.buckets ?? []) {
    byDay.set(bucket, { day: bucket, input: 0, cacheRead: 0, cacheWrite: 0, output: 0 });
  }
  for (const row of response.rows) {
    byDay.set(row.day, toUsagePoint(row));
  }

  return {
    rows: Array.from(byDay.values()).sort((left, right) => left.day.localeCompare(right.day)),
    sampledUsers: totalUsers,
    totalUsers,
  };
}

export async function getOverview(): Promise<OverviewData> {
  return withServerCache("overview", CACHE_TTL_MS, async () => {
    // /users computes totals, status counts, and the month-usage global ordering from the same
    // complete scan. The first five rows are therefore the real top five, not the top five
    // within an arbitrary recent-user cap.
    const [topPage, blocked, history, events, quotaPolicy] = await Promise.all([
      listUsersPage({ pageSize: 5 }),
      meterFetch<BackendBlockedUsers>("/admin/blocked"),
      aggregateHistory(0),
      listAuditRecords({ limit: OVERVIEW_EVENT_LIMIT }),
      meterFetch<BackendGlobalQuotaPolicy>("/admin/settings/quota"),
    ]);

    return {
      quotaTimeZone: quotaPolicy.quotaTimeZone,
      totals: topPage.totals,
      blocked: blocked.rows.length,
      warning: topPage.statusCounts.warning,
      topUsers: topPage.users,
      history: history.rows,
      historyMeta: { sampledUsers: topPage.totalCount, totalUsers: topPage.totalCount },
      events,
    };
  });
}

/** Authoritative blocked set from /admin/blocked (a sparse STATE scan, NOT bounded by the
 *  aggregate top-N). knownUsers() only covers the top BEDROCK_METER_AGGREGATE_LIMIT users by
 *  month credits, so a blocked user outside that window would be missed if we filtered the
 *  aggregate alone. We take /admin/blocked as the source of truth and enrich each with full
 *  usage from the aggregate page when present, else a per-user snapshot. */
export async function getBlockedUsers(): Promise<ConsoleUser[]> {
  return withServerCache("blocked-users", CACHE_TTL_MS, async () => {
    const [blocked, known] = await Promise.all([
      meterFetch<BackendBlockedUsers>("/admin/blocked"),
      knownUsers(),
    ]);
    const byArn = new Map(known.map((user) => [user.userArn, user]));

    const enriched = await Promise.all(
      blocked.rows.map(async (row) => {
        const hit = byArn.get(row.user_arn);
        if (hit) return hit;
        // Outside the aggregate window — fetch the user's full snapshot. Fall back to a
        // minimal blocked entry if the lookup fails (e.g. non-IAM-user arn).
        const username = row.username || usernameFromArn(row.user_arn) || row.user_arn;
        try {
          return await getUser(username);
        } catch {
          const metadata = metadataByUsername()[username] || {};
          return {
            username,
            userArn: row.user_arn,
            principalType: "user",
            manageable: true,
            displayName: metadata.displayName,
            team: metadata.team,
            status: "blocked" as UserStatus,
            blockedScope: row.blocked_scope || undefined,
            blockedReason: row.blocked_reason || undefined,
            blockedAt: row.blocked_at || undefined,
            day: quota(0, 0),
            week: quota(0, 0),
            month: quota(0, 0),
            breakdown: { rawInput: 0, cacheRead: 0, cacheWrite: 0, output: 0 },
          } satisfies ConsoleUser;
        }
      }),
    );
    return enriched.sort((left, right) => right.month.used - left.month.used);
  });
}

export async function getUserLimit(username: string): Promise<LimitOverride> {
  const limit = await meterFetch<BackendLimit>(`/limits/${assertUsername(username)}`);
  return toLimitOverride(limit);
}

export async function getLimitsSummary(): Promise<LimitsSummary> {
  return withServerCache("limits-summary", CACHE_TTL_MS, async () => {
    const [currentLimit, globalPolicy] = await Promise.all([
      meterFetch<BackendLimit>("/limits/me"),
      meterFetch<BackendGlobalQuotaPolicy>("/admin/settings/quota"),
    ]);
    const current = toLimitOverride(currentLimit);
    const usernames = unique([current.username, ...(await discoverUsernames())]);
    const settled = await Promise.allSettled(usernames.map((username) => getUserLimit(username)));

    return {
      current,
      knownUsers: settled
        .filter((result): result is PromiseFulfilledResult<LimitOverride> => result.status === "fulfilled")
        .map((result) => result.value)
        .sort((left, right) => left.username.localeCompare(right.username)),
      globalPolicy,
    };
  });
}

// Reason rides along in the request body so the core API attaches it to the SNS
// payload → audit-writer → AuditTable. The audit log is now system-of-record;
// the BFF no longer keeps its own copy.
// The signed-in operator behind a console write. Forwarded to Core as SigV4-signed headers so
// the audit actor is the person, not the BFF role. Core trusts these ONLY from the admin BFF role.
export type Operator = { email?: string; username?: string };

function operatorHeaders(operator?: Operator): Record<string, string> {
  const headers: Record<string, string> = {};
  if (operator?.email) headers["x-bedrock-meter-operator-email"] = operator.email;
  if (operator?.username) headers["x-bedrock-meter-operator-username"] = operator.username;
  return headers;
}

export async function updateUserLimit(username: string, changes: LimitPatch, reason: string, operator?: Operator): Promise<unknown> {
  const result = await meterFetch(`/limits/${assertUsername(username)}`, {
    method: "PUT",
    body: { ...changes, reason },
    headers: operatorHeaders(operator),
  });
  clearServerCache();
  return result;
}

export async function deleteUserLimit(username: string, reason: string, operator?: Operator): Promise<unknown> {
  const result = await meterFetch(`/limits/${assertUsername(username)}`, {
    method: "DELETE",
    body: { reason },
    headers: operatorHeaders(operator),
  });
  clearServerCache();
  return result;
}

export async function updateQuotaTimeZone(
  quotaTimeZone: string,
  reason: string,
  operator?: Operator,
): Promise<BackendGlobalQuotaPolicy> {
  const result = await meterFetch<BackendGlobalQuotaPolicy>("/admin/settings/quota", {
    method: "PUT",
    body: { quotaTimeZone, reason },
    headers: operatorHeaders(operator),
  });
  clearServerCache();
  return result;
}

export async function forceUnblock(username: string, reason: string, operator?: Operator): Promise<unknown> {
  const result = await meterFetch(`/admin/unblock/${assertUsername(username)}`, {
    method: "POST",
    body: { reason },
    headers: operatorHeaders(operator),
  });
  clearServerCache();
  return result;
}

export type AuditFilters = {
  event?: string;
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

type BackendAuditResponse = {
  records: Array<{
    id: string;
    timestamp: string;
    event: string;
    severity?: string;
    scope?: string;
    actor?: string;
    target?: string;
    targetArn?: string;
    reason?: string;
    before?: string;
    after?: string;
  }>;
  count: number;
  nextCursor?: string | null;
};

function toAuditRecord(record: BackendAuditResponse["records"][number]): AuditRecord {
  return {
    id: record.id,
    timestamp: record.timestamp,
    event: record.event as AuditRecord["event"],
    actor: record.actor || "system",
    target: record.target || "—",
    reason: record.reason || "",
    before: record.before,
    after: record.after,
  };
}

/** Cursor-paginated audit page. Pass filters.cursor (from a prior page's nextCursor)
 *  to fetch older records; nextCursor is null when the window is exhausted. */
export async function listAuditPage(filters: AuditFilters = {}): Promise<AuditPage> {
  const query: Record<string, string | number> = {};
  if (filters.event) query.event = filters.event;
  if (filters.target) query.target = filters.target;
  if (filters.from) query.from = filters.from;
  if (filters.to) query.to = filters.to;
  if (filters.cursor) query.cursor = filters.cursor;
  query.limit = Math.min(200, Math.max(1, Math.trunc(filters.limit || 50)));

  const response = await meterFetch<BackendAuditResponse>("/admin/audit", { query });
  return {
    records: response.records.map(toAuditRecord),
    nextCursor: response.nextCursor ?? null,
  };
}

/** Flat list (no pagination) — used by the Overview's recent-activity strip. */
export async function listAuditRecords(filters: AuditFilters = {}): Promise<AuditRecord[]> {
  const page = await listAuditPage(filters);
  return page.records;
}
