// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// In-browser stand-in for the API v2 Lambda (dev:mock only). Mirrors API.md shapes, validation and 409s.
import { CAP_FIELD, CAP_FIELDS, PERIODS } from '../types';
import { ApiError } from '../api';
import type {
  AuditRow, CapFields, CapSource, Counters, GroupRow, Limit, LimitState, Me, Overview, Period, Policy, RateLimits, Usage, UserDetail, UserRow, UserState,
} from '../types';
import { ALIASES, EVENTS, HOUR_SHARE, MODELS, NOW, STATE, TODAY, USER_GROUPS, iso, nextVersion, type Event } from './data';

const ACTOR = 'dave@example.com';
const NAME = /^[A-Za-z0-9._@+-]{1,128}$/;
let reqN = 0;
const rid = () => `mock-${Date.now().toString(36)}-${++reqN}`;
const fail = (status: number, error: string, extra: Record<string, unknown> = {}) => new ApiError(status, error, { request_id: rid(), ...extra });
const round = (n: number) => Math.round(n * 10000) / 10000;

function role(): Me['role'] {
  const q = new URLSearchParams(window.location.search).get('mockrole');
  if (q) sessionStorage.setItem('ocgw-mockrole', q);
  return sessionStorage.getItem('ocgw-mockrole') === 'viewer' ? 'viewer' : 'admin';
}

const zero = (): Counters => ({ usd: 0, input: 0, output: 0, requests: 0 });
function add(c: Counters, e: Counters) {
  c.usd = round(c.usd + e.usd);
  c.input += e.input;
  c.output += e.output;
  c.requests += e.requests;
  return c;
}
const month = TODAY.slice(0, 7);

function resetsAt(p: Period): string {
  const d = new Date(NOW);
  if (p === 'Monthly') return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)).toISOString();
  if (p === 'Daily') return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1)).toISOString();
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours() + 1)).toISOString();
}

function spent(user: string, p: Period): number {
  const today = EVENTS.filter((e) => e.user === user && e.date === TODAY).reduce((a, e) => a + e.usd, 0);
  if (p === 'Hourly') return round(today * HOUR_SHARE[user]);
  if (p === 'Daily') return round(today);
  return round(EVENTS.filter((e) => e.user === user && e.date.startsWith(month)).reduce((a, e) => a + e.usd, 0));
}

function resolveCap(user: string, field: keyof CapFields): { value: number | null; source: CapSource } {
  const o = STATE.overrides[user]?.[field];
  if (o !== undefined && o !== null) return { value: o, source: 'user' };
  let best: { value: number; source: CapSource } | null = null;
  for (const g of USER_GROUPS[user] ?? []) {
    const val = STATE.groups[g]?.[field];
    if (val !== undefined && val !== null && (!best || val > best.value)) best = { value: val, source: `group:${g}` };
  }
  if (best) return best;
  const org = STATE.org[field];
  if (org !== undefined && org !== null) return { value: org, source: 'org' };
  const env = STATE.envDefaults[field];
  return env !== null ? { value: env, source: 'default' } : { value: null, source: null };
}

// A live boost as the API returns it, with in_effect from the mock's own caps.
function boostView(user: string) {
  const b = STATE.boosts[user];
  if (!b || new Date(b.until) <= NOW) return null;
  const v = resolveCap(user, CAP_FIELD[b.period]).value;
  return { ...b, in_effect: v !== null && v > 0 };
}

function limitsFor(user: string): Limit[] {
  const [w1, w2] = STATE.org.warn_pct;
  return PERIODS.map((period) => {
    const { value, source } = resolveCap(user, CAP_FIELD[period]);
    const b = STATE.boosts[user];
    // As the API: a boost on a period with no cap does nothing, so it adds 0 (and shows as not in effect).
    const boost = b && b.period === period && new Date(b.until) > NOW && value !== null && value > 0 ? b.usd : 0;  // never lifts a $0 cap
    const s = spent(user, period);
    const cap = value === null ? null : value + boost;
    const pct = cap === null ? null : cap === 0 ? 100 : round((s / cap) * 100);
    // As the API: an unreadable record has no amount, is blocked where capped and warns where not.
    if (STATE.unreadable.includes(`${user}:${period}`))
      return { period, cap, base: value, source, boost, spent: null, pct: null, state: cap === null ? 'warn' : 'blocked', unreadable: true, resets_at: resetsAt(period) };
    const state: LimitState = pct === null ? 'none' : pct >= 100 ? 'blocked' : pct >= w2 ? 'critical' : pct >= w1 ? 'warn' : 'ok';
    return { period, cap, base: value, source, boost, spent: s, pct, state, unreadable: false, resets_at: resetsAt(period) };
  });
}

