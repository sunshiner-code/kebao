"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { STAGE_NAMES } from "@/lib/format";
import { DataLevel, KV, Pill, RiskLevel, Section, StatCard, ZoneBadge } from "@/components/common";
import { ForceGraph, Heatmap } from "@/components/graph";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const INS_TONE: Record<string, "green" | "amber" | "gray"> = { yes: "green", partial: "amber", no: "gray" };

function RiskRow({ r, evidence, open, onToggle }: { r: any; evidence: Record<string, any>; open: boolean; onToggle: () => void }) {
  const hasBaseline = r.baseline_l != null && r.baseline_s != null;
  const reason = typeof r.adjust_reason === "string" ? r.adjust_reason.replace(/[。；;]+$/, "") : "";
  // 需求描述项目由模型直接判定分数，没有财务基线，展示口径要跟着切换
  const scoreBasis = (
    hasBaseline
      ? [`基线 ${r.baseline_l}×${r.baseline_s}`, r.indicator_note, reason]
      : [`模型直接判定 ${r.likelihood ?? "—"}×${r.severity ?? "—"}`, reason, r.score_basis]
  )
    .filter((part) => typeof part === "string" && part.trim())
    .join("；");
  const bearers: string[] = Array.isArray(r.bearers) ? r.bearers : [];
  const createdNote = typeof r.basis === "string" && r.basis.trim() ? r.basis : "现有分类体系没有合适类型，据此创建";
  const items: { label: string; value: React.ReactNode; full?: boolean }[] = [
    { label: "评分依据", value: scoreBasis, full: true },
    ...(r.stage_rationale ? [{ label: "阶段判定", value: r.stage_rationale, full: true }] : []),
    ...(r.controllability || r.trend ? [{ label: "可控性 / 趋势", value: `${r.controllability || "—"} / ${r.trend || "—"}` }] : []),
    ...(r.confidence != null ? [{ label: "置信度", value: r.confidence }] : []),
    { label: "可保性说明", value: r.insurability_reason || r.insurability_analysis?.judgment || "—", full: true },
    ...(r.event ? [{ label: "事件", value: r.event, full: true }] : []),
    ...(bearers.length ? [{ label: "承担主体", value: bearers.join("、"), full: true }] : []),
    ...(r.novel ? [{ label: "创建说明", value: createdNote, full: true }] : []),
    ...(r.advice || r.form_reason ? [{ label: "管理建议", value: r.advice || r.form_reason, full: true }] : []),
  ];
  return (
    <>
      <tr className={cn("cursor-pointer border-b hover:bg-muted/40", open && "bg-muted/40")} onClick={onToggle}>
        <td className="w-[3%] py-2 pl-3">{open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}</td>
        <td className="w-[7%] py-2 font-mono text-xs tabular">{r.id}</td>
        <td className="w-[28%] py-2 pr-2">
          <div className="font-medium leading-snug">{r.title}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            <span>{r.type_name}</span>
            {r.novel && <Pill tone="amber">新创建</Pill>}
            {r.inferred && <Pill tone="violet">推断</Pill>}
          </div>
        </td>
        <td className="w-[7%] py-2">
          <RiskLevel level={r.level} />
        </td>
        <td className="w-[10%] py-2 text-center tabular text-xs">
          {r.likelihood != null && r.severity != null ? `${r.likelihood}×${r.severity}=${r.score}` : "—"}
        </td>
        <td className="w-[8%] py-2 text-xs whitespace-nowrap">{r.stage_label}</td>
        <td className="w-[9%] py-2">
          <Pill tone={INS_TONE[r.insurability] || "gray"}>{r.insurability_label}</Pill>
        </td>
        <td className="w-[22%] py-2 text-xs text-muted-foreground leading-snug break-words">
          {(r.line_names || []).length ? (r.line_names as string[]).join("、") : "—"}
        </td>
        <td className="w-[6%] py-2 pr-3 text-center text-xs tabular">{(r.evidence_ids || []).length}</td>
      </tr>
      {open && (
        <tr className="border-b bg-muted/20">
          <td />
          <td colSpan={8} className="py-3 pr-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-2 text-sm">
                <p>{r.description}</p>
                <KV
                  cols={2}
                  items={items}
                />
              </div>
              <div className="space-y-2">
                <div className="text-xs font-medium">证据卡片</div>
                {(r.evidence_ids || []).slice(0, 6).map((id: string) => {
                  const e = evidence[id];
                  if (!e) return null;
                  return (
                    <div key={id} className="rounded-lg border bg-card p-2 text-xs">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[10px] text-muted-foreground">{id}</span>
                        <span className="font-medium">{e.source_name}</span>
                        <DataLevel level={e.level} />
                        <ZoneBadge zone={e.zone} />
                        <span className="ml-auto text-muted-foreground">置信度 {e.confidence}</span>
                      </div>
                      <div className="mt-1 text-muted-foreground">{e.text}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function RiskReportView({ data }: { data: any }) {
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<string[] | null>(null);
  const risks: any[] = data.risks || [];
  const shown = filter ? risks.filter((r) => filter.includes(r.id)) : risks;
  const graph = useMemo(() => {
  const g = data.graph || { nodes: [], edges: [] };
  const nodes = Array.isArray(g.nodes) ? g.nodes : [];
  const edges = Array.isArray(g.edges) ? g.edges : [];
  return { nodes: nodes.map((n: any) => ({ id: n.id, label: n.label, type: n.type, size: n.type === "风险" ? 7 + (n.level || 0) : n.type === "企业" ? 12 : 8 })), edges: edges.map((e: any) => ({ source: e.source, target: e.target, label: e.rel })) };
  }, [data.graph]);
  const development = Boolean(data.scene_summary);
  const coverage: any[] = data.coverage || [];
  const identifiedTypes = coverage.filter((c) => c.status === "identified").length;
  const hasCoverage = coverage.length > 0;
  return (
    <div className="space-y-4">
      {development && (
        <Section title="场景与需求">
          <p className="text-sm leading-relaxed">{data.scene_summary}</p>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            {(data.demand_items || []).map((item: any, index: number) => (
              <div key={index} className="rounded-lg border p-3 text-sm">
                <div className="text-xs text-muted-foreground">{item.status}</div>
                <div className="mt-1">{item.text}</div>
              </div>
            ))}
          </div>
        </Section>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <StatCard label="识别风险" value={data.stats?.total} hint={`覆盖 ${data.stats?.types_covered ?? identifiedTypes}${hasCoverage ? ` / ${coverage.length}` : ""} 个一级类型${data.stats?.novel ? `，新创建 ${data.stats.novel} 条` : ""}`} />
          <StatCard label="高及以上" value={data.stats?.high} tone="danger" hint={`总体风险 ${data.stats?.overall_level}`} />
          <StatCard label="研发阶段" value={data.stats?.stage_counts?.rd} />
          <StatCard label="成果转化" value={data.stats?.stage_counts?.transfer} />
          <StatCard label="产业化" value={data.stats?.stage_counts?.industrial} />
        </div>
      <Section title="评估结论">
        <p className="text-sm leading-relaxed">{data.summary}</p>
        {(data.highlights || []).length > 0 && (
          <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
            {data.highlights.map((h: string, i: number) => (
              <li key={i}>· {h}</li>
            ))}
          </ul>
        )}
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-lg bg-muted/50 p-3 text-xs">
            <div className="mb-1 font-medium">管理重点</div>
            {(data.management_focus || []).map((m: string) => (
              <div key={m}>· {m}</div>
            ))}
          </div>
          <div className="rounded-lg bg-amber-500/5 p-3 text-xs">
            <div className="mb-1 font-medium">不确定性与信息缺口</div>
            {[...(data.uncertainties || []), ...(data.gaps || [])].map((m: string, i: number) => (
              <div key={i}>· {m}</div>
            ))}
          </div>
        </div>
      </Section>

      <Tabs defaultValue="list">
        <TabsList>
          <TabsTrigger value="list">风险清单</TabsTrigger>
          <TabsTrigger value="matrix">风险矩阵与类型覆盖</TabsTrigger>
          <TabsTrigger value="lifecycle">生命周期</TabsTrigger>
          <TabsTrigger value="graph">风险图谱</TabsTrigger>
        </TabsList>
        <TabsContent value="list" className="mt-3">
          <Section contentClassName="p-0" title={filter ? `筛选：${filter.length} 条` : `全部 ${risks.length} 条风险`} actions={filter && <button className="text-xs text-primary" onClick={() => setFilter(null)}>清除筛选</button>}>
            <div className="overflow-x-auto">
              <table className="w-full table-fixed text-sm">
                <colgroup>
                  <col className="w-[3%]" />
                  <col className="w-[7%]" />
                  <col className="w-[28%]" />
                  <col className="w-[7%]" />
                  <col className="w-[10%]" />
                  <col className="w-[8%]" />
                  <col className="w-[9%]" />
                  <col className="w-[22%]" />
                  <col className="w-[6%]" />
                </colgroup>
                <thead className="border-y bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pl-3" />
                    <th className="py-2">编号</th>
                    <th className="py-2">风险</th>
                    <th className="py-2">等级</th>
                    <th className="py-2 text-center">可能性×严重性<span className="block font-normal">排序分数，不是发生概率</span></th>
                    <th className="py-2">阶段</th>
                    <th className="py-2">可保性</th>
                    <th className="py-2">险种</th>
                    <th className="py-2 pr-3 text-center">证据</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <RiskRow key={r.id} r={r} evidence={data.evidence || {}} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} />
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </TabsContent>
        <TabsContent value="matrix" className="mt-3">
          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="风险热力图" description="点击格子筛选风险清单">
              <Heatmap
                grid={data.heatmap}
                onCell={(ids) => {
                  if (ids.length) setFilter(ids);
                }}
              />
            </Section>
            <Section title="一级风险类型覆盖" description={hasCoverage ? `共 ${coverage.length} 个一级类型，已识别 ${identifiedTypes} 个${data.stats?.novel ? `，另新创建 ${data.stats.novel} 条` : ""}` : "本报告未附带类型覆盖明细"}>
              <div className="space-y-1.5">
                {!hasCoverage && <div className="text-sm text-muted-foreground">重新运行风险评估后，会按全部一级风险类型逐项给出结论。</div>}
                {coverage.map((c: any) => (
                  <div key={c.code} className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-mono text-xs text-muted-foreground">{c.code}</span>
                    <span className="flex-1">{c.name}</span>
                    <Pill tone={c.status === "identified" ? "blue" : c.status === "inferred" ? "violet" : "gray"}>{c.label}</Pill>
                  </div>
                ))}
                {data.stats?.novel > 0 && (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-mono text-xs text-muted-foreground">NX</span>
                    <span className="flex-1">新创建风险</span>
                    <Pill tone="amber">新创建 {data.stats.novel} 条</Pill>
                  </div>
                )}
              </div>
            </Section>
          </div>
        </TabsContent>
        <TabsContent value="lifecycle" className="mt-3 space-y-3">
          {(data.lifecycle || []).map((p: any) => (
            <Section key={p.name} title={`${p.name}（TRL ${p.trl}）`} description={(p.basis || []).join("；")}>
              <div className="flex gap-1 overflow-x-auto pb-1">
                {p.phases.map((ph: any) => (
                  <div key={ph.code} className={cn("min-w-[110px] flex-1 rounded-lg border p-2", ph.is_current ? "border-primary bg-primary/5" : ph.is_past ? "bg-muted/40 opacity-70" : "")}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium">{ph.name}</span>
                      <span className="text-[10px] text-muted-foreground">{STAGE_NAMES[ph.stage]}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(ph.risk_ids || []).slice(0, 6).map((id: string) => (
                        <span key={id} className="rounded bg-rose-500/10 px-1 font-mono text-[10px] text-rose-700">
                          {id}
                        </span>
                      ))}
                      {(ph.forward || []).slice(0, 4).map((f: any, i: number) => (
                        <span key={i} className="rounded bg-sky-500/10 px-1 text-[10px] text-sky-700">
                          前瞻：{f.title || f}
                        </span>
                      ))}
                    </div>
                    {ph.is_current && <div className="mt-1 text-[10px] font-medium text-primary">当前阶段</div>}
                  </div>
                ))}
              </div>
            </Section>
          ))}
        </TabsContent>
        <TabsContent value="graph" className="mt-3">
          <Section title="企业风险图谱" description={`节点 ${graph.nodes.length} 个、关系 ${graph.edges.length} 条；悬停查看关联`}>
            <ForceGraph nodes={graph.nodes} edges={graph.edges} height={520} />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
