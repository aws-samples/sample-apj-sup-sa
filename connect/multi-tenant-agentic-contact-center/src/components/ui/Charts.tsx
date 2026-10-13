import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TimePoint } from "@/data/types";
import { formatCurrency, formatNumber } from "@/lib/format";

export function Sparkline({
  data,
  color = "#635bff",
}: {
  data: TimePoint[];
  color?: string;
}) {
  const id = `spark-${color.replace("#", "")}`;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          fill={`url(#${id})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
  money,
}: {
  active?: boolean;
  payload?: Array<{ value: number; name?: string; color?: string }>;
  label?: string;
  money?: boolean;
}) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-elevated">
      <div className="mb-1 font-semibold text-ink-900">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-ink-600">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: p.color }}
          />
          {money ? formatCurrency(p.value) : formatNumber(p.value)}
        </div>
      ))}
    </div>
  );
}

export function AreaTrend({
  data,
  color = "#635bff",
  money = true,
  height = 260,
}: {
  data: TimePoint[];
  color?: string;
  money?: boolean;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 10, right: 8, left: -8, bottom: 0 }}>
        <defs>
          <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "#9aa4b2" }}
          interval="preserveStartEnd"
          minTickGap={28}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={54}
          tick={{ fontSize: 11, fill: "#9aa4b2" }}
          tickFormatter={(v: number) =>
            money ? formatCurrency(v, "USD", { compact: true }) : formatNumber(v, true)
          }
        />
        <Tooltip content={<ChartTooltip money={money} />} />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2.4}
          fill="url(#areaFill)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function BarTrend({
  data,
  color = "#635bff",
  money = false,
  height = 260,
}: {
  data: TimePoint[];
  color?: string;
  money?: boolean;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 10, right: 8, left: -8, bottom: 0 }}>
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "#9aa4b2" }}
          interval="preserveStartEnd"
          minTickGap={28}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={54}
          tick={{ fontSize: 11, fill: "#9aa4b2" }}
          tickFormatter={(v: number) =>
            money ? formatCurrency(v, "USD", { compact: true }) : formatNumber(v, true)
          }
        />
        <Tooltip
          cursor={{ fill: "rgba(99,91,255,0.06)" }}
          content={<ChartTooltip money={money} />}
        />
        <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DonutChart({
  data,
  height = 220,
}: {
  data: Array<{ name: string; value: number; color: string }>;
  height?: number;
}) {
  return (
    <div className="flex items-center gap-6">
      <div style={{ width: height, height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius="62%"
              outerRadius="100%"
              paddingAngle={2}
              stroke="none"
              isAnimationActive={false}
            >
              {data.map((d) => (
                <Cell key={d.name} fill={d.color} />
              ))}
            </Pie>
            <Tooltip
              content={({ active, payload }) =>
                active && payload && payload.length ? (
                  <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-elevated">
                    <span className="font-semibold text-ink-900">
                      {payload[0].name}
                    </span>
                    <span className="ml-2 text-ink-600">
                      {payload[0].value}%
                    </span>
                  </div>
                ) : null
              }
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex-1 space-y-2.5">
        {data.map((d) => (
          <li key={d.name} className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2 text-ink-600">
              <span
                className="h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: d.color }}
              />
              {d.name}
            </span>
            <span className="font-semibold text-ink-900">{d.value}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
