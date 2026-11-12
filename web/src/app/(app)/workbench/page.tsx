"use client";

import Link from "next/link";
import { Activity, AlertOctagon, ArrowRight, Boxes, ClipboardCheck, Cloud, FileText, LifeBuoy, Lock, ShieldCheck, Siren, Workflow } from "lucide-react";
import { useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { relative } from "@/lib/format";
import { AgentBadge, AlertLevel, EmptyState, ErrorBlock, PageHeader, PageLoading, Pill, RunStatus, Section, StatCard } from "@/components/common";
import { HitlCardView } from "@/components/hitl";
import { Button } from "@/components/ui/button";

const STAGES = [
  ["risk", "风险评估"],
  ["preference", "偏好"],
  ["clause", "条款设计"],
  ["pricing", "精算定价"],
  ["package", "产品包"],
  ["approval", "审批发布"],
];
const NODE_TONE: Record<string, string> = { done: "bg-emerald-500", running: "bg-sky-500 animate-pulse", waiting_confirm: "bg-amber-500", waiting_input: "bg-amber-500", waiting_approval: "bg-violet-500", ready: "bg-indigo-400", failed: "bg-rose-500", skipped: "bg-muted-foreground/30", stale: "bg-orange-400", pending: "bg-muted-foreground/25" };

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "上午好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
}

