// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Shapes returned by the Agentic Insights Radar API (backend `to_public` methods). */

import type { Profile } from '@/lib/profile';

export type Role = 'admin' | 'editor' | 'viewer';

export interface AppConfig {
  region: string;
  user_pool_id: string;
  user_pool_client_id: string;
  cognito_domain: string;
  /** Set when people sign in only through an external identity provider: sign-in goes straight to it. */
  identity_provider?: string | null;
  chat_runtime_arn: string;
  version: string;
  genai_enabled: boolean;
  /** Absent from servers older than the profile feature: the built-in text applies. */
  profile?: Profile;
}

export interface Me {
  subject: string;
  email: string;
  role: Role;
  can: string[];
}

export interface NavEntry {
  kind: string;
  label: string;
  module: string;
}

export interface Nav {
  builtin: string[];
  custom: NavEntry[];
}

export interface GraphNode {
  id: string;
  type: string;
  category: 'entity' | 'evidence' | 'output';
  title: string;
  summary: string;
  description: string;
  attributes: Record<string, unknown>;
  aliases: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
  version: number;
  degree: number;
  evidence_url?: string;
  published_at?: string;
  confidence?: number;
  insight_kind?: string;
  module?: string;
  working_on?: WorkingOn[];
}

export interface Brief {
  id: string;
  type: string;
  title: string;
  summary: string;
  updated_at: string;
  evidence_url?: string;
  insight_kind?: string;
}

export interface Edge {
  from: string;
  type: string;
  to: string;
  created_by: string;
  created_at: string;
  confidence?: number;
}

export interface WorkingOn {
  user: string;
  email: string;
  since: string;
}

/** One typed field of a list, from the module that writes it (its attribute schema). */
export interface AttributeFieldDef {
  key: string;
  label: string;
  type: 'enum' | 'string_list' | 'date' | 'url' | 'text' | 'number';
  required: boolean;
  filter: boolean;
  sort: boolean;
  help: string;
  values?: { value: string; label: string; rank: number }[];
}

export interface InsightPage {
  items: GraphNode[];
  cursor: string | null;
  sort: string;
  page_size: number;
  /** The list's typed fields; empty when its module declares none (or an older server). */
  schema?: AttributeFieldDef[];
  /** With filters or a field sort: how many records match, and whether the scan hit its cap. */
  total?: number;
  truncated?: boolean;
}

/** What an overview draws for a list with typed fields (GET /api/insights/{kind}/facets). */
export interface InsightFacets {
  total: number;
  /** How many records were read (the cap when `truncated`); `total` is how many of them the caller sees. */
  scanned: number;
  /** field key -> value -> how many records carry it */
  counts: Record<string, Record<string, number>>;
  /** Free-form fields whose counts keep only their most common values. */
  counts_cut?: string[];
  /** Records whose date fields fall within the next `days` days, soonest first. */
  upcoming: { field: string; date: string; node: GraphNode }[];
  days: number;
  truncated: boolean;
  schema: AttributeFieldDef[];
}

export interface EvidenceEntry {
  edge: string;
  direction: 'in' | 'out';
  node: Brief;
}

export type EvidenceGroup =
  | 'supports'
  | 'contradicts'
  | 'about'
  | 'addresses'
  | 'addressed_by'
  | 'supported_by'
  | 'drafts'
  | 'other';

export interface Feedback {
  up: number;
  down: number;
  mine: { vote: 'up' | 'down'; comment: string } | null;
}

export interface InsightDetail {
  node: GraphNode;
  evidence: Record<EvidenceGroup, EvidenceEntry[]>;
  feedback?: Feedback;
  working_on?: WorkingOn[];
}

export interface SearchHit {
  node: GraphBrief;
  score: number;
  semantic_rank: number | null;
  keyword_rank: number | null;
}

/**
 * What /api/graph/explore and /api/graph/nodes/{id}/neighbors return for each node: no description,
 * attributes, aliases or version, and a summary cut to 300 characters. 150 full nodes could carry
 * 40,000-character descriptions and push the response past the Lambda limit, so anything that needs
 * the whole record reads it with api.node(id).
 */
export type GraphBrief = Pick<GraphNode, 'id' | 'type' | 'category' | 'title' | 'summary' | 'degree' | 'updated_at'> &
  Partial<Pick<GraphNode, 'evidence_url' | 'insight_kind' | 'module'>>;

export interface Neighborhood {
  nodes: GraphBrief[];
  edges: Edge[];
  /** The neighbourhood was cut to max_nodes. */
  truncated?: boolean;
  /** One-hop paging: how many direct neighbours the node has, and where this page starts. */
  total?: number;
  offset?: number;
}

