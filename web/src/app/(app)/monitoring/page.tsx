"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AlertOctagon, Siren } from "lucide-react";
import { useApi } from "@/lib/api";
import { pct, relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AlertLevel, EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section, StatCard } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";

const LEVELS = [
  ["", "全部等级"],
  ["red", "红色"],
  ["orange", "橙色"],
  ["yellow", "黄色"],
];
const STATUSES = [
  ["", "全部状态"],
  ["new", "待处置"],
  ["processing", "处置中"],
  ["escalated", "已升级为事故"],
  ["closed,false_positive", "已关闭"],
];

function Overview() {
  const q = useSearchParams().get("q") || "";
  const [level, setLevel] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams({ ...(level ? { level } : {}), ...(status ? { status } : {}) });
  const alerts = useApi<any[]>(`/alerts?${qs}`, { refreshInterval: 6000 });
  const policies = useApi<any[]>("/policies", { refreshInterval: 15000 });
  const all = alerts.data || [];
  const rows = all.filter((a) => !q || a.alert_no.includes(q) || (a.enterprise || "").includes(q));
  const page = usePagedList(rows, `${level}|${status}|${q}`);
  const open = all.filter((a) => ["new", "processing"].includes(a.status));
  return (
    <div>
      <PageHeader title="风险预警" description="风险预警智能体：常驻监测企业授权数据与外部事件，五种通用检测与五种科技产品专项检测融合研判，分级推送并跟踪处置。" />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="监测中保单" value={(policies.data || []).filter((p) => p.plan_id).length} />
        <StatCard label="红色（1 小时内处置）" value={open.filter((a) => a.level === "red").length} tone="danger" icon={AlertOctagon} />
        <StatCard label="橙色（4 小时内）" value={open.filter((a) => a.level === "orange").length} tone="warning" />
        <StatCard label="黄色（1 个工作日）" value={open.filter((a) => a.level === "yellow").length} />
        <StatCard label="超时未处置" value={all.filter((a) => a.overdue).length} tone={all.some((a) => a.overdue) ? "danger" : "success"} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div>
          <div className="mb-3 flex flex-wrap gap-2">
            {LEVELS.map(([k, l]) => (
              <button key={k} onClick={() => setLevel(k)} className={cn("rounded-full border px-3 py-1 text-xs", level === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
                {l}
              </button>
            ))}
            <span className="mx-1 h-4 w-px self-center bg-border" />
            {STATUSES.map(([k, l]) => (
              <button key={k} onClick={() => setStatus(k)} className={cn("rounded-full border px-3 py-1 text-xs", status === k ? "border-foreground/30 bg-foreground/5 font-medium" : "bg-card hover:bg-muted")}>
                {l}
              </button>
            ))}
          </div>
          {alerts.error ? (
            <ErrorBlock error={alerts.error} />
          ) : !alerts.data ? (
            <LoadingBlock />
          ) : rows.length === 0 ? (
            <EmptyState title="暂无预警" icon={Siren} description="在保单详情页推进监测时间后，监测数据会进入检测流程" />
          ) : (
            <div>
            <div className="space-y-2">
              {page.items.map((a) => (
                <Link key={a.id} href={`/monitoring/alerts/${a.id}`} className={cn("block rounded-xl border bg-card p-3 transition-shadow hover:shadow-md", a.level === "red" && "border-l-4 border-l-rose-500", a.level === "orange" && "border-l-4 border-l-orange-400", a.level === "yellow" && "border-l-4 border-l-amber-300")}>
                  <div className="flex flex-wrap items-center gap-2">
                    <AlertLevel level={a.level} />
                    <span className="font-medium">{a.title}</span>
                    <span className="text-xs text-muted-foreground">{a.enterprise}</span>
                    <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                      {a.overdue && <Pill tone="red">已超时</Pill>}
                      <Pill>{a.status_label}</Pill>
                      {relative(a.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{a.explanation}</div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                    {a.methods.map((m: string) => (
                      <Pill key={m} tone={m.startsWith("TP") ? "violet" : "blue"}>
                        {m}
                      </Pill>
                    ))}
                    <span className="text-muted-foreground">融合置信度 {pct(a.confidence)}</span>
                    {a.relation && <span className="text-muted-foreground">· 与保险责任{a.relation}</span>}
                    <span className="ml-auto font-mono text-muted-foreground">{a.alert_no}</span>
                  </div>
                </Link>
              ))}
            </div>
            <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
            </div>
          )}
        </div>
        <Section title="监测中的保单">
          <div className="space-y-1.5">
            {(policies.data || []).map((p) => (
              <Link key={p.id} href={`/policies/${p.id}`} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted/40">
                <div className="min-w-0">
                  <div className="truncate font-medium">{p.enterprise}</div>
                  <div className="text-xs text-muted-foreground">{p.policy_no}</div>
                </div>
                {p.max_level ? <AlertLevel level={p.max_level} /> : <Pill tone="green">平稳</Pill>}
              </Link>
            ))}
          </div>
          <Link href="/monitoring/incidents" className="mt-3 block text-xs text-primary hover:underline">
            查看事故响应 →
          </Link>
        </Section>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Overview />
    </Suspense>
  );
}
