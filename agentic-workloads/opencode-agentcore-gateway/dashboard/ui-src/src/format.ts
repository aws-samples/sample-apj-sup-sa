// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Display helpers. All times are shown in UTC, labelled as such.

const usdFmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const intFmt = new Intl.NumberFormat('en-US');
const compactFmt = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

export function usd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '-';
  if (n > 0 && n < 0.01) return '<$0.01';
  return usdFmt.format(n);
}
export const int = (n: number | null | undefined) => (n === null || n === undefined ? '-' : intFmt.format(n));
export const compact = (n: number | null | undefined) => (n === null || n === undefined ? '-' : compactFmt.format(n));
export const pct = (n: number | null | undefined, digits = 0) => (n === null || n === undefined ? '-' : `${n.toFixed(digits)}%`);

export function utc(isoTs: string | null | undefined): string {
  if (!isoTs) return '-';
  const d = new Date(isoTs);
  if (Number.isNaN(d.getTime())) return isoTs;
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

export function ago(isoTs: string | null | undefined): string {
  if (!isoTs) return 'Never';
  const s = (Date.now() - new Date(isoTs).getTime()) / 1000;
  if (s < 60) return 'Just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const shortDate = (d: string) => {
  const dt = new Date(`${d.slice(0, 10)}T00:00:00Z`);
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
};
export const shortMonth = (d: string) => {
  const dt = new Date(`${d.slice(0, 7)}-01T00:00:00Z`);
  return dt.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
};
