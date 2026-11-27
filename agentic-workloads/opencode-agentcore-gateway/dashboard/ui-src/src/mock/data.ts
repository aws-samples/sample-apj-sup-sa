// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Mock data for `npm run dev:mock`. Deterministic, generated relative to today (UTC).
import type { AuditRow, Boost, CapFields, GatewayLimit, GroupPolicy, Model, OrgPolicy, Period, Price } from '../types';

export const MODELS: (Model & { price: Price })[] = [
  { id: 'ow/kimi-k3', display_name: 'Kimi K3', description: 'Moonshot AI, open weight on Amazon Bedrock', price: { input: 3.3, output: 16.5 } },
  { id: 'ow/glm-5.3', display_name: 'GLM 5.3', description: 'Z.ai, open weight on Amazon Bedrock', price: { input: 1.848, output: 5.808 } },
  { id: 'ow/deepseek-v3.2', display_name: 'DeepSeek V3.2', description: 'DeepSeek, open weight on Amazon Bedrock', price: { input: 0.62, output: 1.85 } },
  { id: 'ow/glm-5', display_name: 'GLM 5', description: 'Z.ai, open weight on Amazon Bedrock', price: { input: 1.0, output: 3.2 } },
];
export const ALIASES: Record<string, string> = {
  kimi: 'ow/kimi-k3',
};

export const USER_GROUPS: Record<string, string[]> = {
  alice: ['ocgw-team-a'],
  bob: ['ocgw-team-b'],
  carol: ['ocgw-team-a', 'ocgw-team-b'],
  dave: ['ocgw-admin'],
};
const USER_MODELS: Record<string, { model: string; weight: number }[]> = {
  alice: [{ model: 'ow/kimi-k3', weight: 1.0 }, { model: 'ow/glm-5.3', weight: 0.6 }, { model: 'ow/deepseek-v3.2', weight: 0.4 }],
  bob: [{ model: 'ow/glm-5.3', weight: 1.0 }, { model: 'ow/deepseek-v3.2', weight: 0.5 }],
  carol: [{ model: 'ow/glm-5.3', weight: 0.8 }, { model: 'ow/kimi-k3', weight: 0.7 }, { model: 'ow/deepseek-v3.2', weight: 0.3 }],
  dave: [{ model: 'ow/glm-5', weight: 0.5 }, { model: 'ow/glm-5.3', weight: 0.9 }, { model: 'ow/glm-5.3', weight: 0.3 }],
};
const USER_TAGS: Record<string, string[]> = {
  alice: ['project=billing-api', 'project=checkout'],
  bob: ['project=checkout'],
  carol: ['project=billing-api', 'team=platform'],
  dave: ['team=platform'],
};
const USER_SCALE: Record<string, number> = { alice: 1.0, bob: 1.6, carol: 1.2, dave: 0.5 };

export type Event = {
  user: string;
  date: string;
  model: string;
  tag: string;
  input: number;
  output: number;
  requests: number;
  usd: number;
};

// mulberry32: small seeded PRNG so the mock looks the same on every reload.
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const iso = (d: Date) => d.toISOString().slice(0, 10);
export const NOW = new Date();
export const TODAY = iso(NOW);
const DAY_MS = 86400000;

function price(model: string) {
  return MODELS.find((m) => m.id === model)!.price;
}

export const EVENTS: Event[] = (() => {
  const r = rng(42);
  const out: Event[] = [];
  const start = new Date(Date.UTC(NOW.getUTCFullYear() - 1, NOW.getUTCMonth() - 1, 1));
  for (let t = start.getTime(); t <= NOW.getTime(); t += DAY_MS) {
    const d = new Date(t);
    const date = iso(d);
    const weekday = d.getUTCDay() !== 0 && d.getUTCDay() !== 6;
    // adoption grows over the year
    const ramp = 0.25 + 0.75 * Math.min(1, (t - start.getTime()) / (330 * DAY_MS));
    for (const [user, models] of Object.entries(USER_MODELS)) {
      if (user === 'carol' && t < NOW.getTime() - 200 * DAY_MS) continue; // joined later
      for (const { model, weight } of models) {
        const base = (weekday ? 1 : 0.15) * ramp * weight * USER_SCALE[user];
        const requests = Math.round(base * (20 + r() * 60));
        if (requests === 0) continue;
        const input = Math.round(requests * (900 + r() * 2200));
        const output = Math.round(requests * (350 + r() * 900));
        const p = price(model);
        const usd = (input * p.input + output * p.output) / 1e6;
        const tags = USER_TAGS[user];
        out.push({ user, date, model, tag: tags[Math.floor(r() * tags.length)], input, output, requests, usd: Math.round(usd * 10000) / 10000 });
      }
    }
  }
  return out;
})();

// share of today's spend that happened in the current hour
export const HOUR_SHARE: Record<string, number> = { alice: 0.11, bob: 0.22, carol: 0.09, dave: 0.05 };

