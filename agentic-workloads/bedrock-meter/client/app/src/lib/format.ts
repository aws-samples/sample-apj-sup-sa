const numberFmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const fullNumberFmt = new Intl.NumberFormat("en");
const dateFmt = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function compactNumber(value: number) {
  return numberFmt.format(value);
}

export function fullNumber(value: number) {
  return fullNumberFmt.format(value);
}

export function percent(value: number) {
  return `${Math.round(value)}%`;
}

export function formatDateTime(value?: string) {
  if (!value) return "Never";
  return dateFmt.format(new Date(value));
}

// Trend X-axis labels. Backend buckets are UTC: day = "2026-06-05",
// hour = "2026-06-05T13:00" (no timezone suffix). For hour we parse as UTC
// (append "Z") then render in the browser's local zone, so the axis shows
// local time. Day stays as-is — it's a date label and converting it would
// shift the day boundary.
const hourTickFmt = new Intl.DateTimeFormat(undefined, {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatTrendTick(value: string, granularity: "day" | "hour"): string {
  if (granularity === "hour" && value.includes("T")) {
    const utc = new Date(`${value}:00Z`); // "...T13:00" -> "...T13:00:00Z"
    if (!Number.isNaN(utc.getTime())) {
      return hourTickFmt.format(utc).replace(",", "");
    }
  }
  return value;
}

// Short local timezone label for axis captions, e.g. "GMT+9" / "KST".
export function localZoneLabel(): string {
  try {
    const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "local";
  } catch {
    return "local";
  }
}
