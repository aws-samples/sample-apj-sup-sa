import { cn } from "@/lib/cn";

type Tone =
  | "green"
  | "amber"
  | "red"
  | "blue"
  | "gray"
  | "purple"
  | "cyan";

const toneClasses: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  amber: "bg-amber-50 text-amber-700 ring-amber-600/20",
  red: "bg-red-50 text-red-700 ring-red-600/20",
  blue: "bg-blue-50 text-blue-700 ring-blue-600/20",
  gray: "bg-ink-100 text-ink-600 ring-ink-500/20",
  purple: "bg-brand-50 text-brand-700 ring-brand-600/20",
  cyan: "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
};

export function Badge({
  children,
  tone = "gray",
  dot = false,
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneClasses[tone],
        className
      )}
    >
      {dot && (
        <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      )}
      {children}
    </span>
  );
}

// Mapping helpers for domain statuses ---------------------------------------

export function paymentTone(status: string): Tone {
  switch (status) {
    case "succeeded":
      return "green";
    case "pending":
      return "amber";
    case "in_progress":
      return "blue";
    case "authorized":
      return "purple";
    case "failed":
      return "red";
    case "refunded":
      return "gray";
    case "disputed":
      return "purple";
    default:
      return "gray";
  }
}

export function merchantTone(status: string): Tone {
  switch (status) {
    case "active":
      return "green";
    case "review":
      return "amber";
    case "restricted":
      return "red";
    case "onboarding":
      return "blue";
    default:
      return "gray";
  }
}

export function payoutTone(status: string): Tone {
  switch (status) {
    case "paid":
      return "green";
    case "in_transit":
      return "blue";
    case "scheduled":
      return "gray";
    case "failed":
      return "red";
    default:
      return "gray";
  }
}

export function disputeTone(status: string): Tone {
  switch (status) {
    case "needs_response":
      return "red";
    case "under_review":
      return "amber";
    case "won":
      return "green";
    case "lost":
      return "gray";
    default:
      return "gray";
  }
}

export function priorityTone(priority: string): Tone {
  switch (priority) {
    case "urgent":
      return "red";
    case "high":
      return "amber";
    case "medium":
      return "blue";
    case "low":
      return "gray";
    default:
      return "gray";
  }
}

export function ticketTone(status: string): Tone {
  switch (status) {
    case "open":
      return "blue";
    case "pending":
      return "amber";
    case "resolved":
      return "green";
    default:
      return "gray";
  }
}

export function riskTone(level: string): Tone {
  switch (level) {
    case "low":
      return "green";
    case "medium":
      return "amber";
    case "high":
      return "red";
    default:
      return "gray";
  }
}

export function humanize(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
