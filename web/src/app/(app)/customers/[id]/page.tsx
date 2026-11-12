"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Brain } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { amount, date, dateTime, pct } from "@/lib/format";
import { AlertLevel, DataLevel, EmptyState, ErrorBlock, KV, PageHeader, PageLoading, Pill, Section, StatCard } from "@/components/common";
import { UploadButton } from "@/components/upload";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function CustomerPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const { data: e, error, mutate } = useApi<any>(`/enterprises/${id}`);
  const timeline = useApi<any[]>(user?.role === "designer" ? null : `/enterprises/${id}/timeline`);
  const auths = useApi<any[]>(user?.role === "designer" ? null : `/enterprises/${id}/authorizations`);
  const contacts = useApi<any[]>(user?.role === "designer" ? null : `/enterprises/${id}/contacts`);
  const [subject, setSubject] = useState("");
  const [content, setContent] = useState("");
  if (error) return <ErrorBlock error={error} retry={() => mutate()} />;
  if (!e) return <PageLoading />;
  const p = e.profile || {};
  const f = p.financials || {};
  const rdRatio = typeof f.rd_expense === "number" && f.revenue ? f.rd_expense / f.revenue : null;
  const cashMonths = typeof f.cash_reserve === "number" && f.monthly_fixed_cost ? f.cash_reserve / f.monthly_fixed_cost : null;
  return (
    <div>
      <PageHeader
        crumbs={[{ label: "企业客户", href: "/customers" }, { label: e.name }]}
        title={e.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Pill tone="blue">{e.track}</Pill>
            <span>{e.tech_field}</span>
            <span>· {e.region}</span>
            <span>· 成立 {e.founded_year}</span>
            <span>· {e.listed ? "上市" : "非上市"}</span>
            <span>· {e.credit_code}</span>
          </span>
        }
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <StatCard label="营业收入" value={amount(f.revenue)} />
        <StatCard label="净利润" value={amount(f.net_profit)} tone={f.net_profit < 0 ? "danger" : "success"} />
        <StatCard label="研发投入强度" value={pct(rdRatio)} />
        <StatCard label="现金可支撑" value={cashMonths == null ? "—" : `${cashMonths.toFixed(1)} 月`} />
        <StatCard label="在保保单" value={e.policies.length} />
        <StatCard label="预警" value={e.alerts.length} tone={e.alerts.some((a: any) => a.level === "red") ? "danger" : "default"} />
      </div>
      <Tabs defaultValue="profile" className="mt-4">
        <TabsList>
          <TabsTrigger value="profile">画像</TabsTrigger>
          <TabsTrigger value="risk">风险评估</TabsTrigger>
          <TabsTrigger value="biz">投保与保单</TabsTrigger>
          <TabsTrigger value="alerts">预警与理赔</TabsTrigger>
          <TabsTrigger value="materials">尽调资料</TabsTrigger>
          {user?.role !== "designer" && <TabsTrigger value="auth">数据授权</TabsTrigger>}
          {user?.role !== "designer" && <TabsTrigger value="visits">拜访</TabsTrigger>}
          {user?.role !== "designer" && <TabsTrigger value="timeline">时间线</TabsTrigger>}
        </TabsList>
        <TabsContent value="profile" className="mt-3 grid gap-4 lg:grid-cols-2">
          <Section title="科技产品与生命周期">
            {(p.products || []).length === 0 && (p.milestones || []).length === 0 && <EmptyState title="暂无科技产品" />}
            {(p.products || []).map((pr: any) => (
              <div key={pr.name} className="mb-2 rounded-lg border p-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{pr.name}</span>
                  <Pill tone="violet">TRL {pr.trl}</Pill>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{pr.description}</div>
              </div>
            ))}
            {(p.milestones || []).map((m: any) => (
              <div key={m.name} className="text-xs">
                里程碑：{m.name}（计划 {m.planned}）<Pill tone={m.status === "延期" ? "red" : "blue"}>{m.status}</Pill>
              </div>
            ))}
          </Section>
          <Section title="关键研发设备">
            {(p.equipment || []).length === 0 && <EmptyState title="暂无设备" />}
            {(p.equipment || []).map((eq: any) => (
              <div key={eq.serial} className="flex justify-between border-b py-1.5 text-xs last:border-0">
                <span>
                  {eq.name} <span className="font-mono text-muted-foreground">{eq.serial}</span>
                </span>
                <span className="text-muted-foreground">
                  {amount(eq.value)} · {eq.purchase_date} · {eq.location}
                </span>
              </div>
            ))}
          </Section>
          <Section title="关键供应商">
            {(p.suppliers || []).length === 0 && <EmptyState title="暂无供应商" />}
            {(p.suppliers || []).map((s: any) => (
              <div key={s.name} className="flex items-center justify-between border-b py-1.5 text-xs last:border-0">
                <span>
                  {s.name} · {s.item}
                </span>
                <Pill tone={s.share >= 0.5 ? "orange" : "gray"}>依赖度 {pct(s.share)}</Pill>
              </div>
            ))}
          </Section>
          <Section title="团队与信息安全">
            <KV
              cols={2}
              items={[
                { label: "核心团队", value: p.team ? `${p.team.core_size ?? "—"} 人，近一年离职 ${p.team.left_last_year ?? "—"} 人` : "—" },
                { label: "核心岗位", value: (p.team?.core_roles || []).map((r: any) => r.role).join("、") || "—" },
                { label: "等保级别", value: p.security?.level ? `${p.security.level} 级` : "—" },
                { label: "数据规模档位", value: p.security?.data_scale || "—" },
                ...(user?.role !== "designer" ? [{ label: "法定代表人", value: p.legal_rep }, { label: "联系电话", value: p.phone }] : []),
              ]}
            />
            {e.memory && (
              <div className="mt-3 rounded-lg bg-violet-500/5 p-3 text-xs">
                <div className="flex items-center gap-1 font-medium">
                  <Brain className="size-3.5 text-violet-600" /> 企业记忆（智能体跨运行沉淀）
                </div>
                <div className="mt-1 text-muted-foreground">{e.memory.text}</div>
              </div>
            )}
          </Section>
        </TabsContent>
        <TabsContent value="risk" className="mt-3">
          <Section title="风险评估报告">
            {e.risk_reports.length === 0 ? (
              <EmptyState title="暂无企业级风险评估" description="可在设计项目中新建企业定制项目进行评估" />
            ) : (
              e.risk_reports.map((r: any) => (
                <Link key={r.artifact_key + r.version} href={`/design/risk/${r.artifact_key}`} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                  <span>
                    {r.title} V{r.version}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {r.risks} 条风险 · {dateTime(r.created_at)}
                  </span>
                </Link>
              ))
            )}
          </Section>
        </TabsContent>
        <TabsContent value="biz" className="mt-3 grid gap-4 lg:grid-cols-2">
          <Section title="投保单">
            {e.applications.length === 0 && <EmptyState title="暂无投保" />}
            {e.applications.map((a: any) => (
              <Link key={a.id} href={`/underwriting/${a.id}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {a.app_no} · {a.product}
                </span>
                <Pill>{a.status_label}</Pill>
              </Link>
            ))}
          </Section>
          <Section title="保单">
            {e.policies.length === 0 && <EmptyState title="暂无保单" />}
            {e.policies.map((pl: any) => (
              <Link key={pl.id} href={`/policies/${pl.id}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {pl.policy_no} · {pl.product}
                </span>
                <span className="text-xs text-muted-foreground">
                  保费 {amount(pl.premium)} · {date(pl.start_date)} 起
                </span>
              </Link>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="alerts" className="mt-3 grid gap-4 lg:grid-cols-2">
          <Section title="预警">
            {e.alerts.length === 0 && <EmptyState title="暂无预警" />}
            {e.alerts.map((a: any) => (
              <Link key={a.id} href={`/monitoring/alerts/${a.id}`} className="mb-1.5 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <AlertLevel level={a.level} />
                <span className="flex-1 truncate">{a.title}</span>
                <span className="text-xs text-muted-foreground">{a.status_label}</span>
              </Link>
            ))}
          </Section>
          <Section title="理赔">
            {e.claims.length === 0 && <EmptyState title="暂无理赔" />}
            {e.claims.map((c: any) => (
              <Link key={c.id} href={`/claims/${c.id}`} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <span>
                  {c.claim_no} · {c.incident_type}
                </span>
                <Pill>{c.status_label}</Pill>
              </Link>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="materials" className="mt-3">
          <Section title="尽调资料（私域）" description="上传后自动识别数据级别；L2 以上材料只在私域解析，出域前脱敏" actions={user?.role !== "admin" && <UploadButton ownerType="enterprise" ownerId={e.id} enterpriseId={e.id} materialTypes={["尽调报告", "访谈纪要", "财务报表", "技术资料", "其他材料"]} onUploaded={() => mutate()} />}>
            {e.materials.map((m: any) => (
              <div key={m.id} className="mb-2 rounded-lg border p-3">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium">{m.name}</span>
                  <Pill>{m.type}</Pill>
                  <DataLevel level={m.data_level} />
                  {m.detected_level > m.data_level - 1 && m.detected_level >= 3 && <Pill tone="red">检出个人信息或技术秘密</Pill>}
                  <span className="ml-auto text-xs text-muted-foreground">{m.chars} 字</span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{m.preview}</div>
              </div>
            ))}
          </Section>
        </TabsContent>
        <TabsContent value="auth" className="mt-3 space-y-2">
          {(auths.data || []).map((a) => (
            <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
              <div>
                <div className="font-medium">{a.auth_no} · {a.scope_labels.join("、")}</div>
                <div className="text-xs text-muted-foreground">{a.status} · 至 {a.valid_to?.slice(0, 10)} · 授权人 {a.grantor}</div>
              </div>
              {a.status === "active" && <Button size="sm" variant="outline" onClick={async () => { const reason = window.prompt("撤回原因"); if (!reason) return; try { await api(`/authorizations/${a.id}/revoke`, { method: "POST", json: { reason } }); toast.success("已撤回，监测指标已降级"); auths.mutate(); } catch (err: any) { toast.error(err.message); } }}>撤回</Button>}
            </div>
          ))}
          {(auths.data || []).length === 0 && <EmptyState title="暂无数据接入授权" />}
        </TabsContent>
        <TabsContent value="visits" className="mt-3">
          <Section title="新增拜访纪要" description="保存后在私域抽取要点、风险信号和待跟进事项。">
            <Input className="mb-2 h-8" placeholder="主题" value={subject} onChange={(ev) => setSubject(ev.target.value)} />
            <Textarea rows={3} placeholder="纪要正文" value={content} onChange={(ev) => setContent(ev.target.value)} />
            <Button className="mt-2" size="sm" onClick={async () => { try { await api(`/enterprises/${id}/contacts`, { method: "POST", json: { kind: "visit", subject, content } }); toast.success("已登记"); setSubject(""); setContent(""); contacts.mutate(); } catch (err: any) { toast.error(err.message); } }}>保存</Button>
          </Section>
          <div className="mt-3 space-y-2">
            {(contacts.data || []).map((c) => (
              <div key={c.id} className="rounded-lg border p-3 text-sm">
                <div className="font-medium">{c.kind_label} · {c.subject}</div>
                <div className="mt-1 text-xs text-muted-foreground">{(c.summary?.key_points || []).join("；") || c.content}</div>
                {(c.summary?.risks || []).length > 0 && <div className="mt-1 text-xs text-rose-600">风险：{c.summary.risks.map((r: any) => r.text).join("；")}</div>}
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="timeline" className="mt-3">
          <Section title="动态时间线">
            {(timeline.data || []).map((ev, i) => (
              <div key={i} className="border-b py-2 text-sm last:border-0">
                <div className="font-medium">{ev.title}</div>
                <div className="text-xs text-muted-foreground">{ev.at?.slice(0, 16)} · {ev.detail}</div>
              </div>
            ))}
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
