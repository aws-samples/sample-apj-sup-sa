// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type {
  Delivery,
  DeliveryChanges,
  DeliveryList,
  DeliveryTest,
  Digest,
  DigestChannel,
  DigestChannelList,
  NewDelivery,
  WatchedNode,
  SampleDataResult,
  Andon,
  AppConfig,
  AuditItem,
  ChatLimits,
  ChatStatus,
  EntityHygiene,
  EntryHistory,
  FeedbackItem,
  GraphNode,
  Health,
  ImportReport,
  InsightDetail,
  MergeReport,
  RadarEntryHit,
  RadarTag,
  InsightFacets,
  InsightPage,
  KnowledgeDocument,
  Me,
  ModelSpec,
  Module,
  ModulePrompt,
  PromptVersion,
  Skill,
  SkillSummary,
  ModuleRun,
  Nav,
  Neighborhood,
  Passage,
  Product,
  Radar,
  RadarDiffChanges,
  RadarEntry,
  RadarSchema,
  RadarTemplate,
  RadarVersion,
  RadarView,
  SearchHit,
  Source,
  Suggestion,
  UploadTicket,
  User,
} from './types';
import { storedText } from '@/lib/html';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    /** Seconds to wait before trying again (a 429 or a 503), or null when the API did not say. */
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /**
   * False for a 401 that no dialog announced, because the user chose "Not now" on it: the caller's
   * error message is then the only sign that the action failed.
   */
  announced = true;

  /**
   * The session ended: the app shows one "Your session has expired" dialog for it, so a caller's
   * error toast would only stack up behind that dialog saying "Unauthorized".
   */
  get sessionEnded(): boolean {
    return this.status === 401;
  }

  /**
   * No answer came back (a timeout, a dropped connection, a gateway error page): a write may or may
   * not have been stored, so the caller must check before it offers to try again (r3 F3-014).
   */
  get uncertain(): boolean {
    return UNCERTAIN.has(this.code);
  }
}

/** How many entries a refused shrinking change had and would leave. */
export interface Shrink {
  before: number;
  after: number;
}

/** The API's own rule for a change that must be confirmed (radars/service.py `_guard_shrink`). */
export const SHRINK_MIN_REMOVED = 3;
export function needsShrinkConfirm(before: number, removed: number): boolean {
  return removed >= SHRINK_MIN_REMOVED && removed * 2 > before;
}

/**
 * The counts of a change the API refused because it would remove most of a radar's entries
 * (`shrinking_import`, `shrinking_save`, `shrinking_rollback`): it is sent again with allow_empty
 * once the user has confirmed them. Null for any other error. The API sends these as a
 * `validation_failed` error with the specific code in `details.code` (core/errors.py `to_body`), so
 * that is where it is read (CR3-05).
 */
export function shrinkOf(err: unknown, code: 'shrinking_import' | 'shrinking_save' | 'shrinking_rollback'): Shrink | null {
  if (!(err instanceof ApiError) || err.details.code !== code) return null;
  const { before, after } = err.details;
  return typeof before === 'number' && typeof after === 'number' ? { before, after } : null;
}

/** 503s a GET is not sent again for: they last until an administrator acts, or until UTC midnight. */
const NO_RETRY = new Set(['genai_disabled', 'budget_exhausted']);

/** The codes of a request that got no answer from the API itself. */
const UNCERTAIN = new Set(['timeout', 'network', 'gateway']);

/** The least time between two profile re-reads asked for by role refusals. */
export const FORBIDDEN_REREAD_MS = 60_000;

/** How long a request waits for an answer: API Gateway gives up after 30 seconds anyway (r3 F3-016). */
export const REQUEST_TIMEOUT_MS = 40_000;

/** What a request that got no answer says; a write adds that it may or may not have been saved. */
export function noAnswerMessage(code: 'timeout' | 'network' | 'gateway', write: boolean, status = 0): string {
  const what =
    code === 'timeout'
      ? 'The server did not answer in time.'
      : code === 'network'
        ? navigator.onLine === false
          ? 'No connection: you are offline. Check your network and try again.'
          : 'No connection: the server could not be reached. Check your network and try again.'
        : `The server did not answer properly (${status}).`;
  return write ? `${what} The change may or may not have been saved: reload the page to check before you try again.` : what;
}

