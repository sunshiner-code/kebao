"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { CheckCircle2, Circle, Database, ExternalLink, Truck } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { amount, dateTime, relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ErrorBlock, KV, PageHeader, PageLoading, Pill, Section } from "@/components/common";
import { HitlCardView } from "@/components/hitl";
import { Button } from "@/components/ui/button";

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const { data: inc, error, mutate } = useApi<any>(`/incidents/${id}`, { refreshInterval: 5000 });
  const run = useApi<any>(inc?.run_id ? `/runs/${inc.run_id}` : null, { refreshInterval: 5000 });
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!inc) return <PageLoading />;
  const r = inc.response || {};
  const plan = r.response || {};
  const triage = r.triage || {};
  const pre = r.pre_claim;
  const openCards = (run.data?.cards || []).filter((c: any) => c.status === "open");
  const mark = async (no: number, status: string) => {
    try {
      await api(`/incidents/${id}/steps/${no}`, { method: "POST", json: { status } });
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "事故响应", href: "/monitoring/incidents" }, { label: inc.incident_no }]}
        title={`${inc.incident_type} · ${inc.enterprise}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone="orange">{inc.status_label}</Pill>
            <span>{inc.incident_no}</span>
            <span>· 发生于 {dateTime(inc.occurred_at)}</span>
            <Link href={`/policies/${inc.policy_id}`} className="text-primary">
              {inc.policy_no}
            </Link>
            {inc.alert_id && (
              <Link href={`/monitoring/alerts/${inc.alert_id}`} className="text-primary">
                来源预警
              </Link>
            )}
            {inc.run_id && (
              <Link href={`/runs/${inc.run_id}`} className="text-primary">
                响应运行 #{inc.run_id}
              </Link>
            )}
            {inc.claim_id && (
              <Link href={`/claims/${inc.claim_id}`} className="font-medium text-emerald-700">
                已移交理赔 →
              </Link>
            )}
          </span>
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
      <Section title="事故初判" className="mb-4">
        <KV
          cols={3}
          items={[
            { label: "预案", value: plan.playbook_name || "生成中" },
            { label: "可能涉及的保险责任", value: (triage.liabilities || []).join("、") || "—" },
            { label: "紧急程度", value: triage.urgency || "—" },
            { label: "事故描述", value: inc.description, full: true },
          ]}
        />
      </Section>
      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <div className="space-y-4">
          <Section title="应急预案步骤" description="带时限的处置步骤；服务人员跟踪执行状态直至响应结束">
            <ol className="space-y-2">
              {(plan.steps || []).map((s: any) => {
                const done = s.status === "done";
                const overdue = !done && new Date(s.due + "Z") < new Date();
                return (
                  <li key={s.no} className={cn("flex items-start gap-3 rounded-lg border p-3", done && "bg-emerald-500/5")}>
                    {done ? <CheckCircle2 className="mt-0.5 size-4 text-emerald-600" /> : <Circle className="mt-0.5 size-4 text-muted-foreground" />}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">
                        {s.no}. {s.title}
                      </div>
                      <div className={cn("text-xs", overdue ? "text-rose-600" : "text-muted-foreground")}>
                        {s.deadline_hours} 小时内 · 截止 {dateTime(s.due)} · 负责：{s.owner}
                        {s.updated_at && ` · ${s.by} 于 ${relative(s.updated_at)} 更新`}
                      </div>
                    </div>
                    <Button size="xs" variant={done ? "ghost" : "outline"} onClick={() => mark(s.no, done ? "pending" : "done")}>
                      {done ? "撤销" : "标记完成"}
                    </Button>
                  </li>
                );
              })}
            </ol>
          </Section>
          <Section title="减损指导（发送客户）">
            <div className="space-y-1.5 text-sm leading-relaxed">
              {(plan.guidance || []).map((g: string, i: number) => (
                <p key={i}>{g}</p>
              ))}
            </div>
            {plan.customer_notice && <div className="mt-3 rounded-lg bg-sky-500/5 p-3 text-xs">{plan.customer_notice}</div>}
          </Section>
        </div>
        <div className="space-y-4">
          <Section title="减损措施">
            {(plan.mitigation || []).map((m: any) => (
              <div key={m.measure} className="mb-2 rounded-lg border p-2.5 text-xs">
                <div className="font-medium">{m.measure}</div>
                <div className="text-muted-foreground">预期效果：{m.effect}</div>
                {m.quantified && (
                  <div className="mt-1 text-emerald-700">
                    预计减损 {amount(m.quantified.saving)}（{m.quantified.basis}）
                  </div>
                )}
              </div>
            ))}
          </Section>
          <Section title="证据保全清单">
            {(plan.evidence || []).map((e: any) => (
              <div key={e.item} className="flex items-start justify-between gap-2 border-b py-1.5 text-xs last:border-0">
                <div>
                  <div className="font-medium">{e.item}</div>
                  <div className="text-muted-foreground">{e.method}</div>
                </div>
                <Pill>{e.deadline_hours} 小时内</Pill>
              </div>
            ))}
          </Section>
          <Section title="服务商调度">
            {(r.providers || []).map((p: any) => {
              const sent = (r.dispatched || []).some((d: any) => d.id === p.id);
              return (
                <div key={p.id} className="flex items-center gap-2 border-b py-1.5 text-xs last:border-0">
                  <Truck className="size-3.5 text-muted-foreground" />
                  <span className="flex-1">
                    {p.name} <span className="text-muted-foreground">· {p.category} · {p.response_hours} 小时响应</span>
                  </span>
                  {sent ? <Pill tone="green">已通知</Pill> : <Pill>待确认</Pill>}
                </div>
              );
            })}
          </Section>
          {r.data_window && (
            <Section title="事故数据窗口（私域）">
              <div className="flex items-center gap-2 text-xs">
                <Database className="size-4 text-teal-600" />
                <span className="flex-1">
                  {r.data_window.from?.slice(0, 10)} 至 {r.data_window.to?.slice(0, 10)} · {r.data_window.series} 条序列 · {r.data_window.points} 个数据点
                </span>
                <Link href={`/artifacts/${r.data_window.artifact_key}`} className="flex items-center gap-1 text-primary">
                  查看 <ExternalLink className="size-3" />
                </Link>
              </div>
            </Section>
          )}
          {pre && (
            <Section title="预报案草稿">
              <KV
                cols={1}
                items={[
                  { label: "估损", value: `${amount(pre.estimated_loss)}（${amount(pre.loss_range?.[0])}–${amount(pre.loss_range?.[1])}）` },
                  { label: "涉及责任", value: (pre.liabilities || []).join("、") },
                  { label: "参与方", value: (pre.parties || []).map((p: any) => `${p.name}（${p.role}）`).join("、") },
                ]}
              />
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
