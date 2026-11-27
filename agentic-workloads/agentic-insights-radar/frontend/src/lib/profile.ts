// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * The deployment profile from `/api/config`: what this deployment calls itself and its lists, and which
 * lists it shows. Applied once at start-up, before the first render, so every label helper in
 * `format.ts` reads the deployment's words without each page having to know about profiles.
 */
import type { Nav } from '@/api/types';
import { KIND_HELP, KIND_LABELS, MODULE_NAMES, NODE_LISTS, TYPE_LABELS } from '@/lib/format';

export interface ListProfile {
  label: string;
  item: string;
  help: string;
  visible: boolean;
  order: number;
  /** The fields this deployment changed; absent on a list it did not configure. */
  set?: string[];
}

export interface Profile {
  app_name: string;
  tagline: string;
  about_md: string;
  lists: Record<string, ListProfile>;
  /** Parts of the app this deployment shows; a section it does not name is shown. */
  sections?: Record<string, boolean>;
  /** What a radar is for here ("" = the built-in text). */
  radars_help?: string;
  version: number;
}

/** What Radars and Getting started say a radar is for, unless the deployment says otherwise. */
export const DEFAULT_RADARS_HELP =
  'A radar places things in rings: competitors by how close they are, vendors by relationship, technologies by adopt / trial / assess / hold.';

export const DEFAULT_APP_NAME = 'Agentic Insights Radar';

const state: {
  appName: string;
  tagline: string;
  aboutMd: string;
  lists: Record<string, ListProfile>;
  sections: Record<string, boolean>;
  radarsHelp: string;
} = {
  appName: DEFAULT_APP_NAME,
  tagline: '',
  aboutMd: '',
  lists: {},
  sections: {},
  radarsHelp: '',
};

/** The built-in list each node type is published in, inverted: list -> node type. */
const LIST_TYPES: Record<string, string> = Object.fromEntries(Object.entries(NODE_LISTS).map(([t, k]) => [k, t]));

export function applyProfile(profile: Profile | undefined | null): void {
  if (!profile) return;
  state.appName = profile.app_name || DEFAULT_APP_NAME;
  state.tagline = profile.tagline ?? '';
  state.aboutMd = profile.about_md ?? '';
  state.lists = profile.lists ?? {};
  state.sections = profile.sections ?? {};
  state.radarsHelp = profile.radars_help ?? '';
  for (const [kind, lp] of Object.entries(state.lists)) {
    if (!(kind in KIND_LABELS)) continue; // custom kinds take their label from /api/nav
    // Only what the deployment changed: the built-in module names stay unless the list is renamed.
    const set = new Set(lp.set ?? []);
    // Every built-in list has help text and a node type (format.ts), so both lookups always hit.
    const help = KIND_HELP[kind] as (typeof KIND_HELP)[string];
    if (set.has('label')) {
      KIND_LABELS[kind] = lp.label;
      help.moduleName = lp.label;
      MODULE_NAMES[help.module] = lp.label;
    }
    if (set.has('help') && lp.help) help.about = lp.help;
    if (set.has('item')) TYPE_LABELS[LIST_TYPES[kind] as string] = lp.item;
  }
  document.title = state.appName;
}

export const appName = (): string => state.appName;
export const tagline = (): string => state.tagline;
export const aboutMd = (): string => state.aboutMd;

/** Whether a part of the app (Products) is shown in this deployment. Unknown sections show. */
export const sectionVisible = (key: string): boolean => state.sections[key] ?? true;

/** What a radar is for in this deployment. */
export const radarsHelp = (): string => state.radarsHelp || DEFAULT_RADARS_HELP;

/** Whether a list (built-in kind or `insight:<kind>`) is shown in this deployment. Unknown lists show. */
export const listVisible = (kind: string): boolean => state.lists[kind]?.visible ?? true;

/** Display order of a list; lists without a profile entry keep their place after configured ones. */
export const listOrder = (kind: string): number => state.lists[kind]?.order ?? 100;

/** A custom list's name from the profile (`lists["insight:<kind>"].label`), else the module's own. */
export function relabelNav(nav: Nav): Nav {
  return { ...nav, custom: nav.custom.map((c) => ({ ...c, label: state.lists[c.kind]?.label || c.label })) };
}
