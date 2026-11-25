"use client";

import { RotateCcw, ShieldAlert, Unlock } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { QuotaBar } from "@/components/common/QuotaBar";
import { ReasonDialog } from "@/components/common/ReasonDialog";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { getErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useBlockedUsers, useForceUnblockUser } from "@/lib/queries";
import styles from "@/features/pages.module.css";

export default function BlockedPage() {
  const [notice, setNotice] = useState("");
  const unblockUser = useForceUnblockUser();
  const { canWrite } = useAuth();
  const { data = [], error, isLoading, refetch } = useBlockedUsers();

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Blocked</h2>
          <p className={styles.pageDescription}>
            Review users with an active Bedrock deny policy and decide whether to reevaluate or force unblock.
          </p>
        </div>
      </div>

      {notice ? <div className={styles.notice}>{notice}</div> : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Blocked Users</CardTitle>
            <CardDescription>Force unblock should be reserved for reviewed operational exceptions.</CardDescription>
          </div>
          <ShieldAlert size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.tableWrap}>
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
                      Loading blocked users...
                    </td>
                  </tr>
                ) : error ? (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      {getErrorMessage(error)}
                    </td>
                  </tr>
                ) : data.length ? (
                  data.map((user) => (
                    <tr key={user.userArn}>
                      <td>
                        <div className={styles.userCell}>
                          <span className={styles.userName}>{user.username}</span>
                          <span className={styles.subtle}>{user.userArn}</span>
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
                        <div className={styles.actions}>
                          <Button asChild size="sm" variant="secondary">
                            <Link href={`/users/detail?username=${encodeURIComponent(user.username)}`}>Inspect</Link>
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={async () => {
                              await refetch();
                              setNotice(`Latest blocked state loaded for ${user.username}.`);
                            }}
                          >
                            Refresh
                          </Button>
                          <ReasonDialog
                            title={`Force unblock ${user.username}`}
                            description={canWrite
                              ? "This removes the inline deny policy immediately and records the reason in the console activity stream."
                              : "Force unblock requires the admin role."}
                            confirmLabel="Force unblock"
                            triggerVariant="danger"
                            triggerDisabled={!canWrite}
                            danger
                            onConfirm={async (reason) => {
                              await unblockUser.mutateAsync({ username: user.username, reason });
                              setNotice(`Force unblock completed for ${user.username}.`);
                            }}
                          />
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      No users are currently blocked.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <section className={styles.twoColumn}>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Reevaluation Model</CardTitle>
              <CardDescription>Automatic unblock happens only when every enforced scope is below limit.</CardDescription>
            </div>
            <RotateCcw size={18} aria-hidden />
          </CardHeader>
          <CardContent>
            <div className={styles.guardrailGrid}>
              <div>
                <strong>Day</strong>
                <p>Hot-path DynamoDB counter must be below the daily limit.</p>
              </div>
              <div>
                <strong>Week</strong>
                <p>Athena aggregate must be below the weekly limit.</p>
              </div>
              <div>
                <strong>Month</strong>
                <p>Athena aggregate must be below the monthly limit.</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Manual Unblock</CardTitle>
              <CardDescription>Use force unblock when the operator has verified the exception outside automation.</CardDescription>
            </div>
            <Unlock size={18} aria-hidden />
          </CardHeader>
          <CardContent>
            <p className={styles.bodyText}>
              The console calls{" "}
              <code className={styles.inlineCode}>POST /admin/unblock/{"{username}"}</code> through the UI backend, not
              from the browser. That keeps SigV4 signing and AWS credentials off the client.
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
