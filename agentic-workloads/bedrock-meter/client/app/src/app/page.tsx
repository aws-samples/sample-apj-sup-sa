"use client";

import { AlertTriangle, Ban, CalendarDays, Clock3 } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MetricCard } from "@/components/common/MetricCard";
import { QuotaBar } from "@/components/common/QuotaBar";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { compactNumber, formatDateTime } from "@/lib/format";
import { getErrorMessage } from "@/lib/api";
import { useOverview } from "@/lib/queries";
import styles from "@/features/pages.module.css";

// Cache tokens dwarf raw input/output, so they get a separate chart with its own
// Y axis. Each group stacks bottom→top.
const CACHE_SERIES = [
  { key: "cacheRead", name: "Cache read", stroke: "#0f766e", fill: "#ccfbf1" },
  { key: "cacheWrite", name: "Cache write", stroke: "#7c3aed", fill: "#ede9fe" },
] as const;
const IO_SERIES = [
  { key: "input", name: "Raw input", stroke: "#2563eb", fill: "#dbeafe" },
  { key: "output", name: "Output", stroke: "#d97706", fill: "#fef3c7" },
] as const;

type OverviewSeries = { key: string; name: string; stroke: string; fill: string };

function OverviewTrendChart({
  data,
  series,
}: {
  data: { day: string }[];
  series: readonly OverviewSeries[];
}) {
  return (
    <div className={styles.chart}>
      <ResponsiveContainer>
        <AreaChart data={data}>
          <CartesianGrid stroke="#e6ebf2" vertical={false} />
          <XAxis dataKey="day" tickLine={false} axisLine={false} fontSize={12} />
          <YAxis tickFormatter={compactNumber} tickLine={false} axisLine={false} fontSize={12} width={48} />
          <Tooltip formatter={(value, name) => [compactNumber(Number(value)), name]} />
          <Legend />
          {series.map((s) => (
            <Area
              key={s.key}
              dataKey={s.key}
              name={s.name}
              type="monotone"
              stackId="tokens"
              stroke={s.stroke}
              fill={s.fill}
              fillOpacity={0.85}
              strokeWidth={2}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function OverviewPage() {
  const { data, error, isLoading } = useOverview();

  if (isLoading) {
    return <div className={styles.empty}>Loading usage overview...</div>;
  }

  if (error) {
    return <div className={styles.empty}>{getErrorMessage(error)}</div>;
  }

  if (!data) {
    return <div className={styles.empty}>No overview data available.</div>;
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Overview</h2>
          <p className={styles.pageDescription}>
            Current credit usage, quota pressure, blocked users, and recent meter activity.
          </p>
        </div>
        <Badge tone={data.warning > 0 ? "amber" : "green"}>{data.warning} warning users</Badge>
      </div>

      <section className={styles.kpiGrid}>
        <MetricCard label="Today credits" value={compactNumber(data.totals.day)} detail={`${data.quotaTimeZone} current day`} icon={<Clock3 size={18} />} />
        <MetricCard label="Week credits" value={compactNumber(data.totals.week)} detail={`ISO week · ${data.quotaTimeZone}`} icon={<CalendarDays size={18} />} />
        <MetricCard label="Month credits" value={compactNumber(data.totals.month)} detail={`Calendar month · ${data.quotaTimeZone}`} icon={<CalendarDays size={18} />} />
        <MetricCard label="Blocked users" value={`${data.blocked}`} detail="Require review" icon={<Ban size={18} />} />
      </section>

      <section className={styles.twoColumn}>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Usage Trend</CardTitle>
              <CardDescription>
                Raw input, cache read/write, and output tokens over the last 30 days ({data.quotaTimeZone}).
                {data.historyMeta && data.historyMeta.totalUsers > data.historyMeta.sampledUsers
                  ? ` Sampled top ${data.historyMeta.sampledUsers} of ${data.historyMeta.totalUsers} users.`
                  : ""}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className={styles.chartStack}>
              <div>
                <div className={styles.sectionLabel}>Cache tokens</div>
                <OverviewTrendChart data={data.history} series={CACHE_SERIES} />
              </div>
              <div>
                <div className={styles.sectionLabel}>Input / output tokens</div>
                <OverviewTrendChart data={data.history} series={IO_SERIES} />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Recent Activity</CardTitle>
              <CardDescription>Latest warnings, blocks, and admin actions.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <table className="data-grid">
              <tbody>
                {data.events.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <div className={styles.userCell}>
                        <span className={styles.userName}>{event.event}</span>
                        <span className={styles.subtle}>{event.reason}</span>
                      </div>
                    </td>
                    <td>{event.target}</td>
                    <td className={styles.subtle}>{formatDateTime(event.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Top Users</CardTitle>
            <CardDescription>Users with the highest month-to-date credit usage.</CardDescription>
          </div>
          <AlertTriangle size={18} color="#d97706" aria-hidden />
        </CardHeader>
        <CardContent>
          <table className="data-grid">
            <thead>
              <tr>
                <th>User</th>
                <th>Status</th>
                <th>Day</th>
                <th>Week</th>
                <th>Month</th>
              </tr>
            </thead>
            <tbody>
              {data.topUsers.map((user) => (
                <tr key={user.userArn}>
                  <td>
                    <div className={styles.userCell}>
                      <span className={styles.userName}>{user.username}</span>
                      <span className={styles.subtle}>{user.team || user.userArn}</span>
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
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
