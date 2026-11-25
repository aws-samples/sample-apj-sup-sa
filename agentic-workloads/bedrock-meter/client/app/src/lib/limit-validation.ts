import type { LimitOverride } from "@/lib/types";

/**
 * Client-side limit validation, mirroring the backend rules so operators see problems inline
 * instead of getting a 400 back:
 *  - each limit must be >= 1 (the backend rejects 0/negative — 0 would DISABLE a scope, not
 *    block it; a blanked numeric input becomes 0, so this catches the common empty-field case);
 *  - weekly >= daily, monthly >= weekly.
 * Skipped entirely for exempt users, whose limits are not enforced.
 */
export type LimitDraft = Pick<
  LimitOverride,
  "dailyTokenLimit" | "weeklyTokenLimit" | "monthlyTokenLimit" | "exempt"
>;

const SCOPE_LABELS: { key: keyof LimitDraft; label: string }[] = [
  { key: "dailyTokenLimit", label: "Daily" },
  { key: "weeklyTokenLimit", label: "Weekly" },
  { key: "monthlyTokenLimit", label: "Monthly" },
];

export function limitHierarchyErrors(draft: LimitDraft): string[] {
  if (draft.exempt) return [];

  const errors: string[] = [];
  for (const { key, label } of SCOPE_LABELS) {
    const value = draft[key] as number;
    if (!Number.isFinite(value) || value < 1) {
      errors.push(`${label} limit must be at least 1.`);
    }
  }

  const daily = draft.dailyTokenLimit;
  const weekly = draft.weeklyTokenLimit;
  const monthly = draft.monthlyTokenLimit;

  if (daily >= 1 && weekly >= 1 && weekly < daily) {
    errors.push("Weekly limit must be greater than or equal to the daily limit.");
  }
  if (weekly >= 1 && monthly >= 1 && monthly < weekly) {
    errors.push("Monthly limit must be greater than or equal to the weekly limit.");
  }
  return errors;
}
