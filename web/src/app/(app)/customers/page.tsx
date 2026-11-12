"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Building2, Plus, Search } from "lucide-react";
import { useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { amount } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CreateEnterpriseDialog } from "@/components/create-enterprise-dialog";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, RiskLevel } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function Customers() {
  const router = useRouter();
  const { user } = useAuth();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [track, setTrack] = useState("");
  const [creating, setCreating] = useState(false);
  const { data, error, mutate } = useApi<any[]>("/enterprises");
  const tracks = Array.from(new Set((data || []).map((e) => e.track).filter((t): t is string => Boolean(t))));
  const rows = (data || []).filter((e) => (!track || e.track === track) && (!q || e.name.includes(q) || e.tech_field.includes(q)));
  const page = usePagedList(rows, `${track}\0${q}`);
  return (
    <div>
      <PageHeader
        title="企业客户"
        description="科技企业客户档案与 360 画像：风险评估、投保与保单、监测预警、理赔与企业记忆。"
        actions={
          user?.role === "service" && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" /> 新建客户
            </Button>
          )
        }
      />
      <CreateEnterpriseDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(enterprise) => {
          mutate();
          router.push(`/customers/${enterprise.id}`);
        }}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索企业名称或技术领域" className="pl-8" />
        </div>
        <button key="all" onClick={() => setTrack("")} className={cn("rounded-full border px-3 py-1 text-xs", track === "" ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
          全部赛道
        </button>
        {tracks.map((t) => (
          <button key={t} onClick={() => setTrack(t)} className={cn("rounded-full border px-3 py-1 text-xs", track === t ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
            {t}
          </button>
        ))}
      </div>
      {error ? (
        <ErrorBlock error={error} retry={() => mutate()} />
      ) : !data ? (
        <LoadingBlock />
      ) : rows.length === 0 ? (
        <EmptyState title="没有匹配的企业" icon={Building2} />
      ) : (
        <div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {page.items.map((e) => (
            <Link key={e.id} href={`/customers/${e.id}`} className="rounded-xl border bg-card p-4 transition-shadow hover:shadow-md">
              <div className="flex items-start gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500/15 to-sky-500/15 text-sm font-semibold text-primary">{e.name.slice(0, 2)}</div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{e.name}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {e.track} · {e.tech_field}
                  </div>
                </div>
                <RiskLevel level={e.risk_level} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <div>
                  <div className="text-muted-foreground">营业收入</div>
                  <div className="font-medium">{amount(e.revenue)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">员工</div>
                  <div className="font-medium">{e.employees} 人</div>
                </div>
                <div>
                  <div className="text-muted-foreground">技术成熟度</div>
                  <div className="font-medium">TRL {e.trl ?? "—"}</div>
                </div>
              </div>
              <div className="mt-3 flex gap-1.5">
                <Pill tone={e.policies ? "green" : "gray"}>保单 {e.policies}</Pill>
                {e.open_alerts > 0 && <Pill tone="amber">未关闭预警 {e.open_alerts}</Pill>}
                <Pill>{e.region}</Pill>
              </div>
            </Link>
          ))}
          </div>
          <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Customers />
    </Suspense>
  );
}
