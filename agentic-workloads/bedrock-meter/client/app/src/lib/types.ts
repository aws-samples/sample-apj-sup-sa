export type UserStatus = "normal" | "warning" | "blocked" | "exempt";
export type AuditEvent =
  | "LIMIT_CHANGED"
  | "LIMIT_REMOVED"
  | "QUOTA_TIMEZONE_CHANGED"
  | "EXEMPT_CHANGED"
  | "MANUAL_UNBLOCK"
  | "REEVALUATE_UNBLOCK"
  | "RESOLVED"
  | "WARNING"
  | "BLOCKED";

export type QuotaUsage = {
  used: number;
  limit: number;
  percentage: number;
};

export type TokenBreakdown = {
  rawInput: number;
  cacheRead: number;
  cacheWrite: number;
  output: number;
};

export type ConsoleUser = {
  username: string;
  userArn: string;
  principalType: "user" | "role";
  manageable: boolean;
  displayName?: string;
  team?: string;
  status: UserStatus;
  blockedScope?: "DAY" | "WEEK" | "MONTH";
  blockedReason?: string;
  blockedAt?: string;
  day: QuotaUsage;
  week: QuotaUsage;
  month: QuotaUsage;
  lastUpdated?: string;
  breakdown: TokenBreakdown;
};

export type UsagePoint = {
  day: string;
  input: number;
  cacheRead: number;
  cacheWrite: number;
  output: number;
};

export type ModelTrendMetric = "totalTokens" | "invocations";
// Day uses calendar/relative periods + custom; hour uses sliding windows (last24h/48h/7d).
export type ModelTrendPeriod =
  | "week"
  | "month"
  | "last7d"
  | "last30d"
  | "custom"
  | "last24h"
  | "last48h";
export type TrendGranularity = "day" | "hour";

// Pivoted for charting: one entry per bucket, with one numeric key per model id
// (plus "other" when the backend folds the long tail). `day` is the x-axis —
// "2026-06-05" for day granularity, "2026-06-05T13:00" for hour.
export type ModelTrendPoint = { day: string } & Record<string, number | string>;

export type ModelTrend = {
  period: ModelTrendPeriod;
  granularity: TrendGranularity;
  metric: ModelTrendMetric;
  top: number;
  models: string[];
  points: ModelTrendPoint[];
};

export type AuditRecord = {
  id: string;
  timestamp: string;
  event: AuditEvent;
  actor: string;
  target: string;
  reason: string;
  before?: string;
  after?: string;
};

export type LimitOverride = {
  username: string;
  dailyTokenLimit: number;
  weeklyTokenLimit: number;
  monthlyTokenLimit: number;
  warningThresholdPct: number;
  exempt: boolean;
};

export type GlobalQuotaPolicy = {
  mode: "CALENDAR";
  quotaTimeZone: string;
  quotaTimeZoneVersion: number;
};

export type OverviewData = {
  quotaTimeZone: string;
  totals: {
    day: number;
    week: number;
    month: number;
  };
  blocked: number;
  warning: number;
  topUsers: ConsoleUser[];
  history: UsagePoint[];
  historyMeta: {
    sampledUsers: number;
    totalUsers: number;
  };
  events: AuditRecord[];
};

export type LimitsSummary = {
  // `current` is the caller's own limit. When the console BFF calls Core as its execution role
  // (not an IAM user), /limits/me has no IAM username → username is null. knownUsers are always
  // resolved IAM users, so their username stays a string.
  current: Omit<LimitOverride, "username"> & { username: string | null };
  knownUsers: LimitOverride[];
  globalPolicy: GlobalQuotaPolicy;
};
