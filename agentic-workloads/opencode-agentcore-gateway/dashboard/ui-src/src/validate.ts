// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Client-side checks that mirror API.md, so most mistakes are caught before a request.
/** Parses an optional numeric input. '' means "not set" (null). Returns [value, error]. */
export function parseCap(raw: string, min = 0, max = 100000, integer = false): [number | null, string | undefined] {
  const s = raw.trim();
  if (s === '') return [null, undefined];
  const n = Number(s);
  if (!Number.isFinite(n)) return [null, 'Enter a number'];
  if (n < min || n > max) return [null, `Must be between ${min} and ${max.toLocaleString('en-US')}`];
  if (integer && !Number.isInteger(n)) return [null, 'Must be a whole number'];
  // A dollar cap between 0 and a cent would block like 0 without the $0 confirmation: the API refuses it too.
  if (!integer && min === 0 && n > 0 && n < 0.01) return [null, 'Use 0 to block, or at least 0.01'];
  return [n, undefined];
}

export const capToInput = (n: number | null | undefined) => (n === null || n === undefined ? '' : String(n));