/**
 * Seconds from `details.retry_after` or the Retry-After header (same-origin, so it is readable).
 * AI features paused by an administrator (`genai_disabled`) have nothing to wait for: never a wait.
 */
export function retryAfterOf(status: number, details: Record<string, unknown>, headers: Headers, code = ''): number | null {
  if ((status !== 429 && status !== 503) || code === 'genai_disabled') return null;
  const given = details.retry_after;
  if (typeof given === 'number' && Number.isFinite(given) && given >= 0) return Math.round(given);
  const header = headers.get('retry-after');
  // Number(null) is 0, which would read as "try again in a moment" for every throttle without the header.
  const seconds = header === null ? Number.NaN : Number(header);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.round(seconds) : null;
}

/**
 * An HTML 403 comes from the web application firewall. Its rules block content that looks like an
 * attack, but also too many requests from one address (an office behind one NAT) and addresses on a
 * reputation list, and the answer does not say which (r3 F3-015), so this says neither.
 */
export const BLOCKED_MESSAGE =
  'The firewall in front of the app blocked this request. Wait a minute and try again. If it keeps happening, remove anything ' +
  'that looks like code (HTML or script tags, a path such as ../) from what you sent, or ask an administrator.';

type Query = Record<string, string | number | undefined>;
type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** "type/uid" -> "type/uid" with each part URL-encoded (node ids travel as two path segments). */
export function nodePath(id: string): string {
  const [type = '', uid = ''] = id.split('/', 2);
  // encodeURIComponent leaves '.' alone, and a '.' or '..' segment would be resolved by the URL
  // parser into another API path; no node id is made only of dots, so such a part becomes '-'.
  const part = (p: string) => (/^\.+$/.test(p) ? '-' : encodeURIComponent(p));
  return `${part(type)}/${part(uid)}`;
}

const seg = encodeURIComponent;

/** The most users one GET /api/users page returns (the backend's cap). */
export const USERS_LIMIT = 500;
/** The most runs one page of GET /api/runs returns (the backend's cap). */
export const RUNS_LIMIT = 100;
/** Pages read when following a list's cursor; a list longer than that says it is incomplete. */
export const MAX_PAGES = 20;

/** How long a retried GET waits when the API did not say, and the longest it ever waits. */
export const RETRY_BASE_MS = 250;
export const RETRY_MAX_MS = 2000;

/**
 * A whole list, read page by page; `truncated` when it had more than MAX_PAGES pages, or when a
 * page after the first could not be read (the pages already read are kept rather than thrown away).
 */
export interface Listing<T> {
  items: T[];
  truncated?: boolean;
}

/** Every call the app makes, same-origin under /api (CloudFront routes it to the API). */
/** A list query with its field filters as `filter.<key>=a,b` (a filter with no values is left out). */
function withFilters(query: Query, filters: Record<string, string[]> | undefined): Query {
  const flat: Query = { ...query };
  for (const [key, values] of Object.entries(filters ?? {})) if (values.length) flat[`filter.${key}`] = values.join(',');
  return flat;
}

export class ApiClient {
  /** Called on every 401 with its error code: usually the session has expired and the app asks the user
   * to sign in again; `account_disabled` means signing in would not help. */
  private unauthorized: ((code: string) => boolean) | undefined = undefined;

  /** Sets what happens on a 401: the handler says whether it told the user (a dialog). */
  setUnauthorizedHandler(handler: (code: string) => boolean): void {
    this.unauthorized = handler;
  }

  /** Called when a response says AI features are paused, so the app can re-read /api/config. */
  private genaiDisabled: (() => void) | undefined = undefined;

  setGenaiDisabledHandler(handler: () => void): void {
    this.genaiDisabled = handler;
  }

  /** Called on a 403, so the app can re-read /api/me: the caller's role may have been changed. */
  private forbidden: (() => void) | undefined = undefined;

  setForbiddenHandler(handler: () => void): void {
    this.forbidden = handler;
  }

  /** When a role refusal last asked for the profile to be read again. */
  private lastForbidden = Number.NEGATIVE_INFINITY;

