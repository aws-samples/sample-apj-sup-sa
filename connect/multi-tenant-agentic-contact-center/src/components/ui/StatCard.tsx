import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { TrendUp, TrendDown } from "./icons";
import { Sparkline } from "./Charts";
import type { TimePoint } from "@/data/types";

export function StatCard({
  label,
  value,
  delta,
  icon,
  spark,
  sparkColor = "#635bff",
}: {
  label: string;
  value: ReactNode;
  delta?: number;
  icon?: ReactNode;
  spark?: TimePoint[];
  sparkColor?: string;
}) {
  const positive = (delta ?? 0) >= 0;
  return (
    <div className="rounded-xl border border-ink-200 bg-white p-5 shadow-card">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-ink-500">{label}</span>
        {icon && (
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink-50 text-ink-500">
            {icon}
          </span>
        )}
      </div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <div>
          <div className="text-2xl font-bold tracking-tight text-ink-900">
            {value}
          </div>
          {delta !== undefined && (
            <div
              className={cn(
                "mt-1.5 flex items-center gap-1 text-xs font-medium",
                positive ? "text-emerald-600" : "text-red-600"
              )}
            >
              {positive ? (
                <TrendUp className="h-3.5 w-3.5" />
              ) : (
                <TrendDown className="h-3.5 w-3.5" />
              )}
              {positive ? "+" : ""}
              {delta.toFixed(1)}%
              <span className="text-ink-400">vs last 30d</span>
            </div>
          )}
        </div>
        {spark && (
          <div className="h-10 w-24">
            <Sparkline data={spark} color={sparkColor} />
          </div>
        )}
      </div>
    </div>
  );
}
