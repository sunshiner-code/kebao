"use client";

import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { amount, LINE_NAMES, pct } from "@/lib/format";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { KV, Pill, RiskLevel, Section } from "@/components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const ORIGIN: Record<string, { label: string; tone: "gray" | "blue" | "teal" | "violet" | "amber" | "orange" | "green" }> = {
  module: { label: "复用", tone: "gray" },
  generated: { label: "生成", tone: "gray" },
  personalized: { label: "个性化", tone: "teal" },
  extension: { label: "扩展", tone: "blue" },
  innovation: { label: "创新", tone: "violet" },
  fix: { label: "合规整改", tone: "amber" },
  consistency: { label: "衔接", tone: "orange" },
  params: { label: "参数", tone: "green" },
  revision: { label: "修订", tone: "orange" },
};
const PATH: Record<string, string> = { reuse: "复用", extend: "改造", innovate: "创新" };

const STRUCTURE_LABEL: Record<string, string> = {
  restructured: "重组新主险",
  package: "组合包",
  attach: "主险+附加险",
  single: "单险种",
  innovative: "创新险种",
  单险种: "单险种",
  组合包: "组合包",
  "主险+附加险": "主险+附加险",
  重组新主险: "重组新主险",
  创新险种: "创新险种",
};

function DocViewer({ doc, showDesignMarks = true }: { doc: any; showDesignMarks?: boolean }) {
  return (
    <div className="rounded-xl border bg-card">
      <div className="border-b px-5 py-4 text-center">
        <div className="text-lg font-semibold">{doc.name}</div>
        <div className="mt-1 flex flex-wrap justify-center gap-1.5">
          {(doc.lines || []).map((l: string) => {
            const isRider = (doc.riders || []).includes(l);
            return (
              <Pill key={l} tone={isRider ? "orange" : "blue"}>
                {(LINE_NAMES[l] || doc.name || l) + (isRider ? "（附加）" : "")}
              </Pill>
            );
          })}
          <Pill>{STRUCTURE_LABEL[doc.structure as string] || doc.structure || "条款"}</Pill>
        </div>
      </div>
      <div className="max-h-[680px] space-y-5 overflow-y-auto px-6 py-5">
        {(doc.chapters || []).map((ch: any) => (
          <div key={ch.key}>
            <div className="mb-2 text-center text-sm font-semibold">{ch.title}</div>
            <div className="space-y-2">
              {(ch.articles || []).map((a: any, i: number) => (
                <div key={i} className="text-sm leading-relaxed">
                  <span className="font-semibold">{a.no}</span>
                  {a.title && <span className="font-medium">【{a.title}】</span>}
                  <span className={cn(a.bold && "font-semibold")}>{a.text}</span>
                  {showDesignMarks && (
                    <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
                      {ORIGIN[a.origin] && a.origin !== "module" && a.origin !== "generated" && <Pill tone={ORIGIN[a.origin].tone}>{ORIGIN[a.origin].label}</Pill>}
                      {(a.risk_ids || []).map((r: string) => (
                        <span key={r} className="font-mono text-[10px] text-rose-600">
                          {r}
                        </span>
                      ))}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ClausePlanView({ data, projectId, onChanged }: { data: any; projectId?: number | null; onChanged?: () => void }) {
  const docs: any[] = data.docs || [];
  const [docId, setDocId] = useState(docs[0]?.doc_id);
  const doc = docs.find((d) => d.doc_id === docId) || docs[0];
  const rec = data.recommendation || {};
  const hasRecommendation =
    Boolean(rec.summary) ||
    (rec.structure?.products || []).length > 0 ||
    (rec.items || []).length > 0;
  const showDesignMarks = Boolean(projectId);
  const comp = (data.compliance?.rules || []).find((r: any) => r.doc_id === doc?.doc_id);
  const rules = data.product_rules;
  const hasRules = Boolean(rules && (rules.applicable_scope || rules.liability || rules.period || rules.billing_base || rules.deductible != null));
  const [billing, setBilling] = useState(String(rules?.billing_amount || ""));
  const [saving, setSaving] = useState(false);
  const saveBilling = async () => {
    if (!projectId) return;
    setSaving(true);
    try {
      await api(`/design-projects/${projectId}/rules/billing`, { method: "POST", json: { billing_base: rules?.billing_base || "累计限额", billing_amount: Number(billing) } });
      toast.success("产品规则已更新，条款和费率已按新基数重算");
      onChanged?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="space-y-4">
      {hasRules && (
        <Section title="产品规则" description={rules.source === "直接指定" ? "责任来自直接指定" : "由圈定结果确认"}>
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-sm">
              <colgroup>
                <col className="w-[18%]" />
                <col className="w-[82%]" />
              </colgroup>
              <tbody>
                {[
                  ["适用范围", rules.applicable_scope],
                  ["保险责任", (rules.liability || []).join("；")],
                  ["保险期间", rules.period],
                  ["计费基数", `${rules.billing_base || "—"} · ${rules.billing_amount || "—"}`],
                  ["免赔额", rules.deductible],
                  ["免赔率", rules.deductible_rate],
                ].map(([label, value]) => (
                  <tr key={String(label)} className="border-b">
                    <th className="py-2 pr-3 text-left align-top text-xs font-medium text-muted-foreground whitespace-nowrap">{label}</th>
                    <td className="py-2 break-words leading-relaxed">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.compliance_report && (
            <div className="mt-3 text-sm">
              <div className="font-medium">合规性分析 · {data.compliance_report.review_status}</div>
              <div className="text-muted-foreground">{data.compliance_passed ? "已通过系统校验，尚未报批或备案。" : "尚未通过，不能交付。"}</div>
            </div>
          )}
          {data.clause_docx_name && data.compliance_passed && <div className="mt-2 text-xs text-muted-foreground">Word：{data.clause_docx_name}</div>}
          {projectId && (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <label className="text-xs text-muted-foreground">
                调整计费基数
                <input value={billing} onChange={(event) => setBilling(event.target.value)} className="mt-1 block h-8 w-40 rounded-lg border bg-background px-2 text-sm" />
              </label>
              <button type="button" onClick={saveBilling} disabled={saving} className="h-8 rounded-lg bg-primary px-3 text-xs text-primary-foreground">
                {saving ? "重算中" : "保存并重算"}
              </button>
            </div>
          )}
        </Section>
      )}
      {hasRecommendation && (
        <Section
          title="产品推荐方案"
          description={rec.summary}
          actions={
            rec.main_count != null ? (
              <div className="flex items-center gap-1">
                <Pill tone="blue">主险 {rec.main_count}</Pill>
                <Pill tone={rec.rider_count ? "orange" : "gray"}>附加险 {rec.rider_count ?? 0}</Pill>
              </div>
            ) : undefined
          }
        >
          {rec.consolidation && (
            <div className="mb-3 rounded-lg border border-teal-200 bg-teal-50/60 px-3 py-2 text-xs leading-relaxed">
              <span className="font-medium">组合合并建议：</span>
              {rec.consolidation.note}
            </div>
          )}
          {!rec.consolidation && rec.structure_advice && (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs leading-relaxed">
              <span className="font-medium">结构提示：</span>
              {rec.structure_advice}
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-3">
            {(rec.structure?.products || []).map((p: any) => (
              <div key={p.name} className="rounded-lg bg-muted/50 p-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{p.product_name || p.name}</span>
                  <Pill tone={p.structure === "innovative" ? "violet" : p.structure === "restructured" ? "teal" : p.structure === "attach" ? "orange" : "blue"}>{STRUCTURE_LABEL[p.structure as string] || p.structure}</Pill>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{p.note}</div>
                <div className="mt-1.5 space-y-1">
                  {(p.lines || []).map((l: string) => {
                    const isRider = (p.riders || []).includes(l);
                    const roleText = isRider ? "附加险" : p.structure === "restructured" ? "并入" : p.structure === "innovative" ? "新主险" : "主险";
                    return (
                      <div key={l} className={cn("flex items-center gap-1.5", isRider && "pl-4")}>
                        <Pill tone={isRider ? "orange" : "blue"}>{LINE_NAMES[l] || p.name || l}</Pill>
                        <span className="text-[11px] text-muted-foreground">{roleText}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          {(rec.items || []).length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full table-fixed text-sm">
                <colgroup>
                  <col className="w-[20%]" />
                  <col className="w-[14%]" />
                  <col className="w-[10%]" />
                  <col className="w-[42%]" />
                  <col className="w-[14%]" />
                </colgroup>
                <thead className="border-b text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2">险种</th>
                    <th>角色</th>
                    <th>路径</th>
                    <th>覆盖风险</th>
                    <th className="text-right">预估保费</th>
                  </tr>
                </thead>
                <tbody>
                  {(rec.items || []).map((it: any) => (
                    <tr key={it.line} className="border-b align-top last:border-0">
                      <td className={cn("py-2 font-medium leading-snug break-words", it.role === "附加险" && "pl-4")}>{it.name}</td>
                      <td className="py-2 align-top">
                        <Pill tone={it.role === "附加险" ? "orange" : "blue"}>{it.role}</Pill>
                        {it.attached_to_name && <div className="mt-1 text-[11px] leading-snug text-muted-foreground">附加于「{it.attached_to_name}」</div>}
                      </td>
                      <td className="py-2">
                        <Pill tone={it.path === "innovate" ? "violet" : it.path === "extend" ? "blue" : "gray"}>{PATH[it.path] || it.path}</Pill>
                      </td>
                      <td className="py-2 text-xs leading-relaxed break-words">
                        {(it.risks || []).map((r: any) => (
                          <span key={r.id} className="mr-2 inline">
                            {r.title}
                            <span className="ml-0.5 text-muted-foreground">({r.level})</span>
                          </span>
                        ))}
                      </td>
                      <td className="py-2 text-right tabular whitespace-nowrap">{amount(it.premium_estimate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      <Tabs defaultValue="docs">
        <TabsList className="h-auto w-full flex-wrap">
          <TabsTrigger value="docs" className="flex-none">条款文本</TabsTrigger>
          <TabsTrigger value="tiers" className="flex-none">三档方案</TabsTrigger>
          <TabsTrigger value="compliance" className="flex-none">合规审查</TabsTrigger>
          <TabsTrigger value="matrix" className="flex-none">风险覆盖矩阵</TabsTrigger>
          <TabsTrigger value="explain" className="flex-none">通俗解读与核保要点</TabsTrigger>
        </TabsList>
        <TabsContent value="docs" className="mt-3">
          <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
            <div className="space-y-1.5">
              {docs.map((d) => (
                <button key={d.doc_id} onClick={() => setDocId(d.doc_id)} className={cn("w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors", d.doc_id === doc?.doc_id ? "border-primary bg-primary/5" : "bg-card hover:bg-muted/50")}>
                  <div className="font-medium">{d.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {d.doc_id} · {(d.chapters || []).reduce((s: number, c: any) => s + (c.articles || []).length, 0)} 条
                    {(d.innovations || []).length > 0 && " · 含创新条款"}
                  </div>
                </button>
              ))}
              {doc && (
                <div className="rounded-lg border bg-card p-3 text-xs">
                  <div className="mb-1 font-medium">参数</div>
                  <KV
                    cols={1}
                    items={[
                      { label: "免赔额", value: amount(doc.params?.deductible) },
                      { label: "累计赔偿限额", value: amount(doc.params?.limit) },
                      { label: "自担比例", value: pct(doc.params?.coinsurance) },
                      { label: "等待期 / 赔偿期", value: `${doc.params?.waiting_days ?? "—"} 天 / ${doc.params?.indemnity_months ?? "—"} 个月` },
                    ]}
                  />
                  {doc.personalization_rationale && <div className="mt-2 text-muted-foreground">{doc.personalization_rationale}</div>}
                </div>
              )}
            </div>
            {doc && <DocViewer doc={doc} showDesignMarks={showDesignMarks} />}
          </div>
        </TabsContent>
        <TabsContent value="tiers" className="mt-3">
          <div className="grid gap-3 lg:grid-cols-3">
            {Object.entries(data.tiers || {}).map(([k, t]: any) => (
              <Section key={k} title={`${t.label}档`} className={k === "standard" ? "ring-2 ring-primary/40" : ""}>
                <div className="space-y-2">
                  {t.lines.map((l: any) => (
                    <div key={l.line} className="rounded-lg bg-muted/50 p-2 text-xs">
                      <div className="font-medium">{l.name}</div>
                      <div className="mt-0.5 text-muted-foreground">
                        免赔 {amount(l.params.deductible)} · 限额 {amount(l.params.limit)} · 自担 {pct(l.params.coinsurance)}
                      </div>
                    </div>
                  ))}
                </div>
              </Section>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="compliance" className="mt-3 space-y-4">
          {(data.compliance?.issues || []).length > 0 && (
            <Section
              title="合规审查意见"
              description={`共 ${(data.compliance?.issues || []).length} 项；严重未整改项会阻断产品提交审批`}
            >
              <div className="space-y-2">
                {(data.compliance?.issues || []).map((issue: any, idx: number) => {
                  const high = issue.severity === "high" || issue.severity === "critical";
                  const resolved = Boolean(issue.resolved);
                  return (
                    <div key={idx} className={cn("rounded-lg border p-3 text-sm", high && !resolved ? "border-rose-300 bg-rose-50/60" : "bg-card")}>
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={resolved ? "green" : high ? "red" : issue.severity === "medium" ? "orange" : "gray"}>
                          {resolved ? "已整改" : high ? "严重" : issue.severity === "medium" ? "中等" : "一般"}
                        </Pill>
                        {issue.article && <span className="text-xs text-muted-foreground">{issue.article}</span>}
                        {(issue.doc_name || issue.line) && (
                          <span className="text-xs text-muted-foreground truncate max-w-[60%]">
                            {issue.doc_name || issue.line}
                            {issue.doc_name && issue.line && issue.line !== issue.doc_name ? ` · ${issue.line}` : ""}
                          </span>
                        )}
                      </div>
                      <div className="mt-1.5 font-medium">{issue.issue || issue.title || "合规问题"}</div>
                      {issue.basis && <div className="mt-1 text-xs text-muted-foreground">依据：{issue.basis}</div>}
                      {issue.suggestion && <div className="mt-1 text-xs text-sky-800">建议：{issue.suggestion}</div>}
                    </div>
                  );
                })}
              </div>
            </Section>
          )}
          <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
            <div className="space-y-1.5">
              {(data.compliance?.rules || []).map((r: any) => {
                const fails = r.checks.filter((c: any) => !c.passed).length;
                return (
                  <button key={r.doc_id} onClick={() => setDocId(r.doc_id)} className={cn("flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm", r.doc_id === doc?.doc_id ? "border-primary bg-primary/5" : "bg-card")}>
                    <span className="truncate">{r.name}</span>
                    {fails ? <Pill tone="red">{fails} 项未过</Pill> : <Pill tone="green">全部通过</Pill>}
                  </button>
                );
              })}
              {(data.fix_log || []).length > 0 && (
                <div className="rounded-lg bg-amber-500/5 p-3 text-xs">
                  <div className="mb-1 font-medium">自动整改记录</div>
                  {data.fix_log.map((f: string) => (
                    <div key={f}>· {f}</div>
                  ))}
                </div>
              )}
              {!(data.compliance?.rules || []).length && !(data.compliance?.issues || []).length && (
                <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">暂无合规审查明细。请打开条款方案产物页查看，或重新组装产品包以带入合规意见。</div>
              )}
            </div>
            <Section title={`${doc?.name || ""} · 强制合规规则 C01–C16`}>
              <div className="grid gap-2 md:grid-cols-2">
                {(comp?.checks || []).map((c: any) => (
                  <div key={c.code} className="flex items-start gap-2 rounded-lg border p-2 text-xs">
                    {c.passed ? <CheckCircle2 className="mt-0.5 size-3.5 text-emerald-600" /> : <XCircle className="mt-0.5 size-3.5 text-rose-500" />}
                    <div>
                      <div className="font-medium">
                        {c.code} {c.name}
                      </div>
                      <div className="text-muted-foreground">{c.basis}</div>
                      {c.detail && <div className="text-rose-600">{c.detail}</div>}
                    </div>
                  </div>
                ))}
                {!(comp?.checks || []).length && <div className="text-xs text-muted-foreground">当前产物未附带 C01–C16 明细。</div>}
              </div>
              {(data.backtests || []).length > 0 && (
                <div className="mt-4">
                  <div className="mb-2 text-sm font-medium">创新条款理赔反推测试</div>
                  {data.backtests.map((b: any) => (
                    <div key={b.doc_id} className="mb-2 rounded-lg bg-muted/40 p-2 text-xs">
                      <div className="font-medium">{b.name}</div>
                      {b.scenarios.map((sc: any) => (
                        <div key={sc.name} className="mt-1 flex items-center gap-2">
                          {sc.passed ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : <XCircle className="size-3.5 text-rose-500" />}
                          {sc.name}（预期{sc.expect === "pay" ? "赔付" : "拒赔"}）
                          <span className="text-muted-foreground">
                            {Object.entries(sc.checks).map(([k, v]) => `${k}${v ? "✓" : "✗"}`).join(" ")}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </Section>
          </div>
        </TabsContent>
        <TabsContent value="matrix" className="mt-3">
          <Section contentClassName="overflow-x-auto">
            <table className="w-full table-fixed text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="w-[36%] py-2">风险</th>
                  <th className="w-[10%]">等级</th>
                  {docs.map((d) => (
                    <th key={d.doc_id} className="px-1 text-center" title={d.name}>
                      <span className="line-clamp-2 break-words font-normal">{d.name || d.doc_id}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data.coverage_matrix || []).map((row: any) => (
                  <tr key={row.risk_id} className="border-b align-top last:border-0">
                    <td className="py-1.5 pr-2 leading-snug break-words">{row.title}</td>
                    <td className="py-1.5">
                      <RiskLevel level={row.level} />
                    </td>
                    {docs.map((d) => {
                      const c = row.cells[d.doc_id];
                      return (
                        <td key={d.doc_id} className="px-1 text-center">
                          {c?.status === "covered" ? <span className="inline-block size-3 rounded-sm bg-emerald-500" title={(c.articles || []).join("、")} /> : c?.status === "partial" ? <span className="inline-block size-3 rounded-sm bg-amber-400" /> : <span className="inline-block size-3 rounded-sm bg-muted" />}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 text-[11px] text-muted-foreground">{docs.map((d) => `${d.doc_id}=${d.name}`).join("；")}</div>
          </Section>
        </TabsContent>
        <TabsContent value="explain" className="mt-3 grid gap-3 lg:grid-cols-2">
          {(data.explanations || []).map((e: any) => (
            <Section key={e.line} title={e.name}>
              <div className="space-y-2 text-sm">
                <p>
                  <span className="font-medium text-emerald-700">保什么：</span>
                  {e.covers}
                </p>
                <p>
                  <span className="font-medium text-rose-700">不保什么：</span>
                  {e.not_covered}
                </p>
                <p>
                  <span className="font-medium text-sky-700">怎么理赔：</span>
                  {e.how_to_claim}
                </p>
              </div>
            </Section>
          ))}
          {(data.uw_requirements || []).map((u: any) => (
            <Section key={u.doc_id} title={`核保要求：${u.name}`}>
              <div className="flex flex-wrap gap-1">
                {u.materials.map((m: string) => (
                  <Pill key={m}>{m}</Pill>
                ))}
              </div>
              <div className="mt-2 text-xs text-muted-foreground">{u.key_points.join("；")}</div>
            </Section>
          ))}
        </TabsContent>
      </Tabs>
    </div>
  );
}