export interface Dimension {
  name: string;
  color?: string;
  glyph?: string;
}

export interface RadarSchema {
  sectors: Dimension[];
  rings: Dimension[];
  markers: Dimension[];
  sector_label: string;
  ring_label: string;
  marker_label: string;
}

export type RadarMode = 'manual' | 'assist' | 'copilot' | 'autopilot';

export interface Radar {
  slug: string;
  name: string;
  schema: RadarSchema;
  purpose: string;
  description: string;
  mode: RadarMode;
  auto_apply_days: number;
  owner: string;
  owner_email: string;
  editors: string[];
  current_version: number;
  entry_count: number;
  created_at: string;
  updated_at: string;
  updated_by: string;
  version_lock: number;
  /** On autopilot, whether the Radar curator's proposals apply without review (administrators set it). */
  auto_apply_agent?: boolean;
}

export interface RadarEntry {
  key: string;
  title: string;
  sector: string;
  ring: string;
  description: string;
  marker?: string;
  node_id?: string;
  link?: string;
  /** Free-text group inside a sector (the Tech Radar's sub-segment). */
  sub_sector?: string;
}

export interface RadarView {
  radar: Radar;
  version: number;
  schema: RadarSchema;
  entries: RadarEntry[];
  can_edit: boolean;
  can_admin: boolean;
}

export interface RadarTemplate {
  key: string;
  purpose: string;
  schema: RadarSchema;
}

export interface RadarVersion {
  version: number;
  created_at: string;
  created_by: string;
  entry_count: number;
  note?: string;
  schema_changed?: boolean;
}

export interface Suggestion {
  entry_key: string;
  is_new: boolean;
  proposal: Partial<RadarEntry>;
  base: RadarEntry | null;
  reason: string;
  evidence: string[];
  created_at: string;
  created_by: string;
  run_id?: string;
}

export interface UnmatchedValue {
  row: number;
  field: string;
  value: string;
  suggestion: string | null;
}

export interface ImportReport {
  rows: number;
  unmatched: UnmatchedValue[];
  duplicate_rows: number[];
  /** Values of the key column used on more than one row: the import refuses them (CTR HA-033). */
  duplicate_keys?: string[];
  accepted: boolean;
  /** The import would leave the radar with no entries (a Replace with an empty file): it must be confirmed. */
  empty?: boolean;
  /** How many entries the radar has now, and how many a Replace would remove: both need confirming. */
  before?: number;
  removed?: number;
}

export interface RadarDiffChanges {
  added: RadarEntry[];
  removed: RadarEntry[];
  moved: { key: string; title: string; from: string; to: string }[];
  recategorised: { key: string; title: string; from: string; to: string }[];
  marker_changed: { key: string; title: string; from: string | null; to: string | null }[];
  edited: { key: string; title: string; fields: string[] }[];
}

export interface Budget {
  consumed: number;
  limit: number;
  state: 'open' | 'warn' | 'half_open' | 'closed';
}

export interface McpServer {
  name: string;
  url?: string; // only sent to people who can manage modules: an address can carry a credential
}

export interface Module {
  slug: string;
  name: string;
  template: string;
  output_kind: string | null;
  description: string;
  instructions: string;
  insight_kind: string | null;
  insight_label: string | null;
  input_types: string[];
  grants: string[];
  product_id: string | null;
  model_key: string;
  interval_minutes: number;
  daily_token_budget: number;
  max_concurrency: number;
  mcp_servers: McpServer[];
  /** "extend": the instructions fill the template's focus slot; "replace": they are the whole job description. */
  prompt_mode?: 'extend' | 'replace';
  /** An administrator's own version of the template ("" = the packaged template). Managers only. */
  system_prompt?: string;
  /** Names of library skills its runs may load. */
  skills?: string[];
  enabled: boolean;
  builtin: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  version: number;
  budget?: Budget;
  last_dispatched_at?: string | null;
  /** Read by GET /api/modules/{slug}: how far through the graph's evidence the module has read. */
  progress?: { last_processed_at: string | null };
}

export interface Source {
  id: string;
  module: string;
  name: string;
  type: 'web_page' | 'dynamic_web_page' | 'rss' | 'public_api';
  enabled: boolean;
  /** Configuration, returned only to people who manage modules (an address can carry an API key). */
  address?: string;
  instruction?: string;
  cursor?: string;
  created_at?: string;
  updated_at?: string;
  /** When a run last fetched it successfully ("" = never). */
  read_at?: string;
  /** When a run last tried to fetch it, read or failed: the Scout's pass order. */
  tried_at?: string;
  /** What the source watcher last saw (managers only; null when it has not checked this source yet). */
  watch?: SourceWatch | null;
}

