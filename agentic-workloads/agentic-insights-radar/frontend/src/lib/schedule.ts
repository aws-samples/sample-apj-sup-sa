// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Delivery schedules in the reader's words: "Weekly on Monday at 08:00 Asia/Singapore". */

/** Day 0 is Monday, as the API (Python's weekday()) counts them. */
export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** 8 -> "08:00": schedules run on the hour. */
export const hourLabel = (hour: number): string => `${String(hour).padStart(2, '0')}:00`;

export interface Schedule {
  cadence: 'daily' | 'weekly';
  weekday?: number | null;
  hour: number;
  timezone: string;
}

export function scheduleText(s: Schedule): string {
  const at = `at ${hourLabel(s.hour)} ${s.timezone}`;
  return s.cadence === 'weekly' ? `Weekly on ${WEEKDAYS[s.weekday ?? 0] ?? WEEKDAYS[0]} ${at}` : `Daily ${at}`;
}

/** The browser's own zone, the default for a new delivery; UTC when the browser does not say. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * Every IANA zone the browser knows, A to Z, with UTC and `extra` (a delivery's saved zone, the
 * browser's own) added: some browsers leave UTC out of their list, and a saved zone must stay pickable.
 */
export function timeZones(extra: string[] = []): string[] {
  const intl = Intl as { supportedValuesOf?: (key: string) => string[] };
  const known = typeof intl.supportedValuesOf === 'function' ? intl.supportedValuesOf('timeZone') : [];
  return [...new Set([...known, 'UTC', ...extra.filter(Boolean)])].sort((a, b) => a.localeCompare(b));
}
