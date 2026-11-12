"use client";

import Link from "next/link";
import { AlertTriangle, Cloud, Inbox, Loader2, Lock, Server, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AGENTS, ZONE_LABELS } from "@/lib/format";

export function PageHeader({ title, description, actions, crumbs, children }: { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; crumbs?: { label: string; href?: string }[]; children?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3">
      {crumbs && crumbs.length > 0 && (
        <nav className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-border">/</span>}
              {c.href ? (
                <Link href={c.href} className="hover:text-foreground">
                  {c.label}
                </Link>
              ) : (
                <span className="text-foreground/80">{c.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground md:text-2xl">{title}</h1>
          {description && <div className="mt-1 text-sm text-muted-foreground">{description}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function StatCard({ label, value, hint, icon: Icon, tone = "default", href }: { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ComponentType<{ className?: string }>; tone?: "default" | "primary" | "warning" | "danger" | "success"; href?: string }) {
  const toneCls = { default: "bg-muted text-foreground/70", primary: "bg-primary/10 text-primary", warning: "bg-amber-500/10 text-amber-600", danger: "bg-rose-500/10 text-rose-600", success: "bg-emerald-500/10 text-emerald-600" }[tone];
  const body = (
    <Card className="h-full transition-shadow hover:shadow-md" size="sm">
      <CardContent className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular">{value}</div>
          {hint && <div className="mt-1 truncate text-xs text-muted-foreground">{hint}</div>}
        </div>
        {Icon && (
          <div className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", toneCls)}>
            <Icon className="size-4.5" />
          </div>
        )}
      </CardContent>
    </Card>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

export function Section({ title, description, actions, children, className, contentClassName }: { title?: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; contentClassName?: string }) {
  return (
    <Card className={className}>
      {(title || actions) && (
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <CardTitle>{title}</CardTitle>}
            {description && <CardDescription className="mt-1">{description}</CardDescription>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </CardHeader>
      )}
      <CardContent className={contentClassName}>{children}</CardContent>
    </Card>
  );
}

export function EmptyState({ title = "暂无数据", description, action, icon: Icon = Inbox }: { title?: string; description?: React.ReactNode; action?: React.ReactNode; icon?: React.ComponentType<{ className?: string }> }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <div className="text-sm font-medium">{title}</div>
      {description && <div className="max-w-md text-xs text-muted-foreground">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function LoadingBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-3", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

export function PageLoading() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-72 w-full" />
    </div>
  );
}

export function ErrorBlock({ error, retry }: { error: any; retry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <AlertTriangle className="mt-0.5 size-4 text-destructive" />
      <div className="flex-1">
        <div className="font-medium text-destructive">{error?.category || "加载失败"}</div>
        <div className="mt-0.5 text-muted-foreground">{error?.message || String(error)}</div>
        {error?.suggestion && <div className="mt-1 text-xs text-muted-foreground">建议：{error.suggestion}</div>}
      </div>
      {retry && (
        <button onClick={retry} className="text-xs font-medium text-primary hover:underline">
          重试
        </button>
      )}
    </div>
  );
}

export function KV({ items, cols = 2 }: { items: { label: string; value: React.ReactNode; full?: boolean }[]; cols?: 1 | 2 | 3 | 4 }) {
  const grid = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[cols];
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3", grid)}>
      {items.map((it, i) => (
        <div key={i} className={cn("min-w-0", it.full && "sm:col-span-full")}>
          <dt className="text-xs text-muted-foreground">{it.label}</dt>
          <dd className="mt-0.5 text-sm break-words text-foreground">{it.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

type Tone = "gray" | "blue" | "green" | "amber" | "red" | "violet" | "teal" | "orange" | "sky";
const TONES: Record<Tone, string> = {
  gray: "bg-muted text-muted-foreground border-border",
  blue: "bg-blue-500/10 text-blue-700 border-blue-500/20",
  green: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
  amber: "bg-amber-500/12 text-amber-700 border-amber-500/25",
  red: "bg-rose-500/10 text-rose-700 border-rose-500/20",
  violet: "bg-violet-500/10 text-violet-700 border-violet-500/20",
  teal: "bg-teal-500/10 text-teal-700 border-teal-500/20",
  orange: "bg-orange-500/12 text-orange-700 border-orange-500/25",
  sky: "bg-sky-500/10 text-sky-700 border-sky-500/20",
};

export function Pill({ tone = "gray", children, className, dot }: { tone?: Tone; children: React.ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cn("inline-flex h-5 shrink-0 items-center gap-1 rounded-full border px-2 text-[11px] font-medium whitespace-nowrap", TONES[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

const RUN_TONE: Record<string, Tone> = { queued: "gray", planning: "sky", running: "blue", waiting_human: "amber", paused: "gray", completed: "green", failed: "red", cancelled: "gray" };
const RUN_LABEL: Record<string, string> = { queued: "排队中", planning: "规划中", running: "执行中", waiting_human: "等待人工", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" };

export function RunStatus({ status, label }: { status: string; label?: string }) {
  const active = status === "running" || status === "planning";
  return (
    <Pill tone={RUN_TONE[status] || "gray"}>
      {active ? <Loader2 className="size-3 animate-spin" /> : <span className={cn("size-1.5 rounded-full bg-current", status === "waiting_human" && "pulse-dot")} />}
      {label || RUN_LABEL[status] || status}
    </Pill>
  );
}

export function ZoneBadge({ zone, className }: { zone: string; className?: string }) {
  const cfg: Record<string, { tone: Tone; icon: React.ComponentType<{ className?: string }> }> = { cloud: { tone: "sky", icon: Cloud }, private: { tone: "teal", icon: Lock }, platform: { tone: "gray", icon: Server }, follow: { tone: "violet", icon: ShieldCheck } };
  const c = cfg[zone] || cfg.platform;
  const Icon = c.icon;
  return (
    <Pill tone={c.tone} className={className}>
      <Icon className="size-3" />
      {ZONE_LABELS[zone] || zone}
    </Pill>
  );
}

export function DataLevel({ level }: { level: number }) {
  const tone: Tone[] = ["gray", "blue", "amber", "red"];
  const label = ["L0 公开", "L1 内部", "L2 敏感", "L3 高敏"];
  return <Pill tone={tone[level] || "gray"}>{label[level] || `L${level}`}</Pill>;
}

const ALERT_TONE: Record<string, Tone> = { red: "red", orange: "orange", yellow: "amber", blue: "blue" };
const ALERT_LABEL: Record<string, string> = { red: "红色", orange: "橙色", yellow: "黄色", blue: "蓝色" };

export function AlertLevel({ level }: { level: string }) {
  return (
    <Pill tone={ALERT_TONE[level] || "gray"} dot>
      {ALERT_LABEL[level] || level}
    </Pill>
  );
}

export function RiskLevel({ level }: { level?: string | null }) {
  const tone: Record<string, Tone> = { 低: "green", 中: "amber", 高: "orange", 极高: "red" };
  if (!level) return <span className="text-muted-foreground">—</span>;
  return <Pill tone={tone[level] || "gray"}>{level}</Pill>;
}

export function AgentBadge({ agentId, label }: { agentId: string; label?: string }) {
  const a = AGENTS[agentId];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium">
      <span className={cn("size-2 rounded-full", a?.color || "bg-gray-400")} />
      {label || a?.name || agentId}
    </span>
  );
}

export function StartMode({ mode, degrade }: { mode?: string; degrade?: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Pill tone={mode === "warm" ? "green" : "sky"}>{mode === "warm" ? "热启动" : "冷启动"}</Pill>
      {degrade && degrade !== "D0" && <Pill tone={degrade >= "D3" ? "orange" : "amber"}>降级 {degrade}</Pill>}
    </span>
  );
}

export function ScoreBadge({ score, passed }: { score?: number | null; passed?: boolean | null }) {
  if (score === null || score === undefined) return <span className="text-xs text-muted-foreground">未评估</span>;
  return (
    <Pill tone={passed ? "green" : "amber"}>
      {passed ? "达标" : "未达标"} {score.toFixed(0)}
    </Pill>
  );
}

export function Bar({ value, max = 1, tone = "primary", className }: { value: number; max?: number; tone?: "primary" | "amber" | "red" | "green" | "sky"; className?: string }) {
  const pctv = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const color = { primary: "bg-primary", amber: "bg-amber-500", red: "bg-rose-500", green: "bg-emerald-500", sky: "bg-sky-500" }[tone];
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}>
      <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pctv}%` }} />
    </div>
  );
}

export function Mono({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-xs text-muted-foreground">{children}</span>;
}
