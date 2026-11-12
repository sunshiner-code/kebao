"use client";

import { useState } from "react";
import { toast } from "sonner";
import { BookOpen, Brain, CheckCircle2, Sparkles, Wrench } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { AGENTS, dateTime, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, LoadingBlock, PageHeader, Pill, Section, ZoneBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const LAYER: Record<string, any> = { hard: ["硬规则", "red"], metric: ["指标", "blue"], review: ["大模型评审", "violet"], evidence: ["证据", "teal"] };

function SkillViewer({ id }: { id: string }) {
  const { data } = useApi<any>(`/skills/${id}`);
  if (!data) return <LoadingBlock rows={2} />;
  return (
    <div className="space-y-3">
      <ol className="space-y-1">
        {data.procedure.map((p: any, i: number) => (
          <li key={p.key} className="flex items-start gap-2 text-xs">
            <span className="w-5 shrink-0 text-muted-foreground tabular">{i + 1}</span>
            <span className="flex-1">
              {p.title}
              {p.purpose && <span className="text-muted-foreground"> — {p.purpose}</span>}
            </span>
            <span className="flex shrink-0 gap-1">
              {p.tool && <span className="font-mono text-[10px] text-muted-foreground">{p.tool}</span>}
              {p.hitl && <Pill tone="amber">人审 {p.hitl}</Pill>}
              {p.condition && <Pill>条件 {p.condition}</Pill>}
              {p.stage_eval && <Pill tone="green">阶段评估</Pill>}
            </span>
          </li>
        ))}
      </ol>
      {Object.entries(data.subprocedures).map(([k, steps]: any) => (
        <div key={k}>
          <div className="text-xs font-medium">子规程：{k}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {steps.map((s: any) => (
              <Pill key={s.key} tone={s.hitl ? "amber" : "gray"}>
                {s.title}
              </Pill>
            ))}
          </div>
        </div>
      ))}
      <details className="text-xs">
        <summary className="cursor-pointer text-primary">查看技能全文（第二层按需加载内容）</summary>
        <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">{data.text}</pre>
      </details>
    </div>
  );
}

export default function AgentsPage() {
  const { user } = useAuth();
  const agents = useApi<any[]>("/agents");
  const tools = useApi<any[]>("/tools");
  const memory = useApi<any[]>("/memory?kind=experience");
  const [sel, setSel] = useState("AG-RA");
  const a = (agents.data || []).find((x) => x.agent_id === sel);
  const toolMap = Object.fromEntries((tools.data || []).map((t) => [t.name, t]));
  const setStatus = async (id: number, status: string) => {
    try {
      await api(`/memory/${id}/status`, { method: "POST", json: { status, reason: "人工评审" } });
      toast.success("经验条目状态已更新");
      memory.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader title="智能体与自进化" description="六个业务智能体共用一套运行框架（统一主循环 + 各自配置包）：技能、工具白名单、评估量表、反思策略、人审节点；经验库记录反思沉淀与人工修订聚类形成的候选经验，评审后生效。" />
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {(agents.data || []).map((x) => (
          <button key={x.agent_id} onClick={() => setSel(x.agent_id)} className={cn("rounded-xl border bg-card p-3 text-left transition-all", sel === x.agent_id ? "border-primary shadow-sm ring-2 ring-primary/20" : "hover:shadow-sm")}>
            <div className="flex items-center gap-2">
              <span className={cn("size-2.5 rounded-full", AGENTS[x.agent_id]?.color)} />
              <span className="text-sm font-medium">{AGENTS[x.agent_id]?.name || x.short}</span>
              <span className="ml-auto text-[10px] text-muted-foreground">v{x.version}</span>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-1 text-center text-[11px]">
              <div>
                <div className="font-semibold tabular">{x.runs}</div>
                <div className="text-muted-foreground">运行</div>
              </div>
              <div>
                <div className="font-semibold tabular">{x.avg_score || "—"}</div>
                <div className="text-muted-foreground">均分</div>
              </div>
              <div>
                <div className="font-semibold tabular">{x.pass_rate !== null ? pct(x.pass_rate) : "—"}</div>
                <div className="text-muted-foreground">达标率</div>
              </div>
            </div>
          </button>
        ))}
      </div>
      {a && (
        <Tabs defaultValue="config" className="mt-4">
          <TabsList>
            <TabsTrigger value="config">配置包</TabsTrigger>
            <TabsTrigger value="skill">技能</TabsTrigger>
            <TabsTrigger value="tools">工具</TabsTrigger>
            <TabsTrigger value="evolution">自进化（经验库）</TabsTrigger>
          </TabsList>
          <TabsContent value="config" className="mt-3 grid gap-4 lg:grid-cols-2">
            <Section title={a.name} description={a.description}>
              <div className="space-y-2 text-xs">
                <div>
                  核心技能 <Pill tone="violet">{a.core_skill}</Pill> 共享技能 {a.shared_skills.map((s: string) => <Pill key={s} className="ml-1">{s}</Pill>)}
                </div>
                <div>
                  运行方式：
                  {Object.entries(a.purposes).length === 0 ? " 主运行" : ` 主运行、${Object.values(a.purposes).join("、")}`}
                </div>
                <div>人审节点：{a.hitl.join("、") || "无"}</div>
                <div>达标线：{a.pass_score} 分</div>
              </div>
            </Section>
            <Section title="评估量表">
              <div className="space-y-1.5">
                {a.rubric.map((d: any) => (
                  <div key={d.key} className="flex items-center gap-2 text-xs">
                    <CheckCircle2 className="size-3.5 text-emerald-600" />
                    <span className="flex-1">{d.name}</span>
                    <Pill tone={LAYER[d.layer]?.[1]}>{LAYER[d.layer]?.[0]}</Pill>
                    <span className="w-24 text-right text-muted-foreground">{d.threshold}</span>
                  </div>
                ))}
              </div>
            </Section>
            <Section title="反思策略" className="lg:col-span-2">
              <div className="grid gap-2 md:grid-cols-2">
                {a.reflection.map((r: any, i: number) => (
                  <div key={i} className="rounded-lg bg-amber-500/5 p-2 text-xs">
                    <div className="font-medium">
                      {r.dims.map((d: string) => (d === "*human" ? "人工驳回" : d)).join("、")} → {r.category}
                    </div>
                    <div className="text-muted-foreground">{r.strategy}</div>
                  </div>
                ))}
              </div>
            </Section>
          </TabsContent>
          <TabsContent value="skill" className="mt-3">
            <Section title={`${a.core_skill} 作业规程`} actions={<BookOpen className="size-4 text-muted-foreground" />}>
              <SkillViewer id={a.core_skill} />
            </Section>
          </TabsContent>
          <TabsContent value="tools" className="mt-3">
            <Section title={`工具白名单（${a.tools.length}）`} description="权限级别 T0 只读 / T1 写草稿 / T2 写正式数据 / T3 对外发送；计算域决定数据能否出域">
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {a.tools.map((t: string) => {
                  const spec = toolMap[t];
                  return (
                    <div key={t} className="rounded-lg border p-2.5 text-xs">
                      <div className="flex items-center gap-1.5">
                        <Wrench className="size-3.5 text-muted-foreground" />
                        <span className="font-medium">{spec?.title || t}</span>
                        <Pill className="ml-auto">{spec?.permission}</Pill>
                        {spec && <ZoneBadge zone={spec.zone} />}
                      </div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">{t}</div>
                      <div className="mt-0.5 text-muted-foreground">{spec?.description}</div>
                    </div>
                  );
                })}
              </div>
            </Section>
          </TabsContent>
          <TabsContent value="evolution" className="mt-3">
            <Section title="经验库" description="来源：反思成功后的策略沉淀、人工修订高频字段聚类；试用中的经验按 20% 抽样注入，成功次数累积后可评审生效" actions={<Sparkles className="size-4 text-violet-500" />}>
              {(memory.data || []).filter((m) => !m.agent_id || m.agent_id === sel).length === 0 ? (
                <EmptyState title="该智能体暂无经验条目" icon={Brain} />
              ) : (
                <div className="space-y-2">
                  {(memory.data || [])
                    .filter((m) => !m.agent_id || m.agent_id === sel)
                    .map((m) => (
                      <div key={m.id} className="flex items-start gap-3 rounded-lg border p-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 text-sm">
                            <span className="font-medium">{m.title}</span>
                            <Pill tone={m.status === "active" ? "green" : m.status === "trial" ? "amber" : "gray"}>{m.status === "active" ? "生效" : m.status === "trial" ? "试用" : "已退役"}</Pill>
                          </div>
                          <div className="mt-0.5 text-xs text-muted-foreground">{m.text}</div>
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            命中 {m.hits} · 成功 {m.successes} · {m.skill_id} · {dateTime(m.updated_at)}
                            {m.source_run_id && ` · 来源运行 #${m.source_run_id}`}
                          </div>
                        </div>
                        {user?.role === "admin" && (
                          <div className="flex shrink-0 gap-1">
                            {m.status !== "active" && (
                              <Button size="xs" onClick={() => setStatus(m.id, "active")}>
                                生效
                              </Button>
                            )}
                            {m.status !== "retired" && (
                              <Button size="xs" variant="outline" onClick={() => setStatus(m.id, "retired")}>
                                退役
                              </Button>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                </div>
              )}
            </Section>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
