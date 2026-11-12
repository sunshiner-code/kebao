"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Boxes, CheckCircle2, ExternalLink, Loader2, Play, RotateCcw } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { dateTime, pct } from "@/lib/format";
import { NODE_LABEL, NODE_TONE, STAGES, stageActionReady } from "@/lib/pipeline";
import { cn } from "@/lib/utils";
import { AgentBadge, EmptyState, ErrorBlock, KV, PageHeader, PageLoading, Pill, RunStatus, ScoreBadge, Section, StartMode } from "@/components/common";
import { Button } from "@/components/ui/button";

const STAGE_LINK: Record<string, string> = { risk: "/design/risk", clause: "/design/clauses", pricing: "/design/pricing" };
const STAGE_AGENT: Record<string, string> = { risk: "AG-RA", clause: "AG-CD", pricing: "AG-AP" };

function PreferenceForm({ pid, onDone }: { pid: number; onDone: () => void }) {
  const { data: qs } = useApi<any[]>("/preference-questions");
  const [ans, setAns] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const out = await api<any>(`/design-projects/${pid}/preference`, { method: "POST", json: { answers: ans } });
      toast.success(`风险偏好：${out.preference_label}，承受能力：${out.capacity_level}；条款设计已启动`);
      onDone();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="风险偏好与承受能力评估" description="12 题问卷（1 = 完全不同意，5 = 完全同意）；承受能力由私域根据企业财务自动计算（净资产 5%、经营现金流 20%、现金储备减 6 个月固定支出三者取小）">
      <div className="grid gap-3 md:grid-cols-2">
        {(qs || []).map((q) => (
          <div key={q.key} className="rounded-lg border p-3">
            <div className="text-sm">{q.text}</div>
            <div className="mt-2 flex gap-1.5">
              {[1, 2, 3, 4, 5].map((v) => (
                <button key={v} onClick={() => setAns({ ...ans, [q.key]: v })} className={cn("size-8 rounded-md border text-sm", (ans[q.key] ?? 3) === v ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                  {v}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <Button className="mt-4" onClick={submit} disabled={busy}>
        {busy && <Loader2 className="size-3.5 animate-spin" />} 提交并启动条款设计
      </Button>
    </Section>
  );
}

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { data: p, error, mutate } = useApi<any>(`/design-projects/${id}`, { refreshInterval: 4000 });
  const [busy, setBusy] = useState<string | null>(null);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!p) return <PageLoading />;
  const nodes = p.pipeline.nodes;
  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    try {
      await fn();
      toast.success(`${label}：已提交`);
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  };
  const stages = STAGES;
  const start = (stage: string) => act(`启动${stages.find((s) => s[0] === stage)?.[1]}`, () => api(`/design-projects/${id}/stages/${stage}/start`, { method: "POST" }));
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "产品设计" }, { label: "设计项目", href: "/design" }, { label: p.name }]}
        title={p.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone={p.project_type === "track" ? "blue" : p.project_type === "need" ? "teal" : "violet"}>{p.project_type === "track" ? `赛道通用 · ${p.track}` : p.project_type === "need" ? "需求描述" : "企业定制"}</Pill>
            {p.enterprises.map((e: any) => (
              <Link key={e.id} href={`/customers/${e.id}`} className="text-xs hover:text-primary">
                {e.name}
              </Link>
            ))}
            <span className="text-xs">· 模式 {p.config?.mode === "semi" ? "半自动" : "自动"}</span>
          </span>
        }
        actions={
          <>
            {nodes.package?.status === "ready" && (
              <Button onClick={() => act("组装产品包", () => api(`/design-projects/${id}/package`, { method: "POST" }))} disabled={!!busy}>
                {busy === "组装产品包" ? <Loader2 className="size-4 animate-spin" /> : <Boxes className="size-4" />} 组装产品包
              </Button>
            )}
            {p.product && (
              <Link href={`/products/${p.product.id}`}>
                <Button variant="outline">
                  查看产品 {p.product.code} <ArrowRight className="size-4" />
                </Button>
              </Link>
            )}
          </>
        }
      />

      <Section title="设计流水线" description="任务图：上游版本冻结后自动推进下游；上游更新后下游标记为需重跑">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          {stages.map(([k, l], i) => {
            const n = nodes[k] || {};
            const st = n.status || "pending";
            const artifact = n.artifact_key || n.draft_key;
            return (
              <div key={k} className="relative">
                <div className={cn("h-full rounded-xl border p-3", st === "running" && "border-sky-400 shadow-[0_0_0_3px_rgba(14,165,233,.12)]", st.startsWith("waiting") && "border-amber-400", st === "done" && "border-emerald-300")}>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">0{i + 1}</span>
                    <Pill tone={NODE_TONE[st]}>{NODE_LABEL[st] || st}</Pill>
                  </div>
                  <div className="mt-1 font-medium">{l}</div>
                  {k === "pricing" ? <div className="mt-1 text-xs text-muted-foreground">创新积分制定价与分类费率厘定</div> : STAGE_AGENT[k] && <AgentBadge agentId={STAGE_AGENT[k]} label={`${{ "AG-RA": "风险评估", "AG-CD": "条款设计", "AG-AP": "精算定价" }[STAGE_AGENT[k]]}智能体`} />}
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {n.version && <div>版本 V{n.version}</div>}
                    {n.eval_score !== undefined && n.eval_score !== null && <ScoreBadge score={n.eval_score} passed={n.eval_score >= 75} />}
                    {n.start_mode && <StartMode mode={n.start_mode} degrade={n.degrade_level} />}
                    {n.progress !== undefined && st === "running" && <div>进度 {pct(n.progress)}</div>}
                    {n.note && <div className="text-orange-700">{n.note}</div>}
                    {k === "preference" && n.result && (
                      <div>
                        {n.result.preference_label} · 承受能力{n.result.capacity_level}
                      </div>
                    )}
                    {k === "package" && n.product_id && <Link href={`/products/${n.product_id}`} className="text-primary">产品 #{n.product_id}</Link>}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {n.run_id && (
                      <Link href={`/runs/${n.run_id}`}>
                        <Button size="xs" variant="outline">
                          {st === "waiting_confirm" ? "去确认" : "运行台"}
                        </Button>
                      </Link>
                    )}
                    {artifact && STAGE_LINK[k] && (
                      <Link href={`${STAGE_LINK[k]}/${artifact}`}>
                        <Button size="xs" variant="secondary">
                          产物 <ExternalLink className="size-3" />
                        </Button>
                      </Link>
                    )}
                    {STAGE_AGENT[k] && stageActionReady(nodes, k, p.project_type) && (["pending", "failed", "stale", "cancelled", "done"].includes(st) || (st === "waiting_confirm" && !n.run_id)) && (
                      <Button size="xs" variant="ghost" onClick={() => start(k)} disabled={!!busy} title={st === "pending" ? "开始本阶段" : "重跑本阶段将重置后续步骤，并在本阶段完成后按流程继续"}>
                        {st === "pending" ? <Play className="size-3" /> : <RotateCcw className="size-3" />} {st === "pending" ? "开始" : st === "waiting_confirm" ? "重新评估" : "重跑"}
                      </Button>
                    )}
                    {STAGE_AGENT[k] && st === "pending" && !stageActionReady(nodes, k, p.project_type) && (
                      <span className="text-[11px] text-muted-foreground">等待前序完成</span>
                    )}
                  </div>
                </div>
                {i < stages.length - 1 && <ArrowRight className="absolute top-1/2 -right-2.5 z-10 hidden size-4 -translate-y-1/2 text-muted-foreground xl:block" />}
              </div>
            );
          })}
        </div>
      </Section>

      {nodes.preference?.status === "waiting_input" && (
        <div className="mt-4">
          <PreferenceForm pid={Number(id)} onDone={() => mutate()} />
        </div>
      )}

      {nodes.approval?.status === "waiting_approval" && p.product && (
        <div className="mt-4 flex items-center gap-3 rounded-xl border border-violet-300 bg-violet-500/5 p-4 text-sm">
          <CheckCircle2 className="size-5 text-violet-600" />
          <div className="flex-1">产品包已组装，等待二级及以上审批人审批发布。</div>
          <Link href={`/products/${p.product.id}`}>
            <Button size="sm">前往审批</Button>
          </Link>
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Section title="智能体运行">
          {p.runs.length === 0 ? (
            <EmptyState title="暂无运行" />
          ) : (
            <div className="space-y-1.5">
              {p.runs
                .slice()
                .reverse()
                .map((r: any) => (
                  <Link key={r.id} href={`/runs/${r.id}`} className="flex items-center gap-3 rounded-lg border px-3 py-2 hover:bg-muted/40">
                    <AgentBadge agentId={r.agent_id} label="" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm">{r.title}</div>
                      <div className="truncate text-xs text-muted-foreground">{r.summary || dateTime(r.created_at)}</div>
                    </div>
                    <ScoreBadge score={r.eval_score} passed={r.eval_passed} />
                    <RunStatus status={r.status} />
                  </Link>
                ))}
            </div>
          )}
          {(p.pipeline.revisions || []).length > 0 && (
            <div className="mt-4">
              <div className="mb-2 text-sm font-medium">修订回路（精算定价 → 条款设计）</div>
              {p.pipeline.revisions.map((r: any) => (
                <div key={r.request_no} className="mb-1.5 rounded-lg bg-amber-500/5 p-2 text-xs">
                  <span className="font-medium">{r.request_no}</span> · {r.status_label}
                  {r.new_version && ` · 条款方案 V${r.new_version}`}
                  <div className="text-muted-foreground">{(r.applied || r.changes || []).map((c: any) => (typeof c === "string" ? c : c.reason)).join("；")}</div>
                </div>
              ))}
            </div>
          )}
        </Section>
        <Section title="流水线日志">
          <ol className="space-y-2 text-xs">
            {(p.pipeline.history || [])
              .slice()
              .reverse()
              .map((h: any, i: number) => (
                <li key={i} className="flex gap-2">
                  <span className="w-28 shrink-0 text-muted-foreground">{dateTime(h.ts)}</span>
                  <span>{h.text}</span>
                </li>
              ))}
          </ol>
          <div className="mt-4 border-t pt-3">
            <KV
              cols={1}
              items={[
                { label: "目标险种", value: p.target_lines.length ? p.target_lines.join("、") : "由智能体推荐" },
                { label: "产物", value: p.artifacts.map((a: any) => `${a.title} V${a.version}${a.status === "frozen" ? "（冻结）" : ""}`).join("；") },
              ]}
            />
          </div>
        </Section>
      </div>
    </div>
  );
}
