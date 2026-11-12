"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { AGENTS, dateTime, pct } from "@/lib/format";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function EvalsPage() {
  const sets = useApi<any[]>("/eval-sets");
  const jobs = useApi<any[]>("/eval-jobs", { refreshInterval: 5000 });
  const props = useApi<any[]>("/evolution", { refreshInterval: 8000 });
  const online = useApi<any>("/evals/online?days=30");
  const [agent, setAgent] = useState("AG-UW");
  const [name, setName] = useState("黄金集");
  const build = async () => {
    try {
      const out = await api<any[]>("/eval-sets/from-runs", { method: "POST", json: { agent_id: agent, name, limit: 8 } });
      toast.success(`已沉淀 ${out.reduce((n, s) => n + s.samples, 0)} 个样本`);
      sets.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const run = async (id: number) => {
    try {
      await api("/eval-jobs", { method: "POST", json: { eval_set_id: id, label: "手动评测" } });
      toast.success("评测已排队，样本以评测模式重放，不写业务数据");
      jobs.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const analyze = async () => {
    try {
      const out = await api<any[]>("/evolution/analyze", { method: "POST" });
      toast.success(out.length ? `生成 ${out.length} 条提案` : "最近运行没有达到生成提案的条件");
      props.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const call = async (path: string, ok: string) => {
    try {
      await api(path, { method: "POST", json: path.endsWith("/rollback") ? { reason: "在评测与发布页发起回滚" } : {} });
      toast.success(ok);
      props.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const setPage = usePagedList(sets.data || [], "");
  const jobPage = usePagedList(jobs.data || [], "");
  const propPage = usePagedList(props.data || [], "");
  if (sets.error) return <ErrorBlock error={sets.error} />;
  return (
    <div>
      <PageHeader title="评测与发布" description="评测集从线上运行沉淀并划分开发集与保留集。评测任务以评测模式重放：模拟审核人按主选项处理，不触发出单、报案和支付。改进提案经评测对比、审批、灰度和回滚后才全量生效。" />
      <Tabs defaultValue="sets">
        <TabsList>
          <TabsTrigger value="sets">评测集</TabsTrigger>
          <TabsTrigger value="jobs">评测任务</TabsTrigger>
          <TabsTrigger value="online">线上评估</TabsTrigger>
          <TabsTrigger value="evo">改进提案</TabsTrigger>
        </TabsList>
        <TabsContent value="sets" className="mt-3 space-y-3">
          <Section title="从已完成运行沉淀">
            <div className="flex flex-wrap items-center gap-2">
              <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={agent} onChange={(e) => setAgent(e.target.value)}>
                {Object.entries(AGENTS).map(([id, meta]) => <option key={id} value={id}>{meta.name}</option>)}
              </select>
              <Input className="h-8 w-40" value={name} onChange={(e) => setName(e.target.value)} />
              <Button size="sm" onClick={build}>沉淀样本</Button>
            </div>
          </Section>
          {(sets.data || []).length === 0 && <EmptyState title="还没有评测集" />}
          {setPage.items.map((s) => (
            <Section key={s.id} title={s.name} actions={<Button size="sm" variant="outline" onClick={() => run(s.id)}>发起评测</Button>}>
              <div className="text-xs text-muted-foreground">{s.agent_name} · {s.split_label} V{s.version} · {s.samples} 个样本 · {dateTime(s.created_at)}</div>
            </Section>
          ))}
          <PaginationBar page={setPage.page} pages={setPage.pages} total={setPage.total} onPage={setPage.setPage} />
        </TabsContent>
        <TabsContent value="jobs" className="mt-3 space-y-3">
          {(jobs.data || []).length === 0 && <EmptyState title="还没有评测任务" />}
          {jobPage.items.map((j) => (
            <Section key={j.id} title={j.label || `评测 #${j.id}`} actions={<Pill tone={j.status === "completed" ? "green" : j.status === "failed" ? "red" : "blue"}>{j.status}</Pill>}>
              <div className="text-xs text-muted-foreground">{j.agent_name} · 配置 {j.config_version} · 进度 {pct(j.progress)}</div>
              {j.metrics?.avg_score != null && <div className="mt-1 text-sm">均分 {j.metrics.avg_score} · 达标率 {pct(j.metrics.pass_rate)} · 结论一致率 {pct(j.metrics.match_rate)}{j.metrics.delta ? ` · 相比基线 ${j.metrics.delta.avg_score ?? 0} 分` : ""}</div>}
              {j.metrics?.failure_attribution?.length > 0 && <div className="mt-1 text-xs text-muted-foreground">失败归因：{j.metrics.failure_attribution.map((d: any) => `${d.dim} ${d.count}`).join("、")}</div>}
              <Link href={`/agents/evals/${j.id}`} className="mt-1 inline-block text-xs text-primary hover:underline">查看样本</Link>
            </Section>
          ))}
          <PaginationBar page={jobPage.page} pages={jobPage.pages} total={jobPage.total} onPage={jobPage.setPage} />
        </TabsContent>
        <TabsContent value="online" className="mt-3">
          {!online.data ? <LoadingBlock rows={2} /> : (
            <Section title="近 30 天线上运行">
              <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                <div>运行 {online.data.runs}</div>
                <div>人审修改率 {pct(online.data.human_modification_rate)}</div>
                <div>预警精确率 {pct(online.data.business.alert_precision)}</div>
                <div>赔付偏差 {pct(online.data.business.claim_payout_deviation)}</div>
              </div>
              <div className="mt-2 text-xs text-muted-foreground">事后评估 {online.data.posthoc.length} 条（出险、预警处置、赔付偏差回填到原运行）</div>
            </Section>
          )}
        </TabsContent>
        <TabsContent value="evo" className="mt-3 space-y-3">
          <Button size="sm" onClick={analyze}>运行进化分析</Button>
          {(props.data || []).length === 0 && <EmptyState title="还没有改进提案" />}
          {propPage.items.map((p) => (
            <Section key={p.id} title={p.title} actions={<Pill>{p.status}</Pill>}>
              <div className="text-sm">{p.rationale}</div>
              <div className="mt-1 text-xs text-muted-foreground">{p.agent_name} · {p.kind_label} · {p.proposal_no}</div>
              {p.eval_summary?.conclusion && <div className="mt-1 text-xs">评测：{p.eval_summary.conclusion}</div>}
              <div className="mt-2 flex flex-wrap gap-2">
                {p.status === "draft" && <Button size="sm" variant="outline" onClick={() => call(`/evolution/${p.id}/evaluate`, "已发起提案评测")}>评测</Button>}
                {p.status === "evaluated" && <Button size="sm" onClick={() => call(`/evolution/${p.id}/submit`, "已提交发布审批")}>提交审批</Button>}
                {p.status === "canary" && <Button size="sm" onClick={() => call(`/evolution/${p.id}/release`, "已全量发布")}>全量发布</Button>}
                {["canary", "released"].includes(p.status) && <Button size="sm" variant="ghost" onClick={() => call(`/evolution/${p.id}/rollback`, "已回滚")}>回滚</Button>}
              </div>
            </Section>
          ))}
          <PaginationBar page={propPage.page} pages={propPage.pages} total={propPage.total} onPage={propPage.setPage} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
