"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Clock, FastForward, FileBarChart, LifeBuoy, Loader2, Settings2, Siren } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { amount, date, dateTime, LINE_NAMES, pct } from "@/lib/format";
import { AgentBadge, AlertLevel, EmptyState, ErrorBlock, KV, LoadingBlock, PageHeader, PageLoading, Pill, RunStatus, ScoreBadge, Section, StatCard } from "@/components/common";
import { IndicatorTable, MonitorPlanView } from "@/components/artifacts/monitoring";
import { TelemetryCard } from "@/components/telemetry";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

function ThresholdDialog({ plan, onDone }: { plan: any; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const inds = (plan.indicators || []).filter((i: any) => ["iot", "log", "ops"].includes(i.source) && i.status !== "degraded");
  const [key, setKey] = useState(inds[0]?.key || "");
  const cur = inds.find((i: any) => i.key === key);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const submit = async () => {
    try {
      await api(`/monitor-plans/${plan.id}/threshold`, { method: "POST", json: { key, value: Number(value), reason } });
      toast.success("阈值已调整，下一个检测周期生效");
      setOpen(false);
      onDone();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Settings2 className="size-3.5" /> 调整阈值
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>调整监测阈值</DialogTitle>
            <DialogDescription>阈值调整属于强制人审事项，变更记录写入监测方案的阈值变更日志。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>指标</Label>
              <select className="h-8 w-full rounded-lg border bg-background px-2 text-sm" value={key} onChange={(e) => setKey(e.target.value)}>
                {inds.map((i: any) => (
                  <option key={i.id} value={i.key}>
                    {i.name}（当前 {i.threshold}）
                  </option>
                ))}
              </select>
              {cur && <div className="text-xs text-muted-foreground">依据：{cur.threshold_basis}</div>}
            </div>
            <div className="space-y-1.5">
              <Label>新阈值</Label>
              <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder={String(cur?.threshold ?? "")} />
            </div>
            <div className="space-y-1.5">
              <Label>调整原因</Label>
              <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例如：开机阶段振动偏高属正常，误报较多" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={submit} disabled={!value || !reason}>
              确认调整
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ClaimDialog({ policyId, onDone }: { policyId: number; onDone: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const [desc, setDesc] = useState("");
  const [at, setAt] = useState(new Date().toISOString().slice(0, 16));
  const [amt, setAmt] = useState("");
  const [type, setType] = useState("equipment_damage");
  const submit = async () => {
    try {
      const out = await api<{ claim_id: number }>(`/policies/${policyId}/claims`, { method: "POST", json: { description: desc, occurred_at: at + ":00", claimed_amount: Number(amt || 0), incident_type: type } });
      toast.success("报案已登记，请上传理赔材料后启动定损");
      setOpen(false);
      onDone(out.claim_id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <LifeBuoy className="size-3.5" /> 报案登记
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>报案登记</DialogTitle>
            <DialogDescription>登记后上传理赔材料，由定损理赔智能体完成条款核查、定损与理算。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>事故类型</Label>
                <select className="h-8 w-full rounded-lg border bg-background px-2 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="equipment_damage">关键研发设备损坏</option>
                  <option value="data_breach">数据泄露或勒索攻击</option>
                  <option value="lab_fire">实验室火灾或水浸</option>
                  <option value="product_liability">产品责任事故</option>
                  <option value="ip_infringement">知识产权侵权</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <Label>出险时间</Label>
                <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>报损估计（元）</Label>
              <Input value={amt} onChange={(e) => setAmt(e.target.value.replace(/[^\d]/g, ""))} />
            </div>
            <div className="space-y-1.5">
              <Label>事故经过</Label>
              <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={submit} disabled={!desc}>
              登记报案
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UnpublishedPlan({ policy }: { policy: any }) {
  const embedded = policy.draft_plan;
  const waiting = (policy.runs || []).find((r: any) => r.purpose === "plan" && r.status === "waiting_human");
  const run = useApi<any>(!embedded?.data && waiting ? `/runs/${waiting.id}` : null);
  const key = embedded?.artifact_key || run.data?.output?.artifact_key;
  const version = embedded?.version || run.data?.output?.version;
  const art = useApi<any>(!embedded?.data && key ? `/artifacts/${key}${version ? `?version=${version}` : ""}` : null);
  const data = embedded?.data || art.data?.data;
  const runId = embedded?.run_id || waiting?.id;
  if (!data) {
    if (waiting || key) return <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> 正在加载监测方案草案</div>;
    return <EmptyState title="监测方案尚未发布" description="出单后风险预警智能体会生成监测方案，服务人员确认后注册到常驻监测。" />;
  }
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
        监测方案尚未发布。下面是待确认草案，在运行里点「确认发布」后才会开始实时监测。
        {runId && (
          <Link href={`/runs/${runId}`} className="ml-2 text-primary hover:underline">
            打开确认运行 #{runId}
          </Link>
        )}
      </div>
      <MonitorPlanView data={data} />
    </div>
  );
}

function PolicyDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const tab = useSearchParams().get("tab") || "monitor";
  const numeric = /^\d+$/.test(id || "");
  const { data: p, error, mutate } = useApi<any>(numeric ? `/policies/${id}` : null, { refreshInterval: numeric ? 8000 : 0 });
  const [busy, setBusy] = useState<string | null>(null);
  if (!numeric) return <EmptyState title="没有这个页面" />;
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!p) return <PageLoading />;
  const plan = p.plan;
  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    try {
      const out = await fn();
      toast.success(label, { description: out?.launched ? (out.launched.length ? `发现 ${out.launched.length} 个候选事件，风险预警智能体正在研判` : "未发现新的候选事件") : undefined });
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  };
  const charts = (p.metrics || []).filter((m: any) => ["vibration_rms", "bearing_temp", "motor_current", "login_failures", "egress_mb", "milestone_delay_days"].includes(m.metric));
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "保单与监测", href: "/policies" }, { label: p.policy_no }]}
        title={`${p.enterprise} · ${p.policy_no}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone="green">有效</Pill>
            <span>{p.product}</span>
            <span>
              · {date(p.start_date)} – {date(p.end_date)}
            </span>
            {plan && (
              <span className="flex items-center gap-1 rounded-full bg-sky-500/10 px-2 py-0.5 text-xs text-sky-700">
                <Clock className="size-3" /> 监测时间 {dateTime(p.sim_now)}
              </span>
            )}
          </span>
        }
        actions={
          <>
            {plan && (
              <>
                <Button size="sm" variant="outline" onClick={() => act("推进 6 小时", () => api(`/policies/${id}/advance`, { method: "POST", json: { hours: 6 } }))} disabled={!!busy}>
                  {busy?.includes("6") ? <Loader2 className="size-3.5 animate-spin" /> : <FastForward className="size-3.5" />} 推进 6 小时
                </Button>
                <Button size="sm" variant="outline" onClick={() => act("推进 24 小时", () => api(`/policies/${id}/advance`, { method: "POST", json: { hours: 24 } }))} disabled={!!busy}>
                  {busy?.includes("24") ? <Loader2 className="size-3.5 animate-spin" /> : <FastForward className="size-3.5" />} 推进 24 小时
                </Button>
                <Button size="sm" variant="outline" onClick={() => act("已发起风险管理报告", () => api(`/policies/${id}/reports`, { method: "POST", json: { period: "month" } }))} disabled={!!busy}>
                  <FileBarChart className="size-3.5" /> 生成月度报告
                </Button>
              </>
            )}
            <ClaimDialog policyId={Number(id)} onDone={(cid) => router.push(`/claims/${cid}`)} />
          </>
        }
      />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="保费" value={amount(p.premium)} />
        <StatCard label="保险金额" value={amount(p.sum_insured)} />
        <StatCard label="监测指标" value={plan?.indicators?.length ?? 0} hint={plan ? `降级 ${(plan.indicators || []).filter((i: any) => i.status === "degraded").length} 项` : "未发布"} />
        <StatCard label="预警" value={p.alerts.length} tone={p.alerts.some((a: any) => a.level === "red") ? "danger" : "warning"} hint={`未关闭 ${p.alerts.filter((a: any) => ["new", "processing"].includes(a.status)).length}`} />
        <StatCard label="事故 / 理赔" value={`${p.incidents.length} / ${p.claims.length}`} />
      </div>
      <Tabs defaultValue={tab}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="monitor">实时监测</TabsTrigger>
          <TabsTrigger value="plan">监测方案</TabsTrigger>
          <TabsTrigger value="alerts">预警 {p.alerts.length ? `(${p.alerts.length})` : ""}</TabsTrigger>
          <TabsTrigger value="incidents">事故与理赔</TabsTrigger>
          <TabsTrigger value="reports">风险报告</TabsTrigger>
          <TabsTrigger value="coverage">保障内容</TabsTrigger>
          <TabsTrigger value="runs">智能体运行</TabsTrigger>
        </TabsList>
        <TabsContent value="monitor" className="mt-3 space-y-4">
          {!plan ? (
            <UnpublishedPlan policy={p} />
          ) : (
            <>
              <div className="grid gap-3 lg:grid-cols-2">
                {charts.map((m: any) => (
                  <TelemetryCard key={m.metric + m.asset} policyId={p.id} metric={m.metric} asset={m.asset} refresh={15000} />
                ))}
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Section title="检测记录" description="DM1–DM5、TP1–TP5 按小时运行；候选事件经归并、融合、定级与抑制后交给智能体研判">
                  <div className="max-h-80 space-y-1 overflow-y-auto">
                    {(plan.detections || [])
                      .slice()
                      .reverse()
                      .slice(0, 60)
                      .map((d: any, i: number) => (
                        <div key={i} className="flex items-start gap-2 border-b py-1 text-xs last:border-0">
                          <span className="w-12 shrink-0 text-muted-foreground tabular">第 {d.hour} 时</span>
                          <Pill tone={d.method.startsWith("TP") ? "violet" : "blue"}>{d.method}</Pill>
                          <span className="min-w-0 flex-1 text-muted-foreground">{d.detail}</span>
                        </div>
                      ))}
                    {(plan.detections || []).length === 0 && <div className="py-6 text-center text-xs text-muted-foreground">暂无检测信号，指标处于正常范围</div>}
                  </div>
                </Section>
                <Section title="抑制与周报汇总" description="静默期内同类事件、维护窗口、蓝色预警不单独推送" actions={<ThresholdDialog plan={plan} onDone={() => mutate()} />}>
                  <div className="max-h-80 space-y-1 overflow-y-auto text-xs">
                    {(plan.suppressed || [])
                      .slice()
                      .reverse()
                      .map((s: any, i: number) => (
                        <div key={i} className="flex gap-2 border-b py-1 last:border-0">
                          <span className="w-12 shrink-0 text-muted-foreground">第 {s.hour} 时</span>
                          <span className="flex-1">
                            {s.key} · {s.reason}
                          </span>
                          <AlertLevel level={s.level} />
                        </div>
                      ))}
                    {(plan.threshold_changes || []).map((c: any, i: number) => (
                      <div key={`t${i}`} className="rounded bg-amber-500/5 p-1.5">
                        阈值变更：{c.name} {c.before} → {c.after}（{c.reason}）
                      </div>
                    ))}
                    {Object.keys(plan.fpr || {}).length > 0 && <div className="pt-2 text-muted-foreground">方法误报率（处置反馈校准）：{Object.entries(plan.fpr).map(([k, v]: any) => `${k} ${pct(v)}`).join("、")}</div>}
                  </div>
                </Section>
              </div>
              {(plan.scheduled_events || []).length > 0 && (
                <Section title="外部事件源" description="舆情、司法、供应链、漏洞情报按订阅接入，由大模型抽取事件并判断与保单责任的相关性">
                  {plan.scheduled_events.map((e: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 border-b py-1.5 text-xs last:border-0">
                      <Pill tone={e.fired ? "green" : "gray"}>{e.fired ? "已接入" : `第 ${e.at_hour} 时`}</Pill>
                      <span className="font-medium">{e.title}</span>
                      <span className="text-muted-foreground">· {e.enterprise}</span>
                    </div>
                  ))}
                </Section>
              )}
            </>
          )}
        </TabsContent>
        <TabsContent value="plan" className="mt-3">
          {plan ? (
            <Section title="监测指标清单" description={`方案 #${plan.id} · 发布运行 #${plan.run_id}`} contentClassName="p-0">
              <IndicatorTable indicators={plan.indicators} />
            </Section>
          ) : (
            <UnpublishedPlan policy={p} />
          )}
        </TabsContent>
        <TabsContent value="alerts" className="mt-3">
          <Section contentClassName="space-y-1.5">
            {p.alerts.length === 0 && <EmptyState title="暂无预警" icon={Siren} />}
            {p.alerts.map((a: any) => (
              <Link key={a.id} href={`/monitoring/alerts/${a.id}`} className="flex items-center gap-3 rounded-lg border px-3 py-2 hover:bg-muted/40">
                <AlertLevel level={a.level} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{a.title}</div>
                  <div className="truncate text-xs text-muted-foreground">{a.explanation}</div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {a.methods.map((m: string) => (
                    <Pill key={m} tone={m.startsWith("TP") ? "violet" : "blue"} className="hidden md:inline-flex">
                      {m}
                    </Pill>
                  ))}
                </div>
                <Pill>{a.status_label}</Pill>
              </Link>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="incidents" className="mt-3 grid gap-4 lg:grid-cols-2">
          <Section title="事故响应">
            {p.incidents.length === 0 && <EmptyState title="暂无事故" />}
            {p.incidents.map((i: any) => (
              <Link key={i.id} href={`/monitoring/incidents/${i.id}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {i.incident_no} · {i.incident_type}
                </span>
                <Pill tone="orange">{i.status_label}</Pill>
              </Link>
            ))}
          </Section>
          <Section title="理赔案件">
            {p.claims.length === 0 && <EmptyState title="暂无理赔" />}
            {p.claims.map((c: any) => (
              <Link key={c.id} href={`/claims/${c.id}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {c.claim_no} · {c.incident_type}
                </span>
                <Pill>{c.status_label}</Pill>
              </Link>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="reports" className="mt-3">
          <Section>
            {p.reports.length === 0 && <EmptyState title="暂无风险管理报告" description="点击右上角“生成月度报告”" />}
            {p.reports.map((r: any) => (
              <Link key={r.artifact_key + r.version} href={`/monitoring/reports/${r.artifact_key}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {r.title} V{r.version}
                </span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Pill tone={r.status === "frozen" ? "green" : "amber"}>{r.status === "frozen" ? "已定稿" : "待审阅"}</Pill>
                  {dateTime(r.created_at)}
                </span>
              </Link>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="coverage" className="mt-3 grid gap-3 md:grid-cols-2">
          {(p.coverage?.docs || []).map((d: any) => (
            <Section key={d.doc_id} title={d.name}>
              <div className="mb-2 flex flex-wrap gap-1">
                {(d.lines || []).map((l: string) => (
                  <Pill key={l} tone="blue">
                    {LINE_NAMES[l]}
                  </Pill>
                ))}
              </div>
              <KV
                cols={2}
                items={[
                  { label: "免赔额", value: amount(d.params?.deductible) },
                  { label: "累计限额", value: amount(d.params?.limit) },
                  { label: "自担比例", value: pct(d.params?.coinsurance) },
                  { label: "等待期", value: `${d.params?.waiting_days ?? "—"} 天` },
                ]}
              />
            </Section>
          ))}
          {(p.coverage?.conditions || []).length > 0 && (
            <Section title="核保附加条件">
              {p.coverage.conditions.map((c: any) => (
                <div key={c.text} className="text-sm">
                  · {c.text}
                </div>
              ))}
            </Section>
          )}
        </TabsContent>
        <TabsContent value="runs" className="mt-3">
          <Section contentClassName="space-y-1.5">
            {p.runs.map((r: any) => (
              <Link key={r.id} href={`/runs/${r.id}`} className="flex items-center gap-3 rounded-lg border px-3 py-2 hover:bg-muted/40">
                <AgentBadge agentId={r.agent_id} label={{ plan: "监测方案", alert: "预警研判", incident: "事故响应", main: "风险报告" }[r.purpose as string] || r.purpose} />
                <span className="min-w-0 flex-1 truncate text-sm">{r.summary || r.title}</span>
                <ScoreBadge score={r.eval_score} passed={r.eval_passed} />
                <RunStatus status={r.status} />
              </Link>
            ))}
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <PolicyDetail />
    </Suspense>
  );
}
