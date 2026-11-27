import { cn } from "@/lib/utils";
import styles from "@/components/ui/ui.module.css";

type ProgressProps = {
  value: number;
  tone?: "blue" | "amber" | "red" | "green";
  className?: string;
};

export function Progress({ value, tone = "blue", className }: ProgressProps) {
  const bounded = Math.max(0, Math.min(value, 100));
  return (
    <div className={cn(styles.progressTrack, className)} role="progressbar" aria-valuenow={bounded}>
      <div className={cn(styles.progressValue, styles[`progress_${tone}`])} style={{ width: `${bounded}%` }} />
    </div>
  );
}
