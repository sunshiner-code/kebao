"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  BookOpen,
  Brain,
  CheckCircle2,
  Circle,
  CircleDot,
  Cloud,
  Cpu,
  FileOutput,
  GitBranch,
  Hand,
  Layers,
  Loader2,
  Lock,
  Pause,
  Play,
  RefreshCcw,
  ShieldAlert,
  ShieldCheck,
  Square,
  Timer,
  Wrench,
  XCircle,
} from "lucide-react";
import { api, getToken, useApi } from "@/lib/api";
import { dateTime, num, pct, runDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AgentBadge, Bar, DataLevel, EmptyState, ErrorBlock, KV, PageHeader, PageLoading, Pill, RunStatus, ScoreBadge, Section, StartMode, ZoneBadge } from "@/components/common";
import { HitlCardView } from "@/components/hitl";

type Ev = { seq: number; type: string; payload: any; ts: string };

function useRunEvents(runId: number, active: boolean) {
  const [events, setEvents] = useState<Ev[]>([]);
  const last = useRef(0);
  useEffect(() => {
    let closed = false;
    let es: EventSource | null = null;
    let timer: any = null;
    const push = (ev: Ev) => {
      if (ev.seq <= last.current) return;
      last.current = ev.seq;
      setEvents((prev) => [...prev, ev]);
    };
    const poll = async () => {
      try {
        const rows = await api<Ev[]>(`/runs/${runId}/events?after=${last.current}`);
        rows.forEach(push);
      } catch {
        /* 网络抖动时下一轮重试 */
      }
      if (!closed) timer = setTimeout(poll, 2000);
    };
    api<Ev[]>(`/runs/${runId}/events?after=0`).then((rows) => {
      rows.forEach(push);
      if (closed) return;
      if (!active) return;
      try {
        es = new EventSource(`/api/runs/${runId}/stream?after=${last.current}&token=${encodeURIComponent(getToken() || "")}`);
        es.onmessage = (m) => push(JSON.parse(m.data));
        es.onerror = () => {
          es?.close();
          es = null;
          if (!closed) poll();
        };
      } catch {
        poll();
      }
    });
    return () => {
      closed = true;
      es?.close();
      clearTimeout(timer);
    };
  }, [runId, active]);
  return events;
}

const ICONS: Record<string, { icon: React.ComponentType<{ className?: string }>; tone: string }> = {
  "run.created": { icon: CircleDot, tone: "text-muted-foreground" },
  "run.started": { icon: Play, tone: "text-primary" },
  "skill.loaded": { icon: BookOpen, tone: "text-violet-600" },
  "memory.injected": { icon: Brain, tone: "text-violet-600" },
  "plan.updated": { icon: Layers, tone: "text-indigo-600" },
  "tool.finished": { icon: Wrench, tone: "text-slate-600" },
  "model.routed": { icon: Cpu, tone: "text-sky-600" },
  "egress.done": { icon: ShieldCheck, tone: "text-teal-600" },
  "egress.blocked": { icon: ShieldAlert, tone: "text-rose-600" },
  "reasoning.summary": { icon: Brain, tone: "text-slate-500" },
  "evaluation.finished": { icon: CheckCircle2, tone: "text-emerald-600" },
  "reflection.started": { icon: RefreshCcw, tone: "text-amber-600" },
  "hitl.requested": { icon: Hand, tone: "text-amber-600" },
  "hitl.resolved": { icon: Hand, tone: "text-emerald-600" },
  "subtask.spawned": { icon: GitBranch, tone: "text-indigo-600" },
  "subtask.progress": { icon: GitBranch, tone: "text-indigo-400" },
  "background.started": { icon: Timer, tone: "text-sky-600" },
  "background.finished": { icon: Timer, tone: "text-emerald-600" },
  "context.compacted": { icon: Layers, tone: "text-slate-400" },
  "degrade.changed": { icon: AlertTriangle, tone: "text-amber-600" },
  "todo.nag": { icon: AlertTriangle, tone: "text-amber-500" },
  "run.finished": { icon: FileOutput, tone: "text-emerald-600" },
};

const HIDDEN = new Set(["progress", "run.queued", "step.started", "step.finished", "status.changed", "tool.started", "background.progress", "control"]);

