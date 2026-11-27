"use client";

import { LineChart as LineChartIcon } from "lucide-react";
import { useState } from "react";
import type { CSSProperties } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Field";
import { getErrorMessage } from "@/lib/api";
import { compactNumber, formatTrendTick, localZoneLabel } from "@/lib/format";
import { useModelTrend } from "@/lib/queries";
import type { ModelTrendMetric, ModelTrendPeriod, ModelTrendPoint, TrendGranularity } from "@/lib/types";
import styles from "@/features/pages.module.css";

// Stable, color-blind-friendly palette; "other" always renders grey.
const SERIES_COLORS = [
  "#2563eb",
  "#0f766e",
  "#d97706",
  "#9333ea",
  "#dc2626",
  "#0891b2",
  "#65a30d",
  "#db2777",
  "#4f46e5",
  "#ca8a04",
];
const OTHER_COLOR = "#94a3b8";

// `global.anthropic.claude-opus-4-8` → `opus-4-8`; keeps the legend legible.
function shortModelLabel(modelId: string): string {
  if (modelId === "other") return "other";
  const tail = modelId.split(".").pop() || modelId;
  return tail.replace(/^claude-/, "").replace(/-v\d+:\d+$/, "");
}

function seriesColor(modelId: string, index: number): string {
  if (modelId === "other") return OTHER_COLOR;
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

type ModelGroup = {
  key: string;
  label: string;
  sourceModels: string[];
  colorIndex: number;
};

function modelGroups(models: string[]): ModelGroup[] {
  return models.reduce<ModelGroup[]>((groups, modelId, index) => {
    const label = shortModelLabel(modelId);
    const existing = groups.find((group) => group.key === label);

    if (existing) {
      existing.sourceModels.push(modelId);
      return groups;
    }

    return [...groups, { key: label, label, sourceModels: [modelId], colorIndex: index }];
  }, []);
}

function groupedTrendPoints(points: ModelTrendPoint[], groups: ModelGroup[]): ModelTrendPoint[] {
  return points.map((point) => {
    const groupedPoint: ModelTrendPoint = { day: point.day };

    for (const group of groups) {
      groupedPoint[group.key] = group.sourceModels.reduce((sum, modelId) => sum + Number(point[modelId] ?? 0), 0);
    }

    return groupedPoint;
  });
}

const trendTickLabel = formatTrendTick;

type ChartType = "line" | "stacked";

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

// Default custom range: last 14 days ending today (local), as YYYY-MM-DD.
function defaultCustomRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - 13);
  return { from: isoDay(from), to: isoDay(to) };
}

// Period options differ by granularity: day uses calendar/relative ranges + custom;
// hour uses fixed sliding windows (7d/168 buckets is the legibility ceiling).
const DAY_PERIOD_OPTIONS: { value: ModelTrendPeriod; label: string }[] = [
  { value: "week", label: "This week" },
  { value: "month", label: "This month" },
  { value: "last7d", label: "Last 7 days" },
  { value: "last30d", label: "Last 30 days" },
  { value: "custom", label: "Custom range…" },
];
const HOUR_PERIOD_OPTIONS: { value: ModelTrendPeriod; label: string }[] = [
  { value: "last24h", label: "Last 24 hours" },
  { value: "last48h", label: "Last 48 hours" },
  { value: "last7d", label: "Last 7 days" },
];
const DEFAULT_DAY_PERIOD: ModelTrendPeriod = "month";
const DEFAULT_HOUR_PERIOD: ModelTrendPeriod = "last48h";
const HOUR_WINDOW_LABELS: Record<string, string> = {
  last24h: "the last 24 hours",
  last48h: "the last 48 hours",
  last7d: "the last 7 days",
};

