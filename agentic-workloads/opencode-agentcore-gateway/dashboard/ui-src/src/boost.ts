// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { Limit, Period } from './types';

// The one boost-eligibility rule the UI shows, as the API's boost() enforces it: a boost needs a cap above $0 to
// raise. takesBoost answers it for a loaded limit row; boostRefusal says why period x is refused ('no cap', or
// '$0 cap (blocked)'), or undefined when it takes a boost or the limits are not loaded yet (the API then answers).
export const takesBoost = (l: Pick<Limit, 'base'>) => typeof l.base === 'number' && l.base > 0;

export function boostRefusal(rows: Pick<Limit, 'period' | 'base'>[] | undefined, x: Period): string | undefined {
  const row = rows?.find((l) => l.period === x);
  if (!row || row.base === undefined || takesBoost(row)) return undefined;
  return row.base === null ? 'no cap' : '$0 cap (blocked)';
}
