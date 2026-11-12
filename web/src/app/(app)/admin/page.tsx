"use client";

import { toast } from "sonner";
import { Cloud, Cpu, Lock, ShieldCheck } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { AGENTS, dateTime, num } from "@/lib/format";
import { DataLevel, ErrorBlock, LoadingBlock, PageHeader, Pill, Section, StatCard, ZoneBadge } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Bars } from "@/components/charts";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TT: Record<string, string> = { "TT-01": "整体规划", "TT-02": "公开信息分析", "TT-03": "私有材料抽取", "TT-04": "私域脱敏改写", "TT-05": "综合推理", "TT-06": "长文本生成", "TT-07": "结构化格式化", "TT-08": "评审", "TT-09": "反思诊断", "TT-10": "图像理解", "TT-11": "检索", "TT-12": "语音转写", "TT-13": "统计与机器学习", "TT-14": "上下文压缩", "TT-15": "对话解释" };

export default function AdminPage() {
  const compute = useApi<any>("/admin/compute", { refreshInterval: 15000 });
  const models = useApi<any[]>("/admin/models");
  const rules = useApi<any[]>("/admin/routing");
  const runtime = useApi<any>("/admin/runtime", { refreshInterval: 5000 });
  const rulePage = usePagedList(rules.data || [], "rules");
  const callPage = usePagedList(compute.data?.recent_calls || [], "calls");
  if (compute.error) return <ErrorBlock error={compute.error} />;
  const c = compute.data;
  const toggle = async (id: string, enabled: boolean) => {
    try {
      await api(`/admin/models/${id}`, { method: "PATCH", json: { enabled } });
      toast.success(enabled ? "模型已启用" : "模型已停用，路由将沿兜底链回退");
      models.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader title="云私计算与路由" description="分层模型路由：任务类型 × 数据级别 → 模型链。云端大模型负责通用推理与长文本（只接收脱敏数据），私有化多模态小模型处理私有材料；任何模型不可用时沿兜底链回退，最终由内置推理引擎兜底。" />
      {!c ? (
        <LoadingBlock />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard label="云域调用（7 天）" value={c.zones.cloud?.calls || 0} icon={Cloud} tone="primary" hint={`${num((c.zones.cloud?.tokens_in || 0) + (c.zones.cloud?.tokens_out || 0), 0)} tokens · 均 ${c.zones.cloud?.avg_latency_ms || 0} ms`} />
            <StatCard label="私域调用（7 天）" value={c.zones.private?.calls || 0} icon={Lock} tone="success" hint={`${num((c.zones.private?.tokens_in || 0) + (c.zones.private?.tokens_out || 0), 0)} tokens · 均 ${c.zones.private?.avg_latency_ms || 0} ms`} />
            <StatCard label="脱敏出域" value={c.egress.count} icon={ShieldCheck} hint={`占位符 ${c.egress.placeholders} 个`} />
            <StatCard label="出域拦截" value={c.egress.blocked} tone={c.egress.blocked ? "warning" : "default"} hint="二次扫描发现原文残留" />
            <StatCard label="运行执行器" value={`${runtime.data?.active?.length ?? 0} / ${runtime.data?.executor_workers ?? "—"}`} icon={Cpu} hint={`排队 ${runtime.data?.queue ?? 0} · 监测${runtime.data?.monitor?.enabled ? "开启" : "关闭"}${runtime.data?.llm_disabled ? " · 大模型已关闭" : ""}`} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Section title="各智能体的云域与私域调用">
              <Bars data={Object.entries(c.by_agent).map(([k, v]: any) => ({ name: AGENTS[k]?.name || k, 云域: v.cloud, 私域: v.private }))} xKey="name" bars={[{ key: "云域", name: "云域", color: "#0ea5e9" }, { key: "私域", name: "私域", color: "#14b8a6" }]} />
            </Section>
            <Section title="按任务类型分布">
              <Bars data={Object.entries(c.by_task_type).map(([k, v]: any) => ({ name: TT[k] || k, 云域: v.cloud, 私域: v.private, 兜底: v.fallback }))} xKey="name" horizontal height={300} bars={[{ key: "云域", name: "云域", color: "#0ea5e9" }, { key: "私域", name: "私域", color: "#14b8a6" }, { key: "兜底", name: "兜底回退", color: "#f59e0b" }]} />
            </Section>
          </div>
        </>
      )}
      <Tabs defaultValue="models" className="mt-4">
        <TabsList>
          <TabsTrigger value="models">模型注册</TabsTrigger>
          <TabsTrigger value="rules">路由策略</TabsTrigger>
          <TabsTrigger value="calls">最近调用</TabsTrigger>
        </TabsList>
        <TabsContent value="models" className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(models.data || []).map((m) => (
            <Section key={m.id} title={m.name} actions={<Switch checked={m.enabled} onCheckedChange={(v) => toggle(m.id, v)} />}>
              <div className="flex flex-wrap items-center gap-1.5">
                <ZoneBadge zone={m.zone} />
                <Pill>{m.tier}</Pill>
                <Pill tone={m.configured ? "green" : "gray"}>{m.configured ? "已配置" : "未配置"}</Pill>
                <Pill tone={m.kind === "llm" ? "blue" : "gray"}>{m.kind === "builtin" ? "内置引擎" : m.kind}</Pill>
                <span className="text-xs text-muted-foreground">最高 L{m.max_level}</span>
              </div>
              {m.kind === "llm" ? (
                <div className="mt-2 text-xs text-muted-foreground">{m.display_model || m.model_name}</div>
              ) : (
                <>
                  <div className="mt-2 text-xs text-muted-foreground">{m.model_name ? `${m.model_name} · ${m.base_url}` : "平台内置推理引擎（规则与统计算法实现，所有兜底链的最后一环）"}</div>
                  {Object.keys(m.params || {}).length > 0 && <div className="mt-1 font-mono text-[10px] text-muted-foreground">{JSON.stringify(m.params)}</div>}
                </>
              )}
              <div className="mt-1 text-xs text-muted-foreground">能力：{(m.capabilities || []).join("、")}</div>
            </Section>
          ))}
        </TabsContent>
        <TabsContent value="rules" className="mt-3">
          <Section contentClassName="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="py-2">规则</th>
                  <th>任务类型</th>
                  <th>数据级别</th>
                  <th>首选模型链</th>
                  <th>兜底链</th>
                  <th>版本</th>
                </tr>
              </thead>
              <tbody>
                {rulePage.items.map((r) => (
                  <tr key={r.id} className="border-b align-top last:border-0">
                    <td className="py-2">
                      <div className="font-medium">{r.name}</div>
                      <div className="font-mono text-[10px] text-muted-foreground">{r.id}</div>
                    </td>
                    <td>{r.task_types.map((t: string) => TT[t] || t).join("、")}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {r.data_levels.map((l: number) => (
                          <DataLevel key={l} level={l} />
                        ))}
                      </div>
                    </td>
                    <td className="font-mono text-[11px]">{r.preferred.join(" → ")}</td>
                    <td className="font-mono text-[11px] text-muted-foreground">{r.fallback.join(" → ")}</td>
                    <td>v{r.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PaginationBar page={rulePage.page} pages={rulePage.pages} total={rulePage.total} onPage={rulePage.setPage} />
            <div className="mt-3 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">规则校验：L3 高敏数据的任务不允许出现云端模型；兜底链只允许从云域回退到私域，不允许反向。</div>
          </Section>
        </TabsContent>
        <TabsContent value="calls" className="mt-3">
          <Section contentClassName="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="py-2">时间</th>
                  <th>运行</th>
                  <th>推理任务</th>
                  <th>模型</th>
                  <th>计算域</th>
                  <th>数据级别</th>
                  <th className="text-right">耗时</th>
                  <th className="text-right">tokens</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {callPage.items.map((x: any) => (
                  <tr key={x.id} className="border-b last:border-0">
                    <td className="py-1.5 text-muted-foreground">{dateTime(x.ts)}</td>
                    <td>#{x.run_id}</td>
                    <td className="font-mono">{x.task}</td>
                    <td>{x.model_id}</td>
                    <td>
                      <ZoneBadge zone={x.zone} />
                    </td>
                    <td>
                      <DataLevel level={x.data_level} />
                    </td>
                    <td className="text-right tabular">{x.latency_ms} ms</td>
                    <td className="text-right tabular">{x.tokens}</td>
                    <td>{x.status === "ok" ? <Pill tone="green">成功{x.fallback ? "（兜底）" : ""}</Pill> : <Pill tone="red" className="max-w-56 truncate">{x.error || x.status}</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PaginationBar page={callPage.page} pages={callPage.pages} total={callPage.total} onPage={callPage.setPage} />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
