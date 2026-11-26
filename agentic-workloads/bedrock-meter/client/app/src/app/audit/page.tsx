"use client";

import { FileClock, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Field";
import { getErrorMessage } from "@/lib/api";
import { auditActorDisplay } from "@/lib/audit-display";
import { formatDateTime } from "@/lib/format";
import { useAudit } from "@/lib/queries";
import type { AuditEvent } from "@/lib/types";
import styles from "@/features/pages.module.css";

function eventTone(event: AuditEvent): "neutral" | "blue" | "amber" | "red" | "green" {
  if (event === "BLOCKED") return "red";
  if (event === "WARNING") return "amber";
  if (event === "MANUAL_UNBLOCK" || event === "REEVALUATE_UNBLOCK" || event === "RESOLVED") return "green";
  if (event === "LIMIT_CHANGED" || event === "EXEMPT_CHANGED" || event === "QUOTA_TIMEZONE_CHANGED") return "blue";
  return "neutral";
}

const KNOWN_EVENTS: AuditEvent[] = [
  "LIMIT_CHANGED",
  "LIMIT_REMOVED",
  "QUOTA_TIMEZONE_CHANGED",
  "EXEMPT_CHANGED",
  "BLOCKED",
  "WARNING",
  "MANUAL_UNBLOCK",
  "RESOLVED",
];

export default function AuditPage() {
  const [eventFilter, setEventFilter] = useState<AuditEvent | "all">("all");
  const [q, setQ] = useState("");
  // Event filter is applied server-side; free-text search stays client-side over
  // the already-loaded pages so typing doesn't fire a request per keystroke.
  const {
    data,
    error,
    isLoading,
    isFetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useAudit({
    event: eventFilter === "all" ? undefined : eventFilter,
    limit: 50,
  });

  const records = useMemo(() => (data?.pages ?? []).flatMap((page) => page.records), [data]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return records;
    return records.filter((record) =>
      `${record.event} ${record.actor} ${auditActorDisplay(record.actor).label} ${record.target} ${record.reason}`
        .toLowerCase()
        .includes(query),
    );
  }, [records, q]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Audit</h2>
          <p className={styles.pageDescription}>
            Track warning, blocking, limit, exempt, and unblock decisions with operator-provided reasons.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Audit Log</CardTitle>
            <CardDescription>
              Persistent record of admin actions and system decisions{isFetching ? " · refreshing…" : ""}.
            </CardDescription>
          </div>
          <FileClock size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.toolbar}>
            <div className={styles.filters}>
              <div className={styles.searchBox}>
                <Search aria-hidden size={16} />
                <Input
                  aria-label="Search audit"
                  placeholder="Search actor, target, reason"
                  value={q}
                  onChange={(event) => setQ(event.target.value)}
                />
              </div>
              <Select
                aria-label="Event type"
                value={eventFilter}
                onChange={(event) => setEventFilter(event.target.value as AuditEvent | "all")}
              >
                <option value="all">All events</option>
                {KNOWN_EVENTS.map((event) => (
                  <option key={event} value={event}>
                    {event}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className={styles.tableWrap} style={{ marginTop: 14 }}>
            <table className={`data-grid ${styles.auditTable}`}>
              <colgroup>
                <col className={styles.auditTimeColumn} />
                <col className={styles.auditEventColumn} />
                <col className={styles.auditActorColumn} />
                <col className={styles.auditTargetColumn} />
                <col className={styles.auditReasonColumn} />
                <col className={styles.auditChangeColumn} />
              </colgroup>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>Target</th>
                  <th>Reason</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={6} className={styles.empty}>
                      Loading audit log...
                    </td>
                  </tr>
                ) : error ? (
                  <tr>
                    <td colSpan={6} className={styles.empty}>
                      {getErrorMessage(error)}
                    </td>
                  </tr>
                ) : filtered.length ? (
                  filtered.map((record) => {
                    const actor = auditActorDisplay(record.actor);
                    return (
                      <tr key={record.id}>
                        <td className={styles.subtle}>{formatDateTime(record.timestamp)}</td>
                        <td>
                          <Badge tone={eventTone(record.event)}>{record.event}</Badge>
                        </td>
                        <td className={styles.auditActorCell}>
                          {actor.isBedrockMeter ? (
                            <Badge tone="neutral" className={styles.systemActor}>BedrockMeter</Badge>
                          ) : (
                            <span className={styles.actorText} title={actor.label}>{actor.label}</span>
                          )}
                        </td>
                        <td className={styles.auditTargetCell}>{record.target}</td>
                        <td className={styles.auditReasonCell}>{record.reason}</td>
                        <td>
                          {record.before || record.after ? (
                            <div className={styles.changeCell}>
                              {record.before ? (
                                <span className={styles.changeLine}>
                                  <span className={styles.changeLabel}>Before</span>
                                  <span className={styles.changeValue}>{record.before}</span>
                                </span>
                              ) : null}
                              {record.after ? (
                                <span className={styles.changeLine}>
                                  <span className={styles.changeLabel}>After</span>
                                  <span className={styles.changeValue}>{record.after}</span>
                                </span>
                              ) : null}
                            </div>
                          ) : (
                            <span className={styles.subtle}>No field diff</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={6} className={styles.empty}>
                      No audit records match the selected filters.
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
                {isFetchingNextPage ? "Loading…" : "Load older records"}
              </Button>
            ) : !isLoading && records.length > 0 ? (
              <span className={styles.subtle}>End of audit history.</span>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
