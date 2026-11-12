"use client";

import Link from "next/link";
import { Siren } from "lucide-react";
import { useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { Bar, EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";

export default function IncidentsPage() {
  const { data, error, mutate } = useApi<any[]>("/incidents", { refreshInterval: 6000 });
  const page = usePagedList(data || [], "");
  return (
    <div>
      <PageHeader title="事故响应" description="红色预警判定疑似事故、客户报案或服务人员手动发起后，风险预警智能体按预案生成带时限的处置步骤、减损措施与证据保全指引，调度服务商并打包事故前后数据窗口，生成预报案移交理赔。" />
      {error ? (
        <ErrorBlock error={error} retry={() => mutate()} />
      ) : !data ? (
        <LoadingBlock />
      ) : data.length === 0 ? (
        <EmptyState title="暂无事故响应" icon={Siren} />
      ) : (
        <div>
        <div className="grid gap-3 lg:grid-cols-2">
          {page.items.map((i) => (
            <Link key={i.id} href={`/monitoring/incidents/${i.id}`}>
              <Section className="h-full transition-shadow hover:shadow-md">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <Siren className="size-4 text-rose-500" />
                      <span className="font-medium">{i.incident_type}</span>
                      <Pill tone={i.status === "claimed" ? "green" : i.status === "awaiting_claim" ? "amber" : "orange"}>{i.status_label}</Pill>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {i.incident_no} · {i.enterprise} · {i.policy_no}
                    </div>
                  </div>
                  <span className="text-xs text-muted-foreground">{dateTime(i.occurred_at)}</span>
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-muted-foreground">{i.description}</div>
                <div className="mt-3 flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground">预案步骤</span>
                  <Bar value={i.steps_done} max={i.steps_total || 1} tone="green" />
                  <span className="tabular">
                    {i.steps_done}/{i.steps_total}
                  </span>
                </div>
              </Section>
            </Link>
          ))}
        </div>
        <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
        </div>
      )}
    </div>
  );
}
