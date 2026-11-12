"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Clock } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { amount, dateTime, METHOD_NAMES, pct, relative } from "@/lib/format";
import { AlertLevel, ErrorBlock, KV, PageHeader, PageLoading, Pill, Section } from "@/components/common";
import { TimeSeries } from "@/components/charts";
import { HitlCardView } from "@/components/hitl";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

const ACTIONS = [
  ["contact", "联系客户核实"],
  ["advice", "建议防损措施"],
  ["service", "安排风险减量服务"],
  ["notice", "发送风险提示函"],
  ["adjust", "调整监测频率或阈值"],
  ["escalate", "升级等级"],
  ["incident", "启动事故响应"],
  ["false_positive", "标记误报"],
  ["close", "处置完成关闭"],
];
const FP_REASONS = ["设备维护或调试", "开机或负载切换的正常波动", "数据质量问题（断点或时钟偏差）", "已知外部事件且已处置", "阈值设置过严"];

export default function AlertPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: a, error, mutate } = useApi<any>(`/alerts/${id}`, { refreshInterval: 6000 });
  const run = useApi<any>(a?.run_id ? `/runs/${a.run_id}` : null);
  const [action, setAction] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState(FP_REASONS[0]);
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!a) return <PageLoading />;
  const an = a.analysis || {};
  const ev = a.evidence || {};
  const impact = ev.impact || {};
  const submit = async () => {
    try {
      const out = await api<any>(`/alerts/${id}/handle`, { method: "POST", json: { action, note, reason: action === "false_positive" ? reason : "" } });
      toast.success("处置已记录" + (out.incident_id ? "，已启动事故响应" : ""));
      setAction(null);
      setNote("");
      mutate();
      if (out.incident_id) router.push(`/monitoring/incidents/${out.incident_id}`);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const openCards = (run.data?.cards || []).filter((c: any) => c.status === "open");
  const closed = ["closed", "false_positive", "escalated"].includes(a.status);
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "风险预警", href: "/monitoring" }, { label: a.alert_no }]}
        title={a.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <AlertLevel level={a.level} />
            <Pill>{a.status_label}</Pill>
            <span>{a.enterprise}</span>
            <Link href={`/policies/${a.policy_id}`} className="text-primary">
              {a.policy_no}
            </Link>
            <span className="flex items-center gap-1 text-xs">
              <Clock className="size-3" /> 处置时限 {relative(a.sla_due)}
            </span>
            {a.run_id && (
              <Link href={`/runs/${a.run_id}`} className="text-xs text-primary">
                研判运行 #{a.run_id}
              </Link>
            )}
            {a.incident_id && (
              <Link href={`/monitoring/incidents/${a.incident_id}`} className="text-xs font-medium text-rose-600">
                事故响应 →
              </Link>
            )}
          </span>
        }
      />
      {openCards.map((c: any) => (
        <div key={c.id} className="mb-4">
          <HitlCardView card={c} onResolved={() => mutate()} />
        </div>
      ))}
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-4">
          <Section title="研判说明" description="大模型依据证据生成；所有陈述引用证据编号">
            <p className="text-sm leading-relaxed">{an.explanation}</p>
            <div className="mt-3 text-xs font-medium">可能原因</div>
            <ul className="mt-1 space-y-1 text-sm">
              {(an.causes || []).map((c: any, i: number) => (
                <li key={i}>
                  · {c.text}
                  {(c.evidence || []).map((e: string) => (
                    <span key={e} className="ml-1 rounded bg-muted px-1 font-mono text-[10px]">
                      {e}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          </Section>
          <Section title="触发依据">
            <div className="space-y-4">
              {(ev.signals || []).map((s: any) => (
                <div key={s.id + s.method} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="rounded bg-muted px-1.5 font-mono text-xs">{s.id}</span>
                    <Pill tone={s.method.startsWith("TP") ? "violet" : "blue"}>
                      {s.method} {METHOD_NAMES[s.method]}
                    </Pill>
                    <span className="text-xs text-muted-foreground">
                      强度 {s.strength} · 严重度 {s.severity}
                    </span>
                  </div>
                  <div className="mt-1 text-sm">{s.detail}</div>
                  {s.health && <div className="mt-1 text-xs text-muted-foreground">健康指数 {s.health.index} · 日均变化 {s.health.slope_per_day} · 剩余可用 {s.health.rul_days ?? "—"} 天</div>}
                  {s.series && Object.keys(s.series).length > 0 && <TimeSeries series={Object.values(s.series).map((x: any) => ({ name: x.name, points: x.ts.map((t: string, i: number) => [t, x.values[i]]) }))} height={170} />}
                  {s.event && (
                    <div className="mt-2 rounded-md bg-muted/50 p-2 text-xs">
                      <div className="font-medium">
                        {s.event.title} <span className="text-muted-foreground">· {s.event.subject} · {dateTime(s.event.published_at)}</span>
                      </div>
                      <div className="mt-0.5 text-muted-foreground">{s.event.content}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>
        </div>
        <div className="space-y-4">
          <Section title="影响评估">
            <KV
              cols={1}
              items={[
                { label: "风险类型", value: `${impact.risk_name || a.risk_type}（${a.risk_type}）` },
                { label: "与保险责任", value: `${impact.relation || "—"}${(impact.liabilities || []).length ? `：${impact.liabilities.join("、")}` : ""}` },
                { label: "潜在损失区间", value: impact.loss_range ? `${amount(impact.loss_range[0])} – ${amount(impact.loss_range[1])}` : "—" },
                { label: "测算依据", value: impact.loss_basis },
                { label: "生命周期影响", value: impact.lifecycle },
                { label: "融合置信度", value: `${pct(ev.fused?.confidence)}（${(ev.fused?.methods || []).join("+")}）` },
                { label: "定级依据", value: (ev.grading?.notes || []).join("；") },
                { label: "推送渠道", value: (ev.grading?.channels || []).join("、") },
              ]}
            />
          </Section>
          <Section title="建议动作">
            <div className="space-y-2">
              {(an.actions || []).map((x: any, i: number) => (
                <div key={i} className="rounded-lg bg-muted/50 p-2 text-xs">
                  <div className="font-medium">{x.action}</div>
                  <div className="text-muted-foreground">
                    {x.deadline_hours} 小时内 · {x.owner}
                  </div>
                </div>
              ))}
            </div>
          </Section>
          <Section title="处置">
            {!closed && (
              <div className="grid grid-cols-2 gap-1.5">
                {ACTIONS.map(([k, l]) => (
                  <Button key={k} size="sm" variant={k === "incident" ? "destructive" : k === "close" ? "default" : "outline"} onClick={() => setAction(k)} disabled={k === "incident" && !!a.incident_id}>
                    {l}
                  </Button>
                ))}
              </div>
            )}
            <ol className="mt-3 space-y-2 border-l pl-3">
              {(a.handling || []).map((h: any, i: number) => (
                <li key={i} className="text-xs">
                  <div className="font-medium">
                    {h.action} <span className="font-normal text-muted-foreground">· {h.by} · {dateTime(h.at)}</span>
                  </div>
                  {h.note && <div className="text-muted-foreground">{h.note}</div>}
                </li>
              ))}
            </ol>
          </Section>
        </div>
      </div>
      <Dialog open={!!action} onOpenChange={(o) => !o && setAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{ACTIONS.find((x) => x[0] === action)?.[1]}</DialogTitle>
            <DialogDescription>{action === "false_positive" ? "误报原因会回流用于检测方法误报率与阈值校准" : action === "notice" ? "风险提示函属于对外发送，确认后记录发送" : "处置记录将进入预警处置日志"}</DialogDescription>
          </DialogHeader>
          {action === "false_positive" && (
            <div className="space-y-1.5">
              {FP_REASONS.map((r) => (
                <label key={r} className="flex items-center gap-2 text-sm">
                  <input type="radio" checked={reason === r} onChange={() => setReason(r)} className="accent-[var(--primary)]" /> {r}
                </label>
              ))}
            </div>
          )}
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="处置说明" rows={3} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setAction(null)}>
              取消
            </Button>
            <Button onClick={submit}>确认</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
