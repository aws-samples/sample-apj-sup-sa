"use client";

import { Eye, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { LimitDialog } from "@/components/common/LimitDialog";
import { QuotaBar } from "@/components/common/QuotaBar";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Field";
import { getErrorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useUsers } from "@/lib/queries";
import type { UserStatus } from "@/lib/types";
import styles from "@/features/pages.module.css";

export default function UsersPage() {
  const [status, setStatus] = useState<UserStatus | "all">("all");
  const [team, setTeam] = useState("all");
  const [q, setQ] = useState("");
  const {
    data,
    error,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useUsers({ status, team, q });

  const users = useMemo(() => (data?.pages ?? []).flatMap((page) => page.users), [data]);
  const teams = useMemo(() => Array.from(new Set(users.map((user) => user.team).filter(Boolean))).sort(), [users]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Users</h2>
          <p className={styles.pageDescription}>
            Review per-user quota pressure across day, week, and month scopes.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>User Usage</CardTitle>
            <CardDescription>Filter by status or team and open a user for detailed operations.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className={styles.toolbar}>
            <div className={styles.filters}>
              <div className={styles.searchBox}>
                <Search aria-hidden size={16} />
                <Input
                  aria-label="Search users"
                  placeholder="Search users"
                  value={q}
                  onChange={(event) => setQ(event.target.value)}
                />
              </div>
              <Select aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value as UserStatus | "all")}>
                <option value="all">All statuses</option>
                <option value="normal">Normal</option>
                <option value="warning">Warning</option>
                <option value="blocked">Blocked</option>
                <option value="exempt">Exempt</option>
              </Select>
              {teams.length > 0 ? (
                <Select aria-label="Team" value={team} onChange={(event) => setTeam(event.target.value)}>
                  <option value="all">All teams</option>
                  {teams.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </Select>
              ) : null}
            </div>
            {team !== "all" ? (
              // Team is console-only metadata applied client-side, so it narrows within the
              // already-loaded pages (status/search are server-side over all users). Disclosed
              // so the filtered count isn't mistaken for a global team total.
              <p className={styles.subtle} style={{ marginTop: 6 }}>
                Team filter applies to loaded users — load more pages to widen its reach.
              </p>
            ) : null}
          </div>

          <div className={styles.tableWrap} style={{ marginTop: 14 }}>
            <table className="data-grid">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Status</th>
                  <th>Day</th>
                  <th>Week</th>
                  <th>Month</th>
                  <th>Last updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      Loading users...
                    </td>
                  </tr>
                ) : error ? (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      {getErrorMessage(error)}
                    </td>
                  </tr>
                ) : users.length ? (
                  users.map((user) => (
                    <tr key={user.userArn}>
                      <td>
                        <div className={styles.userCell}>
                          <span className={styles.userName}>
                            {user.username}{user.principalType === "role" ? " (role)" : ""}
                          </span>
                          <span className={styles.subtle}>{[user.displayName, user.team].filter(Boolean).join(" · ") || user.userArn}</span>
                        </div>
                      </td>
                      <td>
                        <StatusBadge status={user.status} />
                      </td>
                      <td>
                        <QuotaBar quota={user.day} />
                      </td>
                      <td>
                        <QuotaBar quota={user.week} />
                      </td>
                      <td>
                        <QuotaBar quota={user.month} />
                      </td>
                      <td className={styles.subtle}>{formatDateTime(user.lastUpdated)}</td>
                      <td>
                        {user.manageable ? (
                          <div className={styles.actions}>
                            <Button asChild size="sm" variant="secondary">
                              <Link href={`/users/detail?username=${encodeURIComponent(user.username)}`}>
                                <Eye size={15} />
                                View
                              </Link>
                            </Button>
                            <LimitDialog username={user.username} />
                          </div>
                        ) : (
                          <span className={styles.subtle}>Role aggregate</span>
                        )}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      No users match the selected filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className={styles.actionsCenter}>
            {hasNextPage ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage ? "Loading…" : "Load more users"}
              </Button>
            ) : !isLoading && users.length > 0 ? (
              <span className={styles.subtle}>All matching users shown.</span>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
