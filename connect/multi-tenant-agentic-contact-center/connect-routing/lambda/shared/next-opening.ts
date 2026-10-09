// ============================================================================
// Next-opening calculation for the OOH backlog (Pattern B).
// ============================================================================
// Pure functions (no AWS calls) so the scheduling rules are unit-testable:
//   - input  : GetEffectiveHoursOfOperations output (overrides ALREADY applied,
//              times in the hours' own time zone) + "now".
//   - output : the first opening that starts strictly after now, as epoch
//              seconds, plus an optional offset (agents need a few minutes to
//              log in), CAPPED at the StartTaskContact scheduling limit
//              (now + 6 days). When the real opening is beyond the cap the
//              result carries reschedule=true, and the OOH task flow re-checks
//              hours when that task starts (re-schedule loop).
// ----------------------------------------------------------------------------

export interface TimeSlice {
  Hours?: number;
  Minutes?: number;
}
export interface EffectiveDay {
  Date?: string; // YYYY-MM-DD in the hours' time zone
  OperationalHours?: { Start?: TimeSlice; End?: TimeSlice }[];
}

export interface NextOpening {
  /** Epoch seconds to schedule the task at (always in the future). */
  scheduleEpoch: number;
  /** The real next opening (may be beyond the cap), epoch seconds; null if none in the window. */
  nextOpenEpoch: number | null;
  /** True when scheduleEpoch was capped below the real opening (closure > cap). */
  reschedule: boolean;
}

// StartTaskContact: ScheduledTime must be in the future and at most 6 days ahead.
export const MAX_SCHEDULE_SECONDS = 6 * 24 * 3600;
// Keep clear of the hard limit (clock skew + flow/Lambda latency).
export const CAP_BUFFER_SECONDS = 15 * 60;
// A scheduled time closer than this to "now" is treated as not-in-the-future.
export const MIN_LEAD_SECONDS = 60;

/** Offset (ms) of `timeZone` from UTC at the given instant: local = utc + offset. */
function tzOffsetMs(utcMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}

/** Wall-clock date+time in `timeZone` -> epoch ms (DST-safe: offset re-evaluated at the result). */
export function zonedToEpochMs(date: string, hours: number, minutes: number, timeZone: string): number {
  const [y, m, d] = date.split("-").map(Number);
  const naive = Date.UTC(y, m - 1, d, hours, minutes);
  let utc = naive - tzOffsetMs(naive, timeZone);
  utc = naive - tzOffsetMs(utc, timeZone);
  return utc;
}

/** YYYY-MM-DD for `epochMs` in `timeZone`. */
export function zonedDate(epochMs: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(epochMs));
}

/** ISO-8601 local time with offset, e.g. 2026-10-07T08:00:00+08:00 (for the acknowledgement). */
export function zonedIso(epochMs: number, timeZone: string): string {
  const off = tzOffsetMs(epochMs, timeZone);
  const local = new Date(epochMs + off).toISOString().slice(0, 19);
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off) / 60000;
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  return `${local}${sign}${hh}:${mm}`;
}

/** First interval START strictly after now (epoch ms), or null if none in the window. */
export function firstOpeningAfter(days: EffectiveDay[], timeZone: string, nowMs: number): number | null {
  let best: number | null = null;
  for (const day of days) {
    if (!day.Date) continue;
    for (const oh of day.OperationalHours ?? []) {
      const s = oh.Start;
      if (!s) continue;
      const t = zonedToEpochMs(day.Date, s.Hours ?? 0, s.Minutes ?? 0, timeZone);
      if (t > nowMs && (best === null || t < best)) best = t;
    }
  }
  return best;
}

/**
 * Compute when to schedule the OOH task.
 * @param demoDelaySeconds when > 0, ignore the hours and schedule now + delay
 *        (demo mode: lets the task route minutes later instead of next morning).
 */
export function computeNextOpening(opts: {
  days: EffectiveDay[];
  timeZone: string;
  nowMs: number;
  openOffsetSeconds?: number;
  demoDelaySeconds?: number;
}): NextOpening {
  const nowS = Math.floor(opts.nowMs / 1000);
  const capS = nowS + MAX_SCHEDULE_SECONDS - CAP_BUFFER_SECONDS;

  if (opts.demoDelaySeconds && opts.demoDelaySeconds > 0) {
    const t = nowS + Math.max(opts.demoDelaySeconds, MIN_LEAD_SECONDS);
    return { scheduleEpoch: Math.min(t, capS), nextOpenEpoch: t, reschedule: false };
  }

  const openMs = firstOpeningAfter(opts.days, opts.timeZone, opts.nowMs);
  if (openMs === null) {
    // No opening in the lookup window (very long closure): park at the cap and
    // let the task flow re-check hours when it starts.
    return { scheduleEpoch: capS, nextOpenEpoch: null, reschedule: true };
  }
  const openS = Math.floor(openMs / 1000) + (opts.openOffsetSeconds ?? 0);
  if (openS > capS) return { scheduleEpoch: capS, nextOpenEpoch: openS, reschedule: true };
  return { scheduleEpoch: openS, nextOpenEpoch: openS, reschedule: false };
}

/** Guard used before StartTaskContact: the scheduled time must be in the future. */
export function isSchedulable(epochS: number, nowMs: number): boolean {
  const nowS = Math.floor(nowMs / 1000);
  return epochS >= nowS + MIN_LEAD_SECONDS && epochS <= nowS + MAX_SCHEDULE_SECONDS;
}
