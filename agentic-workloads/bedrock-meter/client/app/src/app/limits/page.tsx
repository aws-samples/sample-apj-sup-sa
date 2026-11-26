import { Suspense } from "react";
import { LimitsPageClient } from "@/features/LimitsPageClient";
import styles from "@/features/pages.module.css";

export default function LimitsPage() {
  return (
    <Suspense fallback={<div className={styles.empty}>Loading limits...</div>}>
      <LimitsPageClient />
    </Suspense>
  );
}
