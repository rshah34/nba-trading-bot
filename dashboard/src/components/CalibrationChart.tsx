"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CalibrationBin } from "@/lib/types";

interface Point {
  x: number; // avg predicted prob
  y: number; // actual win rate
  bin: string;
  n: number;
}

const pct = (v: number) => `${Math.round(v * 100)}%`;

function CalibrationTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-surface px-3 py-2 text-xs shadow-sm">
      <div className="font-medium text-primary">Predicted {p.bin}</div>
      <div className="tnum mt-1 text-secondary">Model said: {pct(p.x)}</div>
      <div className="tnum text-secondary">Actually won: {pct(p.y)}</div>
      <div className="tnum mt-1 text-muted">{p.n} games</div>
    </div>
  );
}

export function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const data: Point[] = bins.map((b) => ({
    x: b.avg_predicted,
    y: b.actual_win_rate,
    bin: b.bin,
    n: b.n,
  }));

  return (
    <div>
      <div className="mb-3 flex items-center gap-4 text-xs text-secondary">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-series-1" aria-hidden />
          Model
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 bg-baseline" aria-hidden />
          Perfect calibration
        </span>
      </div>
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
            <CartesianGrid stroke="var(--gridline)" strokeDasharray="0" vertical={false} />
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke="var(--baseline)"
              strokeDasharray="4 4"
              ifOverflow="extendDomain"
            />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={pct}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              stroke="var(--baseline)"
              label={{
                value: "Predicted win probability",
                position: "bottom",
                fill: "var(--text-secondary)",
                fontSize: 12,
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={pct}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              stroke="var(--baseline)"
              label={{
                value: "Actual win rate",
                angle: -90,
                position: "insideLeft",
                fill: "var(--text-secondary)",
                fontSize: 12,
                style: { textAnchor: "middle" },
              }}
            />
            <Tooltip content={<CalibrationTooltip />} />
            <Line
              type="monotone"
              dataKey="y"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={{ r: 5, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 2 }}
              activeDot={{ r: 7 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
