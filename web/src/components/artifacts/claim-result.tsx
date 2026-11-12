"use client";

import { CheckCircle2, HelpCircle, XCircle } from "lucide-react";
import { fileUrl } from "@/lib/api";
import { amount, dateTime, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bar, KV, Pill, Section, StatCard } from "@/components/common";
import { DagGraph } from "@/components/graph";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const SCOPE_LABELS: Record<string, string> = { downtime_hours: "停机时长（小时）", capacity_loss_ratio: "产能损失比例（反事实基线）", peak_vibration: "峰值振动", baseline_current: "事故前基线电流", excess_egress_mb: "超出基线的外传量（MB）", exfil_hours: "外传持续小时", affected_records: "受影响记录数", records_range: "记录数区间", declared_interruption_hours: "申报中断时长" };
const STATEMENT_LABELS: Record<string, string> = { occurred_at: "出险时间", place: "地点", process: "经过", stated_cause: "陈述原因", objects: "受损对象", reported_at: "报案时间", claimed_amount: "报损估计", mitigation: "施救措施" };

function Concl({ v }: { v: string }) {
  const good = ["满足", "不适用", "在期内", "一致", "已履行", "已确定", "否", "100%"].includes(v);
  const bad = ["不满足", "适用", "不在期内", "不一致"].includes(v) || v.startsWith("未履行");
  return <Pill tone={good ? "green" : bad ? "red" : v === "待证实" ? "amber" : "blue"}>{v}</Pill>;
}