function EventRow({ ev }: { ev: Ev }) {
  const p = ev.payload || {};
  const cfg = ICONS[ev.type] || { icon: Circle, tone: "text-muted-foreground" };
  const Icon = cfg.icon;
  let title: React.ReactNode = ev.type;
  let body: React.ReactNode = null;
  let extra: React.ReactNode = null;
  switch (ev.type) {
    case "run.created":
      title = "运行已创建";
      body = (p.reasons || []).slice(0, 3).join("；");
      extra = <StartMode mode={p.start_mode} degrade={p.degrade_level} />;
      break;
    case "run.started":
      title = `${p.agent} 开始运行`;
      body = `模式：${p.mode === "auto" ? "自动" : "半自动"}`;
      break;
    case "skill.loaded":
      title = `加载技能 ${p.skill_id} ${p.title}`;
      body = p.section ? `分区：${p.section}` : `全文 ${p.chars} 字（第二层按需加载）`;
      break;
    case "memory.injected":
      title = "记忆注入";
      body = (p.items || []).map((i: any) => i.title).join("；") || "无相关记忆";
      break;
    case "plan.updated":
      title = `计划已更新（${(p.todos || []).length} 项待办）`;
      body = `进度 ${pct(p.progress)}`;
      break;
    case "tool.finished":
      title = p.title;
      body = p.summary;
      extra = (
        <span className="flex items-center gap-1.5">
          <ZoneBadge zone={p.zone} />
          {p.status !== "ok" && <Pill tone="red">{p.status}</Pill>}
          <span className="text-[11px] text-muted-foreground tabular">{p.duration_ms} ms</span>
        </span>
      );
      break;
    case "model.routed":
      title = `模型路由：${p.task}`;
      body = `${p.model}（规则 ${p.rule_id || "—"}，数据级别 L${p.level}）${p.fallback ? "，兜底链回退" : ""}`;
      extra = (
        <span className="flex items-center gap-1.5">
          <ZoneBadge zone={p.zone} />
          {p.desensitized && <Pill tone="teal">已脱敏出域</Pill>}
        </span>
      );
      break;
    case "egress.done":
      title = `脱敏出域 #${p.egress_id}`;
      body = `${p.task} → ${p.model}；替换占位符 ${p.placeholder_count} 个，命中规则 ${Object.entries(p.rules_hit || {}).map(([k, v]) => `${k}×${v}`).join("、") || "无"}`;
      break;
    case "egress.blocked":
      title = "出域二次扫描拦截";
      body = `${p.model}：${(p.findings || []).map((f: any) => f.kind).join("、")}，已改由私域处理`;
      break;
    case "reasoning.summary":
      title = p.title;
      body = p.summary;
      break;
    case "evaluation.finished":
      title = `${p.kind === "final" ? "最终评估" : "阶段评估"}：${p.target}`;
      body = `综合得分 ${num(p.score, 1)}（达标线 ${p.threshold}）` + ((p.failed || []).length ? `；未通过：${(p.dimensions || []).filter((d: any) => !d.passed).map((d: any) => d.name).join("、")}` : "");
      extra = <ScoreBadge score={p.score} passed={p.passed} />;
      break;
    case "reflection.started":
      title = `反思第 ${p.round} 轮（${p.trigger}）`;
      body = `${p.diagnosis || ""} 修正计划：${(p.plan || []).join("；")}`;
      break;
    case "hitl.requested":
      title = `请求人审：${p.title}`;
      body = p.reason;
      extra = <Pill tone="amber">{p.type_label}</Pill>;
      break;
    case "hitl.resolved":
      title = `人审已处理：${p.label}`;
      body = p.comment;
      break;
    case "subtask.spawned":
      title = `派生 ${(p.children || []).length} 个子智能体（独立上下文）`;
      body = (p.children || []).map((c: any) => c.title).join("；");
      break;
    case "subtask.progress":
      title = `子智能体 #${p.child_run_id}：${p.title || p.event}`;
      body = p.summary;
      extra = p.zone ? <ZoneBadge zone={p.zone} /> : null;
      break;
    case "background.started":
      title = `后台任务启动：${p.name}`;
      break;
    case "background.finished":
      title = `后台任务${p.status === "done" ? "完成" : "失败"}：${p.name}`;
      body = p.error;
      break;
    case "context.compacted":
      title = `上下文压缩（${p.layer === "stage" ? "阶段压缩" : p.layer === "auto" ? "自动压缩" : "微压缩"}）`;
      body = `${num(p.before, 0)} → ${num(p.after, 0)} 字符`;
      break;
    case "degrade.changed":
      title = `降级为 ${p.level}`;
      body = p.note;
      break;
    case "todo.nag":
      title = `待办提醒：${p.todo_id} 已 ${p.rounds} 轮未更新`;
      break;
    case "run.finished":
      title = `运行${p.status === "completed" ? "完成" : p.status === "failed" ? "失败" : "结束"}`;
      body = p.summary;
      extra = p.memory ? <span className="text-[11px] text-muted-foreground">记忆沉淀：企业 {p.memory.entity}、案例 {p.memory.case}、经验 {p.memory.experience}</span> : null;
      break;
  }
  return (
    <div className="relative flex gap-3 pb-3">
      <div className="flex flex-col items-center">
        <div className={cn("flex size-6 items-center justify-center rounded-full border bg-card", cfg.tone)}>
          <Icon className="size-3.5" />
        </div>
        <div className="mt-1 w-px flex-1 bg-border" />
      </div>
      <div className="min-w-0 flex-1 pb-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium">{title}</span>
          {extra}
          <span className="ml-auto text-[10px] text-muted-foreground tabular">#{ev.seq} · {new Date(ev.ts).toLocaleTimeString("zh-CN", { hour12: false })}</span>
        </div>
        {body && <div className="mt-0.5 text-xs break-words whitespace-pre-wrap text-muted-foreground">{body}</div>}
      </div>
    </div>
  );
}

