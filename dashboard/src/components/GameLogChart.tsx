"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { shortDate } from "@/lib/format";

export interface LogPoint {
  key: string;
  date: string;
  opp: string;
  home: boolean | undefined;
  value: number;
  minutes: number;
  result?: string;
}

function side(value: number, line: number) {
  return value > line ? "Over" : value < line ? "Under" : "Push";
}

function LogTooltip({
  active,
  payload,
  line,
  unit,
}: {
  active?: boolean;
  payload?: { payload: LogPoint }[];
  line: number;
  unit: string;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-surface px-3 py-2 text-xs shadow-sm">
      <div className="text-sm font-semibold text-primary">
        {p.value} {unit}
        <span className="ml-2 font-normal text-secondary">{side(p.value, line)} {line}</span>
      </div>
      <div className="mt-1 text-secondary">
        {shortDate(p.date)} {p.home === false ? "at" : "vs"} {p.opp}
        {p.result ? ` · ${p.result}` : ""}
      </div>
      <div className="tnum text-muted">{p.minutes.toFixed(0)} min</div>
    </div>
  );
}

/**
 * One bar per game, oldest to newest, against a betting line. Games over the line
 * are highlighted and the rest recede, so the hit rate reads at a glance.
 */
export function GameLogChart({ points, line, unit, lineSource }: { points: LogPoint[]; line: number; unit: string; lineSource: string }) {
  // Opponent names fit under a recent stretch of games; across a whole season they
  // would only be every few games, so they're left to the tooltip.
  const labelled = points.length <= 20;
  const peak = Math.max(line, ...points.map((p) => p.value), 1) * 1.08;
  const stepSize = peak <= 6 ? 1 : peak <= 12 ? 2 : peak <= 30 ? 5 : 10;
  const top = Math.ceil(peak / stepSize) * stepSize;
  const ticks = Array.from({ length: top / stepSize + 1 }, (_, i) => i * stepSize);
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-secondary">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-series-1" aria-hidden />
          Over {line}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-series-rest" aria-hidden />
          Under {line}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-primary" aria-hidden />
          {lineSource}
        </span>
      </div>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -18 }} barCategoryGap="14%">
            <CartesianGrid stroke="var(--gridline)" vertical={false} />
            <XAxis
              dataKey="key"
              tickFormatter={(key: string) => points.find((p) => p.key === key)?.opp ?? ""}
              tick={labelled ? { fill: "var(--text-muted)", fontSize: 11 } : false}
              height={labelled ? 30 : 4}
              stroke="var(--baseline)"
              tickLine={false}
              interval={0}
            />
            <YAxis
              domain={[0, top]}
              ticks={ticks}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              stroke="var(--baseline)"
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--gridline)", opacity: 0.5 }}
              content={<LogTooltip line={line} unit={unit} />}
            />
            <Bar dataKey="value" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {points.map((p) => (
                <Cell key={p.key} fill={p.value > line ? "var(--series-1)" : "var(--series-rest)"} />
              ))}
            </Bar>
            <ReferenceLine y={line} stroke="var(--text-primary)" strokeWidth={1.5} ifOverflow="extendDomain" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
