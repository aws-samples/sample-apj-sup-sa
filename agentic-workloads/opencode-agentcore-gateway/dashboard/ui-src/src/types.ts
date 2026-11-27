// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Shapes from dashboard/ui-src/API.md (API v2 contract).

export type Counters = {
  usd: number;
  input: number;
  output: number;
  requests: number;
};
export type Period = 'Monthly' | 'Daily' | 'Hourly';
export const PERIODS: Period[] = ['Monthly', 'Daily', 'Hourly'];
// The limits body field of each period's cap (the API's user and policy items use the same names).
export const CAP_FIELD: Record<Period, 'monthly_usd' | 'daily_usd' | 'hourly_usd'> = { Monthly: 'monthly_usd', Daily: 'daily_usd', Hourly: 'hourly_usd' };
export const CAP_FIELDS = PERIODS.map((p) => CAP_FIELD[p]);  // the three cap fields, in period order
export type CapSource = 'user' | `group:${string}` | 'org' | 'default' | null;
export type LimitState = 'ok' | 'warn' | 'critical' | 'blocked' | 'none';
export type Limit = {
  period: Period;
  cap: number | null;  // the cap the gateway enforces, a live boost included
  base: number | null;  // the cap before any boost (null: no cap; 0: a block, which a boost never lifts)
  source: CapSource;
  boost: number;
  spent: number | null;  // null when the record cannot be read: it has no amount to show
  pct: number | null;
  state: LimitState;
  unreadable: boolean;  // the window's spend record cannot be read: calls are refused when the window has a cap
  resets_at: string;
};
export type Model = { id: string; display_name: string; description: string };

export type Me = { email: string | null; sub: string; sign_out?: boolean; groups: string[]; role: 'admin' | 'viewer'; tag_dims?: string[] };

export type Overview = {
  month: string;
  today: string;
  kpis: {
    mtd_usd: number;
    today_usd: number;
    projected_usd: number;
    active_users_today: number;
    requests_mtd: number;
    org_budget_usd: number | null;
  };
  daily: { date: string; by_model: Record<string, number> }[];
  model_mix: { model: string; usd: number; requests: number }[];
  attention: {
    user: string;
    groups: string[];
    period: Period;
    spent: number | null;
    cap: number | null;
    base: number | null;
    pct: number | null;
    state: LimitState;
    unreadable: boolean;
  }[];
  generated_at: string;
};

export type UsagePoint = { t: string; usd: number; requests: number; input: number; output: number };
export type Usage = {
  series: { key: string; points: UsagePoint[] }[];
  table: ({ key: string } & Counters)[];
  total: Counters;
  notes: string[];
  tag_dims: string[];  // the tag dimensions this gateway rolls up ([] when tags are off)
  dim: string | null;  // the one this answer used
};

export type UserState = 'ok' | 'warn' | 'critical' | 'blocked';
export type Rpm = { value: number | null; source: CapSource };
export type UserRow = {
  user: string;
  removed?: boolean;  // no BUDGET item: removed from the deployment's users, so it cannot call or be edited
  groups: string[];
  mtd: Counters;
  today_usd: number;
  hour_usd: number;
  limits: Limit[];
  rpm: Rpm;
  last_seen: string | null;
  state: UserState;
};

// in_effect: false when the boost's period has no cap (removed after the grant) or a $0 cap (a block), so it changes nothing.
export type Boost = { usd: number; period: Period; until: string; reason: string; in_effect?: boolean };
export type CapFields = { monthly_usd?: number | null; daily_usd?: number | null; hourly_usd?: number | null; rpm?: number | null };

export type AuditRow = {
  at: string;
  actor: string;
  action: string;
  target: string;
  before: object | null;
  after: object | null;
  reason: string | null;
};

export type UserDetail = {
  user: string;
  groups: string[];
  limits: Limit[];
  removed?: boolean;  // no BUDGET item: removed from the deployment's users, so limits and boosts cannot be edited
  boost_state?: 'live' | 'expired' | 'unreadable' | null;  // what the item holds of a boost; End boost clears any of it
  version: string;  // send it back with a limits or boost save: another admin's save in between gives 409
  rpm: Rpm;
  boost: Boost | null;
  override: CapFields;
  models: string[];
  by_model_mtd: ({ model: string } & Counters)[];
  daily: { date: string; usd: number; requests: number }[];
  audit: AuditRow[];
};

export type GroupPolicy = CapFields & { models?: string[] };
export type GroupRow = {
  group: string;
  members_seen: string[];
  mtd: Counters;
  policy: GroupPolicy;
  models_effective: string[];
  models_source: 'dynamodb' | 'env';
  version: string | null;
  /** false for "(no group)": its users get the org policy, so there is nothing to edit */
  editable?: boolean;
};

export type OrgPolicy = CapFields & { warn_pct: [number, number]; block_message?: string; version: string | null };
export type Policy = {
  org: OrgPolicy;
  env_defaults: { monthly_usd: number | null; daily_usd: number | null; hourly_usd: number | null; rpm: number | null };
  boosts: ({ user: string; version?: string } & Boost)[];  // version: the user's limits version, sent back to end the boost
};

export type Price = { input: number; output: number };
export type Models = { models: (Model & { price: Price })[]; aliases: Record<string, string> };

export type GatewayLimit = {
  id: string;
  description: string;
  dimension: string;
  value: string;
  user: string | null;  // the user this entry applies to (null for the "*" default)
  requests_per_minute?: number;
  tokens_per_minute?: number;
  status: string;
};
export type RateLimits = {
  interceptor: { default?: number | null; org: number | null; groups: Record<string, number>; users: Record<string, number> };
  gateway: GatewayLimit[];
};

export type WriteResult<T = unknown> = { ok: true; item: T; audit: AuditRow };
