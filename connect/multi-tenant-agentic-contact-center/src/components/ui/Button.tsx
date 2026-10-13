import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const variants: Record<Variant, string> = {
  primary:
    "bg-brand-600 text-white hover:bg-brand-700 shadow-sm border border-transparent",
  secondary:
    "bg-white text-ink-700 hover:bg-ink-50 border border-ink-200 shadow-sm",
  ghost: "bg-transparent text-ink-600 hover:bg-ink-100 border border-transparent",
  danger:
    "bg-red-600 text-white hover:bg-red-700 border border-transparent shadow-sm",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  className,
  // Default to "button" so a Button never implicitly submits a surrounding
  // <form> (which reloads the page). Pass type="submit" explicitly when needed.
  type = "button",
  ...rest
}: {
  children: ReactNode;
  variant?: Variant;
  size?: Size;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-brand-500/40 disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function ProgressBar({
  value,
  tone = "#635bff",
  className,
}: {
  value: number;
  tone?: string;
  className?: string;
}) {
  return (
    <div className={cn("h-1.5 w-full rounded-full bg-ink-100", className)}>
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${Math.min(100, Math.max(0, value))}%`, backgroundColor: tone }}
      />
    </div>
  );
}