  constructor(
    private readonly token: () => string | undefined,
    private readonly fetcher: Fetch = (i, init) => fetch(i, init),
    private readonly base = '',
  ) {}

  /**
   * One request. A GET that the API answers with 503 "the service is busy" is sent again once after
   * the wait it asked for: a read is idempotent, and a cold Lambda or a brief throttle is the usual
   * cause, so a retry is cheaper than every page on screen turning into an error.
   */
  async raw(method: string, path: string, body?: unknown, query?: Query): Promise<Response> {
    try {
      return await this.send(method, path, body, query);
    } catch (err) {
      // Paused AI features and a closed daily budget do not come back in a second or two.
      if (!(method === 'GET' && err instanceof ApiError && err.status === 503) || NO_RETRY.has(err.code)) throw err;
      await this.wait(err.retryAfter === null ? RETRY_BASE_MS : Math.min(1000 * err.retryAfter, RETRY_MAX_MS));
      return this.send(method, path, body, query);
    }
  }

  private async send(method: string, path: string, body?: unknown, query?: Query): Promise<Response> {
    const qs = query
      ? '?' +
        Object.entries(query)
          .filter(([, v]) => v !== undefined && v !== '')
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join('&')
      : '';
    const headers: Record<string, string> = { accept: 'application/json' };
    const token = this.token();
    if (token) headers.authorization = `Bearer ${token}`;
    if (body !== undefined) headers['content-type'] = 'application/json';
    const write = method !== 'GET';
    let res: Response;
    try {
      res = await this.fetcher(`${this.base}${path}${qs === '?' ? '' : qs}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        // A stalled connection used to leave a spinner (and a read-only table) up for ever.
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (err) {
      // The browser's own words ("Failed to fetch", "Load failed") said nothing a person could act on.
      const code = err instanceof DOMException && err.name === 'TimeoutError' ? 'timeout' : 'network';
      throw new ApiError(0, code, noAnswerMessage(code, write), {}, null);
    }
    if (!res.ok) {
      let data: Record<string, unknown> = {};
      let json = true;
      try {
        const parsed: unknown = await res.json();
        // Only the API's own answer (an object with a string code) counts as JSON. Anything else, such as null,
        // a list, or API Gateway's {"message": "Service Unavailable"}, is handled like a non-JSON body, so the
        // blocked and gateway answers apply (scan cycles 20 to 22).
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) data = parsed as Record<string, unknown>;
        if (typeof data.code !== 'string') json = false;
      } catch {
        json = false; // non-JSON error body: keep the status text
      }
      const announced = res.status !== 401 || this.unauthorized?.(String(data.code ?? 'unauthorized')) !== false;
      // The kill switch was flipped in another tab: the app's copy of /api/config is stale.
      if (data.code === 'genai_disabled') this.genaiDisabled?.();
      const raw = data.details;
      const details = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
      if (res.status === 403) {
        // The API always answers in JSON; an HTML 403 comes from the web application firewall in front of it.
        if (!json) throw new ApiError(403, 'blocked', BLOCKED_MESSAGE);
        // Someone's role was changed while they had the app open. /api/me is excluded: a 403 from the
        // re-read itself would ask for another one for ever. Only a refusal by role says so (it names
        // the action); a per-radar refusal does not, and at most one re-read a minute (r3 F3-108).
        const now = Date.now();
        if (path !== '/api/me' && typeof details.action === 'string' && now - this.lastForbidden >= FORBIDDEN_REREAD_MS) {
          this.lastForbidden = now;
          this.forbidden?.();
        }
      }
      // A gateway or CloudFront error page instead of the API's JSON: the request may have run.
      const gateway = !json && [502, 503, 504].includes(res.status);
      const error = new ApiError(
        res.status,
        gateway ? 'gateway' : String(data.code ?? 'error'),
        // statusText is '' over HTTP/2, so it cannot be the last resort.
        gateway ? noAnswerMessage('gateway', write, res.status) : String(data.message ?? (res.statusText || 'Request failed')),
        details,
        retryAfterOf(res.status, details, res.headers, String(data.code ?? '')),
      );
      error.announced = announced;
      throw error;
    }
    return res;
  }

  async request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
    const res = await this.raw(method, path, body, query);
    return (await res.json()) as T;
  }

  private get<T>(path: string, query?: Query): Promise<T> {
    return this.request<T>('GET', path, undefined, query);
  }

  /** Overridable in tests so a retry does not make them wait. */
  protected wait(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Every page of a cursor-paged list (users, versions, audit rows), so nothing past the first page
   * is hidden. A page after the first that cannot be read leaves the list incomplete rather than
   * losing the pages already read: eight sequential calls and one throttle on the last used to show
   * "could not be loaded" for a history of 1,500 versions.
   */
  private async every<T>(path: string, query: Query = {}): Promise<Listing<T>> {
    const items: T[] = [];
    let cursor: string | undefined;
    for (let page = 0; page < MAX_PAGES; page++) {
      let res: { items: T[]; cursor?: string | null };
      try {
        res = await this.get<{ items: T[]; cursor?: string | null }>(path, { ...query, cursor });
      } catch (err) {
        if (page === 0) throw err; // nothing to show: the caller reports it with Retry
        return { items, truncated: true };
      }
      items.push(...(res.items ?? []));
      if (!res.cursor) return { items };
      cursor = res.cursor;
    }
    return { items, truncated: true };
  }

  // ---- identity / config -----------------------------------------------------------
  config = (): Promise<AppConfig> => this.get('/api/config');
  me = (): Promise<Me> => this.get('/api/me');
  nav = (): Promise<Nav> => this.get('/api/nav');
  chatStatus = (): Promise<ChatStatus> => this.get('/api/chat/status');

  // ---- insights and graph ----------------------------------------------------------------
  insights = (
    kind: string,
    { filters, ...query }: { sort?: string; q?: string; cursor?: string; order?: string; include_closed?: 'true'; filters?: Record<string, string[]> } = {},
  ): Promise<InsightPage> => {
    return this.get(`/api/insights/${seg(kind).replaceAll('%3A', ':')}`, withFilters(query, filters));
  };
  /** The list as CSV or a Markdown table, with the same filters and field sort as on screen. */
  exportList = async (
    kind: string,
    format: 'csv' | 'md',
    { filters, ...query }: { sort?: string; order?: string; filters?: Record<string, string[]> } = {},
  ): Promise<string> => {
    const flat = withFilters({ ...query, format }, filters);
    return (await this.raw('GET', `/api/insights/${seg(kind).replaceAll('%3A', ':')}/export`, undefined, flat)).text();
  };
  facets = (kind: string, days = 90): Promise<InsightFacets> =>
    this.get(`/api/insights/${seg(kind).replaceAll('%3A', ':')}/facets`, { days });
  insight = (id: string): Promise<InsightDetail> => this.get(`/api/insights/nodes/${nodePath(id)}`);
  feedback = (id: string, vote: 'up' | 'down', comment = ''): Promise<InsightDetail['feedback']> =>
    this.request('POST', `/api/nodes/${nodePath(id)}/feedback`, { vote, comment });
  /** Withdraw the caller's own vote; answers with the same summary as a vote. */
  withdrawFeedback = (id: string): Promise<InsightDetail['feedback']> => this.request('DELETE', `/api/nodes/${nodePath(id)}/feedback`);
  setWorking = (id: string, on: boolean): Promise<{ working_on: InsightDetail['working_on'] }> =>
    this.request(on ? 'PUT' : 'DELETE', `/api/nodes/${nodePath(id)}/working`);
  requestDraft = (id: string, brief: string): Promise<ModuleRun> =>
    this.request('POST', `/api/nodes/${nodePath(id)}/draft`, brief ? { brief } : {});
  draftMarkdown = async (id: string): Promise<string> => (await this.raw('GET', `/api/drafts/${nodePath(id)}`)).text();
  search = (query: string, types: string[] = [], limit = 20): Promise<{ items: SearchHit[]; degraded?: boolean }> =>
    this.request('POST', '/api/graph/search', { query, types, limit });
  node = (id: string): Promise<GraphNode> => this.get(`/api/graph/nodes/${nodePath(id)}`);
  /** A node with more links than one request can take apart answers 202 `{queued, edges}`: the sweep finishes it. */
  deleteNode = (id: string): Promise<{ queued?: boolean; edges?: number }> => this.request('DELETE', `/api/graph/nodes/${nodePath(id)}`);
  entityHygiene = (): Promise<EntityHygiene> => this.get('/api/graph/hygiene');
  /** Merge an entity into another; a dry run unless ``apply``. */
  mergeEntity = (id: string, into: string, apply = false): Promise<MergeReport> =>
    this.request('POST', `/api/graph/nodes/${nodePath(id)}/merge`, { into, apply });
  /** With ``offset``, one page of the node's direct neighbours (``total`` says how many there are). */
  neighbors = (id: string, depth = 1, offset?: number): Promise<Neighborhood> =>
    this.get(`/api/graph/nodes/${nodePath(id)}/neighbors`, offset === undefined ? { depth } : { depth, offset });
  explore = (limit = 150, types?: string): Promise<Neighborhood> => this.get('/api/graph/explore', { limit, types });

  // ---- radars --------------------------------------------------------------------------
  radarTemplates = (): Promise<{ items: RadarTemplate[] }> => this.get('/api/radar-templates');
  radars = (): Promise<{ items: (Radar & { can_edit: boolean })[] }> => this.get('/api/radars');
  entryHistory = (slug: string, key: string): Promise<EntryHistory> =>
    this.get(`/api/radars/${seg(slug)}/entries/${seg(key)}/history`);
  favourites = (): Promise<{ radars: string[] }> => this.get('/api/me/favourites');
  setFavourite = (slug: string, on: boolean): Promise<{ slug: string; favourite: boolean }> =>
    this.request(on ? 'PUT' : 'DELETE', `/api/me/favourites/radars/${seg(slug)}`, on ? {} : undefined);
  watches = (): Promise<{ items: WatchedNode[] }> => this.get('/api/me/watches');
  setWatch = (id: string, on: boolean): Promise<{ id: string; watching: boolean }> =>
    this.request(on ? 'PUT' : 'DELETE', `/api/me/watches/nodes/${nodePath(id)}`, on ? {} : undefined);
  /** What is new since ``since`` or the last read; reading it does not mark it read. */
  digest = (since?: string): Promise<Digest> => this.get('/api/me/digest', since ? { since } : undefined);
  /** Marks read up to ``upto`` (the digest's ``until``), so what arrived after it shows next time. */
  markDigestRead = (upto?: string): Promise<{ read_at: string }> => this.request('POST', '/api/me/digest/read', upto ? { upto } : {});
  /** The caller's digest deliveries (Slack, email), and what this deployment lets them deliver to. */
  deliveries = (): Promise<DeliveryList> => this.get('/api/me/deliveries');
  createDelivery = (body: NewDelivery): Promise<Delivery> => this.request('POST', '/api/me/deliveries', body);
  updateDelivery = (id: string, changes: DeliveryChanges): Promise<Delivery> =>
    this.request('PATCH', `/api/me/deliveries/${seg(id)}`, changes);
  /** The body is not read: the API may answer 204 with none. */
  deleteDelivery = async (id: string): Promise<void> => {
    await this.raw('DELETE', `/api/me/deliveries/${seg(id)}`);
  };
  /** Sends the current digest once, without moving the delivery's cursor (one test per 10 minutes). */
  testDelivery = (id: string): Promise<DeliveryTest> => this.request('POST', `/api/me/deliveries/${seg(id)}/test`, {});
  digestChannels = (): Promise<DigestChannelList> => this.get('/api/admin/digest/channels');
  setDigestChannels = (channels: DigestChannel[], version: number): Promise<DigestChannelList> =>
    this.request('PUT', '/api/admin/digest/channels', { channels, version });
  /** Turns off the one delivery a signed unsubscribe link names; needs no sign-in. */
  unsubscribe = (token: string): Promise<{ ok: boolean }> => this.request('POST', '/api/digest/unsubscribe', { t: token });
  /** Entries on every radar that name ``q`` or link to ``node_id``. */
  findRadarEntries = (by: { q?: string; node_id?: string }): Promise<{ items: RadarEntryHit[] }> =>
    this.get('/api/radars/entries', by);
  createRadar = (body: { slug: string; name: string; template?: string; schema?: RadarSchema; description?: string; purpose?: string; mode?: string }): Promise<Radar> =>
    this.request('POST', '/api/radars', body);
  /** The current version, or one pinned by number or by tag (a shared link to a tagged version). */
  radar = async (slug: string, version?: number, tag?: string): Promise<RadarView> => {
    const view = await this.get<RadarView>(`/api/radars/${seg(slug)}`, tag ? { tag } : { version });
    // Descriptions are edited and shown as text: the stored HTML entities are decoded once, here (r3 F3-026).
    return { ...view, entries: view.entries.map((e) => (e.description ? { ...e, description: storedText(e.description) } : e)) };
  };
  updateRadar = (slug: string, changes: Partial<Radar>, versionLock: number): Promise<Radar> =>
    this.request('PATCH', `/api/radars/${seg(slug)}`, { ...changes, version_lock: versionLock });
  /** `allowEmpty` confirms a save that removes most of the radar (refused first with `shrinking_save`). */
  saveEntries = (slug: string, entries: RadarEntry[], expectedVersion: number, note = '', allowEmpty = false): Promise<Radar> =>
    this.request('PUT', `/api/radars/${seg(slug)}/entries`, {
      entries,
      expected_version: expectedVersion,
      note,
      ...(allowEmpty ? { allow_empty: true } : {}),
    });
  /**
   * A dry run of the file on its own, always in replace mode: its row count and every row number it
   * reports are then the file's own. (A merge dry run validates the kept entries first, so "row 41"
   * would be file row 1 and an old entry that no longer matches would read as a problem in the file.)
   */
  checkImport = (slug: string, format: 'csv' | 'json', text: string): Promise<ImportReport> =>
    this.request('POST', `/api/radars/${seg(slug)}/import`, { format, text, dry_run: true, mode: 'replace' });
  importEntries = (slug: string, format: 'csv' | 'json', text: string, expectedVersion: number, mode: 'replace' | 'merge', allowEmpty = false): Promise<Radar> =>
    this.request('POST', `/api/radars/${seg(slug)}/import`, {
      format,
      text,
      expected_version: expectedVersion,
      mode,
      ...(allowEmpty ? { allow_empty: true } : {}),
    });
  exportEntries = async (slug: string, format: 'csv' | 'json', version?: number): Promise<string> =>
    (await this.raw('GET', `/api/radars/${seg(slug)}/export`, undefined, { format, version })).text();
  importTemplate = async (slug: string): Promise<string> => (await this.raw('GET', `/api/radars/${seg(slug)}/template`)).text();
  versions = (slug: string): Promise<Listing<RadarVersion>> => this.every(`/api/radars/${seg(slug)}/versions`);
  tags = (slug: string): Promise<{ items: RadarTag[] }> => this.get(`/api/radars/${seg(slug)}/tags`);
  tagVersion = (slug: string, version: number, tag: string): Promise<{ tag: string; version: number }> =>
    this.request('PUT', `/api/radars/${seg(slug)}/versions/${version}/tag`, { tag });
  untag = (slug: string, tag: string): Promise<{ deleted: boolean }> =>
    this.request('DELETE', `/api/radars/${seg(slug)}/tags/${seg(tag)}`);
  /** `allowEmpty` confirms a restore that removes most of the radar (refused first with `shrinking_rollback`). */
  rollback = (slug: string, version: number, expectedVersion: number, allowEmpty = false): Promise<Radar> =>
    this.request('POST', `/api/radars/${seg(slug)}/rollback`, {
      version,
      expected_version: expectedVersion,
      ...(allowEmpty ? { allow_empty: true } : {}),
    });
  diff = (slug: string, from: number, to: number): Promise<{ from: number; to: number; changes: RadarDiffChanges }> =>
    this.get(`/api/radars/${seg(slug)}/diff`, { from, to });
  updateSchema = (slug: string, schema: RadarSchema, expectedVersion: number, renames: Record<string, Record<string, string>>, dropUnmatched: boolean): Promise<Radar> =>
    this.request('PUT', `/api/radars/${seg(slug)}/schema`, { schema, expected_version: expectedVersion, renames, drop_unmatched: dropUnmatched });
  /** `owner` is the new owner's account id (username); the API takes it from the user pool, and only
   *  an administrator may send it. */
  /** ``confirmUnknown`` adds editors that no account signs in with, after the API named them (CTR HCB-037). */
  setAccess = (slug: string, editors: string[], versionLock: number, owner?: string, confirmUnknown = false): Promise<Radar> =>
    this.request('PUT', `/api/radars/${seg(slug)}/access`, {
      editors,
      version_lock: versionLock,
      ...(owner ? { owner: { subject: owner } } : {}),
      ...(confirmUnknown ? { confirm_unknown: true } : {}),
    });
  deleteRadar = (slug: string): Promise<unknown> => this.request('DELETE', `/api/radars/${seg(slug)}`);
  suggestions = (slug: string): Promise<{ items: Suggestion[] }> => this.get(`/api/radars/${seg(slug)}/suggestions`);
  acceptSuggestion = (slug: string, key: string, edits?: Partial<RadarEntry>): Promise<Radar> =>
    this.request('POST', `/api/radars/${seg(slug)}/suggestions/${seg(key)}/accept`, edits ? { edits } : {});
  discardSuggestion = (slug: string, key: string): Promise<unknown> =>
    this.request('DELETE', `/api/radars/${seg(slug)}/suggestions/${seg(key)}`);

  // ---- products ------------------------------------------------------------------------
  products = (): Promise<{ items: Product[] }> => this.get('/api/products');
  product = (id: string): Promise<Product> => this.get(`/api/products/${seg(id)}`);
  createProduct = (body: Partial<Product>): Promise<Product> => this.request('POST', '/api/products', body);
  updateProduct = (id: string, changes: Partial<Product>, version: number): Promise<Product> =>
    this.request('PATCH', `/api/products/${seg(id)}`, { ...changes, version });
  deleteProduct = (id: string): Promise<unknown> => this.request('DELETE', `/api/products/${seg(id)}`);
  resyncProduct = (id: string): Promise<unknown> => this.request('POST', `/api/products/${seg(id)}/sync`);
  askProduct = (id: string, query: string): Promise<{ items: Passage[] }> =>
    this.request('POST', `/api/products/${seg(id)}/ask`, { query });
  documents = (id: string): Promise<{ items: KnowledgeDocument[] }> => this.get(`/api/products/${seg(id)}/documents`);
  presignUpload = (id: string, filename: string, size: number): Promise<UploadTicket> =>
    this.request('POST', `/api/products/${seg(id)}/documents`, { filename, size });
  completeUpload = (id: string, doc: string): Promise<KnowledgeDocument> =>
    this.request('POST', `/api/products/${seg(id)}/documents/${seg(doc)}/complete`);
  deleteDocument = (id: string, doc: string): Promise<unknown> =>
    this.request('DELETE', `/api/products/${seg(id)}/documents/${seg(doc)}`);

  // ---- modules -------------------------------------------------------------------------
  models = (): Promise<{ items: ModelSpec[] }> => this.get('/api/models');
  modules = (): Promise<{ items: Module[] }> => this.get('/api/modules');
  module = (slug: string): Promise<Module> => this.get(`/api/modules/${seg(slug)}`);

  modulePrompt = (slug: string): Promise<ModulePrompt> => this.get(`/api/modules/${seg(slug)}/prompt`);

  promptHistory = (slug: string): Promise<{ items: PromptVersion[] }> => this.get(`/api/modules/${seg(slug)}/prompt/history`);

  skills = (): Promise<{ items: SkillSummary[] }> => this.get('/api/skills');

  skill = (name: string): Promise<Skill> => this.get(`/api/skills/${seg(name)}`);

  /** Create a skill (version 0) or replace the version read. */
  saveSkill = (name: string, content: string, version: number): Promise<Skill> =>
    this.request('PUT', `/api/skills/${seg(name)}`, { content, version });

  deleteSkill = (name: string): Promise<void> => this.request('DELETE', `/api/skills/${seg(name)}`);
  createModule = (body: Partial<Module>): Promise<Module> => this.request('POST', '/api/modules', body);
  updateModule = (slug: string, changes: Partial<Module>, version: number): Promise<Module> =>
    this.request('PATCH', `/api/modules/${seg(slug)}`, { ...changes, version });
  deleteModule = (slug: string): Promise<unknown> => this.request('DELETE', `/api/modules/${seg(slug)}`);
  /** Where the module reads from on its next run: an RFC 3339 time, or null for the beginning. */
  resetModuleProgress = (slug: string, at: string | null): Promise<{ last_processed_at: string | null }> =>
    this.request('PUT', `/api/modules/${seg(slug)}/progress`, { last_processed_at: at });
  runModule = (slug: string): Promise<ModuleRun> => this.request('POST', `/api/modules/${seg(slug)}/run`, {});
  /** Stop a queued or running run (administrators). */
  cancelRun = (slug: string, runId: string): Promise<ModuleRun> =>
    this.request('POST', `/api/modules/${seg(slug)}/runs/${seg(runId)}/cancel`, {});
  // Runs are 25 to a page: without the cursor a module that failed 40 times overnight showed 25 of
  // them and counted that as the total.
  moduleRuns = (slug: string): Promise<Listing<ModuleRun>> => this.every(`/api/modules/${seg(slug)}/runs`, { limit: RUNS_LIMIT });
  runs = (): Promise<Listing<ModuleRun>> => this.every('/api/runs', { limit: RUNS_LIMIT });
  /** Only the newest page: what a page polls while a run is going. */
  moduleRunsFirst = (slug: string): Promise<{ items: ModuleRun[] }> => this.get(`/api/modules/${seg(slug)}/runs`, { limit: RUNS_LIMIT });
  sources = (slug: string): Promise<{ items: Source[] }> => this.get(`/api/modules/${seg(slug)}/sources`);
  checkSources = (slug: string): Promise<{ requested: boolean }> => this.request('POST', `/api/modules/${seg(slug)}/sources/check`, {});
  addSource = (slug: string, body: Partial<Source>): Promise<Source> =>
    this.request('POST', `/api/modules/${seg(slug)}/sources`, body);
  updateSource = (slug: string, id: string, changes: Partial<Source>): Promise<Source> =>
    this.request('PATCH', `/api/modules/${seg(slug)}/sources/${seg(id)}`, changes);
  deleteSource = (slug: string, id: string): Promise<unknown> =>
    this.request('DELETE', `/api/modules/${seg(slug)}/sources/${seg(id)}`);

  // ---- administration --------------------------------------------------------------------
  // Every page, USERS_LIMIT at a time: the Users table filters and pages them in the browser.
  users = (): Promise<Listing<User>> => this.every('/api/users', { limit: USERS_LIMIT });
  inviteUser = (email: string, role: string): Promise<User> => this.request('POST', '/api/users', { email, role });
  updateUser = (username: string, changes: { role?: string; enabled?: boolean }): Promise<User> =>
    this.request('PATCH', `/api/users/${seg(username)}`, changes);
  deleteUser = (username: string): Promise<unknown> => this.request('DELETE', `/api/users/${seg(username)}`);
  andon = (): Promise<Andon> => this.get('/api/andon');
  setAndon = (enabled: boolean, reason: string): Promise<Andon> =>
    this.request('PUT', '/api/andon', { genai_enabled: enabled, reason: reason || undefined });
  chatLimits = (): Promise<ChatLimits> => this.get('/api/chat/settings');
  setChatLimits = (limits: ChatLimits): Promise<ChatLimits> => this.request('PUT', '/api/chat/settings', limits);
  audit = (date?: string): Promise<Listing<AuditItem>> => this.every('/api/audit', { date, limit: 1000 });
  /** Votes and "Not useful" comments on one kind of insight, newest first (audit.read). */
  feedbackList = (type: string): Promise<Listing<FeedbackItem>> => this.every('/api/feedback', { type, limit: 500 });
  health = (): Promise<Health> => this.get('/api/health');
  loadSampleData = (): Promise<SampleDataResult> => this.request('POST', '/api/admin/sample-data', {});
}
