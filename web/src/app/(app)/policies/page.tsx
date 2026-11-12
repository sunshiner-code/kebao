"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { FileText } from "lucide-react";
import { useApi } from "@/lib/api";
import { amount, date, dateTime } from "@/lib/format";
import { AlertLevel, EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function Policies() {
  const q = useSearchParams().get("q") || "";
  const { data, error, mutate } = useApi<any[]>("/policies", { refreshInterval: 10000 });
  const rows = (data || []).filter((p) => !q || p.policy_no.includes(q) || p.enterprise.includes(q));
  const page = usePagedList(rows, q);
  return (
    <div>
      <PageHeader title="保单与监测" description="有效保单与其常驻监测方案：接入企业授权数据后，检测方法发现候选事件，由风险预警智能体研判发布。" />
      <Section contentClassName="p-0">
        {error ? (
          <div className="p-4">
            <ErrorBlock error={error} retry={() => mutate()} />
          </div>
        ) : !data ? (
          <div className="p-4">
            <LoadingBlock />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState title="暂无保单" description="核保通过后自动出单并生成监测方案" icon={FileText} />
          </div>
        ) : (
          <>
          <Table className="table-fixed">
            <colgroup>
              <col className="w-[12%]" />
              <col className="w-[16%]" />
              <col className="w-[20%]" />
              <col className="w-[9%]" />
              <col className="w-[9%]" />
              <col className="w-[14%]" />
              <col className="w-[12%]" />
              <col className="w-[8%]" />
            </colgroup>
            <TableHeader>
              <TableRow>
                <TableHead>保单号</TableHead>
                <TableHead>被保险企业</TableHead>
                <TableHead>产品</TableHead>
                <TableHead className="text-right">保费</TableHead>
                <TableHead className="text-right">保险金额</TableHead>
                <TableHead>保险期间</TableHead>
                <TableHead>监测时间</TableHead>
                <TableHead>预警</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="align-top">
                    <Link href={`/policies/${p.id}`} className="break-all font-mono text-xs text-primary hover:underline">
                      {p.policy_no}
                    </Link>
                  </TableCell>
                  <TableCell className="align-top font-medium leading-snug break-words">{p.enterprise}</TableCell>
                  <TableCell className="align-top leading-snug break-words text-sm">{p.product}</TableCell>
                  <TableCell className="align-top text-right tabular whitespace-nowrap">{amount(p.premium)}</TableCell>
                  <TableCell className="align-top text-right tabular whitespace-nowrap">{amount(p.sum_insured)}</TableCell>
                  <TableCell className="align-top text-xs leading-snug text-muted-foreground whitespace-nowrap">
                    {date(p.start_date)} – {date(p.end_date)}
                  </TableCell>
                  <TableCell className="align-top text-xs leading-snug">
                    {p.plan_id ? <span className="tabular text-muted-foreground">{dateTime(p.sim_now)}</span> : <Pill>未发布方案</Pill>}
                  </TableCell>
                  <TableCell className="align-top">
                    {p.max_level ? (
                      <div className="flex flex-col items-start gap-1">
                        <AlertLevel level={p.max_level} />
                        <span className="text-[11px] text-muted-foreground">{p.open_alerts} 条未关闭</span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">无</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationBar className="border-t px-4 py-3" page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
          </>
        )}
      </Section>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Policies />
    </Suspense>
  );
}