const RANK: Record<LimitState, number> = { none: 0, ok: 0, warn: 1, critical: 2, blocked: 3 };
const worst = (ls: Limit[]): UserState => (['ok', 'warn', 'critical', 'blocked'] as UserState[])[Math.max(...ls.map((l) => RANK[l.state]))];

function counters(evts: Event[]): Counters {
  return evts.reduce((c, e) => add(c, e), zero());
}

function groupModels(g: string): { models: string[]; source: 'dynamodb' | 'env' } {
  const m = STATE.groups[g]?.models;
  if (m) return { models: m, source: 'dynamodb' };
  return { models: STATE.envGroupModels[g] ?? [], source: 'env' };
}

function userRow(user: string): UserRow {
  const limits = limitsFor(user);
  const lastEvt = EVENTS.filter((e) => e.user === user).at(-1);
  return {
    user,
    groups: USER_GROUPS[user],
    mtd: counters(EVENTS.filter((e) => e.user === user && e.date.startsWith(month))),
    today_usd: spent(user, 'Daily'),
    hour_usd: spent(user, 'Hourly'),
    limits,
    rpm: resolveCap(user, 'rpm'),
    last_seen: lastEvt ? (lastEvt.date === TODAY ? new Date(NOW.getTime() - 4 * 60000 * (1 + Object.keys(USER_GROUPS).indexOf(user))).toISOString() : `${lastEvt.date}T17:42:00Z`) : null,
    state: worst(limits),
  };
}

// As the API's budget_version: every editable attribute of the BUDGET item, a hand-edited boost included.
function userVersion(u: string): string {
  return JSON.stringify([STATE.overrides[u] ?? {}, STATE.boosts[u] ?? null, STATE.handBoosts.includes(u)]);
}

// As the API's _check_version: a save that carries a version (not null) must match; one without keeps going.
function checkVersion(body: Record<string, unknown>, u: string) {
  if (body.version !== undefined && body.version !== null && body.version !== userVersion(u)) throw fail(409, 'changed by someone else');
}

// As the API: a save that names no editable field is a 400, not a new version and an audit row.
function checkSomething(body: Record<string, unknown>, fields: string[]) {
  if (!fields.some((k) => k in body)) throw fail(400, 'nothing to change');
}

function audit(action: string, target: string, before: object | null, after: object | null, reason?: string | null): AuditRow {
  const row = { at: new Date().toISOString(), actor: ACTOR, action, target, before, after, reason: reason || null };
  STATE.audit.unshift(row);
  return row;
}

function checkNum(body: Record<string, unknown>, field: string, min: number, max: number) {
  const v = body[field];
  if (v === undefined || v === null) return;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) throw fail(400, `${field} must be a number between ${min} and ${max}`, { field });
}
function checkCaps(body: Record<string, unknown>) {
  for (const f of ['monthly_usd', 'daily_usd', 'hourly_usd']) checkNum(body, f, 0, 100000);
  checkNum(body, 'rpm', 1, 100000);
  const zeroCap = ['monthly_usd', 'daily_usd', 'hourly_usd'].some((f) => body[f] === 0);
  if (zeroCap && !(typeof body.reason === 'string' && body.reason.trim())) throw fail(400, 'reason is required when a cap is set to 0', { field: 'reason' });
}
function pick(body: Record<string, unknown>, keys: string[]) {
  const out: Record<string, unknown> = {};
  for (const k of keys) if (k in body) out[k] = body[k];
  return out;
}
function applyFields(target: Record<string, unknown>, patch: Record<string, unknown>) {
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete target[k];
    else target[k] = v;
  }
}

