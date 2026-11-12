"use client";

import Link from "next/link";
import { useState } from "react";
import { Boxes } from "lucide-react";
import { useApi } from "@/lib/api";
import { amount, date, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";

const STATUS: Record<string, { label: string; tone: any }> = { published: { label: "已发布", tone: "green" }, pending_approval: { label: "待审批", tone: "violet" }, draft: { label: "草稿", tone: "gray" }, rejected: { label: "已退回", tone: "red" } };

export default function ProductsPage() {
  const [status, setStatus] = useState("");
  const { data, error, mutate } = useApi<any[]>(`/products${status ? `?status=${status}` : ""}`);
  const page = usePagedList(data || [], status);
  return (
    <div>
      <PageHeader title="产品库" description="设计流水线产出的保险产品：条款、正式费率与精算报告、核保要求、监测要点与理赔材料清单作为一个产品包统一发布。" />
      <div className="mb-4 flex gap-2">
        {[
          ["", "全部"],
          ["published", "已发布"],
          ["pending_approval", "待审批"],
        ].map(([k, l]) => (
          <button key={k} onClick={() => setStatus(k)} className={cn("rounded-full border px-3 py-1 text-xs", status === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
            {l}
          </button>
        ))}
      </div>
      {error ? (
        <ErrorBlock error={error} retry={() => mutate()} />
      ) : !data ? (
        <LoadingBlock />
      ) : data.length === 0 ? (
        <EmptyState title="暂无产品" description="完成设计流水线后组装产品包并审批发布。" icon={Boxes} />
      ) : (
        <div>
          <div className="grid gap-4 lg:grid-cols-2">
          {page.items.map((p) => (
            <Link key={p.id} href={`/products/${p.id}`}>
              <Section className="h-full transition-shadow hover:shadow-md">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-base font-semibold">{p.name}</span>
                      <Pill tone={STATUS[p.status]?.tone}>{STATUS[p.status]?.label || p.status}</Pill>
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {p.code} · V{p.version} · {p.stage_names.join("、")} · {p.start_mode === "warm" ? "热启动" : "冷启动"} · 发布 {date(p.published_at)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-semibold tabular">{p.policies}</div>
                    <div className="text-xs text-muted-foreground">在保保单</div>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {p.line_names.map((l: string) => (
                    <Pill key={l} tone="blue">
                      {l}
                    </Pill>
                  ))}
                </div>
                <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
                  {p.components.slice(0, 6).map((c: any) => (
                    <div key={c.name + c.line} className="flex justify-between rounded-md bg-muted/50 px-2 py-1 text-xs">
                      <span className="truncate">{c.name}</span>
                      <span className="shrink-0 tabular text-muted-foreground">
                        {c.gross_rate == null ? "费率未选定" : pct(c.gross_rate, 3)} · 限额 {amount(c.limit)}
                      </span>
                    </div>
                  ))}
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