function TodoPanel({ todos }: { todos: any[] }) {
  if (!todos.length) return <div className="text-xs text-muted-foreground">计划生成中…</div>;
  return (
    <ol className="space-y-1">
      {todos.map((t) => {
        const icon = t.status === "completed" ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : t.status === "in_progress" ? <Loader2 className="size-3.5 animate-spin text-primary" /> : t.status === "failed" ? <XCircle className="size-3.5 text-rose-600" /> : t.status === "skipped" ? <Circle className="size-3.5 text-muted-foreground/40" /> : <Circle className="size-3.5 text-muted-foreground" />;
        return (
          <li key={t.id} className={cn("flex gap-2 rounded-md px-1.5 py-1 text-xs", t.status === "in_progress" && "bg-primary/5")}>
            <span className="mt-0.5">{icon}</span>
            <div className="min-w-0 flex-1">
              <div className={cn("leading-snug", t.status === "skipped" && "text-muted-foreground line-through")}>
                {t.content}
                {t.origin === "reflection" && <Pill tone="amber" className="ml-1">反思</Pill>}
                {t.origin === "human" && <Pill tone="violet" className="ml-1">人工</Pill>}
              </div>
              <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                {t.tool && <span className="font-mono">{t.tool}</span>}
                {t.hitl && <span>· 人审 {t.hitl}</span>}
                {t.stage_eval && <span>· 阶段评估</span>}
                {t.condition && <span>· 条件 {t.condition}</span>}
                {t.note && <span className="truncate">· {t.note}</span>}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Evaluations({ evals }: { evals: any[] }) {
  if (!evals.length) return <EmptyState title="尚无评估" description="阶段评估与最终评估完成后显示在这里" />;
  return (
    <div className="space-y-3">
      {evals
        .slice()
        .reverse()
        .map((e) => (
          <div key={e.id} className="rounded-lg border p-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{e.kind === "final" ? "最终评估" : "阶段评估"} · {e.target}</span>
              <span className="ml-auto">
                <ScoreBadge score={e.score} passed={e.passed} />
              </span>
            </div>
            <div className="mt-2 space-y-1.5">
              {e.dimensions.map((d: any) => (
                <div key={d.key} className="flex items-start gap-2 text-xs">
                  {d.passed ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 size-3.5 shrink-0 text-rose-500" />}
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{d.name}</span>
                    <span className="ml-1 text-muted-foreground">
                      [{d.layer_label}] 阈值 {d.threshold} · 实际 {typeof d.value === "number" ? num(d.value, 3) : String(d.value ?? "—")}
                    </span>
                    <div className="text-muted-foreground">{d.detail}</div>
                  </div>
                </div>
              ))}
            </div>
            {e.reviewer_model && <div className="mt-2 text-[11px] text-muted-foreground">评审模型：{e.reviewer_model}</div>}
          </div>
        ))}
    </div>
  );
}

export function RunConsole({ runId }: { runId: number }) {
  const { data: run, error, mutate } = useApi<any>(`/runs/${runId}`, { refreshInterval: (d) => (d && ["completed", "failed", "cancelled"].includes(d.status) ? 0 : 3000) });
  const active = !run || !["completed", "failed", "cancelled"].includes(run.status);
  const events = useRunEvents(runId, active);
  const lastPlan = useMemo(() => [...events].reverse().find((e) => e.type === "plan.updated"), [events]);
  const todos = lastPlan?.payload?.todos || run?.todos || [];
  const shown = events.filter((e) => !HIDDEN.has(e.type));
  const scroller = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const el = scroller.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [shown.length, active]);
  useEffect(() => {
    const t = events[events.length - 1]?.type;
    if (t && ["hitl.requested", "hitl.resolved", "run.finished", "evaluation.finished", "subtask.spawned"].includes(t)) mutate();
  }, [events.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const status = run?.status;
  useEffect(() => {
    if (!status || !["planning", "running"].includes(status)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [status]);

  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!run) return <PageLoading />;

  const control = async (command: string) => {
    try {
      await api(`/runs/${runId}/control`, { method: "POST", json: { command } });
      toast.success("指令已发送");
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const openCards = (run.cards || []).filter((c: any) => c.status === "open");
  const u = run.usage || {};
  const outKey = run.output?.artifact_key;

  return (
    <div>
      <PageHeader
        crumbs={[{ label: "智能体中心" }, { label: "运行记录", href: "/runs" }, { label: `#${run.id}` }]}
        title={run.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <AgentBadge agentId={run.agent_id} label={`${run.agent_name}${run.purpose !== "main" ? ` · ${run.purpose}` : ""}`} />
            <RunStatus status={run.status} />
            <StartMode mode={run.start_mode} degrade={run.degrade_level} />
            <ScoreBadge score={run.eval_score} passed={run.eval_passed} />
            {run.reflection_rounds > 0 && <Pill tone="amber">反思 {run.reflection_rounds} 轮</Pill>}
            {run.parent_run_id && (
              <Link href={`/runs/${run.parent_run_id}`} className="text-xs text-primary hover:underline">
                父运行 #{run.parent_run_id}
              </Link>
            )}
          </span>
        }
        actions={
          <>
            {["running", "planning"].includes(run.status) && (
              <Button size="sm" variant="outline" onClick={() => control("pause")}>
                <Pause className="size-3.5" /> 暂停
              </Button>
            )}
            {run.status === "paused" && (
              <Button size="sm" onClick={() => control("resume")}>
                <Play className="size-3.5" /> 继续
              </Button>
            )}
            {active && (
              <Button size="sm" variant="outline" onClick={() => control(run.mode === "auto" ? "mode:semi" : "mode:auto")}>
                切换为{run.mode === "auto" ? "半自动" : "自动"}
              </Button>
            )}
            {active && (
              <Button size="sm" variant="destructive" onClick={() => confirm("确定取消该运行？已有产物将保存为草稿。") && control("cancel")}>
                <Square className="size-3.5" /> 取消
              </Button>
            )}
            {outKey && (
              <Link href={`/artifacts/${outKey}`}>
                <Button size="sm">
                  <FileOutput className="size-3.5" /> 查看产物
                </Button>
              </Link>
            )}
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
        <div className="col-span-2 rounded-xl border bg-card p-3">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>进度</span>
            <span className="tabular">{pct(run.progress)}</span>
          </div>
          <Bar value={run.progress} className="mt-2" />
          <div className="mt-2 text-[11px] text-muted-foreground">用时 {runDuration(run, now)}</div>
        </div>
        <div className="rounded-xl border bg-card p-3">
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <Cloud className="size-3 text-sky-600" /> 云域调用
          </div>
          <div className="mt-1 text-lg font-semibold tabular">{u.cloud_calls || 0}</div>
          <div className="text-[11px] text-muted-foreground">{num((u.cloud_tokens_in || 0) + (u.cloud_tokens_out || 0), 0)} tokens</div>
        </div>
        <div className="rounded-xl border bg-card p-3">
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <Lock className="size-3 text-teal-600" /> 私域调用
          </div>
          <div className="mt-1 text-lg font-semibold tabular">{u.private_calls || 0}</div>
          <div className="text-[11px] text-muted-foreground">{num((u.private_tokens_in || 0) + (u.private_tokens_out || 0), 0)} tokens</div>
        </div>
        <div className="rounded-xl border bg-card p-3">
          <div className="text-xs text-muted-foreground">工具调用 / 步骤</div>
          <div className="mt-1 text-lg font-semibold tabular">
            {u.tool_calls || 0} / {u.steps || 0}
          </div>
          <div className="text-[11px] text-muted-foreground">脱敏出域 {u.egress || 0} 次</div>
        </div>
        <div className="rounded-xl border bg-card p-3">
          <div className="text-xs text-muted-foreground">技能</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {(run.skills_loaded || []).map((s: string) => (
              <Pill key={s} tone="violet">
                {s}
              </Pill>
            ))}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">版本 {run.agent_version}</div>
        </div>
      </div>

      {openCards.length > 0 && (
        <div className="mb-4 space-y-3">
          {openCards.map((c: any) => (
            <HitlCardView key={c.id} card={c} onResolved={() => mutate()} />
          ))}
        </div>
      )}
      {run.error && (
        <div className="mb-4">
          <ErrorBlock error={{ category: "运行失败", message: run.error.split("\n").slice(-2).join(" ") }} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_380px]">
        <Section title="计划与待办" description="同一时刻只有一项执行中" contentClassName="max-h-[640px] overflow-y-auto">
          <TodoPanel todos={todos} />
          {(run.children || []).length > 0 && (
            <div className="mt-4 border-t pt-3">
              <div className="mb-1.5 text-xs font-medium">子智能体</div>
              {run.children.map((c: any) => (
                <Link key={c.id} href={`/runs/${c.id}`} className="flex items-center justify-between gap-2 rounded-md px-1.5 py-1 text-xs hover:bg-muted">
                  <span className="truncate">{c.title}</span>
                  <RunStatus status={c.status} />
                </Link>
              ))}
            </div>
          )}
          {(run.background || []).length > 0 && (
            <div className="mt-4 border-t pt-3">
              <div className="mb-1.5 text-xs font-medium">后台任务</div>
              {run.background.map((b: any) => (
                <div key={b.id} className="mb-1.5 text-xs">
                  <div className="flex justify-between">
                    <span>{b.name}</span>
                    <span className="text-muted-foreground">{b.status === "done" ? "完成" : b.status}</span>
                  </div>
                  <Bar value={b.progress} tone="sky" className="mt-1" />
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="运行轨迹" description={active ? "实时事件流（断线自动续传）" : `共 ${shown.length} 个事件，可回放`} contentClassName="p-0">
          <div ref={scroller} className="scrollbar-thin max-h-[640px] overflow-y-auto px-4 pt-1">
            {shown.length === 0 ? <div className="py-8 text-center text-xs text-muted-foreground">等待事件…</div> : shown.map((e) => <EventRow key={e.seq} ev={e} />)}
            {active && (
              <div className="flex items-center gap-2 pb-4 pl-1 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> {run.status === "waiting_human" ? "等待人工处理" : "智能体运行中"}
              </div>
            )}
          </div>
        </Section>

        <Section contentClassName="pt-0" title="评估与可观测性">
          <Tabs defaultValue="eval">
            <TabsList className="w-full">
              <TabsTrigger value="eval">评估</TabsTrigger>
              <TabsTrigger value="model">模型</TabsTrigger>
              <TabsTrigger value="egress">出域</TabsTrigger>
              <TabsTrigger value="tools">工具</TabsTrigger>
            </TabsList>
            <TabsContent value="eval" className="mt-3 max-h-[560px] overflow-y-auto">
              <Evaluations evals={run.evaluations || []} />
              {(run.reflections || []).length > 0 && (
                <div className="mt-4 space-y-2">
                  <div className="text-xs font-medium">反思记录</div>
                  {run.reflections.map((r: any) => (
                    <div key={r.round} className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs">
                      <div className="font-medium">
                        第 {r.round} 轮 · {r.trigger} · {(r.categories || []).join("、")}
                      </div>
                      <div className="mt-1 text-muted-foreground">{r.diagnosis}</div>
                      <div className="mt-1">策略：{r.strategy}</div>
                      <div className="mt-1 text-muted-foreground">
                        得分 {r.score_before ?? "—"} → {r.score_after ?? "—"} · {r.outcome === "resolved" ? "已解决" : r.outcome || "进行中"}
                        {r.experience_id && ` · 已沉淀为候选经验 #${r.experience_id}`}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>
            <TabsContent value="model" className="mt-3 max-h-[560px] space-y-1.5 overflow-y-auto">
              {(run.model_calls || []).length === 0 && <EmptyState title="暂无模型调用" />}
              {(run.model_calls || []).map((c: any) => (
                <div key={c.id} className="rounded-md border px-2.5 py-1.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <ZoneBadge zone={c.zone} />
                    <span className="truncate font-medium">{c.task}</span>
                    <span className="ml-auto text-muted-foreground tabular">{c.latency_ms} ms</span>
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span>{c.model_id}</span>
                    <span>· {c.task_type}</span>
                    <DataLevel level={c.data_level} />
                    {c.fallback && <Pill tone="amber">兜底</Pill>}
                    {c.egress_id && <Pill tone="teal">出域 #{c.egress_id}</Pill>}
                    {c.status !== "ok" && <Pill tone="red">{c.status}</Pill>}
                    {c.tokens_in + c.tokens_out > 0 && <span>{num(c.tokens_in + c.tokens_out, 0)} tokens</span>}
                  </div>
                  {c.error && <div className="mt-0.5 line-clamp-2 text-[11px] text-rose-600">{c.error}</div>}
                </div>
              ))}
            </TabsContent>
            <TabsContent value="egress" className="mt-3 max-h-[560px] space-y-2 overflow-y-auto">
              {(run.egress || []).length === 0 && <EmptyState title="本次运行无出域" description="私域数据未离开私域，或只调用了私域模型" />}
              {(run.egress || []).map((e: any) => (
                <div key={e.id} className="rounded-md border p-2.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium">#{e.id} {e.task}</span>
                    <DataLevel level={e.source_level} />
                    <Pill tone={e.scan_result === "pass" ? "green" : "red"}>二次扫描{e.scan_result === "pass" ? "通过" : "拦截"}</Pill>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    → {e.target_model} · 占位符 {e.placeholder_count} · {Object.entries(e.rules_hit || {}).map(([k, v]) => `${k}×${v}`).join("、")}
                  </div>
                  <div className="mt-1.5 rounded bg-muted/70 p-1.5 font-mono text-[10.5px] leading-relaxed break-all">{e.content_preview?.slice(0, 360)}</div>
                </div>
              ))}
            </TabsContent>
            <TabsContent value="tools" className="mt-3 max-h-[560px] space-y-1.5 overflow-y-auto">
              {(run.tool_calls || []).map((t: any) => (
                <div key={t.id} className="rounded-md border px-2.5 py-1.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono font-medium">{t.tool}</span>
                    <Pill>{t.permission}</Pill>
                    <ZoneBadge zone={t.zone} />
                    <span className="ml-auto text-muted-foreground tabular">{t.duration_ms} ms</span>
                  </div>
                  <div className="mt-0.5 line-clamp-3 text-[11px] text-muted-foreground">{t.result}</div>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                    {Object.entries(t.guard || {}).map(([k, v]) => (
                      <span key={k} className={cn(v !== "pass" && "text-amber-700")}>
                        {k}:{String(v)}
                      </span>
                    ))}
                    {t.retries > 0 && <span>重试 {t.retries}</span>}
                  </div>
                </div>
              ))}
            </TabsContent>
          </Tabs>
        </Section>
      </div>

      <Section title="运行信息" className="mt-4">
        <KV
          cols={4}
          items={[
            { label: "运行编号", value: `#${run.id}` },
            { label: "创建时间", value: dateTime(run.created_at) },
            { label: "结束时间", value: dateTime(run.ended_at) },
            { label: "业务对象", value: Object.entries(run.business_ref || {}).map(([k, v]) => `${k}=${v}`).join("，") },
            { label: "锁定版本", value: `${run.locked_versions?.agent || ""} · ${Object.entries(run.locked_versions?.skills || {}).map(([k, v]) => `${k}@${v}`).join("，")}`, full: true },
            { label: "结果摘要", value: run.output?.summary, full: true },
          ]}
        />
      </Section>
    </div>
  );
}