function usage(q: URLSearchParams): Usage {
  const from = q.get('from') ?? `${month}-01`;
  const to = q.get('to') ?? TODAY;
  const gran = q.get('granularity') === 'month' ? 'month' : 'day';
  const by = (q.get('group_by') ?? 'model') as 'model' | 'user' | 'group' | 'tag';
  const fUser = q.get('user');
  const fGroup = q.get('group');
  const fModel = q.get('model');
  const fTag = q.get('tag');
  const evts = EVENTS.filter(
    (e) => e.date >= from && e.date <= to && (!fUser || e.user === fUser) && (!fGroup || USER_GROUPS[e.user].includes(fGroup)) && (!fModel || e.model === fModel) && (!fTag || e.tag === fTag),
  );
  const keysOf = (e: Event): string[] => (by === 'user' ? [e.user] : by === 'group' ? USER_GROUPS[e.user] : by === 'tag' ? [e.tag] : [e.model]);
  const bucket = (d: string) => (gran === 'month' ? `${d.slice(0, 7)}-01` : d);
  const buckets: string[] = [];
  for (let t = Date.parse(`${bucket(from)}T00:00:00Z`); t <= Date.parse(`${to}T00:00:00Z`); ) {
    const d = new Date(t);
    buckets.push(iso(d));
    t = gran === 'month' ? Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) : t + 86400000;
  }
  const series = new Map<string, Map<string, Counters>>();
  const table = new Map<string, Counters>();
  for (const e of evts) {
    for (const k of keysOf(e)) {
      if (!series.has(k)) series.set(k, new Map(buckets.map((b) => [b, zero()])));
      add(series.get(k)!.get(bucket(e.date))!, e);
      add(table.get(k) ?? (table.set(k, zero()), table.get(k)!), e);
    }
  }
  const notes = ['Spend is estimated by the gateway from token counts and list prices. Model spend arrives hourly via usage_sync.'];
  if (by === 'group') notes.push('A user in several groups counts toward each of them, so group totals can add up to more than the total.');
  return {
    series: [...series.entries()].map(([key, pts]) => ({ key, points: [...pts.entries()].map(([t, c]) => ({ t, ...c })) })),
    table: [...table.entries()].map(([key, c]) => ({ key, ...c })).sort((a, b) => b.usd - a.usd),
    total: counters(evts),
    notes,
    tag_dims: ['team', 'project', 'environment'],
    dim: 'team',
  };
}