export default function StatisticsPage() {
  const [period, setPeriod] = useState<ModelTrendPeriod>("month");
  const [metric, setMetric] = useState<ModelTrendMetric>("totalTokens");
  const [top, setTop] = useState(10);
  const [granularity, setGranularity] = useState<TrendGranularity>("day");
  const [chartType, setChartType] = useState<ChartType>("line");
  const [customRange, setCustomRange] = useState(defaultCustomRange);
  const [hiddenModelGroups, setHiddenModelGroups] = useState<string[]>([]);

  const isCustom = granularity === "day" && period === "custom";
  const rangeValid = customRange.from !== "" && customRange.to !== "" && customRange.from <= customRange.to;

  const { data, error, isLoading, isFetching } = useModelTrend(
    {
      period,
      metric,
      top,
      granularity,
      ...(isCustom ? { from: customRange.from, to: customRange.to } : {}),
    },
    // Pause while a custom range is mid-edit into an invalid state.
    !isCustom || rangeValid,
  );

  const models = data?.models ?? [];
  const points = data?.points ?? [];
  const hasData = points.length > 0 && models.length > 0;
  const groups = modelGroups(models);
  const chartPoints = groupedTrendPoints(points, groups);
  const visibleGroups = groups.filter((group) => !hiddenModelGroups.includes(group.key));
  const visibleModelCount = visibleGroups.length;

  function toggleModelGroup(groupKey: string) {
    setHiddenModelGroups((current) =>
      current.includes(groupKey) ? current.filter((item) => item !== groupKey) : [...current, groupKey],
    );
  }

  function switchGranularity(next: TrendGranularity) {
    if (next === granularity) return;
    setGranularity(next);
    // Period vocabularies don't overlap cleanly across granularities — reset to a valid default.
    setPeriod(next === "hour" ? DEFAULT_HOUR_PERIOD : DEFAULT_DAY_PERIOD);
  }

  const periodOptions = granularity === "hour" ? HOUR_PERIOD_OPTIONS : DAY_PERIOD_OPTIONS;

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Statistics</h2>
          <p className={styles.pageDescription}>
            Per-model token and invocation trends from the analytics API (Athena, cached ~60s).
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Model Usage Trend</CardTitle>
            <CardDescription>
              {granularity === "hour" ? "Hourly" : "Daily"}{" "}
              {metric === "invocations" ? "invocations" : "total tokens"} for the top {top} models
              {granularity === "hour"
                ? ` over ${HOUR_WINDOW_LABELS[period] ?? "the last 48 hours"} (${localZoneLabel()})`
                : ""}
              . Models outside the top {top} are folded into <strong>other</strong>.
            </CardDescription>
          </div>
          <LineChartIcon size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.toolbar}>
            <div className={styles.filters}>
              <div className={styles.segmented} role="group" aria-label="Trend granularity">
                <Button
                  size="sm"
                  variant={granularity === "day" ? "primary" : "ghost"}
                  onClick={() => switchGranularity("day")}
                >
                  Day
                </Button>
                <Button
                  size="sm"
                  variant={granularity === "hour" ? "primary" : "ghost"}
                  onClick={() => switchGranularity("hour")}
                >
                  Hour
                </Button>
              </div>
              <label className={styles.sectionLabel}>
                Period{" "}
                <Select value={period} onChange={(e) => setPeriod(e.target.value as ModelTrendPeriod)}>
                  {periodOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </Select>
              </label>
              {isCustom ? (
                <>
                  <label className={styles.sectionLabel}>
                    From{" "}
                    <Input
                      type="date"
                      value={customRange.from}
                      max={customRange.to || undefined}
                      onChange={(e) => setCustomRange((r) => ({ ...r, from: e.target.value }))}
                    />
                  </label>
                  <label className={styles.sectionLabel}>
                    To{" "}
                    <Input
                      type="date"
                      value={customRange.to}
                      min={customRange.from || undefined}
                      onChange={(e) => setCustomRange((r) => ({ ...r, to: e.target.value }))}
                    />
                  </label>
                </>
              ) : null}
              <label className={styles.sectionLabel}>
                Metric{" "}
                <Select value={metric} onChange={(e) => setMetric(e.target.value as ModelTrendMetric)}>
                  <option value="totalTokens">Total tokens</option>
                  <option value="invocations">Invocations</option>
                </Select>
              </label>
              <label className={styles.sectionLabel}>
                Top{" "}
                <Select value={String(top)} onChange={(e) => setTop(Number(e.target.value))}>
                  <option value="5">5</option>
                  <option value="10">10</option>
                  <option value="20">20</option>
                </Select>
              </label>
              <div className={styles.segmented} role="group" aria-label="Chart type">
                <Button
                  size="sm"
                  variant={chartType === "line" ? "primary" : "ghost"}
                  onClick={() => setChartType("line")}
                >
                  Line
                </Button>
                <Button
                  size="sm"
                  variant={chartType === "stacked" ? "primary" : "ghost"}
                  onClick={() => setChartType("stacked")}
                >
                  Stacked
                </Button>
              </div>
            </div>
            {isFetching ? <span className={styles.subtle}>Updating…</span> : null}
          </div>

          {isCustom && !rangeValid ? (
            <div className={styles.empty}>Pick a valid date range — “From” must be on or before “To”.</div>
          ) : error ? (
            <div className={styles.empty}>{getErrorMessage(error)}</div>
          ) : isLoading ? (
            <div className={styles.empty}>Loading model trend…</div>
          ) : !hasData ? (
            <div className={styles.empty}>
              {granularity === "hour"
                ? `No invocations in ${HOUR_WINDOW_LABELS[period] ?? "the last 48 hours"}.`
                : "No invocation data for this period yet."}
            </div>
          ) : (
            <div className={styles.chartStack}>
              <div className={styles.modelVisibility}>
                <div className={styles.modelVisibilityHeader}>
                  <div>
                    <div className={styles.sectionLabel}>Model visibility</div>
                    <div className={styles.subtle}>
                      Showing {visibleModelCount} of {groups.length}
                    </div>
                  </div>
                  <div className={styles.actionsStart}>
                    <Button size="sm" variant="ghost" onClick={() => setHiddenModelGroups([])}>
                      Show all
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setHiddenModelGroups(groups.map((group) => group.key))}>
                      Hide all
                    </Button>
                  </div>
                </div>
                <div className={styles.modelToggleGrid}>
                  {groups.map((group) => {
                    const isVisible = !hiddenModelGroups.includes(group.key);

                    return (
                      <label
                        key={group.key}
                        className={isVisible ? styles.modelToggle : styles.modelToggleOff}
                        title={group.sourceModels.join("\n")}
                      >
                        <input
                          type="checkbox"
                          checked={isVisible}
                          onChange={() => toggleModelGroup(group.key)}
                          aria-label={`Toggle ${group.label}`}
                        />
                        <span
                          className={styles.modelSwatch}
                          style={{ "--series-color": seriesColor(group.key, group.colorIndex) } as CSSProperties}
                          aria-hidden
                        />
                        <span className={styles.modelToggleText}>{group.label}</span>
                        {group.sourceModels.length > 1 ? (
                          <span className={styles.modelToggleCount}>{group.sourceModels.length}</span>
                        ) : null}
                      </label>
                    );
                  })}
                </div>
              </div>

              {visibleModelCount === 0 ? (
                <div className={styles.empty}>Choose at least one model to draw the trend.</div>
              ) : (
                <div className={styles.chart}>
                  <ResponsiveContainer>
                    {chartType === "stacked" ? (
                      <AreaChart data={chartPoints}>
                        <CartesianGrid stroke="#e6ebf2" vertical={false} />
                        <XAxis
                          dataKey="day"
                          tickLine={false}
                          axisLine={false}
                          fontSize={12}
                          minTickGap={granularity === "hour" ? 24 : 16}
                          tickFormatter={(value) => trendTickLabel(String(value), granularity)}
                        />
                        <YAxis tickFormatter={compactNumber} tickLine={false} axisLine={false} fontSize={12} />
                        <Tooltip
                          labelFormatter={(value) => trendTickLabel(String(value), granularity)}
                          formatter={(value, name) => [compactNumber(Number(value)), String(name)]}
                        />
                        <Legend />
                        {visibleGroups.map((group) => {
                          const color = seriesColor(group.key, group.colorIndex);
                          return (
                            <Area
                              key={group.key}
                              dataKey={group.key}
                              name={group.label}
                              type="monotone"
                              stackId="models"
                              stroke={color}
                              fill={color}
                              fillOpacity={0.65}
                              strokeWidth={1}
                              dot={false}
                            />
                          );
                        })}
                      </AreaChart>
                    ) : (
                      <LineChart data={chartPoints}>
                        <CartesianGrid stroke="#e6ebf2" vertical={false} />
                        <XAxis
                          dataKey="day"
                          tickLine={false}
                          axisLine={false}
                          fontSize={12}
                          minTickGap={granularity === "hour" ? 24 : 16}
                          tickFormatter={(value) => trendTickLabel(String(value), granularity)}
                        />
                        <YAxis tickFormatter={compactNumber} tickLine={false} axisLine={false} fontSize={12} />
                        <Tooltip
                          labelFormatter={(value) => trendTickLabel(String(value), granularity)}
                          formatter={(value, name) => [compactNumber(Number(value)), String(name)]}
                        />
                        <Legend />
                        {visibleGroups.map((group) => (
                          <Line
                            key={group.key}
                            dataKey={group.key}
                            name={group.label}
                            type="monotone"
                            stroke={seriesColor(group.key, group.colorIndex)}
                            strokeWidth={2}
                            dot={false}
                          />
                        ))}
                      </LineChart>
                    )}
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
