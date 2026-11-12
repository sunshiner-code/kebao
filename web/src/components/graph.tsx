"use client";

import { useMemo, useState } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { cn } from "@/lib/utils";

export type GNode = { id: string; label: string; type?: string; color?: string; size?: number; highlight?: boolean };
export type GEdge = { source: string; target: string; label?: string; weight?: number; dashed?: boolean };

const PALETTE = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#f43f5e", "#8b5cf6", "#22c55e", "#64748b", "#ec4899", "#84cc16"];

type SimNode = GNode & SimulationNodeDatum;

export function ForceGraph({ nodes, edges, height = 420, typeColors, onSelect }: { nodes: GNode[]; edges: GEdge[]; height?: number; typeColors?: Record<string, string>; onSelect?: (n: GNode | null) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const width = 900;
  const types = useMemo(() => Array.from(new Set(nodes.map((n) => n.type || "节点"))), [nodes]);
  const colorOf = (t?: string) => typeColors?.[t || ""] || PALETTE[types.indexOf(t || "节点") % PALETTE.length];
  const layout = useMemo(() => {
    const sim: SimNode[] = nodes.map((n) => ({ ...n }));
    const ids = new Set(sim.map((n) => n.id));
    const links = edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e }));
    const s = forceSimulation(sim)
      .force("link", forceLink(links as any).id((d: any) => d.id).distance(70).strength(0.5))
      .force("charge", forceManyBody().strength(-230))
      .force("center", forceCenter(width / 2, height / 2))
      .force("x", forceX(width / 2).strength(0.05))
      .force("y", forceY(height / 2).strength(0.08))
      .force("collide", forceCollide(26))
      .stop();
    for (let i = 0; i < 320; i++) s.tick();
    const pos = new Map(sim.map((n) => [n.id, { x: Math.max(30, Math.min(width - 30, n.x || 0)), y: Math.max(24, Math.min(height - 24, n.y || 0)) }]));
    return { pos, links: links as unknown as GEdge[] };
  }, [nodes, edges, height]);
  const neighbors = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!m.has(e.source)) m.set(e.source, new Set());
      if (!m.has(e.target)) m.set(e.target, new Set());
      m.get(e.source)!.add(e.target);
      m.get(e.target)!.add(e.source);
    }
    return m;
  }, [edges]);
  const isDim = (id: string) => hover !== null && hover !== id && !neighbors.get(hover)?.has(id);
  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full rounded-lg bg-[radial-gradient(circle_at_center,oklch(0.98_0.01_255),oklch(1_0_0))]">
        {edges.map((e, i) => {
          const a = layout.pos.get(typeof e.source === "string" ? e.source : (e.source as any).id);
          const b = layout.pos.get(typeof e.target === "string" ? e.target : (e.target as any).id);
          if (!a || !b) return null;
          const dim = hover && !(e.source === hover || e.target === hover);
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={dim ? "#e2e8f0" : "#cbd5e1"} strokeWidth={Math.max(1, (e.weight || 1) * 1.2)} strokeDasharray={e.dashed ? "4 3" : undefined} />;
        })}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id);
          if (!p) return null;
          const r = n.size || 8;
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" opacity={isDim(n.id) ? 0.25 : 1} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onClick={() => onSelect?.(n)}>
              <circle r={r + (n.highlight ? 3 : 0)} fill={n.color || colorOf(n.type)} stroke={n.highlight ? "#0f172a" : "white"} strokeWidth={n.highlight ? 2.5 : 1.5} />
              <text y={r + 11} textAnchor="middle" className="fill-slate-700 text-[10px]" style={{ fontSize: 10 }}>
                {n.label.length > 12 ? n.label.slice(0, 12) + "…" : n.label}
              </text>
              <title>{`${n.type ? n.type + "：" : ""}${n.label}`}</title>
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {types.map((t) => (
          <span key={t} className="flex items-center gap-1">
            <span className="size-2.5 rounded-full" style={{ background: colorOf(t) }} />
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

export function DagGraph({ nodes, edges, height = 300, kindColors }: { nodes: { id: string; label: string; kind?: string; sub?: string }[]; edges: { from: string; to: string; strength: number; source?: string; key?: boolean }[]; height?: number; kindColors?: Record<string, string> }) {
  const width = 900;
  const layers = useMemo(() => {
    const indeg = new Map(nodes.map((n) => [n.id, 0]));
    edges.forEach((e) => indeg.set(e.to, (indeg.get(e.to) || 0) + 1));
    const level = new Map<string, number>();
    const queue = nodes.filter((n) => !indeg.get(n.id)).map((n) => n.id);
    queue.forEach((id) => level.set(id, 0));
    while (queue.length) {
      const id = queue.shift()!;
      for (const e of edges.filter((x) => x.from === id)) {
        level.set(e.to, Math.max(level.get(e.to) || 0, (level.get(id) || 0) + 1));
        indeg.set(e.to, (indeg.get(e.to) || 0) - 1);
        if (!indeg.get(e.to)) queue.push(e.to);
      }
    }
    const maxL = Math.max(0, ...Array.from(level.values()));
    const cols: string[][] = Array.from({ length: maxL + 1 }, () => []);
    nodes.forEach((n) => cols[level.get(n.id) ?? 0].push(n.id));
    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((col, ci) => col.forEach((id, ri) => pos.set(id, { x: 80 + (ci * (width - 160)) / Math.max(1, maxL), y: ((ri + 1) * height) / (col.length + 1) })));
    return pos;
  }, [nodes, edges, height]);
  const colors = kindColors || { behavior: "#8b5cf6", state: "#0ea5e9", event: "#f59e0b", outcome: "#f43f5e" };
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full rounded-lg bg-muted/30">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const a = layers.get(e.from);
        const b = layers.get(e.to);
        if (!a || !b) return null;
        const mx = (a.x + b.x) / 2;
        const path = `M ${a.x + 52} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - 54} ${b.y}`;
        return (
          <g key={i}>
            <path d={path} fill="none" stroke={e.source === "both" ? "#14b8a6" : "#94a3b8"} strokeWidth={1 + e.strength * 4} strokeOpacity={0.75} markerEnd="url(#arrow)" strokeDasharray={e.key ? undefined : "5 4"} />
            <text x={mx} y={(a.y + b.y) / 2 - 6} textAnchor="middle" style={{ fontSize: 10 }} className="fill-slate-600">
              {e.strength.toFixed(2)}
            </text>
          </g>
        );
      })}
      {nodes.map((n) => {
        const p = layers.get(n.id);
        if (!p) return null;
        return (
          <g key={n.id} transform={`translate(${p.x - 52},${p.y - 18})`}>
            <rect width={104} height={36} rx={9} fill="white" stroke={colors[n.kind || ""] || "#64748b"} strokeWidth={2} />
            <text x={52} y={n.sub ? 15 : 22} textAnchor="middle" style={{ fontSize: 11, fontWeight: 600 }} className="fill-slate-800">
              {n.label}
            </text>
            {n.sub && (
              <text x={52} y={28} textAnchor="middle" style={{ fontSize: 9 }} className="fill-slate-500">
                {n.sub}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function Heatmap({ grid, onCell, selected }: { grid: string[][][]; onCell?: (ids: string[], l: number, s: number) => void; selected?: string | null }) {
  // grid[severity-1][likelihood-1] = 风险编号列表
  const color = (l: number, s: number) => {
    const v = l * s;
    return v >= 20 ? "bg-rose-500/80 text-white" : v >= 12 ? "bg-orange-400/80 text-white" : v >= 6 ? "bg-amber-300/80" : "bg-emerald-200/70";
  };
  return (
    <div className="inline-grid grid-cols-[auto_repeat(5,minmax(44px,1fr))] gap-1 text-[11px]">
      <div />
      {[1, 2, 3, 4, 5].map((l) => (
        <div key={l} className="text-center text-muted-foreground">
          可能性 {l}
        </div>
      ))}
      {[5, 4, 3, 2, 1].map((s) => (
        <div key={s} className="contents">
          <div className="pr-1 text-right leading-[44px] text-muted-foreground">严重性 {s}</div>
          {[1, 2, 3, 4, 5].map((l) => {
            const ids = grid?.[s - 1]?.[l - 1] || [];
            return (
              <button key={l} onClick={() => onCell?.(ids, l, s)} className={cn("flex h-11 items-center justify-center rounded-md font-semibold transition-transform hover:scale-105", color(l, s), ids.length === 0 && "opacity-40", ids.some((i) => i === selected) && "ring-2 ring-foreground")}>
                {ids.length || ""}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}