const sumUsd = (pred: (e: Event) => boolean) => EVENTS.filter(pred).reduce((a, e) => a + e.usd, 0);
const month = TODAY.slice(0, 7);
const mtd = (u: string) => sumUsd((e) => e.user === u && e.date.startsWith(month));
const today = (u: string) => sumUsd((e) => e.user === u && e.date === TODAY);

let v = 1;
export const nextVersion = () => `v${++v}-${Date.now().toString(36)}`;

export type State = {
  org: OrgPolicy;
  envDefaults: { monthly_usd: number | null; daily_usd: number | null; hourly_usd: number | null; rpm: number | null };
  groups: Record<string, GroupPolicy & { version: string | null }>;
  envGroupModels: Record<string, string[]>;
  overrides: Record<string, CapFields>;
  boosts: Record<string, Boost>;
  handBoosts: string[];  // users whose BUDGET item holds boost values the API reads as a hand edit, until End boost
  unreadable: string[];  // "user:Period" windows whose usage record cannot be read (a hand edit), until repaired
  gateway: GatewayLimit[];
  audit: AuditRow[];
};

const ALICE_MONTHLY = Math.ceil(mtd('alice') / 0.97);
const BOB_DAILY = Math.floor(today('bob') * 0.93 * 100) / 100;
const ago = (mins: number) => new Date(NOW.getTime() - mins * 60000).toISOString();

export const STATE: State = {
  org: { monthly_usd: 400, daily_usd: 40, hourly_usd: null, rpm: 60, warn_pct: [75, 95], block_message: 'You have reached your opencode gateway budget. Ask #ai-platform for a boost.', version: 'v1-org' },
  envDefaults: { monthly_usd: 200, daily_usd: 25, hourly_usd: 8, rpm: 60 },
  groups: {
    'ocgw-team-b': { monthly_usd: 500, rpm: 90, models: ['ow/glm-5.3', 'ow/deepseek-v3.2'], version: 'v1-g2' },
    'ocgw-admin': { monthly_usd: 1000, daily_usd: 100, version: 'v1-g3' },
  },
  envGroupModels: {
    'ocgw-team-a': ['ow/*'],
    'ocgw-team-b': ['ow/glm-*'],
    'ocgw-admin': ['ow/glm-*', 'ow/*'],
  },
  overrides: {
    // tuned from the generated data so the mock shows each state
    alice: { monthly_usd: ALICE_MONTHLY },
    bob: { daily_usd: BOB_DAILY, rpm: 30 },
  },
  handBoosts: ['bob'],  // so the mock shows the "boost values that cannot be read" note and its End boost action
  unreadable: ['bob:Hourly'],  // so the mock shows an unreadable row and its Repair record action
  boosts: {
    carol: { usd: 50, period: 'Monthly' as Period, until: new Date(NOW.getTime() + 3 * DAY_MS).toISOString(), reason: 'Release week for the eSIM API migration' },
  },
  gateway: [
    { id: 'rl-users', description: 'ocgw requests and tokens per minute per user', dimension: 'jwt:email', value: '*', user: null, requests_per_minute: 120, tokens_per_minute: 400000, status: 'ACTIVE' },
    { id: 'rl-users', description: 'ocgw requests and tokens per minute per user', dimension: 'jwt:email', value: 'bob', user: 'bob', requests_per_minute: 30, tokens_per_minute: 150000, status: 'ACTIVE' },
    { id: 'rl-users', description: 'ocgw requests and tokens per minute per user', dimension: 'jwt:email', value: 'carol', user: 'carol', requests_per_minute: 60, tokens_per_minute: 400000, status: 'UPDATING' },
  ],
  audit: [
    { at: ago(35), actor: 'dave@example.com', action: 'grant_boost', target: 'user:carol', before: null, after: { usd: 50, period: 'Monthly', hours: 72 }, reason: 'Release week for the eSIM API migration' },
    { at: ago(180), actor: 'dave@example.com', action: 'set_user_limits', target: 'user:bob', before: { daily_usd: 20 }, after: { daily_usd: BOB_DAILY, rpm: 30 }, reason: 'Keep bob under the daily cap during load testing' },
    { at: ago(60 * 26), actor: 'dave@example.com', action: 'set_group_policy', target: 'group:ocgw-team-b', before: { monthly_usd: 300 }, after: { monthly_usd: 500, rpm: 90, models: ['ow/glm-5.3', 'ow/deepseek-v3.2'] }, reason: null },
    { at: ago(60 * 50), actor: 'priya@example.com', action: 'set_org_policy', target: 'org', before: { monthly_usd: 300, warn_pct: [80, 95] }, after: { monthly_usd: 400, warn_pct: [75, 95] }, reason: 'Q4 budget approved' },
    { at: ago(60 * 75), actor: 'dave@example.com', action: 'set_user_limits', target: 'user:alice', before: null, after: { monthly_usd: ALICE_MONTHLY }, reason: 'Pilot cap for the open-weight trial' },
  ],
};
