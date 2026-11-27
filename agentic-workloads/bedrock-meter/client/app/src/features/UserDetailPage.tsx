"use client";

import { ArrowLeft, Ban, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
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
import { ReasonDialog } from "@/components/common/ReasonDialog";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { getErrorMessage, type HistoryGranularity } from "@/lib/api";
import { compactNumber, formatDateTime, formatTrendTick, fullNumber, localZoneLabel } from "@/lib/format";
import { useForceUnblockUser, useUser, useUserHistory } from "@/lib/queries";
import type { TokenBreakdown, UsagePoint } from "@/lib/types";
import styles from "@/features/pages.module.css";

const trendTickLabel = formatTrendTick;

type ChartMode = "stacked" | "lines";

type TokenSeries = { key: string; name: string; stroke: string; fill: string };

// Cache tokens dwarf raw input/output (cache read often >99% of volume), so they
// get their own chart with an independent Y axis. Splitting by magnitude keeps
// every series readable instead of cache read flattening everything else.
const CACHE_SERIES: TokenSeries[] = [
  { key: "cacheRead", name: "Cache read", stroke: "#0f766e", fill: "#ccfbf1" },
  { key: "cacheWrite", name: "Cache write", stroke: "#7c3aed", fill: "#ede9fe" },
];
const IO_SERIES: TokenSeries[] = [
  { key: "input", name: "Raw input", stroke: "#2563eb", fill: "#dbeafe" },
  { key: "output", name: "Output", stroke: "#d97706", fill: "#fef3c7" },
];

function TokenTrendChart({
  data,
  series,
  mode,
  granularity,
}: {
  data: UsagePoint[];
  series: TokenSeries[];
  mode: ChartMode;
  granularity: HistoryGranularity;
}) {
  return (
    <div className={styles.chart}>
      <ResponsiveContainer>
        <AreaChart data={data}>
          <CartesianGrid stroke="#e6ebf2" vertical={false} />
          <XAxis
            dataKey="day"
            tickLine={false}
            axisLine={false}
            fontSize={12}
            minTickGap={granularity === "hour" ? 24 : 16}
            tickFormatter={(value) => trendTickLabel(String(value), granularity)}
          />
          <YAxis tickFormatter={compactNumber} tickLine={false} axisLine={false} fontSize={12} width={48} />
          <Tooltip
            labelFormatter={(value) => trendTickLabel(String(value), granularity)}
            formatter={(value, name) => [compactNumber(Number(value)), name]}
          />
          <Legend />
          {series.map((s) => (
            <Area
              key={s.key}
              dataKey={s.key}
              name={s.name}
              type="monotone"
              stackId={mode === "stacked" ? "tokens" : undefined}
              stroke={s.stroke}
              fill={s.fill}
              fillOpacity={mode === "stacked" ? 0.85 : 0.25}
              strokeWidth={2}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function BreakdownRows({ breakdown }: { breakdown: TokenBreakdown }) {
  const total = Object.values(breakdown).reduce((sum, value) => sum + value, 0);
  const rows = [
    { label: "Raw input", value: breakdown.rawInput },
    { label: "Cache read", value: breakdown.cacheRead },
    { label: "Cache write", value: breakdown.cacheWrite },
    { label: "Output", value: breakdown.output },
  ];

  return (
    <div className={styles.breakdown}>
      {rows.map((row) => (
        <div className={styles.breakdownRow} key={row.label}>
          <span>{row.label}</span>
          <div className={styles.miniBarTrack} aria-hidden>
            <div
              className={styles.miniBarValue}
              style={{ width: `${total ? Math.max(4, Math.round((row.value / total) * 100)) : 0}%` }}
            />
          </div>
          <strong>{compactNumber(row.value)}</strong>
        </div>
      ))}
    </div>
  );
}

export function UserDetailPage({ username }: { username: string }) {
  const [notice, setNotice] = useState("");
  const [granularity, setGranularity] = useState<HistoryGranularity>("day");
  const [chartMode, setChartMode] = useState<ChartMode>("stacked");
  const unblockUser = useForceUnblockUser();
  const { canWrite } = useAuth();
  const { data: user, error, isLoading, refetch: refetchUser } = useUser(username);
  const { data: history = [], refetch: refetchHistory } = useUserHistory(username, granularity);

  if (isLoading) {
    return <div className={styles.empty}>Loading user detail...</div>;
  }

  if (error) {
    return <div className={styles.empty}>{getErrorMessage(error)}</div>;
  }

  if (!user) {
    return (
      <div className={styles.page}>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>User not found</CardTitle>
              <CardDescription>No usage record exists for {username}.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <Button asChild variant="secondary">
              <Link href="/users">
                <ArrowLeft size={15} />
                Back to users
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <Button asChild variant="ghost" size="sm">
            <Link href="/users">
              <ArrowLeft size={15} />
              Users
            </Link>
          </Button>
          <h2 className={styles.pageTitle}>{user.username}</h2>
          <p className={styles.pageDescription}>
            {[user.displayName, user.team, `Last updated ${formatDateTime(user.lastUpdated)}`].filter(Boolean).join(" · ")}
          </p>
        </div>
        <StatusBadge status={user.status} />
      </div>

      {notice ? <div className={styles.notice}>{notice}</div> : null}

      <section className={styles.threeColumn}>
        <MetricCard label="Today" value={compactNumber(user.day.used)} detail={`${user.day.percentage}% of daily limit`} icon={<ShieldCheck size={18} />} />
        <MetricCard label="Week" value={compactNumber(user.week.used)} detail={`${user.week.percentage}% of weekly limit`} icon={<ShieldCheck size={18} />} />
        <MetricCard label="Month" value={compactNumber(user.month.used)} detail={`${user.month.percentage}% of monthly limit`} icon={<ShieldCheck size={18} />} />
      </section>

      <section className={styles.twoColumn}>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Quota Pressure</CardTitle>
              <CardDescription>Day, week, and month scopes are enforced independently.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className={styles.quotaStack}>
              <div>
                <div className={styles.sectionLabel}>Day</div>
                <QuotaBar quota={user.day} />
              </div>
              <div>
                <div className={styles.sectionLabel}>Week</div>
                <QuotaBar quota={user.week} />
              </div>
              <div>
                <div className={styles.sectionLabel}>Month</div>
                <QuotaBar quota={user.month} />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Token Breakdown</CardTitle>
              <CardDescription>Current daily token composition (raw breakdown, not credits).</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <BreakdownRows breakdown={user.breakdown} />
          </CardContent>
        </Card>
      </section>

      <section className={styles.twoColumn}>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>User Trend</CardTitle>
              <CardDescription>
                {granularity === "hour"
                  ? `Hourly token shape over the last 24 hours (${localZoneLabel()}).`
                  : "Daily token shape over the last 30 days (UTC days)."}
              </CardDescription>
            </div>
            <div className={styles.chartControls}>
              <div className={styles.segmented} role="group" aria-label="Trend granularity">
                <Button
                  size="sm"
                  variant={granularity === "day" ? "primary" : "ghost"}
                  onClick={() => setGranularity("day")}
                >
                  Day
                </Button>
                <Button
                  size="sm"
                  variant={granularity === "hour" ? "primary" : "ghost"}
                  onClick={() => setGranularity("hour")}
                >
                  Hour
                </Button>
              </div>
              <div className={styles.segmented} role="group" aria-label="Chart mode">
                <Button
                  size="sm"
                  variant={chartMode === "stacked" ? "primary" : "ghost"}
                  onClick={() => setChartMode("stacked")}
                >
                  Stacked
                </Button>
                <Button
                  size="sm"
                  variant={chartMode === "lines" ? "primary" : "ghost"}
                  onClick={() => setChartMode("lines")}
                >
                  Lines
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {history.length ? (
              <div className={styles.chartStack}>
                <div>
                  <div className={styles.sectionLabel}>Cache tokens</div>
                  <TokenTrendChart data={history} series={CACHE_SERIES} mode={chartMode} granularity={granularity} />
                </div>
                <div>
                  <div className={styles.sectionLabel}>Input / output tokens</div>
                  <TokenTrendChart data={history} series={IO_SERIES} mode={chartMode} granularity={granularity} />
                </div>
              </div>
            ) : (
              <div className={styles.empty}>
                {granularity === "hour"
                  ? "No invocations in the last 24 hours."
                  : "No invocations in the last 30 days."}
              </div>
            )}
          </CardContent>
        </Card>

        <div className={styles.sideStack}>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Effective Limits</CardTitle>
                <CardDescription>Values resolved from default policy plus user override.</CardDescription>
              </div>
              <SlidersHorizontal size={18} aria-hidden />
            </CardHeader>
            <CardContent>
              <dl className={styles.keyValues}>
                <div>
                  <dt>Daily</dt>
                  <dd>{fullNumber(user.day.limit)}</dd>
                </div>
                <div>
                  <dt>Weekly</dt>
                  <dd>{fullNumber(user.week.limit)}</dd>
                </div>
                <div>
                  <dt>Monthly</dt>
                  <dd>{fullNumber(user.month.limit)}</dd>
                </div>
                <div>
                  <dt>User ARN</dt>
                  <dd className={styles.codeText}>{user.userArn}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Operations</CardTitle>
                <CardDescription>Admin actions require an audit reason.</CardDescription>
              </div>
              <Ban size={18} aria-hidden />
            </CardHeader>
            <CardContent>
              <div className={styles.actionsStart}>
                <Button asChild variant="secondary">
                  <Link href={`/limits?user=${user.username}`}>Edit limits</Link>
                </Button>
                {user.status === "blocked" ? (
                  <ReasonDialog
                    title="Force unblock user"
                    description={canWrite
                      ? "This removes the inline deny policy and records the reason in the console activity stream."
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
                ) : (
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      await Promise.all([refetchUser(), refetchHistory()]);
                      setNotice(`Latest quota state loaded for ${user.username}.`);
                    }}
                  >
                    Refresh
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </section>
    </div>
  );
}
