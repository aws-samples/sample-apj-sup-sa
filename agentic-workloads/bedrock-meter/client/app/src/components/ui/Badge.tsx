import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import styles from "@/components/ui/ui.module.css";

type BadgeTone = "neutral" | "blue" | "amber" | "red" | "green";

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
};

export function Badge({ className, tone = "neutral", ...props }: BadgeProps) {
  return <span className={cn(styles.badge, styles[`badge_${tone}`], className)} {...props} />;
}
