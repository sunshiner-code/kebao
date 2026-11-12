"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Loader2 } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { amount, dateTime, pct, METHOD_NAMES, formatPricingMethods, lineLabel, pricingMethodLabel } from "@/lib/format";
import { ErrorBlock, KV, PageHeader, PageLoading, Pill, Section, StatCard } from "@/components/common";
import { ClausePlanView } from "@/components/artifacts/clause-plan";
import { ReportSections, moneyText, rateText } from "@/components/artifacts/pricing-plan";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const METHOD_KEYS = ["weighted", "classification"] as const;
const METHOD_TITLE: Record<string, string> = { weighted: "方法一 · 创新积分制定价法", classification: "方法二 · 分类费率厘定法" };

function RatesTab({ pkg }: { pkg: any }) {
  const pricing = pkg.pricing || {};
  const schemes = pricing.schemes || {};
  const keys = METHOD_KEYS.filter((key) => schemes[key]);
  // 旧产品包把精算报告整体放在 actuarial.sections[0]；新产品包直接保存 actuarial_report
  const legacyReport = pkg.actuarial?.sections?.[0];
  const report = pricing.actuarial_report || (legacyReport?.sections ? legacyReport : { ...(pkg.actuarial || {}) });
  const components: any[] = pkg.components || [];
  const primary = components[0] || {};
  const rules = pkg.product_rules || {};
  const selected = pricing.selected_method;
  if (keys.length === 0) {
    const methods = components.flatMap((c: any) => c.methods || []);
    if (methods.length > 0) {
      return (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard label="计费保额" value={moneyText(rules.billing_amount ?? primary.limit ?? primary.params?.limit)} hint={rules.billing_base || "计费基数"} />
            <StatCard label="保险期间" value={rules.period || "—"} />
            <StatCard label="免赔额 / 免赔率" value="0 / 0" hint="产品规则口径" />
            <StatCard label="正式毛费率" value={rateText(primary.rate?.gross_rate)} hint={primary.rate?.band ? `浮动区间 ${rateText(primary.rate.band[0])}–${rateText(primary.rate.band[1])}` : "已选定方法"} />
          </div>
          <Section title="已选定的定价方法" description="产品包是在本次定价页面改版前组装的，仅保留了方法名称与建议费率。重新组装产品包后可查看两种方案明细、方法对照和完整精算报告。">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-3">方法</th>
                    <th className="py-2 pr-3">建议费率</th>
                    <th className="py-2">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {methods.map((m: any) => (
                    <tr key={m.name} className="border-b last:border-0">
                      <td className="py-2 pr-3 font-medium">{pricingMethodLabel(m.name)}</td>
                      <td className="py-2 pr-3 tabular">{rateText(m.suggested_rate)}</td>
                      <td className="py-2">{primary.rate?.gross_rate != null && String(m.suggested_rate) === String(primary.rate.gross_rate) ? <Pill tone="green">当前采用</Pill> : <Pill tone="gray">未采用</Pill>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {pkg.actuarial?.conclusion && <div className="mt-3 rounded-lg bg-primary/5 p-3 text-sm">{pkg.actuarial.conclusion}</div>}
          </Section>
        </div>
      );
    }
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-muted-foreground">
          这是旧版定价产物（多模型组件定价）。重新运行精算定价并组装产品包后，这里会展示创新积分制与分类费率两种方案、方法对照和保险费率精算报告。
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {components.map((c: any) => (
            <Section key={c.id || `${c.line}-${c.name}`} title={c.name} actions={<Pill tone={c.start_mode === "warm" ? "green" : "sky"}>{c.start_mode === "warm" ? "热启动" : "冷启动"}</Pill>}>
              <div className="text-3xl font-semibold text-primary tabular">{pct(c.gross_rate, 3)}</div>
              <div className="text-xs text-muted-foreground">毛费率 · 浮动区间 {pct(c.band?.[0], 3)}–{pct(c.band?.[1], 3)}</div>
              <KV
                cols={2}
                items={[
                  { label: "限额", value: amount(c.limit) },
                  { label: "免赔额", value: amount(c.deductible) },
                  { label: "纯费率", value: pct(c.pure_rate, 3) },
                  { label: "定价方法", value: formatPricingMethods(c.methods) },
                ]}
              />
              <div className="mt-2 text-[11px] text-muted-foreground">相对系数 {(c.relativities || []).filter((r: any) => !r.is_base).length} 项 · 免赔调整 {(c.deductible_factors || []).length} 档</div>
            </Section>
          ))}
        </div>
        {pkg.actuarial?.conclusion && <div className="rounded-lg bg-primary/5 p-3 text-sm">{pkg.actuarial.conclusion}</div>}
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="计费保额" value={moneyText(rules.billing_amount ?? primary.limit ?? primary.params?.limit)} hint={rules.billing_base || "计费基数"} />
        <StatCard label="保险期间" value={rules.period || "—"} hint="两种方法使用同一期间" />
        <StatCard label="免赔额 / 免赔率" value="0 / 0" hint="测算与产品规则口径一致" />
        <StatCard label="正式毛费率" value={selected ? rateText(primary.rate?.gross_rate) : "尚未选定"} hint={selected ? `已选定：${schemes[selected]?.method || selected}` : report.status || "待精算人员审定"} />
      </div>
      <Section title="两种定价方法对照" description="不加权合成。正式费率取自已选定的方法，可在设计项目的精算定价页重新选定。">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">方法</th>
                <th className="py-2 pr-3">建议费率</th>
                <th className="py-2 pr-3">建议保费</th>
                <th className="py-2 pr-3">基准费率</th>
                <th className="py-2 pr-3">相对基准调整</th>
                <th className="py-2">状态</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const scheme = schemes[key];
                return (
                  <tr key={key} className="border-b last:border-0">
                    <td className="py-2 pr-3 font-medium">{scheme.method || METHOD_TITLE[key]}</td>
                    <td className="py-2 pr-3 tabular">{rateText(scheme.suggested_rate)}</td>
                    <td className="py-2 pr-3 tabular">{moneyText(scheme.premium)}</td>
                    <td className="py-2 pr-3 tabular">{rateText(scheme.benchmark_rate)}</td>
                    <td className="py-2 pr-3">{scheme.adjustment ?? "—"}</td>
                    <td className="py-2">{selected === key ? <Pill tone="green">当前采用</Pill> : <Pill tone="gray">未采用</Pill>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>
      <div className="grid gap-3 lg:grid-cols-2">
        {keys.map((key) => {
          const scheme = schemes[key];
          const bands: any[] = scheme.bands || [];
          const factors: any[] = scheme.factors || [];
          return (
            <Section key={key} title={scheme.method || METHOD_TITLE[key]}>
              <KV
                cols={2}
                items={
                  key === "weighted"
                    ? [
                        { label: "示例综合得分", value: scheme.example_score ?? "—" },
                        { label: "得分区间", value: `${scheme.score_band?.label || "—"}（纯风险费率 ${rateText(scheme.score_band?.pure)}）` },
                        { label: "参照区间", value: scheme.reference_band || "—" },
                        { label: "权重合计", value: scheme.weight_sum ?? "—" },
                        { label: "纯风险损失率", value: rateText(scheme.pure_loss_ratio) },
                        { label: "映射依据", value: scheme.mapping_basis || "—", full: true },
                      ]
                    : [
                        { label: "基准风险条件", value: scheme.benchmark_condition || "—", full: true },
                        { label: "基准纯风险损失率", value: rateText(scheme.benchmark_pure_loss_ratio) },
                        { label: "综合调整系数", value: scheme.pure_correction ?? "—" },
                        { label: "拟合状态", value: scheme.fitted ? "已用样本拟合" : `样本 ${scheme.sample_count ?? 0} 条，使用已维护假设` , full: true },
                        { label: "纯风险损失率", value: rateText(scheme.pure_loss_ratio) },
                      ]
                }
              />
              {bands.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="border-b text-left text-muted-foreground">
                      <tr>
                        <th className="py-1.5 pr-3">得分区间</th>
                        <th className="py-1.5 pr-3">纯风险费率</th>
                        <th className="py-1.5">参照区间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bands.map((band: any) => (
                        <tr key={band.label} className="border-b last:border-0">
                          <td className="py-1.5 pr-3 whitespace-nowrap">{band.label}（{band.min}–{band.max}{band.closed_max ? "，含上限" : ""}）</td>
                          <td className="py-1.5 pr-3 tabular whitespace-nowrap">{rateText(band.pure)}</td>
                          <td className="py-1.5">{band.reference ? <Pill tone="green">参照</Pill> : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {factors.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="border-b text-left text-muted-foreground">
                      <tr>
                        <th className="py-1.5 pr-3">因子</th>
                        <th className="py-1.5 pr-3">取值条件</th>
                        <th className="py-1.5 text-right">系数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {factors.map((factor: any) => (
                        <tr key={factor.risk_key || factor.name} className="border-b last:border-0">
                          <td className="py-1.5 pr-3">{factor.name}</td>
                          <td className="py-1.5 pr-3 text-muted-foreground">{factor.condition || "—"}</td>
                          <td className="py-1.5 text-right tabular">{factor.coefficient}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          );
        })}
      </div>
      <Section
        title="保险费率精算报告"
        description="供精算人员审定签署；签署前不得用于报批或备案。报告内容与设计项目中的精算定价页一致。"
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Pill tone={report.signed ? "green" : report.status === "退回修改" ? "red" : "amber"}>{report.status || "待精算人员审定"}</Pill>
          <Pill tone={report.signed ? "green" : "gray"}>{report.signed ? "已签署" : "未签署"}</Pill>
          <Pill>{report.filed ? "已报批" : "未报批"}</Pill>
          {report.provisional && <Pill tone="amber">数据不足 · 数值暂定</Pill>}
          {report.reviewed_by && <span className="text-xs text-muted-foreground">审定人 {report.reviewed_by} · {report.reviewed_at ? dateTime(report.reviewed_at) : "—"}</span>}
        </div>
        {report.review_comment && (
          <div className="mb-3 rounded-lg bg-muted/50 p-2.5 text-xs">
            <span className="font-medium">审定意见：</span>
            {report.review_comment}
          </div>
        )}
        {(report.sections || []).length > 0 ? <ReportSections sections={report.sections} /> : <div className="text-sm text-muted-foreground">产品包未附结构化精算报告，请重新运行精算定价后组装产品包。</div>}
      </Section>
    </div>
  );
}

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const { data: p, error, mutate } = useApi<any>(`/products/${id}`);
  const { data: completeness } = useApi<any>(p && (p.status === "draft" || p.status === "rejected") ? `/products/${id}/completeness` : null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!p) return <PageLoading />;
  const pkg = p.package || {};
  const approve = async (action: string) => {
    setBusy(true);
    try {
      await api(`/products/${id}/approve`, { method: "POST", json: { action, comment } });
      toast.success(action === "approve" ? "产品已发布" : "已退回");
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "产品库", href: "/products" }, { label: p.name }]}
        title={p.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone={p.status === "published" ? "green" : p.status === "suspended" ? "red" : "violet"}>{{ published: "已发布", pending_approval: "审批中", draft: "待提交", scheduled: "待上线", suspended: "已停售", rejected: "已退回" }[p.status as string] || p.status}</Pill>
            <span>
              {p.code} · V{p.version} · {p.stage_names.join("、")} · {p.start_mode === "warm" ? "热启动" : "冷启动"}
            </span>
            {p.project_id && (
              <Link href={`/design/${p.project_id}`} className="text-primary hover:underline">
                设计项目 #{p.project_id}
              </Link>
            )}
          </span>
        }
        actions={
          <Link href={`/products/${id}/performance`}>
            <Button variant="outline">产品表现</Button>
          </Link>
        }
      />
      {(p.status === "draft" || p.status === "rejected") && (
        <Section title="提交发布审批" description="先做完整性检查（条款合规、费率、核保要求、上线时间、销售区域和停售条件），通过后进入一审、二审和终审。" className="mb-4">
          {completeness && (
            <div className="mb-3 space-y-2">
              {(completeness.checks || []).map((c: any) => (
                <div key={c.key} className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm">
                  {c.passed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <Pill tone="red">未过</Pill>}
                  <div className="min-w-0">
                    <div className="font-medium">{c.name}{c.blocking && !c.passed ? "（阻断）" : ""}</div>
                    <div className="text-xs text-muted-foreground break-words">{c.detail}</div>
                  </div>
                </div>
              ))}
              {(completeness.blocking || []).length > 0 && (
                <div className="rounded-lg border border-rose-200 bg-rose-50/70 px-3 py-2 text-xs text-rose-800">
                  当前不能提交：{(completeness.blocking || []).join("、")}。请先到「条款与方案」查看合规意见，或回到设计项目补齐费率与材料。
                </div>
              )}
            </div>
          )}
          <Button disabled={busy || (completeness && !completeness.passed)} onClick={async () => { setBusy(true); try { const out = await api<any>(`/products/${id}/submit`, { method: "POST" }); toast.success(`已提交 ${out.request_no}`); mutate(); } catch (e: any) { toast.error(e.message); } finally { setBusy(false); } }}>提交审批</Button>
        </Section>
      )}
      {p.status === "pending_approval" && (
        <Section title="审批发布" description="产品发布需要二级及以上审批权限；审批后可用于投保" className="mb-4 border-violet-300">
          <div className="flex flex-col gap-3 md:flex-row md:items-end">
            <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="审批意见" rows={2} className="flex-1" />
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => approve("reject")} disabled={busy || !comment}>
                退回
              </Button>
              <Button onClick={() => approve("approve")} disabled={busy}>
                {busy && <Loader2 className="size-3.5 animate-spin" />} 本节点通过
              </Button>
            </div>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">一审、二审由产品设计岗位处理，终审由业务管理员发布。当前账号审批等级 {user?.approval_level}。</div>
        </Section>
      )}
      <Tabs defaultValue="rates">
        <TabsList>
          <TabsTrigger value="rates">费率与精算报告</TabsTrigger>
          <TabsTrigger value="clauses">条款与方案</TabsTrigger>
          <TabsTrigger value="service">核保、监测与理赔要求</TabsTrigger>
          <TabsTrigger value="approvals">审批记录</TabsTrigger>
        </TabsList>
        <TabsContent value="rates" className="mt-3">
          <RatesTab pkg={pkg} />
        </TabsContent>
        <TabsContent value="clauses" className="mt-3">
          <ClausePlanView data={pkg} />
        </TabsContent>
        <TabsContent value="service" className="mt-3 grid gap-4 lg:grid-cols-3">
          <Section title="核保要求与材料">
            {(pkg.uw_requirements || []).map((u: any) => (
              <div key={u.doc_id || u.name} className="mb-3">
                <div className="text-sm font-medium">{u.name}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(u.materials || []).map((m: string) => (
                    <Pill key={m}>{m}</Pill>
                  ))}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{(u.key_points || []).join("；")}</div>
              </div>
            ))}
          </Section>
          <Section title="监测要点">
            {(pkg.monitoring_points || []).map((m: any, i: number) => (
              <div key={i} className="flex items-start justify-between gap-2 border-b py-1.5 text-xs last:border-0">
                <span>{m.indicator}</span>
                <span className="flex shrink-0 gap-1">
                  {(m.methods || []).map((x: string) => (
                    <Pill key={x} tone={x.startsWith("TP") ? "violet" : "blue"}>
                      {x} {METHOD_NAMES[x]}
                    </Pill>
                  ))}
                </span>
              </div>
            ))}
          </Section>
          <Section title="理赔材料清单">
            {Object.entries(pkg.claim_materials || {}).map(([k, v]: any) => {
              const docName = (pkg.docs || []).find((d: any) => (d.lines || []).includes(k))?.name;
              const compName = (pkg.components || []).find((c: any) => c.line === k)?.name;
              return (
                <div key={k} className="mb-2 text-xs">
                  <div className="font-medium">{k === "通用" ? "通用" : lineLabel(k, docName || compName)}</div>
                  <div className="text-muted-foreground">必要：{(v?.required || []).join("、") || "—"}</div>
                  <div className="text-muted-foreground/80">可选：{(v?.optional || []).join("、") || "—"}</div>
                </div>
              );
            })}
          </Section>
        </TabsContent>
        <TabsContent value="approvals" className="mt-3">
          <Section>
            {(p.approvals || []).length === 0 && <div className="text-sm text-muted-foreground">暂无审批记录</div>}
            {(p.approvals || []).map((a: any, i: number) => (
              <div key={i} className="flex items-center gap-3 border-b py-2 text-sm last:border-0">
                <CheckCircle2 className={a.action === "approve" ? "size-4 text-emerald-600" : "size-4 text-rose-500"} />
                <span>
                  {a.name}（{a.level} 级）{a.action === "approve" ? "审批通过" : "退回"}
                </span>
                <span className="text-muted-foreground">{a.comment}</span>
                <span className="ml-auto text-xs text-muted-foreground">{dateTime(a.at)}</span>
              </div>
            ))}
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
