"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Banknote, FileText, Loader2, Play } from "lucide-react";
import { api, fileUrl, useApi } from "@/lib/api";
import { amount, dateTime, LINE_NAMES } from "@/lib/format";
import { DataLevel, EmptyState, ErrorBlock, KV, PageHeader, PageLoading, Pill, RunStatus, Section } from "@/components/common";
import { ClaimResultView } from "@/components/artifacts/claim-result";
import { HitlCardView } from "@/components/hitl";
import { UploadButton } from "@/components/upload";
import { Button } from "@/components/ui/button";

export default function ClaimPage() {
  const { id } = useParams<{ id: string }>();
  const { data: c, error, mutate } = useApi<any>(`/claims/${id}`, { refreshInterval: (d) => (d && d.status === "assessing" ? 3000 : 10000) });
  const run = useApi<any>(c?.run_id ? `/runs/${c.run_id}` : null, { refreshInterval: 5000 });
  const [busy, setBusy] = useState(false);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!c) return <PageLoading />;
  const req = c.required_materials || {};
  const types = Array.from(new Set(Object.values(req).flatMap((v: any) => [...v.required, ...v.optional]))) as string[];
  const provided = (c.materials_list || []).map((m: any) => m.type);
  const openCards = (run.data?.cards || []).filter((x: any) => x.status === "open");
  const act = async (label: string, path: string) => {
    setBusy(true);
    try {
      await api(path, { method: "POST" });
      toast.success(label);
      mutate();
      run.mutate();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "定损理赔", href: "/claims" }, { label: c.claim_no }]}
        title={`${c.claim_no} · ${c.enterprise}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone={c.status === "closed" ? "green" : c.status === "pending_approval" ? "amber" : "blue"}>{c.status_label}</Pill>
            <span>{c.incident_type}</span>
            <Link href={`/policies/${c.policy_id}`} className="text-primary">
              {c.policy_no}
            </Link>
            {c.incident_id && (
              <Link href={`/monitoring/incidents/${c.incident_id}`} className="text-primary">
                事故响应
              </Link>
            )}
            {c.run_id && (
              <Link href={`/runs/${c.run_id}`} className="flex items-center gap-1 text-primary">
                定损运行 #{c.run_id} <RunStatus status={c.run_status} />
              </Link>
            )}
          </span>
        }
        actions={
          <>
            {["registered", "reported"].includes(c.status) && (
              <Button onClick={() => act("定损理赔智能体已启动", `/claims/${id}/start`)} disabled={busy || !c.materials_list.length}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />} 启动智能定损
              </Button>
            )}
            {c.status === "approved" && (
              <Button onClick={() => act("支付指令已确认，案件结案并回流知识库", `/claims/${id}/pay`)} disabled={busy}>
                <Banknote className="size-4" /> 确认支付指令并结案
              </Button>
            )}
          </>
        }
      />
      {openCards.map((x: any) => (
        <div key={x.id} className="mb-4">
          <HitlCardView card={x} onResolved={() => {
            mutate();
            run.mutate();
          }} />
        </div>
      ))}
      <div className="mb-4 grid gap-4 lg:grid-cols-[1fr_1.5fr]">
        <Section title="报案信息">
          <KV
            cols={2}
            items={[
              { label: "来源", value: c.report?.source === "pre_claim" ? "风险预警预报案" : "客户报案" },
              { label: "出险时间", value: dateTime(c.report?.occurred_at) },
              { label: "申报 / 预估损失", value: amount(c.claimed_amount) },
              { label: "建议赔付", value: amount(c.suggested_amount) },
              { label: "核定赔付", value: amount(c.approved_amount) },
              { label: "参与方", value: (c.parties || []).map((p: any) => `${p.name}（${p.role}）`).join("、") },
              { label: "事故经过", value: c.report?.description, full: true },
            ]}
          />
          {c.report?.supplement_requested && <div className="mt-2 rounded-md bg-orange-500/5 p-2 text-xs text-orange-700">已发送补充材料通知：{c.report.supplement_requested.items.join("、")}</div>}
          {(c.approvals || []).length > 0 && (
            <div className="mt-3 border-t pt-2 text-xs">
              {c.approvals.map((a: any, i: number) => (
                <div key={i}>
                  {a.action === "payment" ? "支付指令确认" : `审批：${a.action}`} · {amount(a.amount)} · {dateTime(a.at)}
                  {a.deviation !== undefined && ` · 与建议偏差 ${(a.deviation * 100).toFixed(1)}%`}
                </div>
              ))}
            </div>
          )}
        </Section>
        <Section title="理赔材料" description={`已提交 ${c.materials_list.length} 份；文档与照片只在私域解析`} actions={["registered", "reported", "pending_approval"].includes(c.status) && <UploadButton ownerType="claim" ownerId={c.id} enterpriseId={c.enterprise_id} materialTypes={types.length ? types : ["其他材料"]} onUploaded={() => mutate()} label="上传理赔材料" />}>
          <div className="mb-3 space-y-1">
            {Object.entries(req).map(([k, v]: any) => (
              <div key={k} className="flex flex-wrap items-center gap-1 text-xs">
                <span className="w-24 shrink-0 text-muted-foreground">{k === "通用" ? "通用" : LINE_NAMES[k] || k}</span>
                {v.required.map((m: string) => (
                  <Pill key={m} tone={provided.some((p: string) => p.includes(m.replace("照片", "")) || m.includes(p)) ? "green" : "orange"}>
                    {m}
                  </Pill>
                ))}
              </div>
            ))}
          </div>
          {c.materials_list.length === 0 ? (
            <EmptyState title="尚未提交理赔材料" description="上传索赔申请书、事故声明、损失清单、维修报价、发票、检测报告与损坏照片等" />
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {c.materials_list.map((m: any) => (
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
      {c.result ? (
        <ClaimResultView data={c.result} />
      ) : c.status === "assessing" ? (
        <Section title="定损进行中">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> 定损理赔智能体正在解析材料、核查条款与认定责任，
            <Link href={`/runs/${c.run_id}`} className="text-primary">
              在运行台查看实时轨迹
            </Link>
          </div>
        </Section>
      ) : null}
    </div>
  );
}
