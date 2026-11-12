"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { History, Lock } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { ErrorBlock, PageHeader, PageLoading, Pill } from "@/components/common";
import { Button } from "@/components/ui/button";
import { ClaimResultView } from "./claim-result";
import { ClausePlanView } from "./clause-plan";
import { DataWindowView, MonitorPlanView, RiskMgmtReportView } from "./monitoring";
import { PricingPlanView } from "./pricing-plan";
import { RiskReportView } from "./risk-report";
import { UwReportView } from "./uw-report";

export const KIND_LABELS: Record<string, string> = { risk_report: "风险评估报告", clause_plan: "条款方案", pricing_plan: "精算定价方案", uw_report: "核保报告", monitor_plan: "监测方案", risk_mgmt_report: "风险管理报告", claim_result: "定损理赔结果", data_window: "事故数据窗口" };
const CRUMB: Record<string, { label: string; href: string }> = { risk_report: { label: "风险评估", href: "/design/risk" }, clause_plan: { label: "条款设计", href: "/design/clauses" }, pricing_plan: { label: "精算定价", href: "/design/pricing" }, uw_report: { label: "承保风控", href: "/underwriting" }, monitor_plan: { label: "保单与监测", href: "/policies" }, risk_mgmt_report: { label: "风险预警", href: "/monitoring" }, claim_result: { label: "定损理赔", href: "/claims" }, data_window: { label: "事故响应", href: "/monitoring/incidents" } };

export function ArtifactBody({ kind, data, projectId, onChanged }: { kind: string; data: any; projectId?: number | null; onChanged?: () => void }) {
  switch (kind) {
    case "risk_report":
      return <RiskReportView data={data} />;
    case "clause_plan":
      return <ClausePlanView data={data} projectId={projectId} onChanged={onChanged} />;
    case "pricing_plan":
      return <PricingPlanView data={data} projectId={projectId} onChanged={onChanged} />;
    case "uw_report":
      return <UwReportView data={data} />;
    case "claim_result":
      return <ClaimResultView data={data} />;
    case "monitor_plan":
      return <MonitorPlanView data={data} />;
    case "risk_mgmt_report":
      return <RiskMgmtReportView data={data} />;
    case "data_window":
      return <DataWindowView data={data} />;
    default:
      return <pre className="overflow-x-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(data, null, 2)}</pre>;
  }
}

export function ArtifactPage({ artifactKey }: { artifactKey: string }) {
  const [version, setVersion] = useState<number | null>(null);
  const { data, error, mutate } = useApi<any>(`/artifacts/${artifactKey}${version ? `?version=${version}` : ""}`);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!data) return <PageLoading />;
  const crumb = CRUMB[data.kind];
  const freeze = async () => {
    try {
      await api(`/artifacts/${artifactKey}/freeze?version=${data.version}`, { method: "POST" });
      toast.success("已冻结该版本");
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader
        crumbs={[crumb || { label: "产物" }, { label: data.title }]}
        title={data.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone="blue">{KIND_LABELS[data.kind] || data.kind}</Pill>
            <Pill tone={data.status === "frozen" ? "green" : "amber"}>{data.status === "frozen" ? "已冻结" : "草稿"}</Pill>
            <span>V{data.version} · {dateTime(data.created_at)}</span>
            {data.run_id && (
              <Link href={`/runs/${data.run_id}`} className="text-primary hover:underline">
                生成运行 #{data.run_id}
              </Link>
            )}
            {(data.upstream || []).map((u: any) => (
              <Link key={u.artifact_key} href={`/artifacts/${u.artifact_key}`} className="text-xs text-muted-foreground hover:text-foreground">
                上游 {u.artifact_key}@V{u.version}
              </Link>
            ))}
          </span>
        }
        actions={
          <>
            {(data.versions || []).length > 1 && (
              <div className="flex items-center gap-1 text-xs">
                <History className="size-3.5 text-muted-foreground" />
                {data.versions.map((v: any) => (
                  <button key={v.version} onClick={() => setVersion(v.version)} className={`rounded px-1.5 py-0.5 ${v.version === data.version ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                    V{v.version}
                  </button>
                ))}
              </div>
            )}
            {data.status !== "frozen" && (
              <Button size="sm" variant="outline" onClick={freeze}>
                <Lock className="size-3.5" /> 冻结版本
              </Button>
            )}
          </>
        }
      />
      {(data.revisions || []).length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
          人工修订 {data.revisions.length} 处：{data.revisions.map((r: any) => `${r.path}（${r.reason}）`).join("；")}
        </div>
      )}
      <ArtifactBody kind={data.kind} data={data.data} projectId={data.project_id} onChanged={() => mutate()} />
    </div>
  );
}
