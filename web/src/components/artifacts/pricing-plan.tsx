"use client";

import { useState } from "react";
import { CheckCircle2, Download, PenLine, XCircle } from "lucide-react";
import { toast } from "sonner";
import { amount, dateTime } from "@/lib/format";
import { api, fileUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { KV, Pill, Section, StatCard } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function rateText(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return `${(number * 100).toFixed(4)}%`;
}

export function moneyText(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return amount(number, 2);
}

function ReportBlock({ block }: { block: any }) {
  switch (block.type) {
    case "kv":
      return <KV cols={2} items={(block.items || []).map((item: any) => ({ label: item.label, value: item.value ?? "—" }))} />;
    case "table":
      return (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                {(block.columns || []).map((column: string) => (
                  <th key={column} className="py-2 pr-3 font-medium whitespace-nowrap">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(block.rows || []).map((row: any[], i: number) => (
                <tr key={i} className="border-b align-top last:border-0">
                  {row.map((cell: any, j: number) => (
                    <td key={j} className="py-2 pr-3 leading-relaxed break-words">
                      {cell ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "list":
      return (
        <ul className="space-y-1.5 text-sm">
          {(block.items || []).map((item: string) => (
            <li key={item} className="flex gap-2">
              <span className="text-muted-foreground">·</span>
              <span className="leading-relaxed">{item}</span>
            </li>
          ))}
        </ul>
      );
    case "checks":
      return (
        <div className="space-y-1.5">
          {(block.items || []).map((check: any) => (
            <div key={check.item} className="flex items-start gap-2 text-sm">
              {check.passed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" />}
              <span className="leading-relaxed">
                {check.item}
                {check.note && <span className="text-muted-foreground">（{check.note}）</span>}
              </span>
            </div>
          ))}
        </div>
      );
    case "text":
      return <p className="text-sm leading-relaxed text-muted-foreground">{block.text}</p>;
    default:
      return null;
  }
}

export function ReportSections({ sections }: { sections: any[] }) {
  return (
    <div className="space-y-6">
      {sections.map((section: any) => (
        <section key={section.id || section.title}>
          <h3 className="mb-3 border-l-2 border-primary pl-2 text-sm font-semibold">{section.title}</h3>
          <div className="space-y-3">
            {(section.blocks || []).map((block: any, i: number) => (
              <ReportBlock key={i} block={block} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function ReviewBox({ projectId, onChanged }: { projectId?: number | null; onChanged?: () => void }) {
  const { user } = useAuth();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  if (!user?.permissions?.includes("product.review1")) {
    return <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">审定签署需要精算岗权限，当前账号可查看报告、示例测算与下载 Word。</div>;
  }
  const submit = async (approve: boolean) => {
    if (!projectId) return;
    setBusy(approve ? "approve" : "reject");
    try {
      await api(`/design-projects/${projectId}/rates/review`, { method: "POST", json: { approve, comment } });
      toast.success(approve ? "已审定签署，报告版本已更新" : "已退回修改，意见已写入报告");
      setComment("");
      onChanged?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="rounded-lg border border-primary/25 bg-primary/5 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-medium">
        <PenLine className="size-4" /> 精算审定
      </div>
      <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="审定意见（退回修改时建议写明修改要求）" className="min-h-16 bg-background text-sm" />
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => submit(true)} disabled={busy !== null || !projectId}>
          {busy === "approve" ? "签署中…" : "审定通过并签署"}
        </Button>
        <Button size="sm" variant="outline" onClick={() => submit(false)} disabled={busy !== null || !projectId}>
          {busy === "reject" ? "退回中…" : "退回修改"}
        </Button>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">审定只记录意见并升级报告版本，不改变两种方法的数值；签署前报告不得用于报批。</div>
    </div>
  );
}

function WeightedExample({ projectId, scheme }: { projectId?: number | null; scheme: any }) {
  const [score, setScore] = useState(String(scheme.example_score ?? ""));
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      const out = await api(`/design-projects/${projectId}/rates/example`, { method: "POST", json: { method: "weighted", score } });
      setResult(out);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="方法一示例 · 创新积分制得分查区间" description={scheme.mapping_basis || "按报告维护的得分区间查表，代码不把得分高低自动换算成费率方向。"} actions={<Pill tone="blue">不改写正式费率</Pill>}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-muted-foreground">
          创新积分制综合得分（0–100）
          <Input type="number" min={0} max={100} step={0.1} value={score} onChange={(e) => setScore(e.target.value)} className="mt-1 w-36" />
        </label>
        <Button size="sm" onClick={run} disabled={busy || !projectId || score.trim() === ""}>
          {busy ? "测算中…" : "示例测算"}
        </Button>
      </div>
      {(scheme.bands || []).length > 0 && (
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
              {(scheme.bands || []).map((band: any) => (
                <tr key={band.label} className="border-b last:border-0">
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    {band.label}（{band.min}–{band.max}
                    {band.closed_max ? "，含上限" : ""}）
                  </td>
                  <td className="py-1.5 pr-3 tabular whitespace-nowrap">{rateText(band.pure)}</td>
                  <td className="py-1.5">{band.reference ? <Pill tone="green">参照</Pill> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result && (
        <div className="mt-4 space-y-3">
          <KV
            cols={2}
            items={[
              { label: "综合得分", value: result.example_score },
              { label: "落入区间", value: `${result.score_band?.label || "—"}（纯风险费率 ${rateText(result.score_band?.pure)}）` },
              { label: "参照区间", value: result.reference_band || "—" },
              { label: "纯风险损失率", value: rateText(result.pure_loss_ratio) },
              { label: "示例建议费率", value: <span className="font-semibold text-primary">{rateText(result.suggested_rate)}</span> },
              { label: "基准费率", value: rateText(result.benchmark_rate) },
              { label: "相对基准调整", value: result.adjustment ?? "—" },
              { label: "示例保费", value: moneyText(result.premium) },
            ]}
          />
          <div className="text-xs text-muted-foreground">基于定价方案 V{result.version} 维护的区间计算；示例结果不写回正式费率与报告。</div>
        </div>
      )}
    </Section>
  );
}

function ClassificationExample({ projectId, scheme }: { projectId?: number | null; scheme: any }) {
  const factors: any[] = scheme.factors || [];
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      const coefficients = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
      const out = await api(`/design-projects/${projectId}/rates/example`, { method: "POST", json: { method: "classification", coefficients } });
      setResult(out);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section
      title="方法二示例 · 调整因子系数"
      description={`基准风险条件：${scheme.benchmark_condition || "—"}；基准纯风险损失率 ${rateText(scheme.benchmark_pure_loss_ratio)}。留空表示沿用方案默认系数。`}
      actions={<Pill tone="blue">不改写正式费率</Pill>}
    >
      <div className="space-y-2">
        {factors.map((factor: any) => {
          const key = factor.risk_key || factor.name;
          return (
            <div key={key} className="flex flex-wrap items-center gap-2 rounded-lg border p-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{factor.name}</div>
                <div className="text-xs text-muted-foreground">{factor.condition || "—"}</div>
              </div>
              <Input
                type="number"
                step={0.01}
                min={0}
                placeholder={`默认 ${factor.coefficient}`}
                value={values[key] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                className="w-32"
              />
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={run} disabled={busy || !projectId || factors.length === 0}>
          {busy ? "测算中…" : "示例测算"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setValues({});
            setResult(null);
          }}
        >
          恢复默认系数
        </Button>
      </div>
      {result && (
        <div className="mt-4 space-y-3">
          <KV
            cols={2}
            items={[
              { label: "综合调整系数", value: result.combined_coefficient ?? "—" },
              { label: "纯风险损失率", value: rateText(result.pure_loss_ratio) },
              { label: "示例建议费率", value: <span className="font-semibold text-primary">{rateText(result.suggested_rate)}</span> },
              { label: "基准费率", value: rateText(result.benchmark_rate) },
              { label: "相对基准调整", value: result.adjustment ?? "—" },
              { label: "示例保费", value: moneyText(result.premium) },
            ]}
          />
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="py-1.5 pr-3">因子</th>
                  <th className="py-1.5 pr-3">取值条件</th>
                  <th className="py-1.5 text-right">本次系数</th>
                </tr>
              </thead>
              <tbody>
                {(result.factors || []).map((factor: any) => (
                  <tr key={factor.risk_key || factor.name} className="border-b last:border-0">
                    <td className="py-1.5 pr-3">{factor.name}</td>
                    <td className="py-1.5 pr-3 text-muted-foreground">{factor.condition || "—"}</td>
                    <td className="py-1.5 text-right tabular">{factor.coefficient}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-xs text-muted-foreground">基于定价方案 V{result.version} 的基准与因子计算；示例结果不写回正式费率与报告。</div>
        </div>
      )}
    </Section>
  );
}

function DocumentPricingView({ data, projectId, onChanged }: { data: any; projectId?: number | null; onChanged?: () => void }) {
  const schemes = data.schemes || {};
  const weighted = schemes.weighted;
  const classification = schemes.classification;
  const primary = weighted?.suggested_rate != null ? weighted : classification || {};
  const rules = data.product_rules || {};
  const report = data.actuarial_report || {};
  const sections: any[] = report.sections || [];
  const selected = data.selected_method;
  const comparison = data.comparison || {};
  const sensitivity = data.sensitivity || {};
  const [choosing, setChoosing] = useState<string | null>(null);
  const choose = async (method: string) => {
    if (!projectId) return;
    setChoosing(method);
    try {
      await api(`/design-projects/${projectId}/rates/select`, { method: "POST", json: { method } });
      toast.success("已选定该建议费率为正式毛费率");
      onChanged?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setChoosing(null);
    }
  };
  const methods = [
    { key: "weighted", scheme: weighted },
    { key: "classification", scheme: classification },
  ].filter((item) => item.scheme?.suggested_rate != null);
  const grossRate = selected ? data.components?.[0]?.rate?.gross_rate : null;
  const selectedName = methods.find((item) => item.key === selected)?.scheme?.method;
  const sensitivityExpense = rateText(Object.values(sensitivity)[0] ? (Object.values(sensitivity)[0] as any).expense_ratio : null);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="计费保额" value={moneyText(primary.billing_amount)} hint={primary.billing_base || rules.billing_base || "计费基数"} />
        <StatCard label="保险期间" value={primary.period || rules.period || "—"} hint="两种方法使用同一期间" />
        <StatCard label="免赔额 / 免赔率" value="0 / 0" hint="测算口径为无免赔" />
        <StatCard label="正式毛费率" value={selected ? rateText(grossRate) : "尚未选定"} hint={selected ? `已选定：${selectedName || selected}` : report.status || "待精算人员审定"} />
      </div>
      <Section
        title="保险费率精算报告"
        description="供精算人员审定签署；签署前不得用于报批或备案。Word 版本与页面内容一致，可下载归档。"
        actions={
          projectId && sections.length > 0 ? (
            <a
              href={fileUrl(`/design-projects/${projectId}/pricing-report.docx`)}
              className="inline-flex h-7 items-center gap-1 rounded-lg border border-border bg-background px-2.5 text-[0.8rem] font-medium hover:bg-muted"
            >
              <Download className="size-3.5" /> 下载 Word
            </a>
          ) : undefined
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Pill tone={report.signed ? "green" : report.status === "退回修改" ? "red" : "amber"}>{report.status || "待精算人员审定"}</Pill>
          <Pill tone={report.signed ? "green" : "gray"}>{report.signed ? "已签署" : "未签署"}</Pill>
          <Pill>{report.filed ? "已报批" : "未报批"}</Pill>
          {report.provisional && <Pill tone="amber">数据不足 · 数值暂定</Pill>}
          {report.reviewed_by && (
            <span className="text-xs text-muted-foreground">
              审定人 {report.reviewed_by} · {dateTime(report.reviewed_at)}
            </span>
          )}
        </div>
        {report.review_comment && (
          <div className="mb-3 rounded-lg bg-muted/50 p-2.5 text-xs">
            <span className="font-medium">审定意见：</span>
            {report.review_comment}
          </div>
        )}
        {!selected && <ReviewBox projectId={projectId} onChanged={onChanged} />}
      </Section>
      <Tabs defaultValue="report">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="report">精算报告</TabsTrigger>
          <TabsTrigger value="example">示例测算</TabsTrigger>
          <TabsTrigger value="compare">方法比较与选定</TabsTrigger>
        </TabsList>
        <TabsContent value="report" className="mt-3">
          <Section title="报告正文" description="数字全部来自确定性计算输出；与 Word 下载内容一致。">
            {sections.length > 0 ? <ReportSections sections={sections} /> : <div className="text-sm text-muted-foreground">报告缺少结构化内容，请重新运行精算定价。</div>}
          </Section>
        </TabsContent>
        <TabsContent value="example" className="mt-3 space-y-4">
          <div className="rounded-lg border border-blue-500/25 bg-blue-500/5 p-3 text-xs text-muted-foreground">
            示例测算用于讲解与复核：按报告维护的区间和因子即时计算，结果不写回正式费率，也不改变报告内容。
          </div>
          {weighted?.suggested_rate != null && <WeightedExample projectId={projectId} scheme={weighted} />}
          {classification?.suggested_rate != null && <ClassificationExample projectId={projectId} scheme={classification} />}
        </TabsContent>
        <TabsContent value="compare" className="mt-3 space-y-4">
          <Section title="两种方法对照" description="不加权合成。选择要看产品覆盖的风险条件，而不是只比较费率高低。">
            <KV
              cols={2}
              items={[
                { label: "方法一建议费率", value: rateText(comparison.weighted_rate) },
                { label: "方法二建议费率", value: rateText(comparison.classification_rate) },
                { label: "差额", value: rateText(comparison.difference) },
                { label: "合成费率", value: "不合成" },
                { label: "选择建议", value: comparison.recommendation, full: true },
              ]}
            />
            <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
              {(comparison.reasons || []).map((reason: string) => (
                <li key={reason}>· {reason}</li>
              ))}
            </ul>
          </Section>
          {Object.keys(sensitivity).length > 0 && (
            <Section title="费用率敏感性" description="只记录假设变化，不替换当前建议费率。">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="py-2 pr-3">方法</th>
                      <th className="py-2 pr-3">当前建议费率</th>
                      <th className="py-2 pr-3">费用率 {sensitivityExpense} 时</th>
                      <th className="py-2">对应保费</th>
                    </tr>
                  </thead>
                  <tbody>
                    {methods.map(({ key, scheme }) => (
                      <tr key={key} className="border-b last:border-0">
                        <td className="py-2 pr-3">{scheme.method}</td>
                        <td className="py-2 pr-3 tabular">{rateText(scheme.suggested_rate)}</td>
                        <td className="py-2 pr-3 tabular">{rateText(sensitivity[key]?.suggested_rate)}</td>
                        <td className="py-2 tabular">{moneyText(sensitivity[key]?.premium)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            {methods.map(({ key, scheme }) => (
              <Section
                key={key}
                title={scheme.method || key}
                description={selected === key ? "当前采用的正式毛费率" : "尚未选定为正式毛费率"}
                actions={
                  <div className="flex gap-1.5">
                    {selected === key && <Pill tone="green">当前采用</Pill>}
                    <Pill tone={scheme.provisional ? "amber" : "green"}>{scheme.provisional ? "暂定" : "已给出数值"}</Pill>
                  </div>
                }
              >
                <div className="grid grid-cols-2 gap-3">
                  <StatCard label="建议费率" value={rateText(scheme.suggested_rate)} />
                  <StatCard label="建议保费" value={moneyText(scheme.premium)} />
                  <StatCard label="基准费率" value={rateText(scheme.benchmark_rate)} />
                  <StatCard label="相对基准调整" value={scheme.adjustment ?? "—"} hint="与纯风险修正系数分开" />
                </div>
                <Button size="sm" variant={selected === key ? "default" : "outline"} className="mt-3" onClick={() => choose(key)} disabled={!projectId || choosing !== null}>
                  {choosing === key ? "选定中…" : selected === key ? "当前采用" : "选定此费率为正式毛费率"}
                </Button>
              </Section>
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function LegacyPricingView({ data }: { data: any }) {
  const components: any[] = data.components || [];
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-muted-foreground">
        这是旧版定价产物（多模型组件定价）。新版流程以《保险费率精算报告》为准，重新运行精算定价后将生成新版报告。
      </div>
      {components.map((component: any) => (
        <Section key={component.id} title={component.name} description={component.doc_name}>
          <KV
            cols={2}
            items={[
              { label: "毛费率", value: component.rate?.gross_rate == null ? "尚未选定" : rateText(component.rate.gross_rate) },
              { label: "纯费率", value: rateText(component.rate?.pure_rate) },
              { label: "按限额计保费", value: moneyText(component.rate?.premium_at_limit) },
              { label: "方法", value: (component.methods || []).map((m: any) => m.name).join("、") || "—" },
            ]}
          />
        </Section>
      ))}
    </div>
  );
}

export function PricingPlanView({ data, projectId, onChanged }: { data: any; projectId?: number | null; onChanged?: () => void }) {
  if (data.schemes) return <DocumentPricingView data={data} projectId={projectId} onChanged={onChanged} />;
  return <LegacyPricingView data={data} />;
}