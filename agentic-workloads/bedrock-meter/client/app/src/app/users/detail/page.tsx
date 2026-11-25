"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { UserDetailPage } from "@/features/UserDetailPage";
import styles from "@/features/pages.module.css";

// Query-param route (?username=) instead of a [username] dynamic segment, so the
// console can be a fully static export (no per-user prerender / server runtime).
function UserDetail() {
  const username = useSearchParams().get("username") || "";
  if (!username) {
    return <div className={styles.empty}>No user selected.</div>;
  }
  return <UserDetailPage username={username} />;
}

export default function UserDetailRoute() {
  return (
    <Suspense fallback={<div className={styles.empty}>Loading user detail…</div>}>
      <UserDetail />
    </Suspense>
  );
}