export default function Workbench() {
  const { user } = useAuth();
  const { data, error, mutate } = useApi<any>("/dashboard", { refreshInterval: 8000 });
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!data) return <PageLoading />;
  const isDesigner = user?.role === "designer" || user?.role === "admin";
  const isService = user?.role === "service" || user?.role === "admin";
  const compute = data.compute || {};
  return (
    <div>
      <PageHeader
        title={`${greeting()}，${user?.display_name}`}
        description={`${user?.role_label} · ${user?.positions?.join("、")}。你有 ${data.todo_count} 项待处理事项${data.overdue_count ? `，其中 ${data.overdue_count} 项已超时` : ""}。`}
        actions={
          <>
            {isDesigner && (
              <Link href="/design?new=1">
                <Button>
                  <Workflow className="size-4" /> 新建设计项目
                </Button>
              </Link>
            )}
            {isService && (
              <Link href="/underwriting?new=1">
                <Button variant={isDesigner ? "outline" : "default"}>
                  <ShieldCheck className="size-4" /> 新建投保
                </Button>
              </Link>
            )}
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="待办事项" value={data.todo_count} hint={data.overdue_count ? `超时 ${data.overdue_count} 项` : "均在时限内"} icon={ClipboardCheck} tone={data.overdue_count ? "danger" : "primary"} href="/inbox" />
        <StatCard label="运行中的智能体" value={data.active_runs.length} hint="点击查看运行台" icon={Activity} tone="primary" href="/runs" />
        {isDesigner && <StatCard label="已发布产品" value={data.products?.published ?? 0} hint={`待审批 ${data.products?.pending_approval ?? 0}`} icon={Boxes} tone="success" href="/products" />}
        {isService && (
          <>
            <StatCard label="核保队列" value={data.uw_queue} hint="待核保与待决定" icon={ShieldCheck} href="/underwriting" />
            <StatCard label="未关闭预警" value={(data.alerts?.open?.red || 0) + (data.alerts?.open?.orange || 0) + (data.alerts?.open?.yellow || 0)} hint={`红 ${data.alerts?.open?.red || 0} · 橙 ${data.alerts?.open?.orange || 0} · 黄 ${data.alerts?.open?.yellow || 0}`} icon={AlertOctagon} tone={data.alerts?.open?.red ? "danger" : "warning"} href="/monitoring" />
            <StatCard label="理赔在办" value={data.claims_open} hint={`事故响应 ${data.incidents_open} 起`} icon={LifeBuoy} href="/claims" />
            <StatCard label="有效保单" value={data.policies_active} hint="持续监测中" icon={FileText} href="/policies" />
          </>
        )}
      </div>

      {user?.role === "admin" && (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <StatCard label="云域调用（7 天）" value={compute.cloud?.calls || 0} hint={`平均 ${compute.cloud?.avg_latency_ms || 0} ms · 兜底 ${compute.cloud?.fallbacks || 0}`} icon={Cloud} tone="primary" href="/admin" />
          <StatCard label="私域调用（7 天）" value={compute.private?.calls || 0} hint={`平均 ${compute.private?.avg_latency_ms || 0} ms`} icon={Lock} tone="success" href="/admin" />
          <StatCard label="脱敏出域" value={data.egress_count} hint="全部出域记录可审计" icon={ShieldCheck} href="/admin/audit" />
        </div>
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Section title="我的待办" description="需要你确认、审批或补充信息的事项" actions={<Link href="/inbox" className="text-xs text-primary hover:underline">全部待办</Link>}>
            {data.todo_cards.length === 0 ? (
              <EmptyState title="没有待处理事项" description="智能体需要人工介入时会通知你。" />
            ) : (
              <div className="space-y-3">
                {data.todo_cards.slice(0, 4).map((c: any) => (
                  <HitlCardView key={c.id} card={c} compact onResolved={() => mutate()} />
                ))}
              </div>
            )}
          </Section>

          {isDesigner && data.projects && (
            <Section title="设计项目流水线" actions={<Link href="/design" className="text-xs text-primary hover:underline">全部项目</Link>}>
              {data.projects.length === 0 ? (
                <EmptyState title="还没有设计项目" action={<Link href="/design?new=1"><Button size="sm">新建项目</Button></Link>} />
              ) : (
                <div className="space-y-2">
                  {data.projects.map((p: any) => (
                    <Link key={p.id} href={`/design/${p.id}`} className="flex flex-col gap-2 rounded-lg border p-3 transition-colors hover:bg-muted/40 sm:flex-row sm:items-center">
                      <div className="min-w-0 sm:w-56">
                        <div className="truncate text-sm font-medium">{p.name}</div>
                        <div className="text-xs text-muted-foreground">{p.project_type === "track" ? `赛道通用 · ${p.track}` : "企业定制"}</div>
                      </div>
                      <div className="flex flex-1 items-center gap-1">
                        {STAGES.map(([k, l]) => (
                          <div key={k} className="flex flex-1 flex-col items-center gap-1">
                            <div className={`h-1.5 w-full rounded-full ${NODE_TONE[p.nodes[k]] || NODE_TONE.pending}`} />
                            <span className="text-[10px] text-muted-foreground">{l}</span>
                          </div>
                        ))}
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </Section>
          )}
        </div>

        <div className="space-y-4">
          {isService && data.alerts && (
            <Section title="最新预警" actions={<Link href="/monitoring" className="text-xs text-primary hover:underline">监测总览</Link>}>
              {data.alerts.recent.length === 0 ? (
                <EmptyState title="暂无预警" icon={Siren} />
              ) : (
                <div className="space-y-1.5">
                  {data.alerts.recent.map((a: any) => (
                    <Link key={a.id} href={`/monitoring/alerts/${a.id}`} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted/60">
                      <AlertLevel level={a.level} />
                      <span className="min-w-0 flex-1 truncate text-sm">{a.title}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">{relative(a.created_at)}</span>
                    </Link>
                  ))}
                </div>
              )}
            </Section>
          )}
          <Section title="智能体运行" actions={<Link href="/runs" className="text-xs text-primary hover:underline">运行记录</Link>}>
            <div className="space-y-1">
              {[...data.active_runs, ...data.recent_runs.filter((r: any) => !data.active_runs.some((a: any) => a.id === r.id))].slice(0, 9).map((r: any) => (
                <Link key={r.id} href={`/runs/${r.id}`} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted/60">
                  <AgentBadge agentId={r.agent_id} label="" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{r.title}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{r.summary || r.agent_name}</div>
                  </div>
                  <RunStatus status={r.status} />
                </Link>
              ))}
            </div>
          </Section>
          <Section title="平台能力">
            <div className="grid grid-cols-2 gap-2 text-xs">
              {[
                ["技能两层加载", "目录常驻、全文按需"],
                ["工具守卫", "权限、隐私、预算、结果四类"],
                ["自我评估", "硬规则、指标、大模型评审"],
                ["反思与自进化", "失败归因 → 修正 → 经验沉淀"],
                ["云私分层", "云端推理、私域敏感数据"],
                ["人审节点", "关键决策可追溯"],
              ].map(([a, b]) => (
                <div key={a} className="rounded-lg bg-muted/60 p-2">
                  <div className="font-medium">{a}</div>
                  <div className="text-muted-foreground">{b}</div>
                </div>
              ))}
            </div>
            <Link href="/agents" className="mt-3 flex items-center gap-1 text-xs text-primary hover:underline">
              进入智能体中心 <ArrowRight className="size-3" />
            </Link>
          </Section>
        </div>
      </div>
      <Pill className="hidden">.</Pill>
    </div>
  );
}
