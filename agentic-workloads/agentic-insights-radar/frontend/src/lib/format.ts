// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Display helpers. */

export function when(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * Relative time for recent moments ("5m ago", "3d ago"). Older than 30 days, or more than a minute
 * in the future (clock skew), shows the date instead: a relative time would mislead there.
 */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '-';
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(s)) return iso;
  if (s < -60 || s >= 86400 * 30) return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  if (s < 60) return 'just now';
  const units: [number, string][] = [[86400, 'd'], [3600, 'h']];
  for (const [size, label] of units) if (s >= size) return `${Math.floor(s / size)}${label} ago`;
  return `${Math.floor(s / 60)}m ago`;
}

/** An address ("slug"): lower-case letters, digits and single hyphens, no hyphen at either end. */
export function slugify(name: string): string {
  return slugTyping(name).replace(/-+$/, '');
}

/**
 * The address while it is being typed: like slugify, but a trailing hyphen stays, so "crm-" can
 * become "crm-rivals". Fields finish it with slugify when they lose focus and on submit.
 */
export function slugTyping(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '') // drop the accents NFKD splits off
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '')
    .slice(0, 64);
}

/** The radar modes, in order, with what each one does. */
export const RADAR_MODES: { value: 'manual' | 'assist' | 'copilot' | 'autopilot'; label: string; description: string }[] = [
  { value: 'manual', label: 'Off', description: 'No AI suggestions.' },
  { value: 'assist', label: 'Assist', description: 'The Radar curator suggests changes; people accept or discard them under Suggestions.' },
  { value: 'copilot', label: 'Copilot', description: 'Like Assist, and the Radar tab says how many suggestions wait for review.' },
  {
    value: 'autopilot',
    label: 'Autopilot',
    // The toggle first: the Radar curator is the only source of suggestions, so without it autopilot
    // applies nothing, and the old first sentence promised that it would.
    description:
      'Applies the Radar curator\'s proposals after the delay only when an administrator has also turned on Apply agent proposals without review. Until then they wait for review as in Copilot.',
  },
];

export const modeLabel = (mode: string): string => RADAR_MODES.find((m) => m.value === mode)?.label ?? mode;

/** "1 entry", "3 entries": `many` defaults to `one` + "s". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Text cut to at most `max` characters at a word boundary, with an ellipsis when it was cut. */
export function clip(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(' ');
  return `${(space > max / 2 ? cut.slice(0, space) : cut).replace(/[\s,.;:]+$/, '')}\u2026`;
}

/** A file size in B, KB, MB or GB. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let n = bytes / 1024;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n >= 10 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

export const ROLE_LABELS: Record<string, string> = { viewer: 'Viewer', editor: 'Editor', admin: 'Administrator' };

export const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role;

/** Sentence-case labels for run statuses and triggers (the API sends lower-case codes). */
const RUN_STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  budget_exhausted: 'Budget used up',
  cancelled: 'Cancelled',
};

export const runStatusLabel = (status: string): string => RUN_STATUS_LABELS[status] ?? sentence(status);

const TRIGGER_LABELS: Record<string, string> = { manual: 'Started by a person', schedule: 'Schedule' };

export const triggerLabel = (trigger: string): string => TRIGGER_LABELS[trigger] ?? sentence(trigger);

