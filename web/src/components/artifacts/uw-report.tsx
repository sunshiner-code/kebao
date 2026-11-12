"use client";

import { CheckCircle2, HelpCircle, XCircle } from "lucide-react";
import { amount, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bar, KV, Pill, RiskLevel, Section } from "@/components/common";
import { RadarScore } from "@/components/charts";
import { ForceGraph } from "@/components/graph";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const DECISION_TONE: Record<string, "green" | "amber" | "orange" | "red"> = { standard: "green", conditional: "amber", manual: "orange", reject: "red" };

export function UwReportView({ data }: { data: any }) {
  const sc = data.scorecard || {};
  const fr = data.fraud || {};
  const d = data.decision || {};
  const graph = fr.graph || { nodes: [], edges: [] };
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <Section title="核保决策建议" actions={<Pill tone={DECISION_TONE[d.decision] || "gray"}>{d.label}</Pill>}>
          <div className="grid gap-4 sm:grid-cols-[150px_1fr]">
            <div className="flex flex-col items-center justify-center rounded-xl bg-muted/50 p-3">
              <div className="text-xs text-muted-foreground">风险总分</div>
              <div className="text-4xl font-semibold tabular">{sc.total?.toFixed(0)}</div>
              <RiskLevel level={sc.level} />
            </div>
            <div className="space-y-2 text-sm">
              <div>
                <span className="text-muted-foreground">理由：</span>
                {(d.reasons || []).join("；")}
              </div>
              {(d.conditions || []).length > 0 && (
                <div>
                  <div className="text-muted-foreground">附加条件：</div>
                  <ul className="mt-1 space-y-1">
                    {d.conditions.map((c: any) => (
                      <li key={c.text} className="rounded-md bg-amber-500/5 px-2 py-1 text-xs">
                        {c.text}
                        {c.loading ? <span className="ml-1 text-muted-foreground">（费率调整 {c.loading > 0 ? "+" : ""}{pct(c.loading)}）</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <KV
                cols={3}
                items={[
                  { label: "试算保费", value: <span className="font-semibold">{amount(d.premium)}</span> },
                  { label: "基础保费", value: amount(d.base_premium) },
                  { label: "审批等级", value: `${d.approval_level} 级` },
                ]}
              />
              {(d.supplements || []).length > 0 && <div className="text-xs text-orange-700">需补充材料：{d.supplements.join("、")}</div>}
            </div>
          </div>
        </Section>
        <Section title="风险评分卡" description={`按 ${sc.weights_line || ""} 险种权重汇总`}>
          <RadarScore data={(sc.subs || []).map((s: any) => ({ name: s.name, score: Math.round(s.score) }))} height={220} />
        </Section>
      </div>

      <Tabs defaultValue="fraud">
        <TabsList>
          <TabsTrigger value="fraud">反欺诈研判</TabsTrigger>
          <TabsTrigger value="consistency">真实性与一致性核验</TabsTrigger>
          <TabsTrigger value="elements">要素抽取</TabsTrigger>
          <TabsTrigger value="background">经营与技术背景</TabsTrigger>
          <TabsTrigger value="opinion">核保意见书</TabsTrigger>
        </TabsList>
        <TabsContent value="fraud" className="mt-3 grid gap-4 lg:grid-cols-[1fr_1.2fr]">
          <Section title={`融合欺诈概率 ${pct(fr.probability, 1)}`} description="专家规则、监督模型、无监督异常、关系网络、文本线索五路子模型，以逻辑回归堆叠融合">
            <div className="space-y-2">
              {Object.entries(fr.submodels || {}).map(([k, v]: any) => (
                <div key={k} className="text-xs">
                  <div className="flex justify-between">
                    <span>{k}</span>
                    <span className="tabular">{typeof v === "number" && v <= 1 ? pct(v, 1) : `${v} 条`}</span>
                  </div>
                  <Bar value={typeof v === "number" ? Math.min(1, v <= 1 ? v : v / 5) : 0} tone={v > 0.5 ? "red" : "primary"} className="mt-1" />
                </div>
              ))}
            </div>
            <div className="mt-4 space-y-2">
              <div className="text-xs font-medium">可疑模式</div>
              {(fr.patterns || []).length === 0 && <div className="text-xs text-muted-foreground">未命中可疑模式</div>}
              {(fr.patterns || []).map((p: any) => (
                <div key={p.code} className="rounded-lg border border-rose-500/20 bg-rose-500/5 p-2 text-xs">
                  <div className="font-medium">
                    {p.code} {p.name} <span className="ml-1 text-muted-foreground">置信度 {pct(p.confidence)}</span>
                  </div>
                  {p.evidence.map((e: string) => (
                    <div key={e} className="text-muted-foreground">
                      · {e}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            {fr.model_metrics && (
              <div className="mt-3 text-[11px] text-muted-foreground">
                监督模型保留集曲线下面积 {fr.model_metrics.auc}，10% 预警比例召回 {pct(fr.model_metrics.recall_at_10pct)}
              </div>
            )}
          </Section>
          <Section title="关系网络子图" description={fr.nearest_fraud ? `距最近欺诈主体 ${fr.nearest_fraud.distance} 跳` : "共用联系人、电话、地址、中介构成的关系网络"}>
            {graph.nodes.length > 1 ? (
              <ForceGraph
                nodes={graph.nodes.map((n: any) => ({ id: n.id, label: n.label, type: n.kind, highlight: n.id === "APP:current", color: n.fraud === 1 ? "#f43f5e" : undefined }))}
                edges={graph.edges}
                height={360}
              />
            ) : (
              <div className="py-12 text-center text-sm text-muted-foreground">该投保主体与历史承保主体无共用联系信息</div>
            )}
          </Section>
        </TabsContent>
        <TabsContent value="consistency" className="mt-3">
          <Section contentClassName="space-y-2">
            {(data.consistency || []).map((c: any, i: number) => (
              <div key={i} className="flex items-start gap-3 rounded-lg border p-3 text-sm">
                {c.status === "一致" ? <CheckCircle2 className="mt-0.5 size-4 text-emerald-600" /> : c.status === "不一致" ? <XCircle className="mt-0.5 size-4 text-rose-500" /> : <HelpCircle className="mt-0.5 size-4 text-muted-foreground" />}
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{c.item}</div>
                  <div className="mt-0.5 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                    {Object.entries(c.sources || {}).map(([k, v]: any) => (
                      <span key={k}>
                        {k}：{String(v)}
                      </span>
                    ))}
                  </div>
                  {c.note && <div className={cn("mt-0.5 text-xs", c.status === "不一致" ? "text-rose-600" : "text-muted-foreground")}>{c.note}</div>}
                </div>
                <Pill tone={c.status === "一致" ? "green" : c.status === "不一致" ? "red" : "gray"}>{c.status}</Pill>
              </div>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="elements" className="mt-3 grid gap-3 lg:grid-cols-2">
          {Object.entries(data.elements?.docs || {}).map(([k, doc]: any) => (
            <Section key={k} title={doc.name} actions={<Pill tone={doc.confidence >= 0.85 ? "green" : "amber"}>置信度 {doc.confidence}</Pill>}>
              <KV cols={2} items={Object.entries(doc.fields).slice(0, 10).map(([kk, vv]: any) => ({ label: kk, value: vv }))} />
              {doc.rows?.length > 0 && <div className="mt-2 text-xs text-muted-foreground">表格行 {doc.rows.length} 行</div>}
            </Section>
          ))}
          {(data.elements?.photos || []).map((p: any) => (
            <Section key={p.material_id} title={p.name} actions={p.ela?.suspicious ? <Pill tone="red">疑似篡改</Pill> : <Pill tone="green">未见篡改</Pill>}>
              <KV
                cols={2}
                items={[
                  { label: "设备 / 型号", value: `${p.equipment || "—"} / ${p.model || "—"}` },
                  { label: "铭牌序列号", value: p.serial || "未识别" },
                  { label: "拍摄时间", value: p.captured_at || "—" },
                  { label: "错误级别分析", value: `得分 ${p.ela?.score}，倍数 ${p.ela?.ratio}` },
                  { label: "现场隐患", value: (p.hazards || []).join("；") || "未发现", full: true },
                ]}
              />
            </Section>
          ))}
        </TabsContent>
        <TabsContent value="background" className="mt-3 grid gap-3 lg:grid-cols-2">
          <Section title="经营与行业背景">
            <p className="text-sm">{data.background?.summary}</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div className="rounded-lg bg-rose-500/5 p-2 text-xs">
                <div className="font-medium">关注点</div>
                {(data.background?.concerns || []).map((c: string) => (
                  <div key={c}>· {c}</div>
                ))}
              </div>
              <div className="rounded-lg bg-emerald-500/5 p-2 text-xs">
                <div className="font-medium">积极因素</div>
                {(data.background?.positives || []).map((c: string) => (
                  <div key={c}>· {c}</div>
                ))}
              </div>
            </div>
          </Section>
          <Section title={`技术风险 ${data.technical?.score}`} description={`依据：${data.technical?.source}`}>
            {(data.technical?.items || []).map((i: any) => (
              <div key={i.title} className="flex justify-between border-b py-1 text-xs last:border-0">
                <span>{i.title}</span>
                <span className="text-muted-foreground">{i.note || i.level}</span>
              </div>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="opinion" className="mt-3">
          <Section title={data.opinion?.title}>
            <div className="space-y-2 text-sm leading-relaxed">
              {(data.opinion?.paragraphs || []).map((p: string, i: number) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
