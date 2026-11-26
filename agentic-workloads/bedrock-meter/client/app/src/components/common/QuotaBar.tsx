import { Progress } from "@/components/ui/Progress";
import { compactNumber, percent } from "@/lib/format";
import type { QuotaUsage } from "@/lib/types";
import styles from "@/components/common/common.module.css";

function tone(value: number): "blue" | "amber" | "red" | "green" {
  if (value >= 100) return "red";
  if (value >= 80) return "amber";
  return "blue";
}

export function QuotaBar({ quota }: { quota: QuotaUsage }) {
  return (
    <div className={styles.quota}>
      <div className={styles.quotaMeta}>
        <span>{percent(quota.percentage)}</span>
        <span>
          {compactNumber(quota.used)} / {compactNumber(quota.limit)}
        </span>
      </div>
      <Progress value={quota.percentage} tone={tone(quota.percentage)} />
    </div>
  );
}
