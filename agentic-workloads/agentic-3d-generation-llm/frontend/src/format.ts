// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export const fmtTokens = (n?: number) => (n === undefined ? '—' : n.toLocaleString('en-US'));

export const fmtSeconds = (s?: number) => {
  if (s === undefined) return '—';
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
};

export const fmtCost = (usd?: number) => {
  if (usd === undefined) return '—';
  if (usd === 0) return '$0.00';
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`;
};

export const fmtBytes = (b?: number) => {
  if (!b) return '';
  return b < 1024 * 1024 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`;
};

export const fmtWhen = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export const secondsSince = (iso?: string) => (iso ? Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000) : 0);