function route(method: string, path: string, body: Record<string, unknown>): unknown {
  const url = new URL(path, 'http://mock');
  const p = url.pathname.replace(/^\/api\/v2/, '');
  const seg = p.split('/').filter(Boolean).map(decodeURIComponent);
  const isWrite = method !== 'GET';
  if (isWrite && role() !== 'admin') throw fail(403, 'admin role required');

  if (method === 'GET') {
    if (p === '/me') return { email: ACTOR, sub: 'a1b2c3d4-sub', groups: ['ocgw-admin'], role: role(), tag_dims: ['team', 'project', 'environment'], sign_out: true } satisfies Me;
    if (p === '/overview') {
      const monthEvts = EVENTS.filter((e) => e.date.startsWith(month));
      const daily = new Map<string, Record<string, number>>();
      for (const e of monthEvts) {
        const row = daily.get(e.date) ?? (daily.set(e.date, {}), daily.get(e.date)!);
        row[e.model] = round((row[e.model] ?? 0) + e.usd);
      }
      const mix = new Map<string, { usd: number; requests: number }>();
      for (const e of monthEvts) {
        const m = mix.get(e.model) ?? (mix.set(e.model, { usd: 0, requests: 0 }), mix.get(e.model)!);
        m.usd = round(m.usd + e.usd);
        m.requests += e.requests;
      }
      const c = counters(monthEvts);
      const dayOfMonth = NOW.getUTCDate();
      const daysInMonth = new Date(Date.UTC(NOW.getUTCFullYear(), NOW.getUTCMonth() + 1, 0)).getUTCDate();
      const attention: Overview['attention'] = [];
      for (const u of Object.keys(USER_GROUPS)) {
        for (const l of limitsFor(u)) {
          if (l.state === 'critical' || l.state === 'blocked' || l.unreadable) attention.push({ user: u, groups: USER_GROUPS[u], period: l.period, spent: l.spent, cap: l.cap, base: l.base, pct: l.pct, state: l.state, unreadable: l.unreadable });
        }
      }
      attention.sort((a, b) => Number(b.unreadable) - Number(a.unreadable) || (b.pct ?? 0) - (a.pct ?? 0));  // as the API
      return {
        month,
        today: TODAY,
        kpis: {
          mtd_usd: c.usd,
          today_usd: round(monthEvts.filter((e) => e.date === TODAY).reduce((a, e) => a + e.usd, 0)),
          projected_usd: round((c.usd / dayOfMonth) * daysInMonth),
          active_users_today: new Set(monthEvts.filter((e) => e.date === TODAY).map((e) => e.user)).size,
          requests_mtd: c.requests,
          org_budget_usd: 2000,
        },
        daily: [...daily.entries()].map(([date, by_model]) => ({ date, by_model })),
        model_mix: [...mix.entries()].map(([model, v]) => ({ model, ...v })).sort((a, b) => b.usd - a.usd),
        attention,
        generated_at: new Date().toISOString(),
      } satisfies Overview;
    }
    if (p === '/usage') return usage(url.searchParams);
    if (p === '/users') return { users: Object.keys(USER_GROUPS).map(userRow) };
    if (seg[0] === 'users' && seg.length === 2) {
      const u = seg[1];
      if (!USER_GROUPS[u]) throw fail(404, `user ${u} not seen`);
      const row = userRow(u);
      const byModel = new Map<string, Counters>();
      for (const e of EVENTS.filter((e) => e.user === u && e.date.startsWith(month))) add(byModel.get(e.model) ?? (byModel.set(e.model, zero()), byModel.get(e.model)!), e);
      const since = iso(new Date(NOW.getTime() - 29 * 86400000));
      const daily = new Map<string, { usd: number; requests: number }>();
      for (let t = Date.parse(since); t <= NOW.getTime(); t += 86400000) daily.set(iso(new Date(t)), { usd: 0, requests: 0 });
      for (const e of EVENTS.filter((e) => e.user === u && e.date >= since)) {
        const d = daily.get(e.date)!;
        d.usd = round(d.usd + e.usd);
        d.requests += e.requests;
      }
      const models = [...new Set(USER_GROUPS[u].flatMap((g) => groupModels(g).models))];
      return {
        user: u,
        groups: row.groups,
        limits: row.limits,
        rpm: row.rpm,
        boost: boostView(u),
        // As the API: a boost whose end has passed is still recorded until End boost clears it.
        boost_state: boostView(u) ? 'live' : STATE.handBoosts.includes(u) ? 'unreadable' : STATE.boosts[u] ? 'expired' : null,
        override: STATE.overrides[u] ?? {},
        models,
        by_model_mtd: [...byModel.entries()].map(([model, c]) => ({ model, ...c })).sort((a, b) => b.usd - a.usd),
        daily: [...daily.entries()].map(([date, v]) => ({ date, ...v })),
        audit: STATE.audit.filter((a) => a.target === `user:${u}`).slice(0, 50),
        version: userVersion(u),
      } satisfies UserDetail;
    }
    if (p === '/groups') {
      const names = new Set([...Object.values(USER_GROUPS).flat(), ...Object.keys(STATE.groups)]);
      return {
        groups: [...names].sort().map((g): GroupRow => {
          const members = Object.keys(USER_GROUPS).filter((u) => USER_GROUPS[u].includes(g));
          const { models, source } = groupModels(g);
          const { version, ...policy } = STATE.groups[g] ?? { version: null };
          return { group: g, members_seen: members, mtd: counters(EVENTS.filter((e) => members.includes(e.user) && e.date.startsWith(month))), policy, models_effective: models, models_source: source, version };
        }),
      };
    }
    if (p === '/policy') {
      return {
        org: STATE.org,
        env_defaults: STATE.envDefaults,
        boosts: Object.keys(STATE.boosts).flatMap((user) => { const b = boostView(user); return b ? [{ user, ...b, version: userVersion(user) }] : []; }),
      } satisfies Policy;
    }
    if (p === '/models') return { models: MODELS, aliases: ALIASES };
    if (p === '/ratelimits') {
      return {
        interceptor: {
          default: STATE.envDefaults.rpm ?? null,
          org: STATE.org.rpm ?? null,
          groups: Object.fromEntries(Object.entries(STATE.groups).filter(([, g]) => g.rpm).map(([k, g]) => [k, g.rpm!])),
          users: Object.fromEntries(Object.entries(STATE.overrides).filter(([, o]) => o.rpm).map(([k, o]) => [k, o.rpm!])),
        },
        gateway: STATE.gateway,
      } satisfies RateLimits;
    }
    if (p === '/audit') {
      const q = url.searchParams;
      const from = q.get('from');
      const to = q.get('to');
      const actor = q.get('actor');
      const target = q.get('target');
      return {
        rows: STATE.audit.filter((a) => (!from || a.at.slice(0, 10) >= from) && (!to || a.at.slice(0, 10) <= to) && (!actor || a.actor.includes(actor)) && (!target || a.target.includes(target))),
      };
    }
    throw fail(404, `no route GET ${p}`);
  }

  // ---- writes ----
  if (method === 'PUT' && p === '/policy/org') {
    if (body.version !== STATE.org.version) throw fail(409, 'changed by someone else', { current: STATE.org });
    checkSomething(body, [...CAP_FIELDS, 'rpm', 'warn_pct', 'block_message']);
    checkCaps(body);
    if (body.warn_pct !== undefined) {
      const w = body.warn_pct as number[];
      if (!Array.isArray(w) || w.length !== 2 || !(w[0] >= 1 && w[0] < w[1] && w[1] <= 100)) throw fail(400, 'warn_pct must be two ascending numbers in 1..100', { field: 'warn_pct' });
    }
    const before = { ...STATE.org };
    applyFields(STATE.org as unknown as Record<string, unknown>, pick(body, ['monthly_usd', 'daily_usd', 'hourly_usd', 'rpm', 'warn_pct', 'block_message']));
    STATE.org.version = nextVersion();
    return { ok: true, item: STATE.org, audit: audit('set_org_policy', 'org', before, { ...STATE.org }, body.reason as string) };
  }
  if (seg[0] === 'policy' && seg[1] === 'groups' && seg.length === 3) {
    const g = seg[2];
    if (!NAME.test(g)) throw fail(400, 'invalid group name', { field: 'group' });
    const cur = STATE.groups[g];
    // As put_policy: nothing to remove is a 404 before the version check, so a stale client does not retry a 409.
    if (method === 'DELETE' && !cur) throw fail(404, 'no policy to remove');
    if ((body.version ?? null) !== (cur?.version ?? null)) throw fail(409, 'changed by someone else', { current: cur ?? null });
    if (method === 'DELETE') {
      delete STATE.groups[g];
      return { ok: true, item: null, audit: audit('delete_group_policy', `group:${g}`, cur ?? null, null, body.reason as string) };
    }
    checkSomething(body, [...CAP_FIELDS, 'rpm', 'models']);
    checkCaps(body);
    if (body.models !== undefined && body.models !== null) {
      const ids = MODELS.map((m) => m.id);
      for (const m of body.models as string[]) if (!(ids.includes(m) || /^ow\/[A-Za-z0-9._/-]*\*$/.test(m))) throw fail(400, `unknown model ${m}`, { field: 'models' });
    }
    const before = cur ? { ...cur } : null;
    const next = { ...(cur ?? {}) } as Record<string, unknown>;
    applyFields(next, pick(body, ['monthly_usd', 'daily_usd', 'hourly_usd', 'rpm', 'models']));
    next.version = nextVersion();
    STATE.groups[g] = next as (typeof STATE.groups)[string];
    return { ok: true, item: STATE.groups[g], audit: audit('set_group_policy', `group:${g}`, before, { ...next }, body.reason as string) };
  }
  if (seg[0] === 'users' && seg.length === 3) {
    const u = seg[1];
    if (!NAME.test(u)) throw fail(400, 'invalid user name', { field: 'user' });
    const known = () => { if (!USER_GROUPS[u]) throw fail(404, `${u} is not a user of this gateway (no BUDGET item; add them to var.users)`); };  // as _known_budget
    if (!(seg[2] === 'repair' && method === 'POST')) known();  // repair checks its body first, as repair_window
    if (seg[2] === 'limits' && method === 'PUT') {
      checkVersion(body, u);
      checkSomething(body, [...CAP_FIELDS, 'rpm']);
      checkCaps(body);
      const before = STATE.overrides[u] ? { ...STATE.overrides[u] } : null;
      const next = { ...(STATE.overrides[u] ?? {}) } as Record<string, unknown>;
      applyFields(next, pick(body, ['monthly_usd', 'daily_usd', 'hourly_usd', 'rpm']));
      STATE.overrides[u] = next as CapFields;
      return { ok: true, item: next, audit: audit('set_user_limits', `user:${u}`, before, next, body.reason as string) };
    }
    if (seg[2] === 'boost' && method === 'POST') {
      checkVersion(body, u);  // first, as the API
      checkNum(body, 'usd', 0.01, 100000);
      checkNum(body, 'hours', 1, 720);
      if (typeof body.usd !== 'number') throw fail(400, 'usd is required', { field: 'usd' });
      if (typeof body.hours !== 'number' || !Number.isInteger(body.hours)) throw fail(400, 'hours must be a whole number 1..720', { field: 'hours' });
      if (!['Monthly', 'Daily', 'Hourly'].includes(body.period as string)) throw fail(400, 'period must be Monthly, Daily or Hourly', { field: 'period' });
      const per = body.period as Period;
      const base = resolveCap(u, CAP_FIELD[per]).value;
      if (base === null)
        throw fail(400, `${u} has no ${per.toLowerCase()} cap, so a ${per.toLowerCase()} boost would have no effect`, { field: 'period' });
      if (base <= 0) throw fail(400, `${u}'s ${per.toLowerCase()} cap is $0, which blocks them; raise the cap instead of boosting it`, { field: 'period' });
      if (!(typeof body.reason === 'string' && body.reason.trim())) throw fail(400, 'reason is required for a boost', { field: 'reason' });
      const before = STATE.boosts[u] ?? (STATE.handBoosts.includes(u) ? { usd: '<S> "lots"' } : null);
      STATE.handBoosts = STATE.handBoosts.filter((x) => x !== u);  // a grant SETs every boost attribute: no hand edit left
      STATE.boosts[u] = { usd: body.usd, period: body.period as Period, until: new Date(Date.now() + body.hours * 3600000).toISOString(), reason: body.reason };
      return { ok: true, item: STATE.boosts[u], audit: audit('grant_boost', `user:${u}`, before, STATE.boosts[u], body.reason) };
    }
    if (seg[2] === 'repair' && method === 'POST') {
      if (!['Monthly', 'Daily', 'Hourly'].includes(body.period as string)) throw fail(400, 'period must be Monthly, Daily or Hourly', { field: 'period' });
      if (!(typeof body.reason === 'string' && body.reason.trim())) throw fail(400, 'a reason is required to repair a usage record', { field: 'reason' });
      known();
      const key = `${u}:${body.period}`;
      if (!STATE.unreadable.includes(key)) throw fail(409, `${u}'s usage record can be read; nothing to repair`);
      STATE.unreadable = STATE.unreadable.filter((k) => k !== key);
      // As the API: the window's sort key, and the removed value as text.
      const p = new Date().toISOString();
      const window = { Monthly: `MONTH#${p.slice(0, 7)}`, Daily: `DAY#${p.slice(0, 10)}`, Hourly: `HOUR#${p.slice(0, 13)}` }[body.period as Period];
      const item = { window, removed: ['spend_usd'] };
      return { ok: true, item, audit: audit('repair_usage_record', `user:${u}`, { window, spend_usd: '<S> "hand edit"' }, item, body.reason) };
    }
    if (seg[2] === 'boost' && method === 'DELETE') {
      const hand = STATE.handBoosts.includes(u);
      if (!STATE.boosts[u] && !hand) throw fail(404, 'no boost to end');  // before the version, as the API
      checkVersion(body, u);
      const before = STATE.boosts[u] ?? (hand ? { usd: '<S> "lots"' } : null);
      delete STATE.boosts[u];
      STATE.handBoosts = STATE.handBoosts.filter((x) => x !== u);
      return { ok: true, item: null, audit: audit('end_boost', `user:${u}`, before, null, body.reason as string) };
    }
  }
  throw fail(404, `no route ${method} ${p}`);
}

export async function handle(method: string, path: string, body: unknown): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 200));
  // deep copy so callers cannot mutate mock state
  return JSON.parse(JSON.stringify(route(method, path, (body ?? {}) as Record<string, unknown>)));
}