/** "market_shift" -> "Market shift". */
export function sentence(code: string): string {
  const text = code.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

export const KIND_LABELS: Record<string, string> = {
  signals: 'Signals',
  trends: 'Trends',
  gaps: 'Gaps',
  content: 'Content ideas',
  drafts: 'Drafts',
};

/**
 * The lists, and the node types in them, that are written from private product documents: a caller
 * without `product.read` (a viewer) is refused the lists and never sees their records (B3-018).
 */
export const PRIVATE_KINDS: ReadonlySet<string> = new Set(['gaps', 'content', 'drafts']);
export const PRIVATE_TYPES: ReadonlySet<string> = new Set(['gap', 'content_idea', 'content_draft']);

/** What a viewer is told for a list or record that is kept from their role. */
export const NOT_FOR_ROLE = 'Gaps, content ideas and drafts are written from private product documents, so they are not available to your role. Ask an administrator if you need them.';

/** What each insight list holds and which module fills it (shown on cards, lists and empty states). */
export const KIND_HELP: Record<string, { about: string; module: string; moduleName: string; empty?: string }> = {
  signals: { about: 'Early movements worth knowing about, each backed by evidence.', module: 'signal', moduleName: 'Signals' },
  trends: { about: 'Patterns that hold across many signals over weeks, not days.', module: 'trend', moduleName: 'Trends' },
  gaps: { about: 'What the market asks for that your product does not do yet.', module: 'gap', moduleName: 'Gaps' },
  content: { about: 'Things to publish: questions your product answers that nobody has written about yet.', module: 'content', moduleName: 'Content ideas' },
  drafts: {
    about: 'Full Markdown drafts written from a content idea, citing their sources.',
    module: 'draft',
    moduleName: 'Content drafts',
    // Drafts are written on request, never on a schedule.
    empty: 'No drafts yet. Open a content idea and choose Write a draft: the draft appears here when it is ready.',
  },
};

/** Display names of the built-in modules, by slug; custom modules show their slug. */
export const MODULE_NAMES: Record<string, string> = {
  scout: 'Scout',
  signal: 'Signals',
  trend: 'Trends',
  gap: 'Gaps',
  content: 'Content ideas',
  draft: 'Content drafts',
  curator: 'Radar curator',
};

/** Who made something, in words: "Sample data", "Signals module", "a person" (never a raw principal id). */
export function actorLabel(createdBy: string, module?: string | null, id = ''): string {
  if (id.includes('/sample-')) return 'Sample data';
  const slug = module ?? (createdBy.startsWith('module:') ? createdBy.slice('module:'.length) : null);
  // The module's own name, as the navigation and the Modules page use it: "Gap module" read as an
  // internal code next to a list called "Gaps".
  if (slug) return MODULE_NAMES[slug] ?? `${slug.charAt(0).toUpperCase()}${slug.slice(1)} module`;
  if (createdBy.startsWith('user:')) return 'Added by a person';
  return createdBy || 'Unknown';
}



export const moduleName = (slug: string): string => MODULE_NAMES[slug] ?? slug;

export const TYPE_LABELS: Record<string, string> = {
  signal: 'Signal',
  trend: 'Trend',
  gap: 'Gap',
  content_idea: 'Content idea',
  content_draft: 'Draft',
  insight: 'Insight',
  organization: 'Organization',
  person: 'Person',
  product: 'Product',
  topic: 'Topic',
  segment: 'Segment',
  fact: 'Fact',
  opinion: 'Opinion',
  event: 'Event',
  adoption: 'Adoption',
  sentiment: 'Sentiment',
};

export function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type.replace(/_/g, ' ');
}

/** "type/uid" -> the in-app page; each part is URL-encoded (a uid may hold "/", "?" or "#"). */
export function nodeHref(id: string): string {
  const cut = id.indexOf('/');
  const [type, uid] = cut < 0 ? [id, ''] : [id.slice(0, cut), id.slice(cut + 1)];
  return `/insights/nodes/${encodeURIComponent(type)}/${encodeURIComponent(uid)}`;
}

/** Insight node types and the list each one is published in. */
export const NODE_LISTS: Record<string, string> = { signal: 'signals', trend: 'trends', gap: 'gaps', content_idea: 'content', content_draft: 'drafts' };

/** The list page a node belongs to, or null for graph records that are in no list. */
export function nodeList(type: string): { href: string; label: string } | null {
  const kind = NODE_LISTS[type];
  return kind ? { href: `/insights/${kind}`, label: KIND_LABELS[kind] as string } : null;
}

/** The knowledge graph page opened at a node (the node stays in the address as ?focus=). */
export function graphHref(id: string): string {
  return `/graph?focus=${encodeURIComponent(id)}`;
}

/** The host to show for a source link; relative or unparsable URLs are shown as written. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url;
  }
}

/** A calendar date (YYYY-MM-DD) with how far it is from today, "(56 days ago)", "(today)", "(in 89 days)":
 *  a lapsed deadline then does not read like an open one, and the wording fits a start date as well as an
 *  end date. Any other text comes back unchanged. */
export function datedText(value: string, today: Date = new Date()): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  // Both sides as calendar days: the value is a date with no time zone, and "today" is the reader's own
  // calendar day (its UTC date is tomorrow in Singapore every evening after 16:00 UTC).
  const days = Math.round((Date.parse(`${value}T00:00:00Z`) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86_400_000);
  if (Number.isNaN(days)) return value;
  const span = (n: number) => `${n} day${n === 1 ? '' : 's'}`;
  return `${value} (${days === 0 ? 'today' : days < 0 ? `${span(-days)} ago` : `in ${span(days)}`})`;
}

/** "a", "a and b", "a, b and c". */
export function joinWords(words: string[]): string {
  return words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}