export function ClaimResultView({ data }: { data: any }) {
  const ev = data.evidence_analysis || {};
  const scope = data.loss_scope || {};
  const resp = data.responsibility || {};
  const liab = data.liability || {};
  const adj = data.adjustment || {};
  const tl = data.text_loss || {};
  const il = data.image_loss || {};
  const facts: Record<string, any> = Object.fromEntries((data.facts || []).map((f: any) => [f.id, f]));
  const g = data.causal_graph;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="申报损失" value={amount(tl.declared)} />
        <StatCard label="核定损失" value={amount(adj.assessed)} tone="primary" />
        <StatCard label="建议赔付" value={amount(adj.payout)} tone="success" hint={`需 ${adj.approval_level} 级审批`} />
        <StatCard label="代位求偿建议" value={amount(adj.subrogation)} hint={Object.keys(adj.subrogation_targets || {}).join("、") || "无第三方"} />
        <StatCard label="理赔欺诈概率" value={pct(ev.fraud?.probability, 1)} tone={ev.fraud?.probability > 0.3 ? "danger" : "default"} hint={(ev.fraud?.patterns || []).map((p: any) => p.code).join("、") || "未命中"} />
      </div>
      <Tabs defaultValue="clause">
        <TabsList className="flex-wrap">
          <TabsTrigger value="clause">条款核查</TabsTrigger>
          <TabsTrigger value="evidence">证据与时间线</TabsTrigger>
          <TabsTrigger value="scope">损失范围</TabsTrigger>
          <TabsTrigger value="liability">责任认定</TabsTrigger>
          <TabsTrigger value="loss">文本与图像定损</TabsTrigger>
          <TabsTrigger value="adjust">理算与赔付建议</TabsTrigger>
          <TabsTrigger value="materials">材料解析</TabsTrigger>
        </TabsList>

        <TabsContent value="clause" className="mt-3 space-y-3">
          <Section title="八项条款核查" description="私域抽取事实、脱敏后逐项推理；结论附条款原文与事实编号">
            <div className="space-y-2">
              {(data.checks || []).map((c: any) => (
                <div key={c.item} className="rounded-lg border p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{c.item}</span>
                    <Concl v={c.conclusion} />
                    <span className="ml-auto flex flex-wrap gap-1">
                      {(c.facts || []).map((f: string) => (
                        <span key={f} title={facts[f]?.text} className="cursor-help rounded bg-muted px-1 font-mono text-[10px]">
                          {f}
                        </span>
                      ))}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{c.note}</div>
                  {c.clause_ref && <div className="mt-0.5 text-[11px] text-muted-foreground/80">条款依据：{c.clause_ref}</div>}
                </div>
              ))}
            </div>
          </Section>
          {(data.clause?.docs || []).map((d: any) => (
            <Section key={d.doc_id} title={`相关保险责任：${d.name}`}>
              <div className="space-y-1.5 text-xs">
                {(d.articles || []).map((a: any) => (
                  <div key={a.no}>
                    <span className="font-medium">
                      {a.no}【{a.title}】
                    </span>
                    <span className="text-muted-foreground">{a.text}</span>
                  </div>
                ))}
              </div>
            </Section>
          ))}
        </TabsContent>

        <TabsContent value="evidence" className="mt-3 grid gap-4 lg:grid-cols-[1.2fr_1fr]">
          <Section title="时间线重建" description={`统一时钟后排序；物联网网关时钟偏差校正 ${ev.clock_skew_minutes > 0 ? "+" : ""}${ev.clock_skew_minutes} 分钟`}>
            <ol className="relative space-y-3 border-l pl-4">
              {(ev.timeline || []).map((e: any, i: number) => (
                <li key={i} className="text-xs">
                  <span className="absolute -left-1.5 mt-1 size-3 rounded-full border-2 border-background bg-primary/70" />
                  <div className="font-mono text-[11px] text-muted-foreground">{dateTime(e.time)}</div>
                  <div>{e.event}</div>
                  <div className="text-[10px] text-muted-foreground">
                    {e.source}
                    {e.fact && ` · ${e.fact}`}
                  </div>
                </li>
              ))}
            </ol>
          </Section>
          <div className="space-y-4">
            <Section title="真实性核验">
              {(ev.authenticity || []).map((a: any, i: number) => (
                <div key={i} className="flex items-start gap-2 border-b py-1.5 text-xs last:border-0">
                  {["未见异常", "一致", "已核验"].includes(a.result) ? <CheckCircle2 className="mt-0.5 size-3.5 text-emerald-600" /> : <XCircle className="mt-0.5 size-3.5 text-rose-500" />}
                  <div>
                    <div className="font-medium">{a.object}</div>
                    <div className="text-muted-foreground">
                      {a.check}：{a.result}（{a.detail}）
                    </div>
                  </div>
                </div>
              ))}
            </Section>
            <Section title="矛盾点与欺诈复核">
              {(ev.contradictions || []).length === 0 && <div className="text-xs text-muted-foreground">声明、数据与第三方报告之间未发现矛盾</div>}
              {(ev.contradictions || []).map((c: any, i: number) => (
                <div key={i} className="mb-1.5 rounded-md bg-amber-500/5 p-2 text-xs">
                  <span className="font-medium">{c.item}</span>：{c.detail}
                </div>
              ))}
              {(ev.fraud?.patterns || []).map((p: any) => (
                <div key={p.code} className="mt-1.5 rounded-md bg-rose-500/5 p-2 text-xs">
                  <span className="font-medium">
                    {p.code} {p.name}
                  </span>
                  ：{p.evidence}
                </div>
              ))}
            </Section>
          </div>
        </TabsContent>

        <TabsContent value="scope" className="mt-3">
          <Section title="多源融合定损范围" description="时间对齐 → 事故窗口识别（变点检测）→ 受影响对象映射 → 停机时长与反事实基线量化">
            <div className="grid gap-4 md:grid-cols-2">
              <KV
                cols={1}
                items={[
                  { label: "受影响对象", value: (scope.objects || []).map((o: any) => `${o.name}（${o.object}，映射为${o.mapped_to}）`).join("；") },
                  { label: "事故窗口", value: scope.window ? `${dateTime(scope.window.start)} 起${scope.window.ongoing ? "，尚未恢复" : ` 至 ${dateTime(scope.window.end)}`}` : "—" },
                  { label: "置信度", value: pct(scope.confidence) },
                  { label: "说明", value: (scope.notes || []).join("；") || "—" },
                ]}
              />
              <div className="space-y-2">
                {Object.entries(scope.metrics || {}).map(([k, v]: any) => (
                  <div key={k} className="flex justify-between rounded-md bg-muted/50 px-3 py-1.5 text-xs">
                    <span>{SCOPE_LABELS[k] || k}</span>
                    <span className="font-medium tabular">{Array.isArray(v) ? v.join("–") : typeof v === "number" && v <= 1 && k.includes("ratio") ? pct(v) : String(v ?? "—")}</span>
                  </div>
                ))}
                {(scope.sensitivity || []).map((s: any, i: number) => (
                  <div key={i} className="text-[11px] text-muted-foreground">
                    敏感性：{s.assumption} → {Object.entries(s).filter(([k]) => k !== "assumption").map(([, v]) => String(v)).join("")}
                  </div>
                ))}
              </div>
            </div>
          </Section>
        </TabsContent>

        <TabsContent value="liability" className="mt-3 space-y-4">
          <Section title={`近因：${liab.proximate_cause || "—"}`} description={`${liab.insured_event ? "属于" : "不属于"}保险事故 · 置信度 ${pct(liab.confidence)}`} actions={<Pill tone={liab.insured_event ? "green" : "red"}>{liab.insured_event ? "保险事故" : "非保险事故"}</Pill>}>
            <div className="mb-2 flex flex-wrap items-center gap-1 text-xs">
              {(liab.chain || []).map((c: string, i: number) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-muted-foreground">→</span>}
                  <span className="rounded bg-muted px-1.5 py-0.5">{c}</span>
                </span>
              ))}
            </div>
            <ul className="space-y-1 text-sm">
              {(liab.reasoning || []).map((r: any, i: number) => (
                <li key={i}>
                  · {r.text}
                  {(r.facts || []).map((f: string) => (
                    <span key={f} title={facts[f]?.text} className="ml-1 cursor-help rounded bg-muted px-1 font-mono text-[10px]">
                      {f}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          </Section>
          {resp.multi_party && g && (
            <>
              <Section title="因果图" description={`以「${g.template}」模板为骨架，数据驱动校正边强度（青色为数据支持的边，虚线为非关键边）${g.confirmed ? "；关键边已由理赔人员确认" : ""}`}>
                <DagGraph nodes={g.nodes.map((n: any) => ({ id: n.id, label: n.label, kind: n.kind, sub: n.party_role }))} edges={g.edges} height={300} />
              </Section>
              <div className="grid gap-4 lg:grid-cols-2">
                <Section title="原因力与建议责任比例" description="反事实干预 → Shapley 值分摊 → 过错与合同调整 → 重抽样区间">
                  <div className="space-y-3">
                    {Object.keys(resp.shares || {}).map((p) => {
                      const iv = resp.intervals?.[p] || {};
                      return (
                        <div key={p} className="text-xs">
                          <div className="flex justify-between">
                            <span className="font-medium">
                              {p}
                              {resp.faults?.[p] && <span className="ml-1 text-muted-foreground">（{resp.faults[p]}）</span>}
                            </span>
                            <span className="tabular">
                              原因力 {pct(resp.causation_shares?.[p])} → 建议 <b>{pct(resp.shares[p])}</b>
                              <span className="ml-1 text-muted-foreground">
                                [{pct(iv.p05)}–{pct(iv.p95)}]
                              </span>
                            </span>
                          </div>
                          <div className="relative mt-1 h-2 rounded-full bg-muted">
                            <div className="absolute h-2 rounded-full bg-primary/25" style={{ left: `${(iv.p05 || 0) * 100}%`, width: `${Math.max(1, ((iv.p95 || 0) - (iv.p05 || 0)) * 100)}%` }} />
                            <div className="absolute top-0 h-2 w-1 rounded bg-primary" style={{ left: `${resp.shares[p] * 100}%` }} />
                          </div>
                        </div>
                      );
                    })}
                    {(resp.adjust_notes || []).map((n: string) => (
                      <div key={n} className="text-[11px] text-amber-700">
                        {n}
                      </div>
                    ))}
                  </div>
                </Section>
                <Section title="反事实结果与敏感性">
                  <div className="space-y-1.5 text-xs">
                    {Object.entries(resp.counterfactual || {}).map(([p, v]: any) => (
                      <div key={p} className="flex justify-between border-b pb-1 last:border-0">
                        <span>若{p}按规范行事</span>
                        <span className="text-muted-foreground tabular">
                          事故概率 {pct(v.p_actual)} → {pct(v.p_counterfactual)}，损失减少 {amount(v.loss_change)}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 text-xs font-medium">关键假设敏感性（±20%）</div>
                  <div className="mt-1 space-y-1">
                    {(resp.sensitivity || []).slice(0, 6).map((s: any) => (
                      <div key={s.assumption} className="flex items-center gap-2 text-[11px]">
                        <span className="w-56 truncate">{s.assumption}</span>
                        <Bar value={s.max_change} max={0.3} tone="amber" />
                        <span className="w-10 text-right tabular">{pct(s.max_change, 1)}</span>
                      </div>
                    ))}
                  </div>
                </Section>
              </div>
              <Section title="参与方证据（子智能体整理）">
                <div className="grid gap-3 md:grid-cols-3">
                  {(data.parties || []).map((p: any) => (
                    <div key={p.name} className="rounded-lg border p-3 text-xs">
                      <div className="font-medium">{p.name}</div>
                      <div className="text-muted-foreground">
                        {p.role} · 偏离规范 {p.deviation} · {p.fault}
                        {p.cap && ` · 责任上限 ${amount(p.cap)}`}
                      </div>
                      <ul className="mt-1.5 space-y-0.5 text-muted-foreground">
                        {(p.evidence || []).slice(0, 3).map((e: any, i: number) => (
                          <li key={i}>· {e.text}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </Section>
            </>
          )}
        </TabsContent>

        <TabsContent value="loss" className="mt-3 space-y-4">
          <Section title="文本定损" description={`申报 ${amount(tl.declared)}，核定 ${amount(tl.assessed)}`} contentClassName="overflow-x-auto">
            <table className="w-full table-fixed text-xs">
              <colgroup>
                <col className="w-[18%]" />
                <col className="w-[10%]" />
                <col className="w-[12%]" />
                <col className="w-[12%]" />
                <col className="w-[10%]" />
                <col className="w-[38%]" />
              </colgroup>
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="py-2">项目</th>
                  <th>类别</th>
                  <th className="text-right">申报</th>
                  <th className="text-right">核定</th>
                  <th>状态</th>
                  <th>核定依据</th>
                </tr>
              </thead>
              <tbody>
                {(tl.items || []).map((i: any) => (
                  <tr key={i.item} className="border-b align-top last:border-0">
                    <td className="py-2 pr-2 font-medium leading-snug break-words">{i.item}</td>
                    <td className="py-2 whitespace-nowrap">{i.category}</td>
                    <td className="py-2 text-right tabular whitespace-nowrap">{amount(i.declared)}</td>
                    <td className="py-2 text-right font-medium tabular whitespace-nowrap">{amount(i.assessed)}</td>
                    <td className="py-2">
                      <Pill tone={i.status === "核定" ? "green" : i.status === "剔除" ? "red" : "amber"}>{i.status}</Pill>
                    </td>
                    <td className="py-2 leading-relaxed break-words text-muted-foreground">{i.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
          {!il.skipped && (
            <Section title="图像定损" description="私域识别损伤部位、类型、程度，判定维修或更换并与承保照片比对新旧损">
              <div className="grid gap-3 md:grid-cols-2">
                {(il.items || []).map((o: any) => (
                  <div key={o.photo} className="overflow-hidden rounded-lg border">
                    <div className="relative">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={fileUrl(`/materials/${o.material_id}/file`)} alt={o.photo} className="aspect-[4/3] w-full object-cover" />
                      <svg viewBox="0 0 640 480" className="absolute inset-0 h-full w-full">
                        {(o.regions || []).map((r: any, i: number) => (
                          <g key={i}>
                            <rect x={r.x} y={r.y} width={r.w} height={r.h} fill="none" stroke={r.type === "烧蚀" ? "#f43f5e" : r.type === "锈蚀" ? "#f59e0b" : "#0ea5e9"} strokeWidth={3} />
                            <text x={r.x + 4} y={r.y + 16} fill="white" stroke="black" strokeWidth={0.6} style={{ fontSize: 15, fontWeight: 700 }}>
                              {r.type}
                            </text>
                          </g>
                        ))}
                      </svg>
                    </div>
                    <div className="space-y-1 p-3 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{o.part}</span>
                        <Pill tone={o.level >= 3 ? "red" : o.level >= 2 ? "orange" : "amber"}>{o.grade}</Pill>
                        <Pill tone={o.action === "更换" ? "violet" : "blue"}>{o.action}</Pill>
                        {o.old_damage && <Pill tone="red">含旧损：{o.old_types.join("、")}</Pill>}
                      </div>
                      <div className="text-muted-foreground">{(o.damages || []).map((d: any) => `${d.type}${d.grade}`).join("、")} · 对应清单：{o.list_item || "未匹配"} · 置信度 {o.confidence}</div>
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}
          <Section title="交叉校验" description={data.cross_check?.text_image_diff !== null && data.cross_check?.text_image_diff !== undefined ? `文本与图像定损差异 ${pct(data.cross_check.text_image_diff)}（阈值 15%）` : undefined}>
            {(data.cross_check?.issues || []).length === 0 ? (
              <div className="flex items-center gap-2 text-sm text-emerald-700">
                <CheckCircle2 className="size-4" /> 文本与图像定损一致，损失范围与金额相互印证
              </div>
            ) : (
              <div className="space-y-1.5">
                {data.cross_check.issues.map((i: any, k: number) => (
                  <div key={k} className="flex gap-2 rounded-md bg-amber-500/5 p-2 text-xs">
                    <HelpCircle className="size-3.5 text-amber-600" />
                    <span>
                      <b>{i.type}</b>：{i.detail}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </TabsContent>

        <TabsContent value="adjust" className="mt-3 grid gap-4 lg:grid-cols-[1.3fr_1fr]">
          <Section title="理算书" description="核定损失 → 残值 → 不足额比例 → 免赔 → 自担 → 分项与累计限额 → 施救费用">
            <ol className="space-y-1.5">
              {(adj.steps || []).map((s: any, i: number) => (
                <li key={i} className={cn("flex items-start gap-3 rounded-md px-2 py-1.5 text-xs", s.step.includes("建议赔付") && "bg-emerald-500/10 font-medium")}>
                  <span className="w-5 shrink-0 text-muted-foreground tabular">{i + 1}</span>
                  <span className="flex-1">
                    <div>{s.step}</div>
                    <div className="text-muted-foreground">{s.basis}</div>
                  </span>
                  <span className="shrink-0 font-medium tabular">{amount(s.amount)}</span>
                </li>
              ))}
            </ol>
          </Section>
          <Section title="赔付建议">
            <KV
              cols={1}
              items={[
                { label: "建议赔付金额", value: <span className="text-lg font-semibold text-emerald-700">{amount(adj.payout)}</span> },
                { label: "支付对象 / 方式", value: `${adj.payee || "—"} · ${adj.method || "—"}` },
                { label: "代位求偿建议", value: adj.subrogation ? `${amount(adj.subrogation)}（${Object.entries(adj.subrogation_targets || {}).map(([k, v]: any) => `${k} ${pct(v)}`).join("、")}）` : "无" },
                { label: "审批等级", value: `${adj.approval_level} 级` },
                { label: "拒赔或部分拒赔", value: (adj.rejected || []).map((r: any) => r.item).join("、") || "无" },
              ]}
            />
            <div className="mt-4 space-y-2 border-t pt-3 text-sm leading-relaxed">
              {(data.opinion?.paragraphs || []).map((p: string, i: number) => (
                <p key={i} className="text-xs">
                  {p}
                </p>
              ))}
            </div>
          </Section>
        </TabsContent>

        <TabsContent value="materials" className="mt-3 grid gap-3 lg:grid-cols-2">
          <Section title="材料清单核对">
            {(data.material_check || []).map((m: any) => (
              <div key={m.material} className="flex items-center justify-between border-b py-1 text-xs last:border-0">
                <span>
                  {m.material} {!m.required && <span className="text-muted-foreground">（可选）</span>}
                </span>
                {m.status === "provided" ? <span className="text-emerald-700">✓ {m.file}</span> : <span className={m.required ? "text-rose-600" : "text-muted-foreground"}>缺失</span>}
              </div>
            ))}
          </Section>
          <Section title="声明要点">
            <KV
              cols={1}
              items={Object.entries(data.parsed?.statement || {})
                .filter(([k]) => k !== "material_id")
                .map(([k, v]: any) => ({ label: STATEMENT_LABELS[k] || k, value: Array.isArray(v) ? v.join("；") : typeof v === "number" ? amount(v) : String(v || "—") }))}
            />
          </Section>
          {Object.entries(data.parsed?.docs || {}).map(([k, d]: any) => (
            <Section key={k} title={d.name} actions={<Pill tone={d.confidence >= 0.9 ? "green" : "amber"}>{d.label} · {d.confidence}</Pill>}>
              <KV cols={2} items={Object.entries(d.fields).slice(0, 8).map(([kk, vv]: any) => ({ label: kk, value: vv }))} />
            </Section>
          ))}
        </TabsContent>
      </Tabs>
    </div>
  );
}