export interface SourceWatch {
  checked_at: string;
  status: 'ok' | 'empty' | 'error' | '';
  error: string;
  items: number;
  pending: number;
  dropped: number;
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'budget_exhausted' | 'cancelled';

export interface ModuleRun {
  module: string;
  run_id: string;
  status: RunStatus;
  trigger: string;
  started_at: string;
  requested_by: string;
  finished_at: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  weighted_tokens: number;
  nodes_written: number;
  summary: string;
  error: string | null;
  execution_arn?: string | null;
  target_id?: string | null;
}

export interface ModelSpec {
  key: string;
  label: string;
  tier: string;
  price_in_out: [number, number];
  default: boolean;
}

export type IngestionStatus = 'none' | 'pending' | 'indexing' | 'ready' | 'failed';

export interface Product {
  id: string;
  name: string;
  summary: string;
  capabilities: string[];
  segments: string[];
  competitors: string[];
  doc_urls: string[];
  notes: string;
  web_status: IngestionStatus;
  web_pages: number;
  web_crawled_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  version: number;
  stats: Record<string, unknown>;
  /**
   * Set for a caller who does not manage products: only the name, summary and documentation hosts
   * are given, and the lists, notes and crawl report are empty because they are withheld (B3-017).
   */
  restricted?: boolean;
}

export interface KnowledgeDocument {
  id: string;
  product_id: string;
  filename: string;
  content_type: string;
  size: number;
  status: IngestionStatus;
  uploaded_by: string;
  uploaded_at: string;
  error?: string | null;
}

export interface Passage {
  text: string;
  source: string;
  score: number;
  product_id: string | null;
}

export interface UploadTicket {
  document: KnowledgeDocument;
  upload: { url: string; fields: Record<string, string> };
}

export interface User {
  username: string;
  /** The token's sub, which radars and marks record; differs from username for federated users. */
  subject?: string;
  email: string;
  role: Role;
  enabled: boolean;
  status: string;
  created_at: string;
}

export interface Andon {
  genai_enabled: boolean;
  reason?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

/** One vote on an insight, for the feedback list under Administration. */
export interface FeedbackItem {
  node_id: string;
  vote: 'up' | 'down';
  comment: string;
  at: string;
  /** The voter's email, or their account id for an older vote. */
  by: string;
}

export interface AuditItem {
  actor: string;
  email: string;
  action: string;
  target: string;
  details: Record<string, unknown>;
  at: string;
}

export interface ChatStatus {
  genai_enabled: boolean;
  allowed: boolean;
  shared: { state: string; remaining: number };
  user: { state: string; remaining: number };
}

export interface ChatLimits {
  daily_tokens: number;
  per_user_daily_tokens: number;
}

export interface Health {
  runs: Record<string, number>;
  recent_failures: ModuleRun[];
  budgets: { module: string; enabled: boolean; consumed: number; limit: number; state: string }[];
  genai_enabled: boolean;
}

export interface SampleDataResult {
  nodes_created: number;
  edges_created: number;
  /** Null when another radar already holds the sample address. */
  radar: string | null;
  skipped: string[];
}

/** An entity in the hygiene report. */
export interface EntityBrief {
  id: string;
  type: string;
  title: string;
  degree: number;
}

/** GET /api/graph/hygiene: entities with no links, and groups that look like one entity (best-linked first). */
export interface EntityHygiene {
  scanned: number;
  orphans: EntityBrief[];
  orphan_count: number;
  duplicates: EntityBrief[][];
  duplicate_count: number;
}

/** What merging one entity into another does (or would do, on a dry run). */
export interface MergeReport {
  duplicate: string;
  into: string;
  applied: boolean;
  edges: number;
  moved: number;
  dropped: { from: string; type: string; to: string; why: string }[];
  /** Links to a linked entity whose own merge stopped part way, taken on to the entity it was merging into (`via`). */
  redirected: { from: string; type: string; to: string; via: string }[];
  aliases_added: string[];
  descriptions_rewritten: string[];
}

/** An entry found on a radar by GET /api/radars/entries (which radars place something). */
export interface RadarEntryHit extends Omit<RadarEntry, 'description'> {
  radar: string;
  radar_name: string;
  version: number;
}

/** One version's change to an entry (GET /api/radars/{slug}/entries/{key}/history). */
export interface EntryEvent {
  version: number;
  at: string | null;
  by: string;
  note: string;
  /** Written from suggestions (the Radar curator's). */
  ai: boolean;
  change: 'added' | 'changed' | 'removed' | 'restored';
  title: string;
  sector: string;
  ring: string;
  ring_from?: string;
  sector_from?: string;
  fields?: string[];
}

export interface EntryHistory {
  key: string;
  from_version: number;
  to_version: number;
  current: boolean;
  events: EntryEvent[];
}

/** A version's name (GET /api/radars/{slug}/tags). */
export interface RadarTag {
  tag: string;
  version: number;
  by: string;
  at: string | null;
}

/** A graph record the caller watches. */
export interface WatchedNode {
  id: string;
  type: string;
  title: string;
}

export interface DigestRow extends WatchedNode {
  summary: string;
  evidence_url: string | null;
  updated_at: string;
}

/** What is new since a time: new conclusions by type, and new records about each watched one. */
export interface Digest {
  since: string;
  until: string;
  total: number;
  new: Record<string, DigestRow[]>;
  /** Lists cut at their first records: the rest come in the next digest once this one is read. */
  more?: Record<string, boolean>;
  /** The list label of each custom module's kind in ``new`` (``insight:<kind>``). */
  labels?: Record<string, string>;
  watched: { node: DigestRow; new: DigestRow[]; count: number }[];
}

/** GET /api/modules/{slug}/prompt: the prompt a run of the module gets, and what it is made of. */
export interface ModulePrompt {
  slug: string;
  template: string;
  mode: 'extend' | 'replace';
  /** The packaged template. */
  default: string;
  /** The administrator's version of it, or "". */
  override: string;
  slots: { name: string; means: string }[];
  /** Shared rules appended to every prompt of this kind; no edit removes them. */
  partials: { name: string; text: string }[];
  effective: string;
  max_override: number;
}

/** One kept version of a module's prompt (GET /api/modules/{slug}/prompt/history). */
export interface PromptVersion {
  at: string;
  by: string;
  module_version: number;
  instructions: string;
  system_prompt: string;
  prompt_mode: 'extend' | 'replace';
  skills: string[];
}

/** A skill in the library (GET /api/skills). */
export interface SkillSummary {
  name: string;
  description: string;
  size: number;
  /** Shipped with the application (humanizer, no-ai-slop). */
  default: boolean;
  /** A default an administrator changed: deploys leave it as they saved it. */
  edited: boolean;
  updated_by: string;
  updated_at: string;
  version: number;
  /** The modules it is attached to (list only). */
  modules?: string[];
}

export interface Skill extends SkillSummary {
  content: string;
}

/** Where a digest delivery goes (GET /api/me/deliveries). */
export type DeliveryKind = 'slack_channel' | 'slack_dm' | 'email';

/** How the last scheduled send went: sent, nothing new to send, failed, or stopped by a bounce or complaint. */
export type DeliveryStatus = 'ok' | 'empty' | 'error' | 'suppressed';

/** One digest delivery subscription. `weekday` is 0 (Monday) to 6 (Sunday), as Python's weekday(). */
export interface Delivery {
  id: string;
  kind: DeliveryKind;
  target_label: string;
  channel_id?: string | null;
  cadence: 'daily' | 'weekly';
  weekday?: number | null;
  hour: number;
  timezone: string;
  enabled: boolean;
  next_at: string | null;
  last_sent_at?: string | null;
  last_status: DeliveryStatus | null;
  last_error?: string | null;
  suppressed?: boolean;
  stop_reason?: string | null;
}

/** A Slack channel an administrator offers for channel deliveries. */
export interface DigestChannel {
  id: string;
  label: string;
}

/** The admin view of the channel list (scan cycle 31). */
export interface DigestChannelList {
  channels: DigestChannel[];
  /** The version a save must quote, so a save over someone else's newer list is refused. */
  version?: number;
  /** Groups of IDs whose names read the same: subscribers see all but the first as '<name> (<id>)' (scan cycles 31 to 35). */
  clashes?: string[][];
}

/** What this deployment and this user can deliver to. */
export interface DeliveryOptions {
  slack_channel: boolean;
  slack_dm: boolean;
  email: boolean;
  channels: DigestChannel[];
  /** The signed-in user's own verified address: the only one an email delivery goes to. */
  email_address: string | null;
}

export interface DeliveryList {
  items: Delivery[];
  options: DeliveryOptions;
}

/** POST /api/me/deliveries. */
export interface NewDelivery {
  kind: DeliveryKind;
  channel_id?: string;
  cadence: 'daily' | 'weekly';
  weekday?: number;
  hour: number;
  timezone: string;
  enabled?: boolean;
}

/** PATCH /api/me/deliveries/{id}: the schedule and the on/off switch; the target never changes. */
export type DeliveryChanges = Partial<Pick<NewDelivery, 'cadence' | 'weekday' | 'hour' | 'timezone' | 'enabled'>>;

/** POST /api/me/deliveries/{id}/test. */
export interface DeliveryTest {
  sent: boolean;
  total: number;
  error?: string;
}
