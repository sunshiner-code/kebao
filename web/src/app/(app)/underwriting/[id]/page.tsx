"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { FileText, Loader2, Play } from "lucide-react";
import { api, fileUrl, useApi } from "@/lib/api";
import { amount, dateTime } from "@/lib/format";
import { DataLevel, EmptyState, ErrorBlock, KV, PageHeader, PageLoading, Pill, RunStatus, Section } from "@/components/common";
import { HitlCardView } from "@/components/hitl";
import { UwReportView } from "@/components/artifacts/uw-report";
import { UploadButton } from "@/components/upload";
import { Button } from "@/components/ui/button";

export default function ApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const { data: a, error, mutate } = useApi<any>(`/applications/${id}`, { refreshInterval: (d) => (d && ["underwriting", "submitted"].includes(d.status) ? 3000 : 0) });
  const run = useApi<any>(a?.uw_run_id ? `/runs/${a.uw_run_id}` : null, { refreshInterval: 4000 });
  const [busy, setBusy] = useState(false);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!a) return <PageLoading />;
  const required = Array.from(new Set((a.requirements || []).flatMap((r: any) => r.materials))) as string[];
  const provided = (a.materials || []).map((m: any) => m.type);
  const openCards = (run.data?.cards || []).filter((c: any) => c.status === "open");
  const start = async () => {
    setBusy(true);
    try {
      const out = await api<{ run_id: number }>(`/applications/${id}/underwrite`, { method: "POST" });
      toast.success(`承保风控智能体已启动（运行 #${out.run_id}）`);
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
        crumbs={[{ label: "承保风控", href: "/underwriting" }, { label: a.app_no }]}
        title={`${a.enterprise} · ${a.app_no}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone={a.status === "accepted" ? "green" : a.status === "rejected" ? "red" : "amber"}>{a.status_label}</Pill>
            <span>{a.product}</span>
            <span>· 保额 {amount(a.sum_insured)}</span>
            {a.uw_run_id && (
              <Link href={`/runs/${a.uw_run_id}`} className="flex items-center gap-1 text-primary">
                核保运行 #{a.uw_run_id} <RunStatus status={a.uw_run_status} />
              </Link>
            )}
            {a.policy_id && (
              <Link href={`/policies/${a.policy_id}`} className="text-primary hover:underline">
                已出单，查看保单
              </Link>
            )}
          </span>
        }
        actions={
          ["draft", "returned", "submitted"].includes(a.status) && !a.uw_run_id ? (
            <Button onClick={start} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />} 发起智能核保
            </Button>
          ) : undefined
        }
      />
      {openCards.map((c: any) => (
        <div key={c.id} className="mb-4">
          <HitlCardView card={c} onResolved={() => {
            mutate();
            run.mutate();
          }} />
        </div>
      ))}
      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <Section title="投保信息">
          <KV
            cols={2}
            items={[
              { label: "企业", value: <Link href={`/customers/${a.enterprise_id}`} className="text-primary">{a.enterprise}</Link> },
              { label: "方案档次", value: { basic: "基础", standard: "标准", premium: "优选" }[a.tier as string] || a.tier },
              { label: "保险期间", value: `${a.period_months} 个月` },
              { label: "中介渠道", value: a.intermediary || "直销" },
              { label: "数据接入授权", value: Object.entries(a.declared?.authorizations || {}).filter(([, v]) => v).map(([k]) => ({ iot: "物联网", log: "日志", ops: "运营" }[k])).join("、") || "未授权" },
              { label: "创建时间", value: dateTime(a.created_at) },
              { label: "投保声明", value: a.declared?.statement, full: true },
            ]}
          />
          {a.decision?.decision && (
            <div className="mt-3 rounded-lg bg-muted/50 p-3 text-xs">
              <div className="font-medium">核保决定：{{ standard: "标准承保", conditional: "附条件承保", reject: "拒保" }[a.decision.decision as string]}</div>
              <div className="text-muted-foreground">
                保费 {amount(a.decision.premium)} · {a.decision.consistent_with_agent ? "与智能体建议一致" : `智能体建议 ${a.decision.suggested}`} {a.decision.comment && `· ${a.decision.comment}`}
              </div>
            </div>
          )}
        </Section>
        <Section title="投保材料" description={`产品要求 ${required.length} 项，已提供 ${required.filter((r) => provided.includes(r)).length} 项`} actions={["draft", "returned", "submitted", "pending_decision"].includes(a.status) && <UploadButton ownerType="application" ownerId={a.id} enterpriseId={a.enterprise_id} materialTypes={required.length ? required : ["其他材料"]} onUploaded={() => mutate()} />}>
          <div className="mb-3 flex flex-wrap gap-1">
            {required.map((r) => (
              <Pill key={r} tone={provided.includes(r) ? "green" : "orange"}>
                {provided.includes(r) ? "✓" : "缺"} {r}
              </Pill>
            ))}
          </div>
          {(a.materials || []).length === 0 ? (
            <EmptyState title="尚未上传材料" description="上传投保单、营业执照、财务报表、设备清单、发票与铭牌照片等材料" />
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {a.materials.map((m: any) => (
                <div key={m.id} className="flex items-center gap-2 rounded-lg border p-2 text-xs">
                  {m.mime.startsWith("image") ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={fileUrl(`/materials/${m.id}/file`)} alt={m.name} className="size-10 rounded object-cover" />
                  ) : (
                    <FileText className="size-4 text-muted-foreground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{m.name}</div>
                    <div className="text-muted-foreground">{m.type}</div>
                  </div>
                  <DataLevel level={m.data_level} />
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>
      <div className="mt-4">
        {a.report ? (
          <UwReportView data={a.report} />
        ) : a.uw_run_id ? (
          <Section title="核保进行中">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> 承保风控智能体正在核验材料与研判欺诈风险，
              <Link href={`/runs/${a.uw_run_id}`} className="text-primary">
                在运行台查看实时轨迹
              </Link>
            </div>
          </Section>
        ) : null}
      </div>
    </div>
  );
}
