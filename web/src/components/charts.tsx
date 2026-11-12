"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const AXIS = { fontSize: 11, fill: "#64748b" };
export const SERIES_COLORS = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#f43f5e", "#8b5cf6"];

function shortTs(ts: string) {
  const d = new Date(/Z$/.test(ts) ? ts : ts + "Z");
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:00`;
}

export function TimeSeries({ series, height = 220, threshold, thresholdLabel, unit, markers }: { series: { name: string; points: [string, number][]; color?: string }[]; height?: number; threshold?: number | null; thresholdLabel?: string; unit?: string; markers?: { ts: string; label: string }[] }) {
  const map = new Map<string, any>();
  series.forEach((s, i) =>
    s.points.forEach(([t, v]) => {
      const row = map.get(t) || { t };
      row[`s${i}`] = v;
      map.set(t, row);
    }),
  );
  const data = Array.from(map.values()).sort((a, b) => (a.t < b.t ? -1 : 1));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
        <CartesianGrid stroke="#eef2f7" vertical={false} />
        <XAxis dataKey="t" tickFormatter={shortTs} tick={AXIS} minTickGap={48} />
        <YAxis tick={AXIS} width={44} />
        <Tooltip labelFormatter={(l) => shortTs(String(l))} formatter={(v: any, n: any) => [`${Number(v).toFixed(2)}${unit ? " " + unit : ""}`, series[Number(String(n).slice(1))]?.name || n]} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
        {series.length > 1 && <Legend formatter={(v) => series[Number(String(v).slice(1))]?.name} wrapperStyle={{ fontSize: 11 }} />}
        {threshold !== null && threshold !== undefined && <ReferenceLine y={threshold} stroke="#f43f5e" strokeDasharray="4 4" label={{ value: thresholdLabel || `阈值 ${threshold}`, position: "insideTopRight", fontSize: 10, fill: "#f43f5e" }} />}
        {(markers || []).map((m) => (
          <ReferenceLine key={m.ts + m.label} x={m.ts} stroke="#f59e0b" strokeDasharray="3 3" label={{ value: m.label, position: "insideTopLeft", fontSize: 10, fill: "#b45309" }} />
        ))}
        {series.map((s, i) => (
          <Line key={i} dataKey={`s${i}`} type="monotone" dot={false} stroke={s.color || SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={1.8} isAnimationActive={false} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function AreaTrend({ data, dataKey, xKey, height = 200, color = "#6366f1", domain }: { data: any[]; dataKey: string; xKey: string; height?: number; color?: string; domain?: [number, number] }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
        <defs>
          <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#eef2f7" vertical={false} />
        <XAxis dataKey={xKey} tick={AXIS} minTickGap={30} />
        <YAxis tick={AXIS} domain={domain} width={40} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
        <Area type="monotone" dataKey={dataKey} stroke={color} fill={`url(#g-${dataKey})`} strokeWidth={2} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function Bars({ data, xKey, bars, height = 220, horizontal, colorByIndex }: { data: any[]; xKey: string; bars: { key: string; name: string; color?: string }[]; height?: number; horizontal?: boolean; colorByIndex?: string[] }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ top: 8, right: 12, bottom: 0, left: horizontal ? 40 : -8 }}>
        <CartesianGrid stroke="#eef2f7" vertical={!!horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tick={AXIS} />
            <YAxis type="category" dataKey={xKey} tick={AXIS} width={110} />
          </>
        ) : (
          <>
            <XAxis dataKey={xKey} tick={AXIS} interval={0} />
            <YAxis tick={AXIS} width={44} />
          </>
        )}
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
        {bars.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
        {bars.map((b, i) => (
          <Bar key={b.key} dataKey={b.key} name={b.name} fill={b.color || SERIES_COLORS[i]} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false}>
            {colorByIndex && data.map((_, j) => <Cell key={j} fill={colorByIndex[j % colorByIndex.length]} />)}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function RadarScore({ data, height = 240 }: { data: { name: string; score: number }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke="#e2e8f0" />
        <PolarAngleAxis dataKey="name" tick={{ fontSize: 11, fill: "#475569" }} />
        <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "#94a3b8" }} />
        <Radar dataKey="score" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} isAnimationActive={false} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
      </RadarChart>
    </ResponsiveContainer>
  );
}
