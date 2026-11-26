import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/Card";
import styles from "@/components/common/common.module.css";

export function MetricCard({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: string;
  detail?: string;
  icon?: ReactNode;
}) {
  return (
    <Card>
      <CardContent className={styles.metricCard}>
        <div>
          <div className={styles.metricLabel}>{label}</div>
          <div className={styles.metricValue}>{value}</div>
          {detail ? <div className={styles.metricDetail}>{detail}</div> : null}
        </div>
        {icon ? <div className={styles.metricIcon}>{icon}</div> : null}
      </CardContent>
    </Card>
  );
}
